# FECHAMENTO OPERACIONAL DO CORNER INTELLIGENCE

**Data:** 2026-09-14
**Branch:** `etapa-fechamento-operacional`
**Baseline:** 678 passed / 18 skipped / 0 failed (após macroetapa evidência multifonte, commit 262e5c8)
**Após fechamento:** 694 passed / 18 skipped / 0 failed (+16 testes da camada operacional, 0 regressões)
**Motor:** intacto (diff vazio em todos os módulos matemático/decisórios)

---

## 1. Objetivo

Fechar operacionalmente o projeto **sem recalibrar, sem mudar thresholds e sem
fabricar aprovação**. Criar uma camada operacional oficial que:

1. usa o motor existente;
2. preserva integralmente suas regras;
3. exponha uma saída oficial única;
4. permita ação operacional somente onde já existe aprovação estatística;
5. bloqueie explicitamente os demais mercados;
6. prepare o projeto para o teste posterior Claude Code × ChatGPT;
7. deixe o projeto pronto para operação e manutenção contínua.

**Princípios inegociáveis preservados:**

- NÃO criar modelo novo. NÃO recalibrar. NÃO alterar Poisson. NÃO alterar
  thresholds. NÃO alterar predictions históricas. NÃO alterar settlement histórico.
- A nova camada NÃO duplica lógica matemática — consome a saída oficial já existente.
- Nunca promover automaticamente mercado por cobertura.
- Não inventar linha. Não inventar odd. Não transformar ausência em zero.
- Nunca forçar aposta — "NENHUMA OPORTUNIDADE OPERACIONAL APROVADA" é resultado válido.

---

## 2. Arquitetura da camada operacional

```
┌─────────────────────────────────────────────────────────┐
│  CAMADA OPERACIONAL  (src/operacional.py)               │
│  - STATUS_MERCADOS: registro oficial dos 6 mercados     │
│  - SaidaOficial: saída canônica única                   │
│  - analisar_fixture() / varredura_data()                │
│  - Gate: apenas GOALS aprovado → operacional            │
└───────────────┬─────────────────────────────────────────┘
                │ consome (READ, registrar=False)
                ▼
┌─────────────────────────────────────────────────────────┐
│  MOTOR EXISTENTE (intacto)                              │
│  src/prejogo_opportunity.py  (Poisson, confiança,       │
│  aprovação, thresholds)                                 │
│  src/policy.py · src/cobertura.py · src/analysis.py     │
└─────────────────────────────────────────────────────────┘
```

A camada é **função pura da saída do motor**: mesmo fixture + mesmos dados +
mesma versão do motor = mesma decisão oficial. A IA (Claude Code, API, MCP,
ChatGPT futuramente) consulta a `SaidaOficial`; a IA **interpreta**, não
substitui o motor.

---

## 3. Registro oficial de status dos mercados

Fonte: validação estatística multifonte (macroetapa 5F-F, baseline
`bt-20260913224716-PRE_GAME`). Informativo/operacional — **não é recalibração**.

| Mercado | Status | Motivo |
|---|---|---|
| **gols** | `APROVADO_PARA_PROXIMA_FASE` | OOS hit 0.9421, walk-forward estável 0.93–0.95; único mercado aprovado |
| **resultado** | `EM_OBSERVACAO` | experimental; OOS 0.9259 mas walk-forward variável (0.88–0.98) |
| **escanteios** | `EM_OBSERVACAO` | drift temporal confirmado (0.927 → 0.875 pós-cutoff) |
| **cartoes** | `NÃO_AVALIÁVEL` | limitação estrutural da fonte (red_cards NULL 5D); 207/207 NA |
| **pressao_live** | `BLOQUEADO` | série temporal por fixture incompleta; experimental a calibrar |
| **odds_roi** | `BLOQUEADO` | odds prospectivas não sobrepõem settled backtest; ROI não calculável |

**Decisão oficial por status (o gate operacional):**

| Status estatístico | Decisão oficial |
|---|---|
| `APROVADO_PARA_PROXIMA_FASE` | `ENTRAR` (operacional) |
| `EM_OBSERVACAO` | `OBSERVACAO` (observação, não operacional) |
| `NÃO_AVALIÁVEL` | `NAO_AVALIAVEL` (bloqueado) |
| `BLOQUEADO` | `BLOQUEADO` |

A decisão oficial é **distinta** da decisão do motor: o motor decide
`aprovada` (Poisson + confiança + thresholds); a camada roteia pelo status
estatístico. Apenas `APROVADO_PARA_PROXIMA_FASE` vira oportunidade operacional.

