"""Regressão: HTTP 500 no pre-live de 4 fixtures com nomes ambíguos (15/09/2026).

CAUSA RAIZ (confirmada com dados reais): `analisar_partida_prelive` tinha
o fixture em mãos (`get_fixture_by_id`) mas re-resolvia as equipes POR
NOME (`pre_match_analysis` -> `resolve_team_validated` sem contexto de
liga). Nomes ambíguos na API-Football — "Valencia", "Platense",
"Vasco da Gama", "Boca Juniors" — levantavam AmbiguousTeamError =>
HTTP 500 no endpoint e `erro_motor` mascarado como "inelegível" na
varredura por data.

CORREÇÃO (mínima, aditiva — nenhum cálculo muda): o fixture conhecido
atravessa a cadeia `analisar_fixture` -> `scan_pregame_opportunities` ->
`pre_match_analysis` e ancora a identidade pelos IDs do PRÓPRIO fixture
(regra 12 do projeto: `fixture_teams`), validados por /teams?id= contra o
nome registrado no próprio fixture. A resolução por nome NEM RODA quando
o fixture é conhecido; sem fixture, o caminho por nome permanece
exatamente como era (regra 4: ambiguidade continua interrompendo o
fluxo por nome — o usuário é quem desambigua).

Preservado: NULL permanece NULL; erro não vira aposta; nenhum mercado
deixou de ser avaliado; nenhum threshold/fórmula/calibração muda.
"""
from types import SimpleNamespace

import pytest

from src.analysis import pre_match_analysis
from src.exceptions import AmbiguousTeamError
from src.operacional import analisar_fixture, varredura_data
from src.prejogo_opportunity import scan_pregame_opportunities


# Os 4 fixtures que retornavam HTTP 500 (partidas reais de 15/09/2026).
# Os IDs de time aqui são SINTÉTICOS de propósito: no fluxo real vêm
# sempre do próprio fixture (é exatamente essa âncora que se protege —
# ID de time nunca é assumido, memorizado ou hardcoded).
FIXTURES_500 = [
    (1570383, "Alaves", "Valencia", 101, 102, "La Liga"),
    (1630778, "Platense", "Fluminense", 201, 202,
     "CONMEBOL Sudamericana"),
    (1635577, "Vasco DA Gama", "Santa Fe", 301, 302,
     "CONMEBOL Sudamericana"),
    (1629831, "Sao Paulo", "Boca Juniors", 401, 402,
     "CONMEBOL Libertadores"),
]


def _fixture_ns(fid, home, away, hid, aid, league):
    """Fixture sintético NÃO iniciado (o cenário exato do pre-live)."""
    return SimpleNamespace(
        fixture_id=fid, date="2026-09-15T21:30:00", status="NS",
        elapsed=None, league_id=99, league_name=league,
        round="Group Stage", season=2026,
        home_team_id=hid, home_team_name=home,
        away_team_id=aid, away_team_name=away,
        goals_home=None, goals_away=None, venue="Estadio",
        is_finished=False, is_live=False,
    )


