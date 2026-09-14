# ETAPA 5F — COLETA PROSPECTIVA DE ODDS REAIS

**IMPORTANTE:** NÃO é Etapa 6. A Etapa 6 permanece **BLOQUEADA**. Esta etapa
CRIA INFRAESTRUTURA para coletar odds reais prospectivamente, preservando
FATO → CÁLCULO → INTERPRETAÇÃO → DECISÃO. **Não recalibra o motor. Não tenta
provar lucro. Não calcula ROI. Não altera nenhuma regra.**

Módulo: `src/odds_coleta.py`. Testes: `tests/test_odds_coleta.py` (38 testes).
CLI: `python -m src.app oddscoleta {status,cache,coleta}`. JSON:
`docs/etapa5f_coleta_odds_reais.json`.

---

## 1. Estado inicial

- branch: `etapa-5f-coleta-odds-reais` (criada a partir de `main` em `3e34fda`).
- HEAD inicial: `3e34fdab9b79b9043113599a3b82ec8b8ae7976d`.
- suíte inicial: **507 passed / 18 skipped / 0 failed**.
- status herdados: GOALS=OPERACIONAL/NÃO ALTERAR; CORNERS=EM OBSERVAÇÃO;
  CARDS=NÃO AVALIÁVEL; RESULTADO=EXPERIMENTAL; PRESSÃO=EXPERIMENTAL;
  ROI=NÃO AVALIÁVEL; ETAPA 6=BLOQUEADA.

## 2. FASE 1 — Auditoria do cache de odds (data-driven, 7 perguntas)

Auditadas as tabelas `api_cache` (9.280 linhas, `corner_intelligence.db`) e
`bt_predictions` (288.362 linhas, `backtest.db`), em modo somente leitura.
Nenhuma API_KEY aparece em campo algum.

1. **Existem odds pré-jogo reais no cache?** SIM. `/odds`: **86 respostas**
   válidas (todas parseáveis, todas com `fixture` em `params`).
2. **Para quantos fixtures?** **86 fixtures distintos** em `/odds`; **28** em
   `/odds/live`.
3. **De quais bookmakers?** **14 bookmakers** distintos. Top: William Hill
   (78), Bet365 (78), Marathonbet (78), Pinnacle (78), BetVictor (76), 1xBet
   (64), 10Bet/Unibet/Betfair/Betano/Superbet/Dafabet (48), SBO (45),
   888Sport (31).
4. **Quais mercados possuem cobertura?** **186 mercados**. Os 4 alvo do motor
   presentes com cobertura saudável: Match Winner (816), Goals Over/Under
   (690), Corners Over Under (454), Cards Over/Under (210). Mais Double
   Chance (679), Asian Handicap (644), e variantes 1º/2º tempo/mandante/
   visitante (não-FT, corretamente rejeitadas como UNMAPPED na classificação).
5. **Existem timestamps que provem quando a odd estava disponível?** SIM, em
   dois níveis. `bookmakers[].update` = **sempre `null`** (não serve). **`
   response[i].update`** (top-level) = **timestamp ISO-8601 válido** (38
   valores distintos, ex.: `2026-09-06T12:04:14+00:00`). `api_cache.created_at`
   = **Unix timestamp válido** (06-Set a 10-Set 2026). Ambos existem no nível
   correto e são usados pelo coletor (`update_feed` + `collected_at`).
6. **Por que `bt_predictions.odd_real` está zerado?** **NÃO está zerado — está
   `NULL` em 100% das 288.362 previsões** (0 com `odd_real=0`; 0 com
   `odd_real>0`; 4.352 ENTRAR, todas `NULL`; `bookmaker` 100% `NULL`). Causa
   raiz: **interseção = 0** entre os 86 fixtures com `/odds` no cache e os
   3.627 fixtures do backtest — **desalinhamento temporal** (odds cacheadas
   para fixtures 06–12 Set; universo do backtest termina 06-Set 19:30 UTC; o
   `backtest.db` foi gravado em 13-Set 22:50 e o `corner_intelligence.db`
   modificado em 14-Set 09:14).
