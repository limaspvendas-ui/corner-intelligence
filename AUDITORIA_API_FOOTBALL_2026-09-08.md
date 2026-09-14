# AUDITORIA TÉCNICA COMPLETA — API-FOOTBALL v3
**Data:** 08/09/2026 · **Plano atual:** API-Football v3 (limites observados: 300 req/min, 7.500 req/dia)
**Método:** análise de 6.018 respostas já em cache (custo zero) + 42 chamadas reais controladas (cota: 7.500 → 6.953) + prova ao vivo com jogos em andamento (UCL, Eredivisie, Saudi Pro League, Primera C) + investigação específica da Série D.
**Registro:** nenhum código alterado, nada implementado, nenhuma recomendação registrada, ledger intocado.

---

## ETAPA 1 — MAPEAMENTO DE TODOS OS ENDPOINTS POR GRUPO

Legenda: ✅ existe na nossa conta · ❌ NÃO existe na nossa conta (HTTP 200 + `{"endpoint": "The X endpoint does not exist."}` — o plano atual não inclui) · 🔶 existe mas requer re-teste · "Já usamos" = chamado pelo código da plataforma.

### Countries
| Rota | Finalidade | Parâmetros | Campos | Nossa conta | Já usamos |
|---|---|---|---|---|---|
| `/countries` | Lista de países | `name`, `code`, `search` | name, code, flag | ✅ 171 países | Não |

### Leagues
| Rota | Finalidade | Parâmetros | Campos | Nossa conta | Já usamos |
|---|---|---|---|---|---|
| `/leagues` | Ligas + temporadas + **coverage** | id, name, country, season, type, current, search, code | league{id,name,type,country,logo}, seasons[]{year,start,end,current,coverage{...}} | ✅ | **Sim** (fonte de coverage) |
| `/leagues?country=X&current=true` | Ligas ativas de um país | idem | idem | ✅ (108 BR / 178 World) | Não |

- Coverage por liga-temporada (booleans OFICIAIS da API): `fixtures.{events,lineups,statistics_fixtures,statistics_players}`, `standings`, `players`, `top_scorers`, `top_assists`, `top_cards`, `injuries`, `predictions`, `odds`.
- **Atenção:** flag `lineups=true` não significa que o endpoint existe — na nossa conta `/lineups` não existe. As flags são a INTENÇÃO de coleta da API, não garantia por jogo.

### Seasons
- Não existe endpoint separado. Temporadas vêm embutidas no `/leagues` (array `seasons` com coverage por ano). FORNECIDO DIRETAMENTE.

### Teams
| Rota | Finalidade | Parâmetros | Nossa conta | Já usamos |
|---|---|---|---|---|
| `/teams` | Dados dos times | id, name, league, season, country, search, venue | ✅ | **Sim** |
| `/teams/statistics` | Estatísticas agregadas time×liga×temporada | team, league, season | ✅ | **Sim** |

### Venues
| Rota | Finalidade | Parâmetros | Campos | Nossa conta | Já usamos |
|---|---|---|---|---|---|
| `/venues` | Estádios | id, name, city, country, search | id, name, address, city, country, **capacity**, **surface** (grama/sintético), image | ✅ | Não |

### Fixtures
| Rota | Finalidade | Parâmetros | Nossa conta | Já usamos |
|---|---|---|---|---|
| `/fixtures` | Jogos (futuros, ao vivo, passados) | id, ids, live=all, date, league, season, team, last, next, from/to, round, status, venue, timezone | ✅ | **Sim** (by id/date/league/team/next/last/live/headtohead) |
| `/fixtures?live=all` | Todos os jogos ao vivo (50 no momento do teste) | live | ✅ status+elapsed atualizados | **Sim** |
| `/fixtures/rounds` | Rodadas da temporada | league, season, current | ✅ (38 rodadas Brasileirão) | Não |
| `/fixtures/events` | Eventos do jogo | fixture, team, type, player | ✅ | **Sim** |
| `/fixtures/statistics` | Estatísticas por jogo (16–19 tipos) | fixture, **half=true** (split 1T/2T) | ✅ | **Sim** (com half) |
| `/fixtures/players` | Estatísticas por jogador no jogo | fixture, team | 🔶 respondeu `results=0` no jogo testado (Real Madrid x Inter de HOJE — ainda não encerrado); existe na API, mas cobertura por liga depende da flag `statistics_players` | Não |

### Lineups
| Rota | Nossa conta |
|---|---|
| `/lineups` | **❌ "The Lineups endpoint does not exist."** — escalações INDISPONÍVEIS na conta atual |

### Statistics (jogador/estatísticas gerais)
| Rota | Finalidade | Nossa conta | Já usamos |
|---|---|---|---|
| `/players` | Ficha + estatísticas do jogador por temporada | id, team, league, season, page | ✅ (1 resultado testado) | Não |
| `/players/topscorers` | Artilheiros por liga-temporada | league, season | ✅ (20 no Brasileirão 2026) | Não |
| `/players/topassists`, `/players/topcards` | Assistentes / cartões | league, season | ✅ presumido (mesma família do topscorers; flags `top_assists`/`top_cards` true no coverage) | Não |
| `/trophies` | Títulos por jogador/técnico | player, coach | ✅ (94 registros testados) | Não |
| `/sidelined` | Histórico de ausências (lesão/suspensão) | player, coach | ✅ (17 registros testados) | Não |

### Coaches
| Rota | Nossa conta |
|---|---|
| `/coaches` | **❌ "The Coaches endpoint does not exist."** — dados de técnicos INDISPONÍVEIS na conta atual |

### Injuries
| Rota | Finalidade | Parâmetros | Campos | Nossa conta | Já usamos |
|---|---|---|---|---|---|
| `/injuries` | Lesões/ausências | fixture, team, player, league, season | player{id,name,photo,**type,reason**}, team, fixture, league | ✅ (20 registros no jogo testado) | Não |

### Standings
| Rota | Finalidade | Parâmetros | Campos | Nossa conta | Já usamos |
|---|---|---|---|---|---|
| `/standings` | Classificação | league, season | rank, team, points, goalsDiff, group, **form (WWWLW)**, status, **description (zonas de classificação/rebaixamento)**, all/home/away{played,win,draw,lose,goals{for,against}}, update | ✅ | Não |

