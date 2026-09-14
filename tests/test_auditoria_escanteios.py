"""Testes do modulo de auditoria de escanteios (Etapa 5C).

Garante que o modulo e SOMENTE LEITURA: nao altera regras, reproduz o achado,
fatiamento temporal correto, settlement revalidado, parsing de linha, e
determinismo. Nao toca no motor.
"""
from __future__ import annotations

import json
import sqlite3

from src.auditoria_escanteios import (
    _parse_linha,
    _settle,
    auditar,
    _concentracao_fixture,
    _over_vs_under,
)
from src.backtest import VERSAO_BACKTEST_ENGINE
from src.prejogo_opportunity import VERSAO_PREJOGO_OP
from src.live_opportunity import PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1

from tests.test_backtest_validacao import _build_mini_db, _corpus


def test_parse_linha():
    assert _parse_linha("Over 4.5 escanteios (total do jogo)") == ("over", 4.5)
    assert _parse_linha("Under 13.5 escanteios (total do jogo)") == ("under", 13.5)
    assert _parse_linha("sem sentido") == (None, None)


def test_settle_over_under():
    assert _settle("Over 4.5 escanteios (total do jogo)", 6.0) == "GANHA"
    assert _settle("Over 4.5 escanteios (total do jogo)", 4.0) == "PERDIDA"
    assert _settle("Under 13.5 escanteios (total do jogo)", 9.0) == "GANHA"
    assert _settle("Under 13.5 escanteios (total do jogo)", 14.0) == "PERDIDA"
    assert _settle("Over 4.5 escanteios (total do jogo)", None) == "NÃO AVALIÁVEL"


def test_over_vs_under_separados():
    corn = [p for p in _corpus() if p["mercado"] == "escanteios"]
    ov = _over_vs_under(corn)
    assert "over" in ov or "under" in ov
    # nao mistura os dois numa unica taxa
    assert set(ov).issubset({"over", "under"})


def test_concentracao_fixture_soma_perdas():
    corn = [p for p in _corpus() if p["mercado"] == "escanteios"]
    c = _concentracao_fixture(corn)
    assert c["total_perdas"] == sum(1 for p in corn if p["resultado_final"] == "PERDIDA")
    # fixtures com 1 ou 2 entrar cobrem o total
    assert c["fx_com_1_entrar"] + c["fx_com_2_entrar"] + c["fx_com_3plus_entrar"] == \
        c["fixtures_com_entrar"]


def test_auditoria_reproduz_e_nao_altera_regras(tmp_path):
    # constantes congeladas antes
    antes = (PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1,
             VERSAO_PREJOGO_OP, VERSAO_BACKTEST_ENGINE)
    db = _build_mini_db(_corpus(), str(tmp_path / "a.db"))
    res = auditar(db, "test", blocos=3)
    depois = (PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1,
              VERSAO_PREJOGO_OP, VERSAO_BACKTEST_ENGINE)
    assert antes == depois  # read-only: nada mudou
    # reproduz contagens do corpus de escanteios
    n_ant = res["reproducao"]["anterior"]["entrar"]
    n_pos = res["reproducao"]["posterior"]["entrar"]
    assert n_ant + n_pos == sum(1 for p in _corpus()
                                if p["mercado"] == "escanteios" and p["decisao"] == "ENTRAR")
    # settlement revalidado
    assert res["settlement_revalidate"]["settlement_ok"] is True


def test_auditoria_determinista(tmp_path):
    db = _build_mini_db(_corpus(), str(tmp_path / "a.db"))
    r1 = auditar(db, "test", blocos=3)
    r2 = auditar(db, "test", blocos=3)
    assert json.dumps(r1, sort_keys=True, default=str) == \
        json.dumps(r2, sort_keys=True, default=str)


def test_auditoria_somente_leitura_db(tmp_path):
    """Rodar a auditoria nao escreve no DB nem altera previsoes."""
    db = _build_mini_db(_corpus(), str(tmp_path / "a.db"))
    conn = sqlite3.connect(db)
    n_antes = conn.execute("SELECT COUNT(*) FROM bt_predictions").fetchone()[0]
    conn.close()
    # snapshot completo antes
    conn = sqlite3.connect(db)
    snap_antes = conn.execute(
        "SELECT id, decisao, prob, resultado_final FROM bt_predictions ORDER BY id"
    ).fetchall()
    conn.close()
    auditar(db, "test", blocos=3)
    conn = sqlite3.connect(db)
    n_depois = conn.execute("SELECT COUNT(*) FROM bt_predictions").fetchone()[0]
    snap_depois = conn.execute(
        "SELECT id, decisao, prob, resultado_final FROM bt_predictions ORDER BY id"
    ).fetchall()
    conn.close()
    assert n_antes == n_depois  # nada inserido/removido
    # previsoes intactas: cada linha (id, decisao, prob, resultado) inalterada
    assert snap_antes == snap_depois