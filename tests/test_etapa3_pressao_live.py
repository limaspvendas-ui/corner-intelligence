"""ETAPA 3 - SERIE TEMPORAL IMUTAVEL DE SNAPSHOTS LIVE + PRESSAO 5/10/15.

Suite da Etapa 3 (FATO + CALCULO). Cobertura:
  1. multiplos snapshots para mesmo fixture;
  2. ordem cronologica;
  3. snapshot antigo nao sobrescrito;
  4. obtencao do snapshot mais recente;
  5. janela 5 minutos;
  6. janela 10 minutos;
  7. janela 15 minutos;
  8. historico insuficiente;
  9. None nao vira zero;
 10. delta de escanteios;
 11. delta de shots;
 12. delta de SOT;
 13. delta de blocked shots;
 14. posse ausente;
 15. intervalo irregular entre snapshots;
 16. correcao da API / delta negativo;
 17. dois fixtures simultaneos nao se misturam;
 18. encerramento de partida;
 19. persistencia apos reabrir banco;
 20. compatibilidade com fluxo LIVE existente.

Regras: banco temporario (tmp_path) em todos os testes; nunca o banco
operacional. Testes nao esperam 60s reais (sleep_fn injetado / epochs
sinteticos). None nunca vira zero.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from src.exceptions import UserFacingError
from src.live import LiveSnapshot, LiveSnapshotStore, now_brt
from src.live_opportunity import Candidato, deep_dive
from src.live_pressure import (
    NAO_AVALIAVEL_HISTORICO,
    PRESSURE_STATUS,
    PRESSURE_WINDOWS,
    HistoryRecord,
    LiveSnapshotHistory,
    collect_live_series,
    compute_pressure_windows,
)

FID = 9001
HOME_ID, HOME = 131, "Corinthians"
AWAY_ID, AWAY = 132, "Chapecoense-sc"


# ----------------------------------------------------------------------
# Builders
# ----------------------------------------------------------------------
def _snap(
    *,
    fixture_id: int = FID,
    elapsed: int = 50,
    status: str = "2H",
    gh: int = 1,
    ga: int = 1,
    corners_h: int | None = 5,
    corners_a: int | None = 3,
    shots_h: int | None = 10,
    shots_a: int | None = 7,
    sot_h: int | None = 3,
    sot_a: int | None = 2,
    blocked_h: int | None = 2,
    blocked_a: int | None = 1,
    posse_h: str | None = "58%",
    posse_a: str | None = "42%",
    collected_at: str = "",
) -> LiveSnapshot:
    def _put(d: dict[str, Any], k: str, v: Any) -> None:
        if v is not None:
            d[k] = v

    sh: dict[str, Any] = {}
    sa: dict[str, Any] = {}
    _put(sh, "Corner Kicks", corners_h)
    _put(sa, "Corner Kicks", corners_a)
    _put(sh, "Total Shots", shots_h)
    _put(sa, "Total Shots", shots_a)
    _put(sh, "Shots on Goal", sot_h)
    _put(sa, "Shots on Goal", sot_a)
    _put(sh, "Blocked Shots", blocked_h)
    _put(sa, "Blocked Shots", blocked_a)
    _put(sh, "Ball Possession", posse_h)
    _put(sa, "Ball Possession", posse_a)
    return LiveSnapshot(
        fixture_id=fixture_id, league_name="Serie A", country="Brazil",
        season=2026, round="Regular Season - 26",
        date_local="2026-09-06T19:30:00-03:00",
        home_team_id=HOME_ID, home_team_name=HOME,
        away_team_id=AWAY_ID, away_team_name=AWAY,
        goals_home=gh, goals_away=ga, halftime_home=0, halftime_away=1,
        status=status, elapsed=elapsed, league_id=71,
        stats_home=sh, stats_away=sa, stats_1h={}, stats_2h={},
        events=[], collected_at=collected_at, has_stats=True,
    )


def _rec(
    epoch: float,
    snap: LiveSnapshot,
    *,
    seq: int = 1,
    texto: str = "t",
) -> HistoryRecord:
    return HistoryRecord(
        fixture_id=snap.fixture_id, seq=seq,
        collected_at_epoch=float(epoch), collected_at_text=texto,
        snapshot=snap,
    )


def _hist_db(tmp_path):
    return LiveSnapshotHistory(db_path=str(tmp_path / "hist.db"))


# ----------------------------------------------------------------------
# 1. multiplos snapshots para mesmo fixture
# ----------------------------------------------------------------------
def test_1_multiplos_snapshots_mesmo_fixture(tmp_path):
    h = _hist_db(tmp_path)
    for i in range(3):
        h.append(_snap(elapsed=50 + i), collected_at_epoch=1000.0 * (i + 1))
    assert h.count(FID) == 3


# ----------------------------------------------------------------------
# 2. ordem cronologica
# ----------------------------------------------------------------------
def test_2_ordem_cronologica(tmp_path):
    h = _hist_db(tmp_path)
    # inserir fora de ordem: o records() deve ordenar por epoch
    h.append(_snap(elapsed=60), collected_at_epoch=2000.0, collected_at_text="b")
    h.append(_snap(elapsed=50), collected_at_epoch=1000.0, collected_at_text="a")
    h.append(_snap(elapsed=70), collected_at_epoch=3000.0, collected_at_text="c")
    regs = h.records(FID)
    assert [r.collected_at_epoch for r in regs] == [1000.0, 2000.0, 3000.0]
    assert [r.snapshot.elapsed for r in regs] == [50, 60, 70]


# ----------------------------------------------------------------------
# 3. snapshot antigo nao sobrescrito
# ----------------------------------------------------------------------
def test_3_snapshot_antigo_nao_sobrescrito(tmp_path):
    h = _hist_db(tmp_path)
    h.append(_snap(elapsed=50, corners_h=5), collected_at_epoch=1000.0)
    h.append(_snap(elapsed=55, corners_h=6), collected_at_epoch=2000.0)
    h.append(_snap(elapsed=60, corners_h=7), collected_at_epoch=3000.0)
    regs = h.records(FID)
    assert len(regs) == 3  # nada foi sobrescrito
    # o primeiro registro mantem os dados originais
    assert regs[0].snapshot.elapsed == 50
    assert regs[0].snapshot.stats_home["Corner Kicks"] == 5
    assert regs[2].snapshot.stats_home["Corner Kicks"] == 7


# ----------------------------------------------------------------------
# 4. obtencao do snapshot mais recente
# ----------------------------------------------------------------------
def test_4_snapshot_mais_recente(tmp_path):
    h = _hist_db(tmp_path)
    h.append(_snap(elapsed=50), collected_at_epoch=1000.0)
    h.append(_snap(elapsed=60), collected_at_epoch=5000.0)
    h.append(_snap(elapsed=55), collected_at_epoch=3000.0)
    latest = h.latest(FID)
    assert latest is not None
    assert latest.collected_at_epoch == 5000.0
    assert latest.snapshot.elapsed == 60


# ----------------------------------------------------------------------
# 5/6/7. janelas 5/10/15 minutos
# ----------------------------------------------------------------------
def _series(span_min: int, step_min: int = 1, *, fixture_id: int = FID):
    """Serie de `span_min` minutos, snapshots a cada `step_min` min,
    com escanteios crescentes 1 por minuto."""
    regs: list[HistoryRecord] = []
    n = int(span_min // step_min) + 1
    for i in range(n):
        epoch = i * step_min * 60.0
        snap = _snap(
            fixture_id=fixture_id, elapsed=50 + i,
            corners_h=10 + i, corners_a=5 + i,
            shots_h=20 + i, shots_a=15 + i,
            sot_h=4 + i, sot_a=3 + i,
            blocked_h=2 + i, blocked_a=1 + i,
            posse_h=f"{55 + (i % 3)}%", posse_a=f"{45 - (i % 3)}%",
        )
        regs.append(_rec(epoch, snap, seq=i + 1))
    return regs


def test_5_janela_5_minutos():
    regs = _series(span_min=6)  # cobre >= 5 min (epochs 0..360)
    wins = compute_pressure_windows(regs)
    w5 = next(w for w in wins if w.janela_min == 5)
    assert w5.avaliable is True
    # tip em 360s; alvo = 360-300 = 60; base = epoch 60 (i=1)
    # corners_h: 11 -> 16 = 5 ; corners_a: 6 -> 11 = 5 ; total 10
    assert w5.corners.home == 5.0
    assert w5.corners.away == 5.0
    assert w5.corners.total == 10.0
    assert w5.actual_window_s == pytest.approx(300.0)
    assert w5.corners.rate_per_min_total == pytest.approx(10.0 / 5.0)


def test_6_janela_10_minutos():
    regs = _series(span_min=11)  # epochs 0..660
    wins = compute_pressure_windows(regs)
    w10 = next(w for w in wins if w.janela_min == 10)
    assert w10.avaliable is True
    # tip 660; alvo = 660-600 = 60; base epoch 60; janela real 600s
    assert w10.actual_window_s == pytest.approx(600.0)


def test_7_janela_15_minutos():
    regs = _series(span_min=16)  # epochs 0..960
    wins = compute_pressure_windows(regs)
    w15 = next(w for w in wins if w.janela_min == 15)
    assert w15.avaliable is True
    # tip 960; alvo = 960-900 = 60; base epoch 60; janela real 900s
    assert w15.actual_window_s == pytest.approx(900.0)


# ----------------------------------------------------------------------
# 8. historico insuficiente
# ----------------------------------------------------------------------
def test_8_historico_insuficiente():
    # 7 minutos de monitoramento: 5 avaliable, 10/15 NAO AVALIAVEL
    regs = _series(span_min=7)
    wins = compute_pressure_windows(regs)
    w5 = next(w for w in wins if w.janela_min == 5)
    w10 = next(w for w in wins if w.janela_min == 10)
    w15 = next(w for w in wins if w.janela_min == 15)
    assert w5.avaliable is True
    assert w10.avaliable is False
    assert w10.motivo == NAO_AVALIAVEL_HISTORICO
    assert w15.avaliable is False
    assert w15.motivo == NAO_AVALIAVEL_HISTORICO


def test_8b_menos_que_dois_snapshots_tudo_nao_avalivel():
    wins = compute_pressure_windows([_rec(0.0, _snap())])
    assert len(wins) == len(PRESSURE_WINDOWS)
    assert all(not w.avaliable for w in wins)
    assert all(w.motivo == NAO_AVALIAVEL_HISTORICO for w in wins)


# ----------------------------------------------------------------------
# 9. None nao vira zero
# ----------------------------------------------------------------------
def test_9_none_nao_vira_zero():
    base = _snap(corners_h=5, corners_a=3, shots_h=None, shots_a=None)
    tip = _snap(elapsed=55, corners_h=6, corners_a=4, shots_h=None, shots_a=None)
    regs = [_rec(0.0, base, seq=1), _rec(300.0, tip, seq=2)]
    w = compute_pressure_windows(regs)[0]
    assert w.avaliable is True
    # shots ausentes em ambas: delta None (nunca 0)
    assert w.shots.home is None
    assert w.shots.away is None
    assert w.shots.total is None
    assert w.shots.motivo is not None
    # corners presentes: delta calculado
    assert w.corners.total == 2.0


# ----------------------------------------------------------------------
# 10/11/12/13. deltas por stat
# ----------------------------------------------------------------------
def test_10_delta_escanteios():
    base = _snap(corners_h=5, corners_a=3)
    tip = _snap(elapsed=55, corners_h=8, corners_a=5)
    w = compute_pressure_windows([_rec(0.0, base, seq=1),
                                  _rec(300.0, tip, seq=2)])[0]
    assert w.corners.home == 3.0
    assert w.corners.away == 2.0
    assert w.corners.total == 5.0
    assert w.corners.rate_per_min_total == pytest.approx(5.0 / 5.0)


def test_11_delta_shots():
    base = _snap(shots_h=20, shots_a=15)
    tip = _snap(elapsed=55, shots_h=26, shots_a=18)
    w = compute_pressure_windows([_rec(0.0, base, seq=1),
                                  _rec(300.0, tip, seq=2)])[0]
    assert w.shots.home == 6.0
    assert w.shots.away == 3.0
    assert w.shots.total == 9.0


def test_12_delta_sot():
    base = _snap(sot_h=4, sot_a=3)
    tip = _snap(elapsed=55, sot_h=7, sot_a=4)
    w = compute_pressure_windows([_rec(0.0, base, seq=1),
                                  _rec(300.0, tip, seq=2)])[0]
    assert w.shots_on_goal.home == 3.0
    assert w.shots_on_goal.away == 1.0
    assert w.shots_on_goal.total == 4.0


def test_13_delta_blocked_shots():
    base = _snap(blocked_h=2, blocked_a=1)
    tip = _snap(elapsed=55, blocked_h=5, blocked_a=3)
    w = compute_pressure_windows([_rec(0.0, base, seq=1),
                                  _rec(300.0, tip, seq=2)])[0]
    assert w.blocked_shots.home == 3.0
    assert w.blocked_shots.away == 2.0
    assert w.blocked_shots.total == 5.0


# ----------------------------------------------------------------------
# 14. posse ausente
# ----------------------------------------------------------------------
def test_14_posse_ausente():
    base = _snap(posse_h="58%", posse_a="42%")
    tip = _snap(elapsed=55, posse_h=None, posse_a=None)  # posse sumiu
    w = compute_pressure_windows([_rec(0.0, base, seq=1),
                                  _rec(300.0, tip, seq=2)])[0]
    assert w.possession_change is not None
    assert w.possession_change.home_change is None
    assert w.possession_change.motivo is not None  # NAO DISPONIVEL


def test_14b_posse_presente_variacao_sinalizada():
    base = _snap(posse_h="58%", posse_a="42%")
    tip = _snap(elapsed=55, posse_h="50%", posse_a="50%")
    w = compute_pressure_windows([_rec(0.0, base, seq=1),
                                  _rec(300.0, tip, seq=2)])[0]
    assert w.possession_change.home_change == -8.0
    assert w.possession_change.away_change == 8.0  # sinal legitimo


# ----------------------------------------------------------------------
# 15. intervalo irregular entre snapshots
# ----------------------------------------------------------------------
def test_15_intervalo_irregular():
    # snapshots em epochs 0, 150, 500 (intervalos irregulares)
    regs = [
        _rec(0.0, _snap(elapsed=50, corners_h=1, corners_a=0), seq=1),
        _rec(150.0, _snap(elapsed=52, corners_h=3, corners_a=1), seq=2),
        _rec(500.0, _snap(elapsed=58, corners_h=6, corners_a=2), seq=3),
    ]
    wins = compute_pressure_windows(regs)
    w5 = next(w for w in wins if w.janela_min == 5)
    assert w5.avaliable is True
    # alvo = 500 - 300 = 200; base = maior epoch <= 200 = 150
    assert w5.actual_window_s == pytest.approx(350.0)  # 500 - 150
    # rate usa a janela REAL (350s), nao W*60=300s
    # base epoch 150 (corners 3/1), tip epoch 500 (corners 6/2): total 4
    assert w5.corners.rate_per_min_total == pytest.approx(
        4.0 / (350.0 / 60.0)
    )
    # 10/15 min: span=500 < 600/900 -> NAO AVALIAVEL
    assert next(w for w in wins if w.janela_min == 10).avaliable is False
    assert next(w for w in wins if w.janela_min == 15).avaliable is False


# ----------------------------------------------------------------------
# 16. correcao da API / delta negativo
# ----------------------------------------------------------------------
def test_16_correcao_api_delta_negativo():
    # snapshot anterior: 5 escanteios; posterior: 4 (a API corrigiu)
    base = _snap(corners_h=5, corners_a=3)
    tip = _snap(elapsed=55, corners_h=4, corners_a=3)  # 5 -> 4 (delta -1)
    regs = [_rec(0.0, base, seq=1), _rec(300.0, tip, seq=2)]
    w = compute_pressure_windows(regs)[0]
    assert w.avaliable is True
    assert w.inconsistencia_fonte is True
    # metrica afetada NAO AVALIAVEL (sem abs, sem zero, sem esconder)
    assert w.corners.home is None
    assert w.corners.total is None
    assert w.corners.rate_per_min_total is None
    assert w.corners.inconsistencia_fonte is True
    assert "correcao da fonte" in (w.corners.motivo or "")
    assert "delta negativo" in (w.corners.motivo or "")
    # ambos snapshots preservados no historico (imutavel)
    assert regs[0].snapshot.stats_home["Corner Kicks"] == 5
    assert regs[1].snapshot.stats_home["Corner Kicks"] == 4


# ----------------------------------------------------------------------
# 17. dois fixtures simultaneos nao se misturam
# ----------------------------------------------------------------------
def test_17_dois_fixtures_nao_se_misturam(tmp_path):
    h = _hist_db(tmp_path)
    fa, fb = 8001, 8002
    h.append(_snap(fixture_id=fa, elapsed=50), collected_at_epoch=1000.0)
    h.append(_snap(fixture_id=fb, elapsed=40), collected_at_epoch=1100.0)
    h.append(_snap(fixture_id=fa, elapsed=55), collected_at_epoch=2000.0)
    h.append(_snap(fixture_id=fb, elapsed=45), collected_at_epoch=2100.0)
    assert h.count(fa) == 2
    assert h.count(fb) == 2
    assert [r.fixture_id for r in h.records(fa)] == [fa, fa]
    assert [r.fixture_id for r in h.records(fb)] == [fb, fb]
    # pressao de um fixture nao mistura com o outro
    wa = compute_pressure_windows(h.records(fa))
    assert all(getattr(w, "janela_min", None) for w in wa)
    assert all(r.fixture_id == fa for r in h.records(fa))


# ----------------------------------------------------------------------
# 18. encerramento de partida (coletor para sozinho)
# ----------------------------------------------------------------------
class _ColetaClient:
    """Client falso: retorna N leituras 2H e entao FT (encerrado)."""

    def __init__(self, live_count: int):
        self.live_count = live_count
        self.calls = 0

    def get(self, endpoint, params=None, use_cache=True, ttl=None):
        if endpoint == "/fixtures":
            self.calls += 1
            if self.calls <= self.live_count:
                return [{
                    "fixture": {"id": FID, "date": "2026-09-06T19:30:00-03:00",
                                "status": {"short": "2H",
                                           "elapsed": 50 + self.calls}},
                    "league": {"id": 71, "name": "Serie A", "country": "Brazil",
                               "round": "R - 26", "season": 2026},
                    "teams": {"home": {"id": HOME_ID, "name": HOME},
                              "away": {"id": AWAY_ID, "name": AWAY}},
                    "goals": {"home": 1, "away": 1},
                    "score": {"halftime": {"home": 0, "away": 1},
                              "fulltime": {"home": None, "away": None}},
                }]
            # partida encerrada: status FT
            return [{
                "fixture": {"id": FID, "date": "2026-09-06T19:30:00-03:00",
                            "status": {"short": "FT", "elapsed": 90}},
                "league": {"id": 71, "name": "Serie A", "country": "Brazil",
                           "round": "R - 26", "season": 2026},
                "teams": {"home": {"id": HOME_ID, "name": HOME},
                          "away": {"id": AWAY_ID, "name": AWAY}},
                "goals": {"home": 2, "away": 1},
                "score": {"halftime": {"home": 0, "away": 1},
                          "fulltime": {"home": 2, "away": 1}},
            }]
        if endpoint == "/teams":
            tid = (params or {}).get("id")
            names = {HOME_ID: HOME, AWAY_ID: AWAY}
            return [{"team": {"id": tid, "name": names.get(tid, ""),
                              "country": "Brazil"}}]
        if endpoint == "/fixtures/statistics":
            return [
                {"team": {"id": HOME_ID, "name": HOME},
                 "statistics": [{"type": "Corner Kicks",
                                 "value": 5 + self.calls}]},
                {"team": {"id": AWAY_ID, "name": AWAY},
                 "statistics": [{"type": "Corner Kicks", "value": 3}]},
            ]
        if endpoint == "/fixtures/events":
            return []
        return []


def test_18_encerramento_de_partida(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    monkeypatch.setattr("src.live.DB_PATH", str(tmp_path / "live.db"))
    monkeypatch.setattr("src.live_pressure.DB_PATH", str(tmp_path / "hist.db"))
    client = _ColetaClient(live_count=3)  # 3 leituras live, depois FT
    res = collect_live_series(
        client, FID, interval=60, max_iterations=None,
        sleep_fn=lambda _s: None, seed=False,
    )
    assert res["motivo"] == "partida_encerrada"
    assert res["coletas"] == 3  # nao coletou o FT, nao coletou indefinidamente
    h = LiveSnapshotHistory(db_path=str(tmp_path / "hist.db"))
    assert h.count(FID) == 3
    # nenhum snapshot de FT foi gravado
    assert all(r.snapshot.status == "2H" for r in h.records(FID))


def test_18b_ctrl_c_nao_corrompe_banco(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    monkeypatch.setattr("src.live.DB_PATH", str(tmp_path / "live.db"))
    monkeypatch.setattr("src.live_pressure.DB_PATH", str(tmp_path / "hist.db"))
    client = _ColetaClient(live_count=10)

    def boom(_s: float) -> None:
        raise KeyboardInterrupt

    res = collect_live_series(
        client, FID, interval=60, sleep_fn=boom, seed=False,
    )
    assert res["motivo"] == "interrompido_operador"
    assert res["coletas"] == 1  # uma coleta antes do sleep disparar
    # banco consistente: a coleta gravada permanece
    h = LiveSnapshotHistory(db_path=str(tmp_path / "hist.db"))
    assert h.count(FID) == 1


# ----------------------------------------------------------------------
# 19. persistencia apos reabrir banco
# ----------------------------------------------------------------------
def test_19_persistencia_apos_reabrir_banco(tmp_path):
    path = str(tmp_path / "hist.db")
    h = _hist_db(tmp_path)
    for i in range(4):
        h.append(_snap(elapsed=50 + i), collected_at_epoch=1000.0 * (i + 1))
    # reabre o mesmo banco (nova instancia)
    h2 = LiveSnapshotHistory(db_path=path)
    assert h2.count(FID) == 4
    regs = h2.records(FID)
    assert [r.snapshot.elapsed for r in regs] == [50, 51, 52, 53]
    # imutabilidade persiste: dados antigos intactos
    assert regs[0].snapshot.stats_home["Corner Kicks"] == 5


# ----------------------------------------------------------------------
# 20. compatibilidade com fluxo LIVE existente
# ----------------------------------------------------------------------
def test_20_compatibilidade_fluxo_live_existente(monkeypatch, tmp_path):
    # Em producao cache (live_snapshots) e serie temporal compartilham o
    # MESMO DB_PATH; o teste espelha isso num unico arquivo temporario.
    db = str(tmp_path / "ci.db")
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    # (a) LiveSnapshotStore (tabela unica, INSERT OR REPLACE) intacta
    store = LiveSnapshotStore(db_path=db)
    s1 = _snap(elapsed=50, corners_h=5)
    s2 = _snap(elapsed=55, corners_h=6)
    store.save(s1)
    store.save(s2)
    assert store.get(FID).elapsed == 55  # ultimo vence (INSERT OR REPLACE)
    with store._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM live_snapshots WHERE fixture_id = ?",
            (FID,),
        ).fetchone()[0]
    assert n == 1  # tabela unica preservada

    # (b) serie temporal coexiste no mesmo banco sem quebrar o cache;
    # seed incorpora o snapshot atual como 1o registro historico SEM
    # inventar timestamp (usa o epoch real updated_at do cache)
    h = LiveSnapshotHistory(db_path=db)
    seeded = h.seed_from_latest(FID)
    assert seeded is not None
    assert h.count(FID) == 1
    raw = store._raw_row(FID)
    assert raw is not None
    assert seeded.collected_at_epoch == pytest.approx(raw[2])
    assert seeded.snapshot.elapsed == 55  # dado real, nao inventado

    # (c) deep_dive aceita o param pressao; default False => pressao None
    cand = Candidato(snapshot=_snap())
    assert cand.pressao is None
    import inspect
    sig = inspect.signature(deep_dive)
    assert "pressao" in sig.parameters
    assert sig.parameters["pressao"].default is False

    # (d) query_pressure respeita db_path isolado e traz status EXPERIMENTAL
    from src.live_pressure import query_pressure
    h.append(_snap(elapsed=60, corners_h=7), collected_at_epoch=2000.0)
    wins = query_pressure(FID, db_path=db)
    assert len(wins) == len(PRESSURE_WINDOWS)
    assert all(w.status == PRESSURE_STATUS for w in wins)