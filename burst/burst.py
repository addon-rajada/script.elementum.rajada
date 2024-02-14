# -*- coding: utf-8 -*-

"""
Rajada processing thread
"""

from __future__ import unicode_literals
from future.utils import PY3, iteritems
from difflib import SequenceMatcher

import traceback
import re
import json
import time
from threading import Thread
from elementum.provider import append_headers, get_setting, log, set_setting
if PY3:
    from queue import Queue
    from urllib.parse import urlparse
    from urllib.parse import unquote
    basestring = str
    long = int
else:
    from Queue import Queue
    from urlparse import urlparse
    from urllib import unquote
from .parser.ehp import Html
from kodi_six import xbmc, xbmcgui, xbmcaddon, py2_encode

from .provider import process
from .providers.definitions import definitions, longest
from .filtering import apply_filters, Filtering, cleanup_results
from .client import USER_AGENT, Client
from .utils import ADDON_ICON, notify, translation, sizeof, get_icon_path, get_enabled_providers, get_alias, get_float, get_int
from .tracker import get_torrent_info, TR_FULL_STR

query_value_from_provider = None
provider_names = []
provider_results = []
provider_cache = {}
available_providers = 0
request_time = time.time()

use_kodi_language = get_setting('kodi_language', bool)
auto_timeout = get_setting("auto_timeout", bool)
timeout = get_setting("timeout", int)
debug_parser = get_setting("use_debug_parser", bool)
max_results = get_setting('max_results', int)
sort_by = get_setting('sort_by', int)
use_additional_filters = get_setting('additional_filters', bool)
use_block = get_setting('block', str).strip().lower()
check_seeders_peers = get_setting("check_seeders_peers", bool)
use_sequential_tracker_checking = False
custom_elementum_settings = get_setting("custom_elementum_settings", bool)

use_similarity_filter = get_setting("use_similarity_filter", bool)
sim_filter_minimum = get_float(get_setting('sim_filter_minimum'))
sim_filter_acceptable = get_float(get_setting('sim_filter_acceptable'))
sim_filter_good = get_float(get_setting('sim_filter_good'))
sim_filter_tolerance = 0.09 # at least 25% of similarity

c_lime = "[COLOR lime]"
c_green = "[COLOR mediumseagreen]"
c_crimson = "[COLOR indianred]" # crimson
c_white = "[COLOR white]"
c_close = "[/COLOR]"

special_chars = "()\"':.[]<>/\\?"
elementum_timeout = 0

try:
	#import multiprocessing # will not work at android
	from concurrent.futures import ThreadPoolExecutor, as_completed
except ImportError: # rajada: if python < 3, multithread checking of tracker is not possible
    use_sequential_tracker_checking = True
    notify(translation(32250), ADDON_ICON)

elementum_addon = xbmcaddon.Addon(id='plugin.video.elementum')
if elementum_addon:
    # rajada: set elementum timeout due to bad behaviour
	# elementum default: 30 / burst default: 25
	# ref: https://github.com/elgatito/plugin.video.elementum/issues/414
	# ref: https://github.com/elgatito/plugin.video.elementum/issues/675
    if check_seeders_peers and elementum_addon.getSetting('custom_provider_timeout_enabled') == 'false':
        elementum_custom_timeout = '40'
        elementum_addon.setSetting('custom_provider_timeout_enabled', 'true')
        elementum_addon.setSetting('custom_provider_timeout', elementum_custom_timeout)
        notify(translation(32251) % elementum_custom_timeout, ADDON_ICON)

    if not check_seeders_peers and elementum_addon.getSetting('custom_provider_timeout_enabled') == 'true':
        elementum_custom_timeout = '40'
        elementum_addon.setSetting('custom_provider_timeout', elementum_custom_timeout)
        notify(translation(32251) % elementum_custom_timeout, ADDON_ICON)

    # in case of disabled checking and provider timeout equals false, we do nothing

    if elementum_addon.getSetting('custom_provider_timeout_enabled') == "true":
        #notify(translation(32252), ADDON_ICON)
        elementum_timeout = int(elementum_addon.getSetting('custom_provider_timeout'))
    else:
        #notify(translation(32253), ADDON_ICON)
        elementum_timeout = 40
    log.info("Using timeout from Elementum: %d seconds" % (elementum_timeout))

    # rajada: custom elementum settings for fast setup
    if custom_elementum_settings:
        #notify('Using custom elementum settings', ADDON_ICON)
		# general
        elementum_addon.setSetting('torrent_history_size', '200')
        set_setting('max_size', '300')
		# appearance
        elementum_addon.setSetting('results_per_page', '29')
        elementum_addon.setSetting('viewmode_movies', '54') # InfoWall
        elementum_addon.setSetting('viewmode_tvshows', '54') # InfoWall
        # providers
        elementum_addon.setSetting('sorting_mode_movies', '1') # by Resolution
        elementum_addon.setSetting('sorting_mode_shows', '1') # by Resolution
        #elementum_addon.setSetting('use_cache_search', 'true') # ToDo: causing search fail (elementum directory error) ?
        #elementum_addon.setSetting('cache_search_duration', '20')
    else:
        # appearance
        elementum_addon.setSetting('viewmode_movies', '0') # List
        elementum_addon.setSetting('viewmode_tvshows', '0') # List
        # providers
        elementum_addon.setSetting('sorting_mode_movies', '1') # by Resolution
        elementum_addon.setSetting('sorting_mode_shows', '1') # by Resolution

# Make sure timeout is always less than the one from Elementum.
if auto_timeout:
    timeout = elementum_timeout - 3
elif elementum_timeout > 0 and timeout > elementum_timeout - 3:
    log.info("Redefining timeout to be less than Elementum's: %d to %d seconds" % (timeout, elementum_timeout - 3))
    timeout = elementum_timeout - 3

timeout_offset = int(elementum_timeout / 2) - 7 # rajada: offset to split providers/trackers checking (40/2 - 7 = 13)
new_timeout = timeout - timeout_offset if check_seeders_peers else timeout # 37-13=24 for providers and 13 for tracker

