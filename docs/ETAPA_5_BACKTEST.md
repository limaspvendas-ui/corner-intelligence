# ETAPA 5 — BACKTEST DEDICADO DO CORNER INTELLIGENCE

**REGRA CENTRAL:** PRECISÃO > QUANTIDADE. FATO → CÁLCULO → INTERPRETAÇÃO → DECISÃO.
Nenhum dado inventado, nenhum histórico reconstruído, nenhum resultado futuro na
previsão, nenhum missing virado zero, nenhum parâmetro otimizado depois de ver o
resultado, nenhum ROI sem odd real, nenhuma regra experimental promovida.

Engine: `src/backtest.py` (versão `backtest-1.0`). Testes: `tests/test_backtest.py`
(27 testes). Armazenamento próprio: `data/backtest.db` (SEPARADO do ledger
operacional `data/recomendacoes.db`). Baseline: `docs/etapa5_baseline_panorama.json`.

---

## 1. ESTADO INICIAL

- Branch: `etapa-5-backtest` (criada a partir de `main` após fast-forward da Etapa 4).
- Commit pai: `6f77423` (etapa 4: consolidar matriz real de cobertura da api).
- Suíte antes da Etapa 5: **422 passed, 18 skipped, 0 failed** (main e branch).
- Working tree limpa antes de iniciar. Nenhum push, nenhum remote, nenhum merge em main.

## 2. AUDITORIA DO HISTÓRICO DISPONÍVEL (FASE A)

Cache SQLite `data/corner_intelligence.db`, tabela `api_cache` (9.280 linhas):

| endpoint | linhas | observação |
|---|---|---|
| `/fixtures/statistics` (half=true) | 6.851 | 4.906 com `statistics_1h` (1º tempo real) |
| `/fixtures/statistics` (sem half) | 229 | só jogo completo |
| `/fixtures` | 1.102 respostas | 23.096 fixtures únicos, 21.681 encerrados |
| `/odds` (pré-jogo) | 86 | 78 com dados — **0 encerrados** |
| `/odds/live` | 28 | auditoria: indisponível na fonte (suspended) |
| `/fixtures/events` | 173 | só 173 fixtures |
| `/fixtures/headtohead` | 148 | confrontos diretos |
| `/teams` | 626 | times |
| `/leagues` | 37 | ligas |

- 7.028 fixtures únicos com estatística cacheada; 4.906 com split 1ºT/2ºT real.
- **`live_snapshot_history` NÃO EXISTE** (tabela da Etapa 3 nunca foi populada);
  `live_snapshots` tem 1 linha. → **PRESSÃO 5/10/15 = AINDA NÃO AVALIÁVEL**.
- Ledger operacional `recomendacoes`: 79 linhas, **0 com odd real**.

Classificação de backtestabilidade (FASE A):
- **PRE-JOGO: BACKTESTÁVEL.** Histórico + benchmarks reconstruíveis do cache com
  filtro AS_OF; motor invocado diretamente; resultado final conhecido para liquidar.
- **LIVE DE PONTO ÚNICO: PARCIALMENTE BACKTESTÁVEL.** Só no intervalo (minuto ~45,
  status HT) via `statistics_1h` + placar do intervalo. Não minutos arbitrários; a
  fotografia final nunca é tratada como trajetória.
- **PRESSÃO 5/10/15: AINDA NÃO AVALIÁVEL.** Sem snapshots temporais históricos.

## 3. O QUE É BACKTESTÁVEL NO PRE

4.906 fixtures encerrados com estatística completa + 1º tempo. Para cada fixture
encerrado numa liga VIÁVEL: histórico dos times (últimos 20 antes de AS_OF),
benchmark da liga (encerradas antes de AS_OF), H2H (confrontos antes de AS_OF com
estatística), avaliação Poisson 90min, aprovação pela disciplina congelada e
liquidação pelo resultado final. Nenhum dado pós-kickoff entra na previsão.

## 4. O QUE É BACKTESTÁVEL NO LIVE

