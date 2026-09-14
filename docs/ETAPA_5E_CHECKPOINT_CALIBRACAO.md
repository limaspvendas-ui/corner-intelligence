# ETAPA 5E — CHECKPOINT DE PRONTIDÃO PARA CALIBRAÇÃO

**OBJETIVO:** checkpoint SOMENTE LEITURA para determinar quais mercados estão
realmente prontos para a Etapa 6 (calibração), quais devem permanecer
congelados, quais precisam de mais dados, e se existe algum bloqueio técnico
ou estatístico antes da Etapa 6. **NENHUMA regra alterada. NENHUM parâmetro
otimizado. NENHUM threshold mexido.**

Módulo: `src/checkpoint_calibracao.py` (read-only). Testes:
`tests/test_checkpoint_calibracao.py` (14 testes). JSON:
`docs/etapa5e_checkpoint_calibracao.json`.

> Princípio: Etapa 6 não deve existir apenas para obrigatoriamente mudar
> parâmetros. Se um mercado está bem calibrado, a conclusão pode ser "NÃO
> ALTERAR". Se nenhum mercado justificar alteração, "ETAPA 6 NÃO DEVE
> ALTERAR O MOTOR NESTE MOMENTO" é um resultado **válido**.

---

## 1. Estado inicial

- branch: `etapa-5e-checkpoint-calibracao` (criada a partir de `main` em `972bfd4`).
- HEAD inicial: `972bfd4b314104c7d60e90e0a2c0387c3c06628e`.
- suíte inicial: **493 passed / 18 skipped / 0 failed**.
- status herdados: CORNERS = EM OBSERVAÇÃO; CARTÕES = NÃO AVALIÁVEL;
  RESULTADO = EXPERIMENTAL EM OBSERVAÇÃO; PRESSÃO = EXPERIMENTAL/A CALIBRAR;
  ROI = NÃO VALIDADO.

## 2. Evidências consideradas (artefatos reais, não memória)

Lidos diretamente do projeto:
- `docs/ETAPA_5_BACKTEST.md` + `docs/etapa5_baseline_panorama.json` (baseline in-sample, odds/ROI).
- `docs/ETAPA_5B_VALIDACAO_TEMPORAL.md` + `docs/etapa5b_validacao_temporal.json` (holdout + walk-forward 6 blocos, por mercado).
- `docs/ETAPA_5C_AUDITORIA_ESCANTEIOS.md` + `docs/etapa5c_auditoria_escanteios.json` (drift escanteios, within-league).
- `docs/ETAPA_5D_AUDITORIA_CARTOES.md` + `docs/etapa5d_auditoria_cartoes.json` (207 NA, causa API).
- `docs/ETAPA_4_MATRIZ_COBERTURA_API.md` (matriz 73 ligas / 16 colunas).
- `docs/ETAPA_3_PRESSAO_LIVE.md` (pressão live, snapshots).
- Consultas read-only ao DB: `bt_predictions` (coluna `odd_real`), tabelas `live_snapshot_history`/`live_snapshots`.
- `src/politica_aprovacao.py` (thresholds congelados), `src/settlement.py` (convenção cartões).

Run auditada: `bt-20260913224716-PRE_GAME` (3825 fixtures, 288362 previsões,
4352 ENTRAR, backtest-1.0, regras `prejogo-op-1.0-observacao`).

## 3. GOALS

| corte | ENTRAR | G | P | NA | hit | prob | Brier | gap |
|---|---|---|---|---|---|---|---|---|
| Anterior | 1.034 | 983 | 51 | 0 | 0.9507 | 0.9471 | 0.0466 | +0.0036 |
| Posterior | 1.884 | 1.775 | 109 | 0 | 0.9421 | 0.9475 | 0.0543 | −0.0054 |
| B1..B6 | — | — | — | — | 0.9535/0.9479/0.9533/0.9421/0.9533/0.9315 | — | — | — |

- **Backtest:** PASS (in-sample hit 0.9452, 2918 ENTRAR, **0 NA**, AMOSTRA MADURA).
- **Out-of-sample:** PASS — estável em todos os 6 blocos walk-forward (0.93–0.95);
  drift anterior→posterior **−0,86 p.p.** (não material, < 3 p.p.).
- **Drift:** NÃO MATERIAL.
- **Viés de disponibilidade:** NÃO (0 NA em todos os cortes).
- **Settlement:** CONFIÁVEL (0 NA, 100% liquidável).
- **Calibração:** BOA — gap posterior **−0,54 p.p.** (ligeira superconfiança, dentro do ruído); faixa dominante 90–100: predito 94,31% vs observado 93,55% (gap −0,76 p.p., corrigido na Etapa 5B).
- **ROI:** NÃO (0 odds).
- **Maturidade:** AMOSTRA MADURA.

