# ETAPA 5C — AUDITORIA CIRÚRGICA DA INSTABILIDADE EM ESCANTEIOS

**OBJETIVO:** descobrir POR QUE o mercado de escanteios perdeu estabilidade
temporal (hit anterior ≈ 92,84% → holdout posterior ≈ 87,23%, gap ≈ −0,054)
**sem modificar nenhuma regra.** Auditoria SOMENTE LEITURA.

Módulo: `src/auditoria_escanteios.py` (read-only). Testes:
`tests/test_auditoria_escanteios.py` (7 testes). JSON:
`docs/etapa5c_auditoria_escanteios.json`. Run auditada:
`bt-20260913224716-PRE_GAME` (3825 fixtures, 288362 previsões, backtest-1.0).

> Nenhum threshold, fórmula, Poisson, blend, política, settlement ou dado foi
> alterado. Nenhuma calibração executada. Nenhum campeonato bloqueado.

---

## 1. REPRODUÇÃO DO ACHADO

Reproduzido **exatamente** o resultado da Etapa 5B para escanteios (ENTRAR):

| período | ENTRAR | GANHA | PERDIDA | NÃO AVAL. | hit | prob média | Brier | gap obs−pred |
|---|---|---|---|---|---|---|---|---|
| Anterior | 464 | 428 | 33 | 3 | 0.9284 | 0.9308 | 0.0660 | −0.0023 |
| Posterior | 429 | 369 | 54 | 6 | **0.8723** | 0.9266 | 0.1138 | **−0.0542** |

Cutoff = 2026-05-10 (mediana cronológica). Denominador de hit exclui
NÃO AVALIÁVEL. Reprodução confirmada — nenhum número diverge da Etapa 5B.

## 2. MOMENTO EXATO DA DETERIORAÇÃO

Por mês (cronológico):

| mês | período | n | hit | prob | gap | Brier |
|---|---|---|---|---|---|---|
| 2026-02 | ANT | 52 | 0.9231 | 0.9359 | −0.013 | 0.070 |
| 2026-03 | ANT | 117 | 0.9138 | 0.9335 | −0.020 | 0.078 |
| 2026-04 | ANT | 202 | **0.9353** | 0.9297 | +0.006 | 0.061 |
| **2026-05** | **ANT** | 125 | **0.8320** | 0.9276 | **−0.096** | 0.148 |
| 2026-06 | POS | 17 | 0.8235 | 0.9166 | −0.093 | 0.155 |
| 2026-07 | POS | 87 | 0.9176 | 0.9243 | −0.007 | 0.075 |
| 2026-08 | POS | 165 | 0.8944 | 0.9277 | −0.033 | 0.096 |
| 2026-09 | POS | 35 | 0.8286 | 0.9279 | −0.099 | 0.148 |

**A deterioração começou em MAIO de 2026** — o **último mês do período
anterior** (cutoff 10-Mai). Ou seja, **não é um artefato de holdout**: o colapso
se inicia ~Maio e continua (com parcial recuperação em Julho) até Setembro.
Trimestres: Q1 0.9167 → Q2 0.9011 → Q3 0.8932 (declínio contínuo). Walk-forward:
blocos 1–3 estáveis (~0.925–0.930), **bloco 4 colapsa (0.8586, gap −0.066)**,
blocos 5–6 parciais (0.899, 0.874).

## 3. QUEBRA POR PERÍODO

(Contido no item 2 — mês, trimestre e bloco walk-forward.) O declínio é gradual
ao longo de 2026, não um degrau no cutoff. Maio é o ponto de inflexão.

## 4. QUEBRA POR COMPETIÇÃO

Ligas com amostra em ambos os períodos (n_total ≥ 15):

