"""Testes da validacao OBRIGATORIA de identidade (ID + nome + competicao).

Regressao dos casos reais que motivaram o modulo src/identity.py:
    - ID 1404 (SKU Amstetten, Austria) tratado como Chapecoense;
    - ID 125 (Botafogo-SP, que disputa a Serie B) tratado como Botafogo.
Em ambos, o historico coletado foi de OUTRO clube. Com o modulo, essas
coletas INTERROMPEM com IdentityDivergenceError antes de tocar em
qualquer estatistica.

Todos os testes sao offline (FakeAPI, sem rede e sem gastar requisicoes).
"""

import pytest

import src.identity as identity
from src.exceptions import (
    AmbiguousTeamError,
    IdentityDivergenceError,
    UserFacingError,
)
from src.identity import (
    ValidatedTeamsStore,
    fixture_teams,
    normalize_name,
    resolve_match_teams_validated,
    resolve_team_validated,
    validate_team_identity,
)
from src.match_stats import fetch_team_history


# ----------------------------------------------------------------------
# Isolamento do cache de identidade (nunca escreve no DB de producao)
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _db_isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(identity, "DATA_DIR", tmp_path)


# ----------------------------------------------------------------------
# Fakes no formato real da API-Football
# ----------------------------------------------------------------------
def team_raw(team_id, name, country="Brazil"):
    return {
        "team": {
            "id": team_id,
            "name": name,
            "code": None,
            "country": country,
            "founded": 1900,
        },
        "venue": {},
    }


def fixture_raw(
    fixture_id,
    home_id,
    home_name,
    away_id,
    away_name,
    status="NS",
    league_id=71,
    season=2026,
):
    return {
        "fixture": {
            "id": fixture_id,
            "date": "2026-09-06T16:00:00-03:00",
            "status": {"short": status, "elapsed": None},
        },
        "league": {
            "id": league_id,
            "name": "Serie A",
            "round": "Regular Season - 25",
            "season": season,
        },
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "goals": {"home": None, "away": None},
    }


def stats_block(team_id, name, corners):
    return {
        "team": {"id": team_id, "name": name},
        "statistics": [{"type": "Corner Kicks", "value": corners}],
    }


class FakeAPI:
    """Responde por (endpoint + subconjunto de parametros).

    Qualquer chamada nao mockada falha o teste: nenhum modulo pode
    coletar dados de uma rota que nao foi explicitamente autorizada.
    """

    def __init__(self):
        self.routes = []
        self.calls = []

    def on(self, endpoint, subset, response):
        # Rotas registradas DEPOIS tem prioridade (permitem overrides)
        self.routes.insert(0, (endpoint, subset, response))

    def get(self, endpoint, params=None, use_cache=True):
        params = params or {}
        self.calls.append((endpoint, params))
        for ep, subset, response in self.routes:
            if ep == endpoint and all(
                params.get(k) == v for k, v in subset.items()
            ):
                return response
        raise AssertionError(
            f"Chamada nao autorizada no teste: {endpoint} {params}"
        )

    def endpoints(self):
        return [ep for ep, _ in self.calls]


# ----------------------------------------------------------------------
# Normalizacao de nomes
# ----------------------------------------------------------------------
def test_normalizacao_ignora_acentos_caixa_e_hifens():
    assert normalize_name("Botafogo") == normalize_name("botafogo")
    assert normalize_name("Chapecoense-sc") == normalize_name("CHAPECOENSE SC")
    assert normalize_name("São Paulo") == normalize_name("sao paulo")
    # grafias realmente DIFERENTES nao sao fundidas (identidade nao e fuzz)
    assert normalize_name("Chapecoense-sc") != normalize_name("Chapecense")
    assert normalize_name("Botafogo") != normalize_name("Botafogo-SP")
    assert normalize_name("") == ""


# ----------------------------------------------------------------------
# Resolucao validada por nome
# ----------------------------------------------------------------------
def test_resolucao_validada_retorna_o_id_real():
    client = FakeAPI()
    client.on("/teams", {"name": "Botafogo"}, [team_raw(120, "Botafogo")])
    client.on("/teams", {"id": 120}, [team_raw(120, "Botafogo")])

    team = resolve_team_validated(client, "Botafogo")

    assert team.id == 120
    assert team.name == "Botafogo"


