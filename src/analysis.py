"""Analises de alto nivel (casos de uso da plataforma).

Cada funcao combina resolucao de nomes, coleta de dados e calculos,
retornando dicionarios prontos para o modulo de formatacao (report.py).
Regra global: se a API nao fornecer um dado, o resultado diz isso -
nenhum valor e inventado.
"""

from __future__ import annotations

import warnings
from typing import Any

from src.api_client import APIFootballClient
from src.config import DEFAULT_LAST_N, DEFAULT_TIMEZONE, OVER_LINES
from src.exceptions import (
    DataUnavailableError,
    IdentityDivergenceError,
    UserFacingError,
)
from src.fixtures import (
    Fixture,
    get_fixtures_today,
    get_live_fixtures,
    get_next_fixtures,
)
from src.h2h import H2HResult, get_h2h_analysis, h2h_corner_stats
from src.identity import (
    fixture_teams,
    resolve_match_teams_validated,
    resolve_team_validated,
)
from src.match_stats import (
    MatchStats,
    TeamGameStats,
    fetch_match_stats,
    fetch_team_history,
)
from src.resolver import (
    League,
    Team,
    resolve_league,
)
from src.stats import (
    compute_h2h_stats,
    compute_team_stats,
    metrics_panel,
    xcorners_heuristic,
)


# ----------------------------------------------------------------------
# Time: perfil de escanteios dos ultimos N jogos
# ----------------------------------------------------------------------
def analyze_team(
    client: APIFootballClient, team_name: str, last: int = DEFAULT_LAST_N
) -> dict[str, Any]:
    # REGRA DE IDENTIDADE: ID resolvido pela API e validado (ID + nome)
    # ANTES de qualquer coleta de estatisticas (todos os modulos).
    team = resolve_team_validated(
        client, team_name, context=f"perfil do time '{team_name}'"
    )
    games, unavailable = fetch_team_history(
        client, team.id, last=last, team_name=team.name
    )
    stats = compute_team_stats(games) if games else None
    return {
        "time": team,
        "solicitados": last,
        "com_dados": len(games),
        "sem_estatisticas": unavailable,
        "stats": stats,
        "metricas": metrics_panel(games) if games else None,
        "jogos": list(reversed(games)),  # mais recente primeiro
    }


# ----------------------------------------------------------------------
# Comparar: dois historicos individuais lado a lado
# ----------------------------------------------------------------------
def compare_teams(
    client: APIFootballClient,
    name_a: str,
    name_b: str,
    last: int = DEFAULT_LAST_N,
) -> dict[str, Any]:
    team_a = resolve_team_validated(
        client, name_a, context=f"comparacao: '{name_a}'"
    )
    team_b = resolve_team_validated(
        client, name_b, context=f"comparacao: '{name_b}'"
    )
    games_a, un_a = fetch_team_history(client, team_a.id, last, team_a.name)
    games_b, un_b = fetch_team_history(client, team_b.id, last, team_b.name)
    stats_a = compute_team_stats(games_a) if games_a else None
    stats_b = compute_team_stats(games_b) if games_b else None
    xcorners = None
    if stats_a and stats_b:
        xcorners = xcorners_heuristic(stats_a, stats_b)
    return {
        "time_a": team_a,
        "time_b": team_b,
        "historico": "individual",  # duas amostras separadas (NAO e H2H)
        "stats_a": stats_a,
        "stats_b": stats_b,
        "sem_estatisticas_a": un_a,
        "sem_estatisticas_b": un_b,
        "xcorners": xcorners,
    }


# ----------------------------------------------------------------------
# H2H: confronto direto (apenas partidas entre os dois times)
# ----------------------------------------------------------------------
def analyze_h2h(
    client: APIFootballClient,
    name_a: str,
    name_b: str,
    last: int = 10,
) -> dict[str, Any]:
    team_a = resolve_team_validated(
        client, name_a, context=f"H2H: '{name_a}'"
    )
    team_b = resolve_team_validated(
        client, name_b, context=f"H2H: '{name_b}'"
    )
    h2h: H2HResult = get_h2h_analysis(client, team_a.id, team_b.id, last=last)
    stats = h2h_corner_stats(h2h)
    return {"time_a": team_a, "time_b": team_b, "h2h": h2h, "stats": stats}


# ----------------------------------------------------------------------
# Hoje: jogos do dia
# ----------------------------------------------------------------------
def analyze_today(
    client: APIFootballClient, league_name: str | None = None
) -> dict[str, Any]:
    league: League | None = None
    league_id: int | None = None
    if league_name:
        league = resolve_league(client, league_name)
        league_id = league.id
    fixtures = get_fixtures_today(client, league_id=league_id)
    fixtures.sort(key=lambda f: (f.league_name, f.date))
    return {"liga": league, "jogos": fixtures}


