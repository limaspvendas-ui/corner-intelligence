# ETAPA 5B — VALIDAÇÃO TEMPORAL FORA DA AMOSTRA

**OBJETIVO:** validação cronológica formal das regras congeladas do motor. NÃO
treinar, NÃO otimizar, NÃO ajustar threshold, NÃO escolher cutoff por resultado,
NÃO criar ROI sem odds reais, NÃO validar pressão artificialmente.

Módulo: `src/backtest_validacao.py`. Testes: `tests/test_backtest_validacao.py`
(13 testes). Resultado: `docs/etapa5b_validacao_temporal.json`.

---

## 1. CORREÇÃO DOCUMENTAL REALIZADA (FASE A)

Corrigido **somente o relatório** da Etapa 5 (`docs/ETAPA_5_BACKTEST.md`), sem
alterar cálculos históricos. A afirmação "leve superconfiança ~4 p.p." era
incorreta porque o denominador incluía casos NÃO AVALIÁVEL (missing). Resultado
correto da faixa dominante 90–100 (ENTRAR, excl. missing):

- predito médio ≈ **94,31%**
- observado ≈ **93,55%**
- diferença (observado − predito) ≈ **−0,76 p.p.**

Como observado < predito, a interpretação matemática correta é **ligeira
superconfiança de ~0,76 p.p.** (e não subconfiança, e não ~4 p.p.). Corrigidas as
seções 18, 27, 33, 34 e as respostas finais A e D, com nota explícita da causa
(denominador anterior incluía NÃO AVALIÁVEL). Histórico do backtest intacto.

## 2. COMMIT / REGRAS CONGELADAS (FASE B)

- **Commit base das regras:** `4cd9133` (tip de `etapa-5-backtest`, base da
  branch `etapa-5b-validacao-temporal`).
- **Versão das regras do motor:** `prejogo-op-1.0-observacao` (lida da fonte,
  não hardcoded).
- **Versão do engine de backtest:** `backtest-1.0`.
- **Thresholds congelados** (lidos da fonte, não alterados):
  - `PROB_MIN_APROVAR = 0.70`, `PROB_MAX_APROVAR = 0.97`, `CONF_MIN_TOP1 = 0.60`
  - `DEFAULT_LAST_N = 20`
  - Blend: **65% times + 35% liga**; Poisson λ = `0.65*baseline + 0.35*league_mean`
  - H2H com peso menor (componente de confiança `h2h_recente_peso_menor = 0.20`)
  - `MIN_AMOSTRA_OBSERVACAO = 10`, `MIN_AMOSTRA_MADURA = 30`
- **Política:** `src/policy.py` (`LIGAS_PRIORITARIAS` + `LIGAS_EXTENSAO`).
- **Matriz de cobertura (Etapa 4):** 73 ligas na matriz, **29 VIÁVEL**.
- **PROIBIDO durante toda a validação:** alterar threshold, escolher linha
  pós-resultado, mudar mercado, recalibrar probabilidade, eliminar liga por
  desempenho no holdout. Confirmado: nenhum arquivo de regra foi alterado (só
  adicionados `src/backtest_validacao.py` + testes, e corrigido o doc da Etapa 5).

## 3. CRITÉRIO DO HOLDOUT (FASE C)

**Cutoff = data mediana dos fixtures únicos considerados** (critério mecânico,
result-blind: não inspecta taxa de acerto nem probabilidade — só cronologia).

- Cutoff: **2026-05-10T18:30:00-03:00**
- Anterior (date ≤ cutoff): **1.758 fixtures / 135.218 previsões / 1.642 ENTRAR**
- Posterior / HOLDOUT (date > cutoff): **1.869 fixtures / 153.144 previsões / 2.710 ENTRAR**

O posterior é o **holdout intocado pelas regras** (regras congeladas ex-ante).
O cutoff não foi escolhido por resultado; é a mediana cronológica.

## 4. CRITÉRIO DO WALK-FORWARD (FASE C)

**6 blocos consecutivos por quantil de contagem de fixtures únicos** sobre o
ordenamento cronológico. Sem sobreposição, sem embaralhamento. AS_OF por
previsão preserva anti-lookahead dentro de cada bloco (cada previsão usa só
dados com `date < as_of`). Regras idênticas em todos os blocos.

Fixtures por bloco: 507 / 573 / 670 / 623 / 591 / 663.

**Equivalência:** a previsão de um fixture é determinística dada o cache e
`AS_OF = fixture.date`, independente dos demais fixtures no run. Logo, fatiar as
previsões persistidas por data ≡ rodar o engine por janela. Verificado por teste
(`test_slice_equivalente_engine`): run cheio vs run limitado produzem previsões
idênticas por fixture.