def query_correction(q): # rajada: apply some useful changes to query
    q = q.lower()
    q = q.replace(':', ' ')
    q = q.replace(',', ' ')
    q = q.replace('!', ' ')
    q = q.replace('-', ' ')
    q = q.replace('+', ' ') # year explorer
    for part in range(1,10):
        q = q.replace(' parte %s' % part, ' ')
        q = q.replace(' parte 0%s' % part, ' ')
        q = q.replace(' parte %s' % 'i'*part, ' ')
    return q

def search(payload, method="general"):
    """ Main search entrypoint

    Args:
        payload (dict): Search payload from Elementum.
        method   (str): Type of search, can be ``general``, ``movie``, ``show``, ``season`` or ``episode``

    Returns:
        list: All filtered results in the format Elementum expects
    """
    log.debug("Searching with payload (%s): %s" % (method, repr(payload)))

    if method == 'episode' and 'anime' in payload and payload['anime']:
        method = 'anime'

    if method == 'general':
        if 'query' in payload:
            payload['query'] = query_correction(payload['query'])
            payload['title'] = payload['query']
            payload['titles'] = {
                'source': payload['query'],
                'original': payload['query']
            }
        else:
            payload = query_correction(payload)
            payload = {
                'title': payload,
                'titles': {
                    'source': payload,
                    'original': payload
                },
            }

    payload['titles'] = dict((k.lower(), v) for k, v in iteritems(payload['titles']))

    # If titles[] exists in payload and there are special chars in titles[source]
    #   then we set a flag to possibly modify the search query
    payload['has_special'] = 'titles' in payload and \
                             bool(payload['titles']) and \
                             'source' in payload['titles'] and \
                             any(c in payload['titles']['source'] for c in special_chars)
    if payload['has_special']:
        log.debug("Query title contains special chars, so removing any quotes in the search query")
    if 'episode' not in payload:
        payload['episode'] = 0

    if 'proxy_url' not in payload:
        payload['proxy_url'] = ''
    if 'internal_proxy_url' not in payload:
        payload['internal_proxy_url'] = ''
    if 'elementum_url' not in payload:
        payload['elementum_url'] = ''
    if 'silent' not in payload:
        payload['silent'] = False
    if 'skip_auth' not in payload:
        payload['skip_auth'] = False

    global query_value_from_provider
    global request_time
    global provider_cache
    global provider_names
    global provider_results
    global available_providers

    query_value_from_provider = None
    provider_cache = {}
    provider_names = []
    provider_results = []
    available_providers = 0
    request_time = time.time()

    providers = get_enabled_providers(method)

    if len(providers) == 0:
        if not payload['silent']:
            notify(translation(32060), image=get_icon_path())
        log.error("No providers enabled")
        return []

    log.info("Burstin' with %s" % ", ".join([definitions[provider]['name'] for provider in providers]))

    if use_kodi_language:
        kodi_language = xbmc.getLanguage(xbmc.ISO_639_1)
        if not kodi_language:
            log.warning("Kodi returned empty language code...")
        elif 'titles' not in payload or not payload['titles']:
            log.info("No translations available...")
        elif payload['titles'] and kodi_language not in payload['titles']:
            log.info("No '%s' translation available..." % kodi_language)

    p_dialog = xbmcgui.DialogProgressBG()
    if not payload['silent']:
        p_dialog.create('Elementum [COLOR FFFF6B00]Rajada[/COLOR]', translation(32061))

    if 'titles' in payload:
        log.debug("Translated titles from Elementum: %s" % (repr(payload['titles'])))

    providers_time = time.time()

    # rajada: copy processing from provider::process(), just to get updated query at global level (for similarity filter), using one provider only
    provider_index = len(providers) - 1 # assert is brazilian
    filterToGetQuery = Filtering()
    filterToGetQuery.define_languages(providers[provider_index])
    filterToGetQuery.use_general(providers[provider_index], payload) if method == "general" else filterToGetQuery.use_movie(providers[provider_index], payload) # {title} or {title} {year}
    definition = definitions[providers[provider_index]]
    definition = get_alias(definition, get_setting("%s_alias" % providers[provider_index]))
    for query, extra, priority in zip(filterToGetQuery.queries, filterToGetQuery.extras, filterToGetQuery.queries_priorities):
        query_value_from_provider = filterToGetQuery.process_keywords(providers[provider_index], query, definition)
    log.debug("burst::search - updated query: %s | using provider [%s]" % (query_value_from_provider, providers[provider_index]))

    for provider in providers:
        available_providers += 1
        provider_names.append(definitions[provider]['name'])
        task = Thread(target=run_provider, args=(provider, payload, method, providers_time, new_timeout))
        task.start()

    total = float(available_providers)

    # Exit if all providers have returned results or timeout reached, check every 100ms
    while time.time() - providers_time < new_timeout and available_providers > 0:
        timer = time.time() - providers_time
        log.debug("Timer: %ds / %ds" % (timer, timeout))
        if timer > new_timeout:
            break
        message = translation(32062) % available_providers if available_providers > 1 else translation(32063)
        if not payload['silent']:
            p_dialog.update(int((total - available_providers) / total * 100), message=message)
        time.sleep(0.25)

    if not payload['silent']:
        p_dialog.close()
    del p_dialog

    if available_providers > 0:
        message = ', '.join(provider_names)
        message = message + translation(32064)
        log.warning(message)
        if not payload['silent']:
            notify(message, ADDON_ICON)

    log.debug("all provider_results of %d: %s" % (len(provider_results), repr(provider_results)))

    filtered_results = apply_filters(provider_results, process_sizes=True)

    log.debug("all filtered_results of %d: %s" % (len(filtered_results), repr(filtered_results)))

    log.info("Providers returned %d results in %s seconds" % (len(filtered_results), round(time.time() - request_time, 2)))

    """
		results format

		[
			{
				'id': ...
				'name': ...
				'uri': ...
				'info_hash': ...
				'size': ...
				'seeds': ...
				'peers': ...
				'language': ...
				'provider': ...
				'icon': ...
				'sort_resolution': ...
				'sort_balance': ...
			}
		]
	
	"""

    # rajada: check seeders and peers from trackers
    p_dialog = xbmcgui.DialogProgressBG()
    if not payload['silent'] and check_seeders_peers:
        p_dialog.create('Elementum [COLOR FFFF6B00]Rajada[/COLOR]', translation(32061))
    total_results = float(len(filtered_results)) # float as python 2 has precision error
    missing_results = int(total_results)
    not_br_results = 0
    already_checked_hashes = []

    if check_seeders_peers:
        log.debug("Timer: %ds / %ds" % (timer, timeout))

        # -> sequential version (slow) <-
        if use_sequential_tracker_checking:
            for r in filtered_results:
                if 'FFF14E13' not in r['provider']: # only check links from brazilian trackers
                    not_br_results += 1
                    missing_results -= 1
                    continue
                message = translation(32254) % missing_results
                if not payload['silent']: p_dialog.update(int((total_results - missing_results) / total_results * 100), message=message)
                timer = time.time() - providers_time
                log.debug("Timer: %ds / %ds" % (timer, timeout))
                if timer + 5 >= timeout:
                    log.debug("Timer reached Timeout for sequential tracker checking")
                    break
                result_hash, parsed_seeds, parsed_peers = get_torrent_info(r)
                r['seeds'] = parsed_seeds if (parsed_seeds and parsed_seeds > r['seeds']) else r['seeds']
                r['peers'] = parsed_peers if (parsed_peers and parsed_peers > r['peers']) else r['peers']
                missing_results -= 1
		
		# -> parallel version with multiprocessing (android does not support multiprocessing) <-
		# ref: https://github.com/xbmc/xbmc/issues/20031
		# ref: https://github.com/termux/termux-app/issues/1272
        #with multiprocessing.Pool(2 * multiprocessing.cpu_count()) as pool:
        #    for pool_result in pool.map(get_torrent_info, filtered_results):
        #        message = "Verificando Seeders e Leechers\n%s Links Restantes" % missing_results
        #        if not payload['silent']: p_dialog.update(int((total_results - missing_results) / total_results * 100), message=message)
        #        timer = time.time() - providers_time
        #        log.debug("Timer: %ds / %ds" % (timer, timeout))
        #        if timer + 4 >= timeout:
        #            log.debug("Timer reached Timeout for tracker checking")
        #            break
        #        for r in filtered_results:
        #            if r['info_hash'] == pool_result[0]: # same hash
        #                parsed_seeds, parsed_peers = pool_result[1], pool_result[2]
        #                r['seeds'] = parsed_seeds if (parsed_seeds and parsed_seeds > r['seeds']) else r['seeds']
        #                r['peers'] = parsed_peers if (parsed_peers and parsed_peers > r['peers']) else r['peers']
        #                missing_results -= 1

        # -> parallel version with ThreadPool (working at android if python >= 3) <-
        else:
            results_to_check = [x for x in filtered_results if 'FFF14E13' in x['provider']] # only check links from brazilian trackers
            workers = len(results_to_check) if len(results_to_check) > 0 and len(results_to_check) <= 16 else 16
            with ThreadPoolExecutor(max_workers = workers) as executor:
                futures = [executor.submit(get_torrent_info, torrent_obj = x) for x in results_to_check]
                not_br_results = missing_results - len(results_to_check)
                missing_results -= not_br_results # remove non brazilian entries from counter
                for f in as_completed(futures):
                    try: pool_result = f.result()
                    except Exception: continue
                    message = translation(32254) % missing_results
                    if not payload['silent']: p_dialog.update(int((total_results - missing_results) / total_results * 100), message=message)
                    timer = time.time() - providers_time
                    log.debug("Timer: %ds / %ds" % (timer, timeout))
                    if timer + 5 >= timeout:
                        cancel_list = [fut.cancel() for fut in futures]; cancel_str = '%s cancel ok / %s cancel fail' % (cancel_list.count(True), cancel_list.count(False))
                        log.debug("Timer reached Timeout for ThreadPool tracker checking - %s" % cancel_str)
                        break
                    for r in results_to_check:
                        if timer + 5 >= timeout:
                            cancel_list = [fut.cancel() for fut in futures]; cancel_str = '%s cancel ok / %s cancel fail' % (cancel_list.count(True), cancel_list.count(False))
                            log.debug("Timer reached Timeout (inside results_to_check loop) for ThreadPool tracker checking - %s" % cancel_str)
                            break
                        if r['info_hash'] in already_checked_hashes:
                            continue
                        if r['info_hash'] == pool_result[0]: # same hash
                            already_checked_hashes.append(r['info_hash'])
                            parsed_seeds, parsed_peers = pool_result[1], pool_result[2]
                            r['seeds'] = parsed_seeds if (parsed_seeds and parsed_seeds > r['seeds']) else r['seeds']
                            r['peers'] = parsed_peers if (parsed_peers and parsed_peers > r['peers']) else r['peers']
                            missing_results -= 1

        if not payload['silent']: p_dialog.close()
        del p_dialog

        #xbmcgui.Dialog().ok('Checking Results', 'total %s\nnot br %s\nmissing %s' % (total_results, not_br_results, missing_results))

        if missing_results > 0:
            message = translation(32255) % missing_results
            if not payload['silent']: notify(message, ADDON_ICON)

    # rajada: add verified trackers to magnet link
    for r in filtered_results:
        if '(T)' in r['name']: r['uri'] = r['uri'] + TR_FULL_STR # ToDO: will fail for (S) and international links, why ?

	# rajada: priority to better similarity (color lime)
    #filtered_results.sort(key = lambda r: ( (abs(get_int(r['seeds'])) * 10) if "COLOR lime" in r['name'] else get_int(r['seeds']) ), reverse=True)
    return filtered_results
    # ToDo: not working as expected, as elementum daemon does the sorting
	# ToDo: ref: https://github.com/elgatito/elementum/blob/master/providers/search.go
	# ToDo: ref: https://github.com/elgatito/elementum/blob/master/providers/sort.go
    #return sorted(filtered_results, key = lambda r: ( (abs(get_int(r['seeds'])) * 10) if "COLOR lime" in r['name'] else get_int(r['seeds']) ), reverse=True)


