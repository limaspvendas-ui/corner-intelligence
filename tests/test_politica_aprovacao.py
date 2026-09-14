"""Testes da POLITICA DE APROVACAO POR SEGURANCA (pre-jogo + live).

Garantem a SEPARACAO de decisao: (A) seguranca da aposta e (B)
qualidade financeira da odd - e que a camada financeira (EV/odd justa,
src/valor_aposta.py, intocado) e apenas INFORMATIVA.

Regras testadas:
  - EV negativo NAO veta selecao aprovada por seguranca;
  - EV positivo NAO aprova selecao reprovada por seguranca;
  - ausencia de odd NAO reprova a analise esportiva;
  - retorno minimo declarado pelo usuario e respeitado;
  - sem minimo declarado => RETORNO NAO VALIDADO;
  - matematica de pre-jogo e live permanece intacta (as funcoes
    validadas reproduzem os valores do caso controlado);
  - a camada de politica nao tem persistencia alguma;
  - vocabulario nunca afirma acerto absoluto.

Caso controlado (probabilidades-base JA calculadas pelo motor
validado, NAO recalculadas aqui): Cagliari Under 13.0 (85,45%,
confianca 0,80) e Getafe Under 12.0 (86,20%, confianca 0,96), regra
real: igualdade com a linha = derrota.
"""

import inspect

import pytest

import src.politica_aprovacao as pa
from src.live_opportunity import (
    CONF_MIN_TOP1,
    CONF_MIN_TOP2,
    PROB_MAX_APROVAR,
    PROB_MIN_APROVAR,
    poisson_ge,
    poisson_le,
)
from src.politica_aprovacao import (
    APROVADA_COM_RESSALVA,
    APROVADA_POR_SEGURANCA,
    RETORNO_ACEITAVEL,
    RETORNO_BAIXO,
    RETORNO_NAO_INFORMADO,
    RETORNO_NAO_VALIDADO,
    REPROVADA_POR_SEGURANCA,
    avaliar_multipla_seguranca,
    avaliar_selecao,
    classificar_retorno,
    classificar_seguranca,
)
from src.valor_aposta import (
    REGRA_IGUALDADE_PERDE,
    classificar as classificar_ev,
    odd_justa,
)

# ----- caso controlado (valores do motor validado) -----
_CAG = {"probabilidade": 0.8545, "confianca": 0.80,
        "riscos": [], "contracoes": [], "odd": 1.14,
        "desfechos": (0.8545, 0.0567, 0.0888),
        "regra_liquidacao": REGRA_IGUALDADE_PERDE}
_GET = {"probabilidade": 0.8620, "confianca": 0.96,
        "riscos": [], "contracoes": [], "odd": 1.14,
        "desfechos": (0.8620, 0.0564, 0.0816),
        "regra_liquidacao": REGRA_IGUALDADE_PERDE}


# ----------------------------------------------------------------------
# 1. EV negativo NAO veta selecao segura
# ----------------------------------------------------------------------
def test_ev_negativo_nao_veta_selecao_aprovada_por_seguranca():
    r = avaliar_selecao(**_CAG)
    fin = r["financeiro_informativo"]
    assert fin["ev"] == pytest.approx(-0.0259, abs=1e-3)  # EV negativo
    assert fin["odd_justa"] == pytest.approx(1.1703, abs=1e-3)
    # ...e a seguranca segue APROVADA: EV nao e veto
    assert r["seguranca"]["classificacao"] == APROVADA_POR_SEGURANCA
    assert r["seguranca"]["probabilidade_estimada"] == 0.8545
    assert r["seguranca"]["confianca"] == 0.80


# ----------------------------------------------------------------------
# 2. EV positivo NAO aprova selecao insegura
# ----------------------------------------------------------------------
def test_ev_positivo_nao_aprova_selecao_reprovada_por_seguranca():
    # probabilidade 60%: ABAIXO da janela existente (70-97%); odd 5.0
    # => EV fortemente positivo. Seguranca manda: REPROVADA.
    r = avaliar_selecao(
        probabilidade=0.60, confianca=0.90, odd=5.0,
        desfechos=(0.60, 0.0, 0.40),
        regra_liquidacao=REGRA_IGUALDADE_PERDE)
    assert r["financeiro_informativo"]["ev"] > 0  # EV positivo...
    assert r["seguranca"]["classificacao"] == REPROVADA_POR_SEGURANCA
    assert "abaixo da janela" in r["seguranca"]["motivos"][0]


# ----------------------------------------------------------------------
# 3. Ausencia de odd NAO reprova a analise esportiva
# ----------------------------------------------------------------------
def test_ausencia_de_odd_nao_reprova_a_analise_esportiva():
    r = avaliar_selecao(
        probabilidade=0.8545, confianca=0.80, odd=None,
        desfechos=(0.8545, 0.0567, 0.0888),
        regra_liquidacao=REGRA_IGUALDADE_PERDE)
    assert r["retorno"]["classificacao"] == RETORNO_NAO_INFORMADO
    assert r["seguranca"]["classificacao"] == APROVADA_POR_SEGURANCA
    assert r["financeiro_informativo"] is None  # sem odd: sem EV


