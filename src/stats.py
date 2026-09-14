"""Calculos estatisticos objetivos (escanteios e demais metricas).

Funcoes puras (sem I/O), testaveis isoladamente. Entrada tipica:
lista de TeamGameStats (perspectiva de um time) ou valores por
partida (para over/frequencia).

Calcula:
    - janelas de ultimos 5, 10 e 20 jogos
    - escanteios a favor, contra e total (media)
    - mediana, desvio-padrao, minimo, maximo
    - frequencia de Over 7.5 / 8.5 / 9.5 / 10.5 / 11.5 (total da partida)
    - desempenho casa / fora
    - tendencia (regressao linear simples sobre o total por jogo)
    - MOTOR GENERICO: painel objetivo por metrica (gols, escanteios,
      finalizacoes, no alvo, cartoes, posse, faltas, impedimentos),
      sempre calculado SOBRE jogos com valor real - dado ausente
      (None) e ignorado e contabilizado, jamais tratado como zero.
"""

from __future__ import annotations

import statistics
from typing import Any, Sequence

from src.config import OVER_LINES, STATS_WINDOWS
from src.match_stats import TeamGameStats


# ----------------------------------------------------------------------
def describe(values: Sequence[float]) -> dict[str, Any]:
    """Resumo estatistico de uma amostra de numeros."""
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "media": round(statistics.fmean(values), 2),
        "mediana": round(statistics.median(values), 2),
        "desvio": round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0,
        "minimo": min(values),
        "maximo": max(values),
    }


def over_frequency(
    values: Sequence[float], lines: Sequence[float] = OVER_LINES
) -> dict[float, dict[str, Any]]:
    """Frequencia de linhas de escanteios (ex.: total > 9.5) na amostra.

    'Over X.5' bate quando o total da partida e estritamente maior
    que X.5 - ex.: 10 escanteios batem Over 9.5, mas nao Under 9.5.
    """
    n = len(values)
    result: dict[float, dict[str, Any]] = {}
    for line in lines:
        hits = sum(1 for v in values if v > line)
        result[line] = {
            "hits": hits,
            "total": n,
            "pct": round(100 * hits / n, 1) if n else None,
        }
    return result


def linear_trend(values: Sequence[float]) -> dict[str, Any]:
    """Tendencia: inclinacao da regressao linear simples da serie.

    Classificacao:
        slope > 0.05  -> "crescente"
        slope < -0.05 -> "decrescente"
        senao         -> "estavel"
    """
    n = len(values)
    if n < 3:
        return {"slope": None, "classificacao": "amostra insuficiente (min. 3)"}
    xs = range(n)
    mean_x = (n - 1) / 2
    mean_y = statistics.fmean(values)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope = num / den if den else 0.0
    if slope > 0.05:
        label = "crescente"
    elif slope < -0.05:
        label = "decrescente"
    else:
        label = "estavel"
    return {"slope": round(slope, 3), "classificacao": label}


# ----------------------------------------------------------------------
def _windows(values: list[float], windows: Sequence[int] = STATS_WINDOWS) -> dict[int, list[float]]:
    """Sub-amostras mais recentes por janela (5, 10, 20...)."""
    return {w: values[-w:] for w in windows if w > 0}


def _games_to_totals(games: Sequence[TeamGameStats]) -> list[float]:
    return [float(g.corners_total) for g in games]


def _games_for(games: Sequence[TeamGameStats]) -> list[float]:
    return [float(g.corners_for) for g in games]


def _games_against(games: Sequence[TeamGameStats]) -> list[float]:
    return [float(g.corners_against) for g in games]


def compute_team_stats(
    games: Sequence[TeamGameStats],
    windows: Sequence[int] = STATS_WINDOWS,
    lines: Sequence[float] = OVER_LINES,
) -> dict[str, Any]:
    """Painel completo de estatisticas de escanteios de um time.

    Estrutura:
        geral: describe de a favor / contra / total
        janelas: {5: {...}, 10: {...}, 20: {...}} (media, over, tendencia)
        casa / fora: describe + over
        over_geral: frequencia das linhas na amostra completa
    """
    by_window = _windows(_games_to_totals(games), windows)

    def window_summary(totals: list[float]) -> dict[str, Any]:
        return {
            "describe": describe(totals),
            "over": over_frequency(totals, lines),
            "tendencia": linear_trend(totals),
        }

    home = [g for g in games if g.played_at_home]
    away = [g for g in games if not g.played_at_home]

    return {
        "n_jogos": len(games),
        "favor": describe(_games_for(games)),
        "contra": describe(_games_against(games)),
        "total": describe(_games_to_totals(games)),
        "janelas": {w: window_summary(t) for w, t in by_window.items()},
        "casa": {
            "n_jogos": len(home),
            "favor": describe(_games_for(home)),
            "contra": describe(_games_against(home)),
            "total": describe(_games_to_totals(home)),
            "over": over_frequency(_games_to_totals(home), lines),
        },
        "fora": {
            "n_jogos": len(away),
            "favor": describe(_games_for(away)),
            "contra": describe(_games_against(away)),
            "total": describe(_games_to_totals(away)),
            "over": over_frequency(_games_to_totals(away), lines),
        },
        "over_geral": over_frequency(_games_to_totals(games), lines),
        "tendencia_geral": linear_trend(_games_to_totals(games)),
        # Por tempo: True quando ao menos um jogo tem escanteios de 1oT/2oT
        # vindos da API (half=true); caso contrario False - nunca inventado.
        "tempos_disponiveis": any(
            g.corners_for_1st_half is not None for g in games
        ),
    }