### Predictions
| Rota | Finalidade | Nossa conta | Já usamos |
|---|---|---|---|
| `/predictions` | Predição da API por jogo | fixture | ✅ (funciona, mas superficial — ver ETAPA 3) | Não |

### Odds
| Rota | Finalidade | Nossa conta | Já usamos |
|---|---|---|---|
| `/odds` | Odds PRÉ-JOGO | fixture, league, season, bet, bookmaker, date, timezone | ✅ (6–14 bookmakers, **183 mercados**) | **Sim** |
| `/odds/live` | Odds AO VIVO | fixture, league, bet | **❌ NA PRÁTICA: retorna sempre `bookmakers: []`** — testado 33x (28 em cache + 5 probes ao vivo hoje: UCL, Eredivisie, Saudi Pro League, Primera C). O plano atual NÃO entrega odds live. | **Sim** (sempre vazio) |
| `/bets` | Catálogo de mercados | — | **❌ "The Bets endpoint does not exist."** (enumeramos via /odds) | Não |
| `/bookmakers` | Catálogo de casas | — | **❌ "The Bookmakers endpoint does not exist."** (enumeramos via /odds) | Não |

### Transfers
| Rota | Finalidade | Nossa conta | Já usamos |
|---|---|---|---|
| `/transfers` | Transferências | player, team | ✅ (338 registros Real Madrid: player, update, transfers[]{date,type,teams in/out}) | Não |

### Referees
| Rota | Nossa conta |
|---|---|
| `/referees` | **❌ "The Referees endpoint does not exist."** — só existe o NOME (string) em `fixture.referee` |

### Weather / News
- Não existem rotas. Nenhum campo de clima em fixture; nenhuma rota de notícias. (ETAPAs 13–14.)

---

## ETAPA 2 — CONTEXTO COMPETITIVO

| Item | Como obter | Status |
|---|---|---|
| Liga vs. Copa | `/leagues` campo `type` ("League"/"Cup") | FORNECIDO DIRETAMENTE |
| Rodada | `league.round` no fixture ("Regular Season - 27", "League Stage - 1") | FORNECIDO DIRETAMENTE |
| Lista de rodadas da temporada | `/fixtures/rounds` (Brasileirão: 38; UCL: Qualifying, League Stage 1–8, Play-offs, Round of 16, Quarter-finals, Semi-finals, Final) | FORNECIDO DIRETAMENTE |
| Fase (grupos/mata-mata) | inferível por `round` ("League Stage" vs. "Round of 16" etc.) | FORNECIDO PARCIALMENTE (via string da rodada) |
| Ida e volta | o round NÃO distingue 1º/2º jogo (ex.: "Round of 16" aparece igual nas duas partidas) | PODE SER CALCULADO: agrupar fixtures da mesma round+season com os mesmos times e ordenar por data |
| Placar agregado | não existe campo | PODE SER CALCULADO: soma dos gols dos dois fixtures da mesma eliminatória (com regra de gols fora) |
| Classificação/grupos | `/standings` (com grupos separados — útil em fases de grupos; copas mata-mata não têm) | FORNECIDO DIRETAMENTE |
| Zonas de classificação/desempate | campo `description` por linha do standings ("Promotion - Copa Libertadores (Group Stage)") | FORNECIDO PARCIALMENTE (descrição textual, não regras) |
| Critérios de desempate | não existe | NÃO FORNECIDO (aplicar regras internamente se necessário) |
| Prorrogação | `fixture.score.extratime` + status "Match Finished" com extratime preenchido | FORNECIDO DIRETAMENTE (quando houve) |
| Pênaltis | `fixture.score.penalty` + status AET/PEN | FORNECIDO DIRETAMENTE (quando houve) |
| Placar intervalo | `fixture.score.halftime` | FORNECIDO DIRETAMENTE |
| "To Qualify" (odds de classificação) | mercado BET 61 "To Qualify" observado 1x em /odds (mata-mata) | FORNECIDO PARCIALMENTE (raro; só quando o bookmaker abre) |

---

## ETAPA 3 — DADOS PRÉ-JOGO

| Item | Fonte | Status |
|---|---|---|
| Jogos futuros (data/local/rodada) | `/fixtures?next=N` | ✅ |
| Árbitro | `fixture.referee` | ⚠ PARCIAL — só o nome (sem ID/histórico); presente em 5.693/18.411 fixtures do cache; **nos jogos do Brasileirão a 4 dias do jogo está `null`** — é atribuído próximo ao jogo; nos jogos da UCL de HOJE já estava preenchido |
| Estádio/cidade | `fixture.venue{name,city}` + `/venues` (capacidade, tipo de gramado) | ✅ |
| H2H | `/fixtures/headtohead` | ✅ (já usamos) |
| Forma recente | `/standings` campo `form` (últimos 5) + `/predictions` → `teams.{home,away}.last_5{att,def,goals}` | ✅ |
| Classificação + splits casa/fora | `/standings` | ✅ |
| Estatísticas do time na temporada | `/teams/statistics` (já usamos) | ✅ |
| Lesões | `/injuries?fixture=` | ✅ (reason: Hamstring, Hip, Illness, Injury, Knee, Muscle, **Red Card**) |
| Suspensão por vermelho | `/injuries` com reason "Red Card" | ⚠ PARCIAL |
| Suspensão por acúmulo de amarelos | não existe | ❌ NÃO FORNECIDO |
| Escalações | `/lineups` | ❌ ENDPOINT NÃO EXISTE NA NOSSA CONTA |
| Técnico | `/coaches` | ❌ ENDPOINT NÃO EXISTE NA NOSSA CONTA |
| Odds pré-jogo | `/odds` | ✅ 6–14 bookmakers · 183 mercados (ver ETAPA 10) |
| Predição da API | `/predictions` | ⚠ PARCIAL: entrega `advice` ("Double chance : Coritiba or draw"), `percent` (45/45/10), `comparison` (form/att/def/poisson/h2h/goals/total). Mas: `under_over` e `goals` frequentemente `null`; no início de temporada zera (Real Madrid x Inter: 50/50/0 e comparison zerado); em jogos da UCL de hoje respondeu **"No predictions available"**. Não é fonte de probabilidade numérica robusta. |
| Clima | — | ❌ NÃO FORNECIDO (ETAPA 13) |
| Notícias/contexto | — | ❌ NÃO FORNECIDO (ETAPA 14) |

