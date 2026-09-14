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

    def __init__(self) -> None:
        self._calls = 0

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def fetch_odds(self, sport_key: str = "soccer_epl") -> list[NormalizedOdd]:
        """Busca odds pre-match. Implementacao real requer gate aberto."""
        self._guard()
        # Endpoint confirmado por documentacao. Chamada real fica pendente
        # ate o gate abrir; o parser abaixo segue o shape documentado.
        raise CredentialsBlocked(
            f"CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (the_odds_api).")


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
    # Endpoints NAO confirmados neste etapa => A CONFIRMAR (nao inventados).
    ENDPOINTS_CONFIRMADOS: dict[str, str] = {}

    def __init__(self) -> None:
        self._calls = 0

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def fetch_odds(self, *args, **kw) -> list[NormalizedOdd]:
        self._guard()
        # Endpoints A CONFIRMAR: nao inventa implementacao.
        raise EndpointNotConfirmed(
            f"five_dollar_football: endpoints A CONFIRMAR (documentacao/"
            "resposta real nao comprovados nesta etapa).")


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
    BASE_URL = "https://api.sportmonks.com/v3"

    def __init__(self) -> None:
        self._calls = 0

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def fetch_competitions(self) -> list[dict]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (sportmonks).")

    def fetch_match(self, match_id: str) -> list[NormalizedFact]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (sportmonks).")

    def fetch_statistics(self, match_id: str) -> list[NormalizedFact]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (sportmonks).")

    def fetch_events(self, match_id: str) -> list[NormalizedFact]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (sportmonks).")


class APIFootballComProvider:
    """Adapter APIFootball.com (apifootball.com).

    IMPORTANTE: NAO confundir com API-Football / API-Sports (api-sports.io).
    Provider canonico: apifootball_com (distinto de api_football em todo
    codigo, banco, relatorio e logs).

    DOCUMENTADO vs OBSERVADO: A CONFIRMAR. Endpoints publicados incluem
    ?action=get_fixtures, ?action=get_events, ?action=get_statistics,
    ?action=get_H2H, com APIkey=... . Confirmacao real exige chamada
    autenticada (gate fechado => NAO TESTADA).
    """
    provider_name = "apifootball_com"
    CRED_ENV = "APIFOOTBALL_COM_API_KEY"
    HARD_LIMIT = 15
    BASE_URL = "https://apifootball.com/api"

    def __init__(self) -> None:
        self._calls = 0

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def fetch_competitions(self) -> list[dict]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (apifootball_com).")

    def fetch_match(self, match_id: str) -> list[NormalizedFact]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (apifootball_com).")

    def fetch_statistics(self, match_id: str) -> list[NormalizedFact]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (apifootball_com).")

    def fetch_events(self, match_id: str) -> list[NormalizedFact]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (apifootball_com).")


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

    def __init__(self) -> None:
        self._calls = 0

    def is_available(self) -> bool:
        return credenciais_rotacionadas() and _cred_configurada(self.CRED_ENV)

    def _guard(self) -> None:
        _checar_gate(self.provider_name, self.CRED_ENV)
        if self._calls >= self.HARD_LIMIT:
            raise RateLimitHit(f"{self.provider_name}: HARD_LIMIT atingido")

    def fetch_competitions(self) -> list[dict]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (football_data_org).")

    def fetch_match(self, match_id: str) -> list[NormalizedFact]:
        self._guard()
        raise CredentialsBlocked("CHAMADAS REAIS BLOQUEADAS POR SEGURANCA (football_data_org).")

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