# ETAPA 5F-E2 — COLETA PROSPECTIVA DE ODDS MULTIPROVIDER

**COLETAR / NORMALIZAR / PERSISTIR — NÃO altera o motor de decisão.**

> **RESULTADO PRINCIPAL:** extensão multiprovider da camada de coleta de odds,
> reusando maximamente a infra 5F-C/5F-D (OddSnapshot, OddsSnapshotStore.
> append_many, _unit, adapters FiveDollarFootballProvider/TheOddsAPIProvider).
> 5Dollar entrega corners/cards/goalline/asian com fases **OPENING/CLOSING/
> INPLAY** explícitas; The Odds API entrega h2h/totals/spreads (sem fase —
> nunca inventada). **PROVIDER ≠ BOOKMAKER** garantido em colunas distintas.
> Hash V3 (com phase+market_canonical+market_original) para multiprovider;
> V2 preservado para api_football — **histórico jamais re-migrado**. Migração
> aditiva 3→4 (3 colunas nullable). Persistência append-only (INSERT OR IGNORE
> por hash). Motor intacto. Sem merge/push.

---

## ESTADO

- branch: `etapa-5fe-fallback-controlado` (sobre `19712f9`/5F-E1)
- testes: **653 passed / 18 skipped / 0 failed / 671 total** (629 + 24 novos)
- migração: `user_version 3 → 4` (aditiva, idempotente)
- backup: o da 5F-E1 (`data/corner_intelligence.db.bak_5fe1_20260914_150535`) permanece

## PRE-FLIGHT (Seção 1)

- HEAD base = `19712f9` ✓; branch `etapa-5fe-fallback-controlado` ✓;
  working tree tracked limpa ✓; `.env` gitignored ✓; 0 segredo versionado ✓;
  `PRAGMA integrity_check = ok` ✓; `user_version = 3` ✓.
- Baseline registrada (NÃO alterada): `odds_snapshot_history` = **317.951**
  (todos `api_football`); por coleta_tipo: `pre_match` 310.898 / `live` 7.053;
  min collected_at 2026-09-06 / max 2026-09-10. 21 colunas (sem phase/canonical).

## AUDITO DA INFRA EXISTENTE (Seção 2 — reuso máximo)

| Componente | Reuso | Papel em 5F-E2 |
|---|---|---|
| `OddSnapshot` | estendido | +3 campos: `phase`, `market_canonical`, `market_original` |
| `OddsSnapshotStore.append_many` | único caminho append | INSERT OR IGNORE por hash (V2/V3) |
| `_unit` / validação de status | reusado via `_status_odd` | mesmo contrato: invalido=>motivo, nunca zero |
| `FiveDollarFootballProvider._parse_odds` | reusado | entrega `NormalizedOdd` com side/line/phase parseados |
| `TheOddsAPIProvider.fetch_odds` | reusado | entrega `NormalizedOdd` h2h/totals/spreads |
| `_hash_identidade` (V2) | preservado | api_football (histórico + novas) |
| `_hash_identidade_v3` (novo) | — | multiprovider (phase+canonical+original) |

**NENHUM coletor paralelo.** Estrutura centralizada em `src/odds_coleta.py`.
`_construir_snapshots_multiprovider` é o único conversor NormalizedOdd→OddSnapshot.

## PROVIDERS (Seção 3)

| Provider | Mercados observados | Fase | coleta_tipo |
|---|---|---|---|
| `five_dollar_football` | corner, corner_asian, goalline, cards, cards_asian, asian, 1x2 | OPENING/CLOSING/INPLAY (explícita do feed) | opening/closing→pre_match; inplay→live |
| `five_dollar_football` (btts) | A CONFIRMAR | — | UNKNOWN (sem invenção) |
| `the_odds_api` | h2h, totals, spreads | NULL (não classifica — nunca inventada) | pre_match |
| `the_odds_api` (não suportados) | h2h_lay, alternate_totals, corners… | — | UNKNOWN |
| `api_football` (histórico) | preservado (317.951) | NULL | não reprocessado |

## NORMALIZAÇÃO CANÔNICA (Seções 4-5)

Colunas persistidas: `provider`, `bookmaker`, `fixture_id`, `coleta_tipo`,
`market_original` (cru, preservado), `market_canonical`, `familia`,
`subfamilia`, `lado`, `linha`, `odd`, `phase`, `status`, `motivo`,
`snapshot_hash`, `collected_at`, `fixture_date`, `e_pre_jogo`.

