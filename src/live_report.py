"""Relatorios da ETAPA 2 (jogos ao vivo): apenas [FATO] e [CALCULO].

Regra de saida da etapa: NENHUMA interpretacao, projecao ou
recomendacao de aposta. [CALCULO] traz somente operacoes objetivas
(totais, diferencas, ritmo medio ate o minuto atual - sempre rotulado
como calculo). [INTERPRETAÇÃO] fica para etapa futura, sob pedido
explicito do usuario.
"""

from __future__ import annotations

from typing import Any

from src.live import (
    LiveGame,
    LiveSnapshot,
    STAT_DISPLAY,
    _KNOWN_STAT_TYPES,
)

INDISPONIVEL = "dado nao disponivel na fonte"
SEM_TEMPO = "DADO POR TEMPO NAO DISPONIVEL NA API."


def _one(value: Any) -> str:
    """Valor unico: 'nd' quando a API nao fornece (nunca zero)."""
    return "nd" if value is None else str(value)


def _pair(home: Any, away: Any) -> str:
    return f"{_one(home)} - {_one(away)}"


def format_live_list(games: list[LiveGame]) -> str:
    """[FATO] Lista dos jogos realmente ao vivo agora."""
    out = ["[FATO]"]
    if not games:
        out.append(
            "Nenhum jogo ao vivo neste momento "
            "(consulta real a API-Football; nenhum fixture inventado)."
        )
        return "\n".join(out)
    out.append(f"JOGOS AO VIVO AGORA: {len(games)}")
    for g in games:
        out.append("")
        out.append(f"fixture {g.fixture_id} | {g.league_name} ({g.country})")
        out.append(
            f"  temporada {g.season if g.season is not None else 'nd'}"
            + (f" | {g.round}" if g.round else " | rodada nao informada")
        )
        out.append(
            f"  {g.home_team_name} {_one(g.goals_home)} x "
            f"{_one(g.goals_away)} {g.away_team_name}"
        )
        minuto = f"{g.elapsed}'" if g.elapsed is not None else "minuto nd"
        hora = g.date_local[11:16] if len(g.date_local) >= 16 else g.date_local
        out.append(f"  status {g.status} | minuto {minuto} | inicio {hora} (America/Sao_Paulo)")
    return "\n".join(out)


def format_stat_table(
    snapshot: LiveSnapshot,
    stats_home: dict[str, Any],
    stats_away: dict[str, Any],
) -> list[str]:
    """Tabela de estatisticas: TODAS as categorias reais da fonte."""
    lines: list[str] = []
    for label, stat_type in STAT_DISPLAY:
        lines.append(
            f"  {label}: {_pair(stats_home.get(stat_type), stats_away.get(stat_type))}"
        )
    # qualquer OUTRA estatistica real que a API fornecer para a partida
    for stat_type in sorted(set(stats_home) | set(stats_away)):
        if stat_type not in _KNOWN_STAT_TYPES:
            lines.append(
                f"  {stat_type} (fonte): "
                f"{_pair(stats_home.get(stat_type), stats_away.get(stat_type))}"
            )
    return lines


def _format_half(
    snapshot: LiveSnapshot,
    half_stats: dict[int, dict[str, Any]],
    titulo: str,
) -> list[str]:
    lines: list[str] = []
    if not half_stats:
        lines.append(f"{titulo}: {SEM_TEMPO}")
        return lines
    home = half_stats.get(snapshot.home_team_id, {})
    away = half_stats.get(snapshot.away_team_id, {})
    if not home and not away:
        lines.append(f"{titulo}: {SEM_TEMPO}")
        return lines
    lines.append(f"{titulo} (blocos reais da API para esta partida):")
    lines.extend(format_stat_table(snapshot, home, away))
    return lines


def format_events(snapshot: LiveSnapshot) -> list[str]:
    from src.live import _describe_event

    lines = ["EVENTOS (ordem cronologica real da API):"]
    if not snapshot.events:
        lines.append("  nenhum evento retornado pela API para esta partida")
        return lines
    for ev in snapshot.events:
        lines.append(f"  {_describe_event(ev)}")
    return lines


