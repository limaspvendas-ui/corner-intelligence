# LIVE em Modo Teste — Override Auditável + Saída Oficial Live

**Data:** 2026-09-14
**Branch:** `etapa-live-override-teste` → merge FF em `main`
**Tag:** `v1.1.0-live-teste` (nova; **`v1.0.0-operacional` intocada**)
**Base:** `5837737` (v1.0.0-operacional, main=origin/main)

## 1. Motivo

O fluxo LIVE existe como motor experimental completo em
`src/live_opportunity.py` (Poisson sobre tempo restante, `blend_rate`,
`_confianca`, `_aprovar` com auditoria, máx 2 aprovadas de fixtures
diferentes), mas estava **BLOQUEADO** na camada operacional
(`STATUS_MERCADOS["pressao_live"]=BLOQUEADO`, sem override, sem
`SaidaOficial` live). A auditoria anterior recusou a varredura live por
falta de liberação operacional.

O operador autorizou o desenvolvimento **mínimo necessário** para
habilitar LIVE em **MODO TESTE**: um override auditável de MODO (não de
mercado), uma `SaidaOficial` canônica live consumindo o motor live
existente, CLI oficial, testes determinísticos e fechamento. A tag
`v1.0.0-operacional` não é tocada.

## 2. Verdade estatística (PRESERVADA)

- **STATUS_ESTATISTICO_LIVE = BLOQUEADO / NÃO_VALIDADO.**
- `STATUS_MERCADOS["pressao_live"]["status"]` permanece `BLOQUEADO`
  (intocado). `pressao_live` NÃO entra em `OVERRIDE_OPERACIONAL`.
- O override de MODO **NÃO** é aprovação estatística. Nenhuma string
  `APROVADO_ESTATISTICAMENTE` é emitida para LIVE.
- A pressão ofensiva live (5/10/15) é **experimental** — sem thresholds
  validados, sem backtest out-of-sample. Permanece em observação.

## 3. Decisão operacional (override auditável)

- **STATUS_OPERACIONAL_LIVE = HABILITADO_PARA_TESTE_POR_OVERRIDE_DO_USUARIO.**
- `MODO_TESTE_LIVE["modo_teste"] = "true"`.
- Override auditável: `override_operador="true"`,
  `decisao_humana="true"`, `motivo` explícito, `timestamp_override`
  real, `versao_projeto` (hash do HEAD em runtime via `_projeto_hash()`).
- **Override NÃO cria sinal:** só aparecem oportunidades que o motor live
  retornar `ENTRAR` (`aprovada_motor=True`). Se o motor não aprovar,
  vira observação (não vira oportunidade operacional).

## 4. Arquitetura

```
scan_live_opportunities (motor live, src/live_opportunity.py)
        │  READ: fetch_live_snapshot(record=False) — não grava histórico
        │  de validação; não registra no baseline pré-live.
        ▼
varredura_live (src/operacional.py)  ← filtro de universo (classificar_liga)
        │  reusa o motor; NÃO duplica matemática; NÃO recalibra.
        ▼
_construir_saida_live (função pura da saída do motor)
        │  roteia pelo status estatístico + override de mercado
        │  (_rota_mercado, agnóstica a modo)
        ▼
SaidaOficial (mode="live", modo_teste=True)
        │  to_dict() canônico (--json) | formatar_saida_live (texto)
        ▼
CLI: python -m src.app aovivooficial [--mercado ...] [--json]
```

- **GOALS live** → operacional por via **estatística**
  (`origem_operacional="estatistico"`,
  `status_operacional="HABILITADO_ESTATISTICAMENTE`).
- **CORNERS live** → operacional por **override de mercado**
  (`origem_operacional="override_usuario"`,
  `status_operacional="HABILITADO_POR_OVERRIDE_DO_USUARIO"`,
  status estatístico `EM_OBSERVAÇÃO` preservado).
- **RESULTADO live** → observação. **CARDS live** → bloqueado
  (não avaliável). **pressao_live / odds_roi** → bloqueado status-only.
- **Frescor:** `data_freshness` computado comparando `varredura.hora`
  com `now_brt()`. Se delta > 180s (`_LIVE_FRESHNESS_SEG`,
  espelha `AUDIT_FRESHNESS_SEG` do motor) → `STALE` e **nenhuma**
  aprovada vira operacional (rebaixada a observação com
  `decisao_oficial="DADO_LIVE_DESATUALIZADO"`).
- **NULL ≠ ZERO:** `minuto=None` é repassado como `None` (não 0);
  exibido como "NÃO DISPONÍVEL" no formatador.

## 5. Saída oficial canônica live (campos)

`SaidaOficial.to_dict()` em modo live contém:

| Campo | Descrição |
|---|---|
| `fixture_id` / `espec` (home x away) / `competicao` | identificação |
| `kickoff` (`snapshot.date_local`) | início da partida |
| `live_minute` / `score` / `status_live` | minuto, placar, status (2H, etc.) |
| `generated_at` | timestamp da geração da saída |
| `provider` | `API-Football v3 (api-sports.io)` |
| `engine_version` | `VERSAO_LIVE_OP` = `live-op-1.0-experimental` |
| `camada_version` | `VERSAO_CAMADA_LIVE` = `operacional-live-0.1-teste` |
| `projeto_hash` | hash do HEAD (runtime) |
| `data_freshness` | `FRESCO` / `STALE` |
| `mode` / `modo_teste` | `"live"` / `True` |
| `live.status_estatistico` | `BLOQUEADO` |
| `live.status_operacional` | `HABILITADO_PARA_TESTE_POR_OVERRIDE_DO_USUARIO` |
| `live.override_operador` / `decisao_humana` | `"true"` |
| `live.timestamp_override` / `motivo_override` | auditabilidade |
| `live.versao_projeto` | hash do HEAD |
| `markets` | status + contagens por mercado |
| `operational_opportunities` | sinais ENTRAR (aprovada_motor=True) |
| `observations` | classe C + não-aprovadas + stale demoted |
| `blocked` | pressao_live, odds_roi, cartoes |
| `provenance` | motor + camada + fonte + nota |

