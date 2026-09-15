"""Servidor MCP oficial do Corner Intelligence.

Camada fina sobre a API HTTP canônica (src/api_server.py). Este servidor NÃO
recalcula probabilidades, NÃO altera decisões e NÃO executa apostas. Ele
apenas expõe ao ChatGPT as mesmas respostas produzidas pela SaidaOficial.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

BACKEND_URL = os.getenv(
    "CORNER_BACKEND_URL",
    "https://corner-intelligence-api.onrender.com",
).rstrip("/")
PORT = int(os.getenv("PORT", "10000"))

mcp = FastMCP(
    "Corner Intelligence",
    instructions=(
        "Ferramentas somente de leitura ligadas ao motor oficial do Corner Intelligence. "
        "Nunca invente probabilidade, linha, confiança, placar ou dado ausente. "
        "NULL permanece NULL. O ChatGPT deve apresentar a decisão oficial sem alterá-la."
    ),
    host="0.0.0.0",
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET resiliente para atravessar cold start do Render Free."""
    url = f"{BACKEND_URL}{path}"
    retry_delays = (0, 5, 10, 15, 20, 20)
    last_error: Exception | None = None

    for attempt, delay in enumerate(retry_delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            response = requests.get(url, params=params or {}, timeout=90)
            if response.status_code in {502, 503, 504} and attempt < len(retry_delays):
                last_error = requests.HTTPError(
                    f"Backend temporariamente indisponivel: HTTP {response.status_code}",
                    response=response,
                )
                continue
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
            if attempt >= len(retry_delays):
                raise
        except requests.HTTPError:
            raise

    if last_error:
        raise last_error
    raise RuntimeError("Falha inesperada ao consultar o Corner Intelligence.")


@mcp.tool()
def verificar_status() -> dict[str, Any]:
    """Verifica backend, versões, mercados, banco e status do LIVE."""
    return _get("/health")


@mcp.tool()
def buscar_jogos_do_dia(date: str) -> dict[str, Any]:
    """Lista fixtures elegíveis da data YYYY-MM-DD, sem inventar probabilidade."""
    return _get("/api/jogos-do-dia", {"date": date})


@mcp.tool()
def varredura_prelive(date: str) -> dict[str, Any]:
    """Executa a varredura pré-live oficial para a data YYYY-MM-DD."""
    return _get("/api/varredura-prelive", {"date": date})


@mcp.tool()
def analisar_partida_prelive(fixture: int) -> dict[str, Any]:
    """Retorna a SaidaOficial pré-live de uma partida por fixture_id."""
    return _get("/api/partida-prelive", {"fixture_id": fixture})


@mcp.tool()
def varredura_live() -> dict[str, Any]:
    """Executa a varredura LIVE oficial em modo teste, somente leitura."""
    return _get("/api/varredura-live")


@mcp.tool()
def analisar_partida_live(fixture: int) -> dict[str, Any]:
    """Retorna snapshot e lógica LIVE oficial para uma partida por fixture_id."""
    return _get("/api/partida-live", {"fixture_id": fixture})


@mcp.tool()
def obter_status_mercados() -> dict[str, Any]:
    """Retorna status estatístico e operacional de todos os mercados."""
    return _get("/api/status-mercados")


# Compatibilidade com o app/connector anterior.
@mcp.tool()
def analisar_dados_da_partida(fixture: int) -> dict[str, Any]:
    """Compatibilidade: usa a análise oficial pré-live da fixture."""
    return analisar_partida_prelive(fixture)


@mcp.tool()
def testar_coleta_automatica(date: str) -> dict[str, Any]:
    """Compatibilidade: escolhe a primeira fixture elegível e valida a análise oficial."""
    jogos = buscar_jogos_do_dia(date)
    data = jogos.get("data") or {}
    elegiveis = data.get("elegiveis") or []
    if not elegiveis:
        return {
            "success": True,
            "date": date,
            "message": "NENHUM JOGO ELEGIVEL DISPONIVEL PARA TESTE",
            "fixture": None,
        }
    fixture_id = elegiveis[0].get("fixture_id")
    if not fixture_id:
        return {
            "success": False,
            "date": date,
            "message": "Fixture elegivel sem fixture_id valido",
        }
    return analisar_partida_prelive(int(fixture_id))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
