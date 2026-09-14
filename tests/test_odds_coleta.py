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
    PROVIDER_API_FOOTBALL,
    APIFootballOddsProvider,
    OddsSnapshotStore,
    OddSnapshot,
    _PROVIDER_REGISTRY,
    _construir_snapshots,
    _e_pre_jogo,
    _hash_identidade,
    _migrar_para_multiprovider,
    classificar_mercado,
    coletar_fixture,
    extrair_lado_linha,
    ingerir_cache,
    resolve_provider,
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
          status="OK", motivo=None, provider=PROVIDER_API_FOOTBALL) -> OddSnapshot:
    return OddSnapshot(
        provider=provider,
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


# ======================================================================
# ETAPA 5F-C -- FUNDACAO MULTI-PROVIDER E PROVENIENCIA (20 testes)
# ======================================================================
import hashlib


def _hash_v1(fixture_id, coleta_tipo, bookmaker, bet_name, bet_id, familia,
             subfamilia, lado, linha, value_feed, odd, suspended, status):
    """Reproduz o hash V1 (sem provider) para semear DBs legados em testes."""
    payload = json.dumps(
        [fixture_id, coleta_tipo, bookmaker, bet_name, bet_id, familia,
         subfamilia, lado, linha, value_feed,
         None if odd is None else round(odd, 6),
         None if suspended is None else int(suspended), status],
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_LEGACY_SCHEMA = """
CREATE TABLE odds_snapshot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL,
    coleta_tipo TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    bet_name TEXT NOT NULL,
    bet_id INTEGER,
    familia TEXT NOT NULL,
    subfamilia TEXT,
    lado TEXT,
    linha REAL,
    value_feed TEXT NOT NULL,
    odd REAL,
    suspended INTEGER,
    update_feed TEXT,
    collected_at REAL NOT NULL,
    fixture_date TEXT,
    e_pre_jogo INTEGER,
    status TEXT NOT NULL,
    motivo TEXT,
    snapshot_hash TEXT NOT NULL,
    UNIQUE (snapshot_hash)
);
"""


def _seed_legacy_db(db_path: Path, n_rows: int = 3) -> None:
    """Cria um DB legado (sem coluna provider, hashes V1) com n_rows linhas."""
    con = sqlite3.connect(str(db_path))
    con.executescript(_LEGACY_SCHEMA)
    for i in range(n_rows):
        fid = 100 + i
        odd = 1.90 + i * 0.01
        h = _hash_v1(fid, COLETA_PRE, "Bet365", "Goals Over/Under", 5, "gols",
                     None, "Over", 2.5, "Over 2.5", odd, None, "OK")
        con.execute(
            "INSERT INTO odds_snapshot_history (fixture_id, coleta_tipo, "
            "bookmaker, bet_name, bet_id, familia, subfamilia, lado, linha, "
            "value_feed, odd, suspended, update_feed, collected_at, "
            "fixture_date, e_pre_jogo, status, motivo, snapshot_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, COLETA_PRE, "Bet365", "Goals Over/Under", 5, "gols", None,
             "Over", 2.5, "Over 2.5", odd, None, "2026-09-10T12:00:00+00:00",
             1789000000.0, "2026-09-12T01:30:00+00:00", 1, "OK", None, h))
    con.commit()
    con.close()


def _legacy_rows(db_path: Path):
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT id, fixture_id, bookmaker, odd, collected_at, fixture_date, "
        "linha, snapshot_hash FROM odds_snapshot_history ORDER BY id"
    ).fetchall()
    con.close()
    return rows


# --- 1. banco antigo sem provider migra corretamente ---
def test_migracao_banco_antigo_sem_provider(tmp_path):
    db = tmp_path / "legacy1.db"
    _seed_legacy_db(db, n_rows=3)
    OddsSnapshotStore(db_path=str(db))  # dispara a migracao
    con = sqlite3.connect(str(db))
    cols = [r[1] for r in con.execute("PRAGMA table_info(odds_snapshot_history)")]
    assert "provider" in cols  # coluna adicionada
    provs = con.execute("SELECT DISTINCT provider FROM odds_snapshot_history").fetchall()
    con.close()
    assert provs == [("api_football",)]


