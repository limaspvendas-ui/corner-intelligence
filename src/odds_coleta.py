"""ETAPA 5F -- COLETA PROSPECTIVA DE ODDS REAIS.

Infraestrutura de coleta e persistencia de odds reais (cotacoes factuais de
bookmaker), SEPARADA do motor. NAO altera probabilidade, aprovacao, thresholds,
settlement, recomendacao ou qualquer regra do motor. NAO calcula ROI.

PRINCIPIOS (FASE 2 / 5 / 6 / 7 / 8):
    - ODD REAL = cotacao factual de bookmaker/API, com bookmaker, mercado, linha
      e timestamp identificaveis. ODD JUSTA = 1/probabilidade do modelo.
      ODD JUSTA NUNCA preenche odd_real. Nenhum bookmaker e fabricado. Nenhuma
      odd de mercado ausente e inferida.
    - Append-only: odds_snapshot_history NUNCA sobrescreve uma odd antiga.
      Cada cotacao observada vira um snapshot temporal imutavel.
    - Anti-leakage: cada odd traz timestamp factual (update_feed) e timestamp
      de coleta (collected_at). Futuro matching so podera usar uma odd se
      timestamp_da_odd <= momento_da_decisao. Nunca odd pos-jogo como entrada.
    - As 288.362 previsoes antigas NAO sao alteradas e NAO recebem odd_real
      retroativa. Este modulo apenas COLETA e ARMAZENA fatos para o futuro.
    - Mercado irreconhecivel => familia 'UNMAPPED' (nunca casamento
      aproximado). Odd invalida (ausente/nao numerica/<=0) => registrada com
      motivo, nunca convertida em zero.

Uso:
    python -m src.app oddscoleta status
    python -m src.app oddscoleta cache [--dry-run]
    python -m src.app oddscoleta coleta <fixture_id> [--live] [--intervalo N] [--max-iter N]
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.config import DB_PATH
from src.odds import (
    BET_IDS_FT_EXATOS,
    BOOKMAKER_DESCONHECIDO,
    _MARCADORES_FORA_FT,
    _to_float,
    _to_int,
    parse_ah_value,
    parse_total_value,
)

# Marcadores de periodo/escopo: presentes no nome => o mercado NAO e o FT de
# total do jogo (reuso da regra de src/odds.py para classificacao EXATA).
_MARCADORES = _MARCADORES_FORA_FT

# Nomes canonicos da familia RESULTADO (FT, total do jogo / resultado final).
_RESULTADO_NOMES = {
    "match winner": ("resultado", "1X2"),
    "double chance": ("resultado", "DC"),
    "asian handicap": ("resultado", "AH"),
    "draw no bet": ("resultado", "DNB"),
}

# Status de cada snapshot armazenado.
ST_OK = "OK"
ST_SUSPENDED = "SUSPENDED"      # live: cotacao existe mas mercado suspenso
ST_UNMAPPED = "UNMAPPED"        # mercado nao reconhecido (familia UNMAPPED)
ST_INVALID = "INVALID"          # value/odd ausente, nao numerica ou <= 0

COLETA_PRE = "pre_match"
COLETA_LIVE = "live"

# Provider canonico historico (unico integrado quando os registros legados
# foram produzidos -- confirmado por auditoria Etapa 5F-B).
PROVIDER_API_FOOTBALL = "api_football"

# ----------------------------------------------------------------------
# Etapa 5F-E2: providers de odds multiprovider (5Dollar + The Odds API).
# Diferem do OddsProvider (Protocol .get) -- expoem fetch_odds -> NormalizedOdd.
# Registry centralizado em odds_coleta (camada de coleta), NAO no motor.
# ----------------------------------------------------------------------
PROVIDER_FIVE_DOLLAR = "five_dollar_football"
PROVIDER_THE_ODDS_API = "the_odds_api"

# Fases observadas no feed 5Dollar (opening/closing/inplay). The Odds API NAO
# classifica fase -> phase=NULL (nunca inventada). Histórico api_football=NULL.
PHASE_OPENING = "OPENING"
PHASE_CLOSING = "CLOSING"
PHASE_INPLAY = "INPLAY"

# Mercados canonicos (Seção 5 da 5F-E2). market_canonical preserva a familia
# sem perder o mercado original (coluna market_original).
MK_MATCH_RESULT = "MATCH_RESULT"
MK_TOTAL_GOALS = "TOTAL_GOALS"
MK_ASIAN_HANDICAP = "ASIAN_HANDICAP"
MK_TOTAL_CORNERS = "TOTAL_CORNERS"
MK_ASIAN_CORNERS = "ASIAN_CORNERS"
MK_TOTAL_CARDS = "TOTAL_CARDS"
MK_ASIAN_CARDS = "ASIAN_CARDS"
MK_UNKNOWN = "UNKNOWN"

# Mapa mercado-original 5Dollar -> (familia, subfamilia, market_canonical).
# familias validas no schema: gols|escanteios|cartoes|resultado|UNMAPPED.
# btts fica A CONFIRMAR => UNKNOWN (sem invencao), conforme Seção 3 da 5F-E2.
_5DOLLAR_MARKET_MAP: dict[str, tuple[str, str | None, str]] = {
    "corner":       ("escanteios", None,      MK_TOTAL_CORNERS),
    "corner_asian": ("escanteios", None,      MK_ASIAN_CORNERS),
    "goalline":     ("gols",       None,      MK_TOTAL_GOALS),
    "cards":        ("cartoes",    None,      MK_TOTAL_CARDS),
    "cards_asian":  ("cartoes",    None,      MK_ASIAN_CARDS),
    "asian":        ("resultado",  "AH",      MK_ASIAN_HANDICAP),
    "1x2":          ("resultado",  "1X2",     MK_MATCH_RESULT),
    "btts":         ("UNMAPPED",   None,      MK_UNKNOWN),
}

# Mapa The Odds API -> (familia, subfamilia, market_canonical).
# Apenas h2h/totals/spreads (Seção 3); demais => UNKNOWN.
_THEODDS_MARKET_MAP: dict[str, tuple[str, str | None, str]] = {
    "h2h":    ("resultado", "1X2", MK_MATCH_RESULT),
    "totals": ("gols",      None,  MK_TOTAL_GOALS),
    "spreads":("resultado", "AH",  MK_ASIAN_HANDICAP),
}

# Fase 5Dollar: submarket vem como "<mk_key>/<phase_key>" (parse em _phase_5dollar).
_5DOLLAR_PHASE_MAP: dict[str, str] = {
    "opening": PHASE_OPENING,
    "close":   PHASE_CLOSING,
    "closing": PHASE_CLOSING,
    "in_play": PHASE_INPLAY,
    "inplay":  PHASE_INPLAY,
    "live":    PHASE_INPLAY,
}


# ----------------------------------------------------------------------
# Abstracao de Provider (Etapa 5F-C)
# ----------------------------------------------------------------------
# O coletor nao assume mais diretamente que qualquer cliente sempre e
# API-Football. Cada provider tem identidade explicita (provider_name) que
# acompanha os dados ate o armazenamento (coluna `provider`).
#
# PROVIDER  = API que forneceu os dados (api_football, the_odds_api, ...).
# BOOKMAKER = casa de apostas representada dentro do feed (bet365, ...).
# Conceitos distintos -- nunca confundir.

from typing import Protocol


class OddsProvider(Protocol):
    """Interface de um provider de odds. Identidade explicita via provider_name."""

    provider_name: str

    def get(self, endpoint: str, params: dict | None = None, **kw) -> list:
        ...


class APIFootballOddsProvider:
    """Adapter do provider API-Football / API-Sports.

    Reusa src/api_client.py (nao reescreve cliente estavel). Preserva chamadas,
    cache e comportamento existentes. Apenas acrescenta proveniencia explicita.
    """

    provider_name = PROVIDER_API_FOOTBALL

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, endpoint: str, params: dict | None = None, **kw) -> list:
        return self._client.get(endpoint, params=params, **kw)


# Registry: nome canonico -> classe adapter. Nesta etapa SOMENTE api_football.
# Nao criar adapters falsos para APIs ainda nao implementadas.
_PROVIDER_REGISTRY: dict[str, type] = {
    PROVIDER_API_FOOTBALL: APIFootballOddsProvider,
}


def resolve_provider(obj: Any) -> OddsProvider:
    """Resolve um objeto para um OddsProvider com provider_name explicito.

    - Se `obj` ja expoe `provider_name` (e um OddsProvider): valida contra o
      registry e retorna como-is. Provider desconhecido => ValueError explicito.
    - Se `obj` e um client legado (sem `provider_name`): envolve em
      APIFootballOddsProvider. Justificativa auditada: o unico client que
      existia antes da Etapa 5F-C era API-Football. Nao e inferencia silenciosa
      de um provider novo -- e o camado de compatibilidade do provider historico.
    """
    name = getattr(obj, "provider_name", None)
    if name:
        if name not in _PROVIDER_REGISTRY:
            raise ValueError(f"provider desconhecido no registry: {name!r}")
        return obj  # type: ignore[return-value]
    return APIFootballOddsProvider(obj)


# ----------------------------------------------------------------------
# Hash V2 de identidade de snapshot (Etapa 5F-C: inclui provider)
# ----------------------------------------------------------------------
def _hash_identidade(
    provider: str,
    fixture_id: int,
    coleta_tipo: str,
    bookmaker: str,
    bet_name: str,
    bet_id: int | None,
    familia: str,
    subfamilia: str | None,
    lado: str | None,
    linha: float | None,
    value_feed: str,
    odd: float | None,
    suspended: bool | int | None,
    status: str,
) -> str:
    """SHA-256 da identidade factual + provider (V2, Etapa 5F-C).

    Mesmo provider + mesmo snapshot factual => mesma hash (dedup).
    Provider diferente + mesmo bookmaker/fixture/mercado/linha/odd => hash
    diferente => ambos preservados (sem colisao multi-fonte). 1.90 -> 1.89 =>
    novo snapshot. `provider` entra PRIMEIRO (a fonte precede os fatos).

    V2 NAO inclui phase/market_canonical/market_original -- esses campos sao
    NULL para todo o histórico api_football e para novas coletas api_football.
    Registros multiprovider (5Dollar/The Odds API) usam _hash_identidade_v3.
    Histórico jamais e re-migrado ( hashes V2 armazenados permanecem )."""
    payload = json.dumps(
        [
            provider,
            fixture_id, coleta_tipo, bookmaker, bet_name, bet_id,
            familia, subfamilia, lado, linha, value_feed,
            None if odd is None else round(odd, 6),
            None if suspended is None else int(suspended),
            status,
        ],
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_identidade_v3(
    provider: str,
    fixture_id: int,
    coleta_tipo: str,
    bookmaker: str,
    bet_name: str,
    bet_id: int | None,
    familia: str,
    subfamilia: str | None,
    lado: str | None,
    linha: float | None,
    value_feed: str,
    odd: float | None,
    suspended: bool | int | None,
    status: str,
    phase: str | None,
    market_canonical: str | None,
    market_original: str | None,
) -> str:
    """SHA-256 V3 (Etapa 5F-E2): V2 + phase + market_canonical + market_original.

    Usada APENAS por registros multiprovider (5Dollar/The Odds API), cujos
    campos phase/market_canonical/market_original sao populados. Distintas
    fases (OPENING/CLOSING/INPLAY) do mesmo mercado/linha/odd => hashes
    distintas => todas preservadas (append-only). api_football permanece V2."""
    payload = json.dumps(
        [
            provider,
            fixture_id, coleta_tipo, bookmaker, bet_name, bet_id,
            familia, subfamilia, lado, linha, value_feed,
            None if odd is None else round(odd, 6),
            None if suspended is None else int(suspended),
            status,
            phase, market_canonical, market_original,
        ],
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_snapshot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,             -- proveniencia da FONTE (ex.: 'api_football')
    fixture_id INTEGER NOT NULL,
    coleta_tipo TEXT NOT NULL,          -- 'pre_match' | 'live'
    bookmaker TEXT NOT NULL,
    bet_name TEXT NOT NULL,
    bet_id INTEGER,
    familia TEXT NOT NULL,              -- 'gols'|'escanteios'|'cartoes'|'resultado'|'UNMAPPED'
    subfamilia TEXT,                    -- '1X2'|'DC'|'AH'|'DNB'|NULL
    lado TEXT,                          -- 'Over'|'Under'|'Home'|'Draw'|'Away'|NULL
    linha REAL,                         -- linha numerica quando aplicavel
    value_feed TEXT NOT NULL,           -- value exato do feed (ex.: "Over 2.5")
    odd REAL,                           -- cotacao factual; NULL em INVALID
    suspended INTEGER,                  -- 0/1/NULL (apenas live)
    update_feed TEXT,                   -- timestamp da odd na fonte (response[i].update)
    collected_at REAL NOT NULL,         -- timestamp da coleta (time.time)
    fixture_date TEXT,                  -- kickoff (ISO) quando conhecido
    e_pre_jogo INTEGER,                 -- 1 se coleta <= kickoff (anti-leakage); 0 pos; NULL desconhecido
    status TEXT NOT NULL,               -- OK|SUSPENDED|UNMAPPED|INVALID
    motivo TEXT,                        -- motivo quando status != OK
    snapshot_hash TEXT NOT NULL,        -- HASH V2: inclui provider (Etapa 5F-C)
    UNIQUE (snapshot_hash)              -- dedup: cotacao identica + mesmo provider => mesma hash
);
CREATE INDEX IF NOT EXISTS idx_odds_hist_fixture
    ON odds_snapshot_history (fixture_id, coleta_tipo);
CREATE INDEX IF NOT EXISTS idx_odds_hist_familia
    ON odds_snapshot_history (familia, status);
"""


