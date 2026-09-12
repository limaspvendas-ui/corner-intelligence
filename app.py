import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request

from config import PROJECT_RULES, WHITELIST_COMPETITIONS

app = Flask(__name__)
API_BASE_URL = "https://v3.football.api-sports.io"


def api_headers():
    api_key = os.getenv("API_FOOTBALL_KEY")
    if not api_key:
        return None
    return {"x-apisports-key": api_key}


def api_get(path, params=None):
    headers = api_headers()
    if headers is None:
        return None, (jsonify({"error": "API_FOOTBALL_KEY nao configurada no ambiente do servidor."}), 503)
    try:
        response = requests.get(f"{API_BASE_URL}{path}", headers=headers, params=params or {}, timeout=20)
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, (jsonify({"error": "Falha ao consultar API-Football", "detail": str(exc)}), 502)


def compact_fixture(item):
    fixture = item.get("fixture", {})
    league = item.get("league", {})
    teams = item.get("teams", {})
    return {
        "fixture_id": fixture.get("id"),
        "kickoff": fixture.get("date"),
        "status": fixture.get("status"),
        "league": {"id": league.get("id"), "name": league.get("name"), "country": league.get("country"), "season": league.get("season")},
        "home": teams.get("home"),
        "away": teams.get("away"),
        "goals": item.get("goals", {}),
    }


def corner_map(stats_response):
    out = {}
    for block in stats_response or []:
        team = block.get("team") or {}
        tid = team.get("id")
        if not tid:
            continue
        value = None
        for stat in block.get("statistics") or []:
            if stat.get("type") == "Corner Kicks":
                value = stat.get("value")
                break
        out[tid] = value
    return out


def fetch_fixture_halves(item, target_team_id):
    fixture_id = (item.get("fixture") or {}).get("id")
    base = compact_fixture(item)
    result = {"fixture": base, "first_half": None, "second_half": None, "missing": []}
    for half, key in ((1, "first_half"), (2, "second_half")):
        data, error = api_get("/fixtures/statistics", {"fixture": fixture_id, "half": half})
        if error:
            result["missing"].append(f"half_{half}")
            continue
        cmap = corner_map(data.get("response", []))
        home_id = ((item.get("teams") or {}).get("home") or {}).get("id")
        away_id = ((item.get("teams") or {}).get("away") or {}).get("id")
        home_c = cmap.get(home_id)
        away_c = cmap.get(away_id)
        target_c = cmap.get(target_team_id)
        total_c = None if home_c is None or away_c is None else home_c + away_c
        result[key] = {
            "home_corners": home_c,
            "away_corners": away_c,
            "match_total_corners": total_c,
            "target_team_corners": target_c,
        }
        if home_c is None or away_c is None:
            result["missing"].append(f"half_{half}_corners")
    return result


def collect_team_corner_history(team_id, last=25):
    fetch_last = max(last + 10, 35)
    data, error = api_get("/fixtures", {"team": team_id, "last": fetch_last})
    if error:
        return None, error
    finished = []
    for item in data.get("response", []):
        short = ((item.get("fixture") or {}).get("status") or {}).get("short")
        if short in {"FT", "AET", "PEN"}:
            finished.append(item)
    finished = finished[:last]
    rows = [None] * len(finished)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_fixture_halves, item, team_id): i for i, item in enumerate(finished)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                rows[i] = fut.result()
            except Exception as exc:
                rows[i] = {"fixture": compact_fixture(finished[i]), "first_half": None, "second_half": None, "missing": [str(exc)]}
    return {
        "source": "API-Football",
        "team_id": team_id,
        "requested_last": last,
        "returned": len(rows),
        "matches": rows,
        "note": "Escanteios por tempo coletados diretamente de /fixtures/statistics com parametro half. Dados ausentes permanecem nulos.",
    }, None


def collect_deep_dive(fixture_id):
    fixture_data, error = api_get("/fixtures", {"id": fixture_id})
    if error:
        return None, error
    response = fixture_data.get("response", [])
    if not response:
        return None, (jsonify({"error": "Partida nao encontrada.", "fixture_id": fixture_id}), 404)
    match = response[0]
    teams = match.get("teams", {})
    home_id = (teams.get("home") or {}).get("id")
    away_id = (teams.get("away") or {}).get("id")
    league = match.get("league", {})
    season = league.get("season")
    facts = {"fixture": compact_fixture(match)}
    missing = []
    statistics, stat_error = api_get("/fixtures/statistics", {"fixture": fixture_id})
    if stat_error:
        facts["fixture_statistics"] = None
        missing.append("fixture_statistics")
    else:
        stats_response = statistics.get("response", [])
        facts["fixture_statistics"] = stats_response if stats_response else None
        if not stats_response:
            missing.append("fixture_statistics")
    for label, team_id in (("home", home_id), ("away", away_id)):
        if not team_id:
            facts[f"{label}_recent"] = None
            missing.append(f"{label}_team_id")
            continue
        recent, recent_error = api_get("/fixtures", {"team": team_id, "last": 10})
        if recent_error:
            facts[f"{label}_recent"] = None
            missing.append(f"{label}_recent")
        else:
            recent_items = [compact_fixture(x) for x in recent.get("response", [])]
            facts[f"{label}_recent"] = recent_items if recent_items else None
            if not recent_items:
                missing.append(f"{label}_recent")
    if league.get("id") and season:
        standings, standings_error = api_get("/standings", {"league": league.get("id"), "season": season})
        if standings_error:
            facts["standings"] = None
            missing.append("standings")
        else:
            standings_response = standings.get("response", [])
            facts["standings"] = standings_response if standings_response else None
            if not standings_response:
                missing.append("standings")
    else:
        facts["standings"] = None
        missing.append("league_or_season")
    return {"fixture_id": fixture_id, "source": "API-Football", "mode": "FACT_ONLY_DEEP_DIVE", "facts": facts, "missing_data": sorted(set(missing)), "evaluation_status": "NAO AVALIAVEL" if missing else "DADOS COLETADOS", "note": "Coleta factual para validar insumos. Nenhuma probabilidade ou aposta e calculada nesta rota."}, None


