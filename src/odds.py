"""ODDS da API-Football v3 - pre-jogo e AO VIVO (ETAPA 2.2).

REGRAS DE OURO desta camada (secao 6 da especificacao):
    - Odd AO VIVO so vem do endpoint dedicado /odds/live. A resposta
      traz o campo "update" (timestamp da coleta pela fonte).
    - ODD PRE-JOGO (/odds) NUNCA e apresentada como odd ao vivo.
      A funcao fetch_pregame_odds existe apenas para comparacao
      explicita, com rotulo claro de origem.
    - Sem odds ao vivo reais => o chamador informa
      "ODD AO VIVO NAO DISPONIVEL NA FONTE." e classifica a achega
      como OPORTUNIDADE ESTATISTICA. Nada e inventado.

Formatos aceitos (tolerante aos dois shapes reais da API):
    - pre-jogo:  response[0].bookmakers = [{name, update, bets:
                  [{name, values: [{value, odd}]}]}]
    - ao vivo:   response[0].odds = [{name, values: [{value, odd}]}]
      (feed live agrega a casa; o nome da casa pode nao vir - nesse
      caso exibimos "bookmaker nao informado pela fonte live").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.api_client import APIFootballClient
from src.config import CACHE_TTL_LIVE
from src.handicap import implied_prob

SEM_ODD_LIVE = "ODD AO VIVO NAO DISPONIVEL NA FONTE."
BOOKMAKER_DESCONHECIDO = "bookmaker nao informado pela fonte live"


@dataclass
class OddValue:
    """Uma linha de aposta com seu preco, EXATAMENTE como vem no feed."""

    value: str
    odd: float

    @property
    def implied(self) -> float:
        return implied_prob(self.odd)


@dataclass
class BetMarket:
    name: str  # nome real do mercado no feed (ex.: "Corners Over Under")
    id: int | None = None  # bet ID estavel do feed (ex.: 45 = Corners Over Under)
    values: list[OddValue] = field(default_factory=list)


@dataclass
class BookmakerOdds:
    name: str
    update: str
    markets: list[BetMarket] = field(default_factory=list)

    def find_market(self, *substrings: str) -> BetMarket | None:
        """Primeiro mercado cujo nome contem TODOS os substrings (case-insens.)."""
        wanted = [s.lower() for s in substrings]
        for market in self.markets:
            name = market.name.lower()
            if all(s in name for s in wanted):
                return market
        return None


@dataclass
class FixtureOdds:
    """Todas as odds de um fixture, agrupadas por casa."""

    fixture_id: int
    update: str
    bookmakers: list[BookmakerOdds] = field(default_factory=list)

    def find_market(self, *substrings: str) -> BetMarket | None:
        for book in self.bookmakers:
            market = book.find_market(*substrings)
            if market is not None:
                return market
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_values(raw_values: list[dict[str, Any]] | None) -> list[OddValue]:
    out: list[OddValue] = []
    for item in raw_values or []:
        odd = _to_float(item.get("odd"))
        if odd is None or odd <= 0:
            continue
        out.append(OddValue(value=str(item.get("value") or ""), odd=odd))
    return out


def _parse_bets(raw_bets: list[dict[str, Any]] | None) -> list[BetMarket]:
    return [
        BetMarket(
            name=str(bet.get("name") or ""),
            id=_to_int(bet.get("id")),
            values=_parse_values(bet.get("values")),
        )
        for bet in raw_bets or []
        if bet.get("name")
    ]


def _parse_response(fixture_id: int, raw: dict[str, Any]) -> FixtureOdds:
    """Aceita os dois shapes reais (bookmakers de pre-jogo; odds de live)."""
    update = str(raw.get("update") or "")
    bookmakers: list[BookmakerOdds] = []

    # Shape pre-jogo (com casas explicitas)
    for book in raw.get("bookmakers") or []:
        name = str(book.get("name") or "casa nao informada")
        book_update = str(book.get("update") or update)
        bookmakers.append(
            BookmakerOdds(
                name=name,
                update=book_update,
                markets=_parse_bets(book.get("bets")),
            )
        )

    # Shape ao vivo (mercados agregados, casa pode nao vir no payload)
    raw_odds = raw.get("odds")
    if raw_odds:
        book_name = str(
            (raw.get("bookmaker") or {}).get("name") or BOOKMAKER_DESCONHECIDO
        )
        markets = [
            BetMarket(
                name=str(bet.get("name") or ""),
                id=_to_int(bet.get("id")),
                values=_parse_values(bet.get("values")),
            )
            for bet in raw_odds
            if bet.get("name")
        ]
        bookmakers.append(
            BookmakerOdds(name=book_name, update=update, markets=markets)
        )

    return FixtureOdds(fixture_id=fixture_id, update=update, bookmakers=bookmakers)


def fetch_live_odds(
    client: APIFootballClient, fixture_id: int, refresh: bool = False
) -> FixtureOdds | None:
    """Odds REALMENTE ao vivo de um fixture (/odds/live; TTL 60s).

    Retorna None quando a fonte nao fornece odds live para a partida -
    nesse caso o chamador informa SEM_ODD_LIVE e NAO usa odd pre-jogo
    como se fosse live.
    """
    response = client.get(
        "/odds/live",
        params={"fixture": fixture_id},
        use_cache=not refresh,
        ttl=CACHE_TTL_LIVE,
    )
    if not response:
        return None
    return _parse_response(fixture_id, response[0])


def fetch_pregame_odds(
    client: APIFootballClient, fixture_id: int
) -> FixtureOdds | None:
    """Odds PRE-JOGO do fixture (endpoint /odds).

    ATENCAO (regra de ouro): estas odds sao de ABERTURA/pre-jogo.
    NUNCA apresentar como odd ao vivo - o timestamp de update e o
    proprio aviso de que o preco pode estar defasado. Uso permitido
    apenas para comparacao explicita, sempre com rotulo "pre-jogo".
    """
    response = client.get("/odds", params={"fixture": fixture_id})
    if not response:
        return None
    return _parse_response(fixture_id, response[0])


# ----------------------------------------------------------------------
# Leitura de linhas dos "value" do feed (validacao lado + linha)
# ----------------------------------------------------------------------
_RE_AH = re.compile(r"^(Home|Away)\s*([+-]?\d+(?:[.,]\d+)?)$", re.IGNORECASE)
_RE_TOTAL = re.compile(r"^(Over|Under)\s+(\d+(?:[.,]\d+)?)$", re.IGNORECASE)


def parse_ah_value(value: str) -> tuple[str, float] | None:
    """'Away -1.5' -> ('Away', -1.5) | 'Home +0.25' -> ('Home', 0.25).

    Retorna None quando o value nao tem o formato lado+linha: sem
    validacao de lado e linha, o handicap NAO e interpretado.
    """
    match = _RE_AH.match((value or "").strip())
    if not match:
        return None
    side = match.group(1).capitalize()
    line = float(match.group(2).replace(",", "."))
    return side, line


def parse_total_value(value: str) -> tuple[str, float] | None:
    """'Over 2.5' -> ('Over', 2.5) | 'Under 9.5' -> ('Under', 9.5)."""
    match = _RE_TOTAL.match((value or "").strip())
    if not match:
        return None
    direction = match.group(1).capitalize()
    line = float(match.group(2).replace(",", "."))
    return direction, line


def find_odd(
    odds: FixtureOdds, market_substrings: tuple[str, ...], value_substring: str
) -> OddValue | None:
    """Procura uma linha exata: mercado (substrings) + value (substring).

    Case-insensitive. Retorna a primeira achega REAL do feed - nada
    de aproximacao ou invento.

    ATENCAO (bug corrigido em 08/09/2026, auditado na varredura
    09/09/2026): casamento por SUBSTRING aceita mercados da mesma
    familia com periodo/escopo DIFERENTES (ex.: "Total Corners (1st
    Half)" casando com linha FT de total do jogo). Para associar odd
    a LINHA ANALISADA use obrigatoriamente casar_odd_ft - casamento
    EXATO de fixture + familia + periodo FT + escopo (total do jogo)
    + lado + linha. Esta funcao permanece apenas para mercados sem
    familia/periodo/escopo (ex.: Asian Handicap).
    """
    if odds is None:
        return None
    wanted_value = value_substring.lower()
    for book in odds.bookmakers:
        for market in book.markets:
            name = market.name.lower()
            if not all(s.lower() in name for s in market_substrings):
                continue
            for ov in market.values:
                if wanted_value in ov.value.lower():
                    return ov
    return None


# ----------------------------------------------------------------------
# CASAMENTO EXATO LINHA ANALISADA x ODD (regra obrigatoria, 08/09/2026)
#
# Uma odd so pode ser associada a uma linha analisada com casamento
# EXATO de: (1) fixture, (2) familia de mercado, (3) periodo (FT),
# (4) escopo (TOTAL DO JOGO), (5) lado Over/Under, (6) linha numerica.
#
# NUNCA aceitar como substituto: 1o/2o tempo, total do mandante ou do
# visitante, Home/Away Team Total, mercado de jogador, Yellow Cards
# isolado ou qualquer mercado apenas por conter palavra semelhante.
#
# IDs estaveis do feed (verificados em TODAS as respostas /odds em
# cache na auditoria de 09/09/2026 - um unico ID por nome canonico):
#     Goals Over/Under    = 5    Corners Over Under = 45
#     Cards Over/Under    = 80
# Nomes canonicos FT de TOTAL DO JOGO validados na mesma auditoria.
# ----------------------------------------------------------------------

BET_IDS_FT_EXATOS: dict[str, int] = {
    "gols": 5,
    "escanteios": 45,
    "cartoes": 80,
}

MERCADOS_FT_EXATOS: dict[str, str] = {
    "gols": "goals over/under",
    "escanteios": "corners over under",
    "cartoes": "cards over/under",
}

# marcadores de periodo/escopo/liquidacao: presentes no nome => o
# mercado NUNCA casa com linha FT de total do jogo, mesmo com ID certo
_MARCADORES_FORA_FT = (
    "1st", "2nd", "half", "home", "away", "team",
    "yellow", "player", "range", "between",
)

ODD_CARTOES_NAO_VALIDADA = (
    "ODD NAO VALIDADA - REGRA DE LIQUIDACAO NAO CONFIRMADA"
)


@dataclass
class OddCasada:
    """Odd casada EXATAMENTE com a linha analisada (FT, total do jogo).

    validada=False somente em cartoes: o feed nao informa a regra de
    liquidacao da casa (amarelo=1, vermelho=2), entao a odd existe no
    feed mas NAO pode ser usada como OPORTUNIDADE OPERACIONAL
    CONFIRMADA.
    """

    familia: str
    lado: str
    linha: float
    odd: float
    value_feed: str
    bookmaker: str
    mercado_feed: str
    update: str
    validada: bool
    motivo_nao_validada: str | None = None


def _normaliza_nome(nome: str) -> str:
    return re.sub(r"\s+", " ", (nome or "").strip().lower())


def mercado_ft_total(familia: str, market: BetMarket) -> bool:
    """TRUE somente para o mercado FT de TOTAL DO JOGO da familia.

    ID estavel do feed em primeiro lugar; nome canonico EXATO como
    fallback quando o feed nao traz ID. Nome com marcador de
    periodo/escopo (1o/2o tempo, mandante/visitante, equipe, jogador,
    amarelo isolado) rejeita o mercado mesmo com ID correto.
    """
    nome = _normaliza_nome(market.name)
    if any(m in nome for m in _MARCADORES_FORA_FT):
        return False
    if market.id is not None:
        return market.id == BET_IDS_FT_EXATOS.get(familia)
    return nome == MERCADOS_FT_EXATOS.get(familia)


def casar_odd_ft(
    odds: FixtureOdds | None,
    familia: str,
    lado: str,
    linha: float | str,
) -> OddCasada | None:
    """Casa a linha analisada com a odd REAL do mercado FT exato.

    Casamento EXATO: familia + mercado FT de TOTAL DO JOGO (ID estavel
    ou nome canonico) + lado Over/Under + linha numerica identica.
    Sem correspondencia exata => None ("ODD REAL NAO DISPONIVEL") -
    NUNCA se adapta a odd de outro mercado.

    Cartoes: a odd e encontrada mas devolvida com validada=False e
    motivo ODD_CARTOES_NAO_VALIDADA (regra de liquidacao da casa nao
    confirmada pelo feed).
    """
    if odds is None or familia not in MERCADOS_FT_EXATOS:
        return None
    try:
        linha_f = float(str(linha).replace(",", "."))
    except (TypeError, ValueError):
        return None
    lado_f = str(lado).strip().capitalize()
    for book in odds.bookmakers:
        for market in book.markets:
            if not mercado_ft_total(familia, market):
                continue
            for ov in market.values:
                parsed = parse_total_value(ov.value)
                if parsed is None or parsed != (lado_f, linha_f):
                    continue
                if familia == "cartoes":
                    return OddCasada(
                        familia=familia, lado=lado_f, linha=linha_f,
                        odd=ov.odd, value_feed=ov.value,
                        bookmaker=book.name, mercado_feed=market.name,
                        update=book.update or odds.update,
                        validada=False,
                        motivo_nao_validada=ODD_CARTOES_NAO_VALIDADA,
                    )
                return OddCasada(
                    familia=familia, lado=lado_f, linha=linha_f,
                    odd=ov.odd, value_feed=ov.value,
                    bookmaker=book.name, mercado_feed=market.name,
                    update=book.update or odds.update,
                    validada=True,
                )
    return None