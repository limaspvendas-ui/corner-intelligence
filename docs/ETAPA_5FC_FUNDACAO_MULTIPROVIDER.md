# ETAPA 5F-C — FUNDAÇÃO MULTI-PROVIDER E PROVENIÊNCIA

**ALTERAÇÃO ESTRUTURAL CONTROLADA — NÃO altera o motor.**

Esta etapa prepara a infraestrutura do Corner Intelligence para trabalhar
futuramente com múltiplas fontes de dados e odds, preservando integralmente o
motor de decisão, as regras, os thresholds, o Poisson, os blends, o
settlement, a política de aprovação, as previsões históricas, os resultados de
backtest, a matriz de cobertura e o ledger histórico.

**NÃO integra ainda** The Odds API, Sportmonks, APIFootball.com,
5DollarFootballAPI, football-data.org ou StatsBomb. Cria APENAS a fundação
necessária para essas integrações futuras.

Módulo: `src/odds_coleta.py`. Testes: `tests/test_odds_coleta.py` (58 testes).
JSON: `docs/etapa5fc_fundacao_multiprovider.json`.

---

## 1. Estado inicial (auditado)

- branch de origem: `etapa-5f-coleta-odds-reais` (HEAD `1cc2d6e`).
- suíte inicial: **545 passed / 18 skipped / 0 failed**.
- tabela `odds_snapshot_history`: **317.951 linhas** (live 7.053, pre_match
  310.898), **sem coluna `provider`**.
- `snapshot_hash` (V1) = SHA-256 de (fixture_id, coleta_tipo, bookmaker,
  bet_name, bet_id, familia, subfamilia, lado, linha, value_feed,
  odd[round 6], suspended[int], status) — **sem provider**.
- provider histórico único: **API-Football / API-Sports** (confirmado por
  auditoria 5F-B: único client integrado, única BASE_URL em `src/config.py`).
- `snapshot_hash` **não tem** FK, referência externa, relatório, teste ou
  lógica que dependa do seu valor — usado apenas para dedup `UNIQUE` nesta
  tabela. Recomputar é seguro.

## 2. Backup obrigatório

- Backup criado: **SIM** — `data/corner_intelligence_backup_5fc_20260914.db`
  (204.836.864 bytes, igual ao original; gitignored por `data/*.db`).
- `PRAGMA integrity_check` do backup: **ok**.
- 317.951 linhas preservadas no backup.
- Backups anteriores não foram apagados.

## 3. Estratégia de hash V2

O hash V1 não diferencia provider. Se os registros legados conservassem o hash
V1 e as novas coletas API-Football começassem a gerar hash V2 (com provider), a
mesma cotação histórica poderia ser reinserida como duplicata. Para evitar
isso, a migração **recomputa os hashes legados para V2** (incluindo
`provider='api_football'`), em transação atômica.

**Hash V2** = SHA-256 de (`provider`, fixture_id, coleta_tipo, bookmaker,
bet_name, bet_id, familia, subfamilia, lado, linha, value_feed,
odd[round 6], suspended[int], status). `provider` entra **primeiro**
(a fonte precede os fatos). Função única: `_hash_identidade(...)` em
`src/odds_coleta.py`, reusada por `OddSnapshot.hash()` e pela migração
(single source of truth).

**Segurança do recompute (V1→V2):**
- Todos os hashes V1 armazenados são distintos (`UNIQUE`, confirmado:
  317.951 hashs distintos = total). Logo todas as identidades factuais são
  distintas, logo todos os hashes V2 serão distintos (V2 = V1 + 1 campo).
  Nenhuma violação de `UNIQUE` durante o `UPDATE` in-place.
- O recompute ocorre em transação explícita (`BEGIN`/`COMMIT`/`ROLLBACK`);
  falha => rollback desfaz TODOS os `UPDATE`s de hash; `user_version` não é
  incrementado, então a reinicialização tenta de novo.

**Propriedades:**
- (A) mesmo provider + mesmo snapshot factual => mesma hash V2 => dedup.
- (B) provider diferente + mesmo bookmaker/fixture/mercado/linha/odd => hash
  diferente => ambos preservados (sem colisão multi-fonte).
