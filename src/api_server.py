"""Servidor API oficial do Corner Intelligence (camada fina sobre o motor).

Integração ChatGPT via HTTP/OpenAPI. NÃO é um segundo motor: cada endpoint
consome a SaidaOficial canônica já existente (src/operacional.py) e apenas
serializa o resultado em JSON. Nenhuma probabilidade/confiança/linha é
recalculada aqui; nenhum ENTRAR/NÃO-ENTRAR é alterado; nenhuma aposta é
executada. NULL permanece NULL (nunca vira zero).

Arquitetura:

    FONTES REAIS (API-Football v3)
        ↓
    BACKEND CORNER INTELLIGENCE (src/)
        ↓
    MOTOR OFICIAL (prejogo_opportunity / live_opportunity)
        ↓
    SaidaOficial (src/operacional.py)
        ↓
    API HTTP (este módulo)  ← camada fina de acesso
        ↓
    CHATGPT (consulta/apresenta; não recalcula)

Execução local:
    uvicorn src.api_server:app --host 0.0.0.0 --port 10000
    python -m src.api_server   (atalho via __main__)

Deploy (Render): ver docs/INTEGRACAO_CHATGPT_FINAL.md + render.yaml.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from src.operacional import (
    MODO_TESTE_LIVE,
    OVERRIDE_OPERACIONAL,
    STATUS_MERCADOS,
    VERSAO_CAMADA_LIVE,
    VERSAO_CAMADA_OPERACIONAL,
    VERSAO_LIVE_OP,
    _projeto_hash,
    analisar_fixture,
    jogo_elegivel,
    varredura_data,
    varredura_live,
)
from src.prejogo_opportunity import VERSAO_PREJOGO_OP

PROVIDER = "API-Football v3 (api-sports.io)"
API_VERSAO = "api-1.0-chatgpt"

app = FastAPI(
    title="Corner Intelligence — API Oficial",
    description=(
        "Camada fina de acesso à SaidaOficial do motor Corner Intelligence. "
        "Consulta somente leitura (pré-live + live modo teste). Nenhum "
        "endpoint recalcula probabilidade, altera decisão do motor ou "
        "executa aposta. NULL permanece NULL."
    ),
    version=API_VERSAO,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _client():
    """Cria APIFootballClient sob demanda (exige API_KEY só ao usar)."""
    from src.api_client import APIFootballClient
    from src.config import require_api_key

    require_api_key()
    return APIFootballClient()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _envelope(
    success: bool, mode: str, data: Any, *,
    engine_version: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Contrato padrão de resposta (Passo 5). NULL permanece NULL."""
    body: dict[str, Any] = {
        "success": success,
        "generated_at": _now(),
        "project_hash": _projeto_hash(),
        "engine_version": engine_version,
        "mode": mode,
        "provider": PROVIDER,
        "api_version": API_VERSAO,
        "data": data,
    }
    if extra:
        body.update(extra)
    return body


def _db_status() -> dict[str, Any]:
    """Status do banco (somente leitura; sem segredos)."""
    try:
        import sqlite3
        c = sqlite3.connect("data/corner_intelligence.db")
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        user_version = c.execute("PRAGMA user_version").fetchone()[0]
        c.close()
        return {"ok": True, "integrity_check": integrity,
                "user_version": user_version}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "erro": f"{type(e).__name__}: {e}"}


def _status_mercados_serializado() -> list[dict[str, Any]]:
    """Serializa STATUS_MERCADOS + OVERRIDE + origem operacional por mercado."""
    out = []
    for mk, info in STATUS_MERCADOS.items():
        ov = OVERRIDE_OPERACIONAL.get(mk)
        entry = {
            "mercado": mk,
            "status_estatistico": info["status"],
            "motivo_status": info["motivo"],
            "status_operacional": (
                ov["status_operacional"] if ov else None
            ),
            "origem_operacional": (
                ov["origem"] if ov else None
            ),
            "override_operador": ("true" if ov else "false"),
            "motivo_override": (ov["motivo"] if ov else None),
            "timestamp_override": (ov["timestamp_override"] if ov else None),
        }
        out.append(entry)
    return out


