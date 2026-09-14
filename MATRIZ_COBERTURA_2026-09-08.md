# MATRIZ DE COBERTURA POR COMPETIÇÃO × MERCADO × MODO — RELATÓRIO FINAL

**Data:** 08/09/2026
**Fonte da evidência:** `C:\CornerIntelligence\AUDITORIA_API_FOOTBALL_2026-09-08.md` (auditoria concluída, 42 chamadas reais + mineração de cache de 6.018 entradas)
**Escopo:** SOMENTE a camada de ELEGIBILIDADE/COBERTURA. Nada de probabilidade, threshold, fórmula, política de seleção ou ledger foi alterado.

---

## 1. ARQUIVOS

| Arquivo | Natureza | Conteúdo |
|---|---|---|
| `src/cobertura.py` | **NOVO** (módulo central) | Matriz estrutural (71 ligas da auditoria × 3 mercados × 2 modos), validação dinâmica PRE (histórico coletado) e LIVE (snapshot atual), `VeredictoCobertura`, filtro de avaliações, relatório `COMPETIÇÃO \| MODO \| MERCADO \| CLASSE \| STATUS \| MOTIVO` |
| `src/live_opportunity.py` | Modificado | Portão de cobertura no scan LIVE (etapas 2 e 4, ANTES do deep dive) + `separar_aprovadas_por_cobertura` (classe C sai das aprovadas, vira observação sem registro) |
| `src/live_opportunity_report.py` | Modificado | Seções `[COBERTURA]` no relatório da varredura e do candidato |
| `src/prejogo_opportunity.py` | Modificado | Filtro de cobertura ANTES de `aprovar_pregame`; linhas de mercado bloqueado nunca são avaliadas/aprovadas/registradas |
| `src/prejogo_opportunity_report.py` | Modificado | Matriz de cobertura + contagem de linhas bloqueadas no relatório |
| `src/app.py` | Modificado (`cmd_prejogoop --politica`) | Exibe a matriz e restringe a seleção `selecionar_melhor` às linhas com status PERMITIDO; reprova o jogo se nenhum mercado tem cobertura plena |
| `tests/test_cobertura.py` | **NOVO** | Os 13 testes especificados (ETAPA 12) + 2 testes de estrutura/relatório |
| `tests/test_live_opportunity.py` | Modificado | Ajustado ao novo comportamento por-mercado (jogo sem corners segue para gols; strings do relatório) |

**Não alterados:** `src/policy.py`, `src/match_stats.py`, `src/live.py`, `src/benchmarks.py`, `src/gols.py`, `src/escanteios.py`, `src/cartoes.py`, `src/resultado.py`, `src/registry.py` (ledger), e todos os motores de probabilidade/confiança.

---

## 2. ESTRUTURA DA MATRIZ (ETAPA 1)

A célula é **(COMPETIÇÃO, MODO, MERCADO)** — nunca uma regra única por competição. Cada célula tem:

1. **Classe estrutural** (da auditoria): A (≥95% entrega de statistics, 31 ligas) / B (100% em amostra pequena, 15 ligas) / C (~50%, 4 ligas) / E (0 statistics, 21 ligas). Liga fora da auditoria → **C** (observação, nunca permissão inventada).
2. **Derivação por mercado/modo** (ETAPA 4): a classe base não se aplica igual aos três mercados — ex. Série D tem placares completos, então GOALS PRE = B, mas CORNERS = E e CARDS = D.
3. **Validação dinâmica do fixture** (ETAPA 6): A/B são re-validados contra os dados reais coletados; classe C permanece observação (nunca sobe para permitido); D/E bloqueiam sempre.

**REGRA ABSOLUTA respeitada em todo o código: DADO AUSENTE ≠ ZERO.** Ausência permanece `None` no detalhe e bloqueia com "DADO INSUFICIENTE"; nenhuma média/contagem zero é inventada.

### Totais (71 ligas mapeadas, `resumo_matriz()`)