def _normaliza_nome(nome: str) -> str:
    import re
    return re.sub(r"\s+", " ", (nome or "").strip().lower())


# ----------------------------------------------------------------------
# Classificacao EXATA de mercado (FASE 4: sem casamento aproximado)
# ----------------------------------------------------------------------
def classificar_mercado(bet_name: str, bet_id: int | None) -> tuple[str, str | None]:
    """(familia, subfamilia). NUNCA aproxima: irreconhecivel => ('UNMAPPED', None).

    Ordem: ID estavel do feed (5/45/80) SEM marcador de periodo/escopo; depois
    nome canonico exato da familia resultado. Nome com marcador (1st/2nd/half/
    home/away/team/yellow/player/range/between) rejeita totais mesmo com ID certo.
    """
    nome = _normaliza_nome(bet_name)
    tem_marcador = any(m in nome for m in _MARCADORES)
    if bet_id is not None and not tem_marcador:
        for fam, bid in BET_IDS_FT_EXATOS.items():
            if bet_id == bid:
                return fam, None
    if not tem_marcador and nome in _RESULTADO_NOMES:
        return _RESULTADO_NOMES[nome]
    return "UNMAPPED", None


def extrair_lado_linha(
    familia: str, subfamilia: str | None, value_feed: str
) -> tuple[str | None, float | None]:
    """(lado, linha) a partir do value do feed, conforme a familia."""
    v = (value_feed or "").strip()
    if familia in ("gols", "escanteios", "cartoes"):
        parsed = parse_total_value(v)
        if parsed is not None:
            return parsed[0], parsed[1]
        return None, None
    if familia == "resultado":
        if subfamilia == "1X2":
            if v in ("Home", "Draw", "Away"):
                return v, None
            return None, None
        if subfamilia == "DC":
            return (v or None), None
        if subfamilia == "AH":
            parsed = parse_ah_value(v)
            if parsed is not None:
                return parsed[0], parsed[1]
            return None, None
        if subfamilia == "DNB":
            if v in ("Home", "Away"):
                return v, None
            return None, None
    return None, None