Apenas o **intervalo (HT)**. `LiveSnapshot` reconstruído de `statistics_1h` +
placar do intervalo (`score.halftime`); `deep_dive` reusado integralmente via
`CacheClient` (proxy AS_OF sobre o cache). O pipeline de aprovação real
(`_aprovar`) inclui auditoria de status "ainda ao vivo" que exige leitura fresca
— **não reproduzível honestamente do cache** (o status cacheado é o final). A
decisão LIVE_HT usa o MESMO critério de janela prob/conf do motor live, sem a
auditoria de frescor (checagem operacional, não de calibração). Classificação:
**PARCIALMENTE BACKTESTÁVEL**. O baseline desta etapa foca PRE (FASE Q); LIVE_HT
fica implementado e classificado, sem execução de baseline agora.

## 5. SITUAÇÃO DA PRESSÃO 5/10/15 (FASE D/N)

**AINDA NÃO AVALIÁVEL.** A tabela `live_snapshot_history` (Etapa 3) não existe no
cache; `live_snapshots` tem 1 linha. Não há snapshots temporais históricos para
reconstruir janelas de 5/10/15 min. **Nenhuma taxa de acerto é apresentada.** Nenhum
snapshot artificial, nenhuma interpolação, nenhuma fotografia final como trajetória.
Coleta prospectiva (FASE O) é o único caminho.

## 6. ARQUITETURA DO ENGINE (FASE E)

`src/backtest.py` — mecanismo dedicado, separado do fluxo operacional:
- `CacheIndex`: carrega `api_cache` em memória; acesso AS_OF-filtrado
  (`team_history`, `league_benchmark_*`, `h2h_n`, `match_stats`).
- `CacheClient`: adapter que simula `APIFootballClient.get` sobre o `CacheIndex`
  com filtro AS_OF, para reusar `deep_dive` no LIVE_HT sem rede/vazamento.
- `BacktestEngine`: modo PRE (motor pré-jogo direto) / LIVE_HT (`deep_dive`) /
  PRESSAO (vazio). Aplica matriz de cobertura da Etapa 4 antes da aprovação.
- `BacktestStore`: grava em `data/backtest.db` (tabelas `bt_runs`, `bt_predictions`).
  **Nunca toca `data/recomendacoes.db`.**
- `Metrics`: cortes por mercado/liga/modo/decisão/faixa de prob; Brier, taxa de
  acerto, maturidade, ROI só onde há odd real.
- CLI: `python -m src.backtest run --modo PRE_GAME [--ligas ...] [--panorama]`.

## 7. PROTEÇÃO CONTRA LOOKAHEAD (FASE F)

- Toda previsão registra `as_of` (= `date` do fixture, momento da decisão).
- `team_history`: só jogos com `date < as_of` (estritamente).
- `league_benchmark_*`: só encerradas com `date < as_of`.
- `h2h_n`: só confrontos com `date < as_of` e estatística cacheada.
- `CacheClient` (LIVE_HT): filtra `/fixtures` por `date < as_of`.
- Missing → `NÃO AVALIÁVEL`, nunca zero.
- Testes: `test_team_history_exclui_fixture_futuro`, `test_benchmark_respeita_as_of`,
  `test_resultado_final_nao_entra_na_previsao`, `test_toda_previsao_tem_as_of`,
  `test_none_nao_vira_zero`.

## 8. REGRAS CONGELADAS (FASE G)

Nenhum parâmetro do motor é otimizado. O engine avalia as regras COMO ESTÃO:
`avaliar_pregame`, `avaliar_resultado_prejogo`, `avaliar_cartoes_prejogo`,
`aprovar_pregame` (PROB_MIN_APROVAR=0.70, PROB_MAX_APROVAR=0.97, CONF_MIN_TOP1=0.60,
máx. 2/jogo), `deep_dive` (LIVE_HT). Versão das regras lida da fonte:
`prejogo-op-1.0-observacao`. Poisson, blend_rate, EDGE_MINIMO, pesos 65/35, leque de
linhas — nada é alterado. Primeiro resultado = baseline (FASE R: sem segundo try).

## 9. INTERVALO DE DATAS

Baseline PRE: **2025-09-13 .. 2026-09-06** (datas dos fixtures encerrados nas 29
ligas VIÁVEIS com estatística cacheada). Sem `--inicio`/`--fim` (todas as
encerradas VIÁVEIS no cache).

