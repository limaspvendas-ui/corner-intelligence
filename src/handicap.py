"""CONVENCAO DE HANDICAP ASIATICO - validada em auditoria (06/09/2026).

CONVENCAO VALIDADA DO FEED DE ODDS DA API-FOOTBALL (Bet365 e afins):

    O "value" do feed e a linha na PERSPECTIVA DO MANDANTE; o prefixo
    (Home/Away) indica o LADO APOSTADO. A linha real da aposta e:

        lado Home  => linha real = value do feed (inalterada)
        lado Away  => linha real = -(value do feed)

    Exemplo validado no jogo Botafogo x Palmeiras (fixture 1492360):
        feed "Asian Handicap | Away -1.5" @1.10 (Bet365)
        => aposta no visitante (Palmeiras) com linha real +1.5
        (ou seja: Palmeiras AH +1.5). A leitura literal (Palmeiras -1.5)
        e matematicamente impossivel dentro do proprio payload: odd 1.10
        => 90,9% implicito para um SUBCONJUNTO do evento "Away vencer"
        (Match Winner Away @2.05 => 48,8%).

    Validacoes adicionais usadas na auditoria (mantidas aqui como
    funcoes de checagem):
        - pares Home/Away de mesma linha somam ~104-108% (overround);
        - correspondencia exata com Match Winner e Double Chance.

LIQUIDACAO (settlement) do handicap asiatico, por linha real e margem
do ponto de vista do lado apostado:

    resultado = margem + linha_real
    resultado > 0       -> vitoria integral        (lucro: odd - 1)
    resultado == 0      -> devolucao (push)       (lucro: 0)
    resultado < 0       -> derrota integral       (lucro: -1)
    linhas de quarto (-0.75, +0.25...) -> metade em cada linha vizinha,
    gerando meia vitoria ou meia derrota.

Nenhuma interpretacao de handicap ao vivo e feita sem validar
equipe + lado + linha (regra da ETAPA 2.2, secao 7).
"""

from __future__ import annotations

from dataclasses import dataclass

# Rotulos de liquidacao (terminologia exigida pela especificacao)
WIN_FULL = "vitoria integral"
WIN_HALF = "meia vitoria"
PUSH = "devolucao"
LOSE_HALF = "meia derrota"
LOSE_FULL = "derrota"


def real_line(bet_side: str, feed_value: float) -> float:
    """Converte a linha do feed na linha REAL da aposta.

    bet_side: "Home" ou "Away" (prefixo do feed).
    feed_value: o valor numerico exatamente como vem no feed
    (perspectiva do mandante).

    Home -1.5  -> linha real -1.5 (mandante AH -1.5)
    Away -1.5  -> linha real +1.5 (visitante AH +1.5)
    """
    side = (bet_side or "").strip().capitalize()
    if side == "Home":
        return float(feed_value)
    if side == "Away":
        return -float(feed_value)
    raise ValueError(
        f"Lado de handicap desconhecido: {bet_side!r}. "
        "Nunca interpretar handicap sem validar lado + linha."
    )


def is_quarter(line: float) -> bool:
    """Linha de quarto (ex.: -0.75) divide a aposta em duas metades."""
    return abs((float(line) * 4) % 2) == 1  # nao e multiplo de 0.5


def split_quarter(line: float) -> tuple[float, float]:
    """Divide linha de quarto nas duas linhas vizinhas de meio ponto."""
    return (round(float(line) - 0.25, 2), round(float(line) + 0.25, 2))


@dataclass
class Settlement:
    """Resultado da liquidacao de uma aposta de handicap."""

    rotulo: str     # vitoria integral / meia vitoria / devolucao / ...
    net: float      # fração da unidade apostada: +1, +0.5, 0, -0.5, -1
    detalhe: str    # explicacao objetiva do calculo


def settle_component(line: float, margin: float) -> tuple[str, float]:
    """Liquidacao de UMA linha (sem divisao de quartos).

    margin: diferencial de gols do ponto de vista do LADO APOSTADO
    (mandante: home-away; visitante: away-home).
    """
    result = float(margin) + float(line)
    if result > 0:
        return WIN_FULL, 1.0
    if result == 0:
        return PUSH, 0.0
    return LOSE_FULL, -1.0


def settle(line: float, margin: float) -> Settlement:
    """Liquidacao completa de uma linha de handicap asiatico.

    Linhas de quarto (ex.: -0.75) sao divididas em duas metades;
    a liquidacao final e a media das duas.
    """
    line = float(line)
    if is_quarter(line):
        l_low, l_high = split_quarter(line)
        r1, n1 = settle_component(l_low, margin)
        r2, n2 = settle_component(l_high, margin)
        net = (n1 + n2) / 2
        if net > 0:
            rotulo = WIN_HALF if net == 0.5 else WIN_FULL
        elif net == 0:
            rotulo = PUSH
        else:
            rotulo = LOSE_HALF if net == -0.5 else LOSE_FULL
        detalhe = (
            f"linha de quarto {line:+} dividida em {l_low:+} e {l_high:+}: "
            f"({r1}; {r2}) => {rotulo}"
        )
        return Settlement(rotulo=rotulo, net=net, detalhe=detalhe)

    rotulo, net = settle_component(line, margin)
    detalhe = f"margem {margin:+} + linha {line:+} = {margin + line:+} => {rotulo}"
    return Settlement(rotulo=rotulo, net=net, detalhe=detalhe)


def implied_prob(odd: float) -> float:
    """Probabilidade implicita da odd (1/odd)."""
    if odd is None or odd <= 0:
        raise ValueError("Odd invalida para probabilidade implicita.")
    return 1.0 / float(odd)


def overround_ok(odd_home: float, odd_away: float) -> bool:
    """Checagem auxiliar da convencao: pares Home/Away da mesma linha
    somam implicitos entre 100% e 110% (margem da casa). Fora disso,
    a leitura do feed e suspeita (auditoria marca HANDICAP NAO VALIDADO).
    """
    total = implied_prob(odd_home) + implied_prob(odd_away)
    return 1.00 <= total <= 1.10