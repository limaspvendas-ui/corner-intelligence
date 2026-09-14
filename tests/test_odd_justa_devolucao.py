# -*- coding: utf-8 -*-
"""BLOCO A DA ETAPA 2 (09/09/2026) - ODD JUSTA COM DEVOLUCAO E ODD
MINIMA ACEITAVEL (src/resultado.py decomposicao_ah +
src/politica_operacional.py).

Os 20 casos obrigatorios do operador + o exemplo auditado:
probabilidade de vitoria 0.5822, probabilidade de empate/devolucao
0.2345 => a odd justa de DNB tem que dar aproximadamente 1.31, e NUNCA
1.72 (o antigo 1/prob).

Regras testadas:
  - mercado SEM devolucao (Over/Under X,5, 1X2, Dupla Chance): odd
    justa continua EXATAMENTE 1/prob;
  - DNB e AH 0.0: odd justa = (1 - P_devolucao)/P_vitoria - a MESMA
    formula validada de src/valor_aposta.odd_justa;
  - linhas de QUARTO (+/-0.25, +/-0.75, +/-1.25): decomposicao EXATA da
    liquidacao (odd = 1 + perda/ganho, com meia vitoria/meia perda a
    0.5) - nunca aproximada por 1/prob;
  - linha sem calculo exato possivel => ODD JUSTA NAO CALCULAVEL -
    DADOS INSUFICIENTES (nunca aproximada);
  - ODD MINIMA ACEITAVEL = odd justa + 5% de margem operacional,
    arredondada PARA CIMA em centavos; o 1.15 continua apenas como
    PISO DE UTILIDADE, nunca apresentado como odd minima;
  - situacoes: OPORTUNIDADE ESTATISTICA - AGUARDANDO ODD / REPROVADA -
    SEM VALOR SUFICIENTE / APROVAVEL / BAIXA UTILIDADE OPERACIONAL /
    NAO AVALIAVEL;
  - edge e valor esperado SO com odd real - nunca inventados.

Sem requisicoes a API: distribuicoes 100% sinteticas.
"""

import pytest
from types import SimpleNamespace

from src.handicap import settle
from src.politica_operacional import (
    MARGEM_OPERACIONAL,
    MARCA_BAIXA_UTILIDADE,
    ODD_JUSTA_NAO_CALCULAVEL,
    ODD_MIN_OPERACIONAL,
    ODD_REAL_INDISPONIVEL,
    SIT_APROVAVEL,
    SIT_BAIXA_UTILIDADE,
    SIT_NAO_AVALIAVEL,
    SIT_OPORTUNIDADE,
    SIT_REPROVADA_SEM_VALOR,
    avaliar_utilidade,
    format_bloco_operacional,
    odd_justa,
    odd_justa_avaliacao,
    odd_justa_equilibrio,
    odd_minima_aceitavel,
    prob_devolucao_avaliacao,
    selecionar_linha_operacional,
)
from src.resultado import (
    avaliar_resultado_prejogo,
    decomposicao_ah,
    fmt_ah,
    prob_ah,
)
from src.valor_aposta import odd_justa as odd_justa_valor_aposta

# Distribuicao de margem sintetica (mandante - visitante), soma 1.00
DIST5 = {-2: 0.10, -1: 0.20, 0: 0.30, 1: 0.25, 2: 0.15}
# Exemplo auditado pelo operador (Etapa 2): P(vitoria) 0.5822,
# P(empate) 0.2345, P(derrota) 0.1833
DIST_AUDIT = {1: 0.5822, 0: 0.2345, -1: 0.1833}
# Favorito com empate: P(vitoria) 0.72, P(devolucao) 0.15, P(perda) 0.13
DIST_WIN = {2: 0.22, 1: 0.50, 0: 0.15, -1: 0.09, -2: 0.04}


def _gl(dist, lado, linha):
    """Ganho/perda/devolucao calculados de forma INDEPENDENTE a partir
    do PRIMITIVO validado (src/handicap.py settle) - a composicao do
    decomposicao_ah e verificada contra este somatorio."""
    g = l = d = 0.0
    for d_raw, p in dist.items():
        margem = d_raw if lado == "mandante" else -d_raw
        s = settle(linha, margem)
        if s.net > 0:
            g += p * s.net
        elif s.net < 0:
            l += p * (-s.net)
        else:
            d += p
    return g, l, d