**PROVIDER ≠ BOOKMAKER**: `provider`=fonte da API (`five_dollar_football`),
`bookmaker`=casa de apostas (`Bet 365`). Colunas distintas, nunca conflitadas.

Famílias canônicas (Seção 5): MATCH_RESULT, TOTAL_GOALS, ASIAN_HANDICAP,
TOTAL_CORNERS, ASIAN_CORNERS, TOTAL_CARDS, ASIAN_CARDS. Sem mapeamento
seguro ⇒ `market_canonical=UNKNOWN`, `familia=UNMAPPED`, `status=UNMAPPED`,
motivo explícito — **sem invenção**.

## FASE (Seção 6)

5Dollar: `_phase_5dollar` extrai OPENING/CLOSING/INPLAY do `submarket`
("`<mk>/<phase_key>`"). Sem classificação explícita ⇒ `None` (nunca inventada).
The Odds API: `phase=NULL` (não classifica). Histórico: `NULL`.

## APPEND-ONLY + HASH (Seções 7-8)

- `append_many`: INSERT OR IGNORE por `snapshot_hash`. Repetir o mesmo
  snapshot factual (mesma hash) ⇒ ignorado (dedup). Odd mudou (1.85→1.90)
  ⇒ nova hash ⇒ novo snapshot (ambos preservados, nunca sobrescrito).
- **Hash V2** (api_football): inalterado — 317.951 hashes armazenados
  preservados, **sem re-migração**.
- **Hash V3** (multiprovider): V2 + `phase` + `market_canonical` +
  `market_original`. Distintas fases do mesmo mercado/linha/odd ⇒ hashes
  distintas ⇒ todas preservadas. Dispatch em `OddSnapshot.hash()` pelos
  campos populados.

## COLETA PROSPECTIVA (Seções 9-10)

`coletar_multiprovider(provider_name, store, ...)` — prospectiva apenas
(pre_match/live conforme fase do feed). NÃO preenche passado artificialmente.
Respeita `HARD_LIMIT` do adapter (5Dollar 15, TheOdds 10). Em
`RateLimitHit`/402/403/429 ⇒ STOP, registra `limite`, **nunca compra plano,
nunca re-tenta**. CLI: `oddscoleta multiprovider --provider ...`.

## LIMITES / SAFETY (Seção 10)

- 5Dollar FREE plan: 2 chamadas reais nesta etapa (fixtures + odds corner),
  dentro do HARD_LIMIT 15. Sem 402/403/429.
- The Odds API Free: 1 chamada real (fetch_odds EPL), dentro do HARD_LIMIT 10.
- Nenhum plano comprado/alterado. Nenhum limite ultrapassado.

## CHAMADAS REAIS CONTROLADAS (Seção 12)

| Provider | Chamadas | Resultado |
|---|---|---|
| The Odds API | 1 (`fetch_odds` soccer_epl h2h/totals/spreads) | 3262 snapshots, 2807 inseridos, 455 duplicados |
| 5Dollar | 2 (`fetch_fixtures` + `fetch_odds` corner fixture 2062437761) | 6 snapshots (2 OPENING + 2 CLOSING + 2 INPLAY), 6 inseridos |

Sem 402/403/429. Sem re-tentativa. Sem compra de plano.

## PERSISTÊNCIA (Seção 13)

| Dimensão | Antes | Depois |
|---|---|---|
| total | 317.951 | **320.764** (+2.813) |
| api_football | 317.951 | 317.951 (intacto) |
| five_dollar_football | 0 | 6 |
| the_odds_api | 0 | 2.807 |
| por phase | (null) 317.951 | (null) 320.758 + OPENING 2 + CLOSING 2 + INPLAY 2 |
| por market_canonical | (null) 317.951 | +TOTAL_CORNERS 6, MATCH_RESULT 1928, TOTAL_GOALS 522, ASIAN_HANDICAP 214, UNKNOWN 143 |
| por coleta_tipo | pre 310.898 / live 7.053 | pre 313.709 / live 7.055 |

Bookmakers observados (multiprovider): Bet 365 (5Dollar); LeoVegas, Unibet,
Winamax, Betfred, Tipico, etc. (TheOdds). PRE/LIVE: 5Dollar OPENING/CLOSING=
pre_match, INPLAY=live; TheOdds=pre_match.

## MOTOR PROTEGIDO (Seção 14)

