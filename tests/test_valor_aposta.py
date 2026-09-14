"""Testes da avaliacao de VALOR / ODD JUSTA com IDENTIFICACAO da
REGRA DE LIQUIDACAO (correcao critica 07/09/2026).

Linha inteira NAO significa push automaticamente: a regra de
liquidacao do mercado e que decide (PUSH devolve; IGUALDADE_PERDE
soma a igualdade na derrota; sem regra => REGRA DE LIQUIDACAO NAO
CONFIRMADA, nada calculado, nada inventado).

Multiplas: enumeracao EXATA de todos os desfechos com a REGRA de
cada perna (perna push vira odd 1,00 e a multipla continua; perna
com igualdade-que-perde derruba a multipla), premissa de
INDEPENDENCIA sempre declarada.

Nenhuma logica validada (projecao, probabilidade, confianca,
selecao, registro, liquidacao, familias) e alterada ou testada aqui
- este arquivo testa SOMENTE src/valor_aposta.py.
"""

import pytest

from src.valor_aposta import (
    APROVADA,
    APROVADA_COM_RESSALVA,
    PREMISSA_INDEPENDENCIA,
    REGRA_IGUALDADE_PERDE,
    REGRA_NAO_CONFIRMADA,
    REGRA_PUSH,
    REPROVADA,
    STATUS_NAO_CONFIRMADA,
    STATUS_OK,
    avaliar_multipla,
    avaliar_simples,
    classificar,
    enumerar_desfechos,
    ev_simples,
    odd_justa,
)

# Casos reais validados (probabilidades do motor validado):
# desfechos BRUTOS ao redor da linha inteira: abaixo / igual / acima
_CAGLIARI = (0.8545, 0.0567, 0.0888, 1.14)  # Under 13.0 @ 1.14
_GETAFE = (0.8620, 0.0564, 0.0816, 1.14)  # Under 12.0 @ 1.14


# ----------------------------------------------------------------------
# A) Mercado SEM push: odd justa classica = 1/W
# ----------------------------------------------------------------------
def test_a_mercado_sem_push_odd_justa_inversa():
    w, p, l = 0.80, 0.0, 0.20
    assert odd_justa(w, p, l) == pytest.approx(1.25)
    # EV zero na odd justa; positivo acima dela; negativo abaixo
    assert ev_simples(w, p, l, 1.25) == pytest.approx(0.0, abs=1e-12)
    assert ev_simples(w, p, l, 1.30) == pytest.approx(0.04)
    assert ev_simples(w, p, l, 1.20) == pytest.approx(-0.04)


# ----------------------------------------------------------------------
# B) Linha INTEIRA COM PUSH confirmado (Cagliari Under 13.0 @ 1.14)
# ----------------------------------------------------------------------
def test_b_linha_inteira_com_push_odd_justa_1104_e_ev_31():
    w, p, l = 0.8545, 0.0567, 0.0888
    assert w + p + l == pytest.approx(1.0)
    # odd justa ~ 1.104 (NAO 1/W = 1.170)
    oj = odd_justa(w, p, l)
    assert oj == pytest.approx(1.104, abs=0.0005)
    assert oj == pytest.approx(1.0 + l / w)
    # EV em 1.14 ~ +3.1% por unidade
    assert ev_simples(w, p, l, 1.14) == pytest.approx(0.031, abs=0.0005)
    res = avaliar_simples(w, p, l, 1.14, REGRA_PUSH)
    assert res["status"] == STATUS_OK
    assert res["odd_justa"] == pytest.approx(1.1039, abs=0.001)
    assert res["ev"] == pytest.approx(0.0308, abs=0.001)
    assert res["push"] == pytest.approx(0.0567)  # push PRESERVADO
    assert res["ev"] > 0