def got_results(provider, results):
    """ Results callback once a provider found all its results, or not

    Args:
        provider (str): The provider ID
        results (list): The list of results
    """
    global provider_names
    global provider_results
    global available_providers
    definition = definitions[provider]
    definition = get_alias(definition, get_setting("%s_alias" % provider))

    # 0 "Resolution"
    # 1 "Seeds"
    # 2 "Size"
    # 3 "Balanced"

    #log.debug("[%s][got_results()] default %s results: [%s]" % (provider, len(results), results))

    if not sort_by or sort_by == 3 or sort_by > 3:
        # TODO: think of something interesting to balance sort results
        #sorted_results = sorted(results, key=lambda r: (nonesorter(r['sort_balance'])), reverse=True)
        sorted_results = sorted(results, key=lambda r: (nonesorter(r['sort_resolution'])), reverse=True) # rajada: assert sort by resolution
    if sort_by == 0:
        sorted_results = sorted(results, key=lambda r: (nonesorter(r['sort_resolution'])), reverse=True)
    elif sort_by == 1:
        sorted_results = sorted(results, key=lambda r: (nonesorter(r['seeds'])), reverse=True)
    elif sort_by == 2:
        sorted_results = sorted(results, key=lambda r: (nonesorter(r['size'])), reverse=True)

    #log.debug("[%s][got_results()] sorted %s results before cut: [%s]" % (provider, len(sorted_results), sorted_results))

    if len(sorted_results) > max_results:
        sorted_results = sorted_results[:max_results]

    #log.debug("[%s][got_results()] sorted %s results after cut: [%s]" % (provider, len(sorted_results), sorted_results))

    log.info("[%s] >> %s returned %2d results in %.1f seconds%s" % (
        provider, definition['name'].rjust(longest), len(results), round(time.time() - request_time, 2),
        (", sending %d best ones" % max_results) if len(results) > max_results else ""))

    #provider_results.extend(sorted_results)
    rcvd_results = results[:max_results] if len(results) > max_results else results
    provider_results.extend(rcvd_results) # rajada: send results at default order
    
    available_providers -= 1
    if definition['name'] in provider_names:
        provider_names.remove(definition['name'])