`git diff --name-only` sobre analysis, politica_aprovacao, policy,
settlement, calibration, backtest, prejogo_opportunity, live_pressure, odds,
identity, ao_vivo, Poisson, thresholds, blend = **vazio**. Apenas
`src/odds_coleta.py` (camada de coleta — permitida), `src/app.py` (CLI) e
testes alterados. Nenhum módulo decisório importa `coletar_multiprovider` ou
`_construir_snapshots_multiprovider` (teste `test_motor_nao_importa_multiprovider_decisao`).

## TESTES (Seção 11 — A-N, determinísticos, sem rede)

24 testes em `tests/test_odds_coleta_multiprovider.py`:
A (corner opening), B (corner closing), C (corner inplay), D (cards),
E (asian cards), F (TheOdds totals), G (TheOdds h2h),
H (mesmo bookmaker 2 providers ⇒ provider distinto), I (repetido ⇒ não duplica),
J (odd mudou ⇒ append-only), K (desconhecido ⇒ UNKNOWN), L (NULL≠ZERO —
linha=0 vs None; odd None nunca vira 0), M (provider≠bookmaker),
N (provenência completa) + classificadores + phase + migração aditiva +
idempotência + isolamento do motor + coletar_multiprovider com mock +
fases distintas preservadas. 2 testes existentes em `test_odds_coleta.py`
atualizados (user_version 2→4 na cadeia de migrações).

## ARQUIVOS

- `src/odds_coleta.py` (estendido) — constantes canônicas, hash V3,
  classificadores 5Dollar/TheOdds, `_unit_multiprovider`,
  `_construir_snapshots_multiprovider`, `_migrar_para_5fe2`, registry
  multiprovider, `coletar_multiprovider`, status com phase/canonical.
- `src/app.py` (estendido) — subcomando `oddscoleta multiprovider`.
- `tests/test_odds_coleta_multiprovider.py` (novo) — 24 testes.
- `tests/test_odds_coleta.py` (ajustado) — user_version esperado 4.
- `docs/ETAPA_5FE2_COLETA_MULTIPROVIDER.md` (novo) — este relatório.
- DB: migração 3→4 aplicada + 2.813 registros multiprovider reais persistidos.

---

## RELATÓRIO FINAL (Seção 16)

| Pergunta | Resposta |
|---|---|
| BRANCH/HEAD? | `etapa-5fe-fallback-controlado` (base `19712f9`) |
| Infra reusada maximamente? | **SIM** (OddSnapshot, append_many, _unit, adapters, hash V2) |
| 5Dollar integrado? | **SIM** (corners/cards/goalline/asian, fases OPENING/CLOSING/INPLAY) |
| The Odds API integrado? | **SIM** (h2h/totals/spreads) |
| api_football histórico preservado? | **SIM** (317.951 intactos, não reprocessados) |
| PROVIDER ≠ BOOKMAKER? | **SIM** (colunas distintas: five_dollar_football/Bet 365) |
| OPENING/CLOSING/INPLAY preservadas? | **SIM** (5Dollar; TheOdds=NULL sem invenção) |
| CORNERS 5Dollar? | **SIM** (TOTAL_CORNERS, 6 registros reais) |
| CARDS 5Dollar? | **SIM** (TOTAL_CARDS/ASIAN_CARDS mapeados; testes D/E) |
| GOALS The Odds API? | **SIM** (TOTAL_GOALS 522 registros reais) |
| Append-only? | **SIM** (INSERT OR IGNORE por hash; odd mudou⇒novo) |
| Histórico alterado? | **NÃO** (317.951 intactos, hashes V2 preservados) |
| Qtd antes/depois? | 317.951 → 320.764 (+2.813) |
| Novos por provider? | five_dollar_football 6, the_odds_api 2.807 |
| Testes passed/skipped/failed/total | **653 / 18 / 0 / 671** |
| Motor intacto? | **SIM** (diff motor vazio) |
| Segredo versionado? | **NÃO** |
| 402/403/429? | **NÃO** (5Dollar 2/15, TheOdds 1/10; sem compra) |
| 5F-E2 aprovada? | **SIM** |
| Pronto para fechamento 5F-E? | **SIM** |

> **PARE.** NÃO iniciar integração das odds ao motor. NÃO calcular ROI para
> aprovação operacional. NÃO iniciar Etapa 6. NÃO mergear em main. NÃO push.