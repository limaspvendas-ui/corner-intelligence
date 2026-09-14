"""Testes da resolucao de ligas - desambiguacao por pais (sem rede).

Cenario real: "Serie A" existe no Brasil (id 71) e na Italia (id 135);
o usuario escolhe pelo pais, nunca precisa de ID.
"""

import pytest

from src.exceptions import NotFoundError, UserFacingError
from src.resolver import resolve_league


def _league_raw(league_id, name, country, current=2026):
    return {
        "league": {"id": league_id, "name": name, "type": "League"},
        "country": {"name": country},
        "seasons": [{"year": current, "current": True}],
    }


class FakeClient:
    def __init__(self, response):
        self._response = response

    def get(self, endpoint, params=None, use_cache=True):
        return self._response


RESULTS = [
    _league_raw(71, "Serie A", "Brazil"),
    _league_raw(135, "Serie A", "Italy"),
]


def test_sem_pais_levanta_ambiguidade():
    with pytest.raises(UserFacingError) as exc:
        resolve_league(FakeClient(RESULTS), "Serie A")
    assert "AMBIGUIDADE" in str(exc.value)
    assert "Brazil" in str(exc.value) and "Italy" in str(exc.value)


def test_com_pais_resolve_a_liga_certa():
    league = resolve_league(FakeClient(RESULTS), "Serie A", country="Brazil")
    assert league.id == 71
    assert league.name == "Serie A"
    assert league.country == "Brazil"
    assert league.current_season == 2026


def test_pais_sem_match_e_nao_encontrado():
    with pytest.raises(NotFoundError):
        resolve_league(FakeClient(RESULTS), "Serie A", country="Espanha")