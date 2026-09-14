# ETAPA 5F-D2 — AUDITORIA AUTENTICADA DAS FONTES EXTERNAS

**CHAMADAS REAIS CONTROLADAS — MOTOR CONGELADO.**

> **RESULTADO PRINCIPAL: GATE ABERTO.** `MULTISOURCE_CREDENTIALS_ROTATED=1`
> e as 6 credenciais estão presentes. **33 chamadas reais executadas** (todas
> dentro dos HARD_LIMIT). Motor intacto (diff vazio). Nenhum segredo versionado.
> Auditoria reusou EXATAMENTE a infra de `src/multifonte.py` /
> `src/auditoria_multifonte.py` — nenhuma arquitetura paralela.

---

## ESTADO

- branch: `etapa-5fd-auditoria-multifonte`
- HEAD base: `3ac8f35` (5F-D)
- testes: **612 passed / 18 skipped / 0 failed** (baseline 606 + 6 novos)
- adapters/abstrações: reusados de 5F-D (FactProvider, OddsProvider, registry,
  reconciliação, ConflictRegistry, matriz_cobertura) — apenas estendidos com
  métodos de busca por data (`fetch_fixtures_by_date`, `fetch_events_by_date`,
  `fetch_matches_by_competition`) e o runner real `rodar_auditoria_real`.
- banco `odds_snapshot_history`: 317.951 linhas, provider_null=0, user_version=2
  (read-only; não misturado com dados de auditoria).

## SEGURANÇA

- gate aberto: **SIM** (`MULTISOURCE_CREDENTIALS_ROTATED=1`).
- `.env` gitignored, **não tracked**.
- segredos em código/docs/JSON/logs/git: **NÃO** (`SEGREDO_VERSIONADO = NÃO`;
  scan dos 6 valores de credencial contra `src/**/*.py`, `tests/**/*.py`,
  `docs/**/*.md`, `docs/**/*.json` → 0 ocorrências).
- raws salvos em `data/auditoria_multifonte/` (gitignored) com `_redact`
  (valores de credencial substituídos por `[REDACTED]`).
- nenhum valor/prefixo/sufixo/tamanho/hash de chave impresso neste relatório.

## CREDENCIAIS (presença + validade observada; valores nunca exibidos)

| Provider | Variável | Presente | Válida (observado) |
|---|---|---|---|
| api_football | `API_KEY` | SIM | SIM (cache existente) |
| the_odds_api | `THE_ODDS_API_KEY` | SIM | SIM (HTTP 200, 84 sports) |
| sportmonks | `SPORTMONKS_API_TOKEN` | SIM | SIM (HTTP 200, plano limitado) |
| apifootball_com | `APIFOOTBALL_COM_API_KEY` | SIM | SIM (HTTP 200, 1.019 ligas) |
| five_dollar_football | `FIVE_DOLLAR_FOOTBALL_API_KEY` | SIM | **NÃO** (HTTP 401 invalid) |
| football_data_org | `FOOTBALL_DATA_API_KEY` | SIM | **NÃO** (HTTP 400 "Your API token is invalid") |
| statsbomb_open | (pública) | — | SIM (sem credencial) |

**2 credenciais presentes mas rejeitadas pelo provider** (5Dollar 401,
football-data 400). Não houve retry além do limite; não se comprou/alterou plano.

## CHAMADAS (totais da sessão vs HARD_LIMIT)

| Provider | Requests | HTTP/status | Quota (usado/limite) |
|---|---|---|---|
| api_football | 0 novos (cache read-only) | VALIDADO_NA_AMOSTRA | n/a |
| the_odds_api | 7 | 200 (sports, odds UCL) | 7/10 |
| five_dollar_football | 3 | 401 invalid credential | 3/15 |
| sportmonks | 6 | 200 (4 ligas; 0 fixtures na data) | 6/15 |
| apifootball_com | 7 | 200 (1.019 ligas, 193 events, 1 get_statistics) | 7/15 |
| football_data_org | 4 | 400 invalid token | 4/10 |
| statsbomb_open | 6 | 200 (80 comps, 3.947 events) | 6/30 |
| **TOTAL** | **33** | — | todos dentro do limite |

Nenhum HTTP 402/429. Nenhum plano comprado/alterado. 401/400 interromperam
o provider (5Dollar, football-data) sem derrubar a auditoria.

## CARTÕES (amostra real — 2026-09-10, 193 events apifootball_com)

Candidatos API-Football (cache, FT em 2026-09-10): 12 fixtures
(8 all_zero, 2 has_red, 2 no_stats). Reconciliação com `apifootball_com`
`get_events` (cards[] por evento, campo `card`: "yellow card"/"red card"):

