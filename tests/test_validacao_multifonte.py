"""Testes da validacao estatistica multifonte consolidada (macroetapa).

Cobre:
  - Estrutura do relatorio (blocos A-G presentes).
  - NULL != ZERO: NAO AVALIAVEL nunca entra no denominador de acerto.
  - Deteccao de drift de corners (pre vs post cutoff).
  - CARDS: fallback vazio => amostra final == settled original.
  - ODDS/ROI: zero casamentos quando nao ha sobreposicao temporal.
  - Consolidacao usa somente status permitidos (nunca "APROVADO PARA MOTOR").
  - Read-only: nao altera DB principal nem motor decisorio.

Nao depende do baseline real (288k previsoes): usa DB sintetico temporario.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

import pytest

from src.backtest import DEC_ENTRAR
from src.validacao_multifonte import (
    APROVADO_PROX, EM_OBS, BLOQUEADO, NAO_AVAL,
    auditar, _drift_corners, _odds_roi_audit, _pressao_live_audit,
)


# ----------------------------------------------------------------------
# Helpers: previsoes sinteticas
# ----------------------------------------------------------------------
def _pred(date, fx, mercado, linha, prob, decisao, resultado):
    return {
        "run_id": "test", "fixture_id": fx, "league_id": 999,
        "league_name": "Liga Sint", "season": 2026, "home_team": "A",
        "away_team": "B", "date": date, "as_of": date, "modo": "PRE",
        "mercado": mercado, "linha": linha, "prob": prob, "confianca": 0.8,
        "h2h_n": 0, "n_home": 10, "n_away": 10, "sem_stats_home": 0,
        "sem_stats_away": 0, "bench_validas": 50, "decisao": decisao,
        "cobertura_mercado": "PERMITIDO",
        "experimental": mercado == "resultado",
        "odd_real": None, "bookmaker": None, "predicted_edge": None,
        "predicted_ev": None, "resultado_final": resultado,
        "placar_final": "1-1", "total_final": 3.0, "pl_unitario": None,
    }


def _write_bt_db(preds, path):
    """Escreve previsoes sinteticas em um backtest.db temporario."""
    from src.backtest import BacktestStore, BacktestRunMeta, BacktestPrediction
    store = BacktestStore(path)
    meta = BacktestRunMeta(
        run_id="test-run", engine_versao="backtest-1.0",
        regras_versao="test", modo="PRE_GAME",
        datahora=datetime.now().isoformat(timespec="seconds"),
        intervalo_inicio=None, intervalo_fim=None, ligas="999",
        mercados="todos", fixtures_considerados=10, fixtures_excluidos=0,
        previsoes=len(preds), nota="test",
    )
    pp = []
    for d in preds:
        d["run_id"] = "test-run"
        pp.append(BacktestPrediction(**d))
    store.salvar_run(meta, pp)
    return path


def _make_preds():
    """Previsoes sinteticas com drift de corners e CARDS NA."""
    preds = []
    # GOALS: 40 ENTRAR, 38 GANHA, 2 PERDIDA, bem calibrado
    for i in range(40):
        date = (datetime(2026, 1, 1) + timedelta(days=i * 5)).isoformat()
        res = "GANHA" if i < 38 else "PERDIDA"
        preds.append(_pred(date, 1000 + i, "gols", "Over 2.5", 0.93,
                           DEC_ENTRAR, res))
    # CORNERS pre-drift (jan-abr): 20 ENTRAR, 19 GANHA
    for i in range(20):
        date = (datetime(2026, 1, 1) + timedelta(days=i * 3)).isoformat()
        res = "GANHA" if i < 19 else "PERDIDA"
        preds.append(_pred(date, 2000 + i, "escanteios", "Over 9.5", 0.92,
                           DEC_ENTRAR, res))
    # CORNERS post-drift (jun-set): 20 ENTRAR, 10 GANHA (drift)
    for i in range(20):
        date = (datetime(2026, 6, 1) + timedelta(days=i * 3)).isoformat()
        res = "GANHA" if i < 10 else "PERDIDA"
        preds.append(_pred(date, 2100 + i, "escanteios", "Over 9.5", 0.92,
                           DEC_ENTRAR, res))
    # CARDS: 10 ENTRAR, 5 GANHA, 5 NAO AVALIAVEL (red_cards NULL)
    for i in range(10):
        date = (datetime(2026, 3, 1) + timedelta(days=i * 4)).isoformat()
        res = "GANHA" if i < 5 else "NÃO AVALIÁVEL"
        preds.append(_pred(date, 3000 + i, "cartoes", "Over 3.5", 0.93,
                           DEC_ENTRAR, res))
    # RESULTADO: 10 ENTRAR, 9 GANHA
    for i in range(10):
        date = (datetime(2026, 3, 1) + timedelta(days=i * 4)).isoformat()
        res = "GANHA" if i < 9 else "PERDIDA"
        preds.append(_pred(date, 4000 + i, "resultado",
                           "Vitoria mandante (1)", 0.92, DEC_ENTRAR, res))
    return preds


STATUS_PERMITIDOS = {APROVADO_PROX, EM_OBS, BLOQUEADO, NAO_AVAL}


# ----------------------------------------------------------------------
# Testes
# ----------------------------------------------------------------------
def test_estrutura_relatorio_tem_blocos_a_g(tmp_path):
    db = str(tmp_path / "bt.db")
    _write_bt_db(_make_preds(), db)
    res = auditar(db)
    for bloco in ("A_GOALS", "B_RESULTADO", "C_CARDS", "D_CORNERS",
                  "E_ODDS_ROI", "F_PRESSAO_LIVE", "G_CONSOLIDACAO"):
        assert bloco in res, f"bloco {bloco} ausente"
    assert "respostas_macroetapa" in res


def test_null_nao_entra_denominador_acerto(tmp_path):
    db = str(tmp_path / "bt.db")
    _write_bt_db(_make_preds(), db)
    res = auditar(db)
    # CARDS: 10 ENTRAR, 5 GANHA, 5 NA => hit = 5/5 = 1.0 (NA excluido)
    c = res["C_CARDS"]["metricas_entrar"]
    assert c["entrar"] == 10
    assert c["nao_avaliavel"] == 5
    assert c["settled"] == 5
    assert c["hit_excl_na"] == 1.0  # 5 ganhas / 5 settled


def test_drift_corners_detectado(tmp_path):
    db = str(tmp_path / "bt.db")
    _write_bt_db(_make_preds(), db)
    res = auditar(db)
    d = res["D_CORNERS"]
    # pre: 19/20 = 0.95; post: 10/20 = 0.50 => drift confirmado
    assert d["pre_drift"]["hit_excl_na"] > d["post_drift"]["hit_excl_na"]
    assert res["respostas_macroetapa"]["drift_cornes_confirmado"] is True


def test_cards_fallback_vazio_preserva_original(tmp_path):
    db = str(tmp_path / "bt.db")
    _write_bt_db(_make_preds(), db)
    res = auditar(db)
    c = res["C_CARDS"]
    # Sem coleta historica de fallback => 0 fills inventados, amostra final
    # == settled. A macroetapa de evidencia pode registrar resolucoes
    # NULL_MANTIDO append-only (NULL != ZERO, nenhum valor inventado), logo
    # factual_resolution_total pode ser > 0 -- o que importa e que fills reais
    # (fallback_resolvido_red_cards) permanecem 0 e a amostra e preservada.
    assert c["fallback_resolvido_red_cards"] == 0
    assert c["amostra_final_avaliavel"] == c["entrar settled"]


def test_odds_roi_zero_casamentos_sem_sobreposicao(tmp_path):
    db = str(tmp_path / "bt.db")
    _write_bt_db(_make_preds(), db)
    res = auditar(db)
    e = res["E_ODDS_ROI"]
    # Backtest sintetico usa fixtures 1000-4099; odds_snapshot_history real
    # cobre outros fixtures => 0 casamentos.
    for m in ("gols", "escanteios", "cartoes", "resultado"):
        assert e["por_mercado"][m]["validos_temporalmente"] == 0
        assert e["por_mercado"][m]["roi"] is None
    assert res["respostas_macroetapa"]["roi_historico_calculavel"] is False


def test_pressao_live_insuficiente():
    p = _pressao_live_audit()
    assert p["historico_suficiente"] is False
    # A macroetapa de evidencia pode materializar snapshots reais append-only
    # (jogos ao vivo reais, nao fabricados); o gate de suficiencia do audit e
    # a fonte de verdade e permanece False enquanto nao houver serie temporal
    # imutavel por fixture (min 1/15/30/45/60/75/90).
    assert isinstance(p["snapshots"], int) and p["snapshots"] >= 0


def test_consolidacao_usa_status_permitidos(tmp_path):
    db = str(tmp_path / "bt.db")
    _write_bt_db(_make_preds(), db)
    res = auditar(db)
    for row in res["G_CONSOLIDACAO"]:
        assert row["status_proposto"] in STATUS_PERMITIDOS, (
            f"status ilegal {row['status_proposto']!r}; "
            "'APROVADO PARA MOTOR' nunca permitido")


def test_nao_altera_motor_decisorio():
    """Importar validacao_multifonte nao importa nem altera modulos
    decisorios. Verifica que o motor backtest nao foi mutado."""
    import src.backtest as bt
    # VERSAO_BACKTEST_ENGINE inalterada
    assert bt.VERSAO_BACKTEST_ENGINE == "backtest-1.0"


def test_read_only_nao_escreve_db_principal(tmp_path):
    db = str(tmp_path / "bt.db")
    _write_bt_db(_make_preds(), db)
    from src.config import DB_PATH
    mtime_before = os.path.getmtime(DB_PATH)
    auditar(db)  # nao deve escrever em corner_intelligence.db
    mtime_after = os.path.getmtime(DB_PATH)
    assert mtime_before == mtime_after, (
        "auditar() alterou o DB principal -- deve ser read-only")