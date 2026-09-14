"""Formatacao das saidas em portugues (usada pelo CLI app.py).

Todas as funcoes recebem os dicionarios gerados por analysis.py
e devolvem texto pronto para o usuario.
"""

from __future__ import annotations

from typing import Any

from src.match_stats import SideStats

NOTE_TEMPOS = (
    "Estatisticas por tempo (1oT/2oT): a API-Football as fornece via half=true "
    "(a partir da temporada 2024). Este resumo agrega o jogo completo; "
    "os tempos aparecem por confronto no comando jogo/h2h quando a API os "
    "retorna para a partida; caso contrario, 'dado nao disponivel na fonte'."
)


def _sd(value: Any) -> str:
    """Valor recebido ou s/d - nunca inventa nem reusa dado de outra consulta."""
    if value is None:
        return "s/d"
    return str(value)


def _fmt_describe(d: dict[str, Any] | None, prefix: str = "media") -> str:
    if not d or d.get("n", 0) == 0:
        return "sem dados"
    return (
        f"{prefix} {d['media']:.2f} | mediana {d['mediana']:.2f} | "
        f"desvio {d['desvio']:.2f} | min {d['minimo']:.0f} | max {d['maximo']:.0f}"
    )


def _fmt_over(over: dict[str, Any]) -> str:
    lines = []
    for line, info in sorted(over.items()):
        pct = f"{info['pct']:.0f}%" if info["pct"] is not None else "s/d"
        lines.append(f"  Over {line}: {info['hits']}/{info['total']} ({pct})")
    return "\n".join(lines)


def _fmt_trend(trend: dict[str, Any]) -> str:
    if trend.get("slope") is None:
        return f"tendencia: {trend.get('classificacao', 's/d')}"
    arrow = {"crescente": "↑", "decrescente": "↓", "estavel": "→"}.get(
        trend["classificacao"], "→"
    )
    return f"tendencia: {trend['classificacao']} {arrow} ({trend['slope']}/jogo)"


# ----------------------------------------------------------------------
def format_team_analysis(result: dict[str, Any], analise: bool = False) -> str:
    team = result["time"]
    stats: dict[str, Any] | None = result["stats"]
    out = [f"=== PERFIL DE ESCANTEIOS - {team.name} ({team.country or '?'}) ==="]
    out.append(
        f"Ultimos {result['solicitados']} jogos: "
        f"{result['com_dados']} com estatisticas completas"
        + (
            f", {result['sem_estatisticas']} sem dados na API (desconsiderados)"
            if result["sem_estatisticas"]
            else ""
        )
    )
    if not stats:
        out.append("Nenhum jogo com estatisticas disponivel para esse time.")
        return "\n".join(out)

    out.append("")
    out.append(f"A favor...: {_fmt_describe(stats['favor'], 'media')}")
    out.append(f"Contra....: {_fmt_describe(stats['contra'], 'media')}")
    out.append(f"Total.....: {_fmt_describe(stats['total'], 'media')}")
    out.append(f"Janela 5..: media {stats['janelas'][5]['describe']['media']:.2f}")
    out.append(f"Janela 10.: media {stats['janelas'][10]['describe']['media']:.2f}")
    if analise:
        out.append(_fmt_trend(stats["tendencia_geral"]))
    out.append("")
    out.append(f"CASA ({stats['casa']['n_jogos']} jogos):")
    out.append(f"  {_fmt_describe(stats['casa']['total'])}")
    out.append(f"FORA ({stats['fora']['n_jogos']} jogos):")
    out.append(f"  {_fmt_describe(stats['fora']['total'])}")
    out.append("")
    out.append(f"OVER (total da partida, {stats['n_jogos']} jogos):")
    out.append(_fmt_over(stats["over_geral"]))
    out.append("")
    out.append(NOTE_TEMPOS)
    return "\n".join(out)


