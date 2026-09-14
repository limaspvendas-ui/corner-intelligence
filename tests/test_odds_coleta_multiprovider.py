"""Testes determinísticos da coleta prospectiva multiprovider (Etapa 5F-E2).

Sem rede. Cobre os casos A-N da Seção 11:
A) 5Dollar corner opening
B) 5Dollar corner closing
C) 5Dollar corner inplay
D) 5Dollar cards
E) 5Dollar asian cards
F) The Odds API totals
G) The Odds API h2h
H) mesmo bookmaker em dois providers => provider continua distinto
I) mesmo snapshot repetido => não duplica
J) odd mudou => novo snapshot append-only
K) mercado desconhecido => UNKNOWN, sem invenção
L) NULL != ZERO
M) provider != bookmaker
N) proveniência completa

Reusa OddsSnapshotStore.append_many (único caminho append) + _construir_snapshots_multiprovider.
Nenhum módulo decisório é importado. Motor intacto.
"""
import sqlite3

import pytest

from src.multifonte import NormalizedOdd, ST_OK
from src.odds_coleta import (
    OddsSnapshotStore,
    OddSnapshot,
    _construir_snapshots_multiprovider,
    _classificar_5dollar,
    _classificar_theodds,
    _phase_5dollar,
    _migrar_para_5fe2,
    _USER_VERSION_5FE2,
    PROVIDER_FIVE_DOLLAR,
    PROVIDER_THE_ODDS_API,
    PHASE_OPENING,
    PHASE_CLOSING,
    PHASE_INPLAY,
    MK_TOTAL_CORNERS,
    MK_ASIAN_CORNERS,
    MK_TOTAL_CARDS,
    MK_ASIAN_CARDS,
    MK_TOTAL_GOALS,
    MK_MATCH_RESULT,
    MK_ASIAN_HANDICAP,
    MK_UNKNOWN,
    COLETA_PRE,
    COLETA_LIVE,
)


# ---- helpers ---------------------------------------------------------------
def _odd5d(market, phase_key, side, line, price, coleta="pre_match",
           bookmaker="Bet365", fixture_id="2062437761"):
    """NormalizedOdd no shape que _parse_odds do 5Dollar produz."""
    return NormalizedOdd(
        provider=PROVIDER_FIVE_DOLLAR, bookmaker=bookmaker,
        fixture_provider_id=fixture_id, fixture_corner_id=None,
        market=market, submarket=f"{market}/{phase_key}",
        side=side, line=line, price=price, timestamp=None,
        coleta_tipo=coleta, status=ST_OK,
    )


def _oddto(market, side, line, price, bookmaker="Bet365",
           fixture_id="ev-1", coleta="pre_match"):
    """NormalizedOdd no shape que TheOddsAPIProvider.fetch_odds produz."""
    return NormalizedOdd(
        provider=PROVIDER_THE_ODDS_API, bookmaker=bookmaker,
        fixture_provider_id=fixture_id, fixture_corner_id=None,
        market=market, submarket=None, side=side, line=line,
        price=price, timestamp="2026-09-15T19:00:00Z",
        coleta_tipo=coleta, status=ST_OK,
    )


def _build_store(tmp_path):
    db = str(tmp_path / "mp.db")
    return OddsSnapshotStore(db_path=db)


def _persist(store, normalized, provider, fixture_id_int=9001,
             collected_at=1700000000.0, fixture_date="2026-09-15T19:00:00Z"):
    snaps = _construir_snapshots_multiprovider(
        normalized, fixture_id_int=fixture_id_int,
        collected_at=collected_at, fixture_date=fixture_date, provider=provider,
    )
    return snaps, store.append_many(snaps)


def _row(store, where="1=1", params=()):
    with store._connect() as conn:
        return conn.execute(
            f"SELECT provider, fixture_id, coleta_tipo, bookmaker, bet_name, "
            f"bet_id, familia, subfamilia, lado, linha, value_feed, odd, "
            f"suspended, status, motivo, phase, market_canonical, "
            f"market_original, snapshot_hash "
            f"FROM odds_snapshot_history WHERE {where}",
            params,
        ).fetchall()