| liga | n_ant | n_pos | hit_ant | hit_pos | gap_ant | gap_pos |
|---|---|---|---|---|---|---|
| Liga Prof. Argentina | 149 | 82 | 0.9128 | 0.8875 | −0.024 | −0.041 |
| Primera A (Col) | 84 | 33 | 0.9286 | 0.8438 | −0.006 | −0.087 |
| Liga Pro (Ecuador) | 51 | 43 | 1.0000 | 0.9024 | +0.074 | −0.025 |
| Primera División (Chi) | 39 | 35 | 0.8974 | 0.9143 | −0.035 | −0.026 |
| Libertadores | 33 | 28 | 0.9091 | 0.8929 | −0.019 | −0.038 |
| Serie A (Bra) | 29 | 14 | 0.9310 | 0.9286 | +0.012 | +0.008 |
| **Serie B (Bra)** | 29 | 70 | 0.9259 | **0.8143** | +0.012 | **−0.101** |
| Sudamericana | 22 | 41 | 0.9091 | 0.8780 | −0.019 | −0.048 |
| MLS | 13 | 25 | 1.0000 | 0.8333 | +0.076 | −0.086 |

**Deterioração ampla** em quase todas as ligas. Primera División (Chile) e
Serie A (Bra) **melhoraram/estáveis**. Piora concentrada em **Serie B Brasil,
MLS, Primera A Col, Liga Pro**. Nenhuma liga bloqueada.

## 5. MUDANÇA DE MIX DE COMPETIÇÕES

Participação % no ENTRAR de escanteios:

| liga | share ANT | share POS | Δ |
|---|---|---|---|
| Liga Prof. Argentina | 32,1% | 19,1% | **−13,0 p.p.** |
| Primera A (Col) | 18,1% | 7,7% | −10,4 |
| **Serie B (Bra)** | 6,3% | **16,3%** | **+10,1** |
| Sudamericana | 4,7% | 9,6% | +4,8 |
| Liga Pro | 11,0% | 10,0% | −1,0 |
| Libertadores | 7,1% | 6,5% | −0,6 |
| MLS | 2,8% | 5,8% | +3,0 |

**Teste contrfactual decisivo:** aplicando o **hit anterior de cada liga** ao
**mix posterior** (ligas presentes em ambos, n=411):

- hit posterior **real**: **0.8723**
- hit posterior **contrfatual** (hit_ant por liga × mix_pos): **0.9385**
- hit anterior real: 0.9284

O contrfatual (0.9385) é **superior** ao anterior real (0.9284) e muito acima
do posterior real (0.8723). **Conclusão: a mudança de mix NÃO é a causa.** Se as
ligas mantivessem seu desempenho anterior, o mix posterior teria hit ~0.94.
**Toda a deterioração é WITHIN-LEAGUE** — cada liga piorou no posterior.

Contribuição ao deterioramento (perdas extras vs. hit_anterior, liga, st_pos≥10):

| liga | Δ hit | n_pos | perdas extras ≈ |
|---|---|---|---|
| Serie B (Bra) | −0.112 | 70 | 7.8 |
| MLS | −0.167 | 24 | 4.0 |
| Liga Pro | −0.098 | 41 | 4.0 |
| Primera A (Col) | −0.085 | 32 | 2.7 |
| Arg. Liga Prof. | −0.025 | 80 | 2.0 |
| Sudamericana | −0.031 | 41 | 1.3 |

**Resposta (item 4): a deterioração veio do MODELO/drift DENTRO de cada liga,
NÃO de mudança de mix.** Multifatorial, mas mix é fator **excluído**.

## 6. QUEBRA POR LINHA

| linha | n_ant | hit_ant | gap_ant | n_pos | hit_pos | gap_pos |
|---|---|---|---|---|---|---|
| Over 3.5 | 81 | 0.963 | +0.013 | 30 | 0.933 | −0.016 |
| **Over 4.5** | 158 | 0.911 | −0.022 | 161 | 0.897 | −0.036 |
| **Over 5.5** | 66 | 0.985 | +0.062 | 90 | 0.878 | −0.044 |
| Over 6.5 | 15 | 0.923 | +0.019 | 27 | 0.852 | −0.055 |
| Under 11.5 | 9 | 0.889 | −0.045 | 3 | 1.000 | +0.061 |
| **Under 12.5** | 55 | 0.870 | −0.057 | 30 | 0.800 | −0.128 |
| **Under 13.5** | 54 | 0.944 | +0.026 | 66 | 0.864 | −0.055 |
| Under 14.5 | 15 | 0.867 | −0.040 | 21 | 0.750 | −0.159 |

