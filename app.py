import os
from datetime import datetime, timezone

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


@app.get("/")
def home():
    return jsonify({
        "service": "Corner Intelligence",
        "status": "online",
        "message": "Backend ativo. API key nunca e exibida por este servico.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok", "api_key_configured": bool(os.getenv("API_FOOTBALL_KEY"))})


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
        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        selected.append({
            "fixture_id": fixture.get("id"),
            "kickoff": fixture.get("date"),
            "timezone": fixture.get("timezone"),
            "status": fixture.get("status"),
            "league": {
                "id": league_id,
                "name": league.get("name"),
                "country": league.get("country"),
                "whitelist_name": WHITELIST_COMPETITIONS[league_id],
            },
            "home": teams.get("home"),
            "away": teams.get("away"),
        })

    selected.sort(key=lambda x: x.get("kickoff") or "")
    return jsonify({
        "date": date,
        "source": "API-Football",
        "priority": PROJECT_RULES["priority"],
        "max_approved_per_day": PROJECT_RULES["max_approved_per_day"],
        "total_api_fixtures": len(data.get("response", [])),
        "eligible_count": len(selected),
        "eligible_fixtures": selected,
        "note": "Esta rota apenas organiza partidas elegiveis. Nao inventa probabilidades nem aprova apostas.",
    })


@app.get("/api/fixtures/statistics")
def fixture_statistics():
    fixture_id = request.args.get("fixture")
    if not fixture_id:
        return jsonify({"error": "Parametro fixture e obrigatorio."}), 400
    params = {"fixture": fixture_id}
    team = request.args.get("team")
    if team:
        params["team"] = team
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
