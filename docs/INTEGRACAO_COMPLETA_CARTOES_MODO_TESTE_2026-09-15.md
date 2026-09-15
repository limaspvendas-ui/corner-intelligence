# Integração Completa — APIs/Fontes Existentes + Cartões em MODO TESTE

**Data:** 2026-09-15
**Branch:** `etapa-integracao-completa-cartoes` → merge FF em `main`
**Tag:** `v1.3.0-cartoes-modo-teste` (nova; `v1.0.0`, `v1.1.0` e `v1.2.0` intocadas)
**Base:** `ead5df0` (v1.2.0-chatgpt-integrado)
**Camada:** `operacional-1.2-cards-teste` | **Motor:** `prejogo-op-1.0-observacao` (INTACTO)

## 1. Objetivo

Consolidar TODAS as APIs/fontes/módulos já existentes no fluxo oficial,
preservar o motor estatístico intacto, habilitar CARTÕES operacionalmente
em **MODO TESTE** por override expressamente autorizado pelo usuário
(15/09/2026) e completar a camada MCP com exatamente as 9 ferramentas
oficiais.

## 2. Fluxo preservado (inalterado em estrutura)

```
FONTES REAIS (API-Football v3 + 6 provedores multifonte)
        ↓
BACKEND CORNER INTELLIGENCE (src/)
        ↓
MOTOR OFICIAL (prejogo_opportunity / live_opportunity)  ← INTACTO
        ↓
SaidaOficial (src/operacional.py)
        ↓
API HTTP (src/api_server.py)   ← camada FINA de acesso
        ↓
MCP (src/mcp_server.py)        ← camada FINA de acesso (delega à API)
        ↓
CHATGPT (consulta/apresenta; não recalcula)
```

## 3. Auditoria — APIs/fontes encontradas e status

| Fonte | Função | Status |
|---|---|---|
| **API-Football Pro v3** (api-sports.io) | Fonte primária: fixtures, ligas, standings, estatísticas, eventos, lineups, jogadores, gols, escanteios, cartões (amarelos por evento; vermelhos = limitação da fonte), PRE-LIVE, LIVE | **ATIVA** (integração preservada; chave/plano inalterados) |
| **The Odds API** | Odds reais (h2h/totals/spreads) para coleta prospectiva + resolução factual | **PREPARADA** — conectada a `src/odds_coleta.py`/`src/resolucao_factual.py` (separada do motor POR PROJETO); credencial válida auditada em 5F-D2 |
| **5DollarFootball** | Odds corners/cards (opening/closing/inplay, Bet365) | **PREPARADA** — conectada; parser corrigido em 5F-F |
| **apifootball.com** | Fallback factual (red cards, fixtures, odds) | **PREPARADA** — conectada; credencial válida |
| **SportMonks** | Fallback/odds | **PREPARADA** — conectada; credencial inválida no ambiente (PENDÊNCIA de credencial, não de código) |
| **football-data.org** | Fixtures/score/status | **PREPARADA** — conectada; credencial retestada OK em 5F-D2C |
| **StatsBomb OpenData** | Dados históricos abertos | **PREPARADA** — conectada |

As fontes de odds/resolução factual são deliberadamente **separadas do
motor** (decisão auditada do projeto: COLETAR/RESOLVER/REGISTRAR, sem
alterar decisão). Nenhuma nova API foi adicionada; nenhum custo gerado.

## 4. Desconexões reais encontradas e corrigidas

1. **CARTÕES desconectados do motor na camada operacional.**
   `analisar_fixture` chamava `scan_pregame_opportunities(...,
   incluir_cartoes=False)` — o motor de cartões (`src/cartoes.py`,
   validado, Poisson 90 min, amarelo=1/vermelho=2, média da liga sobre
   TODAS as partidas encerradas) estava implementado mas nunca invocado
   pelo fluxo oficial. **Correção:** `incluir_cartoes=True` na chamada
   oficial. Nenhuma fórmula/threshold/calibração foi tocada.
