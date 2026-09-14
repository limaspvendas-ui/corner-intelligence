"""Avaliacao de VALOR / ODD JUSTA de uma aposta REAL montada pelo usuario.

IDENTIFICACAO DA REGRA DE LIQUIDACAO (correcao critica 07/09/2026):

    Linha INTEIRA NAO significa automaticamente PUSH/devolucao.
    Antes de aplicar matematica de push, a REGRA DE LIQUIDACAO do
    mercado deve ser conhecida EXPLICITAMENTE:

    - REGRA_PUSH ("PUSH"): igualdade com a linha DEVOLVE a stake
      => Win / Push / Lose com a matematica de push:

            EV        = W * (odd - 1) - L      (push devolve: lucro 0)
            ODD_JUSTA = 1 + L / W = (1 - P) / W

    - REGRA_IGUALDADE_PERDE: igualdade com a linha e DERROTA
      (regra real de varios bookmakers em linha inteira)
      => a probabilidade de igualdade e somada na derrota:

            W = P(X < linha);  Push = 0;  L = P(X >= linha)
            EV        = W * (odd - 1) - L
            ODD_JUSTA = 1 / W   (caso classico, sem devolucao)

    - Linhas FRACIONADAS tem empate impossivel (p_empate = 0) e
      decaem para o caso classico em QUALQUER regra.

    - Sem regra confirmada (None / REGRA_NAO_CONFIRMADA):
      status "REGRA DE LIQUIDAÇÃO NÃO CONFIRMADA" - NENHUM EV e
      calculado, NENHUM push e inventado.

    NUNCA inferir PUSH apenas porque a linha termina em numero
    inteiro (.0).

MULTIPLAS: cada perna tem desfecho bruto win/empate/perda e a SUA
regra de liquidacao. Perna push vira odd 1,00 e a multipla continua
com as demais; igualdade que perde derruba a perna (e a multipla);
qualquer perna perde => multipla perde. O EV e a soma das
probabilidades de TODOS os desfechos possiveis x lucro/prejuizo -
nunca apenas o produto das vitorias. A probabilidade conjunta assume
INDEPENDENCIA entre os eventos; a premissa e DECLARADA em toda saida.

Escopo (INVIOLAVEL): este modulo NAO altera projecao, probabilidade,
confianca, benchmark, selecao de mercado, ranking, registro,
liquidacao, pre-jogo, live, gols, escanteios, cartoes ou Handicap
Asiatico validados. Ele apenas avalia o VALOR de uma aposta montada
pelo usuario, com as probabilidades que o motor validado ja produziu.

Classificacao final (HEURISTICA v1 - ROTULADA, nao calculo validado):
    EV >= +0.05 por unidade  -> APROVADA
    0 < EV < +0.05           -> APROVADA COM RESSALVA (valor fino)
    EV <= 0                  -> REPROVADA (sem valor esperado)
O limiar de 5% por unidade e escolha operacional, recalibravel.
"""

from __future__ import annotations

import math
from itertools import product
from typing import Any

# Premissa declarada para a probabilidade conjunta de multiplas
PREMISSA_INDEPENDENCIA = (
    "ASSUMINDO INDEPENDENCIA ENTRE OS EVENTOS"
)

APROVADA = "APROVADA"
APROVADA_COM_RESSALVA = "APROVADA COM RESSALVA"
REPROVADA = "REPROVADA"

# HEURISTICA v1 (rotulada): limiar de EV por unidade para aprovacao
# plena; abaixo disso, valor positivo mas fino => ressalva.
EV_MIN_APROVADA = 0.05

# ---------------- regras de liquidacao ----------------
REGRA_PUSH = "PUSH"  # igualdade com a linha DEVOLVE a stake
REGRA_IGUALDADE_PERDE = "IGUALDADE_PERDE"  # igualdade = derrota
REGRAS_CONFIRMADAS = (REGRA_PUSH, REGRA_IGUALDADE_PERDE)
REGRA_NAO_CONFIRMADA = "NAO_CONFIRMADA"  # regra desconhecida
STATUS_OK = "OK"
STATUS_NAO_CONFIRMADA = "REGRA DE LIQUIDAÇÃO NÃO CONFIRMADA"

# Tolerancia para validar p_win + p_empate + p_acima = 1
_TOL_SOMA = 1e-6


def _validar_brutos(
    p_win: float, p_empate: float, p_acima: float,
    exigir_win_positivo: bool = True,
) -> None:
    """Exige desfechos brutos completos: soma 1, nada negativo.

    `exigir_win_positivo` e False para PERNAS de multipla: uma perna
    pode nunca vencer (p_win = 0) sem invalidar a multipla - so a
    aposta SIMPLES exige W > 0 (odd justa de W = 0 e indefinida).
    """
    if min(p_win, p_empate, p_acima) < 0:
        raise ValueError(
            "probabilidades nao podem ser negativas: "
            f"win={p_win}, empate={p_empate}, acima={p_acima}"
        )
    if abs((p_win + p_empate + p_acima) - 1.0) > _TOL_SOMA:
        raise ValueError(
            "win + empate + acima deve somar 1 "
            f"(recebido: {p_win + p_empate + p_acima})"
        )
    if exigir_win_positivo and p_win <= 0:
        raise ValueError("probabilidade de vitoria deve ser > 0")