def format_calcs(snapshot: LiveSnapshot) -> list[str]:
    """[CALCULO] Somente operacoes objetivas sobre os dados da leitura.

    Nenhuma estatistica ausente entra em calculo (None nunca vira zero).
    """
    lines: list[str] = []
    h, a = snapshot.stats_home, snapshot.stats_away
    hc, ac = h.get("Corner Kicks"), a.get("Corner Kicks")
    if hc is not None and ac is not None:
        lines.append(f"  total de escanteios: {hc + ac}")
        if snapshot.elapsed:
            lines.append(
                f"  ritmo medio de escanteios ate o minuto atual "
                f"({snapshot.elapsed}'): {round((hc + ac) / snapshot.elapsed, 2)}/minuto "
                f"(CALCULO, nao dado da API)"
            )
    hs, as_ = h.get("Total Shots"), a.get("Total Shots")
    if hs is not None and as_ is not None:
        lines.append(f"  total de finalizacoes: {hs + as_}")
    hp, ap = h.get("Ball Possession"), a.get("Ball Possession")

    def _pct(v: Any) -> int | None:
        if v is None:
            return None
        try:
            return int(str(v).replace("%", "").strip())
        except (TypeError, ValueError):
            return None

    hp_n, ap_n = _pct(hp), _pct(ap)
    if hp_n is not None and ap_n is not None:
        lines.append(f"  diferenca de posse: {hp_n - ap_n:+d} p.p. (mandante)")
    if not lines:
        lines.append(f"  {INDISPONIVEL} para calculo nesta leitura")
    return lines


def format_live_snapshot(
    snapshot: LiveSnapshot,
    changes: list[str] | None = None,
) -> str:
    """Relatorio completo de uma leitura ao vivo: [FATO] + [CALCULO]."""
    out: list[str] = []

    out.append("[FATO]")
    out.append(
        f"=== AO VIVO - {snapshot.home_team_name} x {snapshot.away_team_name} ==="
    )
    out.append(
        f"fixture {snapshot.fixture_id} | {snapshot.league_name} "
        f"({snapshot.country}) | temporada "
        f"{snapshot.season if snapshot.season is not None else 'nd'}"
        + (f" | {snapshot.round}" if snapshot.round else " | rodada nao informada")
    )
    minuto = f"{snapshot.elapsed}'" if snapshot.elapsed is not None else "minuto nd"
    out.append(
        f"status real {snapshot.status} | minuto {minuto} | "
        f"placar {snapshot.goals_home}-{snapshot.goals_away} "
        "(gols mandante-visitante)"
    )
    if snapshot.halftime_home is not None or snapshot.halftime_away is not None:
        out.append(
            f"placar do 1o tempo (intervalo): "
            f"{snapshot.halftime_home}-{snapshot.halftime_away}"
        )
    out.append(f"hora da leitura: {snapshot.collected_at} (America/Sao_Paulo)")

    out.append("")
    if snapshot.has_stats:
        out.append("ESTATISTICAS DA PARTIDA (fonte: API-Football):")
        out.extend(format_stat_table(snapshot, snapshot.stats_home, snapshot.stats_away))
    else:
        out.append(f"ESTATISTICAS: {INDISPONIVEL} para esta partida nesta leitura.")

    out.append("")
    out.extend(_format_half(snapshot, snapshot.stats_1h, "1o TEMPO"))
    out.extend(_format_half(snapshot, snapshot.stats_2h, "2o TEMPO"))

    out.append("")
    out.extend(format_events(snapshot))

    out.append("")
    out.append("[CALCULO]")
    out.extend(format_calcs(snapshot))

    if changes is not None:
        out.append("")
        out.append("ALTERACOES DESDE A ULTIMA ATUALIZACAO:")
        for change in changes:
            out.append(f"- {change}")

    return "\n".join(out)