2. **Camada MCP ausente do fluxo oficial.** O servidor MCP existia só no
   backend antigo (`origin/backup-remote-main-2026-09-14`), que NÃO usava
   o motor novo (proxy direto à API-Football). As ferramentas 8 e 9
   (`analisar_dados_da_partida`, `testar_coleta_automatica`) existiam
   apenas lá, apontando para endpoints que não existem no fluxo oficial.
   **Correção:** `src/mcp_server.py` novo — 9 ferramentas oficiais
   delegando à API oficial; ferramentas 8 e 9 reimplementadas como
   endpoints FACTUAIS da API oficial (`/api/partida-dados`,
   `/api/teste-coleta`) consumidos pelo MCP.
3. **Fragilidade de import circular no mount do MCP** (corrigida em
   revisão): `api_server._montar_mcp()` importava `mcp_server` ainda
   parcialmente inicializado quando `mcp_server` era importado primeiro —
   o mount era silenciosamente ignorado (404 em `/mcp`). Correção:
   acesso tardio `_api()` em `mcp_server.py`; mount robusto a qualquer
   ordem de import.

## 5. CARTÕES — MODO TESTE (override autorizado, não aprovação estatística)

**Autorização expressa do usuário: 15/09/2026.**

| Dimensão | Valor |
|---|---|
| STATUS ESTATÍSTICO | `NÃO_AVALIÁVEL` — **preservado** (limitação estrutural da fonte: Red Cards `null` em 207/207 jogos da amostra 5D; Etapa 5D) |
| STATUS OPERACIONAL | `HABILITADO_EM_MODO_TESTE_POR_OVERRIDE_DO_USUARIO` |
| Rótulo | `MODO TESTE — OVERRIDE AUTORIZADO PELO USUÁRIO — NÃO VALIDADO ESTATISTICAMENTE` |
| Origem | `override_usuario` (decisão humana registrada, com timestamp e motivo) |

**O override NÃO:** cria sinal (só avaliações `aprovada_motor=True` viram
oportunidade operacional; sem sinal → `OBSERVACAO`/`SEM OPORTUNIDADE`),
inventa linha/probabilidade/confiança, reduz threshold, eleva confiança,
altera fórmula/calibração ou promove CARTÕES a mercado aprovado.

**Dados disponíveis na fonte (cartões):** amarelos por equipe e por jogo
(histórico e LIVE), vermelhos por equipe (quando a fonte fornece), pontos
de disciplina (amarelo=1, vermelho=2), média da liga sobre partidas
encerradas, por tempo (1ºT/2ºT quando a fonte fornece), eventos
disciplinares por jogador (endpoint de eventos). **Dados ausentes
(preservados como NULL, nunca preenchidos):** Red Cards nulos em grande
parte do histórico da fonte primária; minuto dos cartões no LIVE quando o
evento não traz o dado; árbitro quando a fonte não fornece. A ausência é
reportada como "dado não disponível na fonte".

## 6. Mercados — status consolidado (após esta macroetapa)

| Mercado | Estatístico | Operacional |
|---|---|---|
| GOALS | `APROVADO_PARA_PROXIMA_FASE` | ATIVO (estatístico; inalterado) |
| CORNERS | `EM_OBSERVAÇÃO` (drift) | HABILITADO por override (modo teste `false` — operacional pleno; rótulo de observação estatística preservado) |
| CARDS | `NÃO_AVALIÁVEL` | **HABILITADO EM MODO TESTE por override autorizado do usuário** |
| RESULTADO | `EM_OBSERVAÇÃO` (experimental) | Observação |
| LIVE (pressão) | `BLOQUEADO` | MODO TESTE por override de MODO (inalterado nesta etapa) |
| ODDS_ROI | `BLOQUEADO` | Bloqueado |

## 7. MCP — exatamente 9 ferramentas

`verificar_status`, `buscar_jogos_do_dia`, `varredura_prelive`,
`analisar_partida_prelive`, `varredura_live`, `analisar_partida_live`,
`obter_status_mercados`, `analisar_dados_da_partida`,
`testar_coleta_automatica` — nenhuma removida, nenhuma extra.