- (C) 1.90 → 1.89 => novo snapshot.
- (D) provider nunca é inferido silenciosamente em novas integrações (o
  adapter passa `provider_name` explicitamente; client legado sem
  `provider_name` é envolvido como `APIFootballOddsProvider` — o único
  provider histórico auditado, documentado, não silencioso).

## 4. Migração dos 317.951 registros existentes

- Coluna `provider` adicionada via
  `ALTER TABLE ... ADD COLUMN provider TEXT NOT NULL DEFAULT 'api_football'`
  (backfill atômico de todos os registros em uma instrução; `NOT NULL` impede
  provider `NULL` dali em diante).
- Todos os registros legados classificados como `provider='api_football'`
  (única origem comprovada pela auditoria 5F-B — não se usa "unknown"/"legacy").
- `snapshot_hash` recomputado para V2 em transação explícita.
- Marcador `PRAGMA user_version = 2` após `COMMIT` bem-sucedido.

**Resultado (verificado no banco real):**

| Verificação | Antes | Depois |
|---|---|---|
| total de linhas | 317.951 | 317.951 |
| hashs distintos | 317.951 | 317.951 |
| por_coleta_tipo | live 7053, pre_match 310898 | live 7053, pre_match 310898 |
| por_status | INVALID 1553, OK 39144, SUSPENDED 1514, UNMAPPED 275740 | idem |
| por_familia | UNMAPPED 278765, cartoes 1166, escanteios 8114, gols 13713, resultado 16193 | idem |
| e_pre_jogo=1 | 298187 | 298187 |
| provider NULL | (n/a) | 0 |
| por_provider | (n/a) | api_football 317.951 |
| user_version | 0 | 2 |
| integrity_check | ok | ok |

- Nenhuma linha perdida. Nenhuma linha duplicada. Nenhuma odd, timestamp,
  fixture ou linha histórica alterada. Apenas `snapshot_hash` (e a nova coluna
  `provider`) mudaram.

**Dedup pós-migração (validação crítica):** re-ingestão do `api_cache`
(gerando hashes V2) produziu **0 inseridos / 317.972 duplicados** — os hashes
V2 das entradas do cache casam exatamente com os hashes V2 computados para os
registros migrados. Sem duplicação, sem perda. A migração preserva a dedup.

## 5. Migração idempotente

- `PRAGMA user_version` (0 → 2) marca o estado da migração no cabeçalho do DB.
- `user_version >= 2` => migração é no-op imediato.
- Se `ALTER` já aplicado mas recompute falhou (crash antes de `user_version=2`):
  a reinicialização reprocessa (recompute determinístico = mesmos hashes V2).
- Executar `OddsSnapshotStore()` repetidamente não altera o banco (testado:
  estado idêntico após múltiplas inicializações).
- DB novo: `_SCHEMA` cria a tabela já com `provider`; migração roda recompute
  sobre 0 linhas e seta `user_version=2`.

## 6. Abstração de Provider

- `OddsProvider` (Protocol): interface com `provider_name: str` e `get(...)`.
- `APIFootballOddsProvider`: adapter do provider API-Football / API-Sports.
  Reusa `src/api_client.py` (não reescreve cliente estável), preserva chamadas,
  cache e comportamento existentes. `provider_name = "api_football"`.
- `resolve_provider(obj)`: resolve um objeto para `OddsProvider` com
  `provider_name` explícito. Provider com `provider_name` desconhecido no
  registry => `ValueError` explícito. Client legado (sem `provider_name`) =>
  envolvido em `APIFootballOddsProvider` (provider histórico auditado).

**PROVIDER ≠ BOOKMAKER.** Provider = API que forneceu os dados
(`api_football`, `the_odds_api`, ...). Bookmaker = casa de apostas
representada dentro do feed (`bet365`, ...). Conceitos distintos — nunca
confundir.

## 7. Registry / resolução de providers

- `_PROVIDER_REGISTRY = {"api_football": APIFootballOddsProvider}`.
- Nesta etapa, **somente** `api_football`. Nenhum adapter falso para APIs não
  implementadas. Nenhuma resposta mockada fingindo suporte.
- Providers futuros podem ser adicionados ao registry sem alterar o motor.

## 8. Compatibilidade com a API existente

