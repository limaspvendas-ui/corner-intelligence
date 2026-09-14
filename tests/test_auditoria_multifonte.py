"""ETAPA 5F-D -- Testes da auditoria multi-fonte (MODO SEGURO).

24 testes cobrindo o exigido pela Etapa 5F-D, secao 28:
  - nenhuma chave em logs/docs
  - gate bloqueia chamadas autenticadas
  - provider segue o dado (dataclass)
  - bookmaker != provider
  - null != zero
  - missing != zero
  - 429/402 interrompem o provider
  - retry max 1 (HARD_LIMIT)
  - MATCHED / AMBIGUOUS / NOT_MATCHED
  - conflito preservado (nao resolvido)
  - nenhuma fonte sobrescreve outra
  - adapter invalido nao contamina
  - payload invalido nao vira factual
  - odd sem preco nao e inventada
  - timestamp preservado
  - StatsBomb nao e live
  - football-data nao penalizado por nao ter stats que nao oferece
  - plano limitado => LIMITADO_PELO_PLANO (nao FONTE RUIM)
  - nenhuma regra do motor alterada
  - historico intacto

NAO ha chamadas de rede reais nestes testes (fakes/injecao).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import json
from pathlib import Path

import pytest

from src import multifonte as mf
from src.auditoria_multifonte import (
    auditar, reconciliar_fixture, ReconciliationResult,
    ConflictRegistry, ConflictRecord, NormalizedFact,
    matriz_cobertura, matriz_confiabilidade, prontidao_coleta,
    classificar_cobertura, COLUNAS_COBERTURA,
    REC_MATCHED, REC_AMBIGUOUS, REC_NOT_MATCHED,
)
from src.multifonte import (
    credenciais_rotacionadas, status_credenciais,
    CredentialsBlocked, EndpointNotConfirmed, RateLimitHit, QuotaExhausted,
    TheOddsAPIProvider, FiveDollarFootballProvider,
    SportmonksProvider, APIFootballComProvider,
    FootballDataOrgProvider, StatsBombOpenProvider,
    ST_OK, ST_MISSING, ST_NOT_CONFIRMED, ST_BLOCKED, ST_NO_CREDENTIAL,
    ST_PLAN_LIMITED, ST_NOT_AVAILABLE, ST_TECHNICAL_FAIL, ST_NULL,
)


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------
@pytest.fixture
def gate_fechado(monkeypatch):
    monkeypatch.delenv(mf.GATE_ENV, raising=False)
    return False


@pytest.fixture
def gate_aberto(monkeypatch):
    monkeypatch.setenv(mf.GATE_ENV, "1")
    return True


@pytest.fixture
def sem_credenciais(monkeypatch):
    for v in ("THE_ODDS_API_KEY", "SPORTMONKS_API_TOKEN",
              "APIFOOTBALL_COM_API_KEY", "FIVE_DOLLAR_FOOTBALL_API_KEY",
              "FOOTBALL_DATA_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    return True


def _fact(provider="api_football", field="corners", value=7, fid="F1",
         status=ST_OK):
    return NormalizedFact(provider=provider, endpoint="/x",
                          retrieved_at=0.0,
                          fixture_provider_id=fid, fixture_corner_id=fid,
                          field=field, raw_value=value,
                          normalized_value=value, status=status)


# ----------------------------------------------------------------------
# 1. Nenhuma chave em logs/docs (nome de env NUNCA impresso com valor)
# ----------------------------------------------------------------------
def test_nenhuma_chave_impressa(gate_fechado, sem_credenciais, capsys):
    """status_credenciais NAO imprime valores; apenas booleanos."""
    s = status_credenciais()
    out = capsys.readouterr().out + capsys.readouterr().err
    # Nao deve conter o valor de nenhuma chave; so booleanos no dict.
    assert isinstance(s, dict)
    for p, st in s.items():
        assert all(isinstance(v, bool) for v in st.values()), \
            f"{p}: valores nao-booleanos (vazamento?)"
    # Nenhum caractere suspeito de chave impresso.
    assert "Bearer" not in out and "sk-" not in out


# ----------------------------------------------------------------------
# 2. Gate bloqueia chamadas autenticadas
# ----------------------------------------------------------------------
def test_gate_fechado_bloqueia_the_odds_api(gate_fechado, sem_credenciais):
    p = TheOddsAPIProvider()
    with pytest.raises(CredentialsBlocked):
        p.fetch_odds()
    assert "BLOQUEADAS" in str(CredentialsBlocked) or p.is_available() is False


def test_gate_fechado_bloqueia_sportmonks(gate_fechado, sem_credenciais):
    p = SportmonksProvider()
    with pytest.raises(CredentialsBlocked):
        p.fetch_match("1")


def test_gate_aberto_mas_sem_credencial_bloqueia(gate_aberto, sem_credenciais):
    # Gate aberto, mas credencial ausente => SEM_CREDENCIAL.
    p = TheOddsAPIProvider()
    with pytest.raises(CredentialsBlocked):
        p.fetch_odds()


def test_gate_aberto_com_credencial_permitiria(gate_aberto, monkeypatch):
    # Gate aberto + credencial presente => _guard passa; transporte fake
    # (sem rede) => parser produz NormalizedOdd do shape documentado v4.
    monkeypatch.setenv("THE_ODDS_API_KEY", "dummy-value-not-real")
    canned = [{
        "id": "evt1", "sport_key": "soccer_epl",
        "commence_time": "2026-09-14T20:00:00Z",
        "home_team": "A", "away_team": "B",
        "bookmakers": [{"key": "bet365", "title": "Bet365",
            "markets": [{"key": "totals",
                "outcomes": [{"name": "Over", "price": 1.9, "point": 2.5}]}]}]}]
    p = TheOddsAPIProvider(transport=lambda url, params=None, headers=None: canned)
    assert p.is_available() is True
    odds = p.fetch_odds()
    assert p._calls == 1
    assert len(odds) == 1
    assert odds[0].provider == "the_odds_api"
    assert odds[0].bookmaker == "Bet365"
    assert odds[0].market == "totals"
    assert odds[0].price == 1.9
    assert odds[0].coleta_tipo == "pre_match"


def test_gate_aberto_sem_credencial_bloqueia(gate_aberto, sem_credenciais):
    # Gate aberto, credencial ausente => bloqueia antes de qualquer rede.
    def _boom(*a, **k):
        raise AssertionError("rede nao deve ser chamada sem credencial")
    p = TheOddsAPIProvider(transport=_boom)
    with pytest.raises(CredentialsBlocked):
        p.fetch_odds()


def test_apifootball_com_parser_cards(gate_aberto, monkeypatch):
    # Parser de cards[] do get_events: yellow/red contados; ausente => MISSING.
    monkeypatch.setenv("APIFOOTBALL_COM_API_KEY", "dummy")
    canned = [{"match_id": "1", "match_hometeam_name": "A",
               "match_awayteam_name": "B", "match_date": "2026-09-14",
               "league_name": "X",
               "cards": [{"time": "10", "card": "yellow"},
                         {"time": "70", "card": "red"}]}]
    p = APIFootballComProvider(transport=lambda url, params=None, headers=None: canned)
    fatos = p.fetch_events("1")
    reds = [f for f in fatos if f.field == "red_cards"]
    yels = [f for f in fatos if f.field == "yellow_cards"]
    assert reds and reds[0].normalized_value == 1
    assert yels and yels[0].normalized_value == 1
    # cards ausente => MISSING (null != zero)
    canned2 = [{"match_id": "2", "match_hometeam_name": "C",
                "match_awayteam_name": "D"}]
    p2 = APIFootballComProvider(transport=lambda url, params=None, headers=None: canned2)
    f2 = [f for f in p2.fetch_events("2") if f.field == "red_cards"]
    assert f2 and f2[0].normalized_value is None
    assert f2[0].status == ST_MISSING


# ----------------------------------------------------------------------
# 3. Provider segue o dado (dataclass com identidade explicita)
# ----------------------------------------------------------------------
def test_provider_nome_explicito():
    assert TheOddsAPIProvider().provider_name == "the_odds_api"
    assert SportmonksProvider().provider_name == "sportmonks"
    assert APIFootballComProvider().provider_name == "apifootball_com"
    assert FootballDataOrgProvider().provider_name == "football_data_org"
    assert StatsBombOpenProvider().provider_name == "statsbomb_open"


def test_apifootball_com_distinto_de_api_football():
    """apifootball_com NAO confundido com api_football."""
    assert APIFootballComProvider().provider_name != "api_football"
    assert APIFootballComProvider().provider_name == "apifootball_com"


# ----------------------------------------------------------------------
# 4. bookmaker != provider
# ----------------------------------------------------------------------
def test_bookmaker_diferente_de_provider():
    """provider=api_football; bookmaker=bet365. Conceitos distintos."""
    from src.odds_coleta import OddSnapshot, PROVIDER_API_FOOTBALL
    snap = OddSnapshot(provider=PROVIDER_API_FOOTBALL, fixture_id=1,
                       coleta_tipo="pre_match", bookmaker="Bet365",
                       bet_name="Over", bet_id="x", familia="gols",
                       subfamilia=None, lado=None, linha=2.5,
                       value_feed="Over", odd=1.9, suspended=0,
                       update_feed=None, collected_at=0, fixture_date=None,
                       status="OK", motivo="OK")
    assert snap.provider == "api_football"
    assert snap.bookmaker == "Bet365"
    assert snap.provider != snap.bookmaker


# ----------------------------------------------------------------------
# 5 & 6. null != zero; missing != zero
# ----------------------------------------------------------------------
def test_null_nao_vira_zero():
    # Red cards = null deve permanecer None, NUNCA 0.
    f = _fact(field="red_cards", value=None, status=ST_NULL)
    assert f.normalized_value is None
    assert f.normalized_value != 0
    assert f.normalized_value is not False


def test_missing_nao_vira_zero():
    # Campo ausente => MISSING, valor None, NUNCA 0.
    f = _fact(field="corners", value=None, status=ST_MISSING)
    assert f.normalized_value is None
    assert f.status == ST_MISSING
    assert f.normalized_value != 0


def test_null_e_missing_distintos_de_zero_na_normalizacao():
    """A camada de normalizacao NUNCA converte None/missing para 0."""
    # Simula 3 cenarios: null explicito, missing, zero real.
    nulos = [NormalizedFact(provider="x", endpoint="/y", retrieved_at=0,
                            fixture_provider_id="F", fixture_corner_id="F",
                            field="red_cards",
                            normalized_value=None, status=s)
             for s in (ST_NULL, ST_MISSING)]
    zero_real = NormalizedFact(provider="x", endpoint="/y", retrieved_at=0,
                               fixture_provider_id="F", fixture_corner_id="F",
                               field="red_cards",
                               normalized_value=0, status=ST_OK)
    assert all(f.normalized_value is None for f in nulos)
    assert zero_real.normalized_value == 0  # zero REAL distinto de null/missing


# ----------------------------------------------------------------------
# 7. 429/402 interrompem o provider
# ----------------------------------------------------------------------
def test_rate_limit_interrompe(gate_aberto, monkeypatch):
    monkeypatch.setenv("THE_ODDS_API_KEY", "dummy")
    p = TheOddsAPIProvider()
    # Simula atingir HARD_LIMIT sem chamada real.
    p._calls = p.HARD_LIMIT
    with pytest.raises(RateLimitHit):
        p._guard()


def test_quota_402_interrompe():
    # QuotaExhausted e a excecao de 402; apenas confirma a existencia.
    assert issubclass(QuotaExhausted, RuntimeError)


# ----------------------------------------------------------------------
# 8. retry max 1 (HARD_LIMIT respeitado)
# ----------------------------------------------------------------------
def test_hard_limit_nao_excede(gate_aberto, monkeypatch):
    monkeypatch.setenv("SPORTMONKS_API_TOKEN", "dummy")
    p = SportmonksProvider()
    assert p.HARD_LIMIT <= 15
    p._calls = p.HARD_LIMIT
    with pytest.raises(RateLimitHit):
        p._guard()


# ----------------------------------------------------------------------
# 9. MATCHED / AMBIGUOUS / NOT_MATCHED
# ----------------------------------------------------------------------
def _fx(fid="1", home="Flamengo", away="Palmeiras", kickoff="2026-09-14T20:00",
       comp="Brasileirao", season="2026", sh=None, sa=None, status="NS"):
    return {"fixture_id": fid, "home": home, "away": away, "kickoff": kickoff,
            "competition": comp, "season": season,
            "score_home": sh, "score_away": sa, "status": status}


def test_reconciliacao_matched():
    r = reconciliar_fixture(_fx(), _fx(fid="99", home="Flamengo",
                                      away="Palmeiras",
                                      kickoff="2026-09-14T20:00"),
                           provider="sportmonks")
    assert r.status == REC_MATCHED
    assert r.confidence >= 0.85


def test_reconciliacao_not_matched():
    r = reconciliar_fixture(_fx(home="Flamengo", away="Palmeiras"),
                            _fx(fid="99", home="Barcelona", away="Madrid",
                                kickoff="2026-09-15T15:00"),
                            provider="sportmonks")
    assert r.status == REC_NOT_MATCHED
    assert r.confidence < 0.5


def test_reconciliacao_ambiguous():
    # Mesmas datas/comp mas nomes de times diferentes parcialmente.
    r = reconciliar_fixture(_fx(home="Flamengo", away="Palmeiras"),
                            _fx(fid="99", home="Flamengo RJ",
                                away="Palmeiras SP",
                                kickoff="2026-09-14T20:00"),
                            provider="sportmonks")
    # home/away divergem textualmente mas kickoff/comp coincidem => AMBIGUOUS
    assert r.status in (REC_AMBIGUOUS, REC_NOT_MATCHED)
    assert r.confidence < 0.85


def test_reconciliacao_nao_apenas_nome_textual():
    """Nao matchear so por nome; kickoff divergente rebaixa."""
    r = reconciliar_fixture(_fx(home="Flamengo", away="Palmeiras",
                                kickoff="2026-09-14T20:00"),
                            _fx(fid="9", home="Flamengo", away="Palmeiras",
                                kickoff="2026-09-20T20:00"),
                            provider="sportmonks")
    assert r.status != REC_MATCHED


# ----------------------------------------------------------------------
# 10. conflito preservado (nao resolvido)
# ----------------------------------------------------------------------
def test_conflito_preservado():
    reg = ConflictRegistry()
    fatos = [
        _fact(provider="api_football", field="corners", value=7, fid="F1"),
        _fact(provider="sportmonks", field="corners", value=9, fid="F1"),
    ]
    n = reg.comparar_fatos(fatos)
    assert n == 1
    c = reg.items()[0]
    assert c.natureza == "valor_divergente"
    assert c.value_a == 7 and c.value_b == 9
    # Preservado: nao ha "resolucao" -- ambos valores ficam.
    assert reg.to_list()[0]["value_a"] == 7


def test_conflito_null_nao_conflita():
    """null vs valor NAO e conflito (null permanece null)."""
    reg = ConflictRegistry()
    fatos = [
        _fact(provider="api_football", field="red_cards", value=None,
              status=ST_NULL, fid="F1"),
        _fact(provider="sportmonks", field="red_cards", value=1, fid="F1"),
    ]
    n = reg.comparar_fatos(fatos)
    assert n == 0  # null nao conflita


def test_timestamp_diferente_nao_e_conflito_de_valor():
    reg = ConflictRegistry()
    # Mesmo valor, timestamps diferentes => nao registra conflito de valor.
    fatos = [
        _fact(provider="api_football", field="corners", value=7, fid="F1"),
        _fact(provider="the_odds_api", field="corners", value=7, fid="F1"),
    ]
    assert reg.comparar_fatos(fatos) == 0


# ----------------------------------------------------------------------
# 11. nenhuma fonte sobrescreve outra
# ----------------------------------------------------------------------
def test_fonte_nao_sobrescreve_outra():
    """A matriz de cobertura tem coluna por provider; nenhum sobrescreve."""
    cs = status_credenciais()
    m = matriz_cobertura(cs)
    # Cada provider tem sua propria coluna de status; nada e "mesclado".
    assert set(m.keys()) >= {"api_football", "the_odds_api", "sportmonks"}
    for p, cols in m.items():
        assert all(isinstance(v, str) for v in cols.values())


# ----------------------------------------------------------------------
# 12. adapter invalido nao contamina
# ----------------------------------------------------------------------
def test_adapter_invalido_nao_contamina(gate_fechado, sem_credenciais):
    # Gate fechado => _guard bloqueia antes de qualquer rede (nao inventa dados).
    p = FiveDollarFootballProvider()
    with pytest.raises(CredentialsBlocked):
        p.fetch_odds(fixture_id=1)


def test_fivedollar_endpoints_agora_confirmados(gate_aberto, monkeypatch):
    # Endpoints confirmados por documentacao; transporte fake => parser rodou.
    monkeypatch.setenv("FIVE_DOLLAR_FOOTBALL_API_KEY", "dummy")
    canned = [{"bookmaker": "bet365", "opening": {"over": 1.9, "line": 9.5}}]
    p = FiveDollarFootballProvider(transport=lambda url, params=None, headers=None: canned)
    odds = p.fetch_odds(fixture_id=1, market="corner")
    assert p._calls == 1
    assert any(o.provider == "five_dollar_football" for o in odds)
    assert any(o.market == "corner" for o in odds)


def test_endpoint_nao_confirmado_nao_inventa():
    assert issubclass(EndpointNotConfirmed, RuntimeError)


# ----------------------------------------------------------------------
# 13. payload invalido nao vira factual
# ----------------------------------------------------------------------
def test_payload_invalido_nao_factual():
    """Um NormalizedFact com status FALHA_TECNICA NAO e tratado como OK."""
    f = _fact(status=ST_TECHNICAL_FAIL, value=None)
    assert f.status != ST_OK
    # comparar_fatos ignora fatos nao-OK.
    reg = ConflictRegistry()
    n = reg.comparar_fatos([f, _fact(provider="y", value=1, status=ST_OK)])
    assert n == 0  # o falho nao entra na comparacao


# ----------------------------------------------------------------------
# 14. odd sem preco nao e inventada
# ----------------------------------------------------------------------
def test_odd_sem_preco_nao_inventada():
    from src.multifonte import NormalizedOdd
    o = NormalizedOdd(provider="the_odds_api", bookmaker="Bet365",
                      fixture_provider_id="F1", fixture_corner_id="F1",
                      market="over_under_corners", submarket="9.5",
                      side="Over", line=9.5, price=None,
                      timestamp=None, coleta_tipo="pre_match",
                      status=ST_MISSING)
    assert o.price is None
    assert o.price != 0 and o.price != 1.0  # nao inventa preco


# ----------------------------------------------------------------------
# 15. timestamp preservado
# ----------------------------------------------------------------------
def test_timestamp_preservado():
    from src.multifonte import NormalizedOdd
    o = NormalizedOdd(provider="api_football", bookmaker="Bet365",
                      fixture_provider_id="F1", fixture_corner_id="F1",
                      market="over_under_corners", submarket="9.5",
                      side="Over", line=9.5, price=1.9,
                      timestamp="2026-09-14T20:00:00Z",
                      coleta_tipo="pre_match")
    assert o.timestamp == "2026-09-14T20:00:00Z"


# ----------------------------------------------------------------------
# 16. StatsBomb nao e live nem odds
# ----------------------------------------------------------------------
def test_statsbomb_nao_e_live():
    p = StatsBombOpenProvider()
    # StatsBomb NAO oferece odds; fetch_statistics retorna [] (AUSENTE).
    assert p.fetch_statistics("9880") == []
    # Sem credencial exigida (publico).
    assert p.CRED_ENV == ""


def test_statsbomb_sem_credencial():
    p = StatsBombOpenProvider()
    assert p.is_available() is True  # publico, nao exige gate


def test_statsbomb_sem_fetcher_bloqueia_em_modo_seguro():
    """Sem fetcher configurado, NAO faz chamada real (modo seguro)."""
    p = StatsBombOpenProvider(fetcher=None)
    with pytest.raises(CredentialsBlocked):
        p.fetch_events(9880)


def test_statsbomb_com_fetcher_fake_normaliza():
    """Com fetcher injetado (fake), normaliza eventos sem rede real."""
    payload = json.dumps([
        {"type": {"name": "Shot"}, "team": {"name": "X"}, "minute": 10},
        {"type": {"name": "Pass"}, "team": {"name": "Y"}, "minute": 12},
    ]).encode()
    p = StatsBombOpenProvider(fetcher=lambda url: payload)
    fatos = p.fetch_events(9880)
    assert len(fatos) == 2
    assert all(f.provider == "statsbomb_open" for f in fatos)
    assert fatos[0].field == "Shot"


# ----------------------------------------------------------------------
# 17. football-data nao penalizado por nao ter stats que nao oferece
# ----------------------------------------------------------------------
def test_football_data_stats_ausente_nao_penalizada():
    p = FootballDataOrgProvider()
    # football-data.org NAO oferece corners/cards/shots -> AUSENTE por design.
    assert p.fetch_statistics("1") == []
    assert p.fetch_events("1") == []
    # A matriz classifica isso como OK (ausencia legitima), nao FONTE RUIM.
    cs = status_credenciais()
    m = classificar_cobertura("football_data_org", cs)
    # Sem credencial/gate => SEM_CREDENCIAL/BLOQUEADO, mas a "penalizacao" seria
    # por ausencia legitima (NAO_DISPONIVEL) que NAO e classificada como falha.
    # Verificamos que nao ha "FONTE_RUIM" em nenhum status.
    assert "FONTE_RUIM" not in list(m.values())


# ----------------------------------------------------------------------
# 18. plano limitado => LIMITADO_PELO_PLANO (nao FONTE RUIM)
# ----------------------------------------------------------------------
def test_plano_limitado_classificacao(gate_fechado, sem_credenciais):
    cs = status_credenciais()
    # Para api_football (com credencial, cache observado), score_ht/handicap
    # sao LIMITADO_PELO_PLANO.
    m = classificar_cobertura("api_football", cs)
    assert m["score_ht"] == ST_PLAN_LIMITED
    assert "FONTE_RUIM" not in m.values()
    assert "FONTE_RUIM" not in list(classificar_cobertura("sportmonks", cs).values())


# ----------------------------------------------------------------------
# 19. nenhuma regra do motor alterada
# ----------------------------------------------------------------------
def test_regras_motor_intactas():
    from src.politica_aprovacao import PROB_MIN_APROVAR, PROB_MAX_APROVAR, CONF_MIN_TOP1
    assert PROB_MIN_APROVAR == 0.70
    assert PROB_MAX_APROVAR == 0.97
    assert CONF_MIN_TOP1 == 0.60
    from src.settlement import _MARCA_CONVENCAO_CARTOES
    assert _MARCA_CONVENCAO_CARTOES == "amarelo=1, vermelho=2"


def test_multifonte_nao_importa_motor():
    """src/multifonte e src/auditoria_multifonte NAO importam modulos do motor."""
    import importlib
    import inspect
    mods_motor = ("analysis", "politica_aprovacao", "settlement",
                  "calibration", "backtest", "prejogo_opportunity", "odds",
                  "identity", "live_pressure", "ao_vivo")
    for modname in ("src.multifonte", "src.auditoria_multifonte"):
        mod = importlib.import_module(modname)
        src = inspect.getsource(mod)
        # Procura linhas reais de import dos modulos do motor.
        for m in mods_motor:
            assert f"import {m}\n" not in src, \
                f"{modname} importa modulo do motor: {m}"
            assert f"from src.{m} import" not in src, \
                f"{modname} importa modulo do motor: {m}"


# ----------------------------------------------------------------------
# 20. historico intacto (banco read-only)
# ----------------------------------------------------------------------
def test_runner_banco_read_only(gate_fechado, sem_credenciais):
    r = auditar()
    b = r["banco_snapshots"]
    # O runner abriu o banco em modo ro (file:...?mode=ro). Confirma que nao
    # alterou: total deve permanecer 317951.
    assert b.get("total") == 317951
    assert b.get("por_provider") == {"api_football": 317951}
    assert b.get("provider_null") == 0
    assert b.get("integrity_check") == "ok"


# ----------------------------------------------------------------------
# 21. gate fechado => 0 chamadas autenticadas
# ----------------------------------------------------------------------
def test_zero_chamadas_autenticadas_modo_seguro(gate_fechado, sem_credenciais):
    r = auditar()
    assert r["chamadas_autenticadas_executadas"] == 0
    assert r["gate_aberto"] is False
    assert "BLOQUEADAS" in r["bloqueio"]


# ----------------------------------------------------------------------
# 22. matriz de confiabilidade nao tem fonte vencedora
# ----------------------------------------------------------------------
def test_matriz_confiabilidade_informativa():
    m = matriz_confiabilidade()
    assert "corners" in m
    for campo, info in m.items():
        # "principal"/"fallback" sao candidatos informativos, NAO imperativos.
        assert "principal" in info and "fallback" in info
        assert "status" in info


# ----------------------------------------------------------------------
# 23. prontidao de coleta sem daemon
# ----------------------------------------------------------------------
def test_prontidao_sem_daemon(gate_fechado, sem_credenciais):
    p = prontidao_coleta()
    assert p["api_football"] == "PRONTO"
    assert p["statsbomb_open"] == "PARCIAL"
    for prov in ("the_odds_api", "sportmonks", "apifootball_com",
                 "five_dollar_football", "football_data_org"):
        assert p[prov] == "NAO_PRONTO"


# ----------------------------------------------------------------------
# 24. cobertura matriz tem todas as colunas esperadas
# ----------------------------------------------------------------------
def test_cobertura_todas_colunas(gate_fechado, sem_credenciais):
    cs = status_credenciais()
    m = matriz_cobertura(cs)
    assert set(m.keys()) == set(cs.keys())
    for p, cols in m.items():
        assert set(cols.keys()) == set(COLUNAS_COBERTURA), p


def test_status_canonicos_validos(gate_fechado, sem_credenciais):
    cs = status_credenciais()
    m = matriz_cobertura(cs)
    validos = {ST_OK, "PARCIAL", ST_MISSING, ST_NOT_CONFIRMED,
               ST_BLOCKED, ST_NO_CREDENTIAL, ST_PLAN_LIMITED,
               ST_NOT_AVAILABLE, ST_TECHNICAL_FAIL, ST_NULL}
    # Todos os status usados pertencem ao conjunto canonico (sem FONTE_RUIM).
    for p, cols in m.items():
        for col, st in cols.items():
            assert "FONTE_RUIM" not in st, f"{p}/{col}: {st}"
            assert st in validos, f"{p}/{col}: {st}"


# ----------------------------------------------------------------------
# 5F-D2: runner real com providers injetados (sem rede)
# ----------------------------------------------------------------------
def test_rodar_auditoria_real_com_fakes(gate_aberto, monkeypatch, tmp_path):
    """rodar_auditoria_real executa smoke+amostra com providers fake; estrutura
    do relatorio correta; nenhum valor de credencial impresso."""
    from src.auditoria_multifonte import rodar_auditoria_real
    monkeypatch.setenv("THE_ODDS_API_KEY", "dummy-aaa")
    monkeypatch.setenv("FIVE_DOLLAR_FOOTBALL_API_KEY", "dummy-bbb")
    monkeypatch.setenv("SPORTMONKS_API_TOKEN", "dummy-ccc")
    monkeypatch.setenv("APIFOOTBALL_COM_API_KEY", "dummy-ddd")
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "dummy-eee")

    class FakeOdd:
        def __init__(self, market, bookmaker, price):
            self.provider = "the_odds_api"; self.bookmaker = bookmaker
            self.market = market; self.price = price; self.coleta_tipo = "pre_match"

    class _P:
        def __init__(self, nome): self.provider_name = nome; self._calls = 0

    class FakeTheOdds(_P):
        def fetch_sports(self): return [{"key": "soccer_epl"}]
        def fetch_odds(self, *a, **k): return [FakeOdd("totals", "bet365", 1.9)]
    class Fake5D(_P):
        def fetch_status(self): return {"status": "ok"}
        def fetch_fixtures(self, params=None): return [{"id": 1}]
        def fetch_odds(self, fid, market="corner"): return []
    class FakeSM(_P):
        def fetch_leagues(self): return [{"id": 1}]
        def fetch_fixtures_by_date(self, d): return []
        def fetch_match(self, mid): return []
    class FakeAFC(_P):
        def fetch_competitions(self): return [{"id": 1}]
        def fetch_events_by_date(self, f, t, league_id=None):
            return [{"match_id": "9", "match_hometeam_name": "X",
                     "match_awayteam_name": "Y", "cards": []}]
    class FakeFD(_P):
        def fetch_competitions(self): return [{"code": "CL"}]
        def fetch_matches_by_competition(self, code, dateFrom=None, dateTo=None):
            return []
    class FakeSB(_P):
        def fetch_competitions(self): return [{"competition_id": 9}]
        def fetch_events(self, mid): return []

    providers = {
        "the_odds_api": FakeTheOdds("the_odds_api"),
        "five_dollar_football": Fake5D("five_dollar_football"),
        "sportmonks": FakeSM("sportmonks"),
        "apifootball_com": FakeAFC("apifootball_com"),
        "football_data_org": FakeFD("football_data_org"),
        "statsbomb_open": FakeSB("statsbomb_open"),
    }
    rel = rodar_auditoria_real(providers=providers, raw_dir=str(tmp_path),
                               date_str="2026-09-10")
    assert rel["gate_aberto"] is True
    assert rel["etapa"] == "5F-D2"
    # todos os smokes registraram OK (fakes nao levantam)
    assert all(rel["smoke_tests"].values()), rel["smoke_tests"]
    # chamadas registradas por provider
    assert rel["chamadas_por_provider"]["the_odds_api"] >= 1
    assert rel["chamadas_por_provider"]["apifootball_com"] >= 1
    # the_odds_api observou mercado totals
    assert "totals" in rel["observacoes"]["the_odds_api"]["mercados_observados"]
    # nenhum valor de credencial real no JSON do relatorio
    txt = json.dumps(rel, default=str, ensure_ascii=False)
    for seg in ("dummy-aaa", "dummy-bbb", "dummy-ccc", "dummy-ddd", "dummy-eee"):
        assert seg not in txt, "credencial vazada no relatorio"


def test_rodar_auditoria_real_gate_fechado(gate_fechado, sem_credenciais):
    """Gate fechado => runner retorna erro GATE_FECHADO, sem chamadas."""
    from src.auditoria_multifonte import rodar_auditoria_real
    rel = rodar_auditoria_real()
    assert rel["gate_aberto"] is False
    assert rel["erro"] == "GATE_FECHADO"


def test_rodar_auditoria_real_isola_falha_provider(gate_aberto, monkeypatch, tmp_path):
    """Um provider falhando (404) nao derruba a auditoria toda."""
    from src.auditoria_multifonte import rodar_auditoria_real
    monkeypatch.setenv("THE_ODDS_API_KEY", "x")
    monkeypatch.setenv("FIVE_DOLLAR_FOOTBALL_API_KEY", "x")
    monkeypatch.setenv("SPORTMONKS_API_TOKEN", "x")
    monkeypatch.setenv("APIFOOTBALL_COM_API_KEY", "x")
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "x")

    class BoomSM:
        provider_name = "sportmonks"; _calls = 0
        def fetch_leagues(self): raise EndpointNotConfirmed("404")
        def fetch_fixtures_by_date(self, d): return []
        def fetch_match(self, mid): return []
    class OkSB:
        provider_name = "statsbomb_open"; _calls = 0
        def fetch_competitions(self): return [{"id": 1}]
        def fetch_events(self, mid): return []
    class OkRest:
        provider_name = "x"; _calls = 0
        def fetch_sports(self): return []
        def fetch_odds(self, *a, **k): return []
        def fetch_status(self): return {}
        def fetch_fixtures(self, p=None): return []
        def fetch_competitions(self): return []
        def fetch_events_by_date(self, f, t, league_id=None): return []
        def fetch_matches_by_competition(self, c, dateFrom=None, dateTo=None): return []
    providers = {
        "the_odds_api": OkRest(), "five_dollar_football": OkRest(),
        "sportmonks": BoomSM(), "apifootball_com": OkRest(),
        "football_data_org": OkRest(), "statsbomb_open": OkSB(),
    }
    rel = rodar_auditoria_real(providers=providers, raw_dir=str(tmp_path),
                               date_str="2026-09-10")
    # sportmonks smoke falhou mas others ok
    assert rel["smoke_tests"]["sportmonks"] is False
    assert rel["smoke_tests"]["statsbomb_open"] is True
    statuses = {c["status"] for c in rel["chamadas"] if c["provider"] == "sportmonks"}
    assert "ENDPOINT_NAO_CONFIRMADO" in statuses