## 5. RESULTADO GLOBAL

| corte | ENTRAR | G | P | DEV | NA | hit (excl NA) | prob média | Brier | gap obs−pred |
|---|---|---|---|---|---|---|---|---|---|
| Holdout anterior | 1.642 | 1.534 | 91 | 0 | 17 | 0.9440 | 0.9416 | 0.0525 | +0.0024 |
| **Holdout posterior** | 2.710 | 2.331 | 180 | 0 | 199 | **0.9283** | 0.9433 | 0.0663 | **−0.0150** |
| Bloco 1 | 242 | 227 | 15 | 0 | 0 | 0.9380 | 0.9410 | 0.0579 | −0.0030 |
| Bloco 2 | 598 | 559 | 32 | 0 | 7 | 0.9459 | 0.9425 | 0.0508 | +0.0034 |
| Bloco 3 | 788 | 736 | 43 | 0 | 9 | 0.9448 | 0.9411 | 0.0516 | +0.0037 |
| Bloco 4 | 1.076 | 936 | 78 | 0 | 62 | 0.9231 | 0.9415 | 0.0704 | −0.0184 |
| Bloco 5 | 798 | 668 | 38 | 0 | 92 | 0.9462 | 0.9442 | 0.0505 | +0.0020 |
| Bloco 6 | 850 | 739 | 65 | 0 | 46 | 0.9192 | 0.9449 | 0.0751 | −0.0258 |

gap negativo = superconfiança (observado < predito). Denominador de hit **exclui**
NÃO AVALIÁVEL.

## 6. RESULTADO POR PERÍODO

Holdout anterior vs posterior (geral): hit 0.944 → 0.928 (−1.6 p.p.); gap
+0.0024 → −0.0150. **Deterioração leve mas real** no holdout, com superconfiança
surgindo no posterior. Brier 0.0525 → 0.0663 (piora). Walk-forward: blocos 4 e 6
dip (~0.92), demais ~0.94-0.95.

## 7. RESULTADO POR MERCADO

**GOLS** (estável):

| corte | ENTRAR | G | P | NA | hit | prob | gap |
|---|---|---|---|---|---|---|---|
| Anterior | 1.034 | 983 | 51 | 0 | 0.9507 | 0.9471 | +0.0036 |
| Posterior | 1.884 | 1.775 | 109 | 0 | 0.9421 | 0.9475 | −0.0054 |
| B1..B6 hit | 0.9535 / 0.9479 / 0.9533 / 0.9421 / 0.9533 / 0.9315 | — | — | — | — | — | — |

**ESCANTEIOS** (deterioração temporal):

| corte | ENTRAR | G | P | NA | hit | prob | gap |
|---|---|---|---|---|---|---|---|
| Anterior | 464 | 428 | 33 | 3 | 0.9284 | 0.9308 | −0.0023 |
| Posterior | 429 | 369 | 54 | 6 | **0.8723** | 0.9266 | **−0.0542** |
| B1..B6 hit | 0.9255 / 0.9304 / 0.9265 / **0.8586** / **0.8992** / **0.8739** | — | — | — | — | — | — |

**CARTÕES** (missing dominante, não validável):

| corte | ENTRAR | settled | NA | hit (settled) | gap |
|---|---|---|---|---|---|
| Anterior | 18 | 4 | 14 | 1.0 (n=4, INSUFICIENTE) | +0.0625 |
| Posterior | 289 | 96 | 193 | 0.9062 | −0.0421 |

**RESULTADO (EXPERIMENTAL)**:

| corte | ENTRAR | G | P | hit | prob | gap |
|---|---|---|---|---|---|---|
| Anterior | 126 | 119 | 7 | 0.9444 | 0.9363 | +0.0082 |
| Posterior | 108 | 100 | 8 | 0.9259 | 0.9318 | −0.0059 |
| B1..B6 hit | 0.8947 / 0.9783 / 0.9333 / 0.88 / 0.9565 / 0.9722 (n=19..60, volátil) | — | — | — | — | — |

## 8. RESULTADO POR COMPETIÇÃO (holdout posterior, ENTRAR ≥ 30)

Melhores (calibrados/estáveis): Leagues Cup 772 (hit 0.9875, gap +0.043), 1.Lig
204 (0.9792, +0.037), Primeira Liga 94 (0.9667, +0.026), Allsvenskan 113
(0.9500, gap +0.0004 — melhor calibrado), Serie A Brasil 71 (0.9464, +0.004).