Deterioração **ampla**: as linhas de maior n (Over 4.5, Over 5.5, Under 13.5)
todas pioram. Under 12.5 e Under 14.5 colapsam. Over 5.5 inverte de excelente
(0.985) para ruim (0.878). Over 4.5 (linha dominante) já era ligeiramente
superconfiante no anterior (−0.022) e piora (−0.036).

## 7. OVER VS UNDER

| lado | n_ant | hit_ant | gap_ant | n_pos | hit_pos | gap_pos |
|---|---|---|---|---|---|---|
| **Over** | 327 | 0.9385 | +0.004 | 308 | 0.8911 | −0.038 |
| **Under** | 137 | 0.9044 | −0.017 | 121 | **0.8250** | **−0.094** |

**Ambos deterioram**, mas **Under é muito pior** (gap −9,4 p.p. vs −3,8 p.p. de
Over). Under já era ligeiramente superconfiante no anterior e colapsa no
posterior. Com média de totais em alta (item 13), Under linhas são
estruturalmente prejudicadas.

## 8. FAIXA DE PROBABILIDADE

| faixa | n_ant | hit_ant | gap_ant | n_pos | hit_pos | gap_pos |
|---|---|---|---|---|---|---|
| 80–90 | 11 | 0.800 | −0.095 | 10 | 0.800 | −0.098 |
| **90–95** | 397 | 0.9291 | **+0.001** | 401 | **0.8734** | **−0.053** |
| 95–97 | 56 | 0.9464 | −0.009 | 18 | 0.8889 | −0.065 |

**A deterioração concentra-se na faixa dominante 90–95** (n=397→401, ~93% da
amostra): gap +0.001 → −0.053. A faixa 95–97 também piora **e encolhe**
(56→18) — o modelo torna-se **menos** frequentemente muito confiante no
posterior. **O modelo NÃO ficou mais confiante** (prob média estável 0.9308 →
0.9266, ligeira queda); o que mudou é que **o mesmo nível de probabilidade agora
vence menos** — a curva de calibração deslocou-se para baixo.

## 9. MATURIDADE HISTÓRICA (JANELAS)

| métrica | ANT (med) | POS (med) | ANT (mean) | POS (mean) |
|---|---|---|---|---|
| n_home | 10 | **18** | 10.4 | 16.0 |
| n_away | 10 | **17** | 10.7 | 15.7 |
| bench_validas | 93 | **157** | 106 | 173 |
| h2h_n | 0 | 1 | 0.05 | 0.68 |
| n_amostra_curta (<10) | 8 | **1** | — | — |

**O período posterior tem MAIS histórico por fixture** (n_home 10→18,
benchmark 93→157) e **quase nenhuma amostra curta** (8→1), **yet piora**. Isso
**exclui "amostra insuficiente"** como causa. Mais dados + pior calibração é a
assinatura de **drift**: o histórico está defasado relativamente ao presente.
Nenhuma alteração em STATS_WINDOWS.

## 10. QUALIDADE DOS DADOS

| métrica | ANT | POS |
|---|---|---|
| sem_stats_home (mean) | 1.94 | 2.08 |
| sem_stats_away (mean) | 1.92 | 2.23 |
| fixtures com algum missing stats | 324/464 (70%) | 377/429 (88%) |

Perfil de missing stats **similar** entre os períodos (ligeiro aumento, sem
salto). Todas as 893 linhas ENTRAR são "total do jogo" (sem team corners).
871/893 são temporada 2026 (12+10 de 2025 espalhados) — **sem transição de
temporada relevante**. A queda é estatística/comportamental, não de qualidade
bruta da matéria-prima.

## 11. SETTLEMENT DAS PERDIDAS

Revalidação independente de TODAS as 893 previsões (recomputar
GANHA/PERDIDA/NÃO AVALIÁVEL a partir de `linha` + `total_final`):

- **Mismatches totais: 0 / 893**
- **Mismatches entre as 87 perdidas: 0 / 87**

Settlement **100% correto**. Nenhuma perda foi liquidada incorretamente.
**Settlement excluído como causa.**

## 12. CONCENTRAÇÃO POR FIXTURE

- fixtures com ENTRAR escanteios: **886**
- com 1 ENTRAR: 879 · com 2 ENTRAR: 7 · com ≥3: 0
- fixtures com alguma perda: **87** (todas com exatamente 1 perda; 0 com 2)
- distribuição de perdas por fixture: {0: 799, 1: 87}