def format_metrics(result: dict[str, Any]) -> str:
    """Bloco de metricas objetivas (motor generico) - so dados e calculos.

    Por metrica: jogos com dado, media, mediana, minimo, maximo e media
    casa/fora. Jogos sem o dado na API sao contabilizados (n com dado x
    total); valores ausentes NUNCA viram zero.
    """
    team = result["time"]
    panel = result.get("metricas")
    out = [f"=== METRICAS OBJETIVAS - {team.name} ==="]
    if not panel:
        out.append("Nenhum jogo com estatisticas disponivel.")
        return "\n".join(out)

    for name, entry in panel.items():
        views = [("do time", "do_time"), ("do adversario", "do_adversario")]
        if entry.get("total") is not None:
            views.append(("total da partida", "total"))
        for label, key in views:
            r = entry[key]
            total_jogos = r["jogos_com_dado"] + r["jogos_sem_dado"]
            d = r["geral"]
            if d.get("n", 0) == 0:
                out.append(
                    f"{name} ({label}): DADO NAO DISPONIVEL NA FONTE "
                    f"(0 de {total_jogos} jogos com dado)"
                )
                continue
            casa, fora = r["casa"], r["fora"]
            casa_txt = (
                f"media {casa['media']} ({casa['n']}j)"
                if casa.get("n", 0)
                else "s/d"
            )
            fora_txt = (
                f"media {fora['media']} ({fora['n']}j)"
                if fora.get("n", 0)
                else "s/d"
            )
            out.append(
                f"{name} ({label}): jogos com dado "
                f"{r['jogos_com_dado']}/{total_jogos}"
                f" | media {d['media']} | mediana {d['mediana']}"
                f" | min {d['minimo']} | max {d['maximo']}"
                f" | casa: {casa_txt} | fora: {fora_txt}"
            )
    return "\n".join(out)


def format_games_table(games: list[Any]) -> str:
    """Tabela dos jogos analisados (mais recente primeiro)."""
    if not games:
        return "Nenhum jogo com estatisticas disponivel."
    lines = [
        "Data       | Adversario           | Local | Esc(A-F-T) | Chutes | Placar"
    ]
    for g in games:
        home = "C" if g.played_at_home else "F"
        date = g.date[:10]
        lines.append(
            f"{date} | {g.opponent[:20]:<20} |  {home}   | "
            f"{g.corners_for}-{g.corners_against}-{g.corners_total}        | "
            f"{g.shots_for or '-':>2}-{g.shots_against or '-':<2} | "
            f"{g.goals_for}-{g.goals_against}"
        )
    return "\n".join(lines)


def format_compare(result: dict[str, Any], analise: bool = False) -> str:
    a, b = result["time_a"], result["time_b"]
    out = [
        f"=== COMPARACAO (historicos INDIVIDUAIS) - {a.name} x {b.name} ===",
        "Amostras separadas. Para partidas entre eles, use H2H (confronto direto).",
        "",
    ]
    for label, team, stats, miss in (
        ("A", a, result["stats_a"], result["sem_estatisticas_a"]),
        ("B", b, result["stats_b"], result["sem_estatisticas_b"]),
    ):
        out.append(f"[{team.name}]")
        if not stats:
            out.append("  sem estatisticas disponiveis")
            continue
        if miss:
            out.append(f"  (ultimos {stats['n_jogos']} jogos com dados; "
                       f"{miss} sem estatisticas na API)")
        out.append(f"  A favor: {_fmt_describe(stats['favor'])}")
        out.append(f"  Contra..: {_fmt_describe(stats['contra'])}")
        out.append(f"  Total...: {_fmt_describe(stats['total'])}")
        out.append(f"  Casa....: {_fmt_describe(stats['casa']['total']) if stats['casa']['total'].get('n') else 'sem dados'}")
        out.append(f"  Fora....: {_fmt_describe(stats['fora']['total']) if stats['fora']['total'].get('n') else 'sem dados'}")
        if analise:
            out.append(f"  {_fmt_trend(stats['tendencia_geral'])}")
        out.append("")
    if analise:
        xc = result.get("xcorners")
        if xc and xc.get("disponivel"):
            out.append(f"xCorners (estimativa): {xc['x_total_estimado']} totais "
                       f"(A {xc['x_escanteios_A']} x B {xc['x_escanteios_B']})")
            out.append(f"  metodo: {xc['metodo']}")
    out.append(NOTE_TEMPOS)
    return "\n".join(out)