# ----------------------------------------------------------------------
# Anti-leakage: e_pre_jogo (coleta <= kickoff?)
# ----------------------------------------------------------------------
def _epoch_de_iso(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        from datetime import datetime
        # suporta Z e +00:00
        ds = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(ds).timestamp()
    except (TypeError, ValueError):
        return None


def _e_pre_jogo(collected_at: float, fixture_date_iso: str | None) -> int | None:
    kickoff = _epoch_de_iso(fixture_date_iso)
    if kickoff is None:
        return None
    return 1 if collected_at <= kickoff else 0


# ----------------------------------------------------------------------
# Snapshot unitario + dedup hash (FASE 11)
# ----------------------------------------------------------------------
@dataclass
class OddSnapshot:
    """Uma cotacao factual unitaria, pronta para append."""

    provider: str            # proveniencia da FONTE (ex.: 'api_football')
    fixture_id: int
    coleta_tipo: str
    bookmaker: str
    bet_name: str
    bet_id: int | None
    familia: str
    subfamilia: str | None
    lado: str | None
    linha: float | None
    value_feed: str
    odd: float | None
    suspended: bool | None
    update_feed: str
    collected_at: float
    fixture_date: str | None
    status: str
    motivo: str | None
    # Etapa 5F-E2: multiprovider prospective. NULL para histórico api_football
    # e para novas coletas api_football (permanecem hash V2).
    phase: str | None = None               # OPENING|CLOSING|INPLAY (5Dollar)
    market_canonical: str | None = None    # TOTAL_CORNERS|...|UNKNOWN
    market_original: str | None = None     # mercado cru do feed (preservado)

    def hash(self) -> str:
        """Hash de identidade+cotacao. Dispatch de versao:

        - V3 (com phase/market_canonical/market_original) quando algum desses
          campos esta populado => registros multiprovider (5Dollar/The Odds API).
          Distintas fases/mercados canonicos => hashes distintas => preservados.
        - V2 (sem os novos campos) caso contrario => api_football (histórico +
          novas coletas).Compativel com os 317.951 hashes V2 ja armazenados.
        """
        if (self.phase is not None or self.market_canonical is not None
                or self.market_original is not None):
            return _hash_identidade_v3(
                self.provider, self.fixture_id, self.coleta_tipo,
                self.bookmaker, self.bet_name, self.bet_id, self.familia,
                self.subfamilia, self.lado, self.linha, self.value_feed,
                self.odd, self.suspended, self.status,
                self.phase, self.market_canonical, self.market_original,
            )
        return _hash_identidade(
            self.provider, self.fixture_id, self.coleta_tipo, self.bookmaker,
            self.bet_name, self.bet_id, self.familia, self.subfamilia,
            self.lado, self.linha, self.value_feed, self.odd,
            self.suspended, self.status,
        )


def _construir_snapshots(
    fixture_id: int,
    coleta_tipo: str,
    raw_entry: dict[str, Any],
    *,
    collected_at: float,
    fixture_date: str | None,
    provider: str = PROVIDER_API_FOOTBALL,
) -> list[OddSnapshot]:
    """Constrói snapshots a partir de UMA entry da resposta (pre ou live).

    `provider` identifica a FONTE dos dados (default api_football para
    compatibilidade do camada historica; adapters passam explicitamente)."""
    update_feed = str(raw_entry.get("update") or "")
    pre = coleta_tipo == COLETA_PRE
    out: list[OddSnapshot] = []

    # Shape pre-jogo: bookmakers[].bets[].values[]
    if pre:
        for book in raw_entry.get("bookmakers") or []:
            bm_name = str(book.get("name") or "casa nao informada")
            for bet in book.get("bets") or []:
                bet_name = str(bet.get("name") or "")
                if not bet_name:
                    continue
                bet_id = _to_int(bet.get("id"))
                familia, sub = classificar_mercado(bet_name, bet_id)
                for v in bet.get("values") or []:
                    out.append(
                        _unit(fixture_id, coleta_tipo, bm_name, bet_name,
                              bet_id, familia, sub, v, update_feed,
                              collected_at, fixture_date, suspended=None,
                              provider=provider)
                    )
        return out

    # Shape live: odds[].values[] (casa agregada; pode nao vir bookmaker)
    book_name = str(
        (raw_entry.get("bookmaker") or {}).get("name") or BOOKMAKER_DESCONHECIDO
    )
    for bet in raw_entry.get("odds") or []:
        bet_name = str(bet.get("name") or "")
        if not bet_name:
            continue
        bet_id = _to_int(bet.get("id"))
        familia, sub = classificar_mercado(bet_name, bet_id)
        for v in bet.get("values") or []:
            suspended = bool(v.get("suspended")) if "suspended" in v else None
            out.append(
                _unit(fixture_id, coleta_tipo, book_name, bet_name, bet_id,
                      familia, sub, v, update_feed, collected_at,
                      fixture_date, suspended=suspended, provider=provider)
            )
    return out


def _unit(
    fixture_id: int, coleta_tipo: str, bm_name: str, bet_name: str,
    bet_id: int | None, familia: str, sub: str | None, v: dict[str, Any],
    update_feed: str, collected_at: float, fixture_date: str | None,
    *, suspended: bool | None, provider: str = PROVIDER_API_FOOTBALL,
) -> OddSnapshot:
    value_feed = str(v.get("value") or "")
    odd = _to_float(v.get("odd"))
    # Validacao (FASE 12): invalido => registrada com motivo, nunca zero.
    if not value_feed:
        status, motivo, odd_v = ST_INVALID, "value ausente no feed", None
    elif odd is None:
        status, motivo, odd_v = ST_INVALID, "odd ausente ou nao numerica", None
    elif odd <= 0:
        status, motivo, odd_v = ST_INVALID, "odd nao positiva", None
    elif suspended is True:
        status, motivo, odd_v = ST_SUSPENDED, "mercado suspenso (live)", odd
    elif familia == "UNMAPPED":
        status, motivo, odd_v = ST_UNMAPPED, "mercado nao mapeado", odd
    else:
        status, motivo, odd_v = ST_OK, None, odd

    lado, linha = (None, None)
    if familia != "UNMAPPED":
        lado, linha = extrair_lado_linha(familia, sub, value_feed)

    return OddSnapshot(
        provider=provider,
        fixture_id=fixture_id, coleta_tipo=coleta_tipo, bookmaker=bm_name,
        bet_name=bet_name, bet_id=bet_id, familia=familia, subfamilia=sub,
        lado=lado, linha=linha, value_feed=value_feed, odd=odd_v,
        suspended=suspended, update_feed=update_feed, collected_at=collected_at,
        fixture_date=fixture_date, status=status, motivo=motivo,
    )


# ----------------------------------------------------------------------
# Etapa 5F-E2: classificacao + construcao multiprovider (5Dollar/The Odds API)
# ----------------------------------------------------------------------
def _classificar_5dollar(market: str) -> tuple[str, str | None, str]:
    """(familia, subfamilia, market_canonical) para mercado 5Dollar.

    Reuso do vocabulario de familias do schema (gols/escanteios/cartoes/
    resultado/UNMAPPED). Mercado nao mapeado com seguranca => (UNMAPPED, None,
    UNKNOWN) -- nunca inventa familia/canonical."""
    return _5DOLLAR_MARKET_MAP.get(
        (market or "").strip().lower(),
        ("UNMAPPED", None, MK_UNKNOWN),
    )


def _classificar_theodds(market: str) -> tuple[str, str | None, str]:
    """(familia, subfamilia, market_canonical) para mercado The Odds API.

    So h2h/totals/spreads (Seção 3 da 5F-E2). Demais (h2h_lay,
    alternate_totals, ...) => UNKNOWN sem invencao."""
    return _THEODDS_MARKET_MAP.get(
        (market or "").strip().lower(),
        ("UNMAPPED", None, MK_UNKNOWN),
    )


def _phase_5dollar(submarket: str | None) -> str | None:
    """Extrai fase OPENING/CLOSING/INPLAY do submarket 5Dollar.

    submarket vem como "<mk_key>/<phase_key>" (ex.: 'corner/opening'). Se o
    provider nao classificou fase explicitamente => None (nunca inventada,
    Seção 6 da 5F-E2)."""
    if not submarket:
        return None
    # pega a parte apos a barra
    ph = submarket.rsplit("/", 1)[-1].strip().lower()
    return _5DOLLAR_PHASE_MAP.get(ph)


def _status_odd(
    value_feed: str, odd: float | None, suspended: bool | None,
    familia: str, market_canonical: str,
) -> tuple[str, str | None, float | None]:
    """Validacao de status reusada pelo caminho multiprovider (espelha _unit).

    Sempre registra com motivo; nunca transforma None em zero."""
    if not value_feed:
        return ST_INVALID, "value ausente no feed", None
    if odd is None:
        return ST_INVALID, "odd ausente ou nao numerica", None
    if odd <= 0:
        return ST_INVALID, "odd nao positiva", None
    if suspended is True:
        return ST_SUSPENDED, "mercado suspenso (live)", odd
    if familia == "UNMAPPED" or market_canonical == MK_UNKNOWN:
        return ST_UNMAPPED, "mercado nao mapeado (UNKNOWN)", odd
    return ST_OK, None, odd


def _unit_multiprovider(
    fixture_id: int, coleta_tipo: str, bookmaker: str,
    market_original: str, market_canonical: str, familia: str,
    sub: str | None, lado: str | None, linha: float | None,
    odd: float | None, phase: str | None, update_feed: str,
    collected_at: float, fixture_date: str | None, *,
    suspended: bool | None, provider: str,
) -> OddSnapshot:
    """Snapshot unitario multiprovider. side/line/phase ja vem parseados pelo
    adapter (NormalizedOdd) -- NAO re-parseia de value_feed (diferente do
    caminho api_football). Reusa _status_odd (mesma logica de validacao)."""
    # value_feed: representacao legivel preservando lado/linha originais.
    if linha is not None:
        value_feed = f"{lado or ''} {linha}".strip()
    else:
        value_feed = str(lado or "")
    status, motivo, odd_v = _status_odd(
        value_feed, odd, suspended, familia, market_canonical)
    return OddSnapshot(
        provider=provider,
        fixture_id=fixture_id, coleta_tipo=coleta_tipo, bookmaker=bookmaker,
        bet_name=market_original, bet_id=None,
        familia=familia, subfamilia=sub, lado=lado, linha=linha,
        value_feed=value_feed, odd=odd_v, suspended=suspended,
        update_feed=update_feed, collected_at=collected_at,
        fixture_date=fixture_date, status=status, motivo=motivo,
        phase=phase, market_canonical=market_canonical,
        market_original=market_original,
    )


def _construir_snapshots_multiprovider(
    normalized_odds: Iterable[Any], *,
    fixture_id_int: int | None, collected_at: float,
    fixture_date: str | None, provider: str,
) -> list[OddSnapshot]:
    """Constrói OddSnapshots a partir de NormalizedOdd (5Dollar/The Odds API).

    Reusa OddSnapshot + _status_odd + append_many (unico caminho de persistencia).
    side/line/phase/coleta_tipo vem do adapter (ja parseados do feed cru).
    `fixture_id_int` mapeia o fixture interno; se None, usa int do provider id
    quando possivel, senao 0 (registro ainda preserva proveniência)."""
    out: list[OddSnapshot] = []
    for no in normalized_odds:
        fid = fixture_id_int
        if fid is None:
            try:
                fid = int(no.fixture_provider_id)
            except (TypeError, ValueError):
                fid = 0
        market_orig = str(no.market or "")
        if provider == PROVIDER_FIVE_DOLLAR:
            familia, sub, mkc = _classificar_5dollar(market_orig)
            phase = _phase_5dollar(no.submarket)
        elif provider == PROVIDER_THE_ODDS_API:
            familia, sub, mkc = _classificar_theodds(market_orig)
            phase = None  # The Odds API nao classifica fase => nunca inventada
        else:
            familia, sub, mkc = "UNMAPPED", None, MK_UNKNOWN
            phase = None
        # coleta_tipo do adapter (opening/closing => pre_match; inplay => live)
        coleta_tipo = no.coleta_tipo or COLETA_PRE
        suspended = None  # multiprovider prospective: feed nao sinaliza suspend
        out.append(_unit_multiprovider(
            fid, coleta_tipo, str(no.bookmaker or "unknown"),
            market_orig, mkc, familia, sub, str(no.side) if no.side else None,
            _to_float(no.line), _to_float(no.price), phase,
            str(no.timestamp or ""), collected_at, fixture_date,
            suspended=suspended, provider=provider,
        ))
    return out


# ----------------------------------------------------------------------
# Migracao idempotente para multi-provider (Etapa 5F-C)
# ----------------------------------------------------------------------
# user_version marca o estado da migracao no cabecalho do DB:
#   0 = pre-5F-C (sem coluna provider, hashes V1)
#   2 = 5F-C aplicado (coluna provider + hashes V2)
# O recompute de hash V2 e deterministico, entao reprocessar e seguro; o
# marcador apenas evita retrabalho. Se um crash ocorrer apos COMMIT mas antes
# de setar user_version, a reinicializacao reprocessa (mesmos V2 hashes).
_USER_VERSION_MULTIPROVIDER = 2


def _migrar_para_multiprovider(conn: sqlite3.Connection) -> None:
    """Adiciona a coluna `provider`, classifica o legado como api_football e
    migra os hashes para V2 (incluindo provider). Idempotente e transacional.

    - DB novo: _SCHEMA ja cria com `provider`; user_version ainda 0 => a funcao
      roda o recompute sobre 0 linhas (no-op) e seta user_version=2.
    - DB legado (sem `provider`): ALTER ADD COLUMN ... NOT NULL DEFAULT
      'api_football' (backfill atomico de todos os registros em uma instrucao)
      + recompute dos snapshot_hash para V2, em transacao explicita.
    - DB ja migrado (user_version>=2): no-op imediato.

    Seguranca do recompute de hash (V1 -> V2):
      * snapshot_hash NAO tem FK, referencia externa, relatorio ou logica que
        dependa do seu valor (auditado: usado apenas para dedup UNIQUE nesta
        tabela). Recomputar e seguro.
      * Todos os hashes V1 armazenados sao distintos (UNIQUE), logo todas as
        identidades factuais sao distintas, logo todos os hashes V2 serao
        distintos (V2 = V1 + 1 campo). Nenhuma violacao de UNIQUE.
      * O recompute ocorre em transacao explicita (BEGIN/COMMIT/ROLLBACK);
        falha => rollback desfaz TODOS os UPDATEs de hash; user_version nao e
        incrementado, entao a reinicializacao tenta de novo.
    """
    user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_ver >= _USER_VERSION_MULTIPROVIDER:
        return  # ja migrado

    cols = [r[1] for r in conn.execute(
        "PRAGMA table_info(odds_snapshot_history)")]
    if "provider" not in cols:
        # ALTER com DEFAULT 'api_football' preenche o legado em instrucao unica
        # (atomico no SQLite). NOT NULL impede provider NULL dali em diante.
        conn.execute(
            "ALTER TABLE odds_snapshot_history "
            "ADD COLUMN provider TEXT NOT NULL DEFAULT 'api_football'")
    # Indice de provider (criado apos a coluna existir; idempotente). Em DBs
    # novos a coluna ja vem do _SCHEMA; em DBs legados vem do ALTER acima.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_odds_hist_provider "
        "ON odds_snapshot_history (provider)")

    # Recomputa snapshot_hash para V2 em transacao explicita. Controle manual
    # (isolation_level=None) evita o auto-commit do DDL/dml do sqlite3.
    old_iso = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN")
        rows = conn.execute(
            """
            SELECT id, fixture_id, coleta_tipo, bookmaker, bet_name, bet_id,
                   familia, subfamilia, lado, linha, value_feed, odd,
                   suspended, status
            FROM odds_snapshot_history
            """).fetchall()
        for (rid, fixture_id, coleta_tipo, bookmaker, bet_name, bet_id,
              familia, subfamilia, lado, linha, value_feed, odd,
              suspended, status) in rows:
            h = _hash_identidade(
                PROVIDER_API_FOOTBALL, fixture_id, coleta_tipo, bookmaker,
                bet_name, bet_id, familia, subfamilia, lado, linha,
                value_feed, odd, suspended, status,
            )
            conn.execute(
                "UPDATE odds_snapshot_history SET snapshot_hash=? "
                "WHERE id=?", (h, rid))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.isolation_level = old_iso
        raise
    # Marcador apos COMMIT bem-sucedido (idempotente: reprocessar e seguro).
    conn.execute(f"PRAGMA user_version = {_USER_VERSION_MULTIPROVIDER}")
    conn.isolation_level = old_iso


