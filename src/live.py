"""ETAPA 2 - JOGOS AO VIVO (modulo novo; nada do pre-jogo muda aqui).

Regras herdadas da plataforma (mesmas do pre-jogo):
    - IDENTIDADE: IDs de mandante/visitante vem OBRIGATORIAMENTE do
      proprio fixture e sao validados (ID + nome) ANTES de coletar
      estatisticas. Divergencia => IdentityDivergenceError e a coleta
      daquele jogo e INTERROMPIDA (src.identity.fixture_teams).
    - DADOS AUSENTES: None nunca vira zero; campo ausente na API fica
      ausente no relatorio ("dado nao disponivel na fonte").
    - POR TEMPO (1oT/2oT): apenas quando a API retorna os blocos reais
      statistics_1h/statistics_2h (half=true). Nunca dividir total por 2
      nem estimar. Sem blocos => "DADO POR TEMPO NAO DISPONIVEL NA API."
    - CACHE AO VIVO: TTL curto (CACHE_TTL_LIVE = 60s) passado
      explicitamente em toda leitura live. "Atualizar" forca refresh
      (use_cache=False) e o estado novo sobrescreve o cache - leitura
      seguinte nunca recebe estado antigo.
    - SAIDA: apenas [FATO] e [CALCULO]. Nenhuma interpretacao, projecao
      ou recomendacao de aposta nesta etapa.

Atualizacao ("Atualize o jogo"): o snapshot da ultima leitura fica
gravado em SQLite (tabela live_snapshots); a atualizacao busca estado
novo na API e relata o que mudou desde a leitura anterior.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from src.api_client import APIFootballClient
from src.config import CACHE_TTL_LIVE, DATA_DIR, DB_PATH, DEFAULT_TIMEZONE
from src.exceptions import UserFacingError
from src.fixtures import LIVE_STATUS, parse_fixture
from src.identity import fixture_teams
from src.logging_config import get_logger

# Status realmente equivalentes a "jogo em andamento" na API-Football v3:
# 1o tempo, intervalo, 2o tempo, prorrogacao, intervalo da prorrogacao,
# disputa de penaltis em andamento, live generico, interrompido e
# suspenso. NAO entram: NS, FT, AET, PEN (decidido nos penaltis), PST,
# CANC, ABD, AWD/SUSP-definitivo, etc.
LIVE_IN_PLAY = LIVE_STATUS | {"SUSP"}

# Categorias exibidas na ordem do relatorio (nome local -> tipo real da API)
STAT_DISPLAY: tuple[tuple[str, str], ...] = (
    ("Escanteios", "Corner Kicks"),
    ("Finalizacoes (total)", "Total Shots"),
    ("No alvo", "Shots on Goal"),
    ("Fora do alvo", "Shots off Goal"),
    ("Bloqueadas", "Blocked Shots"),
    ("De dentro da area", "Shots insidebox"),
    ("De fora da area", "Shots outsidebox"),
    ("Posse de bola", "Ball Possession"),
    ("Gols esperados (xG)", "Expected Goals"),
    ("Cartoes amarelos", "Yellow Cards"),
    ("Cartoes vermelhos", "Red Cards"),
    ("Faltas", "Fouls"),
    ("Impedimentos", "Offsides"),
    ("Defesas do goleiro", "Goalkeeper Saves"),
    ("Passes (total)", "Total passes"),
    ("Passes certos", "Passes accurate"),
    ("Precisao de passes", "Passes %"),
)
_KNOWN_STAT_TYPES = {api_type for _, api_type in STAT_DISPLAY}


def now_brt() -> datetime:
    """Hora local America/Sao_Paulo (fallback UTC-3 sem tzdata)."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    except Exception:  # Windows sem pacote tzdata: offset fixo UTC-3
        return datetime.now(dt_timezone(timedelta(hours=-3)))


# ----------------------------------------------------------------------
# 1. Listagem de jogos ao vivo
# ----------------------------------------------------------------------
@dataclass
class LiveGame:
    """Um jogo em andamento (dados de identificacao vindos do fixture)."""

    fixture_id: int
    league_name: str
    country: str
    season: int | None
    round: str
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    goals_home: int | None
    goals_away: int | None
    status: str
    elapsed: int | None
    date_local: str  # ja em America/Sao_Paulo (timezone na consulta)


