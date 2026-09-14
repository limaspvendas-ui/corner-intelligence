"""POLITICA PERMANENTE DE UNIVERSO E SELECAO DE MERCADOS (v1).

Regra do operador (07/09/2026): concentrar tempo, chamadas da API,
credito e calibracao SOMENTE em jogos que poderiam ser usados para
aposta real. Vale para TESTE/CALIBRACAO e APOSTA REAL, ao vivo e
pre-jogo. NAO altera nenhum calculo validado: probabilidades,
confiancas, benchmarks, identidade, registro imutavel e liquidacao
continham exatamente como estao - este modulo so decide QUAIS jogos
podem ser analisados e QUAL linha (ja validada) e a melhor do jogo.

Duas partes:

1. UNIVERSO UNICO DE ANALISE
   Somente competicoes fortes, conhecidas e com boa cobertura
   (LIGAS_PRIORITARIAS, IDs resolvidos NA API-Football - nunca de
   memoria). Outras competicoes so entram por promocao EXPLICITA em
   LIGAS_EXTENSAO (profissional + reconhecida + cobertura equivalente
   + DADOS=SIM). Exclusoes de nome (reservas, U20/21/23, feminino,
   amador, desenvolvimento, subdivisoes fracas) valem mesmo dentro de
   uma liga prioritaria. DADOS != SIM => inelegivel.

2. COMPARACAO DE MERCADOS E SELECAO DA MELHOR OPORTUNIDADE
   Para cada jogo elegivel sao avaliados TODOS os mercados com logica
   validada (hoje: gols e escanteios over/under totais, TOTAL de cartoes
   over/under e a familia resultado 1X2/Dupla Chance/DNB/AH; o leque de
   familias e extensivel). A disciplina de aprovacao e a MESMA do motor validado
   (janela de probabilidade + confianca minima, constantes
   importadas). A diferenca e a COMPARACAO: em vez de ordenar por
   probabilidade (que favoreceria linhas artificialmente altas), cada
   linha disciplinada recebe um SCORE DE POLITICA que compara
   probabilidade, confianca, utilidade pratica (linhas excessivamente
   largas/conservadoras sao PENALIZADAS - odd justa ~1/prob),
   estabilidade historica, aderencia times x benchmark da competicao,
   cobertura dos dados e contradicoes registradas. O score e uma
   HEURISTICA DE POLITICA claramente rotulada - nunca um calculo
   validado do motor. Padrao: UMA melhor oportunidade por jogo; sem
   evidencia forte => jogo REPROVADO.

   v2 (08/09/2026 - POLITICA OPERACIONAL): a linha PRINCIPAL escolhida
   por selecionar_melhor e a melhor linha OPERACIONAL (piso de odd
   efetiva >= 1,15; detalhe e rotulagem em src/politica_operacional.py).
   Nenhum calculo/benchmark/probabilidade muda - so a escolha da linha
   principal e sua apresentacao; o ranking completo por score continua
   em SelecaoPolitica.comparadas.

   v3 (08/09/2026 - CORRECAO DA ORDEM DE SELECAO): entre as linhas com
   utilidade operacional a prioridade e OBRIGATORIA - maior
   probabilidade, confianca, qualidade da amostra, estabilidade,
   seguranca; o score de politica e consultado apenas nos desempates e
   nunca escolhe uma linha de probabilidade significativamente menor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.config import DEFAULT_LAST_N
from src.live_opportunity import CONF_MIN_TOP1, PROB_MAX_APROVAR, PROB_MIN_APROVAR

# Formatos de linha da familia RESULTADO: os MESMOS regexes da
# liquidacao validada (src/settlement.py) - o comparador so reconhece
# linhas que o registro sabe liquidar (GANHA/PERDIDA/DEVOLVIDA/
# MEIA VITORIA/MEIA DERROTA). Fonte unica de verdade dos formatos.
from src.settlement import (  # noqa: E402  (import local da familia)
    _RE_1X2 as _RE_1X2_RES,
    _RE_AH as _RE_AH_RES,
    _RE_DC as _RE_DC_RES,
    _RE_DNB as _RE_DNB_RES,
)

# ----------------------------------------------------------------------
# 1. UNIVERSO UNICO DE ANALISE
# ----------------------------------------------------------------------
# IDs resolvidos na API-Football (endpoint /leagues, 07/09/2026).
# Manter SEMPRE sincronizado com a regra do operador.
LIGAS_PRIORITARIAS: dict[int, str] = {
    39: "Premier League (England)",
    140: "La Liga (Spain)",
    135: "Serie A (Italy)",
    78: "Bundesliga (Germany)",
    61: "Ligue 1 (France)",
    2: "UEFA Champions League",
    3: "UEFA Europa League",
    848: "UEFA Europa Conference League",
    71: "Serie A (Brazil)",
    94: "Primeira Liga (Portugal)",
    88: "Eredivisie (Netherlands)",
    203: "Süper Lig (Turkey)",
    128: "Liga Profesional Argentina (Argentina)",
    262: "Liga MX (Mexico)",
    239: "Primera A (Colombia)",
    253: "Major League Soccer (USA)",
}

# Promocao explicita do operador apenas. Exigencias para promover:
# competicao profissional, reconhecida, cobertura equivalente e
# DADOS = SIM.
# 09/09/2026 (ETAPA 1 da auditoria de cobertura): o operador promoveu
# as competicoes CONMEBOL de elite e as divisoes nacionais BR com
# cobertura classe A confirmada na matriz (src/cobertura.py) e
# benchmark 2026 valido. Copa Argentina (130) e Copa Colombia (241)
# foram RETIRADAS do universo (classe E: zero estatisticas de partida
# - nao geram recomendacoes oficiais; consulta historica via
# 'liga'/'time' permanece, sem aprovacao de apostas).
LIGAS_EXTENSAO: dict[int, str] = {
    13: "CONMEBOL Libertadores (South America)",
    11: "CONMEBOL Sudamericana (South America)",
    72: "Serie B (Brazil)",
    # Copa do Brasil: cobertura PARCIAL na temporada 2026 (88/150 com
    # estatisticas - fases iniciais sem entrega na fonte). Analise
    # somente quando a VALIDACAO DINAMICA do fixture confirmar dados
    # suficientes no jogo e na fase (src/cobertura.py); ausencia nunca
    # vira zero.
    73: "Copa do Brasil (Brazil)",
}

# Exclusoes de NOME mesmo dentro de liga prioritaria: reservas,
# sub-20/21/23, desenvolvimento, feminino, amador.
_RE_EXCLUI_LIGA = re.compile(
    r"(?i)(\breserves?\b|\bu-?\s?1[89]\b|\bu-?\s?2[0123]\b|"
    r"\bsub-?\s?1[89]\b|\bsub-?\s?2[0123]\b|\bjong\b|\bii\b|"
    r"\bdevelopment\b|\bacadem\w*\b|\bamateur\w*\b|\bfeminin\w*\b|"
    r"\bwomen\b|\bfemenil\b|\bfrauen\b|\bnext pro\b|\brevela\w*\b)"
)
_RE_EXCLUI_TIME = re.compile(
    r"(?i)(\breserves?\b|\bu-?\s?1[89]\b|\bu-?\s?2[0123]\b|"
    r"\bsub-?\s?2[0123]\b|\bjong\b|\bii\b)"
)

PRIORITARIA = "PRIORITARIA"
EXTENSAO = "EXTENSAO"
EXCLUIDA = "EXCLUIDA"


def classificar_liga(league_id: int | None, league_name: str | None
                     ) -> tuple[str, str]:
    """Classifica a competicao no universo da politica.

    Retorna (classe, motivo). IDs e nome vem SEMPRE do fixture/da API.
    """
    if league_id is None or league_name is None:
        return EXCLUIDA, "competicao nao identificada no fixture"
    if league_id in LIGAS_PRIORITARIAS:
        if _RE_EXCLUI_LIGA.search(league_name):
            return EXCLUIDA, (
                f"'{league_name}' e variante excluida (reserva/sub/"
                "desenvolvimento/feminino) mesmo dentro de liga "
                "prioritaria"
            )
        return PRIORITARIA, LIGAS_PRIORITARIAS[league_id]
    if league_id in LIGAS_EXTENSAO:
        return EXTENSAO, LIGAS_EXTENSAO[league_id]
    return EXCLUIDA, (
        f"competicao {league_id} ('{league_name}') fora do universo "
        "forte da politica (prioritaria nem extensao promovida)"
    )


def jogo_elegivel(league_id: int | None, league_name: str | None,
                  home_name: str | None, away_name: str | None,
                  dados: str = "SIM") -> tuple[bool, str]:
    """Elegibilidade COMPLETA de um jogo sob a politica permanente.

    `dados` e o veredicto de cobertura do mapeamento (SIM/PARCIAL/
    INSUFICIENTE). Retorna (elegivel, motivo).
    """
    classe, motivo = classificar_liga(league_id, league_name)
    if classe == EXCLUIDA:
        return False, motivo
    if (dados or "").strip().upper() != "SIM":
        return False, f"cobertura de dados insuficiente (DADOS={dados})"
    for lado, nome in (("mandante", home_name), ("visitante", away_name)):
        if nome and _RE_EXCLUI_TIME.search(nome.strip()):
            return False, f"{lado} '{nome}' e time de reserva/base"
    return True, f"elegivel: {motivo} com DADOS=SIM"


# ----------------------------------------------------------------------
# 2. COMPARACAO DE MERCADOS
# ----------------------------------------------------------------------
_RE_LINHA = re.compile(
    r"^(Over|Under)\s+(\d+(?:[.,]\d+)?)\s+(escanteios|gols|cartoes)\b"
)

# Bandas de UTILIDADE PRATICA (heuristica de politica v1): a odd justa
# de uma linha e ~1/prob. Linhas de probabilidade muito alta tem odd
# trivial e pouca utilidade pratica - penalizadas, nunca banidas.
def _peso_utilidade_prob(prob: float) -> float:
    if prob <= 0.80:
        return 1.00
    if prob <= 0.85:
        return 0.90
    if prob <= 0.90:
        return 0.70
    if prob <= 0.93:
        return 0.45
    if prob <= 0.95:
        return 0.25
    return 0.05  # praticamente decidida: odd trivial


# Linha excessivamente LARGA (longe do equilibrio da projecao) tambem
# e conservadora demais - segunda penalizacao.
def _peso_utilidade_largura(dist_equilibrio: float) -> float:
    if dist_equilibrio <= 2.5:
        return 1.00
    if dist_equilibrio <= 3.5:
        return 0.70
    return 0.40


# Estabilidade historica: coeficiente de variacao dos totais do
# mercado no historico combinado dos dois times (so jogos COM dado na
# fonte; ausencia nunca vira zero). Escanteios (processo mais estavel)
# tem banda propria; gols, cartoes e as demais familias contam usam a
# banda generica - nenhuma banda nova e inventada aqui.
def _peso_estabilidade(cv: float | None, mercado: str) -> float:
    if cv is None:
        return 0.80  # sem amostra suficiente para medir estabilidade
    teto_esc, teto_med = (0.30, 0.40) if mercado == "escanteios" else (0.45, 0.55)
    if cv <= teto_esc:
        return 1.00
    if cv <= teto_med:
        return 0.80
    return 0.60


# Aderencia times x benchmark da competicao: quando as duas evidencias
# apontam na mesma direcao a projecao e mais confiavel.
def _peso_aderencia(gap_relativo: float | None) -> float:
    if gap_relativo is None:
        return 0.85  # sem benchmark: evidencia unica
    if gap_relativo <= 0.10:
        return 1.00
    if gap_relativo <= 0.25:
        return 0.85
    return 0.70


def _peso_cobertura(n_home: int, n_away: int) -> float:
    fracao = (n_home + n_away) / (2.0 * DEFAULT_LAST_N)
    if fracao >= 0.75:
        return 1.00
    if fracao >= 0.50:
        return 0.85
    return 0.70


def _peso_contradicoes(n_riscos: int) -> float:
    return max(1.00 - 0.10 * n_riscos, 0.70)


def _cv(totais: list[float]) -> float | None:
    if len(totais) < 3:
        return None
    media = sum(totais) / len(totais)
    if media <= 0:
        return None
    var = sum((x - media) ** 2 for x in totais) / (len(totais) - 1)
    return (var ** 0.5) / media


def _comparar_resultado(
    a: Any,
    totais_gols: list[float],
    benchmark_gols: dict[str, Any] | None,
    cobertura: float,
) -> ComparacaoMercado | None:
    """Comparacao de UMA linha da familia RESULTADO (bloco EXPERIMENTAL
    EM OBSERVACAO - aprovacao tecnica 07/09/2026 para entrar no
    comparador; aguarda validacao estatistica: 1X2, Dupla Chance, DNB, AH).

    Mesma HEURISTICA DE POLITICA do O/U (rotulada, nunca calculo
    validado), com os componentes adaptados ao mercado:

      - utilidade_prob: MESMAS bandas por probabilidade (linhas de
        probabilidade artificialmente alta seguem penalizadas);
      - utilidade_largura: no AH, distancia |linha| ate a linha de
        equilibrio 0.0 (linha larga = conservadora); 1X2/DC/DNB nao
        tem linha numerica: distancia 0, sem largura artificial;
      - estabilidade: CV dos TOTAIS DE GOLS do historico combinado
        (a familia e movida pelo processo de gols) com os limiares da
        familia gols;
      - aderencia: MESMO calculo do O/U de gols (baseline recuperado
        da mistura 65/35 x benchmark de gols da liga), usando o
        lambda TOTAL (mandante + visitante) do bloco resultado;
      - cobertura e contradicoes: identicos as demais familias.

    Retorna None quando a linha nao e da familia ou o bloco nao
    embutiu os lambdas (nada e inventado).
    """
    texto = (a.linha or "").strip()
    lam_h = a.sustentacao.get("lambda_mandante")
    lam_a = a.sustentacao.get("lambda_visitante")
    if lam_h is None or lam_a is None:
        return None
    lam_total = float(lam_h) + float(lam_a)

    m = _RE_AH_RES.match(texto)
    if m:
        tipo = f"ah {m.group(1)}"
        valor = float(m.group(2).replace(",", "."))
        dist = abs(valor)  # distancia da linha de equilibrio 0.0
    elif _RE_DNB_RES.match(texto):
        tipo, valor, dist = "dnb (ah 0.0)", 0.0, 0.0
    elif _RE_DC_RES.match(texto):
        tipo, valor, dist = "dupla chance", None, 0.0
    elif _RE_1X2_RES.match(texto):
        tipo, valor, dist = "1x2", None, 0.0
    else:
        return None  # formato nao liquidadavel: nunca compara

    cv = _cv(totais_gols)
    u_prob = _peso_utilidade_prob(a.prob)
    u_larg = _peso_utilidade_largura(dist)
    estab = _peso_estabilidade(cv, "gols")

    bench_media = (benchmark_gols or {}).get("describe", {}).get("media")
    if bench_media:
        baseline = (lam_total - 0.35 * bench_media) / 0.65
        gap = abs(baseline - bench_media) / bench_media
    else:
        baseline, gap = lam_total, None
    ader = _peso_aderencia(gap)
    contr = _peso_contradicoes(len(a.riscos or []))

    score = round(
        a.prob * a.confianca * u_prob * u_larg * estab * ader
        * cobertura * contr,
        4,
    )
    return ComparacaoMercado(
        avaliacao=a,
        score=score,
        utilidade_prob=u_prob,
        utilidade_largura=u_larg,
        estabilidade=estab,
        aderencia=ader,
        cobertura=cobertura,
        contracoes=contr,
        cv_amostral=round(cv, 3) if cv is not None else None,
        dist_equilibrio=round(dist, 2),
        detalhe={
            "direcao": tipo,
            "valor": valor,
            "mercado": "resultado",
            "lambda": round(lam_total, 2),
            "baseline_times": round(baseline, 2) if baseline else None,
            "benchmark_liga": bench_media,
            "gap_relativo": round(gap, 3) if gap is not None else None,
            "n_riscos": len(a.riscos or []),
        },
    )


@dataclass
class ComparacaoMercado:
    """Uma linha disciplinada comparada sob a politica."""

    avaliacao: Any            # AvaliacaoPre do motor validado
    score: float              # score de POLITICA (heuristica rotulada)
    utilidade_prob: float
    utilidade_largura: float
    estabilidade: float
    aderencia: float
    cobertura: float
    contracoes: float
    cv_amostral: float | None = None
    dist_equilibrio: float | None = None
    detalhe: dict[str, Any] = field(default_factory=dict)


def comparar_mercados(
    avaliacoes: list[Any],
    hist: dict[str, Any],
    benchmark_esc: dict[str, Any] | None,
    benchmark_gols: dict[str, Any] | None,
    benchmark_cartoes: dict[str, Any] | None = None,
) -> list[ComparacaoMercado]:
    """Compara TODAS as linhas disciplinadas de TODOS os mercados
    validados e devolve ranking por score de politica.

    A disciplina e a MESMA do motor validado (janela de probabilidade +
    confianca minima, constantes importadas - nada recalculado). O
    score de politica e heuristica rotulada, nao calculo validado.

    Cada familia usa o SEU benchmark (cartoes => `benchmark_cartoes`,
    nunca o de gols) e os SEUS totais amostrais do historico: totais
    de cartoes em PONTOS pela convencao validada (amarelo=1,
    vermelho=2), so jogos com o dado completo na fonte (nunca zero).
    """
    pool = [
        a for a in avaliacoes
        if PROB_MIN_APROVAR <= a.prob <= PROB_MAX_APROVAR
        and a.confianca >= CONF_MIN_TOP1
    ]

    # amostra: totais por mercado no historico combinado (com dado)
    games = list(hist.get("games_home") or []) + list(hist.get("games_away") or [])
    totais_por_mercado: dict[str, list[float]] = {
        "escanteios": [], "gols": [], "cartoes": [],
    }
    for g in games:
        if getattr(g, "corners_total", None) is not None:
            totais_por_mercado["escanteios"].append(float(g.corners_total))
        gf, ga = getattr(g, "goals_for", None), getattr(g, "goals_against", None)
        if gf is not None and ga is not None:
            totais_por_mercado["gols"].append(float(gf) + float(ga))
        # cartoes: TOTAL da partida em pontos pela convencao validada
        # (amarelo=1, vermelho=2); qualquer componente ausente => jogo
        # fora da amostra (excluido, nunca zero)
        yc_f, rc_f = getattr(g, "yellow_for", None), getattr(g, "red_for", None)
        yc_a, rc_a = (getattr(g, "yellow_against", None),
                      getattr(g, "red_against", None))
        if None not in (yc_f, rc_f, yc_a, rc_a):
            totais_por_mercado["cartoes"].append(
                float(yc_f) + float(yc_a) + 2.0 * (float(rc_f) + float(rc_a))
            )

    n_home = hist.get("n_home") or 0
    n_away = hist.get("n_away") or 0
    cobertura = _peso_cobertura(n_home, n_away)

    comparacoes: list[ComparacaoMercado] = []
    for a in pool:
        m = _RE_LINHA.match((a.linha or "").strip())
        if not m:
            # Familia RESULTADO (experimental em observacao: 1X2/DC/DNB/AH):
            # concorre em igualdade no MESMO score. Linha de outro formato
            # => fora. Nao e operacional validada - apenas comparada.
            comp_res = _comparar_resultado(
                a, totais_por_mercado.get("gols") or [], benchmark_gols,
                cobertura,
            )
            if comp_res is not None:
                comparacoes.append(comp_res)
            continue
        direcao, valor = m.group(1), float(m.group(2).replace(",", "."))
        mercado = a.mercado

        lam = float(a.sustentacao.get("lambda_por90") or 0) or None
        if lam is None:
            continue
        eq = float(int(lam)) + 0.5
        dist = abs(valor - eq)

        # baseline dos times recuperado da mistura validada 65/35 - cada
        # familia contra o SEU benchmark (cartoes => cartoes, nunca gols)
        bench = (
            benchmark_cartoes if mercado == "cartoes"
            else benchmark_esc if mercado == "escanteios"
            else benchmark_gols
        )
        bench_media = (bench or {}).get("describe", {}).get("media")
        if lam is not None and bench_media:
            baseline = (lam - 0.35 * bench_media) / 0.65
            gap = abs(baseline - bench_media) / bench_media
        else:
            baseline, gap = lam, None

        cv = _cv(totais_por_mercado.get(mercado) or [])

        u_prob = _peso_utilidade_prob(a.prob)
        u_larg = _peso_utilidade_largura(dist)
        estab = _peso_estabilidade(cv, mercado)
        ader = _peso_aderencia(gap)
        contr = _peso_contradicoes(len(a.riscos or []))

        score = round(
            a.prob * a.confianca * u_prob * u_larg * estab * ader
            * cobertura * contr,
            4,
        )
        comparacoes.append(
            ComparacaoMercado(
                avaliacao=a,
                score=score,
                utilidade_prob=u_prob,
                utilidade_largura=u_larg,
                estabilidade=estab,
                aderencia=ader,
                cobertura=cobertura,
                contracoes=contr,
                cv_amostral=round(cv, 3) if cv is not None else None,
                dist_equilibrio=round(dist, 2),
                detalhe={
                    "direcao": direcao,
                    "valor": valor,
                    "mercado": mercado,
                    "lambda": lam,
                    "baseline_times": round(baseline, 2) if baseline else None,
                    "benchmark_liga": bench_media,
                    "gap_relativo": round(gap, 3) if gap is not None else None,
                    "n_riscos": len(a.riscos or []),
                },
            )
        )

    comparacoes.sort(key=lambda c: -c.score)
    return comparacoes


@dataclass
class SelecaoPolitica:
    """Resultado da politica para UM jogo."""

    elegivel: bool
    motivo_inelegibilidade: str | None = None
    melhor: ComparacaoMercado | None = None
    reprovacao: str | None = None
    comparadas: list[ComparacaoMercado] = field(default_factory=list)
    # POLITICA OPERACIONAL (08/09/2026): detalhe da separacao entre
    # melhor previsao estatistica e melhor aposta pratica (src/
    # politica_operacional.py). Nenhum calculo muda - so a escolha da
    # linha PRINCIPAL e sua apresentacao.
    operacional: Any | None = None


def selecionar_melhor(
    avaliacoes: list[Any],
    hist: dict[str, Any],
    benchmark_esc: dict[str, Any] | None,
    benchmark_gols: dict[str, Any] | None,
    benchmark_cartoes: dict[str, Any] | None = None,
    odds_reais: dict[str, float] | None = None,
) -> SelecaoPolitica:
    """A MELHOR oportunidade do jogo (padrao: 1 por jogo).

    v2 (08/09/2026 - POLITICA OPERACIONAL, src/politica_operacional.py):
    a linha PRINCIPAL e a melhor linha OPERACIONAL - odd efetiva
    >= 1,15 (odd real quando a fonte fornece via `odds_reais`; senao
    apenas a odd justa informativa 1/prob, nunca inventada). Linhas
    extremamente largas continuam ESTATISTICAMENTE aprovadas (o ranking
    completo segue em `comparadas`), apenas marcadas "ALTA PROBABILIDADE,
    MAS BAIXA UTILIDADE OPERACIONAL" e nao usadas como principal.

    v3 (08/09/2026 - CORRECAO DA ORDEM): entre as utilizaveis a
    prioridade e OBRIGATORIA - 1. maior probabilidade; 2. confianca;
    3. qualidade da amostra; 4. estabilidade; 5. margem de seguranca
    (subsumida pela probabilidade); 6. demais fatores: contradicoes,
    score de politica e distancia ao equilibrio. O score NUNCA escolhe
    linha de probabilidade menor; e consultado apenas nos desempates. A
    seguranca nunca e rebaixada artificialmente para conseguir odd maior.

    Reprova quando: nenhuma linha dentro da disciplina; ou nenhuma linha
    com utilidade operacional aprovada (o piso de odd substitui a antiga
    regra de prob > 0.95, que esta contida nele).
    """
    comparadas = comparar_mercados(avaliacoes, hist, benchmark_esc,
                                   benchmark_gols, benchmark_cartoes)
    if not comparadas:
        return SelecaoPolitica(
            elegivel=True,
            reprovacao=(
                "nenhuma linha dentro da disciplina validada "
                f"(janela {PROB_MIN_APROVAR:.0%}-{PROB_MAX_APROVAR:.0%} "
                f"+ confianca >= {CONF_MIN_TOP1:.0%})"
            ),
            comparadas=comparadas,
        )
    from src.politica_operacional import (
        NENHUMA_UTIL_MSG,
        ODD_MIN_OPERACIONAL,
        odd_justa_avaliacao,
        selecionar_linha_operacional,
    )

    # A politica operacional roda sobre as MESMAS linhas que o
    # comparador disciplina. Desde a v3.2 a ordem entre as utilizaveis
    # e OBRIGATORIAMENTE: probabilidade > confianca > amostra >
    # estabilidade > seguranca (contradicoes; depois o score; e por
    # ultimo a distancia ao equilibrio - o score NUNCA ultrapassa a
    # probabilidade).
    sel_op = selecionar_linha_operacional(
        [c.avaliacao for c in comparadas],
        odds_reais=odds_reais,
        ordem=comparadas,
    )
    if sel_op.operacional is None:
        melhor_rest = comparadas[0].avaliacao
        oj_rest = odd_justa_avaliacao(melhor_rest)
        oj_txt = (
            f"{oj_rest:.2f}" if oj_rest is not None
            else "NAO CALCULAVEL - DADOS INSUFICIENTES"
        )
        return SelecaoPolitica(
            elegivel=True,
            reprovacao=(
                f"{NENHUMA_UTIL_MSG} (todas as {len(comparadas)} linhas "
                "disciplina tem utilidade pratica desprezivel: melhor "
                f"restante {melhor_rest.linha} prob "
                f"{melhor_rest.prob:.2%}, odd justa {oj_txt} abaixo do "
                f"piso de utilidade {ODD_MIN_OPERACIONAL:.2f}"
                " ou sem valor suficiente)"
            ),
            comparadas=comparadas,
            operacional=sel_op,
        )
    melhor = next(
        (c for c in comparadas if c.avaliacao is sel_op.operacional),
        comparadas[0],
    )
    sel = SelecaoPolitica(elegivel=True, melhor=melhor,
                          comparadas=comparadas, operacional=sel_op)
    return sel