# rajada: function for similarity filter
def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()

# rajada: function for similarity filter
def clean_words(querywords): # ToDO: put these words at settings.xml
    words_to_remove = ['4k', '(4k)', 'dual', 'áudio', 'dublado', '720p', '1080p', '5.1', '7.1', 'web-dl', 'webdl', '2160p', 'download',
    'hd', 'bluray', 'hdcam', '3d', 'hsbs', 'torrent', 'blu-ray', 'rip', 'legendado', 'legenda', '/', '|', '-', '–', 'bd-r', '720p/1080p', 'bdrip', '(Blu-Ray)',
    '720p/1080p/4K', 'novela', 'seriado', 'full', 'hdr', 'h264', 'x264', 'sdr', 'x265', 'h265', '[Dublado', 'Portugues]', 'hdtc', 'ac-3', '720p/1080p/4k',
    'webrip', '10bit', 'hdr10plus', 'atmos', 'pt', 'br', '(bluray)', 'aac', 'ddp5', 'dd2', 'camrip', 'avc', 'dts-h', 'dts', '1080p/4k', 'audio',
    '5.1ch', 'remux', 'hevc', 'dts-hd', 'truehd', 'ma', '[1080p]', '[720p]', '[2160p]', 'hdts', 'amzn', 'dublagem',
    'trilogia', 'imax', 'remastered', '3d', 'stereoscopic', 'hdtv',
	'----------abaixo-stopwords-dos-releasers----------',
    'tpf', '1win', 'rarbg', '210gji', '(by-luanharper)', 'comando.to', 'bludv', '(torrentus', 'filmes)', 'andretpf', 'jef', 'derew', 'fgt', 'filmestorrent',
    'www', 'ThePirateFilmes',
	'------- adicionado 07.02.24 --------------------',
	'x264-rarbg', 'ddp', 'x264-cm', 'atmos-cm', 'r5', 'dv', 'mkv', 'eac3', 'dub-lapumia', 'x260bit',
	'(dual' , 'audio)', 'aud', '[blu', 'ray]', '[1080p', '3d]', '(dublado)', '[dual]', '[bluray]',
	'(720p)', '(1080p)', 'wolverdonfilmes', 'dvdrip', 'avi', 'xvid', 'brrip', 'x264-ion10', 'dat2014',
	'x265-rarbg', 'mp3-xvid', 'uhd', 'rmvb', 'ptbr', 'srt', 'pt-br', 'dub', '264', 'd4v1', 'dd', '[eztv]',
	'dual-baixarfilmesviatorrents', '(sd)', 'animestotais', 'dual-www', 'hmax', 'dual-cfhd', 'fullhd',
	'ddp-cm', '264-www', '-legendado-', 'vemtorrent', '900mb', '1600mb', '[yts', 'mx]', '800mb',
	'1400mb', 'repack', '8bit', 'comandotorrents', 'aac2', 'lapumia', 'ac3', 'encoder', 'extended',
	'x264-cinefile[rartv]', 'vers&atilde;o', 'ramontpb', '-dublado-dual-audio-', 'rapidotorrents',
	'vamostorrent', '480p', 'mp4', 'dublado-www', 'vacatorrent', 'comandotorrentshd', 'dual-wolverdonfilmes',
	'[dublado]', '[1080p-full]', '(2160p-4k)', 'dual-rk', 'ddp2', 'comoeubaixo', 'x264-extreme', 'bluray1080',
	'viatorrents', 'aac-rarbg', '[multi-audio]', '[multi-subs]', '[comandotorrents', 'com]', '[webrip]',
	'[dual', 'dsnp', 'up-beavery', '[bitsearch', 'to]', 'mpg', '(stereoscopic', 'dual-audio', '1080p[ultra',
	'hd]', 'x264-dual', '(720p', 'bluray)', '(1080p', 'hdts-rip', 'ts', 'fhdrip', 'hdrip', 'megafilmestorrent',
	'[extended]', '[repack]', '1080p_от', '[4k]', '[web]', 'rerip', 'dual-mld-acesse', 'wolverdon-filmes',
	'hybridrip', '[bluray', 'rip][ac3', '[hevc]', '-dublado-', 'torrentdosfilmes', '[720p][multiple',
	'[1080p][multiple', 'subtitle]', '950mb', '[2160p-4k]', 'dual-mld-bludv', '[bd', '1080p][hevc',
	'10bit][multi-audio][multi-subs]', '[480p][multiple'
	]
    treated_word = querywords.replace('+', ' ').replace('5.1','').replace('7.1','').replace('.',' ').replace("'","").replace(':','')
    resultwords  = [word for word in treated_word.split() if not word.lower() in words_to_remove]
    return ' '.join(resultwords)

