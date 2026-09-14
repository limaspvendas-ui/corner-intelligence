# ETAPA 5F-D — INTEGRAÇÃO CONTROLADA E AUDITORIA REAL MULTI-FONTE

**MODO SEGURO — SEM ALTERAR O MOTOR DE APOSTAS.**

Esta etapa implementa a camada de **INTEGRAÇÃO/COLETA/AUDITORIA** multi-fonte,
mantida **completamente separada** do motor de aposta:

```
FONTE -> COLETA -> NORMALIZACAO -> AUDITORIA   (esta camada)
MOTOR -> CALCULO -> APROVACAO -> SETTLEMENT     (intacto, congelado)
```

Nenhum fallback automático. Nenhuma fonte vencedora no motor. Nenhuma regra
alterada. Nenhum início de Etapa 6. Nenhum merge. Nenhum push.

---

## Estado de referência confirmado (seção 1)

- Branch de origem: `etapa-5fc-fundacao-multiprovider` (HEAD `4387c40`).
- Suíte inicial: **565 passed / 18 skipped / 0 failed**.
- Banco `odds_snapshot_history`: **317.951 linhas**, `provider='api_football'`
  317.951, `provider_null=0`, `user_version=2`, hashs distintos 317.951,
  `integrity_check=ok` (hash V2 confirmado, fundação 5F-C intacta).
- `?? data/` (não versionado): `baseline_panorama.json` + `baseline_run.log`
  (artefatos operacionais do backtest Etapa 5, NÃO são segredos). Todos os
  `.db`/backups corretamente ignorados. Não silenciados.

## Branch (seção 2)

- Criada: `etapa-5fd-auditoria-multifonte` (de `etapa-5fc-fundacao-multiprovider`).

## GATE de segurança (seção 3)

- Variável de gate: `MULTISOURCE_CREDENTIALS_ROTATED` (o código **NUNCA** a
  cria automaticamente).
- Estado confirmado: **NÃO definida (=0) → GATE FECHADO**.
- `.env`: somente `API_KEY` definida (nomes, valores nunca exibidos).
- Credenciais dos novos providers: **nenhuma configurada**.
- Consequência (regra aplicada): implementar adapters + rodar testes locais
  + permitir auditoria StatsBomb pública + usar cache existente da
  API-Football; **NÃO fazer chamadas autenticadas**; informar:
  **"CHAMADAS REAIS BLOQUEADAS POR SEGURANÇA"**.
- Resultado: **0 chamadas autenticadas executadas** nesta etapa.

## Providers canônicos (seção 4)

| Provider canonico | Credencial env | Tipo | Papel | Estado |
|---|---|---|---|---|
| `api_football` | `API_KEY` | odds+fatos | primário histórico | PRONTO (cache) |
| `the_odds_api` | `THE_ODDS_API_KEY` | odds | odds externas | NÃO PRONTO |
| `five_dollar_football` | `FIVE_DOLLAR_FOOTBALL_API_KEY` | odds | odds externas | NÃO PRONTO |
| `sportmonks` | `SPORTMONKS_API_TOKEN` | fatos | stats/eventos | NÃO PRONTO |
| `apifootball_com` | `APIFOOTBALL_COM_API_KEY` | fatos | eventos | NÃO PRONTO |
| `football_data_org` | `FOOTBALL_DATA_API_KEY` | fatos | identidade/fixture | NÃO PRONTO |
| `statsbomb_open` | (sem credencial) | fatos | histórico público | PARCIAL |

Nenhum provider declarado aprovado antes dos testes. `apifootball_com` é
**distinto** de `api_football` (não confundir APIFootball.com com API-Sports).

## Sem inventar endpoint (seção 5)

- `TheOddsAPIProvider`: endpoints **documentados** (the-odds-api.com v4:
  `/sports`, `/sports/{key}/odds`, `/sports/{key}/scores`, auth `apiKey`).
  OBSERVADO na resposta: **A CONFIRMAR** (nenhuma chamada real nesta etapa).
- `FiveDollarFootballProvider`: endpoints **A CONFIRMAR** — nenhum endpoint
  inventado; `fetch_odds` levanta `EndpointNotConfirmed`.
- `SportmonksProvider`: base **documentada** (`api.sportmonks.com/v3`,
  `api_token`, `/fixtures`, `/fixtures/{id}/statistics`). OBSERVADO:
  A CONFIRMAR.
- `APIFootballComProvider`: endpoints **A CONFIRMAR** (documentação menciona
  `?action=get_*` + `APIkey=`); sem chamada real nesta etapa.
