# ETAPA 5D — AUDITORIA CIRÚRGICA DO MERCADO DE CARTÕES

**OBJETIVO:** descobrir POR QUE 207 de 307 decisões ENTRAR de cartões ficaram
**NÃO AVALIÁVEL** no backtest da Etapa 5 (run `bt-20260913224716-PRE_GAME`:
91 GANHA / 9 PERDIDA / 207 NÃO AVALIÁVEL — 67% da amostra não liquidável),
**sem modificar nenhuma regra.** Auditoria SOMENTE LEITURA.

Módulo: `src/auditoria_cartoes.py` (read-only). Testes:
`tests/test_auditoria_cartoes.py` (24 testes). JSON:
`docs/etapa5d_auditoria_cartoes.json`.

> Nenhum threshold, fórmula, Poisson, blend, política, settlement, matriz de
> cobertura, previsão antiga ou dado foi alterado. Nenhuma correção executada.
> Nenhuma liquidação realizada. Nenhum fixture re-coletado.

---

## 1. Estado inicial

- branch: `etapa-5d-auditoria-cartoes` (criada a partir de `main` em `8707ae1`).
- HEAD inicial: `8707ae16ebea171f8b3a8ca987efe7f2ec88ea95`.
- suíte inicial: 469 passed / 18 skipped / 0 failed.
- CORNERS: EM OBSERVAÇÃO (Etapa 5C) — não tocada.

## 2. Total investigado

207 previsões ENTRAR cartões com resultado NÃO AVALIÁVEL, em **204 fixtures
distintos** (5 fixtures com 2 ENTRAR; 3 deles entre os NA → 207 = 204 + 3
extras). Run auditada: `bt-20260913224716-PRE_GAME` (3825 fixtures,
288362 previsões, backtest-1.0).

## 3. Causa dos 207 NÃO AVALIÁVEL

| categoria (FASE B) | fixtures | causa (FASE I) |
|---|---|---|
| **B — amarelos disponíveis, vermelhos ausentes** (Red Cards type presente, value `null`) | **195** | **1 — API sem dado** (Red Cards = null na fonte) |
| D — 4 tipos presentes, todos valores `null` | 5 | 1 — API sem dado |
| G — resposta `/fixtures/statistics` vazia `[]` | 3 | 1 — API sem dado |
| C — vermelhos disponíveis, amarelos ausentes | 1 | 8 — inconsistência da API |
| **total** | **204** | — |

**Causa principal: 206/207 (99,5%) são "API sem dado"** — o endpoint
`/fixtures/statistics` retorna **Red Cards com valor `null`** (tipo presente,
valor ausente) para os fixtures. 1 caso é inconsistência pontual (amarelos
null com vermelhos presentes). **Nenhum dos 207 é causado por parser,
settlement, cache prematuro ou bug.**

## 4. Resultado de /fixtures/statistics

- **207/207 fixtures têm statistics em cache** (nenhum sem cache).
- **201/204 fixtures têm os 4 tipos de stat presentes** (Yellow + Red para
  casa e fora); 3 fixtures têm resposta vazia `[]`.
- Em **195 fixtures o valor de Red Cards é `null` para ambos os times**
  (Yellow presente com valor). O tipo "Red Cards" EXISTE no bloco, mas o
  `value` é `null` — não "0".
- Cada fixture red-null tem **exatamente 1 versão cacheada** (sem duplicata
  half/no-half que pudesse divergir).

## 5. Resultado de /fixtures/events

- Apenas **173 fixtures têm `/fixtures/events` em cache** (vs 7028 com
  statistics) — events são esparsos no cache.
- **0 dos 207 fixtures NA têm events em cache.** → events **não recuperam
  nenhum** dos 207 com dado factual existente.