# ----------------------------------------------------------------------
# 1. Motor: com fixture conhecido, a identidade vem do PRÓPRIO fixture
#    (a resolução por nome — origem do HTTP 500 — nem roda)
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "fid,home,away,hid,aid,league", FIXTURES_500)
def test_com_fixture_ancora_ids_do_proprio_fixture(
        monkeypatch, fid, home, away, hid, aid, league):
    fx = _fixture_ns(fid, home, away, hid, aid, league)
    chamadas_resolver = []

    def _resolver_ambiguo(client, name, **kw):
        # Exatamente o erro real dos 4 fixtures ("Valencia" tem 3
        # candidatos na API-Football, "Platense" 3, "Vasco da Gama" 3,
        # "Boca Juniors" 2). Se este caminho rodar, o teste falha.
        chamadas_resolver.append(name)
        raise AmbiguousTeamError(
            f"AMBIGUIDADE: encontrei 3 times com esse nome: {name}",
            [{"id": 1, "name": name}, {"id": 2, "name": name},
             {"id": 3, "name": name}])

    monkeypatch.setattr(
        "src.analysis.resolve_team_validated", _resolver_ambiguo)

    home_t = SimpleNamespace(id=hid, name=home, country="X")
    away_t = SimpleNamespace(id=aid, name=away, country="X")
    chamadas_fixture_teams = []

    def _fixture_teams(client, fixture, expect_home=None,
                       expect_away=None):
        # IDs OBRIGATORIAMENTE do fixture (regra 12), validados contra
        # o nome do próprio fixture + os nomes do espec.
        assert fixture is fx
        assert expect_home == home
        assert expect_away == away
        chamadas_fixture_teams.append(fixture.fixture_id)
        return home_t, away_t

    monkeypatch.setattr("src.analysis.fixture_teams", _fixture_teams)

    historicos = []

    def _historico(client, team_id, last, team_name):
        historicos.append((team_id, team_name))
        return [], 0  # sem estatísticas na fonte => None, nunca zero

    monkeypatch.setattr("src.analysis.fetch_team_history", _historico)

    def _proibido(*a, **kw):  # pragma: no cover
        raise AssertionError(
            "com fixture conhecido, a busca por próximos jogos "
            "(get_next_fixtures) não deve rodar")

    monkeypatch.setattr("src.analysis.get_next_fixtures", _proibido)
    monkeypatch.setattr("src.analysis.get_h2h_analysis",
                        lambda c, a, b, last=10: None)
    monkeypatch.setattr("src.analysis.h2h_corner_stats", lambda h: {})
    monkeypatch.setattr("src.analysis.league_corner_average",
                        lambda *a, **kw: None)

    # ANTES: AmbiguousTeamError => HTTP 500.
    # DEPOIS: análise completa, âncora pelos IDs do fixture.
    res = pre_match_analysis(
        _client_stub(), home, away, fixture=fx)

    assert res["proximo_jogo"] is fx
    assert res["time_a"].id == hid
    assert res["time_b"].id == aid
    # A resolução POR NOME não rodou em momento algum:
    assert chamadas_resolver == []
    assert chamadas_fixture_teams == [fid]
    # Históricos coletados pelos IDs do PRÓPRIO fixture:
    assert sorted(historicos) == sorted([(hid, home), (aid, away)])
    # Dado ausente permanece ausente (nunca zero, nunca inventado):
    assert res["stats_a"] is None
    assert res["stats_b"] is None
    assert res["xcorners"] is None


def _client_stub():
    return SimpleNamespace()


# ----------------------------------------------------------------------
# 2. Sem fixture: disciplina por nome preservada (regra 4)
# ----------------------------------------------------------------------
def test_sem_fixture_nome_ambiguo_continua_interrompendo(monkeypatch):
    """Regra 4 intacta: no fluxo POR NOME (ex.: CLI `prejogoop`), a
    ambiguidade continua interrompendo a análise — o fix NÃO enfraquece
    a disciplina; só deixa de re-resolver quando o fixture já é conhecido."""
    def _resolver_ambiguo(client, name, **kw):
        raise AmbiguousTeamError(
            f"AMBIGUIDADE: encontrei 3 times com esse nome: {name}",
            [{"id": 1, "name": name}, {"id": 2, "name": name},
             {"id": 3, "name": name}])

    monkeypatch.setattr(
        "src.analysis.resolve_team_validated", _resolver_ambiguo)
    with pytest.raises(AmbiguousTeamError):
        pre_match_analysis(_client_stub(), "Vasco DA Gama", "Santa Fe")


# ----------------------------------------------------------------------
# 3. Pass-through da cadeia: o fixture atravessa intacto
# ----------------------------------------------------------------------
def test_scan_pregame_repassa_fixture_ao_motor(monkeypatch):
    fx = _fixture_ns(1635577, "Vasco DA Gama", "Santa Fe", 301, 302,
                     "CONMEBOL Sudamericana")
    recebido = {}

    def _pre_match(client, a, b, fixture=None):
        recebido["fixture"] = fixture
        return {"proximo_jogo": None}  # saída válida, sem exceção

    monkeypatch.setattr("src.analysis.pre_match_analysis", _pre_match)

    v = scan_pregame_opportunities(
        _client_stub(), "Vasco DA Gama x Santa Fe",
        registrar=False, fixture=fx)
    assert recebido["fixture"] is fx
    assert v.motivo_sem_jogo is not None  # análise retornou, não explodiu

    # Compatibilidade: sem fixture, o fluxo legado segue idêntico.
    v2 = scan_pregame_opportunities(
        _client_stub(), "Vasco DA Gama x Santa Fe", registrar=False)
    assert recebido["fixture"] is None
    assert v2.motivo_sem_jogo is not None