- `FootballDataOrgProvider`: **documentado** (`api.football-data.org/v4`,
  header `X-Auth-Token`, `/competitions`, `/matches/{id}`). OBSERVADO:
  A CONFIRMAR.
- `StatsBombOpenProvider`: **documentado** (GitHub `statsbomb/open-data`,
  `competitions.json`, `matches/...`, `events/{id}.json`, `lineups/...`,
  `three-sixty/...`). Sem auth. Sem quota paga.

Sem documentação/resposta que comprove → **A CONFIRMAR** (nunca inventado).

## Arquitetura de adapters (seção 6)

- **Reutilizada** a abstração `OddsProvider` (Protocol) da 5F-C (`src/odds_coleta.py`).
- Criada abstração **`FactProvider`** (Protocol) para fatos
  (stats/eventos/placar/identidade), com `provider_name` explícito e operações
  opcionais — método não suportado levanta `NotImplementedError` ⇒ status
  AUSENTE (não obriga provider a implementar o que sua API não oferece).
- Adapters de odds: `TheOddsAPIProvider`, `FiveDollarFootballProvider`.
- Adapters de fatos: `SportmonksProvider`, `APIFootballComProvider`,
  `FootballDataOrgProvider`, `StatsBombOpenProvider`.
- `_checar_gate()` + `_cred_configurada()`: nunca imprimem valor, prefixo,
  sufixo, comprimento ou hash da chave.
- Exceções: `CredentialsBlocked`, `EndpointNotConfirmed`, `RateLimitHit`
  (429), `QuotaExhausted` (402).

## Adapters individuais (seções 7–12)

- **The Odds API** (seção 7): odds. Endpoints documentados. Hard limit 10.
  Sem gate ⇒ `CredentialsBlocked`.
- **5DollarFootballAPI** (seção 8): odds (goals/corners/cards/handicap/
  opening/closing/live). Endpoints **A CONFIRMAR** ⇒ `EndpointNotConfirmed`
  (não inventa).
- **Sportmonks** (seção 9): facts/stats/events. Plano limitado ⇒
  `LIMITADO_PELO_PLANO` (**não** FONTE RUIM). Hard limit 15.
- **APIFootball.com** (seção 10): facts/events. Provider canonico
  `apifootball_com` (≠ `api_football`). Hard limit 15.
- **football-data.org** (seção 11): fixtures/competição/identidade. **Não
  oferece** corners/cards/shots ⇒ `fetch_statistics`/`fetch_events` retornam
  `[]` (AUSENTE por design — fonte **não penalizada** por ausência legítima).
  Hard limit 10.
- **StatsBomb open** (seção 12): histórico público, **sem credencial**,
  **sem quota paga**, **não é live**, **não é odds**. `fetcher` injetável;
  sem fetcher ⇒ `CredentialsBlocked` (modo seguro). Smoke test `match_id
  9880` apenas quando fetcher/rede disponível.

## Proveniência normalizada (seção 13)

`NormalizedFact`: `provider`, `endpoint`, `retrieved_at`,
`fixture_provider_id`, `fixture_corner_id`, `home`, `away`, `kickoff`,
`competition`, `season`, `field`, `raw_value`, `normalized_value`, `status`,
`motivo`.

`NormalizedOdd`: `provider` (≠ bookmaker), `bookmaker`,
`fixture_provider_id`, `fixture_corner_id`, `market`, `submarket`, `side`,
`line`, `price`, `timestamp`, `coleta_tipo` (`pre_match`/`live`), `status`.

## null ≠ zero (seção 14)

- `null` / `missing` / campo ausente / endpoint indisponível / erro / plano
  sem cobertura **NUNCA** viram zero. Especialmente **Red Cards = null ≠ 0**.
- Status canônicos: `OK`, `MISSING`, `A_CONFIRMAR`, `BLOQUEADO_SEGURANCA`,
  `SEM_CREDENCIAL`, `LIMITADO_PELO_PLANO`, `NAO_DISPONIVEL`, `FALHA_TECNICA`,
  `NULL_EXPLICITO`.
- Testes confirmam: null permanece None; zero real permanece 0; ambos
  distintos.

## Reconciliação de fixtures (seção 15)

`reconciliar_fixture(canonical, candidate, provider)` →
`MATCHED` (≥0.85) / `AMBIGUOUS` (≥0.5) / `NOT_MATCHED` (<0.5), por
data/hora, competição, temporada, mandante, visitante, placar e status —
**não só nome textual**. Campos ausentes em ambas as fontes = neutros.
**Não cria identidade permanente no motor.**