def test_divergencia_id_nome_interrompe_antes_de_coletar():
    """Regressao do caso real: ID 1404 e SKU Amstetten, nao a Chapecoense."""
    client = FakeAPI()
    client.on("/teams", {"id": 1404}, [team_raw(1404, "SKU Amstetten", "Austria")])

    with pytest.raises(IdentityDivergenceError) as exc:
        validate_team_identity(
            client, 1404, "Chapecoense-sc", context="historico da Chapecoense"
        )
    msg = str(exc.value)
    assert "SKU Amstetten" in msg
    assert "Chapecoense-sc" in msg
    assert "interrompida" in msg
    # A coleta foi interrompida: nenhuma chamada de estatistica aconteceu
    assert client.endpoints() == ["/teams"]


def test_fetch_team_history_aborta_com_id_de_outro_clube():
    """Regressao do caso real: ID 125 e outro Botafogo (Serie B),
    nao o Botafogo que disputa a Serie A."""
    client = FakeAPI()
    client.on("/teams", {"id": 125}, [team_raw(125, "Botafogo-SP")])
    # As rotas abaixo NAO podem ser chamadas (coleta interrompida):
    # se fossem, FakeAPI levantaria AssertionError, mas o erro certo e
    # o IdentityDivergenceError ANTES de qualquer /fixtures.
    with pytest.raises(IdentityDivergenceError):
        fetch_team_history(client, 125, last=10, team_name="Botafogo")
    assert client.endpoints() == ["/teams"]


def test_fetch_team_history_valida_e_coleta_quando_identidade_bate():
    client = FakeAPI()
    client.on("/teams", {"id": 120}, [team_raw(120, "Botafogo")])
    client.on(
        "/fixtures",
        {"team": 120, "last": 10},
        [
            fixture_raw(
                100, 120, "Botafogo", 121, "Palmeiras",
                status="FT", league_id=71, season=2026,
            )
        ],
    )
    client.on(
        "/fixtures/statistics",
        {"fixture": 100, "half": "true"},
        [
            stats_block(120, "Botafogo", 8),
            stats_block(121, "Palmeiras", 5),
        ],
    )

    games, unavailable = fetch_team_history(
        client, 120, last=10, team_name="Botafogo"
    )

    assert len(games) == 1
    assert unavailable == 0
    assert games[0].corners_for == 8
    assert games[0].played_at_home is True


# ----------------------------------------------------------------------
# Validacao de competicao (ID + nome + competicao)
# ----------------------------------------------------------------------
def test_validacao_de_competicao_confirma_participacao():
    client = FakeAPI()
    client.on("/teams", {"id": 131}, [team_raw(131, "Corinthians")])
    client.on(
        "/fixtures",
        {"team": 131, "league": 71, "season": 2026},
        [fixture_raw(200, 131, "Corinthians", 132, "Chapecoense-sc")],
    )

    team = validate_team_identity(
        client, 131, "Corinthians",
        context="teste", league_id=71, season=2026,
    )
    assert team.id == 131


def test_validacao_de_competicao_falha_sem_partidas_na_liga():
    client = FakeAPI()
    client.on("/teams", {"id": 1404}, [team_raw(1404, "SKU Amstetten", "Austria")])
    client.on("/fixtures", {"team": 1404, "league": 71, "season": 2026}, [])

    # O nome ate bateria, mas o time nao tem partidas na competicao:
    # mesmo assim a validacao por competicao interrompe a coleta.
    with pytest.raises(IdentityDivergenceError) as exc:
        validate_team_identity(
            client, 1404, "SKU Amstetten",
            context="teste", league_id=71, season=2026,
        )
    assert "competicao 71" in str(exc.value)


# ----------------------------------------------------------------------
# Fixture conhecido: IDs OBRIGATORIAMENTE do fixture
# ----------------------------------------------------------------------
def test_fixture_teams_usa_os_ids_do_proprio_fixture():
    client = FakeAPI()
    client.on("/teams", {"id": 120}, [team_raw(120, "Botafogo")])
    client.on("/teams", {"id": 121}, [team_raw(121, "Palmeiras")])
    fixture = fixture_raw(1492360, 120, "Botafogo", 121, "Palmeiras")
    from src.fixtures import parse_fixture

    home, away = fixture_teams(client, parse_fixture(fixture))

    assert home.id == 120
    assert away.id == 121