# ----------------------------------------------------------------------
# Liga: media de escanteios da competicao INTEIRA na temporada atual
# ----------------------------------------------------------------------
def league_corner_average(
    client: APIFootballClient,
    league_id: int,
    season: int,
    league_name: str,
) -> dict[str, Any] | None:
    """Media de escanteios da LIGA sobre a competicao INTEIRA.

    REGRA DE INTEGRIDADE - "media da liga" so pode ser exibida quando:
      1. competicao identificada (league_id);
      2. temporada identificada (season);
      3. usadas TODAS as partidas finalizadas da competicao na temporada
         (nunca amostra dos ultimos N jogos, nunca media de um unico time);
      4. partida sem escanteios na API e EXCLUIDA e contabilizada -
         dado ausente nunca vira zero;
      5. a resposta informa a quantidade de partidas validas.

    Retorna None quando nenhuma partida valida tem escanteios na fonte.
    """
    fixtures = client.get(
        "/fixtures",
        params={
            "league": league_id,
            "season": season,
            "timezone": DEFAULT_TIMEZONE,
        },
    )
    from src.fixtures import parse_fixture

    finished = [parse_fixture(raw) for raw in fixtures]
    finished = [f for f in finished if f.is_finished]

    totals: list[float] = []
    sem_escanteios = 0
    for fixture in finished:
        try:
            match = fetch_match_stats(client, fixture)
        except DataUnavailableError:
            sem_escanteios += 1
            continue
        if (
            match.home.corners is not None
            and match.away.corners is not None
        ):
            totals.append(float(match.home.corners + match.away.corners))
        else:
            # escanteios ausentes para a partida: excluida, nunca zerada
            sem_escanteios += 1

    if not totals:
        return None

    from src.stats import describe, over_frequency

    return {
        "liga_id": league_id,
        "liga": league_name,
        "temporada": season,
        "partidas_encerradas": len(finished),
        "partidas_validas": len(totals),
        "partidas_sem_escanteios": sem_escanteios,
        "describe": describe(totals),
        "over": over_frequency(totals),
    }


def analyze_league(
    client: APIFootballClient, league_name: str, country: str | None = None
) -> dict[str, Any]:
    """Media de escanteios da LIGA na temporada atual (competicao inteira).

    Sem amostragem: usa todas as partidas finalizadas da competicao na
    temporada identificada. Media de amostra de N jogos (ou de um unico
    time) NUNCA e apresentada como "media da liga". `country` desambigua
    ligas com mesmo nome em paises diferentes.
    """
    league = resolve_league(client, league_name, country=country)
    if league.current_season is None:
        raise DataUnavailableError(
            f"Nao consegui identificar a temporada atual de {league.name}."
        )

    media = league_corner_average(
        client,
        league_id=league.id,
        season=league.current_season,
        league_name=league.name,
    )
    return {
        "liga": league,
        "temporada": league.current_season,
        "media": media,  # None quando sem partidas validas na fonte
    }


# ----------------------------------------------------------------------
# Pre-jogo: analise completa de um confronto futuro
# ----------------------------------------------------------------------
def pre_match_analysis(
    client: APIFootballClient, team_a_name: str, team_b_name: str
) -> dict[str, Any]:
    team_a = resolve_team_validated(
        client, team_a_name, context=f"pre-jogo: '{team_a_name}'"
    )
    team_b = resolve_team_validated(
        client, team_b_name, context=f"pre-jogo: '{team_b_name}'"
    )

    # Proximo jogo entre os dois (se houver)
    next_fixture: Fixture | None = None
    next_a = get_next_fixtures(client, team_a.id, next_n=10)
    for fixture in next_a:
        if fixture.involves(team_a.id, team_b.id):
            next_fixture = fixture
            break

    # REGRA DE IDENTIDADE: com fixture conhecido, os IDs das equipes
    # vem OBRIGATORIAMENTE do proprio fixture (revalidados por
    # /teams?id= contra o nome registrado no fixture). Divergencia =>
    # IdentityDivergenceError: a analise nao continua com outro clube.
    if next_fixture is not None:
        home_t, away_t = fixture_teams(client, next_fixture)
        if {home_t.id, away_t.id} != {team_a.id, team_b.id}:
            raise IdentityDivergenceError(
                "DIVERGENCIA DE IDENTIDADE: as equipes do fixture "
                f"{next_fixture.fixture_id} ({home_t.name} x {away_t.name}) "
                f"nao correspondem aos times resolvidos "
                f"({team_a.name} x {team_b.name}). Analise interrompida."
            )
        # Reancora: a partir daqui, TODOS os modulos usam os IDs do fixture
        by_id = {home_t.id: home_t, away_t.id: away_t}
        team_a = by_id[team_a.id]
        team_b = by_id[team_b.id]

    # Historicos individuais
    games_a, un_a = fetch_team_history(client, team_a.id, DEFAULT_LAST_N, team_a.name)
    games_b, un_b = fetch_team_history(client, team_b.id, DEFAULT_LAST_N, team_b.name)
    stats_a = compute_team_stats(games_a) if games_a else None
    stats_b = compute_team_stats(games_b) if games_b else None

    # Confronto direto (ultimos 10)
    h2h = get_h2h_analysis(client, team_a.id, team_b.id, last=10)
    h2h_stats = h2h_corner_stats(h2h)

    # Media da liga do proximo jogo (quando existir)
    league_avg = None
    if next_fixture is not None:
        league_avg = _league_average_for_fixture(client, next_fixture)

    xcorners = None
    if stats_a and stats_b:
        xcorners = xcorners_heuristic(stats_a, stats_b)

    return {
        "time_a": team_a,
        "time_b": team_b,
        "proximo_jogo": next_fixture,
        "stats_a": stats_a,
        "stats_b": stats_b,
        "sem_estatisticas_a": un_a,
        "sem_estatisticas_b": un_b,
        "h2h": h2h,
        "h2h_stats": h2h_stats,
        "media_liga": league_avg,
        "xcorners": xcorners,
    }


