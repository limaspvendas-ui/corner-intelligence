"""Conexao com a API-Football v3 (api-sports.io).

Autenticacao: header "x-apisports-key" com a chave definida no .env.
Toda resposta v3 segue o formato:
    { "get": ..., "parameters": ..., "errors": ..., "response": [...] }

Erros podem voltar com HTTP 200 (campo "errors"), por isso checamos os dois.
Regras: NUNCA expor a chave em logs ou mensagens; erros tecnicos vao para o
log, o usuario recebe mensagens simples em portugues.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from src.cache import Cache
from src.config import BASE_URL, require_api_key
from src.exceptions import (
    SourceQuotaExhaustedError,
    SourceRateLimitError,
    SourceTimeoutError,
    SourceUnavailableError,
    UserFacingError,
)
from src.logging_config import get_logger
from src import source_health

TIMEOUT_SECONDS = 30

# Headers reais da API-Football (plano PRO). NUNCA headers de autenticacao.
_QUOTA_HEADERS = {
    "limit": "x-ratelimit-requests-limit",
    "remaining": "x-ratelimit-requests-remaining",
}
# Frase exata observada no corpo do erro quando a cota diaria esgota
# (HTTP 200 + campo "errors") — condicao REAL da conta, nao bug.
_QUOTA_BODY_MARKERS = ("request limit", "reached the request limit")


class APIFootballClient:
    """Cliente HTTP da API-Football v3, com cache automatico."""

    def __init__(self, api_key: str | None = None, cache: Cache | None = None) -> None:
        self.api_key = api_key or require_api_key()
        self.cache = cache or Cache()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-apisports-key": self.api_key,  # segredo: nunca logar este header
                "accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        use_cache: bool = True,
        ttl: int | None = None,
    ) -> list[dict[str, Any]]:
        """GET em um endpoint da v3; retorna o campo "response".

        Usa cache quando valido. Levanta UserFacingError com mensagem
        simples; detalhes tecnicos ficam no log.

        ttl explicito (dados AO VIVO): a entrada e gravada e validada com
        esse TTL curto, no lugar do TTL padrao do endpoint. Com
        use_cache=False (refresh pedido pelo usuario) e ttl informado, a
        resposta fresca SOBRESCREVE o estado antigo do cache - assim a
        proxima leitura nunca recebe estado atrasado.
        """
        logger = get_logger()
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        url = f"{BASE_URL}{path}"
        safe_params = {k: v for k, v in (params or {}).items() if v is not None}

        if use_cache and self.cache is not None:
            cached = self.cache.get(path, safe_params, ttl=ttl)
            if cached is not None:
                return cached

        # Circuit breaker (Etapa 7): fonte esgotada/limitada => parar de
        # consultar ate o reset (dados em cache acima continuam validos —
        # nao sao consultas a fonte). HALF_OPEN permite a sonda de recuperacao.
        if not source_health.circuito_permite("api_football"):
            info = source_health.circuito_info("api_football")
            motivo = info.get("motivo") or ""
            msg = {
                "QUOTA_EXHAUSTED": (
                    "Cota diaria da API-Football esgotada (plano PRO "
                    "7.500/dia). As consultas estao suspensas ate o reset "
                    "diario — NAO significa ausencia de jogos."),
                "RATE_LIMIT": (
                    "Limite de requisicoes da API atingido. Aguarde um "
                    "minuto e tente novamente."),
                "TIMEOUT": (
                    "A API-Football esta temporariamente suspensa por "
                    "respostas demoradas. Tente novamente em instantes."),
            }.get(motivo, (
                "A API-Football esta temporariamente indisponivel "
                f"(motivo: {motivo or 'tecnico'})."))
            exc_cls = {
                "QUOTA_EXHAUSTED": SourceQuotaExhaustedError,
                "RATE_LIMIT": SourceRateLimitError,
                "TIMEOUT": SourceTimeoutError,
            }.get(motivo, SourceUnavailableError)
            logger.warning(
                "Circuit breaker ABERTO para api_football (%s): %s pulado",
                motivo, path)
            raise exc_cls(msg, motivo=motivo or "CIRCUIT_BREAKER_ABERTO")

        try:
            http = self.session.get(url, params=safe_params, timeout=TIMEOUT_SECONDS)
            http.raise_for_status()
            data = http.json()
        except requests.exceptions.Timeout:
            logger.exception("Timeout ao chamar %s", path)
            source_health.registrar_requisicao("api_football", path, "TIMEOUT")
            raise SourceTimeoutError(
                "A API-Football demorou demais para responder. Tente novamente."
            ) from None
        except requests.exceptions.ConnectionError:
            logger.exception("Falha de rede ao chamar %s", path)
            source_health.registrar_requisicao("api_football", path, "CONNECTION_ERROR")
            raise SourceUnavailableError(
                "Falha de conexao com a API-Football. Verifique sua internet.",
                motivo="CONNECTION_ERROR",
            ) from None
        except requests.exceptions.HTTPError:
            status = http.status_code
            # Nao logamos headers completos para nunca vazar a chave
            logger.error("HTTP %s ao chamar %s", status, path)
            source_health.registrar_requisicao(
                "api_football", path, f"HTTP_{status}")
            if status in (401, 403):
                raise SourceUnavailableError(
                    "Acesso negado pela API-Football. Verifique sua API_KEY no .env "
                    "e se sua assinatura Pro esta ativa.",
                    motivo="CREDENCIAL",
                ) from None
            if status == 429:
                raise SourceRateLimitError(
                    "Limite de requisicoes da API atingido. Aguarde um minuto "
                    "e tente novamente.",
                    motivo="RATE_LIMIT",
                ) from None
            if status in (502, 503, 504):
                # Indisponibilidade tecnica confirmada do provedor: gatilho
                # de failover (nunca "zero jogos"; nunca erro generico).
                raise SourceUnavailableError(
                    f"A API-Football retornou o erro HTTP {status}. "
                    "Consulte logs/corner_intelligence.log para detalhes.",
                    motivo="HTTP_5XX",
                ) from None
            raise UserFacingError(
                f"A API-Football retornou o erro HTTP {status}. "
                "Consulte logs/corner_intelligence.log para detalhes."
            ) from None
        except json.JSONDecodeError:
            logger.exception("Resposta nao-JSON de %s", path)
            source_health.registrar_requisicao("api_football", path, "JSON_INVALIDO")
            raise SourceUnavailableError(
                "A API-Football retornou uma resposta inesperada. Tente novamente.",
                motivo="RESPOSTA_INVALIDA",
            ) from None

        # Contadores de cota do plano (headers reais; NUNCA credenciais).
        # Tolerante a resposta sem headers (tests/objetos fake): a cota
        # e um bonus operacional, nunca um requisito da coleta.
        try:
            headers = getattr(http, "headers", None)
            if headers is not None:
                limit = headers.get(_QUOTA_HEADERS["limit"])
                remaining = headers.get(_QUOTA_HEADERS["remaining"])
                source_health.registrar_quota_api_football(
                    int(limit) if limit is not None else None,
                    int(remaining) if remaining is not None else None,
                )
        except (ValueError, TypeError):
            pass

        # Erros da API podem vir com HTTP 200
        errors = data.get("errors")
        if errors:
            pretty = json.dumps(errors, ensure_ascii=False)[:300]
            logger.error("Erros da API em %s: %s", path, pretty)
            texto = json.dumps(errors, ensure_ascii=False).lower()
            if any(mk in texto for mk in _QUOTA_BODY_MARKERS):
                # Cota diaria REAL da conta esgotada (condicao da fonte):
                # erro estruturado para o router/circuit breaker — nunca
                # tratado como ausencia de jogos ou zero oportunidades.
                source_health.registrar_requisicao(
                    "api_football", path, "QUOTA_EXHAUSTED")
                raise SourceQuotaExhaustedError(
                    f"Erro da API-Football em {path}: {pretty}",
                    motivo="QUOTA_EXHAUSTED",
                ) from None
            source_health.registrar_requisicao("api_football", path, "ERRO_API")
            raise UserFacingError(f"Erro da API-Football em {path}: {pretty}")

        source_health.registrar_requisicao("api_football", path, "OK")
        # Resposta valida da fonte: circuit breaker fecha (recuperacao —
        # ex.: cota renovada no reset diario; a primaria volta a ser usada).
        source_health.circuito_fechar("api_football")
        response = data.get("response", [])
        if self.cache is not None and (use_cache or ttl is not None):
            self.cache.set(path, safe_params, response, ttl=ttl)
        return response