# ----------------------------------------------------------------------
# C) Linha INTEIRA COM PUSH confirmado (Getafe Under 12.0 @ 1.14)
# ----------------------------------------------------------------------
def test_c_linha_inteira_com_push_odd_justa_1095_e_ev_39():
    w, p, l = 0.8620, 0.0564, 0.0816
    assert odd_justa(w, p, l) == pytest.approx(1.095, abs=0.0005)
    assert ev_simples(w, p, l, 1.14) == pytest.approx(0.039, abs=0.0005)
    assert ev_simples(w, p, l, 1.14) > 0


# ----------------------------------------------------------------------
# F) Linha INTEIRA SEM PUSH (igualdade = DERROTA) - caso REAL Bet365
# ----------------------------------------------------------------------
def test_f_linha_inteira_sem_push_igualdade_perde_cagliari():
    # Cagliari Under 13.0: 13 escanteios = PERDE (regra real)
    res = avaliar_simples(*_CAGLIARI, REGRA_IGUALDADE_PERDE)
    assert res["status"] == STATUS_OK
    assert res["regra_liquidacao"] == REGRA_IGUALDADE_PERDE
    # NUNCA inferir push por linha inteira: empate foi para a derrota
    assert res["push"] == 0.0
    assert res["win"] == pytest.approx(0.8545)
    assert res["lose"] == pytest.approx(0.0567 + 0.0888)  # 0.1455
    # odd justa classica: 1/W ~ 1.170 (NAO 1.104 do push)
    assert res["odd_justa"] == pytest.approx(1.170, abs=0.0005)
    # EV em 1.14 ~ -2.59% => REPROVADA
    assert res["ev"] == pytest.approx(-0.0259, abs=0.0005)
    assert res["classificacao"] == REPROVADA


def test_f_linha_inteira_sem_push_igualdade_perde_getafe():
    # Getafe Under 12.0: 12 escanteios = PERDE (regra real)
    res = avaliar_simples(*_GETAFE, REGRA_IGUALDADE_PERDE)
    assert res["push"] == 0.0
    assert res["lose"] == pytest.approx(0.0564 + 0.0816)  # 0.1380
    assert res["odd_justa"] == pytest.approx(1.160, abs=0.0005)
    assert res["ev"] == pytest.approx(-0.0173, abs=0.0005)  # ~ -1.73%
    assert res["classificacao"] == REPROVADA


def test_f_mesmos_brutos_regra_diferente_resultado_diferente():
    # IDENTIFICACAO da regra muda o veredito: nunca e detalhe
    com_push = avaliar_simples(*_CAGLIARI, REGRA_PUSH)
    sem_push = avaliar_simples(*_CAGLIARI, REGRA_IGUALDADE_PERDE)
    assert com_push["ev"] > 0 > sem_push["ev"]
    assert com_push["classificacao"] != REPROVADA
    assert sem_push["classificacao"] == REPROVADA


# ----------------------------------------------------------------------
# G) Linha FRACIONADA: empate impossivel, regra nao altera nada
# ----------------------------------------------------------------------
def test_g_linha_fracionada_empate_zero_regra_indiferente():
    # Under 11.5 (Cagliari): abaixo 0.7753 / igual 0 / acima 0.2247
    fracionada = (0.7753, 0.0, 0.2247, 1.29)
    por_push = avaliar_simples(*fracionada, REGRA_PUSH)
    por_perde = avaliar_simples(*fracionada, REGRA_IGUALDADE_PERDE)
    # linha fracionada: a regra e inutil - mesmos numeros em tudo
    for campo in ("win", "push", "lose", "odd_justa", "ev",
                  "classificacao"):
        assert por_push[campo] == por_perde[campo]
    assert por_push["push"] == 0.0
    assert por_push["odd_justa"] == pytest.approx(1.0 / 0.7753,
                                                   abs=0.001)
    # odd justa ~ 1.290: em 1.29 o EV e ~ zero
    assert abs(por_push["ev"]) < 0.001