---

## ETAPA 4 — DADOS AO VIVO

**Testado ao vivo em 08/09/2026** com jogos em andamento: UCL (AEK x Lask, Club Brugge x Aston Villa), Eredivisie (NEC x Excelsior), Saudi Pro League (Al-Ittihad x Al-Fayha), Primera C-ARG.

| Item | Status | Evidência |
|---|---|---|
| Lista de jogos ao vivo + minuto | ✅ | `/fixtures?live=all` → 50 jogos com `status.short` (1H/2H/HT), `elapsed`, placar |
| Estatísticas live (liga coberta) | ✅ | `/fixtures/statistics?fixture=` DURANTE o jogo: 16 tipos ao vivo (escanteios, posse, chutes, cartões...). Dupla leitura com 75s de intervalo mostrou **atualização em ≤75s** (passes mudaram entre as leituras na UCL) |
| Eventos live | ✅ | `/fixtures/events` com Card/Goal/Var/subst + `time.elapsed`/`time.extra` |
| Árbitro live | ✅ (nome) | preenchido nos jogos de hoje |
| Odds live | ❌ | `/odds/live` retornou `bookmakers: []` em TODAS as tentativas (UCL, Eredivisie, Saudi, Primera C) — 33 chamadas históricas (cache+probes), zero bookmakers. **O plano atual não entrega odds ao vivo.** |
| Escalações live | ❌ | endpoint não existe |
| Estatísticas por jogador live | 🔶 | `/fixtures/players` existe mas não testável com certeza (retornou 0 em jogo não encerrado); não confiável para o fluxo atual |

### Reconstrução de pressão (últimos 5/10 min), aceleração de escanteios/finalizações, mudança de posse
- A API entrega apenas **totais acumulados** por jogo (não existe série temporal por minuto, nem "attacks/dangerous attacks" — o tipo de estatística não existe na API-Football).
- **VEM "PARCIALMENTE PRONTO":** para pressão de 5/10 min é preciso **armazenar snapshots periódicos** (ex.: a cada 60s de `/fixtures/statistics` durante jogos monitorados) e calcular DELTAS entre snapshots. O dado bruto por minuto NÃO é fornecido.
- Veredito: **PODE SER CALCULADO — exige armazenamento próprio de snapshots** (o cache atual da plataforma tem TTL 60s para live, ou seja, sobrescreve; não guarda histórico).

---

## ETAPA 5 (PRIORITÁRIA) — COBERTURA LIVE POR COMPETIÇÃO

Classes: **A** = completa (16 stats live + events + árbitro + odds pré) · **B** = boa com ressalva · **C** = parcial/inconsistente · **D** = só placar/events · **E** = inutilizável para análise.

Classificação por evidência: (1) coverage oficial do `/leagues`; (2) taxa real de entrega de statistics nos jogos já consultados pela plataforma (cache: 4.385 consultas a statistics mapeadas por liga); (3) teste live de hoje.

### Classe A — CONFIÁVEL (statistics entregues ≥95% dos jogos)
| ID | Competição | Evidência |
|---|---|---|
| 71 | Brasileirão Série A | 261/261 jogos com dados (100%) · coverage completo |
| 72 | Série B | 46/46 (100%) |
| 73 | Copa do Brasil | 40/41 |
| 2 | UEFA Champions League | 2026: 45/90 (os 45 vazios são jogos de hoje/ainda não jogados); 2025: 100% · **LIVE testado hoje: 16 stats ao vivo** |
| 3 | UEFA Europa League | 14/14 |
| 848 | Conference League | 5/6 — BORDA: quase A |
| 39 | Premier League (Inglaterra) | 34/36 (2 vazios = futuros) |
| 140 | La Liga | 45/45 + 56/56 (2025) |
| 135 | Serie A (Itália) | 83/83 |
| 78 | Bundesliga | 7/7 + 10/10 |
| 61 | Ligue 1 | 3/3 + 10/10 |
| 88 | Eredivisie | 73/73 · **LIVE testado hoje: 16 stats ao vivo** |
| 94 | Primeira Liga (Portugal) | 73/73 |
| 203 | Süper Lig | 93/93 |
| 204 | 1. Lig (Turquia) | 113/113 |
| 106 | Ekstraklasa (Polônia) | 60/60 |
| 113 | Allsvenskan | 161/161 |
| 144 | Jupiler Pro League (Bélgica) | 15/15 |
| 128 | Liga Profesional Argentina | 374/378 |
| 262 | Liga MX | 68/68 |
| 307 | Saudi Pro League | 48/49 · **LIVE testado hoje: 16 stats ao vivo** |
| 281 | Primera División (Uruguai) | 224/225 |
| 239 | Primera A (Colômbia) | 275/276 |
| 242 | Liga Pro (Equador) | 227/232 |
| 252 | División Profesional (Paraguai) | 52/56 |
| 13 | CONMEBOL Libertadores | 39/39 |
| 11 | CONMEBOL Sudamericana | 34/34 |
| 772 | Leagues Cup | 60/61 |
| 89 | Eerste Divisie (Holanda 2ª) | 95/97 |
| 475 | Paulista A1 | 26/26 (mas coverage `odds=false` → sem odds pré-jogo) |
| 624 | Carioca 1 | 17/17 (idem: `odds=false`) |

### Classe B — BOA, com ressalva
| ID | Competição | Ressalva |
|---|---|---|
| 233 | Premier League (Egito) | 34/36 |
| 114 | Superettan (Suécia 2ª) | 3/3 — amostra pequena |
| 141 | Segunda División (Espanha) | 3/3 — amostra pequena |
| 41 | League One (Inglaterra) | 3/3 — amostra pequena |
| 95/136/137 | Segunda Liga/Coppa Italia | amostras pequenas, 100% |
| 16 | CONCACAF Champions League | 4/4 — amostra pequena |
| 197/283/286/344/383/172/119/62/61(2ª) | várias ligas de 1ª divisão menores | amostras de 1–6 jogos, 100% |
| 525/531/528/529 | UCL Women / UEFA Super Cup / Community Shield | com dados, amostra mínima |

