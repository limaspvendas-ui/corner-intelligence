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
from contextlib import asynccontextmanager
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
# api-1.1-integracao: +2 ferramentas factuais (analisar_dados_da_partida,
# testar_coleta_automatica) reconectadas ao fluxo oficial; CARTOES em MODO
# TESTE por override (status estatistico NAO_AVALIÁVEL preservado).
API_VERSAO = "api-1.1-integracao"

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Lifespan da API: inicializa (e encerra) o session manager do MCP
    montado em /mcp. Tolerante a falha — sem o pacote `mcp` a API HTTP
    continua oficial (o MCP é camada de acesso opcional)."""
    mcp_cm = None
    try:
        from src import mcp_server

        mcp_cm = mcp_server.mcp.session_manager.run()
        await mcp_cm.__aenter__()
    except Exception:  # pragma: no cover - MCP opcional em runtime
        mcp_cm = None
    try:
        yield
    finally:
        if mcp_cm is not None:
            try:
                await mcp_cm.__aexit__(None, None, None)
            except Exception:  # pragma: no cover
                pass


app = FastAPI(
    title="Corner Intelligence — API Oficial",
    description=(
        "Camada fina de acesso à SaidaOficial do motor Corner Intelligence. "
        "Consulta somente leitura (pré-live + live modo teste + dados "
        "factuais). Nenhum endpoint recalcula probabilidade, altera decisão "
        "do motor ou executa aposta. NULL permanece NULL."
    ),
    version=API_VERSAO,
    lifespan=_lifespan,
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
            "modo_teste": (
                ov.get("modo_teste") == "true" if ov else False
            ),
            "rotulo_override": (ov.get("rotulo") if ov else None),
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
        "cards": {"status": STATUS_MERCADOS["cartoes"]["status"],
                  "origem": "override_usuario",
                  "modo_teste": True},
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
        "oportunidades_cartoes": [
            o for o in res["operational_opportunities"]
            if o.get("mercado") == "cartoes"],
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
            "CARDS": ("HABILITADO EM MODO TESTE por override autorizado "
                      "do usuário (MODO TESTE — OVERRIDE AUTORIZADO PELO "
                      "USUÁRIO — NÃO VALIDADO ESTATISTICAMENTE); status "
                      "estatístico NÃO_AVALIÁVEL preservado"),
            "PRESSAO_LIVE": ("BLOQUEADO estatístico; HABILITADO para teste "
                             "por override de MODO (não cria sinal)"),
            "ODDS_ROI": "BLOQUEADO (não avaliável)",
        },
    }
    return _envelope(True, "status", data, engine_version=VERSAO_PREJOGO_OP)


# ----------------------------------------------------------------------
# 8. analisar_dados_da_partida (deep dive FACTUAL — sem probabilidade)
# ----------------------------------------------------------------------
def _side_stats_dict(side: Any) -> dict[str, Any]:
    """Serializa SideStats factual (None permanece None — nunca zero)."""
    if side is None:
        return {}
    return {
        "corners": side.corners,
        "shots": side.shots,
        "shots_on_goal": side.shots_on_goal,
        "yellow_cards": side.yellow_cards,
        "red_cards": side.red_cards,
        "possession_pct": side.possession_pct,
        "expected_goals": side.expected_goals,
        "fouls": side.fouls,
        "offsides": side.offsides,
    }


def _team_game_dict(g: Any) -> dict[str, Any]:
    """Serializa um jogo do historico (TeamGameStats) — factual."""
    return {
        "fixture_id": g.fixture_id,
        "date": g.date,
        "league": g.league,
        "round": g.round,
        "status": g.status,
        "opponent": g.opponent,
        "played_at_home": g.played_at_home,
        "corners_for": g.corners_for,
        "corners_against": g.corners_against,
        "corners_total": g.corners_total,
        "corners_for_1st_half": g.corners_for_1st_half,
        "corners_against_1st_half": g.corners_against_1st_half,
        "corners_for_2nd_half": g.corners_for_2nd_half,
        "corners_against_2nd_half": g.corners_against_2nd_half,
        "goals_for": g.goals_for,
        "goals_against": g.goals_against,
        "shots_for": g.shots_for,
        "shots_on_goal_for": g.shots_on_goal_for,
        "possession_for": g.possession_for,
        "yellow_cards_for": g.yellow_for,
        "red_cards_for": g.red_for,
        "yellow_cards_against": g.yellow_against,
        "red_cards_against": g.red_against,
    }


@app.get("/api/partida-dados")
def analisar_dados_da_partida(
    fixture_id: int = Query(..., description="ID da partida"),
    historico_last: int = Query(
        20, ge=1, le=50,
        description="Últimos N jogos encerrados por time (factual; 1-50)"),
) -> dict[str, Any]:
    """Deep dive FACTUAL de uma partida: fixture, estatísticas (jogo
    completo + 1ºT/2ºT quando a fonte fornece) e histórico recente dos
    dois times. SOMENTE dados da API — nenhuma probabilidade, linha,
    confiança ou decisão é calculada aqui. NULL permanece NULL."""
    try:
        client = _client()
    except Exception as e:
        return _envelope(False, "factual", None,
                         extra={"erro": f"{type(e).__name__}: {e}"})
    from src.exceptions import DataUnavailableError
    from src.fixtures import get_fixture_by_id
    from src.match_stats import fetch_match_stats, fetch_team_history

    fx = get_fixture_by_id(client, fixture_id)
    if fx is None:
        return _envelope(
            False, "factual", None,
            extra={"erro": "fixture não encontrado",
                   "fixture_id": fixture_id})

    ok, motivo = jogo_elegivel(
        fx.league_id, fx.league_name,
        fx.home_team_name, fx.away_team_name,
    )

    facts: dict[str, Any] = {
        "fixture_id": fx.fixture_id,
        "home": fx.home_team_name,
        "away": fx.away_team_name,
        "home_team_id": fx.home_team_id,
        "away_team_id": fx.away_team_id,
        "competition": fx.league_name,
        "league_id": fx.league_id,
        "round": fx.round,
        "season": fx.season,
        "date": fx.date,
        "venue": fx.venue,
        "status": fx.status,
        "elapsed": fx.elapsed,
        "score": (f"{fx.goals_home}-{fx.goals_away}"
                  if fx.goals_home is not None and fx.goals_away is not None
                  else None),
        "elegivel_universo": ok,
        "motivo_elegibilidade": motivo if not ok else None,
    }

    # Estatísticas da partida (factual; halves quando a fonte fornece)
    try:
        match = fetch_match_stats(client, fx)
        facts["estatisticas"] = {
            "disponivel": True,
            "home": _side_stats_dict(match.home),
            "away": _side_stats_dict(match.away),
            "por_tempo_disponivel": match.has_halves,
            "first_half": {
                "home": _side_stats_dict(match.first_home),
                "away": _side_stats_dict(match.first_away),
            } if match.has_halves else None,
            "second_half": {
                "home": _side_stats_dict(match.second_home),
                "away": _side_stats_dict(match.second_away),
            } if match.has_halves else None,
        }
    except DataUnavailableError as e:
        facts["estatisticas"] = {
            "disponivel": False,
            "motivo": f"dado não disponível na fonte: {e}",
        }

    # Histórico recente (factual; jogos sem estatística são contabilizados,
    # nunca preenchidos)
    for lado, team_id, team_name in (
        ("home", fx.home_team_id, fx.home_team_name),
        ("away", fx.away_team_id, fx.away_team_name),
    ):
        try:
            games, unavailable = fetch_team_history(
                client, team_id, last=historico_last, team_name=team_name)
            facts[f"historico_{lado}"] = {
                "team": team_name,
                "team_id": team_id,
                "n_jogos": len(games),
                "n_sem_estatisticas": unavailable,
                "jogos": [_team_game_dict(g) for g in games],
            }
        except Exception as e:
            facts[f"historico_{lado}"] = {
                "team": team_name,
                "team_id": team_id,
                "erro": f"{type(e).__name__}: {e}",
            }

    data = {
        "facts": facts,
        "nota": ("Dados exclusivamente FACTUAIS da fonte. Nenhuma "
                 "probabilidade/linha/decisão é calculada nesta chamada "
                 "(use /api/partida-prelive ou /api/varredura-prelive "
                 "para a análise oficial do motor)."),
    }
    return _envelope(True, "factual", data, engine_version=VERSAO_PREJOGO_OP)


# ----------------------------------------------------------------------
# 9. testar_coleta_automatica (validação da coleta factual)
# ----------------------------------------------------------------------
@app.get("/api/teste-coleta")
def testar_coleta_automatica(
    date: str | None = Query(None, description="YYYY-MM-DD (default: hoje)"),
) -> dict[str, Any]:
    """Escolhe automaticamente uma partida elegível da data e valida a
    coleta FACTUAL (fixture + estatísticas + 1 jogo de histórico por
    time). Relata quais campos estão disponíveis/NULL na fonte — nenhum
    dado é inventado; ausência é reportada como 'dado não disponível'."""
    try:
        client = _client()
    except Exception as e:
        return _envelope(False, "factual", None,
                         extra={"erro": f"{type(e).__name__}: {e}"})
    from src.exceptions import DataUnavailableError
    from src.fixtures import get_fixtures_today
    from src.match_stats import fetch_match_stats, fetch_team_history

    fixtures = get_fixtures_today(client, on_date=date)
    elegiveis = [
        fx for fx in fixtures
        if not fx.is_finished and not fx.is_live
        and jogo_elegivel(
            fx.league_id, fx.league_name,
            fx.home_team_name, fx.away_team_name)[0]
    ]
    if not elegiveis:
        return _envelope(
            True, "factual",
            {"date": date or "hoje",
             "fixtures_considerados": len(fixtures),
             "fixtures_elegiveis": 0,
             "resultado": "SEM_JOGO_ELEGIVEL",
             "nota": "Nenhuma partida elegível na data para testar a coleta."},
            engine_version=VERSAO_PREJOGO_OP)

    fx = elegiveis[0]
    resultado: dict[str, Any] = {
        "date": date or "hoje",
        "fixtures_considerados": len(fixtures),
        "fixtures_elegiveis": len(elegiveis),
        "fixture_testado": {
            "fixture_id": fx.fixture_id,
            "espec": f"{fx.home_team_name} x {fx.away_team_name}",
            "competition": fx.league_name,
            "status": fx.status,
            "date": fx.date,
        },
        "campos_fixture": {
            "league_id": fx.league_id is not None,
            "season": fx.season is not None,
            "venue": fx.venue is not None,
            "elapsed": fx.elapsed is not None,
        },
    }

    # 1) estatísticas da partida (pré-jogo: espera-se indisponível; valida
    #    que a ausência é reportada e NUNCA preenchida)
    try:
        match = fetch_match_stats(client, fx)
        stats_ok = (match.home.corners is not None
                    or match.away.corners is not None)
        resultado["coleta_estatisticas"] = {
            "executada": True,
            "disponivel": stats_ok,
            "nota": ("estatísticas presentes na fonte"
                     if stats_ok else
                     "chegou resposta da fonte sem estatísticas para "
                     "esta partida (campos NULL preservados)"),
        }
    except DataUnavailableError as e:
        resultado["coleta_estatisticas"] = {
            "executada": True,
            "disponivel": False,
            "motivo": f"dado não disponível na fonte: {e}",
        }

    # 2) histórico: 1 jogo encerrado por time valida o caminho de coleta
    for lado, team_id, team_name in (
        ("home", fx.home_team_id, fx.home_team_name),
        ("away", fx.away_team_id, fx.away_team_name),
    ):
        try:
            games, unavailable = fetch_team_history(
                client, team_id, last=1, team_name=team_name)
            resultado[f"coleta_historico_{lado}"] = {
                "executada": True,
                "disponivel": bool(games),
                "n_jogos": len(games),
                "n_sem_estatisticas": unavailable,
                "nota": ("coleta validada" if games else
                         "sem jogo encerrado com estatísticas na fonte"),
            }
        except Exception as e:
            resultado[f"coleta_historico_{lado}"] = {
                "executada": False,
                "motivo": f"{type(e).__name__}: {e}",
            }

    resultado["nota"] = (
        "Validação FACTUAL da coleta (fixture + estatísticas + histórico). "
        "Nenhuma probabilidade/linha/decisão é calculada aqui. Ausência de "
        "dado é reportada como 'dado não disponível na fonte' — nunca "
        "preenchida com zero.")
    return _envelope(True, "factual", resultado,
                     engine_version=VERSAO_PREJOGO_OP)


# ----------------------------------------------------------------------
# Montagem do MCP oficial (mesma camada de acesso; mesmo serviço)
# ----------------------------------------------------------------------
# Monta o servidor MCP (exatamente as 9 ferramentas oficiais) no MESMO
# serviço HTTP. O MCP delega às funções desta API — nenhum cálculo novo.
# A montagem é ADITIVA e tolerante a falha: sem o pacote `mcp` instalado,
# a API HTTP continua funcionando normalmente (tests/CI sem MCP).
def _montar_mcp() -> None:
    try:
        from src import mcp_server

        app.mount("/", mcp_server.mcp.streamable_http_app())
    except Exception:  # pragma: no cover - mcp opcional em runtime
        import logging

        logging.getLogger(__name__).info(
            "Servidor MCP não montado (pacote mcp indisponível ou erro "
            "na montagem); a API HTTP continua oficial.")


_montar_mcp()


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