- `coletar_fixture(client, store, fixture_id, ...)` continua aceitando um
  client legado (sem `provider_name`); `resolve_provider` o envolve como
  `APIFootballOddsProvider`. CLI `oddscoleta coleta` (que passa
  `args.client = APIFootballClient()`) funciona sem alteração.
- `ingerir_cache` passa `provider=PROVIDER_API_FOOTBALL` explicitamente (o
  `api_cache` é da API-Football).
- `_construir_snapshots` e `_unit` receberam `provider` como parâmetro (com
  default `api_football` para compatibilidade do caminho histórico).
- `OddSnapshot` ganhou campo `provider` (primeiro campo, sem default — força
  explicitação).
- `OddsSnapshotStore.status()` adicionou `por_provider` (aditivo).
- `format_status` exibe `por provider (FONTE)`.
- `src/app.py` **não foi alterado** nesta etapa (o +91 é da Etapa 5F).

## 9. Schema de proveniência

Cada snapshot informa: `provider`, `bookmaker`, `fixture_id`, `bet_name`,
`bet_id`, `familia`, `subfamilia`, `lado`, `linha`, `value_feed`, `odd`,
`suspended`, `update_feed`, `collected_at`, `fixture_date`, `e_pre_jogo`,
`status`, `motivo`, `snapshot_hash` (V2).

## 10. Conflito entre fontes

Nesta etapa não há reconciliação de valores de APIs diferentes. A estrutura
permite que no futuro coexistam, sem colisão e sem perda de proveniência:
```
api_football: fixture X, bet365, Over 2.5, 1.90
the_odds_api:  fixture X, bet365, Over 2.5, 1.90
```
(hasht V2 diferentes => dois registros distintos). Nenhum provider tem
autoridade automática sobre outro nesta etapa.

## 11. Não implementar fallback / não configurar chaves

- **NÃO** criado: prioridade entre providers, fallback automático, resolução
  automática de conflito, média entre fontes, escolha por maior odd, fusão de
  estatísticas. (Decidido após auditoria factual multi-fonte, etapa futura.)
- **NÃO** criada nem solicitada nenhuma credencial nova
  (`THE_ODDS_API_KEY`, `SPORTMONKS...`, `APIFOOTBALL...`, `5DOLLAR...`,
  `FOOTBALL_DATA...`). Nenhum nome de variável inventado. Somente API-Football
  existente continua funcional. **Nenhuma chamada externa** foi feita.
- **Nenhuma chave exibida.**

## 12. Testes (20 novos + 38 existentes = 58 no módulo)

`tests/test_odds_coleta.py` — 20 testes novos cobrindo: (1) banco antigo sem
provider migra; (2) legados recebem `api_football`; (3) qtd de linhas idem;
(4) nenhuma odd muda; (5) nenhum timestamp muda; (6) nenhuma fixture muda;
(7) nenhuma linha muda; (8) provider não pode ficar nulo (rejeitado);
(9) mesmo provider + mesmo snapshot = dedup; (10) provider diferente + mesmo
factual = dois registros; (11) mudança de odd = novo snapshot; (12) hash V2
inclui provider; (13) migração idempotente; (14) repetir init não altera
banco; (15) rollback preserva DB em falha simulada; (16) adapter API-Football
identifica provider; (17) registry resolve `api_football`; (18) provider
inexistente falha explícito; (19) coletor antigo (client legado) continua
funcionando; (20) nenhuma regra do motor importada/modificada.

## 13. Motor congelado (diff explícito)

`git diff --name-only 1cc2d6e -- <motor>` retornou **vazio** para:
`analysis.py`, `politica_aprovacao.py`, `policy.py`, `settlement.py`,
`calibration.py`, `backtest.py`, `prejogo_opportunity.py`, `odds.py`,
`identity.py`, `cache.py`, `config.py`, `api_client.py`, `live_pressure.py`,
`ao_vivo.py`, **`app.py`**. Nenhum arquivo do motor foi alterado. 5F-C tocou
**somente** `src/odds_coleta.py` (+276/-35) e `tests/test_odds_coleta.py`
(+352). Thresholds 0.70/0.97/0.60, `amarelo=1, vermelho=2`, Poisson, blend,
H2H, settlement, matriz de cobertura, política de universo — todos intactos
(testes confirmam).

## 14. Suíte completa

