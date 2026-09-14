# ETAPA 4 — MATRIZ DEFINITIVA DE COBERTURA DA API

**Data:** 13/09/2026
**Fonte:** Auditoria cirúrgica da API-Football (08–13/09/2026) + cache SQLite
(9.280 respostas, custo zero de API nesta etapa).
**Módulo:** `src/cobertura.py` (funções `relatorio_matriz_oficial()`,
`status_odds_pre()`, `status_odds_live()`, `status_live_pressao()`,
`status_backtest()`, `status_resultado()`).
**Testes:** `tests/test_etapa4_matriz_cobertura.py` (12 testes, FASE J).

**Princípio do operador (verbatim):** PRECISÃO > QUANTIDADE. Não ampliar
cobertura só para aumentar número de jogos. Não invente cobertura. O que
vale é o que a conta/plano atual realmente entrega.

---

## 1. Matriz oficial (16 colunas)

Gereada por `relatorio_matriz_oficial()` em `src/cobertura.py`. Uma linha
por competição da auditoria (73 ligas). Legenda:

- **STATUS GERAL:** rollup HONESTO por mercado (não bloqueio global). Se
  apenas um mercado é insuficiente, os demais continuam.
- **GOALS/CORNERS/CARDS PRE/LIVE:** `PERMITIDO` / `OBSERVAÇÃO` /
  `BLOQUEADO` (classe estrutural A/B/C/D/E → status).
- **RESULTADO PRE/LIVE:** segue GOALS, mas **sempre** `EXPERIMENTAL EM
  OBSERVAÇÃO` (a matriz não promove mercado experimental).
- **PRESSÃO LIVE 5/10/15:** `LIVE COMPLETO` / `LIVE PARCIAL` /
  `LIVE INSUFICIENTE` / `LIVE NÃO TESTADO` · `EXPERIMENTAL / A CALIBRAR`.
- **ODDS PRE:** `PERMITIDO` / `INSUFICIENTE` / `NÃO TESTADO`.
- **ODDS LIVE:** `INDISPONÍVEL NA FONTE` (sempre — auditoria 0/28).
- **BACKTEST:** `VIÁVEL` / `PARCIAL` / `INVIÁVEL` / `A CONFIRMAR`.