def _fmt_half(label: str, home: SideStats | None, away: SideStats | None) -> str:
    """Linha de estatisticas de UM tempo, por time.

    Valores vem exclusivamente do bloco statistics_1h/statistics_2h da
    MESMA resposta da API para o fixture; campo ausente = s/d (nunca
    preenchido com dado do jogo completo nem de outra consulta).
    """
    h = home or SideStats()
    a = away or SideStats()
    return (
        f"  {label}: escanteios {_sd(h.corners)}-{_sd(a.corners)} | "
        f"chutes {_sd(h.shots)}-{_sd(a.shots)} | "
        f"no gol {_sd(h.shots_on_goal)}-{_sd(a.shots_on_goal)} | "
        f"amarelos {_sd(h.yellow_cards)}-{_sd(a.yellow_cards)} | "
        f"vermelhos {_sd(h.red_cards)}-{_sd(a.red_cards)} | "
        f"posse {_sd(h.possession_pct)}%-{_sd(a.possession_pct)}% | "
        f"faltas {_sd(h.fouls)}-{_sd(a.fouls)} | "
        f"impedimentos {_sd(h.offsides)}-{_sd(a.offsides)}"
    )


def format_h2h(result: dict[str, Any], analise: bool = False) -> str:
    a, b = result["time_a"], result["time_b"]
    h2h = result["h2h"]
    stats = result["stats"]
    out = [f"=== CONFRONTO DIRETO (H2H) - {a.name} x {b.name} ==="]

    if h2h.found < h2h.requested:
        out.append(
            f"ATENCAO: solicitados {h2h.requested} confrontos, "
            f"encontrados {h2h.found} na API. A amostra NAO foi completada "
            "artificialmente."
        )
    else:
        out.append(f"Confrontos: {h2h.requested}")
    if h2h.skipped_no_stats:
        out.append(
            f"{h2h.skipped_no_stats} confronto(s) sem estatisticas na API "
            "(exibidos abaixo sem escanteios, fora dos calculos)."
        )
    out.append("")

    for m in h2h.matches:
        f = m.fixture
        date = f.date[:10]
        score = f"{f.goals_home}-{f.goals_away}" if f.goals_home is not None else "s/d"
        corners = (
            f"{m.corners_home}-{m.corners_away} (total {m.corners_total})"
            if m.corners_total is not None
            else "escanteios s/d"
        )
        shots = (
            f"chutes {m.shots_home}-{m.shots_away}"
            if m.shots_home is not None
            else "chutes s/d"
        )
        cards = (
            f"cartoes Y {_sd(m.yellow_home)}-{_sd(m.yellow_away)} / "
            f"R {_sd(m.red_home)}-{_sd(m.red_away)}"
        )
        poss = (
            f"posse {m.possession_home}%-{m.possession_away}%"
            if m.possession_home is not None
            else "posse s/d"
        )
        fouls = f"faltas {_sd(m.fouls_home)}-{_sd(m.fouls_away)}"
        offs = f"impedimentos {_sd(m.offsides_home)}-{_sd(m.offsides_away)}"
        out.append(
            f"{date} | {f.league_name[:24]} | {f.round[:20]}\n"
            f"  {f.home_team_name} {score} {f.away_team_name} | "
            f"{corners} | {shots} | {cards} | {poss} | {fouls} | {offs}"
        )
        # Por tempo: so quando a API retorna statistics_1h/2h para ESTA partida
        if (
            m.first_home is None and m.first_away is None
            and m.second_home is None and m.second_away is None
        ):
            out.append("  1oT/2oT: DADO NAO DISPONIVEL NA FONTE (nesta partida)")
        else:
            out.append(_fmt_half("1oT", m.first_home, m.first_away))
            out.append(_fmt_half("2oT", m.second_home, m.second_away))

    out.append("")
    if stats and stats.get("n_confrontos_com_dados"):
        d = stats["describe"]
        out.append(
            f"Total de escanteios nos confrontos: {_fmt_describe(d)}"
        )
        out.append(_fmt_over(stats["over"]))
        if analise:
            out.append(_fmt_trend(stats["tendencia"]))
    else:
        out.append("Sem estatisticas de escanteios nos confrontos encontrados.")
    out.append("")
    out.append("s/d = dado nao disponivel na fonte (na resposta da API para a partida).")
    out.append(NOTE_TEMPOS)
    return "\n".join(out)


