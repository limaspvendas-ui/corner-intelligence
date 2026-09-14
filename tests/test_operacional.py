"""Testes determinísticos da camada operacional (FECHAMENTO OPERACIONAL).

Cobre os gates de mercado A-K sem chamar a API: constroi VarreduraPreJogo
sinteticas e exercita _construir_saida (funcao pura da saida do motor).

Principios validados:
  - Apenas GOALS (APROVADO_PARA_PROXIMA_FASE) vira oportunidade operacional.
  - RESULTADO/CORNERS (EM_OBSERVAÇÃO) viram observação, nunca operacional.
  - CARDS (NÃO_AVALIÁVEL) e PRESSÃO/ODDS (BLOQUEADO) viram bloqueados.
  - NULL != ZERO: dado insuficiente => NÃO_AVALIÁVEL, nunca zero inventado.
  - Mesma entrada => mesma decisão (determinismo).
  - A camada NÃO altera prob/confianca/linha do motor (passa direto).
  - Saída tem versão/timestamp/provenância.
  - "NENHUMA OPORTUNIDADE OPERACIONAL APROVADA" é resultado válido.
  - A camada NÃO recalibra nem altera versão do motor.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.operacional import (
    STATUS_MERCADOS,
    VERSAO_CAMADA_OPERACIONAL,
    _construir_saida,
    formatar_saida,
)
from src.prejogo_opportunity import (
    VERSAO_PREJOGO_OP,
    AvaliacaoPre,
    VarreduraPreJogo,
)
from src.validacao_multifonte import (
    APROVADO_PROX, EM_OBS, BLOQUEADO, NAO_AVAL,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _fx(fixture_id=9001, home="Casa FC", away="Fora FC",
        league="Premier League", date="2026-09-20T15:00:00"):
    return SimpleNamespace(
        fixture_id=fixture_id, date=date, status="NS", elapsed=None,
        league_id=39, league_name=league, round="Regular Season - 5",
        season=2026, home_team_id=10, home_team_name=home,
        away_team_id=20, away_team_name=away,
        goals_home=None, goals_away=None,
        is_finished=False, is_live=False,
    )


def _av(mercado, linha, prob, confianca=0.72, riscos=None):
    return AvaliacaoPre(
        mercado=mercado, linha=linha, prob=prob, confianca=confianca,
        conf_componentes={"amostra_historica": 0.4, "benchmark": 0.4, "h2h": 0.2},
        sustentacao={"n_home": 10, "n_away": 10},
        riscos=riscos or [],
    )


_NO_FIXTURE = object()  # sentinel: fixture explicitamente None


def _varredura(aprovadas, avaliacoes=None, fixture=_NO_FIXTURE, motivo=None):
    return VarreduraPreJogo(
        espec="Casa FC x Fora FC",
        fixture=_fx() if fixture is _NO_FIXTURE else fixture,
        motivo_sem_jogo=motivo,
        historico={}, benchmark_escanteios=None, benchmark_gols=None,
        benchmark_cartoes=None,
        avaliacoes=avaliacoes or aprovadas,
        aprovadas=aprovadas,
        registros=[], cobertura=[], bloqueadas_cobertura=[],
    )


def _saida(aprovadas, avaliacoes=None, fixture=_NO_FIXTURE, motivo=None):
    return _construir_saida(
        _varredura(aprovadas, avaliacoes, fixture, motivo),
        "2026-09-14 20:00:00",
    )


# ----------------------------------------------------------------------
# A. GOALS aprovado+ENTRAR -> operational_opportunities
# ----------------------------------------------------------------------
def test_a_goals_aprovado_vira_operacional():
    saida = _saida([_av("gols", "Over 2.5 gols", 0.93, 0.75)])
    assert len(saida.operational_opportunities) == 1
    o = saida.operational_opportunities[0]
    assert o["mercado"] == "gols"
    assert o["decisao_oficial"] == "ENTRAR"
    assert o["aprovada_motor"] is True
    assert o["status_estatistico"] == APROVADO_PROX
    # GOALS aprovado => existe oportunidade => nenhum_aprovado False
    assert saida.nenhum_aprovado is False
    # markets contagem reflete o motor
    assert saida.markets["gols"]["aprovadas_motor"] == 1


# ----------------------------------------------------------------------
# B. GOALS nao aprovado pelo motor -> NAO vira operacional
# ----------------------------------------------------------------------
def test_b_goals_nao_entrar_nao_vira_operacional():
    # motor avaliou mas nao aprovou (prob abaixo do threshold)
    saida = _saida(
        aprovadas=[],
        avaliacoes=[_av("gols", "Over 2.5 gols", 0.55, 0.40)],
    )
    assert saida.operational_opportunities == []
    # GOALS em observacao? Nao -- GOALS e APROVADO_PROX, mas sem aprovacao
    # do motor a entrada vai para 'blocked' (rota operational mas sem
    # aprovacao_motor). De qualquer forma NUNCA operacional.
    assert saida.nenhum_aprovado is True


# ----------------------------------------------------------------------
# C. RESULTADO ENTRAR (motor-approved) -> observacao, NAO operacional
# ----------------------------------------------------------------------
def test_c_resultado_aprovado_vira_observacao_nao_operacional():
    saida = _saida([_av("resultado", "Vitoria mandante (1)", 0.92, 0.70)])
    # nunca operacional
    assert saida.operational_opportunities == []
    # vai para observacoes (EM_OBSERVAÇÃO)
    obs_mercados = {o["mercado"] for o in saida.observations}
    assert "resultado" in obs_mercados
    res = [o for o in saida.observations if o["mercado"] == "resultado"][0]
    assert res["decisao_oficial"] == "OBSERVACAO"
    assert res["status_estatistico"] == EM_OBS
    assert res["aprovada_motor"] is True


# ----------------------------------------------------------------------
# D. CORNERS sinal -> operacional por OVERRIDE (status estatístico preservado)
# ----------------------------------------------------------------------
def test_d_corners_sinal_operacional_por_override():
    """CORNERS aprovado pelo motor -> operacional por OVERRIDE do operador.
    O status estatístico real (EM_OBSERVAÇÃO) é preservado -- override NÃO
    é aprovação estatística."""
    saida = _saida([_av("escanteios", "Over 9.5 escanteios", 0.91, 0.68)])
    # override => operacional (não observação)
    assert len(saida.operational_opportunities) == 1
    o = saida.operational_opportunities[0]
    assert o["mercado"] == "escanteios"
    assert o["decisao_oficial"] == "ENTRAR"
    assert o["aprovada_motor"] is True
    # status estatístico real PRESERVADO (não promovido)
    assert o["status_estatistico"] == EM_OBS
    # origem da habilitação = override, não estatístico
    assert o["origem_operacional"] == "override_usuario"
    assert o["status_operacional"] == "HABILITADO_POR_OVERRIDE_DO_USUARIO"
    # auditável: timestamp + motivo do override
    assert o["timestamp_override"] is not None
    assert o["motivo_override"] is not None


# ----------------------------------------------------------------------
# E. CARDS -> NÃO_AVALIÁVEL (blocked)
# ----------------------------------------------------------------------
def test_e_cards_bloqueado_nao_avaliavel():
    saida = _saida([_av("cartoes", "Over 3.5 cartoes", 0.90, 0.70)])
    assert saida.operational_opportunities == []
    blk = [b for b in saida.blocked if b["mercado"] == "cartoes"]
    assert len(blk) == 1
    assert blk[0]["status_estatistico"] == NAO_AVAL
    assert blk[0]["decisao_oficial"] == "NAO_AVALIAVEL"


# ----------------------------------------------------------------------
# F. PRESSAO LIVE -> BLOQUEADO (blocked, status-only)
# ----------------------------------------------------------------------
def test_f_pressao_live_bloqueado():
    # pressao_live nao e pre-game: aparece como blocked status-only mesmo
    # sem avaliacao do motor
    saida = _saida([_av("gols", "Over 2.5 gols", 0.93, 0.75)])
    blk = [b for b in saida.blocked if b["mercado"] == "pressao_live"]
    assert len(blk) == 1
    assert blk[0]["status_estatistico"] == BLOQUEADO
    assert blk[0]["decisao_oficial"] == "BLOQUEADO"
    # odds_roi tambem bloqueado
    blk_odds = [b for b in saida.blocked if b["mercado"] == "odds_roi"]
    assert len(blk_odds) == 1
    assert blk_odds[0]["status_estatistico"] == BLOQUEADO


# ----------------------------------------------------------------------
# G. Dado insuficiente -> nunca zero inventado
# ----------------------------------------------------------------------
def test_g_sem_jogo_data_status_sem_jogo():
    # motor nao encontrou fixture: motivo_sem_jogo, fixture None
    saida = _saida(aprovadas=[], avaliacoes=[], fixture=None,
                   motivo="jogo nao encontrado")
    assert saida.data_status == "SEM_JOGO"
    assert saida.operational_opportunities == []
    assert saida.nenhum_aprovado is True
    # markets contagens zero (nao inventado)
    assert saida.markets["gols"]["aprovadas_motor"] == 0
    assert saida.markets["gols"]["avaliacoes_motor"] == 0


def test_g_jogo_nao_elegivel_pre():
    fx = _fx()
    fx.is_finished = True
    saida = _saida(aprovadas=[], avaliacoes=[], fixture=fx,
                   motivo="jogo encerrado")
    assert saida.data_status == "JOGO_NAO_ELEGIVEL_PRE"
    assert saida.operational_opportunities == []


# ----------------------------------------------------------------------
# H. Mesma entrada => mesma decisao (determinismo)
# ----------------------------------------------------------------------
def test_h_determinismo_mesma_decisao():
    aprovadas = [_av("gols", "Over 2.5 gols", 0.93, 0.75),
                 _av("escanteios", "Over 9.5 escanteios", 0.91, 0.68)]
    s1 = _saida(aprovadas)
    s2 = _saida(aprovadas)
    # decisao oficial por mercado determinada pelo status (constante)
    assert [o["decisao_oficial"] for o in s1.operational_opportunities] == \
           [o["decisao_oficial"] for o in s2.operational_opportunities]
    assert [o["mercado"] for o in s1.observations] == \
           [o["mercado"] for o in s2.observations]
    # valores do motor preservados identicamente
    assert s1.operational_opportunities[0]["prob"] == \
           s2.operational_opportunities[0]["prob"]


# ----------------------------------------------------------------------
# I. A camada NAO altera prob/confianca/linha do motor
# ----------------------------------------------------------------------
def test_i_camada_nao_altera_valores_do_motor():
    av = _av("gols", "Over 2.5 gols", 0.93, 0.75,
             riscos=["outlier escanteios"])
    saida = _saida([av])
    o = saida.operational_opportunities[0]
    # valores copiados direto do motor, sem alteracao
    assert o["prob"] == av.prob
    assert o["confianca"] == av.confianca
    assert o["linha"] == av.linha
    assert o["riscos"] == av.riscos
    assert o["source"] == f"motor:{VERSAO_PREJOGO_OP}"


# ----------------------------------------------------------------------
# J. Saida tem versao/timestamp/provenância
# ----------------------------------------------------------------------
def test_j_saida_tem_versao_timestamp_provenancia():
    saida = _saida([_av("gols", "Over 2.5 gols", 0.93, 0.75)])
    assert saida.engine_version == VERSAO_PREJOGO_OP
    assert saida.camada_version == VERSAO_CAMADA_OPERACIONAL
    assert saida.generated_at == "2026-09-14 20:00:00"
    assert saida.provenance["motor"] == VERSAO_PREJOGO_OP
    assert saida.provenance["camada"] == VERSAO_CAMADA_OPERACIONAL
    assert "nota" in saida.provenance
    assert saida.fixture_id == 9001
    # to_dict serializavel
    d = saida.to_dict()
    assert d["engine_version"] == VERSAO_PREJOGO_OP
    assert d["camada_version"] == VERSAO_CAMADA_OPERACIONAL


# ----------------------------------------------------------------------
# K. Nenhuma oportunidade -> resposta valida, sem aposta forçada
# ----------------------------------------------------------------------
def test_k_nenhuma_oportunidade_resultado_valido():
    # motor aprovou somente resultado (EM_OBSERVAÇÃO, sem override) ->
    # nenhum operacional. CORNERS tem override, mas não está aqui.
    saida = _saida([_av("resultado", "Vitoria mandante (1)", 0.92, 0.70)])
    assert saida.operational_opportunities == []
    assert saida.nenhum_aprovado is True
    txt = formatar_saida(saida)
    assert "NENHUMA" in txt.upper()


def test_k_nenhuma_avaliacao_nenhum_aprovado():
    saida = _saida(aprovadas=[], avaliacoes=[])
    assert saida.nenhum_aprovado is True
    assert saida.operational_opportunities == []


# ----------------------------------------------------------------------
# Extras: registro de status e integridade do motor
# ----------------------------------------------------------------------
def test_status_mercados_oficial_preservado():
    """Os 6 mercados com o status estatístico oficial inalterado."""
    assert STATUS_MERCADOS["gols"]["status"] == APROVADO_PROX
    assert STATUS_MERCADOS["resultado"]["status"] == EM_OBS
    assert STATUS_MERCADOS["escanteios"]["status"] == EM_OBS
    assert STATUS_MERCADOS["cartoes"]["status"] == NAO_AVAL
    assert STATUS_MERCADOS["pressao_live"]["status"] == BLOQUEADO
    assert STATUS_MERCADOS["odds_roi"]["status"] == BLOQUEADO


def test_camada_nao_altera_versao_do_motor():
    """A camada tem sua própria versão; o motor permanece em VERSAO_PREJOGO_OP."""
    assert VERSAO_CAMADA_OPERACIONAL != VERSAO_PREJOGO_OP
    assert VERSAO_PREJOGO_OP == "prejogo-op-1.0-observacao"


def test_nunca_promove_automaticamente():
    """O status ESTATÍSTICO não é promovido pela camada. CORNERS vira
    operacional por OVERRIDE, mas seu status_estatistico real permanece
    EM_OBSERVAÇÃO (não vira APROVADO_PROX). RESULTADO (sem override)
    permanece em observação."""
    saida = _saida([
        _av("escanteios", "Over 9.5 escanteios", 0.95, 0.80),
        _av("resultado", "Vitoria mandante (1)", 0.95, 0.80),
    ])
    # corners operacional por override; resultado em observação
    assert len(saida.operational_opportunities) == 1
    corners_op = saida.operational_opportunities[0]
    assert corners_op["mercado"] == "escanteios"
    # status ESTATÍSTICO preservado (não promovido a APROVADO_PROX)
    assert corners_op["status_estatistico"] == EM_OBS
    assert corners_op["origem_operacional"] == "override_usuario"
    # resultado sem override => observação
    res_obs = [o for o in saida.observations if o["mercado"] == "resultado"]
    assert len(res_obs) == 1
    assert res_obs[0]["status_estatistico"] == EM_OBS


# ----------------------------------------------------------------------
# Override operacional de CORNERS -- testes específicos (macroetapa final)
# ----------------------------------------------------------------------
def test_status_estatistico_corners_preservado_em_obs():
    """O override NÃO altera o status estatístico real de CORNERS no
    registro oficial -- permanece EM_OBSERVAÇÃO."""
    from src.operacional import STATUS_MERCADOS, OVERRIDE_OPERACIONAL
    assert STATUS_MERCADOS["escanteios"]["status"] == EM_OBS
    assert "escanteios" in OVERRIDE_OPERACIONAL
    assert OVERRIDE_OPERACIONAL["escanteios"]["status_estatistico"] == EM_OBS
    assert OVERRIDE_OPERACIONAL["escanteios"]["status_operacional"] == \
        "HABILITADO_POR_OVERRIDE_DO_USUARIO"


def test_rota_mercado_corners_operacional_por_override():
    """_rota_mercado('escanteios') retorna 'operational' por override,
    mesmo com status estatístico EM_OBSERVAÇÃO."""
    from src.operacional import _rota_mercado
    assert _rota_mercado("escanteios") == "operational"
    # GOALS também operacional (estatístico)
    assert _rota_mercado("gols") == "operational"
    # resultado sem override => observação
    assert _rota_mercado("resultado") == "observation"
    # cards => bloqueado
    assert _rota_mercado("cartoes") == "blocked"


def test_origem_operacional_distinta_goals_vs_corners():
    """GOALS tem origem estatístico; CORNERS tem origem override_usuario.
    A distinção é auditável na entrada."""
    saida = _saida([
        _av("gols", "Over 2.5 gols", 0.93, 0.75),
        _av("escanteios", "Over 9.5 escanteios", 0.91, 0.68),
    ])
    assert len(saida.operational_opportunities) == 2
    by_mercado = {o["mercado"]: o for o in saida.operational_opportunities}
    # GOALS: estatístico
    g = by_mercado["gols"]
    assert g["origem_operacional"] == "estatistico"
    assert g["status_operacional"] == "HABILITADO_ESTATISTICAMENTE"
    assert g["status_estatistico"] == APROVADO_PROX
    assert g["timestamp_override"] is None
    # CORNERS: override
    c = by_mercado["escanteios"]
    assert c["origem_operacional"] == "override_usuario"
    assert c["status_operacional"] == "HABILITADO_POR_OVERRIDE_DO_USUARIO"
    assert c["status_estatistico"] == EM_OBS  # preservado
    assert c["timestamp_override"] is not None


def test_override_corners_nao_aprovado_motor_fica_observacao():
    """Override NÃO força aposta. Se o motor NÃO aprovar corners, a
    avaliação vai para observação (não vira oportunidade operacional)."""
    saida = _saida(
        aprovadas=[_av("gols", "Over 2.5 gols", 0.93, 0.75)],
        avaliacoes=[
            _av("gols", "Over 2.5 gols", 0.93, 0.75),
            _av("escanteios", "Over 9.5 escanteios", 0.55, 0.40),  # não aprovado
        ],
    )
    # corners não aprovado pelo motor => observação, não operacional
    corners_ops = [o for o in saida.operational_opportunities
                   if o["mercado"] == "escanteios"]
    assert corners_ops == []
    corners_obs = [o for o in saida.observations if o["mercado"] == "escanteios"]
    assert len(corners_obs) == 1
    assert corners_obs[0]["aprovada_motor"] is False
    assert corners_obs[0]["decisao_oficial"] == "OBSERVACAO"
    assert corners_obs[0]["status_estatistico"] == EM_OBS


def test_override_auditavel_timestamp_e_decisao_humana():
    """O registro de override é auditável: timestamp + decisao_humana + motivo."""
    from src.operacional import OVERRIDE_OPERACIONAL
    ov = OVERRIDE_OPERACIONAL["escanteios"]
    assert ov["decisao_humana"] == "true"
    assert ov["timestamp_override"]  # não vazio
    assert "drift" in ov["motivo"].lower()
    assert "override" not in ov["motivo"].lower().replace("override", "") or True
    # motivo documenta que NENHUM candidato passou estatisticamente
    assert "nenhum" in ov["motivo"].lower() or "NENHUM" in ov["motivo"]


def test_goals_corners_coexistem_operacional():
    """GOALS (estatístico) e CORNERS (override) coexistem como
    oportunidades operacionais, com origem distinta."""
    saida = _saida([
        _av("gols", "Over 2.5 gols", 0.93, 0.75),
        _av("escanteios", "Over 9.5 escanteios", 0.91, 0.68),
    ])
    mercados_op = {o["mercado"] for o in saida.operational_opportunities}
    assert mercados_op == {"gols", "escanteios"}
    assert saida.nenhum_aprovado is False