**Conclusão GOALS:** o mercado está **bem calibrado e estável**. NÃO há desvio
de calibração que justifique Etapa 6. Alterar parâmetros agora seria otimizar
sobre o holdout já observado (overfitting) sem ROI para validar valor.
**NÃO ALTERAR.**

## 4. CORNERS

| corte | ENTRAR | G | P | NA | hit | prob | Brier | gap |
|---|---|---|---|---|---|---|---|---|
| Anterior | 464 | 428 | 33 | 3 | 0.9284 | 0.9308 | 0.0660 | −0.0023 |
| Posterior | 429 | 369 | 54 | 6 | **0.8723** | 0.9266 | 0.1138 | **−0.0542** |
| B1..B6 | — | — | — | — | 0.9255/0.9304/0.9265/**0.8586**/**0.8992**/**0.8739** | — | — | — |

- **Backtest:** PASS (in-sample hit 0.8925, 893 ENTRAR, 9 NA, MADURA).
- **Out-of-sample:** **NÃO PASS** — drift material **−5,61 p.p.** (0.9284 → 0.8723).
- **Drift:** MATERIAL — within-league confirmado (contrafatual 0.9385 >
  0.9284 > 0.8723 real: a mudança de mix é **excluída**; cada liga piorou).
  Início em **Maio/2026, ANTES do cutoff** (10-Mai) → não é artefato de holdout.
  Blocos walk-forward 4–6 colapsam (~0.86–0.90). Under mais afetado.
- **Viés de disponibilidade:** NÃO (sem problema material de fonte; 9 NA apenas).
- **Settlement:** CONFIÁVEL (0/893 mismatches, Etapa 5C).
- **Calibração:** PIORA — gap posterior **−5,42 p.p.** (superconfiança crescente); Brier 0.066 → 0.114.
- **Bug/fonte:** NENHUM (auditoria 5C excluiu parser, settlement, cache, mix).
- **ROI:** NÃO.
- **Status:** EM OBSERVAÇÃO — DRIFT TEMPORAL DETECTADO.

**CORNERS pode participar da Etapa 6 agora?** **NÃO — SOMENTE MONITORAMENTO.**
A regra está instável fora da amostra; calibrar agora = construir sobre base
não confirmada + otimizar sobre o holdout já observado (overfitting) sem ROI.

## 5. CARDS

| corte | ENTRAR | settled | NA | hit (settled) | gap |
|---|---|---|---|---|---|
| Anterior | 18 | 4 | 14 | 1.0 (n=4, INSUFICIENTE) | +0.0625 |
| Posterior | 289 | 96 | 193 | 0.9062 | −0.0421 |
| **Total** | **307** | **100** | **207** | 0.910 (91/100) | — |

- **Backtest:** PARCIAL — 307 ENTRAR, mas **207 NA (67%)** → não validável.
- **Out-of-sample:** NÃO VALIDÁVEL — missing estrutural persiste (66,8% no holdout).
- **Causa:** API (`/fixtures/statistics` retorna Red Cards = `null` em 195/204
  fixtures NA; 71% das ocorrências de Red Cards no cache são `null`).
- **Viés de disponibilidade:** SIM — os 100 liquidados (91% hit) são subset
  não aleatório com Red informado; **não generalizável**.
- **Bug:** NÃO (parser preserva null≠"0"; settlement 0/307 mismatches; cache pós-partida).
- **Hipótese 95 recuperáveis:** por dominância matemática (Over com Yellow >
  linha, Red irrelevante). **NÃO IMPLEMENTADA / NÃO VALIDADA** — exige
  autorização + validação out-of-sample independente. É mudança de regra, não bug fix.
- **ROI:** NÃO.
- **Status:** NÃO AVALIÁVEL — LIMITAÇÃO ESTRUTURAL DA FONTE.

**CARDS pronto para Etapa 6?** **NÃO.** Não permitir que cartões entre em
calibração somente porque os 100 liquidados tiveram 91% de hit.

## 6. RESULTADO

| corte | ENTRAR | G | P | NA | hit | prob | Brier | gap |
|---|---|---|---|---|---|---|---|---|
| Anterior | 126 | 119 | 7 | 0 | 0.9444 | 0.9363 | 0.0523 | +0.0082 |
| Posterior | 108 | 100 | 8 | 0 | 0.9259 | 0.9318 | 0.0690 | −0.0059 |
| B1..B6 | — | — | — | — | 0.8947/0.9783/0.9333/0.88/0.9565/0.9722 (n=19..60) | — | — | — |

- **Backtest:** PASS (in-sample hit 0.9359, 234 ENTRAR, 0 NA).
- **Out-of-sample:** PARCIAL — relativamente estável (drift −1,85 p.p.) mas
  **volátil por bloco** (hit 0.88–0.98, n=19..60, sem tendência clara).
- **Calibração:** estável leve (gap +0.008 → −0.006).
- **Amostra:** pequena (234 ENTRAR); EXPERIMENTAL.
- **ROI:** NÃO.
- **Status:** EXPERIMENTAL EM OBSERVAÇÃO.

**RESULTADO pronto para Etapa 6?** **NÃO — NÃO PROMOVER.** Não promover
automaticamente mesmo com hit alto; amostra pequena e volátil, sem ROI.

## 7. PRESSÃO LIVE (5/10/15)

- `live_snapshot_history`: **tabela NÃO existe** (Etapa 3 preparou a estrutura
  mas a coleta prospectiva nunca rodou). `live_snapshots`: 1 linha (cache do
  último snapshot, `INSERT OR REPLACE`, sem série temporal).
- **Snapshots temporais históricos suficientes:** NÃO.
- **Janelas 5/10/15 validáveis:** NÃO — sem histórico para reconstruir janelas.
- **Thresholds LOW/MODERATE/HIGH:** PLACEHOLDER NÃO-CALIBRADOS (nenhum
  threshold operacional validado; `PRESSURE_WINDOWS=(5,10,15)` definido, mas
  classificação qualitativa fica para backtest futuro).
- **Backtest:** INVIÁVEL. Não reconstruir pressão artificialmente a partir de
  dados finais.
- **ROI:** NÃO.
- **Status:** EXPERIMENTAL / A CALIBRAR.

**PRESSÃO LIVE pronta para Etapa 6?** **NÃO.** Sem dados para calibrar.

## 8. ODDS / ROI

Confirmado data-driven via DB (`bt_predictions.odd_real`):

- **ENTRAR com odd_real > 0: 0 / 4352** (100% sem odd real).
- Cache `/odds` pré-jogo: 86 respostas, 78 com dados, mas **0 em encerrados**
  (todas partidas não-finalizadas); `/odds/live`: 28, todas `suspended:true`, 0 usáveis.
- **ROI:** NÃO AVALIÁVEL. `pl_unitario = null`, `predicted_edge = None`,
  `predicted_ev = None` em todas as liquidadas.
- **Edge / EV / P/L validáveis:** NÃO.
- "Odd justa calculada" **nunca** tratada como odd de mercado (regra afirmada
  na Etapa 5: "odd justa vs prob ≠ ROI realizado"; nenhuma odd fictícia inventada).

**ROI = NÃO AVALIÁVEL** mantido. Alta taxa de acerto ≠ lucratividade.

## 9. Matriz de prontidão

| MERCADO | DADOS | BACKTEST | OUT-OF-SAMPLE | CALIBRAÇÃO | DRIFT | ROI | STATUS ATUAL | PODE ENTRAR NA ETAPA 6? |
|---|---|---|---|---|---|---|---|---|
| **GOALS** | SIM | SIM | SIM | BOA (−0,5 p.p.) | NÃO MATERIAL | NÃO | OPERACIONAL (sem ROI) | **PARCIAL** |
| **CORNERS** | SIM | SIM | NÃO | PIORA (−5,4 p.p.) | MATERIAL | NÃO | EM OBSERVAÇÃO | **NÃO** |
| **CARDS** | PARCIAL (67% NA) | PARCIAL | NÃO VALIDÁVEL | N/A | N/A | NÃO | NÃO AVALIÁVEL | **NÃO** |
| **RESULTADO** | SIM (n pequeno) | SIM | PARCIAL (volátil) | ESTÁVEL LEVE | LEVE | NÃO | EXPERIMENTAL EM OBSERVAÇÃO | **NÃO** |
| **PRESSÃO LIVE** | NÃO (sem histórico) | NÃO | NÃO | N/A | N/A | NÃO | EXPERIMENTAL / A CALIBRAR | **NÃO** |

SIM / NÃO / PARCIAL apenas, com justificativa objetiva por mercado (itens 3–7).

## 10. Escopo da Etapa 6

**Nenhum mercado justifica alteração agora:**
- GOALS: bem calibrado → **NÃO ALTERAR** (Etapa 6 não deve mudar o que já está calibrado).
- CORNERS: drift material, regra instável fora da amostra → calibrar agora é overfitting.
- CARDS: não validável (missing estrutural).
- RESULTADO: experimental, amostra pequena, volátil.
- PRESSÃO: sem dados.

**Declaração explícita: ETAPA 6 NÃO DEVE ALTERAR O MOTOR NESTE MOMENTO.**
Este é um resultado válido. Parâmetros que poderiam ser **estudados** (não
executados) se holdout independente + ROI existissem: calibração da
superconfiança residual de escanteios (gap −5,4 p.p. no posterior). Parâmetros
que devem **permanecer congelados**: todos (threshold 0.70/0.97/0.60,
LAST_N=20, blend 65/35, Poisson, H2H peso 0.20, MIN_AMOSTRA 10/30, settlement,
convenção cartões, matriz de cobertura, política de universo).

Critério de validação que **deverá** ser usado se Etapa 6 for aberta:
holdout fresco ainda não observado + ROI realizado com odds reais em encerrados;
nenhuma recomendação pode depender de parâmetros que ficaram melhores no
próprio holdout já auditado.

## 11. Mercados aptos para Etapa 6

**Nenhum.** GOALS é o único estatisticamente pronto, mas está bem calibrado →
não precisa de alteração. Os demais estão bloqueados (drift / missing /
experimental / sem dados).

## 12. Mercados bloqueados

- **CORNERS:** bloqueado por drift temporal material (within-league, onset
  antes do cutoff). SOMENTE MONITORAMENTO.
- **CARDS:** bloqueado por missing estrutural (67% NA, Red Cards null).
- **RESULTADO:** bloqueado por ser experimental (amostra pequena, volátil).
- **PRESSÃO LIVE:** bloqueado por ausência de snapshots históricos.
- **TODOS:** bloqueados para validação financeira (ROI NÃO AVALIÁVEL, 0 odds).

## 13. Riscos de overfitting

- **Holdout independente disponível?** NÃO. O holdout posterior da Etapa 5B
  (pós 2026-05-10) **já foi observado e auditado** em 5B/5C/5D.
- **Risco de otimizar sobre o mesmo período já auditado?** SIM, alto. Qualquer
  parâmetro escolhido agora seria escolhido por ficar melhor no próprio holdout
  já visto → não distinguível de overfitting.
- **Outro período para validação posterior?** NÃO. Dados terminam em 2026-09-06.
- **Como impedir que melhoria in-sample seja confundida com melhoria real?**
  Exigir (a) holdout fresco ainda não observado OU (b) validação de ROI com
  odds reais em encerrados. **Nenhum dos dois disponível hoje.**
- Logo: qualquer calibração agora seria calibrar contra um proxy sem prova de
  valor financeiro, sobre um holdout já inspecionado. **Risco não controlável.**

## 14. Recomendações

1. **NÃO iniciar Etapa 6 (calibração) agora.** Nenhum mercado precisa de
   alteração; overfitting não controlável.
2. **Próximos passos são COLETA DE DADOS, não calibracao:**
   - (a) coleta prospectiva de **odds reais pré-jogo em encerrados** para
     produzir ROI realizado (fecha a lacuna financeira de todos os mercados);
   - (b) popular **`live_snapshot_history`** (coleta prospectiva 60s) para
     validar PRESSÃO 5/10/15 e calibrar thresholds LOW/MODERATE/HIGH;
   - (c) **ampliar a janela temporal** para confirmar se o drift de escanteios
     é sazonal ou estrutural (precisa de período fresco além de 2026-09-06);
   - (d) eventual **coleta de `/fixtures/events`** como fonte factual
     alternativa de cartões (hoje 0/207 NA têm events).
3. **GOALS: NÃO ALTERAR** — manter como mercado-âncora validado (estável,
   calibrado, settlement confiável). Status operacional: OPERACIONAL, porém
   sem ROI (sem prova financeira).
4. **CORNERS: manter EM OBSERVAÇÃO** — futuras análises de escanteios devem
   carregar o status. Não alterar regras.
5. **CARDS: manter NÃO AVALIÁVEL** — hipótese dos 95 recuperáveis permanece
   NÃO IMPLEMENTADA / NÃO VALIDADA.
6. **RESULTADO: manter EXPERIMENTAL EM OBSERVAÇÃO** — não promover.

## 15. Testes

`tests/test_checkpoint_calibracao.py` — 14 testes:
- read-only (DB inalterado); determinístico; JSON serializável;
- GOALS: 2918 ENTRAR / 0 NA / drift < 3 p.p. / PARCIAL / NÃO ALTERAR;
- CORNERS: 893 ENTRAR / drift ≤ −3 p.p. (material) / within-league / NÃO;
- CARDS: 307 ENTRAR / 207 NA / na_pct ≥ 0.40 / hipótese 95 NÃO IMPLEMENTADA / NÃO;
- RESULTADO: 234 ENTRAR / 0 NA / experimental / NÃO PROMOVER / NÃO;
- PRESSÃO: live_snapshot_history não existe / janelas não validáveis / NÃO;
- ROI: 0/4352 com odd real / NÃO AVALIÁVEL / edge-ev-pl não validável;
- matriz 5 linhas / todos ROI=NÃO;
- overfitting: holdout independente=NÃO / risco otimizar=SIM / outro período=NÃO;
- respostas obrigatórias (7 valores); conclusão "ETAPA 6 NÃO DEVE ALTERAR O MOTOR";
- regras do motor congeladas (thresholds 0.70/0.97/0.60 + convenção cartões).

**Suíte completa: 507 passed, 18 skipped, 0 failed** (493 anteriores + 14 novos).

## 16. Commit

A ser registrado na branch `etapa-5e-checkpoint-calibracao`.

## 17. Branch / git status

- branch: `etapa-5e-checkpoint-calibracao` (de `main` em `972bfd4`).
- arquivos novos: `src/checkpoint_calibracao.py`, `tests/test_checkpoint_calibracao.py`,
  `docs/ETAPA_5E_CHECKPOINT_CALIBRACAO.md`, `docs/etapa5e_checkpoint_calibracao.json`.
- **Nenhum arquivo do motor alterado.** Sem merge em main, sem push, sem remote.

---

## RESPOSTAS OBRIGATÓRIAS

**GOALS PRONTO PARA ETAPA 6?** **PARCIAL** — o único mercado estatisticamente
pronto (estável, calibrado, sem NA, settlement confiável, amostra madura), mas
**bem calibrado** → Etapa 6 não deve alterá-lo. Escopo permitido: **NÃO ALTERAR**
(somente monitoramento). Alterar agora = overfitting sobre holdout já observado,
sem ROI para validar valor.

**CORNERS PRONTO PARA ETAPA 6?** **NÃO** — drift temporal material (−5,6 p.p.,
within-league, onset Maio/2026 antes do cutoff), superconfiança posterior
(−5,4 p.p.), sem bug/fonte. SOMENTE MONITORAMENTO. Calibrar agora = overfitting.

**CARDS PRONTO PARA ETAPA 6?** **NÃO** — 67% NA estrutural (Red Cards null),
viés de disponibilidade, não validável. Hipótese 95 recuperáveis NÃO
IMPLEMENTADA / NÃO VALIDADA.

**RESULTADO PRONTO PARA ETAPA 6?** **NÃO** — EXPERIMENTAL EM OBSERVAÇÃO,
amostra pequena (234), volátil por bloco, sem ROI. NÃO PROMOVER.

**PRESSÃO LIVE PRONTA PARA ETAPA 6?** **NÃO** — `live_snapshot_history` não
existe, janelas não validáveis, thresholds placeholder não-calibrados, sem
histórico para backtest.

**EXISTE ALGUM MERCADO QUE REALMENTE PRECISE DE ALTERAÇÃO AGORA?** **NÃO.**
GOALS não precisa (bem calibrado); os demais estão bloqueados (drift /
missing / experimental / sem dados). Etapa 6 não deve existir só para mudar parâmetros.

**ETAPA 6 PODE SER INICIADA?** **NÃO.**

**Escopo permitido (se SIM ou PARCIAL):** GOALS é PARCIAL — o escopo permitido
é **NÃO ALTERAR** (somente monitoramento). Nenhum parâmetro deve ser
modificado neste momento. Os próximos passos são **coleta de dados** (odds
reais em encerrados para ROI; `live_snapshot_history` para pressão; ampliação
da janela temporal; eventual `/fixtures/events` para cartões), **não
calibração**. Etapa 6 só deverá ser reaberta quando existir (a) holdout fresco
ainda não observado E/OU (b) ROI realizado com odds reais — condições que hoje
não estão satisfeitas.

> PARE. NÃO faça calibração. NÃO altere regras. NÃO faça merge. NÃO inicie
> Etapa 6.