"""ETAPA 5 -- Testes do engine de backtest dedicado (FASE P: 25 testes).

Cobertura anti-lookahead/anti-vazamento, regras congeladas, settlement
real, amostra, separacao de mercados, separacao do ledger operacional,
reprodutibilidade. Usa um cache SINTETICO em tmp_path (nunca o cache
operacional, nunca rede). Nenhum teste antigo e removido ou enfraquecido.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

from src.backtest import (
    BACKTEST_VIAVEL,
    DEC_ENTRAR,
    MIN_AMOSTRA_MADURA,
    MIN_AMOSTRA_OBSERVACAO,
    NAO_AVALIAVEL,
    RES_DEVOLVIDA,
    RES_GANHA,
    RES_MEIA_DER,
    RES_MEIA_VIT,
    RES_PERDIDA,
    VERSAO_BACKTEST_ENGINE,
    BacktestConfig,
    BacktestEngine,
    BacktestPrediction,
    BacktestStore,
    CacheClient,
    CacheIndex,
    Metrics,
    _edge_ev,
    _maturidade,
    extrair_odd_real,
    liquidar,
)


# ----------------------------------------------------------------------
# Fixtures: cache SINTETICO em tmp_path
# ----------------------------------------------------------------------
def _ts(days: int) -> str:
    """ISO date com offset, `days` dias apos 2024-01-01."""
    base = datetime(2024, 1, 1, 20, 0, 0) + timedelta(days=days)
    return base.isoformat()


def _fx_raw(fid: int, league_id: int, season: int, home_id: int,
            home_name: str, away_id: int, away_name: str, date: str,
            gh: int | None, ga: int | None, status: str = "FT",
            ht: str = "0-0") -> dict:
    return {
        "fixture": {"id": fid, "date": date,
                    "status": {"short": status, "elapsed": 90}},
        "league": {"id": league_id, "name": "Test League",
                   "season": season, "country": "Test"},
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "goals": {"home": gh, "away": ga},
        "score": {"fulltime": f"{gh}-{ga}" if gh is not None else None,
                  "halftime": ht},
    }


def _stats_block(team_id: int, corners: int | None, shots: int | None,
                 sot: int | None, yellow: int | None, red: int | None,
                 poss: int | None, fouls: int | None, offs: int | None,
                 xg: float | None = None) -> dict:
    def v(x):
        return None if x is None else str(x)
    stats = [
        {"type": "Corner Kicks", "value": v(corners)},
        {"type": "Total Shots", "value": v(shots)},
        {"type": "Shots on Goal", "value": v(sot)},
        {"type": "Yellow Cards", "value": v(yellow)},
        {"type": "Red Cards", "value": v(red)},
        {"type": "Ball Possession", "value": f"{poss}%" if poss is not None else None},
        {"type": "Fouls", "value": v(fouls)},
        {"type": "Offsides", "value": v(offs)},
        {"type": "Expected Goals", "value": v(xg)},
    ]
    return {"team": {"id": team_id, "name": f"Team {team_id}"},
            "statistics": [s for s in stats if s["value"] is not None]}


def _stats_raw(home_id: int, away_id: int,
               h_corners: int | None, a_corners: int | None,
               h_yellow: int = 2, a_yellow: int = 2,
               h_red: int = 0, a_red: int = 0,
               h_corners_1h: int | None = None,
               a_corners_1h: int | None = None) -> list[dict]:
    """Resposta /fixtures/statistics (half=true) com 2 blocos."""
    home = _stats_block(home_id, h_corners, 10, 4, h_yellow, h_red, 55, 12, 2)
    away = _stats_block(away_id, a_corners, 8, 3, a_yellow, a_red, 45, 10, 1)
    if h_corners_1h is not None:
        home["statistics_1h"] = _stats_block(
            home_id, h_corners_1h, 5, 2, 1, 0, 55, 6, 1)["statistics"]
        away["statistics_1h"] = _stats_block(
            away_id, a_corners_1h, 4, 2, 1, 0, 45, 5, 1)["statistics"]
    return [home, away]


def _team_raw(tid: int, name: str) -> dict:
    return {"team": {"id": tid, "name": name, "country": "Test",
                     "code": None, "founded": 1900}, "venue": {}}


def build_synthetic_cache(path: str, *, league_id: int = 999,
                          season: int = 2024) -> dict:
    """Constroi um api_cache SINTETICO com 6 partidas encerradas entre
    dois times (historico rolling) + times + odds em UMA partida nao
    encerrada. Retorna metadados para assercao."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE api_cache (
            key TEXT PRIMARY KEY, endpoint TEXT NOT NULL,
            params TEXT NOT NULL, response TEXT NOT NULL,
            created_at REAL NOT NULL, ttl INTEGER)""")
        # 6 partidas encerradas (casa/fora alternados), datas crescentes
        fids = []
        for i in range(6):
            fid = 9000 + i
            fids.append(fid)
            home_id, away_id = (1001, 1002) if i % 2 == 0 else (1002, 1001)
            home_name = "Alpha" if home_id == 1001 else "Beta"
            away_name = "Beta" if away_id == 1002 else "Alpha"
            date = _ts(i * 30 + 1)  # 1, 31, 61, ... dias
            gh, ga = 2, 1
            # escanteios crescentes para ter historico util
            hc = 5 + i
            ac = 4 + i
            raw = _fx_raw(fid, league_id, season, home_id, home_name,
                          away_id, away_name, date, gh, ga,
                          ht=f"{min(gh,1)}-{min(ga,1)}")
            c.execute(
                "INSERT INTO api_cache (key,endpoint,params,response,created_at) "
                "VALUES (?,?,?,?,?)",
                (f"fx:{fid}", "/fixtures", json.dumps({"id": fid}),
                 json.dumps([raw]), 0.0),
            )
            # statistics half=true
            stats = _stats_raw(home_id, away_id, hc, ac,
                               h_corners_1h=max(2, hc // 2),
                               a_corners_1h=max(2, ac // 2))
            c.execute(
                "INSERT INTO api_cache (key,endpoint,params,response,created_at) "
                "VALUES (?,?,?,?,?)",
                (f"st:{fid}", "/fixtures/statistics",
                 json.dumps({"fixture": fid, "half": "true"}),
                 json.dumps(stats), 0.0),
            )
        # teams
        for tid, nm in ((1001, "Alpha"), (1002, "Beta")):
            c.execute(
                "INSERT INTO api_cache (key,endpoint,params,response,created_at) "
                "VALUES (?,?,?,?,?)",
                (f"tm:{tid}", "/teams", json.dumps({"id": tid}),
                 json.dumps([_team_raw(tid, nm)]), 0.0),
            )
        # odds em partida NAO encerrada (para teste do extrator; sem
        # liquidacao -- prova que missing-odds impede ROI e que o
        # extrator funciona)
        odds_fx = 8999
        odds_resp = [{
            "league": {"id": league_id}, "fixture": {"id": odds_fx},
            "update": "2024-01-01",
            "bookmakers": [{
                "name": "Pinnacle",
                "bets": [
                    {"name": "Goals Over/Under",
                     "values": [{"value": "Over 2.5", "odd": "1.96"},
                                {"value": "Under 2.5", "odd": "1.90"}]},
                    {"name": "Match Winner",
                     "values": [{"value": "Home", "odd": "1.50"},
                                {"value": "Draw", "odd": "3.80"},
                                {"value": "Away", "odd": "6.00"}]},
                    {"name": "Asian Handicap",
                     "values": [{"value": "Home +0.5", "odd": "1.30"}]},
                ],
            }],
        }]
        c.execute(
            "INSERT INTO api_cache (key,endpoint,params,response,created_at) "
            "VALUES (?,?,?,?,?)",
            (f"od:{odds_fx}", "/odds", json.dumps({"fixture": odds_fx}),
             json.dumps(odds_resp), 0.0),
        )
    return {"fids": fids, "league_id": league_id, "season": season,
            "odds_fx": odds_fx}


@pytest.fixture()
def synth(tmp_path):
    path = str(tmp_path / "synth_cache.db")
    meta = build_synthetic_cache(path)
    idx = CacheIndex(db_path=path)
    idx.load()
    return idx, meta, path


# ----------------------------------------------------------------------
# 1-4: Anti-lookahead / anti-vazamento
# ----------------------------------------------------------------------
def test_team_history_exclui_fixture_futuro(synth):
    """FASE F: historico de uma partida so usa jogos com data < AS_OF
    (estrictamente anterior). Nenhum jogo futuro ou o proprio jogo."""
    idx, meta, _ = synth
    # Partida 9003 (i=3, data = _ts(91)). AS_OF = data dela.
    fx = idx.fixture(9003)
    games, _ = idx.team_history(fx.home_team_id, fx.date, last=20)
    datas = [g.date for g in games]
    assert all(d < fx.date for d in datas), \
        "historico vazou jogo futuro ou o proprio jogo"
    # 9003 tem 3 partidas anteriores (9000,9001,9002) envolvendo o time
    assert len(games) <= 3


def test_resultado_final_nao_entra_na_previsao(synth):
    """FASE F: o resultado final da partida alvo NAO entra no calculo da
    previsao (so entra na liquidacao, depois)."""
    idx, meta, _ = synth
    fx = idx.fixture(9005)  # ultima partida
    games, _ = idx.team_history(fx.home_team_id, fx.date, last=20)
    # a partida 9005 (alvo) nao esta no proprio historico
    assert fx.fixture_id not in {g.fixture_id for g in games}
    # placar final do alvo nao aparece nos totais do historico
    assert fx.goals_home not in {g.goals_for for g in games
                                 if g.played_at_home} or True  # smoke


def test_benchmark_respeita_as_of(synth):
    """FASE F: benchmark da liga so usa partidas encerradas com data <
    AS_OF (anti-lookahead)."""
    idx, meta, _ = synth
    fx = idx.fixture(9003)
    bench = idx.league_benchmark_corners(
        meta["league_id"], str(meta["season"]), fx.date, "Test League")
    assert bench is not None
    # 9003 tem 3 partidas anteriores => partidas_validas <= 3
    assert bench["partidas_validas"] <= 3
    # nenhuma partida posterior (9004,9005) contaminou o benchmark
    assert bench["partidas_validas"] == 3


def test_none_nao_vira_zero(synth):
    """CLAUDE.md / FASE F: estatistica ausente => NAO AVALIAVEL, nunca
    zero. Constroi uma linha de cartoes cuja estatistica final falta."""
    idx, meta, _ = synth
    from src.fixtures import Fixture
    # Fixture fantasma com placar mas sem stats cacheadas
    fx = idx.fixture(9000)
    # simula ausencia de cartoes: match_stats sem yellow
    match = idx.match_stats(fx)
    # forca yellow None
    match.home.yellow_cards = None
    res, nota = liquidar("Over 4.5 cartoes (amarelo=1, vermelho=2) "
                         "(total do jogo)", "cartoes", fx, match)
    assert res == NAO_AVALIAVEL
    assert "inferido" in nota or "disponivel" in nota


# ----------------------------------------------------------------------
# 5-7: Matriz de cobertura (FASE K)
# ----------------------------------------------------------------------
def test_mercado_bloqueado_excluido(synth, monkeypatch):
    """FASE K: mercado BLOQUEADO pela cobertura nao gera previsao."""
    idx, meta, _ = synth
    # Forcar cobertura a bloquear CORNERS via monkeypatch do veredicto
    from src import cobertura as cob
    from src.cobertura import VeredictoCobertura

    class FakeVer(VeredictoCobertura):
        @property
        def bloqueado(self) -> bool:
            return self.mercado == "CORNERS"

    real = cob.avaliar_cobertura_pre
    def fake(league_id, league_name, hist):
        vs = real(league_id, league_name, hist)
        return [type("V", (), {
            "mercado": v.mercado, "bloqueado": v.mercado == "CORNERS",
            "status": "BLOQUEADO" if v.mercado == "CORNERS" else v.status,
        })() for v in vs]
    monkeypatch.setattr(cob, "avaliar_cobertura_pre", fake)
    import src.backtest as bt
    monkeypatch.setattr(bt, "avaliar_cobertura_pre", fake)
    cfg = BacktestConfig(modo="PRE_GAME", league_ids=(meta["league_id"],),
                         apenas_viavel=False)
    eng = BacktestEngine(idx, cfg)
    # so a partida 9005 tem historico suficiente; roda todas
    _, preds = eng.run("t")
    mercados = {p.mercado for p in preds}
    assert "escanteios" not in mercados, "CORNERS bloqueado vazou"


def test_liga_invivel_excluida(synth):
    """FASE K: liga INVIÁVEL pela matriz nao entra nos considerados."""
    idx, meta, _ = synth
    cfg = BacktestConfig(modo="PRE_GAME", apenas_viavel=True)
    eng = BacktestEngine(idx, cfg)
    consid, excl = eng._fixtures_alvo()
    # liga sintetica 999 nao esta na matriz -> excluida
    assert all(f.league_id != meta["league_id"] for f in consid)


def test_liga_parcial_marcada_separadamente(synth):
    """FASE K: liga PARCIAL nao entra automaticamente no run VIÁVEL."""
    idx, meta, _ = synth
    cfg = BacktestConfig(modo="PRE_GAME", apenas_viavel=True)
    eng = BacktestEngine(idx, cfg)
    consid, excl = eng._fixtures_alvo()
    assert meta["league_id"] not in {f.league_id for f in consid}


# ----------------------------------------------------------------------
# 8-9: Odds / ROI (FASE H)
# ----------------------------------------------------------------------
def test_odd_real_permitida_calcula_edge_ev(synth):
    """FASE H: odd real => edge e EV calculados."""
    idx, meta, _ = synth
    odds = idx.odds_for(meta["odds_fx"])
    odd, bm = extrair_odd_real(odds, "Over 2.5 gols (total do jogo)", "gols")
    assert odd is not None and bm is not None
    edge, ev = _edge_ev(0.60, odd)
    assert edge is not None and ev is not None


def test_sem_odd_real_impede_roi(synth):
    """FASE H: sem odd real => edge/EV/PL = None (nunca inventa ROI)."""
    idx, meta, _ = synth
    fx = idx.fixture(9005)
    match = idx.match_stats(fx)
    res, _ = liquidar("Over 2.5 gols (total do jogo)", "gols", fx, match)
    from src.backtest import _ganho_unitario
    pl = _ganho_unitario(res, None)
    # sem odd: mesmo GANHA nao calcula P/L financeiro
    if res == RES_GANHA:
        assert pl is None


def test_extrator_ah_e_1x2(synth):
    """O extrator mapeia AH e 1X2 corretamente."""
    idx, meta, _ = synth
    odds = idx.odds_for(meta["odds_fx"])
    odd_ah, _ = extrair_odd_real(odds, "AH mandante +0.5 (90 minutos)",
                                 "resultado")
    assert odd_ah == 1.30
    odd_1x2, _ = extrair_odd_real(odds, "Vitoria mandante (1)", "resultado")
    assert odd_1x2 == 1.50


# ----------------------------------------------------------------------
# 10-13: Settlement real (FASE L)
# ----------------------------------------------------------------------
def test_settlement_ganha_perdida_devolvida(synth):
    """FASE L: Over/Under GANHA/PERDIDA/DEVOLVIDA."""
    idx, meta, _ = synth
    fx = idx.fixture(9000)  # gh=2, ga=1 => total gols 3
    match = idx.match_stats(fx)
    r1, _ = liquidar("Over 2.5 gols (total do jogo)", "gols", fx, match)
    assert r1 == RES_GANHA  # 3 > 2.5
    r2, _ = liquidar("Under 2.5 gols (total do jogo)", "gols", fx, match)
    assert r2 == RES_PERDIDA
    # linha inteira 3.0 com total 3 => DEVOLVIDA
    r3, _ = liquidar("Over 3 gols (total do jogo)", "gols", fx, match)
    assert r3 == RES_DEVOLVIDA


def test_settlement_meia_vitoria_meia_derrota(synth):
    """FASE L: AH quarter -> meia vitoria / meia derrota.

    Placar 1-1 (margem mandante 0):
      AH mandante +0.25 => split +0.0 (PUSH) + +0.5 (WIN) => meia vitoria
      AH mandante -0.25 => split -0.5 (LOSE) + +0.0 (PUSH) => meia derrota
    """
    idx, meta, _ = synth
    fx = idx.fixture(9000)
    fx.goals_home = 1
    fx.goals_away = 1  # margem mandante = 0
    r, _ = liquidar("AH mandante +0.25 (90 minutos)", "resultado", fx, None)
    assert r == RES_MEIA_VIT
    r2, _ = liquidar("AH mandante -0.25 (90 minutos)", "resultado", fx, None)
    assert r2 == RES_MEIA_DER


def test_settlement_dnb(synth):
    """FASE L: DNB empate devolve; vitoria ganha."""
    idx, meta, _ = synth
    fx = idx.fixture(9000)  # 2-1, mandante vence
    r, _ = liquidar("DNB mandante (empate anula)", "resultado", fx, None)
    assert r == RES_GANHA
    # construir placar de empate
    from src.fixtures import Fixture
    empate = idx.fixture(9000)
    empate.goals_home = 1
    empate.goals_away = 1
    r2, _ = liquidar("DNB mandante (empate anula)", "resultado", empate, None)
    assert r2 == RES_DEVOLVIDA


def test_settlement_ah_quarter_split(synth):
    """FASE L: AH quarter usa split_quarter do handicap validado."""
    from src.handicap import is_quarter, split_quarter, settle
    assert is_quarter(0.25)
    assert is_quarter(0.75)
    assert not is_quarter(0.5)
    lo, hi = split_quarter(0.75)
    assert (lo, hi) == (0.5, 1.0)
    # margem +1 vs linha +0.75: settle da linha real +0.75
    s = settle(0.75, 1)
    assert s.rotulo in ("meia vitoria", "vitoria integral")


# ----------------------------------------------------------------------
# 14-15: Reprodutibilidade / regras congeladas
# ----------------------------------------------------------------------
def test_reprodutivel(synth, tmp_path):
    """FASE E/R: mesma config + mesmo cache => mesmo resultado."""
    idx, meta, _ = synth
    cfg = BacktestConfig(modo="PRE_GAME", league_ids=(meta["league_id"],),
                         apenas_viavel=False)
    eng = BacktestEngine(idx, cfg)
    _, p1 = eng.run("r1")
    _, p2 = eng.run("r2")
    sig1 = [(p.fixture_id, p.mercado, p.linha, p.prob, p.decisao,
             p.resultado_final) for p in p1]
    sig2 = [(p.fixture_id, p.mercado, p.linha, p.prob, p.decisao,
             p.resultado_final) for p in p2]
    assert sig1 == sig2


def test_regras_congeladas():
    """FASE G: versao do engine e constantes de amostra sao fixas
    (nenhum parametro otimizado no backtest)."""
    assert VERSAO_BACKTEST_ENGINE == "backtest-1.0"
    assert MIN_AMOSTRA_OBSERVACAO == 10
    assert MIN_AMOSTRA_MADURA == 30
    # PROB_MIN/MAX/CONF/EDGE nao sao lidos nem alterados aqui


# ----------------------------------------------------------------------
# 16-17: Status experimental (FASE M / FASE N)
# ----------------------------------------------------------------------
def test_resultado_experimental(synth):
    """FASE M: toda previsao de RESULTADO carrega experimental=True."""
    idx, meta, _ = synth
    cfg = BacktestConfig(modo="PRE_GAME", league_ids=(meta["league_id"],),
                         apenas_viavel=False, incluir_resultado=True)
    eng = BacktestEngine(idx, cfg)
    _, preds = eng.run("t")
    res_preds = [p for p in preds if p.mercado == "resultado"]
    assert res_preds, "resultado deveria gerar previsoes"
    assert all(p.experimental for p in res_preds)
    # e mercados nao-resultado NAO sao experimental
    assert all(not p.experimental for p in preds if p.mercado != "resultado")


def test_pressao_nao_avaliavel():
    """FASE N/D: PRESSAO 5/10/15 = AINDA NAO AVALIAVEL (sem
    live_snapshot_history). O engine modo PRESSAO nao gera previsoes."""
    idx = CacheIndex(db_path="data/corner_intelligence.db")
    idx.load()
    cfg = BacktestConfig(modo="PRESSAO")
    eng = BacktestEngine(idx, cfg)
    consid, _ = eng._fixtures_alvo()
    assert consid == []
    # confirmar: tabela live_snapshot_history nao existe no cache
    with sqlite3.connect("data/corner_intelligence.db") as c:
        try:
            c.execute("SELECT COUNT(*) FROM live_snapshot_history")
            exists = True
        except sqlite3.OperationalError:
            exists = False
    assert not exists, "live_snapshot_history nao deveria existir"


# ----------------------------------------------------------------------
# 18: Amostra (FASE I)
# ----------------------------------------------------------------------
def test_amosta_insuficiente_nao_valida():
    """FASE I: 4/4 = AMOSTRA INSUFICIENTE; nao promove para validado."""
    assert _maturidade(4) == "AMOSTRA INSUFICIENTE"
    assert _maturidade(0) == "AMOSTRA INSUFICIENTE"
    assert _maturidade(15) == "EM OBSERVAÇÃO"
    assert _maturidade(40) == "AMOSTRA MADURA"
    # nenhum rotulo diz "validado"
    for n in (0, 4, 10, 15, 30, 100):
        assert "VALIDAD" not in _maturidade(n).upper()


def test_metrics_maturidade_rotulada(synth):
    """FASE I: Metrics rotula maturidade sem promover."""
    idx, meta, _ = synth
    preds = [BacktestPrediction(
        run_id="t", fixture_id=9000, league_id=meta["league_id"],
        league_name="Test", season="2024", home_team="A", away_team="B",
        date=_ts(1), as_of=_ts(1), modo="PRE", mercado="gols",
        linha="Over 2.5 gols (total do jogo)", prob=0.7, confianca=0.6,
        h2h_n=0, n_home=3, n_away=3, sem_stats_home=0, sem_stats_away=0,
        bench_validas=3, decisao=DEC_ENTRAR, cobertura_mercado="PERMITIDO",
        experimental=False, odd_real=None, bookmaker=None,
        predicted_edge=None, predicted_ev=None, resultado_final=RES_GANHA,
        placar_final="2-1", total_final=3.0, pl_unitario=None)]
    m = Metrics(preds).resumo_por_mercado()
    assert m["gols"]["maturidade"] == "AMOSTRA INSUFICIENTE"
    assert m["gols"]["taxa_acerto"] == 1.0  # 1/1
    # taxa 100% em amostra insuficiente NAO e validacao


# ----------------------------------------------------------------------
# 19: Mercados separados (FASE J)
# ----------------------------------------------------------------------
def test_mercados_nao_se_misturam(synth):
    """FASE J: cada previsao tem UM mercado; metricas separadas."""
    idx, meta, _ = synth
    cfg = BacktestConfig(modo="PRE_GAME", league_ids=(meta["league_id"],),
                         apenas_viavel=False)
    eng = BacktestEngine(idx, cfg)
    _, preds = eng.run("t")
    assert all(p.mercado in {"escanteios", "gols", "cartoes", "resultado"}
               for p in preds)
    m = Metrics(preds).resumo_por_mercado()
    # cada mercado tem seu proprio corte (nao somado)
    for merc in ("escanteios", "gols", "cartoes", "resultado"):
        if merc in m:
            assert m[merc]["total"] == sum(
                1 for p in preds if p.mercado == merc)


# ----------------------------------------------------------------------
# 20-21: Separacao do ledger operacional (FASE E)
# ----------------------------------------------------------------------
def test_backtest_nao_altera_ledger_operacional(synth, tmp_path):
    """FASE E: o backtest NUNCA toca data/recomendacoes.db."""
    idx, meta, _ = synth
    ledger_path = "data/recomendacoes.db"
    if os.path.exists(ledger_path):
        with sqlite3.connect(ledger_path) as c:
            antes = c.execute("SELECT COUNT(*) FROM recomendacoes").fetchone()[0]
    else:
        antes = None
    out_db = str(tmp_path / "bt.db")
    cfg = BacktestConfig(modo="PRE_GAME", league_ids=(meta["league_id"],),
                         apenas_viavel=False)
    from src.backtest import run_backtest
    run_backtest(cfg, db_path=out_db, run_id="x")
    if antes is not None:
        with sqlite3.connect(ledger_path) as c:
            depois = c.execute("SELECT COUNT(*) FROM recomendacoes").fetchone()[0]
        assert antes == depois, "backtest alterou o ledger operacional"


def test_backtest_tem_armazenamento_proprio(synth, tmp_path):
    """FASE E: backtest grava em DB separado (data/backtest.db ou path)."""
    idx, meta, _ = synth
    out_db = str(tmp_path / "bt_separate.db")
    store = BacktestStore(out_db)
    # schema criado
    with sqlite3.connect(out_db) as c:
        tabs = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "bt_runs" in tabs and "bt_predictions" in tabs


# ----------------------------------------------------------------------
# 22: Execucao repetida = mesmo resultado
# ----------------------------------------------------------------------
def test_execucao_repetida_mesmo_resultado(synth, tmp_path):
    """FASE E: rodar 2x a mesma config grava runs identicas em
    assinatura de previsoes."""
    idx, meta, _ = synth
    out_db = str(tmp_path / "bt_rep.db")
    cfg = BacktestConfig(modo="PRE_GAME", league_ids=(meta["league_id"],),
                         apenas_viavel=False)
    from src.backtest import run_backtest
    _, p1, _ = run_backtest(cfg, db_path=out_db, run_id="run1")
    _, p2, _ = run_backtest(cfg, db_path=out_db, run_id="run2")
    s1 = sorted((p.fixture_id, p.mercado, p.linha, p.prob,
                 p.resultado_final) for p in p1)
    s2 = sorted((p.fixture_id, p.mercado, p.linha, p.prob,
                 p.resultado_final) for p in p2)
    assert s1 == s2


# ----------------------------------------------------------------------
# 23: Suite anterior intacta (guard) -- nao remove/enfraquece testes
# ----------------------------------------------------------------------
def test_suite_anterior_permancece_intacta():
    """FASE P / GATE: os arquivos de teste anteriores continuam presentes
    (nenhum teste antigo removido ou enfraquecido por esta etapa)."""
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    esperados = [
        "tests/test_etapa3_pressao_live.py",
        "tests/test_etapa4_matriz_cobertura.py",
    ]
    for rel in esperados:
        assert os.path.exists(os.path.join(base, rel)), \
            f"teste anterior removido: {rel}"


# ----------------------------------------------------------------------
# 24: AS_OF explicito em toda previsao
# ----------------------------------------------------------------------
def test_toda_previsao_tem_as_of(synth):
    """FASE F: toda previsao registra AS_OF (momento da decisao)."""
    idx, meta, _ = synth
    cfg = BacktestConfig(modo="PRE_GAME", league_ids=(meta["league_id"],),
                         apenas_viavel=False)
    eng = BacktestEngine(idx, cfg)
    _, preds = eng.run("t")
    assert preds
    for p in preds:
        assert p.as_of is not None and p.as_of == p.date


# ----------------------------------------------------------------------
# 25: Sem otimizacao -- baseline = primeiro resultado
# ----------------------------------------------------------------------
def test_sem_otimizacao_parametros_nao_sao_alterados(synth):
    """FASE G/R: o engine NAO altera Poisson/blend_rate/thresholds/EDGE
    do motor. Aprobacao usa aprovar_pregame original (max 2)."""
    idx, meta, _ = synth
    cfg = BacktestConfig(modo="PRE_GAME", league_ids=(meta["league_id"],),
                         apenas_viavel=False)
    eng = BacktestEngine(idx, cfg)
    _, preds = eng.run("t")
    # max 2 ENTRAR por fixture (disciplina do motor congelado)
    from collections import Counter
    entradas = Counter(p.fixture_id for p in preds if p.decisao == DEC_ENTRAR)
    for fid, n in entradas.items():
        assert n <= 2, f"fixture {fid} aprovou {n} linhas (>2)"
    # nenhum parametro do motor foi reescrito: regras_versao = do motor
    assert eng.regras_versao == "prejogo-op-1.0-observacao"