## 10. COMPETIÇÕES TESTADAS

29 ligas VIÁVEL da matriz Etapa 4: 2, 3, 11, 13, 39, 61, 71, 72, 78, 88, 89, 94,
106, 113, 128, 135, 140, 144, 203, 204, 239, 242, 253, 262, 281, 307, 475, 624,
772. Nenhuma PARCIAL/INVIÁVEL/A CONFIRMAR foi auto-incluída (FASE K).

## 11. FIXTURES TOTAIS

**3.825 fixtures considerados** (encerrados, com estatística, em ligas VIÁVEL).

## 12. FIXTURES EXCLUÍDOS E MOTIVOS

**0 excluídos** no universo VIÁVEL (todos os encerrados-VIÁVEL-com-stats entraram).
Fixtures de ligas não-VIÁVEL (PARCIAL/INVIÁVEL/A CONFIRMAR) não são considerados por
filtro de matriz — não há "exclusão dentro do VIÁVEL". `status_backtest` decide antes
do run; PARCIAL fica fora automaticamente (FASE K).

## 13. RESULTADOS GOALS (PRE)

Todas as previsões (leque completo): 51.418 previsões, hit 0.50 (esperado: leque ao
redor do equilíbrio é ~50/50 até o filtro de aprovação revelar calibração), Brier
0.1288, AMOSTRA MADURA.

**ENTRAR (aprovadas):**

| métrica | valor |
|---|---|
| aprovadas | 2.918 |
| ganhas | 2.758 |
| perdidas | 160 |
| devolvidas | 0 |
| NÃO AVALIÁVEL | 0 |
| taxa de acerto | **0.9452** |
| Brier | 0.0516 |
| maturidade | AMOSTRA MADURA |
| com odd real | 0 |
| ROI | N/D (sem odd real) |

## 14. RESULTADOS CORNERS (PRE)

Todas: 65.286 previsões, hit 0.491, Brier 0.1838, 1.170 NÃO AVALIÁVEL, MADURA.

**ENTRAR:**

| métrica | valor |
|---|---|
| aprovadas | 893 |
| ganhas | 797 |
| perdidas | 87 |
| NÃO AVALIÁVEL | 9 |
| taxa de acerto | **0.8925** |
| Brier | 0.0889 |
| maturidade | AMOSTRA MADURA |
| ROI | N/D (sem odd real) |

## 15. RESULTADOS CARDS (PRE)

Todas: 56.602 previsões, hit 0.1275, **42.164 NÃO AVALIÁVEL** (estatística de
cartões final ausente na maior parte do cache — amarelo/vermelho não reportados),
Brier 0.1631, MADURA (mas com forte viés de missing).

**ENTRAR:**

| métrica | valor |
|---|---|
| aprovadas | 307 |
| ganhas | 91 |
| perdidas | 9 |
| NÃO AVALIÁVEL | 207 (missing de cartões finais) |
| taxa de acerto (sobre settled) | 0.910 (100/110 settled) |
| taxa de acerto (sobre total) | **0.296** |
| maturidade | EM OBSERVAÇÃO (settled small) |
| ROI | N/D (sem odd real) |

**RISCO:** 67% das aprovadas de cartões não puderam ser liquidadas por ausência de
cartões finais no cache. A taxa sobre settled é alta, mas a amostra liquida é
pequena e o missing é estrutural — **cartões ficam EM OBSERVAÇÃO**, não validados.

## 16. RESULTADO (PRE — EXPERIMENTAL EM OBSERVAÇÃO, FASE M)

Todas: 115.056 previsões (1X2/DC/DNB/AH), hit 0.4334, 6.284 devolvidas, 4.516 meia
vitória, 4.516 meia derrota, Brier 0.1964, MADURA. **experimental=True em todas.**

**ENTRAR:**

| métrica | valor |
|---|---|
| aprovadas | 234 |
| ganhas | 219 |
| perdidas | 15 |
| taxa de acerto | **0.9359** |
| Brier | 0.060 |
| maturidade | AMOSTRA MADURA |
| status | **EXPERIMENTAL EM OBSERVAÇÃO (sem promoção)** |