| COMPETIÇÃO | ID | STATUS GERAL | GOALS PRE | GOALS LIVE | CORNERS PRE | CORNERS LIVE | CARDS PRE | CARDS LIVE | RESULTADO PRE | RESULTADO LIVE | PRESSÃO LIVE 5/10/15 | ODDS PRE | ODDS LIVE | BACKTEST | MOTIVO/OBSERVAÇÃO |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| UEFA Champions League | 2 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A; jogos futuros vazios por definição |
| UEFA Europa League | 3 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | INSUFICIENTE | INDISP. | VIÁVEL | classe A; **odds pre não fornecidas** (flag odds=false) |
| Copa Sudamericana | 11 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A |
| Copa Libertadores | 13 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A |
| CONCACAF CL | 16 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B (4/4) |
| Premier League | 39 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (34/36) |
| League One | 41 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B (3/3) |
| FA Cup | 45 | OBSERVAÇÃO | PERMITIDO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | PERMITIDO · EXP | OBS. · EXP | PARCIAL · EXP | NÃO TESTADO | INDISP. | PARCIAL | classe C |
| Ligue 1 | 61 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (13/13) |
| Liga 62 | 62 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Brasileirão Série A | 71 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (261/261) |
| Série B BR | 72 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (46/46) |
| Copa do Brasil | 73 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | **PARCIAL** · EXP | PERMITIDO | INDISP. | **PARCIAL** | classe A, mas 88/150 parcial; validação dinâmica obrigatória |
| Série C BR | 75 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/196); só placares |
| Série D BR | 76 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | PERMITIDO | INDISP. | INVIÁVEL | classe E; statistics_fixtures=false |
| Bundesliga | 78 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (7/7) |
| Frauen Bundesliga | 82 | OBSERVAÇÃO | PERMITIDO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | PERMITIDO · EXP | OBS. · EXP | PARCIAL · EXP | PERMITIDO | INDISP. | PARCIAL | classe C (11/23) |
| Eredivisie | 88 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (73/73); live verificado |
| Eerste Divisie | 89 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (95/97) |
| Primeira Liga | 94 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (73/73) |
| Segunda Liga POR | 95 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Eliteserien (Noruega) | 103 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B (28/28); **não promove ao universo sem operador** |
| NM Cupen (Noruega) | 105 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/3); era mapeado por erro como Eliteserien |
| Ekstraklasa | 106 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (60/60) |
| Allsvenskan | 113 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (161/161) |
| Superettan | 114 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B (3/3) |
| Liga 119 | 119 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Liga Profesional ARG | 128 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (374/378) |
| Primera Nacional ARG | 129 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/78) |
| Copa Argentina | 130 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | PERMITIDO | INDISP. | INVIÁVEL | classe E (0/8) |
| Primera C MET ARG | 131 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E |
| Primera B MET ARG | 132 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E |
| Torneo Federal A ARG | 134 | OBSERVAÇÃO | PERMITIDO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | PERMITIDO · EXP | OBS. · EXP | PARCIAL · EXP | NÃO TESTADO | INDISP. | PARCIAL | classe C |
| Serie A (Itália) | 135 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (83/83) |
| Série B (Itália) | 136 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Coppa Italia | 137 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Liga 138 | 138 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E |
| La Liga | 140 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (45/45) |
| Segunda División ESP | 141 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B (3/3) |
| Jupiler Pro League | 144 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (15/15) |
| Liga 172 | 172 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Super League (Grécia) | 197 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Süper Lig (Turquia) | 203 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (93/93) |
| 1. Lig (Turquia) | 204 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (113/113) |
| 2. Lig (Turquia) | 205 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/14) |
| 2. Liga (Áustria) | 219 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/33) |
| Premier League (Egito) | 233 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | PERMITIDO | INDISP. | A CONFIRMAR | classe B (34/36) |
| Primera A (Colômbia) | 239 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (275/276) |
| Copa Colombia | 241 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | PERMITIDO | INDISP. | INVIÁVEL | classe E (0/58) |
| Serie A (Equador) | 242 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (227/232) |
| Division Prof. (Paraguai) | 252 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B (52/56=93%); rebaixada de A |
| Major League Soccer | 253 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (341/343) |
| Liga MX | 262 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | COMPLETO · EXP | PERMITIDO | INDISP. | VIÁVEL | classe A (68/68) |
| Primera Division URU | 281 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (224/225) |
| Liga 290 | 290 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E |
| Saudi Pro League | 307 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (48/49); live verificado |
| Liga 344 | 344 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Liga 383 (Israel) | 383 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | A CONFIRMAR | classe B |
| Paulistão A1 | 475 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | INSUFICIENTE | INDISP. | VIÁVEL | classe A; **odds pre não fornecidas** |
| Gaúcho | 477 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/11) |
| Canadian Premier | 479 | OBSERVAÇÃO | PERMITIDO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | PERMITIDO · EXP | OBS. · EXP | PARCIAL · EXP | PERMITIDO | INDISP. | PARCIAL | classe C (41/81=50%) |
| Catarinense | 604 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/10) |
| Paranaense | 606 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/15) |
| Copa do Nordeste | 612 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/4) |
| Carioca 1 | 624 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | INSUFICIENTE | INDISP. | VIÁVEL | classe A; **odds pre não fornecidas** |
| Paraense | 627 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/10) |
| Mineiro | 629 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/15) |
| Amistosos de seleções | 667 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E (0/210); placares sem stats |
| Liga 673 | 673 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E |
| Leagues Cup | 772 | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO | PERMITIDO · EXP | PERMITIDO · EXP | NÃO TESTADO · EXP | NÃO TESTADO | INDISP. | VIÁVEL | classe A (60/61) |
| UEFA Conference League | 848 | OBSERVAÇÃO | PERMITIDO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | OBSERVAÇÃO | PERMITIDO · EXP | OBS. · EXP | **PARCIAL** · EXP | INSUFICIENTE | INDISP. | **PARCIAL** | **reclassificada A→C** (135/263=51% vazios em encerrados) |
| Liga 887 | 887 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E |
| Série C (Itália) | 943 | MISTO | PERMITIDO | OBSERVAÇÃO | BLOQUEADO | BLOQUEADO | BLOQUEADO | BLOQUEADO | PERMITIDO · EXP | OBS. · EXP | INSUFICIENTE · EXP | NÃO TESTADO | INDISP. | INVIÁVEL | classe E; amostra pequena |

> **EXP** = EXPERIMENTAL EM OBSERVAÇÃO (RESULTADO) / EXPERIMENTAL / A
> CALIBRAR (PRESSÃO). A matriz de cobertura **não promove** nenhum
> mercado experimental para validado (FASE H).

---

## 2. Listas operacionais

### Lista 1 — Competições mais completas (STATUS GERAL = PERMITIDO)

