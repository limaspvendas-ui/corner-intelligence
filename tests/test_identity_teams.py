"""Testes AO VIVO (API-Football real) de identidade dos times:
Corinthians, Chapecoense, Botafogo e Palmeiras.

Confirmam que TODOS os modulos usam EXATAMENTE os mesmos IDs reais:
    1. resolucao por nome + validacao ID + nome + competicao
       (Serie A Brasil, temporada atual, via API - nunca assumida);
    2. historico individual: cada fixture retornado contem o mesmo ID;
    3. mando (casa/fora): a perspectiva bate com o lado real do fixture;
    4. estatisticas por partida: escanteios atribuidos ao time certo
       (verificacao independente contra a resposta bruta da API);
    5. H2H: confrontos diretos envolvem exatamente os dois IDs.

Estes testes CONSOMEM requisicoes da API (a maioria vem do cache local,
TTL de 7 dias em /teams). Por isso ficam desligados por padrao:

    PowerShell:  $env:CORNER_LIVE_TESTS = "1"; python -m pytest tests/test_identity_teams.py -v
    bash:        CORNER_LIVE_TESTS=1 python -m pytest tests/test_identity_teams.py -v

Sem a variavel de ambiente, aparecem como "skipped".
"""

import os

import pytest

from src.config import DEFAULT_TIMEZONE

pytestmark = pytest.mark.skipif(
    os.getenv("CORNER_LIVE_TESTS") != "1",
    reason="teste ao vivo (API real): defina CORNER_LIVE_TESTS=1",
)

TIMES = ["Corinthians", "Chapecoense-sc", "Botafogo", "Palmeiras"]
CONFRONTOS = [
    ("Corinthians", "Chapecoense-sc"),
    ("Botafogo", "Palmeiras"),
]


@pytest.fixture(scope="module")
def client():
    from src.api_client import APIFootballClient

    return APIFootballClient()


@pytest.fixture(scope="module")
def serie_a(client):
    from src.resolver import resolve_league

    return resolve_league(client, "Serie A", country="Brazil")


def _resolver_validado(client, serie_a, nome):
    from src.identity import resolve_team_validated

    return resolve_team_validated(
        client,
        nome,
        context=f"teste de identidade: {nome}",
        league_id=serie_a.id,
        season=serie_a.current_season,
    )


# ----------------------------------------------------------------------
# 1. ID real resolvido pela API e validado (ID + nome + competicao)
# ----------------------------------------------------------------------
@pytest.mark.parametrize("nome", TIMES)
def test_id_real_validado_por_api(client, serie_a, nome):
    team = _resolver_validado(client, serie_a, nome)

    # Round-trip visivel: o ID resolvido aponta para o mesmo nome na API
    raw = client.get("/teams", params={"id": team.id})
    assert raw, f"ID {team.id} nao retornou nenhum time na API"
    assert raw[0]["team"]["id"] == team.id

    from src.identity import ValidatedTeamsStore, normalize_name

    assert (
        normalize_name(raw[0]["team"]["name"]) == normalize_name(nome)
    ), f"ID {team.id} corresponde a '{raw[0]['team']['name']}', nao a '{nome}'"

    # Participacao na competicao validada dentro da propria resolucao
    # (divergencia teria lancado IdentityDivergenceError). E a associacao
    # validada fica registrada no cache de identidade:
    record = ValidatedTeamsStore().get(team.name)
    assert record is not None and record["team_id"] == team.id


# ----------------------------------------------------------------------
# 2 e 3. Historico individual e mando usam exatamente o mesmo ID
# ----------------------------------------------------------------------
@pytest.mark.parametrize("nome", TIMES)
def test_historico_e_mando_usam_o_mesmo_id(client, serie_a, nome):
    from src.fixtures import parse_fixture
    from src.match_stats import fetch_team_history

    team = _resolver_validado(client, serie_a, nome)

    # Resposta bruta: TODOS os fixtures do historico envolvem o mesmo ID
    raw = client.get(
        "/fixtures",
        params={"team": team.id, "last": 10, "timezone": DEFAULT_TIMEZONE},
    )
    assert raw, f"Sem historico retornado para o ID {team.id}"
    for item in raw:
        ids = {item["teams"]["home"]["id"], item["teams"]["away"]["id"]}
        assert team.id in ids, (
            f"Fixture {item['fixture']['id']} nao envolve o ID {team.id}"
        )

    # Modulo de historico: mesmos fixtures, mesma perspectiva
    games, unavailable = fetch_team_history(
        client, team.id, last=10, team_name=team.name
    )
    assert len(games) + unavailable > 0

    # Mando (casa/fora) bate com o lado REAL de cada fixture bruto
    raw_by_fixture = {item["fixture"]["id"]: item for item in raw}
    for game in games:
        item = raw_by_fixture[game.fixture_id]
        at_home_real = item["teams"]["home"]["id"] == team.id
        assert game.played_at_home == at_home_real, (
            f"Mando divergente no fixture {game.fixture_id}"
        )