7. **É ausência de dados ou bug de matching?** **Ausência de dados no
   universo de backtest** (alternativa a), **NÃO bug de matching**. A função
   `extrair_odd_real` funciona: teste real sobre a resposta `/odds` do fixture
   1492362 extraiu odds válidas (Over 2.5 → 1.96 Pinnacle; Under 9.5 → 2.15
   Marathonbet; Vitória mandante → 1.46 1xBet; Empate → 4.58; Visitante →
   7.84; Over 10.5 → 2.01; Over 3.5 → 3.40 Bet365). O matching oficial
   existe e funciona; as fixtures com odds nunca entraram no backtest.

**Correção de premissa da Etapa 5E:** a afirmação "`/odds/live`: 28, todas
`suspended:true`, 0 usáveis" é **FALSA** para o estado atual do cache. 24/28
entries têm odds reais; **5.125 valores `suspended:false`** vs 1.949
`suspended:true` (72,5% utilizáveis). A inutilização só ocorre **por design**
no `CacheClient` do backtest (retorna `[]` na linha 554-556), não por conteúdo.

## 3. FASE 2 — ODD REAL ≠ ODD JUSTA

Formalizado em código e documentação:
- **ODD REAL** = cotação factual de bookmaker/API, com bookmaker, mercado,
  linha e timestamp identificáveis.
- **ODD JUSTA** = `1/probabilidade` estimada pelo modelo. **Nunca** preenche
  `odd_real`. Nenhum bookmaker é fabricado. Nenhuma odd de mercado ausente é
  inferida. O coletor só armazena cotações que vêm factualmente do feed.

## 4. FASE 3 — Endpoints cache-first

O coletor respeita o limite de requisições do plano Pro:
- `oddscoleta cache`: lê o `api_cache` existente (`/odds` + `/odds/live`) e
  ingere — **0 chamadas à API**.
- `oddscoleta coleta`: usa `client.get` (cache-aware) — escreve no cache e só
  consulta a API quando necessário. Consumo reportado por chamada.

## 5. FASE 4 — Cobertura por mercado (sem casamento aproximado)

`classificar_mercado(bet_name, bet_id)` faz classificação **EXATA**:
- ID estável do feed (5=gols, 45=escanteios, 80=cartoes) **SEM marcador** de
  período/escopo → família correspondente.
- Nome canônico exato (Match Winner→resultado/1X2; Double Chance→DC; Asian
  Handicap→AH; Draw No Bet→DNB) → família resultado.
- Nome com marcador (`1st`/`2nd`/`half`/`home`/`away`/`team`/`yellow`/
  `player`/`range`/`between`) **rejeita totais mesmo com ID correto**.
- Irreconhecível → **`UNMAPPED`** (nunca casamento aproximado por substring).

Resultado da ingestão (FASE 13): gols 13.713, escanteios 8.114, cartoes
1.166, resultado 16.193, **UNMAPPED 278.765** (mercados fora do escopo FT de
total do jogo, corretamente segregados).

## 6. FASE 5 — Política de bookmaker

O coletor armazena **TODAS as cotações factuais** de todos os bookmakers
presentes no feed. Nenhum hindsight. **Nenhuma política "usar a maior odd"**
é aplicada nesta etapa (a função `extrair_odd_real` do backtest faz isso para
ROI futuro; o coletor apenas preserva o fato bruto). Cada cotação é uma linha
distinta com seu bookmaker identificado.

## 7. FASE 6 — Modelo append-only `odds_snapshot_history`

Nova tabela em `corner_intelligence.db` (junto a `live_snapshots`; nunca
limpa por `clear_expired`, que só toca `api_cache`):

```
fixture_id, coleta_tipo (pre_match|live), bookmaker, bet_name, bet_id,
familia, subfamilia, lado, linha, value_feed, odd, suspended,
update_feed (timestamp da fonte), collected_at (timestamp da coleta),
fixture_date, e_pre_jogo (anti-leakage), status, motivo, snapshot_hash
UNIQUE(snapshot_hash)
```

**REGRA: append-only.** `INSERT OR IGNORE` em `snapshot_hash` — uma cotação
idêntica (mesma identidade + mesma odd) é ignorada; **nunca** uma odd antiga
é sobrescrita. Re-append de uma cotação já existente não altera o row antigo
(testado). O histórico é uma série temporal imutável de cotações observadas.

## 8. FASE 7 — Timestamp / anti-leakage

