"""FAMILIA CARTOES - BLOCO PRE-JOGO: calculo + benchmark da competicao.

Bloco seguinte ao da LIQUIDACAO validada (src/settlement.py, aprovada
pelo operador): avalia linhas de TOTAL de cartoes do jogo ANTES do jogo
comecar. NENHUMA liberacao de recomendacao nesta etapa - nada aqui e
ligado ao comparador de politica, ao registro ou a qualquer comando; e
o bloco de calculo pronto para integracao posterior.

CONVENCAO DE CONTAGEM (a MESMA validada na liquidacao): total em
PONTOS com amarelo=1, vermelho=2, DECLARADA na propria linha - nunca
peso inventado, nunca amarelo tratado igual a vermelho.

Regras inviolaveis do bloco:
    - amarelos OU vermelhos ausentes em um jogo do historico => o jogo
      e EXCLUIDO da amostra e contabilizado (nunca zero, nunca inferido);
    - sem sustentacao (nem baseline dos times, nem benchmark da liga) =>
      a familia NAO e avaliada - lambda nunca e inventado;
    - estatistica de ARBITRO nao existe na fonte (a API-Football v3 nao
      fornece medias de arbitro): declarado como risco, nunca inventado;
    - benchmark = TODAS as partidas finalizadas da competicao na
      temporada identificada (mesma regra de integridade de
      league_corner_average - nunca amostra de N jogos, nunca media de
      um unico time), com quantidade de partidas validas informada;
    - probabilidade = Poisson sobre os 90 minutos completos (as MESMAS
      funcoes validadas do motor);
    - confianca = a MESMA _confianca_prejogo validada (amostra efetiva
      COM dado de cartoes, benchmark da competicao, h2h com peso menor);
    - linhas canonicas liquidadaveis: o MESMO formato de linha que o
      settlement aprovado sabe liquidar (convencao declarada na linha).
"""

from __future__ import annotations

import math
from typing import Any

from src.config import DEFAULT_TIMEZONE
from src.exceptions import DataUnavailableError, UserFacingError
from src.settlement import _MARCA_CONVENCAO_CARTOES

# Unidade canonica da familia: a convencao DECLARADA na linha e a mesma
# que a liquidacao aprovada exige (marcador presente => liquidadavel).
UNIDADE_CARTOES = f"cartoes ({_MARCA_CONVENCAO_CARTOES})"

# Limite do leque de amostra do historico (mesmo teto de confrontos H2H)
LAST_N_MAX = 50


# ----------------------------------------------------------------------
# Amostra: pontos de cartoes por jogo da perspectiva de um time
# ----------------------------------------------------------------------
def _pontos_do_jogo(g: Any) -> tuple[int, int] | None:
    """(pontos recebidos pelo time, pontos recebidos pelo adversario)
    pela convencao validada - ou None quando qualquer componente falta
    na fonte (amarelos OU vermelhos de qualquer lado: jogo excluido,
    nunca zerado)."""
    if any(
        getattr(g, campo, None) is None
        for campo in ("yellow_for", "red_for", "yellow_against",
                      "red_against")
    ):
        return None
    return (
        g.yellow_for + 2 * g.red_for,
        g.yellow_against + 2 * g.red_against,
    )


def _media(valores: list[float]) -> float | None:
    return sum(valores) / len(valores) if valores else None


def _taxa_cruzada_cartoes(
    games_side: list[Any], lado: str, last_n: int | None = None
) -> dict[str, Any]:
    """Medias de pontos de cartoes por jogo no MUNDO casa/fora (lado do
    fixture); amostra geral apenas quando o lado tem menos de 4 jogos
    COM dado de cartoes (mesma regra do motor validado). Jogos sem dado
    sao EXCLUIDOS e contabilizados - nunca viram zero.

    `last_n` limita a amostra aos ultimos N jogos do lado (1..50):
    10/20/ate 50 jogos, quando disponiveis na fonte.
    """
    if last_n is not None:
        last_n = max(1, min(last_n, LAST_N_MAX))
        games_side = sorted(
            games_side, key=lambda g: getattr(g, "date", ""), reverse=True
        )[:last_n]

    no_lado = [
        (g, _pontos_do_jogo(g))
        for g in games_side
        if (lado == "casa") == g.played_at_home
    ]
    gerais = [(g, _pontos_do_jogo(g)) for g in games_side]

    validos_no_lado = [p for _, p in no_lado if p is not None]
    amostra = validos_no_lado if len(validos_no_lado) >= 4 else [
        p for _, p in gerais if p is not None
    ]

    return {
        "n_no_mando": len(validos_no_lado),
        "n_usados": len(amostra),
        "n_sem_dado": sum(
            1 for _, p in (no_lado if len(validos_no_lado) >= 4 else gerais)
            if p is None
        ),
        "cartoes_pro": _media([float(p[0]) for p in amostra]),
        "cartoes_contra": _media([float(p[1]) for p in amostra]),
    }


