# Integração Corner Intelligence × ChatGPT — API Oficial (MCP/HTTP)

**Data:** 2026-09-14
**Branch:** `etapa-integracao-chatgpt-final` → merge FF em `main`
**Tag:** `v1.2.0-chatgpt-integrado` (nova; `v1.0.0-operacional` e `v1.1.0-live-teste` intocadas)
**Base:** `e5834a5` (v1.1.0-live-teste)

## 1. Objetivo

Integrar o Corner Intelligence ao ChatGPT através de uma camada API/MCP
oficial, **sem duplicar o motor, sem alterar modelos estatísticos e sem
modificar regras de aposta**. O ChatGPT consulta exatamente a mesma verdade
operacional usada pelo Claude Code.

## 2. Princípio fundamental

ChatGPT **NÃO** é um segundo motor.

```
FONTES REAIS (API-Football v3)
        ↓
BACKEND CORNER INTELLIGENCE (src/)
        ↓
MOTOR OFICIAL (prejogo_opportunity / live_opportunity)
        ↓
SaidaOficial (src/operacional.py)
        ↓
API HTTP (src/api_server.py)   ← camada FINA de acesso
        ↓
CHATGPT (consulta/apresenta; não recalcula)
```

O ChatGPT apenas: consulta, organiza, explica, compara, apresenta.
O ChatGPT **NÃO** pode: recalcular probabilidade, inventar probabilidade,
mudar ENTRAR↔NÃO-ENTRAR, criar aposta, substituir o motor.

## 3. Auditoria da integração existente (Passo 1)

- **Main atual era CLI-only.** Nenhum MCP/HTTP backend na linha ativa.
- Existia um backend antigo (Flask + FastMCP + openapi.json) preservado em
  `origin/backup-remote-main-2026-09-14`, deployado em
  `corner-intelligence.onrender.com` (2026-09-12).
- **Esse backend antigo NÃO usava o motor novo** — fazia proxy direto à
  API-Football (anterior ao baseline/motor/operacional/SaidaOficial).
- Decisão: **construir novo** (`src/api_server.py`, FastAPI) — camada fina
  sobre `SaidaOficial`. Reaproveitar o *padrão* (PORT env, /health,
  /openapi.json, Render), **não** a lógica antiga (que bypassa o motor).

## 4. Arquitetura do servidor

`src/api_server.py` — FastAPI. Cada endpoint consome uma função pública da
camada operacional e serializa o resultado em JSON. Nenhuma
probabilidade/confiança/linha é recalculada; nenhuma decisão é alterada.

| Endpoint (HTTP GET) | Função operacional consumida | Tool MCP |
|---|---|---|
| `/health`, `/api/status` | `_projeto_hash`, `_db_status`, `STATUS_MERCADOS`, `MODO_TESTE_LIVE` | `verificar_status` |
| `/api/jogos-do-dia?date=` | `get_fixtures_today` + `jogo_elegivel` | `buscar_jogos_do_dia` |
| `/api/varredura-prelive?date=` | `varredura_data` | `varredura_prelive` |
| `/api/partida-prelive?fixture_id=` | `get_fixture_by_id` + `analisar_fixture` | `analisar_partida_prelive` |
| `/api/varredura-live` | `varredura_live` | `varredura_live` |
| `/api/partida-live?fixture_id=` | `fetch_live_snapshot` + `varredura_live` | `analisar_partida_live` |
| `/api/status-mercados` | `STATUS_MERCADOS` + `OVERRIDE_OPERACIONAL` + `MODO_TESTE_LIVE` | `obter_status_mercados` |

- `/openapi.json` — schema OpenAPI auto-gerado (ChatGPT Custom GPT consome).
- `/docs` — Swagger UI interativo.
- Sem segundo motor; sem lógica estatística duplicada.

## 5. Contrato de resposta (JSON estruturado)

Toda resposta segue:
```json
{
  "success": true,
  "generated_at": "2026-09-14 22:00:00",
  "project_hash": "<hash do HEAD>",
  "engine_version": "<motor>",
  "mode": "prelive|live|status",
  "provider": "API-Football v3 (api-sports.io)",
  "api_version": "api-1.0-chatgpt",
  "data": { ... }
}
```
Quando aplicável, `data` contém: `fixture_id, home, away, competition,
kickoff, live_minute, score, market, line, probability, confidence,
decision, status_estatistico, status_operacional, origem_operacional,
modo_teste, data_freshness, provenance`.