**FASE M:** mesmo com taxa alta e amostra madura, RESULTADO **não é promovido**.
O marcador experimental é congelado em cada previsão e o backtest não pode removê-lo.

## 17. LIVE (se honestamente backtestable)

LIVE_HT implementado (intervalo), classificado **PARCIALMENTE BACKTESTÁVEL**. Sem
execução de baseline nesta etapa (FASE Q prioriza PRE; a auditoria de frescor do
pipeline live não é reproduzível do cache). Pendente de execução futura + coleta
prospectiva.

## 18. CALIBRAÇÃO (ENTRAR, por faixa de probabilidade predita)

| faixa | previsões | taxa observada | Brier |
|---|---|---|---|
| 80–90% | 30 | 0.7667 | 0.175 |
| 90–100% | 4.322 | 0.8889 | 0.060 |

**Interpretação:** na faixa 90–100% (predição média ~93%), o observado é 88.9% —
** leve superconfiança** (~4 p.p. abaixo do predito). Na faixa 80–90% o observado
(76.7%) fica próximo do piso. Brier baixo (0.06) na faixa dominante. Nada é
"corrigido" — baseline congelado (FASE G/R).

## 19. TAXA DE ACERTO POR MERCADO (ENTRAR)

gols 0.9452 · escanteios 0.8925 · resultado 0.9359 (EXP) · cartões 0.296 (total) /
0.910 (settled). Ver seções 13–16.

## 20. ABSTENÇÃO / NÃO AVALIÁVEL

- Decisões: ENTRAR 4.352 · AGUARDAR 58.531 · DESCARTAR 225.479.
- NÃO AVALIÁVEL (liquidação): 43.334 previsões — majoritariamente cartões (42.164)
  por ausência de cartões finais no cache; 1.170 escanteios; 0 gols; 0 resultado.
- Taxa de abstensão (não-ENTRAR): ~98,5% das previsões — esperado: o leque é largo
  e a janela de aprovação é estreita por construção (disciplina do motor).

## 21. ODDS / EDGE / EV (FASE H)

**0 previsões com odd real** entre as liquidadas: nenhuma fixture encerrada no cache
tem `/odds` pré-jogo (as 78 com odds são partidas não-encerradas). Logo:
- `predicted_edge` = None em todas as liquidadas.
- `predicted_ev` = None em todas as liquidadas.
- O extrator de odds foi **validado isoladamente** (`test_extrator_ah_e_1x2`,
  `test_odd_real_permitida_calcula_edge_ev`) e retorna a melhor price entre
  bookmakers (Pinnacle/Bet365/1xBet) quando há odds — o mecanismo está pronto; a
  amostra cacheada é que não cruza odds+resultado final.

## 22. ROI SOMENTE ONDE HOUVER ODDS REAIS

**ROI não calculado para nenhuma previsão** (0 com odd real). Nenhuma odd fictícia,
nenhuma cota inventada. Onde o extrator encontra odd real, `_edge_ev` e
`_ganho_unitario` calculam edge/EV/P/L; como não há cruzamento odds+encerrado no
cache, todos os campos financeiros são None. **Risco/pendência:** coletar odds
pré-jogo prospectivamente para partidas que serão encerradas (FASE O).

## 23. AMOSTRAS INSUFICIENTES (FASE I)

Ligas ENTRAR com AMOSTRA INSUFICIENTE (<10 settled): Bundesliga (4, hit 0.75),
Jupiler Pro League (6, hit 1.0), Paulistão A1 (6, hit 0.833), Carioca 1 (2, hit
1.0). **Nenhuma promovida** — 100% em amostra insuficiente não valida nada
(regra central: 4/4 = AMOSTRA INSUFICIENTE).

## 24. AMOSTRAS MADURAS (FASE I)

Ligas ENTRAR com AMOSTRA MADURA (≥30 settled) e taxa: Leagues Cup 0.9875 (80),
MLS 0.9419 (534), Allsvenskan 0.9478 (230), Primeira Liga 0.9474 (38), Eredivisie
0.9286 (42), Liga MX 0.9242 (66), La Liga 0.9118 (68), Copa Libertadores 0.9085
(164), Liga Argentina 0.8914 (580), Copa Sudamericana 0.8866 (194), Primera A Col
0.8871 (434), Brasileirão A 0.8458 (428), Série B 0.8363 (446), Uruguay 0.8506
(348), Equador 0.8235 (374). Maturidade rotulada — **não é validação automática**.