# ----------------------------------------------------------------------
# Motor generico de calculos objetivos por metrica
# ----------------------------------------------------------------------
# Cada metrica mapeia o atributo de TeamGameStats "do time" (favor) e o
# "do adversario" (contra); "somavel" indica se o TOTAL da partida e uma
# metrica aplicavel (posse e sempre 100% por jogo - total nao se aplica).
# Valores vem exclusivamente da resposta da API da partida; jogos sem o
# dado (None) ficam fora do calculo e sao contabilizados em
# jogos_sem_dado - NUNCA tratados como zero.
METRIC_FIELDS: dict[str, dict[str, Any]] = {
    "gols": {"favor": "goals_for", "contra": "goals_against", "somavel": True},
    "escanteios": {"favor": "corners_for", "contra": "corners_against", "somavel": True},
    "finalizacoes": {"favor": "shots_for", "contra": "shots_against", "somavel": True},
    "finalizacoes_no_alvo": {
        "favor": "shots_on_goal_for",
        "contra": "shots_on_goal_against",
        "somavel": True,
    },
    "cartoes_amarelos": {"favor": "yellow_for", "contra": "yellow_against", "somavel": True},
    "cartoes_vermelhos": {"favor": "red_for", "contra": "red_against", "somavel": True},
    "posse": {"favor": "possession_for", "contra": "possession_against", "somavel": False},
    "faltas": {"favor": "fouls_for", "contra": "fouls_against", "somavel": True},
    "impedimentos": {"favor": "offsides_for", "contra": "offsides_against", "somavel": True},
}


def _report_from(
    games: Sequence[TeamGameStats],
    getter: Any,
) -> dict[str, Any]:
    """Resumo de uma serie por jogo: geral + casa/fora, so com valores reais.

    getter(g) devolve o valor da metrica naquele jogo (ou None quando a
    resposta da API nao forneceu o campo para a partida).
    """
    todos: list[float] = []
    casa: list[float] = []
    fora: list[float] = []
    for g in games:
        value = getter(g)
        if value is None:
            continue
        value = float(value)
        todos.append(value)
        (casa if g.played_at_home else fora).append(value)
    return {
        "jogos_com_dado": len(todos),
        "jogos_sem_dado": len(games) - len(todos),
        "geral": describe(todos),
        "casa": describe(casa),
        "fora": describe(fora),
    }


def metric_report(
    games: Sequence[TeamGameStats], field: str
) -> dict[str, Any]:
    """Resumo objetivo de UM campo de TeamGameStats (por atributo)."""
    return _report_from(games, lambda g: getattr(g, field, None))


def metrics_panel(games: Sequence[TeamGameStats]) -> dict[str, Any]:
    """Painel objetivo de TODAS as metricas disponiveis.

    Para cada metrica: do time (favor), do adversario (contra) e total da
    partida (calculado por jogo apenas quando AMBOS os valores existem).
    """
    panel: dict[str, Any] = {}
    for name, fields in METRIC_FIELDS.items():
        fav_field, con_field = fields["favor"], fields["contra"]

        def total_getter(
            g: TeamGameStats, ff: str = fav_field, cf: str = con_field
        ) -> float | None:
            fv = getattr(g, ff, None)
            av = getattr(g, cf, None)
            if fv is None or av is None:
                return None
            return float(fv) + float(av)

        panel[name] = {
            "do_time": metric_report(games, fav_field),
            "do_adversario": metric_report(games, con_field),
            # total da partida: so quando a soma e uma metrica aplicavel
            "total": (
                _report_from(games, total_getter)
                if fields.get("somavel", True)
                else None
            ),
        }
    return panel


def compute_h2h_stats(result: Any) -> dict[str, Any]:
    """Resumo de escanteios dos confrontos diretos (aceita H2HResult).

    Totais por confronto e frequencia de over na amostra H2H.
    """
    from src.h2h import H2HResult  # import tardio para evitar ciclo

    if isinstance(result, H2HResult):
        matches = result.matches
    else:  # sequencia generica com atributo corners_total
        matches = result

    totals = [float(m.corners_total) for m in matches if m.corners_total is not None]
    stats: dict[str, Any] = {
        "n_confrontos_com_dados": len(totals),
        "describe": describe(totals),
        "over": over_frequency(totals),
        "tendencia": linear_trend(totals),
    }
    return stats


def xcorners_heuristic(
    team_a_stats: dict[str, Any], team_b_stats: dict[str, Any]
) -> dict[str, Any]:
    """xCorners v1 - HEURISTICA (nao e dado oficial da API).

    Estimativa simples: xCorners do jogo = media entre a media de
    escanteios totais do time A e a do time B, ajustada pela soma
    ponderada de a favor/contra cruzados:

        x_for_A = (media_for_A + media_contra_B) / 2
        x_for_B = (media_for_B + media_contra_A) / 2
        x_total = x_for_A + x_for_B

    Estrutura preparada para substituir por modelo estatistico/ML.
    """
    favor_a = team_a_stats["favor"].get("media")
    against_a = team_a_stats["contra"].get("media")
    favor_b = team_b_stats["favor"].get("media")
    against_b = team_b_stats["contra"].get("media")

    if None in (favor_a, against_a, favor_b, against_b):
        return {"disponivel": False}

    x_a = (favor_a + against_b) / 2
    x_b = (favor_b + against_a) / 2
    return {
        "disponivel": True,
        "metodo": "heuristica v1 (medias cruzadas) - estimativa, nao dado oficial",
        "x_escanteios_A": round(x_a, 2),
        "x_escanteios_B": round(x_b, 2),
        "x_total_estimado": round(x_a + x_b, 2),
    }