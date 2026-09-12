WHITELIST_COMPETITIONS = {
    39: "Premier League",
    140: "La Liga",
    135: "Serie A Italia",
    78: "Bundesliga",
    61: "Ligue 1",
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    71: "Brasileirao Serie A",
    94: "Primeira Liga Portugal",
    88: "Eredivisie",
    203: "Super Lig",
    128: "Liga Profesional Argentina",
    262: "Liga MX",
    239: "Primera A Colombia",
    130: "Copa Argentina",
    241: "Copa Colombia",
    253: "MLS",
    13: "Libertadores",
    11: "Sudamericana",
    72: "Brasileirao Serie B",
    73: "Copa do Brasil",
}

PRIORITY_MARKETS = ["goals", "corners", "cards"]

PROJECT_RULES = {
    "priority": "PRECISAO > QUANTIDADE",
    "max_approved_per_day": 10,
    "missing_data_policy": "NAO AVALIAVEL",
    "immutable_original_prediction": True,
    "fact_calculation_interpretation_separation": True,
}