# --- 2. registros antigos recebem api_football ---
def test_registros_antigos_recebem_api_football(tmp_path):
    db = tmp_path / "legacy2.db"
    _seed_legacy_db(db, n_rows=5)
    OddsSnapshotStore(db_path=str(db))
    con = sqlite3.connect(str(db))
    contagem = dict(con.execute(
        "SELECT provider, COUNT(*) FROM odds_snapshot_history GROUP BY provider"
    ).fetchall())
    con.close()
    assert contagem == {"api_football": 5}


# --- 3. quantidade de linhas permanece identica ---
def test_migracao_qtd_linhas_idem(tmp_path):
    db = tmp_path / "legacy3.db"
    _seed_legacy_db(db, n_rows=7)
    antes = sqlite3.connect(str(db)).execute(
        "SELECT COUNT(*) FROM odds_snapshot_history").fetchone()[0]
    OddsSnapshotStore(db_path=str(db))
    depois = sqlite3.connect(str(db)).execute(
        "SELECT COUNT(*) FROM odds_snapshot_history").fetchone()[0]
    assert antes == 7 == depois


# --- 4. nenhuma odd historica muda ---
def test_migracao_nenhuma_odd_muda(tmp_path):
    db = tmp_path / "legacy4.db"
    _seed_legacy_db(db, n_rows=3)
    antes = [r[3] for r in _legacy_rows(db)]  # odd
    OddsSnapshotStore(db_path=str(db))
    depois = [r[3] for r in _legacy_rows(db)]
    assert antes == depois


# --- 5. nenhum timestamp historico muda ---
def test_migracao_nenhum_timestamp_muda(tmp_path):
    db = tmp_path / "legacy5.db"
    _seed_legacy_db(db, n_rows=3)
    antes = sqlite3.connect(str(db)).execute(
        "SELECT collected_at, update_feed, fixture_date FROM odds_snapshot_history "
        "ORDER BY id").fetchall()
    OddsSnapshotStore(db_path=str(db))
    depois = sqlite3.connect(str(db)).execute(
        "SELECT collected_at, update_feed, fixture_date FROM odds_snapshot_history "
        "ORDER BY id").fetchall()
    assert antes == depois


# --- 6. nenhuma fixture muda ---
def test_migracao_nenhuma_fixture_muda(tmp_path):
    db = tmp_path / "legacy6.db"
    _seed_legacy_db(db, n_rows=4)
    antes = sqlite3.connect(str(db)).execute(
        "SELECT fixture_id FROM odds_snapshot_history ORDER BY id").fetchall()
    OddsSnapshotStore(db_path=str(db))
    depois = sqlite3.connect(str(db)).execute(
        "SELECT fixture_id FROM odds_snapshot_history ORDER BY id").fetchall()
    assert antes == depois


# --- 7. nenhuma linha (value/lado/linha) muda ---
def test_migracao_nenhuma_linha_muda(tmp_path):
    db = tmp_path / "legacy7.db"
    _seed_legacy_db(db, n_rows=3)
    antes = sqlite3.connect(str(db)).execute(
        "SELECT value_feed, lado, linha FROM odds_snapshot_history ORDER BY id"
    ).fetchall()
    OddsSnapshotStore(db_path=str(db))
    depois = sqlite3.connect(str(db)).execute(
        "SELECT value_feed, lado, linha FROM odds_snapshot_history ORDER BY id"
    ).fetchall()
    assert antes == depois


# --- 8. provider nao pode ficar nulo em novo snapshot valido ---
def test_provider_nao_pode_ficar_nulo(store):
    # INSERT OR IGNORE + NOT NULL: provider NULL => rejeitado (0 linhas).
    s_ok = _snap(provider="api_football")
    s_null = _snap(odd=1.85, provider=None)
    r = store.append_many([s_ok, s_null])
    with sqlite3.connect(store.db_path) as con:
        n = con.execute(
            "SELECT COUNT(*) FROM odds_snapshot_history").fetchone()[0]
        n_null = con.execute(
            "SELECT COUNT(*) FROM odds_snapshot_history WHERE provider IS NULL"
        ).fetchone()[0]
    assert n == 1          # soh o valido (provider nao-nulo) foi inserido
    assert n_null == 0


