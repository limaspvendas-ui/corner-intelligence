"""Testes do checkpoint de prontidao para calibracao (Etapa 5E).

Somente leitura: NAO altera DB, NAO altera regras, NAO recalibra.
Valida: classificacao por mercado, matriz de prontidao, status ROI/pressao
data-driven, protecao contra overfitting, determinismo, respostas obrigatorias.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.backtest import BACKTEST_DB_PATH
from src.checkpoint_calibracao import (
    DRIFT_MATERIAL_PP,
    auditar,
)
from src.config import DB_PATH


@pytest.fixture(scope="module")
def chk():
    return auditar(BACKTEST_DB_PATH, DB_PATH)


# ----------------------------------------------------------------------
# Read-only / determinismo
# ----------------------------------------------------------------------
def test_chk_nao_altera_db():
    bt_antes = sqlite3.connect(BACKTEST_DB_PATH).execute(
        "SELECT COUNT(*) FROM bt_predictions").fetchone()[0]
    api_antes = sqlite3.connect(DB_PATH).execute(
        "SELECT COUNT(*) FROM api_cache").fetchone()[0]
    auditar(BACKTEST_DB_PATH, DB_PATH)
    bt_depois = sqlite3.connect(BACKTEST_DB_PATH).execute(
        "SELECT COUNT(*) FROM bt_predictions").fetchone()[0]
    api_depois = sqlite3.connect(DB_PATH).execute(
        "SELECT COUNT(*) FROM api_cache").fetchone()[0]
    assert bt_antes == bt_depois
    assert api_antes == api_depois


def test_chk_deterministico():
    a = auditar(BACKTEST_DB_PATH, DB_PATH)
    b = auditar(BACKTEST_DB_PATH, DB_PATH)
    assert a["respostas_obrigatorias"] == b["respostas_obrigatorias"]
    assert a["matriz_prontidao"] == b["matriz_prontidao"]
    assert a["por_mercado"]["goals"]["drift_pp"] == b["por_mercado"]["goals"]["drift_pp"]


def test_chk_json_serializavel(tmp_path):
    r = auditar(BACKTEST_DB_PATH, DB_PATH)
    p = tmp_path / "chk.json"
    p.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    r2 = json.loads(p.read_text(encoding="utf-8"))
    assert r2["respostas_obrigatorias"]["etapa6_pode_ser_iniciada"] == "NAO"


# ----------------------------------------------------------------------
# Metricas por mercado (reproduz 5B)
# ----------------------------------------------------------------------
def test_goals_estavel_sem_na(chk):
    g = chk["por_mercado"]["goals"]
    assert g["entrar_total"] == 2918
    assert g["nao_avaliaveis"] == 0
    assert g["hit_anterior"] == pytest.approx(0.9507, abs=1e-4)
    assert g["hit_posterior"] == pytest.approx(0.9421, abs=1e-4)
    assert abs(g["drift_pp"]) < DRIFT_MATERIAL_PP  # drift NAO material
    assert g["pronto_etapa6"] == "PARCIAL"
    assert g["conclusao"] == "NAO ALTERAR"


def test_corners_drift_material(chk):
    c = chk["por_mercado"]["corners"]
    assert c["entrar_total"] == 893
    assert c["hit_anterior"] == pytest.approx(0.9284, abs=1e-4)
    assert c["hit_posterior"] == pytest.approx(0.8723, abs=1e-4)
    assert c["drift_pp"] <= -DRIFT_MATERIAL_PP  # drift MATERIAL
    assert c["withinLeague"] is True
    assert c["onset_antes_cutoff"] is True
    assert c["pronto_etapa6"] == "NAO"
    assert c["conclusao"] == "SOMENTE MONITORAMENTO"


def test_cards_nao_validavel(chk):
    d = chk["por_mercado"]["cards"]
    assert d["entrar_total"] == 307
    assert d["nao_avaliaveis"] == 207
    assert d["na_pct"] >= 0.40
    assert d["pronto_etapa6"] == "NAO"
    assert d["hipotese_95_recuperaveis"]["status"] == "NAO IMPLEMENTADA / NAO VALIDADA"


def test_resultado_experimental(chk):
    r = chk["por_mercado"]["resultado"]
    assert r["entrar_total"] == 234
    assert r["nao_avaliaveis"] == 0
    assert r["experimental"] is True
    assert r["pronto_etapa6"] == "NAO"
    assert r["conclusao"] == "NAO PROMOVER"


def test_pressao_sem_dados(chk):
    p = chk["por_mercado"]["pressao_live"]
    assert p["live_snapshot_history_existe"] is False
    assert p["janelas_validaveis"] is False
    assert p["thresholds_calibrados"] is False
    assert p["pronto_etapa6"] == "NAO"


# ----------------------------------------------------------------------
# ROI / odds data-driven
# ----------------------------------------------------------------------
def test_roi_nao_validavel(chk):
    o = chk["odds_roi"]
    assert o["entrar_total"] == 4352
    assert o["entrar_com_odd_real"] == 0
    assert o["roi_validado"] is False
    assert o["roi_status"] == "NAO AVALIAVEL"
    assert o["edge_ev_pl_validavel"] is False


# ----------------------------------------------------------------------
# Matriz de prontidao
# ----------------------------------------------------------------------
def test_matriz_5_linhas(chk):
    nomes = [r["mercado"] for r in chk["matriz_prontidao"]]
    assert nomes == ["GOALS", "CORNERS", "CARDS", "RESULTADO", "PRESSAO LIVE"]
    for r in chk["matriz_prontidao"]:
        assert r["roi"] == "NAO"  # nenhum mercado tem ROI


# ----------------------------------------------------------------------
# Overfitting
# ----------------------------------------------------------------------
def test_overfitting_protecao(chk):
    o = chk["overfitting"]
    assert o["holdout_independente_disponivel"] is False
    assert o["risco_otimizar_sobre_periodo_auditado"] is True
    assert o["outro_periodo_para_validacao_posterior"] is False
    assert o["roi_validavel"] is False


# ----------------------------------------------------------------------
# Respostas obrigatorias
# ----------------------------------------------------------------------
def test_respostas_obrigatorias(chk):
    r = chk["respostas_obrigatorias"]
    assert r["goals_pronto_etapa6"] == "PARCIAL"
    assert r["corners_pronto_etapa6"] == "NAO"
    assert r["cards_pronto_etapa6"] == "NAO"
    assert r["resultado_pronto_etapa6"] == "NAO"
    assert r["pressao_live_pronta_etapa6"] == "NAO"
    assert r["algum_mercado_precisa_alteracao_agora"] == "NAO"
    assert r["etapa6_pode_ser_iniciada"] == "NAO"
    assert "NAO ALTERAR" in r["escopo_permitido_se_sim_ou_parcial"]


def test_conclusao_geral_etapa6_nao_altera(chk):
    assert "ETAPA 6 NAO DEVE ALTERAR O MOTOR" in chk["conclusao_geral"]


# ----------------------------------------------------------------------
# Regras do motor congeladas (import direto, sem side-effect)
# ----------------------------------------------------------------------
def test_regras_motor_congeladas():
    from src.settlement import _MARCA_CONVENCAO_CARTOES
    from src.politica_aprovacao import (
        PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1,
    )
    assert _MARCA_CONVENCAO_CARTOES == "amarelo=1, vermelho=2"
    # thresholds congelados (Etapa 5B, lidos da fonte)
    assert PROB_MIN_APROVAR == 0.70
    assert PROB_MAX_APROVAR == 0.97
    assert CONF_MIN_TOP1 == 0.60