---

## 4. Saída oficial única (`SaidaOficial`)

```python
@dataclass
class SaidaOficial:
    fixture_id: int | None
    espec: str
    generated_at: str           # timestamp BRT
    engine_version: str         # VERSAO_PREJOGO_OP (motor)
    camada_version: str         # operacional-1.0 (gate/formato)
    projeto_hash: str | None    # git HEAD (proveniência)
    data_status: str            # OK | SEM_JOGO | JOGO_NAO_ELEGIVEL_PRE
    markets: dict               # status + contagens do motor por mercado
    operational_opportunities: list   # apenas GOALS aprovado
    observations: list          # resultado/corners (EM_OBSERVAÇÃO)
    blocked: list               # cards/live/roi (BLOQUEADO/NÃO_AVALIÁVEL)
    provenance: dict            # motor, camada, hash, fonte, nota
    nenhum_aprovado: bool       # True quando 0 operacionais
```

Consumível por:
- **CLI:** `python -m src.app analisar "A x B" [--json]` / `varredura [--data YYYY-MM-DD] [--json]`
- **API/MCP/ChatGPT (futuro):** o dict canônico via `to_dict()` / `--json`

Campos por oportunidade: `fixture_id, espec, competicao, home, away, kickoff,
mercado, linha, lado, prob, confianca, aprovada_motor, decisao_oficial,
status_estatistico, motivo_status, prediction_timestamp, riscos, source`.

Os valores `prob`, `confianca`, `linha`, `riscos` vêm **direto do motor** — a
camada não recalcula nem altera.

---

## 5. CLI (reutiliza src/app.py)

| Comando | Descrição |
|---|---|
| `analisar "A x B"` | Saída oficial canônica de um fixture (READ, não registra) |
| `analisar "A x B" --json` | Saída canônica completa (JSON) |
| `varredura [--data YYYY-MM-DD]` | Varredura por data: elegíveis → motor → saída agregada |
| `varredura --json` | Saída canônica agregada (JSON) |

`analisar` chama `scan_pregame_opportunities(registrar=False, incluir_resultado=True,
incluir_cartoes=False)` — READ: não congela recomendação no registro. `incluir_cartoes=False`
evita chamada extra de `league_cards_average` (CARDS=NÃO_AVALIÁVEL, sem valor operacional).

`varredura` filtra fixtures encerrados/ao vivo, aplica `jogo_elegivel` (policy), executa
o motor por fixture e agrega. Se nenhum jogo passar: `"NENHUMA OPORTUNIDADE OPERACIONAL APROVADA"`.

---

## 6. Separar aprovação estatística de validação econômica

GOALS tem aprovação estatística (`APROVADO_PARA_PROXIMA_FASE`) — separada da
validação econômica/ROI (`odds_roi=BLOQUEADO`). A disponibilidade de odd **não**
vira requisito novo do motor estatístico de GOALS nesta etapa. A camada expõe
`prob/confianca` do motor; a validação de valor financeiro fica em `odds_roi`
(bloqueado).

---

## 7. Coleta prospectiva preservada

A coleta contínua (odds prospectivas, live snapshots, reconciliação, fallback,
source_conflict, factual_resolution) continua existindo em paralelo
(`src/odds_coleta.py`, `src/live_pressure.py`, `src/resolucao_factual.py`,
`src/evidencia_multifonte.py`) e **não bloqueia** este fechamento. A camada
operacional é read-only em relação ao motor de decisão.

---

## 8. Versionamento de decisão

Cada `SaidaOficial` carrega:
- `engine_version` = `prejogo-op-1.0-observacao` (versão do motor — inalterada)
- `camada_version` = `operacional-1.0` (versão do gate/formato de saída)
- `projeto_hash` = git HEAD (proveniência)
- `generated_at` = timestamp BRT
- `prediction_timestamp` por oportunidade

Mesmo fixture + mesmos dados + mesma versão do motor = mesma decisão oficial
(função pura, sem aleatoriedade).

---

## 9. Testes (A-K, determinísticos)

`tests/test_operacional.py` — 16 testes, sem chamar a API (constroi
`VarreduraPreJogo` sintéticas e exercita `_construir_saida`):

