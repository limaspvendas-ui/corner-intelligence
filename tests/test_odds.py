"""Testes da camada de ODDS (ETAPA 2.2) - sem rede.

Valida:
  1. os dois shapes reais da API (bookmakers pre-jogo; odds live);
  2. odds AO VIVO so do endpoint dedicado /odds/live (TTL 60s);
  3. resposta vazia => None => "ODD AO VIVO NAO DISPONIVEL NA FONTE."
     (nunca se usa odd pre-jogo como se fosse live);
  4. leitura de linha dos values (AH lado+linha; Over/Under);
  5. find_odd devolve exatamente a linha real do feed (sem invento).
"""

from src.api_client import APIFootballClient
from src.config import CACHE_TTL_LIVE
from src.odds import (
    BOOKMAKER_DESCONHECIDO,
    SEM_ODD_LIVE,
    _parse_response,
    fetch_live_odds,
    fetch_pregame_odds,
    find_odd,
    parse_ah_value,
    parse_total_value,
)

FID = 200


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, endpoint, params=None, use_cache=True, ttl=None):
        self.calls.append({"endpoint": endpoint, "params": dict(params or {}),
                           "use_cache": use_cache, "ttl": ttl})
        route = self.routes.get(endpoint)
        if route is None:
            return []
        if callable(route):
            return route(dict(params or {}))
        return route


# ----------------------------------------------------------------------
# 1. Shapes reais da resposta
# ----------------------------------------------------------------------
def test_parse_shape_pregame_com_casas():
    raw = {
        "update": "2026-09-06T15:00:00+00:00",
        "bookmakers": [
            {
                "name": "Bet365",
                "update": "2026-09-06T15:00:00+00:00",
                "bets": [
                    {
                        "name": "Asian Handicap",
                        "values": [
                            {"value": "Away -1.5", "odd": "1.10"},
                            {"value": "Home +1.5", "odd": "6.50"},
                        ],
                    },
                ],
            }
        ],
    }
    odds = _parse_response(FID, raw)
    assert odds.fixture_id == FID
    assert len(odds.bookmakers) == 1
    book = odds.bookmakers[0]
    assert book.name == "Bet365"
    market = book.find_market("asian handicap")
    assert market is not None
    assert market.values[0].value == "Away -1.5"
    assert market.values[0].odd == 1.10
    assert market.values[0].implied == 1 / 1.10


def test_parse_shape_live_agregado():
    """Shape ao vivo: response[0].odds = [{name, values}] (pode nao vir casa)."""
    raw = {
        "update": "2026-09-06T21:14:00+00:00",
        "odds": [
            {
                "name": "Corners Over/Under",
                "values": [
                    {"value": "Over 9.5", "odd": "1.85"},
                    {"value": "Under 9.5", "odd": "1.95"},
                ],
            },
        ],
    }
    odds = _parse_response(FID, raw)
    book = odds.bookmakers[0]
    assert book.name == BOOKMAKER_DESCONHECIDO
    assert book.update == "2026-09-06T21:14:00+00:00"
    market = book.find_market("corners")
    assert [v.value for v in market.values] == ["Over 9.5", "Under 9.5"]


def test_parse_sem_nada():
    odds = _parse_response(FID, {"update": ""})
    assert odds.bookmakers == []


# ----------------------------------------------------------------------
# 2 e 3. Odds live: endpoint dedicado, TTL curto, vazio => None
# ----------------------------------------------------------------------
def test_fetch_live_odds_endpoint_e_ttl():
    payload = [{"update": "2026-09-06T21:14:00+00:00",
                "odds": [{"name": "Goals Over/Under",
                          "values": [{"value": "Over 2.5", "odd": "1.75"}]}]}]
    client = FakeClient({"/odds/live": payload})

    odds = fetch_live_odds(client, FID, refresh=True)

    call = client.calls[0]
    assert call["endpoint"] == "/odds/live"
    assert call["params"] == {"fixture": FID}
    assert call["use_cache"] is False and call["ttl"] == CACHE_TTL_LIVE
    assert odds is not None
    assert odds.find_market("goals").values[0].odd == 1.75