- Eventos de cartão existem: tipo `"Card"` (235 ocorrências em 88 fixtures),
  com `detail` = `"Yellow Card"` (225) ou `"Red Card"` (10). A fonte events
  **diferencia** amarelo de vermelho, mas **não há variante "Second Yellow" /
  "Yellow-Red"** no cache — todos os cartões vermelhos (inclusive segundo
  amarelo) vêm como `"Red Card"`.
- Conclusão FASE D: events seriam uma fonte factual viável **se fossem
  coletados**, mas com o cache atual não há cobertura para os 207.

## 6. Zero vs None (CRÍTICO)

A API-Football **distingue** `null` (ausente) de `"0"` (zero verdadeiro). O
parser (`_to_int` / `_parse_side`) preserva ambos corretamente: `null → None`,
`"0" → 0`. **Nenhum zero virou None; nenhum None virou zero.**

Distribuição no cache inteiro (7028 fixtures, ~9896 blocos de cada tipo):

| stat | positive | `"0"` (zero) | `null` (ausente) |
|---|---|---|---|
| Yellow Cards | 8686 | 954 | 258 |
| **Red Cards** | 1071 | 1795 | **7030** |

**Red Cards é `null` em 7030/9896 (71%) das ocorrências** — a fonte
frequentemente não fornece o valor de vermelhos. A presença de 1795 `"0"`
prova que `null` ≠ zero: quando não houve vermelho, a API usa `"0"`; quando
não fornece o dado, usa `null`. A regra do motor (None → NÃO AVALIÁVEL, nunca
zero) está **correta e alinhada com a semântica da fonte**.

No universo NA: 424 raw `null`, 34 raw `"0"`, 0 string vazia.

## 7. Parser (FASE F)

Caminho auditado: `api_cache → _parse_side (match_stats.py) → MatchStats →
liquidar (settlement.py reusado por backtest.py) → resultado_final`.

- Nomes exatos dos tipos reconhecidos: `"Yellow Cards"`, `"Red Cards"` —
  iguais aos da fonte. Nenhum nome alternativo ("Yellow Card" singular etc.)
  aparece em statistics (só em events).
- Strings vs números: o valor vem como string ou int; `_to_int` normaliza.
- `0` preservado; `None` preservado; `""` → None.
- home/away mapeados por `team.id` (identidade ancorada no fixture).
- Condição de NÃO AVALIÁVEL: `match is None` ou qualquer dos 4 valores None.

**Resposta FASE F: NÃO há dado no cache que o parser não esteja
aproveitando.** Os 4 tipos de stat usados (Yellow/Red × home/away) são
exatamente os que a fonte fornece. O parser está correto.

## 8. Cache / momento da coleta (FASE G)

- `api_cache` tem `created_at` (timestamp). Para os 204 fixtures NA:
  - **0 coletados antes da partida** (cache prematuro descartado).
  - **204/204 coletados >2h após o kickoff** (mediana **905h ≈ 38 dias**
    pós-partida, mínimo 137,6h ≈ 5,7 dias).
- O cache **não foi precoce** — foi coletado bem após a consolidação final.
  Red Cards = `null` é gap da fonte, não de timing. Nenhuma versão
  posterior mais completa existe no cache (1 versão por fixture).

## 9. Settlement (FASE H)

Revalidação independente das **307** previsões ENTRAR (recomputar
GANHA/PERDIDA/DEVOLVIDA/NÃO AVALIÁVEL a partir de `linha` + `total_final`
armazenado, e recompute do total a partir do cache para os 100 liquidados):

- **0 / 307 mismatches** · **0 loss_mismatches** · settlement 100% correto.
- Recompute do total a partir do cache para os 100 liquidados: 0 divergências
  vs `total_final` armazenado.
- **Nenhuma perda liquidada incorretamente.** Settlement excluído como causa.

Ponto FASE H (dado desnecessário): em **95 dos 207 NA**, o resultado seria
matematicamente determinável **só com Yellow** (ver item 12) — o settlement
exige Red Cards mesmo quando ele não pode mudar o veredicto. Isso é uma
**escolha de regra conservadora**, não um bug (ver item 18).

