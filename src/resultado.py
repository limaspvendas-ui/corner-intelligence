"""MERCADOS DE RESULTADO PRE-JOGO: 1X2, DUPLA CHANCE, DNB, HANDICAP
ASIATICO - bloco NOVO da ampliacao controlada de mercados.

Este bloco NAO esta liberado para recomendacao: a regra da ampliacao
exige logica validada (liquidacao + testes) ANTES de qualquer uso real.
Ele apenas PRODUZ avaliacoes no mesmo formato do motor validado
(AvaliacaoPre) para que, apos validacao do operador, entrem no mesmo
comparador de oportunidades (src/policy.py).

Nada do motor validado e alterado:
    - baselines: MESMO cruzamento casa/fora do motor (_taxa_cruzada);
    - combinacao com o benchmark da liga: MESMOS pesos do pre-jogo
      validado (65% times + 35% liga, media da liga dividida por lado);
    - confianca: MESMA funcao do pre-jogo validado (_confianca_prejogo);
    - liquidacao do AH: MESMO motor validado (src/handicap.py, convencao
      auditada em jogo real 06/09/2026).

CONVENCAO DE PROBABILIDADE (rotulada - uniforme em TODA a familia):
    prob = P(vitoria integral) + 0.5*P(meia vitoria), computada pela
    MESMA liquidacao validada (src/handicap.py settle) aplicada a cada
    margem do placar simulado; DEVOLUCAO nao conta nem a favor nem
    contra (devolve o stake). E a MESMA convencao do motor live
    validado (_prob_vitoria_equivalente). Consequencias:
    - DNB e AH 0.0 produzem o MESMO numero: sao o mesmo mercado
      (regra do operador) e aqui sao tratados de forma identica;
    - mais CONSERVADORA que a probabilidade condicional implicita em
      odds de mercado com devolucao (odd justa de DNB = (1-empate)/
      vitoria => implicito = vitoria/(1-empate)): ao comparar com odd
      real no futuro, o edge apurado sera um PISO, nunca inflado.

Identidade: as avaliacoes herdam o fixture ja reancorado pelo motor
(os IDs dos times vem do fixture, regra 12); nada e buscado por nome
aqui. O modelo e um CALCULO Poisson por lado sobre os 90 minutos,
rotulado como estimativa - nunca dado da API.
"""

from __future__ import annotations

from typing import Any

from src.handicap import settle
from src.live_opportunity import _taxa_cruzada, poisson_pmf
from src.prejogo_opportunity import AvaliacaoPre, _confianca_prejogo

# Linhas de AH avaliadas por lado (perspectiva do LADO apostado):
# 0.0 (equivale a DNB), quartos, meias e inteiras ate +/-1.5.
MAGNITUDES_AH_PREJOGO = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5)

# Truncamento da soma de Poisson por lado (placares simulados 0..8)
MAX_GOLS_LADO = 8

MERCADO_RESULTADO = "resultado"

_MODELO = (
    "Poisson por lado sobre 90 minutos completos "
    "(CALCULO, nao dado da API)"
)


# ----------------------------------------------------------------------
# Lambdas pre-jogo por lado (mesmo cruzamento validado do motor)
# ----------------------------------------------------------------------
def lambdas_prejogo(
    hist: dict[str, Any],
    benchmark_gols: dict[str, Any] | None,
) -> tuple[float | None, float | None, str]:
    """(lambda mandante, lambda visitante, detalhe).

    Cruzamento casa/fora do motor (gols pro do lado + gols contra do
    adversario, /2) combinado com o benchmark da liga nos MESMOS pesos
    do pre-jogo validado (65% times + 35% liga; media da liga dividida
    por lado). Sem historico => (None, None, motivo): NUNCA se inventa.
    """
    th = _taxa_cruzada(hist.get("games_home") or [], "casa")
    ta = _taxa_cruzada(hist.get("games_away") or [], "fora")
    if None in (th["gols_pro"], th["gols_contra"],
                ta["gols_pro"], ta["gols_contra"]):
        return None, None, "sem medias de gols no historico"

    exp_home = (th["gols_pro"] + ta["gols_contra"]) / 2
    exp_away = (ta["gols_pro"] + th["gols_contra"]) / 2

    league_mean = (benchmark_gols or {}).get("describe", {}).get("media")
    if league_mean is not None:
        lam_home = 0.65 * exp_home + 0.35 * league_mean / 2
        lam_away = 0.65 * exp_away + 0.35 * league_mean / 2
        detalhe = (
            f"cruzamento casa/fora (n mandante {th['n_usados']}, "
            f"n visitante {ta['n_usados']}): mandante "
            f"{round(exp_home, 2)} + visitante {round(exp_away, 2)} gols; "
            f"benchmark da liga {round(league_mean, 2)} "
            "(65% times + 35% liga, media/2 por lado)"
        )
        return lam_home, lam_away, detalhe
    return exp_home, exp_away, (
        f"cruzamento casa/fora (n mandante {th['n_usados']}, n visitante "
        f"{ta['n_usados']}): mandante {round(exp_home, 2)} + visitante "
        f"{round(exp_away, 2)} gols; sem benchmark da liga"
    )