def _av_ah(lado, linha, dist):
    """Avaliacao sintetica de linha DNB/AH no formato real, com a
    decomposicao da liquidacao gerada pelo PROPRIO motor (exatamente
    como avaliar_resultado_prejogo agora faz)."""
    g, l, d = _gl(dist, lado, linha)
    linha_txt = (
        f"DNB {lado} (empate anula)" if linha == 0.0
        else f"AH {lado} {fmt_ah(linha)} (90 minutos)"
    )
    sust = {"lambda_por90": 2.6}
    decomp = decomposicao_ah(dist, lado, linha)
    if decomp is not None:
        sust["equilibrio_liquidacao"] = decomp
    return SimpleNamespace(
        mercado="resultado",
        linha=linha_txt,
        prob=round(g, 4),
        confianca=0.88,
        riscos=[],
        conf_componentes={"amostra_historica": 1.0},
        sustentacao=sust,
    )


def _av_binario(prob, linha="Under 4.5 gols (total do jogo)"):
    return SimpleNamespace(
        mercado="gols",
        linha=linha,
        prob=prob,
        confianca=0.88,
        riscos=[],
        conf_componentes={"amostra_historica": 1.0},
        sustentacao={"lambda_por90": 3.0},
    )


# ----------------------------------------------------------------------
# 1. Mercado binario SEM devolucao: 1/prob continua exato
# ----------------------------------------------------------------------
def test_01_binario_sem_devolucao_usa_1_sobre_prob():
    av = _av_binario(0.80)
    assert odd_justa_avaliacao(av) == odd_justa(0.80) == 1.25
    u = avaliar_utilidade(av.prob, av=av)
    assert u.prob_devolucao == 0.0
    assert u.odd_minima == 1.32          # 1.25 * 1.05 = 1.3125 -> 1.32
    assert u.situacao == SIT_OPORTUNIDADE
    assert u.edge is None and u.ev is None  # sem odd real: nunca inventa


# ----------------------------------------------------------------------
# 2. DNB com empate: o exemplo auditado (1.31, NUNCA 1.72)
# ----------------------------------------------------------------------
def test_02_dnb_exemplo_auditado_odd_justa_131_nao_172():
    av = _av_ah("mandante", 0.0, DIST_AUDIT)
    assert av.prob == 0.5822
    assert prob_devolucao_avaliacao(av) == 0.2345
    # valor ANTIGO errado (1/prob) - nao pode mais ser usado no DNB
    assert odd_justa(av.prob) == 1.72
    # valor CORRETO: (1 - P_devolucao)/P_vitoria = 1 + perda/ganho
    assert odd_justa_avaliacao(av) == 1.31
    # mesma formula VALIDADA de src/valor_aposta (nunca duplicada)
    va = odd_justa_valor_aposta(w=0.5822, p=0.2345)
    assert odd_justa_equilibrio(0.5822, 0.1833) == round(va, 2) == 1.31


# ----------------------------------------------------------------------
# 3. AH 0.0 = DNB: mesma odd justa de equilibrio
# ----------------------------------------------------------------------
def test_03_ah_00_equivale_dnb_na_odd_justa():
    dnb = _av_ah("mandante", 0.0, DIST_AUDIT)
    ah = _av_ah("mandante", 0.0, DIST_AUDIT)
    ah.linha = f"AH mandante {fmt_ah(0.0)} (90 minutos)"
    assert odd_justa_avaliacao(dnb) == odd_justa_avaliacao(ah) == 1.31
    # a decomposicao confirma: ganho + perda + devolucao = 1
    eq = dnb.sustentacao["equilibrio_liquidacao"]
    assert round(eq["ganho"] + eq["perda"] + eq["devolucao"], 6) == 1.0


def _verifica_linha_ah(lado, linha, dist):
    """Motor vs somatorio independente: decomposicao, prob e odd justa."""
    decomp = decomposicao_ah(dist, lado, linha)
    g, l, d = _gl(dist, lado, linha)
    assert decomp == {
        "ganho": round(g, 6),
        "perda": round(l, 6),
        "devolucao": round(d, 6),
    }
    av = _av_ah(lado, linha, dist)
    assert av.prob == round(g, 4)
    assert odd_justa_avaliacao(av) == round(1.0 + l / g, 2)
    return av, g, l, d


