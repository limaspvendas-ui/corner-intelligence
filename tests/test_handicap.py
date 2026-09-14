"""Testes da CONVENCAO DE HANDICAP ASIATICO (validada em auditoria).

A convencao (feed em perspectiva do MANDANTE; prefixo = lado apostado)
foi validada no jogo real Botafogo x Palmeiras (fixture 1492360):
feed "Asian Handicap | Away -1.5" @1.10 => Palmeiras AH +1.5.

Aqui validamos: conversao de linha, liquidacao integral/meia/devolucao,
linhas de quarto, probabilidade implicita e overround do par.
"""

import pytest

from src.handicap import (
    LOSE_FULL,
    PUSH,
    WIN_FULL,
    WIN_HALF,
    implied_prob,
    is_quarter,
    overround_ok,
    real_line,
    settle,
    settle_component,
    split_quarter,
)


# ----------------------------------------------------------------------
# Conversao de linha (convencao validada)
# ----------------------------------------------------------------------
def test_real_line_home_inalterada():
    """Lado Home: linha real = valor do feed, sem conversao."""
    assert real_line("Home", -1.5) == -1.5
    assert real_line("Home", 0.25) == 0.25
    assert real_line("home", -1.0) == -1.0


def test_real_line_away_inverte_o_sinal():
    """CASO REAL DA AUDITORIA: feed 'Away -1.5' @1.10 => Palmeiras +1.5."""
    assert real_line("Away", -1.5) == 1.5
    assert real_line("away", -0.75) == 0.75
    assert real_line("Away", 0.25) == -0.25


def test_real_line_lado_invalido_levanta_erro():
    """Nunca interpretar handicap sem validar lado + linha."""
    with pytest.raises(ValueError):
        real_line("Empate", -1.5)
    with pytest.raises(ValueError):
        real_line("", 0.0)


# ----------------------------------------------------------------------
# Linhas de quarto
# ----------------------------------------------------------------------
def test_is_quarter():
    assert is_quarter(0.25)
    assert is_quarter(-0.75)
    assert not is_quarter(0.5)
    assert not is_quarter(-1.5)
    assert not is_quarter(1.0)
    assert not is_quarter(0.0)


def test_split_quarter():
    assert split_quarter(-0.75) == (-1.0, -0.5)
    assert split_quarter(0.25) == (0.0, 0.5)


# ----------------------------------------------------------------------
# Liquidacao
# ----------------------------------------------------------------------
def test_settle_vitoria_integral():
    result = settle(-1.0, 2)
    assert result.rotulo == WIN_FULL and result.net == 1.0


def test_settle_devolucao():
    result = settle(-1.0, 1)
    assert result.rotulo == PUSH and result.net == 0.0


def test_settle_derrota():
    result = settle(-1.0, 0)
    assert result.rotulo == LOSE_FULL and result.net == -1.0


def test_settle_component_basico():
    assert settle_component(0.0, 1) == (WIN_FULL, 1.0)
    assert settle_component(0.0, 0) == (PUSH, 0.0)
    # -1 + 0.5 = -0.5 < 0 => derrota integral
    assert settle_component(+0.5, -1) == (LOSE_FULL, -1.0)
    assert settle_component(+1.0, 0) == (WIN_FULL, 1.0)


def test_settle_linha_de_quarto_meia_vitoria():
    """AH -0.75 com vitoria por 1: metade empurta (push), metade vence."""
    result = settle(-0.75, 1)
    # divisao em -1.0 (push) e -0.5 (vitoria) => meia vitoria
    assert result.rotulo == WIN_HALF
    assert result.net == 0.5
    assert "-1.0" in result.detalhe and "-0.5" in result.detalhe


def test_settle_linha_de_quarto_meia_derrota():
    """AH +0.25 com empate: metade devolve, metade perde => meia derrota."""
    result = settle(0.25, 0)
    # divisao em 0.0 (push) e +0.5 (vitoria) => meia vitoria
    assert result.rotulo == WIN_HALF and result.net == 0.5
    result2 = settle(-0.25, 0)
    # divisao em -0.5 (derrota) e 0.0 (push) => meia derrota
    assert result2.rotulo == "meia derrota" and result2.net == -0.5


# ----------------------------------------------------------------------
# Probabilidade implicita e overround
# ----------------------------------------------------------------------
def test_implied_prob():
    assert implied_prob(2.0) == pytest.approx(0.5)
    assert implied_prob(1.10) == pytest.approx(0.909, abs=1e-3)
    with pytest.raises(ValueError):
        implied_prob(0)
    with pytest.raises(ValueError):
        implied_prob(-2)


def test_overround_ok():
    # pares reais da auditoria: ~104-107% de implicito somado
    assert overround_ok(2.05, 1.80)  # 48.8% + 55.6% = 104.4%
    assert overround_ok(1.95, 1.95)
    # pares impossiveis sob a convencao: soma muito acima ou abaixo
    assert not overround_ok(1.10, 1.10)  # 181.8%: leitura literal impossivel
    assert not overround_ok(8.0, 8.0)    # 25%: par incompleto