Cada entrada em `operational_opportunities` carrega `modo_teste=True`,
`source="motor:live-op-1.0-experimental"`, `odd` (se disponível) e
`classificacao` ("OPORTUNIDADE COM ODD AO VIVO REAL" | "OPORTUNIDADE
ESTATISTICA AO VIVO REAL").

## 6. Como executar

```bash
# Saída canônica completa (JSON, consumível por API/MCP/ChatGPT)
python -m src.app aovivooficial --json

# Resumo legível
python -m src.app aovivooficial

# Restringir famílias avaliadas pelo deep dive (não altera cálculo)
python -m src.app aovivooficial --mercado gols --json
```

`--mercado` aceita `todos` (default) | `escanteios` | `gols` |
`cartoes` | `resultado`.

Se não houver jogo ao vivo elegível:
```
-- NENHUM JOGO AO VIVO ELEGÍVEL DISPONÍVEL PARA TESTE AGORA --
```
Isso é **resultado válido** — não simula partida passada, não fabrica
oportunidade.

## 7. Limitações (o que NÃO está validado)

- LIVE é **experimental**; **NÃO validado estatisticamente**.
- Pressão ofensiva live (5/10/15 min) é experimental, sem thresholds
  validados e sem backtest out-of-sample. Não é pressão minuto-a-minuto
  (1/15/.../90).
- Sem ROI live; sem integração com bookmaker; sem registro de validação
  live (separado do baseline pré-live congelado).
- A saída live **não congela** no registro de validação pré-live.

## 8. Proibição de aposta automática

- `MODO_TESTE_LIVE=true`: **nenhuma aposta financeira é executada,
  integrada, registrada ou ordenada.**
- A `SaidaOficial` live não contém `aposta_executada`,
  `aposta_realizada`, `ordem_enviada` ou `bookmaker_integration`.
- O override habilita **coleta e teste**, não execução financeira.

## 9. Snapshots append-only

- `varredura_live` chama `scan_live_opportunities`, que usa
  `fetch_live_snapshot(..., record=False)` por padrão — **não grava**
  no histórico imutável `live_snapshot_history` durante a varredura
  oficial de teste.
- `live_snapshots` (cache do último snapshot) é `INSERT OR REPLACE`
  (cache, não histórico).
- `live_snapshot_history` é append-only imutável
  (`UNIQUE(fixture_id,seq)`).

## 10. Integridade

- Motor estatístico 100% intacto (diff vazio em `live_opportunity.py`,
  `prejogo_opportunity.py`, `analysis.py`, `backtest.py`, `policy.py`,
  `validacao_multifonte.py`, `politica_aprovacao.py`).
- Pré-live sem regressão: `SaidaOficial()` default `mode="prejogo"`,
  `live={}`, `modo_teste=False`; `_construir_saida` pré-live inalterado;
  `_rota_mercado` agnóstica a modo.
- Extensão aditiva: 3 campos com defaults em `SaidaOficial`
  (`mode`, `modo_teste`, `live`); 5 funções novas em `operacional.py`
  (`_live_freshness`, `_entry_opp_live`, `_construir_saida_live`,
  `varredura_live`, `formatar_saida_live`); 1 comando CLI
  (`aovivooficial`). `aovivoop` (experimental) preservado.
- Testes: `tests/test_live_override.py` — 16 testes (A-N + 2 extras),
  determinísticos, sem API.
- Regressão completa: **729 passed (713+16), 18 skipped, 0 failed.**
- `.env` gitignored, segredo versionado NÃO, NULL≠ZERO preservado.

## 11. Fechamento

- Commit: `live: habilitar modo teste com override auditavel` na branch
  `etapa-live-override-teste`.
- Merge fast-forward em `main` + push para `origin/main`.
- Nova tag anotada `v1.1.0-live-teste` (+ push). `v1.0.0-operacional`
  **não movida** (permanece em `dedd1cd`).
- Após push: `main == origin/main == v1.1.0-live-teste`.
- Smoke test live real (read-only): ver seção de execução no relatório
  final da sessão.

## 12. Não-fazer (constraints respeitadas)

- Não alterar `v1.0.0-operacional`.
- Não recalibrar LIVE / thresholds / Poisson / `blend_rate` / `_aprovar`.
- Não criar segundo motor nem backend paralelo.
- Não declarar LIVE aprovado estatisticamente.
- Não executar/integrar aposta financeira.
- Não alterar `STATUS_MERCADOS["pressao_live"]` (permanece BLOQUEADO).
- Não simular jogo passado como live; NULL≠ZERO.

---

**Estado final:** PRÉ-LIVE operacional (GOALS estatístico + CORNERS
override) **preservado** + LIVE habilitado em MODO TESTE por override
auditável + verdade estatística LIVE (BLOQUEADO/NÃO_VALIDADO)
**preservada**.