# ----------------------------------------------------------------------
# H) Ausencia de regra de liquidacao: NADA e calculado/inventado
# ----------------------------------------------------------------------
def test_h_sem_regra_de_liquidacao_nada_e_calculado():
    for regra in (None, REGRA_NAO_CONFIRMADA):
        res = avaliar_simples(*_CAGLIARI, regra)
        assert res["status"] == STATUS_NAO_CONFIRMADA
        # sem EV, sem odd justa, sem classificacao - nada inventado
        assert res["ev"] is None
        assert res["odd_justa"] is None
        assert res["classificacao"] is None
        assert res["push"] is None  # push NUNCA e inferido


def test_h_multipla_com_perna_nao_confirmada_fica_nao_confirmada():
    pernas = [
        (*_CAGLIARI, REGRA_PUSH),
        (*_GETAFE, None),  # perna sem regra confirmada
    ]
    res = avaliar_multipla(pernas)
    assert res["status"] == STATUS_NAO_CONFIRMADA
    assert res["pernas_nao_confirmadas"] == [1]
    assert res["ev"] is None
    assert res["desfechos"] is None
    assert res["classificacao"] is None


def test_h_regra_desconhecida_e_rejeitada_com_erro():
    with pytest.raises(ValueError):
        avaliar_simples(*_CAGLIARI, "DEVOLVE_SIM")
    with pytest.raises(ValueError):
        avaliar_multipla([(*_CAGLIARI, "TALVEZ_DEVOLVA")])
    with pytest.raises(ValueError):
        enumerar_desfechos([(*_CAGLIARI, None)])


# ----------------------------------------------------------------------
# D) EV positivo NAO e reprovado pela antiga comparacao W < 1/odd
#    (vale SOMENTE para mercados com push REALMENTE confirmado)
# ----------------------------------------------------------------------
def test_d_ev_positivo_nao_e_reprovado_por_w_vs_inverso_da_odd():
    w, p, l = 0.8545, 0.0567, 0.0888
    odd = 1.14
    # a comparacao ANTIGA (errada com push) reprovaria: W < 1/odd
    assert w < 1.0 / odd  # 0.8545 < 0.8772
    res = avaliar_simples(w, p, l, odd, REGRA_PUSH)
    assert res["ev"] > 0
    assert res["classificacao"] in (APROVADA, APROVADA_COM_RESSALVA)
    assert res["classificacao"] != REPROVADA
    # e sem valor de fato quando a odd fica ABAIXO da justa corrigida:
    res_ruim = avaliar_simples(w, p, l, 1.09, REGRA_PUSH)  # < 1.104
    assert res_ruim["ev"] < 0
    assert res_ruim["classificacao"] == REPROVADA


# ----------------------------------------------------------------------
# E) MULTIPLAS: enumeracao exata de TODOS os desfechos (com push)
# ----------------------------------------------------------------------
def test_e_multipla_vitoria_vitoria():
    pernas = [(0.80, 0.0, 0.20, 1.25, REGRA_PUSH),
              (0.50, 0.0, 0.50, 2.0, REGRA_PUSH)]
    res = avaliar_multipla(pernas)
    odd_comb = 1.25 * 2.0  # 2.5
    assert res["odd_combinada"] == pytest.approx(2.5)
    # hand-check: win = 0.4 paga 2.5; qualquer perna perde (0.6) -1
    assert res["ev"] == pytest.approx(0.4 * (odd_comb - 1) - 0.6,
                                      abs=1e-3)
    assert res["premissa"] == PREMISSA_INDEPENDENCIA


def test_e_multipla_vitoria_mais_push_paga_somente_a_vencedora():
    # perna B sempre empata E DEVOLVE: multipla vira a perna A isolada
    pernas = [(0.90, 0.0, 0.10, 1.5, REGRA_PUSH),
              (0.0, 1.0, 0.0, 3.0, REGRA_PUSH)]
    res = avaliar_multipla(pernas)
    # A vence (0.9) e paga a SUA odd 1.5; A perde (0.1) => -1
    assert res["ev"] == pytest.approx(0.9 * 0.5 - 0.10, abs=1e-6)
    assert res["ev"] == pytest.approx(ev_simples(0.90, 0.0, 0.10, 1.5))