## 10. Regra amarelo/vermelho (FASE E)

- Regra oficial: **amarelo = 1, vermelho = 2** (convenção `amarelo=1,
  vermelho=2` declarada na linha congelada; `_MARCA_CONVENCAO_CARTOES`).
- Total = amarelos + 2 × vermelhos (mesma do settlement e do baseline).
- Segundo amarelo / amarelo+vermelho / vermelho direto: a fonte
  `/fixtures/statistics` agrega em "Yellow Cards" e "Red Cards" — **não
  separa** segundo-amarelo de vermelho-direto. Um segundo amarelo é contado
  em "Yellow Cards" (o amarelo exibido) e em "Red Cards" (a expulsão),
  podendo somar 3 pontos (1 + 2) para um evento que poderia ser tratado
  como 2. **Esta é uma ambiguidade da fonte, não da regra**, e não é a causa
  dos 207 NA. Nenhum ajuste feito (regra congelada).
- A interpretação atual é **compatível** com os dados: a convenção é
  declarada e aplicada de forma idêntica em baseline, settlement e backtest.

## 11. Resultado por competição (FASE J)

| liga | ENTRAR | G | P | NA | %NA | causa principal |
|---|---|---|---|---|---|---|
| Liga Pro (Ecuador) | 75 | 27 | 2 | 46 | 61,3% | API sem dado |
| Serie A (Bra) | 50 | 7 | 0 | 43 | **86,0%** | API sem dado |
| Serie B (Bra) | 42 | 10 | 0 | 32 | 76,2% | API sem dado |
| Primera División | 57 | 25 | 4 | 28 | 49,1% | API sem dado |
| Liga Prof. Argentina | 32 | 8 | 1 | 23 | 71,9% | API sem dado |
| Primera A (Col) | 35 | 12 | 1 | 22 | 62,9% | API sem dado |
| Sudamericana | 9 | 1 | 0 | 8 | **88,9%** | API sem dado |
| Libertadores | 6 | 1 | 1 | 4 | 66,7% | API sem dado |
| MLS | 1 | 0 | 0 | 1 | 100% | API sem dado |

Deterioração ampla: **todas** as ligas têm %NA ≥ 49%. Serie A (Bra) e
Sudamericana passam de 86–89%. A causa é uniformemente "API sem dado" (Red
Cards null). Nenhuma liga tem bug próprio.

## 12. Casos recuperáveis (FASE K)

Dos 207 NÃO AVALIÁVEL:

| categoria | previsões | nota |
|---|---|---|
| **Recuperáveis com dado factual JÁ EXISTENTE** | **95** | Yellow total > linha → Over GANHA (Red irrelevant). Todas 95 seriam **GANHA**. |
| Recuperáveis via events (cache) | 0 | 0/207 têm events |
| Red decide (Yellow ≤ linha, Red ausente) | 103 | Red Cards determinaria o resultado; sem Red, não é liquidável |
| Sem dado de cartões (Yellow e Red null / stats vazia) | 9 | 5 all-null + 1 yellow-null + 3 stats `[]` |
| **total** | **207** | |

As 95 recuperáveis são todas **Over com Yellow total > linha**: como
`total = Y + 2R ≥ Y > linha`, o veredicto (GANHA) é matematicamente certo
independente de R. **Nenhuma cartão é inventado** — a recuperação decorre de
Red ser irrelevante para aquele veredicto específico.

## 13. Casos realmente sem dados

**9 previsões** (5 all-null + 1 yellow-null + 3 statistics vazia `[]`) em que
nem Yellow nem Red estão disponíveis na fonte. Continuam sem informação mesmo
após análise. Nenhum desses tem events em cache.

## 14. Casos que dependeriam de nova coleta