# ----------------------------------------------------------------------
# 1. Health / verificar_status
# ----------------------------------------------------------------------
@app.get("/health")
@app.get("/api/status")
def verificar_status() -> dict[str, Any]:
    """Status do backend: versão, hash, banco, pré-live, live, modo teste."""
    live_block = {
        "status_estatistico": MODO_TESTE_LIVE["status_estatistico"],
        "status_operacional": MODO_TESTE_LIVE["status_operacional"],
        "modo_teste_live": MODO_TESTE_LIVE["modo_teste"],
        "override_operador": MODO_TESTE_LIVE["override_operador"],
        "decisao_humana": MODO_TESTE_LIVE["decisao_humana"],
        "timestamp_override": MODO_TESTE_LIVE["timestamp_override"],
        "engine_version_live": VERSAO_LIVE_OP,
        "camada_version_live": VERSAO_CAMADA_LIVE,
    }
    prelive_block = {
        "engine_version": VERSAO_PREJOGO_OP,
        "camada_version": VERSAO_CAMADA_OPERACIONAL,
        "goals": {"status": STATUS_MERCADOS["gols"]["status"],
                  "origem": "estatistico"},
        "corners": {"status": STATUS_MERCADOS["escanteios"]["status"],
                    "origem": "override_usuario"},
    }
    data = {
        "backend_online": True,
        "api_version": API_VERSAO,
        "project_hash": _projeto_hash(),
        "engine_version_prelive": VERSAO_PREJOGO_OP,
        "engine_version_live": VERSAO_LIVE_OP,
        "database": _db_status(),
        "prelive": prelive_block,
        "live": live_block,
        "modo_teste_live": MODO_TESTE_LIVE["modo_teste"] == "true",
    }
    return _envelope(True, "status", data,
                     engine_version=VERSAO_PREJOGO_OP)


# ----------------------------------------------------------------------
# 2. buscar_jogos_do_dia (fixtures elegíveis; SEM probabilidade)
# ----------------------------------------------------------------------
@app.get("/api/jogos-do-dia")
def buscar_jogos_do_dia(
    date: str | None = Query(None, description="YYYY-MM-DD (default: hoje)"),
) -> dict[str, Any]:
    """Lista somente fixtures ELEGÍVEIS do universo oficial para a data.
    NÃO calcula probabilidade — apenas identifica jogos elegíveis."""
    try:
        client = _client()
    except Exception as e:
        return _envelope(False, "prelive", None,
                         extra={"erro": f"{type(e).__name__}: {e}"})
    from src.fixtures import get_fixtures_today

    fixtures = get_fixtures_today(client, on_date=date)
    elegiveis: list[dict[str, Any]] = []
    inelegiveis: list[dict[str, Any]] = []
    for fx in fixtures:
        if fx.is_finished or fx.is_live:
            continue
        ok, motivo = jogo_elegivel(
            fx.league_id, fx.league_name,
            fx.home_team_name, fx.away_team_name,
        )
        row = {
            "fixture_id": fx.fixture_id,
            "home": fx.home_team_name,
            "away": fx.away_team_name,
            "competition": fx.league_name,
            "league_id": fx.league_id,
            "kickoff": fx.date,
            "round": fx.round,
            "season": fx.season,
        }
        if ok:
            elegiveis.append(row)
        else:
            inelegiveis.append({**row, "motivo_inelegivel": motivo})
    data = {
        "date": date or "hoje",
        "fixtures_considerados": len(fixtures),
        "fixtures_elegiveis": len(elegiveis),
        "elegiveis": elegiveis,
        "inelegiveis": inelegiveis,
        "nota": ("Sem probabilidade nesta chamada — use "
                 "/api/varredura-prelive ou /api/partida-prelive."),
    }
    return _envelope(True, "prelive", data, engine_version=VERSAO_PREJOGO_OP)


