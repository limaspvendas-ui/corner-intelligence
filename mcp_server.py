import os
import requests
from mcp.server.fastmcp import FastMCP

BACKEND_URL = os.getenv("CORNER_BACKEND_URL", "https://corner-intelligence.onrender.com")
PORT = int(os.getenv("PORT", "10000"))

mcp = FastMCP(
    "Corner Intelligence",
    instructions=(
        "Ferramentas somente de leitura para buscar jogos elegiveis e coletar dados factuais. "
        "Nao invente probabilidades, apostas ou dados ausentes. Prioridade: PRECISAO > QUANTIDADE."
    ),
    host="0.0.0.0",
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


def backend_get(path: str, params: dict | None = None) -> dict:
    response = requests.get(f"{BACKEND_URL}{path}", params=params or {}, timeout=45)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def buscar_jogos_do_dia(date: str) -> dict:
    """Lista os jogos elegiveis da whitelist para uma data no formato YYYY-MM-DD."""
    return backend_get("/api/daily", {"date": date})


@mcp.tool()
def analisar_dados_da_partida(fixture: int) -> dict:
    """Executa deep dive factual de uma partida por fixture_id, sem calcular probabilidades ou apostas."""
    return backend_get("/api/deep-dive", {"fixture": fixture})


@mcp.tool()
def testar_coleta_automatica(date: str) -> dict:
    """Escolhe automaticamente uma partida elegivel da data e valida a coleta factual."""
    return backend_get("/api/test-data", {"date": date})


@mcp.tool()
def verificar_status() -> dict:
    """Verifica se o backend do Corner Intelligence esta online e com a API-Football configurada."""
    return backend_get("/health")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
