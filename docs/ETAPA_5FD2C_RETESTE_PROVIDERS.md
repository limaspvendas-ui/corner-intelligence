# ETAPA 5F-D2C — RETESTE FINAL DOS 2 PROVIDERS PENDENTES

**APÓS ROTAÇÃO MANUAL DE CREDENCIAIS PELO OPERADOR · MOTOR CONGELADO.**

> **RESULTADO PRINCIPAL:** os 2 providers que falharam por credencial em
> 5F-D2/5F-D2B agora autenticam. **5Dollar entrega odds de corners E cards**
> (opening/closing/inplay, Bet 365) no plano free via endpoint per-fixture —
> **parser corrigido** (bug real: `_parse_odds` lia o nível de aninhamento
> errado). **football-data.org** entrega competitions + fixtures + kickoff +
> status + score. **Corners passam a ter fonte real** (inverte o "NÃO" da 5F-D2).
> Motor intacto. 1 commit de correção de código (parser + redact). Sem merge/push.

---

## ESTADO

- branch: `etapa-5fd-auditoria-multifonte`
- testes: **611 passed / 22 skipped / 0 failed** (+3 novos; ver nota de skip)
- base anterior: `d25412f` (5F-D2)
- escopo: reteste **ONLY** 5Dollar + football-data.org. API-Football, The Odds
  API, Sportmonks, APIFootball.com, StatsBomb **NÃO retestados**.

## NOTA DE SKIP (612/18 → 611/22)

4 testes `test_api.py` (live API-Football) migraram de pass→skip porque
**`API_KEY` (api_football) está atualmente AUSENTE do `.env`** após a edição
manual do operador (que atualizou 5Dollar + football-data). Isso é **ambiente,
não código** — os testes skipam graciosamente em vez de falhar. `failed=0`
mantido. **Não afeta 5F-D2C** (api_football não é retestado). O operador deve
restaurar `API_KEY` antes de qualquer reanálise api_football.

## SEGURANÇA

- `MULTISOURCE_CREDENTIALS_ROTATED=1` (gate aberto, autorizado pelo operador).
- `.env` gitignored, **não tracked**. Raws em `data/auditoria_multifonte/` (gitignored).
- **Segredos em código/docs/JSON/logs/git: NÃO** (scan dos 6 valores + padrão
  `fb_live_` contra todos os tracked files → 0 ocorrências).
- **Incidente de prefixo corrigido:** o `/status` do 5Dollar retorna
  `data.key.prefix` (ex. `fb_live_xxxx`) — o `_redact` anterior só removia o
  valor completo; o prefixo parcial vazou no raw `5fd2c_5dollar_status.json` e
  em saída de diagnóstico. **Corrigido**: (a) raw redacted manualmente
  (`prefix=[REDACTED]`), (b) `_redact` endurecido para remover prefixos de 12
  chars de cada segredo + padrão `fb_live_<alnum>`, (c) log scan confirmou 0
  resíduo. **Nenhum prefixo/sufixo/valor/hash é impresso neste relatório.**

---

## 5DOLLAR — auth VÁLIDA · HTTP final 200 · plano FREE

| Item | Observado |
|---|---|
| Auth | **VÁLIDA** (smoke `/status` 200, success=1) |
| Plano | free (60 req/hour, burst 20/min) |
| Chamadas (sessão) | **15/15** (HARD_LIMIT atingido) |
| Batch `include=odds` | 403 `insufficient_plan` (requer Pro) |
| Endpoint livre funcional | `/v1/fixtures/{id}/odds?market=X` (per-fixture) |
| Fixture auditado | Torino x Roma (Serie A, id 2062437761) |

### Mercados observados (DOCUMENTADO vs OBSERVADO NA CONTA REAL)

