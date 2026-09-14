# VALIDACAO ESTATISTICA MULTIFONTE — FINAL
_Baseline: bt-20260913224716-PRE_GAME | cutoff holdout: 2026-05-10T18:30:00-03:00_
_Previsoes: 288362 | fixtures: 3627_

---

## A — GOALS
- Anterior: hit=0.9507 gap=0.0036 n=1034
- **OOS (posterior): hit=0.9421 gap=-0.0054 n=1884 brier=0.0543**
- Walk-forward: 0.9535, 0.9479, 0.9533, 0.9421, 0.9533, 0.9315
- Status: **APROVADO_PARA_PROXIMA_FASE**

## B — RESULTADO
- Anterior: hit=0.9444 gap=0.0082 n=126
- **OOS: hit=0.9259 gap=-0.0059 n=108**
- Walk-forward: 0.8947, 0.9783, 0.9333, 0.88, 0.9565, 0.9722
- Status: **EM_OBSERVACAO** (experimental)

## C — CARDS
- ENTRAR: 307 | settled: 100 | NA: 207
- Fallback factual: resolucoes=0 conflitos=0 recuperados_red=0
- Amostra final avaliavel: 100 | OOS possivel: True
- Status: **NÃO_AVALIÁVEL** — Infra de fallback factual pronta (resolucao_factual.py) mas NUNCA executada sobre historico: factual_resolution=0, sourc...

## D — CORNERS (drift)
- Pre-drift: hit=0.927 gap=-0.0039 n=452
- **Post-drift: hit=0.875 gap=-0.0515 n=432**
- DRIFT CONFIRMADO: **True**
- Causa identificada: False | Fonte invalida hipotese: EVIDENCIA_INSUFICIENTE
- Status: **EM_OBSERVACAO**

## E — ODDS/ROI
- Fixtures com odds prospectivas: 91 | providers: api_football, five_dollar_football, the_odds_api
  - gols: entrar=2918 validos=0 settled_odd=0 roi=None
  - escanteios: entrar=893 validos=0 settled_odd=0 roi=None
  - cartoes: entrar=307 validos=0 settled_odd=0 roi=None
  - resultado: entrar=234 validos=0 settled_odd=0 roi=None
- ROI historico calculavel: **False**
- Status: **BLOQUEADO**

## F — PRESSAO LIVE
- Snapshots: 1 | fixtures: 1
- Historico suficiente: False | Status: **BLOQUEADO**

## G — CONSOLIDACAO

| MERCADO | COBERTURA | AMOSTRA | OOS | ESTABILIDADE | CALIBRACAO | ODDS | ROI | STATUS_ATUAL | STATUS_PROPOSTO |
|---|---|---|---|---|---|---|---|---|---|
| GOALS | VALIDADO (api_football, cache) | 1884 | 0.9421 | ESTAVEL (0.93-0.95 wf) | gap -0.0054 | 0 casavel | NAO | OPERACIONAL | APROVADO_PARA_PROXIMA_FASE |
| RESULTADO | VALIDADO (api_football) | 108 | 0.9259 | VARIAVEL (0.88-0.98 wf) | gap -0.0059 | 0 casavel | NAO | EXPERIMENTAL | EM_OBSERVACAO |
| CARDS | LIMITADO (red_cards NULL 5D) | 100 | 0.91 | INSTAVEL (0.82-1.0 wf, n pequeno) | gap -0.0379 | 0 casavel (odds existem mas sem outcome) | NAO | NAO AVALIAVEL | NÃO_AVALIÁVEL |
| CORNERS | VALIDADO (api_football) | 432 | 0.875 | DRIFT (0.93->0.87 pos-cutoff) | gap pos -0.0515 | 0 casavel | NAO | EM OBSERVACAO (drift) | EM_OBSERVACAO |
| PRESSAO LIVE | NAO MATERIALIZADO | 1 | None | N/A | N/A | N/A | NAO | AGUARDANDO HISTORICO | BLOQUEADO |
| ODDS/ROI | 3 providers (api_football/the_odds/5dollar) | 91 | None | N/A | N/A | 91 fixtures | NAO (0 casavel) | NAO AVALIAVEL | BLOQUEADO |

---

## Respostas finais

- **QUAIS MERCADOS PASSARAM?** GOALS (APROVADO_PARA_PROXIMA_FASE)
- **EM OBSERVACAO?** RESULTADO, CORNERS
- **BLOQUEADOS?** PRESSAO LIVE, ODDS/ROI
- **NAO AVALIAVEIS?** CARDS
- **EXISTE MOTIVO TECNICO PARA ALTERAR O MOTOR AGORA?** NAO
- **INFRA MULTIFONTE FOI UTIL?** PARCIAL (infra pronta, populacao historica pendente)
- **PRONTOS PARA INTEGRACAO CONTROLADA DO APROVADO?** NAO (sem odd real para ROI; integracao exige Etapa 6 autorizada)