def extract_torrents(provider, client):
    """ Main torrent extraction generator for non-API based providers

    Args:
        provider  (str): Provider ID
        client (Client): Client class instance

    Yields:
        tuple: A torrent result
    """
    definition = definitions[provider]
    definition = get_alias(definition, get_setting("%s_alias" % provider))
    log.debug("[%s] Extracting torrents from %s using definitions: %s" % (provider, provider, repr(definition)))

    global query_value_from_provider

    if not client.content:
        if debug_parser:
            log.debug("[%s] Parser debug | Page content is empty" % provider)

        raise StopIteration

    dom = Html().feed(client.content)

    id_search = get_search_query(definition, "id")
    key_search = get_search_query(definition, "key")
    row_search = get_search_query(definition, "row")
    name_search = get_search_query(definition, "name")
    torrent_search = get_search_query(definition, "torrent")
    info_hash_search = get_search_query(definition, "infohash")
    size_search = get_search_query(definition, "size")
    seeds_search = get_search_query(definition, "seeds")
    peers_search = get_search_query(definition, "peers")
    referer_search = get_search_query(definition, "referer")

    log.debug("[%s] Parser: %s" % (provider, repr(definition['parser'])))

    q = Queue()
    threads = []
    needs_subpage = 'subpage' in definition and definition['subpage']

    if needs_subpage:
        def extract_subpage(q, id, name, torrent, size, seeds, peers, info_hash, referer):
            try:
                log.debug("[%s] Getting subpage at %s" % (provider, repr(torrent)))
            except Exception as e:
                import traceback
                log.error("[%s] Subpage logging failed with: %s" % (provider, repr(e)))
                map(log.debug, traceback.format_exc().split("\n"))

            # New client instance, otherwise it's race conditions all over the place
            subclient = Client()
            subclient.passkey = client.passkey
            headers = {}

            if "subpage_mode" in definition:
                if definition["subpage_mode"] == "xhr":
                    headers['X-Requested-With'] = 'XMLHttpRequest'
                    headers['Content-Language'] = ''

            if referer:
                headers['Referer'] = referer

            uri = torrent.split('|')  # Split cookies for private trackers
            subclient.open(py2_encode(uri[0]), headers=headers)

            my_torrent_var = None # rajada

            if 'bittorrent' in subclient.headers.get('content-type', ''):
                log.debug('[%s] bittorrent content-type for %s' % (provider, repr(torrent)))
                if len(uri) > 1:  # Stick back cookies if needed
                    torrent = '%s|%s' % (torrent, uri[1])
            else:
                try:
                    torrent = extract_from_page(provider, subclient.content)
                    my_torrent_var = torrent
                    #if torrent and not torrent.startswith('magnet') and len(uri) > 1:  # Stick back cookies if needed
                    #    torrent = '%s|%s' % (torrent, uri[1])
                except Exception as e:
                    import traceback
                    log.error("[%s] Subpage extraction for %s failed with: %s" % (provider, repr(uri[0]), repr(e)))
                    map(log.debug, traceback.format_exc().split("\n"))

            #log.debug("[%s] Subpage torrent for %s: %s" % (provider, repr(uri[0]), torrent))
            torrent_counter = 1
            link_prefix = "[COLOR blue](Link " + str(torrent_counter) + ")[/COLOR] "
            t_name = "[COLOR blue](T)[/COLOR] "
            s_name = "[COLOR blue](S)[/COLOR] "
            similarity_color = (c_lime if c_lime in name else (c_green if c_green in name else (c_crimson if c_crimson in name else c_white)))
            
            if my_torrent_var: # rajada: check if is there any result
                #log.debug("[COLOR gold]%s - size %s parsed %s[/COLOR]" % (provider, size, repr([x[1] for x in my_torrent_var])))
                for torrent_item in my_torrent_var: # rajada: loop over all magnets
                    ret = None
                    size_item = size
                    if hasattr(torrent_item, '__iter__') and len(torrent_item) > 1: # check is iterable, as can be magnet or tuple
                        size_item = torrent_item[1]
                        torrent_item = torrent_item[0]

                    magnet_name = re.findall(r'[?&(&amp;)]dn=([^&]+).*', torrent_item) # r'&dn=(.*?)&'
                    infohash_regex = re.findall(r'urn:btih:([a-zA-Z0-9]+).*', torrent_item)
                    infohash_value = infohash_regex[0] if infohash_regex else info_hash
                    torrent_name = unquote(magnet_name[0]) if len(magnet_name) >= 1 else unquote(name)

                    if len(magnet_name) >= 1: ret = (id, t_name + similarity_color + unquote(magnet_name[0]) + c_close, infohash_value, torrent_item, size_item, seeds, peers)
                    else: ret = (id, s_name + name, infohash_value, torrent_item, size_item, seeds, peers) # name already come with color tag
                    
                    # Cache this subpage result if another query would need to request same url.
                    provider_cache[uri[0]] = torrent_item
                    if use_similarity_filter:
                        # remove color tag from name, when (S)
                        parsed_torrent_name = re.sub(r'\[\/color\]', '', torrent_name, flags=re.IGNORECASE)
                        parsed_torrent_name = re.sub(r'\[color[\sa-zA-Z0-9]*\]', '', parsed_torrent_name, flags=re.IGNORECASE)
                        parsed_torrent_name = parsed_torrent_name.replace('(S)', '')
                        # similarity check
                        similarity_value = similar(clean_words(query_value_from_provider).lower(), clean_words(unquote(parsed_torrent_name)).lower())
                        expected_value = sim_filter_minimum - sim_filter_tolerance
                        if similarity_value >= expected_value:
                            log.debug("[%s] Parser debug | Aceito pelo similarity filter 2 com valor %s (exigido %s) | Query: %s | Nome: %s" % (provider, similarity_value, expected_value, clean_words(query_value_from_provider), clean_words(unquote(parsed_torrent_name))))

							# update name to debug
                            #name_color = (c_lime if similarity_value >= sim_filter_good-sim_filter_tolerance else (c_green if similarity_value >= sim_filter_acceptable-sim_filter_tolerance else (c_crimson if similarity_value >= sim_filter_minimum-sim_filter_tolerance else c_white)))
                            #ret_list = list(ret)
                            #ret_list[1] = ret_list[1] + name_color + (" ({}%)".format(similarity_value * 100)) + c_close
                            #ret = tuple(ret_list)
                            
                            q.put_nowait(ret)
                        else:
                            log.debug("[%s] Parser debug | Bloqueado pelo similarity filter 2 com valor %s (exigido %s) | Query: %s | Nome: %s" % (provider, similarity_value, expected_value, clean_words(query_value_from_provider), clean_words(unquote(parsed_torrent_name))))
                    else:
                        q.put_nowait(ret)
                    
                    torrent_counter += 1
                    log.debug("[%s] Subpage torrent with name (%s) and size (%s) for %s: %s" % (provider, ret[1], size_item, repr(uri[0]), torrent_item))
                torrent_counter = 1
            #xbmc.log('Magnet links for %s: %s' % (provider, my_torrent_var), level=xbmc.LOGINFO) #ref: https://kodi.wiki/view/Log_file/Advanced

    if not dom:
        if debug_parser:
            log.debug("[%s] Parser debug | Could not parse DOM from page content" % provider)

        raise StopIteration

    #if debug_parser:
    #    log.debug("[%s] Parser debug | Page content: %s" % (provider, client.content.replace('\r', '').replace('\n', ''))) # rajada: large output, removed

    key = eval(key_search) if key_search else ""
    if key_search and debug_parser:
        key_str = key.__str__()
        log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'key', key_search, key_str.replace('\r', '').replace('\n', '')))

    items = eval(row_search)
    if debug_parser:
        log.debug("[%s] Parser debug | Matched %d items for '%s' query '%s'" % (provider, len(items), 'row', row_search))

    for item in items:
        #if debug_parser:
        #    item_str = item.__str__()
        #    log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'row', row_search, item_str.replace('\r', '').replace('\n', ''))) # rajada: large output, removed

        if not item:
            continue

        # rajada: pre process name
        name = eval(name_search) if name_search else ""

        # rajada: Check blocked terms at all result names (instead only at release, avoid subpage loading of spam posts)
        must_continue = None
        blocked_terms = []
        if use_additional_filters:
            if use_block:
                block = re.split(r',\s?', use_block)
                blocked_terms.extend(block)
                must_continue = any(x in name.lower() for x in blocked_terms)
        if must_continue:
            log.debug("[%s] Parser debug | Bloqueado devido blocked terms: [%s]" % (provider, name.replace('\r', '').replace('\n', '')))
            continue

        # rajada: Check name similarity with query (due to spam incoming from search mechanisms)
        if query_value_from_provider: # check if query is None
            log.debug("[%s] Parser debug | (similarity) New query_value_from_provider: %s" % (provider, query_value_from_provider))
        else: log.debug("[%s] Parser debug | (similarity) No query_value_from_provider" % (provider))
        
        similarity_value = 0 # declared here to avoid 'referenced before assignment' error
        if use_similarity_filter:
            log.debug("[%s] Parser debug | busca com similaridade (pesos %s | %s | %s) por %s" % (provider, sim_filter_minimum, sim_filter_acceptable, sim_filter_good, name))
        
            similarity_value = similar(clean_words(query_value_from_provider).lower(), clean_words(unquote(name)).lower())
            expected_value = sim_filter_minimum
            if similarity_value >= expected_value:
                log.debug("[%s] Parser debug | Aceito pelo similarity filter com valor %s (exigido %s) | Query: %s | Nome: %s" % (provider, similarity_value, expected_value, clean_words(query_value_from_provider), clean_words(unquote(name))))
            else:
                log.debug("[%s] Parser debug | Bloqueado pelo similarity filter com valor %s (exigido %s) | Query: %s | Nome: %s" % (provider, similarity_value, expected_value, clean_words(query_value_from_provider), clean_words(unquote(name))))
                continue
        else: log.debug("[%s] Parser debug | busca sem similaridade (pesos %s | %s | %s) por %s" % (provider, sim_filter_minimum, sim_filter_acceptable, sim_filter_good, name))

        name_color = (c_lime if similarity_value >= sim_filter_good else (c_green if similarity_value >= sim_filter_acceptable else (c_crimson if similarity_value >= sim_filter_minimum else c_white)))
        name = name_color + name + c_close # this color will only work if (S), otherwise need check at extract_subpage method
        log.debug("[%s] Parser debug | Nome com similaridade: %s" % (provider, name))

        try:
            id = eval(id_search) if id_search else ""
            # rajada: colored name according to similarity value
            torrent = eval(torrent_search) if torrent_search else ""
            size = eval(size_search) if size_search else ""
            seeds = eval(seeds_search) if seeds_search else ""
            peers = eval(peers_search) if peers_search else ""
            info_hash = eval(info_hash_search) if info_hash_search else ""
            referer = eval(referer_search) if referer_search else ""

            if 'magnet:?' in torrent:
                torrent = torrent[torrent.find('magnet:?'):]

            if debug_parser:
                log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'id', id_search, id))
                log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'name', name_search, name))
                log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'torrent', torrent_search, torrent))
                log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'size', size_search, size))
                log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'seeds', seeds_search, seeds))
                log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'peers', peers_search, peers))
                if info_hash_search:
                    log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'info_hash', info_hash_search, info_hash))
                if referer_search:
                    log.debug("[%s] Parser debug | Matched '%s' iteration for query '%s': %s" % (provider, 'info_hash', referer_search, referer))

            # Pass client cookies with torrent if private
            if not torrent.startswith('magnet'):
                user_agent = USER_AGENT

                if client.passkey:
                    torrent = torrent.replace('PASSKEY', client.passkey)
                elif client.token:
                    headers = {'Authorization': client.token, 'User-Agent': user_agent}
                    log.debug("[%s] Appending headers: %s" % (provider, repr(headers)))
                    torrent = append_headers(torrent, headers)
                    log.debug("[%s] Torrent with headers: %s" % (provider, repr(torrent)))
                else:
                    parsed_url = urlparse(torrent.split('|')[0])
                    cookie_domain = '{uri.netloc}'.format(uri=parsed_url)
                    cookie_domain = re.sub('www\d*\.', '', cookie_domain)
                    cookies = []
                    for cookie in client._cookies:
                        if cookie_domain in cookie.domain:
                            cookies.append(cookie)
                    headers = {}
                    if cookies:
                        headers = {'User-Agent': user_agent}
                        if client.request_headers:
                            headers.update(client.request_headers)
                        if client.url:
                            headers['Referer'] = client.url
                            headers['Origin'] = client.url
                        # Need to set Cookie afterwards to avoid rewriting it with session Cookies
                        headers['Cookie'] = ";".join(["%s=%s" % (c.name, c.value) for c in cookies])
                    else:
                        headers = {'User-Agent': user_agent}

                    torrent = append_headers(torrent, headers)

            if name and torrent and needs_subpage and not torrent.startswith('magnet'):
                if not torrent.startswith('http'):
                    torrent = definition['root_url'] + py2_encode(torrent)
                # Check if this url was previously requested, to avoid doing same job again.
                uri = torrent.split('|')
                if uri and uri[0] and uri[0] in provider_cache and provider_cache[uri[0]]:
                    yield (id, name, info_hash, provider_cache[uri[0]], size, seeds, peers)
                    continue

                t = Thread(target=extract_subpage, args=(q, id, name, torrent, size, seeds, peers, info_hash, referer))
                threads.append(t)
            else:
                yield (id, name, info_hash, torrent, size, seeds, peers)
        except Exception as e:
            log.error("[%s] Got an exception while parsing results: %s | Stack %s" % (provider, repr(e), traceback.format_exc()))

    if needs_subpage:
        log.debug("[%s] Starting subpage threads..." % provider)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i in range(q.qsize()):
            ret = q.get_nowait()
            log.debug("[%s] Queue %d got: %s" % (provider, i, repr(ret)))
            yield ret

    # Save cookies in cookie jar
    client.save_cookies()