Piores (gaps negativos maiores no holdout): Eerste Divisie 89 (hit 0.8684, gap
**−0.081**), Serie A Itália 135 (0.8824, −0.058), La Liga 140 (0.9000, −0.044),
Serie B Brasil 72 (0.9000, −0.036), Primera A Col 239 (0.9078, −0.034), Ecuador
281 (0.9240, −0.025), Libertadores 13 (0.9130, −0.024).

## 9. ESTABILIDADE TEMPORAL (FASE E)

- **Gols: SIM, estáveis.** 0.94–0.95 em todos os 6 blocos e ambos os holdouts;
  gap próximo de zero (+0.004 → −0.005). Melhor estabilidade temporal.
- **Escanteios: NÃO — deterioração clara.** Três primeiros blocos ~0.93, três
  últimos ~0.86–0.90; holdout posterior 0.8723 vs anterior 0.9284 (−5.6 p.p.);
  gap posterior **−0.054** (superconfiança crescente). Instabilidade temporal
  material.
- **Resultado (EXP): relativamente estável, porém volátil por bloco** (n=19..60,
  sem tendência clara); leve queda posterior 0.944 → 0.926. Experimental — sem
  promoção.
- **Deterioração em períodos posteriores: SIM, leve geral (0.944 → 0.928),
  concentrada em escanteios.** Gols e resultado se mantêm.
- **Campeonato concentra perda:** Eerste Divisie, Serie A Itália, La Liga, Serie
  B Brasil, Primera A Col com gaps negativos mais acentuados no holdout.
- **Dependência excessiva de 2026:** o holdout posterior é todo 2026; a
  deterioração de escanteios ocorre no 2º semestre de 2026. Os números gerais
  **não** são puramente artefato 2026 (gols estável desde 2025), mas escanteios
  está fortemente carregado para o início do período — a headline 89.25% é
  front-loaded e **não se mantém** no holdout.
- **Mudança de calibração entre períodos: SIM em escanteios** (gap +0.0024 →
  −0.054 entre holdouts); gols e resultado estáveis (gaps ~0 em ambos).

## 10. CALIBRAÇÃO POR PERÍODO

| mercado | gap anterior | gap posterior | variação |
|---|---|---|---|
| gols | +0.0036 | −0.0054 | −0.9 p.p. (estável) |
| escanteios | −0.0023 | **−0.0542** | **−5.2 p.p. (superconfiança surge)** |
| cartões | +0.0625 (n=4) | −0.0421 | não interpretável (missing) |
| resultado (EXP) | +0.0082 | −0.0059 | −1.4 p.p. (estável) |

Gols permanece bem calibrado em ambos os períodos. Escanteios perde calibração no
holdout (superconfiança ~5 p.p.). Nenhuma regra nova criada com base nisto.

## 11. COMPORTAMENTO DE CARTÕES (FASE F)

Missing (NÃO AVALIÁVEL) por período:

| período | ENTRAR | NA | % missing |
|---|---|---|---|
| Bloco 2 | 9 | 6 | 66,7% |
| Bloco 3 | 8 | 7 | 87,5% |
| Bloco 4 | 83 | 60 | 72,3% |
| Bloco 5 | 118 | 89 | 75,4% |
| Bloco 6 | 89 | 45 | 50,6% |
| Holdout ant. | 18 | 14 | 77,8% |
| **Holdout post.** | 289 | 193 | **66,8%** |

**O viés de disponibilidade PERSISTE no holdout** (66,8% missing). Concentrado
em Serie A Brasil (40 NA), Serie B (32), Liga Pro (46), Ecuador (27), Argentina
(23). Settled = 96 no holdout posterior, hit 0.9062 — **não generalizável**.
**Cartões NÃO declarados validados.** A taxa de 91%/0.9062 não é usada como taxa
geral.

## 12. STATUS DE ROI (FASE G)

**NÃO AVALIÁVEL.** 0 fixtures encerrados com odds pré-jogo no cache. Nenhuma odd
simulada, nenhuma odd justa tratada como odd de mercado, nenhum edge inventado.
`com_odd_real = 0` em todos os cortes. Alta taxa de acerto ≠ lucratividade.

## 13. STATUS DE PRESSÃO 5/10/15 (FASE H)

**AINDA NÃO AVALIÁVEL.** `live_snapshot_history` não existe; nenhum snapshot
temporal histórico reconstruído artificialmente. Coleta prospectiva necessária
está **preparada** (requisito documentado na Etapa 5, seção 35/FASE O): registrar
snapshot imutável por minuto/evento com fixture, timestamp, minuto, placar,
stats, pressão 5/10/15, versão das regras; anexar resultado após encerramento;
mínimo ≥30 snapshots por janela por mercado antes de reportar taxa.