### Classe C — PARCIAL / inconsistente (usar com verificação por jogo)
| ID | Competição | Evidência |
|---|---|---|
| 479 | Canadian Premier League | 41/81 (50%) — metade dos jogos sem stats |
| 82 | Frauen Bundesliga | 11/23 (48%) |
| 45 | FA Cup | 2/4 em 2026 (3/3 em 2025) |
| 134 | Torneo Federal A (Argentina) | 2/4 |
| 525 | UCL Women | 3/4 |

### Classe D/E — NÃO UTILIZAR (0 statistics entregues)
| ID | Competição | Evidência |
|---|---|---|
| **75** | **Série C (Brasil)** | **0/196** · coverage oficial `statistics_fixtures=false` |
| **76** | **Série D (Brasil)** | coverage oficial `statistics_fixtures=false` · teste real: 0 grupos de stats (ver ETAPA 7) |
| 129 | Primera Nacional (Argentina) | 0/78 |
| 130 | Copa Argentina | 0/8 |
| 241 | Copa Colombia | 0/58 |
| 205 | 2. Lig (Turquia) | 0/14 |
| 219 | 2. Liga (Áustria) | 0/33 |
| 138/943/774-779 | Serie C italiana / divisões menores | 0/N |
| 667 | Amistosos | 0/210 |
| 477 | Gaúcho | 0/11 |
| 629 | Mineiro | 0/15 |
| 606 | Paranaense | 0/10 |
| 604 | Catarinense | 0/10 |
| 627 | Paraense | 0/10 |
| 612 | Copa do Nordeste | 0/4 |
| 290 | Persian Gulf Pro League | 0/6 |
| 673 | Liga MX Femenil | 0/4 |
| 906/1612/1218/1240/871/1086/1179 | Reservas/U20/U23/U17 | 0/N |
| 132/131 | Primera C/B Metropolitana (Argentina) | 0/3 + teste live hoje (Primera C): stats VAZIAS ao vivo |
| 887/173/652/653/361-371/382 etc. | divisões de 2ª/3ª da Europa/Ásia/África | 0/N |

**Padrão estrutural confirmado:** as divisões inferiores brasileiras (C, D), estaduais (exceto Paulista A1 e Carioca 1), copas regionais BR, divisões inferiores argentinas/turcas/austríacas e amistosos NÃO têm cobertura de estatísticas — nem pré, nem pós, nem live. Não é atraso: é `coverage=false` oficial (estrutural).

---

## ETAPA 6 — TABELA DE COBERTURA LIVE POR MERCADO

Critério: para gols live serve qualquer liga (placar é core da API); para escanteios e cartões live é preciso `statistics` durante o jogo (escanteios NÃO existem em events; cartões existem em events, mas em ligas descobertas os events são incompletos — ver Série D).

| Competição | GOLS LIVE | ESCANTEIOS LIVE | CARTÕES LIVE | NÍVEL | OBS |
|---|---|---|---|---|---|
| Brasileirão Série A | ✅ CONFIÁVEL | ✅ CONFIÁVEL | ✅ CONFIÁVEL | A | 261/261 com stats; referee próximo ao jogo |
| Série B | ✅ | ✅ | ✅ | A | 46/46 |
| Copa do Brasil | ✅ | ✅ | ✅ | A | 40/41 |
| **Série C** | ✅ | ❌ NÃO UTILIZAR | ⚠ PARCIAL (events incompletos) | E | 0/196 stats |
| **Série D** | ✅ | ❌ NÃO UTILIZAR | ❌ NÃO UTILIZAR | E | 0 stats; events só gols (ETAPA 7) |
| Libertadores / Sudamericana | ✅ | ✅ | ✅ | A | 39/39 · 34/34 |
| UCL / UEL / Conference | ✅ | ✅ | ✅ | A / A / B | LIVE verificado hoje na UCL (16 stats) |
| Premier League / La Liga / Serie A / Bundesliga / Ligue 1 | ✅ | ✅ | ✅ | A | 100% das amostras |
| Eredivisie / Eerste Divisie | ✅ | ✅ | ✅ | A | LIVE verificado hoje |
| Primeira Liga / Süper Lig / 1. Lig | ✅ | ✅ | ✅ | A | — |
| Ekstraklasa / Allsvenskan / Jupiler Pro | ✅ | ✅ | ✅ | A | — |
| Liga MX / Saudi Pro League | ✅ | ✅ | ✅ | A | Saudi LIVE verificado hoje |
| Argentina 1ª / Uruguai 1ª / Colômbia 1ª / Equador 1ª / Paraguai 1ª | ✅ | ✅ | ✅ | A | — |
| Paulista A1 / Carioca 1 | ✅ | ✅ | ✅ | A | mas **sem odds pré-jogo** (coverage `odds=false`) |
| Canadian CPL / Frauen Bundesliga | ✅ | ⚠ PARCIAL | ⚠ PARCIAL | C | ~50% dos jogos sem stats |
| Amistosos / reservas / U20/U23 | ✅ | ❌ | ❌ | E | 0 stats |
| Estaduais BR (exceto A1/Carioca) | ✅ | ❌ | ❌ | E | 0 stats |
| Divisões inferiores ARG/TUR/AUT/ITA | ✅ | ❌ | ❌ | E | 0 stats |

**ODDS LIVE: ❌ NÃO UTILIZAR em NENHMA competição** — `/odds/live` retorna sempre vazio na conta atual.

---

## ETAPA 7 — COMPETIÇÕES PROBLEMÁICAS: SÉRIE D (league 76, season 2026)