def extract_from_api(provider, client):
    """ Main API parsing generator for API-based providers

    An almost clever API parser, mostly just for YTS, RARBG and T411

    Args:
        provider  (str): Provider ID
        client (Client): Client class instance

    Yields:
        tuple: A torrent result
    """
    try:
        data = json.loads(client.content)
    except:
        data = []
    log.debug("[%s] JSON response from API: %s" % (unquote(provider), repr(data)))

    definition = definitions[provider]
    definition = get_alias(definition, get_setting("%s_alias" % provider))
    api_format = definition['api_format']

    results = []
    # If 'results' is empty - then we can try to take all the data as an array of results.
    # Usable when api returns results without any other data.
    if not api_format['results']:
        results = data
    else:
        result_keys = api_format['results'].split('.')
        log.debug("[%s] result_keys: %s" % (provider, repr(result_keys)))
        for key in result_keys:
            if key in data:
                data = data[key]
            else:
                data = []
        results = data
    log.debug("[%s] results: %s" % (provider, repr(results)))

    if 'subresults' in api_format:
        from copy import deepcopy
        for result in results:  # A little too specific to YTS but who cares...
            result['name'] = result[api_format['name']]
        subresults = []
        subresults_keys = api_format['subresults'].split('.')
        for key in subresults_keys:
            for result in results:
                if key in result:
                    for subresult in result[key]:
                        sub = deepcopy(result)
                        sub.update(subresult)
                        subresults.append(sub)
        results = subresults
        log.debug("[%s] with subresults: %s" % (provider, repr(results)))

    for result in results:
        if not result or not isinstance(result, dict):
            continue
        id = ''
        name = ''
        info_hash = ''
        torrent = ''
        size = ''
        seeds = ''
        peers = ''
        if 'id' in api_format:
            id = result[api_format['id']]
        if 'name' in api_format:
            name = result[api_format['name']]
        if 'description' in api_format:
            if name:
                name += ' '
            name += result[api_format['description']]
        if 'torrent' in api_format:
            torrent = result[api_format['torrent']]
            if 'download_path' in definition:
                torrent = definition['download_path'] + torrent
            if client.token:
                user_agent = USER_AGENT
                headers = {'Authorization': client.token, 'User-Agent': user_agent}
                log.debug("[%s] Appending headers: %s" % (provider, repr(headers)))
                torrent = append_headers(torrent, headers)
                log.debug("[%s] Torrent with headers: %s" % (provider, repr(torrent)))
        if 'info_hash' in api_format:
            info_hash = result[api_format['info_hash']]
        if 'quality' in api_format:  # Again quite specific to YTS...
            name = "%s - %s" % (name, result[api_format['quality']])
        if 'size' in api_format:
            size = result[api_format['size']]
            if isinstance(size, (long, int)):
                size = sizeof(size)
            elif isinstance(size, basestring) and size.isdigit():
                size = sizeof(int(size))
        if 'seeds' in api_format:
            seeds = result[api_format['seeds']]
            if isinstance(seeds, basestring) and seeds.isdigit():
                seeds = int(seeds)
        if 'peers' in api_format:
            peers = result[api_format['peers']]
            if isinstance(peers, basestring) and peers.isdigit():
                peers = int(peers)
        yield (id, name, info_hash, torrent, size, seeds, peers)