def converter_pela_regra(
    p_win: float, p_empate: float, p_acima: float, regra: str,
) -> tuple[float, float, float]:
    """Mapeia os desfechos BRUTOS em (W, P, L) pela regra de liquidacao.

    PUSH: empate devolve => (win, empate, acima).
    IGUALDADE_PERDE: empate soma na derrota => (win, 0, empate+acima).
    Qualquer outra regra: erro (regra desconhecida nunca e avaliada).
    """
    if regra == REGRA_PUSH:
        return p_win, p_empate, p_acima
    if regra == REGRA_IGUALDADE_PERDE:
        return p_win, 0.0, p_empate + p_acima
    raise ValueError(
        f"regra de liquidacao desconhecida: {regra!r} "
        f"(validas: {REGRAS_CONFIRMADAS})"
    )


# ----------------------------------------------------------------------
# Matematica BRUTA de (W, P, L) - preservada para mercados que DEVOLVEM
# ----------------------------------------------------------------------
def odd_justa(w: float, p: float = 0.0, l: float | None = None) -> float:
    """Odd justa de uma aposta simples.

    Sem push (P = 0): 1 / W (caso classico).
    Com push: 1 + L / W = (1 - P) / W - o push devolve a stake e
    NAO pode ser tratado como derrota.
    """
    if l is None:
        l = 1.0 - w - p
    _validar_brutos(w, p, l)
    return (1.0 - p) / w


def ev_simples(
    w: float, p: float, l: float | None, odd: float,
) -> float:
    """EV por unidade apostada: W*(odd-1) - L (push devolve, lucro 0)."""
    if l is None:
        l = 1.0 - w - p
    _validar_brutos(w, p, l)
    if odd <= 1.0:
        raise ValueError(f"odd decimal deve ser > 1 (recebido: {odd})")
    return w * (odd - 1.0) - l


def classificar(ev: float) -> str:
    """Classificacao final pela HEURISTICA v1 (rotulada): ver docstring."""
    if ev >= EV_MIN_APROVADA:
        return APROVADA
    if ev > 0.0:
        return APROVADA_COM_RESSALVA
    return REPROVADA


# ----------------------------------------------------------------------
# Aposta SIMPLES com IDENTIFICACAO da regra de liquidacao
# ----------------------------------------------------------------------
def avaliar_simples(
    p_win: float,
    p_empate: float,
    p_acima: float,
    odd: float,
    regra_liquidacao: str | None,
) -> dict[str, Any]:
    """Avaliacao completa de UMA selecao PELA regra de liquidacao real.

    Entradas sao os desfechos BRUTOS ao redor da linha:
        p_win    = P(ficar ABAIXO da linha)
        p_empate = P(EMPATAR a linha) - 0.0 em linha fracionada
        p_acima  = P(ficar ACIMA da linha)
    Sem regra confirmada => status REGRA DE LIQUIDAÇÃO NÃO CONFIRMADA,
    sem EV, sem odd justa, sem classificacao. NADA e inventado.
    """
    _validar_brutos(p_win, p_empate, p_acima)
    base = {
        "status": STATUS_OK,
        "p_win": p_win,
        "p_empate": p_empate,
        "p_acima": p_acima,
        "odd": odd,
        "prob_implicita": round(1.0 / odd, 4),
    }
    if regra_liquidacao is None or (
        isinstance(regra_liquidacao, str)
        and regra_liquidacao == REGRA_NAO_CONFIRMADA
    ):
        return {
            **base,
            "status": STATUS_NAO_CONFIRMADA,
            "regra_liquidacao": REGRA_NAO_CONFIRMADA,
            "win": None,
            "push": None,
            "lose": None,
            "odd_justa": None,
            "ev": None,
            "classificacao": None,
        }
    if regra_liquidacao not in REGRAS_CONFIRMADAS:
        raise ValueError(
            f"regra de liquidacao desconhecida: {regra_liquidacao!r} "
            f"(validas: {REGRAS_CONFIRMADAS})"
        )
    w, p, l = converter_pela_regra(
        p_win, p_empate, p_acima, regra_liquidacao)
    ev = ev_simples(w, p, l, odd)
    return {
        **base,
        "regra_liquidacao": regra_liquidacao,
        "win": round(w, 4),
        "push": round(p, 4),
        "lose": round(l, 4),
        "odd_justa": round(odd_justa(w, p, l), 4),
        "ev": round(ev, 4),
        "classificacao": classificar(ev),
    }


# ----------------------------------------------------------------------
# MULTIPLAS (enumeracao completa de desfechos, nunca produto de W)
# ----------------------------------------------------------------------
def _perna_valida(
    perna: tuple[float, float, float, float, str],
) -> None:
    p_win, p_empate, p_acima, odd, regra = perna
    # perna pode ter p_win = 0 (nunca vence); a soma 1 e obrigatoria
    _validar_brutos(p_win, p_empate, p_acima, exigir_win_positivo=False)
    if odd <= 1.0:
        raise ValueError(
            f"odd decimal da perna deve ser > 1 (recebido: {odd})"
        )
    if regra not in REGRAS_CONFIRMADAS:
        raise ValueError(
            f"regra de liquidacao da perna deve ser confirmada "
            f"(recebido: {regra!r}; validas: {REGRAS_CONFIRMADAS})"
        )