# ----------------------------------------------------------------------
# Baseline pre-jogo: cruzamento casa/fora dos dois times
# ----------------------------------------------------------------------
def baseline_cartoes(
    hist: dict[str, Any], last_n: int | None = None
) -> dict[str, Any] | None:
    """Baseline pre-jogo do TOTAL de cartoes em pontos (cruzamento
    casa/fora - mesmo padrao validado do _baseline_escanteios).

    Esperado do lado do mandante = (cartoes recebidos pelo mandante em
    seus jogos no mando + cartoes recebidos pelos adversarios do
    visitante em seus jogos fora) / 2; espelhado para o visitante.

    Retorna None quando falta sustentacao (sem medias de cartoes no
    historico) - nunca se inventa baseline.
    """
    th = _taxa_cruzada_cartoes(hist.get("games_home") or [], "casa", last_n)
    ta = _taxa_cruzada_cartoes(hist.get("games_away") or [], "fora", last_n)
    if th["cartoes_pro"] is None or ta["cartoes_pro"] is None:
        return None
    exp_home = (th["cartoes_pro"] + ta["cartoes_contra"]) / 2
    exp_away = (ta["cartoes_pro"] + th["cartoes_contra"]) / 2
    return {
        "total": exp_home + exp_away,
        "lado_mandante": exp_home,
        "lado_visitante": exp_away,
        "n_usados_mandante": th["n_usados"],
        "n_usados_visitante": ta["n_usados"],
        "n_sem_dado_mandante": th["n_sem_dado"],
        "n_sem_dado_visitante": ta["n_sem_dado"],
        "detalhe": (
            f"cruzamento casa/fora em pontos de cartoes (n mandante "
            f"{th['n_usados']}, n visitante {ta['n_usados']}): lado "
            f"mandante {round(exp_home, 2)} + lado visitante "
            f"{round(exp_away, 2)} (amarelo=1, vermelho=2)"
        ),
    }


# ----------------------------------------------------------------------
# Benchmark real: media de cartoes da competicao INTEIRA na temporada
# ----------------------------------------------------------------------
def league_cards_average(
    client: Any,
    league_id: int,
    season: int,
    league_name: str,
) -> dict[str, Any] | None:
    """Media de PONTOS de cartoes da LIGA sobre a competicao INTEIRA.

    MESMA regra de integridade de league_corner_average (validada):
      1. competicao identificada (league_id);
      2. temporada identificada (season);
      3. TODAS as partidas finalizadas da competicao na temporada -
         nunca amostra dos ultimos N jogos, nunca media de um unico time;
      4. partida sem amarelos OU vermelhos de qualquer equipe na fonte e
         EXCLUIDA e contabilizada - dado ausente nunca vira zero;
      5. a resposta informa validas / encerradas / excluidas.

    Total da partida pela convencao validada: amarelos + 2*vermelhos
    (a MESMA da liquidacao). Retorna None quando nenhuma partida valida
    tem cartoes completos na fonte.
    """
    from src.fixtures import parse_fixture
    from src.match_stats import fetch_match_stats
    from src.stats import describe, over_frequency

    fixtures = client.get(
        "/fixtures",
        params={
            "league": league_id,
            "season": season,
            "timezone": DEFAULT_TIMEZONE,
        },
    )
    finished = [parse_fixture(raw) for raw in fixtures]
    finished = [f for f in finished if f.is_finished]

    totals: list[float] = []
    sem_cartoes = 0
    for fixture in finished:
        try:
            match = fetch_match_stats(client, fixture)
        except DataUnavailableError:
            sem_cartoes += 1
            continue
        valores = (
            match.home.yellow_cards, match.home.red_cards,
            match.away.yellow_cards, match.away.red_cards,
        )
        if any(v is None for v in valores):
            # cartoes incompletos na fonte: excluida, nunca zerada
            sem_cartoes += 1
            continue
        totals.append(float(
            valores[0] + valores[2] + 2 * (valores[1] + valores[3])
        ))

    if not totals:
        return None

    media = sum(totals) / len(totals)
    eq = math.floor(media) + 0.5
    over = over_frequency(totals, lines=(eq - 1.0, eq, eq + 1.0))

    return {
        "liga_id": league_id,
        "liga": league_name,
        "temporada": season,
        "partidas_encerradas": len(finished),
        "partidas_validas": len(totals),
        "partidas_sem_cartoes": sem_cartoes,
        "describe": describe(totals),
        "over": over,
        "convencao": _MARCA_CONVENCAO_CARTOES,
    }