**103 previsões** (Red decide) + os **9 sem dado** = **112** dependeriam de
nova coleta para serem liquidadas. Options (não executadas):
- re-buscar `/fixtures/statistics` (pode retornar `null` novamente — gap da
  fonte, não de frescor; mediana 38 dias pós-partida já descarta cache
  precoce);
- coletar `/fixtures/events` (fonte factual alternativa com Yellow/Red por
  evento, mas hoje 0/207 têm events).

## 15. Simulação de auditoria dos recuperáveis (FASE L)

> **SIMULAÇÃO DE AUDITORIA — NÃO É RESULTADO OFICIAL.** Nenhuma previsão
> antiga foi alterada; o banco oficial não foi tocado.

| cenário | GANHA | PERDIDA | NÃO AVALIÁVEL |
|---|---|---|---|
| resultado atual (oficial) | 91 | 9 | 207 |
| hipotético se liquidados os 95 recuperáveis (só Yellow, Red irrelevant) | **186** | 9 | 112 |

Hit rate dos liquidados passaria de 91/100 = 91,0% para 186/195 = 95,4% —
**mas este número NÃO é válido estatisticamente**: os 95 recuperáveis são
exatamente os casos em que Yellow > linha (Over ganho), um viés de seleção
que maximiza GANHA. A simulação serve apenas para dimensionar o impacto de
uma eventual mudança de regra, **não para validar o mercado**.

## 16. Viés de disponibilidade

Há viés de disponibilidade duplo:

1. **Na amostra liquidada hoje**: os 100 liquidados requerem Red Cards não
   nulo na fonte. Como Red Cards = null em 71% do cache, os fixtures que
   liquidam são os da minoria com Red informado — possivelmente um subset
   não aleatório (ligas/seasons com melhor cobertura de vermelhos).
2. **Na simulação**: os 95 "recuperáveis" são por construção todos GANHA
   (Yellow > linha). Liquidá-los sem os 103 "Red-decide" superestima o hit
   rate.

Conclusão: **o hit de 91% (e o hipotético 95,4%) são viesados pela
disponibilidade seletiva de Red Cards na fonte** e não devem ser reportados
como taxa de acerto do mercado de cartões.

## 17. Existe bug?

**NÃO.**

- Parser: `null → None`, `"0" → 0` — correto, nenhum dado confundido.
- Settlement: 0/307 mismatches, 0 perdas erradas — 100% correto.
- Cache: coletado pós-partida (mediana 38 dias), não prematuro.
- Identidade: home/away ancorados por `team.id` do fixture.
- Nenhuma regra do motor alterada; nenhum dado inventado.

## 18. Existe correção segura?

**A CONFIRMAR (não executada).**

Há uma correção **matematicamente segura** identificada: relaxar o settlement
para liquidar como GANHA os Over com Yellow total > linha (Red irrelevante) —
95 casos. Isso não inventa cartão; apenas deixa de exigir um dado que não
muda o veredicto. **Mas é uma MUDANÇA DE REGRA**, não um bug fix:

- exige autorização explícita do operador (muda a regra "amarelos OU
  vermelhos ausentes => NÃO AVALIÁVEL");
- exige validação out-of-sample independente (o holdout que revelaria o
  efeito não pode ser o mesmo usado para calibrar);
- exige decisão sobre o simétrico Under (Under com Yellow > linha → PERDIDA
  determinável, mas liquidar como PERDIDA é mais delicado);
- não resolve os 112 casos sem Red.

**Nenhuma correção foi executada nesta etapa.** A outra via (coletar
`/fixtures/events` como fonte factual de Red/Yellow) é viável mas exige
nova coleta — fora do escopo de uma auditoria somente leitura.

## 19. Testes

`tests/test_auditoria_cartoes.py` — 24 testes:
- parsing de linha (Over/Under + valor); `0` vs `None` vs `""`;
- `_is_half_line` (.5 vs inteira);
- classificação FASE B (categorias A/B/C/D/E/G) com blocos simulados;
- determinabilidade FASE K/L (Over-GANHA, Under-PERDIDA, não-determinável,
  sem Yellow);
- auditoria reproduz contagens oficiais (307/91/9/207/204);
- auditoria **determinística** (duas execuções idênticas);
- auditoria **somente leitura** (DB: nenhuma linha inserida/removida;
  previsões oficiais idênticas antes/depois);
- zero ≠ None no universo NA e no cache global;
- events não inventam cartão (só "Yellow Card"/"Red Card"; 0/207 têm events);
- settlement oficial intacto (0 mismatches);
- cache coletado pós-partida (0 antes; mediana > 24h);
- simulação consistente (95 + 103 + 9 = 207; hipotético 186/9/112);
- regras do motor congeladas (convenção `amarelo=1, vermelho=2` intacta;
  contagem de previsões estável);
- JSON serializável.

**Suíte completa: 493 passed, 18 skipped, 0 failed** (469 anteriores + 24
novos).

## 20. Hash do commit

`e0845e16b3956c9784f8889cb944c302145a8f5d` (branch `etapa-5d-auditoria-cartoes`,
"etapa 5d: auditoria cirurgica dos dados de cartoes").

## 21. Branch

`etapa-5d-auditoria-cartoes` (criada a partir de `main` em `8707ae1`).
Arquivos novos: `src/auditoria_cartoes.py`, `tests/test_auditoria_cartoes.py`,
`docs/ETAPA_5D_AUDITORIA_CARTOES.md`, `docs/etapa5d_auditoria_cartoes.json`.
**Nenhum arquivo do motor alterado.** Sem push, sem merge em main.

## 22. Git status final

Apenas `data/` untracked (diretório de banco/cache, não relacionado). Nenhuma
alteração pendente no motor.

---

## RESPOSTAS OBRIGATÓRIAS

**CAUSA PRINCIPAL DOS 207 NÃO AVALIÁVEL:** **API** — o endpoint
`/fixtures/statistics` retorna **Red Cards com valor `null`** (tipo presente,
valor ausente) para 195/204 fixtures; 9 fixtures têm dados de cartões ainda
mais esparsos (all-null / amarelos null / statistics vazia). 71% de todas as
ocorrências de Red Cards no cache são `null`. Parser, settlement, cache e
regras estão corretos — a causa é a **fonte não fornecer o dado de cartões
vermelhos** (e, em minoria, amarelos). Componente secundário SETTLEMENT
(regra conservadora exige Red mesmo quando irrelevante — 95 casos) — não é
bug, é escolha de regra.

**EXISTE BUG?** **NÃO.** Parser preserva `null`≠`"0"`; settlement 0/307
mismatches; cache pós-partida; nenhum dado inventado; nenhuma regra alterada.

**QUANTOS DOS 207 SÃO RECUPERÁVEIS COM DADO FACTUAL JÁ EXISTENTE?** **95**
(todos GANHA — Over com Yellow total > linha, onde Red Cards é
matematicamente irrelevante). Via events: **0** (0/207 têm events em cache).

**EXISTE CORREÇÃO SEGURA?** **A CONFIRMAR.** Há uma correção
matematicamente segura (liquidar Over-GANHA quando Yellow > linha, sem
exigir Red) para 95 casos, **mas é mudança de regra** — exige autorização
explícita + validação out-of-sample independente. Não executada.

**CARTÕES CONTINUAM:** **NÃO AVALIÁVEL** — 67% da amostra (207/307) não pode
ser liquidada por gap da fonte (Red Cards = null). Os 100 liquidados têm
91% de hit, mas são viesados pela disponibilidade seletiva de Red Cards e
não validam o mercado. Antes de qualquer uso operacional: (a) coletar
`/fixtures/events` como fonte factual alternativa; (b) avaliar a mudança de
regra dos 95 recuperáveis em etapa dedicada com validação out-of-sample.

> PARE. Não executar correção. Não alterar regra. Não iniciar Etapa 6. Sem
> push, sem merge em main.