| Fixture (cache) | apifootball_com | cache_red | afc_red | concordância |
|---|---|---|---|---|
| Bayern München x Bodo/Glimt (UCL) | Bayern Munich x Bodo/Glimt | 1 | 1 | **SIM** (fuzzy_prefix) |
| Slavia Praha x Lens (UCL) | Slavia Prague x Lens | 1 | 1 | **SIM** (fuzzy_prefix) |
| Como x RB Leipzig (UCL) | Como x RB Leipzig | 0 | 0 | **SIM** (exact) |
| Fenerbahçe x AS Roma (UCL) | Fenerbahçe x AS Roma | 0 | 0 | **SIM** (exact) |
| Once Caldas x Alianza Valledupar (Copa Colombia) | Once Caldas x Alianza | null | 0 | null→0 (cand no_stats) |

- **concordância red positivo: 2/2** (Bayern, Slavia — cache e apifootball_com
  concordam em 1 cartão vermelho cada).
- **concordância red zero: 2/2** (Como, Fenerbahçe).
- **null→0 (Once Caldas)**: cache sem statistics (no_stats), apifootball_com
  reporta 0 vermelhos — não é recuperação de `all_null` (não havia fixture
  `all_null` na data auditada).
- **recuperação null→positivo: 0** (nenhuma fixture `all_null` na amostra da data).
- **conflitos red divergente: 0**.
- Reconhecimento de nome: accent-fold + prefixo fuzzy (4 chars) capturou
  München/Munich, Praha/Prague; youth/U19 excluído para evitar colisão.
- `sportmonks`: **0 fixtures** na data (plano cobre só 4 ligas menores —
  Superliga dinamarquesa, Premiership NI) → sem comparação de cartões.
- Convenção `amarelo=1, vermelho=2` intacta. Nenhum settlement alterado.

### Respostas obrigatórias (cartões)
- casos null testados (api_football cache total): **3.531** (Etapa 5D/5F-D)
- recuperados como zero explícito por fonte externa (nesta amostra data): **0**
  (não havia `all_null` na data; Once Caldas era `no_stats`)
- recuperados como >0 por fonte externa: **0** (não demonstrado nesta amostra)
- concordância positiva cross-provider (apifootball_com): **2/2**
- concordância zero cross-provider: **2/2**
- conflitos: **0**
- melhor cobertura observada: **api_football** (factual) + **apifootball_com**
  (candidato fallback — cobre UCL, detecta vermelhos, concorda)
- evidência real para futuro fallback de RED CARDS: **SIM (PARCIAL)** —
  apifootball_com entrega cards[] concordante; recuperação null→>0 não
  demonstrada nesta amostra (sem fixture all_null na data).

## CORNERS (amostra real)

- `sportmonks`: 0 fixtures na data (plano limitado) → **sem corners**.
- `apifootball_com` `get_statistics` (fixture UCL 853153 Bayern): **0
  statistics** retornadas → **sem corners**.
- `the_odds_api`: NÃO oferece mercado de corners (mercados observados:
  h2h, h2h_lay, totals, spreads — nenhum de corners/cards).
- `5dollar` (único provider documentado com mercado `corner`): **401 — não
  testável** (credencial inválida).
- concordância cross-provider de corners: **NÃO AVALIÁVEL** (nenhuma fonte
  externa entregou corners na amostra).
- drift temporal (Etapa 5C): **EVIDÊNCIA INSUFICIENTE** — sem fonte
  independente de corners observada. Regra não alterada.

## ODDS (observado real por provider)

| Provider | Mercados observados | Bookmakers | Pre/Live | Corners | Cards | Opening | Closing |
|---|---|---|---|---|---|---|---|
| api_football | pre/live (cache 317.951) | bet365 etc. | ambos | (8.114 familia escanteios) | (1.166 cartoes) | update_feed | update_feed |
| the_odds_api | h2h(1713), totals(422), spreads(72), h2h_lay(162) | 32 distintos | pre_match | **NÃO** | **NÃO** | NÃO | NÃO |
| five_dollar_football | NÃO TESTÁVEL (401) | — | — | NÃO TESTÁVEL | NÃO TESTÁVEL | — | — |

- **The Odds API agrega valor**: **SIM** — 2.369 odds UCL, 32 bookmakers,
  mercados h2h + totals (gols over/under) + spreads (handicap) pre-match.
  **NÃO** entrega odds de corners nem de cards (não oferecido pela API).
- **5Dollar**: credencial rejeitada (401) — não foi possível observar os
  mercados documentados (corner, corner_asian, cards, cards_asian, goalline,
  btts). Pergunta "5Dollar entrega odds de corners/cards?" → **NÃO TESTÁVEL**.
- OPENING/CLOSING: API-Football não marca abertura/fechamento (update_feed).
  The Odds API entrega pre_match sem marcação opening/closing explícita. Não
  inferir `OPENING_PROVIDER`/`CLOSING_PROVIDER`; usar
  `PRIMEIRO_SNAPSHOT_LOCAL`/`ULTIMO_SNAPSHOT_LOCAL`.