# --- 9. mesma fonte + mesmo snapshot = dedup ---
def test_mesmo_provider_mesmo_snapshot_dedup(store):
    a = _snap(provider="api_football")
    b = _snap(provider="api_football")  # identico
    r = store.append_many([a, b])
    assert r["inseridos"] == 1
    assert r["duplicados"] == 1


# --- 10. fonte diferente + mesmo snapshot = dois registros ---
def test_provider_diferente_dois_registros(store):
    a = _snap(provider="api_football", bookmaker="Bet365")
    b = _snap(provider="the_odds_api", bookmaker="Bet365")  # mesmo factual, outra fonte
    r = store.append_many([a, b])
    assert r["inseridos"] == 2  # ambos preservados (sem colisao multi-fonte)
    with sqlite3.connect(store.db_path) as con:
        provs = sorted(r[0] for r in con.execute(
            "SELECT provider FROM odds_snapshot_history").fetchall())
    assert provs == ["api_football", "the_odds_api"]


# --- 11. mudanca de odd = novo snapshot ---
def test_mudanca_de_odd_eh_novo_snapshot(store):
    a = _snap(odd=1.90, provider="api_football")
    b = _snap(odd=1.89, provider="api_football")
    r = store.append_many([a, b])
    assert r["inseridos"] == 2


# --- 12. hash V2 inclui provider ---
def test_hash_v2_inclui_provider():
    # mesmo factual, providers diferentes => hashes diferentes
    h1 = _snap(provider="api_football").hash()
    h2 = _snap(provider="the_odds_api").hash()
    assert h1 != h2
    # hash V2 difere do V1 (sem provider) para o mesmo factual
    h_v1 = _hash_v1(1, COLETA_PRE, "Bet365", "Goals Over/Under", 5, "gols",
                    None, "Over", 2.5, "Over 2.5", 1.90, None, "OK")
    assert _snap(provider="api_football").hash() != h_v1


# --- 13. migracao e idempotente (rodar de novo nao altera) ---
def test_migracao_idempotente(tmp_path):
    db = tmp_path / "legacy13.db"
    _seed_legacy_db(db, n_rows=4)
    OddsSnapshotStore(db_path=str(db))  # 1a migracao
    estado1 = sqlite3.connect(str(db)).execute(
        "SELECT id, provider, snapshot_hash FROM odds_snapshot_history ORDER BY id"
    ).fetchall()
    user_ver1 = sqlite3.connect(str(db)).execute("PRAGMA user_version").fetchone()[0]
    OddsSnapshotStore(db_path=str(db))  # 2a (idempotente)
    estado2 = sqlite3.connect(str(db)).execute(
        "SELECT id, provider, snapshot_hash FROM odds_snapshot_history ORDER BY id"
    ).fetchall()
    user_ver2 = sqlite3.connect(str(db)).execute("PRAGMA user_version").fetchone()[0]
    assert estado1 == estado2          # nenhum hash/provider reprocessado
    # 5F-C (multiprovider -> 2) + 5F-E2 (phase/canonical -> 4): idempotente.
    assert user_ver1 == user_ver2 == 4


# --- 14. repetir inicializacao nao altera banco ---
def test_repetir_init_nao_altera_banco(tmp_path):
    db = tmp_path / "legacy14.db"
    _seed_legacy_db(db, n_rows=3)
    OddsSnapshotStore(db_path=str(db))
    antes = sqlite3.connect(str(db)).execute(
        "SELECT COUNT(*), COUNT(DISTINCT snapshot_hash) FROM odds_snapshot_history"
    ).fetchone()
    for _ in range(3):
        OddsSnapshotStore(db_path=str(db))
    depois = sqlite3.connect(str(db)).execute(
        "SELECT COUNT(*), COUNT(DISTINCT snapshot_hash) FROM odds_snapshot_history"
    ).fetchone()
    assert antes == depois


