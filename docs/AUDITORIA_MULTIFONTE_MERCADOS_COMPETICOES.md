# AUDITORIA MULTIFONTE — MERCADOS E COMPETIÇÕES

> **ESCOPO:** Mede **COBERTURA** e **QUALIDADE DAS FONTES**. NÃO aprova mercado,
> NÃO recalibra, NÃO altera status estatístico, NÃO integra ao motor.
> **Mais APIs ≠ mercado aprovado. Nova fonte ≠ modelo validado. Odds ≠ ROI.**
> Redundância ≠ calibração. (Seção 16)

---

## ESTADO VALIDADO (PRE-FLIGHT, Seção 1)

- branch = `main`, HEAD = `fa66569` ✓
- working tree tracked limpa ✓; `.env` gitignored ✓; 0 segredo versionado ✓
- DB: integrity_check = ok, user_version = 4, odds_snapshot_history = 320.764 ✓
- api_cache: 9.280 rows (7.080 /fixtures/statistics, 1.102 /fixtures, 626 /teams,
  173 /fixtures/events, 148 /headtohead, 86 /odds, 37 /leagues, 28 /odds/live)

## CHAMADAS REAIS CONTROLADAS (Seção 13)

| Provider | Chamadas | Limite auditoria | HARD_LIMIT | Resultado |
|---|---|---|---|---|
| The Odds API | 1 (sports) | 3 | 10 | 83 sports, 47 soccer |
| football_data | 1 (competitions) | 3 | 10 | 13 top competitions |
| apifootball_com | 1 (competitions) | 5 | 15 | 1.019 leagues |
| 5Dollar | 1 (fixtures) | 5 | 15 | 5 fixtures hoje |
| sportmonks | 1 (leagues) | 3 | 15 | 4 ligas (plano limitado) |
| statsbomb | 0 (bloqueado modo seguro) | — | 30 | fetcher não configurado |
| api_football | 0 adicional (cache) | 5 | — | cache reusado |

**Sem 402/403/429. Nenhum plano comprado/contornado.**

---

## 2-3. FONTES — DOCUMENTADO vs OBSERVADO

| Provider | Função | Documentado | Observado REALMENTE | Status |
|---|---|---|---|---|
| **api_football** | FACTUAL+ODDS+LIVE+IDENTIDADE | fixtures/stats/odds/live/events | 23.096 fixtures, 7.028 stats, 317.951 odds, 15 bookmakers | OBSERVADO |
| **five_dollar_football** | ODDS (corners/cards/fases) | 1x2/asian/goalline/corner/cards + opening/closing/inplay | corner OPENING/CLOSING/INPLAY Bet 365 (Serie A/EPL/La Liga hoje) | OBSERVADO |
| **the_odds_api** | ODDS (gols/resultado/handicap) | h2h/totals/spreads | 47 soccer comps, h2h 1928 + totals 522 + spreads 214 | OBSERVADO |
| **apifootball_com** | FACTUAL fallback (cards) + IDENTIDADE | leagues/events/stats/cards | 1.019 leagues, cards concordantes (5F-D2: 4/4) | OBSERVADO |
| **football_data_org** | IDENTIDADE/FIXTURES/SCORE | competitions/matches/score | 13 top comps, PL/SA matches, kickoff/status/score | OBSERVADO |
| **sportmonks** | FACTUAL (plano) | fixtures/stats | 4 ligas menores (Superliga DK, Premiership NI) | LIMITADO_PELO_PLANO |
| **statsbomb** | HISTÓRICO (público) | competitions/events (open data) | 5F-D2: 80 comps + 3.947 events | A CONFIRMAR (fetcher bloqueado modo seguro) |

BTTS 5Dollar: **A CONFIRMAR** (não observado nesta auditoria — endpoint existe mas
odds BTTS não retornadas nas chamadas realizadas).

---

## 4. INVENTÁRIO DE COMPETIÇÕES POR PROVIDER

### api_football (cache /fixtures + /odds — 669 competições distintas no cache)
Top por volume de odds: MLS (61.763), UCL (35.635), Liga Argentina (24.505),
Serie B Brazil (24.481), Serie A Brazil (24.378), Championship (15.674),
La Liga (14.550), Serie A Italy (13.877), Süper Lig (11.426), Premiership SCO
(10.051), Primeira Liga (8.171), Libertadores (7.666), Liga MX (6.616),
Eredivisie (5.486), Sudamericana (4.942), Bundesliga (3.337).