# ----------------------------------------------------------------------
# 4. AH +0.25 (linha de quarto: decomposicao exata, nunca 1/prob)
# ----------------------------------------------------------------------
def test_04_ah_mais_025():
    av, g, l, d = _verifica_linha_ah("mandante", 0.25, DIST5)
    # m=0 e' MEIA VITORIA (+0.5): ganho 0.30*0.5 + 0.40 = 0.55 (nao 0.85)
    assert (g, l, d) == pytest.approx((0.55, 0.30, 0.0))
    assert odd_justa_avaliacao(av) == 1.55
    assert odd_justa(av.prob) == 1.82  # aproximacao antiga: ERRADA aqui


# ----------------------------------------------------------------------
# 5. AH -0.25
# ----------------------------------------------------------------------
def test_05_ah_menos_025():
    av, g, l, d = _verifica_linha_ah("mandante", -0.25, DIST5)
    # m=0 e' MEIA DERROTA (-0.5): perda 0.10+0.20+0.15 = 0.45
    assert (g, l, d) == pytest.approx((0.40, 0.45, 0.0))
    assert odd_justa_avaliacao(av) == round(1.0 + 0.45 / 0.40, 2)


# ----------------------------------------------------------------------
# 6. AH +0.75
# ----------------------------------------------------------------------
def test_06_ah_mais_075():
    av, g, l, d = _verifica_linha_ah("mandante", 0.75, DIST5)
    # m=-1 e' MEIA DERROTA: perda 0.10 + 0.20*0.5 = 0.20
    assert (g, l, d) == pytest.approx((0.70, 0.20, 0.0))
    assert odd_justa_avaliacao(av) == 1.29
    assert odd_justa(av.prob) == 1.43  # 1/prob: ERRADA aqui


# ----------------------------------------------------------------------
# 7. AH -0.75
# ----------------------------------------------------------------------
def test_07_ah_menos_075():
    av, g, l, d = _verifica_linha_ah("mandante", -0.75, DIST5)
    # m=0: derrota plena; m=1: MEIA VITORIA (+0.5)
    assert (g, l, d) == pytest.approx((0.275, 0.60, 0.0))
    assert odd_justa_avaliacao(av) == round(1.0 + 0.60 / 0.275, 2) == 3.18


# ----------------------------------------------------------------------
# 8. AH +1.00 (linha inteira COM devolucao)
# ----------------------------------------------------------------------
def test_08_ah_mais_100():
    av, g, l, d = _verifica_linha_ah("mandante", 1.0, DIST5)
    assert (g, l, d) == pytest.approx((0.70, 0.10, 0.20))  # m=-1 devolve
    assert odd_justa_avaliacao(av) == 1.14  # 1/prob daria 1.43: ERRADO
    # equivalencia com a formula validada (1 - devolucao)/vitoria
    assert odd_justa_avaliacao(av) == round(
        odd_justa_valor_aposta(w=0.70, p=0.20), 2
    )
    # odd justa abaixo do piso => BAIXA UTILIDADE mesmo com prob na janela
    u = avaliar_utilidade(av.prob, av=av)
    assert u.situacao == SIT_BAIXA_UTILIDADE
    assert not u.utilizavel


# ----------------------------------------------------------------------
# 9. AH -1.00
# ----------------------------------------------------------------------
def test_09_ah_menos_100():
    av, g, l, d = _verifica_linha_ah("mandante", -1.0, DIST5)
    assert (g, l, d) == pytest.approx((0.15, 0.60, 0.25))  # m=1 devolve
    assert odd_justa_avaliacao(av) == 5.00
    assert odd_justa_avaliacao(av) == round(
        odd_justa_valor_aposta(w=0.15, p=0.25), 2
    )


# ----------------------------------------------------------------------
# 10. AH +1.25
# ----------------------------------------------------------------------
def test_10_ah_mais_125():
    av, g, l, d = _verifica_linha_ah("mandante", 1.25, DIST5)
    # m=-1 e' MEIA VITORIA: ganho 0.70 + 0.20*0.5 = 0.80
    assert (g, l, d) == pytest.approx((0.80, 0.10, 0.0))
    assert odd_justa_avaliacao(av) == round(1.0 + 0.10 / 0.80, 2)


# ----------------------------------------------------------------------
# 11. AH -1.25
# ----------------------------------------------------------------------
def test_11_ah_menos_125():
    av, g, l, d = _verifica_linha_ah("mandante", -1.25, DIST5)
    # m=1 e' MEIA DERROTA: perda 0.60 + 0.25*0.5 = 0.725
    assert (g, l, d) == pytest.approx((0.15, 0.725, 0.0))
    assert odd_justa_avaliacao(av) == 5.83


