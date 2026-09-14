"""ETAPA 3 - SERIE TEMPORAL IMUTAVEL DE SNAPSHOTS LIVE + PRESSAO 5/10/15.

Camada FATO + CALCULO (nao INTERPRETACAO/DECISAO) da Etapa 3.

O LiveSnapshotStore (src/live.py) mantem somente o ULTIMO snapshot por
fixture (INSERT OR REPLACE) - e o cache da "ultima leitura" que sustenta
o diff do `aovivodados --atualizar`. Essa tabela NAO e destruida: outras
partes do sistema dependem dela (update_live_snapshot, cmd_aovivodados,
testes test_7/test_7b/test_7c). Aqui criamos armazenamento TEMPORAL
SEPARADO, append-only, que preserva cada coleta cronologicamente.

REGRAS DA ETAPA (verbatim do operador):
  - NAO usar INSERT OR REPLACE para o historico. Cada coleta e
    preservada cronologicamente (FATO capturado naquele instante).
  - NAO destruir a tabela atual sem verificar dependencias (verificado:
    live_snapshots e usada por update_live_snapshot/cmd_aovivodados/
    testes -> mantida intacta).
  - Incorporar o snapshot atual existente como primeiro registro
    historico SEM INVENTAR timestamp/dado, quando possivel com seguranca.
  - None NUNCA vira zero. Ausente continua None / NAO DISPONIVEL.
  - Timestamps REAIS das coletas medem o intervalo (Windows: nunca
    presumir "minuto 70 -> 75 = 300s"; usa-se o epoch real).
  - Historico insuficiente -> "NAO AVALIAVEL - HISTORICO TEMPORAL
    INSUFICIENTE".
  - Correcao da fonte / delta negativo (ex.: 5 -> 4 escanteios): NAO
    virar zero, NAO usar valor absoluto, NAO esconder. Marca
    inconsistencia/correcao da fonte, metrica afetada NAO AVALIAVEL,
    preserva os dois snapshots originais.
  - NAO inventar thresholds BAIXA/MODERADA/ALTA sem regra validada.
    Status: "EXPERIMENTAL / A CALIBRAR".
  - NAO confundir com STATS_WINDOWS=(5,10,20) (janelas de JOGOS
    HISTORICOS); PRESSURE_WINDOWS=(5,10,15) sao MINUTOS LIVE.
  - NAO alterar PROB_MIN_APROVAR/PROB_MAX_APROVAR/CONF_MIN_TOP1/
    CONF_MIN_TOP2/EDGE_MINIMO/Poisson/blend_rate/politica operacional/
    regras de aprovacao. Pressao entra como info experimental/auditavel.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.config import DB_PATH, DATA_DIR
from src.exceptions import UserFacingError
from src.live import LiveSnapshot, LiveSnapshotStore, fetch_live_snapshot

# Janelas LIVE em MINUTOS (nao confundir com STATS_WINDOWS de jogos
# historicos em src/config.py).
PRESSURE_WINDOWS: tuple[int, ...] = (5, 10, 15)

# Intervalo padrao de coleta (segundos) - mesmo TTL live da plataforma.
COLETA_INTERVALO_PADRAO = 60

# Status da metrica de pressao: dados objetivos temporais sem threshold
# validado (FATO + CALCULO; classificacao qualitativa fica para backtest).
PRESSURE_STATUS = "EXPERIMENTAL / A CALIBRAR"

NAO_AVALIAVEL_HISTORICO = (
    "NAO AVALIAVEL - HISTORICO TEMPORAL INSUFICIENTE"
)

# Stats cumulativas monitoradas (tipo exato da API -> rotulo). Cumulativas
# nunca diminuem; delta negativo = correcao/inconsistencia da fonte.
STATS_CUMULATIVAS: tuple[tuple[str, str], ...] = (
    ("Corner Kicks", "escanteios"),
    ("Total Shots", "finalizacoes"),
    ("Shots on Goal", "chutes_no_alvo"),
    ("Blocked Shots", "bloqueadas"),
)
STAT_POSSE = "Ball Possession"

# Status que indicam partida encerrada (parada de coleta).
STATUS_ENCERRADO = {"FT", "AET", "PEN"}


# ----------------------------------------------------------------------
# Registro temporal (um fato capturado em um instante)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class HistoryRecord:
    """Um snapshot historico com sua identidade temporal inequivoca.

    `collected_at_epoch` (real, time.time() da coleta) ordena e mede
    intervalos. `collected_at_text` e a hora BRT legivel (campo real do
    snapshot). `seq` e o contador monotono por fixture (chave de
    imutabilidade: fixture_id + seq e unico).
    """

    fixture_id: int
    seq: int
    collected_at_epoch: float
    collected_at_text: str
    snapshot: LiveSnapshot


# ----------------------------------------------------------------------
# Armazenamento temporal (append-only, IMUTAVEL)
# ----------------------------------------------------------------------
class LiveSnapshotHistory:
    """Serie temporal imutavel de snapshots LIVE por fixture (SQLite).

    Tabela SEPARADA de `live_snapshots` (que continua como cache do
    ultimo snapshot). Aqui cada coleta = nova linha; NUNCA INSERT OR
    REPLACE, NUNCA overwrite. A chave (fixture_id, seq) garante que um
    snapshot antigo nunca e sobrescrito.

    Cada append e sua propria transacao confirmada (Ctrl+C nao corrompe
    o banco: nao ha transacao multi-linha aberta entre coletas).
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or DB_PATH)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # ---------------------------- escrita ----------------------------
    def append(
        self,
        snapshot: LiveSnapshot,
        *,
        collected_at_epoch: float | None = None,
        collected_at_text: str | None = None,
    ) -> HistoryRecord:
        """Acrescenta UM snapshot ao historico (INSERT, nunca replace).

        `collected_at_epoch` default = time.time() real da coleta.
        `collected_at_text` default = snapshot.collected_at (hora BRT
        real gravada pelo fetch). Nenhum timestamp e inventado.

        Retorna o HistoryRecord gravado (identidade temporal real).
        """
        epoch = float(collected_at_epoch if collected_at_epoch is not None
                      else time.time())
        texto = collected_at_text or snapshot.collected_at or ""
        seq = self._proximo_seq(snapshot.fixture_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_snapshot_history
                    (fixture_id, seq, snapshot, collected_at_text,
                     collected_at_epoch)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.fixture_id,
                    seq,
                    json.dumps(snapshot.to_dict(), ensure_ascii=False),
                    texto,
                    epoch,
                ),
            )
        return HistoryRecord(
            fixture_id=snapshot.fixture_id,
            seq=seq,
            collected_at_epoch=epoch,
            collected_at_text=texto,
            snapshot=snapshot,
        )

    def _proximo_seq(self, fixture_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM live_snapshot_history "
                "WHERE fixture_id = ?",
                (fixture_id,),
            ).fetchone()
        return int(row[0]) + 1

    def seed_from_latest(
        self,
        fixture_id: int,
        *,
        latest_store: LiveSnapshotStore | None = None,
    ) -> HistoryRecord | None:
        """Incorpora o snapshot atual (cache `live_snapshots`) como
        PRIMEIRO registro historico, SEM INVENTAR timestamp/dado.

        Condicoes (todas): historico vazio para o fixture E existe linha
        em `live_snapshots` para ele. Usa o epoch real (`updated_at`,
        gravado pelo LiveSnapshotStore.save no momento da coleta) e o
        `collected_at` real do snapshot. Se o historico ja nao esta
        vazio, nao faz nada (retorna None) - nunca sobrescreve.

        Se nao houver snapshot atual, retorna None (preserva como esta).
        """
        if self.count(fixture_id) > 0:
            return None
        store = latest_store or LiveSnapshotStore(db_path=self.db_path)
        row = store._raw_row(fixture_id) if hasattr(store, "_raw_row") \
            else None
        if row is None:
            # fallback: le via get + consulta direta da updated_at
            snap = store.get(fixture_id)
            if snap is None:
                return None
            epoch = self._latest_epoch_from_store(fixture_id, store)
            if epoch is None:
                # sem epoch real armazenado: nao inventa; preserva
                return None
            return self.append(
                snap,
                collected_at_epoch=epoch,
                collected_at_text=snap.collected_at,
            )
        fixture_id_db, snapshot_json, updated_at = row
        snap = LiveSnapshot.from_dict(json.loads(snapshot_json))
        return self.append(
            snap,
            collected_at_epoch=float(updated_at),
            collected_at_text=snap.collected_at,
        )

    def _latest_epoch_from_store(
        self, fixture_id: int, store: LiveSnapshotStore
    ) -> float | None:
        try:
            with sqlite3.connect(store.db_path) as conn:
                row = conn.execute(
                    "SELECT updated_at FROM live_snapshots "
                    "WHERE fixture_id = ?",
                    (fixture_id,),
                ).fetchone()
        except sqlite3.Error:
            return None
        return float(row[0]) if row else None

    # ---------------------------- leitura ----------------------------
    def records(
        self, fixture_id: int, *, since_epoch: float | None = None
    ) -> list[HistoryRecord]:
        """Historico do fixture ordenado cronologicamente (epoch real).

        `since_epoch` opcional filtra registros a partir daquele epoch
        (inclusive). Retorna lista vazia se nenhum.
        """
        sql = (
            "SELECT fixture_id, seq, collected_at_text, "
            "collected_at_epoch, snapshot "
            "FROM live_snapshot_history WHERE fixture_id = ?"
        )
        params: list[Any] = [fixture_id]
        if since_epoch is not None:
            sql += " AND collected_at_epoch >= ?"
            params.append(since_epoch)
        sql += " ORDER BY collected_at_epoch ASC, seq ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            HistoryRecord(
                fixture_id=int(r[0]),
                seq=int(r[1]),
                collected_at_text=r[2],
                collected_at_epoch=float(r[3]),
                snapshot=LiveSnapshot.from_dict(json.loads(r[4])),
            )
            for r in rows
        ]

    def latest(self, fixture_id: int) -> HistoryRecord | None:
        regs = self.records(fixture_id)
        return regs[-1] if regs else None

    def count(self, fixture_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM live_snapshot_history "
                "WHERE fixture_id = ?",
                (fixture_id,),
            ).fetchone()
        return int(row[0])

    def span_seconds(self, fixture_id: int) -> float | None:
        """Cobertura temporal real (latest_epoch - earliest_epoch).

        None se < 2 registros (nao ha intervalo para medir).
        """
        regs = self.records(fixture_id)
        if len(regs) < 2:
            return None
        return regs[-1].collected_at_epoch - regs[0].collected_at_epoch