### the_odds_api (47 soccer competitions)
EPL, La Liga, Serie A, Bundesliga, Bundesliga 2, Ligue 1, Ligue 2, Serie B Italy,
Eredivisie, Primeira Liga, Süper Lig, Premiership SCO, Superliga DK, Allsvenskan,
Ekstraklasa, Swiss Superleague, Greece SL, Russia PL, J League, K League 1,
**Brasileirão Série A, Brasileirão Série B**, Liga MX, MLS, Argentina Primera,
Chile, China, Libertadores, Sudamericana, UCL, UEL, UECL, Nations League,
Championship, League 1/2, EFL Cup, Coppa Italia, DFB-Pokal, 3. Liga, etc.

### football_data_org (13 top competitions — FREE plan)
Premier League, Championship, La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie,
Primeira Liga, Brasileirão Série A, UCL, Libertadores, Euro, World Cup.

### apifootball_com (1.019 leagues)
Brasil 108, England 26, Argentina 16, Italy 14, Netherlands 14, Germany 13,
Portugal 11, Spain 10, USA 19, Australia 23, Asia 25, intl 142.

### 5Dollar (por fixture/dia — FREE plan)
Cobertura **por fixture disponível no dia**, não por liga inteira. Hoje:
Italy Serie A (3), EPL (1), La Liga (1). Mercado forte (corners/cards com fases),
amplitude limitada pelo plano.

### sportmonks (4 ligas — LIMITADO_PELO_PLANO)
Superliga (Dinamarca), Premiership (Irlanda do Norte) + play-offs. Sem top league.

### statsbomb (HISTORICAL_AUDIT)
80 competições + 3.947 events (observado 5F-D2, público, não-live, não-odds).
Esta sessão: fetcher bloqueado em modo seguro (A CONFIRMAR para reuso operacional).

### NOME CANÔNICO / RECONCILIAÇÃO
A camada `reconciliar_fixture` (5F-D/5F-E1) resolve equivalência de fixtures entre
providers com threshold MATCHED≥0.85. **0 fixtures reconciliados** no DB até aqui
(a coleta multiprovider 5F-E2 não foi cruzada com api_football). Equivalência de
**competições** entre providers: mapeamento seguro para top leagues (EPL=PL=
soccer_epl; Serie A=SA=soccer_italy_serie_a; etc.); demais ⇒ AMBIGUOUS até
reconciliação. **Nenhuma equivalência forçada.**

---

## 5. COMPETIÇÕES PRIORITÁRIAS — DISPONIBILIDADE REAL

| Competição | api_football (factual) | api_football (odds) | 5Dollar | TheOdds | football-data | apifootball_com |
|---|---|---|---|---|---|---|
| Brasileirão Série A | ✓ (257 stats) | ✓ (24.378) | — | ✓ | ✓ | ✓ |
| Brasileirão Série B | ✓ (265 stats) | ✓ (24.481) | — | ✓ | — | ✓ |
| Premier League | ✓ (30 stats, baixo) | ✓ (cache limitado) | ✓ (hoje) | ✓ | ✓ | ✓ |
| La Liga | ✓ (41 stats) | ✓ (14.550) | ✓ (hoje) | ✓ | ✓ | ✓ |
| Serie A Italy | ✓ (30 stats) | ✓ (13.877) | ✓ (hoje) | ✓ | ✓ | ✓ |
| Bundesliga | ✓ (18 stats) | ✓ (3.337) | — | ✓ | ✓ | ✓ |
| Ligue 1 | ✓ (27 stats) | — | — | ✓ | ✓ | ✓ |
| Champions League | ✓ | ✓ (35.635) | — | ✓ | ✓ | ✓ |
| Europa League | — | — | — | ✓ | — | ✓ |
| Conference League | ✓ (130 stats) | ✓ (366 fx) | — | ✓ | — | ✓ |
| Libertadores | ✓ | ✓ (7.666) | — | ✓ | ✓ | ✓ |
| Sudamericana | ✓ | ✓ (4.942) | — | ✓ | — | ✓ |
| Liga MX | ✓ | ✓ (6.616) | — | ✓ | — | ✓ |
| MLS | ✓ (341 stats) | ✓ (61.763) | — | ✓ | — | ✓ |
| Argentina 1ª | ✓ (371 stats) | ✓ (24.505) | — | ✓ | — | ✓ |
| Colombia Primera A | ✓ (273 stats) | — | — | — | — | ✓ |
| Portugal 1ª | ✓ | ✓ (8.171) | — | ✓ | ✓ | ✓ |
| Eredivisie | ✓ | ✓ (5.486) | — | ✓ | ✓ | ✓ |

