# DIAGNÓSTICO FINAL DE CORNERS E ENCERRAMENTO DO DESENVOLVIMENTO PRINCIPAL

**Macroetapa final — CORNERS:** diagnóstico completo + decisão operacional do operador + fechamento.
**Data:** 2026-09-14
**Branch:** `etapa-final-corners-override-operacional`
**Base:** `9fbc67b` (main, fechamento operacional)
**Testes:** 713 passed / 18 skipped / 0 failed (+6 testes de override, 0 regressões)
**Motor estatístico:** 100% intacto (diff vazio em todos os módulos do motor)

---

## 1. Verdade estatística real de CORNERS (PASSOS 1-10)

### 1.1 Baseline congelado

Cutoff mecânico cronológico (result-blind): **2026-05-10**.

| Período | n ENTRAR | settled | hit | prob média | gap (obs−pred) |
|---|---|---|---|---|---|
| **PRÉ-DRIFT** (< 2026-05-10) | 455 | 452 | **0.927** | 0.9309 | −0.0039 |
| **PÓS-DRIFT** (≥ 2026-05-10) | 438 | 432 | **0.875** | 0.9265 | **−0.0515** |

**Drift:** −5.2 p.p. Calibration gap pós: −0.0515 (superconfiança).

### 1.2 Walk-forward 6 blocos

| Bloco | Período | n | hit | gap |
|---|---|---|---|---|
| 1 | 2026-02-10..03-22 | 145 | 0.910 | −0.024 |
| 2 | 2026-03-22..04-19 | 150 | 0.947 | +0.016 |
| 3 | 2026-04-20..05-10 | 161 | 0.925 | −0.003 |
| **4** | **2026-05-10..07-08** | **158** | **0.834** | **−0.091** |
| 5 | 2026-07-11..08-14 | 142 | 0.907 | −0.021 |
| 6 | 2026-08-14..09-06 | 137 | 0.889 | −0.038 |

Blocos 1-3 estáveis; onset no bloco 4 (mai-jul); recuperação parcial bloco 5,
recaída bloco 6. **Nenhum sinal pré-drift previa a queda.**

### 1.3 Drift SEGMENTADO (não global)

