# ETAPA 3 — SÉRIE TEMPORAL DE SNAPSHOTS LIVE + PRESSÃO 5/10/15 MIN

Status: **EXPERIMENTAL / A CALIBRAR** (FATO + CÁLCULO). Nenhum threshold
operacional validado. A pressão temporal **não altera** regras de
aprovação, Poisson, blend_rate, EDGE_MINIMO, PROB_MIN/MAX_APROVAR,
CONF_MIN_TOP1/TOP2 ou a política operacional.

## 1. Estrutura temporal criada

Nova tabela **separada** `live_snapshot_history` (SQLite, mesmo DB do
cache live `data/corner_intelligence.db`), **append-only / imutável**:

| coluna | tipo | descrição |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | id global |
| `fixture_id` | INTEGER NOT NULL | partida |
| `seq` | INTEGER NOT NULL | contador monotono por fixture |
| `snapshot` | TEXT NOT NULL | JSON do `LiveSnapshot` (to_dict) |
| `collected_at_text` | TEXT NOT NULL | hora BRT real (campo `collected_at` do snapshot) |
| `collected_at_epoch` | REAL NOT NULL | epoch real da coleta (`time.time()`) |
| | `UNIQUE(fixture_id, seq)` | imutabilidade: snapshot antigo nunca sobrescrito |
| | index `(fixture_id, collected_at_epoch)` | consulta por janela |

Escrita: **INSERT puro** (nunca `INSERT OR REPLACE`). Cada coleta = novo
fato temporal. Módulo: `src/live_pressure.py` (`LiveSnapshotHistory`).

## 2. Compatibilidade preservada

A tabela `live_snapshots` (cache do **último** snapshot, `INSERT OR
REPLACE`, uma linha por fixture) **não foi destruída** — `update_live_snapshot`,
`cmd_aovivodados --atualizar` e os testes `test_7/7b/7c` dependem dela e
continuam intactos. A série temporal é **adicional**. O `LiveSnapshotStore`
ganhou apenas `_raw_row()` (expõe o epoch real `updated_at` para o seed,
sem inventar timestamp).

## 3. Seed sem inventar timestamp

`LiveSnapshotHistory.seed_from_latest(fixture_id)`: se o histórico do
fixture está vazio **e** existe linha em `live_snapshots`, incorpora esse
snapshot como 1º registro histórico usando o **epoch real** `updated_at`
(gravado por `LiveSnapshotStore.save` no momento da coleta) e o
`collected_at` real do snapshot. Nada é inventado. Se não houver linha,
preserva como está (retorna `None`). Se o histórico já não está vazio,
não faz nada (nunca sobrescreve).

## 4. Coleta automática (60s)

`collect_live_series(client, fixture_id, interval=60, ...)` —

- intervalo padrão **60s** (configurável tecnicamente via `--intervalo`);
- cada coleta chama `fetch_live_snapshot(refresh=True, record=True)`
  (mantém o cache `live_snapshots`) **e** appenda na série temporal;
- para sozinho quando a partida **encerra** (status `FT/AET/PEN` —
  `fetch_live_snapshot` levanta `UserFacingError` "encerrado");
- **Ctrl+C** interrompe limpo: cada `append` é sua própria transação
  confirmada — nenhuma transação multi-linha aberta entre coletas;
- **não duplica descontroladamente**: 1 coleta por intervalo, born pelo
  tempo de partida (~90–120 registros no máximo), para no encerramento;
- `max_iterations` e `sleep_fn` injetáveis — **testes não esperam 60s
  reais** (`sleep_fn=lambda s: None`, `max_iterations=N`).

CLI: `python -m src.app aovivocoleta "Flamengo x Palmeiras" [--intervalo 60] [--max-iter N]`

## 5. Pressão 5/10/15 MIN

`compute_pressure_windows(records)` (função **pura**, sem DB/rede) e
`query_pressure(fixture_id, db_path=...)` (lê o histórico gravado).

