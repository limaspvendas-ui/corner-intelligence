# ETAPA 5F-E1 — FUNDAÇÃO DO FALLBACK FACTUAL CONTROLADO

**COLETAR / NORMALIZAR / RESOLVER / REGISTRAR — NÃO altera o motor de decisão.**

> **RESULTADO PRINCIPAL:** camada de resolução field-level isolada do motor,
> reusando a infra 5F-D (NormalizedFact, reconciliar_fixture, ConflictRegistry).
> Regra central **NULL != ZERO** garantida. Fallback só preenche com
> reconciliação MATCHED; primary explícito (incl. 0) nunca sobrescrito;
> divergências viram **CONFLITO_DE_FONTE** (ambos preservados). Proveniência
> completa + persistência append-only idempotente (user_version 2→3). Motor
> intacto. Sem merge/push.

---

## ESTADO

- branch: `etapa-5fe-fallback-controlado` (criada a partir de `c283469`)
- testes: **629 passed / 18 skipped / 0 failed / 647 total** (615 + 14 novos)
- backup DB: `data/corner_intelligence.db.bak_5fe1_20260914_150535` (integro)
- migração: `user_version 2 → 3` (aditiva, idempotente)

## PRE-FLIGHT

- HEAD base = `c283469` ✓; working tree tracked limpa ✓; `.env` gitignored ✓;
  0 segredo versionado ✓; `PRAGMA integrity_check = ok` ✓.

## POLÍTICA OFICIAL DE FONTES (Seção 3)

| Provider | Papel | Escopo operacional em E1 |
|---|---|---|
| `api_football` | PRIMARY_FACTUAL | fixture, times, kickoff, status, score, stats, corners, cards |
| `football_data_org` | FALLBACK_IDENTITY | só home/away/kickoff/status/score_home/score_away — **nunca** corners/cards/shots |
| `apifootball_com` | FALLBACK_CARDS | só red_cards/yellow_cards, quando primary é NULL |
| `statsbomb_open` | HISTORICAL_AUDIT | não operacional live nesta etapa |
| `sportmonks` | PLAN_LIMITED | não operacional nesta etapa |
| `five_dollar_football` / `the_odds_api` | ODDS | **não resolvidos em E1** → E2 |

## REGRAS CENTRAIS GARANTIDAS

1. **NULL != ZERO**: `None` = ausente; `0` = explícito. Nunca `None→0`.
   (Testes C, test_null_distinto_de_zero_nao_conflita.)
2. **MATCHED obrigatório**: AMBIGUOUS/NOT_MATCHED → fallback não preenche
   (registra motivo). (Testes D, E.)
3. **Primary explícito nunca sobrescrito**: `0` não vira `1`; divergência =
   CONFLITO_DE_FONTE preservando ambos. (Testes B, G.)
4. **Proveniência completa**: value, field, source_provider, fixture_id_source,
   fixture_internal, coleta_timestamp, is_fallback/is_primary,
   reconciliation_status, motivo, raw_values. (Teste H.)
5. **Idempotente**: hash determinístico + UNIQUE + INSERT OR IGNORE. Repetir a
   resolução não duplica nem muta. (Teste I.)

## RESOLUÇÃO FIELD-LEVEL (Seção 6)

```
primary = NULL, fallback = 1, MATCHED  =>  value=1, source=apifootball_com, is_fallback=True
primary = 0,   fallback = 1, MATCHED  =>  CONFLITO_DE_FONTE; primary=0 mantido; ambos preservados
primary = NULL, fallback = 0, MATCHED  =>  value=0 (zero válido do fallback; não virou None)
primary = NULL, fallback = 1, AMBIGUOUS/NOT_MATCHED => fallback NÃO utilizado
fallback não suporta campo             =>  NÃO utilizado (RES_FALLBACK_SEM_SUPORTE_CAMPO)
```

## PERSISTÊNCIA (Seção 9 — aditiva, append-only)

Novas tabelas (migração `user_version 2→3`, idempotente):
- `factual_resolution` — toda resolução field-level + proveniência + `raw_values`
  (JSON). UNIQUE(`resolution_hash`); INSERT OR IGNORE.
- `source_conflict` — conflitos durable (provider_a/b, value_a/b, timestamps,
  reconciliation_status, motivo, natureza). UNIQUE(`conflict_hash`).

**Histórico preservado**: `odds_snapshot_history` 317.951, `api_cache` 9.280,
`validated_teams` 502 — contagens idênticas antes/depois. Nenhuma tabela
existente alterada/apagada. Nenhum settlement/backtest/recomendação tocado.

Reuso: `ConflictRegistry`/`ConflictRecord` (in-memory, append-only) mantidos
para detecção; a tabela `source_conflict` adiciona durabilidade sem duplicar a
lógica.

## ISOLAMENTO DO MOTOR (Seção 10)

- `src/resolucao_factual.py` importa apenas `src/multifonte` (NormalizedFact,
  ST_OK/ST_MISSING) e `src/auditoria_multifonte` (reconciliação, ConflictRegistry).
- **Nenhum módulo decisório importa `resolucao_factual`**: analysis,
  politica_aprovacao, policy, settlement, calibration, backtest,
  prejogo_opportunity, live_pressure, odds, identity, ao_vivo, app, odds_coleta
  → 0 ocorrências (teste `test_motor_nao_importa_resolucao_factual`).
- `git diff --name-only -- <motor>` = **vazio**.
- A camada resolve/registra; **não muda decisão de aposta**.

## TESTES (Seção 11 — A-I, determinísticos, sem rede)

14 testes em `tests/test_resolucao_factual.py`: casos A-I + política de fontes
+ NULL!=ZERO + isolamento do motor + migração aditiva que preserva histórico.
Nenhuma chamada real a API (mocks/fixtures).

## ARQUIVOS

- `src/resolucao_factual.py` (novo) — resolver, política, migração, persistência.
- `tests/test_resolucao_factual.py` (novo) — 14 testes.
- `docs/ETAPA_5FE1_FALLBACK_FACTUAL.md` (novo) — este relatório.
- DB: migração aditiva aplicada (tabelas `factual_resolution`, `source_conflict`).

---

## RELATÓRIO FINAL (Seção 14)

| Pergunta | Resposta |
|---|---|
| BRANCH CRIADA? | **SIM** (`etapa-5fe-fallback-controlado`) |
| BACKUP DO BANCO? | **SIM** |
| INTEGRITY_CHECK? | **OK** |
| POLÍTICA DE FONTES IMPLEMENTADA? | **SIM** |
| NULL != ZERO GARANTIDO? | **SIM** |
| RECONCILIAÇÃO MATCHED OBRIGATÓRIA? | **SIM** |
| FALLBACK FIELD-LEVEL? | **SIM** |
| CONFLITO_DE_FONTE IMPLEMENTADO? | **SIM** |
| PROVENIÊNCIA COMPLETA? | **SIM** |
| HISTÓRICO PRESERVADO? | **SIM** |
| MOTOR CONTINUA SEM CONSUMIR FALLBACK? | **SIM** |
| TESTES passed/skipped/failed/total | **629 / 18 / 0 / 647** |
| MOTOR INTACTO? | **SIM** |
| SEGREDO VERSIONADO? | **NÃO** |
| 5F-E1 APROVADA? | **SIM** |
| PRONTO PARA 5F-E2 (odds multiprovider)? | **SIM** |

> **PARE.** NÃO iniciar 5F-E2 automaticamente. NÃO integrar fallback ao motor.
> NÃO iniciar Etapa 6. NÃO mergear em main. NÃO push.