"—" = não observado para aquele provider/competição nesta auditoria (não implica
indisponibilidade permanente; 5Dollar é por-dia, api_football odds dependem de coleta).

---

## 6. MERCADOS FACTUAIS (api_football — observado no cache)

| Campo | Cobertura nas stats | Observação |
|---|---|---|
| fixture/kickoff/status | 23.096 fixtures | OBSERVADO |
| placar (gols) | 13.834/23.096 (60%) | OBSERVADO |
| Corner Kicks | 4.604/4.683 stats (98%) | OBSERVADO — BOA quando stats existem |
| Yellow Cards | 4.558/4.683 (97%) | OBSERVADO |
| Red Cards non-null | **552/4.683 (11%)** | 70% NULL (3.309) — limitação estrutural |
| Shots on Goal / Total Shots | OBSERVADO (em stats) | PRESSÃO live derivável |
| Ball Possession | OBSERVADO | |
| Fouls / Offsides | OBSERVADO | |
| Passes | OBSERVADO | |

**NULL ≠ ZERO:** Red Cards NULL (ausente) ≠ 0 (explícito). 3.309 NULL vs 824 zero
explícito vs 552 positivo. Nunca convertidos entre si.

apifootball_com: cards[] (yellow+red) observado concordante (5F-D2). Sem corners.
football_data: NÃO entrega corners/cards/shots (só identidade/score) — confirmado.

## 7. MERCADOS DE ODDS (observado)

| Mercado | api_football | 5Dollar | TheOdds | Confiança |
|---|---|---|---|---|
| 1X2 / MATCH_RESULT | resultado 16.193 | 1x2 (map) | h2h 1.928 | OBSERVADO (3 fontes) |
| O/U Gols / TOTAL_GOALS | gols 13.713 | goalline (map) | totals 522 | OBSERVADO (3 fontes) |
| Handicap / ASIAN_HANDICAP | resultado(AH) | asian (map) | spreads 214 | OBSERVADO (3 fontes) |
| O/U Corners / TOTAL_CORNERS | escanteios 8.114 | corner OPENING/CLOSING/INPLAY | — | OBSERVADO (2 fontes) |
| Asian Corners | — | corner_asian (map) | — | OBSERVADO (1 fonte) |
| O/U Cards / TOTAL_CARDS | cartoes 1.166 | cards (map) | — | OBSERVADO (2 fontes) |
| Asian Cards | — | cards_asian (map) | — | OBSERVADO (1 fonte) |
| BTTS | — | A CONFIRMAR | — | A CONFIRMAR |
| h2h_lay (TheOdds) | — | — | 143 (UNKNOWN) | NÃO mapeado |

**The Odds API NÃO oferece corners/cards** — confirmado (não atribuído sem evidência).
**api_football: 88% das odds UNMAPPED** (278.765/317.951) — mercados não classificados
pelo `classificar_mercado` (API-Football-specific, só ID exato 5/45/80 + resultado).

## 8. PRE-MATCH x LIVE

| Provider | PRE-MATCH | LIVE | Fases 5Dollar |
|---|---|---|---|
| api_football | SIM (310.898) | SIM (7.053 /odds/live) | — |
| 5Dollar | SIM (OPENING/CLOSING → pre_match) | SIM (INPLAY → live) | OPENING + CLOSING + INPLAY |
| TheOdds | SIM (2.807, tudo pre_match) | NÃO observado | — |
| apifootball_com | SIM (events) | NÃO observado | — |
| football_data | SIM (matches) | status IN_PLAY observado (5F-D2C) | — |
| sportmonks | LIMITADO | LIMITADO | — |