# ----------------------------------------------------------------------
# 4. Estatisticas por partida atribuidas ao time certo
# ----------------------------------------------------------------------
@pytest.mark.parametrize("nome", TIMES)
def test_estatisticas_da_partida_sao_do_time_certo(client, serie_a, nome):
    from src.fixtures import FINISHED_STATUS, parse_fixture
    from src.exceptions import DataUnavailableError
    from src.match_stats import fetch_match_stats, to_team_perspective

    team = _resolver_validado(client, serie_a, nome)

    raw = client.get(
        "/fixtures",
        params={"team": team.id, "last": 5, "timezone": DEFAULT_TIMEZONE},
    )
    finished = [
        parse_fixture(item)
        for item in raw
        if item["fixture"]["status"]["short"] in FINISHED_STATUS
    ]
    assert finished, "Nenhum jogo encerrado para validar estatisticas"

    match = None
    for fixture in finished:
        try:
            match = fetch_match_stats(client, fixture)
            break
        except DataUnavailableError:
            # partida sem estatisticas na fonte: tenta a proxima
            match = None
    assert match is not None, "Nenhuma partida com estatisticas na fonte"

    # Verificacao INDEPENDENTE contra a resposta bruta da API:
    # o bloco de escanteios do mandante e o mesmo que o modulo atribuiu
    raw_stats = client.get(
        "/fixtures/statistics",
        params={"fixture": match.fixture.fixture_id, "half": "true"},
    )

    def _corners(team_id):
        for block in raw_stats:
            if block["team"]["id"] == team_id:
                for stat in block.get("statistics") or []:
                    if stat.get("type") == "Corner Kicks":
                        return stat.get("value")
        return None

    assert match.home.corners == _corners(match.fixture.home_team_id)
    assert match.away.corners == _corners(match.fixture.away_team_id)

    # Perspectiva do time validado (base de gols, cartoes, chutes, posse,
    # faltas, impedimentos e escanteios por tempo):
    game = to_team_perspective(match, team.id)
    at_home = match.fixture.is_home_for(team.id)
    assert game.played_at_home == at_home
    own_corners = match.home.corners if at_home else match.away.corners
    assert game.corners_for == own_corners


# ----------------------------------------------------------------------
# 5. H2H usa exatamente os dois IDs reais
# ----------------------------------------------------------------------
@pytest.mark.parametrize("nome_a, nome_b", CONFRONTOS)
def test_h2h_usa_exatamente_os_dois_ids(client, serie_a, nome_a, nome_b):
    from src.h2h import get_h2h_analysis
    from src.fixtures import get_h2h_fixtures

    team_a = _resolver_validado(client, serie_a, nome_a)
    team_b = _resolver_validado(client, serie_a, nome_b)
    assert team_a.id != team_b.id

    fixtures = get_h2h_fixtures(client, team_a.id, team_b.id, last=5)
    for fixture in fixtures:
        assert fixture.involves(team_a.id, team_b.id), (
            f"Confronto {fixture.fixture_id} nao envolve "
            f"{team_a.id} x {team_b.id}"
        )

    result = get_h2h_analysis(client, team_a.id, team_b.id, last=5)
    assert result.team_a_id == team_a.id
    assert result.team_b_id == team_b.id
    for m in result.matches:
        assert m.fixture.involves(team_a.id, team_b.id)


# ----------------------------------------------------------------------
# Identidade unica atravessando os modulos de analise de alto nivel
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "nome", ["Corinthians", "Chapecoense-sc", "Palmeiras"]
)
def test_analyze_team_usa_identidade_validada(client, serie_a, nome):
    from src.analysis import analyze_team

    team = _resolver_validado(client, serie_a, nome)
    result = analyze_team(client, nome, last=10)

    # O modulo de analise usa o MESMO ID real resolvido e validado
    assert result["time"].id == team.id
    for game in result["jogos"]:
        assert isinstance(game.fixture_id, int)


def test_analyze_team_botafogo_ambiguo_pede_escolha(client):
    """'Botafogo' sem contexto de competicao e genuinamente ambiguo
    (Brazil 120 x Cameroon 5562): o backend deve perguntar ao usuario
    (regra 4) - nunca escolher sozinho."""
    from src.analysis import analyze_team
    from src.exceptions import AmbiguousTeamError

    with pytest.raises(AmbiguousTeamError) as exc:
        analyze_team(client, "Botafogo", last=10)
    ids = {c.id for c in exc.value.candidates}
    assert 120 in ids
    assert 5562 in ids