def format_today(result: dict[str, Any]) -> str:
    fixtures = result["jogos"]
    league = result.get("liga")
    title = "=== JOGOS DE HOJE"
    if league:
        title += f" - {league.name} ({league.country or '?'})"
    out = [title + " ==="]
    if not fixtures:
        out.append("Nenhum jogo encontrado para hoje."
                    + (" nessa liga." if league else ""))
        return "\n".join(out)

    current_league = None
    for f in fixtures:
        if f.league_name != current_league:
            current_league = f.league_name
            out.append(f"\n[{current_league}]")
            out.append(f"  Rodada/fase: {f.round}")
        score = (
            f"{f.goals_home}-{f.goals_away}"
            if f.goals_home is not None
            else "x"
        )
        # Status e minuto EXATOS da API (codigos curtos, sem descricao inventada)
        status = f.status
        if f.elapsed:
            status += f" {f.elapsed}'"
        out.append(f"  {f.date[11:16]} | {f.home_team_name} {score} {f.away_team_name} | {status} | ID {f.fixture_id}")
    out.append("\nHorarios no fuso America/Sao_Paulo. Status: codigos da API "
               "(NS = agendado; FT = encerrado; 1H/HT/2H = periodos).")
    out.append("\nDica: use 'prejogo \"Time A x Time B\"' para analise completa de um jogo.")
    return "\n".join(out)


def format_league(result: dict[str, Any]) -> str:
    """Media da liga - competicao inteira na temporada identificada.

    Exibe obrigatoriamente: competicao (com ID), temporada, quantidade de
    partidas encerradas, validas (com escanteios na API) e excluidas.
    Nunca representa amostra de N jogos como media da liga.
    """
    league = result["liga"]
    out = [f"=== MEDIA DE ESCANTEIOS - {league.name} ({league.country or '?'}) ==="]
    out.append(
        f"Competicao: {league.name} (id {league.id}) | "
        f"Temporada: {result['temporada']}"
    )
    m = result.get("media")
    if not m:
        out.append(
            "Sem partidas validas com escanteios na fonte para essa "
            "competicao/temporada."
        )
        return "\n".join(out)
    out.append(
        f"Partidas encerradas na temporada: {m['partidas_encerradas']} | "
        f"com escanteios validos: {m['partidas_validas']} | "
        f"excluidas sem estatistica: {m['partidas_sem_escanteios']}"
    )
    out.append("")
    out.append(f"Total por partida: {_fmt_describe(m['describe'])}")
    out.append(_fmt_over(m["over"]))
    out.append("")
    out.append(
        "Regra de integridade: media sobre TODAS as partidas finalizadas da "
        "competicao na temporada (nunca amostra de N jogos); partida sem "
        "escanteios na API e excluida, nunca zerada."
    )
    return "\n".join(out)


