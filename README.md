# Elementum Rajada

Uma rajada de provedores.

---

### Funcionalidade

- Rápido
- Provedores brasileiros e estrangeiros
- Fácil configuração de provedores e filtros
- Compatível com Elementum
- Utilizável em outros addons através do [menu de contexto](https://github.com/addon-rajada/context.elementum) do Elementum

---

### Instalação

**Importante: Caso já tenha alguma versão do Rajada instalada, recomendado desinstalar a mesma e reiniciar o Kodi antes de prosseguir**

Adicionar o repositório https://addon-rajada.github.io e instalar o addon.

Mais detalhes [aqui](https://github.com/addon-rajada/addon-rajada.github.io).

---

### Compatibilidade

Abaixo os casos de teste realizados pelo desenvolvedor. Outras versões necessário o usuário verificar por si.

| **Kodi** | **Elementum** | **Status Repo** | **Status Rajada** |
|-|-|-|-|
| 17.6 | 0.1.87 arm | Fail | Fail |
| 18.6 | 0.1.87 x86 | Ok | Ok |
|      | 0.1.94 x86 | Ok | Ok |
| 18.9 | 0.1.87 arm | Ok | Ok |
| 19.5 | 0.1.87 arm | Ok | Ok |
| 20.1 | 0.1.87 x86 | Ok | Ok |
|      | 0.1.94 x86 | Ok | Ok |
|      | 0.1.98 x86 | Ok | Ok |
| 20.2 | 0.1.87 x86 | Ok | Ok |
| 21.0 ALPHA 2 | 0.1.87 x86 | Ok | Ok |
| 21.0 BETA 3 | 0.1.99 arm | Ok | Ok |
|      | 0.1.99 arm64 | Ok | Ok |

---

### Documentação

- [Guia para adição de provedores](https://elementumorg.github.io/burst/create/)
- [Documentação do Burst](https://readthedocs.org/projects/scriptelementumburst/downloads/pdf/latest/)

---

### Changelog

**0.5**

- provedores atualizados
- filtro de similaridade melhorado
- traduções atualizadas
- obtendo tamanho dos magnets via regex quando possível
- nova ação para atualizar manualmente os provedores (settings.xml e providers.json)
- atualizado script para verificar resultados dos provedores
- resolvido bug de conexão do socket
- resolvido bug de números negativos no contador de links
- resolvido bug de timeout (nenhum link encontrado) para max_results muito alto

**0.4**

- provedores atualizados
- script para verificar resultados dos provedores

**0.3**

- provedores atualizados
- busca com título pt-BR e fallback com título original
- aplicando filtro de similaridade nos nomes dos links magnéticos da subpage
- opção para exibir ou esconder nomes dos provedores brasileiros na tela de resultados
- verificação multithread (python 3) e sequencial (python 2) do número de seeders e leechers via tracker UDP (não considera DHT)
- ajuste de timeout do elementum

**0.2**

- adicionado provedores
- obtendo nome e hash a partir do link magnético
- melhorado filtro de termos bloqueados para evitar carregamento inútil da subpage
- adicionado filtro de similaridade de strings
- tamanho do addon reduzido

**0.1**

- provedores brasileiros
- parsing de todos os links magnéticos da subpage

---

### Créditos

- @scakemyer pelo módulo inicial do Quasar Burst
- @mancuniancol por seu trabalho no Magnetic
- @elgatito pelo módulo [Elementum Burst](https://github.com/elgatito/script.elementum.burst)
- @icarok99 pela ajuda com os provedores