def test_fetch_live_odds_vazio_retorna_none():
    """Sem odds live na fonte => None; o chamador informa SEM_ODD_LIVE e
    NUNCA usa odd pre-jogo como se fosse live."""
    client = FakeClient({"/odds/live": []})
    assert fetch_live_odds(client, FID) is None
    assert SEM_ODD_LIVE == "ODD AO VIVO NAO DISPONIVEL NA FONTE."


def test_fetch_pregame_odds_rotulo_de_origem():
    payload = [{"update": "2026-09-05T10:00:00+00:00",
                "bookmakers": [{"name": "Bet365", "bets": []}]}]
    client = FakeClient({"/odds": payload})
    odds = fetch_pregame_odds(client, FID)
    call = client.calls[0]
    assert call["endpoint"] == "/odds"
    assert odds.bookmakers[0].name == "Bet365"
    # update ANTERIOR ao jogo: preco de abertura, nunca "live"
    assert odds.update == "2026-09-05T10:00:00+00:00"


# ----------------------------------------------------------------------
# 4. Leitura de linhas (lado + linha validaveis ou NAO interpreta)
# ----------------------------------------------------------------------
def test_parse_ah_value():
    # CASO REAL DA AUDITORIA: "Away -1.5" => lado Away, feed -1.5
    assert parse_ah_value("Away -1.5") == ("Away", -1.5)
    assert parse_ah_value("Home +0.25") == ("Home", 0.25)
    assert parse_ah_value("home 0,75") == ("Home", 0.75)
    # sem lado+linha validaveis: NUNCA interpreta
    assert parse_ah_value("Over 2.5") is None
    assert parse_ah_value("Empate") is None
    assert parse_ah_value("") is None
    assert parse_ah_value("Away") is None


def test_parse_total_value():
    assert parse_total_value("Over 9.5") == ("Over", 9.5)
    assert parse_total_value("Under 2,5") == ("Under", 2.5)
    assert parse_total_value("under 10.5") == ("Under", 10.5)
    # formato desconhecido => None (sem invento)
    assert parse_total_value("Away -1.5") is None
    assert parse_total_value("Home/Away") is None


# ----------------------------------------------------------------------
# 5. find_odd: linha EXATA do feed
# ----------------------------------------------------------------------
def test_find_odd():
    raw = {
        "update": "2026-09-06T21:14:00+00:00",
        "odds": [
            {"name": "Corners Over/Under",
             "values": [{"value": "Over 9.5", "odd": "1.85"},
                        {"value": "Under 9.5", "odd": "1.95"}]},
            {"name": "Asian Handicap",
             "values": [{"value": "Away -1.5", "odd": "1.10"}]},
        ],
    }
    odds = _parse_response(FID, raw)
    ov = find_odd(odds, ("corners", "over"), "Over 9.5")
    assert ov is not None and ov.odd == 1.85
    ah = find_odd(odds, ("asian handicap",), "Away")
    assert ah is not None and ah.value == "Away -1.5"
    # linha inexistente: None (nunca aproximado)
    assert find_odd(odds, ("corners",), "Over 12.5") is None
    assert find_odd(None, ("corners",), "Over 9.5") is None


# ----------------------------------------------------------------------
# Integracao minima com o cliente real (cache desativado)
# ----------------------------------------------------------------------
def test_cliente_real_live_odds_vazio(monkeypatch):
    """Com a API devolvendo vazio, o cliente real entrega [] e a camada
    de odds devolve None (sem odd inventada)."""
    client = APIFootballClient(api_key="chave-de-teste", cache=None)

    class FakeHTTP:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self_inner):
            return {"errors": {}, "response": []}

    monkeypatch.setattr(
        client.session, "get",
        lambda url, params=None, timeout=None: FakeHTTP(),
    )
    assert fetch_live_odds(client, 987654) is None