**Ausente ≠ zero.** NULL permanece NULL (serializado como `null`).

## 6. Segurança contra invenção

- A API **não** transforma `null → 0`. Campos ausentes retornam `null`
  e/ou `"NAO_DISPONIVEL"`.
- A API **não** inventa probabilidade, linha, escanteios, gols, minuto,
  placar ou confiança. Todos vêm do motor (`SaidaOficial`).
- Override de mercado (CORNERS) e override de modo (LIVE) **não criam
  sinal**: só oportunidades que o motor retornar `ENTRAR`
  (`aprovada_motor=True`) aparecem em `sinais_operacionais`.

## 7. Data e tempo

- **Pré-live:** usa apenas fixtures não encerrados/não ao vivo da data
  informada (sem informação futura).
- **Live:** `varredura_live` usa `scan_live_opportunities` (motor live),
  que valida status/minuto/freshness. Cache antigo nunca é apresentado
  como LIVE atual. `data_freshness`: `FRESCO` (delta ≤ 180s) ou `STALE`.
  STALE ⇒ nenhuma oportunidade operacional (`DADO_LIVE_DESATUALIZADO`).

## 8. Não executar apostas

A integração é **somente leitura**. Nenhum endpoint cria execução
automática, integração financeira, ordem para bookmaker ou confirmação
automática de aposta. As respostas não contêm `aposta_executada`,
`aposta_realizada`, `ordem_enviada` ou `bookmaker_integration`.

## 9. Endpoint de saúde

`GET /health` → `status=ok`, `version`, `project_hash`, `database`
(integrity_check, user_version), `prelive` (GOALS/CORNERS), `live`
(status_estatistico BLOQUEADO, status_operacional, modo_teste_live),
`modo_teste_live`. Sem segredos.

## 10. Deploy (Render)

Config: `render.yaml` (web service, Python 3.12, FastAPI + uvicorn,
`PORT` env, `healthCheckPath: /health`, auto-deploy da `main`).

**Variáveis de ambiente (dashboard Render, NUNCA no repo):**
`API_KEY` (obrigatória), e opcionais `THE_ODDS_API_KEY`,
`SPORTMONKS_API_TOKEN`, `APIFOOTBALL_COM_API_KEY`,
`FIVE_DOLLAR_FOOTBALL_API_KEY`, `FOOTBALL_DATA_API_KEY`.

Start: `uvicorn src.api_server:app --host 0.0.0.0 --port $PORT`

OpenAPI para ChatGPT: `https://<service>.onrender.com/openapi.json`

> **Nota:** o deploy remoto exige ação do operador no dashboard Render
> (este ambiente não possui credencial/CLI do Render). O código, a config
> e os testes locais estão prontos.

## 11. Como conectar no ChatGPT

1. Deployar o serviço no Render (via `render.yaml` ou dashboard).
2. No ChatGPT: criar um Custom GPT → Actions → importar OpenAPI schema
   de `https://<service>.onrender.com/openapi.json`.
3. O ChatGPT poderá chamar os 7 endpoints. Ele apresenta os resultados;
   não recalcula nada.

## 12. Exemplos de chamadas

```
GET /health
GET /api/status-mercados
GET /api/jogos-do-dia?date=2026-09-15
GET /api/varredura-prelive?date=2026-09-15
GET /api/partida-prelive?fixture_id=9001
GET /api/varredura-live
GET /api/partida-live?fixture_id=9001
```

## 13. Status dos mercados (preservado)

| Mercado | Status estatístico | Status operacional | Origem |
|---|---|---|---|
| GOALS | APROVADO_PARA_PROXIMA_FASE | HABILITADO_ESTATISTICAMENTE | estatístico |
| CORNERS | EM_OBSERVAÇÃO | HABILITADO_POR_OVERRIDE_DO_USUARIO | override_usuario |
| RESULTADO | EM_OBSERVAÇÃO | — (observação) | — |
| CARDS | NÃO_AVALIÁVEL | — (bloqueado) | — |
| PRESSAO_LIVE | BLOQUEADO | HABILITADO_PARA_TESTE_POR_OVERRIDE_DO_USUARIO | override de MODO |
| ODDS_ROI | BLOQUEADO | — (bloqueado) | — |