def test_fixture_teams_rejeita_nome_esperado_divergente():
    client = FakeAPI()
    client.on("/teams", {"id": 131}, [team_raw(131, "Corinthians")])
    client.on("/teams", {"id": 132}, [team_raw(132, "Chapecoense-sc")])
    fixture = fixture_raw(1492362, 131, "Corinthians", 132, "Chapecoense-sc")
    from src.fixtures import parse_fixture

    # Quem chama esperava Chapecoense como mandante; o fixture diz outro
    with pytest.raises(IdentityDivergenceError):
        fixture_teams(
            client, parse_fixture(fixture),
            expect_home="Chapecoense-sc",
        )


# ----------------------------------------------------------------------
# Resolucao de confronto ("A x B") validada
# ----------------------------------------------------------------------
def test_resolve_match_teams_validado():
    client = FakeAPI()
    client.on("/teams", {"name": "Corinthians"}, [team_raw(131, "Corinthians")])
    client.on("/teams", {"id": 131}, [team_raw(131, "Corinthians")])
    client.on(
        "/teams", {"name": "Chapecoense-sc"}, [team_raw(132, "Chapecoense-sc")]
    )
    client.on("/teams", {"id": 132}, [team_raw(132, "Chapecoense-sc")])

    team_a, team_b = resolve_match_teams_validated(
        client, "Corinthians x Chapecoense-sc"
    )
    assert team_a.id == 131
    assert team_b.id == 132


def test_resolve_match_teams_rejeita_nome_divergente_no_round_trip():
    client = FakeAPI()
    # A busca por nome devolve um candidato cujo ID aponta para OUTRO clube
    client.on("/teams", {"name": "Botafogo"}, [team_raw(125, "Botafogo")])
    client.on("/teams", {"id": 125}, [team_raw(125, "Botafogo-SP")])

    with pytest.raises(IdentityDivergenceError):
        resolve_match_teams_validated(client, "Botafogo x Palmeiras")


# ----------------------------------------------------------------------
# Pre-jogo: reancoragem pelos IDs do fixture conhecido
# ----------------------------------------------------------------------
def _pre_match_routes(client, corinthians_name="Corinthians",
                      chape_name="Chapecoense-sc"):
    client.on(
        "/teams", {"name": "Corinthians"}, [team_raw(131, corinthians_name)]
    )
    client.on(
        "/teams", {"name": "Chapecoense-sc"}, [team_raw(132, chape_name)]
    )
    client.on("/teams", {"id": 131}, [team_raw(131, corinthians_name)])
    client.on("/teams", {"id": 132}, [team_raw(132, chape_name)])
    client.on(
        "/fixtures",
        {"team": 131, "next": 10},
        [fixture_raw(1492362, 131, "Corinthians", 132, "Chapecoense-sc")],
    )
    client.on(
        "/fixtures",
        {"team": 131, "last": 20},
        [fixture_raw(100, 131, "Corinthians", 121, "Palmeiras", status="FT")],
    )
    client.on(
        "/fixtures",
        {"team": 132, "last": 20},
        [
            fixture_raw(
                101, 119, "Internacional", 132, "Chapecoense-sc", status="FT"
            )
        ],
    )
    client.on(
        "/fixtures/statistics",
        {"fixture": 100, "half": "true"},
        [stats_block(131, "Corinthians", 7), stats_block(121, "Palmeiras", 6)],
    )
    client.on(
        "/fixtures/statistics",
        {"fixture": 101, "half": "true"},
        [stats_block(119, "Internacional", 4), stats_block(132, "Chapecoense-sc", 5)],
    )
    client.on("/fixtures/headtohead", {"h2h": "131-132"}, [])
    client.on("/fixtures", {"league": 71, "season": 2026}, [])


def test_pre_jogo_reancora_os_ids_do_fixture():
    from src.analysis import pre_match_analysis

    client = FakeAPI()
    _pre_match_routes(client)

    result = pre_match_analysis(client, "Corinthians", "Chapecoense-sc")

    next_fixture = result["proximo_jogo"]
    assert next_fixture.home_team_id == 131
    assert next_fixture.away_team_id == 132
    # Identidade validada = a do fixture, usada por todos os modulos:
    assert result["time_a"].id == next_fixture.home_team_id
    assert result["time_b"].id == next_fixture.away_team_id
    assert result["stats_a"]["n_jogos"] >= 1
    assert result["stats_b"]["n_jogos"] >= 1


