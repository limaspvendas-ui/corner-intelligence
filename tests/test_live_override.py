"""Testes determinísticos do MODO TESTE LIVE (override auditável + SaidaOficial live).

Cobre a extensão live da camada operacional (src/operacional.py) sem chamar a
API: constrói Varredura/Candidato/Avaliacao/LiveSnapshot live sintéticas e
exercita _construir_saida_live (função pura da saída do motor live).

Princípios validados (A-N):
  A) LIVE permanece NÃO VALIDADO estatisticamente (pressao_live BLOQUEADO).
  B) LIVE fica HABILITADO_PARA_TESTE_POR_OVERRIDE_DO_USUARIO.
  C) modo_teste_live = true; SaidaOficial.mode="live".
  D) override possui timestamp, motivo e decisao_humana=true.
  E) override de modo NÃO cria sinal (aprovadas=[] => sem operational).
  F) scanner sem sinal => nenhum_aprovado; sem jogos live => mensagem.
  G) dados stale => nenhuma oportunidade (DADO_LIVE_DESATUALIZADO).
  H) NULL não vira zero (minuto None => NÃO DISPONÍVEL, não 0).
  I) snapshot append-only: varredura_live não grava histórico (record=False).
  J) mesma entrada => mesma decisão (determinismo).
  K) nenhuma aposta financeira executada.
  L) GOALS pré-live não sofre regressão (defaults aditivos).
  M) CORNERS pré-live não sofre regressão (override de mercado intacto).
  N) motor matemático pré-live permanece intacto (versões inalteradas).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from src.live import LiveSnapshot, now_brt
from src.live_opportunity import (
    Avaliacao,
    Candidato,
    OddUsada,
    Varredura,
)
from src.operacional import (
    MODO_TESTE_LIVE,
    OVERRIDE_OPERACIONAL,
    STATUS_MERCADOS,
    STATUS_OP_LIVE_TESTE,
    TIMESTAMP_OVERRIDE_LIVE,
    MOTIVO_OVERRIDE_LIVE,
    VERSAO_CAMADA_LIVE,
    VERSAO_CAMADA_OPERACIONAL,
    VERSAO_LIVE_OP,
    SaidaOficial,
    _construir_saida,
    _construir_saida_live,
    _rota_mercado,
    formatar_saida_live,
    varredura_live,
)
from src.prejogo_opportunity import VERSAO_PREJOGO_OP, VarreduraPreJogo
from src.validacao_multifonte import APROVADO_PROX, BLOQUEADO, EM_OBS


# ----------------------------------------------------------------------
# Helpers sintéticos (sem API)
# ----------------------------------------------------------------------
def _snap(fixture_id=9001, home="Casa FC", away="Fora FC",
          league="Premier League", league_id=39,
          date_local="2026-09-14T20:00:00", status="2H", elapsed=55):
    return LiveSnapshot(
        fixture_id=fixture_id, league_name=league, country="England",
        season=2026, round="Regular Season - 5", date_local=date_local,
        home_team_id=10, home_team_name=home,
        away_team_id=20, away_team_name=away,
        goals_home=1, goals_away=1, halftime_home=1, halftime_away=0,
        status=status, elapsed=elapsed, league_id=league_id,
        stats_home={}, stats_away={}, stats_1h={}, stats_2h={},
        events=[], collected_at="", has_stats=True,
    )


def _av(mercado, linha, prob, confianca=0.72, fixture_id=9001,
        minuto=55, status="2H", placar="1x1", odd=None,
        jogo="Casa FC x Fora FC", competicao="Premier League"):
    return Avaliacao(
        jogo=jogo, fixture_id=fixture_id, competicao=competicao,
        minuto=minuto, status=status, placar=placar,
        mercado=mercado, linha=linha, prob=prob,
        sustentacao={"n_home": 10, "n_away": 10}, confianca=confianca,
        conf_componentes={"amostra": 0.4, "benchmark": 0.4, "h2h": 0.2},
        riscos=[], odd=odd, odds_live_existentes=odd is not None,
        auditoria=[], aprovada=True, rejeicao=None,
    )


def _cand(snap, avaliacoes):
    return Candidato(snapshot=snap, avaliacoes=avaliacoes)


def _hora_fresh():
    return now_brt().replace(tzinfo=None).strftime("%d/%m/%Y %H:%M:%S")


def _hora_stale(minutes=10):
    t = now_brt().replace(tzinfo=None) - timedelta(minutes=minutes)
    return t.strftime("%d/%m/%Y %H:%M:%S")


def _varredura(cands, aprovadas, observacao=None, total_ao_vivo=1, hora=None):
    return Varredura(
        hora=hora or _hora_fresh(),
        total_ao_vivo=total_ao_vivo,
        total_elegiveis=len(cands),
        total_triados=len(cands),
        candidatos=cands,
        aprovadas=aprovadas,
        observacao=observacao or [],
    )


def _saida_live(cands, aprovadas, observacao=None, total_ao_vivo=1,
                hora=None, generated_at="2026-09-14 20:30:00"):
    v = _varredura(cands, aprovadas, observacao, total_ao_vivo, hora)
    return _construir_saida_live(v, generated_at)


# ----------------------------------------------------------------------
# A. LIVE permanece NÃO VALIDADO estatisticamente
# ----------------------------------------------------------------------
def test_a_live_nao_validado_estatisticamente():
    assert MODO_TESTE_LIVE["status_estatistico"] == BLOQUEADO
    assert STATUS_MERCADOS["pressao_live"]["status"] == BLOQUEADO
    assert "pressao_live" not in OVERRIDE_OPERACIONAL


# ----------------------------------------------------------------------
# B. LIVE HABILITADO_PARA_TESTE_POR_OVERRIDE_DO_USUARIO
# ----------------------------------------------------------------------
def test_b_live_habilitado_por_override():
    assert MODO_TESTE_LIVE["status_operacional"] == STATUS_OP_LIVE_TESTE
    assert STATUS_OP_LIVE_TESTE == "HABILITADO_PARA_TESTE_POR_OVERRIDE_DO_USUARIO"
    snap = _snap()
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75)
    saida = _saida_live([_cand(snap, [av])], [av])
    assert saida.live["status_operacional"] == STATUS_OP_LIVE_TESTE


# ----------------------------------------------------------------------
# C. modo_teste_live = true; SaidaOficial mode="live"
# ----------------------------------------------------------------------
def test_c_modo_teste_live_true():
    assert MODO_TESTE_LIVE["modo_teste"] == "true"
    snap = _snap()
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75)
    saida = _saida_live([_cand(snap, [av])], [av])
    assert saida.mode == "live"
    assert saida.modo_teste is True
    assert saida.camada_version == VERSAO_CAMADA_LIVE
    assert saida.engine_version == VERSAO_LIVE_OP


# ----------------------------------------------------------------------
# D. override possui timestamp, motivo e decisao_humana=true
# ----------------------------------------------------------------------
def test_d_override_auditavel():
    assert MODO_TESTE_LIVE["decisao_humana"] == "true"
    assert MODO_TESTE_LIVE["override_operador"] == "true"
    assert MODO_TESTE_LIVE["timestamp_override"] == TIMESTAMP_OVERRIDE_LIVE
    assert MODO_TESTE_LIVE["motivo"] == MOTIVO_OVERRIDE_LIVE
    assert TIMESTAMP_OVERRIDE_LIVE  # não vazio
    assert "liberação" in MOTIVO_OVERRIDE_LIVE.lower() or "Lib" in MOTIVO_OVERRIDE_LIVE
    snap = _snap()
    saida = _saida_live([_cand(snap, [])], [], total_ao_vivo=1)
    assert saida.live["decisao_humana"] == "true"
    assert saida.live["override_operador"] == "true"
    assert saida.live["timestamp_override"] == TIMESTAMP_OVERRIDE_LIVE


# ----------------------------------------------------------------------
# E. override de modo NÃO cria sinal
# ----------------------------------------------------------------------
def test_e_override_nao_cria_sinal():
    snap = _snap()
    saida = _saida_live([_cand(snap, [])], [], total_ao_vivo=1)
    assert saida.operational_opportunities == []
    assert saida.nenhum_aprovado is True


# ----------------------------------------------------------------------
# F. scanner sem sinal => nenhum_aprovado; sem jogos => mensagem
# ----------------------------------------------------------------------
def test_f_scanner_sem_sinal_mensagem_valida():
    saida = _saida_live([], [], total_ao_vivo=0)
    assert saida.nenhum_aprovado is True
    assert saida.data_status == "SEM_JOGO_LIVE"
    txt = formatar_saida_live(saida)
    assert "NENHUM JOGO AO VIVO ELEGÍVEL" in txt
    snap = _snap()
    saida2 = _saida_live([_cand(snap, [])], [], total_ao_vivo=1)
    txt2 = formatar_saida_live(saida2)
    assert "NENHUMA APROVADA PELO MOTOR" in txt2


# ----------------------------------------------------------------------
# G. dados stale => nenhuma oportunidade (DADO_LIVE_DESATUALIZADO)
# ----------------------------------------------------------------------
def test_g_stale_nenhuma_oportunidade():
    snap = _snap()
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75)
    saida = _saida_live([_cand(snap, [av])], [av], hora=_hora_stale(10))
    assert saida.live["data_freshness"] == "STALE"
    assert saida.operational_opportunities == []
    stale_obs = [o for o in saida.observations
                 if o["decisao_oficial"] == "DADO_LIVE_DESATUALIZADO"]
    assert len(stale_obs) == 1
    txt = formatar_saida_live(saida)
    assert "DADO LIVE DESATUALIZADO" in txt.upper()


# ----------------------------------------------------------------------
# H. NULL não vira zero (minuto None => NÃO DISPONÍVEL, não 0)
# ----------------------------------------------------------------------
def test_h_null_nao_vira_zero():
    snap = _snap()
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75, minuto=None)
    saida = _saida_live([_cand(snap, [av])], [av])
    o = saida.operational_opportunities[0]
    assert o["live_minute"] is None  # NULL != ZERO
    assert o["prob"] == 0.93  # prob do motor preservada
    txt = formatar_saida_live(saida)
    assert "NÃO DISPONÍVEL" in txt


# ----------------------------------------------------------------------
# I. snapshot append-only: varredura_live não grava histórico
# ----------------------------------------------------------------------
def test_i_snapshot_append_only(monkeypatch):
    snap = _snap()
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75)
    v = _varredura([_cand(snap, [av])], [av])
    calls = []

    def _stub(client, deep=2, mercados=None):
        calls.append((deep, mercados))
        return v

    monkeypatch.setattr(
        "src.live_opportunity.scan_live_opportunities", _stub)
    saida = varredura_live(object(), mercados=None)
    assert saida.mode == "live"
    assert len(calls) == 1
    deep, mercados = calls[0]
    assert mercados is None  # camada só repassa mercados; sem flag de gravação
    d = saida.to_dict()
    assert "aposta_executada" not in d
    assert "registro_validacao" not in d


# ----------------------------------------------------------------------
# J. determinismo: mesma entrada => mesma saída
# ----------------------------------------------------------------------
def test_j_determinismo(monkeypatch):
    monkeypatch.setattr("src.operacional._projeto_hash", lambda: "FIXED")
    snap = _snap()
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75)
    av2 = _av("escanteios", "Over 9.5 escanteios", 0.91, 0.68)
    cand = _cand(snap, [av, av2])
    s1 = _saida_live([cand], [av, av2])
    s2 = _saida_live([cand], [av, av2])
    assert s1.to_dict() == s2.to_dict()


# ----------------------------------------------------------------------
# K. nenhuma aposta financeira executada
# ----------------------------------------------------------------------
def test_k_nenhuma_aposta_financeira():
    snap = _snap()
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75)
    saida = _saida_live([_cand(snap, [av])], [av])
    d = saida.to_dict()
    assert saida.modo_teste is True
    for forbidden in ("aposta_executada", "aposta_realizada",
                      "ordem_enviada", "bookmaker_integration"):
        assert forbidden not in d
        assert forbidden not in d.get("live", {})
    txt = formatar_saida_live(saida)
    assert "MODO TESTE" in txt.upper()


# ----------------------------------------------------------------------
# L. GOALS pré-live não sofre regressão (defaults aditivos)
# ----------------------------------------------------------------------
def test_l_goals_prelive_preservado():
    s = SaidaOficial(None, "x", "t", VERSAO_PREJOGO_OP,
                     VERSAO_CAMADA_OPERACIONAL, None, "OK")
    assert s.mode == "prejogo"
    assert s.live == {}
    assert s.modo_teste is False
    assert _rota_mercado("gols") == "operational"
    assert STATUS_MERCADOS["gols"]["status"] == APROVADO_PROX


# ----------------------------------------------------------------------
# M. CORNERS pré-live não sofre regressão (override de mercado intacto)
# ----------------------------------------------------------------------
def test_m_corners_prelive_preservado():
    assert _rota_mercado("escanteios") == "operational"
    assert STATUS_MERCADOS["escanteios"]["status"] == EM_OBS
    assert "escanteios" in OVERRIDE_OPERACIONAL
    snap = _snap()
    av = _av("escanteios", "Over 9.5 escanteios", 0.91, 0.68)
    saida = _saida_live([_cand(snap, [av])], [av])
    c = [o for o in saida.operational_opportunities
         if o["mercado"] == "escanteios"][0]
    assert c["origem_operacional"] == "override_usuario"
    assert c["status_operacional"] == "HABILITADO_POR_OVERRIDE_DO_USUARIO"
    assert c["status_estatistico"] == EM_OBS


# ----------------------------------------------------------------------
# N. motor matemático pré-live permanece intacto
# ----------------------------------------------------------------------
def test_n_motor_prelive_intacto():
    assert VERSAO_PREJOGO_OP == "prejogo-op-1.0-observacao"
    assert VERSAO_CAMADA_OPERACIONAL == "operacional-1.1-override"
    v = VarreduraPreJogo(espec="x", fixture=None, motivo_sem_jogo="sem jogo")
    s = _construir_saida(v, "2026-09-14 20:30:00")
    assert s.mode == "prejogo"
    assert s.live == {}
    assert s.modo_teste is False
    assert s.camada_version == VERSAO_CAMADA_OPERACIONAL
    assert s.engine_version == VERSAO_PREJOGO_OP


# ----------------------------------------------------------------------
# Extra: GOALS live roteia como estatístico (não override)
# ----------------------------------------------------------------------
def test_goals_live_origem_estatistico():
    snap = _snap()
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75)
    saida = _saida_live([_cand(snap, [av])], [av])
    g = saida.operational_opportunities[0]
    assert g["mercado"] == "gols"
    assert g["origem_operacional"] == "estatistico"
    assert g["status_operacional"] == "HABILITADO_ESTATISTICAMENTE"
    assert g["status_estatistico"] == APROVADO_PROX
    assert g["modo_teste"] is True
    assert g["source"] == "motor:" + VERSAO_LIVE_OP
    assert g["live_minute"] == 55
    assert g["score"] == "1x1"


# ----------------------------------------------------------------------
# Extra: odd live real é repassada sem recalculo
# ----------------------------------------------------------------------
def test_odd_live_repassada():
    snap = _snap()
    odd = OddUsada(bookmaker="Bet365", mercado_feed="Over 0.5",
                   value_feed="1.20", odd=1.20, update="2026-09-14 20:00",
                   implied=0.833, atual=True)
    av = _av("gols", "Over 0.5 gols", 0.93, 0.75, odd=odd)
    saida = _saida_live([_cand(snap, [av])], [av])
    g = saida.operational_opportunities[0]
    assert g["odd"] == 1.20
    assert g["classificacao"] == "OPORTUNIDADE COM ODD AO VIVO REAL"