def test_e_multipla_push_mais_vitoria_e_simetrica():
    pernas = [(0.0, 1.0, 0.0, 1.5, REGRA_PUSH),
              (0.90, 0.0, 0.10, 1.5, REGRA_PUSH)]
    res = avaliar_multipla(pernas)
    assert res["ev"] == pytest.approx(0.9 * 0.5 - 0.10, abs=1e-6)


def test_e_multipla_push_push_devolve_a_stake():
    pernas = [(0.0, 1.0, 0.0, 1.14, REGRA_PUSH),
              (0.0, 1.0, 0.0, 1.14, REGRA_PUSH)]
    res = avaliar_multipla(pernas)
    assert res["ev"] == pytest.approx(0.0)
    unico = [d for d in res["desfechos"] if d["probabilidade"] > 0]
    assert len(unico) == 1
    assert unico[0]["pushes"] == 2
    assert unico[0]["payout"] == pytest.approx(1.0)  # stake devolvida


def test_e_multipla_qualquer_derrota_perde_tudo():
    # A: win .5 / empate .2 / acima .3 (push); B: sempre empata
    pernas = [(0.50, 0.20, 0.30, 2.0, REGRA_PUSH),
              (0.0, 1.0, 0.0, 3.0, REGRA_PUSH)]
    res = avaliar_multipla(pernas)
    assert res["ev"] == pytest.approx(0.5 * 1.0 + 0.2 * 0.0 - 0.3,
                                      abs=1e-6)
    # derrota em qualquer posicao: B sempre empata, A acima => -1
    pernas2 = [(0.0, 1.0, 0.0, 2.0, REGRA_PUSH),
               (0.50, 0.20, 0.30, 3.0, REGRA_PUSH)]
    res2 = avaliar_multipla(pernas2)
    assert res2["ev"] == pytest.approx(0.5 * 2.0 - 0.3, abs=1e-6)


def test_e_multipla_real_dupla_cagliari_getafe_com_push():
    # DUPLA com push CONFIRMADO nas duas pernas: enumeracao exata dos
    # 9 desfechos; a perna que empurra vira odd 1,00
    res = avaliar_multipla([(*_CAGLIARI, REGRA_PUSH),
                            (*_GETAFE, REGRA_PUSH)])
    assert res["status"] == STATUS_OK
    assert res["odd_combinada"] == pytest.approx(1.2996)
    w_full = 0.8545 * 0.8620
    p_aw = 0.0567 * 0.8620
    p_wb = 0.8545 * 0.0564
    p_pp = 0.0567 * 0.0564
    l_tot = 1.0 - (w_full + p_aw + p_wb + p_pp)
    ev_manual = (w_full * 0.2996 + p_aw * 0.14 + p_wb * 0.14
                 + p_pp * 0.0 - l_tot)
    assert res["ev"] == pytest.approx(ev_manual, abs=1e-3)
    assert res["ev"] == pytest.approx(0.0712, abs=0.0005)  # ~ +7.1%
    assert len(res["desfechos"]) == 9  # 3^2 desfechos enumerados
    assert res["premissa"] == (
        "ASSUMINDO INDEPENDENCIA ENTRE OS EVENTOS"
    )