_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_snapshot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    collected_at_text TEXT NOT NULL,
    collected_at_epoch REAL NOT NULL,
    UNIQUE (fixture_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_live_hist_fixture_epoch
    ON live_snapshot_history (fixture_id, collected_at_epoch);
"""


# ----------------------------------------------------------------------
# Calculo de janelas de pressao (FATO + CALCULO, puro)
# ----------------------------------------------------------------------
@dataclass
class StatDelta:
    """Delta de uma stat cumulativa em uma janela temporal.

    `home/away/total` None quando NAO AVALIAVEL (dado ausente em alguma
    leitura OU correcao da fonte com delta negativo). `rate_per_min_*`
    usam o intervalo REAL (epoch), nunca presumem W minutos exatos.
    """

    rotulo: str
    home: float | None = None
    away: float | None = None
    total: float | None = None
    rate_per_min_home: float | None = None
    rate_per_min_away: float | None = None
    rate_per_min_total: float | None = None
    inconsistencia_fonte: bool = False
    motivo: str | None = None


@dataclass
class PossessionChange:
    """Mudanca de posse (porcentagem, SINALDA legitimo - pode cair)."""

    home_change: float | None = None
    away_change: float | None = None
    motivo: str | None = None


@dataclass
class PressureWindow:
    """Janela de pressao temporal (5/10/15 min) - dados objetivos.

    `avaliable` = historico suficiente para a janela. Mesmo com
    avaliable=True, stats individuais podem ser NAO AVALIAVEIS por
    ausencia de dado ou correcao da fonte (ver StatDelta.motivo).
    """

    janela_min: int
    avaliable: bool
    motivo: str | None = None
    base_collected_at: str | None = None
    tip_collected_at: str | None = None
    actual_window_s: float | None = None
    snaps_usados: int | None = None
    inconsistencia_fonte: bool = False
    corners: StatDelta | None = None
    shots: StatDelta | None = None
    shots_on_goal: StatDelta | None = None
    blocked_shots: StatDelta | None = None
    possession_change: PossessionChange | None = None
    status: str = PRESSURE_STATUS


def _parse_possession(valor: Any) -> float | None:
    """"58%" -> 58.0 ; None/ausente -> None (nunca zero)."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace("%", "").strip()
    if texto == "":
        return None
    try:
        return float(texto)
    except ValueError:
        return None


def _stat_values(
    base: LiveSnapshot, tip: LiveSnapshot, stat_type: str
) -> tuple[float | None, float | None, float | None, float | None]:
    """(base_home, base_away, tip_home, tip_away) numericos ou None."""
    bh = base.stats_home.get(stat_type)
    ba = base.stats_away.get(stat_type)
    th = tip.stats_home.get(stat_type)
    ta = tip.stats_away.get(stat_type)

    def num(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return num(bh), num(ba), num(th), num(ta)


def _delta_cumulative(
    base: LiveSnapshot, tip: LiveSnapshot, stat_type: str, rotulo: str,
    actual_window_s: float,
) -> StatDelta:
    """Delta de uma stat cumulativa. Delta negativo = correcao da fonte
    (NAO AVALIAVEL, sem abs, sem zero, sem esconder; ambos snapshots
    preservados no historico imutavel)."""
    bh, ba, th, ta = _stat_values(base, tip, stat_type)
    if None in (bh, ba, th, ta):
        return StatDelta(
            rotulo=rotulo,
            motivo="estatistica ausente em uma das leituras (None "
                   "permanece None, nunca vira zero)",
        )
    dh = th - bh
    da = ta - ba
    if dh < 0 or da < 0:
        return StatDelta(
            rotulo=rotulo,
            inconsistencia_fonte=True,
            motivo=(
                f"correcao da fonte (delta negativo): "
                f"casa {bh:g} -> {th:g}, fora {ba:g} -> {ta:g}; "
                "metrica NAO AVALIAVEL, ambos snapshots preservados"
            ),
        )
    dt = dh + da
    minutos = actual_window_s / 60.0 if actual_window_s > 0 else None
    rph = dh / minutos if (minutos and minutos > 0) else None
    rpa = da / minutos if (minutos and minutos > 0) else None
    rpt = dt / minutos if (minutos and minutos > 0) else None
    return StatDelta(
        rotulo=rotulo,
        home=dh,
        away=da,
        total=dt,
        rate_per_min_home=rph,
        rate_per_min_away=rpa,
        rate_per_min_total=rpt,
    )


def _possession_delta(
    base: LiveSnapshot, tip: LiveSnapshot
) -> PossessionChange:
    bh = _parse_possession(base.stats_home.get(STAT_POSSE))
    ba = _parse_possession(base.stats_away.get(STAT_POSSE))
    th = _parse_possession(tip.stats_home.get(STAT_POSSE))
    ta = _parse_possession(tip.stats_away.get(STAT_POSSE))
    if None in (bh, ba, th, ta):
        return PossessionChange(
            motivo="posse ausente em uma das leituras (NAO DISPONIVEL)"
        )
    return PossessionChange(
        home_change=th - bh,
        away_change=ta - ba,
    )


def _window_nao_avalivel(janela_min: int) -> PressureWindow:
    return PressureWindow(
        janela_min=janela_min,
        avaliable=False,
        motivo=NAO_AVALIAVEL_HISTORICO,
        status=PRESSURE_STATUS,
    )


def compute_pressure_windows(
    records: list[HistoryRecord],
) -> list[PressureWindow]:
    """Computa PRESSAO 5/10/15 MIN a partir da serie temporal (puro).

    `records` deve estar ordenado por collected_at_epoch asc (use
    LiveSnapshotHistory.records). Funcao pura: sem DB, sem rede - testavel
    com series sinteticas.

    Para cada janela W:
      - avaliable iff (tip_epoch - first_epoch) >= W*60 e >= 2 snapshots;
      - base = snapshot de maior epoch <= (tip_epoch - W*60);
      - actual_window_s = tip_epoch - base_epoch (REAL, pode exceder W
        quando o intervalo entre snapshots e irregular - honesto);
      - deltas e rates por minuto usam actual_window_s (nunca presumem
        W minutos exatos).
    """
    if len(records) < 2:
        return [_window_nao_avalivel(w) for w in PRESSURE_WINDOWS]

    tip = records[-1]
    tip_epoch = tip.collected_at_epoch
    first_epoch = records[0].collected_at_epoch
    span = tip_epoch - first_epoch

    out: list[PressureWindow] = []
    for w in PRESSURE_WINDOWS:
        alvo = tip_epoch - w * 60
        if span < w * 60:
            out.append(_window_nao_avalivel(w))
            continue
        # base = maior epoch <= alvo (garantido: first_epoch <= alvo)
        base = records[0]
        for r in records:
            if r.collected_at_epoch <= alvo:
                base = r
            else:
                break
        actual_s = tip_epoch - base.collected_at_epoch
        snaps_no_intervalo = sum(
            1 for r in records
            if base.collected_at_epoch <= r.collected_at_epoch
            <= tip_epoch
        )
        corners = _delta_cumulative(
            base.snapshot, tip.snapshot, "Corner Kicks", "escanteios",
            actual_s,
        )
        shots = _delta_cumulative(
            base.snapshot, tip.snapshot, "Total Shots", "finalizacoes",
            actual_s,
        )
        sot = _delta_cumulative(
            base.snapshot, tip.snapshot, "Shots on Goal", "chutes_no_alvo",
            actual_s,
        )
        blocked = _delta_cumulative(
            base.snapshot, tip.snapshot, "Blocked Shots", "bloqueadas",
            actual_s,
        )
        poss = _possession_delta(base.snapshot, tip.snapshot)
        inconsistencia = any(
            d.inconsistencia_fonte
            for d in (corners, shots, sot, blocked)
            if d is not None
        )
        out.append(PressureWindow(
            janela_min=w,
            avaliable=True,
            motivo=None,
            base_collected_at=base.collected_at_text,
            tip_collected_at=tip.collected_at_text,
            actual_window_s=actual_s,
            snaps_usados=snaps_no_intervalo,
            inconsistencia_fonte=inconsistencia,
            corners=corners,
            shots=shots,
            shots_on_goal=sot,
            blocked_shots=blocked,
            possession_change=poss,
            status=PRESSURE_STATUS,
        ))
    return out


def query_pressure(
    fixture_id: int, *, db_path: str | None = None
) -> list[PressureWindow]:
    """Consulta as janelas 5/10/15 do historico gravado do fixture."""
    hist = LiveSnapshotHistory(db_path=db_path)
    return compute_pressure_windows(hist.records(fixture_id))


# ----------------------------------------------------------------------
# Coletor automatico (FASE C)
# ----------------------------------------------------------------------
def collect_live_series(
    client: Any,
    fixture_id: int,
    *,
    interval: int = COLETA_INTERVALO_PADRAO,
    max_iterations: int | None = None,
    db_path: str | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    seed: bool = True,
) -> dict[str, Any]:
    """Coleta snapshots LIVE a cada `interval` segundos ate a partida
    encerrar (FT/AET/PEN) ou o operador interromper (Ctrl+C).

    - `interval` padrao 60s (configuravel tecnicamente).
    - `max_iterations` limita o numero de coletas (testes: None em
      producao; inteiro nos testes para nao esperar 60s reais).
    - `sleep_fn` injetavel (testes usam no-op; producao usa time.sleep).
    - `seed=True` incorpora o snapshot atual existente como primeiro
      registro historico (sem inventar timestamp/dado) antes de coletar.
    - Cada append e sua propria transacao confirmada => Ctrl+C nao
      corrompe o banco (nao ha transacao multi-linha aberta).
    - Para ao detectar partida encerrada (UserFacingError de
      fetch_live_snapshot com status FT/AET/PEN, ou snapshot.status em
      STATUS_ENCERRADO). Nao coleta indefinidamente partida encerrada.
    - `record=True` em fetch_live_snapshot mantem o cache `live_snapshots`
      (ultimo snapshot) intacto para o fluxo existente (compatibilidade).

    Retorna dict: {coletas, motivo, fixture_id, intervalo}.
    """
    history = LiveSnapshotHistory(db_path=db_path)
    if seed:
        try:
            history.seed_from_latest(fixture_id)
        except sqlite3.Error:
            # nao inventa; segue coletando se o cache nao estiver disponivel
            pass

    coletas = 0
    motivo = "interrompido"
    try:
        while True:
            if max_iterations is not None and coletas >= max_iterations:
                motivo = "limite_iteracoes"
                break
            try:
                snap = fetch_live_snapshot(
                    client, fixture_id, refresh=True, record=True
                )
            except UserFacingError as exc:
                texto = str(exc).lower()
                if "encerrado" in texto or any(
                    s.lower() in texto for s in STATUS_ENCERRADO
                ):
                    motivo = "partida_encerrada"
                    break
                # status nao-live mas nao encerrado (NS/PST/...): para
                # a coleta sem corromper; motivo tecnico
                motivo = f"parada: {exc}"
                break
            history.append(snap)
            coletas += 1
            if snap.status in STATUS_ENCERRADO:
                motivo = "partida_encerrada"
                break
            sleep_fn(interval)
    except KeyboardInterrupt:
        # Ctrl+C: banco ja consistente (cada append confirmado). Sai limpo.
        motivo = "interrompido_operador"

    return {
        "coletas": coletas,
        "motivo": motivo,
        "fixture_id": fixture_id,
        "intervalo": interval,
    }


# ----------------------------------------------------------------------
# Formato CLI (FATO + CALCULO, sem INTERPRETACAO)
# ----------------------------------------------------------------------
def format_pressure_windows(
    windows: list[PressureWindow], *, snap: LiveSnapshot | None = None
) -> str:
    """Relatorio das janelas 5/10/15: somente dados objetivos + status
    EXPERIMENTAL. Sem threshold BAIXA/MODERADA/ALTA, sem palpite."""

    def _side(d: StatDelta | None) -> str:
        if d is None:
            return "nd"
        if d.inconsistencia_fonte or d.home is None:
            return "NAO AVALIAVEL"
        return f"{d.home:g}/{d.away:g}/{d.total:g}"

    def _rate(d: StatDelta | None, lado: str) -> str:
        if d is None:
            return "nd"
        v = {"home": d.rate_per_min_home, "away": d.rate_per_min_away,
             "total": d.rate_per_min_total}[lado]
        return "NAO AVALIAVEL" if v is None else f"{v:g}/min"

    linhas: list[str] = []
    if snap is not None:
        linhas.append(
            f"[FATO] {snap.home_team_name} x {snap.away_team_name} "
            f"- minuto {snap.elapsed if snap.elapsed is not None else 'nd'} "
            f"({snap.status}) - placar "
            f"{snap.goals_home if snap.goals_home is not None else 'nd'}-"
            f"{snap.goals_away if snap.goals_away is not None else 'nd'}"
        )
    linhas.append(
        "[CALCULO] PRESSAO TEMPORAL LIVE (5/10/15 min) - "
        f"status {PRESSURE_STATUS}"
    )
    for w in windows:
        if not w.avaliable:
            linhas.append(
                f"  PRESSAO {w.janela_min} MIN: {NAO_AVALIAVEL_HISTORICO} "
                f"({w.motivo or ''})"
            )
            continue
        actual = (w.actual_window_s or 0.0) / 60.0
        linhas.append(
            f"  PRESSAO {w.janela_min} MIN (janela real {actual:.1f} min, "
            f"{w.snaps_usados} snapshots, {w.base_collected_at} -> "
            f"{w.tip_collected_at}):"
        )
        for rotulo, d in (
            ("escanteios", w.corners),
            ("finalizacoes", w.shots),
            ("chutes no alvo", w.shots_on_goal),
            ("bloqueadas", w.blocked_shots),
        ):
            if d is None:
                continue
            linhas.append(
                f"    {rotulo}: delta casa/fora/total = {_side(d)} | "
                f"ritmo casa {_rate(d, 'home')}, fora {_rate(d, 'away')}, "
                f"total {_rate(d, 'total')}"
            )
            if d.motivo:
                linhas.append(f"      -> {d.motivo}")
        if w.possession_change is not None:
            pc = w.possession_change
            if pc.home_change is None:
                linhas.append("    posse: NAO DISPONIVEL " +
                              (pc.motivo or ""))
            else:
                linhas.append(
                    f"    posse: variacao casa {pc.home_change:+g} p.p., "
                    f"fora {pc.away_change:+g} p.p."
                )
        if w.inconsistencia_fonte:
            linhas.append(
                "    [INCONSISTENCIA] correcao da fonte detectada em ao "
                "menos uma stat cumulativa - metrica(s) afetada(s) NAO "
                "AVALIAVEIS; ambos os snapshots preservados no historico"
            )
    linhas.append(
        f"  STATUS: {PRESSURE_STATUS} - dados objetivos temporais; "
        "nenhum threshold operacional validado; nao usar para forcar aposta"
    )
    return "\n".join(linhas)