5Dollar: fases **preservadas do feed** (não inferidas). TheOdds: phase=NULL (não
inventa). Nenhum CLOSING/LIVE inferido artificialmente.

---

## 9. COBERTURA REAL NO BANCO (api_football, por competição — top)

| Liga | fixtures | com stats | corners | cob% | yellow | red_nn | red_null | gols |
|---|---|---|---|---|---|---|---|---|
| Serie A Brazil (71) | 380 | 257 | 257 | 67% | 252 | 33 | 190 | 257 |
| Serie B Brazil (72) | 380 | 265 | 262 | 68% | 261 | 29 | 199 | 267 |
| MLS (253) | 510 | 341 | 341 | 66% | 335 | 35 | 280 | 343 |
| Liga Argentina (128) | 495 | 371 | 371 | 74% | 369 | 46 | 277 | 375 |
| Primera A Colombia (239) | 394 | 273 | 271 | 68% | 265 | 49 | 172 | 274 |
| Primera Div. Peru (281) | 306 | 224 | 223 | 72% | 216 | 46 | 138 | 225 |
| UECL (848) | 366 | 130 | 130 | 35% | 124 | 10 | 65 | 258 |
| Premier League (39) | 380 | 30 | 30 | 7% | 29 | 1 | 29 | 30 |
| La Liga (140) | 380 | 41 | 41 | 10% | 41 | 3 | 23 | 41 |
| Serie A Italy (135) | 380 | 30 | 30 | 7% | 29 | 1 | 21 | 30 |
| Bundesliga (78) | 306 | 18 | 18 | 5% | 18 | 1 | 15 | 18 |
| Ligue 1 (61) | 306 | 27 | 27 | 8% | 26 | 3 | 20 | 27 |

**GLOBAIS:** 23.096 fixtures, 4.683 com stats, 4.604 com corners (98% das stats),
4.558 yellow (97%), **552 red non-null (11%)**, 3.309 red NULL (70%), 13.834 gols.

**Separação AUSENTE/NULL/ZERO (red cards):** AUSENTE = fixture sem stats (18.413);
NULL = stats presentes mas Red Cards=null (3.309, 70% das stats); ZERO explícito =
824; POSITIVO = 552.

CSV auxiliar: `data/auditoria_multifonte/cobertura_por_competicao.csv` (669 linhas).

---

## 10. REDUNDÂNCIA ENTRE FONTES

| Campo | Fontes | n | Classificação |
|---|---|---|---|
| identidade/fixture | api_football, football-data, apifootball_com, 5Dollar, TheOdds | 5 | REDUNDÂNCIA BOA |
| score | api_football, football-data, apifootball_com | 3 | REDUNDÂNCIA BOA |
| corners (factual) | api_football | 1 | SEM REDUNDÂNCIA |
| yellow cards (factual) | api_football, apifootball_com | 2 | REDUNDÂNCIA PARCIAL |
| red cards (factual) | api_football (70% NULL), apifootball_com | 2 | REDUNDÂNCIA PARCIAL |
| odds 1X2 | api_football, 5Dollar, TheOdds | 3 | REDUNDÂNCIA BOA |
| odds gols | api_football, 5Dollar, TheOdds | 3 | REDUNDÂNCIA BOA |
| odds corners | api_football, 5Dollar | 2 | REDUNDÂNCIA PARCIAL |
| odds cards | api_football, 5Dollar | 2 | REDUNDÂNCIA PARCIAL |
| odds handicap | api_football, 5Dollar, TheOdds | 3 | REDUNDÂNCIA BOA |

**Redundância ≠ validação estatística.** Redundância mede se há ≥2 fontes; não mede
concordância/calibração (Seção 16).

## 11. CONCORDÂNCIA ENTRE FONTES

- **DB atual: 0 fixtures reconciliados** entre providers (factual_resolution=0,
  source_conflict=0). A coleta multiprovider 5F-E2 não foi cruzada com api_football.
- **Evidência prévia (5F-D2):** apifootball_com vs api_football — red/yellow cards
  concordantes em 4/4 fixtures (Bayern 1=1, Slavia 1=1, Como 0=0, Fenerbahçe 0=0).
