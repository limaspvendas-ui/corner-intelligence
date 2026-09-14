# MACROETAPA FINAL DE CORNERS — DIAGNÓSTICO, CORREÇÃO, REVALIDAÇÃO

**Data:** 2026-09-14
**Branch:** `etapa-final-corners`
**Baseline:** 694 passed / 18 skipped / 0 failed (após fechamento operacional, commit 9fbc67b)
**Após macroetapa:** 707 passed / 18 skipped / 0 failed (+13 testes do diagnóstico, 0 regressões)
**Motor:** intacto (diff vazio em todos os módulos matemático/decisórios)
**Veredicto:** **CORNERS PERMANECE EM_OBSERVAÇÃO** — nenhum candidato legítimo passou nos critérios.

---

## 1. Objetivo

Tratar CORNERS como prioridade máxima: diagnosticar o drift, identificar a causa,
testar candidatos de correção com metodologia temporal rigorosa, e promover
CORNERS ao fluxo operacional **somente se critérios objetivos fossem satisfeitos**.

**Princípios respeitados:**
- NÃO aprovar Corners por ordem administrativa.
- NÃO esconder drift.
- NÃO manipular threshold para fazer o modelo passar.
- NÃO usar informação futura (candidato derivado do holdout = LEAKAGE).
- NÃO alterar histórico para melhorar métricas.

---

## 2. Baseline congelado

Cutoff mecânico cronológico (result-blind): **2026-05-10** (cutoff mediano do
baseline, reutilizado de `validacao_multifonte.DRIFT_CORNERS_CUTOFF`).

| Período | n ENTRAR | settled | hit | prob média | gap (obs−pred) | corners média | sd |
|---|---|---|---|---|---|---|---|
| **PRÉ-DRIFT** (< 2026-05-10) | 455 | 452 | **0.927** | 0.9309 | −0.0039 | 8.99 | 3.15 |
| **PÓS-DRIFT** (≥ 2026-05-10) | 438 | 432 | **0.875** | 0.9265 | **−0.0515** | 9.34 | 3.58 |

**Drift:** −5.2 p.p. (0.927 → 0.875). Calibration gap pós: **−0.0515** (superconfiança).

Walk-forward 6 blocos:

| Bloco | Período | n | hit | gap |
|---|---|---|---|---|
| 1 | 2026-02-10..03-22 | 145 | 0.9097 | −0.0244 |
| 2 | 2026-03-22..04-19 | 150 | 0.9467 | +0.0157 |
| 3 | 2026-04-20..05-10 | 161 | 0.9245 | −0.0034 |
| **4** | **2026-05-10..07-08** | **158** | **0.8344** | **−0.0911** |
| 5 | 2026-07-11..08-14 | 142 | 0.9065 | −0.0205 |
| 6 | 2026-08-14..09-06 | 137 | 0.8889 | −0.0383 |

Blocos 1-3 estáveis (0.91-0.95). **Onset do drift no bloco 4** (mai-jul, hit 0.834).
Recuperação parcial no bloco 5, recaída no bloco 6. **Nenhum sinal pré-drift previa a queda.**

---

## 3. Diagnóstico do drift

### 3.1 Drift é CONCENTRADO, não global

**Hit rate por linha (pré → pós):**