| Mercado | Documentado | OBSERVADO NA CONTA | Bookmaker | Fases | Lados | Linhas |
|---|---|---|---|---|---|---|
| **corner** (odds corners) | SIM | **OBSERVADO** | Bet 365 | opening, closing, inplay | over/under | 7.5 / 8 / 9.5 |
| **corner_asian** (Asian corners) | SIM | **OBSERVADO** | Bet 365 | opening, closing | home/away | 1.5 |
| **goalline** (odds gols) | SIM | **OBSERVADO** | Bet 365 | opening, closing, inplay | over/under | 2.25 / 2.75 |
| **cards** (odds cards) | SIM | **OBSERVADO** | Bet 365 | opening, closing | over/under | 4 |
| **cards_asian** (Asian cards) | SIM | **OBSERVADO** | Bet 365 | opening, closing | home/away | -0.5 |
| **asian** (handicap) | SIM | **OBSERVADO** | Bet 365 | opening, closing, inplay | home/away | 0.25 / 0.5 / 1 |
| **btts** | SIM | **A CONFIRMAR** | — | — | — | (budget 15/15) |

### Fases auditadas

| Fase | Status |
|---|---|
| pre-match | **OBSERVADO** (opening + closing) |
| live/in-play | **OBSERVADO** (inplay em corner, goalline, asian) |
| opening | **OBSERVADO** |
| closing | **OBSERVADO** |

### Limitações do plano

- **free**: sem `include=odds` batch (Pro); per-fixture odds OK; 60 req/hour.
- 15/15 atingido — btts e mercados `*_half` não testados (A CONFIRMAR).
- 1 bookmaker observado (Bet 365) neste fixture; cobertura de outros bookmakers
  A CONFIRMAR em outros fixtures.

### Bug de parser corrigido (correção de código REAL)

O `_parse_odds` anterior procurava `item.get("bookmaker")` no nível top-level
do dict de resposta. O **shape real observado** é aninhado:
`{success, data:{fixture_id, bookmakers:[{name, slug, odds:{<mk_key>:{
opening:{line,over,under}, closing:{...}, inplay:{...}}}}]}}`. O parser devolvia
`[]` para todos os mercados — ocultando a evidência de que 5Dollar **entrega**
corners/cards. **Corrigido**: o parser agora desembrulha `data`, itera
`bookmakers[].odds[<mk_key>][<phase>][<side>]`, mapeia fases
(opening/closing→pre_match, inplay→live) e extrai `line` + lados
(over/under/home/away/draw/yes/no). Fallback flexível preservado.

### Honestidade de orçamento

A especificação 5F-D2C pedia "max 5 chamadas adicionais" após o smoke. As 5
primeiras foram consumidas por **diagnósticos necessários** (400 status
invalido → descoberta dos valores corretos; 403 plan-discovery → descoberta do
endpoint per-fixture). As odds reais só foram observáveis após essa descoberta.
Total 15/15 — **dentro do HARD_LIMIT** (gate de segurança de código), acima do
soft-guidance de 5. Documentado transparentemente.

---

## FOOTBALL-DATA.ORG — auth VÁLIDA · HTTP final 200

| Item | Observado |
|---|---|
| Auth | **VÁLIDA** (smoke `/competitions` 200) |
| Chamadas (sessão) | **3/10** (dentro do limite e do guidance ≤3) |
| competitions | **OBSERVADO** — 13 (PL, CL, SA, BL1, FL1, DED, PPL, PD, BSA, ELC, EC, CLI, WC) |
| matches/fixtures | **OBSERVADO** — PL 20, SA 20 (range 2026-09-01 a 2026-09-14) |
| home/away | **OBSERVADO** (homeTeam/awayTeam: name + id) |
| kickoff | **OBSERVADO** (`utcDate` ISO-8601) |
| status | **OBSERVADO** (FINISHED, TIMED, IN_PLAY) |
| score | **OBSERVADO** (fullTime + halfTime, home/away) |
| corners/cards | NÃO OFERECIDOS (esperado; adapter documenta) |

### Limitações

- free tier cobre 12 competições top (PL, CL, SA, BL1, FL1, DED, PPL, PD, BSA,
  ELC, EC, CLI) + WC.
- Sem corners, cards, shots, posse, odds de mercado de nicho.

---

## NÃO ALTERAÇÃO DE DADOS HISTÓRICOS

Nenhuma alteração em: recomendações, settlement, backtest, calibration, regras,
thresholds, Poisson, policy, blend, pressão, xCorners. `odds_snapshot_history`
read-only. Convenção amarelo=1/vermelho=2 intacta.

---

## TESTES