**Diferença estatística vs override:** GOALS é operacional por aprovação
estatística. CORNERS é operacional por override explícito do operador
(status estatístico EM_OBSERVAÇÃO **preservado** — não é aprovação
estatística). LIVE é operacional em MODO TESTE por override de MODO
(status estatístico BLOQUEADO **preservado**).

## 14. Modo teste live

- `modo_teste_live = true` em todas as respostas live.
- `status_estatistico_live = BLOQUEADO / NÃO_VALIDADO`.
- `status_operacional_live = HABILITADO_PARA_TESTE_POR_OVERRIDE_DO_USUARIO`.
- Override de MODO não cria sinal; override auditável (`override_operador`,
  `decisao_humana`, `motivo`, `timestamp_override`, `versao_projeto`).
- LIVE **NÃO** é aprovado estatisticamente.

## 15. Limitações

- LIVE é experimental; não validado estatisticamente.
- Pressão ofensiva live (5/10/15) é experimental, sem thresholds validados.
- Sem ROI live; sem integração com bookmaker; sem registro de validação
  live (separado do baseline pré-live congelado).
- `analisar_partida_live` roda a varredura live oficial para extrair o
  fixture (fiel ao motor; não duplica lógica).

## 16. Testes

- `tests/test_api_server.py` — 13 testes determinísticos (A-M) via
  TestClient + monkeypatch (sem API real). Validam: status responde,
  jogos do dia sem probabilidade, varredura usa SaidaOficial, GOALS/CORNERS
  preservados, API não promove CORNERS, modo_teste live, LIVE não aprovado
  estatisticamente, override não cria sinal, NULL permanece NULL, stale
  não vira fresco, nenhuma aposta, mesma decisão CC × API (DIVERGÊNCIA=0).
- Regressão completa: **742 passed (729+13), 18 skipped, 0 failed.**
- Teste local HTTP real: `/health`, `/api/status-mercados`,
  `/openapi.json` validados via uvicorn + curl.
- Teste CC × MCP real: `/api/varredura-live` over HTTP repassa
  `SaidaOficial` canônica idêntica ao CLI (DIVERGÊNCIA = 0).

## 17. Integridade

- Motor 100% intacto (diff vazio em live_opportunity, prejogo_opportunity,
  analysis, backtest, policy, validacao_multifonte, politica_aprovacao,
  operacional). A API é aditiva (novo arquivo + requirements).
- Pré-live sem regressão; LIVE modo teste sem regressão.
- `.env` gitignored; segredo versionado NÃO; NULL ≠ ZERO preservado.
- `v1.0.0-operacional` e `v1.1.0-live-teste` intocadas.

## 18. Pendência futura — NÃO IMPLEMENTAR NESTA ETAPA

Registrar para revisão futura (não resolvido nesta macroetapa, por
instrução explícita do operador): lógica relacionada a favorito da partida,
favorito mandante/visitante, empate, dupla chance, empate anula, proteção
de resultado, linhas alternativas, interação entre resultado e gols.
**NÃO alterar o motor hoje.**

## 19. Não-fazer (constraints respeitadas)

- Não criar segundo motor nem backend paralelo.
- Não recalibrar / alterar thresholds / modelo estatístico / lógica de decisão.
- Não mexer em favorito/dupla chance/empate anula/resultado protegido/
  handicap/novas linhas/novos mercados.
- Não executar/integrar aposta financeira.
- Não alterar `v1.0.0-operacional` nem `v1.1.0-live-teste`.
- Não destruir deploy antigo (branch backup preservada).

---

**Estado final:** PRÉ-LIVE operacional + LIVE modo teste **preservados** +
API oficial ChatGPT (FastAPI, 7 endpoints + /health + OpenAPI) funcionando
localmente + mesma verdade operacional (SaidaOficial) para Claude Code e
ChatGPT + DIVERGÊNCIA = 0.