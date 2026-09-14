"""ETAPA 5F-D -- Camada multi-fonte: abstracoes, adapters e GATE de seguranca.

Camada de INTEGRACAO/COLETA, totalmente SEPARADA do motor de aposta. NAO
importa nem altera politica_aprovacao, analysis, settlement, backtest,
calibration, policy ou qualquer regra de decisao. NAO calcula ROI. NAO
implementa fallback automatico. NAO escolhe fonte vencedora.

Principios:
    - FONTE -> COLETA -> NORMALIZACAO -> AUDITORIA  (esta camada)
      separada de MOTOR -> CALCULO -> APROVACAO -> SETTLEMENT.
    - PROVIDER (API que fornece os dados) != BOOKMAKER (casa de apostas).
    - null != zero. missing != zero. campo ausente/endpoint indisponivel/erro
      /plano sem cobertura NUNCA viram zero (regra absoluta, testada).
    - NAO inventar endpoint. NAO inferir campo. Sem comprovacao por
      documentacao oficial OU resposta real => "A CONFIRMAR".
    - GATE de seguranca: chamadas autenticadas SO ocorrem se
      MULTISOURCE_CREDENTIALS_ROTATED == 1. Caso contrario, adapters sao
      implementados e testados localmente, mas chamadas reais sao BLOQUEADAS.
    - Nenhuma chave e impressa (inteira, inicio, final, comprimento ou hash).
    - Quota: 429/402 interrompem o provider; no max 1 retry tecnico.

Providers canonicos:
    api_football, the_odds_api, five_dollar_football, sportmonks,
    apifootball_com, football_data_org, statsbomb_open.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


# ----------------------------------------------------------------------
# GATE de seguranca de credenciais (Etapa 5F-D)
# ----------------------------------------------------------------------
# Credenciais foram expostas visualmente antes desta etapa. Nenhuma chamada
# autenticada deve ocorrer automaticamente. O usuario precisa setar
# MULTISOURCE_CREDENTIALS_ROTATED=1 explicitamente no .env apos rotacionar as
# chaves. O codigo NUNCA cria essa variavel automaticamente.

GATE_ENV = "MULTISOURCE_CREDENTIALS_ROTATED"


def credenciais_rotacionadas() -> bool:
    """True apenas se o GATE explicito estiver aberto pelo usuario."""
    return os.getenv(GATE_ENV) == "1"


class CredentialsBlocked(RuntimeError):
    """Chamada autenticada bloqueada: GATE fechado ou credencial ausente."""


class EndpointNotConfirmed(RuntimeError):
    """Endpoint/campo nao confirmado por documentacao oficial ou resposta real."""


class RateLimitHit(RuntimeError):
    """Provider respondeu 429 (rate limit). Interrompe o provider."""


class QuotaExhausted(RuntimeError):
    """Provider respondeu 402 (pagamento/quota). Interrompe o provider."""


# Status de dado normalizado (nunca "zero" para ausente).
ST_OK = "OK"
ST_MISSING = "MISSING"          # campo ausente no feed
ST_NOT_CONFIRMED = "A_CONFIRMAR"  # endpoint/campo nao confirmado
ST_BLOCKED = "BLOQUEADO_SEGURANCA"  # gate fechado
ST_NO_CREDENTIAL = "SEM_CREDENCIAL"
ST_PLAN_LIMITED = "LIMITADO_PELO_PLANO"
ST_NOT_AVAILABLE = "NAO_DISPONIVEL"
ST_TECHNICAL_FAIL = "FALHA_TECNICA"
ST_NULL = "NULL_EXPLICITO"       # null factual (diferente de zero)


# ----------------------------------------------------------------------
# Normalizacao de proveniencia
# ----------------------------------------------------------------------
@dataclass
class NormalizedFact:
    """Dado factual normalizado com proveniencia completa.

    provider = API que forneceu (api_football, sportmonks, ...).
    raw_value = valor bruto como veio (pode ser None).
    normalized_value = valor normalizado (None se ausente; NUNCA 0 para ausente).
    status = situacao do dado (ST_*).
    """
    provider: str
    endpoint: str
    retrieved_at: float
    fixture_provider_id: str | None
    fixture_corner_id: str | None = None      # mapeamento para fixture API-Football
    home: str | None = None
    away: str | None = None
    kickoff: str | None = None
    competition: str | None = None
    season: str | None = None
    field: str = ""                            # nome do campo (corners, red_cards, ...)
    raw_value: Any = None
    normalized_value: Any = None
    status: str = ST_OK
    motivo: str | None = None


@dataclass
class NormalizedOdd:
    """Odd normalizada com proveniencia. provider != bookmaker."""
    provider: str
    bookmaker: str
    fixture_provider_id: str | None
    fixture_corner_id: str | None
    market: str
    submarket: str | None
    side: str | None
    line: float | None
    price: float | None
    timestamp: str | None
    coleta_tipo: str       # 'pre_match' | 'live'
    status: str = ST_OK
    motivo: str | None = None


# ----------------------------------------------------------------------
# Abstracoes de Provider
# ----------------------------------------------------------------------
class OddsProvider(Protocol):
    """Reusado da 5F-C. Provider de ODDS com identidade explicita."""
    provider_name: str

    def is_available(self) -> bool: ...
    def fetch_odds(self, *args, **kw) -> list[NormalizedOdd]: ...


class FactProvider(Protocol):
    """Provider de FATOS (stats/eventos/placar/identidade). Identidade explicita.

    NAO obriga um provider a implementar operacoes que sua API nao oferece:
    metodos nao suportados levantam NotImplementedError => status AUSENTE.
    """
    provider_name: str

    def is_available(self) -> bool: ...
    def fetch_competitions(self) -> list[dict]: ...
    def fetch_match(self, match_id: str) -> list[NormalizedFact]: ...
    def fetch_statistics(self, match_id: str) -> list[NormalizedFact]: ...
    def fetch_events(self, match_id: str) -> list[NormalizedFact]: ...


# ----------------------------------------------------------------------
# Helper: checagem de gate + credencial (nunca imprime valores)
# ----------------------------------------------------------------------
def _checar_gate(nome_provider: str, cred_env: str) -> None:
    """Levanta CredentialsBlocked se GATE fechado ou credencial ausente.

    NUNCA imprime valor, prefixo, sufixo, comprimento ou hash da chave.
    """
    if not credenciais_rotacionadas():
        raise CredentialsBlocked(
            f"CHAMADAS REAIS BLOQUEADAS POR SEGURANCA: {GATE_ENV}!=1 "
            f"(provider {nome_provider}).")
    if not os.getenv(cred_env):
        raise CredentialsBlocked(
            f"SEM CREDENCIAL para {nome_provider} (env {cred_env} ausente).")


def _cred_configurada(cred_env: str) -> bool:
    """True se a credencial esta presente (NAO imprime valor)."""
    return os.getenv(cred_env) not in (None, "")


# ----------------------------------------------------------------------
# Transporte HTTP injetavel (Etapa 5F-D2: chamadas reais)
# ----------------------------------------------------------------------
# transport(url, params=None, headers=None, timeout=30) -> parsed JSON.
# Levanta RateLimitHit(429)/QuotaExhausted(402)/CredentialsBlocked(401,403)/
# EndpointNotConfirmed(404) conforme o HTTP. Default usa requests.
# NUNCA imprime credenciais; o valor da chave vai somente no param/header.
class _HTTPError(RuntimeError):
    """Erro HTTP nao classificado (status nao mapeado)."""


def _default_transport(url, params=None, headers=None, timeout=30):
    """Transporte real via requests. Nao imprime credenciais."""
    import requests
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
    except Exception as e:  # rede/DNS/timeout
        raise _HTTPError(f"rede: {type(e).__name__}") from e
    base = url.split("?")[0]
    if r.status_code == 429:
        raise RateLimitHit(f"HTTP 429 rate-limit em {base}")
    if r.status_code == 402:
        raise QuotaExhausted(f"HTTP 402 pagamento/quota em {base}")
    if r.status_code in (401, 403):
        raise CredentialsBlocked(f"HTTP {r.status_code} credencial invalida em {base}")
    if r.status_code == 404:
        raise EndpointNotConfirmed(f"HTTP 404 endpoint nao confirmado em {base}")
    if not r.ok:
        raise _HTTPError(f"HTTP {r.status_code} em {base}")
    try:
        return r.json()
    except Exception:
        return r.text


def _redact(text: str, secrets: list[str]) -> str:
    """Remove ocorrencias dos valores de credencial de um texto (raw save).

    Redact tambem PREFIXOS de credencial: provedores como 5Dollar retornam
    ``data.key.prefix`` (ex. ``fb_live_xxxx``) na resposta de /status — apenas
    o valor completo nao basta. Redact (a) o valor completo, (b) os 12 primeiros
    caracteres de cada segredo longo, e (c) o padrao ``fb_live_<alnum>``.
    """
    import re as _re
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[REDACTED]")
            if len(s) >= 12:
                text = text.replace(s[:12], "[REDACTED]")
    # padrao de prefixo 5Dollar (fb_live_ + run alfanumerica)
    text = _re.sub(r"fb_live_[A-Za-z0-9_-]{4,}", "[REDACTED]", text)
    return text


# ----------------------------------------------------------------------
# Adapters de ODDS
# ----------------------------------------------------------------------
class TheOddsAPIProvider:
    """Adapter The Odds API (the-odds-api.com), v4.

    DOCUMENTADO (https://the-odds-api.com/liveapi/guides/v4/):
      - Base: https://api.the-odds-api.com/v4
      - /sports                       (lista esportes)
      - /sports/{sport_key}/odds       (odds pre-match, com bookmakers/markets)
      - /sports/{sport_key}/scores    (scores, quando disponivel)
      - Auth: query param apiKey=...
      - Rate limit: header X-Requests-Remaining / X-Requests-Used.
    OBSERVADO NA RESPOSTA: A CONFIRMAR (nenhuma chamada real feita nesta etapa).
    NAO acessa endpoint historico pago.
    """
    provider_name = "the_odds_api"
    CRED_ENV = "THE_ODDS_API_KEY"
    BASE_URL = "https://api.the-odds-api.com/v4"
    # Hard limit de seguranca por execucao (Phase 22).
    HARD_LIMIT = 10

    def __init__(self, transport=None) -> None:
        self._calls = 0
        self._transport = transport or _default_transport

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def _get(self, path, params=None):
        self._guard()
        url = f"{self.BASE_URL}{path}"
        p = dict(params or {})
        p["apiKey"] = os.getenv(self.CRED_ENV)
        self._calls += 1
        return self._transport(url, params=p)

    def fetch_sports(self) -> list[dict]:
        """GET /sports — lista esportes/ligas acessiveis na conta."""
        return self._get("/sports")

    def fetch_odds(self, sport_key: str = "soccer_epl",
                   markets: str = "h2h,totals,spreads,alternate_totals",
                   regions: str = "eu,uk") -> list[NormalizedOdd]:
        """GET /sports/{sport_key}/odds — odds pre-match, shape documentado v4.

        Response: [{id, sport_key, commence_time, home_team, away_team,
        bookmakers:[{key,title,markets:[{key,outcomes:[{name,price,point}]}]}]}].
        """
        data = self._get(f"/sports/{sport_key}/odds",
                         {"regions": regions, "markets": markets,
                          "oddsFormat": "decimal"})
        if not isinstance(data, list):
            return []
        out: list[NormalizedOdd] = []
        for ev in data:
            fid = str(ev.get("id", ""))
            home = ev.get("home_team"); away = ev.get("away_team")
            for bm in ev.get("bookmakers", []) or []:
                bk = bm.get("title") or bm.get("key")
                for mk in bm.get("markets", []) or []:
                    market = mk.get("key", "")
                    for oc in mk.get("outcomes", []) or []:
                        out.append(NormalizedOdd(
                            provider=self.provider_name, bookmaker=bk,
                            fixture_provider_id=fid, fixture_corner_id=None,
                            market=market, submarket=None,
                            side=oc.get("name"),
                            line=oc.get("point"),
                            price=oc.get("price"),
                            timestamp=ev.get("commence_time"),
                            coleta_tipo="pre_match",
                            status=ST_OK))
        return out


class FiveDollarFootballProvider:
    """Adapter 5DollarFootballAPI.

    DOCUMENTADO vs OBSERVADO: A CONFIRMAR. A documentacao menciona mercados
    (1X2, O/U gols, AH, BTTS, corners, asian corners, cards, asian cards,
    opening, closing, in-play), mas a confirmacao real de endpoints/cobertura
    da conta atual exige chamada autenticada (gate fechado => NAO TESTADA).
    Nenhum endpoint e inventado aqui; o adapter fica A CONFIRMAR.
    """
    provider_name = "five_dollar_football"
    CRED_ENV = "FIVE_DOLLAR_FOOTBALL_API_KEY"
    HARD_LIMIT = 15
    # Endpoints confirmados por documentacao oficial (5dollarfootballapi.com/docs):
    BASE_URL = "https://api.5dollarfootballapi.com/v1"
    # market values: 1x2, asian, goalline, corner, corner_asian, cards,
    # cards_asian, asian_half, goalline_half, corner_half, btts
    MARKETS = ["1x2", "asian", "goalline", "corner", "corner_asian",
               "cards", "cards_asian", "btts"]

    def __init__(self, transport=None) -> None:
        self._calls = 0
        self._transport = transport or _default_transport

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def _get(self, path, params=None):
        self._guard()
        url = f"{self.BASE_URL}{path}"
        headers = {"Authorization": f"Bearer {os.getenv(self.CRED_ENV)}"}
        self._calls += 1
        return self._transport(url, params=params, headers=headers)

    def fetch_status(self) -> dict:
        """GET /status — plano/limites/uso (smoke test ideal)."""
        return self._get("/status")

    def fetch_fixtures(self, params=None) -> list[dict]:
        """GET /v1/fixtures -- params start_time/end_time (unix UTC, janela
        <=24h). Resposta real: {success, data:[...], pagination}; retorna a
        lista de fixtures (desembrulha 'data'), nunca o dict bruto."""
        data = self._get("/fixtures", params)
        if isinstance(data, dict):
            data = data.get("data")
        return data or []

    def fetch_odds(self, fixture_id, market="corner") -> list[NormalizedOdd]:
        """GET /fixtures/{id}/odds?market=... — opening/closing/in-play.

        Shape OBSERVADO na conta real (5F-D2C, fixture 2062437761, mercado
        corner): ``{success, data:{fixture_id, bookmakers:[{name, slug,
        odds:{<market_key>:{opening:{line,over,under}, closing:{...},
        inplay:{...}}}}]}}``. Cada bloco de mercado tem fases
        opening/closing/inplay; cada fase tem ``line`` + lados (over/under para
        totais, home/draw/away para 1x2, home/away para asian, yes/no para
        btts). market em MARKETS.
        """
        data = self._get(f"/fixtures/{fixture_id}/odds", {"market": market})
        return self._parse_odds(data, fixture_id, market)

    # fases observadas -> coleta_tipo
    _PHASE_MAP = (("opening", "pre_match"), ("close", "pre_match"),
                  ("closing", "pre_match"), ("in_play", "live"),
                  ("inplay", "live"), ("live", "live"))
    # chaves que sao metadados de linha, nao lados apostaveis
    _LINE_KEYS = ("line", "point", "handicap", "h")

    def _parse_odds(self, data, fixture_id, market) -> list[NormalizedOdd]:
        out: list[NormalizedOdd] = []
        if not isinstance(data, (list, dict)):
            return out

        # Shape OBSERVADO: {success, data:{bookmakers:[...]}}
        # Desembrulha ate achar a lista de bookmakers.
        root = data
        if isinstance(root, dict) and isinstance(root.get("data"), dict) \
                and isinstance(root["data"].get("bookmakers"), list):
            root = root["data"]

        bks = root.get("bookmakers") if isinstance(root, dict) else None
        if isinstance(bks, list):
            # Caminho observado: data.bookmakers[].odds[<mk_key>][<phase>][<side>]
            for bk_obj in bks:
                if not isinstance(bk_obj, dict):
                    continue
                bk = bk_obj.get("name") or bk_obj.get("slug") or "unknown"
                odds_blk = bk_obj.get("odds")
                if not isinstance(odds_blk, dict):
                    continue
                for mk_key, mk_block in odds_blk.items():
                    if not isinstance(mk_block, dict):
                        continue
                    for phase_key, coleta in self._PHASE_MAP:
                        phase = mk_block.get(phase_key)
                        if not isinstance(phase, dict):
                            continue
                        line = phase.get("line") or phase.get("point") \
                            or phase.get("handicap")
                        for side, price in phase.items():
                            if side in self._LINE_KEYS:
                                continue
                            if isinstance(price, (int, float)) or price is None:
                                out.append(NormalizedOdd(
                                    provider=self.provider_name, bookmaker=bk,
                                    fixture_provider_id=str(fixture_id),
                                    fixture_corner_id=None, market=market,
                                    submarket=f"{mk_key}/{phase_key}",
                                    side=str(side), line=line, price=price,
                                    timestamp=None, coleta_tipo=coleta,
                                    status=(ST_OK if price is not None
                                            else ST_MISSING)))
            return out

        # Fallback flexivel (shape flat alternativo, nao observado nesta conta).
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            bk = item.get("bookmaker") or item.get("bookmaker_slug") or "unknown"
            for phase_key, coleta in self._PHASE_MAP:
                block = item.get(phase_key)
                if isinstance(block, dict):
                    for side, price in block.items():
                        if side in self._LINE_KEYS:
                            continue
                        if isinstance(price, (int, float)) or price is None:
                            out.append(NormalizedOdd(
                                provider=self.provider_name, bookmaker=bk,
                                fixture_provider_id=str(fixture_id),
                                fixture_corner_id=None, market=market,
                                submarket=phase_key, side=str(side),
                                line=item.get("line") or item.get("point"),
                                price=price, timestamp=None,
                                coleta_tipo=coleta,
                                status=(ST_OK if price is not None
                                        else ST_MISSING)))
            if isinstance(item.get("odds"), list):
                for oc in item["odds"]:
                    out.append(NormalizedOdd(
                        provider=self.provider_name, bookmaker=bk,
                        fixture_provider_id=str(fixture_id),
                        fixture_corner_id=None, market=market,
                        submarket=item.get("phase"),
                        side=str(oc.get("side") or oc.get("name") or ""),
                        line=oc.get("line") or oc.get("point"),
                        price=oc.get("price"), timestamp=oc.get("updated_at"),
                        coleta_tipo="pre_match",
                        status=(ST_OK if oc.get("price") is not None
                                else ST_MISSING)))
        return out


# ----------------------------------------------------------------------
# Adapters de FATOS
# ----------------------------------------------------------------------
class SportmonksProvider:
    """Adapter Sportmonks (api.sportmonks.com), v3 football.

    DOCUMENTADO (https://docs.sportmonks.com/football):
      - Base: https://api.sportmonks.com/v3
      - Auth: query param api_token=...
      - /fixtures, /fixtures/{id}, /fixtures/{id}/statistics
      - Estatisticas por fixture quando o plano cobre.
    OBSERVADO: A CONFIRMAR. Se o plano cobrir so ligas de teste =>
    LIMITAÇÃO DE PLANO (nao confundir com FONTE RUIM).
    """
    provider_name = "sportmonks"
    CRED_ENV = "SPORTMONKS_API_TOKEN"
    HARD_LIMIT = 15
    BASE_URL = "https://api.sportmonks.com/v3/football"

    def __init__(self, transport=None) -> None:
        self._calls = 0
        self._transport = transport or _default_transport

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def _get(self, path, params=None):
        self._guard()
        url = f"{self.BASE_URL}{path}"
        p = dict(params or {})
        p["api_token"] = os.getenv(self.CRED_ENV)
        self._calls += 1
        return self._transport(url, params=p)

    def fetch_leagues(self) -> list[dict]:
        """Smoke: GET /leagues?limit=1."""
        d = self._get("/leagues", {"limit": 1})
        return d.get("data", []) if isinstance(d, dict) else (d or [])

    def fetch_fixtures_by_date(self, date_str: str) -> list[dict]:
        """GET /fixtures/between/{date}/{date} — lista basica de fixtures no dia.

        Documentado (docs.sportmonks.com/v3): path params YYYY-MM-DD. Retorna
        lista com id/nomes/placar/data (sem includes agregados). Use
        fetch_match(id) para detalhe de cards/corners/statistics.
        """
        d = self._get(f"/fixtures/between/{date_str}/{date_str}")
        return d.get("data", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])

    def fetch_match(self, match_id: str) -> list[NormalizedFact]:
        """GET /fixtures/{id}?include=statistics;events;cards;corners;scores."""
        d = self._get(f"/fixtures/{match_id}",
                      {"include": "statistics;events;cards;corners;"
                                  "scores;lineups"})
        return self._parse(d, match_id)

    def fetch_statistics(self, match_id: str) -> list[NormalizedFact]:
        return self.fetch_match(match_id)

    def fetch_events(self, match_id: str) -> list[NormalizedFact]:
        return self.fetch_match(match_id)

    def _parse(self, d, match_id) -> list[NormalizedFact]:
        out: list[NormalizedFact] = []
        if not isinstance(d, dict):
            return out
        fx = d.get("data", d)
        if isinstance(fx, list):
            fx = fx[0] if fx else {}
        if not isinstance(fx, dict):
            return out
        fid = str(fx.get("id", match_id))
        # statistics: lista de {type:{name}, data:{...}} por team
        for stat in fx.get("statistics", []) or []:
            tname = (stat.get("type") or {}).get("name", "") if isinstance(stat.get("type"), dict) else str(stat.get("type", ""))
            for side in ("home", "away"):
                val = (stat.get("data") or {}).get(side) if isinstance(stat.get("data"), dict) else stat.get(side)
                field = self._map(tname)
                if field:
                    out.append(NormalizedFact(
                        provider=self.provider_name, endpoint="/fixtures",
                        retrieved_at=0.0, fixture_provider_id=fid,
                        fixture_corner_id=None, field=f"{field}_{side}",
                        raw_value=val, normalized_value=self._num(val),
                        status=(ST_OK if val is not None else ST_MISSING)))
        # cards: eventos de cartao
        cards = fx.get("cards", []) or []
        yell = red = None
        if isinstance(cards, list):
            for c in cards:
                ct = (c.get("type") or c.get("card_type") or "")
                if isinstance(ct, dict):
                    ct = ct.get("name", "")
                ct = str(ct).lower()
                if "red" in ct: red = (red or 0) + 1
                elif "yellow" in ct: yell = (yell or 0) + 1
            has = bool(cards)
            for field, val in (("yellow_cards", yell), ("red_cards", red)):
                out.append(NormalizedFact(
                    provider=self.provider_name, endpoint="/fixtures",
                    retrieved_at=0.0, fixture_provider_id=fid,
                    fixture_corner_id=None, field=field,
                    raw_value=cards, normalized_value=(val if has else None),
                    status=(ST_OK if has else ST_MISSING)))
        return out

    @staticmethod
    def _map(t):
        t = (t or "").lower()
        if "corner" in t: return "corners"
        if "yellow" in t: return "yellow_cards"
        if "red" in t: return "red_cards"
        if "shot" in t and "on" in t: return "shots_on_target"
        if "shot" in t: return "shots"
        if "possession" in t: return "possession"
        if "blocked" in t: return "blocked_shots"
        if "goal" in t: return "goals"
        return None

    @staticmethod
    def _num(v):
        if v is None: return None
        s = str(v).replace("%", "").strip()
        try: return int(s)
        except ValueError:
            try: return float(s)
            except ValueError: return None


class APIFootballComProvider:
    """Adapter APIFootball.com (apiv3.apifootball.com).

    IMPORTANTE: NAO confundir com API-Football / API-Sports (api-sports.io).
    Provider canonico: apifootball_com (distinto de api_football em todo
    codigo, banco, relatorio e logs).

    DOCUMENTADO (apifootball.com/documentation):
      - Base: https://apiv3.apifootball.com/
      - Auth: query param APIkey=...
      - ?action=get_events&match_id=...   (cards[], goalscorer[], statistics[])
      - ?action=get_statistics&match_id=... (statistics[]: {type,home,away})
      - ?action=get_odds&match_id=...
    OBSERVADO: A CONFIRMAR na resposta real (parser flexivel).
    """
    provider_name = "apifootball_com"
    CRED_ENV = "APIFOOTBALL_COM_API_KEY"
    HARD_LIMIT = 15
    BASE_URL = "https://apiv3.apifootball.com"

    def __init__(self, transport=None) -> None:
        self._calls = 0
        self._transport = transport or _default_transport

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def _call(self, action, extra=None):
        self._guard()
        params = {"action": action, "APIkey": os.getenv(self.CRED_ENV)}
        if extra:
            params.update(extra)
        self._calls += 1
        return self._transport(self.BASE_URL + "/", params=params)

    def fetch_competitions(self) -> list[dict]:
        return self._call("get_leagues") or []

    def fetch_events_by_date(self, from_d: str, to_d: str,
                             league_id: str | None = None) -> list[dict]:
        """get_events com from/to (yyyy-mm-dd) — retorna todas as partidas no
        intervalo, cada uma com cards[] (card: 'yellow card'/'red card').

        Documentado (apifootball.com/documentation): from, to, league_id
        (opcional). NAO inventa campos; parser flexivel em fetch_events.
        """
        extra = {"from": from_d, "to": to_d}
        if league_id:
            extra["league_id"] = league_id
        data = self._call("get_events", extra)
        return data if isinstance(data, list) else ([] if data is None else [data])

    def fetch_events(self, match_id: str) -> list[NormalizedFact]:
        """get_events — inclui cards[] (Yellow/Red por evento)."""
        data = self._call("get_events", {"match_id": match_id})
        if not isinstance(data, list):
            data = [data] if isinstance(data, dict) else []
        out: list[NormalizedFact] = []
        for ev in data:
            if not isinstance(ev, dict):
                continue
            fid = str(ev.get("match_id", match_id))
            home = ev.get("match_hometeam_name")
            away = ev.get("match_awayteam_name")
            # cards[]: {time, card: "yellow"/"red", ...}
            yell = red = None
            for c in ev.get("cards", []) or []:
                ct = (c.get("card") or c.get("card_type") or "").lower()
                if "red" in ct:
                    red = (red or 0) + 1
                elif "yellow" in ct:
                    yell = (yell or 0) + 1
            # se cards[] existir mas vazio => zero explicito; se ausente => None
            has_cards = "cards" in ev
            out.append(NormalizedFact(
                provider=self.provider_name, endpoint="get_events",
                retrieved_at=0.0, fixture_provider_id=fid,
                fixture_corner_id=None, home=home, away=away,
                kickoff=ev.get("match_date"),
                competition=ev.get("league_name"),
                field="yellow_cards",
                raw_value=ev.get("cards"),
                normalized_value=(yell if has_cards else None),
                status=(ST_OK if has_cards else ST_MISSING)))
            out.append(NormalizedFact(
                provider=self.provider_name, endpoint="get_events",
                retrieved_at=0.0, fixture_provider_id=fid,
                fixture_corner_id=None, home=home, away=away,
                kickoff=ev.get("match_date"),
                competition=ev.get("league_name"),
                field="red_cards",
                raw_value=ev.get("cards"),
                normalized_value=(red if has_cards else None),
                status=(ST_OK if has_cards else ST_MISSING)))
        return out

    def fetch_statistics(self, match_id: str) -> list[NormalizedFact]:
        """get_statistics — statistics[]: {type, home, away}."""
        data = self._call("get_statistics", {"match_id": match_id})
        out: list[NormalizedFact] = []
        # shape: {match_id: {statistics:[...], player_statistics:[...]}}
        block = data
        if isinstance(data, dict) and match_id in data:
            block = data[match_id]
        stats = (block or {}).get("statistics", []) if isinstance(block, dict) else []
        for s in stats or []:
            t = s.get("type", "")
            for side in ("home", "away"):
                v = s.get(side)
                field = self._map_stat(t)
                if field:
                    out.append(NormalizedFact(
                        provider=self.provider_name, endpoint="get_statistics",
                        retrieved_at=0.0, fixture_provider_id=str(match_id),
                        fixture_corner_id=None, field=f"{field}_{side}",
                        raw_value=v, normalized_value=self._num(v),
                        status=(ST_OK if v is not None else ST_MISSING)))
        return out

    def fetch_match(self, match_id: str) -> list[NormalizedFact]:
        return self.fetch_events(match_id)

    @staticmethod
    def _map_stat(t):
        t = (t or "").lower()
        if "corner" in t: return "corners"
        if "shot" in t and "on" not in t and "goal" not in t: return "shots"
        if "shot on target" in t or ("shot" in t and "on" in t): return "shots_on_target"
        if "possession" in t: return "possession"
        if "yellow" in t: return "yellow_cards"
        if "red" in t: return "red_cards"
        if "goal" in t: return "goals"
        if "blocked" in t: return "blocked_shots"
        return None

    @staticmethod
    def _num(v):
        if v is None: return None
        s = str(v).replace("%", "").strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return None


class FootballDataOrgProvider:
    """Adapter football-data.org (v4).

    DOCUMENTADO (https://www.football-data.org/coverage):
      - Base: https://api.football-data.org/v4
      - Auth: header X-Auth-Token: ...
      - /competitions, /competitions/{id}/matches, /matches/{id},
        /teams/{id}/matches, /competitions/{id}/standings
      - Plano free: ~10 chamadas/min. Sem corners/cards/shots (nao oferecidos).
    Avaliar a fonte pelo papel correto: identidade/fixture/competicao/placar.
    NAO penalizada por nao ter stats que nao oferece.
    OBSERVADO: A CONFIRMAR (gate fechado => NAO TESTADA).
    """
    provider_name = "football_data_org"
    CRED_ENV = "FOOTBALL_DATA_API_KEY"
    HARD_LIMIT = 10
    BASE_URL = "https://api.football-data.org/v4"

    def __init__(self, transport=None) -> None:
        self._calls = 0
        self._transport = transport or _default_transport

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def _get(self, path, params=None):
        self._guard()
        url = f"{self.BASE_URL}{path}"
        headers = {"X-Auth-Token": os.getenv(self.CRED_ENV)}
        self._calls += 1
        return self._transport(url, params=params, headers=headers)

    def fetch_competitions(self) -> list[dict]:
        """Smoke: GET /competitions."""
        d = self._get("/competitions")
        return d.get("competitions", []) if isinstance(d, dict) else (d or [])

    def fetch_matches_by_competition(self, code: str,
                                     dateFrom: str | None = None,
                                     dateTo: str | None = None) -> list[dict]:
        """GET /competitions/{code}/matches?dateFrom=&dateTo= — identidade/
        placar/status por competicao. Sem corners/cards (nao oferecidos).

        Documentado (football-data.org v4): code ex. 'PL','CL','PD','SA','BL1'.
        """
        params = {}
        if dateFrom and dateTo:
            params = {"dateFrom": dateFrom, "dateTo": dateTo}
        d = self._get(f"/competitions/{code}/matches", params=params or None)
        if isinstance(d, dict):
            return d.get("matches", [])
        return d if isinstance(d, list) else []

    def fetch_match(self, match_id: str) -> list[NormalizedFact]:
        """GET /matches/{id} — identidade/placar/status. Sem corners/cards."""
        d = self._get(f"/matches/{match_id}")
        out: list[NormalizedFact] = []
        if not isinstance(d, dict):
            return out
        m = d
        home = (m.get("homeTeam") or {}).get("name")
        away = (m.get("awayTeam") or {}).get("name")
        comp = (m.get("competition") or {}).get("name")
        sc = m.get("score") or {}
        ft = sc.get("fullTime") or {}
        ht = sc.get("halfTime") or {}
        for field, val in (("score_ft_home", ft.get("home")),
                           ("score_ft_away", ft.get("away")),
                           ("score_ht_home", ht.get("home")),
                           ("score_ht_away", ht.get("away")),
                           ("status", m.get("status")),
                           ("kickoff", (m.get("utcDate")))):
            out.append(NormalizedFact(
                provider=self.provider_name, endpoint=f"/matches/{match_id}",
                retrieved_at=0.0, fixture_provider_id=str(match_id),
                fixture_corner_id=None, home=home, away=away,
                kickoff=m.get("utcDate"), competition=comp,
                field=field, raw_value=val, normalized_value=val,
                status=(ST_OK if val is not None else ST_MISSING)))
        return out

    def fetch_statistics(self, match_id: str) -> list[NormalizedFact]:
        # football-data.org NAO oferece stats de corners/cards/shots.
        return []  # AUSENTE por design da API (nao e limitacao do plano)

    def fetch_events(self, match_id: str) -> list[NormalizedFact]:
        return []  # AUSENTE por design da API


class StatsBombOpenProvider:
    """Adapter StatsBomb Open Data (publico, SEM autenticacao).

    DOCUMENTADO (https://github.com/statsbomb/open-data):
      - Base: https://raw.githubusercontent.com/statsbomb/open-data/master/data
      - competitions.json
      - matches/{competition_id}/{season_id}.json
      - events/{match_id}.json
      - lineups/{match_id}.json
      - three-sixty/{match_id}.json
    Sem auth. Sem quota paga. NAO e fonte LIVE. NAO e fonte de odds. Fonte de
    pesquisa/historico. Pode usar match_id 9880 como smoke test conhecido.
    """
    provider_name = "statsbomb_open"
    CRED_ENV = ""  # sem credencial
    HARD_LIMIT = 30  # uso moderado; dados publicos
    BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

    def __init__(self, fetcher=None) -> None:
        # fetcher injetavel para testes (NAO faz chamada real por padrao).
        self._calls = 0
        self._fetcher = fetcher  # callable(url) -> bytes/str | None

    def is_available(self) -> bool:
        # Publico: sempre "disponivel" em principio; chamada real depende de
        # fetcher/rede. Nao exige gate nem credencial.
        return True

    def _fetch(self, path: str) -> Any:
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit("statsbomb_open: HARD_LIMIT atingido")
        if self._fetcher is None:
            # Sem fetcher configurado => nao faz chamada real (modo seguro).
            raise CredentialsBlocked(
                "statsbomb_open: fetcher nao configurado (modo seguro).")
        url = f"{self.BASE_URL}/{path}"
        self._calls += 1
        return self._fetcher(url)

    def fetch_competitions(self) -> list[dict]:
        raw = self._fetch("competitions.json")
        import json
        return json.loads(raw) if raw else []

    def fetch_match_list(self, competition_id: int, season_id: int) -> list[dict]:
        raw = self._fetch(f"matches/{competition_id}/{season_id}.json")
        import json
        return json.loads(raw) if raw else []

    def fetch_events(self, match_id: int | str) -> list[NormalizedFact]:
        raw = self._fetch(f"events/{match_id}.json")
        if not raw:
            return []
        import json
        events = json.loads(raw)
        # Normalizacao minima: cada evento vira um NormalizedFact de pesquisa.
        out: list[NormalizedFact] = []
        for ev in events:
            out.append(NormalizedFact(
                provider=self.provider_name,
                endpoint=f"events/{match_id}.json",
                retrieved_at=0.0,
                fixture_provider_id=str(match_id),
                fixture_corner_id=None,
                field=str(ev.get("type", {}).get("name", "")),
                raw_value=ev,
                normalized_value=ev.get("type", {}).get("name"),
                status=ST_OK,
            ))
        return out

    def fetch_match(self, match_id: str) -> list[NormalizedFact]:
        return self.fetch_events(match_id)

    def fetch_statistics(self, match_id: str) -> list[NormalizedFact]:
        # StatsBomb nao tem tabela de "statistics" agregadas; os eventos
        # (shots, cards, etc.) sao derivados dos eventos. Nao inventa campo.
        return []


# ----------------------------------------------------------------------
# Registry estendido (reusa o da 5F-C + novos providers)
# ----------------------------------------------------------------------
# O registry de ODDS da 5F-C permanece em src/odds_coleta.py (_PROVIDER_REGISTRY,
# com api_football). Aqui addedos os novos providers de odds e fatos.
ODDS_PROVIDERS: dict[str, type] = {
    "the_odds_api": TheOddsAPIProvider,
    "five_dollar_football": FiveDollarFootballProvider,
}

FACT_PROVIDERS: dict[str, type] = {
    "sportmonks": SportmonksProvider,
    "apifootball_com": APIFootballComProvider,
    "football_data_org": FootballDataOrgProvider,
    "statsbomb_open": StatsBombOpenProvider,
}


def status_credenciais() -> dict[str, dict[str, bool]]:
    """Status de credencial e gate por provider (NAO imprime valores)."""
    providers = [
        ("api_football", "API_KEY"),
        ("the_odds_api", "THE_ODDS_API_KEY"),
        ("five_dollar_football", "FIVE_DOLLAR_FOOTBALL_API_KEY"),
        ("sportmonks", "SPORTMONKS_API_TOKEN"),
        ("apifootball_com", "APIFOOTBALL_COM_API_KEY"),
        ("football_data_org", "FOOTBALL_DATA_API_KEY"),
        ("statsbomb_open", ""),  # sem credencial
    ]
    gate = credenciais_rotacionadas()
    out: dict[str, dict[str, bool]] = {}
    for nome, env in providers:
        cfg = (env == "") or _cred_configurada(env)  # statsbomb sempre "cfg"
        out[nome] = {
            "credencial_configurada": cfg,
            "gate_aberto": gate,
            "chamada_real_autorizada": gate and cfg and env != "",
        }
    return out