def _parse_live_game(raw: dict[str, Any]) -> LiveGame:
    return LiveGame(
        fixture_id=raw["fixture"]["id"],
        league_name=raw["league"]["name"],
        country=raw["league"].get("country") or "",
        season=raw["league"].get("season"),
        round=raw["league"].get("round") or "",
        home_team_id=raw["teams"]["home"]["id"],
        home_team_name=raw["teams"]["home"]["name"],
        away_team_id=raw["teams"]["away"]["id"],
        away_team_name=raw["teams"]["away"]["name"],
        goals_home=raw["goals"]["home"],
        goals_away=raw["goals"]["away"],
        status=raw["fixture"]["status"]["short"],
        elapsed=raw["fixture"]["status"].get("elapsed"),
        date_local=raw["fixture"]["date"],
    )


def list_live_games(client: APIFootballClient) -> list[LiveGame]:
    """Todos os jogos REALMENTE em andamento agora (filtro duplo:
    consulta live=all da API + checagem do status real de cada fixture)."""
    response = client.get(
        "/fixtures",
        params={"live": "all", "timezone": DEFAULT_TIMEZONE},
        ttl=CACHE_TTL_LIVE,
    )
    return [
        _parse_live_game(raw)
        for raw in response
        if raw["fixture"]["status"]["short"] in LIVE_IN_PLAY
    ]