# ----------------------------------------------------------------------
# 3. varredura_prelive (saída oficial agregada por data)
# ----------------------------------------------------------------------
@app.get("/api/varredura-prelive")
def varredura_prelive(
    date: str | None = Query(None, description="YYYY-MM-DD (default: hoje)"),
) -> dict[str, Any]:
    """Executa a varredura oficial pré-live (motor + SaidaOficial)."""
    try:
        client = _client()
    except Exception as e:
        return _envelope(False, "prelive", None,
                         extra={"erro": f"{type(e).__name__}: {e}"})
    res = varredura_data(client, date)
    data = {
        "date": res["data"],
        "fixtures_considerados": res["fixtures_considerados"],
        "fixtures_elegiveis": res["fixtures_elegiveis"],
        "fixtures_inelegiveis": res["fixtures_inelegiveis"],
        "oportunidades_goals": [
            o for o in res["operational_opportunities"]
            if o.get("mercado") == "gols"],
        "oportunidades_corners": [
            o for o in res["operational_opportunities"]
            if o.get("mercado") == "escanteios"],
        "observacoes": res["observations"],
        "nao_avaliaveis": [b for b in res["blocked"]
                           if b.get("decisao_oficial") == "NAO_AVALIAVEL"],
        "bloqueados": [b for b in res["blocked"]
                       if b.get("decisao_oficial") == "BLOQUEADO"],
        "nenhum_aprovado": res["nenhum_aprovado"],
        "mensagem_nenhum": res["mensagem_nenhum"],
        "saida_oficial_completa": res,
    }
    return _envelope(True, "prelive", data, engine_version=VERSAO_PREJOGO_OP)


# ----------------------------------------------------------------------
# 4. analisar_partida_prelive (um fixture por fixture_id)
# ----------------------------------------------------------------------
@app.get("/api/partida-prelive")
def analisar_partida_prelive(
    fixture_id: int = Query(..., description="ID da partida"),
) -> dict[str, Any]:
    """Saída oficial canônica de UM fixture (por fixture_id)."""
    try:
        client = _client()
    except Exception as e:
        return _envelope(False, "prelive", None,
                         extra={"erro": f"{type(e).__name__}: {e}"})
    from src.fixtures import get_fixture_by_id

    fx = get_fixture_by_id(client, fixture_id)
    if fx is None:
        return _envelope(
            False, "prelive", None,
            extra={"erro": "fixture não encontrado",
                   "fixture_id": fixture_id})
    if fx.is_finished or fx.is_live:
        return _envelope(
            False, "prelive",
            {"fixture_id": fixture_id, "status": fx.status,
             "elegivel_pre": False},
            extra={"erro": "fixture não é elegível pré-live "
                           "(encerrado ou ao vivo)"})
    ok, motivo = jogo_elegivel(
        fx.league_id, fx.league_name,
        fx.home_team_name, fx.away_team_name,
    )
    if not ok:
        return _envelope(
            False, "prelive",
            {"fixture_id": fixture_id, "elegivel_pre": False,
             "motivo_inelegivel": motivo},
            extra={"erro": "fixture inelegível (universo oficial)"})
    espec = f"{fx.home_team_name} x {fx.away_team_name}"
    saida = analisar_fixture(client, espec, registrar=False)
    d = saida.to_dict()
    data = {
        "fixture_id": fixture_id,
        "home": fx.home_team_name,
        "away": fx.away_team_name,
        "competition": fx.league_name,
        "kickoff": fx.date,
        "elegivel_pre": True,
        "oportunidades_operacionais": d["operational_opportunities"],
        "observacoes": d["observations"],
        "bloqueados": d["blocked"],
        "nenhum_aprovado": d["nenhum_aprovado"],
        "saida_oficial_completa": d,
    }
    return _envelope(True, "prelive", data, engine_version=VERSAO_PREJOGO_OP)


# ----------------------------------------------------------------------
# 5. varredura_live (saída oficial live, modo teste)
# ----------------------------------------------------------------------
@app.get("/api/varredura-live")
def varredura_live_endpoint() -> dict[str, Any]:
    """Varredura LIVE oficial (modo teste). Jogos realmente ao vivo agora."""
    try:
        client = _client()
    except Exception as e:
        return _envelope(False, "live", None,
                         extra={"erro": f"{type(e).__name__}: {e}"})
    saida = varredura_live(client)
    d = saida.to_dict()
    lv = d.get("live", {})
    data = {
        "total_ao_vivo": lv.get("total_ao_vivo"),
        "total_elegiveis": lv.get("total_elegiveis"),
        "total_triados": lv.get("total_triados"),
        "data_freshness": lv.get("data_freshness"),
        "status_estatistico_live": lv.get("status_estatistico"),
        "status_operacional_live": lv.get("status_operacional"),
        "modo_teste_live": d.get("modo_teste"),
        "override_operador": lv.get("override_operador"),
        "decisao_humana": lv.get("decisao_humana"),
        "timestamp_override": lv.get("timestamp_override"),
        "sinais_operacionais": d["operational_opportunities"],
        "observacoes": d["observations"],
        "bloqueados": d["blocked"],
        "nenhum_aprovado": d["nenhum_aprovado"],
        "saida_oficial_completa": d,
    }
    return _envelope(True, "live", data, engine_version=VERSAO_LIVE_OP,
                     extra={"modo_teste": d.get("modo_teste"),
                            "data_freshness": lv.get("data_freshness")})


