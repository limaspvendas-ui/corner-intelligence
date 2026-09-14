"""Testes da ETAPA 2 - JOGOS AO VIVO (sem rede).

Usa respostas falsas no formato real da API-Football v3. Valida:
  1. listagem de jogos realmente ao vivo;
  2. exclusao de NS/FT e outros status nao-ao-vivo;
  3. identidade usando IDs do fixture (divergencia interrompe a coleta);
  4. leitura de estatisticas live (todas as reais; TTL curto);
  5. eventos ordenados cronologicamente (minuto nunca inventado);
  6. None nunca vira zero;
  7. atualizacao do mesmo fixture (diff desde a leitura anterior);
  8. cache live nao retorna estado antigo indevidamente;
  9. timezone America/Sao_Paulo em toda consulta;
 10. pre-jogo continua funcionando normalmente (comportamento intacto).
"""

import sqlite3
import time

import pytest

from src.config import DEFAULT_TIMEZONE
from src.exceptions import IdentityDivergenceError, UserFacingError
from src.live import (
    LIVE_IN_PLAY,
    LiveSnapshotStore,
    diff_snapshots,
    fetch_live_snapshot,
    find_live_fixture_id,
    list_live_games,
    update_live_snapshot,
)
from src.live_report import format_live_list, format_live_snapshot

FID = 200
HOME_ID, HOME = 131, "Corinthians"
AWAY_ID, AWAY = 132, "Chapecoense-sc"