| Linha | pré hit | pré n | pós hit | pós n | delta |
|---|---|---|---|---|---|
| Over 4.5 escanteios | 0.909 | 154 | 0.900 | 160 | **−0.009 (estável)** |
| Over 5.5 escanteios | 0.984 | 64 | 0.880 | 92 | **−0.104 (driver #1)** |
| Under 13.5 escanteios | 0.943 | 53 | 0.866 | 67 | −0.078 |
| Over 6.5 escanteios | 0.923 | 13 | 0.852 | 27 | −0.071 |
| Under 12.5 escanteios | 0.870 | 54 | 0.800 | 30 | −0.070 |
| Under 14.5 escanteios | 0.846 | 13 | 0.773 | 22 | −0.073 |

Over 4.5 (maior volume) estável. Drift em Over 5.5, Under 13.5+, Over 6.5.

Por competição: Série B Brasil (0.920→0.819, n 27→72), MLS (1.000→0.833),
Primera A Colômbia (0.929→0.844), Liga Pro Equador (1.000→0.907).
Serie A Brasil estável (+0.005).

**DRIFT MAIS FORTE EM OVER?** SIM (Over 5.5 −0.104 é o driver #1).
**DRIFT MAIS FORTE EM UNDER?** SIM, secundário (Under 13.5/14.5 −0.073..−0.078, cauda direita).
**SELEÇÃO DE LINHA CONTRIBUI?** PARCIAL — concentração em Over 5.5 e Under 13.5+.

### 1.4 Causa: OVERDISPERSION pós-drift

| Métrica | pré | pós | Poisson prediz |
|---|---|---|---|
| sd real dos corners | 3.15 | **3.58** | √média |
| sd Poisson (√média) | 3.00 | 3.06 | — |
| **var/mean (Poisson=1.0)** | **1.104** | **1.372** | 1.0 |

A variância dos corners reais excedeu a relação média=variância da Poisson
pós-drift. Caudas mais pesadas → Over 5.5 falha em ≤5 corners, Under 13.5+
falha em 14-20 corners. Prob do modelo estável em ~0.93 (Poisson não prevê
overdispersion) → superconfiança. Composição mudou (mais Série B/Sudamericana/
MLS) durante Copa do Mundo 2026 (jun-jul) + fim das temporadas europeias.

### 1.5 Fonte independente (Passo 3)

**HÁ EVIDÊNCIA DE PROBLEMA DA FONTE? EVIDÊNCIA INSUFICIENTE.**
- multifonte_reconciliation / factual_resolution: cobrem só cartões (92 linhas).
- 5Dollar: 18 odds de corners em 3 fixtures (odds, não totais factuais).
- Cobertura total_final: 0.5% missing pré → 3.0% pós (leve, não explica drift).
- Odd ≠ fato factual. Amostra independente inválida para validação.

### 1.6 Auditoria do modelo (Passo 4)

- Features: médias casa/fora ponderadas, h2h, benchmark competição, confiança.
- Inputs pós-drift MAIORES (n_home 10→16, h2h 0.05→0.67, confiança 0.734→0.793)
  mas hit CAIU → superconfiança, não falta de dados.
- **PROBLEMA DE MODELO? PARCIAL** — limitação estrutural do Poisson frente a
  overdispersion out-of-sample (não é bug; é hipótese não prevista pelo modelo).

### 1.7 Candidatos testados (Passos 6-9)

| Candidato | Leakage | n OOS | hit OOS | gap OOS | Aprovável? |
|---|---|---|---|---|---|
| BASELINE_ATUAL | — | 432 | 0.875 | −0.0515 | referência |
| CAND_A excluir Série B | **SIM** | 360 | 0.886 | −0.0426 | NÃO (leakage + <0.927) |
| CAND_B só Over 4.5 | **SIM** | 160 | 0.900 | −0.0338 | NÃO (leakage + <0.927) |
| CAND_C isotonic pré-drift | NÃO | 410 | 0.878 | **+0.0023** | NÃO (corrige calibração, não hit) |
| CAND_D shrink prob 0.90 | NÃO | 432 | 0.875 | −0.0249 | NÃO (não muda hit) |

**MELHOR CANDIDATO: NENHUM.** Legítimos (C/D) corrigem calibração mas não
hit-rate drift. Com leakage (A/B) desclassificados e não alcançam 0.927.

### 1.8 Status estatístico real (Passo 10)

- N total ENTRAR: 893 · N holdout (pós-drift): 438 · settled: 432
- Wins (pós): 378 · Losses (pós): 54
- Hit rate holdout: **0.875** · Calibration gap: **−0.0515**
- Hit pré-drift: **0.927** · Hit pós-drift: **0.875** · Drift residual: −5.2 p.p.
- Risco de overfit: ALTO (qualquer correção que seleciona linhas/competições
  do holdout = leakage; legítimos não resolvem hit rate)
- **STATUS ESTATÍSTICO: EM_OBSERVAÇÃO** (não APROVADO_ESTATISTICAMENTE)

**NUNCA alterar esse status para atender decisão do operador.** E não foi alterado.

---

## 2. Decisão operacional explícita do operador (PASSOS 11-12)

O operador determinou previamente: **CORNERS será habilitado operacionalmente
após o diagnóstico, independentemente do resultado estatístico.**

Como STATUS_ESTATISTICO = EM_OBSERVAÇÃO (≠ APROVADO_ESTATISTICAMENTE):

**STATUS_OPERACIONAL = HABILITADO_POR_OVERRIDE_DO_USUARIO**

Preservado obrigatoriamente (auditável):
- status estatístico real: **EM_OBSERVAÇÃO** (intocado em STATUS_MERCADOS)
- hit rate real: 0.875 (pós-drift)
- calibration gap real: −0.0515
- drift real: −5.2 p.p.
- risco real: ALTO overfit se tentar corrigir; overdispersion não resolvida
- motivo da não aprovação estatística: nenhum candidato passou nos 8 critérios
- timestamp do override: 2026-09-14T00:00:00Z
- decisão humana explícita: `decisao_humana=true`

**NÃO chamar override de aprovação estatística.** A entrada de CORNERS carrega
`origem_operacional="override_usuario"` e `status_operacional="HABILITADO_POR_OVERRIDE_DO_USUARIO"`,
distinto de GOALS (`origem_operacional="estatistico"`,
`status_operacional="HABILITADO_ESTATISTICAMENTE"`).

### 2.1 Implementação do override

`src/operacional.py` (camada operacional, NÃO motor):
- Novo registro `OVERRIDE_OPERACIONAL["escanteios"]` — habilita o gate
  operacional preservando `STATUS_MERCADOS["escanteios"]["status"]=EM_OBS`.
- `_rota_mercado("escanteios")` retorna `"operational"` por override.
- `_entry_opp` adiciona `origem_operacional`, `status_operacional`,
  `timestamp_override`, `motivo_override` a cada entrada.
- Override NÃO força aposta: se o motor não aprovar corners, a avaliação vai
  a observação (não vira oportunidade operacional).
- Versão da camada: `operacional-1.0` → `operacional-1.1-override`.

---

## 3. Preservação de GOALS e outros mercados (PASSOS 13-14)

| Mercado | Status | Operacional? | Origem |
|---|---|---|---|
| **GOALS** | APROVADO_PARA_PROXIMA_FASE | **SIM** | estatístico |
| **CORNERS** | EM_OBSERVAÇÃO | **SIM (override)** | override_usuario |
| RESULTADO | EM_OBSERVAÇÃO | não (observação) | — |
| CARDS | NÃO_AVALIÁVEL | não (bloqueado) | — |
| PRESSÃO LIVE | BLOQUEADO | não (bloqueado) | — |
| ODDS/ROI | BLOQUEADO | não (bloqueado) | — |

GOALS: regras inalteradas, sem regressão, comportamento validado mantido.
Motor estatístico 100% intacto (diff vazio em prejogo_opportunity, backtest,
analysis, politica_aprovacao, cobertura, settlement, policy, validacao_multifonte,
checkpoint_calibracao). Outros mercados não tocados.

---

## 4. Smoke test operacional (PASSO 16)

Fixture: Liverpool x Manchester City (1557424, Premier League, 2026-10-11,
cache, `registrar=False` — read-only).

- **OPORTUNIDADES OPERACIONAIS: 2**
  - `[ESTATISTICO] gols | Under 5.5 gols | prob=0.948 conf=1.00 | ENTRAR`
  - `[ESTATISTICO] gols | Over 0.5 gols | prob=0.928 conf=1.00 | ENTRAR`
- **OBSERVAÇÕES:** escanteios (Over/Under 14.5, aprov_motor=False), resultado
  (38 linhas, aprov_motor=False) — motor não aprovou; override não força aposta.
- **BLOQUEADOS:** pressao_live, odds_roi.

Nenhum corners aprovado pelo motor neste fixture → corners fica em observação
(override presente, não fabrica aposta). O caminho completo do override
(corners aprovado → operacional por override) é validado pelos testes
sintéticos: `test_d_corners_sinal_operacional_por_override`,
`test_origem_operacional_distinta_goals_vs_corners`,
`test_goals_corners_coexistem_operacional`.

---

## 5. Testes (PASSO 15)

713 passed / 18 skipped / 0 failed. Novos/alterados:
- `test_d_corners_sinal_operacional_por_override` — corners aprovado →
  operacional por override, status_estatistico EM_OBS preservado.
- `test_status_estatistico_corners_preservado_em_obs` — override não altera
  STATUS_MERCADOS (EM_OBS).
- `test_rota_mercado_corners_operacional_por_override` — roteamento.
- `test_origem_operacional_distinta_goals_vs_corners` — GOALS estatístico,
  CORNERS override, distinção auditável.
- `test_override_corners_nao_aprovado_motor_fica_observacao` — override não
  força aposta (motor NÃO ENTRAR → observação).
- `test_override_auditavel_timestamp_e_decisao_humana` — timestamp + motivo.
- `test_goals_corners_coexistem_operacional` — coexistência com origem distinta.
- `test_corners_status_estatistico_preservado_em_obs` (diagnóstico) — status
  estatístico preservado + rota operational por override.
- Atualizados: test_k (usa resultado, sem override), test_nunca_promove
  (status estatístico não promovido mesmo com override operacional).

Validados: baseline imutável, split temporal, ausência de data leakage,
status estatístico verdadeiro, override separado do estatístico, GOALS
preservado, NULL≠ZERO, nenhuma aposta forçada, mesma entrada=>mesma decisão.

---

## 6. Auditoria final do projeto (PASSO 18)

- ✅ GOALS operacional (estatístico)
- ✅ CORNERS operacional (override do operador)
- ✅ Status estatístico de CORNERS preservado (EM_OBSERVAÇÃO)
- ✅ Override auditável (timestamp, decisão humana, motivo)
- ✅ RESULTADO em observação
- ✅ CARDS não avaliável
- ✅ PRESSÃO LIVE bloqueado
- ✅ ODDS/ROI bloqueado
- ✅ Saída oficial única (SaidaOficial)
- ✅ Motor único (intacto, diff vazio)
- ✅ Banco íntegro (integrity_check ok, user_version 4)
- ✅ Segredo não versionado (.env gitignored)
- ✅ Testes com 0 failed (713/18/0)

**PROJETO PRONTO PARA ENCERRAMENTO DO DESENVOLVIMENTO PRINCIPAL? SIM.**

---

## 7. Respostas finais (PASSO 17)

| Pergunta | Resposta |
|---|---|
| DRIFT CONFIRMADO? | **SIM** (0.927→0.875, −5.2 p.p.) |
| GLOBAL OU SEGMENTADO? | **SEGMENTADO** (Over 4.5 estável; Over 5.5, Under 13.5+ drivers) |
| CAUSA IDENTIFICADA? | **SIM** (overdispersion var/mean 1.10→1.37 + composição Copa 2026) |
| PROBLEMA DE FONTE? | **EVIDÊNCIA INSUFICIENTE** (sem fonte factual independente de corners) |
| PROBLEMA DE MODELO? | **PARCIAL** (Poisson não prevê overdispersion out-of-sample) |
| PROBLEMA DE LINHA? | **PARCIAL** (concentração Over 5.5, Under 13.5+) |
| CANDIDATOS TESTADOS | 4 (+BASELINE) |
| MELHOR CANDIDATO | **NENHUM** (legítimos não resolvem hit; leakage desclassificados) |
| N HOLDOUT | 432 settled (438 ENTRAR) |
| WINS / LOSSES (pós) | 378 / 54 |
| HIT RATE HOLDOUT | 0.875 |
| CALIBRATION GAP | −0.0515 |
| WALK-FORWARD | blocos 1-3 estáveis, onset bloco 4 (0.834), recaída bloco 6 |
| HIT PRÉ-DRIFT / PÓS-DRIFT | 0.927 / 0.875 |
| DRIFT RESIDUAL | −5.2 p.p. (não reduzido por candidato legítimo) |
| RISCO DE OVERFIT | ALTO |
| STATUS ESTATÍSTICO | **EM_OBSERVAÇÃO** |
| STATUS OPERACIONAL | **HABILITADO_POR_OVERRIDE_DO_USUARIO** |
| CORNERS OPERACIONAL? | **SIM** (por override) |
| GOALS OPERACIONAL? | **SIM** (estatístico) |
| PROJETO AGORA OPERA COM GOALS + CORNERS? | **SIM** |

---

## 8. Status final dos mercados

```
GOALS        = APROVADO_PARA_PROXIMA_FASE  → operacional (estatístico)
CORNERS      = EM_OBSERVAÇÃO               → operacional (override do operador)
RESULTADO    = EM_OBSERVAÇÃO               → observação
CARDS        = NÃO_AVALIÁVEL               → bloqueado
PRESSÃO LIVE = BLOQUEADO                   → bloqueado
ODDS/ROI     = BLOQUEADO                   → bloqueado
```

---

## 9. Commit e encerramento

Commit único na branch `etapa-final-corners-override-operacional`:
- diagnóstico read-only (src/corners_drift_diagnostico.py + 13 testes)
- habilitação operacional por override (src/operacional.py + testes)
- relatório final (este documento + CORNERS_DRIFT_DIAGNOSTICO_FINAL.md)

**Sem merge. Sem push.** main e origin/main permanecem em 9fbc67b.

**Próxima ação (somente após autorização):** fechamento final controlado +
merge/push + testes Claude Code x ChatGPT.

**Ressalva obrigatória:** CORNERS opera por override do operador, NÃO por
aprovação estatística. O drift (−5.2 p.p.) e a superconfiança (gap −0.0515)
permanecem não resolvidos estatisticamente. Toda oportunidade individual de
corners ainda precisa cumprir as regras do motor (aprovada_motor=True).
"NENHUMA OPORTUNIDADE OPERACIONAL APROVADA" continua sendo resultado válido.