def test_analisar_fixture_repassa_fixture_sem_tocar_flags_do_motor(
        monkeypatch):
    fx = _fixture_ns(1635577, "Vasco DA Gama", "Santa Fe", 301, 302,
                     "CONMEBOL Sudamericana")
    recebido = {}

    def _scan(client, espec, registrar=True, incluir_resultado=False,
              incluir_cartoes=False, fixture=None):
        recebido.update(
            espec=espec, registrar=registrar,
            incluir_resultado=incluir_resultado,
            incluir_cartoes=incluir_cartoes, fixture=fixture)
        return SimpleNamespace()  # consumido pelo stub de _construir_saida

    sentinel = object()
    monkeypatch.setattr(
        "src.operacional.scan_pregame_opportunities", _scan)
    monkeypatch.setattr(
        "src.operacional._construir_saida", lambda v, ts: sentinel)

    saida = analisar_fixture(
        _client_stub(), "Vasco DA Gama x Santa Fe",
        registrar=False, fixture=fx)
    assert saida is sentinel
    assert recebido["fixture"] is fx
    assert recebido["registrar"] is False
    assert recebido["incluir_resultado"] is True
    assert recebido["incluir_cartoes"] is True
    # O override de CARTÕES em MODO TESTE permanece exatamente como era:
    assert recebido["incluir_cartoes"] is True


# ----------------------------------------------------------------------
# 4. Endpoint: o site exato do HTTP 500 agora entrega o fixture à camada
#    oficial em vez de re-resolver por nome
# ----------------------------------------------------------------------
def test_endpoint_prelive_passa_fixture_e_responde_200(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api_server import app

    fx = _fixture_ns(1635577, "Vasco DA Gama", "Santa Fe", 301, 302,
                     "CONMEBOL Sudamericana")
    monkeypatch.setattr("src.api_server._client", lambda: object())
    monkeypatch.setattr("src.fixtures.get_fixture_by_id",
                        lambda c, fid: fx)
    monkeypatch.setattr(
        "src.api_server.jogo_elegivel",
        lambda lid, ln, h, a, dados=None: (True, ""))
    recebido = {}

    def _analisar(client, espec, registrar=False, fixture=None):
        recebido.update(espec=espec, fixture=fixture)
        return SimpleNamespace(to_dict=lambda: {
            "operational_opportunities": [], "observations": [],
            "blocked": [], "nenhum_aprovado": True,
        })

    monkeypatch.setattr("src.api_server.analisar_fixture", _analisar)

    c = TestClient(app)
    r = c.get("/api/partida-prelive?fixture_id=1635577")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    assert d["data"]["fixture_id"] == 1635577
    assert d["data"]["elegivel_pre"] is True
    # O fixture foi entregue intacto à camada oficial (âncora de identidade):
    assert recebido["fixture"] is fx
    assert recebido["espec"] == "Vasco DA Gama x Santa Fe"


# ----------------------------------------------------------------------
# 5. Varredura por data: fixture em mãos não vira "inelegível" por erro
#    de motor (o mascaramento que escondia os 4 jogos elegíveis)
# ----------------------------------------------------------------------
def test_varredura_data_passa_fixture_e_nao_mascara_elegivel(
        monkeypatch):
    fx = _fixture_ns(1635577, "Vasco DA Gama", "Santa Fe", 301, 302,
                     "CONMEBOL Sudamericana")
    monkeypatch.setattr("src.operacional.get_fixtures_today",
                        lambda c, on_date=None: [fx])
    monkeypatch.setattr(
        "src.operacional.jogo_elegivel",
        lambda lid, ln, h, a: (True, ""))

    def _analisar(client, espec, registrar=False, fixture=None):
        assert fixture is fx  # a varredura passou o fixture que já tem
        return SimpleNamespace(
            operational_opportunities=[
                {"mercado": "gols", "fixture_id": fx.fixture_id}],
            observations=[], blocked=[], nenhum_aprovado=False)

    monkeypatch.setattr("src.operacional.analisar_fixture", _analisar)

    out = varredura_data(_client_stub())
    # O jogo elegível é ANALISADO (antes: caía em fixtures_inelegiveis
    # com "erro_motor: AmbiguousTeamError", subnotificando os elegíveis)
    assert out["fixtures_elegiveis"] == 1
    assert out["fixtures_inelegiveis"] == []
    assert out["operational_opportunities"][0]["fixture_id"] == 1635577