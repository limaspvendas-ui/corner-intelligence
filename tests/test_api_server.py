"""Testes determinísticos da API oficial Corner Intelligence (integração ChatGPT).

Cobre src/api_server.py (FastAPI) sem chamar a API real: usa TestClient +
monkeypatch das funções da camada operacional. Valida que a API é uma camada
FINA sobre a SaidaOficial — sem segundo motor, sem recalcular probabilidade,
sem criar sinal, sem aposta, NULL preservado.

Princípios (A-M):
  A) verificar_status responde.
  B) buscar_jogos_do_dia funciona (sem probabilidade).
  C) varredura_prelive usa saída oficial.
  D) GOALS mantém status estatístico correto.
  E) CORNERS mantém EM_OBSERVAÇÃO + override_usuario.
  F) API não promove CORNERS estatisticamente.
  G) varredura_live mantém modo_teste=true.
  H) LIVE não aparece como aprovado estatisticamente.
  I) override live não cria sinal.
  J) NULL continua NULL (não vira zero).
  K) dados stale não aparecem como live fresco.
  L) nenhum endpoint executa aposta.
  M) mesma entrada oficial produz mesma decisão Claude Code x API.
  N) CARDS em MODO TESTE por override (status NÃO_AVALIÁVEL preservado).
  O) varredura_prelive expõe oportunidades de cartões separadamente.
  P) /api/partida-dados é FACTUAL (sem probabilidade; NULL preservado).
  Q) /api/teste-coleta valida coleta factual (ausência reportada).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api_server import app, API_VERSAO, PROVIDER
from src.operacional import (
    MODO_TESTE_LIVE,
    STATUS_OP_LIVE_TESTE,
    VERSAO_CAMADA_LIVE,
    VERSAO_LIVE_OP,
    SaidaOficial,
)
from src.prejogo_opportunity import VERSAO_PREJOGO_OP
from src.validacao_multifonte import APROVADO_PROX, BLOQUEADO, EM_OBS


@pytest.fixture
def client():
    return TestClient(app)


def _patch_client(monkeypatch):
    """Evita criar APIFootballClient real (exigiria API_KEY)."""
    monkeypatch.setattr("src.api_server._client", lambda: object())


# ----------------------------------------------------------------------
# Helpers sintéticos
# ----------------------------------------------------------------------
def _saida_live(aprovadas=None, obs=None, stale=False, total_ao_vivo=1,
                nenhum=True):
    lv = {
        "total_ao_vivo": total_ao_vivo,
        "total_elegiveis": 1, "total_triados": 1,
        "status_estatistico": BLOQUEADO,
        "status_operacional": STATUS_OP_LIVE_TESTE,
        "override_operador": "true", "decisao_humana": "true",
        "timestamp_override": "2026-09-14T20:30:00Z",
        "motivo_override": "Liberação explícita do operador para coleta e teste live",
        "data_freshness": "STALE" if stale else "FRESCO",
        "versao_projeto": "test-hash",
    }
    return SaidaOficial(
        fixture_id=None, espec="VARREDURA LIVE",
        generated_at="2026-09-14 22:00:00",
        engine_version=VERSAO_LIVE_OP, camada_version=VERSAO_CAMADA_LIVE,
        projeto_hash="test-hash", data_status="LIVE",
        operational_opportunities=aprovadas or [],
        observations=obs or [],
        blocked=[{"mercado": "pressao_live",
                  "decisao_oficial": "BLOQUEADO",
                  "status_estatistico": BLOQUEADO}],
        provenance={"motor": VERSAO_LIVE_OP, "camada": VERSAO_CAMADA_LIVE},
        nenhum_aprovado=nenhum, mode="live", modo_teste=True, live=lv,
    )


def _saida_prelive(aprovadas=None, obs=None, nenhum=True):
    return SaidaOficial(
        fixture_id=9001, espec="Casa FC x Fora FC",
        generated_at="2026-09-14 22:00:00",
        engine_version=VERSAO_PREJOGO_OP,
        camada_version="operacional-1.1-override",
        projeto_hash="test-hash", data_status="OK",
        operational_opportunities=aprovadas or [],
        observations=obs or [],
        blocked=[],
        provenance={"motor": VERSAO_PREJOGO_OP},
        nenhum_aprovado=nenhum, mode="prejogo", modo_teste=False, live={},
    )


# ----------------------------------------------------------------------
# A. verificar_status responde
# ----------------------------------------------------------------------
def test_a_verificar_status_responde(client):
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    assert d["mode"] == "status"
    assert d["provider"] == PROVIDER
    assert d["api_version"] == API_VERSAO
    assert d["data"]["backend_online"] is True
    assert d["data"]["modo_teste_live"] is True
    assert d["data"]["database"]["integrity_check"] == "ok"
    # alias /api/status também funciona
    r2 = client.get("/api/status")
    assert r2.status_code == 200
    assert r2.json()["data"]["backend_online"] is True


# ----------------------------------------------------------------------
# B. buscar_jogos_do_dia funciona (sem probabilidade)
# ----------------------------------------------------------------------
def test_b_buscar_jogos_do_dia(client, monkeypatch):
    _patch_client(monkeypatch)
    fx = SimpleNamespace(
        fixture_id=9001, date="2026-09-15T15:00:00", status="NS",
        elapsed=None, league_id=39, league_name="Premier League",
        round="Regular Season - 5", season=2026,
        home_team_id=10, home_team_name="Casa FC",
        away_team_id=20, away_team_name="Fora FC",
        goals_home=None, goals_away=None,
        is_finished=False, is_live=False,
    )
    monkeypatch.setattr("src.fixtures.get_fixtures_today",
                        lambda c, on_date=None: [fx])
    r = client.get("/api/jogos-do-dia?date=2026-09-15")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    data = d["data"]
    assert data["fixtures_elegiveis"] == 1
    row = data["elegiveis"][0]
    assert row["fixture_id"] == 9001
    assert row["home"] == "Casa FC"
    assert row["competition"] == "Premier League"
    # SEM probabilidade nesta chamada
    assert "prob" not in row
    assert "confianca" not in row


# ----------------------------------------------------------------------
# C. varredura_prelive usa saída oficial
# ----------------------------------------------------------------------
def test_c_varredura_prelive_usa_saida_oficial(client, monkeypatch):
    _patch_client(monkeypatch)
    saida = _saida_prelive(
        aprovadas=[{"mercado": "gols", "linha": "Over 2.5 gols",
                    "prob": 0.93, "confianca": 0.75,
                    "decisao_oficial": "ENTRAR",
                    "status_estatistico": APROVADO_PROX,
                    "fixture_id": 9001}],
        nenhum=False,
    )
    monkeypatch.setattr(
        "src.api_server.varredura_data",
        lambda c, date: {
            "data": date or "hoje", "generated_at": "2026-09-14 22:00:00",
            "engine_version": VERSAO_PREJOGO_OP,
            "camada_version": "operacional-1.1-override",
            "projeto_hash": "test-hash",
            "fixtures_considerados": 5, "fixtures_elegiveis": 1,
            "fixtures_inelegiveis": [],
            "operational_opportunities": saida.operational_opportunities,
            "observations": [], "blocked": [],
            "nenhum_aprovado": False, "mensagem_nenhum": None,
            "provenance": {"motor": VERSAO_PREJOGO_OP},
        })
    r = client.get("/api/varredura-prelive")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    assert d["engine_version"] == VERSAO_PREJOGO_OP
    assert len(d["data"]["oportunidades_goals"]) == 1
    assert d["data"]["saida_oficial_completa"]["engine_version"] == \
        VERSAO_PREJOGO_OP


# ----------------------------------------------------------------------
# D. GOALS mantém status estatístico correto
# ----------------------------------------------------------------------
def test_d_goals_status_estatistico_correto(client):
    r = client.get("/api/status-mercados")
    d = r.json()
    gols = [m for m in d["data"]["mercados"] if m["mercado"] == "gols"][0]
    assert gols["status_estatistico"] == APROVADO_PROX
    assert gols["origem_operacional"] is None  # estatístico, não override


# ----------------------------------------------------------------------
# E. CORNERS mantém EM_OBSERVAÇÃO + override_usuario
# ----------------------------------------------------------------------
def test_e_corners_em_obs_override(client):
    r = client.get("/api/status-mercados")
    d = r.json()
    c = [m for m in d["data"]["mercados"] if m["mercado"] == "escanteios"][0]
    assert c["status_estatistico"] == EM_OBS
    assert c["status_operacional"] == "HABILITADO_POR_OVERRIDE_DO_USUARIO"
    assert c["origem_operacional"] == "override_usuario"
    assert c["override_operador"] == "true"
    assert c["timestamp_override"] is not None


# ----------------------------------------------------------------------
# F. API não promove CORNERS estatisticamente
# ----------------------------------------------------------------------
def test_f_api_nao_promove_corners(client):
    r = client.get("/api/status-mercados")
    d = r.json()
    c = [m for m in d["data"]["mercados"] if m["mercado"] == "escanteios"][0]
    # override habilita operacionalmente, MAS status estatístico permanece
    # EM_OBS (não vira APROVADO_PARA_PROXIMA_FASE)
    assert c["status_estatistico"] == EM_OBS
    assert c["status_estatistico"] != APROVADO_PROX


# ----------------------------------------------------------------------
# G. varredura_live mantém modo_teste=true
# ----------------------------------------------------------------------
def test_g_varredura_live_modo_teste_true(client, monkeypatch):
    _patch_client(monkeypatch)
    monkeypatch.setattr("src.api_server.varredura_live",
                        lambda c, mercados=None: _saida_live(nenhum=True))
    r = client.get("/api/varredura-live")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    assert d["mode"] == "live"
    assert d["data"]["modo_teste_live"] is True
    assert d["data"]["status_estatistico_live"] == BLOQUEADO
    assert d["data"]["status_operacional_live"] == STATUS_OP_LIVE_TESTE
    assert d["data"]["override_operador"] == "true"


# ----------------------------------------------------------------------
# H. LIVE não aparece como aprovado estatisticamente
# ----------------------------------------------------------------------
def test_h_live_nao_aprovado_estatisticamente(client, monkeypatch):
    _patch_client(monkeypatch)
    monkeypatch.setattr("src.api_server.varredura_live",
                        lambda c, mercados=None: _saida_live(nenhum=True))
    r = client.get("/api/varredura-live")
    d = r.json()
    assert d["data"]["status_estatistico_live"] == BLOQUEADO
    assert d["data"]["status_estatistico_live"] != APROVADO_PROX
    # nenhuma string de aprovação estatística
    body = r.text
    assert "APROVADO_ESTATISTICAMENTE" not in body
    assert "APROVADO_PARA_PROXIMA_FASE" not in \
        d["data"]["saida_oficial_completa"]["live"]["status_estatistico"]


# ----------------------------------------------------------------------
# I. override live não cria sinal
# ----------------------------------------------------------------------
def test_i_override_live_nao_cria_sinal(client, monkeypatch):
    _patch_client(monkeypatch)
    # override ativo, mas motor não aprovou nada
    monkeypatch.setattr("src.api_server.varredura_live",
                        lambda c, mercados=None: _saida_live(
                            aprovadas=[], nenhum=True))
    r = client.get("/api/varredura-live")
    d = r.json()
    assert d["data"]["override_operador"] == "true"
    assert d["data"]["sinais_operacionais"] == []
    assert d["data"]["nenhum_aprovado"] is True
    assert d["data"]["modo_teste_live"] is True


# ----------------------------------------------------------------------
# J. NULL continua NULL (não vira zero)
# ----------------------------------------------------------------------
def test_j_null_continua_null(client, monkeypatch):
    _patch_client(monkeypatch)
    av = {"fixture_id": 9001, "mercado": "gols", "linha": "Over 0.5 gols",
          "prob": 0.93, "confianca": 0.75, "decisao_oficial": "ENTRAR",
          "status_estatistico": APROVADO_PROX, "live_minute": None,
          "score": None, "odd": None}
    monkeypatch.setattr("src.api_server.varredura_live",
                        lambda c, mercados=None: _saida_live(
                            aprovadas=[av], nenhum=False))
    r = client.get("/api/varredura-live")
    d = r.json()
    o = d["data"]["sinais_operacionais"][0]
    # None serializa como null (não 0)
    assert o["live_minute"] is None
    assert o["score"] is None
    assert o["odd"] is None
    # prob do motor preservada
    assert o["prob"] == 0.93


# ----------------------------------------------------------------------
# K. dados stale não aparecem como live fresco
# ----------------------------------------------------------------------
def test_k_stale_nao_fresco(client, monkeypatch):
    _patch_client(monkeypatch)
    monkeypatch.setattr("src.api_server.varredura_live",
                        lambda c, mercados=None: _saida_live(stale=True,
                                                              nenhum=True))
    r = client.get("/api/varredura-live")
    d = r.json()
    assert d["data"]["data_freshness"] == "STALE"
    assert d["data"]["data_freshness"] != "FRESCO"
    # stale => nenhum sinal operacional
    assert d["data"]["sinais_operacionais"] == []


# ----------------------------------------------------------------------
# L. nenhum endpoint executa aposta
# ----------------------------------------------------------------------
def test_l_nenhuma_aposta(client, monkeypatch):
    _patch_client(monkeypatch)
    monkeypatch.setattr("src.api_server.varredura_live",
                        lambda c, mercados=None: _saida_live(nenhum=True))
    monkeypatch.setattr("src.api_server.varredura_data",
                        lambda c, date: {
                            "data": "hoje", "generated_at": "x",
                            "engine_version": VERSAO_PREJOGO_OP,
                            "camada_version": "op", "projeto_hash": "h",
                            "fixtures_considerados": 0,
                            "fixtures_elegiveis": 0,
                            "fixtures_inelegiveis": [],
                            "operational_opportunities": [],
                            "observations": [], "blocked": [],
                            "nenhum_aprovado": True,
                            "mensagem_nenhum": "NENHUMA",
                            "provenance": {}})
    forbidden = ("aposta_executada", "aposta_realizada", "ordem_enviada",
                 "bookmaker_integration", "aposta")
    for path in ("/health", "/api/status-mercados", "/api/varredura-prelive",
                 "/api/varredura-live"):
        r = client.get(path)
        body = r.text
        for f in forbidden:
            assert f not in body.lower(), f"{f} em {path}"


# ----------------------------------------------------------------------
# M. mesma entrada oficial produz mesma decisão Claude Code x API
# ----------------------------------------------------------------------
def test_m_mesma_decisao_cc_x_api(client, monkeypatch):
    """A API devolve exatamente a SaidaOficial que o motor produziu —
    mesma decisão, sem divergência. Claude Code (CLI) e ChatGPT (API)
    leem a mesma verdade operacional."""
    _patch_client(monkeypatch)
    av = {"fixture_id": 9001, "mercado": "gols", "linha": "Over 2.5 gols",
          "prob": 0.93, "confianca": 0.75, "decisao_oficial": "ENTRAR",
          "status_estatistico": APROVADO_PROX,
          "origem_operacional": "estatistico",
          "status_operacional": "HABILITADO_ESTATISTICAMENTE"}
    saida = _saida_live(aprovadas=[av], nenhum=False)
    monkeypatch.setattr("src.api_server.varredura_live",
                        lambda c, mercados=None: saida)
    r = client.get("/api/varredura-live")
    d = r.json()
    # a API repassa o dict canônico do motor sem alterar
    api_opp = d["data"]["sinais_operacionais"][0]
    # mesma decisão que o Claude Code veria via SaidaOficial.to_dict()
    cc_opp = saida.to_dict()["operational_opportunities"][0]
    assert api_opp["decisao_oficial"] == cc_opp["decisao_oficial"]
    assert api_opp["prob"] == cc_opp["prob"]
    assert api_opp["confianca"] == cc_opp["confianca"]
    assert api_opp["linha"] == cc_opp["linha"]
    assert api_opp["mercado"] == cc_opp["mercado"]
    assert api_opp["status_estatistico"] == cc_opp["status_estatistico"]
    # DIVERGÊNCIA = 0
    assert api_opp == cc_opp


# ----------------------------------------------------------------------
# N. CARDS em MODO TESTE por override (status estatístico preservado)
# ----------------------------------------------------------------------
def test_n_cards_modo_teste_override(client):
    r = client.get("/api/status-mercados")
    d = r.json()
    c = [m for m in d["data"]["mercados"] if m["mercado"] == "cartoes"][0]
    # status ESTATÍSTICO real preservado (não promovido)
    assert c["status_estatistico"] == "NÃO_AVALIÁVEL"
    assert c["status_estatistico"] != APROVADO_PROX
    # operacional = MODO TESTE por override, rotulado
    assert c["status_operacional"] == (
        "HABILITADO_EM_MODO_TESTE_POR_OVERRIDE_DO_USUARIO")
    assert c["origem_operacional"] == "override_usuario"
    assert c["modo_teste"] is True
    assert c["rotulo_override"] == (
        "MODO TESTE — OVERRIDE AUTORIZADO PELO USUÁRIO — "
        "NÃO VALIDADO ESTATISTICAMENTE")
    assert c["override_operador"] == "true"
    assert c["timestamp_override"] is not None
    # resumo textual coerente
    assert "MODO TESTE" in d["data"]["resumo"]["CARDS"]
    # health também expõe cards
    h = client.get("/health").json()
    assert h["data"]["prelive"]["cards"]["modo_teste"] is True
    assert h["data"]["prelive"]["cards"]["status"] == "NÃO_AVALIÁVEL"


# ----------------------------------------------------------------------
# O. varredura_prelive expõe oportunidades de cartões separadamente
# ----------------------------------------------------------------------
def test_o_varredura_prelive_expoes_cartoes(client, monkeypatch):
    _patch_client(monkeypatch)
    opps = [
        {"mercado": "gols", "linha": "Over 2.5 gols", "prob": 0.93,
         "confianca": 0.75, "fixture_id": 9001},
        {"mercado": "cartoes",
         "linha": "Over 3.5 cartoes (amarelo=1, vermelho=2) (total do jogo)",
         "prob": 0.90, "confianca": 0.70, "fixture_id": 9001,
         "modo_teste": True,
         "rotulo_override": ("MODO TESTE — OVERRIDE AUTORIZADO PELO "
                             "USUÁRIO — NÃO VALIDADO ESTATISTICAMENTE")},
    ]
    monkeypatch.setattr(
        "src.api_server.varredura_data",
        lambda c, date: {
            "data": date or "hoje", "generated_at": "2026-09-15 10:00:00",
            "engine_version": VERSAO_PREJOGO_OP,
            "camada_version": "op", "projeto_hash": "h",
            "fixtures_considerados": 5, "fixtures_elegiveis": 1,
            "fixtures_inelegiveis": [],
            "operational_opportunities": opps,
            "observations": [], "blocked": [],
            "nenhum_aprovado": False, "mensagem_nenhum": None,
            "provenance": {},
        })
    r = client.get("/api/varredura-prelive")
    d = r.json()["data"]
    assert len(d["oportunidades_goals"]) == 1
    assert len(d["oportunidades_cartoes"]) == 1
    card = d["oportunidades_cartoes"][0]
    assert card["modo_teste"] is True
    assert "NÃO VALIDADO ESTATISTICAMENTE" in card["rotulo_override"]


# ----------------------------------------------------------------------
# P. /api/partida-dados é FACTUAL (sem probabilidade; NULL preservado)
# ----------------------------------------------------------------------
def _fx_ns(fixture_id=9001):
    return SimpleNamespace(
        fixture_id=fixture_id, date="2026-09-15T15:00:00", status="NS",
        elapsed=None, league_id=39, league_name="Premier League",
        round="Regular Season - 5", season=2026,
        home_team_id=10, home_team_name="Casa FC",
        away_team_id=20, away_team_name="Fora FC",
        goals_home=None, goals_away=None, venue="Londres",
        is_finished=False, is_live=False,
    )


def test_p_partida_dados_factual_sem_probabilidade(client, monkeypatch):
    _patch_client(monkeypatch)
    monkeypatch.setattr("src.fixtures.get_fixture_by_id",
                        lambda c, fid: _fx_ns(fid))
    monkeypatch.setattr(
        "src.api_server.jogo_elegivel",
        lambda lid, ln, h, a, dados=None: (True, ""))
    from src.exceptions import DataUnavailableError

    def _raise(client, fixture):
        raise DataUnavailableError("sem estatísticas na fonte")
    monkeypatch.setattr("src.match_stats.fetch_match_stats", _raise)
    monkeypatch.setattr(
        "src.match_stats.fetch_team_history",
        lambda c, tid, last=20, team_name="": ([], 0))
    r = client.get("/api/partida-dados?fixture_id=9001")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    facts = d["data"]["facts"]
    assert facts["fixture_id"] == 9001
    assert facts["home"] == "Casa FC"
    assert facts["score"] is None            # NULL preservado
    assert facts["elegivel_universo"] is True
    # estatísticas indisponíveis: reportadas, nunca preenchidas
    assert facts["estatisticas"]["disponivel"] is False
    assert "dado não disponível" in facts["estatisticas"]["motivo"]
    # FACTUAL: nenhum campo de probabilidade/decisão
    assert "prob" not in facts
    assert "probabilidade" not in facts
    assert "decisao_oficial" not in facts
    assert "confianca" not in facts


def test_p2_partida_dados_fixture_nao_encontrado(client, monkeypatch):
    _patch_client(monkeypatch)
    monkeypatch.setattr("src.fixtures.get_fixture_by_id",
                        lambda c, fid: None)
    r = client.get("/api/partida-dados?fixture_id=1")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is False
    assert "fixture não encontrado" in d["erro"]


# ----------------------------------------------------------------------
# Q. /api/teste-coleta valida coleta factual
# ----------------------------------------------------------------------
def test_q_teste_coleta_valida_coleta(client, monkeypatch):
    _patch_client(monkeypatch)
    monkeypatch.setattr("src.fixtures.get_fixtures_today",
                        lambda c, on_date=None: [_fx_ns()])
    monkeypatch.setattr(
        "src.api_server.jogo_elegivel",
        lambda lid, ln, h, a, dados=None: (True, ""))
    from src.exceptions import DataUnavailableError

    def _raise(client, fixture):
        raise DataUnavailableError("pré-jogo: sem estatísticas")
    monkeypatch.setattr("src.match_stats.fetch_match_stats", _raise)

    game = SimpleNamespace(
        fixture_id=8001, date="2026-09-10T15:00:00", league="Premier League",
        round="Regular Season - 4", status="FT", opponent="Adversario FC",
        played_at_home=True, corners_for=6, corners_against=4,
        corners_total=10, corners_for_1st_half=None,
        corners_against_1st_half=None, corners_for_2nd_half=None,
        corners_against_2nd_half=None, goals_for=2, goals_against=1,
        shots_for=12, shots_on_goal_for=5, possession_for=55,
        yellow_for=2, red_for=None, yellow_against=3, red_against=None,
    )
    monkeypatch.setattr(
        "src.match_stats.fetch_team_history",
        lambda c, tid, last=1, team_name="": ([game], 0))
    r = client.get("/api/teste-coleta")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    res = d["data"]
    assert res["fixtures_elegiveis"] == 1
    assert res["coleta_estatisticas"]["disponivel"] is False
    assert "dado não disponível" in res["coleta_estatisticas"]["motivo"]
    # histórico coletado factualmente; red_cards NULL preservado
    assert res["coleta_historico_home"]["disponivel"] is True
    assert res["coleta_historico_home"]["n_jogos"] == 1
    # FACTUAL: sem probabilidade
    assert "prob" not in res


def test_q2_teste_coleta_sem_jogos(client, monkeypatch):
    _patch_client(monkeypatch)
    monkeypatch.setattr("src.fixtures.get_fixtures_today",
                        lambda c, on_date=None: [])
    r = client.get("/api/teste-coleta")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["resultado"] == "SEM_JOGO_ELEGIVEL"