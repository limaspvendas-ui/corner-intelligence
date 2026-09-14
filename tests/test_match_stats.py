"""Testes do parsing de /fixtures/statistics com half=true (por tempo).

Usa respostas falsas no formato real da API (blocos statistics,
statistics_1h e statistics_2h) - nao gasta requisicoes.
"""

from src.fixtures import Fixture
from src.match_stats import fetch_match_stats


class FakeClient:
    """Client falso: devolve a resposta montada e registra os parametros."""

    def __init__(self, response):
        self._response = response
        self.last_params = None

    def get(self, endpoint, params=None, use_cache=True):
        self.last_params = params
        return self._response


def _fixture():
    return Fixture(
        fixture_id=1492363,
        date="2026-09-06T13:00:00-03:00",
        status="FT",
        elapsed=90,
        league_id=71,
        league_name="Serie A",
        round="Regular Season - 26",
        season=2026,
        home_team_id=147,
        home_team_name="Coritiba",
        away_team_id=7848,
        away_team_name="Mirassol",
        goals_home=1,
        goals_away=2,
    )


def _stat(stat_type, value):
    return {"type": stat_type, "value": value}


def _block(team_id, name, stats, stats_1h=None, stats_2h=None):
    block = {"team": {"id": team_id, "name": name}, "statistics": stats}
    if stats_1h is not None:
        block["statistics_1h"] = stats_1h
    if stats_2h is not None:
        block["statistics_2h"] = stats_2h
    return block


def test_parametro_half_true_e_parsing_por_tempo():
    home = _block(
        147, "Coritiba",
        [_stat("Corner Kicks", 5), _stat("Fouls", 14)],
        [_stat("Corner Kicks", 2)],  # 1oT SEM Fouls
        [_stat("Corner Kicks", 3)],
    )
    away = _block(
        7848, "Mirassol",
        [_stat("Corner Kicks", 7), _stat("Fouls", 10)],
        [_stat("Corner Kicks", 5)],
        [_stat("Corner Kicks", 2)],
    )
    client = FakeClient([home, away])

    match = fetch_match_stats(client, _fixture())

    assert client.last_params == {"fixture": 1492363, "half": "true"}
    # jogo completo
    assert match.home.corners == 5
    assert match.away.corners == 7
    assert match.home.fouls == 14
    # 1o tempo
    assert match.first_home.corners == 2
    assert match.first_away.corners == 5
    # 2o tempo
    assert match.second_home.corners == 3
    assert match.second_away.corners == 2
    # campo ausente no bloco de tempo fica None (nunca inventado)
    assert match.first_home.fouls is None
    assert match.has_halves is True


def test_resposta_sem_blocos_por_tempo():
    """Partidas sem statistics_1h/2h: tempos ficam None, jogo completo intacto."""
    home = _block(147, "Coritiba", [_stat("Corner Kicks", 5)])
    away = _block(7848, "Mirassol", [_stat("Corner Kicks", 7)])
    client = FakeClient([home, away])

    match = fetch_match_stats(client, _fixture())

    assert match.home.corners == 5
    assert match.away.corners == 7
    assert match.first_home is None
    assert match.first_away is None
    assert match.second_home is None
    assert match.second_away is None
    assert match.has_halves is False


def test_perspective_por_tempo():
    """to_team_perspective popula escanteios 1oT/2oT quando existem."""
    from src.match_stats import to_team_perspective

    home = _block(
        147, "Coritiba",
        [_stat("Corner Kicks", 5)],
        [_stat("Corner Kicks", 2)],
        [_stat("Corner Kicks", 3)],
    )
    away = _block(
        7848, "Mirassol",
        [_stat("Corner Kicks", 7)],
        [_stat("Corner Kicks", 5)],
        [_stat("Corner Kicks", 2)],
    )
    match = fetch_match_stats(FakeClient([home, away]), _fixture())

    g = to_team_perspective(match, 147)  # perspectiva do mandante
    assert g.corners_for_1st_half == 2
    assert g.corners_against_1st_half == 5
    assert g.corners_for_2nd_half == 3
    assert g.corners_against_2nd_half == 2