| Item | Resultado real | Evidência |
|---|---|---|
| Fixtures existem? | ✅ SIM | 10 últimos jogos retornados com id, data, times |
| Placar? | ✅ SIM | placares finais corretos em todos os 10 |
| Status/minuto? | ✅ SIM para status (FT nos testados); minuto live não testável (nenhum jogo ao vivo no momento) | — |
| Referee? | ❌ NÃO | `referee=None` em TODOS os 10 fixtures (no Brasileirão 4 dias antes já existem jogos sem árbitro também — na Série D é crônico) |
| Events? | ⚠ PARCIAL E INCONSISTENTE | jogo de 06/09: **2 events (só gols)**; jogo de 30/08: 3 events (gols+VAR). **Nenhum card, nenhuma substituição registrada** em 2 jogos testados — events NÃO servem para cartões |
| Statistics (escanteios, chutes, posse, cartões)? | ❌ VAZIO TOTAL | `/fixtures/statistics` retornou **0 grupos** em TODOS os fixtures testados (inclusive jogos ENCERRADOS) — não é atraso, é coverage oficial `statistics_fixtures=false, statistics_players=false` |
| Escanteios? | ❌ NÃO EXISTEM | não vêm em statistics (vazio) nem em events |
| Cartões? | ❌ NÃO CONFIÁVEL | statistics vazio; events incompletos (sem Card nos testados) |
| Chutes/Posse? | ❌ NÃO EXISTEM | — |
| Atraso ou permanente? | **PERMANENTE/ESTRUTURAL** | flag oficial de coverage da temporada 2026 = false; vale para todo o campeonato |
| Varia por jogo? | A estrutura é sempre vazia; events variam (2–3 por jogo, só gols/VAR) | — |
| Standings? | ✅ existem (1 grupo) | — |
| Odds? | coverage oficial `odds=true` (pré-jogo) | — |

### VEREDITO SÉRIE D
> **SÉRIE D — NÃO UTILIZAR PARA ANÁLISE LIVE DE ESCANTEIOS/CARTÕES.**
> Não há NENHUMA estatística (nem pós-jogo). Serve apenas para: placar/gols (pré e live), fixtures, classificação, odds pré-jogo. O mesmo vale para a **Série C** (0/196) e os demais níveis E da ETAPA 5.

---

## ETAPA 8 — WHITELIST LIVE

| Nível | IDs |
|---|---|
| **A — LIBERAR (confiável para gols/escanteios/cartões live)** | 71 (Série A BR), 72 (Série B), 73 (Copa do Brasil), 2 (UCL), 3 (UEL), 39 (Premier League), 140 (La Liga), 135 (Serie A ITA), 78 (Bundesliga), 61 (Ligue 1), 88 (Eredivisie), 94 (Primeira Liga), 203 (Süper Lig), 204 (1. Lig), 106 (Ekstraklasa), 113 (Allsvenskan), 144 (Jupiler Pro), 128 (ARG 1ª), 262 (Liga MX), 307 (Saudi Pro League), 281 (Uruguai 1ª), 239 (Colômbia 1ª), 242 (Equador 1ª), 252 (Paraguai 1ª), 13 (Libertadores), 11 (Sudamericana), 772 (Leagues Cup), 848 (Conference — borda A/B), 89 (Eerste Divisie), 475 (Paulista A1 — sem odds), 624 (Carioca 1 — sem odds) |
| **B — LIBERAR COM OBSERVAÇÃO (amostras pequenas, 100% até agora)** | 233 (Egito), 114 (Superettan), 141 (Segunda ESP), 41 (League One), 95 (Segunda Liga POR), 136 (Série B ITA), 137 (Coppa Italia), 16 (CONCACAF CL), 197 (Grécia), 344, 383 (Israel), 172, 119, 62, 105+ (Noruega etc. — validar com amostra maior antes de liberar) |
| **OBSERVAÇÃO (parcial ~50%, verificar por jogo)** | 479 (Canadian CPL), 82 (Frauen Bundesliga), 45 (FA Cup 2026), 134 (Torneo Federal A), 525 (UCL Women) |
| **BLOQUEAR para análise live de escanteios/cartões** | **75 (Série C), 76 (Série D)**, 129 (Primera Nacional), 130 (Copa Argentina), 241 (Copa Colombia), 205 (2. Lig TUR), 219 (2. Liga AUT), 138/943 (Serie C ITA), 667 (Amistosos), 477 (Gaúcho), 629 (Mineiro), 606 (Paranaense), 604 (Catarinense), 627 (Paraense), 612 (Copa do Nordeste), 290 (Persian Gulf), 673 (Liga MX Femenil), TODAS as ligas de reserva/U20/U23/U17 (906, 1218, 1240, 871, 1086, 1114, 1179...), 131/132 (Primera B/C ARG), 887, e as demais com 0/N na ETAPA 5 |

Temporada testada: 2026 (atual). Odds live: bloquear em todas (indisponível na conta).

---

## ETAPA 9 — HISTÓRICO E ESTATÍSTICAS

| Necessidade | Como obter | Status |
|---|---|---|
| Histórico por competição (temporada) | `/fixtures?league&season&from/to` + `/teams/statistics` + `/standings` | ✅ FORNECIDO (jogos completos desde temporadas anteriores no /leagues) |
| Stats por jogo no passado | `/fixtures/statistics` (histórico preservado pós-jogo; metade via `half=true`) | ✅ nas ligas classe A/B (⚠ classes D/E: nunca existiram) |
| Por fase/rodada específica | `/fixtures?league&season&round=` | ✅ PARCIALMENTE (rodadas de liga sim; mata-mata via round string) |
| Grupos de fase de grupos | `/standings` (array de grupos) + `league.round` | ✅ |
| Ida/volta | cálculo próprio sobre fixtures da mesma eliminatória | CALCULÁVEL |
| Agregado | cálculo próprio (gols ida+volta, fora-casa) | CALCULÁVEL |
| Prorrogação/pênaltis históricos | `score.extratime`/`score.penalty` + status | ✅ FORNECIDO |
| Artilharia/assistências/cartões por temporada | `/players/topscorers`, `topassists`, `topcards` | ✅ |
| Ficha do jogador por temporada | `/players?id&season` (rating, jogos, gols, passes, cartões...) | ✅ |
| Histórico de lesões/ausências | `/sidelined?player` (17 registros no teste) | ✅ |
| Transferências (contexto de elenco) | `/transfers` | ✅ |
| Títulos | `/trophies` | ✅ |
| Histórico de árbitro | NÃO FORNECIDO pela API → **CALCULÁVEL**: agregar `fixture.referee` (nome) sobre fixtures armazenados (cartões médios, média de gols por árbitro) | CALCULÁVEL (requer acúmulo próprio) |
| Estatísticas por jogador POR JOGO | `/fixtures/players` | 🔣 INCONCLUSIVO (retornou 0 no jogo não encerrado testado; cobertura por liga via flag `statistics_players`) |