- **565 passed / 18 skipped / 0 failed** (em ~68s).
- Baseline 545 mantido; +20 testes novos. Nenhum teste antigo removido ou
  enfraquecido.

## 15. Arquivos alterados (5F-C)

| Arquivo | Natureza |
|---|---|
| `src/odds_coleta.py` | coluna `provider`, hash V2, migração idempotente, abstração `OddsProvider`, adapter `APIFootballOddsProvider`, registry, `resolve_provider` |
| `tests/test_odds_coleta.py` | +20 testes de migração/provider |
| `docs/ETAPA_5FC_FUNDACAO_MULTIPROVIDER.md` | este documento |
| `docs/etapa5fc_fundacao_multiprovider.json` | artefato de auditoria |

`src/app.py` e todos os módulos do motor: **não alterados**.

## 16. Confirmação de que novas APIs ainda não foram integradas

- **NÃO** há código para The Odds API, Sportmonks, APIFootball.com,
  5DollarFootballAPI, football-data.org ou StatsBomb.
- **NÃO** há variáveis de ambiente para essas fontes.
- **NÃO** houve chamada externa, gasto de quota, fallback, calibração ou
  início de Etapa 6.
- O registry contém somente `api_football`.

## 17. Git

- branch: `etapa-5fc-fundacao-multiprovider` (de `etapa-5f-coleta-odds-reais`
  em `1cc2d6e`).
- commit: `etapa 5fc: preparar fundacao multiprovider com proveniencia`.
- **NÃO merge em main. NÃO push.** Banco/backup não versionados (gitignored).

---

## RESPOSTAS OBRIGATÓRIAS

**BANCO ESTÁ PRONTO PARA MULTI-PROVIDER?** **SIM** — coluna `provider` presente
(`NOT NULL`), índice `idx_odds_hist_provider`, hash V2 diferencia provider,
registry + abstração de provider implementados, legado classificado como
`api_football`, dedup multi-fonte validada (sem colisão).

**PROVENIÊNCIA DE PROVIDER ESTÁ GARANTIDA?** **SIM** — cada registro carrega
`provider` explícito (FONTE distinta de bookmaker); adapter passa
`provider_name` até o armazenamento; `provider` é `NOT NULL`.

**API-FOOTBALL CONTINUA FUNCIONANDO?** **SIM** — adapter
`APIFootballOddsProvider` reusa `src/api_client.py` sem reescrita; CLI
`oddscoleta` inalterado; coleta e ingestão de cache produziram hashes V2
compatíveis com o legado migrado (0 duplicatas, 0 perdas).

**THE ODDS API PODE SER ADICIONADA COMO ADAPTER NA PRÓXIMA ETAPA?** **SIM** —
a fundação (Protocol, registry, coluna `provider`, hash V2) está pronta; basta
criar o adapter e registrá-lo (sem tocar o motor).

**5DOLLAR PODE SER ADICIONADA COMO ADAPTER NA PRÓXIMA ETAPA?** **SIM** — mesma
fundação; novo adapter + entrada no registry.

**SPORTMONKS/APIFOOTBALL PODEM SER TESTADAS FUTURAMENTE SEM ALTERAR O MOTOR?**
**SIM** — o motor é independente da camada de coleta; novos adapters não
importam `politica_aprovacao`/`analysis`/`settlement`.

**MOTOR DE APOSTAS PERMANECE INTACTO?** **SIM** — diff vazio em todos os
módulos de decisão; testes confirmam regras congeladas (0.70/0.97/0.60 +
`amarelo=1, vermelho=2`).

**ESTAMOS PRONTOS PARA A ETAPA 5F-D — INTEGRAÇÃO/AUDITORIA REAL DAS FONTES?**
**SIM (infraestrutura)** — a fundação multi-provider está pronta. Mas a 5F-D
exige **antes**: (1) rotacionar `API_KEY` (exposta visualmente, conforme
precheck 5F-A); (2) configurar credenciais das fontes escolhidas; (3) só então
fazer as chamadas reais de auditoria. Sem autorização explícita e rotação de
chaves, 5F-D não deve ser iniciada.

---

> PARE. NÃO integrar novas APIs. NÃO chamar APIs externas. NÃO iniciar
> fallback. NÃO iniciar calibração. NÃO iniciar Etapa 6. NÃO fazer merge. NÃO
> fazer push.