def distribuicao_margem_prejogo(
    lam_home: float,
    lam_away: float,
    max_gols: int = MAX_GOLS_LADO,
) -> dict[int, float]:
    """Distribuicao do diferencial de gols final (mandante - visitante)
    por Poisson independente por lado, truncada em `max_gols` por lado
    e RENORMALIZADA (a massa da cauda truncada e redistribuida: a soma
    das probabilidades e 1 por construcao, aproximacao rotulada)."""
    dist: dict[int, float] = {}
    for i in range(max_gols + 1):
        pi = poisson_pmf(i, lam_home)
        for j in range(max_gols + 1):
            d = i - j
            dist[d] = dist.get(d, 0.0) + pi * poisson_pmf(j, lam_away)
    massa = sum(dist.values())
    if massa > 0:
        dist = {d: p / massa for d, p in dist.items()}
    return dist


# ----------------------------------------------------------------------
# Probabilidades por mercado (convencao rotulada no cabecalho)
# ----------------------------------------------------------------------
def prob_1x2(dist: dict[int, float]) -> tuple[float, float, float]:
    """(P mandante, P empate, P visitante)."""
    p1 = sum(p for d, p in dist.items() if d > 0)
    px = dist.get(0, 0.0)
    p2 = sum(p for d, p in dist.items() if d < 0)
    return p1, px, p2


def prob_dupla_chance(dist: dict[int, float]) -> dict[str, float]:
    p1, px, p2 = prob_1x2(dist)
    return {"1X": p1 + px, "12": p1 + p2, "X2": px + p2}


def prob_dnb(dist: dict[int, float], lado: str) -> float | None:
    """DNB = AH 0.0: probabilidade de vitoria do lado (empate devolve
    o stake e NAO conta nem a favor nem contra - convencao uniforme,
    conservadora, rotulada no cabecalho do modulo)."""
    return prob_ah(dist, lado, 0.0)


def prob_ah(dist: dict[int, float], lado: str, linha: float) -> float | None:
    """Probabilidade de vitoria equivalente da linha: P(vitoria
    integral) + 0.5*P(meia vitoria), pela MESMA liquidacao validada
    (src/handicap.py settle) aplicada a cada margem simulada. A
    devolucao (net == 0) nao entra no somatorio.
    """
    win_equiv = 0.0
    for d, p in dist.items():
        margem = d if lado == "mandante" else -d
        s = settle(linha, margem)
        if s.net > 0:
            win_equiv += p * s.net          # 1.0 integral / 0.5 meia
    return min(win_equiv, 1.0)


def decomposicao_ah(
    dist: dict[int, float], lado: str, linha: float
) -> dict[str, float] | None:
    """Decomposicao EXATA da liquidacao de uma linha AH/DNB sobre a
    distribuicao de margem, pela MESMA liquidacao validada
    (src/handicap.py settle: vitoria plena +1, meia vitoria +0.5,
    devolucao 0, meia perda -0.5, perda plena -1):

        GANHO     G = soma p*max(net, 0)   (== prob_ah da linha)
        PERDA     L = soma p*max(-net, 0)
        DEVOLUCAO D = soma p com net == 0  (stake devolvido)

    Base da ODD JUSTA DE EQUILIBRIO (EV = 0, Bloco A 09/09/2026):
    EV(odd) = (odd-1)*G - L = 0  =>  odd* = 1 + L/G.

    Exata tambem nas linhas de QUARTO (meia vitoria/meia perda
    contabilizadas a 0.5) - nunca aproximada por 1/prob. Reduz a
    (1-D)/G no DNB/AH 0.0 (mesma formula validada de
    src/valor_aposta.odd_justa) e a 1/prob nas linhas de meia sem
    devolucao. GANHO nulo => linha sem vitoria possivel: retorna None
    (ODD JUSTA NAO CALCULAVEL - DADOS INSUFICIENTES).
    """
    if not dist:
        return None
    ganho = perda = devolucao = 0.0
    for d, p in dist.items():
        margem = d if lado == "mandante" else -d
        s = settle(linha, margem)
        if s.net > 0:
            ganho += p * s.net
        elif s.net < 0:
            perda += p * (-s.net)
        else:
            devolucao += p
    if ganho <= 0.0:
        return None
    return {
        "ganho": round(ganho, 6),
        "perda": round(perda, 6),
        "devolucao": round(devolucao, 6),
    }