---

## ETAPA 10 — TODOS OS MERCADOS DE APOSTAS REAIS (verificados na resposta real)

**Bookmakers observados no /odds pré-jogo (13 no cache + 888Sport hoje = 14):** William Hill (7), Bet365 (8), Marathonbet (2), Pinnacle (4), 1xBet (11), BetVictor (36), 10Bet (1), Unibet (16), Betfair (3), Betano (32), Superbet (34), Dafabet (9), SBO (5), 888Sport.
- Cobertura varia por jogo: UCL hoje = 14 bookmakers/183 mercados; Brasileirão da rodada 27 = 6 bookmakers/97 mercados (odds de jogos a 4 dias ainda não completas).
- **LIVE: ❌ NENHUM mercado em NENHUMA competição** (`/odds/live` sempre vazio na conta atual).
- **PLATAFORMA SUPORTA:** pré-jogo nos mercados que usa (resultado, gols O/U, escanteios, cartões e famílias derivadas) via `/odds`.

### 183 mercados observados (ID · nome · disponibilidade)
**Resultado/placar:** 1 Match Winner · 2 Home/Away · 3 Second Half Winner · 7 HT/FT Double · 9 Handicap Result (Europeu) · 10 Exact Score · 11 Highest Scoring Half · 12 Double Chance · 13 First Half Winner · 20 DC 1ºT · 33 DC 2ºT · 47 Winning Margin · 61 To Qualify (raro, mata-mata) · 86 RCARD (vermelho) · 110 Scoring Draw · 124/129/222 Come From Behind · 181 European Handicap 2ºT
**Handicaps asiáticos:** 4 AH · 19 AH 1ºT · 104 AH 2ºT · 50 Goal Line · 72 Goal Line 1ºT
**Gols O/U:** 5 Goals O/U · 6 O/U 1ºT · 26 O/U 2ºT · 16 Total Home · 17 Total Away · 25 Result/Total Goals · 49 Total/BTTS · 38 Exact Goals Number · 40/41 Team Exact Goals · 42 2ºT Exact Goals · 46 1ºT Exact Goals · 105–108 Team Totals por tempo · 114–117 Team Score a Goal por tempo · 43/44 Team Score a Goal · 34/35 BTTS por tempo · 8 BTTS · 113 BTTS Both Halves · 184 To Score in Both Halves · 27/28 Clean Sheet · 29/30/36 Win to Nil · 37/53 Win Both Halves · 32/39/48 To Win Either Half · 14/15 First/Last Team to Score · 185 First Team to Score 3-way · 99/100 To Score/Miss Penalty · 59 Own Goal · 97 First Goal Method · 229 Goal Method Outside Box · 245 Away Header · 192/193 Highest Scoring Half por time · 144–149 Goal in 15-min window · 197/198 O/U 15–30/30–45m · 54 First 10 min Winner · 136/139 1x2 por 15/30/60/75 min · 21/22/23/60/63 Odd-Even (total/1ºT/2ºT/home/away)
**ESCANTEIOS:** 45 Corners O/U · 55 Corners 1x2 · 56 Corners AH · 57/58 Home/Away Corners O/U · 77 Total Corners 1ºT · 127 Total Corners 2ºT · 85 Corners 3-way · 125/126 Corners AH 1ºT/2ºT · 130/131 Corners 1x2 1ºT/2ºT · 132–135 Team Corners por tempo · 239 Corners European Handicap · 247 Corners Race To (3/5/7/9) · 249 Multicorners · 295 Corners Range · 297 Corners 0–10min · 338 Corners Odd/Even · 339 Corners Double Chance
**CARTÕES:** 80 Cards O/U · 79 Cards European Handicap · 81 Cards AH · 82/83 Team Total Cards · 150/151 Team Yellow Cards O/U · 153 Yellow O/U · 155/156 Yellow O/U 1ºT/2ºT · 152/159/160 Yellow AH (total/por tempo) · 154/169 Yellow (e Fouls) Double Chance · 157 Yellow Odd/Even · 158/161/162 Yellow 1x2 (total/por tempo) · 250 First Card · 299 Cards 0–10min · 342 Red Card 1ºT
**FALTAS:** 170/171 Fouls Home/Away Total · 173 Fouls Total · 174 Fouls Handicap · 175 Fouls 1x2 · 172/343 Fouls Double Chance/Odd-Even
**IMPEDIMENTOS:** 164 Offsides Total · 165 Offsides 1x2 · 166 Offsides Handicap · 167/168 Offsides Home/Away Total · 169 Offsides DC
**CHUTES:** 87 Total ShotOnGoal O/U · 176 ShotOnTarget 1x2 · 177 ShotOnTarget Handicap · 211 Total Shots · 340 Shots 1x2
**JOGADOR:** 92 Anytime Goal Scorer · 93 First Goal Scorer · 94 Last Goal Scorer · 212 Player Assists · 213 Player Triples · 215 Player Singles · 218/219/226/231/232/233 Anytime/First/Last por casa-fora · 240/241/269/275/276 Player Shots/On Target · 257 Score or Assist · 266 Player Fouls · 267 Goalkeeper Saves
**DIVERSOS:** 78 RTG_H1 (result/tempo gols) · 296+ ranges · 349 Number of Goals ranges

> Todos ✅ PRÉ-JOGO (nas ligas com `odds=true` na coverage); todos ❌ LIVE na conta atual.
> Atenção: mercado 45 (Corners O/U) aparece com valores "Over 9.5/Under 9.5/Over 10/Under 10" — **linhas com .0 além das .5**.

---

## ETAPA 11 — LESÕES / SUSPENSÕES / ESCALAÇÕES / TÉCNICOS