| MODO | MERCADO | A | B | C | D | E |
|---|---|---|---|---|---|---|
| PRE_GAME | GOALS | 31 | 40 | 0 | 0 | 0 |
| PRE_GAME | CORNERS | 31 | 15 | 4 | 0 | 21 |
| PRE_GAME | CARDS | 31 | 15 | 4 | 21 | 0 |
| LIVE | GOALS | 31 | 15 | 25 | 0 | 0 |
| LIVE | CORNERS | 31 | 15 | 4 | 0 | 21 |
| LIVE | CARDS | 31 | 15 | 4 | 0 | 21 |

(PRE GOALS tem 40 em B porque toda liga com placares completos — inclusive classe E em escanteios — é avaliável pelo histórico de placares, exatamente como exige a ETAPA 4.)

---

## 3. WHITELISTS — COMPETIÇÕES PERMITIDAS PARA ANÁLISE/RECOMENDAÇÃO (classe estrutural A/B)

O status final PERMITIDO ainda exige a validação dinâmica do fixture (item 5). Whitelist estrutural:

**Classe A (31 ligas, em GOALS, CORNERS e CARDS, PRE e LIVE):**
2 UEFA Champions League · 3 UEFA Europa League · 848 UEFA Conference League · 11 Serie B (IT) · 13 Ligue 2 · 39 Premier League · 61 Ligue 1 · 71 Brasileirão Série A · 72 Série B · 73 Série C · 78 Bundesliga · 88 Eredivisie · 89 League Two · 94 Primeira Liga · 106 MLS · 113 Liga MX · 128 Scottish Premiership · 135 Serie A (IT) · 140 La Liga · 144 Jupiler Pro League · 203 Championship · 204 League One · 239 Ekstraklasa · 242 Swiss Super League · 252 Áustria Bundesliga · 262 Süper Lig · 281 Brasileirão Fem · 307 Saudi Pro League · 475 Série C (Brasileirão Assaí) · 624 Copa do Nordeste · 772 2. Bundesliga

**Classe B (15 ligas, CORNERS e CARDS PRE e LIVE; GOALS idem):**
16 Championship 22/23 · 41 Ligue 2 22/23 · 62 Ligue 1 22/23 · 95 Bolívia Apertura · 105 Escócia Premiership · 114 Bundesliga 2 · 119 Escócia Championship · 136 Venezuela Primera B · 137 Colômbia Primera A · 141 J1 League · 172 Argentina Primera Div · 197 Noruega Eliteserien · 233 Serie C (IT) · 344 Argentina Primera Nacional · 383 Dinamarca Superligaen

**PRE GOALS classe B adicionais (placares completos sem statistics):**
75 Copa do Brasil · 76 Série D · 129 Carioca · 130 Paulista · 131 Copa do NE 23 · 132 Carioca 23 · 134 Copa do Imperador · 138 Copa Libertadores · 205 Copa do Brasil Fem · 219 Brasileirão Fem · 241 Copa Verde · 290 CONMEBOL Sudamericana · 477 Euro Fem · 479 Canadian CPL · 604 UEFA U19 · 606 Libertadores Fem · 612 Gold Cup · 627 Copa América Fem · 629 Olimpíadas Fem · 667 Copa América · 673 Eliminatórias Ásia · 887 Libertadores Fem 23 · 943 Copa do Mundo Fem · 45 Egito Premier (C em corners/cartões) · 82 Arábia 23/24 (C em corners/cartões)

---

## 4. BLACKLIST — COMBINAÇÕES BLOQUEADAS (classe D/E estrutural; dado dinâmico não derruba A/B sem motivo)

**CORNERS PRE + LIVE — classe E (21):** 75 Copa do Brasil · 76 Série D · 129 Carioca · 130 Paulista · 131 Copa do NE 23 · 132 Carioca 23 · 138 Copa Libertadores · 205 Copa do Brasil Fem · 219 Brasileirão Fem · 241 Copa Verde · 290 CONMEBOL Sudamericana · 477 Euro Fem · 604 UEFA U19 · 606 Libertadores Fem · 612 Gold Cup · 627 Copa América Fem · 629 Olimpíadas Fem · 667 Copa América · 673 Eliminatórias Ásia · 887 Libertadores Fem 23 · 943 Copa do Mundo Fem

