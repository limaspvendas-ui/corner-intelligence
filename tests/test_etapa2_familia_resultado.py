"""ETAPA 2 DA AUDITORIA - alinhamento do status operacional da familia
RESULTADO (1X2 / Dupla Chance / DNB / Handicap Asiatico).

A Etapa 2 resolveu a inconsistencia: a familia RESULTADO tem logica
TECNICA (liquidacao + testes) mas NAO tem validacao ESTATISTICA formal
(calibracao com amostra madura - MIN_AMOSTRA nao atingida; a politica
permanente exige AH "somente quando validados"). Decisao conservadora:
RESULTADO permanece EXPERIMENTAL EM OBSERVACAO - nao e "recomendacao
operacional validada".

Esta suite garante que a decisao vale para o comportamento FUTURO, sem
tocar o historico congelado:
  1. toda avaliacao NOVA de resultado (pre-jogo e live) carrega o marcador
     RISCO_STATUS_RESULTADO (experimental em observacao, nao validada);
  2. o marcador e congelado no registro (trilha de riscos) - a
     recomendacao futura nunca e tratada silenciosamente como validada;
  3. o texto do marcador afirma explicitamente observacao + nao validada
     (nunca "validado"/"aprovado");
  4. as familias VALIDADAS (gols/escanteios) NAO carregam o marcador de
     resultado - nenhuma outra familia foi afetada;
  5. os calculos de 1X2/DC/DNB/AH continuam funcionando (1X2 soma 1,
     DC/DNB/AH no [0,1], DNB == AH 0.0);
  6. ausencia de dado continua virando lista vazia (None nunca vira 0);
  7. a previsao original de uma recomendacao resultado e IMUTAVEL no
     banco (trigger do SQLite);
  8. o historico de 5 recomendacoes de resultado (congelado ANTES desta
     etapa) permanece intacto e o marcador NAO foi retroaplicado.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from src.config import REGISTRY_DB_PATH
from src.live import LiveSnapshot, now_brt
from src.live_opportunity import Candidato, _avaliar_resultado
from src.prejogo_opportunity import avaliar_pregame, registrar_aprovadas_prejogo
from src.resultado import (
    RISCO_STATUS_RESULTADO,
    avaliar_resultado_prejogo,
    distribuicao_margem_prejogo,
    lambdas_prejogo,
    prob_1x2,
    prob_ah,
    prob_dnb,
    prob_dupla_chance,
)
from src.registry import RegistroRecomendacoes

_BENCH_GOLS = {"partidas_validas": 40, "describe": {"media": 2.8}}
_BENCH_ESC = {"partidas_validas": 40, "describe": {"media": 10.0}}

# Versoes historicas congeladas ANTES da Etapa 2 (imutaveis no ledger).
_VERSOES_HISTORICAS_RESULTADO = (
    "live-op-2.3-familia-resultado",
    "prejogo-politica-3.2-observacao",
)


# ----------------------------------------------------------------------
# Builders (mesmo formato real dos objetos do motor)
# ----------------------------------------------------------------------
def _game(gf, gc, casa):
    return SimpleNamespace(
        played_at_home=casa, goals_for=gf, goals_against=gc,
        corners_for=6, corners_against=4, yellow_for=2,
        date="2026-08-20T16:00:00-03:00",
    )


def _hist(n=10):
    games_h = [_game(1.8, 1.0, True) for _ in range(n)]
    games_a = [_game(1.0, 1.3, False) for _ in range(n)]
    return {"games_home": games_h, "games_away": games_a,
            "n_home": n, "n_away": n}


def _snap(status="2H", elapsed=47, gh=1, ga=1):
    return LiveSnapshot(
        fixture_id=700, league_name="Serie A", country="Brazil",
        season=2026, round="Regular Season - 26",
        date_local="2026-09-06T19:30:00-03:00",
        home_team_id=131, home_team_name="Corinthians",
        away_team_id=132, away_team_name="Chapecoense-sc",
        goals_home=gh, goals_away=ga, halftime_home=0, halftime_away=1,
        status=status, elapsed=elapsed, league_id=71,
        stats_home={"Corner Kicks": 6, "Total Shots": 12,
                    "Ball Possession": "58%", "Red Cards": 0},
        stats_away={"Corner Kicks": 4, "Total Shots": 8,
                    "Ball Possession": "42%", "Red Cards": 0},
        stats_1h={}, stats_2h={}, events=[],
        collected_at=now_brt().strftime("%d/%m/%Y %H:%M:%S"),
        has_stats=True,
    )


def _cand(snap, n=10):
    cand = Candidato(snapshot=snap)
    cand.historico = _hist(n)
    cand.benchmark_gols = _BENCH_GOLS
    cand.odds = None
    return cand


def _fx(fixture_id=9001):
    return SimpleNamespace(
        fixture_id=fixture_id, league_name="Serie A",
        home_team_name="Time A", away_team_name="Time B",
    )


# ----------------------------------------------------------------------
# 1/2. Marcador experimental em toda avaliacao NOVA de resultado
# ----------------------------------------------------------------------
def test_resultado_prejogo_carrega_marcador_experimental():
    """Toda avaliacao pre-jogo de resultado carrega o marcador de
    observacao - nao e promovida a validada silenciosamente."""
    aves = avaliar_resultado_prejogo(_hist(), _BENCH_GOLS, h2h_n=5)
    assert aves, "deve produzir avaliacoes com historico+benchmark"
    for av in aves:
        assert av.mercado == "resultado"
        assert RISCO_STATUS_RESULTADO in av.riscos


def test_resultado_live_carrega_marcador_experimental():
    """Toda avaliacao live de resultado carrega o marcador de
    observacao."""
    aves = _avaliar_resultado(_cand(_snap()))
    assert aves, "deve produzir avaliacoes live com historico+placar"
    for av in aves:
        assert av.mercado == "resultado"
        assert RISCO_STATUS_RESULTADO in av.riscos


def test_marcador_experimental_diz_nao_validada():
    """O texto do marcador afirma explicitamente observacao + nao
    validada - nunca as palavras 'validado'/'aprovado'."""
    texto = RISCO_STATUS_RESULTADO.lower()
    assert "observacao" in texto
    assert "nao operacional validada" in texto
    assert "validado" not in texto
    assert "aprovado" not in texto


# ----------------------------------------------------------------------
# 2. Marcador congelado no registro (recomendacao futura)
# ----------------------------------------------------------------------
def test_marcador_congelado_no_registro_prejogo(tmp_path):
    """O marcador experimental e congelado na trilha de riscos do
    registro - a recomendacao FUTURA de resultado entra no ledger ja
    rotulada como observacional, nunca silenciosamente validada."""
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    aves = avaliar_resultado_prejogo(_hist(), _BENCH_GOLS, h2h_n=5)
    alvo = max(aves, key=lambda a: a.prob)
    regs = registrar_aprovadas_prejogo(
        reg, [alvo], _fx(), _hist(),
        versao="prejogo-politica-3.2-observacao",
    )
    assert regs and regs[0][1] is True
    rec = reg.obter(regs[0][0])
    assert rec["mercado"] == "resultado"
    joined = " ".join(rec["contradicoes_riscos"] or [])
    assert RISCO_STATUS_RESULTADO in joined


# ----------------------------------------------------------------------
# 3. Familias VALIDADAS nao carregam o marcador de resultado
# ----------------------------------------------------------------------
def test_familias_validadas_sem_marcador_resultado():
    """gols/escanteios (validados) NAO carregam o marcador experimental
    de resultado - nenhuma outra familia foi afetada pela Etapa 2."""
    aves = avaliar_pregame(_hist(), _BENCH_ESC, _BENCH_GOLS, h2h_n=5)
    assert aves, "motor validado continua produzindo linhas"
    for av in aves:
        assert av.mercado in ("escanteios", "gols")
        assert RISCO_STATUS_RESULTADO not in av.riscos


# ----------------------------------------------------------------------
# 4. Calculos 1X2/DC/DNB/AH continuam funcionando
# ----------------------------------------------------------------------
def test_calculos_resultado_continua_funcionando():
    """A capacidade estatistica e preservada: 1X2 soma 1 (distribuicao
    renormalizada), DC/DNB/AH no [0,1], DNB == AH 0.0 (mesmo mercado)."""
    lam_h, lam_a, _det = lambdas_prejogo(_hist(), _BENCH_GOLS)
    assert lam_h is not None and lam_a is not None
    dist = distribuicao_margem_prejogo(lam_h, lam_a)

    p1, px, p2 = prob_1x2(dist)
    assert p1 + px + p2 == pytest.approx(1.0, abs=1e-9)

    dc = prob_dupla_chance(dist)
    for v in dc.values():
        assert 0.0 <= v <= 1.0

    for lado in ("mandante", "visitante"):
        dnb = prob_dnb(dist, lado)
        ah0 = prob_ah(dist, lado, 0.0)
        assert dnb == ah0  # DNB == AH 0.0: mesmo mercado, mesmo numero
        assert 0.0 <= ah0 <= 1.0

    for linha in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5):
        for lado in ("mandante", "visitante"):
            p = prob_ah(dist, lado, linha)
            assert p is None or 0.0 <= p <= 1.0

    # avaliar_resultado_prejogo produz o leque completo de mercados
    aves = avaliar_resultado_prejogo(_hist(), _BENCH_GOLS, h2h_n=5)
    linhas = {av.linha for av in aves}
    assert any("Vitoria mandante" in l for l in linhas)   # 1X2
    assert any("Empate" in l for l in linhas)
    assert any("Dupla chance" in l for l in linhas)       # DC
    assert any(l.startswith("DNB") for l in linhas)       # DNB
    assert any(l.startswith("AH ") for l in linhas)       # AH


# ----------------------------------------------------------------------
# 5. None nunca vira zero (ausencia de dado => lista vazia)
# ----------------------------------------------------------------------
def test_ausencia_de_dado_nao_vira_zero_prejogo():
    """Sem medias de gols no historico => lista VAZIA (None nunca vira
    zero, nada e inventado)."""
    vazios = {"games_home": [], "games_away": [],
              "n_home": 0, "n_away": 0}
    assert avaliar_resultado_prejogo(vazios, _BENCH_GOLS) == []


def test_ausencia_de_dado_nao_vira_zero_live():
    """Live sem historico de gols => lista VAZIA (nunca 0-0 inventado)."""
    cand = _cand(_snap())
    cand.historico = {"games_home": [], "games_away": [],
                      "n_home": 0, "n_away": 0}
    assert _avaliar_resultado(cand) == []


# ----------------------------------------------------------------------
# 6. Previsao original imutavel no banco (trigger do SQLite)
# ----------------------------------------------------------------------
def test_previsao_original_resultado_imutavel_no_banco(tmp_path):
    """A previsao original de uma recomendacao resultado NAO pode ser
    alterada (trigger do SQLite) - inclusive o marcador experimental,
    uma vez congelado, permanece."""
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    aves = avaliar_resultado_prejogo(_hist(), _BENCH_GOLS, h2h_n=5)
    alvo = max(aves, key=lambda a: a.prob)
    regs = registrar_aprovadas_prejogo(
        reg, [alvo], _fx(), _hist(),
        versao="prejogo-politica-3.2-observacao",
    )
    rid = regs[0][0]
    antes = reg.obter(rid)
    # tentativa de alterar a previsao original => ABORT pelo trigger
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(str(tmp_path / "reg.db")) as conn:
            conn.execute(
                "UPDATE recomendacoes SET mercado = 'gols' WHERE id = ?",
                (rid,),
            )
    # registro intacto: previsao e marcador preservados
    depois = reg.obter(rid)
    assert depois["mercado"] == antes["mercado"] == "resultado"
    assert depois["linha"] == antes["linha"]
    assert depois["probabilidade"] == antes["probabilidade"]
    assert RISCO_STATUS_RESULTADO in " ".join(
        depois["contradicoes_riscos"] or [])


# ----------------------------------------------------------------------
# 7. Historico congelado permanece imutavel e sem marcador retroativo
# ----------------------------------------------------------------------
@pytest.mark.skipif(
    not REGISTRY_DB_PATH.exists(),
    reason="registro permanente ausente neste ambiente",
)
def test_historico_resultado_imutavel_e_sem_marcador_retroativo():
    """As 5 recomendacoes historicas de resultado (congeladas ANTES da
    Etapa 2) permanecem intactas: mercado/linha/versao/probabilidade
    originais, e o marcador RISCO_STATUS_RESULTADO NAO foi retroaplicado
    - prova de imutabilidade e de que o marcador vale somente para o
    comportamento FUTURO. Novas recomendacoes (posteriores) carregarao o
    marcador, mas as 5 historicas jamais serao tocadas."""
    with sqlite3.connect(str(REGISTRY_DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT id, tipo, versao_analise, mercado, linha, "
            "probabilidade, contradicoes_riscos FROM recomendacoes "
            "WHERE mercado = 'resultado' ORDER BY id"
        ).fetchall()

    # sem marcador = congeladas antes da Etapa 2 (as 5 historicas)
    historicas = [
        r for r in rows
        if RISCO_STATUS_RESULTADO not in (r[6] or "")
    ]
    assert len(historicas) == 5, (
        f"esperado 5 historicas sem marcador, achei {len(historicas)}; "
        "historico foi alterado ou marcador foi retroaplicado"
    )
    for _id, tipo, versao, mercado, linha, prob, cri in historicas:
        assert mercado == "resultado"
        assert versao in _VERSOES_HISTORICAS_RESULTADO
        assert prob is not None and 0.0 < prob <= 1.0
        # marcador novo ausente: registro congelado antes da etapa
        assert RISCO_STATUS_RESULTADO not in (cri or "")