Cada snapshot traz dois timestamps factuais:
- `update_feed`: `response[i].update` (quando a fonte atualizou a odd).
- `collected_at`: `time.time()` real da coleta (ou `created_at` do cache na
  ingestão).

Campo `e_pre_jogo`: **1** se `collected_at <= kickoff` (cotação disponível
antes do jogo — válida como odd de entrada); **0** se após (closing/hindsight
— não serve como entrada); **NULL** se `fixture_date` desconhecido.

**Regra anti-leakage formalizada:** futuramente uma previsão só poderá usar
uma odd se `timestamp_da_odd <= momento_da_decisão`. Nunca odd pós-jogo. Nunca
closing odd como odd de entrada. O coletor registra os fatos; o enforcing
ocorrerá no matching futuro. **Nenhuma cotação coletada depois da decisão é
associada retroativamente.**

Resultado da ingestão: **298.187 snapshots `e_pre_jogo=1`** (válidos como
entrada) de 317.951 totais.

## 9. FASE 8 — Previsões antigas não alteradas

As **288.362 previsões antigas NÃO são alteradas** e **NÃO recebem `odd_real`
retroativo**. O coletor apenas COLETA e ARMAZENA fatos novos. A coluna
`bt_predictions.odd_real` permanece `NULL` em 100% das previsões — nenhum
backfill foi feito. Teste `test_nao_altera_backtest_db` confirma: a ingestão
do cache não altera a contagem de `bt_predictions`.

## 10. FASE 9 — Coletor separado do motor

`src/odds_coleta.py` é **totalmente separado** do motor:
- **NÃO** importa `politica_aprovacao`, `analysis`, `prejogo_opportunity`,
  `settlement` ou qualquer módulo de probabilidade/aprovação/threshold.
- **NÃO** pode alterar probabilidade, aprovação, recommendation, thresholds,
  Poisson, blend, settlement, matriz de cobertura ou política.
- **AUTO DESABILITADO por padrão**: coleta periódica exige `--intervalo > 0`
  E `--max-iter` explícitos; sem eles é one-shot.
- **Consumo de API calculado e reportado**: `/odds` = 1 chamada/fixture;
  `/odds/live` = +1 chamada/fixture. `coletar_fixture` retorna `consumo_api`.
- Operações: one-shot (`coletar_fixture`) + periódica (`coletar_periodico`).

## 11. FASE 10 — CLI

```
python -m src.app oddscoleta status
python -m src.app oddscoleta cache [--dry-run]
python -m src.app oddscoleta coleta <fixture_id> [--live] [--intervalo N] [--max-iter N]
```

- `status`: estado do `odds_snapshot_history` (append-only).
- `cache`: ingere odds do `api_cache` (0 chamadas API); `--dry-run` preview.
- `coleta`: coleta um fixture na API; `--live` adiciona `/odds/live`;
  `--intervalo>0` + `--max-iter` = periódica (auto-desabilitada por padrão).

## 12. FASE 11 — Dedup sem perder histórico

`snapshot_hash` = SHA-256 de (fixture_id, coleta_tipo, bookmaker, bet_name,
bet_id, familia, subfamilia, lado, linha, value_feed, odd, suspended, status).
- Cotação **idêntica** → mesma hash → `INSERT OR IGNORE` ignora (dedup).
- **1.90 → 1.89** → odd diferente → hash diferente → **novo snapshot**.
- Re-observar 1.89 (já visto) → ignorado (não há "ainda 1.89 em t3", mas
  toda mudança de preço é capturada).

Ingestão real: 317.951 inseridos, **21 duplicados** (dedup).

## 13. FASE 12 — Validação

Cada value do feed é validado:
- `value` ausente/vazio → **INVALID**, motivo "value ausente no feed".
- `odd` ausente/não-numérica → **INVALID**, motivo "odd ausente ou nao
  numerica".
- `odd <= 0` → **INVALID**, motivo "odd nao positiva".
- live `suspended=true` → **SUSPENDED** (cotação factual mantida, mercado
  suspenso).
- mercado irreconhecível → **UNMAPPED**.
- demais → **OK**.