## STATSBOMB (smoke real — 6 requests na sessão)

| Recurso | Resultado |
|---|---|
| competitions.json | 80 competições |
| events/9880.json (smoke) | 3.947 eventos |
| status | VALIDADO_NA_AMOSTRA |

StatsBomb **NÃO é live, NÃO é bookmaker, NÃO é odds provider** (confirmado).
Dados brutos salvos em `data/auditoria_multifonte/` (gitignored, público).

## IDENTIDADE / RECONCILIAÇÃO

- `apifootball_com`: 6/12 candidatos reconciliados (5 AMBIGUOUS, 1 NOT_MATCHED
  por divergência de nome Sabah FA). Nenhum MATCHED≥0.85 (season/score/status
  ausentes no lado apifootball reduziram a confiança — não há falsa confirmação).
- `football_data_org`: credencial inválida (400) → identidade não testável.
- `sportmonks`: 0 fixtures → identidade não testável.
- MATCHED/AMBIGUOUS/NOT_MATCHED preservados (sem forçar matching).

## MATRIZ DE COBERTURA (observada real)

| Provider | AUTH | FIXTURES | SCORE | EVENTS | CORNERS | YELLOW | RED | ODDS | LIVE | STATUS |
|---|---|---|---|---|---|---|---|---|---|---|
| api_football | cache | V | V | P | V | V | V(null) | V | V | VALIDADO_NA_AMOSTRA |
| the_odds_api | V(200) | V(odds) | — | — | ND | ND | ND | V(h2h/totals/spreads) | ND | VALIDADO_NA_AMOSTRA |
| apifootball_com | V(200) | V(193) | P | P | ND(0 stats) | V(cards[]) | V(cards[]) | ND | ND | VALIDADO_NA_AMOSTRA |
| sportmonks | V(200) | LIMITADO(0) | — | — | ND | ND | ND | ND | ND | LIMITADO_PELO_PLANO |
| five_dollar | INVALIDA(401) | — | — | — | NT | NT | NT | NT | NT | CREDENCIAL_INVALIDA |
| football_data_org | INVALIDA(400) | — | — | — | ND | ND | ND | ND | ND | CREDENCIAL_INVALIDA |
| statsbomb_open | pública | V | V | V | ND | ND | ND | ND | ND | VALIDADO_NA_AMOSTRA |

V=VALIDADO_NA_AMOSTRA · P=PARCIAL · ND=NAO_DISPONIVEL · NT=NAO_TESTAVEL ·
LIMITADO=LIMITADO_PELO_PLANO.

## MATRIZ DE CONFIABILIDADE (observada, sem implementar)

| CAMPO | PRIMARIA | FALLBACK | N_COMP | COBERTURA | CONCORDANCIA | CONFLITOS | STATUS |
|---|---|---|---|---|---|---|---|
| fixture_identity | api_football | apifootball_com | 6 | PARCIAL | 0.80 (ambig) | 0 | A_CONFIRMAR |
| score | api_football | football_data_org | 0 | PARCIAL | — | 0 | A_CONFIRMAR (credencial invalida) |
| corners | api_football | sportmonks | 0 (cross) | PARCIAL | — | 0 | A_CONFIRMAR (sem fonte externa observada) |
| yellow_cards | api_football | apifootball_com | 4 | PARCIAL | SIM (0=0, mas yellow não contado separadamente) | 0 | A_CONFIRMAR |
| red_cards | api_football | apifootball_com | 4 | PARCIAL→LIMITADO | SIM (2/2 positivo, 2/2 zero) | 0 | PARCIAL (fallback candidato real) |
| shots | api_football | statsbomb_open | 0 | PARCIAL | — | 0 | A_CONFIRMAR |
| possession | api_football | statsbomb_open | 0 | PARCIAL | — | 0 | A_CONFIRMAR |
| odds_goals | api_football | the_odds_api | 0 | PARCIAL | — | 0 | A_CONFIRMAR (the_odds valida totals) |
| odds_corners | api_football | five_dollar | 0 | LIMITADO | — | 0 | A_CONFIRMAR (5dollar 401) |
| odds_cards | api_football | five_dollar | 0 | LIMITADO | — | 0 | A_CONFIRMAR (5dollar 401) |

Nenhum vencedor escolhido. Registro append-only preservado; 0 conflitos.

## ROI / COLETA PROSPECTIVA

Não calculado ROI retroativo. Por mercado:

| Mercado | Suficiente para coleta prospectiva futura? |
|---|---|
| GOALS | PARCIAL (api_football + the_odds_api totals validado) |
| CORNERS | PARCIAL (api_football; 5dollar 401; sem cross-provider) |
| CARDS | PARCIAL (apifootball_com fallback candidato para red; api_football null=3.531) |
| RESULTADO | PARCIAL |