## 25. MELHORES RESULTADOS (sem validação automática)

- **gols ENTRAR**: 0.9452 em 2.918 (MADURA, Brier 0.052) — melhor calibração geral.
- **Leagues Cup ENTRAR**: 0.9875 em 80 (MADURA) — maior taxa por liga.
- **Allsvenskan/MLS/Primeira Liga**: 0.94+ em amostras maduras.
- Mesmo as melhores **não são promovidas a validadas** por este backtest: promoção
  exige validação estatística formal + odds reais (ROI) + ausência de
  superconfiança — pendente.

## 26. PIORES RESULTADOS

- **cartões ENTRAR**: 0.296 (total) por 67% de NÃO AVALIÁVEL estrutural — liquidação
  inviável na amostra cacheada.
- **Bundesliga**: 0.75 em 4 (INSUFICIENTE).
- **Equador/Série B/Uruguay**: 0.82–0.85 em amostras maduras — abaixo da média,
  sugerem calibração mais fraca dessas ligas (não invalidam o motor; sinalizam
  onde investigar).

## 27. REGRAS PARA INVESTIGAÇÃO FUTURA

- Superconfiança ~4 p.p. na faixa 90–100% (observado 88.9% vs predito ~93%) —
  investigar se Poisson 90min superestima caudas em ligas específicas.
- Missing estrutural de cartões finais — viabilizar coleta antes de validar cartões.
- Ligas de menor taxa (Equador/Série B/Uruguay) — investigar benchmark vs histórico.
- LIVE_HT — executar baseline quando houver auditoria de frescor reproduzível.
- PRESSÃO 5/10/15 — coleta prospectiva (FASE O).

## 28. ARQUIVOS CRIADOS/ALTERADOS

Criados: `src/backtest.py`, `tests/test_backtest.py`,
`docs/ETAPA_5_BACKTEST.md`, `docs/etapa5_baseline_panorama.json`,
`docs/etapa5_baseline_run.log`, `data/backtest.db` (gitignored — mutável).
**Nenhum arquivo de regra do motor foi alterado** (FASE G). `src/cobertura.py`,
`src/prejogo_opportunity.py`, `src/resultado.py`, `src/cartoes.py`, `src/settlement.py`,
`src/handicap.py`, `src/match_stats.py`, `src/live_opportunity.py` — apenas lidos.

## 29. TESTES NOVOS (FASE P)

`tests/test_backtest.py` — 27 testes (sintetizam um cache em `tmp_path`, sem rede):
anti-lookahead (AS_OF em histórico/benchmark/previsão), None≠zero, mercado
bloqueado excluído, liga INVIÁVEL/PARCIAL excluída, odd real permite edge/EV, sem
odd impede ROI, extrator AH/1X2, settlement GANHA/PERDIDA/DEVOLVIDA, meia
vitória/derrota, DNB, AH quarter split, reprodutível, regras congeladas, RESULTADO
experimental, PRESSÃO não-avaliável, amostra insuficiente não validada, mercados
separados, ledger operacional intocado, armazenamento próprio, execução repetida
idêntica, suite anterior intacta, AS_OF em toda previsão, sem otimização (máx 2/jogo).

## 30. RESULTADO DA SUÍTE (GATE DE REGRESSÃO)

**449 passed, 18 skipped, 0 failed** (422 anteriores + 27 novos). Nenhum teste
antigo removido ou enfraquecido (`test_suite_anterior_permancece_intacta`).

## 31. HASH DO COMMIT

A ser registrado ao final: commit "etapa 5: implementar backtest dedicado e
baseline" na branch `etapa-5-backtest`. (Hash preenchido no commit.)

## 32. GIT STATUS FINAL