def extract_from_page(provider, content):
    """ Sub-page extraction method

    Args:
        provider (str): Provider ID
        content  (str): Page content from Client instance

    Returns:
        str: Torrent or magnet link extracted from sub-page
    """
    definition = definitions[provider]
    definition = get_alias(definition, get_setting("%s_alias" % provider))

    try:
        matches = re.findall(r'magnet:\?[^\'"\s<>\[\]]+', content)
        if matches:
            #result = matches # rajada: return all results
            log.debug('[%s] Matched magnet link: %s' % (provider, repr(matches)))
            try:
                sizes = re.findall(r'[0-9]+(?:\.[0-9]{1,2})?\s(?:[GM]B|[gm]b)', content)
                if len(sizes) >= len(matches):
                    sizes = sizes[len(sizes) - len(matches):]
                else:
                    sizes = ['' for x in matches]
                result = zip(matches, sizes)
            except Exception as e:
                sizes = ['' for x in matches]
                result = zip(matches, sizes)
            return result

        matches = re.findall('http(.*?).torrent["\']', content)
        if matches:
            result = 'http' + matches[0] + '.torrent'
            result = result.replace('torcache.net', 'itorrents.org')
            log.debug('[%s] Matched torrent link: %s' % (provider, repr(result)))
            return result

        matches = re.findall('http(.*?)/download/.torrent["\']', content)
        if matches:
            result = 'http' + matches[0] + '.torrent'
            result = result.replace('torcache.net', 'itorrents.org')
            log.debug('[%s] Matched torrent link: %s' % (provider, repr(result)))
            return result

        matches = re.findall('/download\?token=[A-Za-z0-9%]+', content)
        if matches:
            result = definition['root_url'] + matches[0]
            log.debug('[%s] Matched download link with token: %s' % (provider, repr(result)))
            return result

        matches = re.findall('"(/download/[A-Za-z0-9]+)"', content)
        if matches:
            result = definition['root_url'] + matches[0]
            log.debug('[%s] Matched download link: %s' % (provider, repr(result)))
            return result

        matches = re.findall('/torrents/download/\?id=[a-z0-9-_.]+', content)  # t411
        if matches:
            result = definition['root_url'] + matches[0]
            log.debug('[%s] Matched download link with an ID: %s' % (provider, repr(result)))
            return result

        matches = re.findall('\: ([A-Fa-f0-9]{40})', content)
        if matches:
            result = "magnet:?xt=urn:btih:" + matches[0]
            log.debug('[%s] Matched magnet info_hash search: %s' % (provider, repr(result)))
            return result

        matches = re.findall('/download.php\?id=([A-Za-z0-9]{40})\W', content)
        if matches:
            result = "magnet:?xt=urn:btih:" + matches[0]
            log.debug('[%s] Matched download link: %s' % (provider, repr(result)))
            return result

        matches = re.findall('(/download.php\?id=[A-Za-z0-9]+[^\s\'"]*)', content)
        if matches:
            result = definition['root_url'] + matches[0]
            log.debug('[%s] Matched download link: %s' % (provider, repr(result)))
            return result
    except:
        pass

    return None