# ----------------------------------------------------------------------
# Migracao idempotente Etapa 5F-E2 (user_version 3 -> 4)
# ----------------------------------------------------------------------
# Adiciona 3 colunas nullable a odds_snapshot_history:
#   phase            TEXT  -- OPENING|CLOSING|INPLAY (5Dollar); NULL resto
#   market_canonical TEXT  -- TOTAL_CORNERS|...|UNKNOWN; NULL historico
#   market_original  TEXT  -- mercado cru do feed (preservado); NULL historico
# Puramente aditiva: NENHUMA linha histórica é tocada/recomputada. Hashes V2
# armazenados permanecem (api_football). Novos registros multiprovider usam V3
# (dispatch em OddSnapshot.hash). Sem re-migracao de hashes históricos.
_USER_VERSION_5FE2 = 4


def _migrar_para_5fe2(conn: sqlite3.Connection) -> None:
    """Aditiva colunas phase/market_canonical/market_original. Idempotente.

    - DB já em user_version>=4: no-op imediato.
    - DB em user_version<4: ALTER ADD COLUMN (nullable, sem default => NULL
      para todo o histórico) + seta user_version=4. Sem transacao explicita:
      DDL idempotente (IF NOT EXISTS via checagem de coluna); executescript
      auto-commita. Nao reprocessa hashes."""
    user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_ver >= _USER_VERSION_5FE2:
        return  # ja migrado
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(odds_snapshot_history)")}
    if "phase" not in cols:
        conn.execute(
            "ALTER TABLE odds_snapshot_history ADD COLUMN phase TEXT")
    if "market_canonical" not in cols:
        conn.execute(
            "ALTER TABLE odds_snapshot_history ADD COLUMN market_canonical TEXT")
    if "market_original" not in cols:
        conn.execute(
            "ALTER TABLE odds_snapshot_history ADD COLUMN market_original TEXT")
    conn.execute(
        f"PRAGMA user_version = {_USER_VERSION_5FE2}")


