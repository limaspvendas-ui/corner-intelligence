"""Testes da macroetapa de GERACAO DE EVIDENCIA MULTIFONTE.

Cobre (SEM chamadas reais a API):
  - Gate fechado => blocos de coleta (A/C/D/F) bloqueiam sem chamada.
  - NULL != ZERO: fallback zero explicito vs NULL mantido (resolver).
  - Append-only + idempotencia: multifonte_reconciliation nao duplica.
  - Bloco E: classificacao VALIDO/INVALIDO/SEM_ODD a partir do audit.
  - Bloco I: consolidacao usa somente status permitidos.
  - Estrutura de gerar_evidencia (blocos A-I + meta).
  - Motor NAO alterado (sem import de modulos decisorios).

Naao depende de chamadas autenticadas: usa gate fechado (monkeypatch) e
auditoria fake. Le o backtest.db real apenas read-only onde inevitavel.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

import src.evidencia_multifonte as ev
from src.multifonte import NormalizedFact, ST_OK, ST_MISSING
from src.auditoria_multifonte import (
    ReconciliationResult, REC_MATCHED, ConflictRegistry,
)
from src.resolucao_factual import (
    ResolverFactual, FallbackCandidate,
    RES_RESOLVIDO_FALLBACK, RES_NULL_MANTIDO,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def gate_fechado(monkeypatch):
    """Fecha o gate para todos os blocos de coleta (sem chamada de API)."""
    monkeypatch.setattr(ev, "credenciais_rotacionadas", lambda: False)
    # tambem no modulo multifonte (usado internamente pelos adapters)
    import src.multifonte as mf
    monkeypatch.setattr(mf, "credenciais_rotacionadas", lambda: False)
    yield


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "tmp_ci.db")


# ----------------------------------------------------------------------
# Bloco A: gate fechado => bloqueado, sem coleta
# ----------------------------------------------------------------------
def test_bloco_a_gate_fechado_bloqueia(gate_fechado, monkeypatch, tmp_db):
    # backtest.db real existe; a query read-only roda (so conta NA)
    a = ev.bloco_a_cards(backtest_db=ev.BACKTEST_DB_PATH, db_path=tmp_db)
    assert a["provider_status"] == ev.ST_PROVIDER_BLOCKED
    # nao houve coleta: matched=0, resolvidos=0
    assert a["matched"] == 0
    assert a["resolvidos_fallback"] == 0
    assert "GATE" in a.get("nota", "") or a.get("nota") is not None


def test_bloco_c_gate_fechado_bloqueia(gate_fechado, monkeypatch, tmp_db):
    # auditoria fake para nao ler backtest real pesado
    monkeypatch.setattr(ev, "_auditar_validacao", lambda *a, **k: {
        "D_CORNERS": {"pre_drift": {"hit_excl_na": 0.927},
                       "post_drift": {"hit_excl_na": 0.875, "gap": -0.05}},
        "respostas_macroetapa": {"drift_cornes_confirmado": True},
    })
    c = ev.bloco_c_corners(db_path=tmp_db)
    assert c["provider_status"] == ev.ST_PROVIDER_BLOCKED
    # ainda assim responde drift (read-only)
    assert c["drift_respostas"]["drift_continua"] is True
    assert c["drift_respostas"]["pode_avancar"] is False


def test_bloco_d_gate_fechado_bloqueia(gate_fechado, tmp_db):
    d = ev.bloco_d_odds(db_path=tmp_db)
    assert d["five_dollar"]["status"] == ev.ST_PROVIDER_BLOCKED
    assert d["the_odds_api"]["status"] == ev.ST_PROVIDER_BLOCKED


def test_bloco_f_gate_fechado_bloqueia(gate_fechado, tmp_db):
    # cria a tabela live_snapshot_history no tmp_db (schema do live_pressure)
    from src.live_pressure import LiveSnapshotHistory
    LiveSnapshotHistory(db_path=tmp_db)
    f = ev.bloco_f_pressao_live(db_path=tmp_db)
    assert f["infra_pronta"] is True
    assert f["provider_status"] == ev.ST_PROVIDER_BLOCKED
    assert f["historico_total"] == 0


# ----------------------------------------------------------------------
# NULL != ZERO: resolver
# ----------------------------------------------------------------------
def test_resolver_null_fallback_zero_e_valor_valido(tmp_db):
    """NULL+fallback=0 => RESOLVIDO_FALLBACK (zero explicito valido).
    NULL sem cards[] => NULL_MANTIDO. Nunca None -> zero automatico."""
    resolver = ResolverFactual(db_path=tmp_db)
    rec_ok = ReconciliationResult(
        status=REC_MATCHED, canonical_fixture_id="1",
        candidate_provider="apifootball_com", candidate_fixture_id="x",
        confidence=0.9, motivos=[])
    # Caso 1: primary NULL, fallback red=0 (zero explicito)
    primary = NormalizedFact(provider="api_football", endpoint="/x",
                             retrieved_at=0.0, fixture_provider_id="1",
                             fixture_corner_id="1", field="red_cards",
                             raw_value=None, normalized_value=None,
                             status=ST_MISSING)
    fb_zero = NormalizedFact(provider="apifootball_com", endpoint="get_events",
                             retrieved_at=1.0, fixture_provider_id="x",
                             fixture_corner_id="1", field="red_cards",
                             raw_value=[], normalized_value=0, status=ST_OK)
    out_zero = resolver.resolver_campo(
        "1", "red_cards", primary,
        [FallbackCandidate(fact=fb_zero, reconciliation=rec_ok)])
    assert out_zero.status == RES_RESOLVIDO_FALLBACK
    assert out_zero.resolved_value == 0  # zero explicito, nao None
    # Caso 2: primary NULL, fallback sem cards (NULL)
    fb_null = NormalizedFact(provider="apifootball_com", endpoint="get_events",
                             retrieved_at=1.0, fixture_provider_id="x",
                             fixture_corner_id="1", field="red_cards",
                             raw_value=None, normalized_value=None,
                             status=ST_MISSING)
    out_null = resolver.resolver_campo(
        "1", "red_cards", primary,
        [FallbackCandidate(fact=fb_null, reconciliation=rec_ok)])
    assert out_null.status == RES_NULL_MANTIDO
    assert out_null.resolved_value is None  # NULL permanece None


def test_resolver_primary_explicito_nunca_sobrescrito(tmp_db):
    """Primary explicito (incl. 0) NUNCA sobrescrito por fallback."""
    resolver = ResolverFactual(db_path=tmp_db)
    rec_ok = ReconciliationResult(
        status=REC_MATCHED, canonical_fixture_id="1",
        candidate_provider="apifootball_com", candidate_fixture_id="x",
        confidence=0.9, motivos=[])
    primary = NormalizedFact(provider="api_football", endpoint="/x",
                             retrieved_at=0.0, fixture_provider_id="1",
                             fixture_corner_id="1", field="red_cards",
                             raw_value=[...], normalized_value=0,
                             status=ST_OK)
    fb = NormalizedFact(provider="apifootball_com", endpoint="get_events",
                        retrieved_at=1.0, fixture_provider_id="x",
                        fixture_corner_id="1", field="red_cards",
                        raw_value=[...], normalized_value=2, status=ST_OK)
    out = resolver.resolver_campo(
        "1", "red_cards", primary,
        [FallbackCandidate(fact=fb, reconciliation=rec_ok)])
    # primary 0 != fallback 2 => CONFLITO_DE_FONTE; primary 0 preservado
    assert out.status == "CONFLITO_DE_FONTE"
    assert out.resolved_value == 0  # primary mantido


# ----------------------------------------------------------------------
# Append-only + idempotencia: multifonte_reconciliation
# ----------------------------------------------------------------------
def test_persist_recon_idempotente(tmp_db):
    ev._ensure_recon_schema(tmp_db)
    ins1 = ev._persist_recon("1", "api_football", "apifootball_com",
                             "1", "x", "red_cards", None, 0,
                             REC_MATCHED, 0.9, 1, tmp_db)
    ins2 = ev._persist_recon("1", "api_football", "apifootball_com",
                             "1", "x", "red_cards", None, 0,
                             REC_MATCHED, 0.9, 1, tmp_db)
    assert ins1 is True
    assert ins2 is False  # mesma hash => ignorado (idempotente)
    with sqlite3.connect(tmp_db) as c:
        n = c.execute(
            "SELECT COUNT(*) FROM multifonte_reconciliation").fetchone()[0]
    assert n == 1  # nao duplicou


def test_persist_recon_distintos(tmp_db):
    ev._ensure_recon_schema(tmp_db)
    ev._persist_recon("1", "api_football", "apifootball_com",
                      "1", "x", "red_cards", None, 0, REC_MATCHED, 0.9, 1, tmp_db)
    ev._persist_recon("1", "api_football", "apifootball_com",
                      "1", "x", "yellow_cards", None, 3, REC_MATCHED, 0.9, 1, tmp_db)
    with sqlite3.connect(tmp_db) as c:
        n = c.execute(
            "SELECT COUNT(*) FROM multifonte_reconciliation").fetchone()[0]
    assert n == 2  # campos distintos => 2 linhas


# ----------------------------------------------------------------------
# Bloco E: classificacao
# ----------------------------------------------------------------------
def test_bloco_e_classificacao(monkeypatch, tmp_db):
    fake = {
        "odds_snapshots_total_familia_mapeada": 100,
        "fixtures_com_odds": 10, "providers": ["api_football"],
        "por_mercado": {
            "gols": {"entrar": 100, "validos_temporalmente": 5,
                     "invalidos_temporalmente": 3, "indeterminados": 1,
                     "sem_odd_casavel": 91, "settled_com_odd": 5,
                     "roi": 0.02, "avg_odd": 1.9, "hit_rate_settled": 0.6},
            "escanteios": {"entrar": 50, "validos_temporalmente": 0,
                           "invalidos_temporalmente": 4, "indeterminados": 0,
                           "sem_odd_casavel": 46, "settled_com_odd": 0,
                           "roi": None, "avg_odd": None, "hit_rate_settled": None},
            "cartoes": {"entrar": 30, "validos_temporalmente": 0,
                        "invalidos_temporalmente": 0, "indeterminados": 0,
                        "sem_odd_casavel": 30, "settled_com_odd": 0,
                        "roi": None, "avg_odd": None, "hit_rate_settled": None},
            "resultado": {"entrar": 20, "validos_temporalmente": 0,
                          "invalidos_temporalmente": 0, "indeterminados": 0,
                          "sem_odd_casavel": 20, "settled_com_odd": 0,
                          "roi": None, "avg_odd": None, "hit_rate_settled": None},
        },
        "roi_historico_calculavel": True,
        "nota": "teste",
    }
    monkeypatch.setattr(ev, "_odds_roi_audit", lambda preds: fake)
    e = ev.bloco_e_roi(preds=[], db_path=tmp_db)
    assert e["por_mercado"]["gols"]["classificacao"] == "VALIDO"
    assert e["por_mercado"]["escanteios"]["classificacao"] == "INVALIDO_TEMPORALMENTE"
    assert e["por_mercado"]["cartoes"]["classificacao"] == "SEM_ODD_CASAVEL"
    assert e["por_mercado"]["resultado"]["classificacao"] == "SEM_ODD_CASAVEL"
    assert e["roi_historico_calculavel"] is True


# ----------------------------------------------------------------------
# Bloco G/H: status permitidos + motor nao alterado
# ----------------------------------------------------------------------
def test_bloco_g_h_status_permitidos():
    fake_val = {
        "A_GOALS": {"metricas_entrar": {"entrar": 10, "settled": 10,
                                          "hit_excl_na": 0.95, "gap": 0.0},
                    "oos": {"hit_excl_na": 0.94}, "walk_forward": [
                        {"hit_excl_na": 0.93}, {"hit_excl_na": 0.94}]},
        "B_RESULTADO": {"metricas_entrar": {"entrar": 5, "settled": 5,
                                             "hit_excl_na": 0.9, "gap": 0.0},
                        "oos": {"hit_excl_na": 0.88}, "walk_forward": [
                            {"hit_excl_na": 0.89}]},
    }
    g = ev.bloco_g_resultado(fake_val)
    h = ev.bloco_h_goals(fake_val)
    permitidos = {ev.APROVADO_PROX, ev.EM_OBS, ev.BLOQUEADO, ev.NAO_AVAL}
    assert g["status_novo"] in permitidos
    assert h["status_novo"] in permitidos
    assert g["motor_alterado"] is False
    assert h["motor_alterado"] is False


def test_bloco_h_deterioracao_rebaixa():
    fake_val = {
        "A_GOALS": {"metricas_entrar": {"entrar": 10, "settled": 10,
                                          "hit_excl_na": 0.95, "gap": 0.0},
                    "oos": {"hit_excl_na": 0.85}, "walk_forward": [
                        {"hit_excl_na": 0.93}]},
    }
    h = ev.bloco_h_goals(fake_val)
    assert h["deterioracao_material"] is True
    assert h["status_novo"] == ev.EM_OBS  # rebaixado na analise
    assert h["motor_alterado"] is False  # motor intacto


# ----------------------------------------------------------------------
# Bloco I: consolidacao status permitidos
# ----------------------------------------------------------------------
def test_bloco_i_status_permitidos():
    a = {"entrar_na_red_null": 0, "resolvidos_fallback": 0,
         "resolvidos_fallback_zero": 0, "resolvidos_fallback_positivo": 0,
         "matched": 0, "datas_visitadas": 0}
    b = {"correspondencias_registradas": 0}
    c = {"corners_odds_coletados": 0, "drift_post_hit": 0.875,
         "drift_respostas": {"pode_avancar": False,
                              "pode_avancar_nota": "x"}}
    d = {"five_dollar": {"inseridos": 0}, "the_odds_api": {"inseridos": 0}}
    e = {"por_mercado": {mk: {"validos_temporalmente": 0, "classificacao": "SEM_ODD_CASAVEL"}
                         for mk in ("gols", "escanteios", "cartoes", "resultado")},
         "roi_historico_calculavel": False, "nota": "x"}
    f = {"historico_total": 0, "fixtures_no_historico": 0, "nota": "x"}
    g = {"oos_hit": None, "status_novo": ev.EM_OBS, "justificativa": "x"}
    h = {"oos_hit": 0.94, "status_novo": ev.APROVADO_PROX, "justificativa": "x"}
    rows = ev.bloco_i_consolidacao(a, b, c, d, e, f, g, h)
    permitidos = {ev.APROVADO_PROX, ev.EM_OBS, ev.BLOQUEADO, ev.NAO_AVAL}
    assert len(rows) == 6
    for r in rows:
        assert r["STATUS_NOVO"] in permitidos
        assert "APROVADO PARA MOTOR" not in str(r["STATUS_NOVO"]).upper()


# ----------------------------------------------------------------------
# Estrutura + motor intacto
# ----------------------------------------------------------------------
def test_gerar_evidencia_estrutura(gate_fechado, monkeypatch, tmp_db):
    monkeypatch.setattr(ev, "_auditar_validacao", lambda *a, **k: {
        "A_GOALS": {"metricas_entrar": {"entrar": 1, "settled": 1,
                                          "hit_excl_na": 0.9, "gap": 0.0},
                    "oos": {"hit_excl_na": 0.9}, "walk_forward": []},
        "B_RESULTADO": {"metricas_entrar": {"entrar": 1, "settled": 1,
                                             "hit_excl_na": 0.9, "gap": 0.0},
                        "oos": {"hit_excl_na": 0.9}, "walk_forward": []},
        "D_CORNERS": {"pre_drift": {"hit_excl_na": 0.9},
                       "post_drift": {"hit_excl_na": 0.8, "gap": -0.1}},
        "respostas_macroetapa": {"drift_cornes_confirmado": True},
    })
    monkeypatch.setattr(ev, "_odds_roi_audit", lambda preds: {
        "odds_snapshots_total_familia_mapeada": 0, "fixtures_com_odds": 0,
        "providers": [], "por_mercado": {
            mk: {"entrar": 0, "validos_temporalmente": 0,
                 "invalidos_temporalmente": 0, "indeterminados": 0,
                 "sem_odd_casavel": 0, "settled_com_odd": 0, "roi": None,
                 "avg_odd": None, "hit_rate_settled": None}
            for mk in ("gols", "escanteios", "cartoes", "resultado")},
        "roi_historico_calculavel": False, "nota": "x"})
    # bloco_f precisa da tabela live_snapshot_history no tmp_db
    from src.live_pressure import LiveSnapshotHistory
    LiveSnapshotHistory(db_path=tmp_db)
    res = ev.gerar_evidencia(backtest_db=ev.BACKTEST_DB_PATH, db_path=tmp_db)
    for bloco in ("A_CARDS", "B_RECONCILIACAO", "C_CORNERS", "D_ODDS",
                  "E_ROI", "F_PRESSAO_LIVE", "G_RESULTADO", "H_GOALS",
                  "I_CONSOLIDACAO", "meta"):
        assert bloco in res, f"bloco {bloco} ausente"
    assert res["meta"]["macroetapa"] == "GERACAO_DE_EVIDENCIA_MULTIFONTE_PENDENTE"
    assert res["meta"]["gate_aberto"] is False  # gate fechado no fixture


def test_motor_nao_alterado():
    """Importar evidencia_multifonte nao altera modulos decisorios."""
    import src.backtest as bt
    assert bt.VERSAO_BACKTEST_ENGINE == "backtest-1.0"
    # evidencia nao importa modulos decisorios
    import inspect
    src_mod = inspect.getsource(ev)
    for proibido in ("from src.analysis", "from src.policy",
                     "from src.settlement", "from src.calibration",
                     "from src.prejogo_opportunity", "import src.app"):
        assert proibido not in src_mod, f"import proibido: {proibido}"


def test_read_only_nao_escreve_db_principal(gate_fechado, monkeypatch, tmp_db):
    """Blocos com gate fechado nao escrevem no DB principal."""
    from src.config import DB_PATH
    mtime_before = os.path.getmtime(DB_PATH)
    monkeypatch.setattr(ev, "_auditar_validacao", lambda *a, **k: {})
    monkeypatch.setattr(ev, "_odds_roi_audit", lambda preds: {
        "por_mercado": {}, "roi_historico_calculavel": False,
        "providers": [], "fixtures_com_odds": 0,
        "odds_snapshots_total_familia_mapeada": 0, "nota": ""})
    from src.live_pressure import LiveSnapshotHistory
    LiveSnapshotHistory(db_path=tmp_db)
    ev.gerar_evidencia(backtest_db=ev.BACKTEST_DB_PATH, db_path=tmp_db)
    mtime_after = os.path.getmtime(DB_PATH)
    assert mtime_before == mtime_after, "DB principal alterado com gate fechado"