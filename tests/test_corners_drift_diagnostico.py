"""Testes do diagnostico de drift de CORNERS (macroetapa final de corners).

Valida:
  - baseline preservado (numeros congelados reproduziveis);
  - ausencia de data leakage (as_of <= date);
  - split temporal cronologico (pre < cutoff, pos >= cutoff);
  - read-only (nao altera backtest.db);
  - determinismo (mesma entrada => mesma saida);
  - candidatos legittimos (sem leakage) NAO reduzem hit-rate drift;
  - candidatos com leakage sao flaggeados;
  - nenhum candidato aprovado (CORNERS permanece EM_OBSERVAÇÃO);
  - NULL != ZERO (missing nao conta como zero).
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from src.backtest import BACKTEST_DB_PATH, DEC_ENTRAR
from src.corners_drift_diagnostico import (
    CUTOFF,
    _hit,
    _load_corners_entrar,
    _split,
    congelar_baseline,
    diagnostico,
    rodar,
)
from src.corners_drift_diagnostico import testar_candidatos as _testar_candidatos


@pytest.fixture(scope="module")
def preds():
    return _load_corners_entrar()


# ----------------------------------------------------------------------
# Baseline congelado
# ----------------------------------------------------------------------
def test_baseline_congelado_reproduzivel(preds):
    b = congelar_baseline(preds)
    assert b["cutoff"] == "2026-05-10"
    # Numeros congelados (baseline oficial 5F-F)
    assert b["pre_drift"]["n_entrar"] == 455
    assert b["pre_drift"]["hit"] == pytest.approx(0.927, abs=1e-3)
    assert b["pre_drift"]["gap"] == pytest.approx(-0.0039, abs=1e-3)
    assert b["post_drift"]["n_entrar"] == 438
    assert b["post_drift"]["hit"] == pytest.approx(0.875, abs=1e-3)
    assert b["post_drift"]["gap"] == pytest.approx(-0.0515, abs=1e-3)
    assert b["drift_pp"] == pytest.approx(-5.2, abs=0.1)


def test_baseline_determinista(preds):
    b1 = congelar_baseline(preds)
    b2 = congelar_baseline(preds)
    assert b1 == b2


# ----------------------------------------------------------------------
# Split temporal cronologico
# ----------------------------------------------------------------------
def test_split_temporal_cronologico(preds):
    pre, pos = _split(preds)
    for p in pre:
        assert (p["date"] or "")[:10] < CUTOFF
    for p in pos:
        assert (p["date"] or "")[:10] >= CUTOFF
    # sem sobreposicao
    ids_pre = {p["id"] for p in pre}
    ids_pos = {p["id"] for p in pos}
    assert not (ids_pre & ids_pos)


# ----------------------------------------------------------------------
# Data leakage: as_of <= date
# ----------------------------------------------------------------------
def test_sem_data_leakage_as_of(preds):
    """as_of (data dos dados usados) deve ser <= date (data do fixture).
    Nenhuma previsao usa informacao posterior ao fixture."""
    futuras = [p for p in preds
               if p["as_of"] and p["date"] and p["as_of"] > p["date"]]
    assert futuras == [], f"{len(futuras)} previsoes com as_of > date (leakage)"


# ----------------------------------------------------------------------
# NULL != ZERO
# ----------------------------------------------------------------------
def test_null_nao_e_zero(preds):
    """total_final NULL (missing) NAO conta como zero no denominador de hit.
    Settled exclui NAO AVALIAVEL."""
    hit, n_settled, n_g, prob, gap = _hit(preds)
    # 9 ENTRAR com total_final NULL -> NAO AVALIAVEL, excluidas
    na = sum(1 for p in preds if p["resultado_final"] == "NÃO AVALIÁVEL")
    assert na == 9
    assert n_settled == len(preds) - na - sum(
        1 for p in preds if p["resultado_final"] not in
        ("GANHA", "PERDIDA", "DEVOLVIDA", "MEIA_VIT", "MEIA_DERROTA",
         "MEIA_DER", "NÃO AVALIÁVEL"))
    # hit nao usa zero para missing
    assert hit is not None


# ----------------------------------------------------------------------
# Read-only
# ----------------------------------------------------------------------
def test_diagnostico_read_only():
    """rodar() nao altera backtest.db."""
    mtime_before = os.path.getmtime(BACKTEST_DB_PATH)
    rodar()
    mtime_after = os.path.getmtime(BACKTEST_DB_PATH)
    assert mtime_before == mtime_after


# ----------------------------------------------------------------------
# Diagnostico: drift concentrado, overdispersion
# ----------------------------------------------------------------------
def test_drift_concentrado_nao_global(preds):
    d = diagnostico(preds)
    # Over 4.5 e estavel (pre ~0.909, pos ~0.900); Over 5.5 e o driver
    o45_pre = d["linhas_pre"].get("Over 4.5 escanteios (total do jogo)", {})
    o45_pos = d["linhas_pos"].get("Over 4.5 escanteios (total do jogo)", {})
    assert o45_pre["hit"] == pytest.approx(0.909, abs=1e-3)
    assert o45_pos["hit"] == pytest.approx(0.900, abs=1e-3)
    # Over 5.5: queda material
    o55_pre = d["linhas_pre"].get("Over 5.5 escanteios (total do jogo)", {})
    o55_pos = d["linhas_pos"].get("Over 5.5 escanteios (total do jogo)", {})
    assert o55_pre["hit"] > o55_pos["hit"]
    assert (o55_pre["hit"] - o55_pos["hit"]) > 0.08


def test_overdispersion_pos_drift(preds):
    d = diagnostico(preds)
    od = d["overdispersion"]
    # var/mean aumentou pos-drift (overdispersion relativa a Poisson)
    assert od["pos"]["ratio_var_mean"] > od["pre"]["ratio_var_mean"]
    # pos-drift: sd real > sd Poisson (variância excede a média)
    assert od["pos"]["sd_real"] > od["pos"]["sd_poisson"]


# ----------------------------------------------------------------------
# Candidatos: nenhum aprovado
# ----------------------------------------------------------------------
def test_nenhum_candidato_aprovado(preds):
    res = _testar_candidatos(preds)
    for c in res["candidatos"]:
        # BASELINE nao conta como aprovavel
        if c["nome"] == "BASELINE_ATUAL":
            continue
        assert c["aprovavel"] is False, (
            f"{c['nome']} marcado como aprovavel — CORNERS nao deve ser "
            f"aprovado por ordem administrativa")


def test_candidatos_leakage_flaggeados(preds):
    res = _testar_candidatos(preds)
    by_nome = {c["nome"]: c for c in res["candidatos"]}
    # CAND_A e CAND_B derivados do holdout => leakage=True
    assert by_nome["CAND_A_excluir_serie_b"]["leakage"] is True
    assert by_nome["CAND_B_so_over_45"]["leakage"] is True
    # CAND_C e CAND_D legittimos (treino em pre-drift / prior fixo)
    assert by_nome["CAND_C_isotonic_pre_drift"]["leakage"] is False
    assert by_nome["CAND_D_shrink_prob_090"]["leakage"] is False


def test_candidato_legitimo_nao_reduz_hit_drift(preds):
    """CAND_C (isotonic pre-drift, sem leakage) corrige calibracao (gap)
    mas NAO reduz o hit-rate drift — confirma que recalibracao nao promove."""
    res = _testar_candidatos(preds)
    by_nome = {c["nome"]: c for c in res["candidatos"]}
    base = by_nome["BASELINE_ATUAL"]
    cand_c = by_nome["CAND_C_isotonic_pre_drift"]
    # gap melhorou (calibracao)
    assert abs(cand_c["gap_oos"]) < abs(base["gap_oos"])
    # hit rate praticamente inalterado (nao reduz drift materialmente)
    assert abs(cand_c["hit_oos"] - base["hit_oos"]) < 0.01


def test_candidato_leakage_nao_supera_baseline_pre(preds):
    """Mesmo candidatos com leakage nao alcançam o hit pre-drift (0.927):
    a degradacao nao e totalmente explicada por subconjuntos."""
    res = _testar_candidatos(preds)
    pre_hit = res["baseline_pre_hit"]
    for c in res["candidatos"]:
        if c["nome"] == "BASELINE_ATUAL":
            continue
        # nenhum candidato alcancou pre_hit - 0.01 no OOS
        assert c["hit_oos"] < pre_hit - 0.01, (
            f"{c['nome']} superou pre-drift no OOS — revisar")


# ----------------------------------------------------------------------
# Gate de Corners (camada operacional): EM_OBSERVAÇÃO => observacao
# ----------------------------------------------------------------------
def test_corners_gate_em_observacao():
    """CORNERS status = EM_OBSERVAÇÃO => nunca operacional na camada."""
    from src.operacional import STATUS_MERCADOS, _rota_mercado
    from src.validacao_multifonte import EM_OBS
    assert STATUS_MERCADOS["escanteios"]["status"] == EM_OBS
    assert _rota_mercado("escanteios") == "observation"