Branch `etapa-5-backtest`. Arquivos novos: `src/backtest.py`,
`tests/test_backtest.py`, `docs/ETAPA_5_BACKTEST.md`,
`docs/etapa5_baseline_panorama.json`, `docs/etapa5_baseline_run.log`.
`data/backtest.db` gitignored (mutável). **Sem merge em main, sem push, sem remote.**

## 33. RISCOS

- **Sem ROI real**: 0 fixtures encerradas com odds pré-jogo no cache. Toda a
  evidência é de calibração (prob vs acerto), não de valor financeiro. Odd justa
  vs prob ≠ ROI realizado.
- **Superconfiança ~4 p.p.** na faixa 90–100% — pode superestimar EV real.
- **Missing estrutural de cartões** (67% NÃO AVALIÁVEL nas aprovadas) — cartões
  não são validáveis nesta amostra.
- **/odds/live** inconsistente (24/28 com valores na mineração vs 0/28 na auditoria)
  — point-in-time com `suspended:true`, não série histórica; não usado para ROI.
- **LIVE_HT parcial**: auditoria de frescor não reproduzível do cache.
- **Benchmark/histórico limitados ao cacheado**: times/ligas com poucos jogos
  cacheados geram histórico curto (contabilizado em `sem_stats_home/away`).
- **RESULTADO/PRESSÃO experimentais** — não promovidos.

## 34. PENDÊNCIAS

- Coletar odds pré-jogo prospectivamente para partidas que serão encerradas (ROI).
- Popular `live_snapshot_history` (coleta prospectiva) para validar PRESSÃO 5/10/15.
- Executar baseline LIVE_HT quando a auditoria de frescor for reproduzível.
- Investigar superconfiança 90–100% por liga/mercado.
- Viabilizar coleta de cartões finais (missing estrutural).

## 35. RECOMENDAÇÃO DA PRÓXIMA ETAPA

Iniciar **coleta prospectiva controlada (FASE O)**: para cada jogo elegível,
congelar no momento da decisão (fixture, timestamp, minuto, placar, stats, pressão
5/10/15, decisão do motor, mercado, linha, odd real externa se disponível, status
experimental, versão das regras) e anexar o resultado **após** o encerramento sem
alterar a previsão original. Isso fecha as três lacunas do backtest cacheado: odds
reais (ROI), snapshots temporais (pressão) e auditoria de frescor (LIVE). Sem
otimização de parâmetros até houver ROI realizado que justifique.

---

## RESPOSTAS FINAIS

**A. O motor mostrou evidência suficiente para continuar?**
**PARCIAL.** A calibração das aprovadas é forte em amostras maduras (gols 0.9452
em 2.918, escanteios 0.8925 em 893, Brier 0.05–0.09), o que sustenta continuar em
modo observação. **Mas** não há ROI real (0 odds em encerrados), há superconfiança
~4 p.p. na faixa 90–100%, cartões com missing estrutural e RESULTADO/PRESSÃO
experimentais. Evidência de calibração é suficiente para continuar observando;
evidência de valor financeiro ainda não existe.

**B. A pressão 5/10/15 já pode ser validada?**
**NÃO.** Sem `live_snapshot_history` populada, não há snapshots temporais
históricos. Nenhuma taxa de acerto é apresentada. Requer coleta prospectiva.

**C. Alguma regra para desativar imediatamente?**
**NÃO.** Nenhuma regra mostra evidência de dano nesta amostra que justifique
desativar agora. Cartões têm missing estrutural (não regra ruim — dado ausente);
as de menor taxa (Equador/Série B/Uruguay) ficam EM OBSERVAÇÃO, não desativadas.
RESULTADO/PRESSÃO já são experimentais por marcador. Nada é alterado sem ROI
realizado que comprove.

**D. Seguro iniciar futura otimização?**
**NÃO.** Sem ROI realizado (0 odds em encerrados) e com superconfiança observada,
otimizar parâmetros agora seria calibrar contra acerto sem saber se há valor
financeiro — risco de overfitting a um proxy. A otimização só é segura após
coleta prospectiva com odds reais produzindo ROI realizado e após investigar a
superconfiança. Até lá: regras congeladas, baseline = referência.

> PARE. Nenhum threshold alterado. Nenhuma regra promovida a validada. MCP não
> tocado. Sem push. Sem merge em main.