## MOTOR CONGELADO

`git diff --name-only` (tracked) = `src/auditoria_multifonte.py`,
`src/multifonte.py`, `tests/test_auditoria_multifonte.py` — **todos da camada
de coleta/auditoria**. `git diff --name-only -- <motor>` = **vazio**
(analysis, politica_aprovacao, policy, settlement, calibration, backtest,
prejogo_opportunity, odds, identity, cache, config, api_client, live_pressure,
ao_vivo, app, **odds_coleta.py** — todos intactos). Thresholds 0.70/0.97/0.60,
amarelo=1/vermelho=2, Poisson, blend, pressão — intactos.

---

## 10 PERGUNTAS OBRIGATÓRIAS

1. **EXISTE FALLBACK REAL PARA RED CARDS?** **SIM (PARCIAL)** —
   `apifootball_com` entrega `cards[]` com detecção de vermelho, concordante
   com api_football em 2/2 positivos e 2/2 zeros (UCL 2026-09-10). Recuperação
   null→>0 não demonstrada nesta amostra (sem fixture all_null na data).
   `sportmonks` limitado pelo plano (0 fixtures). Não implementado (motor intacto).
2. **EXISTE FALLBACK REAL PARA CORNERS?** **NÃO** — nenhuma fonte externa
   entregou corners na amostra (sportmonks 0 fixtures; apifootball
   get_statistics vazio; the_odds_api não oferece corners; 5dollar 401).
3. **THE ODDS API AGREGA VALOR?** **SIM** — 2.369 odds UCL, 32 bookmakers,
   h2h + totals (gols) + spreads (handicap) pre-match. (Não cobre corners/cards.)
4. **5DOLLAR ENTREGA ODDS DE CORNERS?** **NÃO TESTÁVEL** — credencial
   rejeitada (HTTP 401 invalid). Não se assume cobertura por documentação.
5. **5DOLLAR ENTREGA ODDS DE CARDS?** **NÃO TESTÁVEL** — mesmo motivo (401).
6. **SPORTMONKS AJUDA EM CARTÕES?** **NÃO (nesta conta)** — plano limitado a
   4 ligas menores (Superliga dinamarquesa, Premiership NI); 0 fixtures na
   data auditada. LIMITADO_PELO_PLANO (não confundir com fonte ruim).
7. **APIFOOTBALL.COM AJUDA EM CARTÕES?** **SIM** — cobre UCL (193 events no
   dia), `cards[]` com vermelho/amarelo, concordante com api_football
   (2/2 positivo, 2/2 zero). Candidato fallback real para red cards.
8. **PODEMOS AVANÇAR PARA 5F-E?** **PARCIAL** — evidência suficiente para
   desenhar 5F-E considerando: api_football primária + apifootball_com
   fallback para cartões + the_odds_api para odds de gols. **Bloqueadores**:
   corners sem fallback, 5dollar/football-data credenciais inválidas,
   recuperação null→>0 de red não demonstrada. Decisão final exige autorização
   do operador (rodar credenciais 5dollar/football-data ou descartá-las).
9. **ETAPA 6 CONTINUA BLOQUEADA?** **SIM**.
10. **MOTOR PERMANECE INTACTO?** **SIM** (diff vazio; 612/18/0).

---

## O que foi POSSÍVEL vs BLOQUEADO

**Possível (executado com dados reais):**
- The Odds API: 84 sports + 2.369 odds UCL (h2h/totals/spreads, 32 bks).
- apifootball_com: 1.019 ligas + 193 events (cards[]) + concordância de
  cartões com cache (2/2 positivo, 2/2 zero).
- sportmonks: smoke OK (4 ligas) + 0 fixtures na data (plano limitado).
- StatsBomb: 80 competições + 3.947 eventos.
- api_football cache read-only: 3.531 Red Cards null, corners n=4.916.
- Suíte 612/18/0; motor intacto; 0 segredo versionado.

**Bloqueado (não testável nesta execução):**
- 5DollarFootballAPI: credencial inválida (401) — odds de corners/cards não
  observadas.
- football-data.org: credencial inválida (400) — identidade/placar não testáveis.
- Corners cross-provider: nenhuma fonte externa entregou corners.
- Recuperação null→>0 de red cards: sem fixture all_null na data auditada.

**Para desbloquear o restante:** rotacionar credenciais válidas de 5Dollar e
football-data.org (ou descartá-las) → reexecutar a 5F-D2 com uma data que
inclua fixtures `all_null` → então desenhar 5F-E.

---

> PARE. NÃO implementar fallback automático. NÃO escolher fonte vencedora no
> motor. NÃO alterar settlement. NÃO recalibrar. NÃO iniciar Etapa 6. NÃO
> mergear em main. NÃO push. NÃO iniciar daemon contínuo.