"""Estatisticas por partida (endpoint /fixtures/statistics).

Cobre: escanteios, gols, finalizacoes (chutes), cartoes, posse de bola,
faltas e impedimentos.

ESTATISTICAS POR TEMPO (1oT/2oT):
    O endpoint aceita o parametro half=true. A resposta traz, POR TIME,
    tres blocos: "statistics" (jogo completo), "statistics_1h" (1o tempo)
    e "statistics_2h" (2o tempo). A API-Football fornece esses dados a
    partir da temporada 2024; partidas anteriores (ou sem cobertura) nao
    os retornam - nesse caso os campos ficam None e o relatorio informa
    "dado nao disponivel na fonte". Nenhum valor e inventado nem
    extrapolado de outra consulta.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from src.api_client import APIFootballClient
from src.exceptions import DataUnavailableError
from src.fixtures import Fixture
from src.logging_config import get_logger

# Tipos de estatistica usados pela API-Football v3 (nomes exatos)
STAT_CORNER_KICKS = "Corner Kicks"
STAT_TOTAL_SHOTS = "Total Shots"
STAT_SHOTS_ON_GOAL = "Shots on Goal"
STAT_YELLOW_CARDS = "Yellow Cards"
STAT_RED_CARDS = "Red Cards"
STAT_BALL_POSSESSION = "Ball Possession"
STAT_EXPECTED_GOALS = "Expected Goals"
STAT_FOULS = "Fouls"
STAT_OFFSIDES = "Offsides"


@dataclass
class SideStats:
    """Estatisticas de um time em uma partida.

    Serve tanto para o jogo completo quanto para um tempo (1oT/2oT):
    valores ficam None quando a API nao os fornece naquele bloco.
    """

    corners: int | None = None
    shots: int | None = None
    shots_on_goal: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    possession_pct: int | None = None
    expected_goals: float | None = None
    fouls: int | None = None
    offsides: int | None = None


@dataclass
class MatchStats:
    """Estatisticas completas de uma partida, por lado.

    home/away = jogo completo. first_*/second_* = por tempo, vindos de
    statistics_1h/statistics_2h (parametro half=true; a API os fornece a
    partir da temporada 2024). Quando a API nao retorna os blocos por
    tempo para a partida, ficam None - nunca preenchidos artificialmente.
    """

    fixture: Fixture
    home: SideStats
    away: SideStats
    first_home: SideStats | None = None
    first_away: SideStats | None = None
    second_home: SideStats | None = None
    second_away: SideStats | None = None

    @property
    def corners_total(self) -> int:
        if self.home.corners is None or self.away.corners is None:
            raise DataUnavailableError(
                f"Escanteios nao disponiveis para a partida "
                f"{self.fixture.fixture_id} ({self.fixture.date[:10]})."
            )
        return self.home.corners + self.away.corners

    @property
    def has_halves(self) -> bool:
        return self.first_home is not None or self.second_home is not None


@dataclass
class TeamGameStats:
    """Uma partida vista da perspectiva de um time (base dos calculos)."""

    fixture_id: int
    date: str
    league: str
    round: str
    status: str
    opponent: str
    played_at_home: bool
    corners_for: int
    corners_against: int
    corners_total: int
    goals_for: int | None
    goals_against: int | None
    shots_for: int | None
    shots_against: int | None
    shots_on_goal_for: int | None
    shots_on_goal_against: int | None
    possession_for: int | None
    yellow_for: int | None
    red_for: int | None
    fouls_for: int | None = None
    offsides_for: int | None = None
    # Do adversario (mesma resposta da API da partida; None quando ausente):
    yellow_against: int | None = None
    red_against: int | None = None
    possession_against: int | None = None
    fouls_against: int | None = None
    offsides_against: int | None = None
    # Por tempo: so populados quando a API retorna statistics_1h/2h
    # (half=true); caso contrario ficam None - nunca inventados:
    corners_for_1st_half: int | None = None
    corners_against_1st_half: int | None = None
    corners_for_2nd_half: int | None = None
    corners_against_2nd_half: int | None = None


# ----------------------------------------------------------------------
def _extract(statistics: list[dict[str, Any]], stat_type: str) -> Any:
    for stat in statistics:
        if stat.get("type") == stat_type:
            return stat.get("value")
    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _to_possession(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_side(statistics: list[dict[str, Any]] | None) -> SideStats:
    return SideStats(
        corners=_to_int(_extract(statistics or [], STAT_CORNER_KICKS)),
        shots=_to_int(_extract(statistics or [], STAT_TOTAL_SHOTS)),
        shots_on_goal=_to_int(_extract(statistics or [], STAT_SHOTS_ON_GOAL)),
        yellow_cards=_to_int(_extract(statistics or [], STAT_YELLOW_CARDS)),
        red_cards=_to_int(_extract(statistics or [], STAT_RED_CARDS)),
        possession_pct=_to_possession(
            _extract(statistics or [], STAT_BALL_POSSESSION)
        ),
        expected_goals=_to_float(_extract(statistics or [], STAT_EXPECTED_GOALS)),
        fouls=_to_int(_extract(statistics or [], STAT_FOULS)),
        offsides=_to_int(_extract(statistics or [], STAT_OFFSIDES)),
    )


def fetch_match_stats(
    client: APIFootballClient, fixture: Fixture
) -> MatchStats:
    """Busca as estatisticas de uma partida - jogo completo E, quando a API
    os fornece para a partida, os tempos 1o/2o (parametro half=true).

    Se a API nao fornecer as estatisticas, levanta DataUnavailableError
    (nunca preenche com valores falsos).
    """
    response = client.get(
        "/fixtures/statistics",
        params={"fixture": fixture.fixture_id, "half": "true"},
    )

    by_team: dict[int, SideStats] = {}
    by_team_1h: dict[int, SideStats] = {}
    by_team_2h: dict[int, SideStats] = {}
    for block in response:
        team_id = (block.get("team") or {}).get("id")
        if team_id is None:
            continue
        by_team[team_id] = _parse_side(block.get("statistics"))
        # Por tempo: so existem quando a API os retorna para a partida
        if block.get("statistics_1h"):
            by_team_1h[team_id] = _parse_side(block["statistics_1h"])
        if block.get("statistics_2h"):
            by_team_2h[team_id] = _parse_side(block["statistics_2h"])

    home = by_team.get(fixture.home_team_id, SideStats())
    away = by_team.get(fixture.away_team_id, SideStats())

    if home.corners is None and away.corners is None and fixture.is_finished:
        get_logger().warning(
            "Estatisticas ausentes no fixture %s (%s).",
            fixture.fixture_id,
            fixture.league_name,
        )
        raise DataUnavailableError(
            f"A API-Football nao fornece estatisticas para a partida "
            f"{fixture.home_team_name} x {fixture.away_team_name} "
            f"({fixture.date[:10]}, {fixture.league_name}). "
            "Nenhum valor foi inventado."
        )

    return MatchStats(
        fixture=fixture,
        home=home,
        away=away,
        first_home=by_team_1h.get(fixture.home_team_id),
        first_away=by_team_1h.get(fixture.away_team_id),
        second_home=by_team_2h.get(fixture.home_team_id),
        second_away=by_team_2h.get(fixture.away_team_id),
    )


def to_team_perspective(match: MatchStats, team_id: int) -> TeamGameStats:
    """Converte MatchStats para a perspectiva de um dos times."""
    fixture = match.fixture
    at_home = fixture.is_home_for(team_id)
    own, opp = (match.home, match.away) if at_home else (match.away, match.home)

    if own.corners is None or opp.corners is None:
        raise DataUnavailableError(
            f"Escanteios incompletos na partida {fixture.fixture_id}."
        )

    if at_home:
        goals_for, goals_against = fixture.goals_home, fixture.goals_away
        own_1h, opp_1h = match.first_home, match.first_away
        own_2h, opp_2h = match.second_home, match.second_away
    else:
        goals_for, goals_against = fixture.goals_away, fixture.goals_home
        own_1h, opp_1h = match.first_away, match.first_home
        own_2h, opp_2h = match.second_away, match.second_home

    return TeamGameStats(
        fixture_id=fixture.fixture_id,
        date=fixture.date,
        league=fixture.league_name,
        round=fixture.round,
        status=fixture.status,
        opponent=fixture.away_team_name if at_home else fixture.home_team_name,
        played_at_home=at_home,
        corners_for=own.corners,
        corners_against=opp.corners,
        corners_total=own.corners + opp.corners,
        goals_for=goals_for,
        goals_against=goals_against,
        shots_for=own.shots,
        shots_against=opp.shots,
        shots_on_goal_for=own.shots_on_goal,
        shots_on_goal_against=opp.shots_on_goal,
        possession_for=own.possession_pct,
        yellow_for=own.yellow_cards,
        red_for=own.red_cards,
        fouls_for=own.fouls,
        offsides_for=own.offsides,
        yellow_against=opp.yellow_cards,
        red_against=opp.red_cards,
        possession_against=opp.possession_pct,
        fouls_against=opp.fouls,
        offsides_against=opp.offsides,
        corners_for_1st_half=own_1h.corners if own_1h else None,
        corners_against_1st_half=opp_1h.corners if opp_1h else None,
        corners_for_2nd_half=own_2h.corners if own_2h else None,
        corners_against_2nd_half=opp_2h.corners if opp_2h else None,
    )


def fetch_team_history(
    client: APIFootballClient,
    team_id: int,
    last: int = 20,
    team_name: str = "",
) -> tuple[list[TeamGameStats], int]:
    """Ultimos N jogos encerrados do time com estatisticas completas.

    Retorna (jogos_validos, jogos_sem_estatisticas). Jogos sem dados na
    API sao contabilizados e avisados, nunca completados artificialmente.

    REGRA DE IDENTIDADE: quando o nome do time e informado, valida
    ID + nome ANTES de coletar. Divergencia => IdentityDivergenceError
    e a coleta e interrompida - nunca coleta historico de outro clube.
    """
    from src.fixtures import get_last_fixtures

    logger = get_logger()
    if team_name:
        from src.identity import validate_team_identity

        validate_team_identity(
            client,
            team_id,
            team_name,
            context=f"historico de '{team_name}'",
        )
    fixtures = get_last_fixtures(client, team_id, last=last)
    finished = [f for f in fixtures if f.is_finished]

    games: list[TeamGameStats] = []
    unavailable = 0
    for fixture in finished:
        try:
            match = fetch_match_stats(client, fixture)
            games.append(to_team_perspective(match, team_id))
        except DataUnavailableError:
            unavailable += 1
            logger.warning(
                "Sem estatisticas: fixture %s (%s, %s)",
                fixture.fixture_id,
                team_name or team_id,
                fixture.date[:10],
            )
    return games, unavailable