# ----------------------------------------------------------------------
# 6. analisar_partida_live (snapshot factual + lógica live de um fixture)
# ----------------------------------------------------------------------
@app.get("/api/partida-live")
def analisar_partida_live(
    fixture_id: int = Query(..., description="ID da partida ao vivo"),
) -> dict[str, Any]:
    """Snapshot factual atual + resultado da lógica live existente para um
    fixture. Não inventa probabilidade; se não houver, probabilidade=null."""
    try:
        client = _client()
    except Exception as e:
        return _envelope(False, "live", None,
                         extra={"erro": f"{type(e).__name__}: {e}"})
    from src.live import fetch_live_snapshot

    snapshot = fetch_live_snapshot(client, fixture_id, record=False)
    if snapshot is None:
        return _envelope(
            False, "live", {"fixture_id": fixture_id, "live": False},
            extra={"erro": "fixture não está ao vivo (ou não encontrado)",
                   "status": "NAO_DISPONIVEL"})
    # Lógica live: roda a varredura live oficial e extrai este fixture.
    saida = varredura_live(client)
    d = saida.to_dict()
    opps = [o for o in d["operational_opportunities"]
            if o.get("fixture_id") == fixture_id]
    obs = [o for o in d["observations"]
           if o.get("fixture_id") == fixture_id]
    factual = {
        "fixture_id": fixture_id,
        "home": snapshot.home_team_name,
        "away": snapshot.away_team_name,
        "competition": snapshot.league_name,
        "kickoff": snapshot.date_local,
        "live": True,
        "live_minute": snapshot.elapsed,
        "score": (f"{snapshot.goals_home}-{snapshot.goals_away}"
                  if snapshot.goals_home is not None
                  and snapshot.goals_away is not None else None),
        "status_live": snapshot.status,
        "collected_at": snapshot.collected_at,
        "has_stats": snapshot.has_stats,
        "data_freshness": (d.get("live", {}) or {}).get("data_freshness"),
        "modo_teste_live": d.get("modo_teste"),
        "status_estatistico_live": (d.get("live", {}) or {}).get(
            "status_estatistico"),
        "status_operacional_live": (d.get("live", {}) or {}).get(
            "status_operacional"),
        "sinais_operacionais": opps,
        "observacoes": obs,
        "nenhum_sinal": len(opps) == 0,
    }
    return _envelope(True, "live", factual, engine_version=VERSAO_LIVE_OP,
                     extra={"modo_teste": d.get("modo_teste"),
                            "data_freshness": (d.get("live", {}) or {}).get(
                                "data_freshness")})


# ----------------------------------------------------------------------
# 7. obter_status_mercados
# ----------------------------------------------------------------------
@app.get("/api/status-mercados")
def obter_status_mercados() -> dict[str, Any]:
    """Status oficial de todos os mercados (estatístico + operacional)."""
    data = {
        "mercados": _status_mercados_serializado(),
        "modo_teste_live": MODO_TESTE_LIVE,
        "resumo": {
            "GOALS": "LIBERADO (estatístico)",
            "CORNERS": ("LIBERADO operacionalmente por override; status "
                        "estatístico EM_OBSERVAÇÃO"),
            "RESULTADO": "EM_OBSERVAÇÃO (observação)",
            "CARDS": "NÃO_AVALIÁVEL (bloqueado)",
            "PRESSAO_LIVE": ("BLOQUEADO estatístico; HABILITADO para teste "
                             "por override de MODO (não cria sinal)"),
            "ODDS_ROI": "BLOQUEADO (não avaliável)",
        },
    }
    return _envelope(True, "status", data, engine_version=VERSAO_PREJOGO_OP)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def _run() -> None:  # pragma: no cover
    import uvicorn

    port = int(os.environ.get("PORT", "10000"))
    uvicorn.run("src.api_server:app", host="0.0.0.0", port=port,
                log_level="info")


if __name__ == "__main__":  # pragma: no cover
    _run()