def _league_average_for_fixture(
    client: APIFootballClient, fixture: Fixture
) -> dict[str, Any] | None:
    """Media de escanteios da liga de um fixture (competicao INTEIRA).

    Regra de integridade: usa todas as partidas finalizadas da liga na
    temporada do fixture - nunca amostra de N jogos nem media de um
    unico time. Cache faz chamadas repetidas nao gastarem requisicoes.
    """
    try:
        return league_corner_average(
            client,
            league_id=fixture.league_id,
            season=fixture.season,  # type: ignore[arg-type]
            league_name=fixture.league_name,
        )
    except (DataUnavailableError, UserFacingError):
        return None


# ----------------------------------------------------------------------
# Ao vivo: jogo em andamento
# ----------------------------------------------------------------------
def live_analysis(
    client: APIFootballClient, spec: str, analise: bool = False
) -> dict[str, Any]:
    """Ao vivo: dados da API do jogo em andamento.

    Por padrao NAO gera projecao/interpretacao (regra de saida).
    A projecao de ritmo (estimativa) so e calculada quando o usuario
    pede explicitamente analise (analise=True).

    spec pode ser "Time A x Time B" ou o ID da partida.
    """
    fixture: Fixture | None = None
    if spec.strip().isdigit():
        from src.fixtures import get_fixture_by_id

        fixture = get_fixture_by_id(client, int(spec))
        if fixture and not fixture.is_live:
            fixture = None
    else:
        team_a, team_b = resolve_match_teams_validated(
            client, spec, context="analise ao vivo"
        )
        live = get_live_fixtures(client)
        for f in live:
            if f.involves(team_a.id, team_b.id):
                fixture = f
                break

    if fixture is None:
        live_now = get_live_fixtures(client)
        msg = (
            "Nenhum jogo ao vivo encontrado para esse confronto agora."
            f" ({len(live_now)} jogos em andamento no total)."
        )
        raise UserFacingError(msg)

    try:
        match = fetch_match_stats(client, fixture)
    except DataUnavailableError:
        match = None

    projection = None
    if (
        analise
        and match
        and match.home.corners is not None
        and match.away.corners is not None
        and fixture.elapsed
        and fixture.elapsed > 0
    ):
        total = match.home.corners + match.away.corners
        projection = {
            "escanteios_atuais": total,
            "minuto": fixture.elapsed,
            "ritmo_por_minuto": round(total / fixture.elapsed, 3),
            "projecao_90min": round(total / fixture.elapsed * 90, 1),
        }

    return {"fixture": fixture, "match": match, "projecao": projection}


# ----------------------------------------------------------------------
# Pressao ofensiva (proxy com chutes/posse do jogo)
# ----------------------------------------------------------------------
def offensive_pressure(match: MatchStats) -> dict[str, Any]:
    """Resumo de pressao ofensiva de uma partida (chutes e posse)."""
    fixture = match.fixture
    return {
        "jogo": f"{fixture.home_team_name} x {fixture.away_team_name}",
        "chutes": {
            fixture.home_team_name: match.home.shots,
            fixture.away_team_name: match.away.shots,
        },
        "chutes_no_gol": {
            fixture.home_team_name: match.home.shots_on_goal,
            fixture.away_team_name: match.away.shots_on_goal,
        },
        "posse": {
            fixture.home_team_name: match.home.possession_pct,
            fixture.away_team_name: match.away.possession_pct,
        },
        "gols_esperados": {
            fixture.home_team_name: match.home.expected_goals,
            fixture.away_team_name: match.away.expected_goals,
        },
    }