**Regra: invalid → registrado com motivo, NUNCA missing→zero.** Odd inválida
fica `NULL` na coluna `odd` (nunca `0`). Ingestão: INVALID 1.553, SUSPENDED
1.514, UNMAPPED 275.740, OK 39.144.

## 14. FASE 13 — Primeira coleta controlada (cache-first)

Executada ingestão do `api_cache` (0 chamadas à API):
- 78 entries `/odds` pré-jogo (86 respostas, 78 com bookmakers);
- 24 entries `/odds/live` (28 respostas, 24 com odds reais);
- **317.951 snapshots inseridos**, 21 duplicados;
- 89 fixtures distintos cobertos;
- **298.187 `e_pre_jogo=1`** (válidos como odd de entrada);
- 0 chamadas à API.

## 15. FASE 14 — Sem ROI

**ROI NÃO calculado nesta etapa.** Nenhum `edge`, `EV`, `P/L`, `pl_unitario`.
O coletor preserva fatos; o cálculo de ROI exige matching futuro com
previsões + odds `e_pre_jogo=1` em encerrados, e pertence a uma etapa
posterior (não iniciada). `format_status` declara "ROI: NÃO CALCULADO".

## 16. FASE 15 — Testes

`tests/test_odds_coleta.py` — **38 testes**, DB temporário isolado (nunca
toca o DB real exceto `test_nao_altera_backtest_db` que é read-only):
- append-only (não sobrescreve; re-append preserva row antigo);
- dedup (idêntica ignora; 1.90→1.89 é novo);
- timestamps (`update_feed`, `collected_at` persistidos);
- validação (odd ausente/zero/value ausente → INVALID com motivo, nunca zero);
- classificação EXATA (gols/corners/cards por ID; resultado por nome;
  marcador rejeita totais mesmo com ID correto; desconhecido → UNMAPPED);
- parsing lado/linha (Over/Under, AH, 1X2);
- multi-bookmaker (ambos armazenados);
- pre-match vs live separados; live `suspended` preserva odd;
- anti-leakage (`e_pre_jogo` 1/0/NULL; persistido);
- erro de API (vazio/None → 0 snapshots, sem crash); payload parcial;
- ingestão do cache (0 API; dry-run não grava);
- determinismo (hash); JSON serializável;
- **não altera backtest.db**; **regras do motor congeladas** (0.70/0.97/0.60
  + amarelo=1/vermelho=2); **coletor não importa motor de aprovação**.

**Suíte completa: 545 passed, 18 skipped, 0 failed** (507 anteriores + 38 novos).

## 17. FASE 16 — Pre-match vs live separados

`coleta_tipo` distingue `pre_match` (`/odds`, shape `bookmakers[].bets[]`)
de `live` (`/odds/live`, shape `odds[]` agregado, `suspended` por valor).
- Pre-match nunca apresentado como live.
- Live com cotações reais disponíveis (correção da premissa 5E): 24/28
  entries, 5.125 valores `suspended:false`.
- Se live indisponível para um fixture: 0 snapshots live (registrado, não
  fabricado). **Nunca odd pré-jogo reutilizada como live.**
- Ingestão: pre_match 310.898, live 7.053.

## 18. FASE 17 — Outros gargalos não tocados

**NÃO mexido em:** `live_snapshot_history` (pressão); `/fixtures/events`
(95 cartões); correção de drift de corners; calibração (Etapa 6); matriz de
cobertura; settlement; thresholds; Poisson; blend; política de universo.
Apenas infraestrutura de odds foi adicionada.

## 19. FASE 18 — Git

Commit sugerido: `etapa 5f: coleta prospectiva de odds reais` na branch
`etapa-5f-coleta-odds-reais`. **NÃO merge em main. NÃO push. NÃO remote.**

## 20. Módulo / CLI / schema

- `src/odds_coleta.py`: ~400 linhas. Reusa helpers de `src/odds.py`
  (`BET_IDS_FT_EXATOS`, `_MARCADORES_FORA_FT`, `parse_total_value`,
  `parse_ah_value`, `_to_float`, `_to_int`) — mesma regra de casamento EXATO.
- CLI: `cmd_oddscoleta` em `src/app.py` (diff puramente aditivo, 91 linhas,
  nenhum cálculo do motor alterado).
- Schema: `odds_snapshot_history` (append-only, `UNIQUE(snapshot_hash)`).