| Item | Status | Detalhe |
|---|---|---|
| Lesões por jogo | ✅ `/injuries?fixture=` | player{id,name,photo,type,reason}; type="Missing Fixture"; 20 registros no jogo testado |
| Lesões por time/liga/jogador | ✅ `/injuries?team= /league= /player=` | — |
| Suspensão (vermelho) | ⚠ PARCIAL | detectável via `/injuries` reason="Red Card" |
| Suspensão por acúmulo de amarelos | ❌ NÃO FORNECIDO | calcular internamente com events dos jogos anteriores |
| Escalações (titulares/banco) | ❌ INDISPONÍVEL | `/lineups` não existe na conta |
| Técnico (nome/histórico) | ❌ INDISPONÍVEL | `/coaches` não existe na conta |
| Histórico de ausências do jogador | ✅ `/sidelined` | por jogador |

---

## ETAPA 12 — ÁRBITRO

| Item | Status |
|---|---|
| Nome | ✅ `fixture.referee` (string) — mas ausente em muitos jogos com antecedência (Brasileirão rodada 27, a 4 dias: `null`) |
| ID do árbitro | ❌ não existe |
| Endpoint /referees | ❌ "The Referees endpoint does not exist." |
| Histórico/médias do árbitro | ❌ NÃO FORNECIDO → **CALCULÁVEL**: agregando `fixture.referee` sobre o histórico de fixtures armazenados (cartões/jogos, escanteios/jogo, gols/jogo por árbitro). A plataforma já guarda referee nos fixtures em cache. |

---

## ETAPA 13 — CLIMA / CONDIÇÕES EXTERNAS

- **NÃO FORNECIDO.** Nenhum campo de weather em fixture; `/venues` dá apenas capacity + surface (grama/sintético) + endereço.
- Se necessário: exigiria fonte externa (ex.: API meteorológica por lat/long da cidade do venue).

---

## ETAPA 14 — NOTÍCIAS / CONTEXTO EXTERNO

- **NÃO FORNECIDO.** Sem rota de notícias.
- Inferível PARCIALMENTE com dados estruturados: `/transfers` (reforços/saídas), `/injuries` (desfalques), `/sidelined` (histórico de ausências), `/trophies`.
- Contexto extra (especulação, clima de vestiário, troca de técnico não anunciada em dados, motivação) = **somente fonte externa** (news API/scraping).

---

## ETAPA 15 — MATRIZ FINAL DE CAPACIDADES + 8 LISTAS

### Matriz resumida
| Área | A API oferece | Nós usamos hoje |
|---|---|---|
| Fixtures/placar/status | ✅ completo | ✅ |
| Estatísticas por jogo (16–19 tipos, com 1º/2º tempo) | ✅ (ligas classe A/B) | ✅ |
| Events | ✅ | ✅ |
| Odds pré-jogo (14 casas, 183 mercados) | ✅ | ✅ |
| Odds live | ❌ (vazio na conta) | chamado, sempre vazio |
| Lineups | ❌ (endpoint não existe) | ❌ |
| Coaches | ❌ (endpoint não existe) | ❌ |
| Referees (histórico) | ❌ (só nome) | não |
| Injuries/sidelined/transfers/trophies/players/topscorers | ✅ | ❌ (não usamos) |
| Standings/rounds/venues/countries | ✅ | ❌ (não usamos) |
| Predictions | ⚠ superficial | ❌ |
| Clima/notícias | ❌ | ❌ |

### As 8 listas
1. **Existe e NÃO aproveitamos:** /standings (forma, splits, zonas), /injuries, /sidelined, /transfers, /trophies, /players + topscorers/topassists/topcards, /venues (gramado/capacidade), /countries, /fixtures/rounds, /predictions (referência apenas), fixture.score.extratime/penalty, BETs de cartões (150–162), faltas (170–175), impedimentos (164–169), chutes (87/176/177/211/340), escanteios por tempo (77/127/125/126/130–135), Corners Race To (247), Multicorners (249), O/U por janela de 15 min (197/198), 1x2 por 15/30/60/75 min (136–139).
2. **Podemos CALCULAR internamente:** pressão 5/10 min (snapshots), aceleração de escanteios/chutes (deltas), ida/volta e agregado, histórico e médias de árbitro, suspensão por acúmulo (events), formas casa/fora customizadas, xG próprio (expected_goals existe nas stats 2026!), distribuição de gols por intervalo (events+elapsed), pontos por minuto, cartões por árbitro/rodada.
3. **Não entrega:** odds live (conta), lineups, coaches, histórico de árbitro por ID, clima, notícias, attacks/dangerous attacks (tipo inexistente), estatísticas por minuto (série temporal), acúmulo de amarelos, odds live por bookmaker em tempo real.
4. **Exigiria outra API/fonte de dados:** odds live (ou upgrade de plano API-Football), clima meteorológico, notícias/contexto, lineups em tempo real (se o plano não incluir), rating de jogadores ao vivo.
5. **Exige notícias/web:** motivação, especulação de mercado, bastidores, troca de técnico antes do anúncio, contexto extra-campo.
6. **Competições PRÉ-JOGO confiáveis:** classe A da ETAPA 5 + odds: Série A/B BR, Copa do Brasil, UCL/UEL, Premier League, La Liga, Serie A ITA, Bundesliga, Ligue 1, Eredivisie, Primeira Liga, Süper Lig, Liga MX, Saudi PL, Libertadores, Sudamericana, ARG 1ª, Uruguai/Colômbia/Equador/Paraguai 1ª, Ekstraklasa, Allsvenskan, Jupiler Pro. (Paulista A1/Carioca 1: stats sim, **odds não**.)
7. **Competições LIVE confiáveis (stats em tempo real):** as mesmas da classe A — live verificado HOJE em UCL, Eredivisie e Saudi Pro League (16 tipos, refresh ≤75s). Odds live: nenhuma.
8. **Mercados live confiáveis por competição:** NENHUM via odds live (conta não entrega). Escanteios/cartões/gols live confiáveis apenas via STATISTICS nas classes A/B — A: todas as da classe A; B: com amostragem maior antes de liberar.

---

## ETAPA 16 — RECOMENDAÇÃO DE ARQUITETURA (6 CAMADAS)