| Linha | pré hit | pré n | pós hit | pós n | delta |
|---|---|---|---|---|---|
| Over 4.5 escanteios | 0.909 | 154 | 0.900 | 160 | **−0.009 (estável)** |
| Over 5.5 escanteios | 0.984 | 64 | 0.880 | 92 | **−0.104 (driver #1)** |
| Under 13.5 escanteios | 0.943 | 53 | 0.866 | 67 | −0.078 |
| Over 3.5 escanteios | 0.963 | 81 | 0.933 | 30 | −0.030 |
| Under 12.5 escanteios | 0.870 | 54 | 0.800 | 30 | −0.070 |
| Over 6.5 escanteios | 0.923 | 13 | 0.852 | 27 | −0.071 |
| Under 14.5 escanteios | 0.846 | 13 | 0.773 | 22 | −0.073 |

**Over 4.5** (maior volume, 314 jogos) é estável. O drift concentra-se em:
- **Over 5.5** (−0.104, 11 perdidas pós vs 1 pré — todas com total ≤5 corners)
- **Under 13.5/14.5** (9 perdidas pós, totais 14-20 corners — cauda direita mais pesada)
- **Over 6.5** (−0.071)

### 3.2 Por competição (pré → pós)

| Competição | pré hit | pré n | pós hit | pós n | delta |
|---|---|---|---|---|---|
| Serie B (Brasil) | 0.920 | 25 | 0.819 | 72 | **−0.101 (volume 27→72)** |
| MLS | 1.000 | 13 | 0.833 | 24 | −0.167 |
| Primera A (Colômbia) | 0.929 | 84 | 0.844 | 32 | −0.085 |
| Liga Pro (Equador) | 1.000 | 48 | 0.907 | 43 | −0.093 |
| Argentina | 0.912 | 147 | 0.890 | 82 | −0.021 (modesto) |
| **Serie A (Brasil)** | 0.929 | 28 | **0.933** | 15 | **+0.005 (estável)** |
| Primera División | 0.895 | 38 | 0.917 | 36 | +0.022 |

Composição pós-drift: **mais Série B Brasil** (27→72), Sudamericana (22→41),
MLS (13→25), 1. Lig nova (0→16); menos Argentina (147→84).

### 3.3 Causa: OVERDISPERSION pós-drift

| Métrica | pré | pós | Poisson prediz |
|---|---|---|---|
| sd real dos corners | 3.15 | **3.58** | √média |
| sd Poisson (√média) | 3.00 | 3.06 | — |
| **var/mean (Poisson=1.0)** | **1.104** | **1.372** | 1.0 |

A variância dos corners reais **excedeu a relação média=variância da Poisson**
pós-drift (var/mean 1.10 → 1.37). Caudas mais pesadas → Over 5.5 falha em jogos
de ≤5 corners (cauda baixa) e Under 13.5+ falha em jogos de 14-20 corners (cauda
alta). A prob do modelo ficou estável em ~0.93 (Poisson não prevê overdispersion),
tornando-se superconfiante (gap −0.004 → −0.052).

### 3.4 Cobertura API

- Missing total_final (todos corners): **0.5% pré → 3.0% pós** (leve degradação
  de cobertura; 3% é pequeno, não explica o drift).
- Inputs pós-drift: n_home 10.4→15.9, h2h 0.05→0.67, bench_validas 105→172,
  confiança 0.734→0.793 (mais dados históricos → modelo MAIS confiante),
  mas hit CAIU — assinatura de superconfiança.

### 3.5 Respostas do diagnóstico

- **DRIFT É GLOBAL OU CONCENTRADO?** **CONCENTRADO.** Over 4.5 estável; drift em
  Over 5.5, Under 13.5+, e competições de alta variância (Série B, MLS, Primera A, Liga Pro).
- **CAUSA MAIS PROVÁVEL:** Overdispersion pós-drift (var/mean 1.10→1.37, excedendo
  Poisson) + mudança de composição de competições (mais Série B/Sudamericana/MLS)
  durante a Copa do Mundo 2026 (jun-jul) e fim das temporadas europeias. Onset no
  bloco 4 (mai-jul) coincide com esse período.
- **EVIDÊNCIA:** sd real 3.15→3.58 vs sd Poisson 3.00→3.06; var/mean 1.10→1.37;
  11 perdidas Over 5.5 pós (total≤5) vs 1 pré; 9 perdidas Under 13.5 pós
  (total 14-20) vs 3 pré; composição Série B 27→72.
- **NÍVEL DE CONFIANÇA:** ALTO (drift real e concentrado); MÉDIO (causa
  overdispersion + composição, sem fonte factual independente para confirmar).

---

## 4. Fonte independente (Passo 3)

**Não há dataset factual independente de corners disponível:**
- `multifonte_reconciliation` (92 linhas): cobre somente `yellow_cards` (46) e
  `red_cards` (46) — **nenhum corners**.
- `factual_resolution`: somente `red_cards` e `yellow_cards`.
- 5Dollar: 18 odds de corners em **3 fixtures** (OPENING/CLOSING/INPLAY, Bet 365)
  — **odds, não totais factuais**; amostra minúscula, inválida para validação
  estatística.

**FATO de CORNERS vs ODD de CORNERS separados.** Odd não foi usada para fabricar
estatística factual. A única fonte factual de corners é API-Football (primária,
usada no backtest). Amostra independente: **insuficiente**.

---

## 5. Candidatos testados

Metodologia: desenvolvimento = pré-drift; OOS = pós-drift (holdout nunca usado
na seleção do candidato). Cada candidato com hipótese, alteração, dados,
risco de overfit, flag de leakage.

| Candidato | Leakage | n OOS | hit OOS | gap OOS | hit pré | Aprovável? |
|---|---|---|---|---|---|---|
| **BASELINE_ATUAL** | — | 432 | 0.875 | −0.0515 | 0.927 | NÃO (referência) |
| CAND_A excluir Série B | **SIM** | 360 | 0.8861 | −0.0426 | 0.9274 | **NÃO** (leakage + não supera 0.927) |
| CAND_B só Over 4.5 | **SIM** | 160 | 0.900 | −0.0338 | 0.9091 | **NÃO** (leakage + não supera 0.927) |
| CAND_C isotonic pré-drift | NÃO | 410 | 0.878 | **+0.0023** | 0.927 | **NÃO** (corrige calibração, não hit) |
| CAND_D shrink prob 0.90 | NÃO | 432 | 0.875 | −0.0249 | 0.927 | **NÃO** (não muda hit) |

### Análise por candidato

- **CAND_A (excluir Série B):** LEAKAGE — Série B estava bem pré-drift (0.920);
  excluir com base no holdout é usar informação futura. Mesmo excluindo, hit só
  vai a 0.886, ainda abaixo do baseline pré (0.927). **Reprovado.**

- **CAND_B (só Over 4.5):** LEAKAGE — Over 5.5 era 0.984 pré-drift (excelente);
  restringir a Over 4.5 com base no holdout é leakage. Hit 0.900 ainda abaixo
  de 0.927. **Reprovado.**

- **CAND_C (isotonic pré-drift):** LEGÍTIMO (treino em pré-drift, sem leakage).
  Corrige a calibração: gap −0.0515 → **+0.0023** (excelente). Mas o hit rate
  fica em 0.878 (praticamente inalterado de 0.875). **Confirma que recalibracao
  corrige overconfiança mas NÃO reduz o hit-rate drift** — a seleção de linhas
  continua falhando com caudas mais pesadas. **Reprovado** (não reduz drift materialmente).

- **CAND_D (shrink prob 0.90):** LEGÍTIMO (prior fixo). Melhora gap para −0.0249
  mas não muda o conjunto ENTRAR (0.90 > 0.70 threshold), logo hit inalterado.
  **Reprovado.**

### Conclusão dos candidatos

**A causa raiz (overdispersion) exige mudar a SELEÇÃO de linhas/competições,
o que requer conhecimento do holdout (leakage).** Nenhum candidato derivado
legitimamente do pré-drift pode prever ou corrigir o drift, porque o drift é
uma mudança de distribuição out-of-sample **não previsível** dos dados de
desenvolvimento (blocos 1-3 estáveis e bem calibrados). Recalibracao corrige
calibração mas não hit rate. **Nenhum candidato cumpre os 8 critérios.**

---

## 6. Decisão

**CORNERS = EM_OBSERVAÇÃO.** Não promovido.

Nenhum candidato legítimo (sem leakage) reduziu materialmente o hit-rate drift
nem superou o baseline pré-drift (0.927). Os candidatos com leakage foram
desclassificados pelo critério 6 (vazamento temporal). Promover Corners agora
seria aprovação por ordem administrativa / overfit ao holdout.

---

## 7. Integração controlada

**NÃO APLICÁVEL.** CORNERS não aprovado → nenhuma integração ao motor ou à camada
operacional. O motor, thresholds, Poisson, predictions históricas, settlement e a
camada operacional (`src/operacional.py`) permanecem **intactos**. CORNERS continua
roteado para `observations` (EM_OBSERVAÇÃO), não `operational_opportunities`.

---

## 8. Auditoria de impacto

| Mercado | Impacto? |
|---|---|
| GOALS | NÃO (motor intacto, camada operacional intacta — continua operacional) |
| RESULTADO | NÃO (continua EM_OBSERVAÇÃO) |
| CARDS | NÃO (continua NÃO_AVALIÁVEL) |
| PRESSÃO LIVE | NÃO (continua BLOQUEADO) |
| ODDS/ROI | NÃO (continua BLOQUEADO) |

Diff vazio em: prejogo_opportunity, backtest, analysis, politica_aprovacao,
cobertura, settlement, policy, operacional, validacao_multifonte,
checkpoint_calibracao. GOALS sem regressão.

---

## 9. Arquivos desta macroetapa

| Arquivo | Tipo |
|---|---|
| `src/corners_drift_diagnostico.py` | NOVO — diagnóstico read-only + teste de candidatos |
| `tests/test_corners_drift_diagnostico.py` | NOVO — 13 testes (baseline, leakage, split, gates) |
| `docs/CORNERS_DRIFT_DIAGNOSTICO_FINAL.md` | NOVO — este relatório |

**Nenhum arquivo do motor alterado. Nenhuma promoção de status.**

---

## 10. Achado para trabalho futuro

CAND_C demonstra que uma **camada de calibração isotônica** (treinada em
pré-drift, sem leakage) corrige a superconfiança (gap −0.0515 → +0.0023) sem
tocar no Poisson. Isso NÃO promove CORNERS (não corrige hit rate), mas é um
achado útil: a overconfiança estrutural pós-drift pode ser mitigada por
calibração sem recalibrar o modelo. Ficará disponível para futura avaliação
quando houver nova evidência independente.

---

## 11. Status operacional final (preservado)

- GOALS = APROVADO_PARA_PROXIMA_FASE / OPERACIONAL
- **CORNERS = EM_OBSERVAÇÃO** (não promovido — drift não resolvido legitimamente)
- RESULTADO = EM_OBSERVAÇÃO
- CARDS = NÃO_AVALIÁVEL
- PRESSÃO LIVE = BLOQUEADO
- ODDS/ROI = BLOQUEADO

**Etapa 6 (integração ChatGPT / calibração): NÃO INICIADA. BLOQUEADA.**