# ----------------------------------------------------------------------
# J) DUPLA REAL SEM PUSH (regra real da aposta: igualdade PERDE)
# ----------------------------------------------------------------------
def test_j_dupla_real_sem_push_reprovada():
    # Cagliari Under 13.0 (13 = perde) + Getafe Under 12.0 (12 = perde)
    res = avaliar_multipla([(*_CAGLIARI, REGRA_IGUALDADE_PERDE),
                            (*_GETAFE, REGRA_IGUALDADE_PERDE)])
    assert res["status"] == STATUS_OK
    w_full = 0.8545 * 0.8620  # 0.736579
    # sem push: vitoria plena ou derrota (1 - w_full)
    assert res["odd_combinada"] == pytest.approx(1.2996)
    assert res["odd_justa_vitoria_plena"] == pytest.approx(
        1.0 / w_full, abs=0.0005)  # ~ 1.358
    ev_manual = w_full * 0.2996 - (1.0 - w_full)
    assert res["ev"] == pytest.approx(ev_manual, abs=1e-3)
    assert res["ev"] == pytest.approx(-0.0427, abs=0.0005)  # ~ -4.3%
    assert res["classificacao"] == REPROVADA
    # nenhuma perna com push sobrevivente
    vivos = [d for d in res["desfechos"]
             if not d["perdeu"] and d["pushes"] > 0]
    assert vivos == []


def test_j_multipla_mista_uma_perna_push_outra_igualdade_perde():
    # A devolve no empate; B perde no empate: regras por perna
    pernas = [(*_CAGLIARI, REGRA_PUSH), (*_GETAFE, REGRA_IGUALDADE_PERDE)]
    res = avaliar_multipla(pernas)
    assert res["status"] == STATUS_OK
    # hand-check dos desfechos:
    #   A win & B win:            .8545*.8620 -> paga 1.2996
    #   A empata & B win:         .0567*.8620 -> paga 1.14
    #   A win & B empate-perde:   .8545*.1380 -> -1
    #   A empata & B perde:       .0567*.1380 -> -1
    #   A perde (qualquer B):     .0888       -> -1
    ev_manual = (0.8545 * 0.8620 * 0.2996
                 + 0.0567 * 0.8620 * 0.14
                 - (0.8545 * 0.1380 + 0.0567 * 0.1380 + 0.0888))
    assert res["ev"] == pytest.approx(ev_manual, abs=1e-3)


# ----------------------------------------------------------------------
# Integridade das entradas: nunca aceita desfechos incompletos
# ----------------------------------------------------------------------
def test_desfechos_que_nao_somam_1_sao_rejeitados():
    with pytest.raises(ValueError):
        odd_justa(0.80, 0.10, 0.20)  # soma 1.10
    with pytest.raises(ValueError):
        ev_simples(0.50, 0.10, 0.10, 1.5)  # soma 0.70
    with pytest.raises(ValueError):
        avaliar_simples(0.8545, 0.0667, 0.0888, 1.14, REGRA_PUSH)
    with pytest.raises(ValueError):
        avaliar_multipla([(0.8545, 0.0667, 0.0888, 1.14, REGRA_PUSH)])
    with pytest.raises(ValueError):
        avaliar_simples(-0.1, 0.2, 0.9, 1.14, REGRA_PUSH)
    with pytest.raises(ValueError):
        avaliar_simples(0.0, 0.5, 0.5, 1.14, REGRA_PUSH)  # W = 0
    with pytest.raises(ValueError):
        avaliar_simples(0.9, 0.05, 0.05, 1.0, REGRA_PUSH)  # odd <= 1


def test_classificacao_pelos_tres_categorias():
    # HEURISTICA v1 (rotulada): >= +5% APROVADA; 0 < EV < 5% RESSALVA;
    # EV <= 0 REPROVADA
    assert classificar(0.07) == APROVADA
    assert classificar(0.05) == APROVADA
    assert classificar(0.049) == APROVADA_COM_RESSALVA
    assert classificar(0.001) == APROVADA_COM_RESSALVA
    assert classificar(0.0) == REPROVADA
    assert classificar(-0.01) == REPROVADA


def test_odd_justa_sem_push_e_caso_particular_do_push():
    # quando P = 0, a formula geral (1-P)/W decae para 1/W exatamente
    assert odd_justa(0.80, 0.0, 0.20) == pytest.approx(
        odd_justa(0.80, 0.0, None)
    )
    assert odd_justa(0.80, 0.0, 0.20) == pytest.approx(1.0 / 0.80)