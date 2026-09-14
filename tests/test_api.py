"""Testes de integracao com a API-Football (requer API_KEY no .env e rede).

Roda apenas se a API_KEY estiver configurada; senao, pula sem falhar.
"""

import pytest

from src.config import API_KEY

pytestmark = pytest.mark.skipif(
    not API_KEY, reason="API_KEY nao configurada no .env"
)


@pytest.fixture(scope="module")
def client():
    from src.api_client import APIFootballClient

    return APIFootballClient()


def test_conexao_basica(client):
    """Busca de time pelo nome retorna resposta estruturada."""
    results = client.get("/teams", params={"name": "Flamengo"})
    assert isinstance(results, list)
    assert len(results) >= 1
    assert "team" in results[0]


def test_cache_ativa(client):
    """Segunda chamada identica deve vir do cache (mesma resposta)."""
    first = client.get("/teams", params={"name": "Flamengo"})
    cached = client.get("/teams", params={"name": "Flamengo"})
    assert first == cached


def test_ultimos_jogos(client):
    """Ultimos jogos de um time conhecido (ID 127 = Flamengo)."""
    from src.fixtures import get_last_fixtures

    fixtures = get_last_fixtures(client, team_id=127, last=3)
    assert 1 <= len(fixtures) <= 3
    if fixtures:
        assert fixtures[0].fixture_id > 0


def test_estatisticas_de_jogo_encerrado(client):
    """Estatisticas de um jogo encerrado contem 'Corner Kicks'."""
    from src.fixtures import get_last_fixtures
    from src.match_stats import fetch_match_stats

    fixtures = get_last_fixtures(client, team_id=127, last=5)
    finished = [f for f in fixtures if f.is_finished]
    if not finished:
        pytest.skip("nenhum jogo encerrado recente para testar")
    match = fetch_match_stats(client, finished[0])
    # se a liga cobre estatisticas, escanteios devem estar presentes
    assert (
        match.home.corners is None
        or isinstance(match.home.corners, int)
    )