- Montado no MESMO serviço (`/mcp` via `app.mount` após as rotas da API;
  `/health` e `/api/*` têm precedência). Nenhum serviço novo.
- `mcp>=1.2,<2` fixado (a v2 renomeou FastMCP; quebraria o servidor).
- MCP é camada de acesso: delega à API oficial; **nenhum cálculo
  estatístico vive no MCP** (verificado por teste de delegação pura).

## 8. Alterações nos arquivos

| Arquivo | Alteração |
|---|---|
| `src/operacional.py` | Override `cartoes` (modo teste) em `OVERRIDE_OPERACIONAL`; `modo_teste`/`rotulo_override` emitidos em SaidaOficial (pre-live + live); `incluir_cartoes=True` na chamada oficial do motor; rótulo `TESTE-OVERRIDE` na formatação; versão `operacional-1.2-cards-teste` |
| `src/api_server.py` | Lifespan do MCP; mount `/mcp`; `cards` no `/health`; campos `modo_teste`/`rotulo_override` em status-mercados; `oportunidades_cartoes` na varredura; novos endpoints 8 (`/api/partida-dados`) e 9 (`/api/teste-coleta`), ambos FACTUAIS |
| `src/mcp_server.py` | **NOVO** — servidor MCP oficial (9 ferramentas; acesso tardio à API; streamable-http stateless) |
| `requirements.txt` | `mcp>=1.2,<2` |
| `tests/test_operacional.py` | Testes E–E4 do modo teste de cartões (override não cria sinal; status estatístico preservado; rotulação exata) |
| `tests/test_api_server.py` | Testes N–Q2 (status mercados, exposição de cartões na varredura, endpoints factuais sem probabilidade, NULL preservado) |
| `tests/test_mcp_server.py` | **NOVO** — contrato das 9 ferramentas, delegação pura, `/mcp` montado (tools/list + tools/call), API continua no ar |
| `tests/test_live_override.py` | Asserção da versão da camada atualizada (`operacional-1.2-cards-teste`); motor `prejogo-op-1.0-observacao` intacto verificado |

**Motor:** nenhuma fórmula, threshold, peso, calibração ou algoritmo
alterado (diff vazio nos 13 módulos do motor; testes de integridade do
motor passam). **Histórico/banco:** intacto — nenhuma previsão antiga
alterada, nenhum registro apagado; novos registros de teste carregam
`modo_teste`/rótulo.

## 9. Testes

Suíte completa: **764 passed, 18 skipped, 0 failed** (baseline da
macroetapa anterior: 742/18/0). Novos: 4 (operacional E–E4) + 7 (API
N–Q2) + 13 (MCP) + 2 ajustes.

## 10. Validação real executada

- `tools/list` no `/mcp` montado: HTTP 200, exatamente as 9 ferramentas
  oficiais; `tools/call verificar_status`: 200 com
  `prelive.cards = {status: NÃO_AVALIÁVEL, origem: override_usuario,
  modo_teste: true}`.
- Varredura PRE-LIVE real (2026-09-15) processando GOLS + ESCANTEIOS +
  CARTÕES: ver `data/varredura_prelive_2026-09-15.json` (comprovante em
  repositório local de execução).
- Ausência de oportunidade é resultado válido: nenhuma aposta artificial.

## 11. Deploy

`render.yaml` (autoDeploy a partir de `main`): o serviço único existente
sobe a API + MCP no mesmo processo (`/health` e `/mcp`). **PENDÊNCIA
REAL:** este ambiente não possui credenciais Render — o deploy remoto e
a validação remota pós-push não puderam ser executados daqui; validar
manualmente `https://corner-intelligence.onrender.com/health` e
`POST /mcp` (tools/list) após o autoDeploy.

## 12. Proibições respeitadas

Nenhum segundo motor; nenhuma fórmula/threshold/calibração alterada;
nenhuma API nova/contratada/custo; nenhuma troca de chave/fornecedor;
nenhum dado inventado (NULL permanece NULL); nenhum histórico apagado ou
previsão antiga sobrescrita; MCP sem cálculo estatístico; LIVE
preservado em MODO TESTE.