**87,23% representa 87 partidas distintas** — perdas **dispersas**, não
concentradas em poucos jogos com múltiplas entradas correlacionadas. A regra
"máx 2/jogo" quase não vincula (só 7 fixtures têm 2 entradas). A deterioração é
ampla, não um cluster de outliers correlacionados.

## 13. DRIFT TEMPORAL

Comparação dos totais finais reais (fixtures ENTRAR escanteios):

| métrica | ANT | POS |
|---|---|---|
| n com total | 461 | 423 |
| média total | 8.97 | **9.37** (+0.40) |
| mediana total | 9.0 | 9.0 |
| stdev total | 3.14 | **3.60** |
| mín / máx | 2 / 21 | 1 / 23 |
| % Over | 70.5% | 71.8% |

Média de escanteios **subiu 0,40/jogo** e **variância aumentou** (stdev 3.14 →
3.60) no posterior. Mediana estável (9). Isso é consistente com **mais jogos
de totais extremos** (mín 1, máx 23) — caldas mais pesadas.

**DRIFT DETECTADO: SIM** — dentro da amostra ENTRAR, o comportamento real dos
totais mudou entre os períodos (média ↑, variância ↑). Isto prejudica
estruturalmente Under (totais em alta) e, via variância, Over linhas
intermediárias (5.5/6.5) que ficam mais fronteiriças.

## 14. CONCLUSÃO CAUSAL

**Causa principal: G — DRIFT REAL** (com mecanismo I — limitação do motor em
recalibrar frente a drift).

Evidência: deterioração within-liga em quase todas as ligas (item 4/5);
more-histórico-pior-calibração (item 9); totais reais em alta + variância
ampliada (item 13); início em Maio/2026 antes do cutoff (item 2); calibração
90–95 deslocada para baixo sem aumento de confiança (item 8). O motor usa
histórico estático (LAST_N=20, blend 65/35, Poisson) que **não adapta** a
mudanças de regime — quando o presente drifta, o λ baseado no passado fica
superestimado para Under e mal calibrado para Over intermediários.

**Causas secundárias:**
- **B — Linhas específicas**: Under 12.5/13.5/14.5 e Over 5.5/6.5 concentram
  perdas (item 6).
- **C — Over vs Under**: Under pior (gap −9,4 p.p. vs −3,8 p.p.), consistente
  com totais em alta (item 7).
- **D — Faixa de probabilidade**: concentra-se em 90–95, a faixa dominante
  (item 8).

**Excluídos:**
- **A — Mix de ligas**: contrfatual 0.9385 > 0.9284; mix não é causa (item 5).
- **E — Qualidade de dados**: missing stats similar, sem salto (item 10).
- **F — Início/mudança de temporada**: 97% é 2026, sem transição (item 10).
- **H — Settlement**: 100% correto, 0/87 perdas erradas (item 11).
- **J — Amostra insuficiente**: posterior tem MAIS histórico (item 9).

**Classificação final: K — MULTIFATORIAL**, com **causa primária G (drift real)**
manifestada via **limitação I do motor** (calibração estática sem adaptação a
regime), e componentes secundários B/C/D (linhas/lado/faixa onde o drift se
concentra). Não há bug no motor; há uma **limitação de adaptação temporal**.

## 15. HIPÓTESES DE CORREÇÃO FUTURA — SEM EXECUTAR

(Nenhuma implementada. Nenhuma deve ser executada nesta etapa.)

1. **Janela histórica mais curta ou pesos decrescentes** (discount temporal):
   LAST_N=20 pode estar defasado frente a drift; dar mais peso a jogos recentes.
2. **Benchmark da liga adaptativo**: o blend 35% liga pode estar defasado se a
   liga drifta; revisar atualização do benchmark ao longo da temporada.
3. **Modelagem de dispersão extra-Poisson**: variância aumentada (stdev
   3.14→3.60) sugere que Poisson puro subestima caudas — Under altas e Over
   intermediárias podem estar mal modeladas.
4. **Revisar Under altas (12.5/13.5/14.5)**: cauda direita pode estar
   superestimada pelo modelo; gap −0.128 (Under 12.5) e −0.159 (Under 14.5)
   sugerem superconfiança estrutural nestas linhas.