**CARDS PRE — classe D (21):** as mesmas 21 ligas acima (cartões insuficientes na fonte, mas não "sem dados" a ponto de E).

**CARDS LIVE — classe E (21):** as mesmas 21 ligas.

**CORNERS/CARDS/GOALS LIVE classe C (observação, 25 em GOALS LIVE):** Série D e as 21 ligas acima ficam C em GOALS LIVE (placar/eventos existem) — analisáveis em observação, **nunca recomendação automática**.

**Classe C (~50% de entrega) em CORNERS e CARDS:** 45 Egito Premier · 82 Arábia 23/24 · 134 Copa do Imperador · 479 Canadian CPL — observação, sem recomendação automática.

**Regras dinâmicas que bloqueiam qualquer competição (inclusive A/B), por fixture:**
- PRE: nenhum jogo do histórico coletado com o dado do mercado (GOALS: placar pro/contra; CORNERS: Corner Kicks; CARDS: amarelos+vermelhos completos) → BLOQUEADO "DADO INSUFICIENTE".
- LIVE CORNERS: Corner Kicks ausentes ou presentes para UM só dos lados → BLOQUEADO (a matriz exige os DOIS lados; ausência nunca vira zero).
- LIVE CARDS: qualquer um dos 4 valores (amarelos/vermelhos casa/fora) ausente → BLOQUEADO.
- LIVE GOALS: status fora de {1H, HT, 2H} ou minuto ausente ou placar ausente ou (sem estatísticas E sem eventos) → BLOQUEADO; essenciais presentes mas sem estatísticas de apoio → OBSERVAÇÃO.

**Nota honesta (Série D GOALS PRE):** classe estrutural B (placares completos confirmados na auditoria), porém a coleta atual (`fetch_team_history`) só retorna jogos COM estatística de escanteios — logo, em runtime, o histórico de Série D chega vazio e o veredicto cai para BLOQUEADO "DADO INSUFICIENTE". A camada de cobertura não alterou a coleta (fora do escopo); o veredicto reporta exatamente o que a amostra sustenta.

---

## 5. EXEMPLOS MISTOS (a matriz nunca bloqueia uma competição inteira)

- **Série D (76):** GOALS PRE = B (avaliar pelo histórico de placares) · CORNERS PRE = E (BLOQUEAR) · CARDS PRE = D (BLOQUEAR) · GOALS LIVE = C (observação, conforme placar/events) · CORNERS LIVE = E (BLOQUEAR) · CARDS LIVE = E (BLOQUEAR). Nenhuma regra "Série D bloqueada para tudo".
- **Copa do Brasil (75):** idem Série D — gols pre-jogo permitidos, resto bloqueado/observação.
- **Jogo com statistics live sem Corner Kicks (ex.: fixture 202 do mundo falso — finalizações/posse sem escanteios):** o jogo NÃO é descartado inteiro; GOLS segue para análise e aprovação; nenhuma linha de escanteios é avaliada, aprovada ou registrada.
- **Liga classe A (ex.: 39 Premier League) com fixture sem o dado:** classe estrutural não basta — o mercado cai para BLOQUEADO "DADO INSUFICIENTE" naquele fixture.
- **Liga desconhecida (fora da auditoria):** classe C em tudo — observação, nunca permissão (cobertura nunca é inventada) nem recomendação automática; se a amostra coletada não tem nenhum jogo com o dado, o mercado bloqueia por DADO INSUFICIENTE.

---

## 6. FLUXOS (ETAPAS 9, 10 e 11 — filtro ANTES do deep dive caro)

**PRÉ-JOGO:** matriz estrutural → validação dinâmica do histórico → linhas BLOQUEADAS saem ANTES de `aprovar_pregame` (nunca avaliadas/aprovadas/registradas) → só as restantes seguem para probabilidade/confiança (inalteradas) → no `--politica`, somente linhas com status PERMITIDO concorrem em `selecionar_melhor`.

