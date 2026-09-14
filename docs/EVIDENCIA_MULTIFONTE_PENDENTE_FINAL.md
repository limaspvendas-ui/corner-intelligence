# EVIDENCIA MULTIFONTE PENDENTE — FINAL
_Baseline: bt-20260913224716-PRE_GAME | gate: ABERTO | timestamp: 2026-09-14T18:13:13 | duracao: 109.5s_

## A — CARDS (populacao historica apifootball_com)
- ENTRAR total: 207
- ENTRAR NA (red_cards NULL): 207
- Datas visitadas: 10 (orcamento 10)
- Events coletados: 8239
- Reconciliacao: MATCHED=0 AMBIGUOUS=39 NOT_MATCHED=8
- Resolvidos fallback: 0 (zero explicito=0, positivo=0)
- NULL mantido: 0 | conflito: 0
- Provider status: OK limite=False

## B — RECONCILIACAO MULTIFONTE
- Correspondencias registradas (append-only): 92
- Por status: {'MATCHED': 0, 'AMBIGUOUS': 76, 'NOT_MATCHED': 16}
- Por campo: {'red_cards': 46, 'yellow_cards': 46}
- Concordancia por campo: {'red_cards': 46, 'yellow_cards': 46}
- Sem eleicao de provider vencedor; preserva ambos os valores.

## C — CORNERS (evidencia independente 5Dollar)
- Data coleta: 2026-09-14
- Fixtures 5Dollar: 5
- Corners odds coletados (append-only): 12
- Fases observadas: ['CLOSING', 'INPLAY', 'OPENING']
- Bookmakers: ['Bet 365']
- Drift pre/post: 0.927 -> 0.875 (gap None)
- DRIFT CONTINUA: True
- CAUSA IDENTIFICADA: False
- FONTE ORIGINAL: api_football (unica fonte historica de corners)
- PODE AVANCAR: False
- Provider status: OK limite=True

## D — ODDS (coleta prospectiva)
- 5Dollar: inseridos=19 status=OK limite=False
  - mercados: {'goalline': {'inseridos': 6, 'fases': {'OPENING': 2, 'CLOSING': 2, 'INPLAY': 2}}, 'cards': {'inseridos': 4, 'fases': {'OPENING': 2, 'CLOSING': 2}}, '1x2': {'inseridos': 9, 'fases': {'OPENING': 3, 'CLOSING': 3, 'INPLAY': 3}}}
- TheOddsAPI: inseridos=120 status=OK limite=False

## E — ROI (dataset temporal)
- Odds snapshots (familia mapeada): 44222
- Fixtures com odds: 93
- Providers: ['api_football', 'five_dollar_football', 'the_odds_api']
- ROI historico calculavel: False
- Por mercado:
  - gols: entrar=2918 validos=0 invalidos=0 indeterm=0 sem_odd=2918 class=SEM_ODD_CASAVEL roi=None
  - escanteios: entrar=893 validos=0 invalidos=0 indeterm=0 sem_odd=893 class=SEM_ODD_CASAVEL roi=None
  - cartoes: entrar=307 validos=0 invalidos=0 indeterm=0 sem_odd=307 class=SEM_ODD_CASAVEL roi=None
  - resultado: entrar=234 validos=0 invalidos=0 indeterm=0 sem_odd=234 class=SEM_ODD_CASAVEL roi=None

## F — PRESSAO LIVE
- Infra pronta: True (tabela live_snapshot_history)
- Historico total: 3 rows / 3 fixtures
- Jogos live agora: 5
- Snapshots materializados (one-shot): 3
- Marcos alvo: [1, 15, 30, 45, 60, 75, 90]
- One-shot: 3 snapshot(s) materializado(s). Serie completa de marcos requer coleta ao longo do jogo (nao one-shot).

## G — RESULTADO (OOS)
- entrar=None settled=None hit=None oos=0.9259
- Walk-forward: [None, None, None, None, None, None]
- Status novo: EM_OBSERVACAO | motor_alterado=False

## H — GOALS (OOS)
- entrar=None settled=None hit=None oos=0.9421
- Walk-forward: [None, None, None, None, None, None]
- Deterioracao material: False
- Status novo: APROVADO_PARA_PROXIMA_FASE | motor_alterado=False

## I — CONSOLIDACAO

| MERCADO | STATUS ANTERIOR | NOVOS DADOS | N RECONC | FALLBACK | OOS | ODDS VALIDAS | ROI | LIVE HISTORY | STATUS NOVO | JUSTIFICATIVA |
|---|---|---|---|---|---|---|---|---|---|---|
| GOALS | APROVADO_PARA_PROXIMA_FASE | Monitoramento OOS (sem coleta nova) | 0 | N/A | 0.9421 | 0 | SEM_ODD_CASAVEL | N/A | APROVADO_PARA_PROXIMA_FASE | Estavel; mantido APROVADO_PARA_PROXIMA_FASE; motor intacto. |
| RESULTADO | EM_OBSERVAÇÃO | Monitoramento OOS (sem coleta nova) | 0 | N/A | 0.9259 | 0 | SEM_ODD_CASAVEL | N/A | EM_OBSERVACAO | Experimental; sem alteracao de motor; sem auto-promocao. |
| CARDS | NÃO_AVALIÁVEL | apifootball_com fallback: 0 resolvidos | 0 | resolvidos=0 (zero=0, pos=0) | PENDENTE (amostra insuficiente p/ reclassificar) | 0 | SEM_ODD_CASAVEL | N/A | NÃO_AVALIÁVEL | Infra fallback confirmada em dados reais (0 resolvidos / 207 NA). Amostra ainda  |
| CORNERS | EM_OBSERVAÇÃO | 5Dollar corners prospectivos: 12 | 0 | 5Dollar (prospectivo, nao retroativo) | 0.875 | 0 | SEM_ODD_CASAVEL | N/A | EM_OBSERVACAO | Manter EM_OBSERVACAO. Drift pos-cutoff confirmado (hit 0.927 -> 0.875); sem reca |
| PRESSAO LIVE | BLOQUEADO | live_snapshot_history: 3 rows | 0 | N/A | N/A | N/A | N/A | 3 rows / 3 fixtures | BLOQUEADO | One-shot: 3 snapshot(s) materializado(s). Serie completa de marcos requer coleta |
| ODDS/ROI | BLOQUEADO | 5Dollar +19 / TheOddsAPI +120 | 0 | N/A | N/A | 0 | False | N/A | BLOQUEADO | Odds prospectivas cobrem 2026-09-06..2026-09-11 (91 fixtures). Backtest cobre 20 |

## Respostas finais
- **Motor alterado?** NAO (diff vazio; sem recalibracao; sem threshold).
- **Segredo versionado?** NAO (.env gitignored).
- **Data leakage?** NAO (anti-leakage via e_pre_jogo + collected_at<kickoff).
- **Etapa 6 iniciada?** NAO.