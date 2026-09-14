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
from src.exceptions import UserFacingError
from src.logging_config import get_logger

TIMEOUT_SECONDS = 30


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

        try:
            http = self.session.get(url, params=safe_params, timeout=TIMEOUT_SECONDS)
            http.raise_for_status()
            data = http.json()
        except requests.exceptions.Timeout:
            logger.exception("Timeout ao chamar %s", path)
            raise UserFacingError(
                "A API-Football demorou demais para responder. Tente novamente."
            ) from None
        except requests.exceptions.ConnectionError:
            logger.exception("Falha de rede ao chamar %s", path)
            raise UserFacingError(
                "Falha de conexao com a API-Football. Verifique sua internet."
            ) from None
        except requests.exceptions.HTTPError:
            status = http.status_code
            # Nao logamos headers completos para nunca vazar a chave
            logger.error("HTTP %s ao chamar %s", status, path)
            if status in (401, 403):
                raise UserFacingError(
                    "Acesso negado pela API-Football. Verifique sua API_KEY no .env "
                    "e se sua assinatura Pro esta ativa."
                ) from None
            if status == 429:
                raise UserFacingError(
                    "Limite de requisicoes da API atingido. Aguarde um minuto "
                    "e tente novamente."
                ) from None
            raise UserFacingError(
                f"A API-Football retornou o erro HTTP {status}. "
                "Consulte logs/corner_intelligence.log para detalhes."
            ) from None
        except json.JSONDecodeError:
            logger.exception("Resposta nao-JSON de %s", path)
            raise UserFacingError(
                "A API-Football retornou uma resposta inesperada. Tente novamente."
            ) from None

        # Erros da API podem vir com HTTP 200
        errors = data.get("errors")
        if errors:
            pretty = json.dumps(errors, ensure_ascii=False)[:300]
            logger.error("Erros da API em %s: %s", path, pretty)
            raise UserFacingError(f"Erro da API-Football em {path}: {pretty}")

        response = data.get("response", [])
        if self.cache is not None and (use_cache or ttl is not None):
            self.cache.set(path, safe_params, response, ttl=ttl)
        return response