Cobertura sólida em todos os mercados (GOALS/CORNERS/CARDS PRE+LIVE).
Backtest VIÁVEL (classe A) ou A CONFIRMAR (classe B):

> 2 UCL · 3 UEL · 11 Sudamericana · 13 Libertadores · 16 CONCACAF CL ·
> 39 Premier League · 41 League One · 61 Ligue 1 · 62 · 71 Brasileirão
> Série A · 72 Série B BR · 73 Copa do Brasil · 78 Bundesliga · 88
> Eredivisie · 89 Eerste Divisie · 94 Primeira Liga · 95 Segunda Liga ·
> 103 Eliteserien · 106 Ekstraklasa · 113 Allsvenskan · 114 Superettan ·
> 119 · 128 Liga Profesional ARG · 135 Serie A ITA · 136 Série B ITA ·
> 137 Coppa Italia · 140 La Liga · 141 Segunda División ESP · 144
> Jupiler Pro League · 172 · 197 Grécia · 203 Süper Lig · 204 1. Lig ·
> 233 Egito · 239 Primera A COL · 242 Equador · 252 Paraguai · 253 MLS ·
> 262 Liga MX · 281 Uruguai · 307 Saudi Pro League · 344 · 383 · 475
> Paulistão · 624 Carioca · 772 Leagues Cup

### Lista 2 — Competições parciais (OBSERVAÇÃO / MISTO)

Cobertura parcial — análise permitida em observação/calibração, **nunca
recomendação automática** sem validação dinâmica do fixture:

> 45 FA Cup (C) · 73 Copa do Brasil (PARCIAL live/backtest) · 82 Frauen
> Bundesliga (C) · 134 Torneo Federal A (C) · 479 Canadian Premier (C) ·
> **848 UEFA Conference League (C — reclassificada A→C em 13/09/2026)**

### Lista 3 — Competições bloqueadas (classe E: sem estatísticas de partida)

Placares existem, mas escanteios/cartões/estatísticas não são entregues
pela fonte. GOALS PRE permanece PERMITIDO (historico de placares);
CORNERS/CARDS BLOQUEADOS. Backtest INVIÁVEL para corners/cards:

> 75 Série C BR · 76 Série D BR · 105 NM Cupen · 129 Primera Nacional ·
> 130 Copa Argentina · 131 Primera C MET · 132 Primera B MET · 138 ·
> 205 2. Lig TUR · 219 2. Liga AUT · 241 Copa Colombia · 477 Gaúcho ·
> 604 Catarinense · 606 Paranaense · 612 Copa do Nordeste · 627
> Paraense · 629 Mineiro · 667 Amistosos de seleções · 673 · 887 · 943
> Série C ITA

### Lista 4 — Competições que NÃO vale consultar para determinado mercado

Filtro ANTES da chamada cara (FASE F). Não descarta o jogo inteiro —
apenas o mercado insuficiente:

| Competição (ID) | Mercado | Modo | Motivo |
|---|---|---|---|
| Série C/D BR (75/76), 105, 129, 130, 131, 132, 138, 205, 219, 241, 477, 604, 606, 612, 627, 629, 667, 673, 887, 943 | CORNERS | PRE+LIVE | classe E: zero Corner Kicks na fonte |
| idem | CARDS | PRE+LIVE | classe E: zero cartões completos na fonte |
| 3 UEL, 848 Conference, 475 Paulistão, 624 Carioca | ODDS | PRE | flag odds=false; odds não fornecidas |
| TODAS (73 ligas) | ODDS | LIVE | /odds/live retornou 0/28 — indisponível na fonte |
| 848 Conference, 73 Copa do Brasil | PRESSÃO LIVE | 5/10/15 | LIVE PARCIAL — não tratar como completo |

### Lista 5 — Limitações do plano/API (confirmadas, não presumidas)

1. **Odds LIVE indisponíveis:** `/odds/live` retornou 0/28 partidas com
   odds na conta/plano atual. Não é falha de código — é limite
   comercial/plano. Sem odd live → classificação
   `OPORTUNIDADE ESTATÍSTICA` + "ODD AO VIVO NÃO DISPONÍVEL NA FONTE"
   (já em `src/odds.py`). **None nunca vira zero.**
2. **Odds PRE negadas em 4 ligas:** 3 (UEL), 848 (Conference), 475
   (Paulistão), 624 (Carioca) — flag `odds=false` no `/leagues`. Não
   afeta análise de DADOS, apenas odds.
3. **Conference League (848) — 51% de vazios em jogos ENCERRADOS:**
   128/263 partidas finalizadas (FT/AET/PEN) sem estatísticas. Não é
   atraso — é ausência estrutural. Reclassificada A→C (OBSERVAÇÃO).