@app.get("/")
def home():
    return jsonify({"service": "Corner Intelligence", "status": "online", "message": "Backend ativo. API key nunca e exibida por este servico.", "timestamp": datetime.now(timezone.utc).isoformat()})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "api_key_configured": bool(os.getenv("API_FOOTBALL_KEY"))})


@app.get("/openapi.json")
def openapi_schema():
    schema_path = Path(__file__).with_name("openapi.json")
    with schema_path.open("r", encoding="utf-8") as handle:
        return jsonify(json.load(handle))


@app.get("/api/fixtures")
def fixtures():
    params = {}
    for key in ("date", "league", "season", "team", "live", "id", "timezone", "from", "to", "last", "next", "status"):
        value = request.args.get(key)
        if value is not None:
            params[key] = value
    data, error = api_get("/fixtures", params)
    return error if error else jsonify(data)


@app.get("/api/daily")
def daily():
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "Parametro date e obrigatorio no formato YYYY-MM-DD."}), 400
    data, error = api_get("/fixtures", {"date": date})
    if error:
        return error
    selected = []
    for item in data.get("response", []):
        league = item.get("league", {})
        league_id = league.get("id")
        if league_id not in WHITELIST_COMPETITIONS:
            continue
        compact = compact_fixture(item)
        compact["league"]["whitelist_name"] = WHITELIST_COMPETITIONS[league_id]
        selected.append(compact)
    selected.sort(key=lambda x: x.get("kickoff") or "")
    return jsonify({"date": date, "source": "API-Football", "priority": PROJECT_RULES["priority"], "max_approved_per_day": PROJECT_RULES["max_approved_per_day"], "total_api_fixtures": len(data.get("response", [])), "eligible_count": len(selected), "eligible_fixtures": selected, "note": "Esta rota apenas organiza partidas elegiveis. Nao inventa probabilidades nem aprova apostas."})


@app.get("/api/deep-dive")
def deep_dive():
    fixture_id = request.args.get("fixture")
    if not fixture_id:
        return jsonify({"error": "Parametro fixture e obrigatorio."}), 400
    result, error = collect_deep_dive(fixture_id)
    return error if error else jsonify(result)


@app.get("/api/team-corner-history")
def team_corner_history():
    team_id = request.args.get("team", type=int)
    last = request.args.get("last", default=25, type=int)
    if not team_id:
        return jsonify({"error": "Parametro team e obrigatorio."}), 400
    last = max(1, min(last, 25))
    result, error = collect_team_corner_history(team_id, last)
    return error if error else jsonify(result)


@app.get("/api/test-data")
def test_data():
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "Parametro date e obrigatorio no formato YYYY-MM-DD."}), 400
    data, error = api_get("/fixtures", {"date": date})
    if error:
        return error
    eligible = [item for item in data.get("response", []) if item.get("league", {}).get("id") in WHITELIST_COMPETITIONS and item.get("fixture", {}).get("id")]
    eligible.sort(key=lambda x: x.get("fixture", {}).get("date") or "")
    if not eligible:
        return jsonify({"date": date, "error": "Nenhuma partida elegivel encontrada na whitelist."}), 404
    chosen = eligible[0]
    fixture_id = chosen.get("fixture", {}).get("id")
    result, deep_error = collect_deep_dive(fixture_id)
    if deep_error:
        return deep_error
    result["automatic_test"] = {"date": date, "eligible_count": len(eligible), "selection_rule": "primeira partida elegivel por horario; teste tecnico, nao recomendacao", "selected_fixture": compact_fixture(chosen)}
    return jsonify(result)


@app.get("/api/fixtures/statistics")
def fixture_statistics():
    fixture_id = request.args.get("fixture")
    if not fixture_id:
        return jsonify({"error": "Parametro fixture e obrigatorio."}), 400
    params = {"fixture": fixture_id}
    team = request.args.get("team")
    if team:
        params["team"] = team
    half = request.args.get("half")
    if half:
        params["half"] = half
    data, error = api_get("/fixtures/statistics", params)
    return error if error else jsonify(data)


@app.get("/api/leagues")
def leagues():
    params = {key: value for key in ("id", "name", "country", "code", "season", "current", "team", "type") if (value := request.args.get(key)) is not None}
    data, error = api_get("/leagues", params)
    return error if error else jsonify(data)


@app.get("/api/standings")
def standings():
    params = {key: value for key in ("league", "season", "team") if (value := request.args.get(key)) is not None}
    if "league" not in params or "season" not in params:
        return jsonify({"error": "Parametros league e season sao obrigatorios."}), 400
    data, error = api_get("/standings", params)
    return error if error else jsonify(data)


@app.get("/api/odds")
def odds():
    params = {key: value for key in ("fixture", "league", "season", "date", "timezone", "page", "bookmaker", "bet") if (value := request.args.get(key)) is not None}
    data, error = api_get("/odds", params)
    return error if error else jsonify(data)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