Janelas: `PRESSURE_WINDOWS = (5, 10, 15)` **minutos LIVE** — **não
confundir** com `STATS_WINDOWS=(5,10,20)` (janelas de **jogos
históricos** em `src/config.py`).

Para cada janela **W** (quando há histórico suficiente):

- `avaliable` ⇔ (epoch do tip − epoch do primeiro) ≥ W·60 **e** ≥ 2
  snapshots;
- `base` = snapshot de maior epoch ≤ (tip − W·60);
- `actual_window_s` = tip − base (REAL, pode exceder W quando o intervalo
  entre snapshots é irregular — honesto, **nunca presume** "minuto 70 →
  75 = 300s");
- por time + total: **delta** de escanteios, finalizações, chutes no
  alvo, bloqueadas;
- **ritmo por minuto** = delta ÷ (actual_window_s/60) (usa o intervalo
  real);
- **mudança de posse** (p.p., sinalizada — pode ser negativa, é legítimo);
- `snaps_usados`, `base_collected_at`, `tip_collected_at`.

CLI: `python -m src.app aovivopressao "Flamengo x Palmeiras"`

## 6. Dados insuficientes

Se não há histórico suficiente para a janela: **não extrapola, não
inventa, não usa zero** →
`NÃO AVALIÁVEL — HISTÓRICO TEMPORAL INSUFICIENTE` (`avaliable=False`,
`motivo=NAO_AVALIAVEL_HISTORICO`). Ex.: 7 min monitorados → 5 MIN
avaliable, 10/15 MIN NÃO AVALIÁVEL.

## 7. None ≠ zero

Estatística ausente em uma das leituras → delta `None` (nunca 0) com
`motivo` explicando. Possession ausente → `possession_change.home_change
= None`. O cálculo de totais **não** é feito sem dado.

## 8. Correções da fonte / delta negativo

Stat cumulativa (escanteios/finalizações/SOT/bloqueadas) que diminui
entre duas coletas (ex.: 5 → 4 escanteios) = **correção/inconsistência
da fonte**:

- **não** vira zero, **não** usa valor absoluto, **não** esconde;
- métrica afetada → `None` (NÃO AVALIÁVEL), `inconsistencia_fonte=True`,
  `motivo` documenta a correção;
- **ambos os snapshots preservados** no histórico imutável (imutabilidade
  da série → futuro backtest confiável).

## 9. Integração ao motor LIVE

`deep_dive(..., pressao=False)` (opt-in, ETAPA 3): quando `pressao=True`,
anexa `candidato.pressao = query_pressure(fixture_id, db_path=src.live.DB_PATH)`.
**Default `False`** → o fluxo validado não faz acesso extra a DB e nada
muda nas avaliações. A pressão é **info auditável apenas**: não entra em
probabilidade, confiança, edge, Poisson, blend_rate ou aprovação.

## 10. Status EXPERIMENTAL — o que AINDA NÃO está validado

- **Nenhum threshold** BAIXA/MODERADA/ALTA (não há regra anterior
  documentada e validada para pressão temporal). A classificação
  qualitativa fica para **backtest futuro**.
- A pressão temporal **não força aposta** e **não altera** regras de
  aprovação existentes.
- `offensive_pressure` (src/analysis.py) permanece como proxy de ponto
  único (não temporal) — não foi tocado.

## 11. Imutabilidade

Snapshots históricos são fatos capturados naquele instante. **Nunca**
reescritos porque a API mudou depois. Cada nova captura = novo fato
temporal (`UNIQUE(fixture_id, seq)`, INSERT puro).

## 12. Testes

`tests/test_etapa3_pressao_live.py` — 23 testes cobrindo os 20 itens da
FASE J (+ variações Ctrl+C e posse presente). Banco temporário
(`tmp_path`) em todos; nunca o banco operacional. Sem espera de 60s
reais.