# ----------------------------------------------------------------------
# 4. Retorno minimo declarado pelo usuario e respeitado
# ----------------------------------------------------------------------
def test_retorno_minimo_do_usuario_e_respeitado():
    # comparacao SOMENTE com o retorno solicitado pelo usuario
    assert classificar_retorno(1.29, 1.30)["classificacao"] == (
        RETORNO_BAIXO)
    assert classificar_retorno(1.30, 1.30)["classificacao"] == (
        RETORNO_ACEITAVEL)
    assert classificar_retorno(1.31, 1.30)["classificacao"] == (
        RETORNO_ACEITAVEL)
    # o veredito de seguranca NAO muda com o retorno (camadas uteis)
    for odd in (1.29, 1.30, 1.31):
        r = avaliar_selecao(**{**_CAG, "odd": odd},
                            retorno_minimo=1.30)
        assert r["seguranca"]["classificacao"] == APROVADA_POR_SEGURANCA


# ----------------------------------------------------------------------
# 5. Sem minimo declarado => RETORNO NAO VALIDADO (nunca reprova)
# ----------------------------------------------------------------------
def test_sem_minimo_declarado_retorno_nao_validado():
    r = avaliar_selecao(**_GET)  # odd 1.14, sem retorno_minimo
    ret = r["retorno"]
    assert ret["classificacao"] == RETORNO_NAO_VALIDADO
    assert "aprovado nem reprovado por odd" in ret["nota"]
    # a analise esportiva segue de pe, com EV informativo
    assert r["seguranca"]["classificacao"] == APROVADA_POR_SEGURANCA
    assert r["financeiro_informativo"]["ev"] == pytest.approx(
        -0.0173, abs=1e-3)


# ----------------------------------------------------------------------
# 6. Thresholds SOMENTE os ja existentes; fronteiras exatas
# ----------------------------------------------------------------------
def test_thresholds_sao_somente_os_existentes_do_motor():
    assert PROB_MIN_APROVAR == 0.70
    assert PROB_MAX_APROVAR == 0.97
    assert CONF_MIN_TOP1 == 0.60
    assert CONF_MIN_TOP2 == 0.68
    # fronteiras da janela existente (inclusive)
    seg = classificar_seguranca(0.70, 0.96)
    assert seg["classificacao"] == APROVADA_POR_SEGURANCA
    assert classificar_seguranca(0.97, 0.96)["classificacao"] == (
        APROVADA_POR_SEGURANCA)
    # fora da janela: reprovada
    assert classificar_seguranca(0.699, 0.96)["classificacao"] == (
        REPROVADA_POR_SEGURANCA)
    assert classificar_seguranca(0.971, 0.96)["classificacao"] == (
        REPROVADA_POR_SEGURANCA)
    # confianca: abaixo de TOP1 reprovada; TOP1..TOP2 ressalva;
    # acima de TOP2 aprovada
    assert classificar_seguranca(0.85, 0.599)["classificacao"] == (
        REPROVADA_POR_SEGURANCA)
    assert classificar_seguranca(0.85, 0.60)["classificacao"] == (
        APROVADA_COM_RESSALVA)
    assert classificar_seguranca(0.85, 0.68)["classificacao"] == (
        APROVADA_POR_SEGURANCA)


def test_riscos_e_contradicoes_do_motor_garantem_ressalva():
    r = avaliar_selecao(probabilidade=0.85, confianca=0.90,
                        riscos=["amostra historica pequena: 6 jogos"])
    assert r["seguranca"]["classificacao"] == APROVADA_COM_RESSALVA
    assert "risco(s)" in r["seguranca"]["motivos"][0]
    r2 = avaliar_selecao(probabilidade=0.85, confianca=0.90,
                         contracoes=["contradicao do h2h"])
    assert r2["seguranca"]["classificacao"] == APROVADA_COM_RESSALVA
    # sem sustentacao estatistica => reprovada
    r3 = avaliar_selecao(probabilidade=0.85, confianca=0.90,
                         tem_sustentacao=False)
    assert r3["seguranca"]["classificacao"] == REPROVADA_POR_SEGURANCA


# ----------------------------------------------------------------------
# 7. Matematica de pre-jogo e live permanece INTACTA
# ----------------------------------------------------------------------
def test_matematica_de_pregogo_e_live_intacta():
    # as funcoes VALIDADAS reproduzem exatamente os valores-base do
    # caso controlado (lambda Cagliari 9,28 / Getafe 8,34):
    # P(X<=12 | 9.28) = 85,45% e P(X>=13 | 8.34) = 8,16%
    assert poisson_le(12, 9.28) == pytest.approx(0.8545, abs=1e-3)
    assert poisson_ge(13, 8.34) == pytest.approx(0.0816, abs=1e-3)
    # a camada financeira antiga (EV puro) segue COMO ESTA:
    assert odd_justa(0.8545, 0.0567, 0.0888) == pytest.approx(
        1.1039, abs=1e-3)
    assert classificar_ev(-0.01) == "REPROVADA"  # rotulo proprio dela
    # a politica NOVA nao redefine nenhum threshold: importa os do
    # motor validado
    assert pa.PROB_MIN_APROVAR is PROB_MIN_APROVAR
    assert pa.CONF_MIN_TOP2 is CONF_MIN_TOP2