# Linhas de MEIA sem devolucao: decomposicao exata == 1/prob
def test_ah_meia_linha_sem_devolucao_igual_1_sobre_prob():
    av, g, l, d = _verifica_linha_ah("mandante", 0.5, DIST5)
    assert (g, l, d) == pytest.approx((0.70, 0.30, 0.0))
    assert odd_justa_avaliacao(av) == odd_justa(0.70) == 1.43
    # lado visitante: mesmo teste de formula
    _verifica_linha_ah("visitante", 1.25, DIST5)


# ----------------------------------------------------------------------
# 12. Meia vitoria: conta 0.5 no ganho (nunca 1.0)
# ----------------------------------------------------------------------
def test_12_meia_vitoria_conta_meio_no_ganho():
    dist = {0: 0.60, -1: 0.40}
    decomp = decomposicao_ah(dist, "mandante", 0.25)
    # m=0 => MEIA VITORIA: ganho 0.60*0.5 = 0.30 (nao 0.60)
    assert decomp["ganho"] == 0.30
    assert decomp["perda"] == 0.40 and decomp["devolucao"] == 0.0
    av = _av_ah("mandante", 0.25, dist)
    assert odd_justa_avaliacao(av) == round(1.0 + 0.40 / 0.30, 2) == 2.33
    assert prob_ah(dist, "mandante", 0.25) == 0.30


# ----------------------------------------------------------------------
# 13. Meia perda: conta 0.5 na perda (nunca 1.0)
# ----------------------------------------------------------------------
def test_13_meia_perda_conta_meio_na_perda():
    dist = {0: 0.60, -1: 0.40}
    decomp = decomposicao_ah(dist, "mandante", 0.75)
    # m=-1 => MEIA DERROTA: perda 0.40*0.5 = 0.20 (nao 0.40)
    assert decomp["ganho"] == 0.60
    assert decomp["perda"] == 0.20 and decomp["devolucao"] == 0.0
    av = _av_ah("mandante", 0.75, dist)
    assert odd_justa_avaliacao(av) == round(1.0 + 0.20 / 0.60, 2) == 1.33


# ----------------------------------------------------------------------
# 14. Devolucao total: linha sem vitoria possivel => NAO CALCULAVEL
# ----------------------------------------------------------------------
def test_14_devolucao_total_odd_justa_nao_calculavel():
    dist = {0: 1.00}  # empate certo: 100% de devolucao
    assert decomposicao_ah(dist, "mandante", 0.0) is None
    av = _av_ah("mandante", 0.0, dist)
    assert odd_justa_avaliacao(av) is None
    u = avaliar_utilidade(av.prob, av=av)
    assert u.situacao == SIT_NAO_AVALIAVEL
    assert u.odd_minima is None and not u.utilizavel


# Linha DNB/AH SEM decomposicao na fonte: nunca aproxima por 1/prob
def test_ah_sem_decomposicao_nao_aproxima():
    av = SimpleNamespace(
        mercado="resultado",
        linha="AH mandante +0.25 (90 minutos)",
        prob=0.70,
        confianca=0.88,
        riscos=[],
        conf_componentes={"amostra_historica": 1.0},
        sustentacao={"lambda_por90": 2.6},
    )
    assert odd_justa_avaliacao(av) is None  # NAO CALCULAVEL: sem aproximacao
    u = avaliar_utilidade(av.prob, av=av)
    assert u.situacao == SIT_NAO_AVALIAVEL
    assert not u.utilizavel
    # nao calculavel nunca vira linha principal quando ha alternativa
    outra = _av_binario(0.75, "Under 4.5 gols (total do jogo)")
    sel = selecionar_linha_operacional([outra, av])
    assert sel.operacional is outra
    assert sel.util_operacional.odd_justa == 1.33