## Amostra de cartões (seção 16)

- Estratificada (Red=null, Red=0, Red>0, sem statistics) — reuso da auditoria
  Etapa 5D: **Red Cards = null confirmado (limitação estrutural da fonte,
  207/207 NA)**; null ≠ zero.
- Sportmonks/APIFootball.com: **SEM_CREDENCIAL/BLOQUEADO**.
- **Nenhuma alteração de settlement.** Convenção `amarelo=1, vermelho=2`
  intacta.

## Amostra de escanteios (seção 17)

- Reuso da auditoria Etapa 5C: drift temporal **EM OBSERVAÇÃO**.
- API-Football: cache existente (VALIDADO). Sportmonks/APIFootball.com:
  BLOQUEADO. Conflitos/ausentes/zero reportados por status, sem inventar.

## Gols como controle de identidade (seção 18)

- HT/FT divergente ⇒ `AMBIGUOUS` (possível erro de mapeamento de fixture).
- API-Football VALIDADO; StatsBomb A CONFIRMAR (fetcher/rede pendente).

## Auditoria de odds (seção 19)

- API-Football: **VALIDADO** (317.951 snapshots, 7.053 live, 298.187 pre).
- The Odds API / 5Dollar: **BLOQUEADO**.
- Timestamps diferentes **não** são conflito direto de valor (auditados
  separadamente).

## Abertura/fechamento (seção 20)

- **Não inferir** OPENING_PROVIDER. API-Football `/odds` não marca
  abertura/fechamento de forma confiável (apenas `update_feed`).
- Política: `OPENING_PROVIDER` vs `PRIMEIRO_SNAPSHOT_LOCAL` — usar
  `PRIMEIRO_SNAPSHOT_LOCAL`, não inferir. Status `A_CONFIRMAR`.

## Auditoria live (seção 21)

- API-Football: `/odds/live` + `/fixtures/statistics`, TTL 60s.
- Ausente ⇒ AUSENTE (não zero). **Sem thresholds de pressão alterados**
  (motor intacto).

## Controle de quota (seção 22)

- Hard limits por provider (The Odds 10, Sportmonks 15, APIFootball.com 15,
  5Dollar 15, football-data 10, StatsBomb 30, API-Football sem limite novo).
- `429` ⇒ `RateLimitHit` interrompe o provider. `402` ⇒ `QuotaExhausted`.
- Máx 1 retry técnico (HARD_LIMIT respeitado; testado).

## Controle de custo (seção 23)

- Nenhum plano comprado/ativado. Nenhuma cobrança. Nenhuma assinatura
  alterada. Nenhum cartão. Nenhum endpoint pago conhecido usado.
- Em modo seguro: **BLOQUEADO POR CUSTO** implícito (zero chamadas pagas).

## Respostas brutas (seção 24)

- Local: `data/auditoria_multifonte/` — **gitignored** (`data/auditoria_multifonte/`).
- Tokens redactados. Nenhum payload/DB/backup versionado.
- Nesta etapa: diretório criado vazio (sem chamadas reais ⇒ sem payloads).

## Matriz de cobertura (seção 25)

`matriz_cobertura()` — FONTE × 28 colunas. Status:
`VALIDADO`/`PARCIAL`/`LIMITADO_PELO_PLANO`/`SEM_CREDENCIAL`/`NAO_DISPONIVEL`/
`FALHA_TECNICA`/`A_CONFIRMAR` (conjunto canônico, **sem** "FONTE RUIM").

Resumo (modo seguro):

| Provider | VALIDADO | demais |
|---|---|---|
| api_football | 20/28 | restante A_CONFIRMAR/LIMITADO_PELO_PLANO |
| statsbomb_open | 11/28 | restante NAO_DISPONIVEL |
| the_odds_api | 0 | SEM_CREDENCIAL |
| five_dollar_football | 0 | SEM_CREDENCIAL |
| sportmonks | 0 | SEM_CREDENCIAL |
| apifootball_com | 0 | SEM_CREDENCIAL |
| football_data_org | 0 | SEM_CREDENCIAL |

## Matriz de confiabilidade (seção 26)

`matriz_confiabilidade()` — CAMPO × (principal/fallback/evidência/coverage/
status). Informativa, **não imperativa** (sem fonte vencedora). Em modo
seguro a maioria é `PARCIAL`/`A_CONFIRMAR`/`NAO_AVALIAVEL` (Red Cards =
`NAO_AVALIAVEL` por limitação estrutural confirmada na 5D).

## Registro de conflito entre fontes (seção 27)