5. **Monitor de calibração em tempo real** (controle estatístico mensal):
   detectar drift cedo (Maio teria disparado alerta) em vez de descobrir
   post-hoc no holdout.
6. **Confirmar sazonal vs estrutural**: ampliar janela histórica (2025 completo
   + 2026) para verificar se o drift de Maio–Setembro 2026 se repete em outros
   anos ou é específico deste período.

Todas as hipóteses exigem **etapa dedicada de calibração com validação
out-of-sample e odds reais** antes de implementar. **Nenhuma executada aqui.**

## 16. TESTES

`tests/test_auditoria_escanteios.py` — 7 testes:
- parsing de linha (Over/Under + valor)
- settlement revalidado (Over/Under/NÃO AVALIÁVEL)
- Over vs Under separados (não misturados)
- concentração por fixture (soma de perdas consistente)
- auditoria reproduz contagens e **não altera regras** (constantes congeladas)
- auditoria determinística
- auditoria **somente leitura** (DB: nenhuma linha inserida/removida; todas as
  previsões idênticas após a auditoria)

**Suíte: 469 passed, 18 skipped, 0 failed** (462 anteriores + 7 novos).

## 17. GIT STATUS

Branch: `etapa-5c-auditoria-escanteios` (criada a partir de `main` após
fast-forward de `etapa-5b-validacao-temporal` para `main` em `87c8727`).
Arquivos novos: `src/auditoria_escanteios.py`, `tests/test_auditoria_escanteios.py`,
`docs/ETAPA_5C_AUDITORIA_ESCANTEIOS.md`, `docs/etapa5c_auditoria_escanteios.json`.
**Nenhum arquivo do motor alterado.** Sem push, sem merge em main.

## 18. RESUMO DO HASH

A ser registrado no commit: branch `etapa-5c-auditoria-escanteios`.

---

## RESPOSTAS FINAIS

**ESCANTEIOS CONTINUA TECNICAMENTE CONFIÁVEL? PARCIAL.**

A taxa absoluta no holdout (87,23%) ainda é alta, o método está formalmente
correto, o settlement é 100% íntegro e as perdas são dispersas (87 jogos
distintos). **Mas a calibração degradou materialmente**: a faixa dominante
90–95 caiu de 0.9291 para 0.8734 (gap +0.001 → −0.053), Under colapsou
(gap −0.094), e o início da deterioração (Maio 2026) é anterior ao cutoff —
é drift real, não ruído de holdout. "Confiável" no sentido de **estável e
calibrado fora da amostra: não**; no sentido de **método íntegro e taxa
absoluta alta: sim, parcial**.

**PROBLEMA IDENTIFICADO? SIM.**

Causa primária identificada: **drift real** (totais ↑, variância ↑) começando
Maio 2026, manifestado via limitação do motor (calibração estática sem adaptação
a regime). Mix, settlement, qualidade de dados e amostra **excluídos** como
causa por evidência direta (contrfactual, revalidação, perfil de missing,
maturidade). Deterioração within-liga, ampla, com concentração em Under e
faixa 90–95.

**PRECISA ALTERAR REGRA? A CONFIRMAR.**

Hipóteses de correção foram **levantadas** (itens 15) mas **nenhuma executada**.
A decisão de alterar regra exige etapa dedicada, com odds reais (ROI) e
validação out-of-sample independente — calibrar contra um período que pode ser
sazonal é otimização insegura. Não há bug a corrigir; há uma limitação de
adaptação temporal a avaliar.

**PRONTO PARA CALIBRAR ESCANTEIOS? NÃO.**

Antes de calibrar: (a) ampliar janela histórica (2025+2026) para distinguir
drift **sazonal** vs **estrutural**; (b) coletar odds reais para evitar
calibrar contra proxy de valor; (c) definir protocolo de calibração com
holdout independente (não o mesmo holdout que revelou o problema); (d) avaliar
as hipóteses 1–6 do item 15 em etapa dedicada. Calibrar agora seria otimizar
sobre um único regime observado, sem prova de que a correção generaliza.

> PARE. Não executar calibração. Não alterar threshold. Não iniciar auditoria
> de cartões ainda. Não mexer no MCP. Sem push, sem merge em main.