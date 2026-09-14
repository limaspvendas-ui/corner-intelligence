"""Resolucao automatica de nomes: times, ligas e partidas.

O usuario nunca precisa saber IDs. Regras:
    - busca exata primeiro ("name"), depois busca parcial ("search", min. 3 chars)
    - nenhum resultado  -> NotFoundError (mensagem simples)
    - varios resultados -> AmbiguousTeamError com candidatos;
      a CLI imprime a lista e o Claude pergunta ao usuario qual escolher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.api_client import APIFootballClient
from src.exceptions import AmbiguousTeamError, NotFoundError, UserFacingError
from src.fixtures import Fixture


@dataclass
class Team:
    id: int
    name: str
    country: str | None = None
    code: str | None = None
    founded: int | None = None


@dataclass
class League:
    id: int
    name: str
    country: str | None = None
    current_season: int | None = None


# ----------------------------------------------------------------------
def _team_from(raw: dict[str, Any]) -> Team:
    team = raw["team"]
    return Team(
        id=team["id"],
        name=team["name"],
        country=team.get("country"),
        code=team.get("code"),
        founded=team.get("founded"),
    )


def _candidates_message(candidates: list[Team], kind: str) -> str:
    lines = [f"AMBIGUIDADE: encontrei {len(candidates)} {kind} com esse nome:"]
    for c in candidates:
        extra = f" ({c.country})" if c.country else ""
        lines.append(f"  - {c.name}{extra} | ID {c.id}")
    lines.append("Escolha um dos candidatos e repita o pedido.")
    return "\n".join(lines)


def resolve_team(client: APIFootballClient, name: str) -> Team:
    """Resolve um nome de time em um Team unico."""
    name = name.strip()
    if len(name) < 3:
        raise NotFoundError(f"Digite ao menos 3 caracteres para buscar um time.")

    results = client.get("/teams", params={"name": name})
    if not results:
        results = client.get("/teams", params={"search": name})

    if not results:
        raise NotFoundError(
            f"Nenhum time encontrado na API-Football com o nome '{name}'."
        )

    candidates = [_team_from(raw) for raw in results]
    if len(candidates) > 1:
        raise AmbiguousTeamError(
            _candidates_message(candidates, "times"), candidates
        )
    return candidates[0]


def resolve_league(
    client: APIFootballClient, name: str, country: str | None = None
) -> League:
    """Resolve um nome de liga/campeonato, com a temporada atual.

    `country` (opcional) desambigua ligas com mesmo nome em paises
    diferentes (ex.: Serie A Brasil x Italia) - o usuario escolhe pelo
    pais, nunca precisa de ID.
    """
    name = name.strip()
    if len(name) < 3:
        raise NotFoundError(f"Digite ao menos 3 caracteres para buscar uma liga.")

    results = client.get("/leagues", params={"search": name})
    if not results:
        raise NotFoundError(
            f"Nenhuma liga encontrada na API-Football com o nome '{name}'."
        )

    if country:
        country_norm = country.strip().lower()
        filtrados = [
            raw for raw in results
            if ((raw.get("country") or {}).get("name") or "").lower() == country_norm
        ]
        if not filtrados:
            raise NotFoundError(
                f"Nenhuma liga '{name}' encontrada no pais '{country.strip()}'."
            )
        results = filtrados

    if len(results) > 1:
        candidates = []
        for raw in results:
            league = raw["league"]
            country_name = (raw.get("country") or {}).get("name")
            candidates.append(
                {"nome": league["name"], "pais": country_name, "id": league["id"]}
            )
        lines = [f"AMBIGUIDADE: encontrei {len(candidates)} ligas com esse nome:"]
        for c in candidates:
            extra = f" ({c['pais']})" if c["pais"] else ""
            lines.append(f"  - {c['nome']}{extra} | ID {c['id']}")
        lines.append(
            "Escolha uma das ligas e repita o pedido "
            "(no comando 'liga', use --pais para desambiguar)."
        )
        raise UserFacingError("\n".join(lines))

    raw = results[0]
    league = raw["league"]
    current_season = None
    for season in raw.get("seasons", []):
        if season.get("current"):
            current_season = season.get("year")
            break
    return League(
        id=league["id"],
        name=league["name"],
        country=(raw.get("country") or {}).get("name"),
        current_season=current_season,
    )


# ----------------------------------------------------------------------
def split_match_spec(spec: str) -> tuple[str, str] | None:
    """Separa 'Flamengo x Palmeiras' em ('Flamengo', 'Palmeiras').

    Aceita separadores: ' x ', ' X ', ' vs ', ' contra ' (case-insensitive).
    Retorna None se nao houver dois times.
    """
    match = re.split(r"\s+(?:x|vs\.?|contra)\s+", spec.strip(), flags=re.IGNORECASE)
    if len(match) == 2 and match[0].strip() and match[1].strip():
        return match[0].strip(), match[1].strip()
    return None


def resolve_match_teams(
    client: APIFootballClient, spec: str
) -> tuple[Team, Team]:
    """Resolve 'Time A x Time B' em dois Times unicos."""
    parts = split_match_spec(spec)
    if parts is None:
        raise UserFacingError(
            "Formato nao reconhecido. Use: \"Nome do time A x Nome do time B\"."
        )
    team_a = resolve_team(client, parts[0])
    team_b = resolve_team(client, parts[1])
    if team_a.id == team_b.id:
        raise UserFacingError("Os dois times informados sao o mesmo time.")
    return team_a, team_b


def find_live_or_recent_fixture(
    client: APIFootballClient, team_a: Team, team_b: Team
) -> Fixture | None:
    """Procura um jogo entre os dois times: ao vivo agora ou o mais recente
    (encerrado ou agendado)."""
    from src.fixtures import get_h2h_fixtures

    h2h = get_h2h_fixtures(client, team_a.id, team_b.id, last=10)
    for fixture in h2h:
        if fixture.is_live:
            return fixture
    if h2h:
        return h2h[0]
    return None