# ----------------------------------------------------------------------
# Persistencia append-only (FASE 6: nunca sobrescreve)
# ----------------------------------------------------------------------
class OddsSnapshotStore:
    """Append-only. INSERT OR IGNORE deduplica cotacao identica (mesmo
    provider + mesma identidade => mesma hash). Hash V2 para api_football,
    V3 para multiprovider (5Dollar/The Odds API)."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or DB_PATH)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            _migrar_para_multiprovider(conn)
            _migrar_para_5fe2(conn)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def append_many(self, snapshots: Iterable[OddSnapshot]) -> dict[str, int]:
        """Insere snapshots. Dedup por hash: mesmo provider + mesma
        cotacao => ignorada (V2 api_football / V3 multiprovider). Retorna
        {'inseridos': N, 'duplicados': N}."""
        inseridos = 0
        duplicados = 0
        snaps = list(snapshots)
        if not snaps:
            return {"inseridos": 0, "duplicados": 0}
        with self._connect() as conn:
            for s in snaps:
                h = s.hash()
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO odds_snapshot_history
                        (provider, fixture_id, coleta_tipo, bookmaker, bet_name,
                         bet_id, familia, subfamilia, lado, linha, value_feed,
                         odd, suspended, update_feed, collected_at, fixture_date,
                         e_pre_jogo, status, motivo, snapshot_hash,
                         phase, market_canonical, market_original)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        s.provider,
                        s.fixture_id, s.coleta_tipo, s.bookmaker, s.bet_name,
                        s.bet_id, s.familia, s.subfamilia, s.lado, s.linha,
                        s.value_feed, s.odd,
                        None if s.suspended is None else int(s.suspended),
                        s.update_feed, s.collected_at, s.fixture_date,
                        _e_pre_jogo(s.collected_at, s.fixture_date),
                        s.status, s.motivo, h,
                        s.phase, s.market_canonical, s.market_original,
                    ),
                )
                if cur.rowcount == 1:
                    inseridos += 1
                else:
                    duplicados += 1
        return {"inseridos": inseridos, "duplicados": duplicados}

    # ---------------------------- leitura ----------------------------
    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM odds_snapshot_history").fetchone()[0]
            por_tipo = dict(conn.execute(
                "SELECT coleta_tipo, COUNT(*) FROM odds_snapshot_history "
                "GROUP BY coleta_tipo").fetchall())
            por_familia = dict(conn.execute(
                "SELECT familia, COUNT(*) FROM odds_snapshot_history "
                "GROUP BY familia").fetchall())
            por_status = dict(conn.execute(
                "SELECT status, COUNT(*) FROM odds_snapshot_history "
                "GROUP BY status").fetchall())
            por_provider = dict(conn.execute(
                "SELECT provider, COUNT(*) FROM odds_snapshot_history "
                "GROUP BY provider").fetchall())
            n_fixtures = conn.execute(
                "SELECT COUNT(DISTINCT fixture_id) FROM odds_snapshot_history"
            ).fetchone()[0]
            n_pre_jogo = conn.execute(
                "SELECT COUNT(*) FROM odds_snapshot_history "
                "WHERE e_pre_jogo = 1").fetchone()[0]
            # Etapa 5F-E2: breakdown multiprovider (nullable em DB histórico)
            try:
                por_phase = dict(conn.execute(
                    "SELECT COALESCE(phase,'(null)'), COUNT(*) "
                    "FROM odds_snapshot_history GROUP BY phase").fetchall())
                por_canonical = dict(conn.execute(
                    "SELECT COALESCE(market_canonical,'(null)'), COUNT(*) "
                    "FROM odds_snapshot_history "
                    "GROUP BY market_canonical").fetchall())
            except sqlite3.OperationalError:
                por_phase, por_canonical = {}, {}
            por_bookmaker = dict(conn.execute(
                "SELECT provider || '/' || bookmaker, COUNT(*) "
                "FROM odds_snapshot_history "
                "GROUP BY provider, bookmaker").fetchall())
        return {
            "total_snapshots": total,
            "fixtures_distintos": n_fixtures,
            "por_coleta_tipo": por_tipo,
            "por_familia": por_familia,
            "por_status": por_status,
            "por_provider": por_provider,
            "por_bookmaker_por_provider": por_bookmaker,
            "por_phase": por_phase,
            "por_market_canonical": por_canonical,
            "snapshots_pre_jogo": n_pre_jogo,
        }


# ----------------------------------------------------------------------
# Coleta: cache-first (FASE 3/13) e API (FASE 9)
# ----------------------------------------------------------------------
def _fixture_date_from_cache(conn: sqlite3.Connection, fixture_id: int) -> str | None:
    row = conn.execute(
        "SELECT response FROM api_cache WHERE endpoint='/fixtures' "
        "AND json_extract(params,'$.id') = ?",
        (fixture_id,),
    ).fetchone()
    if row:
        try:
            lista = json.loads(row[0])
            if isinstance(lista, list) and lista:
                fx = (lista[0].get("fixture") or {})
                d = fx.get("date")
                return str(d) if d else None
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    # fallback: procura em qualquer resposta /fixtures que contenha o id
    return None


def ingerir_cache(
    store: OddsSnapshotStore, *, db_path: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """FASE 13: ingere as odds JA existentes no api_cache (0 chamadas a API).

    Le /odds (pre_match) e /odds/live do cache, constroi snapshots e armazena.
    O timestamp de coleta usado e o created_at REAL da entrada no cache (nunca
    inventado). 0 consumo de API.
    """
    db = str(db_path or DB_PATH)
    coletados: list[OddSnapshot] = []
    resumo = {"pre_entries": 0, "live_entries": 0, "snapshots": 0,
              "consumo_api": 0, "dry_run": dry_run}

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # /odds pre-match
        rows = con.execute(
            "SELECT params, response, created_at FROM api_cache "
            "WHERE endpoint='/odds'").fetchall()
        for params, resp, created_at in rows:
            try:
                p = json.loads(params)
                fx = int(p.get("fixture"))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            try:
                lista = json.loads(resp)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(lista, list) or not lista:
                continue
            resumo["pre_entries"] += 1
            entry = lista[0]
            fx_date = _fixture_date_from_cache(con, fx)
            snaps = _construir_snapshots(
                fx, COLETA_PRE, entry,
                collected_at=float(created_at), fixture_date=fx_date,
                provider=PROVIDER_API_FOOTBALL,
            )
            coletados.extend(snaps)
        # /odds/live
        rows = con.execute(
            "SELECT params, response, created_at FROM api_cache "
            "WHERE endpoint='/odds/live'").fetchall()
        for params, resp, created_at in rows:
            try:
                p = json.loads(params)
                fx = int(p.get("fixture"))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            try:
                lista = json.loads(resp)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(lista, list) or not lista:
                continue
            resumo["live_entries"] += 1
            entry = lista[0]
            fx_date = _fixture_date_from_cache(con, fx)
            snaps = _construir_snapshots(
                fx, COLETA_LIVE, entry,
                collected_at=float(created_at), fixture_date=fx_date,
                provider=PROVIDER_API_FOOTBALL,
            )
            coletados.extend(snaps)
    finally:
        con.close()

    resumo["snapshots"] = len(coletados)
    if dry_run:
        # nao escreve; reporta o que seria inserido (com dedup simulado)
        vistos: set[str] = set()
        unicos = 0
        for s in coletados:
            h = s.hash()
            if h not in vistos:
                vistos.add(h)
                unicos += 1
        resumo["inseridos"] = unicos
        resumo["duplicados"] = len(coletados) - unicos
    else:
        grav = store.append_many(coletados)
        resumo.update(grav)
    return resumo


def coletar_fixture(
    client: Any, store: OddsSnapshotStore, fixture_id: int, *,
    live: bool = False, collected_at: float | None = None,
) -> dict[str, Any]:
    """FASE 9: coleta one-shot de UM fixture na API (e grava no cache).

    `client` pode ser um OddsProvider (com provider_name) ou um client legado
    (sem provider_name -> envolvido como APIFootballOddsProvider, o unico
    provider historico auditado). Pre-match: 1 chamada /odds. Live (se --live):
    +1 chamada /odds/live. Retorna consumo de API e contagem de snapshots.
    """
    from src.config import CACHE_TTL_LIVE

    provider = resolve_provider(client)
    provider_name = provider.provider_name
    epoch = float(collected_at if collected_at is not None else time.time())
    consumo = 0
    snaps: list[OddSnapshot] = []
    fx_date: str | None = None

    # data do fixture (anti-leakage) -- tenta cache /fixtures primeiro
    try:
        con = sqlite3.connect(str(DB_PATH))
        fx_date = _fixture_date_from_cache(con, fixture_id)
        con.close()
    except sqlite3.Error:
        pass

    # pre-match /odds
    resp_pre = provider.get("/odds", params={"fixture": fixture_id})
    consumo += 1
    if resp_pre:
        snaps.extend(_construir_snapshots(
            fixture_id, COLETA_PRE, resp_pre[0],
            collected_at=epoch, fixture_date=fx_date, provider=provider_name,
        ))

    # live /odds/live (separado, FASE 16)
    if live:
        resp_live = provider.get(
            "/odds/live", params={"fixture": fixture_id}, ttl=CACHE_TTL_LIVE,
        )
        consumo += 1
        if resp_live:
            snaps.extend(_construir_snapshots(
                fixture_id, COLETA_LIVE, resp_live[0],
                collected_at=epoch, fixture_date=fx_date, provider=provider_name,
            ))

    grav = store.append_many(snaps)
    return {
        "fixture_id": fixture_id, "live": live,
        "snapshots": len(snaps), "consumo_api": consumo, **grav,
    }


def coletar_periodico(
    client: Any, store: OddsSnapshotStore, fixture_ids: Iterable[int], *,
    intervalo: int = 60, max_iter: int | None = None, live: bool = False,
) -> dict[str, Any]:
    """FASE 9: coleta periodica. AUTO DESABILITADO por padrao (requer chamada
    explicita com intervalo). Para a cada max_iter ou quando atingir limite.
    intervalo=0 => iteracoes instantaneas (uso em testes)."""
    passadas = 0
    total_consumo = 0
    total_inseridos = 0
    total_duplicados = 0
    fxs = list(fixture_ids)
    while True:
        passadas += 1
        for fx in fxs:
            r = coletar_fixture(client, store, fx, live=live)
            total_consumo += r["consumo_api"]
            total_inseridos += r["inseridos"]
            total_duplicados += r["duplicados"]
        if max_iter is not None and passadas >= max_iter:
            break
        if intervalo and intervalo > 0:
            time.sleep(intervalo)
        else:
            break  # sem intervalo => uma unica passada (one-shot equivalente)
    return {
        "passadas": passadas, "fixtures": len(fxs),
        "consumo_api": total_consumo, "inseridos": total_inseridos,
        "duplicados": total_duplicados,
    }


# ----------------------------------------------------------------------
# Etapa 5F-E2: coleta prospectiva multiprovider (5Dollar + The Odds API)
# ----------------------------------------------------------------------
# Registry centralizado: nome canonico -> classe adapter (em src.multifonte).
# Os adapters expoem fetch_odds -> list[NormalizedOdd] (lado/linha/fase ja
# parseados do feed cru). O coletor converte NormalizedOdd -> OddSnapshot e
# persiste pelo unico caminho append_many. NAO há coletor paralelo.
def _carregar_registry_multiprovider() -> dict[str, type]:
    """Import lazy de src.multifonte (evita acoplamento no topo do modulo)."""
    from src.multifonte import (
        FiveDollarFootballProvider, TheOddsAPIProvider,
    )
    return {
        PROVIDER_FIVE_DOLLAR: FiveDollarFootballProvider,
        PROVIDER_THE_ODDS_API: TheOddsAPIProvider,
    }


def coletar_multiprovider(
    provider_name: str, store: OddsSnapshotStore, *,
    fixture_id_int: int | None = None, sport_key: str = "soccer_epl",
    market: str = "corner", collected_at: float | None = None,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """Coleta prospectiva multiprovider. PROSPECTIVA apenas (pre-match/live
    conforme fase do feed). NAO preenche passado artificialmente.

    - 5Dollar: uma chamada fetch_odds(fixture_id, market) por mercado.
    - The Odds API: uma chamada fetch_odds(sport_key, markets).

    Respeita HARD_LIMIT do adapter (402/403/429 -> RateLimitHit => STOP,
    registrada como LIMITADO_POR_PLANO/LIMITE_ATINGIDO, nunca compra plano).
    Retorna consumo, snapshots, inseridos/duplicados + limites observados.
    Nao altera motor de decisao."""
    registry = _carregar_registry_multiprovider()
    if provider_name not in registry:
        return {"erro": f"provider multiprovider desconhecido: {provider_name}",
                "providers_validos": sorted(registry)}
    if adapter is None:
        adapter = registry[provider_name]()
    epoch = float(collected_at if collected_at is not None else time.time())
    fx_date: str | None = None
    if fixture_id_int is not None:
        try:
            con = sqlite3.connect(str(DB_PATH))
            fx_date = _fixture_date_from_cache(con, fixture_id_int)
            con.close()
        except sqlite3.Error:
            pass

    resumo: dict[str, Any] = {
        "provider": provider_name, "fixture_id_interno": fixture_id_int,
        "consumo_api": 0, "snapshots": 0, "inseridos": 0, "duplicados": 0,
        "limite": None,
    }
    normalized: list[Any] = []
    try:
        if provider_name == PROVIDER_FIVE_DOLLAR:
            if fixture_id_int is None:
                resumo["erro"] = "5Dollar exige fixture_id_interno"
                return resumo
            normalized = adapter.fetch_odds(fixture_id_int, market=market)
            resumo["consumo_api"] = getattr(adapter, "_calls", 1)
            resumo["market"] = market
        elif provider_name == PROVIDER_THE_ODDS_API:
            normalized = adapter.fetch_odds(
                sport_key=sport_key,
                markets="h2h,totals,spreads")
            resumo["consumo_api"] = getattr(adapter, "_calls", 1)
            resumo["sport_key"] = sport_key
    except Exception as exc:  # RateLimitHit / 402 / 403 / 429 => STOP
        nome = type(exc).__name__
        resumo["limite"] = nome
        resumo["erro"] = f"{nome}: coleta interrompida (limite/plano)"
        # Nao re-tenta, nao compra plano (Seção 10). Retorna o que tem.
        return resumo

    snaps = _construir_snapshots_multiprovider(
        normalized, fixture_id_int=fixture_id_int, collected_at=epoch,
        fixture_date=fx_date, provider=provider_name,
    )
    resumo["snapshots"] = len(snaps)
    grav = store.append_many(snaps)
    resumo["inseridos"] = grav["inseridos"]
    resumo["duplicados"] = grav["duplicados"]
    # breakdown por market_canonical/phase (proveniência, sem dump gigante)
    por_mk: dict[str, int] = {}
    por_ph: dict[str, int] = {}
    por_bm: dict[str, int] = {}
    for s in snaps:
        por_mk[s.market_canonical or "(null)"] = por_mk.get(
            s.market_canonical or "(null)", 0) + 1
        por_ph[s.phase or "(null)"] = por_ph.get(
            s.phase or "(null)", 0) + 1
        por_bm[s.bookmaker] = por_bm.get(s.bookmaker, 0) + 1
    resumo["por_market_canonical"] = por_mk
    resumo["por_phase"] = por_ph
    resumo["por_bookmaker"] = por_bm
    return resumo


# ----------------------------------------------------------------------
# Relatorio (FATO/CALCULO, sem ROI)
# ----------------------------------------------------------------------
def format_status(s: dict[str, Any]) -> str:
    linhas = [
        "[FATO] odds_snapshot_history (append-only, multi-provider)",
        f"  total de snapshots: {s['total_snapshots']}",
        f"  fixtures distintos: {s['fixtures_distintos']}",
        f"  snapshots pre-jogo (e_pre_jogo=1): {s['snapshots_pre_jogo']}",
        f"  por provider (FONTE): {s.get('por_provider', {})}",
        f"  por coleta_tipo: {s['por_coleta_tipo']}",
        f"  por familia: {s['por_familia']}",
        f"  por status: {s['por_status']}",
        f"  por phase (5Dollar OPENING/CLOSING/INPLAY): {s.get('por_phase', {})}",
        f"  por market_canonical: {s.get('por_market_canonical', {})}",
        f"  por bookmaker/provider: {s.get('por_bookmaker_por_provider', {})}",
        "",
        "[CALCULO] ROI: NAO CALCULADO nesta etapa (Etapa 5F = coleta).",
        "[CALCULO] Etapa 6: BLOQUEADA (nao alterada por esta etapa).",
    ]
    return "\n".join(linhas)