- **Classificação: AMOSTRA INSUFICIENTE** para concordância sistemática. A camada
  `reconciliar_fixture` existe mas requer execução sobre dados multiprovider
  (passo futuro, NÃO executado nesta auditoria). Nenhum vencedor escolhido.

---

## 12. COMPARAÇÃO COM AUDITORIA ANTIGA

| Dimensão | ANTES (5E/5F-D) | AGORA (5F-E) |
|---|---|---|
| Factual | só api_football | api_football + apifootball_com (cards) + football-data (identidade/score) |
| Odds | só api_football (88% UNMAPPED) | + 5Dollar (corners/cards fases) + TheOdds (h2h/totals/spreads, 47 comps) |
| Corners factual | 1 fonte (api_football) | **continua 1 fonte** (nenhuma externa entrega corners factual) |
| Corners odds | 1 fonte (api_football escanteios) | + 5Dollar (TOTAL_CORNERS, 3 fases) — **2 fontes** |
| Cards factual | NÃO AVALIÁVEL (red NULL 70%) | + apifootball_com (fallback red/yellow, 4/4 concordante prévio) |
| Cards odds | 1 fonte (api_football cartoes 1.166) | + 5Dollar (TOTAL_CARDS/ASIAN_CARDS) — **2 fontes** |
| Odds gols | 1 fonte | + 5Dollar (goalline) + TheOdds (totals) — **3 fontes** |
| Odds 1X2 | 1 fonte | + 5Dollar (1x2) + TheOdds (h2h) — **3 fontes** |
| Live odds | api_football /odds/live | + 5Dollar INPLAY |

**CORNERS:** cobertura de **odds** melhorou (2 fontes). **Drift temporal: NÃO
resolvido** — drift é fenômeno estatístico (5C: 0.928→0.872 no holdout); novas
fontes resolvem cobertura de odds, não validam modelo. **EVIDÊNCIA INSUFICIENTE**
para declarar drift resolvido (exige validação out-of-sample independente).

**CARDS:** cobertura factual melhorou (apifootball_com fallback red cards).
**NÃO reclassificado operacionalmente** — CARDOS permanece NÃO AVALIÁVEL até nova
validação out-of-sample com a fonte alternativa.

**O que continua sem solução:** corners factual sem redundância; red cards ainda
70% NULL no api_football (apifootball_com recupera null→>0 não demonstrado em
fixture all_null); sportmonks plano limitado; statsbomb não operacional live.

---

## 14. MATRIZ FINAL CONSOLIDADA (top leagues)

Classificações: OBSERVADO / NÃO OBSERVADO / PARCIAL / LIMITADO_PELO_PLANO / A CONFIRMAR