def format_pre_match(result: dict[str, Any], analise: bool = False) -> str:
    a, b = result["time_a"], result["time_b"]
    fixture = result["proximo_jogo"]
    out = [f"=== ANALISE PRE-JOGO - {a.name} x {b.name} ==="]
    if fixture:
        out.append(
            f"Proximo jogo: {fixture.date} | {fixture.league_name} | {fixture.round}\n"
            f"  {fixture.home_team_name} (casa) x {fixture.away_team_name} (fora)"
            + (f" | {fixture.venue}" if fixture.venue else "")
        )
    else:
        out.append("Nenhum jogo agendado entre os dois times foi encontrado "
                    "(analise baseada nos historicos recentes).")
    out.append("")
    for team, stats, miss in (
        (a, result["stats_a"], result["sem_estatisticas_a"]),
        (b, result["stats_b"], result["sem_estatisticas_b"]),
    ):
        out.append(f"[{team.name}] ultimos jogos com dados: "
                   f"{stats['n_jogos'] if stats else 0}"
                   + (f" ({miss} sem estatisticas)" if miss else ""))
        if stats:
            out.append(f"  Total...: {_fmt_describe(stats['total'])}")
            out.append(f"  A favor: {_fmt_describe(stats['favor'])} | "
                       f"Contra: {_fmt_describe(stats['contra'])}")
            out.append(f"  Casa....: {_fmt_describe(stats['casa']['total']) if stats['casa']['total'].get('n') else 'sem dados'}")
            out.append(f"  Fora....: {_fmt_describe(stats['fora']['total']) if stats['fora']['total'].get('n') else 'sem dados'}")
            out.append(f"  Janela 5: {stats['janelas'][5]['describe']['media']:.2f} | "
                       f"Janela 10: {stats['janelas'][10]['describe']['media']:.2f}")
            if analise:
                out.append(f"  {_fmt_trend(stats['tendencia_geral'])}")
            out.append("  " + _fmt_over(stats["over_geral"]).replace("\n", "\n  "))
        out.append("")

    h2h_stats = result.get("h2h_stats")
    if h2h_stats and h2h_stats.get("n_confrontos_com_dados"):
        out.append(f"[CONFRONTO DIRETO] {h2h_stats['n_confrontos_com_dados']} jogos com dados")
        out.append(f"  {_fmt_describe(h2h_stats['describe'])}")
        out.append("  " + _fmt_over(h2h_stats["over"]).replace("\n", "\n  "))
        if analise:
            out.append(f"  {_fmt_trend(h2h_stats['tendencia'])}")
    else:
        out.append("[CONFRONTO DIRETO] sem jogos com estatisticas entre os times")
    out.append("")

    league_avg = result.get("media_liga")
    if league_avg:
        out.append(
            f"[MEDIA DA LIGA - competicao inteira] {league_avg['liga']} "
            f"(id {league_avg['liga_id']}), temporada {league_avg['temporada']}: "
            f"partidas validas {league_avg['partidas_validas']}/"
            f"{league_avg['partidas_encerradas']} encerradas | "
            f"total {league_avg['describe']['media']:.2f} | "
            f"mediana {league_avg['describe']['mediana']:.1f} | "
            f"min {league_avg['describe']['minimo']} | "
            f"max {league_avg['describe']['maximo']}"
        )
        o95 = league_avg["over"].get(9.5)
        if o95:
            out.append(f"  Over 9.5: {o95['hits']}/{o95['total']}")
    out.append("")

    if analise:
        xc = result.get("xcorners")
        if xc and xc.get("disponivel"):
            out.append(f"[xCORNERS - ESTIMATIVA] {xc['x_total_estimado']} totais "
                       f"(A {xc['x_escanteios_A']} x B {xc['x_escanteios_B']})")
            out.append(f"  {xc['metodo']}")
        out.append("")
    out.append(NOTE_TEMPOS)
    return "\n".join(out)


def _fmt_stat(value: Any) -> str:
    """Valor vindo da API ou aviso de indisponibilidade - nunca inventa."""
    if value is None:
        return "s/d (dado nao disponivel na fonte)"
    return str(value)


def format_live(result: dict[str, Any]) -> str:
    fixture = result["fixture"]
    match = result["match"]
    out = [f"=== AO VIVO - {fixture.home_team_name} x {fixture.away_team_name} ==="]
    out.append(
        f"{fixture.league_name} | {fixture.round} | "
        f"{fixture.status} {fixture.elapsed or 0}' | "
        f"placar {fixture.goals_home}-{fixture.goals_away}"
    )
    if not match:
        out.append("Estatisticas ainda nao disponiveis para este jogo.")
        return "\n".join(out)

    home, away = match.home, match.away
    out.append("")
    out.append("[DADOS DA API]")
    out.append(f"Escanteios: {home.corners} - {away.corners}")
    out.append(f"Chutes: {home.shots} - {away.shots} | "
               f"no gol: {home.shots_on_goal} - {away.shots_on_goal}")
    out.append(
        f"Cartoes amarelos: {_fmt_stat(home.yellow_cards)} - "
        f"{_fmt_stat(away.yellow_cards)}"
    )
    out.append(
        f"Cartoes vermelhos: {_fmt_stat(home.red_cards)} - "
        f"{_fmt_stat(away.red_cards)}"
    )
    out.append(f"Posse: {home.possession_pct}% - {away.possession_pct}%")

    # ---- Interpretação: estimativa, claramente separada do dado da API ----
    proj = result.get("projecao")
    if proj:
        out.append("")
        out.append("[PROJECAO - estimativa calculada, NAO e dado da API]")
        out.append(
            f"Ritmo: {proj['escanteios_atuais']} escanteios em {proj['minuto']}' "
            f"({proj['ritmo_por_minuto']}/min) -> projecao {proj['projecao_90min']} "
            "escanteios aos 90'"
        )
    out.append("")
    out.append(
        "Estatisticas ao vivo atualizam a cada minuto (cache de 60s); "
        "repita o comando para atualizar."
    )
    return "\n".join(out)