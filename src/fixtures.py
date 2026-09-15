"""Busca de partidas (endpoint /fixtures e /fixtures/headtohead).

Dados usados de cada fixture:
    fixture.id, fixture.date, fixture.status.{short,elapsed}, fixture.venue.city
    league.{id,name,round,season}, teams.home/away.{id,name}
    goals.home, goals.away
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any

from src.api_client import APIFootballClient
from src.config import DEFAULT_TIMEZONE

FINISHED_STATUS = {"FT", "AET", "PEN"}
LIVE_STATUS = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}


@dataclass
class Fixture:
    fixture_id: int
    date: str
    status: str
    elapsed: int | None          # minuto atual (jogos ao vivo)
    league_id: int
    league_name: str
    round: str                    # fase (ex.: "Quarter-finals", "Regular Season - 12")
    season: int | None
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    goals_home: int | None
    goals_away: int | None
    venue: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATUS

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUS

    def is_home_for(self, team_id: int) -> bool:
        return self.home_team_id == team_id

    def involves(self, team_a: int, team_b: int) -> bool:
        return {self.home_team_id, self.away_team_id} == {team_a, team_b}


def parse_fixture(raw: dict[str, Any]) -> Fixture:
    return Fixture(
        fixture_id=raw["fixture"]["id"],
        date=raw["fixture"]["date"],
        status=raw["fixture"]["status"]["short"],
        elapsed=raw["fixture"]["status"].get("elapsed"),
        league_id=raw["league"]["id"],
        league_name=raw["league"]["name"],
        round=raw["league"].get("round") or "",
        season=raw["league"].get("season"),
        home_team_id=raw["teams"]["home"]["id"],
        home_team_name=raw["teams"]["home"]["name"],
        away_team_id=raw["teams"]["away"]["id"],
        away_team_name=raw["teams"]["away"]["name"],
        goals_home=raw["goals"]["home"],
        goals_away=raw["goals"]["away"],
        venue=(raw["fixture"].get("venue") or {}).get("city"),
    )


# ----------------------------------------------------------------------
# Dedup de consumo (Etapa 10 do failover): fluxos diferentes pedem a
# mesma lista com `last` diferente (pre-live 20, live 10, teste 1) e cada
# valor de `last` gera uma CHAVE de cache distinta => gasto real de cota
# para os mesmos jogos. Reuso: se um `last` MAIOR ja esta em cache e
# valido (mesmo TTL), fatiamos os N mais recentes DELE — sem nova
# chamada. Jogos encerrados sao imutaveis, e a API devolve os mais
# recentes primeiro em `last=N`; o motor recebe exatamente os mesmos N
# jogos mais recentes que a rede retornaria na janela de validade.
# ----------------------------------------------------------------------
_LADDER_LAST = (50, 20, 10, 5, 1)


def _reuso_lista_maior(
    client: APIFootballClient,
    endpoint: str,
    base_params: dict[str, Any],
    last_desejado: int,
) -> list[dict[str, Any]] | None:
    """Procura em cache uma lista `last` MAIOR ainda valida e devolve os
    `last_desejado` itens mais recentes. None se nao houver (fluxo normal
    segue para a rede). Nao altera nenhum dado — apenas evita re-coletar
    exatamente a mesma informacao."""
    cache = getattr(client, "cache", None)
    if cache is None:
        return None
    for maior in _LADDER_LAST:
        if maior <= last_desejado:
            continue
        params = dict(base_params)
        params["last"] = maior
        try:
            cached = cache.get(endpoint, params)
        except Exception:
            continue
        if cached and len(cached) >= last_desejado:
            return cached[:last_desejado]
    return None


def get_last_fixtures(
    client: APIFootballClient, team_id: int, last: int = 20
) -> list[Fixture]:
    """Ultimos N jogos do time (todos os status); o filtro por encerrado
    e feito na camada de estatisticas.

    O parametro timezone garante que fixture.date venha da API ja em
    America/Sao_Paulo (sem conversao local).
    """
    base = {"team": team_id, "timezone": DEFAULT_TIMEZONE}
    reuso = _reuso_lista_maior(client, "/fixtures", base, last)
    if reuso is not None:
        return [parse_fixture(raw) for raw in reuso]
    response = client.get(
        "/fixtures",
        params={**base, "last": last},
    )
    return [parse_fixture(raw) for raw in response]


def get_fixtures_today(
    client: APIFootballClient,
    league_id: int | None = None,
    on_date: str | None = None,
) -> list[Fixture]:
    """Jogos de uma data (padrao: hoje) no fuso America/Sao_Paulo."""
    params = {
        "date": on_date or date_cls.today().isoformat(),
        "timezone": DEFAULT_TIMEZONE,
    }
    if league_id:
        params["league"] = league_id
    response = client.get("/fixtures", params=params)
    return [parse_fixture(raw) for raw in response]


def get_live_fixtures(client: APIFootballClient) -> list[Fixture]:
    """Todos os jogos em andamento agora."""
    """Todos os jogos em andamento agora (datas em America/Sao_Paulo)."""
    response = client.get(
        "/fixtures", params={"live": "all", "timezone": DEFAULT_TIMEZONE}
    )
    return [parse_fixture(raw) for raw in response]


def get_h2h_fixtures(
    client: APIFootballClient, team_a_id: int, team_b_id: int, last: int = 10
) -> list[Fixture]:
    """CONFRONTO DIRETO: ultimas partidas ENTRE os dois times (endpoint
    dedicado /fixtures/headtohead). Nao mistura com historico individual.

    A API nao aceita last > 50; limitamos e avisamos quando ha menos
    confrontos do que o solicitado. Datas em America/Sao_Paulo.
    """
    last = min(last, 50)
    base = {"h2h": f"{team_a_id}-{team_b_id}", "timezone": DEFAULT_TIMEZONE}
    reuso = _reuso_lista_maior(client, "/fixtures/headtohead", base, last)
    if reuso is not None:
        return [parse_fixture(raw) for raw in reuso]
    response = client.get(
        "/fixtures/headtohead",
        params={**base, "last": last},
    )
    return [parse_fixture(raw) for raw in response]


def get_fixture_by_id(
    client: APIFootballClient, fixture_id: int
) -> Fixture | None:
    response = client.get(
        "/fixtures", params={"id": fixture_id, "timezone": DEFAULT_TIMEZONE}
    )
    return parse_fixture(response[0]) if response else None


def get_next_fixtures(
    client: APIFootballClient, team_id: int, next_n: int = 10
) -> list[Fixture]:
    """Proximos N jogos agendados do time (datas em America/Sao_Paulo)."""
    response = client.get(
        "/fixtures",
        params={"team": team_id, "next": next_n, "timezone": DEFAULT_TIMEZONE},
    )
    return [parse_fixture(raw) for raw in response]