| Competição | Provider | Factual | Corners | Cards | RedCards | Odds1X2 | OddsGols | OddsCorners | OddsCards | PRE | LIVE | Open/Close | Confiança |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Brasileirão A | api_football | OBS | OBS | OBS | PARCIAL(70%NULL) | OBS | OBS | OBS | OBS | OBS | OBS | — | BOA |
| Brasileirão A | TheOdds | — | — | — | — | OBS | OBS | NÃO | NÃO | OBS | NÃO | — | BOA |
| Brasileirão A | football-data | OBS(id) | NÃO | NÃO | NÃO | — | — | — | — | OBS | OBS | — | BOA |
| Premier League | api_football | OBS | PARCIAL | PARCIAL | PARCIAL | OBS | OBS | OBS | OBS | OBS | OBS | — | PARCIAL |
| Premier League | 5Dollar | — | — | — | — | A CONF | A CONF | OBS | A CONF | OBS | OBS(INPLAY) | OBS | A CONFIRMAR |
| Premier League | TheOdds | — | — | — | — | OBS | OBS | NÃO | NÃO | OBS | NÃO | — | BOA |
| Serie A Italy | 5Dollar | — | — | — | — | A CONF | A CONF | OBS | A CONF | OBS | OBS(INPLAY) | OBS | A CONFIRMAR |
| Serie A Italy | api_football | OBS | PARCIAL | PARCIAL | PARCIAL | OBS | OBS | OBS | OBS | OBS | OBS | — | PARCIAL |
| Serie A Italy | TheOdds | — | — | — | — | OBS | OBS | NÃO | NÃO | OBS | NÃO | — | BOA |
| La Liga | 5Dollar | — | — | — | — | A CONF | A CONF | OBS | A CONF | OBS | OBS(INPLAY) | OBS | A CONFIRMAR |
| La Liga | TheOdds | — | — | — | — | OBS | OBS | NÃO | NÃO | OBS | NÃO | — | BOA |
| Bundesliga | TheOdds | — | — | — | — | OBS | OBS | NÃO | NÃO | OBS | NÃO | — | BOA |
| UCL | api_football | OBS | OBS | OBS | PARCIAL | OBS | OBS | OBS | OBS | OBS | OBS | — | BOA |
| UCL | TheOdds | — | — | — | — | OBS | OBS | NÃO | NÃO | OBS | NÃO | — | BOA |
| MLS | api_football | OBS | OBS | OBS | PARCIAL | OBS | OBS | OBS | OBS | OBS | OBS | — | BOA |
| MLS | TheOdds | — | — | — | — | OBS | OBS | NÃO | NÃO | OBS | NÃO | — | BOA |
| Argentina 1ª | api_football | OBS | OBS | OBS | PARCIAL | OBS | OBS | OBS | OBS | OBS | OBS | — | BOA |
| Libertadores | api_football | OBS | OBS | OBS | PARCIAL | OBS | OBS | OBS | OBS | OBS | OBS | — | BOA |
| sportmonks (todas) | sportmonks | LIMIT | LIMIT | LIMIT | LIMIT | — | — | — | — | — | — | — | LIMITADO_PELO_PLANO |

5Dollar OPENING/CLOSING/INPLAY preservados quando observados (não inferidos).

---

## 15. CLASSIFICAÇÃO POR MERCADO

| Mercado | Cobertura de dados | Redundância | Pronto para nova validação estatística? | Pronto para motor? |
|---|---|---|---|---|
| **GOALS** | BOA (api_football 13.713 odds + 13.834 gols factual; 3 fontes odds) | BOA (3 fontes) | SIM (já OPERACIONAL 5E) | NÃO nesta auditoria |
| **CORNERS** | PARCIAL (factual 1 fonte; odds 2 fontes) | PARCIAL (factual 1, odds 2) | NÃO (drift 5C não resolvido por cobertura) | NÃO |
| **CARDS** | PARCIAL (factual 70% NULL; +apifootball_com fallback; odds 2 fontes) | PARCIAL (2 fontes) | NÃO (NÃO AVALIÁVEL 5D; exige out-of-sample com fallback) | NÃO |
| **RESULTADO** | BOA (3 fontes odds; resultado 16.193 api_football) | BOA (3 fontes) | PARCIAL (EXPERIMENTAL 5E; cobertura OK mas validação própria) | NÃO |
| **PRESSÃO LIVE** | PARCIAL (api_football live 7.053 + shots/posse em stats; 5Dollar INPLAY) | INSUFICIENTE (1 fonte factual live) | NÃO (AGUARDANDO HISTÓRICO 5E) | NÃO |
| **ODDS/ROI** | PARCIAL (317.951 + 2.813 multiprovider; pre_match dominante) | BOA (odds multiprovider) | NÃO (odd_real desalinhamento temporal 5F; ROI não avaliável) | NÃO |

---

## 19. MOTOR

`git diff --name-only` sobre analysis, politica_aprovacao, policy, settlement,
calibration, backtest, prejogo_opportunity, live_pressure, Poisson, thresholds,
blend = **VAZIO**. Nenhum módulo decisório importa `resolucao_factual`/
`coletar_multiprovider`/`_construir_snapshots_multiprovider`. **MOTOR INTACTO.**

Fallback factual e odds multiprovider: **implementados mas ISOLADOS do motor.**

---

## ARTEFATOS (Seção 17)

- `docs/AUDITORIA_MULTIFONTE_MERCADOS_COMPETICOES.md` (este relatório)
- `data/auditoria_multifonte/api_football_cobertura.json` (669 competições)
- `data/auditoria_multifonte/cobertura_por_competicao.csv` (CSV auxiliar)
- Nenhum dump sensível versionado; dados em `data/` (não rastreado pelo git).