# ----------------------------------------------------------------------
# Formato das linhas (o texto E a chave de liquidacao no registro -
# src/settlement.py casa com estes formatos EXATOS)
# ----------------------------------------------------------------------
def fmt_ah(linha: float) -> str:
    if linha == 0:
        return "0.0"
    return f"{linha:+g}"


def linhas_ah_prejogo() -> list[float]:
    """Leque simetrico por lado: 0.0, +/-0.25 ... +/-1.5."""
    out: list[float] = []
    for mag in MAGNITUDES_AH_PREJOGO:
        if mag == 0.0:
            out.append(0.0)
        else:
            out.extend((-mag, mag))
    return out


# ----------------------------------------------------------------------
# Avaliacao pre-jogo da familia RESULTADO
# ----------------------------------------------------------------------
def avaliar_resultado_prejogo(
    hist: dict[str, Any],
    benchmark_gols: dict[str, Any] | None,
    h2h_n: int = 0,
) -> list[AvaliacaoPre]:
    """Avalia 1X2, Dupla Chance, DNB e AH ANTES do jogo comecar.

    Mesma regra do motor validado: sem sustentacao (sem medias de gols)
    => lista vazia, nada e inventado. As avaliacoes entram NO FORMATO
    AvaliacaoPre para futura comparacao unificada - mas este bloco NAO
    esta liberado para recomendacao/registro (aguarda validacao).
    """
    lam_h, lam_a, det = lambdas_prejogo(hist, benchmark_gols)
    if lam_h is None or lam_a is None:
        return []

    dist = distribuicao_margem_prejogo(lam_h, lam_a)
    p1, px, p2 = prob_1x2(dist)
    dc = prob_dupla_chance(dist)

    ns = [v for v in (hist.get("n_home"), hist.get("n_away")) if v]
    n_min = min(ns) if ns else 0
    validas = (benchmark_gols or {}).get("partidas_validas")
    conf, comps = _confianca_prejogo(n_min, validas, h2h_n)

    riscos = ["mercado de resultado: modelo Poisson por lado (estimativa)"]
    if validas is None:
        riscos.append("sem benchmark da liga: apenas historico dos times")
    if n_min < 10:
        riscos.append(
            f"amostra historica pequena: {n_min} jogos (minimo por time)"
        )

    base_sust = {
        "baseline_pre_jogo": det,
        "lambda_mandante": round(lam_h, 2),
        "lambda_visitante": round(lam_a, 2),
        "modelo": _MODELO,
    }

    out: list[AvaliacaoPre] = []

    def _add(
        linha: str,
        prob: float,
        extra: str = "",
        decomp: dict[str, float] | None = None,
    ) -> None:
        sust = dict(base_sust)
        if extra:
            sust["convencao"] = extra
        if decomp is not None:
            # Bloco A (09/09/2026): decomposicao exata da liquidacao
            # (ganho/perda/devolucao) que sustenta a ODD JUSTA DE
            # EQUILIBRIO EV = 0 (odd* = 1 + perda/ganho) na camada
            # operacional. Nao altera nenhuma probabilidade.
            sust["equilibrio_liquidacao"] = decomp
        out.append(
            AvaliacaoPre(
                mercado=MERCADO_RESULTADO,
                linha=linha,
                prob=round(prob, 4),
                confianca=conf,
                conf_componentes=comps,
                sustentacao=sust,
                riscos=list(riscos),
            )
        )

    # 1X2
    _add("Vitoria mandante (1)", p1)
    _add("Empate (X)", px)
    _add("Vitoria visitante (2)", p2)

    # Dupla chance
    _add("Dupla chance 1X", dc["1X"])
    _add("Dupla chance 12", dc["12"])
    _add("Dupla chance X2", dc["X2"])

    # DNB (equivale a AH 0.0 - mesmo numero, mesmo mercado)
    for lado in ("mandante", "visitante"):
        p = prob_dnb(dist, lado)
        if p is None:
            continue
        _add(
            f"DNB {lado} (empate anula)",
            p,
            extra="prob = P(vitoria do lado); empate devolve o stake e "
                  "nao conta; equivale a AH 0.0 (mesmo numero, mesmo "
                  "mercado)",
            decomp=decomposicao_ah(dist, lado, 0.0),
        )

    # Handicap asiatico por lado (linhas 0.0 +/-0.25 ... +/-1.5)
    for lado in ("mandante", "visitante"):
        for linha in linhas_ah_prejogo():
            p = prob_ah(dist, lado, linha)
            if p is None:
                continue
            _add(
                f"AH {lado} {fmt_ah(linha)} (90 minutos)",
                p,
                extra=(
                    "liquidacao validada (src/handicap.py): "
                    f"linha real {fmt_ah(linha)} na perspectiva do {lado}"
                ),
                decomp=decomposicao_ah(dist, lado, linha),
            )
    return out