# ---- classificadores (unidade) ---------------------------------------------
def test_classificar_5dollar_cobertura():
    assert _classificar_5dollar("corner") == ("escanteios", None, MK_TOTAL_CORNERS)
    assert _classificar_5dollar("corner_asian") == ("escanteios", None, MK_ASIAN_CORNERS)
    assert _classificar_5dollar("goalline") == ("gols", None, MK_TOTAL_GOALS)
    assert _classificar_5dollar("cards") == ("cartoes", None, MK_TOTAL_CARDS)
    assert _classificar_5dollar("cards_asian") == ("cartoes", None, MK_ASIAN_CARDS)
    assert _classificar_5dollar("asian") == ("resultado", "AH", MK_ASIAN_HANDICAP)
    assert _classificar_5dollar("1x2") == ("resultado", "1X2", MK_MATCH_RESULT)
    # btts fica A CONFIRMAR => UNKNOWN (sem invenção)
    assert _classificar_5dollar("btts") == ("UNMAPPED", None, MK_UNKNOWN)
    # desconhecido => UNKNOWN
    assert _classificar_5dollar("exotic") == ("UNMAPPED", None, MK_UNKNOWN)


def test_classificar_theodds_cobertura():
    assert _classificar_theodds("h2h") == ("resultado", "1X2", MK_MATCH_RESULT)
    assert _classificar_theodds("totals") == ("gols", None, MK_TOTAL_GOALS)
    assert _classificar_theodds("spreads") == ("resultado", "AH", MK_ASIAN_HANDICAP)
    # The Odds API NÃO oferece corners/cards (Seção 3); demais => UNKNOWN
    assert _classificar_theodds("h2h_lay") == ("UNMAPPED", None, MK_UNKNOWN)
    assert _classificar_theodds("alternate_totals") == ("UNMAPPED", None, MK_UNKNOWN)
    assert _classificar_theodds("corners") == ("UNMAPPED", None, MK_UNKNOWN)


def test_phase_5dollar_preserva_classificacao_fonte():
    assert _phase_5dollar("corner/opening") == PHASE_OPENING
    assert _phase_5dollar("corner/closing") == PHASE_CLOSING
    assert _phase_5dollar("corner/close") == PHASE_CLOSING
    assert _phase_5dollar("corner/inplay") == PHASE_INPLAY
    assert _phase_5dollar("corner/in_play") == PHASE_INPLAY
    # sem classificação explícita => None (nunca inventada)
    assert _phase_5dollar(None) is None
    assert _phase_5dollar("") is None
    assert _phase_5dollar("corner/unknownphase") is None