1. **Dados estruturados (base):** fixtures + statistics (+half) + events + standings + teams — já em uso; adicionar /standings (forma/zonas) e /venues (gramado) como enriquecimento pré-jogo de baixo custo (TTL alto).
2. **Contexto competitivo (regra):** camada derivada de league.round/type: classificar liga×copa, fase, ida/volta (agrupando por round+times), agregado, prorrogação/pênaltis (score.extratime/penalty) — tudo calculável do que já existe, sem nova dependência.
3. **Histórico contextual:** repositório próprio por liga-temporada com jogos+stats armazenados (o cache atual é TTL-curto; histórico de modelo exige persistência dedicada), incluindo splits casa/fora, por árbitro (referee string), por fase e por minuto de gol (events.elapsed).
4. **Pressão/intensidade LIVE:** poller de snapshots (ex.: 60s) de `/fixtures/statistics` APENAS para jogos da whitelist classe A/B (etapa 8) — deltas entre snapshots = pressão 5/10 min, aceleração de escanteios/chutes, variação de posse. Não existe na API; precisa ser construído e armazenado.
5. **Cobertura live por competição (whitelist):** aplicar as listas A/B/OBSERVAÇÃO/BLOQUEAR da ETAPA 8 com verificação por jogo (statistics não-vazias = confirmação implícita); bloquear automaticamente as classes E (Série C, Série D, estaduais, amistosos, reservas).
6. **Contexto externo/notícias:** por enquanto NÃO integrar (ver resposta I); as lacunas de clima/notícias/odds-live só fazem sentido quando a camada 4–5 estiver madura; odds live exigiria decisão comercial (upgrade de plano) ou outra fonte.

---

## RESPOSTAS OBJETIVAS A–J

**A) O que a API oferece hoje (conta/plano atual):**
Fixtures completos (pré/live/pós, com árbitro-nome, venue, status, extratime/penalty, halves), estatísticas por jogo (16–19 tipos, incl. escanteios, posse, chutes, cartões, xG em 2026; split 1º/2º tempo), events (gols, cartões, VAR, substituições, com minuto), odds PRÉ-jogo (14 bookmakers, 183 mercados), h2h, standings (forma+splits+zonas), teams/statistics, injuries, sidelined, transfers, trophies, players, topscorers/topassists/topcards, venues, countries, rounds, predictions (superficial).

**B) O que a plataforma já utiliza:**
/fixtures (por id/data/liga/time/next/last/live/h2h), /fixtures/events, /fixtures/statistics (+half), /leagues (coverage), /odds, /odds/live (sempre vazio), /teams, /teams/statistics.

**C) O que existe e NÃO utilizamos:**
/standings, /injuries, /sidelined, /transfers, /trophies, /players + top_scorers/assists/cards, /venues, /countries, /fixtures/rounds, /predictions (como referência), score.extratime/penalty, e ~170 dos 183 mercados de odds (a plataforma usa o núcleo resultado/gols/escanteios/cartões).

**D) O que podemos calcular internamente:**
Pressão 5/10 min e aceleração de escanteios/finalizações/posse (snapshots + deltas); ida/volta e agregado de mata-mata; histórico e médias de árbitro; suspensão por acúmulo de amarelos; gols por intervalo de minuto; formas customizadas; classificação projetada.

**E) O que definitivamente NÃO existe (na API ou na conta):**
CONTA: odds live (`/odds/live` sempre vazio), `/lineups`, `/coaches`, `/bets`, `/bookmakers`, `/referees`. API em si: clima, notícias, série temporal por minuto, "dangerous attacks", acúmulo de amarelos, ID/histórico de árbitro.

**F) Competições com LIVE confiável (gols/escanteios/cartões via statistics):**
Whitelist A da ETAPA 8: Série A BR, Série B, Copa do Brasil, UCL, UEL, Conference (borda), Premier League, La Liga, Serie A ITA, Bundesliga, Ligue 1, Eredivisie, Primeira Liga, Süper Lig, 1. Lig, Ekstraklasa, Allsvenskan, Jupiler Pro, Liga Profesional ARG, Liga MX, Saudi Pro League, Uruguai/Colômbia/Equador/Paraguai 1ª, Libertadores, Sudamericana, Leagues Cup, Eerste Divisie, Paulista A1, Carioca 1. Live verificado hoje: UCL, Eredivisie, Saudi.

**G) Competições a BLOQUEAR para LIVE (escanteios/cartões):**
**Série C (75) e Série D (76)** — Série D com veredito explícito acima; Primera Nacional e Copa Argentina, Copa Colombia, 2.ª/3.ª divisões ARG-TUR-AUT-ITA, amistosos, todos os estaduais BR exceto Paulista A1/Carioca 1, ligas de reserva/U20/U23/U17, Liga MX Femenil, Canadian CPL e Frauen Bundesliga (parciais,Observação), e demais classes D/E da ETAPA 5.

**H) Confiar para gols/escanteios/cartões live:**
GOLS: quase todas as competições (placar é core, inclusive Série C/D). ESCANTEIOS e CARTÕES: apenas whitelist A/B da ETAPA 8 — e sempre confirmando que as statistics do jogo em questão não estão vazias (verificação por jogo). **Odds live: nenhuma competição.**

**I) Vale integrar fonte de notícias agora?**
**NÃO.** O gargalo atual não é contexto externo: é (1) odds live indisponível na conta (decisão de plano/comercial, não de código), (2) ausência de snapshots live para pressão, (3) ~170 mercados de odds pré-jogo ainda não aproveitados e camadas de standings/injuries não integradas. Notícias agregariam menos valor por esforço do que essas três lacunas. Recomendação: só voltar a avaliar notícias/clima quando as camadas 4 e 5 da ETAPA 16 estiverem operando.

**J) Próxima melhoria da plataforma:**
Habilitar o **poller de snapshots live** (camada 4) restrito à whitelist classe A/B: coleta de `/fixtures/statistics` a cada ~60s durante os jogos monitorados, persistindo o histórico (hoje o cache live sobrescreve com TTL 60s e perde tudo) para permitir pressão 5/10 min, aceleração de escanteios/chutes e variação de posse — que é exatamente o insumo que a análise live de escanteios/cartões precisa e que a API não entrega pronto. (Sem custo de plano adicional; cabe na cota atual: 300 req/min → 1 jogo = 1 req/min.)

---
*Arquivo de auditoria — nenhuma alteração de código foi feita. Evidências brutas das chamadas estão em `_tmp_audit_api/` até a limpeza final.*