## 21. Consumo de API

- `oddscoleta cache`: **0 chamadas** (somente cache).
- `oddscoleta coleta <fx>`: **1 chamada** (`/odds`); `--live`: **+1**
  (`/odds/live`).
- `oddscoleta coleta <fx> --intervalo N --max-iter M`: **2×M×fixtures**
  chamadas (reportado em `consumo_api`).
- O plano Pro tem limite por minuto; coleta periódica é auto-desabilitada por
  padrão para evitar consumo não-intencional.

## 22. Branch / git status

- branch: `etapa-5f-coleta-odds-reais` (de `main` em `3e34fda`).
- arquivos novos: `src/odds_coleta.py`, `tests/test_odds_coleta.py`,
  `docs/ETAPA_5F_COLETA_ODDS_REAIS.md`, `docs/etapa5f_coleta_odds_reais.json`.
- modificado: `src/app.py` (apenas CLI aditivo, sem motor).
- **Nenhum arquivo do motor alterado.** `data/` (DB) é gitignored.

## 23. Commit

A ser registrado na branch `etapa-5f-coleta-odds-reais`.

---

## RESPOSTAS OBRIGATÓRIAS

**COLETA DE ODDS PRE-MATCH IMPLEMENTADA?** **SIM** — `src/odds_coleta.py`
implementa coleta pre-match (`/odds`) e persistência append-only em
`odds_snapshot_history`. CLI `oddscoleta {status,cache,coleta}`. Ingestão
cache-first executada (78 entries, 317.951 snapshots, 0 API).

**ODDS COLETADAS SÃO FACTUAIS E TIMESTAMPADAS?** **SIM** — cada snapshot traz
bookmaker, mercado, linha, `update_feed` (timestamp da fonte) e `collected_at`
(timestamp da coleta). Nenhuma odd é fabricada ou inferida; ODD JUSTA nunca
preenche ODD REAL.

**HISTÓRICO É APPEND-ONLY?** **SIM** — `INSERT OR IGNORE` em `snapshot_hash`;
odd antiga nunca sobrescrita; re-append preserva o row original (testado).
1.90→1.89 são snapshots distintos.

**EXISTE RISCO DE LEAKAGE IDENTIFICADO?** **SIM** — risco identificado e
mitigado: campo `e_pre_jogo` marca cotações coletadas após o kickoff (0 =
hindsight, não serve como entrada); regra formalizada `timestamp_da_odd <=
momento_da_decisão`; nenhum backfill retroativo das 288.362 previsões; nenhuma
closing odd tratada como odd de entrada. 298.187 snapshots marcados
`e_pre_jogo=1`.

**LIVE ODDS ESTÃO DISPONÍVEIS?** **PARCIAL** — disponíveis no cache atual
(24/28 entries com odds reais, 5.125 valores `suspended:false`; corrige
premissa 5E de "todas suspended"). Coletor separa `coleta_tipo=live` e
preserva `suspended` por valor. Para fixtures sem live na fonte: 0 snapshots
live, registrado, não fabricado, odd pré-jogo nunca reutilizada como live.
Disponibilidade real por fixture depende da API no momento da coleta.

**ALGUMA REGRA DO MOTOR FOI ALTERADA?** **NÃO** — nenhum threshold, Poisson,
blend, settlement, aprovação, matriz de cobertura, política de universo ou
motor de mercado alterado. `src/app.py` tem apenas adição aditiva de CLI.
Testes confirmam regras congeladas (0.70/0.97/0.60 + amarelo=1/vermelho=2).

**ROI JÁ PODE SER VALIDADO?** **NÃO** — esta etapa apenas coleta e armazena
fatos. ROI exige matching futuro (previsão × odd `e_pre_jogo=1` em encerrados)
e pertence a etapa posterior, não iniciada. Nenhum edge/EV/P/L calculado.

**ETAPA 6 FOI INICIADA?** **NÃO** — Etapa 6 permanece BLOQUEADA. Nenhuma
calibração, nenhum parâmetro alterado, nenhuma regra modificada.

---

> PARE. NÃO faça merge. NÃO inicie 5G. NÃO calcule ROI oficial. NÃO altere
> decisões históricas. NÃO inicie Etapa 6.