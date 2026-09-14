"""Identidade de equipes: resolucao e validacao OBRIGATORIA de IDs.

REGRA OBRIGATORIA (integridade - falha real que motivou este modulo):
    1. ID de time NUNCA e assumido, memorizado ou hardcoded: vem da
       API-Football (/teams) ou do PROPRIO fixture conhecido
       (teams.home.id / teams.away.id).
    2. Quando houver um fixture conhecido, os IDs das equipes vem
       OBRIGATORIAMENTE do fixture.
    3. Antes de coletar estatisticas, valida-se ID + nome (+
       competicao/temporada, quando aplicavel). Divergencia =>
       IdentityDivergenceError: a coleta e INTERROMPIDA e o erro
       informado. NUNCA continuar a analise com outro clube por engano.
    4. Somente associacoes JA VALIDADAS sao guardadas no cache de
       identidade (tabela validated_teams).
    5. A mesma identidade validada atravessa TODOS os modulos:
       escanteios, gols, cartoes, finalizacoes, posse, H2H, mando,
       1oT/2oT e futuras analises asiaticas.

Caso real prevenido por este modulo: IDs trocados (1404 = SKU
Amstetten tratado como Chapecoense; 125 = clube da Serie B tratado
como Botafogo) produziram historicos de OUTRO clube. Com a validacao
obrigatoria, essas coletas abortam com erro claro em vez de seguir.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from datetime import datetime, timezone as dt_timezone
from typing import Any

from src.api_client import APIFootballClient
from src.config import DATA_DIR, DB_PATH
from src.exceptions import (
    AmbiguousTeamError,
    IdentityDivergenceError,
    NotFoundError,
    UserFacingError,
)
from src.fixtures import Fixture
from src.resolver import Team, resolve_team, split_match_spec


# ----------------------------------------------------------------------
# Normalizacao de nomes para comparacao
# ----------------------------------------------------------------------
def normalize_name(name: str) -> str:
    """Nome normalizado: minusculas, sem acentos, apenas letras/numeros.

    Permite comparar 'Botafogo' com 'botafogo', 'Chapecoense-sc' com
    'CHAPECENSE-SC' etc., sem depender de grafia exata da API.
    """
    text = unicodedata.normalize("NFD", (name or "").strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return "".join(ch for ch in text if ch.isalnum())


def assert_same_team(
    team: Team, expected_name: str, context: str
) -> Team:
    """Valida ID + nome. Divergencia => IdentityDivergenceError (para tudo)."""
    if normalize_name(team.name) != normalize_name(expected_name):
        raise IdentityDivergenceError(
            f"DIVERGENCIA DE IDENTIDADE: esperava '{expected_name}', mas o "
            f"ID {team.id} corresponde a '{team.name}' "
            f"({team.country or 'pais nao informado'}). "
            f"Coleta interrompida em: {context}. "
            "A analise NAO pode continuar com outro clube por engano."
        )
    return team


# ----------------------------------------------------------------------
# Resolucao via API (nunca assumida)
# ----------------------------------------------------------------------
def fetch_team_by_id(client: APIFootballClient, team_id: int) -> Team:
    """Busca o time PELO ID real na API-Football (round-trip de validacao)."""
    results = client.get("/teams", params={"id": team_id})
    if not results:
        raise NotFoundError(
            f"Nenhum time encontrado na API-Football com o ID {team_id}."
        )
    raw = results[0]
    team = raw["team"]
    return Team(
        id=team["id"],
        name=team["name"],
        country=team.get("country"),
        code=team.get("code"),
        founded=team.get("founded"),
    )


def validate_team_identity(
    client: APIFootballClient,
    team_id: int,
    expected_name: str,
    context: str,
    league_id: int | None = None,
    season: int | None = None,
) -> Team:
    """Valida ID + nome (+ competicao/temporada) ANTES de coletar dados.

    Chamada por todos os modulos de analise. Em divergencia, lanca
    IdentityDivergenceError e a coleta nao comeca. Associacoes validas
    sao guardadas no cache de identidade (somente validadas).
    """
    team = fetch_team_by_id(client, team_id)
    assert_same_team(team, expected_name, context)
    if league_id is not None and season is not None:
        fixtures = client.get(
            "/fixtures",
            params={
                "team": team_id,
                "league": league_id,
                "season": season,
            },
        )
        if not fixtures:
            raise IdentityDivergenceError(
                f"DIVERGENCIA DE IDENTIDADE: '{team.name}' (ID {team_id}) nao "
                f"possui partidas na competicao {league_id} temporada {season}. "
                f"Coleta interrompida em: {context}."
            )
    ValidatedTeamsStore().save(team)
    return team


def resolve_team_validated(
    client: APIFootballClient,
    name: str,
    context: str = "",
    league_id: int | None = None,
    season: int | None = None,
) -> Team:
    """Nome -> ID SEMPRE via API + validacao obrigatoria da identidade.

    resolve_team faz a busca real (exata e depois parcial); o ID
    retornado e revalidado por /teams?id= (round-trip ID + nome). Isso
    impede que qualquer candidatura errada siga para a coleta.

    AMBIGUIDADE com contexto de competicao: quando liga e temporada sao
    informadas, candidatos sao desambiguados POR DADOS (participacao na
    competicao na temporada - ex.: 'Botafogo' Brazil 120 x Cameroon
    5562, apenas o brasileiro na Serie A). Se ainda houver mais de um
    participante, a ambiguidade e mantida - nunca uma escolha por
    palpite. Sem contexto de competicao, AmbiguousTeamError segue
    normal e o usuario escolhe (regra 4).
    """
    try:
        team = resolve_team(client, name)  # busca na API; nunca assumida
    except AmbiguousTeamError:
        if league_id is None or season is None:
            raise
        participants = []
        for candidate in _ambiguous_candidates(client, name):
            fixtures = client.get(
                "/fixtures",
                params={
                    "team": candidate.id,
                    "league": league_id,
                    "season": season,
                },
            )
            if fixtures:
                participants.append(candidate)
        if len(participants) != 1:
            raise  # segue ambiguo: o usuario escolhe
        team = participants[0]
    return validate_team_identity(
        client,
        team.id,
        name,
        context or f"resolucao de '{name}'",
        league_id=league_id,
        season=season,
    )


def _ambiguous_candidates(client: APIFootballClient, name: str) -> list[Team]:
    """Recupera os candidatos da busca por nome (mesma busca do resolver)."""
    results = client.get("/teams", params={"name": name})
    if not results:
        results = client.get("/teams", params={"search": name})
    from src.resolver import _team_from

    return [_team_from(raw) for raw in results]


def resolve_match_teams_validated(
    client: APIFootballClient, spec: str, context: str = ""
) -> tuple[Team, Team]:
    """'Time A x Time B' -> dois Times unicos, ambos validados."""
    parts = split_match_spec(spec)
    if parts is None:
        raise UserFacingError(
            "Formato nao reconhecido. Use: \"Nome do time A x Nome do time B\"."
        )
    team_a = resolve_team_validated(client, parts[0], context)
    team_b = resolve_team_validated(client, parts[1], context)
    if team_a.id == team_b.id:
        raise UserFacingError("Os dois times informados sao o mesmo time.")
    return team_a, team_b


# ----------------------------------------------------------------------
# Fixture conhecido: IDs OBRIGATORIAMENTE do fixture
# ----------------------------------------------------------------------
def fixture_teams(
    client: APIFootballClient,
    fixture: Fixture,
    expect_home: str | None = None,
    expect_away: str | None = None,
) -> tuple[Team, Team]:
    """Equipes de um fixture: IDs vindos OBRIGATORIAMENTE do fixture.

    Cada ID e validado por /teams?id= contra o nome registrado no
    proprio fixture (e contra os nomes esperados, quando informados).
    Divergencia => IdentityDivergenceError; a coleta nao comeca.
    """
    home = validate_team_identity(
        client,
        fixture.home_team_id,
        fixture.home_team_name,
        f"fixture {fixture.fixture_id} (mandante)",
    )
    away = validate_team_identity(
        client,
        fixture.away_team_id,
        fixture.away_team_name,
        f"fixture {fixture.fixture_id} (visitante)",
    )
    if expect_home is not None:
        assert_same_team(
            home, expect_home, f"fixture {fixture.fixture_id}: mandante"
        )
    if expect_away is not None:
        assert_same_team(
            away, expect_away, f"fixture {fixture.fixture_id}: visitante"
        )
    return home, away


# ----------------------------------------------------------------------
# Cache de identidade: SOMENTE associacoes validadas
# ----------------------------------------------------------------------
class ValidatedTeamsStore:
    """Armazena nome->ID apenas depois de validado contra a API.

    Nao e atalho de resolucao (a resolucao sempre passa pela API): e o
    registro auditavel das associacoes que passaram na validacao de
    ID + nome (+ competicao, quando aplicavel).
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or DB_PATH)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS validated_teams (
                    name_norm TEXT NOT NULL,
                    team_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    country TEXT,
                    validated_at TEXT NOT NULL,
                    PRIMARY KEY (name_norm, team_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def save(self, team: Team) -> None:
        now = datetime.now(dt_timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO validated_teams
                    (name_norm, team_id, name, country, validated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalize_name(team.name),
                    team.id,
                    team.name,
                    team.country,
                    now,
                ),
            )

    def get(self, name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name_norm, team_id, name, country, validated_at "
                "FROM validated_teams WHERE name_norm = ?",
                (normalize_name(name),),
            ).fetchone()
        if row is None:
            return None
        return {
            "name_norm": row[0],
            "team_id": row[1],
            "name": row[2],
            "country": row[3],
            "validated_at": row[4],
        }

    def all(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name_norm, team_id, name, country, validated_at "
                "FROM validated_teams ORDER BY name"
            ).fetchall()
        return [
            {
                "name_norm": r[0],
                "team_id": r[1],
                "name": r[2],
                "country": r[3],
                "validated_at": r[4],
            }
            for r in rows
        ]