# ----------------------------------------------------------------------
# 15. Odd real ABAIXO da minima aceitavel => REPROVADA - SEM VALOR
# ----------------------------------------------------------------------
def test_15_odd_real_abaixo_da_minima_reprovada():
    av = _av_ah("mandante", 0.0, DIST_WIN)  # prob 0.72, justa 1.18
    assert odd_justa_avaliacao(av) == 1.18
    assert odd_minima_aceitavel(1.18) == 1.24  # 1.18 * 1.05 = 1.239
    u = avaliar_utilidade(av.prob, odd_real=1.20, av=av)
    assert u.situacao == SIT_REPROVADA_SEM_VALOR
    assert not u.utilizavel
    # odd efetiva acima do piso nao salva: o valor e insuficiente
    assert u.odd_efetiva == 1.20 >= ODD_MIN_OPERACIONAL
    # com odd real, edge e EV existem (e mostram o valor negativo)
    assert u.edge == round((1.20 - 1.18) / 1.18, 4)
    assert u.ev == round(0.72 * 0.20 - 0.13, 4)  # = 0.014


# ----------------------------------------------------------------------
# 16. Odd real EXATAMENTE na minima aceitavel => APROVAVEL
# ----------------------------------------------------------------------
def test_16_odd_real_igual_a_minima_aprovavel():
    av = _av_ah("mandante", 0.0, DIST_WIN)
    u = avaliar_utilidade(av.prob, odd_real=1.24, av=av)
    assert u.odd_minima == 1.24
    assert u.situacao == SIT_APROVAVEL
    assert u.utilizavel and u.marca is None


# ----------------------------------------------------------------------
# 17. Odd real ACIMA da minima aceitavel => APROVAVEL
# ----------------------------------------------------------------------
def test_17_odd_real_acima_da_minima_aprovavel():
    av = _av_ah("mandante", 0.0, DIST_WIN)
    u = avaliar_utilidade(av.prob, odd_real=1.50, av=av)
    assert u.situacao == SIT_APROVAVEL and u.utilizavel
    # edge = (real - justa)/justa; EV por unidade com a decomposicao
    assert u.edge == round((1.50 - 1.18) / 1.18, 4)
    assert u.ev == round(0.72 * 0.50 - 0.13, 4)  # = 0.23


# ----------------------------------------------------------------------
# 18. Sem odd real => OPORTUNIDADE ESTATISTICA - AGUARDANDO ODD
# ----------------------------------------------------------------------
def test_18_sem_odd_real_oportunidade_estatistica():
    extrema = _av_binario(0.96, "Over 0.5 gols (total do jogo)")
    dnb = _av_ah("mandante", 0.0, DIST_WIN)  # prob 0.72, justa 1.18
    sel = selecionar_linha_operacional([extrema, dnb])
    assert sel.operacional is dnb
    assert sel.util_operacional.odd_real is None
    assert sel.util_operacional.situacao == SIT_OPORTUNIDADE
    assert ODD_REAL_INDISPONIVEL in sel.motivo_operacional
    # a linha extrema (justa 1.04) continua marcada, nunca principal
    assert sel.util_maior_prob.utilizavel is False
    texto = "\n".join(format_bloco_operacional(sel))
    assert "OPORTUNIDADE ESTATISTICA - AGUARDANDO ODD" in texto
    assert "PROBABILIDADE DE DEVOLUCAO: 15.00%" in texto


# ----------------------------------------------------------------------
# 19. Odd justa abaixo de 1.15 => BAIXA UTILIDADE OPERACIONAL
# ----------------------------------------------------------------------
def test_19_odd_justa_abaixo_do_piso_baixa_utilidade():
    av = _av_binario(0.96, "Over 0.5 gols (total do jogo)")
    u = avaliar_utilidade(av.prob, av=av)
    assert u.odd_justa == 1.04 < ODD_MIN_OPERACIONAL
    assert u.situacao == SIT_BAIXA_UTILIDADE
    assert u.marca == MARCA_BAIXA_UTILIDADE
    # prob dentro da janela, mas sem utilidade: nunca principal
    sel = selecionar_linha_operacional([av])
    assert sel.operacional is None and sel.nenhuma_operacional


# ----------------------------------------------------------------------
# 20. Arredondamento PARA CIMA da odd minima aceitavel
# ----------------------------------------------------------------------
def test_20_odd_minima_arredonda_para_cima():
    # 1.30 * 1.05 = 1.365 => 1.37 (nunca 1.36)
    assert odd_minima_aceitavel(1.30) == 1.37
    # 1.31 * 1.05 = 1.3755 => 1.38
    assert odd_minima_aceitavel(1.31) == 1.38
    # 1.40 * 1.05 = 1.47 exato => 1.47 (sem subir um centavo de ruido)
    assert odd_minima_aceitavel(1.40) == 1.47
    # 2.00 * 1.05 = 2.10 exato => 2.10 (ruido de float nao sobe p/ 2.11)
    assert odd_minima_aceitavel(2.00) == 2.10
    # 1.25 * 1.05 = 1.3125 => 1.32
    assert odd_minima_aceitavel(1.25) == 1.32
    assert odd_minima_aceitavel(None) is None
    assert MARGEM_OPERACIONAL == 0.05
    # o piso de utilidade NUNCA e apresentado como odd minima
    assert ODD_MIN_OPERACIONAL == 1.15


