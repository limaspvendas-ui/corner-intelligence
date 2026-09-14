"""CONFRONTO DIRETO (H2H) - Flamengo x Palmeiras, etc.

Regras do H2H (nao confundir com historico individual):
    "Ultimos 10 confrontos Flamengo x Palmeiras" = as 10 partidas mais
    recentes disputadas ENTRE Flamengo e Palmeiras (endpoint dedicado
    /fixtures/headtohead). NUNCA misturar com os ultimos 10 jogos de
    cada time separadamente - isso e o comando "comparar".

    - Limite maximo da API: 50 confrontos.
    - Se houver menos confrontos disponiveis que o solicitado, informa
      quantidade solicitada x quantidade encontrada.
    - Nunca completar a amostra artificialmente.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any

from src.api_client import APIFootballClient
from src.exceptions import DataUnavailableError
from src.fixtures import Fixture, get_h2h_fixtures
from src.match_stats import MatchStats, SideStats, fetch_match_stats
from src.stats import compute_h2h_stats


@dataclass
class H2HMatch:
    """Um confronto direto com estatisticas completas quando disponiveis.

    Os campos first_*/second_* (por tempo) ficam None quando a API nao
    retorna statistics_1h/statistics_2h para a partida - nunca inventados.
    """

    fixture: Fixture
    corners_home: int | None = None
    corners_away: int | None = None
    corners_total: int | None = None
    shots_home: int | None = None
    shots_away: int | None = None
    shots_on_goal_home: int | None = None
    shots_on_goal_away: int | None = None
    yellow_home: int | None = None
    yellow_away: int | None = None
    red_home: int | None = None
    red_away: int | None = None
    possession_home: int | None = None
    possession_away: int | None = None
    fouls_home: int | None = None
    fouls_away: int | None = None
    offsides_home: int | None = None
    offsides_away: int | None = None
    # Por tempo (half=true; None quando a API nao fornece para a partida):
    first_home: SideStats | None = None
    first_away: SideStats | None = None
    second_home: SideStats | None = None
    second_away: SideStats | None = None
    stats_available: bool = True

    @classmethod
    def from_match(cls, match: MatchStats) -> "H2HMatch":
        home, away = match.home, match.away
        corners_total = (
            home.corners + away.corners
            if home.corners is not None and away.corners is not None
            else None
        )
        return cls(
            fixture=match.fixture,
            corners_home=home.corners,
            corners_away=away.corners,
            corners_total=corners_total,
            shots_home=home.shots,
            shots_away=away.shots,
            shots_on_goal_home=home.shots_on_goal,
            shots_on_goal_away=away.shots_on_goal,
            yellow_home=home.yellow_cards,
            yellow_away=away.yellow_cards,
            red_home=home.red_cards,
            red_away=away.red_cards,
            possession_home=home.possession_pct,
            possession_away=away.possession_pct,
            fouls_home=home.fouls,
            fouls_away=away.fouls,
            offsides_home=home.offsides,
            offsides_away=away.offsides,
            first_home=match.first_home,
            first_away=match.first_away,
            second_home=match.second_home,
            second_away=match.second_away,
        )


@dataclass
class H2HResult:
    requested: int
    team_a_id: int
    team_b_id: int
    matches: list[H2HMatch] = field(default_factory=list)
    skipped_no_stats: int = 0  # confrontos sem estatisticas na API

    @property
    def found(self) -> int:
        """Total de confrontos encontrados entre os dois times."""
        return len(self.matches) + self.skipped_no_stats

    @property
    def with_stats(self) -> int:
        return len(self.matches)


def get_h2h_analysis(
    client: APIFootballClient,
    team_a_id: int,
    team_b_id: int,
    last: int = 10,
) -> H2HResult:
    """Busca os ultimos confrontos diretos (ate 50) com estatisticas.

    Jogos sem estatisticas na API sao contados como skipped e avisados -
    nunca preenchidos artificialmente.
    """
    last = min(last, 50)
    fixtures = get_h2h_fixtures(client, team_a_id, team_b_id, last=last)
    finished = [f for f in fixtures if f.is_finished]

    result = H2HResult(
        requested=last, team_a_id=team_a_id, team_b_id=team_b_id
    )
    for fixture in finished:
        try:
            match = fetch_match_stats(client, fixture)
            result.matches.append(H2HMatch.from_match(match))
        except DataUnavailableError:
            result.skipped_no_stats += 1
            warnings.warn(
                f"Confronto {fixture.fixture_id} ({fixture.date[:10]}) sem "
                "estatisticas na API - nao incluido nos calculos."
            )
    return result


def h2h_corner_stats(result: H2HResult) -> dict[str, Any] | None:
    """Resumo estatistico dos confrontos diretos com escanteios."""
    if not result.matches:
        return None
    return compute_h2h_stats(result)