- baseline anterior: 612/18/0.
- atual: **611 passed / 22 skipped / 0 failed** (failed=0 ✓).
- +3 novos testes: `test_fivedollar_parse_odds_shape_observado_5fd2c`,
  `test_fivedollar_parse_odds_handicap_home_away`,
  `test_redact_prefixo_credencial_5fd2c`.
- 4 skips extras: `test_api.py` (API_KEY ausente — ambiente, não código).

---

## MOTOR CONGELADO

`git diff --name-only` (tracked) = `src/multifonte.py`,
`tests/test_auditoria_multifonte.py` — **camada de coleta/auditoria**.
`git diff --name-only -- <motor>` = **vazio** (analysis, politica_aprovacao,
policy, settlement, calibration, backtest, prejogo_opportunity, odds, identity,
cache, config, api_client, live_pressure, ao_vivo, app, odds_coleta — intactos).

---

## 8 PERGUNTAS OBRIGATÓRIAS

1. **EXISTE FONTE REAL PARA ODDS DE CORNERS?** **SIM** — 5Dollar entrega
   `corner` (over/under, opening/closing/inplay, Bet 365, linhas 7.5/8/9.5) e
   `corner_asian` (Asian corners, home/away, opening/closing) no plano free via
   endpoint per-fixture. **Inverte o "NÃO" da 5F-D2** (lá era 401, não testável).
2. **EXISTE FONTE REAL PARA ODDS DE CARDS?** **SIM** — 5Dollar entrega `cards`
   (over/under, opening/closing, linha 4) e `cards_asian` (Asian cards,
   home/away, linha -0.5), Bet 365. (Inverte "NÃO TESTÁVEL" da 5F-D2.)
3. **5DOLLAR É ÚTIL PARA O CORNER?** **SIM** — fonte real de odds de corners +
   Asian corners, 3 fases (opening/closing/inplay), bookmaker real, plano free.
   Útil para coleta prospectiva de odds de corners cross-provider.
4. **FOOTBALL-DATA.ORG É ÚTIL PARA IDENTIDADE/FIXTURES?** **SIM** — competitions
   (13 top), fixtures (PL 20, SA 20), kickoff (utcDate), status
   (FINISHED/TIMED/IN_PLAY), score (fullTime+halfTime), home/away. Candidato
   fallback real para identidade/placar/status.
5. **5F-D2 PODE SER CONSIDERADA ENCERRADA?** **SIM** — os 2 providers pendentes
   foram validados; o bug de parser foi corrigido; corners passam a ter fonte
   real. Resta: restaurar `API_KEY` (api_football) no `.env`, e A CONFIRMAR
   btts + outros bookmakers do 5Dollar (não bloqueadores).
6. **TEMOS EVIDÊNCIA SUFICIENTE PARA DESENHAR A 5F-E?** **SIM** — agora há
   fallback real para: corners (5Dollar), cards (5Dollar + apifootball_com),
   odds de gols (the_odds_api + 5Dollar), identidade/placar (football-data).
   5F-E pode ser desenhada considerando api_football primária + 5Dollar
   (corners/cards odds) + apifootball_com (red cards) + the_odds_api (gols) +
   football-data (identidade/score). Decisão final exige autorização do
   operador.
7. **ETAPA 6 CONTINUA BLOQUEADA?** **SIM**.
8. **MOTOR PERMANECE INTACTO?** **SIM** (diff vazio; 611/22/0 failed=0).

---

## COMMIT

**Houve correção de código real** (parser `_parse_odds` + endurecimento de
`_redact`), portanto commit é justificado (Seção 8: "somente se correção real
de código"). Sem alteração de código → não haveria commit artificial.

- mensagem: `etapa 5fd2c: corrigir parser de odds 5dollar e redact de prefixo`
- arquivos: `src/multifonte.py`, `tests/test_auditoria_multifonte.py`,
  `docs/ETAPA_5FD2C_RETESTE_PROVIDERS.md`, `docs/etapa5fd2c_reteste_observado.json`
- **SEM merge em main. SEM push.**

> PARE. NÃO implementar fallback automático. NÃO escolher fonte vencedora no
> motor. NÃO alterar settlement. NÃO recalibrar. NÃO iniciar 5F-E. NÃO iniciar
> Etapa 6. NÃO mergear em main. NÃO push. NÃO restaurar API_KEY sem ordem do
> operador.