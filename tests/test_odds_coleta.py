"""Testes da coleta prospectiva de odds reais (Etapa 5F).

Valida: append-only (nunca sobrescreve), dedup (identica ignora, 1.90->1.89
e novo), timestamps, anti-leakage (e_pre_jogo), validacao (invalida => motivo,
nunca zero), parsing lado/linha, multi-bookmaker, UNMAPPED, separacao
pre-match/live, live suspenso, erro de API, payload parcial, determinismo,
NAO alterar motor/backtest, JSON serializavel. NAO calcula ROI.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from src.odds_coleta import (
    COLETA_LIVE,
    COLETA_PRE,
    OddsSnapshotStore,
    OddSnapshot,
    _construir_snapshots,
    _e_pre_jogo,
    classificar_mercado,
    coletar_fixture,
    extrair_lado_linha,
    ingerir_cache,
)


# ----------------------------------------------------------------------
# Fixtures: DB temporario isolado (nunca toca o DB real)
# ----------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path: Path) -> OddsSnapshotStore:
    return OddsSnapshotStore(db_path=str(tmp_path / "odds_test.db"))


def _snap(fixture_id=1, coleta=COLETA_PRE, bookmaker="Bet365", bet_name="Goals Over/Under",
          bet_id=5, familia="gols", sub=None, lado="Over", linha=2.5,
          value_feed="Over 2.5", odd=1.90, suspended=None, update_feed="2026-09-10T12:00:00+00:00",
          collected_at=1789000000.0, fixture_date="2026-09-12T01:30:00+00:00",
          status="OK", motivo=None) -> OddSnapshot:
    return OddSnapshot(
        fixture_id=fixture_id, coleta_tipo=coleta, bookmaker=bookmaker,
        bet_name=bet_name, bet_id=bet_id, familia=familia, subfamilia=sub,
        lado=lado, linha=linha, value_feed=value_feed, odd=odd,
        suspended=suspended, update_feed=update_feed, collected_at=collected_at,
        fixture_date=fixture_date, status=status, motivo=motivo,
    )


class FakeClient:
    """Cliente fake: devolve respostas /odds e /odds/live pre-configuradas."""

    def __init__(self, pre=None, live=None):
        self.pre = pre
        self.live = live
        self.cache = None
        self.calls = []

    def get(self, endpoint, params=None, **kw):
        self.calls.append(endpoint)
        if endpoint == "/odds":
            return self.pre
        if endpoint == "/odds/live":
            return self.live
        return []


# Resposta /odds pre-match realista (2 bookmakers, varios mercados)
PRE_ODDS = [{
    "update": "2026-09-10T12:30:35+00:00",
    "bookmakers": [
        {
            "name": "Bet365", "update": None, "bets": [
                {"name": "Goals Over/Under", "id": 5, "values": [
                    {"value": "Over 2.5", "odd": "1.90"},
                    {"value": "Under 2.5", "odd": "1.89"},
                ]},
                {"name": "Match Winner", "id": 1, "values": [
                    {"value": "Home", "odd": "1.46"},
                    {"value": "Draw", "odd": "4.50"},
                ]},
                {"name": "Mercado Estranho XYZ", "values": [
                    {"value": "W", "odd": "3.00"},
                ]},
            ],
        },
        {
            "name": "Pinnacle", "update": None, "bets": [
                {"name": "Corners Over Under", "id": 45, "values": [
                    {"value": "Over 9.5", "odd": "2.15"},
                ]},
                {"name": "Goals Over/Under First Half", "id": 5, "values": [
                    {"value": "Over 1.5", "odd": "2.00"},
                ]},
                {"name": "Cards Over/Under", "id": 80, "values": [
                    {"value": "Over 3.5", "odd": "1.00"},   # invalido? 1.00 > 0 => OK
                    {"value": "Over 3.5", "odd": "0"},       # invalido (<=0)
                    {"value": "", "odd": "2.00"},            # invalido (value ausente)
                    {"value": "Under 3.5", "odd": None},     # invalido (odd ausente)
                ]},
            ],
        },
    ],
}]

# Resposta /odds/live realista (odds[] agregado, suspended por valor)
LIVE_ODDS = [{
    "update": "2026-09-06T22:36:22+00:00",
    "bookmaker": {"name": "Bet365"},
    "odds": [
        {"name": "Asian Handicap", "id": 4, "values": [
            {"value": "Home 0", "odd": "1.975", "suspended": False, "handicap": "0", "main": True},
        ]},
        {"name": "Total Corners", "id": 45, "values": [
            {"value": "Over 11.5", "odd": "2.30", "suspended": False},
            {"value": "Under 11.5", "odd": "1.65", "suspended": True},
        ]},
    ],
}]


# ----------------------------------------------------------------------
# Append-only / dedup (FASE 6, 11)
# ----------------------------------------------------------------------
def test_append_only_dedup_identica(store):
    s = _snap()
    r1 = store.append_many([s])
    r2 = store.append_many([s])
    assert r1 == {"inseridos": 1, "duplicados": 0}
    assert r2 == {"inseridos": 0, "duplicados": 1}
    st = store.status()
    assert st["total_snapshots"] == 1  # nao dobrou


def test_dedup_odd_diferente_e_novo_snapshot(store):
    s190 = _snap(odd=1.90, value_feed="Over 2.5")
    s189 = _snap(odd=1.89, value_feed="Over 2.5")
    r = store.append_many([s190, s189])
    assert r["inseridos"] == 2  # 1.90 -> 1.89 sao snapshots distintos
    assert store.status()["total_snapshots"] == 2


def test_nunca_sobrescreve(store, tmp_path):
    """Uma odd antiga nunca e sobrescrita: re-append nao muda o row antigo."""
    s = _snap(odd=1.90, collected_at=1000.0)
    store.append_many([s])
    # tentativa de "atualizar" com odd diferente NUNCA sobrescreve o row 1.90
    s2 = _snap(odd=1.85, collected_at=2000.0)
    store.append_many([s2])
    with sqlite3.connect(store.db_path) as con:
        rows = con.execute(
            "SELECT odd, collected_at FROM odds_snapshot_history "
            "ORDER BY collected_at").fetchall()
    assert rows == [(1.90, 1000.0), (1.85, 2000.0)]  # ambos preservados


# ----------------------------------------------------------------------
# Timestamps (FASE 7)
# ----------------------------------------------------------------------
def test_timestamps_presentes(store):
    s = _snap(update_feed="2026-09-10T12:00:00+00:00", collected_at=1789000000.0)
    store.append_many([s])
    with sqlite3.connect(store.db_path) as con:
        row = con.execute(
            "SELECT update_feed, collected_at FROM odds_snapshot_history").fetchone()
    assert row[0] == "2026-09-10T12:00:00+00:00"
    assert row[1] == 1789000000.0


# ----------------------------------------------------------------------
# Validacao: invalida => motivo, nunca zero (FASE 12)
# ----------------------------------------------------------------------
def test_odd_ausente_invalid_nao_vira_zero(store):
    s = _snap(odd=None, status="INVALID", motivo="odd ausente ou nao numerica")
    store.append_many([s])
    with sqlite3.connect(store.db_path) as con:
        row = con.execute("SELECT odd, status, motivo FROM odds_snapshot_history").fetchone()
    assert row[0] is None        # None permanece None, NUNCA 0
    assert row[1] == "INVALID"
    assert row[2] == "odd ausente ou nao numerica"


def test_odd_zero_invalid(store):
    snaps = _construir_snapshots(
        1, COLETA_PRE, {"update": "t", "bookmakers": [
            {"name": "B", "bets": [{"name": "Goals Over/Under", "id": 5,
             "values": [{"value": "Over 2.5", "odd": "0"}]}]}]},
        collected_at=1789000000.0, fixture_date=None)
    store.append_many(snaps)
    with sqlite3.connect(store.db_path) as con:
        row = con.execute("SELECT status, motivo, odd FROM odds_snapshot_history").fetchone()
    assert row[0] == "INVALID"
    assert "nao positiva" in row[1]
    assert row[2] is None


def test_value_ausente_invalid(store):
    snaps = _construir_snapshots(
        1, COLETA_PRE, {"update": "t", "bookmakers": [
            {"name": "B", "bets": [{"name": "Goals Over/Under", "id": 5,
             "values": [{"value": "", "odd": "2.00"}]}]}]},
        collected_at=1789000000.0, fixture_date=None)
    store.append_many(snaps)
    with sqlite3.connect(store.db_path) as con:
        row = con.execute("SELECT status, motivo FROM odds_snapshot_history").fetchone()
    assert row[0] == "INVALID"
    assert "value ausente" in row[1]


# ----------------------------------------------------------------------
# Classificacao EXATA de mercado (FASE 4: sem aproximacao)
# ----------------------------------------------------------------------
def test_classificar_por_id_gols():
    assert classificar_mercado("Goals Over/Under", 5) == ("gols", None)


def test_classificar_por_id_corners():
    assert classificar_mercado("Corners Over Under", 45) == ("escanteios", None)


def test_classificar_por_id_cards():
    assert classificar_mercado("Cards Over/Under", 80) == ("cartoes", None)


def test_classificar_resultado_match_winner():
    assert classificar_mercado("Match Winner", 1) == ("resultado", "1X2")


def test_classificar_resultado_double_chance():
    assert classificar_mercado("Double Chance", None) == ("resultado", "DC")


def test_marcador_rejeita_total_mesmo_com_id_certo():
    # "Goals Over/Under First Half" tem marcador "half" => UNMAPPED
    fam, _ = classificar_mercado("Goals Over/Under First Half", 5)
    assert fam == "UNMAPPED"


def test_mercado_desconhecido_unmapped():
    fam, _ = classificar_mercado("Mercado Estranho XYZ", None)
    assert fam == "UNMAPPED"


# ----------------------------------------------------------------------
# Parsing lado/linha
# ----------------------------------------------------------------------
def test_extrair_over():
    assert extrair_lado_linha("gols", None, "Over 2.5") == ("Over", 2.5)


def test_extrair_ah():
    assert extrair_lado_linha("resultado", "AH", "Home -1.5") == ("Home", -1.5)


def test_extrair_1x2():
    assert extrair_lado_linha("resultado", "1X2", "Home") == ("Home", None)
    assert extrair_lado_linha("resultado", "1X2", "Empate") == (None, None)


# ----------------------------------------------------------------------
# Multi-bookmaker + construcao completa
# ----------------------------------------------------------------------
def test_multi_bookmaker_ambos_armazenados(store):
    snaps = _construir_snapshots(
        1, COLETA_PRE, PRE_ODDS[0],
        collected_at=1789000000.0, fixture_date="2026-09-12T01:30:00+00:00")
    store.append_many(snaps)
    with sqlite3.connect(store.db_path) as con:
        bms = {r[0] for r in con.execute(
            "SELECT DISTINCT bookmaker FROM odds_snapshot_history").fetchall()}
    assert bms == {"Bet365", "Pinnacle"}


def test_construcao_classifica_familias_corretamente():
    snaps = _construir_snapshots(
        1, COLETA_PRE, PRE_ODDS[0],
        collected_at=1789000000.0, fixture_date="2026-09-12T01:30:00+00:00")
    fams = {s.familia for s in snaps}
    assert "gols" in fams
    assert "resultado" in fams
    assert "escanteios" in fams
    assert "UNMAPPED" in fams
    # Over 2.5 gols => lado Over, linha 2.5
    gol_over = [s for s in snaps if s.familia == "gols" and s.lado == "Over"]
    assert gol_over and gol_over[0].linha == 2.5


def test_construcao_marca_invalidos_com_motivo():
    snaps = _construir_snapshots(
        1, COLETA_PRE, PRE_ODDS[0],
        collected_at=1789000000.0, fixture_date=None)
    inv = [s for s in snaps if s.status == "INVALID"]
    assert len(inv) == 3  # odd 0, value vazio, odd None
    assert all(s.motivo for s in inv)
    assert all(s.odd is None for s in inv)  # nenhum virou zero


# ----------------------------------------------------------------------
# Pre-match vs live separados (FASE 16)
# ----------------------------------------------------------------------
def test_live_separado_e_suspended(store):
    snaps = _construir_snapshots(
        1, COLETA_LIVE, LIVE_ODDS[0],
        collected_at=1789000000.0, fixture_date="2026-09-06T19:00:00+00:00")
    store.append_many(snaps)
    with sqlite3.connect(store.db_path) as con:
        tipos = {r[0] for r in con.execute(
            "SELECT DISTINCT coleta_tipo FROM odds_snapshot_history").fetchall()}
        sus = con.execute(
            "SELECT COUNT(*) FROM odds_snapshot_history WHERE status='SUSPENDED'"
        ).fetchone()[0]
    assert tipos == {"live"}
    assert sus >= 1  # Under 11.5 suspenso


def test_live_suspended_preserva_odd():
    snaps = _construir_snapshots(
        1, COLETA_LIVE, LIVE_ODDS[0],
        collected_at=1789000000.0, fixture_date=None)
    susp = [s for s in snaps if s.status == "SUSPENDED"]
    assert susp and susp[0].odd == 1.65  # odd factual mantida


# ----------------------------------------------------------------------
# Anti-leakage: e_pre_jogo (FASE 7)
# ----------------------------------------------------------------------
def test_e_pre_jogo_antes_kickoff():
    # coleta 1000s, kickoff 1789000000 => coleta muito antes => 1
    assert _e_pre_jogo(1000.0, "2026-09-12T01:30:00+00:00") == 1


def test_e_pre_jogo_depois_kickoff():
    # coleta depois do kickoff => 0 (anti-leakage: nao serve como entrada)
    assert _e_pre_jogo(9999999999.0, "2026-09-12T01:30:00+00:00") == 0


def test_e_pre_jogo_sem_data():
    assert _e_pre_jogo(1000.0, None) is None


def test_e_pre_jogo_persistido(store):
    s_pre = _snap(collected_at=1000.0, fixture_date="2026-09-12T01:30:00+00:00")
    s_pos = _snap(odd=1.85, collected_at=9999999999.0,
                  fixture_date="2026-09-12T01:30:00+00:00")
    store.append_many([s_pre, s_pos])
    with sqlite3.connect(store.db_path) as con:
        rows = con.execute(
            "SELECT e_pre_jogo FROM odds_snapshot_history ORDER BY collected_at"
        ).fetchall()
    assert rows == [(1,), (0,)]


# ----------------------------------------------------------------------
# Erro de API / payload parcial (FASE 9, 12)
# ----------------------------------------------------------------------
def test_api_retorna_vazio_sem_crash(store):
    client = FakeClient(pre=[], live=[])
    r = coletar_fixture(client, store, 999, live=True)
    assert r["snapshots"] == 0
    assert r["consumo_api"] == 2  # tentou /odds + /odds/live
    assert store.status()["total_snapshots"] == 0


def test_api_none_sem_crash(store):
    client = FakeClient(pre=None, live=None)
    r = coletar_fixture(client, store, 999, live=False)
    assert r["snapshots"] == 0
    assert r["inseridos"] == 0


def test_payload_sem_bookmakers(store):
    snaps = _construir_snapshots(
        1, COLETA_PRE, {"update": "t", "bookmakers": []},
        collected_at=1789000000.0, fixture_date=None)
    assert snaps == []
    snaps2 = _construir_snapshots(
        1, COLETA_PRE, {"update": "t"},
        collected_at=1789000000.0, fixture_date=None)
    assert snaps2 == []


def test_coletar_fixture_pre_e_live(store):
    client = FakeClient(pre=PRE_ODDS, live=LIVE_ODDS)
    r = coletar_fixture(client, store, 42, live=True)
    assert r["consumo_api"] == 2
    assert r["inseridos"] > 0
    st = store.status()
    assert st["por_coleta_tipo"].get("pre_match", 0) > 0
    assert st["por_coleta_tipo"].get("live", 0) > 0


# ----------------------------------------------------------------------
# Ingestao do cache (FASE 13) em DB isolado
# ----------------------------------------------------------------------
def _seed_cache_db(db_path):
    con = sqlite3.connect(str(db_path))
    con.execute(
        """CREATE TABLE IF NOT EXISTS api_cache (
            key TEXT PRIMARY KEY, endpoint TEXT, params TEXT,
            response TEXT, created_at REAL, ttl INTEGER)""")
    pre_params = json.dumps({"fixture": 100})
    pre_resp = json.dumps(PRE_ODDS)
    live_params = json.dumps({"fixture": 100})
    live_resp = json.dumps(LIVE_ODDS)
    con.execute(
        "INSERT INTO api_cache (key,endpoint,params,response,created_at,ttl) "
        "VALUES ('k1','/odds',?,?,1788000000.0,NULL)",
        (pre_params, pre_resp))
    con.execute(
        "INSERT INTO api_cache (key,endpoint,params,response,created_at,ttl) "
        "VALUES ('k2','/odds/live',?,?,1788000100.0,NULL)",
        (live_params, live_resp))
    con.commit()
    con.close()


def test_ingerir_cache_zero_api(store, tmp_path):
    cache_db = tmp_path / "cache_test.db"
    _seed_cache_db(cache_db)
    r = ingerir_cache(store, db_path=str(cache_db), dry_run=False)
    assert r["consumo_api"] == 0
    assert r["pre_entries"] == 1
    assert r["live_entries"] == 1
    assert r["inseridos"] > 0
    assert store.status()["fixtures_distintos"] == 1


def test_ingerir_cache_dry_run_nao_grava(store, tmp_path):
    cache_db = tmp_path / "cache_test2.db"
    _seed_cache_db(cache_db)
    r = ingerir_cache(store, db_path=str(cache_db), dry_run=True)
    assert r["dry_run"] is True
    assert store.status()["total_snapshots"] == 0  # nada gravado


# ----------------------------------------------------------------------
# Determinismo
# ----------------------------------------------------------------------
def test_hash_deterministico():
    s = _snap()
    assert s.hash() == s.hash()


def test_hash_diferente_para_odd_diferente():
    assert _snap(odd=1.90).hash() != _snap(odd=1.89).hash()


def test_status_json_serializavel(store):
    s = _snap()
    store.append_many([s])
    st = store.status()
    json.dumps(st)  # nao levanta


# ----------------------------------------------------------------------
# NAO altera motor / backtest (FASE 9: separado do motor)
# ----------------------------------------------------------------------
def test_nao_altera_backtest_db():
    from src.backtest import BACKTEST_DB_PATH
    antes = sqlite3.connect(BACKTEST_DB_PATH).execute(
        "SELECT COUNT(*) FROM bt_predictions").fetchone()[0]
    # ingestao do cache real NAO toca backtest.db
    s = OddsSnapshotStore()
    ingerir_cache(s)
    depois = sqlite3.connect(BACKTEST_DB_PATH).execute(
        "SELECT COUNT(*) FROM bt_predictions").fetchone()[0]
    assert antes == depois


def test_motor_regras_congeladas():
    from src.politica_aprovacao import (
        PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1,
    )
    from src.settlement import _MARCA_CONVENCAO_CARTOES
    assert PROB_MIN_APROVAR == 0.70
    assert PROB_MAX_APROVAR == 0.97
    assert CONF_MIN_TOP1 == 0.60
    assert _MARCA_CONVENCAO_CARTOES == "amarelo=1, vermelho=2"


def test_coletor_nao_importa_motor_de_aprovacao():
    # o modulo de coleta NAO depende do motor de probabilidade/aprovacao
    import src.odds_coleta as m
    assert not hasattr(m, "PROB_MIN_APROVAR")
    assert not hasattr(m, "scan_pregame_opportunities")