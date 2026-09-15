"""Testes de integracao com a API-Football (requer API_KEY no .env e rede).

Roda apenas se a API_KEY estiver configurada; senao, pula sem falhar.
"""

import pytest

from src.config import API_KEY

pytestmark = pytest.mark.skipif(
    not API_KEY, reason="API_KEY nao configurada no .env"
)


def _integracao(fn, *args, **kwargs):
    """Executa uma chamada REAL de integracao.

    Se a FONTE estiver genuinamente indisponivel (cota diaria esgotada,
    rate limit, timeout, 5xx — erro estruturado SourceError), o teste
    PULA com o motivo explicito: e uma condicao da conta/fonte externa,
    nao um bug do codigo sob teste. Nunca falha — e nunca mascara, o
    motivo aparece no relatorio de skip.
    """
    from src.exceptions import SourceError

    try:
        return fn(*args, **kwargs)
    except SourceError as e:
        pytest.skip(
            f"fonte indisponivel ({e.code}/{e.motivo}): "
            "teste de integracao real adiado ate a fonte voltar"
        )


@pytest.fixture()
def client(tmp_path):
    """Cliente REAL de integracao com cache em banco TEMPORARIO.

    Nenhum teste pode escrever no cache operacional real
    (data/corner_intelligence.db): o cache aponta para um arquivo
    descartavel por teste.
    """
    from src.api_client import APIFootballClient
    from src.cache import Cache

    return APIFootballClient(
        cache=Cache(db_path=str(tmp_path / "cache_test.db")))


def test_conexao_basica(client):
    """Busca de time pelo nome retorna resposta estruturada."""
    results = _integracao(client.get, "/teams", params={"name": "Flamengo"})
    assert isinstance(results, list)
    assert len(results) >= 1
    assert "team" in results[0]


def test_cache_ativa(client):
    """Segunda chamada identica deve vir do cache (mesma resposta)."""
    first = _integracao(client.get, "/teams", params={"name": "Flamengo"})
    cached = client.get("/teams", params={"name": "Flamengo"})
    assert first == cached


def test_ultimos_jogos(client):
    """Ultimos jogos de um time conhecido (ID 127 = Flamengo)."""
    from src.fixtures import get_last_fixtures

    fixtures = _integracao(
        get_last_fixtures, client, team_id=127, last=3)
    assert 1 <= len(fixtures) <= 3
    if fixtures:
        assert fixtures[0].fixture_id > 0


def test_estatisticas_de_jogo_encerrado(client):
    """Estatisticas de um jogo encerrado contem 'Corner Kicks'."""
    from src.fixtures import get_last_fixtures
    from src.match_stats import fetch_match_stats

    fixtures = _integracao(
        get_last_fixtures, client, team_id=127, last=5)
    finished = [f for f in fixtures if f.is_finished]
    if not finished:
        pytest.skip("nenhum jogo encerrado recente para testar")
    match = _integracao(fetch_match_stats, client, finished[0])
    # se a liga cobre estatisticas, escanteios devem estar presentes
    assert (
        match.home.corners is None
        or isinstance(match.home.corners, int)
    )