def league_cards_average_for_fixture(
    client: Any, fixture: Any
) -> dict[str, Any] | None:
    """Benchmark de cartoes da liga de um fixture (competicao INTEIRA
    na temporada do fixture). Falha de consulta => None, nunca inventa."""
    if getattr(fixture, "league_id", None) is None or getattr(
        fixture, "season", None
    ) is None:
        return None
    try:
        return league_cards_average(
            client,
            league_id=fixture.league_id,
            season=fixture.season,
            league_name=fixture.league_name,
        )
    except (DataUnavailableError, UserFacingError):
        return None


# ----------------------------------------------------------------------
# Avaliacao pre-jogo de linhas Over/Under de total de cartoes
# ----------------------------------------------------------------------
def avaliar_cartoes_prejogo(
    hist: dict[str, Any],
    benchmark_cartoes: dict[str, Any] | None = None,
    last_n: int | None = None,
    h2h_n: int = 0,
) -> list[Any]:
    """Linhas over/under de TOTAL de cartoes ANTES do jogo comecar.

    Mesmo padrao validado do avaliar_pregame: baseline dos times (peso
    maior) combinado com o benchmark da liga (peso menor, 65/35);
    sem NENHUM dos dois => [] (nunca se inventa lambda). Probabilidade
    Poisson sobre os 90 minutos completos; leque de linhas X.5 ao redor
    da projecao (_linhas_pregame validada).

    NAO libera recomendacao: esta funcao apenas CALCULA. Nada e
    aprovado, registrado ou comparado nesta etapa.
    """
    from src.live_opportunity import poisson_ge, poisson_le
    from src.prejogo_opportunity import (
        AvaliacaoPre,
        _confianca_prejogo,
        _linhas_pregame,
    )

    base = baseline_cartoes(hist, last_n)
    bench_media = (benchmark_cartoes or {}).get("describe", {}).get("media")
    bench_validas = (benchmark_cartoes or {}).get("partidas_validas")

    if base is None and bench_media is None:
        return []

    if base is not None and bench_media is not None:
        lam = 0.65 * base["total"] + 0.35 * bench_media
        det_base = (
            f"{base['detalhe']}; benchmark da liga "
            f"{round(bench_media, 2)} em pontos de cartoes "
            "(65% times + 35% liga)"
        )
    elif base is not None:
        lam, det_base = base["total"], base["detalhe"]
    else:
        lam, det_base = (
            float(bench_media),
            "apenas benchmark da liga (sem medias de cartoes dos times)",
        )

    # amostra efetiva: jogos COM dado de cartoes por lado (nunca o
    # total bruto - jogo sem dado e excluido, nao contado como valido)
    if base is not None:
        n_min = min(base["n_usados_mandante"], base["n_usados_visitante"])
        sem_dado = (
            base["n_sem_dado_mandante"] + base["n_sem_dado_visitante"]
        )
    else:
        n_min, sem_dado = 0, 0

    conf, comps = _confianca_prejogo(n_min, bench_validas, h2h_n)

    riscos = [
        "estatistica de arbitro nao fornecida pela fonte (API-Football "
        "v3); nunca inventada",
        "convencao de contagem da casa nao informada pela fonte; "
        "convencao declarada: amarelo=1, vermelho=2",
    ]
    if base is None:
        riscos.append(
            "sem medias de cartoes dos times no historico: apenas "
            "benchmark da liga"
        )
    if bench_media is None:
        riscos.append(
            "sem benchmark de cartoes da liga: apenas historico dos times"
        )
    if sem_dado:
        riscos.append(
            f"{sem_dado} jogo(s) do historico sem dado de cartoes na "
            "fonte: excluidos da amostra (nunca zero)"
        )
    if n_min < 10:
        riscos.append(
            f"amostra historica pequena: {n_min} jogos com dado de "
            "cartoes (minimo por lado)"
        )

    sustentacao_comum = {
        "baseline_pre_jogo": det_base,
        "lambda_por90": round(lam, 2),
        "amostra_valida_mandante": (
            base["n_usados_mandante"] if base is not None else 0
        ),
        "amostra_valida_visitante": (
            base["n_usados_visitante"] if base is not None else 0
        ),
        "jogos_sem_dado_cartoes": sem_dado,
        "modelo": (
            "Poisson sobre 90 minutos completos "
            "(CALCULO, nao dado da API)"
        ),
    }

    out = []
    for line in _linhas_pregame(lam):
        for direcao in ("Over", "Under"):
            if direcao == "Over":
                prob = poisson_ge(int(math.floor(line)) + 1, lam)
            else:
                prob = poisson_le(int(math.floor(line)), lam)
            out.append(
                AvaliacaoPre(
                    mercado="cartoes",
                    linha=(
                        f"{direcao} {line} {UNIDADE_CARTOES} "
                        "(total do jogo)"
                    ),
                    prob=round(prob, 4),
                    confianca=conf,
                    conf_componentes=comps,
                    sustentacao=dict(sustentacao_comum),
                    riscos=list(riscos),
                )
            )
    return out