4. **Copa do Brasil (73) — 88/150 parcial:** fases iniciais sem entrega
   na fonte (62 excluídas da amostra, nunca zeradas). Validação dinâmica
   obrigatória.
5. **Série D BR (76):** `statistics_fixtures=false` (coverage oficial).
   Eventos só com gols/VAR; sem grupos em standings. Backtest INVIÁVEL
   para corners/cards.
6. **Endpoints inexistentes:** `lineups`, `coaches`, `referees` (como
   endpoint próprio) não existem na conta; dados de árbitro vêm em
   `fixture.referee` (parcial, só nome).
7. **Pressão 5/10/15 = EXPERIMENTAL / A CALIBRAR:** nenhum threshold
   operacional (BAIXA/MODERADA/ALTA) validado. A matriz não cria
   threshold (FASE H). Backtest futuro necessário.
8. **RESULTADO = EXPERIMENTAL EM OBSERVAÇÃO:** a matriz de cobertura
   não promove mercado experimental para validado (FASE H).
9. **NM Cupen (105) vs Eliteserien (103):** erro de mapeamento
   corrigido — 105 (NM Cupen, 0/3 stats) era tratada como Eliteserien;
   103 (28/28 stats) é a liga real. 105 → classe E; 103 → classe B
   (matriz apenas; **não promove ao universo sem operador**).
10. **Paraguai (252) rebaixado A→B:** 52/56 = 93% (< 95%); borda
    inferior da classe A. Permanece PERMITIDO com rótulo honesto.

---

## 3. Regras aplicadas (FASE B–H)

- **Estados por MERCADO** (FASE B): PERMITIDO / OBSERVAÇÃO / BLOQUEADO /
  NÃO TESTADO. **Não há bloqueio global**: se falta corners, GOALS
  continua (`familias_para_deep_dive` filtra por mercado).
- **LIVE com pressão 5/10/15** (FASE C): LIVE COMPLETO / PARCIAL /
  INSUFICIENTE / NÃO TESTADO usando minuto, placar, Corner Kicks, Total
  Shots, Shots on Goal, Blocked Shots, Ball Possession. LIVE PARCIAL não
  é tratado como completo. Campo ausente → explícito. **None nunca vira
  zero.**
- **Odds** (FASE D): PRE quando realmente retornadas; LIVE não disponível
  na fonte (não inventa). Sem odd real → `OPORTUNIDADE ESTATÍSTICA` +
  "ODD AO VIVO NÃO DISPONÍVEL NA FONTE".
- **Ligas bloqueadas** (FASE E): motivo, dados ausentes, mercados
  afetados, evidência e condição para reconsideração registrados em
  `_NOTA_ESPECIAL` e nas listas acima. **Não desbloquear sem evidência
  forte.**
- **Filtro antes de chamadas caras** (FASE F): a matriz é consultada
  antes de `/fixtures/statistics`, `/odds`, etc. Por COMPETIÇÃO ×
  MERCADO × MODO. Não descarta jogo inteiro quando apenas um mercado é
  insuficiente.
- **Backtest** (FASE G): VIÁVEL / PARCIAL / INVIÁVEL / A CONFIRMAR.
  **NÃO executar backtest nesta etapa. Somente preparar a matriz.**
- **Cobertura ≠ validação estatística** (FASE H): RESULTADO pode ter
  FONTE SUFICIENTE e continuar EXPERIMENTAL EM OBSERVAÇÃO. Pressão
  5/10/15 continua EXPERIMENTAL / A CALIBRAR. A matriz não promove
  mercado experimental para validado.

---

## 4. Reclassificações da ETAPA 4 (13/09/2026)

| ID | Competição | Antes | Depois | Evidência |
|---|---|---|---|---|
| 848 | UEFA Conference League | A | C | 135/263 encerrados com stats (51%); 128 vazios em FT/AET/PEN |
| 252 | Division Prof. Paraguai | A | B | 52/56 = 93% (< 95%) |
| 105 | NM Cupen (Noruega) | B | E | 0/3 stats; era mapeado por erro como Eliteserien |
| 103 | Eliteserien (Noruega) | — | B | 28/28 stats (100%); adicionada à matriz (não ao universo) |

**Nenhuma liga foi desbloqueada sem evidência forte.** Nenhuma liga
promovida ao universo de análise (`LIGAS_PRIORITARIAS`/`LIGAS_EXTENSAO`)
— 103 entrou na matriz de cobertura apenas.