`ConflictRegistry` (append-only, **sem resolução automática**). Conflito =
valor divergente entre dois providers para o mesmo fato/fixture. **null vs
valor não é conflito** (null permanece null). Timestamps diferentes não são
conflito de valor. **Nenhuma fonte sobrescreve outra.** Nesta etapa: 0
conflitos registrados (apenas 1 fonte observada — api_football).

## Testes automatizados (seção 28)

`tests/test_auditoria_multifonte.py` — **41 testes** (superset dos 24
exigidos), sem chamadas de rede reais (fakes/injeção de `fetcher`):
nenhuma chave em logs; gate bloqueia chamadas; provider segue o dado;
bookmaker ≠ provider; null ≠ zero; missing ≠ zero; 429/402 interrompem;
HARD_LIMIT respeitado; MATCHED/AMBIGUOUS/NOT_MATCHED; conflito preservado;
fonte não sobrescreve outra; adapter inválido não contamina; payload
inválido não vira factual; odd sem preço não é inventada; timestamp
preservado; StatsBomb não é live/odds; football-data não penalizado;
plano limitado ≠ FONTE RUIM; **nenhuma regra do motor alterada**
(0.70/0.97/0.60 + amarelo=1/vermelho=2); **multifonte/auditoria não
importam módulos do motor**; histórico intacto.

## Motor congelado (seção 29)

`git diff --name-only 4387c40 -- <motor>` retornou **VAZIO** para:
`analysis.py`, `politica_aprovacao.py`, `policy.py`, `settlement.py`,
`calibration.py`, `backtest.py`, `prejogo_opportunity.py`, `odds.py`,
`identity.py`, `cache.py`, `config.py`, `api_client.py`,
`live_pressure.py`, `ao_vivo.py`, **`app.py`**, **`odds_coleta.py`**.
Nenhum arquivo do motor (nem da 5F-C) foi alterado. 5F-D adicionou SOMENTE
3 arquivos novos: `src/multifonte.py`, `src/auditoria_multifonte.py`,
`tests/test_auditoria_multifonte.py` (+ `.gitignore` para
`data/auditoria_multifonte/`).

## Banco de odds (seção 30)

- Os novos adapters usam a infraestrutura multi-provider da 5F-C
  (`provider`, hash V2, registry) **após testes locais**.
- **Nunca sobrescrevem** registros existentes.
- Contagem por-provider registrada (`por_provider` read-only).
- Dados factuais de auditoria **NÃO** são misturados na tabela de odds
  (`odds_snapshot_history` permanece somente odds).

## Coleta prospectiva (seção 31)

`prontidao_coleta()` por provider (PRONTO/PARCIAL/NÃO PRONTO), **sem daemon**:
`api_football`=PRONTO; `statsbomb_open`=PARCIAL; demais=NÃO_PRONTO
(credencial ausente + gate fechado). Nenhuma coleta contínua iniciada.

## ROI (seção 32)

- **NÃO** calculado com odds inexistentes (regra absoluta).
- A infraestrutura é suficiente para futuro EV/P&L/ROI por
  GOALS/CORNERS/CARDS/RESULTADO? **PARCIAL** — odds API-Football observadas,
  mas fontes externas de odds (The Odds API / 5Dollar) ainda
  **NÃO TESTADAS** (gate fechado). Resposta: **PARCIAL**.

## Artefatos (seção 33)

| Arquivo | Natureza |
|---|---|
| `src/multifonte.py` | gate, abstrações, adapters (7 providers), proveniência |
| `src/auditoria_multifonte.py` | reconciliação, conflitos, matrizes, runner MODO SEGURO |
| `tests/test_auditoria_multifonte.py` | 41 testes |
| `docs/ETAPA_5FD_AUDITORIA_MULTIFONTE.md` | este documento |
| `docs/etapa5fd_auditoria_multifonte.json` | artefato de auditoria (sem segredos) |
| `.gitignore` | `data/auditoria_multifonte/` (respostas brutas) |

Nenhum segredo, payload, DB ou backup versionado.

## Suíte completa (seção 34)

- **606 passed / 18 skipped / 0 failed** (~78s).
- Baseline 565 mantido; +41 testes novos. Nenhum teste antigo removido ou
  enfraquecido.

## Git (seção 35)

- branch: `etapa-5fd-auditoria-multifonte`.
- commit: `etapa 5fd: integrar adapters e auditar fontes externas`.
- **NÃO merge em main. NÃO push.**

---

## RELATÓRIO FINAL (seções A–M + 13 perguntas obrigatórias)