**LIVE:** etapa 2 do scan descarta o jogo somente se NENHUM mercado tem dados suficientes na leitura (jogo com gols mas sem corners segue) → etapa 4 re-avalia após o refresh e restringe as famílias passadas ao deep dive (`mercados=familias`) → aprovadas de mercado em OBSERVAÇÃO (classe C) são separadas para observação (`aprovada=False`, rejeição explícita) e **nunca registradas**.

---

## 7. TESTES (ETAPA 12)

**Suíte completa: 327 passed · 18 skipped · 0 failed** (`python -m pytest tests/ -q`).
Os 18 skips são os testes pré-existentes de API real em `tests/test_identity_teams.py` (gated por `CORNER_LIVE_TESTS=1`) — nada relacionado à cobertura.

Os 13 testes especificados (`tests/test_cobertura.py`) — todos passando:

1. `test_serie_d_gols_pre_permitido_com_historico_de_placar` — Série D GOALS PRE com placares na amostra = PERMITIDO (classe B).
2. `test_serie_d_corners_pre_bloqueado_sem_corner_kicks` — Série D CORNERS PRE bloqueado (classe E, ausência não vira zero).
3. `test_serie_d_corners_live_bloqueado` — Série D CORNERS LIVE bloqueado, mesmo se a fonte entregasse corners agora (classe E estrutural não é superada por dado).
4. `test_serie_d_cards_live_bloqueado` — Série D CARDS LIVE bloqueado.
5. `test_ucl_classe_a_passa_para_analise_com_dados_do_fixture` (+ `test_liga_a_sem_dados_do_fixture_e_bloqueada`) — classe A exige dados do fixture: com dados = PERMITIDO nos 3 mercados; sem o dado = BLOQUEADO.
6. `test_liga_a_sem_corners_live_nao_gera_aposta_de_corners` — statistics live sem Corner Kicks: jogo segue para gols, nenhuma aposta de corners.
7. `test_historico_com_gols_sem_corners_permite_gols_bloqueia_corners` — gols permitido, corners bloqueado, no mesmo fixture.
8. `test_dado_ausente_nunca_vira_zero` — detalhes permanecem `None` (nunca 0), "0 de 0" no motivo, liga desconhecida nunca vira permissão.
9. `test_classe_c_gera_somente_observacao` — aprovada de mercado C sai das aprovadas, vira observação sem registro.
10. `test_classes_d_e_nunca_geram_recomendacao` — D/E saem do fluxo antes de qualquer aprovação, pre e live.
11. `test_fluxo_prejogo_mantem_probabilidades` — probabilidades/confiança idênticas com e sem a camada de cobertura.
12. `test_fluxo_live_mantem_probabilidades` — idem no fluxo live (incluindo filtro de mercados preservado).
13. `test_ledger_permanece_inalterado` — `src/cobertura.py` não importa `registry`/`RegistroRecomendacoes`; linhas bloqueadas são filtradas antes de qualquer aprovação/registro.

Extras: `test_prejogo_politica_somente_permitidas_concorrem`, `test_matriz_estrutura_e_relatorio` (formato do relatório e classes da Série D por mercado/modo).

---

## 8. CONFIRMAÇÕES FINAIS

- **Probabilidades: inalteradas** (nenhum motor tocado; teste 11 e 12 provam identidade objeto a objeto).
- **Confiança: inalterada.**
- **Benchmarks: inalterados.**
- **Thresholds: inalterados** (nenhum novo limiar de amostra; a suficiência continua julgada pela lógica de confiança/auditoria já existente — a matriz só exige que exista ao menos um jogo com o dado do mercado, o que é bloqueio, não calibração).
- **Fórmulas dos mercados: inalteradas.**
- **Política de seleção operacional: inalterada** (`selecionar_melhor` intocado; versões "prejogo-politica-3.2-observacao" e "prejogo-op-1.0-observacao" mantidas).
- **Ledger: inalterado e intocado** (`src/registry.py` fora da dependência da camada; teste 13).
- **Nenhuma recomendação registrada** por esta camada; classe C vira somente observação (exibição), classe D/E sai do fluxo.
- **NÃO implementado (conforme REGRA FINAL):** snapshots, pressão 5/10 min, contexto mata-mata, standings no modelo, injuries no modelo, notícias, odds live, novos mercados.