# ----------------------------------------------------------------------
# Apresentacao obrigatoria (Bloco A): os 11 campos por linha
# ----------------------------------------------------------------------
def test_bloco_apresentacao_obrigatoria_com_odd_real():
    dnb = _av_ah("mandante", 0.0, DIST_WIN)
    sel = selecionar_linha_operacional(
        [dnb], odds_reais={"DNB mandante (empate anula)": 1.50}
    )
    assert sel.util_operacional.situacao == SIT_APROVAVEL
    texto = "\n".join(format_bloco_operacional(sel))
    for rotulo in (
        "MERCADO:",
        "PROBABILIDADE ESTIMADA:",
        "PROBABILIDADE DE DEVOLUCAO:",
        "ODD JUSTA:",
        f"MARGEM OPERACIONAL: {MARGEM_OPERACIONAL:.0%}",
        "ODD MINIMA ACEITAVEL:",
        f"PISO DE UTILIDADE: {ODD_MIN_OPERACIONAL:.2f}",
        "ODD REAL:",
        "EDGE:",
        "VALOR ESPERADO:",
        "SITUACAO:",
    ):
        assert rotulo in texto, f"campo ausente: {rotulo}"
    assert "ODD REAL: 1.50" in texto
    assert "SITUACAO: APROVAVEL" in texto


def test_bloco_apresentacao_sem_odd_real_nao_inventa():
    dnb = _av_ah("mandante", 0.0, DIST_WIN)
    sel = selecionar_linha_operacional([dnb])
    texto = "\n".join(format_bloco_operacional(sel))
    assert ODD_REAL_INDISPONIVEL in texto
    assert "nao calculado (sem odd real)" in texto
    assert f"SITUACAO: {SIT_OPORTUNIDADE}" in texto


# ----------------------------------------------------------------------
# Integracao: o motor real (avaliar_resultado_prejogo) ja entrega a
# decomposicao exata em TODAS as linhas DNB/AH
# ----------------------------------------------------------------------
def _hist_gols():
    def jogo(i, casa):
        return SimpleNamespace(
            date=f"2026-08-{i + 1:02d}",
            played_at_home=casa,
            corners_for=6, corners_against=4,
            goals_for=(2 if casa else 1),
            goals_against=(1 if casa else 2),
            yellow_for=2, red_for=0, yellow_against=2, red_against=0,
        )
    gh = [jogo(i, True) for i in range(20)]
    ga = [jogo(i, False) for i in range(20)]
    return {"games_home": gh, "games_away": ga,
            "n_home": 20, "n_away": 20}


_BENCH_GOLS = {"describe": {"media": 2.6}, "partidas_validas": 100,
               "liga_id": 71, "temporada": 2026}


def test_avaliacao_real_carrega_decomposicao_e_odd_de_equilibrio():
    aves = avaliar_resultado_prejogo(_hist_gols(), _BENCH_GOLS, 6)
    assert aves
    dnb = next(a for a in aves if a.linha.startswith("DNB mandante"))
    eq = dnb.sustentacao["equilibrio_liquidacao"]
    # prob == ganho da decomposicao (o calculo NAO mudou)
    assert dnb.prob == round(eq["ganho"], 4)
    oj = odd_justa_avaliacao(dnb)
    assert oj == round(1.0 + eq["perda"] / eq["ganho"], 2)
    # na odd justa o EV e' ZERO (ate o arredondamento em centavos)
    ev = eq["ganho"] * (oj - 1.0) - eq["perda"]
    assert abs(ev) <= 0.005 * eq["ganho"] + 1e-9
    # TODAS as linhas AH tem a decomposicao
    for a in aves:
        if a.linha.startswith(("DNB", "AH")):
            assert "equilibrio_liquidacao" in a.sustentacao
            assert odd_justa_avaliacao(a) is not None