### A. Estado do gate
`MULTISOURCE_CREDENTIALS_ROTATED` = **NÃO definido (FECHADO)**. Única
credencial em `.env`: `API_KEY` (API-Football). Demais credenciais ausentes.

### B. Chamadas reais executadas
**0 chamadas autenticadas.** StatsBomb: 0 (fetcher não configurado — modo
seguro). API-Football: usado **somente o cache local existente** (read-only,
`mode=ro`), sem nova requisição de rede.

### C. Mensagem de bloqueio
**CHAMADAS REAIS BLOQUEADAS POR SEGURANÇA** — informada em logs, status e
relatório. O código **não** criou `MULTISOURCE_CREDENTIALS_ROTATED`
automaticamente.

### D. Providers adaptados
7 providers canônicos com adapter + `provider_name` explícito:
`api_football`, `the_odds_api`, `five_dollar_football`, `sportmonks`,
`apifootball_com`, `football_data_org`, `statsbomb_open`.

### E. Endpoints confirmados vs A CONFIRMAR
- **Documentados** (sem observação real): The Odds API, Sportmonks,
  football-data.org, StatsBomb.
- **A CONFIRMAR** (endpoints não inventados): 5DollarFootballAPI,
  APIFootball.com (documentação publica `?action=get_*` mas não confirmado
  por resposta real nesta etapa).

### F. null ≠ zero
Confirmado por testes e por auditoria 5D (Red Cards = null, 207/207 NA).
Null permanece null; zero real permanece zero; distintos.

### G. Reconciliação
MATCHED/AMBIGUOUS/NOT_MATCHED por 8 dimensões (não só nome). Sem identidade
permanente no motor.

### H. Conflitos
0 conflitos registrados (apenas 1 fonte observada). Registro append-only,
sem resolução automática.

### I. Matriz de cobertura
28 colunas × 7 fontes. Conjunto de status canônico (sem "FONTE RUIM").
api_football VALIDADO em 20/28; statsbomb_open em 11/28; demais
SEM_CREDENCIAL.

### J. Motor
**Intacto** — diff vazio em todos os 16 módulos do motor + `odds_coleta`.
Thresholds 0.70/0.97/0.60, `amarelo=1, vermelho=2`, Poisson, blend, H2H,
settlement, matriz de cobertura — todos intactos.

### K. Suíte
606/18/0. +41 testes novos (superset dos 24 exigidos).

### L. Prontidão de coleta
api_football=PRONTO; statsbomb_open=PARCIAL; demais=NÃO_PRONTO. Sem daemon.

### M. ROI
NÃO calculado. Infra suficiente para futuro ROI? **PARCIAL** (odds externas
ainda não testadas).

---

## 13 PERGUNTAS OBRIGATÓRIAS

1. **O GATE está aberto?** **NÃO** — `MULTISOURCE_CREDENTIALS_ROTATED`
   ausente. Nenhuma chamada autenticada.
2. **Chaves foram expostas?** **NÃO** — nenhum valor/prefixo/sufixo/
   comprimento/hash impresso em código, testes, docs ou JSON.
3. **Chamadas autenticadas executadas?** **0**.
4. **StatsBomb (público) foi auditado?** **PARCIAL** — adapter implementado
   com `fetcher` injetável; sem fetcher/rede, smoke test 9880 fica
   A CONFIRMAR (modo seguro).
5. **API-Football usou cache existente (sem nova requisição)?** **SIM** —
   leitura read-only `mode=ro` de `odds_snapshot_history` (317.951).
6. **null virou zero em algum lugar?** **NÃO** — testes + regra absoluta.
7. **Adapter de odds sem preço inventou preço?** **NÃO** — `price=None`
   permanece None.
8. **Fontes diferentes foram reconciliadas por nome textual apenas?**
   **NÃO** — 8 dimensões; ausentes em ambas = neutros.
9. **Conflito entre fontes foi resolvido automaticamente?** **NÃO** —
   registry append-only, preservado.
10. **Algum módulo do motor foi alterado?** **NÃO** — diff vazio.
11. **Fallback automático foi implementado?** **NÃO**.
12. **Fonte vencedora foi escolhida no motor?** **NÃO** — matriz
    informativa, não imperativa.
13. **Etapa 6 foi iniciada?** **NÃO** — permanece BLOQUEADA.

---

> PARE. NÃO implementar fallback automático. NÃO escolher fonte vencedora no
> motor. NÃO alterar regras. NÃO iniciar Etapa 6. NÃO mergear main. NÃO fazer
> push. NÃO iniciar coleta contínua.