# ----------------------------------------------------------------------
# 2. Snapshot completo de um fixture ao vivo
# ----------------------------------------------------------------------
@dataclass
class LiveSnapshot:
    """Estado completo de um jogo ao vivo em um instante (uma leitura).

    stats_home/stats_away: TODAS as estatisticas reais que a API retornou
    para a partida (tipo exato da API -> valor bruto; valor ausente NAO
    entra, nunca vira zero). stats_1h/stats_2h: por tempo, SOMENTE quando
    a API fornece os blocos reais (half=true), chaveadas por team_id.
    """

    fixture_id: int
    league_name: str
    country: str
    season: int | None
    round: str
    date_local: str
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    goals_home: int | None
    goals_away: int | None
    halftime_home: int | None
    halftime_away: int | None
    status: str
    elapsed: int | None
    # id real da competicao (ETAPA 2.2: benchmark da liga vem do fixture)
    league_id: int | None = None
    stats_home: dict[str, Any] = field(default_factory=dict)
    stats_away: dict[str, Any] = field(default_factory=dict)
    stats_1h: dict[int, dict[str, Any]] = field(default_factory=dict)
    stats_2h: dict[int, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    collected_at: str = ""  # hora local da coleta
    has_stats: bool = False

    # ---------------- serializacao (store de atualizacao) -----------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LiveSnapshot:
        return cls(**data)


def _parse_stat_block(statistics: list[dict[str, Any]] | None) -> dict[str, Any]:
    """TODAS as estatisticas reais do bloco (tipo -> valor bruto).

    Valor None/ausente nao entra no dicionario: campo inexistente nao
    vira zero. Nomes de tipos ficam EXATAMENTE como a API fornece.
    """
    out: dict[str, Any] = {}
    for stat in statistics or []:
        stat_type = stat.get("type")
        if stat_type is None:
            continue
        value = stat.get("value")
        if value is None:
            continue
        out[stat_type] = value
    return out


def _sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordena cronologicamente pelo tempo REAL da API.

    time.elapsed/time.extra da propria API; minuto ausente vai para o
    fim (nunca se inventa minuto).
    """

    def key(ev: dict[str, Any]) -> tuple[int, int, int]:
        elapsed = (ev.get("time") or {}).get("elapsed")
        extra = (ev.get("time") or {}).get("extra") or 0
        if elapsed is None:
            return (1, 0, 0)
        return (0, elapsed, extra)

    return sorted(events, key=key)


def _live_fixture(
    client: APIFootballClient, fixture_id: int, refresh: bool
) -> dict[str, Any]:
    """Fixture atual (TTL live; refresh forcado quando pedido)."""
    response = client.get(
        "/fixtures",
        params={"id": fixture_id, "timezone": DEFAULT_TIMEZONE},
        use_cache=not refresh,
        ttl=CACHE_TTL_LIVE,
    )
    if not response:
        raise UserFacingError(
            f"Nenhuma partida encontrada na API-Football com o ID {fixture_id}."
        )
    return response[0]


def _not_live_message(raw: dict[str, Any]) -> UserFacingError:
    status = raw["fixture"]["status"]["short"]
    home = raw["teams"]["home"]["name"]
    away = raw["teams"]["away"]["name"]
    goals = raw["goals"]
    if status in {"FT", "AET", "PEN"}:
        return UserFacingError(
            f"{home} x {away} nao esta mais ao vivo: status real {status} "
            f"(encerrado). Placar final {goals['home']}-{goals['away']}. "
            "Use comandos de pos-jogo para essa partida."
        )
    return UserFacingError(
        f"{home} x {away} nao esta ao vivo agora: status real {status}. "
        "Use 'aovivos' para ver os jogos em andamento."
    )


def fetch_live_snapshot(
    client: APIFootballClient,
    fixture_id: int,
    refresh: bool = False,
    record: bool = False,
) -> LiveSnapshot:
    """Estado COMPLETO de um jogo ao vivo: fixture + estatisticas +
    eventos, em uma leitura.

    refresh=True => consulta forcada na API (ignora cache) e grava o
    estado novo no cache (nunca serve estado antigo). refresh=False =>
    aceita cache live valido (ate 60s).
    record=True => grava esta leitura como a ultima leitura do fixture
    (base do "o que mudou" na proxima atualizacao).

    IDENTIDADE: os IDs das equipes vem do PROPRIO fixture e sao
    validados (ID + nome, round-trip /teams) ANTES de coletar
    estatisticas. Divergencia interrompe a coleta daquele jogo.
    """
    raw = _live_fixture(client, fixture_id, refresh)
    status = raw["fixture"]["status"]["short"]
    if status not in LIVE_IN_PLAY:
        raise _not_live_message(raw)

    fixture = parse_fixture(raw)

    # IDENTIDADE OBRIGATORIA: IDs do fixture + validacao (interrompe se divergir)
    fixture_teams(client, fixture)

    # Estatisticas: TODAS as reais (jogo completo e, quando a API fornece,
    # os blocos por tempo). TTL live; refresh forcado quando pedido.
    stats_response = client.get(
        "/fixtures/statistics",
        params={"fixture": fixture_id, "half": "true"},
        use_cache=not refresh,
        ttl=CACHE_TTL_LIVE,
    )
    stats_home: dict[str, Any] = {}
    stats_away: dict[str, Any] = {}
    stats_1h: dict[int, dict[str, Any]] = {}
    stats_2h: dict[int, dict[str, Any]] = {}
    for block in stats_response or []:
        team = block.get("team") or {}
        team_id = team.get("id")
        if team_id is None:
            continue
        side = (
            stats_home if team_id == fixture.home_team_id
            else stats_away if team_id == fixture.away_team_id
            else None
        )
        if side is None:
            continue
        side.update(_parse_stat_block(block.get("statistics")))
        if block.get("statistics_1h"):
            stats_1h[team_id] = _parse_stat_block(block["statistics_1h"])
        if block.get("statistics_2h"):
            stats_2h[team_id] = _parse_stat_block(block["statistics_2h"])

    # Eventos (gols, cartoes, substituicoes, VAR): ordem cronologica real.
    # O endpoint aceita apenas o fixture (nao aceita timezone; traz o
    # minuto do evento na propria resposta).
    events_response = client.get(
        "/fixtures/events",
        params={"fixture": fixture_id},
        use_cache=not refresh,
        ttl=CACHE_TTL_LIVE,
    )
    events = _sort_events(events_response or [])

    scores = raw.get("score") or {}
    halftime = scores.get("halftime") or {}

    snapshot = LiveSnapshot(
        fixture_id=fixture_id,
        league_name=raw["league"]["name"],
        country=raw["league"].get("country") or "",
        season=raw["league"].get("season"),
        round=raw["league"].get("round") or "",
        date_local=raw["fixture"]["date"],
        home_team_id=fixture.home_team_id,
        home_team_name=fixture.home_team_name,
        away_team_id=fixture.away_team_id,
        away_team_name=fixture.away_team_name,
        goals_home=raw["goals"]["home"],
        goals_away=raw["goals"]["away"],
        halftime_home=halftime.get("home"),
        halftime_away=halftime.get("away"),
        status=status,
        elapsed=raw["fixture"]["status"].get("elapsed"),
        league_id=raw["league"].get("id"),
        stats_home=stats_home,
        stats_away=stats_away,
        stats_1h=stats_1h,
        stats_2h=stats_2h,
        events=events,
        collected_at=now_brt().strftime("%d/%m/%Y %H:%M:%S"),
        has_stats=bool(stats_home or stats_away),
    )
    if record:
        LiveSnapshotStore().save(snapshot)
    return snapshot


# ----------------------------------------------------------------------
# 3. Atualizacao: ultima leitura gravada + comparacao
# ----------------------------------------------------------------------
class LiveSnapshotStore:
    """Grava o snapshot da ultima leitura de cada fixture (SQLite).

    Permite que 'Atualize o jogo' compare o estado novo com a leitura
    anterior ENTRE execucoes do CLI (processos separados).
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or DB_PATH)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_snapshots (
                    fixture_id INTEGER PRIMARY KEY,
                    snapshot TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def get(self, fixture_id: int) -> LiveSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot FROM live_snapshots WHERE fixture_id = ?",
                (fixture_id,),
            ).fetchone()
        if row is None:
            return None
        return LiveSnapshot.from_dict(json.loads(row[0]))

    def _raw_row(
        self, fixture_id: int
    ) -> tuple[int, str, float] | None:
        """Linha bruta (fixture_id, snapshot_json, updated_at_epoch) do
        cache do ultimo snapshot, ou None. Expoe o epoch REAL da coleta
        (updated_at) para que a serie temporal (src/live_pressure) possa
        incorpora-lo como primeiro registro historico SEM INVENTAR
        timestamp. Nao altera o comportamento de get/save."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fixture_id, snapshot, updated_at "
                "FROM live_snapshots WHERE fixture_id = ?",
                (fixture_id,),
            ).fetchone()
        if row is None:
            return None
        return int(row[0]), str(row[1]), float(row[2])

    def save(self, snapshot: LiveSnapshot) -> None:
        import time

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO live_snapshots
                    (fixture_id, snapshot, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    snapshot.fixture_id,
                    json.dumps(snapshot.to_dict(), ensure_ascii=False),
                    time.time(),
                ),
            )


def _fmt_side(value_home: Any, value_away: Any) -> str:
    """Par 'X - Y'; lado ausente aparece como 'nd' (nunca zero)."""
    def one(v: Any) -> str:
        return "nd" if v is None else str(v)

    return f"{one(value_home)} - {one(value_away)}"


def _event_key(event: dict[str, Any]) -> str:
    """Chave de identificacao de um evento (para achar os NOVOS)."""
    return json.dumps(
        {
            "elapsed": (event.get("time") or {}).get("elapsed"),
            "extra": (event.get("time") or {}).get("extra"),
            "type": event.get("type"),
            "detail": event.get("detail"),
            "team": (event.get("team") or {}).get("id"),
            "player": (event.get("player") or {}).get("name"),
        },
        sort_keys=True,
    )


def diff_snapshots(old: LiveSnapshot, new: LiveSnapshot) -> list[str]:
    """O que mudou entre a leitura anterior e a atual (dados reais).

    Compara apenas campos existentes nas duas leituras; valores ausentes
    aparecem como 'nd' (dado nao disponivel), nunca como zero.
    """
    changes: list[str] = []

    if old.elapsed != new.elapsed:
        changes.append(
            f"minuto: {old.elapsed if old.elapsed is not None else 'nd'} -> "
            f"{new.elapsed if new.elapsed is not None else 'nd'}"
        )
    if (old.goals_home, old.goals_away) != (new.goals_home, new.goals_away):
        changes.append(
            f"placar: {_fmt_side(old.goals_home, old.goals_away)} -> "
            f"{_fmt_side(new.goals_home, new.goals_away)}"
        )

    stat_pairs = (
        ("escanteios", "Corner Kicks"),
        ("finalizacoes", "Total Shots"),
        ("finalizacoes no alvo", "Shots on Goal"),
        ("cartoes amarelos", "Yellow Cards"),
        ("cartoes vermelhos", "Red Cards"),
        ("faltas", "Fouls"),
    )
    for label, stat_type in stat_pairs:
        old_pair = _fmt_side(old.stats_home.get(stat_type),
                             old.stats_away.get(stat_type))
        new_pair = _fmt_side(new.stats_home.get(stat_type),
                             new.stats_away.get(stat_type))
        if old_pair != new_pair:
            changes.append(f"{label}: {old_pair} -> {new_pair}")

    old_pos = _fmt_side(old.stats_home.get("Ball Possession"),
                        old.stats_away.get("Ball Possession"))
    new_pos = _fmt_side(new.stats_home.get("Ball Possession"),
                        new.stats_away.get("Ball Possession"))
    if old_pos != new_pos:
        changes.append(f"posse: {old_pos} -> {new_pos}")

    # eventos novos desde a leitura anterior
    old_keys = {_event_key(ev) for ev in old.events}
    for ev in new.events:
        if _event_key(ev) not in old_keys:
            changes.append(f"novo evento: {_describe_event(ev)}")

    if not changes:
        changes.append("nada mudou desde a leitura anterior")
    return changes


def _describe_event(event: dict[str, Any]) -> str:
    """Descricao objetiva de um evento (campos reais da API; nada inventado)."""
    time = event.get("time") or {}
    elapsed = time.get("elapsed")
    extra = time.get("extra")
    if elapsed is None:
        minute = "minuto nao informado na fonte"
    else:
        minute = f"{elapsed}+{extra}'" if extra else f"{elapsed}'"
    team = (event.get("team") or {}).get("name") or "?"
    etype = event.get("type") or "?"
    detail = event.get("detail") or ""
    player = (event.get("player") or {}).get("name") or "jogador nao informado"
    return f"{etype} ({detail}) - {player} - {team} - aos {minute}"


def update_live_snapshot(
    client: APIFootballClient, fixture_id: int
) -> tuple[LiveSnapshot, LiveSnapshot | None, list[str]]:
    """Atualiza o MESMO fixture sob comando do usuario.

    Sempre consulta a API (refresh forcado), informa a hora da
    atualizacao e retorna (snapshot_novo, snapshot_anterior, mudancas).
    Nao existe atualizacao automatica continua: roda apenas quando
    solicitado.
    """
    store = LiveSnapshotStore()
    old = store.get(fixture_id)
    new = fetch_live_snapshot(client, fixture_id, refresh=True)
    changes = diff_snapshots(old, new) if old is not None else [
        "primeira leitura gravada deste jogo"
    ]
    store.save(new)
    if old is None:
        get_logger().info("Primeira leitura live do fixture %s", fixture_id)
    return new, old, changes


# ----------------------------------------------------------------------
# 4. Localizar um fixture ao vivo a partir do que o usuario pediu
# ----------------------------------------------------------------------
def find_live_fixture_id(client: APIFootballClient, spec: str) -> int:
    """'ID da partida' ou 'Time A x Time B' -> fixture_id de um jogo
    em andamento. Os IDs das equipes vem do fixture; resolucao por nome
    usa a validacao de identidade padrao da plataforma.
    """
    spec = spec.strip()
    if spec.isdigit():
        # valida o status real do fixture (sem aceitar cache antigo)
        raw = client.get(
            "/fixtures",
            params={"id": int(spec), "timezone": DEFAULT_TIMEZONE},
            ttl=CACHE_TTL_LIVE,
        )
        if not raw:
            raise UserFacingError(
                f"Nenhuma partida encontrada na API-Football com o ID {spec}."
            )
        if raw[0]["fixture"]["status"]["short"] not in LIVE_IN_PLAY:
            raise _not_live_message(raw[0])
        return int(spec)

    from src.identity import resolve_match_teams_validated

    team_a, team_b = resolve_match_teams_validated(
        client, spec, context="localizar jogo ao vivo"
    )
    live = list_live_games(client)
    matches = [
        g for g in live if g.home_team_id in (team_a.id, team_b.id)
        and g.away_team_id in (team_a.id, team_b.id)
    ]
    if not matches:
        raise UserFacingError(
            "Nenhum jogo ao vivo encontrado agora entre "
            f"{team_a.name} e {team_b.name} "
            f"({len(live)} jogos em andamento no total)."
        )
    if len(matches) > 1:
        listed = "; ".join(
            f"{g.fixture_id}: {g.home_team_name} x {g.away_team_name} "
            f"({g.league_name})"
            for g in matches
        )
        raise UserFacingError(
            "Mais de um jogo ao vivo entre esses times agora: " + listed
        )
    return matches[0].fixture_id