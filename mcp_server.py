import os
import requests
from concurrent.futures import ThreadPoolExecutor
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
    response = requests.get(f"{BACKEND_URL}{path}", params=params or {}, timeout=90)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def buscar_jogos_do_dia(date: str) -> dict:
    """Lista os jogos elegiveis da whitelist para uma data no formato YYYY-MM-DD."""
    return backend_get("/api/daily", {"date": date})


@mcp.tool()
def analisar_dados_da_partida(fixture: int) -> dict:
    """Executa deep dive factual e inclui historico de escanteios por tempo dos dois times."""
    result = backend_get("/api/deep-dive", {"fixture": fixture})
    facts = result.get("facts") or {}
    match = facts.get("fixture") or {}
    home_id = (match.get("home") or {}).get("id")
    away_id = (match.get("away") or {}).get("id")
    if home_id and away_id:
        with ThreadPoolExecutor(max_workers=2) as pool:
            home_future = pool.submit(backend_get, "/api/team-corner-history", {"team": home_id, "last": 25})
            away_future = pool.submit(backend_get, "/api/team-corner-history", {"team": away_id, "last": 25})
            try:
                facts["home_corner_history_25"] = home_future.result()
            except Exception as exc:
                facts["home_corner_history_25"] = {"error": str(exc)}
            try:
                facts["away_corner_history_25"] = away_future.result()
            except Exception as exc:
                facts["away_corner_history_25"] = {"error": str(exc)}
    result["facts"] = facts
    return result


@mcp.tool()
def buscar_historico_escanteios_por_tempo(team: int, last: int = 25) -> dict:
    """Busca ate 25 jogos finalizados de um time e retorna escanteios do 1o e 2o tempo diretamente da API-Football."""
    return backend_get("/api/team-corner-history", {"team": team, "last": last})


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
