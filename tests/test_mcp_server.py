"""Testes do servidor MCP oficial (src/mcp_server.py — camada de acesso).

Princípios testados:
    1. O contrato oficial: EXATAMENTE 9 ferramentas, com os nomes oficiais
       (nenhuma removida, nenhuma extra).
    2. O MCP é CAMADA DE ACESSO: cada ferramenta delega à função oficial de
       src/api_server.py (SaidaOficial) — nenhum cálculo estatístico vive
       no MCP (delegação pura é verificada por stub).
    3. O endpoint /mcp está montado no MESMO serviço da API oficial
       (tools/list e tools/call respondem pelo HTTP).
"""
from __future__ import annotations

import asyncio

import pytest

mcp = pytest.importorskip("mcp")  # pacote opcional (requirements fixa <2)

from fastapi.testclient import TestClient  # noqa: E402

from src import mcp_server  # noqa: E402
from src.api_server import app  # noqa: E402

JSON_RPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


# ----------------------------------------------------------------------
# 1. Contrato: exatamente 9 ferramentas oficiais
# ----------------------------------------------------------------------
def test_exatamente_9_ferramentas_registradas():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    nomes = sorted(t.name for t in tools)
    assert nomes == sorted(mcp_server.FERRAMENTAS_MCP)
    assert len(nomes) == 9
    assert len(set(nomes)) == 9  # sem duplicidade


def test_nomes_oficiais_das_9_ferramentas():
    # Contrato fixo: nenhuma ferramenta pode ser removida.
    assert mcp_server.FERRAMENTAS_MCP == (
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


# ----------------------------------------------------------------------
# 2. Delegação pura: cada ferramenta chama a função oficial da API
#    (nenhum cálculo estatístico no MCP)
# ----------------------------------------------------------------------
DELEGACOES = [
    ("verificar_status", "verificar_status", {}),
    ("buscar_jogos_do_dia", "buscar_jogos_do_dia", {"date": "2026-09-15"}),
    ("varredura_prelive", "varredura_prelive", {"date": "2026-09-15"}),
    ("analisar_partida_prelive", "analisar_partida_prelive", {"fixture_id": 1}),
    ("varredura_live", "varredura_live_endpoint", {}),
    ("analisar_partida_live", "analisar_partida_live", {"fixture_id": 1}),
    ("obter_status_mercados", "obter_status_mercados", {}),
    ("analisar_dados_da_partida", "analisar_dados_da_partida",
     {"fixture_id": 1, "historico_last": 5}),
    ("testar_coleta_automatica", "testar_coleta_automatica",
     {"date": "2026-09-15"}),
]


@pytest.mark.parametrize("ferramenta,funcao_api,args", DELEGACOES)
def test_ferramenta_delega_para_api_oficial(monkeypatch, ferramenta,
                                            funcao_api, args):
    chamado = {}

    def _stub(*a, **kw):
        chamado["__executado__"] = True
        return {"marker": f"delegado:{ferramenta}"}

    monkeypatch.setattr(f"src.api_server.{funcao_api}", _stub)
    funcao_mcp = getattr(mcp_server, ferramenta)
    resultado = funcao_mcp(**args)
    # A ferramenta MCP chamou a função oficial da API (delegação pura)...
    assert chamado["__executado__"] is True
    # ...e devolveu EXATAMENTE o que a camada oficial produziu
    # (nenhum recálculo/invenção no MCP).
    assert resultado == {"marker": f"delegado:{ferramenta}"}


# ----------------------------------------------------------------------
# 3. Montagem no serviço oficial: /mcp responde no MESMO app
# ----------------------------------------------------------------------
# Nota: o StreamableHTTPSessionManager do mcp 1.x só aceita run() UMA vez
# por instância ("Create a new instance if you need to restart" — doc
# oficial). Em produção há exatamente um lifespan por processo; nos
# testes, TODOS os testes HTTP do módulo compartilham este ÚNICO client
# (fixture de escopo de módulo) para não reentrar na lifespan.
@pytest.fixture(scope="module")
def mcp_client():
    with TestClient(app) as c:  # context manager: roda a lifespan (MCP)
        yield c


def test_mcp_montado_tools_list_e_tools_call(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "src.api_server.verificar_status",
        lambda **kw: {"marker": "delegado:http", "backend_online": True})
    c = mcp_client
    # tools/list: exatamente 9 ferramentas
    r = c.post("/mcp", headers=JSON_RPC_HEADERS, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    nomes = sorted(t["name"] for t in body["result"]["tools"])
    assert nomes == sorted(mcp_server.FERRAMENTAS_MCP)
    assert len(nomes) == 9

    # tools/call: delega à camada oficial (marker do stub vem na resposta)
    r2 = c.post("/mcp", headers=JSON_RPC_HEADERS, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "verificar_status", "arguments": {}}})
    assert r2.status_code == 200
    assert "error" not in r2.json()
    assert "delegado:http" in r2.text


def test_api_continua_funcionando_com_mcp_montado(mcp_client):
    # Rotas oficiais da API continuam no ar no MESMO serviço (mount em "/"
    # no final; /health tem precedência sobre o catch-all do MCP).
    r = mcp_client.get("/health")
    assert r.status_code == 200
    assert r.json()["data"]["backend_online"] is True