def enumerar_desfechos(
    pernas: list[tuple[float, float, float, float, str]],
) -> list[dict[str, Any]]:
    """Todos os desfechos da multipla com probabilidade conjunta e lucro.

    Perna: (p_win, p_empate, p_acima, odd, regra). Cada perna tem 3
    saidas brutas; a REGRA da perna decide o empate:
      - PUSH: empate devolve => perna vira odd 1,00 na multipla;
      - IGUALDADE_PERDE: empate derruba a perna (multipla perde).
    A multipla perde se QUALQUER perna perder; duas ou mais push =>
    permanecem somente as pernas vencedoras; todas push => stake
    devolvida. Probabilidade conjunta = produto (INDEPENDENCIA).
    """
    for perna in pernas:
        _perna_valida(perna)
    if not pernas:
        raise ValueError("multipla sem pernas")

    saidas = []
    for combo in product(*[(0, 1, 2)] * len(pernas)):
        prob = 1.0
        payout = 1.0
        perdeu = False
        pushes = 0
        for perna, s in zip(pernas, combo):
            p_win, p_empate, p_acima, odd, regra = perna
            if s == 0:  # win: paga a odd da perna
                prob *= p_win
                payout *= odd
            elif s == 1 and regra == REGRA_PUSH:  # empate devolve
                prob *= p_empate
                pushes += 1
            elif s == 1:  # empate PERDE: derruba a multipla
                prob *= p_empate
                perdeu = True
            else:  # acima da linha: multipla perde
                prob *= p_acima
                perdeu = True
        lucro = -1.0 if perdeu else payout - 1.0
        saidas.append({
            "desfecho": combo,
            "pushes": pushes,
            "perdeu": perdeu,
            "payout": round(payout, 4),
            "probabilidade": round(prob, 6),
            "lucro_por_unidade": round(lucro, 4),
        })
    return saidas


def avaliar_multipla(
    pernas: list[tuple[float, float, float, float, str | None]],
) -> dict[str, Any]:
    """Avaliacao completa de uma multipla com regra por perna.

    Perna: (p_win, p_empate, p_acima, odd, regra_liquidacao). Se
    QUALQUER perna estiver com a regra nao confirmada, a multipla
    inteira fica REGRA DE LIQUIDAÇÃO NÃO CONFIRMADA - nenhum EV e
    calculado, nenhum push e inventado.
    """
    if not pernas:
        raise ValueError("multipla sem pernas")
    nao_confirmadas = [
        i for i, perna in enumerate(pernas)
        if perna[4] is None or perna[4] == REGRA_NAO_CONFIRMADA
    ]
    base = {
        "premissa": PREMISSA_INDEPENDENCIA,
        "odd_combinada": round(math.prod(perna[3] for perna in pernas), 4),
    }
    if nao_confirmadas:
        return {
            **base,
            "status": STATUS_NAO_CONFIRMADA,
            "pernas_nao_confirmadas": nao_confirmadas,
            "desfechos": None,
            "ev": None,
            "odd_justa_vitoria_plena": None,
            "classificacao": None,
        }

    desfechos = enumerar_desfechos(pernas)
    ev = sum(
        d["probabilidade"] * d["lucro_por_unidade"] for d in desfechos
    )
    odd_combinada = 1.0
    for _w, _e, _a, odd, _r in pernas:
        odd_combinada *= odd

    # Odd justa do desfecho de VITORIA PLENA: odd combinada que zera o
    # EV MANTENDO as odds/pernas nos desfechos com push (a perna que
    # empurra continua pagando a propria odd, nao a combinada). Sem
    # nenhuma perna com push, decai para o classico 1 / W_full.
    vitoria_plena = next(
        (d for d in desfechos
         if not d["perdeu"] and d["pushes"] == 0),
        None,
    )
    com_push = [
        d for d in desfechos
        if not d["perdeu"] and d["pushes"] > 0
    ]
    odd_justa_comb = None
    if vitoria_plena and vitoria_plena["probabilidade"] > 0:
        subsidio_push = sum(
            d["probabilidade"] * d["lucro_por_unidade"] for d in com_push
        )
        perdas = sum(
            d["probabilidade"] for d in desfechos if d["perdeu"]
        )
        w_full = vitoria_plena["probabilidade"]
        odd_justa_comb = 1.0 + (perdas - subsidio_push) / w_full

    return {
        "status": STATUS_OK,
        "premissa": PREMISSA_INDEPENDENCIA,
        "odd_combinada": round(odd_combinada, 4),
        "desfechos": desfechos,
        "ev": round(ev, 4),
        "odd_justa_vitoria_plena": (
            round(odd_justa_comb, 4)
            if odd_justa_comb is not None else None
        ),
        "classificacao": classificar(ev),
    }