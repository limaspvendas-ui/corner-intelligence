"""Servidor MCP oficial do Corner Intelligence (camada de acesso).

Ferramentas somente de leitura para o ChatGPT/agentes externos. O MCP
NÃO é um motor: cada ferramenta delega à camada oficial existente
(src/api_server.py -> SaidaOficial -> motor), sem recalcular NENHUMA
probabilidade, confiança, linha ou decisão. NULL permanece NULL. Nenhuma
aposta é executada. Nenhum cálculo estatístico vive aqui.

Fluxo preservado:

    FONTES REAIS (API-Football v3 + provedores multifonte)
        ↓
    BACKEND CORNER INTELLIGENCE (src/)
        ↓
    MOTOR OFICIAL (prejogo_opportunity / live_opportunity)
        ↓
    SaidaOficial (src/operacional.py)
        ↓
    API HTTP (src/api_server.py)  ← camada fina de acesso
        ↓
    MCP (este módulo)             ← camada fina de acesso (delega à API)
        ↓
    CHATGPT (consulta/apresenta; não recalcula)

As 9 ferramentas (nenhuma pode ser removida):
    1. verificar_status
    2. buscar_jogos_do_dia
    3. varredura_prelive
    4. analisar_partida_prelive
    5. varredura_live
    6. analisar_partida_live
    7. obter_status_mercados
    8. analisar_dados_da_partida   (factual — deep dive sem probabilidade)
    9. testar_coleta_automatica    (factual — validação da coleta)

Execução standalone:
    python -m src.mcp_server   (streamable-http; PORT env, default 10001)

Ou montado no serviço da API oficial (src/api_server.py expõe /mcp).
"""
from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

MCP_VERSAO = "mcp-1.0-oficial"

PORT = int(os.getenv("MCP_PORT", os.getenv("PORT", "10001")))


def _api():
    """Acesso TARDIO à camada oficial (src/api_server.py).

    Importar api_server aqui (e não no topo do módulo) quebra o ciclo de
    import: api_server.py monta este módulo em /mcp no final do arquivo,
    e importá-lo durante a carga deste módulo deixava este parcialmente
    inicializado quando _montar_mcp() o reimportava — o mount era
    silenciosamente ignorado (404 em /mcp). Com o acesso tardio, o mount
    é robusto a QUALQUER ordem de import.
    """
    from src import api_server
    return api_server


mcp = FastMCP(
    "Corner Intelligence",
    instructions=(
        "Ferramentas somente de leitura do Corner Intelligence. Todas as "
        "análises vêm do motor oficial via SaidaOficial (varredura/prelive/"
        "live). Não invente probabilidades, apostas ou dados ausentes. "
        "NULL permanece NULL. Prioridade: PRECISÃO > QUANTIDADE. Cartões "
        "e live estão em MODO TESTE (override do usuário, NÃO validados "
        "estatisticamente) — nunca os apresente como aprovados."
    ),
    host="0.0.0.0",
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


# ----------------------------------------------------------------------
# 1. verificar_status
# ----------------------------------------------------------------------
@mcp.tool()
def verificar_status() -> dict[str, Any]:
    """Verifica se o backend do Corner Intelligence está online, a versão
    do motor/camada, o banco e o status dos mercados (pré-live + live)."""
    return _api().verificar_status()


# ----------------------------------------------------------------------
# 2. buscar_jogos_do_dia
# ----------------------------------------------------------------------
@mcp.tool()
def buscar_jogos_do_dia(date: str | None = None) -> dict[str, Any]:
    """Lista os jogos ELEGÍVEIS do universo oficial para uma data
    (YYYY-MM-DD; default: hoje). SEM probabilidade — apenas identificação."""
    return _api().buscar_jogos_do_dia(date)


# ----------------------------------------------------------------------
# 3. varredura_prelive
# ----------------------------------------------------------------------
@mcp.tool()
def varredura_prelive(date: str | None = None) -> dict[str, Any]:
    """Varredura oficial PRÉ-LIVE da data (motor + SaidaOficial): gols,
    escanteios, cartões (MODO TESTE) e resultado (observação). Ausência de
    oportunidade é resultado válido."""
    return _api().varredura_prelive(date)


# ----------------------------------------------------------------------
# 4. analisar_partida_prelive
# ----------------------------------------------------------------------
@mcp.tool()
def analisar_partida_prelive(fixture_id: int) -> dict[str, Any]:
    """Saída oficial canônica de UMA partida pré-live (por fixture_id):
    oportunidades operacionais, observações e bloqueados do motor oficial."""
    return _api().analisar_partida_prelive(fixture_id)


# ----------------------------------------------------------------------
# 5. varredura_live
# ----------------------------------------------------------------------
@mcp.tool()
def varredura_live() -> dict[str, Any]:
    """Varredura oficial LIVE (MODO TESTE por override do usuário — NÃO
    validada estatisticamente) dos jogos ao vivo agora, pelo motor live."""
    return _api().varredura_live_endpoint()


# ----------------------------------------------------------------------
# 6. analisar_partida_live
# ----------------------------------------------------------------------
@mcp.tool()
def analisar_partida_live(fixture_id: int) -> dict[str, Any]:
    """Snapshot factual + sinais do motor live para UMA partida ao vivo
    (MODO TESTE). Se não houver sinal, probabilidade/decisão ficam vazias
    — nada é inventado."""
    return _api().analisar_partida_live(fixture_id)


# ----------------------------------------------------------------------
# 7. obter_status_mercados
# ----------------------------------------------------------------------
@mcp.tool()
def obter_status_mercados() -> dict[str, Any]:
    """Status oficial de TODOS os mercados: estatístico E operacional
    (GOALS aprovado; CORNERS por override; CARDS em MODO TESTE; LIVE em
    modo teste; ROI bloqueado)."""
    return _api().obter_status_mercados()


# ----------------------------------------------------------------------
# 8. analisar_dados_da_partida
# ----------------------------------------------------------------------
@mcp.tool()
def analisar_dados_da_partida(
    fixture_id: int, historico_last: int = 20,
) -> dict[str, Any]:
    """Deep dive FACTUAL de uma partida: fixture, estatísticas (jogo
    completo + 1º/2º tempo quando a fonte fornece) e histórico recente
    dos dois times. SOMENTE dados da API — sem probabilidade ou decisão."""
    return _api().analisar_dados_da_partida(fixture_id, historico_last)


# ----------------------------------------------------------------------
# 9. testar_coleta_automatica
# ----------------------------------------------------------------------
@mcp.tool()
def testar_coleta_automatica(date: str | None = None) -> dict[str, Any]:
    """Escolhe automaticamente uma partida elegível da data e valida a
    coleta FACTUAL (fixture + estatísticas + histórico). Ausência de dado
    é reportada como 'dado não disponível na fonte' — nunca inventada."""
    return _api().testar_coleta_automatica(date)


# Contrato oficial: EXATAMENTE estas 9 ferramentas (nenhuma removida).
FERRAMENTAS_MCP: tuple[str, ...] = (
    "verificar_status",
    "buscar_jogos_do_dia",
    "varredura_prelive",
    "analisar_partida_prelive",
    "varredura_live",
    "analisar_partida_live",
    "obter_status_mercados",
    "analisar_dados_da_partida",
    "testar_coleta_automatica",
)


def _run() -> None:  # pragma: no cover
    """Executa o servidor MCP standalone (streamable-http)."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":  # pragma: no cover
    _run()