# ----------------------------------------------------------------------
# 8. MULTIPLAS: pernas individuais + conjunta + vereditos separados
# ----------------------------------------------------------------------
def test_multipla_controlada_vereditos_separados():
    r = avaliar_multipla_seguranca(
        [_CAG, _GET], retorno_minimo=1.30, odd_informada=1.30)
    # premissa de independencia SEMPRE declarada
    assert r["premissa"] == (
        "ASSUMINDO INDEPENDENCIA ENTRE OS EVENTOS")
    # pernas analisadas individualmente
    assert [p["seguranca"]["classificacao"] for p in r["pernas"]] == (
        [APROVADA_POR_SEGURANCA, APROVADA_POR_SEGURANCA])
    assert r["pernas"][0]["seguranca"]["probabilidade_estimada"] == (
        0.8545)
    assert r["pernas"][1]["seguranca"]["confianca"] == 0.96
    # pernas sem minimo individual declarado => retorno nao validado
    assert r["pernas"][0]["retorno"]["classificacao"] == (
        RETORNO_NAO_VALIDADO)
    # conjunta: produto; confianca = MENOR individual (conservadora)
    assert r["probabilidade_conjunta"] == pytest.approx(
        0.8545 * 0.8620, abs=1e-6)  # 73,66%
    assert r["confianca_conjunta"] == 0.80
    # seguranca da multipla: conjunta na janela => APROVADA
    assert r["seguranca_multipla"]["classificacao"] == (
        APROVADA_POR_SEGURANCA)
    # retorno da multiula: odd informada 1.30 >= minimo 1.30
    assert r["retorno_multipla"]["classificacao"] == RETORNO_ACEITAVEL
    # EV informativo: negativo (-4,27%) e NAO vetou a multipla
    fin = r["financeiro_informativo"]
    assert fin["ev"] == pytest.approx(-0.0427, abs=1e-3)
    assert fin["odd_combinada_calculada"] == pytest.approx(1.2996)
    assert fin["odd_justa_vitoria_plena"] == pytest.approx(
        1.3576, abs=1e-3)
    assert r["seguranca_multipla"]["classificacao"] == (
        APROVADA_POR_SEGURANCA)  # EV negativo nao e veto


def test_multipla_com_perna_reprovada_e_reprovada():
    insegura = {**_CAG, "probabilidade": 0.55,
                "desfechos": (0.55, 0.0, 0.45)}
    r = avaliar_multipla_seguranca([_CAG, insegura])
    assert r["pernas"][1]["seguranca"]["classificacao"] == (
        REPROVADA_POR_SEGURANCA)
    # a perna reprovada reprova a multipla INTEIRA
    assert r["seguranca_multipla"]["classificacao"] == (
        REPROVADA_POR_SEGURANCA)
    assert "perna(s) [1]" in r["seguranca_multipla"]["motivos"][0]


def test_multipla_confianca_conservadora_e_riscos_unidos():
    a = {**_CAG, "riscos": ["amostra pequena"]}
    b = {**_GET, "confianca": 0.65}
    r = avaliar_multipla_seguranca([a, b])
    assert r["confianca_conjunta"] == 0.65  # MENOR individual
    assert r["seguranca_multipla"]["riscos"] == ["amostra pequena"]
    assert r["seguranca_multipla"]["classificacao"] == (
        APROVADA_COM_RESSALVA)  # riscos unidos garantem ressalva


# ----------------------------------------------------------------------
# 9. Sem persistencia: a politica nao toca banco/registro nenhum
# ----------------------------------------------------------------------
def test_camada_de_politica_nao_tem_persistencia():
    src_text = inspect.getsource(pa)
    assert "sqlite" not in src_text
    assert "src.registry" not in src_text
    assert "RegistroRecomendacoes" not in src_text
    # nenhum membro do modulo vem do modulo de registro
    assert not any(
        getattr(getattr(pa, nome, None), "__module__", "") == (
            "src.registry")
        for nome in dir(pa)
    )


# ----------------------------------------------------------------------
# 10. Vocabulario: nunca afirma acerto absoluto
# ----------------------------------------------------------------------
def test_vocabulario_nunca_afirma_acerto_absoluto():
    src_text = inspect.getsource(pa).lower()
    for proibida in ("certeza", "garantid", "sem risco"):
        assert proibida not in src_text
    # saidas sempre carregam probabilidade estimada + confianca +
    # riscos + contradicoes, nunca rotulo absoluto
    r = avaliar_selecao(**_CAG)
    seg = r["seguranca"]
    for chave in ("probabilidade_estimada", "confianca", "riscos",
                  "contradicoes"):
        assert chave in seg