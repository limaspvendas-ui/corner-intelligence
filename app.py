import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

API_BASE_URL = "https://v3.football.api-sports.io"


def api_headers():
    api_key = os.getenv("API_FOOTBALL_KEY")
    if not api_key:
        return None
    return {
        "x-apisports-key": api_key,
    }


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
    return jsonify({
        "status": "ok",
        "api_key_configured": bool(os.getenv("API_FOOTBALL_KEY")),
    })


@app.get("/api/fixtures")
def fixtures():
    headers = api_headers()
    if headers is None:
        return jsonify({
            "error": "API_FOOTBALL_KEY nao configurada no ambiente do servidor."
        }), 503

    params = {}
    for key in ("date", "league", "season", "team", "live", "id", "timezone", "from", "to", "last", "next", "status"):
        value = request.args.get(key)
        if value is not None:
            params[key] = value

    try:
        response = requests.get(
            f"{API_BASE_URL}/fixtures",
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar API-Football", "detail": str(exc)}), 502

    return jsonify(data)


@app.get("/api/fixtures/statistics")
def fixture_statistics():
    headers = api_headers()
    if headers is None:
        return jsonify({"error": "API_FOOTBALL_KEY nao configurada no ambiente do servidor."}), 503

    fixture_id = request.args.get("fixture")
    if not fixture_id:
        return jsonify({"error": "Parametro fixture e obrigatorio."}), 400

    params = {"fixture": fixture_id}
    team = request.args.get("team")
    if team:
        params["team"] = team

    try:
        response = requests.get(
            f"{API_BASE_URL}/fixtures/statistics",
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar estatisticas", "detail": str(exc)}), 502

    return jsonify(data)


@app.get("/api/leagues")
def leagues():
    headers = api_headers()
    if headers is None:
        return jsonify({"error": "API_FOOTBALL_KEY nao configurada no ambiente do servidor."}), 503

    params = {key: value for key in ("id", "name", "country", "code", "season", "current", "team", "type") if (value := request.args.get(key)) is not None}

    try:
        response = requests.get(f"{API_BASE_URL}/leagues", headers=headers, params=params, timeout=20)
        response.raise_for_status()
        return jsonify(response.json())
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar ligas", "detail": str(exc)}), 502


@app.get("/api/standings")
def standings():
    headers = api_headers()
    if headers is None:
        return jsonify({"error": "API_FOOTBALL_KEY nao configurada no ambiente do servidor."}), 503

    params = {key: value for key in ("league", "season", "team") if (value := request.args.get(key)) is not None}
    if "league" not in params or "season" not in params:
        return jsonify({"error": "Parametros league e season sao obrigatorios."}), 400

    try:
        response = requests.get(f"{API_BASE_URL}/standings", headers=headers, params=params, timeout=20)
        response.raise_for_status()
        return jsonify(response.json())
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar classificacao", "detail": str(exc)}), 502


@app.get("/api/odds")
def odds():
    headers = api_headers()
    if headers is None:
        return jsonify({"error": "API_FOOTBALL_KEY nao configurada no ambiente do servidor."}), 503

    params = {key: value for key in ("fixture", "league", "season", "date", "timezone", "page", "bookmaker", "bet") if (value := request.args.get(key)) is not None}

    try:
        response = requests.get(f"{API_BASE_URL}/odds", headers=headers, params=params, timeout=20)
        response.raise_for_status()
        return jsonify(response.json())
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar odds", "detail": str(exc)}), 502


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