# ----------------------------------------------------------------------
# Infra: cliente falso + builders no formato real da API
# ----------------------------------------------------------------------
class FakeLiveClient:
    """Client falso: devolve a resposta montada e registra as chamadas
    (endpoint, params, use_cache, ttl)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, endpoint, params=None, use_cache=True, ttl=None):
        self.calls.append(
            {
                "endpoint": endpoint,
                "params": dict(params or {}),
                "use_cache": use_cache,
                "ttl": ttl,
            }
        )
        route = self.routes.get(endpoint)
        if route is None:
            return []
        if callable(route):
            return route(dict(params or {}))
        return route


def _game(
    fid=FID,
    status="2H",
    elapsed=47,
    gh=1,
    ga=1,
    ht_home=0,
    ht_away=1,
    date="2026-09-06T19:30:00-03:00",
):
    return {
        "fixture": {
            "id": fid,
            "date": date,
            "status": {"short": status, "elapsed": elapsed},
            "venue": {"name": "Neo Quimica Arena", "city": "Sao Paulo"},
        },
        "league": {
            "id": 71,
            "name": "Serie A",
            "country": "Brazil",
            "round": "Regular Season - 26",
            "season": 2026,
        },
        "teams": {
            "home": {"id": HOME_ID, "name": HOME},
            "away": {"id": AWAY_ID, "name": AWAY},
        },
        "goals": {"home": gh, "away": ga},
        "score": {
            "halftime": {"home": ht_home, "away": ht_away},
            "fulltime": {"home": None, "away": None},
        },
    }


def _stat(stat_type, value):
    return {"type": stat_type, "value": value}


def _block(team_id, name, stats, stats_1h=None, stats_2h=None):
    block = {"team": {"id": team_id, "name": name}, "statistics": stats}
    if stats_1h is not None:
        block["statistics_1h"] = stats_1h
    if stats_2h is not None:
        block["statistics_2h"] = stats_2h
    return block


def _event(elapsed, extra, etype, detail, team_id, player):
    return {
        "time": {"elapsed": elapsed, "extra": extra},
        "team": {"id": team_id, "name": HOME if team_id == HOME_ID else AWAY},
        "player": {"id": 9, "name": player},
        "assist": {"id": None, "name": None},
        "type": etype,
        "detail": detail,
        "comments": None,
    }


def _teams_route(names):
    def route(params):
        tid = params.get("id")
        if tid in names:
            return [{"team": {"id": tid, "name": names[tid],
                              "country": "Brazil"}}]
        return []

    return route


VALID_TEAMS = {HOME_ID: HOME, AWAY_ID: AWAY}


def _snapshot_routes(stats=None, events=None, teams=None):
    return {
        "/fixtures": lambda p: [_game()],
        "/teams": _teams_route(teams or VALID_TEAMS),
        "/fixtures/statistics": stats or [],
        "/fixtures/events": events or [],
    }


# ----------------------------------------------------------------------
# 1 e 2. Listagem: apenas jogos realmente ao vivo
# ----------------------------------------------------------------------
def test_1_listagem_somente_jogos_ao_vivo():
    ao_vivo = [
        _game(101, "2H", 47, 1, 1),
        _game(103, "1H", 12, 0, 0),
        _game(105, "HT", 45, 2, 0),
    ]
    nao_ao_vivo = [
        _game(201, "NS", None, None, None),
        _game(202, "FT", 90, 2, 1),
        _game(203, "PST", None, None, None),
    ]
    client = FakeLiveClient({"/fixtures": ao_vivo + nao_ao_vivo})

    games = list_live_games(client)

    assert [g.fixture_id for g in games] == [101, 103, 105]
    g = games[0]
    assert g.league_name == "Serie A"
    assert g.country == "Brazil"
    assert g.season == 2026
    assert g.round == "Regular Season - 26"
    assert g.home_team_id == HOME_ID and g.home_team_name == HOME
    assert g.away_team_id == AWAY_ID and g.away_team_name == AWAY
    assert g.goals_home == 1 and g.goals_away == 1
    assert g.status == "2H" and g.elapsed == 47
    assert g.date_local.endswith("-03:00")
    # consulta real: live=all + timezone + TTL curto
    call = client.calls[0]
    assert call["params"]["live"] == "all"
    assert call["params"]["timezone"] == DEFAULT_TIMEZONE
    assert call["ttl"] == 60


def test_2_exclui_ns_ft_e_outros_status():
    """NS, FT, AET, PEN, PST, CANC, ABD nunca aparecem como ao vivo."""
    statuses = ["NS", "FT", "AET", "PEN", "PST", "CANC", "ABD"]
    games = [_game(300 + i, st, 90, 1, 1) for i, st in enumerate(statuses)]
    client = FakeLiveClient({"/fixtures": games})

    result = list_live_games(client)

    assert result == []
    # e o relatorio informa SEM inventar fixture
    text = format_live_list(result)
    assert "Nenhum jogo ao vivo" in text
    # status em andamento reconhecidos (equivalentes reais da API)
    assert {"1H", "HT", "2H", "ET", "BT", "P"} <= LIVE_IN_PLAY


def test_2b_lista_vazia_sem_inventar_fixture():
    client = FakeLiveClient({"/fixtures": []})
    assert list_live_games(client) == []
    assert "Nenhum jogo ao vivo" in format_live_list([])


# ----------------------------------------------------------------------
# 3. Identidade: IDs do fixture + validacao (divergencia interrompe)
# ----------------------------------------------------------------------
def test_3_identidade_usa_ids_do_fixture_e_interrompe_na_divergencia(
    monkeypatch, tmp_path
):
    # banco de identidade isolado (nunca suja o DB real)
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    # /teams diz que o ID 131 e o REMO: divergencia com o fixture
    teams_errados = {HOME_ID: "Remo", AWAY_ID: AWAY}
    coletados = {
        "/fixtures/statistics": [
            _block(HOME_ID, HOME, [_stat("Corner Kicks", 5)]),
            _block(AWAY_ID, AWAY, [_stat("Corner Kicks", 7)]),
        ],
        "/fixtures/events": [_event(20, None, "Card", "Yellow Card",
                                    AWAY_ID, "Fulano")],
    }
    client = FakeLiveClient({
        "/fixtures": lambda p: [_game()],
        "/teams": _teams_route(teams_errados),
        "/fixtures/statistics": coletados["/fixtures/statistics"],
        "/fixtures/events": coletados["/fixtures/events"],
    })

    with pytest.raises(IdentityDivergenceError):
        fetch_live_snapshot(client, FID)

    # coleta INTERROMPIDA: estatisticas e eventos nunca foram consultados
    endpoints = [c["endpoint"] for c in client.calls]
    assert "/fixtures/statistics" not in endpoints
    assert "/fixtures/events" not in endpoints
    # a validacao usou exatamente os IDs do PROPRIO fixture
    team_ids = [
        c["params"]["id"] for c in client.calls if c["endpoint"] == "/teams"
    ]
    assert team_ids  # validou ANTES de qualquer estatistica
    assert set(team_ids) <= {HOME_ID, AWAY_ID}


def test_3b_identidade_valida_deixa_coleta_seguir(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    client = FakeLiveClient(_snapshot_routes(
        stats=[
            _block(HOME_ID, HOME, [_stat("Corner Kicks", 5)]),
            _block(AWAY_ID, AWAY, [_stat("Corner Kicks", 7)]),
        ],
        events=[_event(10, None, "Card", "Yellow Card", AWAY_ID, "Beltrano")],
    ))

    snapshot = fetch_live_snapshot(client, FID)

    assert snapshot.home_team_name == HOME
    assert snapshot.stats_home["Corner Kicks"] == 5


# ----------------------------------------------------------------------
# 4. Estatisticas live: TODAS as reais, TTL curto, por tempo real
# ----------------------------------------------------------------------
def test_4_leitura_de_estatisticas_live(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    stats = [
        _block(
            HOME_ID, HOME,
            [
                _stat("Corner Kicks", 5),
                _stat("Total Shots", 9),
                _stat("Shots on Goal", 3),
                _stat("Ball Possession", "58%"),
                _stat("Yellow Cards", 1),
                _stat("Fouls", 8),
                _stat("Goalkeeper Saves", 4),
                _stat("Total passes", 300),
                _stat("Passes accurate", 240),
                _stat("Passes %", "80%"),
                _stat("Estatistica Inedita da Fonte", 12),  # outra real
                _stat("Offsides", None),  # valor ausente: NAO entra
            ],
            stats_1h=[_stat("Corner Kicks", 2)],
            stats_2h=[_stat("Corner Kicks", 3)],
        ),
        _block(
            AWAY_ID, AWAY,
            [
                _stat("Corner Kicks", 7),
                _stat("Total Shots", 11),
                _stat("Shots on Goal", 4),
                _stat("Ball Possession", "42%"),
            ],
            stats_1h=[_stat("Corner Kicks", 4)],
            stats_2h=[_stat("Corner Kicks", 3)],
        ),
    ]
    client = FakeLiveClient(_snapshot_routes(stats=stats))

    snapshot = fetch_live_snapshot(client, FID)

    assert snapshot.stats_home["Corner Kicks"] == 5
    assert snapshot.stats_away["Corner Kicks"] == 7
    assert snapshot.stats_home["Passes %"] == "80%"
    assert snapshot.stats_away["Ball Possession"] == "42%"
    assert snapshot.has_stats is True
    # por tempo: SOMENTE blocos reais da API
    assert snapshot.stats_1h[HOME_ID]["Corner Kicks"] == 2
    assert snapshot.stats_1h[AWAY_ID]["Corner Kicks"] == 4
    assert snapshot.stats_2h[HOME_ID]["Corner Kicks"] == 3
    # valor ausente NAO entrou (None nao vira zero)
    assert "Offsides" not in snapshot.stats_home
    # placar do intervalo real
    assert snapshot.halftime_home == 0 and snapshot.halftime_away == 1
    # TTL curto e half=true em toda leitura live
    stats_call = next(
        c for c in client.calls if c["endpoint"] == "/fixtures/statistics"
    )
    assert stats_call["params"] == {"fixture": FID, "half": "true"}
    assert stats_call["ttl"] == 60
    # relatorio mostra a estatistica "outra real" com o nome da fonte
    text = format_live_snapshot(snapshot)
    assert "Estatistica Inedita da Fonte" in text
    assert "[CALCULO]" in text and "[FATO]" in text
    assert "INTERPRETACAO" not in text and "INTERPRETAÇÃO" not in text
    assert "total de escanteios: 12" in text
    assert "total de finalizacoes: 20" in text
    assert "diferenca de posse: +16 p.p." in text


def test_4b_sem_estatisticas_na_fonte(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    client = FakeLiveClient(_snapshot_routes(stats=[]))

    snapshot = fetch_live_snapshot(client, FID)

    assert snapshot.has_stats is False
    text = format_live_snapshot(snapshot)
    assert "dado nao disponivel na fonte" in text
    # calculo nao inventa nada sem dado
    assert "total de escanteios" not in text


# ----------------------------------------------------------------------
# 5. Eventos: ordem cronologica real; minuto nunca inventado
# ----------------------------------------------------------------------
def test_5_eventos_ordenados_cronologicamente(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    events = [
        _event(67, None, "Card", "Yellow Card", HOME_ID, "Cicrano"),
        _event(12, None, "Goal", "Normal Goal", AWAY_ID, "Fulano"),
        _event(45, 1, "Card", "Yellow Card", AWAY_ID, "Beltrano"),
        _event(None, None, "subst", "Substitution 1", HOME_ID, "Sicrano"),
    ]
    client = FakeLiveClient(_snapshot_routes(events=events))

    snapshot = fetch_live_snapshot(client, FID)

    elapsed_list = [ev["time"]["elapsed"] for ev in snapshot.events]
    # 12' -> 45+1 -> 67' -> minuto ausente por ultimo (nunca inventado)
    assert elapsed_list == [12, 45, 67, None]
    assert snapshot.events[1]["time"]["extra"] == 1
    text = format_live_snapshot(snapshot)
    assert "minuto nao informado na fonte" in text
    # eventos consultados pelo fixture com TTL curto (o endpoint nao
    # aceita timezone: o minuto vem na propria resposta)
    events_call = next(
        c for c in client.calls if c["endpoint"] == "/fixtures/events"
    )
    assert events_call["params"] == {"fixture": FID}
    assert events_call["ttl"] == 60


# ----------------------------------------------------------------------
# 6. None nunca vira zero
# ----------------------------------------------------------------------
def test_6_none_nao_vira_zero(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    stats = [
        _block(HOME_ID, HOME, [_stat("Fouls", 8)]),   # SEM escanteios
        _block(AWAY_ID, AWAY, [_stat("Fouls", 10)]),
    ]
    client = FakeLiveClient(_snapshot_routes(stats=stats))

    snapshot = fetch_live_snapshot(client, FID)

    # campo ausente fica ausente (nunca 0)
    assert snapshot.stats_home.get("Corner Kicks") is None
    assert snapshot.stats_away.get("Corner Kicks") is None
    text = format_live_snapshot(snapshot)
    assert "Escanteios: nd - nd" in text
    # calculo de total de escanteios NAO e feito sem dado
    assert "total de escanteios" not in text
    # sem blocos por tempo: mensagem exata, sem reconstruir nada
    assert "DADO POR TEMPO NAO DISPONIVEL NA API." in text


# ----------------------------------------------------------------------
# 7. Atualizacao do mesmo fixture: refresh forcado + diff real
# ----------------------------------------------------------------------
def test_7_atualizacao_do_mesmo_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    monkeypatch.setattr("src.live.DB_PATH", str(tmp_path / "live.db"))

    state = {"elapsed": 42, "corners_home": 3, "extra_event": False}

    def fixtures_route(params):
        return [_game(elapsed=state["elapsed"])]

    def stats_route(params):
        blocks = [
            _block(HOME_ID, HOME,
                   [_stat("Corner Kicks", state["corners_home"]),
                    _stat("Shots on Goal", 2),
                    _stat("Ball Possession", "55%")]),
            _block(AWAY_ID, AWAY,
                   [_stat("Corner Kicks", 2),
                    _stat("Shots on Goal", 1),
                    _stat("Ball Possession", "45%")]),
        ]
        if state["extra_event"]:
            blocks[1]["statistics"].append(_stat("Yellow Cards", 1))
        return blocks

    def events_route(params):
        events = [_event(20, None, "Card", "Yellow Card", HOME_ID, "Fulano")]
        if state["extra_event"]:
            events.append(
                _event(45, 1, "Card", "Yellow Card", AWAY_ID, "Beltrano")
            )
        return events

    client = FakeLiveClient({
        "/fixtures": fixtures_route,
        "/teams": _teams_route(VALID_TEAMS),
        "/fixtures/statistics": stats_route,
        "/fixtures/events": events_route,
    })

    # leitura 1 (grava como ultima leitura)
    first = fetch_live_snapshot(client, FID, record=True)
    assert first.elapsed == 42

    # o jogo avanca: minuto, escanteios e cartao novo aos 45+1
    state["elapsed"] = 47
    state["corners_home"] = 4
    state["extra_event"] = True

    new, old, changes = update_live_snapshot(client, FID)

    assert old is not None and old.elapsed == 42
    assert new.elapsed == 47
    joined = "\n".join(changes)
    assert "minuto: 42 -> 47" in joined
    assert "escanteios: 3 - 2 -> 4 - 2" in joined
    assert "finalizacoes no alvo: 2 - 1 -> 2 - 1" not in joined
    assert "novo evento" in joined
    assert "45+1" in joined
    # o refresh forcado a API (sem cache) nas consultas do proprio jogo
    # (fixture, estatisticas e eventos); /teams segue cacheado como antes
    game_calls = [
        c for c in client.calls
        if c["endpoint"] in ("/fixtures", "/fixtures/statistics",
                            "/fixtures/events")
    ]
    assert len(game_calls) == 6  # 3 da leitura 1 + 3 da atualizacao
    assert all(c["use_cache"] is False for c in game_calls[3:])
    # e a leitura nova ficou gravada como ultima
    assert LiveSnapshotStore().get(FID).elapsed == 47


def test_7b_primeira_atualizacao_sem_leitura_anterior(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    monkeypatch.setattr("src.live.DB_PATH", str(tmp_path / "live.db"))
    client = FakeLiveClient(_snapshot_routes())

    _new, old, changes = update_live_snapshot(client, FID)

    assert old is None
    assert changes == ["primeira leitura gravada deste jogo"]


def test_7c_nada_mudou(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    monkeypatch.setattr("src.live.DB_PATH", str(tmp_path / "live.db"))
    client = FakeLiveClient(_snapshot_routes(
        stats=[
            _block(HOME_ID, HOME, [_stat("Corner Kicks", 5)]),
            _block(AWAY_ID, AWAY, [_stat("Corner Kicks", 7)]),
        ],
    ))
    fetch_live_snapshot(client, FID, record=True)

    _new, _old, changes = update_live_snapshot(client, FID)

    assert changes == ["nada mudou desde a leitura anterior"]


# ----------------------------------------------------------------------
# 8. Cache live: nunca serve estado antigo indevidamente
# ----------------------------------------------------------------------
def test_8_cache_live_expira_e_nao_retorna_estado_antigo(tmp_path):
    from src.cache import Cache

    cache = Cache(db_path=str(tmp_path / "cache.db"))
    payload = [{"minuto": 42}]

    cache.set("/fixtures/statistics", {"fixture": FID}, payload, ttl=60)
    # dentro do TTL live: serve a leitura recente (nao gasta requisicao)
    assert cache.get("/fixtures/statistics", {"fixture": FID}, ttl=60) == payload

    # envelhece alem do TTL live: NAO pode servir estado antigo
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE api_cache SET created_at = ?", (time.time() - 61,)
        )
    assert cache.get("/fixtures/statistics", {"fixture": FID}, ttl=60) is None
    # mesmo SEM ttl explicito na leitura, a entrada gravada com TTL curto
    # continua curta: nunca e tratada como historico de 7 dias
    assert cache.get("/fixtures/statistics", {"fixture": FID}) is None


def test_8b_refresh_sobrescreve_o_estado_do_cache(tmp_path, monkeypatch):
    from src.api_client import APIFootballClient
    from src.cache import Cache

    cache = Cache(db_path=str(tmp_path / "cache.db"))
    client = APIFootballClient(api_key="chave-de-teste", cache=cache)

    class FakeHTTP:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self_inner):
            return {"errors": {}, "response": self_inner.payload}

    fake = FakeHTTP()
    monkeypatch.setattr(
        client.session, "get", lambda url, params=None, timeout=None: fake
    )

    # leitura 1 (cacheado): minuto 42
    fake.payload = [{"status": {"short": "2H", "elapsed": 42}}]
    r1 = client.get("/fixtures", params={"id": FID, "timezone": DEFAULT_TIMEZONE},
                    ttl=60)
    assert r1[0]["status"]["elapsed"] == 42

    # refresh pedido pelo usuario: ignora o cache, busca o minuto 47 e
    # SOBRESCREVE o estado antigo gravado
    fake.payload = [{"status": {"short": "2H", "elapsed": 47}}]
    r2 = client.get("/fixtures", params={"id": FID, "timezone": DEFAULT_TIMEZONE},
                    use_cache=False, ttl=60)
    assert r2[0]["status"]["elapsed"] == 47
    # a proxima leitura (cacheada) ve o estado NOVO, nunca o antigo
    cached = cache.get("/fixtures", {"id": FID, "timezone": DEFAULT_TIMEZONE},
                       ttl=60)
    assert cached[0]["status"]["elapsed"] == 47


# ----------------------------------------------------------------------
# 9. Timezone America/Sao_Paulo em toda consulta
# ----------------------------------------------------------------------
def test_9_timezone_america_sao_paulo(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    client = FakeLiveClient(_snapshot_routes(
        stats=[
            _block(HOME_ID, HOME, [_stat("Corner Kicks", 5)]),
            _block(AWAY_ID, AWAY, [_stat("Corner Kicks", 7)]),
        ],
    ))

    snapshot = fetch_live_snapshot(client, FID)

    # toda consulta com data (fixtures) usa America/Sao_Paulo;
    # eventos trazem apenas o minuto (endpoint sem timezone)
    for call in client.calls:
        if call["endpoint"] == "/fixtures":
            assert call["params"]["timezone"] == DEFAULT_TIMEZONE
    # data do fixture ja retorna no fuso local
    assert snapshot.date_local.endswith("-03:00")
    # hora da leitura registrada
    assert snapshot.collected_at


def test_9b_find_por_id_rejeita_jogo_que_nao_esta_ao_vivo():
    client = FakeLiveClient({
        "/fixtures": lambda p: [_game(status="NS", elapsed=None,
                                      gh=None, ga=None)],
    })
    with pytest.raises(UserFacingError) as exc:
        find_live_fixture_id(client, str(FID))
    assert "NS" in str(exc.value)

    client = FakeLiveClient({
        "/fixtures": lambda p: [_game(status="FT", elapsed=90, gh=2, ga=1)],
    })
    with pytest.raises(UserFacingError) as exc:
        find_live_fixture_id(client, str(FID))
    assert "encerrado" in str(exc.value)

    ao_vivo = FakeLiveClient({"/fixtures": lambda p: [_game()]})
    assert find_live_fixture_id(ao_vivo, str(FID)) == FID


# ----------------------------------------------------------------------
# 10. Pre-jogo continua funcionando normalmente
# ----------------------------------------------------------------------
def test_10_prejogo_continua_intacto(tmp_path):
    """Comportamento historico preservado: TTLs antigos, roundtrip sem
    ttl explicito, migracao idempotente e fetch_match_stats inalterado."""
    from src.cache import Cache, _ttl_for
    from src.config import CACHE_TTL
    from src.fixtures import Fixture
    from src.match_stats import fetch_match_stats

    # TTLs do pre-jogo seguem os mesmos valores
    assert _ttl_for("/fixtures/statistics", {}) > 86400  # 7 dias
    assert _ttl_for("/fixtures", {}) == CACHE_TTL["/fixtures"]
    assert _ttl_for("/fixtures", {"live": "all"}) == 60

    # cache historico (sem ttl explicito): roundtrip e migracao 2x init
    cache = Cache(db_path=str(tmp_path / "cache.db"))
    cache_again = Cache(db_path=str(tmp_path / "cache.db"))
    payload = [{"id": 1, "name": "Flamengo"}]
    cache.set("/teams", {"search": "fla"}, payload)  # sem ttl: como antes
    assert cache_again.get("/teams", {"search": "fla"}) == payload

    # fetch_match_stats (pre-jogo) pede exatamente como antes
    fixture = Fixture(
        fixture_id=1492363, date="2026-09-06T13:00:00-03:00", status="FT",
        elapsed=90, league_id=71, league_name="Serie A",
        round="Regular Season - 26", season=2026,
        home_team_id=HOME_ID, home_team_name=HOME,
        away_team_id=AWAY_ID, away_team_name=AWAY,
        goals_home=1, goals_away=2,
    )
    fake = FakeLiveClient({
        "/fixtures/statistics": [
            _block(HOME_ID, HOME, [_stat("Corner Kicks", 5)]),
            _block(AWAY_ID, AWAY, [_stat("Corner Kicks", 7)]),
        ],
    })
    match = fetch_match_stats(fake, fixture)
    assert fake.calls[0]["params"] == {"fixture": 1492363, "half": "true"}
    assert match.home.corners == 5 and match.away.corners == 7