## TESTES (Seção 18)

Nenhum código produtivo alterado. Suíte completa: **653 passed / 18 skipped /
0 failed / 671 total**. Sem testes artificiais criados (auditoria = documentação).

---

## 20. RELATÓRIO EXECUTIVO FINAL

| Pergunta | Resposta |
|---|---|
| COMPETIÇÕES MAPEADAS? | **669** (api_football cache) + 47 (TheOdds) + 13 (football-data) + 1.019 (apifootball_com) — top leagues com mapeamento canônico seguro |
| COBERTURA MULTIFONTE? | **~18 top leagues** com ≥2 fontes (factual+odds); top europeias + Brasileirão + Libertadores + MLS + Argentina |
| ODDS REAIS? | **api_football 317.951 + TheOdds 2.807 + 5Dollar 6** = 320.764 snapshots |
| CORNERS? | factual api_football (4.604 stats) + odds 2 fontes (api_football escanteios 8.114 + 5Dollar) |
| CARDS? | factual api_football (yellow 4.558, red 552 non-null) + apifootball_com fallback + odds 2 fontes |
| RED CARDS com fonte alternativa? | **SIM — apifootball_com** (fallback candidato, 4/4 concordante prévio; null→>0 não demonstrado) |
| PRE-MATCH? | **SIM** (todas as fontes com odds) |
| LIVE? | **SIM** — api_football /odds/live (7.053) + 5Dollar INPLAY |

| Provider | Mais útil para |
|---|---|
| api_football | factual geral, identidade, corners factual, histórico, live odds |
| football_data_org | identidade/score fallback (top leagues) |
| apifootball_com | cards (red/yellow) fallback |
| 5Dollar | corners odds (3 fases), cards odds, fases OPENING/CLOSING/INPLAY |
| TheOdds | goals odds, 1X2, handicap (47 comps), amplitude de competições |
| sportmonks | — (LIMITADO_PELO_PLANO) |
| statsbomb | histórico/auditoria (não operacional live) |

| Status | Resposta |
|---|---|
| CORNERS: cobertura melhorou? | **SIM** (odds 2 fontes; factual ainda 1) |
| CORNERS: drift resolvido? | **EVIDÊNCIA INSUFICIENTE** (drift é estatístico, não de cobertura) |
| CARDS: cobertura melhorou? | **SIM** (apifootball_com fallback + 5Dollar odds) |
| CARDS: avaliável estatisticamente? | **NÃO** (exige validação out-of-sample com fallback) |
| GOALS: cobertura suficiente? | **SIM** (3 fontes odds + factual) |
| RESULTADO: cobertura suficiente? | **SIM** (3 fontes) |
| PRESSÃO LIVE: dados históricos suficientes? | **NÃO** (1 fonte factual live; AGUARDANDO HISTÓRICO) |
| ROI: odds reais suficientes para validação prospectiva? | **SIM (condicionalmente)** — 320.764 odds disponíveis, mas odd_real desalinhamento temporal (5F) exige coleta prospectiva alinhada a fixtures encerrados |

**Mercados para nova validação estatística:** GOALS (já operacional, pode revalidar
com odds multiprovider); RESULTADO (cobertura boa, validação própria pendente).
**Mercados que continuam bloqueados:** CORNERS (drift), CARDS (NÃO AVALIÁVEL),
PRESSÃO LIVE (sem histórico), ROI (desalinhamento temporal).

| Auditoria | Resposta |
|---|---|
| AUDITORIA MULTIFONTE APROVADA? | **SIM** (cobertura documentada, fontes separadas, documentado≠observado respeitado) |
| PRONTO PARA DESENHAR PRÓXIMA ETAPA DE VALIDAÇÃO/INTEGRAÇÃO AO MOTOR? | **SIM** (infra pronta; integração exige autorização explícita do operador + validação out-of-sample) |

---

> **PARE.** NÃO integrar dados ao motor. NÃO iniciar Etapa 6 automaticamente.
> NÃO recalibrar. NÃO alterar regras/thresholds. NÃO fazer push.