## 14. TESTES (FASE I)

`tests/test_backtest_validacao.py` — 13 testes:
- split temporal correto (anterior ≤ cutoff, posterior > cutoff, sem sobreposição)
- cutoff mediano mecânico e result-blind
- anti-lookahead entre blocos (datas crescentes, sem sobreposição, as_of == date)
- holdout não altera regras (constantes antes == depois; versões congeladas)
- walk-forward cronológico (6 blocos, union == todos, sem sobreposição)
- nenhuma informação futura (as_of ≤ date em toda previsão)
- nenhum missing vira zero (denominador exclui NÃO AVALIÁVEL)
- cartões NA preservados por bloco
- reprodutível / determinístico
- regras congeladas (valores exatos: 0.70 / 0.97 / 0.60 / versões)
- **equivalência slice == engine por janela** (prova independência por fixture)
- carregar previsões do DB

**Suíte: 462 passed, 18 skipped, 0 failed** (449 anteriores + 13 novos). Nenhum
teste anterior enfraquecido.

## 15. HASH DO COMMIT

- Commit principal (modulo + testes + JSON + relatorio + correcao doc):
  **`23c9df9`** — "etapa 5b: validacao temporal fora da amostra do backtest".
- Commit base das regras congeladas (FASE B): `4cd9133`.
- Branch: `etapa-5b-validacao-temporal` (sem merge em main, sem push).

## 16. GIT STATUS FINAL

Branch `etapa-5b-validacao-temporal`. Arquivos novos: `src/backtest_validacao.py`,
`tests/test_backtest_validacao.py`, `docs/ETAPA_5B_VALIDACAO_TEMPORAL.md`,
`docs/etapa5b_validacao_temporal.json`. Alterado (só doc): `docs/ETAPA_5_BACKTEST.md`
(correção FASE A). **Sem merge em main, sem push, sem remote.** Nenhuma regra do
motor alterada.

---

## RESPOSTAS FINAIS

**VALIDAÇÃO FORA DA AMOSTRA: PARCIAL.**

Justificativa técnica: a validação cronológica formal foi executada (holdout por
mediana + walk-forward de 6 blocos, anti-lookahead preservado, regras congeladas
confirmadas). Resultados:
- **GOLS: estáveis e calibrados no holdout** (0.9507 → 0.9421, gap +0.004 →
  −0.005) e em todos os 6 blocos walk-forward (0.93–0.95). Aprovado para este
  mercado.
- **ESCANTEIOS: NÃO estáveis.** Deterioração material no holdout (0.9284 →
  0.8723, gap −0.002 → −0.054) e nos blocos tardios (0.93 → 0.86–0.90). A
  headline 89.25% é front-loaded e não se mantém fora da amostra inicial.
- **CARTÕES: não validáveis** (66.8% missing no holdout, viés de disponibilidade
  persistente).
- **RESULTADO: experimental**, relativamente estável mas volátil por bloco; sem
  promoção.

Como há um mercado central (escanteios — objeto principal do produto) com
instabilidade temporal não explicada, a validação não é APROVADA plena; como gols
e resultado se mantêm e o método é formalmente correto, também não é REPROVADA.
**PARCIAL.**

**PRONTO PARA ETAPA 6: NÃO.**

Justificativa técnica:
1. Escanteios — mercado-âncora do Corner Intelligence — apresenta deterioração
   temporal no holdout (−5.6 p.p. de hit, gap −5.4 p.p.) não explicada. Iniciar
   Etapa 6 sobre uma regra instável fora da amostra seria construir sobre base
   não confirmada.
2. ROI segue NÃO AVALIÁVEL (0 odds em encerrados). Sem prova de valor financeiro,
   qualquer próximo passo de otimização seria calibrar contra um proxy.
3. Cartões não validáveis (missing estrutural).
4. Superconfiança residual corrigida (~0.76 p.p., não ~4 p.p.) — pequena, mas
   presente no posterior.

Próximo passo recomendado **antes de Etapa 6**: (a) investigar a quebra de
calibração de escanteios no 2º semestre 2026 (benchmark vs histórico por liga);
(b) iniciar coleta prospectiva (FASE O) com odds reais para produzir ROI
realizado; (c) ampliar a janela histórica para confirmar se a deterioração de
escanteios é sazonal ou estrutural. Sem otimizar, sem alterar thresholds.

> PARE. NÃO iniciar Etapa 6. NÃO otimizar. NÃO alterar thresholds. NÃO mexer no
> MCP. Sem push. Sem merge em main.