def run_provider(provider, payload, method, start_time, timeout):
    """ Provider thread entrypoint

    Args:
        provider   (str): Provider ID
        payload   (dict): Search payload from Elementum
        method     (str): Type of search, can be ``general``, ``movie``, ``show``, ``season`` or ``anime``
        start_time (int): Time when search has been started
        timeout    (int): Time limit for searching
    """
    log.debug("[%s] Processing %s with %s method" % (provider, provider, method))

    filterInstance = Filtering()

    # collect languages, defined for this provider
    filterInstance.define_languages(provider)

    if method == 'movie':
        filterInstance.use_movie(provider, payload)
    elif method == 'season':
        filterInstance.use_season(provider, payload)
    elif method == 'episode':
        filterInstance.use_episode(provider, payload)
    elif method == 'anime':
        filterInstance.use_anime(provider, payload)
    else:
        filterInstance.use_general(provider, payload)

    if 'is_api' in definitions[provider]:
        results = process(provider=provider, generator=extract_from_api, filtering=filterInstance, has_special=payload['has_special'], skip_auth=payload['skip_auth'], start_time=start_time, timeout=timeout)
    else:
        results = process(provider=provider, generator=extract_torrents, filtering=filterInstance, has_special=payload['has_special'], skip_auth=payload['skip_auth'], start_time=start_time, timeout=timeout)

    #log.debug("[%s] run_provider() | Query: %s" % (provider, any_var_name)) # rajada: get query here is ok, but we have problem with multiple tasks writing it to global var (race condition)

    # Cleanup results from duplcates before limiting each provider's results.
    results = cleanup_results(results)
    got_results(provider, results)

def get_search_query(definition, key):
    if 'parser' not in definition or key not in definition['parser']:
        return ""

    if key == 'key' or key == 'table' or key == 'row':
        return "dom." + definition['parser'][key]
    return definition['parser'][key]

def nonesorter(a):
    if not a:
        return ""
    return a
