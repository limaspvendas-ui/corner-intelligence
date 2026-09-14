"""POLITICA OPERACIONAL DE SELECAO/APRESENTACAO DA LINHA PRINCIPAL (v2).

Regra do operador (08/09/2026): separar MELHOR PREVISAO ESTATISTICA de
MELHOR APOSTA PRATICA. Este modulo NAO altera nenhum calculo validado:
probabilidade, confianca, coleta de dados, benchmarks, mercados e a
classificacao estatistica de seguranca (src/politica_aprovacao.py)
continham exatamente como estao - inclusive a disciplina de aprovacao
(janela 70%-97% + confianca minima, constantes importadas). A unica
mudanca e QUAL linha e apresentada como APOSTA PRINCIPAL e COMO o
relatorio a apresenta.

Regra operacional: trabalhar, quando possivel, com odds ~>= 1,15
(faixa preferencial 1,15 a 1,20 ou superior). A odd NAO comanda a
probabilidade - probabilidade e confianca continuam sendo a base da
analise; a mudanca e somente na escolha da linha MAIS UTIL para aposta.

    - Linhas extremamente largas (ex.: Under 7.5 gols) podem ter
      probabilidade altissima e continuar ESTATISTICAMENTE aprovadas -
      mas sao marcadas "ALTA PROBABILIDADE, MAS BAIXA UTILIDADE
      OPERACIONAL" e NAO sao apresentadas como aposta principal.
    - Odd REAL comanda o piso quando a fonte fornece (parametro
      `odds_reais`, camada validada src/odds.py). Odd real ausente =>
      NUNCA se inventa: usa-se apenas a ODD JUSTA INFORMATIVA (1/prob),
      rotulada como tal.
    - Seguranca nunca e rebaixada artificialmente para conseguir odd
      maior. ORDEM OBRIGATORIA entre as linhas utilizaveis (v2,
      correcao do operador 08/09/2026):
        1. MAIOR PROBABILIDADE ESTIMADA;
        2. maior confianca;
        3. qualidade da amostra historica;
        4. estabilidade;
        5. margem de seguranca (subsumida pelo proprio fator 1 - e
           monotona com a probabilidade);
        6. demais fatores, nesta ordem: contradicoes registradas,
           posicao no ranking de score da politica e, por ultimo,
           linha mais proxima do equilibrio.
      O score NUNCA pode escolher uma linha de probabilidade
      significativamente menor quando existe outra linha operacional
      com probabilidade maior e odd dentro da faixa aceitavel.
    - Nenhuma linha utilizavel => "NENHUMA LINHA COM UTILIDADE
      OPERACIONAL APROVADA."

BLOCO A (09/09/2026 - CORRECAO DA ODD JUSTA E CRIACAO DA ODD MINIMA,
ETAPA 2 da auditoria): ODD JUSTA passa a ser a de EQUILIBRIO (EV = 0)
- 1/prob apenas nos mercados SEM devolucao; nos mercados com
devolucao (DNB, AH 0.0) vale (1 - P_devolucao)/P_vitoria (mesma
formula validada de src/valor_aposta.odd_justa) e nas linhas de
quarto (+/-0.25, +/-0.75, +/-1.25) a decomposicao EXATA da
liquidacao (odd = 1 + perda/ganho, com meia vitoria/meia perda a
0.5 - src/resultado.py decomposicao_ah). Nao calculavel =>
"ODD JUSTA NAO CALCULAVEL - DADOS INSUFICIENTES" (nunca aproximada).
A ODD MINIMA ACEITAVEL = odd justa + 5% de margem operacional,
arredondada PARA CIMA em centavos; o valor 1.15 continua existindo
apenas como PISO DE UTILIDADE - nunca apresentado como odd minima.
Edge e valor esperado so existem com odd real.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from src.live_opportunity import CONF_MIN_TOP1, PROB_MAX_APROVAR, PROB_MIN_APROVAR

# Piso de utilidade do operador (faixa preferencial 1,15 a 1,20 OU
# SUPERIOR - nunca um teto). Bloco A (09/09/2026): este valor e apenas
# o PISO DE UTILIDADE - NUNCA e apresentado como odd minima aceitavel
# (a odd minima aceitavel vem da odd justa de equilibrio + margem).
ODD_MIN_OPERACIONAL = 1.15

# Bloco A (09/09/2026): margem operacional sobre a odd justa que define
# a ODD MINIMA ACEITAVEL = odd_justa * (1 + MARGEM_OPERACIONAL),
# arredondada PARA CIMA em centavos (o arredondamento nunca reduz a
# margem exigida).
MARGEM_OPERACIONAL = 0.05

MARCA_BAIXA_UTILIDADE = "ALTA PROBABILIDADE, MAS BAIXA UTILIDADE OPERACIONAL"
NENHUMA_UTIL_MSG = "NENHUMA LINHA COM UTILIDADE OPERACIONAL APROVADA."
ODD_REAL_INDISPONIVEL = "ODD NAO DISPONIVEL NA FONTE"
ODD_JUSTA_NAO_CALCULAVEL = "ODD JUSTA NAO CALCULAVEL - DADOS INSUFICIENTES"

# Situacoes da linha perante a odd (Bloco A 09/09/2026)
SIT_APROVAVEL = "APROVAVEL"
SIT_OPORTUNIDADE = "OPORTUNIDADE ESTATISTICA - AGUARDANDO ODD"
SIT_REPROVADA_SEM_VALOR = "REPROVADA - SEM VALOR SUFICIENTE"
SIT_BAIXA_UTILIDADE = "BAIXA UTILIDADE OPERACIONAL"
SIT_NAO_AVALIAVEL = "NAO AVALIAVEL"

# Linha over/under "Over 2.5 gols (total do jogo)" e variantes da
# familia cartoes. Familias sem linha numerica (1X2/DC/DNB): sem
# distancia de equilibrio (0.0), igual ao comparador da politica.
_RE_OU = re.compile(r"^(Over|Under)\s+(\d+(?:[.,]\d+)?)\b")


def odd_justa(prob: float) -> float | None:
    """ODD JUSTA INFORMATIVA: 1/probabilidade. Rotulo obrigatorio -
    nunca e apresentada como odd real da casa.

    Bloco A (09/09/2026): valida SOMENTE para mercados SEM devolucao
    (Over/Under X,5, 1X2, Dupla Chance, AH de meia). Para mercados com
    devolucao (DNB, AH 0.0) e linhas de quarto (+/-0.25, +/-0.75,
    +/-1.25) usar odd_justa_avaliacao - a decomposicao exata da
    liquidacao, onde a odd de equilibrio e 1 + perda/ganho (EV = 0),
    NUNCA 1/prob.
    """
    if not prob or prob <= 0:
        return None
    return round(1.0 / prob, 2)


def decomposicao_avaliacao(av: Any) -> dict[str, float] | None:
    """Decomposicao da liquidacao (ganho/perda/devolucao) guardada pelo
    motor na sustentacao da avaliacao (src/resultado.py
    decomposicao_ah, Bloco A 09/09/2026). Ausente => None."""
    eq = (getattr(av, "sustentacao", None) or {}).get(
        "equilibrio_liquidacao"
    )
    return eq if isinstance(eq, dict) else None


def odd_justa_equilibrio(ganho: float, perda: float) -> float | None:
    """Odd de equilibrio EXATA (EV = 0) a partir da decomposicao da
    liquidacao: EV(odd) = (odd-1)*G - L = 0  =>  odd = 1 + L/G.

    No DNB/AH 0.0 equivale a (1 - P_devolucao)/P_vitoria - a MESMA
    formula validada de src/valor_aposta.odd_justa (nunca duplicada
    por aqui: os testes conferem a equivalencia). Nas linhas de meia
    sem devolucao equivale a 1/prob. Linhas de quarto: valor exato,
    com meia vitoria/meia perda contabilizadas a 0.5. GANHO nulo =>
    None (ODD JUSTA NAO CALCULAVEL - DADOS INSUFICIENTES)."""
    if ganho is None or ganho <= 0:
        return None
    return round(1.0 + (perda or 0.0) / ganho, 2)


def odd_justa_avaliacao(av: Any) -> float | None:
    """Odd justa de equilibrio de UMA avaliacao (Bloco A 09/09/2026):
    usa a decomposicao exata quando existe (familia resultado com
    devolucao - DNB e AH); mercados sem devolucao usam 1/prob.

    Linha DNB/AH SEM decomposicao na sustentacao => None: nunca se
    aproxima por 1/prob o que a fonte permite calcular exatamente
    (ODD JUSTA NAO CALCULAVEL - DADOS INSUFICIENTES)."""
    eq = decomposicao_avaliacao(av)
    if eq is not None:
        return odd_justa_equilibrio(eq.get("ganho"), eq.get("perda"))
    linha = (getattr(av, "linha", "") or "").strip()
    if linha.startswith(("DNB", "AH")):
        return None
    return odd_justa(getattr(av, "prob", None))


def prob_devolucao_avaliacao(av: Any) -> float:
    """Probabilidade de devolucao (stake retornado) da linha: da
    decomposicao quando existe; 0.0 nos mercados sem devolucao."""
    eq = decomposicao_avaliacao(av)
    if eq is None:
        return 0.0
    return float(eq.get("devolucao") or 0.0)


def odd_minima_aceitavel(odd_justa_valor: float | None) -> float | None:
    """ODD MINIMA ACEITAVEL (Bloco A 09/09/2026): odd justa + margem
    operacional de 5%, arredondada PARA CIMA em centavos - o
    arredondamento nunca reduz a margem exigida (1,30*1,05 = 1,365 =>
    1,37, nunca 1,36; 2,00*1,05 = 2,10 exato fica 2,10, sem subir um
    centavo de ruido de ponto flutuante)."""
    if odd_justa_valor is None:
        return None
    bruto = round(odd_justa_valor * (1.0 + MARGEM_OPERACIONAL), 10)
    centavos = bruto * 100
    exato = round(centavos)
    if abs(centavos - exato) < 1e-6:
        return exato / 100
    return math.ceil(centavos) / 100


@dataclass
class UtilidadeLinha:
    """Utilidade operacional de UMA linha ja disciplinada (Bloco A
    09/09/2026): alem do piso de utilidade, carrega a odd justa de
    equilibrio (exata nos mercados com devolucao), a odd minima
    aceitavel (justa + margem de 5%), a probabilidade de devolucao, o
    edge/valor esperado (somente com odd real - nunca inventados) e a
    SITUACAO da linha."""

    prob: float
    odd_justa: float | None
    odd_real: float | None = None
    odd_efetiva: float | None = None  # real quando existe; senao justa
    utilizavel: bool = False
    marca: str | None = None
    prob_devolucao: float = 0.0
    odd_minima: float | None = None   # justa + margem operacional 5%
    situacao: str | None = None
    edge: float | None = None         # (real - justa)/justa; so com odd real
    ev: float | None = None           # por unidade de stake; so com odd real


def avaliar_utilidade(
    prob: float,
    odd_real: float | None = None,
    av: Any | None = None,
) -> UtilidadeLinha:
    """Piso de utilidade sobre a odd EFETIVA (a odd real comanda quando
    a fonte fornece; senao vale a odd justa) + regras de decisao do
    Bloco A (09/09/2026):

      - odd justa nao calculavel => NAO AVALIAVEL;
      - odd EFETIVA abaixo do piso 1.15 => BAIXA UTILIDADE OPERACIONAL
        (mesmo com probabilidade dentro da janela);
      - odd real abaixo da odd minima aceitavel (justa + 5%) =>
        REPROVADA - SEM VALOR SUFICIENTE;
      - odd real >= odd minima aceitavel => APROVAVEL;
      - sem odd real => OPORTUNIDADE ESTATISTICA - AGUARDANDO ODD.

    `av` (a avaliacao da linha) e usado para a odd justa de
    equilibrio EXATA nos mercados com devolucao (decomposicao da
    liquidacao em sustentacao) e para a probabilidade de devolucao.
    Sem `av` (chamada por probabilidade pura) vale 1/prob - valido
    apenas para mercados sem devolucao. Edge e valor esperado SO
    existem com odd real: nunca sao inventados."""
    oj = odd_justa_avaliacao(av) if av is not None else odd_justa(prob)
    efetiva = float(odd_real) if odd_real and odd_real > 0 else oj
    om = odd_minima_aceitavel(oj)
    p_devol = prob_devolucao_avaliacao(av) if av is not None else 0.0

    if oj is None:
        situacao = SIT_NAO_AVALIAVEL
        utilizavel = False
    elif efetiva is not None and efetiva < ODD_MIN_OPERACIONAL:
        situacao = SIT_BAIXA_UTILIDADE
        utilizavel = False
    elif odd_real and odd_real > 0 and om is not None and odd_real < om:
        situacao = SIT_REPROVADA_SEM_VALOR
        utilizavel = False
    elif odd_real and odd_real > 0:
        situacao = SIT_APROVAVEL
        utilizavel = True
    else:
        situacao = SIT_OPORTUNIDADE
        utilizavel = True

    edge = ev = None
    if odd_real and odd_real > 0 and oj is not None:
        edge = round((odd_real - oj) / oj, 4)
        eq = decomposicao_avaliacao(av) if av is not None else None
        if eq:
            ev = round(
                eq["ganho"] * (odd_real - 1.0) - eq["perda"], 4
            )
        else:
            ev = round(prob * (odd_real - 1.0) - (1.0 - prob), 4)

    return UtilidadeLinha(
        prob=prob,
        odd_justa=oj,
        odd_real=odd_real,
        odd_efetiva=efetiva,
        utilizavel=utilizavel,
        marca=MARCA_BAIXA_UTILIDADE if situacao == SIT_BAIXA_UTILIDADE
        else None,
        prob_devolucao=p_devol,
        odd_minima=om,
        situacao=situacao,
        edge=edge,
        ev=ev,
    )


def _disciplinadas(avaliacoes: list[Any]) -> list[Any]:
    """MESMA disciplina do motor validado (nada recalculado aqui): janela
    de probabilidade + confianca minima."""
    return [
        a for a in (avaliacoes or [])
        if PROB_MIN_APROVAR <= a.prob <= PROB_MAX_APROVAR
        and a.confianca >= CONF_MIN_TOP1
    ]


def _dist_equilibrio(av: Any) -> float:
    """Distancia |linha - equilibrio| da projecao (mesma formula do motor:
    equilibrio = parte inteira do lambda + 0.5). Sem lambda ou sem linha
    numerica => 0.0 (nunca penaliza o que nao pode medir)."""
    lam = (getattr(av, "sustentacao", None) or {}).get("lambda_por90")
    m = _RE_OU.match((av.linha or "").strip())
    if lam is None or m is None:
        return 0.0
    valor = float(m.group(2).replace(",", "."))
    return abs(valor - (float(int(lam)) + 0.5))


def _chave_ordenacao(
    av: Any,
    estabilidades: dict[int, float] | None = None,
    rank: dict[int, int] | None = None,
) -> tuple:
    """ORDEM OBRIGATORIA entre as linhas utilizaveis (regra do
    operador 08/09/2026): 1. maior probabilidade; 2. confianca;
    3. qualidade da amostra historica (componente validada da
    confianca); 4. estabilidade (fator do comparador de politica);
    5. margem de seguranca (subsumida pela probabilidade - monotona);
    6. demais fatores, nesta ordem: menos contradicoes registradas,
    posicao no ranking de score da politica e, por ultimo, linha mais
    proxima do equilibrio.

    A probabilidade NUNCA e ultrapassada pelo score: uma linha de
    probabilidade menor nao pode ser escolhida quando existe linha
    operacional de probabilidade maior. O score so e consultado quando
    probabilidade, confianca, amostra, estabilidade e contradicoes
    estao TODAS empatadas."""
    estabilidades = estabilidades or {}
    rank = rank or {}
    comps = getattr(av, "conf_componentes", None) or {}
    amostra = float(comps.get("amostra_historica") or 0)
    return (
        -av.prob,
        -av.confianca,
        -amostra,
        -float(estabilidades.get(id(av), 0.0)),
        len(getattr(av, "riscos", None) or []),
        rank.get(id(av), len(rank)),
        _dist_equilibrio(av),
    )


def _fatores_secundarios(ordem: list[Any] | None) -> tuple[dict[int, float], dict[int, int]]:
    """Extrai os fatores SECUNDARIOS da ordem informada. Os itens podem
    ser avaliacoes cruas OU objetos ComparacaoMercado do comparador
    (atributos `.avaliacao` e `.estabilidade`) - neste ultimo caso a
    estabilidade (prioridade 4) e a posicao no ranking de score
    (desempate FINAL) fluem para a chave. A ordem NUNCA comanda a
    escolha: probabilidade continua no topo."""
    estabilidades: dict[int, float] = {}
    rank: dict[int, int] = {}
    for pos, item in enumerate(ordem or []):
        av = getattr(item, "avaliacao", None) or item
        if av is None:
            continue
        rank[id(av)] = pos
        est = getattr(item, "estabilidade", None)
        if est is not None:
            estabilidades[id(av)] = float(est)
    return estabilidades, rank


@dataclass
class SelecaoOperacional:
    """Resultado da politica operacional para UM jogo."""

    maior_prob: Any | None = None          # melhor previsao estatistica
    util_maior_prob: UtilidadeLinha | None = None
    operacional: Any | None = None         # melhor aposta pratica
    util_operacional: UtilidadeLinha | None = None
    motivo_operacional: str | None = None
    nenhuma_operacional: bool = True
    pool: list[Any] = field(default_factory=list)  # linhas disciplinaas


def _motivo(
    av: Any,
    util: UtilidadeLinha,
    maior: Any | None,
    util_maior: UtilidadeLinha | None,
    n_candidatas: int = 0,
    com_score: bool = False,
) -> str:
    partes: list[str] = []
    if util.odd_justa is None:
        partes.append(
            f"odd justa {ODD_JUSTA_NAO_CALCULAVEL} (linha com "
            "devolucao sem decomposicao da liquidacao)"
        )
    elif util.odd_real:
        partes.append(
            f"odd real {util.odd_real:.2f} >= odd minima aceitavel "
            f"{util.odd_minima:.2f} (justa {util.odd_justa:.2f} + "
            f"margem operacional {MARGEM_OPERACIONAL:.0%})"
        )
    else:
        partes.append(
            f"odd justa de equilibrio {util.odd_justa:.2f} + margem "
            f"operacional {MARGEM_OPERACIONAL:.0%} => odd minima "
            f"aceitavel {util.odd_minima:.2f} >= piso de utilidade "
            f"{ODD_MIN_OPERACIONAL:.2f} (odd real: {ODD_REAL_INDISPONIVEL})"
        )
    partes.append(f"situacao: {util.situacao}")
    partes.append(
        f"prob {av.prob:.2%} com margem de seguranca "
        f"{av.prob - PROB_MIN_APROVAR:+.1%} sobre o piso de aprovacao"
    )
    partes.append(f"confianca {av.confianca:.2f}")
    if com_score:
        partes.append(
            f"maior probabilidade entre as {n_candidatas} linha(s) "
            "utilizavel(is) - ordem obrigatoria: probabilidade, "
            "confianca, qualidade da amostra, estabilidade, seguranca "
            "(ranking de score usado apenas nos desempates)"
        )
    else:
        partes.append(
            f"maior probabilidade entre as {n_candidatas} linha(s) "
            "utilizavel(is) - ordem obrigatoria: probabilidade, "
            "confianca, qualidade da amostra, estabilidade, seguranca"
        )
    if (
        maior is not None
        and util_maior is not None
        and av is not maior
        and not util_maior.utilizavel
    ):
        oj_maior = (
            f"{util_maior.odd_justa:.2f}"
            if util_maior.odd_justa is not None
            else ODD_JUSTA_NAO_CALCULAVEL
        )
        partes.append(
            f"linha de maior probabilidade ({maior.linha}, "
            f"{maior.prob:.2%}) marcada {MARCA_BAIXA_UTILIDADE} "
            f"(odd justa {oj_maior}): nao usada como "
            "principal"
        )
    return "; ".join(partes)


def selecionar_linha_operacional(
    avaliacoes: list[Any],
    odds_reais: dict[str, float] | None = None,
    ordem: list[Any] | None = None,
) -> SelecaoOperacional:
    """Separa a melhor previsao estatistica da melhor aposta pratica.

    `odds_reais` mapeia linha -> odd real da fonte (camada validada
    src/odds.py). Ausente/vazio => apenas odd justa informativa; nada e
    inventado. Nenhuma transformacao nas avaliacoes: elas entram e
    saem exatamente como estao.

    `ordem` (opcional) fornece SOMENTE os fatores secundarios da regra
    (v2): a estabilidade (prioridade 4) e a posicao no ranking de
    score de politica (src/policy.py comparar_mercados), consultada
    apenas nos desempates. Itens podem ser avaliacoes cruas ou objetos
    ComparacaoMercado (`.avaliacao`/`.estabilidade`). O score NUNCA
    comanda a escolha: entre as utilizaveis vence a de MAIOR
    probabilidade (desempates: confianca, qualidade da amostra,
    estabilidade, contradicoes, score, proximidade do equilibrio). A
    seguranca nunca e rebaixada artificialmente: TODAS as candidatas ja
    passaram na MESMA disciplina do motor e no piso operacional de odd.
    """
    odds = odds_reais or {}
    sel = SelecaoOperacional(pool=_disciplinadas(avaliacoes))
    if not sel.pool:
        return sel

    estabilidades, rank = _fatores_secundarios(ordem)
    com_score = bool(rank)

    chave = lambda av: _chave_ordenacao(av, estabilidades, rank)  # noqa: E731

    ordenadas = sorted(sel.pool, key=chave)
    sel.maior_prob = ordenadas[0]
    sel.util_maior_prob = avaliar_utilidade(
        sel.maior_prob.prob, odds.get(sel.maior_prob.linha),
        av=sel.maior_prob,
    )

    candidatas: list[tuple[Any, UtilidadeLinha]] = []
    for av in sorted(sel.pool, key=chave):
        util = avaliar_utilidade(av.prob, odds.get(av.linha), av=av)
        if util.utilizavel:
            candidatas.append((av, util))
    if not candidatas:
        sel.nenhuma_operacional = True
        return sel

    sel.operacional, sel.util_operacional = candidatas[0]
    sel.nenhuma_operacional = False
    sel.motivo_operacional = _motivo(
        sel.operacional, sel.util_operacional,
        sel.maior_prob, sel.util_maior_prob,
        n_candidatas=len(candidatas),
        com_score=com_score,
    )
    return sel


# ----------------------------------------------------------------------
# Apresentacao (o relatorio mostra as duas: previsao e aposta pratica)
# ----------------------------------------------------------------------
def format_detalhe_odd(av: Any, util: UtilidadeLinha) -> list[str]:
    """APRESENTACAO OBRIGATORIA da linha principal (Bloco A 09/09/2026):
    mercado, probabilidade estimada, probabilidade de devolucao, odd
    justa, margem operacional, odd minima aceitavel, piso de utilidade,
    odd real, edge, valor esperado e situacao. Edge e valor esperado SO
    com odd real - nunca inventados. Odd justa ausente => ODD JUSTA
    NAO CALCULAVEL - DADOS INSUFICIENTES."""
    oj = (
        f"{util.odd_justa:.2f}"
        if util.odd_justa is not None else ODD_JUSTA_NAO_CALCULAVEL
    )
    om = (
        f"{util.odd_minima:.2f}"
        if util.odd_minima is not None else ODD_JUSTA_NAO_CALCULAVEL
    )
    odd_real = (
        f"{util.odd_real:.2f}" if util.odd_real else ODD_REAL_INDISPONIVEL
    )
    if util.odd_real and util.odd_justa is not None:
        edge = f"{util.edge:+.2%} (odd real sobre a odd justa)"
        ev = f"{util.ev:+.4f} por unidade de stake"
    else:
        edge = "nao calculado (sem odd real)"
        ev = "nao calculado (sem odd real)"
    return [
        "    - DETALHE DA LINHA PRINCIPAL (odd justa com devolucao,"
        " margem operacional e situacao):",
        f"        MERCADO: {av.linha}",
        f"        PROBABILIDADE ESTIMADA: {av.prob:.2%}",
        f"        PROBABILIDADE DE DEVOLUCAO: {util.prob_devolucao:.2%}",
        f"        ODD JUSTA: {oj}",
        f"        MARGEM OPERACIONAL: {MARGEM_OPERACIONAL:.0%}",
        f"        ODD MINIMA ACEITAVEL: {om}",
        f"        PISO DE UTILIDADE: {ODD_MIN_OPERACIONAL:.2f}",
        f"        ODD REAL: {odd_real}",
        f"        EDGE: {edge}",
        f"        VALOR ESPERADO: {ev}",
        f"        SITUACAO: {util.situacao}",
    ]


def format_bloco_operacional(selecao: SelecaoOperacional) -> list[str]:
    """Bloco [POLITICA-OPERACIONAL] do relatorio de UM jogo.

    Somente FATOS do motor + o rotulo operacional. Nenhuma odd real e
    afirmada quando a fonte nao forneceu.
    """
    out: list[str] = []
    if selecao.maior_prob is None:
        return out  # nada disciplinado: o relatorio ja tratou acima

    maior, util = selecao.maior_prob, selecao.util_maior_prob
    oj_maior = (
        f"{util.odd_justa:.2f}"
        if util.odd_justa is not None else ODD_JUSTA_NAO_CALCULAVEL
    )
    marca = (
        f" | {util.marca} (odd justa {oj_maior} < "
        f"{ODD_MIN_OPERACIONAL:.2f})" if util.marca else ""
    )
    out.append(
        "[POLITICA-OPERACIONAL] melhor previsao estatistica x melhor "
        "aposta pratica:"
    )
    out.append(
        f"  - LINHA DE MAIOR PROBABILIDADE: {maior.linha} | prob "
        f"{maior.prob:.2%} | confianca {maior.confianca:.2f} | odd justa "
        f"{oj_maior}{marca}"
    )
    if selecao.operacional is None:
        out.append(f"  - {NENHUMA_UTIL_MSG}")
        return out

    op, uo = selecao.operacional, selecao.util_operacional
    odd_real = (
        f"{uo.odd_real:.2f}" if uo.odd_real else ODD_REAL_INDISPONIVEL
    )
    oj_op = (
        f"{uo.odd_justa:.2f}"
        if uo.odd_justa is not None else ODD_JUSTA_NAO_CALCULAVEL
    )
    om_op = (
        f"{uo.odd_minima:.2f}"
        if uo.odd_minima is not None else ODD_JUSTA_NAO_CALCULAVEL
    )
    out.append(
        f"  - MELHOR LINHA OPERACIONAL (aposta pratica): {op.linha} | "
        f"prob {op.prob:.2%} | confianca {op.confianca:.2f} | odd real "
        f"{odd_real} | odd justa {oj_op} | odd minima aceitavel {om_op} "
        f"| motivo: {selecao.motivo_operacional}"
    )
    out.extend(format_detalhe_odd(op, uo))
    return out