- **A** GOALS aprovado+ENTRAR → `operational_opportunities`
- **B** GOALS não aprovado pelo motor → não vira operacional
- **C** RESULTADO aprovado pelo motor → observação (nunca operacional)
- **D** CORNERS sinal → `EM_OBSERVAÇÃO` (observação)
- **E** CARDS → `NÃO_AVALIÁVEL` (bloqueado)
- **F** PRESSÃO LIVE → `BLOQUEADO` (status-only)
- **G** dado insuficiente → `SEM_JOGO`/`JOGO_NAO_ELEGIVEL_PRE` (nunca zero inventado)
- **H** determinismo: mesma entrada → mesma decisão
- **I** a camada não altera `prob`/`confianca`/`linha` do motor
- **J** saída tem versão/timestamp/proveniência
- **K** nenhuma oportunidade → resultado válido, sem aposta forçada
- Extras: status oficial preservado, versão do motor intacta, nunca promove automaticamente

**Regressão completa:** 694 passed / 18 skipped / 0 failed (baseline 678/18/0 → +16, 0 regressões).

---

## 10. Auditoria do motor (diff)

Módulos matemático/decisórios **intactos** (diff vazio):

```
INTACTO: src/prejogo_opportunity.py
INTACTO: src/backtest.py
INTACTO: src/analysis.py
INTACTO: src/politica_aprovacao.py
INTACTO: src/cobertura.py
INTACTO: src/settlement.py
INTACTO: src/policy.py
INTACTO: src/validacao_multifonte.py
INTACTO: src/checkpoint_calibracao.py
INTACTO: src/multifonte.py
INTACTO: src/evidencia_multifonte.py
INTACTO: src/auditoria_multifonte.py
INTACTO: src/live_pressure.py
```

Único arquivo modificado: `src/app.py` (diff **puramente aditivo**: 2 funções
`cmd_analisar`/`cmd_varredura` + 2 subparsers + 2 linhas de docstring). Nenhuma
função existente ou lógica de motor alterada.

---

## 11. Smoke test

`python -m src.app analisar "Liverpool x Manchester City"` (fixture real,
id=1557424, cache):

- `data_status=OK`
- GOALS: 2 aprovadas pelo motor → 2 `operational_opportunities` (ENTRAR)
- escanteios: 18 avaliações, 0 aprovadas → observações
- resultado: 34 avaliações, 0 aprovadas → observações
- cartoes/live/roi → bloqueados
- `registrar=False`: nenhuma recomendação registrada (recomendacoes=79 inalterado)
- Tabelas append-only de decisão intactas (factual_resolution=92,
  live_snapshot_history=6, multifonte_reconciliation=92)

Confirma: o motor decide, a camada roteia pelo status estatístico.

---

## 12. Arquivos deste fechamento

| Arquivo | Tipo |
|---|---|
| `src/operacional.py` | NOVO — camada operacional (STATUS_MERCADOS, SaidaOficial, gates) |
| `src/app.py` | MODIFICADO (aditivo) — CLI `analisar` + `varredura` |
| `tests/test_operacional.py` | NOVO — 16 testes A-K |
| `docs/FECHAMENTO_OPERACIONAL_CORNER_INTELLIGENCE.md` | NOVO — este documento |

---

## 13. Pronto para teste Claude Code × ChatGPT

A `SaidaOficial` (dict canônico via `--json`) é a fonte oficial de verdade para
comparação entre IAs. Ambas consomem o mesmo motor, a mesma versão, o mesmo
gate — a interpretação é o que se compara. A integração com ChatGPT **não foi
iniciada** (conforme etapa 20).

---

## 14. Status operacional final

- **CAMADA OPERACIONAL CRIADA:** SIM
- **MOTOR ÚNICO PRESERVADO:** SIM (diff vazio)
- **GOALS DISPONÍVEL (operacional):** SIM
- **GOALS RECALIBRADO:** NÃO
- **THRESHOLDS ALTERADOS:** NÃO
- **POISSON ALTERADO:** NÃO
- **PREDICTIONS HISTÓRICAS ALTERADAS:** NÃO
- **SETTLEMENT HISTÓRICO ALTERADO:** NÃO
- **MERCADOS BLOQUEADOS ALTERADOS:** NÃO
- **PROMOÇÃO AUTOMÁTICA POR COBERTURA:** NÃO
- **BANCO ÍNTEGRO:** SIM (integrity ok, user_version 4)
- **SEGREDO VERSIONADO:** NÃO (.env gitignored)
- **MOTOR INTACTO:** SIM
- **PRONTO PARA AUDITORIA FINAL:** SIM

**Etapa 6 (integração ChatGPT / calibração):** NÃO INICIADA. BLOQUEADA.