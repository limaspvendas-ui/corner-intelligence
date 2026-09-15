"""FAILOVER MULTIFONTE — testes obrigatórios da Etapa 13 (15 itens).

Arquitetura testada: FONTES -> PROVIDER ROUTER -> NORMALIZACAO -> MOTOR.
O motor nunca escolhe fonte; a API-Football PAGA permanece primaria;
fallback so por INDISPONIBILIDADE (quota/rate/timeout/5xx/conexao);
conflito factual nunca e resolvido silenciosamente; NULL permanece NULL;
nenhuma fonte incapaz e usada para aquele tipo de dado; nenhuma
formula/threshold/calibracao do motor e alterada.

Itens (numeracao da especificacao do usuario):
  1. API-Football saudavel permanece primaria (fallback nem e chamado)
  2. QUOTA_EXHAUSTED -> fallback
  3. timeout -> fallback
  4. 503 -> fallback
  5. secundaria indisponivel -> proxima compativel
  6. nenhuma fonte capaz -> NAO DISPONIVEL (nunca zero)
  7. NULL permanece NULL
  8. provedor de odds NUNCA substitui fato
  9. StatsBomb NUNCA e usado como LIVE
  10. conflito factual -> CONFLITO_DE_FONTE
  11. circuit breaker evita repeticao apos quota
  12. recuperacao apos reset volta a primaria
  13. SaidaOficial: mesmos fatos => mesmos campos/decisoes
  14. nenhuma formula/threshold/calibracao alterada
  15. MCP continua com exatamente 9 ferramentas
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import provider_router as pr
from src import source_health
from src.exceptions import (
    SourceDataConflictError,
    SourceQuotaExhaustedError,
    SourceRateLimitError,
    SourceTimeoutError,
    SourceUnavailableError,
)
from src.provider_router import (
    TIPOS_DADO,
    coletar,
    provenancia_fontes,
    provenancia_reset,
    verificar_conflito_factual,
)


# ----------------------------------------------------------------------
# Fixtures: DB temporario (nao polui o banco real) + circuitos zerados
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _db_tmp(tmp_path, monkeypatch):
    db = tmp_path / "test_failover.db"
    monkeypatch.setattr(source_health, "DB_PATH", db)
    monkeypatch.setattr("src.config.DB_PATH", db)
    for p in ("api_football", "football_data_org", "apifootball_com",
              "the_odds_api", "five_dollar_football", "sportmonks",
              "statsbomb_open"):
        source_health.circuito_resetar(p)
    provenancia_reset()
    yield
    for p in ("api_football", "football_data_org", "apifootball_com",
              "the_odds_api", "five_dollar_football", "sportmonks",
              "statsbomb_open"):
        source_health.circuito_resetar(p)
    provenancia_reset()


def _stub_client():
    return SimpleNamespace()


# ----------------------------------------------------------------------
# 1. API-Football saudavel permanece primaria
# ----------------------------------------------------------------------
def test_1_api_football_saudavel_permance_primaria():
    chamadas = {"primaria": 0, "fallback": 0}

    def primaria(client):
        chamadas["primaria"] += 1
        return ["DADO_PRIMARIO"]

    def fallback():
        chamadas["fallback"] += 1
        return ["DADO_FALLBACK"]

    res = coletar(
        "lista_fixtures_dia", primaria, client=_stub_client(),
        fallbacks={"football_data_org": fallback})
    assert res == ["DADO_PRIMARIO"]
    assert chamadas == {"primaria": 1, "fallback": 0}
    prov = provenancia_fontes()["lista_fixtures_dia"]
    assert prov["fonte_principal"] == "api_football"
    assert prov["fonte_utilizada"] == "api_football"
    assert prov["fallback_ativado"] is False
    assert prov["motivo_fallback"] is None
    # circuito permanece fechado
    assert source_health.circuito_info("api_football")["estado"] == "FECHADO"


def test_20_resultado_valido_vazio_nao_e_gatilho_de_failover():
    """'Zero jogos hoje' e um RESULTADO VALIDO da fonte — nao uma falha.
    Nao ativa fallback, nao abre circuit breaker, nao vira erro."""
    chamadas = {"primaria": 0, "fallback": 0}

    def primaria(client):
        chamadas["primaria"] += 1
        return []  # dia sem jogos: resposta legitima

    def fallback():
        chamadas["fallback"] += 1
        return ["FALLBACK_NAO_DEVIA"]

    res = coletar(
        "lista_fixtures_dia", primaria, client=_stub_client(),
        fallbacks={"football_data_org": fallback})
    assert res == []
    assert chamadas == {"primaria": 1, "fallback": 0}
    assert source_health.circuito_info("api_football")["estado"] == "FECHADO"
    prov = provenancia_fontes()["lista_fixtures_dia"]
    assert prov["fonte_utilizada"] == "api_football"
    assert prov["fallback_ativado"] is False


# ----------------------------------------------------------------------
# 2/3/4. Gatilhos de indisponibilidade -> fallback
# ----------------------------------------------------------------------
@pytest.mark.parametrize("exc,esperado_gatilho", [
    (SourceQuotaExhaustedError("cota esgotada", motivo="QUOTA_EXHAUSTED"),
     "QUOTA_EXHAUSTED"),
    (SourceTimeoutError("demorou demais", motivo="TIMEOUT"), "TIMEOUT"),
    (SourceUnavailableError("HTTP 503", motivo="HTTP_5XX"), "HTTP_5XX"),
    (SourceRateLimitError("429", motivo="RATE_LIMIT"), "RATE_LIMIT"),
    (SourceUnavailableError("sem rede", motivo="CONNECTION_ERROR"),
     "CONNECTION_ERROR"),
])
def test_2_3_4_gatilho_indisponibilidade_ativa_fallback(exc, esperado_gatilho):
    def primaria(client):
        raise exc

    def fallback():
        return ["DADO_FALLBACK"]

    res = coletar(
        "lista_fixtures_dia", primaria, client=_stub_client(),
        fallbacks={"football_data_org": fallback})
    assert res == ["DADO_FALLBACK"]
    # circuit breaker da primaria aberto com o gatilho certo
    info = source_health.circuito_info("api_football")
    assert info["estado"] == "ABERTO"
    assert info["motivo"] == esperado_gatilho
    prov = provenancia_fontes()["lista_fixtures_dia"]
    assert prov["fonte_utilizada"] == "football_data_org"
    assert prov["fallback_ativado"] is True
    assert prov["motivo_fallback"] == esperado_gatilho


def test_erro_de_negocio_nao_e_gatilho_de_failover():
    """Ambiguidade/identidade/nao-encontrado NUNCA acionam fallback."""
    from src.exceptions import AmbiguousTeamError

    def primaria(client):
        raise AmbiguousTeamError("AMBIGUIDADE", [{"id": 1, "name": "X"}])

    with pytest.raises(AmbiguousTeamError):
        coletar(
            "lista_fixtures_dia", primaria, client=_stub_client(),
            fallbacks={"football_data_org": lambda: ["INVENTADO"]})
    assert source_health.circuito_info("api_football")["estado"] == "FECHADO"


# ----------------------------------------------------------------------
# 5. Secundaria indisponivel -> proxima compativel
# ----------------------------------------------------------------------
def test_5_secundaria_indisponivel_tenta_proxima_compativel(monkeypatch):
    monkeypatch.setitem(
        TIPOS_DADO, "lista_fixtures_dia",
        ["api_football", "fonte_a", "fonte_b"])
    chamadas = {"a": 0, "b": 0}

    def primaria(client):
        raise SourceQuotaExhaustedError("cota", motivo="QUOTA_EXHAUSTED")

    def fonte_a():
        chamadas["a"] += 1
        raise SourceUnavailableError("fora do ar", motivo="HTTP_503")

    def fonte_b():
        chamadas["b"] += 1
        return ["DADO_FONTE_B"]

    res = coletar(
        "lista_fixtures_dia", primaria, client=_stub_client(),
        fallbacks={"fonte_a": fonte_a, "fonte_b": fonte_b})
    assert res == ["DADO_FONTE_B"]
    assert chamadas == {"a": 1, "b": 1}
    # SEM loop: cada fonte tentada uma unica vez (contador == 1)


# ----------------------------------------------------------------------
# 6. Nenhuma fonte capaz -> NAO DISPONIVEL, nunca zero
# ----------------------------------------------------------------------
def test_6_nenhuma_fonte_capaz_nao_disponivel_nunca_zero():
    def primaria(client):
        raise SourceQuotaExhaustedError("cota", motivo="QUOTA_EXHAUSTED")

    def fallback():
        raise SourceUnavailableError("fora do ar", motivo="HTTP_503")

    with pytest.raises(SourceUnavailableError) as ei:
        coletar(
            "lista_fixtures_dia", primaria, client=_stub_client(),
            fallbacks={"football_data_org": fallback})
    msg = str(ei.value)
    assert "NAO DISPONIVEL" in msg
    # NUNCA convertido em lista vazia / zero jogos / sem oportunidades
    assert "zero" not in msg.lower() or "nunca" in msg.lower()

    # Tipo SEM alternativa compativel (ex.: stats_partida): mesma honestidade
    with pytest.raises(SourceUnavailableError) as ei2:
        coletar("stats_partida", primaria, client=_stub_client())
    assert "nao existe fonte alternativa compativel" in str(ei2.value)


# ----------------------------------------------------------------------
# 7. NULL permanece NULL (normalizacao do fallback nao inventa)
# ----------------------------------------------------------------------
def test_7_null_permance_null_na_normalizacao():
    m = {
        "status": "SCHEDULED",
        "utcDate": "2026-09-15T19:00:00Z",
        "homeTeam": {"name": "Time A"},
        "awayTeam": {"name": "Time B"},
        "matchday": 5,
        "score": {"fullTime": {"home": None, "away": None}},
    }
    fx = pr._fdo_normalizar_match(m, "Premier League")
    assert fx is not None
    assert fx.goals_home is None and fx.goals_away is None  # NULL nao vira 0
    assert fx.home_team_id is None   # espaco de IDs incompativel: nunca mapear
    assert fx.fixture_id is None
    assert fx.elapsed is None       # football-data.org nao tem minuto live
    assert fx.status == "NS"

    # status desconhecido/ausente nunca e inventado como "jogo valido"
    m2 = dict(m, status=None)
    fx2 = pr._fdo_normalizar_match(m2, "Liga X")
    assert fx2 is not None and fx2.status not in ("NS", "FT", "LIVE")


# ----------------------------------------------------------------------
# 8. Provedor de odds NUNCA substitui fato
# ----------------------------------------------------------------------
def test_8_odds_provider_nunca_substitui_fato():
    for tipo in ("lista_fixtures_dia", "lista_fixtures_live",
                 "fixture_por_id", "historico_time", "stats_partida",
                 "h2h", "benchmark_liga", "eventos_cartoes"):
        assert "the_odds_api" not in TIPOS_DADO[tipo], tipo
        assert "five_dollar_football" not in TIPOS_DADO[tipo], tipo
        assert TIPOS_DADO[tipo][0] == "api_football", tipo

    # Funcional: um "fallback" de odds oferecido NUNCA e consultado para
    # tipo factual (a ordem de fallback vem do registro de capacidades).
    def primaria(client):
        raise SourceQuotaExhaustedError("cota", motivo="QUOTA_EXHAUSTED")

    chamadas = {"odds": 0}

    def odds_fake():
        chamadas["odds"] += 1
        return ["ODD_NAO_E_FATO"]

    with pytest.raises(SourceUnavailableError):
        coletar(
            "stats_partida", primaria, client=_stub_client(),
            fallbacks={"the_odds_api": odds_fake})
    assert chamadas["odds"] == 0  # nunca chamado para dado factual


# ----------------------------------------------------------------------
# 9. StatsBomb NUNCA e usado como LIVE
# ----------------------------------------------------------------------
def test_9_statsbomb_nunca_live():
    for tipo in ("lista_fixtures_live", "stats_partida", "historico_time",
                 "eventos_cartoes", "fixture_por_id"):
        assert "statsbomb_open" not in TIPOS_DADO[tipo], tipo
    # StatsBomb so pode aparecer em tipos HISTORICOS (aqui: nenhum, por nao
    # ter ponte de IDs validada — e esse e o estado honesto atual)
    todos = {p for provs in TIPOS_DADO.values() for p in provs}
    assert "statsbomb_open" not in todos


# ----------------------------------------------------------------------
# 10. Conflito factual -> CONFLITO_DE_FONTE (nunca escolha silenciosa)
# ----------------------------------------------------------------------
def test_10_conflito_factual_registra_conflito_de_fonte(tmp_path):
    with pytest.raises(SourceDataConflictError) as ei:
        verificar_conflito_factual(
            5, 6, fixture_id="123", campo="corners",
            provider_b="fonte_b")
    assert "CONFLITO_DE_FONTE" in str(ei.value)
    assert ei.value.code == "SOURCE_DATA_CONFLICT"

    import sqlite3

    conn = sqlite3.connect(str(source_health.DB_PATH))
    # schema append-only da 5F-E1 criado sob demanda (idempotente)
    from src import resolucao_factual as rf

    rf._migrar_para_5fe1(conn)
    rows = conn.execute(
        "SELECT provider_a, value_a, provider_b, value_b, field "
        "FROM source_conflict WHERE fixture_internal='123'").fetchall()
    conn.close()
    assert rows, "conflito deve ser persistido"
    # AMBAS as versoes preservadas (5 e 6 — nenhuma escolhida)
    assert {rows[0][1], rows[0][3]} == {"5", "6"}


def test_10_null_nao_e_conflito():
    # ausencia de um lado nunca conflita (NULL != valor; NULL != zero)
    verificar_conflito_factual(
        None, 6, fixture_id="x", campo="corners")
    verificar_conflito_factual(
        5, None, fixture_id="x", campo="corners")
    verificar_conflito_factual(
        7, 7, fixture_id="x", campo="corners")  # iguais: sem conflito


# ----------------------------------------------------------------------
# 11. Circuit breaker evita repeticao apos quota
# ----------------------------------------------------------------------
def test_11_circuit_breaker_evita_repetir_apos_quota():
    chamadas_primaria = {"n": 0}

    def primaria(client):
        chamadas_primaria["n"] += 1
        raise SourceQuotaExhaustedError("cota", motivo="QUOTA_EXHAUSTED")

    def fallback():
        return ["FALLBACK"]

    for _ in range(3):
        coletar(
            "lista_fixtures_dia", primaria, client=_stub_client(),
            fallbacks={"football_data_org": fallback})
    # A primaria foi consultada UMA vez; depois o circuito ABERTO pulou
    # todas as consultas seguintes (sem martelar a fonte esgotada).
    assert chamadas_primaria["n"] == 1
    info = source_health.circuito_info("api_football")
    assert info["estado"] == "ABERTO"
    assert info["motivo"] == "QUOTA_EXHAUSTED"
    assert info["aberto_ate"] is not None  # abre ATE o reset diario


def test_11_client_pula_rede_com_circuito_aberto(monkeypatch, tmp_path):
    """APIFootballClient.get nem abre conexao quando o circuito esta
    aberto por QUOTA (dados em cache ainda validos continuam servidos)."""
    from src.api_client import APIFootballClient
    from src.cache import Cache

    # cache em ARQUIVO: Cache abre uma nova conexao por operacao, e um
    # banco :memory: perderia as tabelas entre conexoes.
    client = APIFootballClient(
        api_key="chave-de-teste-nao-real",
        cache=Cache(db_path=str(tmp_path / "cache.db")))

    def _proibido(*a, **kw):  # pragma: no cover
        raise AssertionError("rede nao deve ser consultada com circuito aberto")

    monkeypatch.setattr(client.session, "get", _proibido)
    source_health.circuito_abrir("api_football", "QUOTA_EXHAUSTED")
    with pytest.raises(SourceQuotaExhaustedError):
        client.get("/fixtures", params={"date": "2026-09-15"})


# ----------------------------------------------------------------------
# 12. Recuperacao apos reset volta a primaria
# ----------------------------------------------------------------------
def test_12_recuperacao_pos_reset_volta_a_primaria():
    chamadas = {"primaria": 0, "fallback": 0}

    def primaria(client):
        chamadas["primaria"] += 1
        if chamadas["primaria"] == 1:
            raise SourceQuotaExhaustedError("cota", motivo="QUOTA_EXHAUSTED")
        return ["PRIMARIA_RECUPERADA"]

    def fallback():
        chamadas["fallback"] += 1
        return ["FALLBACK"]

    # 1a coleta: quota esgotada -> fallback
    assert coletar("lista_fixtures_dia", primaria, client=_stub_client(),
                   fallbacks={"football_data_org": fallback}) == ["FALLBACK"]
    assert source_health.circuito_info("api_football")["estado"] == "ABERTO"
    # Simula o reset diario: cooldown expirado => HALF_OPEN (sonda permitida)
    source_health.circuito_abrir("api_football", "QUOTA_EXHAUSTED",
                                 ate_ts=source_health.time.time() - 1)
    assert source_health.circuito_permite("api_football") is True
    # 2a coleta: primaria sonda, responde => circuito fecha e volta a ser
    # a fonte usada (fallback NEM e consultado)
    assert coletar("lista_fixtures_dia", primaria, client=_stub_client(),
                   fallbacks={"football_data_org": fallback}) == \
        ["PRIMARIA_RECUPERADA"]
    assert source_health.circuito_info("api_football")["estado"] == "FECHADO"
    assert chamadas == {"primaria": 2, "fallback": 1}


# ----------------------------------------------------------------------
# 13. SaidaOficial: mesmos fatos => mesmos campos/decisoes (aditivo)
# ----------------------------------------------------------------------
def test_13_saida_oficial_mesmos_fatos_mesma_decisao(monkeypatch):
    from src.operacional import _construir_saida
    from src.prejogo_opportunity import AvaliacaoPre, VarreduraPreJogo

    def _varredura():
        av = AvaliacaoPre(
            mercado="gols", linha="Over 2.5 gols", prob=0.61,
            confianca=0.72, conf_componentes={}, sustentacao={})
        fx = SimpleNamespace(fixture_id=99, date="2026-09-15T19:00:00",
                             league_name="La Liga")
        return VarreduraPreJogo(
            espec="A x B", aprovadas=[av], avaliacoes=[av],
            fixture=fx, motivo_sem_jogo=None)

    s1 = _construir_saida(_varredura(), "2026-09-15 12:00:00")
    s2 = _construir_saida(_varredura(), "2026-09-15 12:00:00")
    d1, d2 = s1.to_dict(), s2.to_dict()
    # Campos de decisao identicos com os mesmos fatos — o router nao altera
    # probabilidade/confianca/linha/decisao em hipotese alguma.
    assert d1["operational_opportunities"] == d2["operational_opportunities"]
    assert d1["markets"] == d2["markets"]
    assert d1["nenhum_aprovado"] == d2["nenhum_aprovado"] is False
    o = d1["operational_opportunities"][0]
    assert o["prob"] == 0.61 and o["confianca"] == 0.72
    assert o["decisao_oficial"] == "ENTRAR"  # GOALS: aprovacao estatistica
    # Procedencia de fontes aparece SEM tocar decisao (Etapa 8 — aditivo)
    assert "fontes" in d1["provenance"]


# ----------------------------------------------------------------------
# 14. Nenhuma formula/threshold/calibracao alterada (motor intocado)
# ----------------------------------------------------------------------
def test_14_router_nao_importa_modulos_decisorios_do_motor():
    """O router e camada de COLETA: nao pode importar (nem alterar) os
    modulos de decisao do motor — escolha de fonte nunca e do motor."""
    proibidos = ("src.analysis", "src.prejogo_opportunity",
                 "src.live_opportunity", "src.policy", "src.calibration",
                 "src.cartoes", "src.resultado", "src.settlement",
                 "src.backtest")
    for mod in (pr, source_health):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(?:from|import)\s+([a-z_.]+)", src, re.M):
            alvo = m.group(1)
            assert alvo not in proibidos, f"{mod.__name__} importa {alvo}"


def test_14_coletar_nao_muta_resultado_da_primaria():
    dado = ["FATO_1", "FATO_2"]
    res = coletar("lista_fixtures_dia", lambda client: dado,
                  client=_stub_client())
    assert res is dado  # objeto passado intacto — nenhuma transformacao


# ----------------------------------------------------------------------
# 15. MCP continua com exatamente 9 ferramentas
# ----------------------------------------------------------------------
def test_15_mcp_exatamente_9_ferramentas():
    src = Path("src/mcp_server.py").read_text(encoding="utf-8")
    n = len(re.findall(r"@mcp\.tool\b", src))
    assert n == 9, f"MCP deve ter EXATAMENTE 9 ferramentas (encontradas {n})"


# ----------------------------------------------------------------------
# Extra: mapeamento de erros do cliente primario (Etapa 12) + endpoint
# ----------------------------------------------------------------------
def test_api_client_mapeia_quota_do_corpo():
    """Corpo de erro de cota com HTTP 200 => SourceQuotaExhaustedError
    (era UserFacingError generica — a causa real dos HTTP 500 de 15/09)."""
    from src.api_client import APIFootballClient

    client = APIFootballClient(api_key="k", cache=None)
    sess = SimpleNamespace()

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"errors": {"requests":
                               "You have reached the request limit for the day"}}

        headers = {"x-ratelimit-requests-limit": "7500",
                    "x-ratelimit-requests-remaining": "0"}

    sess.get = lambda *a, **kw: _Resp()
    client.session = sess
    with pytest.raises(SourceQuotaExhaustedError):
        client.get("/fixtures", params={"date": "2026-09-15"})
    # contadores de cota registrados dos headers REAIS (nunca a chave)
    quota = source_health.obter_quota_api_football()
    assert quota["requests_limit"] == 7500
    assert quota["requests_remaining"] == 0


def test_api_client_429_vira_rate_limit():
    import requests

    from src.api_client import APIFootballClient

    client = APIFootballClient(api_key="k", cache=None)

    class _Resp404:
        status_code = 429

        def raise_for_status(self):
            raise requests.exceptions.HTTPError("429")

    client.session = SimpleNamespace(
        get=lambda *a, **kw: _Resp404())
    with pytest.raises(SourceRateLimitError):
        client.get("/fixtures", params={"live": "all"})


def test_endpoint_retorna_erro_estruturado_nunca_500(monkeypatch):
    """Quota esgotada real: /api/jogos-do-dia responde envelope estruturado
    (success=false, erro_codigo=SOURCE_QUOTA_EXHAUSTED), HTTP 200 — nunca
    500 generico, nunca 'zero jogos'."""
    from fastapi.testclient import TestClient

    from src.api_server import app

    monkeypatch.setattr("src.api_server._client", lambda: object())

    def _quota(*a, **kw):
        raise SourceQuotaExhaustedError(
            "Erro da API-Football em /fixtures: cota esgotada",
            motivo="QUOTA_EXHAUSTED")

    monkeypatch.setattr("src.provider_router.get_fixtures_today", _quota)
    c = TestClient(app)
    r = c.get("/api/jogos-do-dia")
    assert r.status_code == 200  # envelope, nao 500
    d = r.json()
    assert d["success"] is False
    assert d["erro_codigo"] == "SOURCE_QUOTA_EXHAUSTED"
    assert d["data"]["status"] == "NAO_DISPONIVEL"
    # substring ASCII-safe da nota (o texto original tem acentos, e a
    # comparacao com acentos e frágil em consoles Windows)
    assert "Nenhum dado foi inventado" in d["data"]["nota"]


def test_varredura_nao_mascara_erro_de_fonte_como_inelegivel(monkeypatch):
    """Jogo cuja analise falha por FONTE vai para
    fixtures_fonte_indisponivel — nunca para fixtures_inelegiveis (o
    erro nunca vira 'sem oportunidade')."""
    from src.operacional import varredura_data

    fx = SimpleNamespace(
        fixture_id=77, date="2026-09-15T21:00:00", status="NS",
        elapsed=None, league_id=140, league_name="La Liga",
        round="Regular Season - 5", season=2026,
        home_team_id=10, home_team_name="Time A",
        away_team_id=20, away_team_name="Time B",
        goals_home=None, goals_away=None, venue="X",
        is_finished=False, is_live=False,
    )
    monkeypatch.setattr("src.operacional.get_fixtures_today",
                        lambda c, on_date=None: [fx])
    monkeypatch.setattr("src.operacional.jogo_elegivel",
                        lambda lid, ln, h, a: (True, ""))

    def _analisar(client, espec, registrar=False, fixture=None):
        raise SourceQuotaExhaustedError("cota", motivo="QUOTA_EXHAUSTED")

    monkeypatch.setattr("src.operacional.analisar_fixture", _analisar)
    out = varredura_data(object())
    assert out["fixtures_inelegiveis"] == []
    assert len(out["fixtures_fonte_indisponivel"]) == 1
    assert out["fixtures_fonte_indisponivel"][0]["erro_codigo"] == \
        "SOURCE_QUOTA_EXHAUSTED"
    assert out["fonte_indisponivel"] is True


# ----------------------------------------------------------------------
# Regressao de isolamento: a suite NAO escreve no banco operacional real
# ----------------------------------------------------------------------
def test_regressao_suite_nao_escreve_no_banco_operacional_real(tmp_path):
    """PASSO de isolamento: operacoes tipicas da suite (cliente com
    sessao fake, coleta roteada com fallback, circuit breaker, quota,
    request log, conflito factual) nao podem alterar NENHUM byte do
    banco operacional real data/corner_intelligence.db. O conftest
    redireciona TODOS os caminhos de banco para um arquivo temporario
    por teste; este teste prova que nada vaza para o arquivo real."""
    import hashlib

    from src.api_client import APIFootballClient
    from src.cache import Cache

    real = Path(__file__).resolve().parent.parent / \
        "data" / "corner_intelligence.db"
    real_reg = Path(__file__).resolve().parent.parent / \
        "data" / "recomendacoes.db"

    def _sha256(p: Path) -> str | None:
        if not p.exists():
            return None
        return hashlib.sha256(p.read_bytes()).hexdigest()

    antes = (_sha256(real), _sha256(real_reg))

    # 1. cliente com sessao fake: cache/cota/request-log/circuit-breaker
    class _Resp:
        status_code = 200
        headers = {"x-ratelimit-requests-limit": "7500",
                   "x-ratelimit-requests-remaining": "1234"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"errors": {}, "response": []}

    client = APIFootballClient(
        api_key="chave-de-teste-nao-real",
        cache=Cache(db_path=str(tmp_path / "cache_regression.db")))
    client.session = SimpleNamespace(get=lambda url, params=None,
                                     timeout=None: _Resp())
    client.get("/fixtures", params={"date": "2026-09-15"})

    # 2. coleta roteada com falha de primaria + fallback
    def _primaria(client):
        raise SourceQuotaExhaustedError("cota", motivo="QUOTA_EXHAUSTED")

    coletar("lista_fixtures_dia", _primaria, client=client,
            fallbacks={"football_data_org": lambda: ["FALLBACK"]})

    # 3. circuit breaker + request log + quota
    source_health.circuito_abrir("api_football", "QUOTA_EXHAUSTED")
    source_health.circuito_info("api_football")
    source_health.status_operacional_fontes()

    # 4. conflito factual (source_conflict / resolucao factual)
    try:
        verificar_conflito_factual(
            5, 6, fixture_id=123, campo="corners_total")
    except SourceDataConflictError:
        pass

    depois = (_sha256(real), _sha256(real_reg))
    assert antes == depois, (
        "ISOLAMENTO VIOLADO: o banco operacional real foi alterado "
        "durante operacoes de teste")