# --- 15. rollback preserva DB em falha simulada ---
def test_rollback_preserva_db_em_falha(tmp_path, monkeypatch):
    db = tmp_path / "legacy15.db"
    _seed_legacy_db(db, n_rows=3)
    antes = sqlite3.connect(str(db)).execute(
        "SELECT id, snapshot_hash, odd, fixture_id FROM odds_snapshot_history "
        "ORDER BY id").fetchall()
    # injeta falha no recompute de hash
    import src.odds_coleta as m
    boom = [False]
    def _bomb(*a, **kw):
        boom[0] = True
        raise RuntimeError("falha simulada")
    monkeypatch.setattr(m, "_hash_identidade", _bomb)
    with pytest.raises(RuntimeError):
        OddsSnapshotStore(db_path=str(db))
    monkeypatch.undo()
    assert boom[0] is True  # chegou ao recompute
    # DB preservado: hashes V1 (nao recomputados), odds/fixtures intactos
    depois = sqlite3.connect(str(db)).execute(
        "SELECT id, snapshot_hash, odd, fixture_id FROM odds_snapshot_history "
        "ORDER BY id").fetchall()
    assert antes == depois  # rollback desfez todos os UPDATEs de hash
    # reinicializacao sem falha conclui a migracao (5F-C -> 2 + 5F-E2 -> 4)
    OddsSnapshotStore(db_path=str(db))
    con = sqlite3.connect(str(db))
    assert con.execute("PRAGMA user_version").fetchone()[0] == 4
    assert con.execute("SELECT COUNT(*) FROM odds_snapshot_history").fetchone()[0] == 3
    con.close()


# --- 16. adapter API-Football identifica provider corretamente ---
def test_adapter_api_football_identifica_provider():
    class LegacyClient:
        def get(self, endpoint, params=None, **kw):
            return []
    adapter = APIFootballOddsProvider(LegacyClient())
    assert adapter.provider_name == "api_football"


# --- 17. registry resolve api_football ---
def test_registry_resolve_api_football():
    assert PROVIDER_API_FOOTBALL in _PROVIDER_REGISTRY
    assert _PROVIDER_REGISTRY[PROVIDER_API_FOOTBALL] is APIFootballOddsProvider


# --- 18. provider inexistente falha de forma explicita ---
def test_provider_inexistente_falha_explicita():
    class FakeProvider:
        provider_name = "fonte_fantasma"
    with pytest.raises(ValueError):
        resolve_provider(FakeProvider())


# --- 19. coletor antigo (client legado) continua funcionando ---
def test_coletor_antigo_continua_funcionando(store):
    client = FakeClient(pre=PRE_ODDS, live=LIVE_ODDS)  # sem provider_name
    r = coletar_fixture(client, store, 42, live=True)
    assert r["consumo_api"] == 2
    assert r["inseridos"] > 0
    with sqlite3.connect(store.db_path) as con:
        provs = {r[0] for r in con.execute(
            "SELECT DISTINCT provider FROM odds_snapshot_history").fetchall()}
    assert provs == {"api_football"}  # client legado => api_football


# --- 20. nenhuma regra do motor e importada/modificada ---
def test_nenhuma_regra_do_motor_alterada_5fc():
    # o modulo de coleta continua sem depender do motor
    import src.odds_coleta as m
    assert not hasattr(m, "PROB_MIN_APROVAR")
    assert not hasattr(m, "PROB_MAX_APROVAR")
    assert not hasattr(m, "scan_pregame_opportunities")
    # regras congeladas permanecem intactas
    from src.politica_aprovacao import (
        PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1,
    )
    from src.settlement import _MARCA_CONVENCAO_CARTOES
    assert PROB_MIN_APROVAR == 0.70
    assert PROB_MAX_APROVAR == 0.97
    assert CONF_MIN_TOP1 == 0.60
    assert _MARCA_CONVENCAO_CARTOES == "amarelo=1, vermelho=2"