# ---- A: 5Dollar corner opening ---------------------------------------------
def test_A_5dollar_corner_opening(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(store, [_odd5d("corner", "opening", "over", 7.5, 1.85)],
                           PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.provider == PROVIDER_FIVE_DOLLAR
    assert s.market_canonical == MK_TOTAL_CORNERS
    assert s.familia == "escanteios"
    assert s.phase == PHASE_OPENING
    assert s.coleta_tipo == COLETA_PRE     # opening => pre_match
    assert s.lado == "over"
    assert s.linha == 7.5
    assert s.odd == 1.85
    assert s.status == "OK"
    assert s.market_original == "corner"
    assert s.bookmaker == "Bet365"


# ---- B: 5Dollar corner closing ---------------------------------------------
def test_B_5dollar_corner_closing(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(store, [_odd5d("corner", "closing", "under", 8.0, 2.05)],
                           PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.phase == PHASE_CLOSING
    assert s.coleta_tipo == COLETA_PRE     # closing => pre_match
    assert s.market_canonical == MK_TOTAL_CORNERS
    assert s.lado == "under"


# ---- C: 5Dollar corner inplay ----------------------------------------------
def test_C_5dollar_corner_inplay(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(
        store, [_odd5d("corner", "inplay", "over", 9.5, 1.95, coleta="live")],
        PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.phase == PHASE_INPLAY
    assert s.coleta_tipo == COLETA_LIVE    # inplay => live
    assert s.market_canonical == MK_TOTAL_CORNERS


# ---- D: 5Dollar cards ------------------------------------------------------
def test_D_5dollar_cards(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(store, [_odd5d("cards", "opening", "over", 3.5, 2.10)],
                           PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.market_canonical == MK_TOTAL_CARDS
    assert s.familia == "cartoes"
    assert s.phase == PHASE_OPENING


# ---- E: 5Dollar asian cards ------------------------------------------------
def test_E_5dollar_asian_cards(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(
        store, [_odd5d("cards_asian", "opening", "home", 1.0, 1.90)],
        PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.market_canonical == MK_ASIAN_CARDS
    assert s.familia == "cartoes"
    assert s.lado == "home"
    assert s.linha == 1.0


# ---- F: The Odds API totals ------------------------------------------------
def test_F_theodds_totals(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(store, [_oddto("totals", "Over", 2.5, 1.95)],
                           PROVIDER_THE_ODDS_API)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.provider == PROVIDER_THE_ODDS_API
    assert s.market_canonical == MK_TOTAL_GOALS
    assert s.familia == "gols"
    # The Odds API NÃO classifica fase => None (nunca inventada)
    assert s.phase is None
    assert s.coleta_tipo == COLETA_PRE
    assert s.lado == "Over"
    assert s.linha == 2.5
    assert s.market_original == "totals"


# ---- G: The Odds API h2h ---------------------------------------------------
def test_G_theodds_h2h(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(
        store, [_oddto("h2h", "Arsenal", None, 2.30)],
        PROVIDER_THE_ODDS_API)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.market_canonical == MK_MATCH_RESULT
    assert s.familia == "resultado"
    assert s.subfamilia == "1X2"
    assert s.lado == "Arsenal"
    assert s.linha is None
    assert s.phase is None


# ---- H: mesmo bookmaker em dois providers => provider distinto -------------
def test_H_mesmo_bookmaker_provider_distinto(tmp_path):
    store = _build_store(tmp_path)
    nos = [
        _odd5d("corner", "opening", "over", 7.5, 1.85, bookmaker="Bet365"),
        _oddto("totals", "Over", 2.5, 1.95, bookmaker="Bet365"),
    ]
    snaps5d, g5d = _persist(
        store, [nos[0]], PROVIDER_FIVE_DOLLAR, fixture_id_int=9001)
    snapsto, gto = _persist(
        store, [nos[1]], PROVIDER_THE_ODDS_API, fixture_id_int=9001)
    assert g5d["inseridos"] == 1 and gto["inseridos"] == 1
    # hashes distintas (provider entra na identidade)
    assert snaps5d[0].hash() != snapsto[0].hash()
    # ambos persistidos com provider distinto, mesmo bookmaker
    rows = _row(store)
    assert len(rows) == 2
    provs = {r[0] for r in rows}
    assert provs == {PROVIDER_FIVE_DOLLAR, PROVIDER_THE_ODDS_API}
    bks = {r[3] for r in rows}
    assert bks == {"Bet365"}  # mesmo bookmaker, providers distintos


# ---- I: mesmo snapshot repetido => não duplica ----------------------------
def test_I_mesmo_snapshot_nao_duplica(tmp_path):
    store = _build_store(tmp_path)
    no = _odd5d("corner", "opening", "over", 7.5, 1.85)
    _, g1 = _persist(store, [no], PROVIDER_FIVE_DOLLAR, collected_at=1700000000.0)
    # mesmo snapshot factual (mesma hash V3) => ignorado
    _, g2 = _persist(store, [no], PROVIDER_FIVE_DOLLAR, collected_at=1700000000.0)
    assert g1["inseridos"] == 1
    assert g2["inseridos"] == 0
    assert g2["duplicados"] == 1
    rows = _row(store)
    assert len(rows) == 1


# ---- J: odd mudou => novo snapshot append-only -----------------------------
def test_J_odd_mudou_novo_snapshot(tmp_path):
    store = _build_store(tmp_path)
    _, g1 = _persist(store, [_odd5d("corner", "opening", "over", 7.5, 1.85)],
                     PROVIDER_FIVE_DOLLAR, collected_at=1700000000.0)
    # odd mudou 1.85 -> 1.90 => nova hash => novo snapshot (append-only)
    _, g2 = _persist(store, [_odd5d("corner", "opening", "over", 7.5, 1.90)],
                     PROVIDER_FIVE_DOLLAR, collected_at=1700000100.0)
    assert g1["inseridos"] == 1 and g2["inseridos"] == 1
    rows = _row(store)
    assert len(rows) == 2
    odds = sorted(r[11] for r in rows)
    assert odds == [1.85, 1.90]   # ambos preservados (nunca sobrescrito)


# ---- K: mercado desconhecido => UNKNOWN, sem invenção ----------------------
def test_K_mercado_desconhecido_unknown(tmp_path):
    store = _build_store(tmp_path)
    # btts: A CONFIRMAR => UNKNOWN (sem invenção de familia/canonical)
    snaps, grav = _persist(store, [_odd5d("btts", "opening", "yes", None, 1.80)],
                           PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.market_canonical == MK_UNKNOWN
    assert s.familia == "UNMAPPED"
    assert s.status == "UNMAPPED"
    assert s.motivo is not None
    # mercado cru preservado (não perdido)
    assert s.market_original == "btts"


def test_K_theodds_mercado_nao_suportado_unknown(tmp_path):
    store = _build_store(tmp_path)
    # The Odds API NÃO oferece corners => UNKNOWN
    snaps, grav = _persist(store, [_oddto("corners", "Over", 9.5, 2.00)],
                           PROVIDER_THE_ODDS_API)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.market_canonical == MK_UNKNOWN
    assert s.status == "UNMAPPED"


# ---- L: NULL != ZERO -------------------------------------------------------
def test_L_null_distinto_de_zero(tmp_path):
    store = _build_store(tmp_path)
    # linha=0.0 (zero explícito) vs linha=None (ausente): distintos, ambos
    # preservados — 0 nunca vira None, None nunca vira 0.
    nos = [
        _odd5d("corner", "opening", "over", 0.0, 1.85),
        _odd5d("corner", "opening", "over", None, 1.85),
    ]
    snaps, grav = _persist(store, nos, PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 2
    linhas = sorted((r[9] for r in _row(store)),
                    key=lambda x: (x is None, x))  # coluna linha
    assert linhas == [0.0, None]
    # zero explícito mantido como 0.0
    z = [s for s in snaps if s.linha == 0.0][0]
    assert z.linha == 0.0 and z.linha is not None
    # ausente mantido como None
    n = [s for s in snaps if s.linha is None][0]
    assert n.linha is None


def test_L_odd_none_nao_vira_zero(tmp_path):
    store = _build_store(tmp_path)
    # price=None => odd NULL (nunca 0); status INVALID com motivo
    snaps, grav = _persist(
        store, [_odd5d("corner", "opening", "over", 7.5, None)],
        PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 1
    s = snaps[0]
    assert s.odd is None          # NULL, nunca zero
    assert s.odd != 0
    assert s.status == "INVALID"
    assert "ausente" in (s.motivo or "")


# ---- M: provider != bookmaker ----------------------------------------------
def test_M_provider_diferente_bookmaker(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(
        store, [_odd5d("corner", "opening", "over", 7.5, 1.85, bookmaker="Bet365")],
        PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 1
    s = snaps[0]
    # PROVIDER (fonte API) != BOOKMAKER (casa de apostas) — conceitos distintos
    assert s.provider == PROVIDER_FIVE_DOLLAR
    assert s.bookmaker == "Bet365"
    assert s.provider != s.bookmaker
    rows = _row(store)
    assert rows[0][0] == PROVIDER_FIVE_DOLLAR  # provider
    assert rows[0][3] == "Bet365"              # bookmaker


# ---- N: proveniência completa ----------------------------------------------
def test_N_proveniencia_completa(tmp_path):
    store = _build_store(tmp_path)
    snaps, grav = _persist(
        store, [_odd5d("corner", "closing", "under", 8.0, 2.05)],
        PROVIDER_FIVE_DOLLAR, fixture_id_int=4242,
        collected_at=1700001234.0, fixture_date="2026-09-15T19:00:00Z")
    assert grav["inseridos"] == 1
    rows = _row(store, "fixture_id=?", (4242,))
    assert len(rows) == 1
    r = rows[0]
    # (provider, fixture_id, coleta_tipo, bookmaker, bet_name, bet_id,
    #  familia, subfamilia, lado, linha, value_feed, odd, suspended,
    #  status, motivo, phase, market_canonical, market_original, hash)
    assert r[0] == PROVIDER_FIVE_DOLLAR
    assert r[1] == 4242
    assert r[2] == COLETA_PRE
    assert r[3] == "Bet365"
    assert r[4] == "corner"              # bet_name = market_original
    assert r[6] == "escanteios"
    assert r[8] == "under"
    assert r[9] == 8.0
    assert r[10] == "under 8.0"          # value_feed legivel
    assert r[11] == 2.05
    assert r[13] == "OK"
    assert r[15] == PHASE_CLOSING
    assert r[16] == MK_TOTAL_CORNERS
    assert r[17] == "corner"             # market_original preservado
    assert r[18] is not None and len(r[18]) == 64  # snapshot_hash sha256


# ---- migração aditiva preserva histórico -----------------------------------
def test_migracao_5fe2_aditiva_preserva_historico(tmp_path):
    db = str(tmp_path / "mig.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE odds_snapshot_history (id INTEGER, provider TEXT, "
        "snapshot_hash TEXT); "
        "INSERT INTO odds_snapshot_history VALUES (1,'api_football','h1');"
        "INSERT INTO odds_snapshot_history VALUES (2,'api_football','h2');")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    # aplica migração 5F-E2
    conn = sqlite3.connect(db)
    _migrar_para_5fe2(conn)
    conn.close()
    conn = sqlite3.connect(db)
    uv = conn.execute("PRAGMA user_version").fetchone()[0]
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(odds_snapshot_history)").fetchall()}
    n = conn.execute("SELECT COUNT(*) FROM odds_snapshot_history").fetchone()[0]
    phase_nn = conn.execute(
        "SELECT COUNT(*) FROM odds_snapshot_history WHERE phase IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert uv == _USER_VERSION_5FE2
    assert {"phase", "market_canonical", "market_original"} <= cols
    assert n == 2                      # histórico preservado
    assert phase_nn == 0               # históricos ficam NULL


# ---- idempotência: reaplicar migração é no-op ------------------------------
def test_migracao_5fe2_idempotente(tmp_path):
    db = str(tmp_path / "mig2.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 3")
    conn.executescript("CREATE TABLE odds_snapshot_history (id INTEGER);")
    conn.commit()
    _migrar_para_5fe2(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    # segunda aplicação: no-op (já em 4)
    _migrar_para_5fe2(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    conn.close()


# ---- isolamento: motor não importa odds_coleta multiprovider ---------------
def test_motor_nao_importa_multiprovider_decisao():
    import os
    motor = ["analysis", "politica_aprovacao", "policy", "settlement",
             "calibration", "backtest", "prejogo_opportunity", "live_pressure",
             "odds", "identity", "ao_vivo"]
    base = os.path.join("src")
    for m in motor:
        path = os.path.join(base, m + ".py")
        if not os.path.exists(path):
            continue
        txt = open(path, encoding="utf-8", errors="ignore").read()
        # odds_coleta pode ser importado (camada de coleta), MAS coletar_multiprovider
        # /_construir_snapshots_multiprovider NÃO devem ser consumidos pelo motor.
        assert "coletar_multiprovider" not in txt, \
            f"motor {m} consome coletar_multiprovider"
        assert "_construir_snapshots_multiprovider" not in txt, \
            f"motor {m} consome _construir_snapshots_multiprovider"


# ---- coletar_multiprovider com adapter mock (sem rede) ---------------------
class _Mock5DollarAdapter:
    provider_name = PROVIDER_FIVE_DOLLAR
    _calls = 1

    def fetch_odds(self, fixture_id, market="corner"):
        return [_odd5d(market, "opening", "over", 7.5, 1.85)]


def test_coletar_multiprovider_com_mock_sem_rede(tmp_path):
    from src.odds_coleta import coletar_multiprovider
    store = _build_store(tmp_path)
    r = coletar_multiprovider(
        PROVIDER_FIVE_DOLLAR, store, fixture_id_int=7777, market="corner",
        adapter=_Mock5DollarAdapter())
    assert "erro" not in r          # sucesso: sem chave de erro
    assert r["inseridos"] == 1
    assert r["por_market_canonical"].get(MK_TOTAL_CORNERS) == 1
    assert r["por_phase"].get(PHASE_OPENING) == 1
    assert r["provider"] == PROVIDER_FIVE_DOLLAR


# ---- 5Dollar e The Odds API preservam fases distintas na persistência ------
def test_fases_distintas_5dollar_todos_preservados(tmp_path):
    store = _build_store(tmp_path)
    nos = [
        _odd5d("corner", "opening", "over", 7.5, 1.85, coleta="pre_match"),
        _odd5d("corner", "closing", "over", 7.5, 1.80, coleta="pre_match"),
        _odd5d("corner", "inplay", "over", 9.5, 1.95, coleta="live"),
    ]
    snaps, grav = _persist(store, nos, PROVIDER_FIVE_DOLLAR)
    assert grav["inseridos"] == 3
    phases = sorted(s.phase for s in snaps)
    assert phases == [PHASE_CLOSING, PHASE_INPLAY, PHASE_OPENING]
    # coleta_tipo segue a fase (opening/closing => pre_match; inplay => live)
    tipos = {s.phase: s.coleta_tipo for s in snaps}
    assert tipos[PHASE_OPENING] == COLETA_PRE
    assert tipos[PHASE_CLOSING] == COLETA_PRE
    assert tipos[PHASE_INPLAY] == COLETA_LIVE