def test_pre_jogo_interrompe_se_o_id_aponta_outro_clube():
    """Round-trip do ID devolve outro clube (caso SKU Amstetten):
    a analise aborta ANTES de coletar qualquer estatistica."""
    from src.analysis import pre_match_analysis

    client = FakeAPI()
    _pre_match_routes(client, chape_name="Chapecoense-sc")
    # /teams?id=132 devolve um clube DIFERENTE do nome buscado:
    client.on("/teams", {"id": 132}, [team_raw(132, "SKU Amstetten", "Austria")])

    with pytest.raises(IdentityDivergenceError):
        pre_match_analysis(client, "Corinthians", "Chapecoense-sc")

    # Nenhuma estatistica foi coletada - a coleta foi interrompida
    assert "/fixtures/statistics" not in client.endpoints()
    assert "/fixtures/headtohead" not in client.endpoints()


def test_mesmo_time_dois_nomes_rejeitado():
    client = FakeAPI()
    client.on("/teams", {"name": "Botafogo"}, [team_raw(120, "Botafogo")])
    client.on("/teams", {"id": 120}, [team_raw(120, "Botafogo")])

    with pytest.raises(UserFacingError):
        resolve_match_teams_validated(client, "Botafogo x Botafogo")


def test_ambiguidade_resolvida_por_dados_de_competicao():
    """Caso real: 'Botafogo' existe no Brasil (120) e em Camaroes (5562).
    Com contexto de competicao (Serie A 2026), apenas um participa: a
    participacao - dado da API - resolve; nao e palpite."""
    client = FakeAPI()
    client.on(
        "/teams",
        {"name": "Botafogo"},
        [
            team_raw(120, "Botafogo", "Brazil"),
            team_raw(5562, "Botafogo", "Cameroon"),
        ],
    )
    client.on("/fixtures", {"team": 5562, "league": 71, "season": 2026}, [])
    client.on(
        "/fixtures",
        {"team": 120, "league": 71, "season": 2026},
        [fixture_raw(300, 120, "Botafogo", 121, "Palmeiras")],
    )
    client.on("/teams", {"id": 120}, [team_raw(120, "Botafogo")])

    team = resolve_team_validated(
        client, "Botafogo", league_id=71, season=2026
    )
    assert team.id == 120


def test_ambiguidade_persiste_se_varios_participam_da_competicao():
    client = FakeAPI()
    client.on(
        "/teams",
        {"name": "Botafogo"},
        [
            team_raw(120, "Botafogo", "Brazil"),
            team_raw(5562, "Botafogo", "Cameroon"),
        ],
    )
    client.on(
        "/fixtures",
        {"team": 5562, "league": 71, "season": 2026},
        [fixture_raw(301, 5562, "Botafogo", 999, "Adversario")],
    )
    client.on(
        "/fixtures",
        {"team": 120, "league": 71, "season": 2026},
        [fixture_raw(302, 120, "Botafogo", 121, "Palmeiras")],
    )

    # Dois participantes: segue ambiguo - o usuario escolhe
    with pytest.raises(AmbiguousTeamError):
        resolve_team_validated(client, "Botafogo", league_id=71, season=2026)


def test_ambiguidade_sem_contexto_vira_pergunta_ao_usuario():
    """Sem competicao informada, a ambiguidade e do usuario (regra 4)."""
    client = FakeAPI()
    client.on(
        "/teams",
        {"name": "Botafogo"},
        [team_raw(120, "Botafogo", "Brazil"), team_raw(5562, "Botafogo", "Cameroon")],
    )

    with pytest.raises(AmbiguousTeamError):
        resolve_team_validated(client, "Botafogo")


# ----------------------------------------------------------------------
# Cache de identidade: SOMENTE associacoes validadas
# ----------------------------------------------------------------------
def test_store_guarda_apenas_associacao_validada():
    client = FakeAPI()
    client.on("/teams", {"name": "Palmeiras"}, [team_raw(121, "Palmeiras")])
    client.on("/teams", {"id": 121}, [team_raw(121, "Palmeiras")])

    resolve_team_validated(client, "Palmeiras")

    store = ValidatedTeamsStore()
    record = store.get("Palmeiras")
    assert record is not None
    assert record["team_id"] == 121
    assert record["name"] == "Palmeiras"


def test_store_nao_guarda_associacao_com_divergencia():
    client = FakeAPI()
    client.on("/teams", {"id": 1404}, [team_raw(1404, "SKU Amstetten", "Austria")])

    with pytest.raises(IdentityDivergenceError):
        validate_team_identity(
            client, 1404, "Chapecoense-sc", context="teste"
        )

    store = ValidatedTeamsStore()
    # Nada foi cacheado: a associacao "Chapecoense-sc -> 1404" e invalida
    assert store.get("Chapecoense-sc") is None
    assert store.get("SKU Amstetten") is None