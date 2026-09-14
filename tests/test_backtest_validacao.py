"""Testes da validacao temporal fora da amostra (Etapa 5B).

Cobre FASE I: split temporal correto, anti-lookahead entre blocos, holdout nao
altera regras, walk-forward cronologico, nenhuma informacao futura, nenhum
missing vira zero, cartoes NA preservados, reprodutibilidade, regras congeladas,
e a equivalencia fundamental (fatiar previsoes por data == rodar o engine por
janela -- base de todo o metodo).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

from src.backtest import (
    BacktestConfig,
    BacktestEngine,
    CacheIndex,
    VERSAO_BACKTEST_ENGINE,
)
from src.backtest_validacao import (
    blocos_walk_forward,
    carregar_previsoes,
    cutoff_mediano,
    dividir_holdout,
    metricas_por_mercado,
    validar_temporal,
    _metricas,
)
from src.live_opportunity import (
    CONF_MIN_TOP1,
    PROB_MAX_APROVAR,
    PROB_MIN_APROVAR,
)
from src.prejogo_opportunity import VERSAO_PREJOGO_OP


# ----------------------------------------------------------------------
# Helpers: previsoes sinteticas em memoria (sem DB, sem rede)
# ----------------------------------------------------------------------
def _pred(date, fixture_id, mercado, linha, prob, decisao, resultado):
    return {
        "run_id": "test", "fixture_id": fixture_id, "league_id": 999,
        "league_name": "Liga Sint", "season": 2026, "home_team": "A",
        "away_team": "B", "date": date, "as_of": date, "modo": "PRE",
        "mercado": mercado, "linha": linha, "prob": prob, "confianca": 0.8,
        "h2h_n": 0, "n_home": 10, "n_away": 10, "sem_stats_home": 0,
        "sem_stats_away": 0, "bench_validas": 50, "decisao": decisao,
        "cobertura_mercado": "PERMITIDO", "experimental": mercado == "resultado",
        "odd_real": None, "bookmaker": None, "predicted_edge": None,
        "predicted_ev": None, "resultado_final": resultado, "placar_final": "1-1",
        "total_final": 3.0, "pl_unitario": None,
    }


def _sint(date_off, fx, mercado, prob, decisao, resultado):
    d = (datetime(2026, 1, 1) + timedelta(days=date_off)).isoformat()
    return _pred(d, fx, mercado, f"Over 2.5 {mercado}", prob, decisao, resultado)


def _corpus():
    """Corpus sintetico: 12 fixtures, datas crescentes, 4 mercados."""
    preds = []
    fx = 1
    for off in range(0, 120, 10):  # 12 fixtures, 10 em 10 dias
        for mercado, prob, res in (
            ("gols", 0.94, "GANHA"),
            ("escanteios", 0.92, "GANHA"),
            ("cartoes", 0.95, "NÃO AVALIÁVEL"),
            ("resultado", 0.93, "GANHA"),
        ):
            preds.append(_pred(
                (datetime(2026, 1, 1) + timedelta(days=off)).isoformat(),
                fx, mercado, f"Over 2.5 {mercado}", prob, "ENTRAR", res))
        # alguns DESCARTAR para ter mistura
        preds.append(_pred(
            (datetime(2026, 1, 1) + timedelta(days=off)).isoformat(),
            fx, "gols", "Under 3.5 gols", 0.40, "DESCARTAR", "PERDIDA"))
        fx += 1
    return preds


# ----------------------------------------------------------------------
# 1. Split temporal correto
# ----------------------------------------------------------------------
def test_split_temporal_correto():
    preds = _corpus()
    cutoff = cutoff_mediano(preds)
    ant, pos = dividir_holdout(preds, cutoff)
    assert ant and pos
    # anterior todo <= cutoff; posterior todo > cutoff
    assert all(p["date"] <= cutoff for p in ant)
    assert all(p["date"] > cutoff for p in pos)
    # sem sobreposicao de fixtures entre as partes
    fx_ant = {p["fixture_id"] for p in ant}
    fx_pos = {p["fixture_id"] for p in pos}
    assert fx_ant.isdisjoint(fx_pos)
    # union == todos os fixtures
    assert fx_ant | fx_pos == {p["fixture_id"] for p in preds}


def test_cutoff_e_mediano_mecanico():
    preds = _corpus()
    cutoff = cutoff_mediano(preds)
    datas = sorted({p["date"] for p in preds})
    assert cutoff == datas[len(datas) // 2]
    # result-blind: cutoff nao depende de resultados/decisoes
    preds2 = [dict(p, resultado_final="PERDIDA") for p in preds]
    assert cutoff_mediano(preds2) == cutoff


# ----------------------------------------------------------------------
# 2. Nenhum fixture posterior entra no historico anterior (anti-lookahead)
# ----------------------------------------------------------------------
def test_nenhum_fixture_posterior_no_bloco_anterior():
    preds = _corpus()
    blocos = blocos_walk_forward(preds, 4)
    # blocos crescentes em data, sem sobreposicao
    maxdates = [max(p["date"] for p in bl) for _, bl in blocos]
    mindates = [min(p["date"] for p in bl) for _, bl in blocos]
    for i in range(len(blocos) - 1):
        assert maxdates[i] < mindates[i + 1], "bloco posterior tem data anterior"
    # nenhuma informacao futura: as_of == date (decisao no kickoff, so dados <)
    for _, bl in blocos:
        for p in bl:
            assert p["as_of"] == p["date"]
            assert p["as_of"] is not None


# ----------------------------------------------------------------------
# 3. Holdout nao altera regras
# ----------------------------------------------------------------------
def test_holdout_nao_altera_regras(tmp_path):
    # constantes congeladas antes
    antes = (PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1,
             VERSAO_PREJOGO_OP, VERSAO_BACKTEST_ENGINE)
    # rodar validacao (em memoria via corpus -> precisa de DB; usa helper DB)
    db = _build_mini_db(_corpus(), str(tmp_path / "v.db"))
    _ = validar_temporal(db, "test", blocos=3)
    depois = (PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1,
              VERSAO_PREJOGO_OP, VERSAO_BACKTEST_ENGINE)
    assert antes == depois
    # versoes congeladas
    assert VERSAO_PREJOGO_OP == "prejogo-op-1.0-observacao"
    assert VERSAO_BACKTEST_ENGINE == "backtest-1.0"
    assert PROB_MIN_APROVAR == 0.70
    assert PROB_MAX_APROVAR == 0.97
    assert CONF_MIN_TOP1 == 0.60


# ----------------------------------------------------------------------
# 4. Walk-forward cronologico
# ----------------------------------------------------------------------
def test_walk_forward_cronologico():
    preds = _corpus()
    blocos = blocos_walk_forward(preds, 6)
    assert len(blocos) == 6
    # todos os blocos nao-vazios
    assert all(bl for _, bl in blocos)
    # union == todos os fixtures
    todos = {p["fixture_id"] for p in preds}
    union = set().union(*({p["fixture_id"] for p in bl} for _, bl in blocos))
    assert union == todos
    # sem sobreposicao
    for i in range(len(blocos)):
        for j in range(i + 1, len(blocos)):
            assert ({p["fixture_id"] for p in blocos[i][1]}.isdisjoint(
                {p["fixture_id"] for p in blocos[j][1]}))


# ----------------------------------------------------------------------
# 5. Nenhuma informacao futura (as_of <= date em toda previsao)
# ----------------------------------------------------------------------
def test_nenhuma_informacao_futura():
    preds = _corpus()
    for p in preds:
        assert p["as_of"] is not None
        assert p["as_of"] <= p["date"]  # decisao nunca depois do kickoff


# ----------------------------------------------------------------------
# 6. Nenhum missing vira zero (denominador exclui NAO AVALIAVEL)
# ----------------------------------------------------------------------
def test_nenhum_missing_vira_zero():
    preds = [dict(p) for p in _corpus()]  # todos cartoes sao NAO AVALIAVEL
    m = _metricas(preds)
    # cartoes nao avaliaveis NAO contam como ganha nem perdida
    assert m["nao_avaliavel"] > 0
    assert m["hit_excl_na"] is not None  # settled existe para gols/escanteios/resultado
    # hit exclui NA do denominador
    assert m["settled"] == m["ganhas"] + m["perdidas"] + m["devolvidas"] + \
        m["meia_vitoria"] + m["meia_derrota"]
    assert m["nao_avaliavel"] not in (0,)  # missing preservado, nao zerado


# ----------------------------------------------------------------------
# 7. Cartoes NA preservados por periodo
# ----------------------------------------------------------------------
def test_cartoes_na_preservados_por_bloco():
    preds = _corpus()
    blocos = blocos_walk_forward(preds, 3)
    for _, bl in blocos:
        pm = metricas_por_mercado(bl)
        if "cartoes" in pm:
            cards = [p for p in bl if p["mercado"] == "cartoes"]
            assert pm["cartoes"]["nao_avaliavel"] == sum(
                1 for p in cards if p["resultado_final"] == "NÃO AVALIÁVEL")
            # nenhum NA virou GANHA
            assert pm["cartoes"]["ganhas"] == sum(
                1 for p in cards if p["resultado_final"] == "GANHA")


# ----------------------------------------------------------------------
# 8/9. Reproduzivel e deterministico
# ----------------------------------------------------------------------
def test_reproduzivel(tmp_path):
    db = _build_mini_db(_corpus(), str(tmp_path / "v.db"))
    r1 = validar_temporal(db, "test", blocos=3)
    r2 = validar_temporal(db, "test", blocos=3)
    assert json.dumps(r1, sort_keys=True, default=str) == \
        json.dumps(r2, sort_keys=True, default=str)


def test_mesma_entrada_mesmo_resultado():
    preds = _corpus()
    assert _metricas(preds) == _metricas([dict(p) for p in preds])


# ----------------------------------------------------------------------
# 10. Nenhuma regra do motor alterada (constantes congeladas)
# ----------------------------------------------------------------------
def test_regras_congeladas_valores():
    assert PROB_MIN_APROVAR == 0.70
    assert PROB_MAX_APROVAR == 0.97
    assert CONF_MIN_TOP1 == 0.60
    assert VERSAO_PREJOGO_OP == "prejogo-op-1.0-observacao"
    assert VERSAO_BACKTEST_ENGINE == "backtest-1.0"


# ----------------------------------------------------------------------
# 11. Equivalencia: fatiar por data == rodar engine por janela
# (base do metodo -- prova independencia por fixture)
# ----------------------------------------------------------------------
def _build_synthetic_cache(tmp_path):
    """Cache sintetico: 6 fixtures entre 1001/1002 na liga 999, datas
    crescentes, com estatistica 1h. Para provar independencia por fixture."""
    db = tmp_path / "cache.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE api_cache (endpoint TEXT, params TEXT, response TEXT)")
    teams = [{"team": {"id": t, "name": f"T{t}", "code": "XX", "logo": ""}}
             for t in (1001, 1002)]
    conn.execute(
        "INSERT INTO api_cache VALUES (?,?,?)",
        ("/teams", json.dumps({"id": 1001}), json.dumps([teams[0]])))
    conn.execute(
        "INSERT INTO api_cache VALUES (?,?,?)",
        ("/teams", json.dumps({"id": 1002}), json.dumps([teams[1]])))
    fixtures = []
    base = datetime(2026, 3, 1, 20, 0, 0)
    for i in range(6):
        off = i * 14  # 14 dias entre jogos
        d = (base + timedelta(days=off)).isoformat()
        home_id = 1001 if i % 2 == 0 else 1002
        away_id = 1002 if i % 2 == 0 else 1001
        gh, ga = (2, 1) if i % 2 == 0 else (1, 2)
        fixtures.append({
            "fixture": {"id": 9000 + i, "date": d, "status": {"short": "FT"}},
            "league": {"id": 999, "name": "Sint", "season": 2026, "country": "X"},
            "teams": {"home": {"id": home_id, "name": f"T{home_id}"},
                      "away": {"id": away_id, "name": f"T{away_id}"}},
            "goals": {"home": gh, "away": ga},
            "score": {"halftime": f"{gh}-{ga}", "fulltime": f"{gh}-{ga}"},
        })
    conn.execute("INSERT INTO api_cache VALUES (?,?,?)",
                 ("/fixtures", json.dumps({"league": 999, "season": 2026}),
                  json.dumps(fixtures)))
    # estatisticas por fixture (half=true) com escanteios/gols
    for i, fx in enumerate(fixtures):
        fid = fx["fixture"]["id"]
        hc, ac = 6 + (i % 3), 4 + (i % 2)
        blocos = [
            {"team": {"id": fx["teams"]["home"]["id"]},
             "statistics": [{"type": "Corner Kicks", "value": str(hc)},
                            {"type": "Ball Possession", "value": "55%"},
                            {"type": "Yellow Cards", "value": "2"},
                            {"type": "Red Cards", "value": "0"}],
             "statistics_1h": [{"type": "Corner Kicks", "value": str(hc // 2)},
                               {"type": "Ball Possession", "value": "55%"}]},
            {"team": {"id": fx["teams"]["away"]["id"]},
             "statistics": [{"type": "Corner Kicks", "value": str(ac)},
                            {"type": "Ball Possession", "value": "45%"},
                            {"type": "Yellow Cards", "value": "3"},
                            {"type": "Red Cards", "value": "0"}],
             "statistics_1h": [{"type": "Corner Kicks", "value": str(ac // 2)},
                               {"type": "Ball Possession", "value": "45%"}]},
        ]
        conn.execute("INSERT INTO api_cache VALUES (?,?,?)",
                     ("/fixtures/statistics", json.dumps({"fixture": fid, "half": "true"}),
                      json.dumps(blocos)))
    conn.commit()
    conn.close()
    return str(db)


def test_slice_equivalente_engine(tmp_path):
    """Rodar engine em todos os fixtures vs fatiar por data: as previsoes
    de um fixture sao identicas independentemente dos outros fixtures no run.
    Isso valida que fatiar o baseline por data == rodar por janela."""
    cache = _build_synthetic_cache(tmp_path)
    idx = CacheIndex(cache)
    idx.load()
    cfg_full = BacktestConfig(modo="PRE_GAME", league_ids=(999,),
                              apenas_viavel=False)
    eng_full = BacktestEngine(idx, cfg_full)
    meta_full, preds_full = eng_full.run("full")
    # run limitado aos 4 primeiros fixtures (por data) via limite_fixture
    cfg_lim = BacktestConfig(modo="PRE_GAME", league_ids=(999,),
                             apenas_viavel=False, limite_fixture=4)
    eng_lim = BacktestEngine(idx, cfg_lim)
    meta_lim, preds_lim = eng_lim.run("lim")
    # fixtures do run limitado
    fxs_lim = {p.fixture_id for p in preds_lim}
    # para cada fixture presente nos dois runs, as previsoes sao identicas
    by_fx_full = {}
    for p in preds_full:
        by_fx_full.setdefault(p.fixture_id, []).append(p)
    by_fx_lim = {}
    for p in preds_lim:
        by_fx_lim.setdefault(p.fixture_id, []).append(p)
    for fx in fxs_lim:
        a = sorted([(p.mercado, p.linha, round(p.prob, 6), p.decisao,
                     p.resultado_final) for p in by_fx_full[fx]])
        b = sorted([(p.mercado, p.linha, round(p.prob, 6), p.decisao,
                     p.resultado_final) for p in by_fx_lim[fx]])
        assert a == b, f"fixture {fx}: previsoes divergem entre run cheio e fatiado"


# ----------------------------------------------------------------------
# Helpers de DB em memoria
# ----------------------------------------------------------------------
def _build_mini_db(preds, path):
    """Cria um DB backtest temporario (path) com a run 'test' e os preds."""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE bt_runs (run_id TEXT PRIMARY KEY, engine_versao TEXT,
        regras_versao TEXT, modo TEXT, datahora TEXT, intervalo_inicio TEXT,
        intervalo_fim TEXT, ligas TEXT, mercados TEXT, fixtures_considerados INTEGER,
        fixtures_excluidos INTEGER, previsoes INTEGER, nota TEXT)""")
    conn.execute(
        """CREATE TABLE bt_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT, fixture_id INTEGER, league_id INTEGER, league_name TEXT,
        season TEXT, home_team TEXT, away_team TEXT, date TEXT, as_of TEXT,
        modo TEXT, mercado TEXT, linha TEXT, prob REAL, confianca REAL,
        h2h_n INTEGER, n_home INTEGER, n_away INTEGER, sem_stats_home INTEGER,
        sem_stats_away INTEGER, bench_validas INTEGER, decisao TEXT,
        cobertura_mercado TEXT, experimental INTEGER, odd_real REAL,
        bookmaker TEXT, predicted_edge REAL, predicted_ev REAL,
        resultado_final TEXT, placar_final TEXT, total_final REAL,
        pl_unitario REAL)""")
    conn.execute("INSERT INTO bt_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("test", VERSAO_BACKTEST_ENGINE, VERSAO_PREJOGO_OP, "PRE_GAME",
                  "2026-09-13T00:00:00", None, None, "999", "todos",
                  12, 0, len(preds), ""))
    cols = ("run_id,fixture_id,league_id,league_name,season,home_team,away_team,"
            "date,as_of,modo,mercado,linha,prob,confianca,h2h_n,n_home,n_away,"
            "sem_stats_home,sem_stats_away,bench_validas,decisao,cobertura_mercado,"
            "experimental,odd_real,bookmaker,predicted_edge,predicted_ev,"
            "resultado_final,placar_final,total_final,pl_unitario")
    for p in preds:
        vals = (p["run_id"], p["fixture_id"], p["league_id"], p["league_name"],
                str(p["season"]), p["home_team"], p["away_team"], p["date"],
                p["as_of"], p["modo"], p["mercado"], p["linha"], p["prob"],
                p["confianca"], p["h2h_n"], p["n_home"], p["n_away"],
                p["sem_stats_home"], p["sem_stats_away"], p["bench_validas"],
                p["decisao"], p["cobertura_mercado"],
                1 if p["experimental"] else 0, p["odd_real"], p["bookmaker"],
                p["predicted_edge"], p["predicted_ev"], p["resultado_final"],
                p["placar_final"], p["total_final"], p["pl_unitario"])
        conn.execute(f"INSERT INTO bt_predictions ({cols}) VALUES ({','.join(['?']*31)})",
                     vals)
    conn.commit()
    conn.close()
    return path


def test_carregar_previsoes_db(tmp_path):
    db = _build_mini_db(_corpus(), str(tmp_path / "v.db"))
    rid, preds = carregar_previsoes(db, "test")
    assert rid == "test"
    assert len(preds) == len(_corpus())