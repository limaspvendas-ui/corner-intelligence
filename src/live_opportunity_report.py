"""Relatorios da ETAPA 2.2 (oportunidades ao vivo).

Separacao obrigatoria: [FATO] / [CALCULO] / [INTERPRETACAO].

    [FATO - ESTADO ATUAL]      dados reais da leitura live: jogo, minuto,
                               placar, escanteios (com total), finalizacoes,
                               posse, cartoes, faltas, impedimentos,
                               expulsoes, eventos recentes. Dado ausente
                               aparece como "s/d" - NUNCA como zero.
    [FATO - CONTEXTO HISTORICO] recortes realmente usados no calculo
                               (ultimos 10 de cada time, mando casa/fora,
                               benchmark da liga, h2h) e a FREQUENCIA
                               HISTORICA DA LINHA escolhida em cada
                               recorte. Recorte nao coletado e informado
                               como s/d - nunca inventado.
    [CALCULO]                  todos os inputs da probabilidade: valores
                               usados, tempo restante, ritmo observado,
                               media historica, projecao final, modelo,
                               probabilidade, confianca e tamanho efetivo
                               da amostra.
    [CONTRADICOES / RISCOS]    dado que mais favorece, dado que mais
                               contradiz, outliers, expulsoes, amostra
                               pequena, forma recente x historico e dados
                               ausentes.
    [ODD]                      odd REALMENTE ao vivo (nunca pre-jogo):
                               casa, linha, odd, timestamp, probabilidade
                               implicita e diferenca para a estimada.
    [INTERPRETACAO]            POR QUE FOI APROVADA (3-5 pontos) e O QUE
                               PODE FAZER DAR ERRADO (2-4 riscos).

Sem aprovacao => a mensagem exata:
    NENHUMA OPORTUNIDADE AO VIVO APROVADA AGORA.
Nunca se forca uma selecao. Nenhuma logica estatistica vive aqui: este
modulo apenas FORMATA dados e calculos ja feitos pelo engine.
"""

from __future__ import annotations

import math
import re
from typing import Any

from src.live import STAT_DISPLAY, _describe_event
from src.live_opportunity import (
    CONF_MIN_TOP1,
    PROB_MIN_APROVAR,
    Avaliacao,
    Candidato,
    NENHUMA_MSG,
    SEM_ODD_LIVE,
    Varredura,
)

INDISPONIVEL = "dado nao disponivel na fonte"
SD = "s/d"  # sem dado na fonte - nunca zero


def _one(value: Any) -> str:
    return SD if value is None else str(value)


# ----------------------------------------------------------------------
# Linha do mercado (Over/Under X.5 de escanteios/gols/cartoes)
# ----------------------------------------------------------------------
_RE_LINHA_MERCADO = re.compile(
    r"^(Over|Under)\s+(\d+(?:[.,]\d+)?)\s+(escanteios|gols|cartoes)"
)


def _parse_linha_total(av: Avaliacao) -> tuple[str, float, str] | None:
    """(direcao, linha, unidade) quando o mercado tem linha de total."""
    m = _RE_LINHA_MERCADO.match(av.linha or "")
    if not m:
        return None
    return m.group(1), float(m.group(2).replace(",", ".")), m.group(3)


def _total_do_jogo(g: Any, unidade: str) -> float | None:
    """Total da partida na unidade do mercado, na perspectiva do time.
    Jogo sem o dado na fonte => None (excluido da amostra, nunca zero)."""
    if unidade == "escanteios":
        if g.corners_total is None:
            return None
        return float(g.corners_total)
    if unidade == "gols":
        if g.goals_for is None or g.goals_against is None:
            return None
        return float(g.goals_for) + float(g.goals_against)
    if unidade == "cartoes":
        # mesma convencao rotulada do mercado: amarelo=1, vermelho=2
        if g.yellow_for is None or g.red_for is None:
            return None
        ya = g.yellow_against if g.yellow_against is not None else 0
        ra = g.red_against if g.red_against is not None else 0
        return float(g.yellow_for + 2 * g.red_for + ya + 2 * ra)
    return None


def _freq_recorte(
    games: list[Any], unidade: str, direcao: str, linha: float
) -> tuple[int, int, int]:
    """(hits, validos, excluidos): jogos sem o dado sao EXCLUIDOS e
    contabilizados - nunca transformados em zero."""
    hits = validos = excluidos = 0
    for g in games:
        total = _total_do_jogo(g, unidade)
        if total is None:
            excluidos += 1
            continue
        validos += 1
        if direcao == "Over" and total > linha:
            hits += 1
        elif direcao == "Under" and total < linha:
            hits += 1
    return hits, validos, excluidos


def _benchmark_pct(
    av: Avaliacao, cand: Candidato
) -> tuple[float | None, str]:
    """(pct, texto) da frequencia da linha no benchmark da competicao.
    pct e None quando o recorte coletado nao permite calcular (o texto
    explica o motivo - nunca se inventa)."""
    parsed = _parse_linha_total(av)
    if parsed is None:
        return None, f"{SD} (mercado sem linha de total)"
    direcao, linha, unidade = parsed
    if unidade == "escanteios":
        bench = cand.benchmark_escanteios
        over = (bench or {}).get("over") or {}
        info = over.get(linha)
        if info is not None and info.get("pct") is not None:
            if direcao == "Over":
                return (
                    float(info["pct"]),
                    f"{info['pct']:.0f}% ({info['hits']}/{info['total']} "
                    f"partidas over {linha:g})",
                )
            # linha X.5 com totais inteiros: complemento exato
            under = 100.0 - float(info["pct"])
            return (
                under,
                f"{under:.0f}% (CALCULO: 100% - {info['pct']:.1f}% over "
                f"{linha:g}; {info['total']} partidas validas)",
            )
        if over:
            linhas = ", ".join(f"{k:g}" for k in sorted(over))
            return None, f"{SD} (benchmark calculado nas linhas {linhas})"
        return None, f"{SD} (benchmark da competicao {INDISPONIVEL})"
    if unidade == "gols":
        bench = cand.benchmark_gols
        if bench:
            media = (bench.get("describe") or {}).get("media")
            return (
                None,
                f"{SD} (benchmark de gols coletado: apenas a media "
                f"{round(media, 2) if media is not None else SD} gols por "
                "partida; frequencia por linha nao calculada nesta varredura)",
            )
        return None, f"{SD} (benchmark de gols {INDISPONIVEL})"
    return (
        None,
        f"{SD} (a fonte nao fornece frequencia por linha de cartoes "
        "da competicao)",
    )


# ----------------------------------------------------------------------
# [FATO - ESTADO ATUAL]: a leitura live completa
# ----------------------------------------------------------------------
def _bloco_fato_live(cand: Candidato) -> list[str]:
    snap = cand.snapshot
    esc_h = snap.stats_home.get("Corner Kicks")
    esc_a = snap.stats_away.get("Corner Kicks")
    esc_total = (
        str(esc_h + esc_a)
        if isinstance(esc_h, int) and isinstance(esc_a, int)
        else SD
    )
    ver_h = snap.stats_home.get("Red Cards")
    ver_a = snap.stats_away.get("Red Cards")
    ver_total = (
        str(ver_h + ver_a)
        if isinstance(ver_h, int) and isinstance(ver_a, int)
        else SD
    )
    out = [
        f"jogo: {snap.home_team_name} x {snap.away_team_name}",
        f"competicao: {snap.league_name} ({snap.country})",
        f"fixture: {snap.fixture_id}",
        f"minuto/status: {_one(snap.elapsed)}' / {snap.status}",
        f"placar: {_one(snap.goals_home)}-{_one(snap.goals_away)}",
        f"horario exato do snapshot: {snap.collected_at} "
        "(America/Sao_Paulo)",
        "escanteios: "
        f"{_one(esc_h)} ({snap.home_team_name}) - "
        f"{_one(esc_a)} ({snap.away_team_name}) | total: {esc_total}",
        f"expulsoes (cartoes vermelhos): {_one(ver_h)} - {_one(ver_a)} "
        f"| total: {ver_total}",
        "estatisticas ao vivo (pares mandante - visitante; "
        f"{SD} = sem dado na fonte nesta leitura):",
    ]
    for label, stat_type in STAT_DISPLAY:
        out.append(
            f"  {label}: {_one(snap.stats_home.get(stat_type))} - "
            f"{_one(snap.stats_away.get(stat_type))}"
        )
    if snap.stats_1h or snap.stats_2h:
        for rotulo, bloco in (
            ("1o tempo", snap.stats_1h), ("2o tempo", snap.stats_2h)
        ):
            linha_h = bloco.get(snap.home_team_id) or {}
            linha_a = bloco.get(snap.away_team_id) or {}
            if not linha_h and not linha_a:
                continue
            esc_bh = linha_h.get("Corner Kicks")
            esc_ba = linha_a.get("Corner Kicks")
            gol_h = linha_h.get("Goals")  # quando a fonte fornece por tempo
            out.append(
                f"  escanteios {rotulo}: {_one(esc_bh)} - {_one(esc_ba)}"
                + (f" | gols {rotulo}: {_one(gol_h)} - "
                   f"{_one(linha_a.get('Goals'))}" if gol_h is not None else "")
            )
    else:
        out.append("  dados por tempo: nao fornecidos pela fonte nesta leitura")
    out.append("eventos recentes relevantes (ordem cronologica da fonte):")
    if snap.events:
        for ev in snap.events[-5:]:
            out.append(f"  - {_describe_event(ev)}")
    else:
        out.append(f"  {SD} (a fonte nao retornou eventos nesta leitura)")
    for aviso in cand.avisos:
        out.append(f"  aviso: {aviso}")
    return out


# ----------------------------------------------------------------------
# [FATO - CONTEXTO HISTORICO]: recortes + frequencia historica da linha
# ----------------------------------------------------------------------
def _bloco_historico(cand: Candidato) -> list[str]:
    """Historico + benchmark + H2H (peso menor) - tudo [FATO]/[CALCULO]."""
    snap = cand.snapshot
    out = [
        "historico recente (ultimos jogos encerrados de cada time):",
        f"  {snap.home_team_name}: {cand.historico.get('n_home', 0)} jogos "
        f"com estatisticas",
        f"  {snap.away_team_name}: {cand.historico.get('n_away', 0)} jogos "
        f"com estatisticas",
    ]
    bench = cand.benchmark_escanteios
    if bench:
        out.append(
            "benchmark da competicao (TODAS as partidas finalizadas da "
            f"temporada {bench.get('temporada')}): {bench.get('liga')} - "
            f"{bench.get('partidas_validas')} validas / "
            f"{bench.get('partidas_encerradas')} encerradas / "
            f"{bench.get('partidas_sem_escanteios')} sem escanteios na fonte "
            "(excluidas, nunca zeradas) - media "
            f"{round((bench.get('describe') or {}).get('media', 0.0), 2)} "
            "escanteios"
        )
    else:
        out.append("benchmark da competicao: " + INDISPONIVEL)
    bench_g = cand.benchmark_gols
    if bench_g:
        out.append(
            "benchmark de gols da competicao: "
            f"{bench_g.get('partidas_validas')} partidas validas - media "
            f"{round((bench_g.get('describe') or {}).get('media', 0.0), 2)} gols"
        )
    if cand.h2h_stats:
        out.append(
            f"h2h (peso menor): {cand.h2h_n} confrontos diretos com "
            "estatisticas na fonte"
        )
    return out


def _bloco_contexto_historico(av: Avaliacao, cand: Candidato) -> list[str]:
    """Recortes REALMENTE usados no calculo + frequencia historica da
    LINHA escolhida em cada recorte ([CALCULO] objetivo sobre os jogos
    ja coletados; nenhum dado novo e buscado)."""
    snap = cand.snapshot
    out = _bloco_historico(cand)
    parsed = _parse_linha_total(av)
    if parsed is None:
        out.append(
            f"  recorte por linha para '{av.linha}': {SD} (mercado sem "
            "linha de total nesta implementacao)"
        )
        return out
    direcao, linha, unidade = parsed
    gh = cand.historico.get("games_home") or []
    ga = cand.historico.get("games_away") or []

    def fmt(games: list[Any], rotulo: str) -> str:
        hits, validos, excl = _freq_recorte(games, unidade, direcao, linha)
        if not validos:
            return f"  {rotulo}: {SD} (nenhum jogo com este dado na amostra)"
        extra = (
            f" | {excl} sem o dado na fonte (excluidos, nunca zerados)"
            if excl else ""
        )
        return f"  {rotulo}: {hits}/{validos} ({100 * hits / validos:.0f}%){extra}"

    hh, vh, _ = _freq_recorte(gh, unidade, direcao, linha)
    ha, va, _ = _freq_recorte(ga, unidade, direcao, linha)
    out.append(
        f"frequencia historica da linha '{av.linha}' nos recortes usados "
        "pelo calculo:"
    )
    out.append(fmt(gh, f"ultimos {len(gh)} ({snap.home_team_name})"))
    out.append(fmt(ga, f"ultimos {len(ga)} ({snap.away_team_name})"))
    if vh + va:
        conj = hh + ha
        out.append(
            f"  conjunto dos dois times (ate 20 jogos): {conj}/{vh + va} "
            f"({100 * conj / (vh + va):.0f}%)"
        )
    else:
        out.append(f"  conjunto dos dois times: {SD} (amostra sem o dado)")
    out.append(
        f"  ate 50 jogos: {SD} (recorte nao coletado nesta varredura; o "
        "calculo usa os ultimos 10 de cada time)"
    )
    casa = [g for g in gh if g.played_at_home]
    fora = [g for g in ga if not g.played_at_home]
    out.append(fmt(casa, f"{snap.home_team_name} em casa (mando do fixture)"))
    out.append(fmt(fora, f"{snap.away_team_name} fora (mando do fixture)"))
    _pct, bench_txt = _benchmark_pct(av, cand)
    out.append(f"  benchmark da liga: {bench_txt}")
    if cand.h2h_stats:
        h2h = cand.h2h_stats
        n_h2h = h2h.get("n_confrontos_com_dados")
        media_h2h = (h2h.get("describe") or {}).get("media")
        extra_h2h = (
            f"; media {round(media_h2h, 2)} escanteios por confronto"
            if media_h2h is not None else ""
        )
        over_h2h = (h2h.get("over") or {}).get(linha)
        if over_h2h is not None and over_h2h.get("pct") is not None:
            pct = (
                float(over_h2h["pct"]) if direcao == "Over"
                else 100.0 - float(over_h2h["pct"])
            )
            extra_h2h += (
                f"; {direcao.lower()} {linha:g} em {pct:.0f}% dos confrontos"
            )
        out.append(f"  h2h (peso menor): {n_h2h} com dados{extra_h2h}")
    else:
        out.append(f"  h2h (peso menor): {SD} (sem confrontos com dados)")
    return out


# ----------------------------------------------------------------------
# [CALCULO]: todos os inputs da probabilidade
# ----------------------------------------------------------------------
def _bloco_calculo(av: Avaliacao, cand: Candidato) -> list[str]:
    sust = av.sustentacao
    out = [
        f"mercado: {av.mercado}",
        f"linha: {av.linha}",
        f"[CALCULO] probabilidade estimada: {av.prob:.1%}",
        "[CALCULO] como esta probabilidade foi obtida (todos os inputs):",
    ]
    ordem = [
        ("modelo", "modelo utilizado"),
        ("atual_no_jogo", "valor atual no jogo (familia do mercado)"),
        ("minuto", "minuto da leitura"),
        ("tempo_restante_min", "tempo restante (min)"),
        ("baseline", "media historica usada (cruzamento casa/fora + benchmark)"),
        ("taxa_combinada_por90", "ritmo combinado por 90' (historico + observado)"),
        ("peso_do_observado", "peso do ritmo observado na mistura (teto 50%)"),
        ("esperado_no_restante", "esperado no tempo restante"),
        ("projecao_final", "projecao final (atual + esperado)"),
        ("distribuicao", "distribuicao de margens"),
        ("convencao", "convencao do handicap"),
    ]
    vistos: set[str] = set()
    for chave, rotulo in ordem:
        if chave in sust:
            vistos.add(chave)
            out.append(f"  {rotulo}: {sust[chave]}")
    for chave, valor in sust.items():
        if chave not in vistos:
            out.append(f"  {chave}: {valor}")
    parsed = _parse_linha_total(av)
    if parsed:
        direcao, linha, unidade = parsed
        atual = sust.get("atual_no_jogo")
        if atual is not None:
            if direcao == "Over":
                precisa = math.floor(linha) + 1 - atual
                regra = (
                    f"Over {linha:g} ja garantido pelo estado atual"
                    if precisa <= 0
                    else f"Over {linha:g} precisa de {precisa} no tempo "
                    "restante"
                )
            else:
                cabem = math.floor(linha) - atual
                regra = (
                    f"Under {linha:g} ja impossivel pelo estado atual"
                    if cabem < 0
                    else f"Under {linha:g} permite no maximo {cabem} no "
                    "tempo restante"
                )
            out.append(f"  regra de batida (CALCULO): {regra}")
    out.append(f"  probabilidade calculada (saida do modelo): {av.prob:.1%}")
    n_home = cand.historico.get("n_home")
    n_away = cand.historico.get("n_away")
    n_min = min(
        v for v in (n_home, n_away) if v is not None
    ) if (n_home is not None or n_away is not None) else None
    out.append(
        "  tamanho efetivo da amostra: "
        f"{_one(n_home)} jogos do mandante + {_one(n_away)} do visitante "
        f"(minimo {_one(n_min)}; exigido >= 5)"
    )
    out.append("")
    out.append(
        f"[CALCULO] confianca: {av.confianca:.0%} "
        f"(minimo exigido {CONF_MIN_TOP1:.0%})"
    )
    if av.conf_componentes:
        out.append("  componentes:")
        for nome, valor in av.conf_componentes.items():
            out.append(f"    {nome}: {valor:.2f}")
    return out


# ----------------------------------------------------------------------
# [CONTRADICOES / RISCOS]: favoravel, contraditorio, outliers, ausencias
# ----------------------------------------------------------------------
def _fatos_favor_contra(
    av: Avaliacao, cand: Candidato
) -> tuple[str, str]:
    """O dado que MAIS favorece e o que MAIS contradiz a linha - escolha
    objetiva por forca numerica (margens e percentuais), nunca palpite."""
    parsed = _parse_linha_total(av)
    if parsed is None:
        return (
            f"{SD} (mercado sem linha de total; a leitura vem da "
            "distribuicao de margens)",
            f"{SD} (mercado sem linha de total; a leitura vem da "
            "distribuicao de margens)",
        )
    direcao, linha, unidade = parsed
    sust = av.sustentacao
    gh = cand.historico.get("games_home") or []
    ga = cand.historico.get("games_away") or []
    fatos: list[tuple[float, str]] = []

    atual = sust.get("atual_no_jogo")
    esperado = sust.get("esperado_no_restante")
    restante = sust.get("tempo_restante_min")
    if atual is not None and esperado is not None and restante is not None:
        if direcao == "Over":
            precisa = math.floor(linha) + 1 - atual
            fatos.append((
                float(esperado) - precisa,
                f"ritmo atual projeta {esperado} no restante vs "
                f"{precisa} necessarios para a linha "
                f"({restante}' restantes)",
            ))
        else:
            cabem = math.floor(linha) - atual
            fatos.append((
                float(cabem) - float(esperado),
                f"cabem apenas {cabem} no restante vs {esperado} "
                f"projetados ({restante}' restantes)",
            ))

    hh, vh, _ = _freq_recorte(gh, unidade, direcao, linha)
    ha, va, _ = _freq_recorte(ga, unidade, direcao, linha)
    if vh + va:
        pct = (hh + ha) / (vh + va)
        fatos.append((
            (pct - 0.5) * 100,
            f"frequencia historica da linha na amostra dos dois times: "
            f"{hh + ha}/{vh + va} ({pct:.0%})",
        ))

    bench_pct, bench_txt = _benchmark_pct(av, cand)
    if bench_pct is not None:
        fatos.append((
            bench_pct - 50,
            f"benchmark da liga: {bench_txt}",
        ))

    if not fatos:
        return (f"{SD} (sem recorte calculavel)", ) * 2
    fatos.sort(key=lambda p: p[0], reverse=True)
    fav = (
        fatos[0][1]
        if fatos[0][0] > 0
        else "nenhum dado isolado favorece com folga; a aprovacao vem do "
        "conjunto (probabilidade combinada pelo modelo)"
    )
    contra = (
        fatos[-1][1]
        if fatos[-1][0] < 0
        else "nenhum dado isolado contradiz com folga (pior recorte ainda "
        "e favoravel ou neutro)"
    )
    return fav, contra


def _outliers_linha(av: Avaliacao, cand: Candidato) -> list[str]:
    """Jogos da amostra que desviam 3+ da linha escolhida."""
    parsed = _parse_linha_total(av)
    if parsed is None:
        return []
    direcao, linha, unidade = parsed
    gh = cand.historico.get("games_home") or []
    ga = cand.historico.get("games_away") or []
    out: list[str] = []
    for g in list(gh) + list(ga):
        total = _total_do_jogo(g, unidade)
        if total is None:
            continue
        if direcao == "Under" and total >= linha + 2.5:
            out.append(
                f"{g.date[:10]} vs {g.opponent}: total {total:g} "
                f"(linha {linha:g}) - estourou a linha"
            )
        elif direcao == "Over" and total <= linha - 2.5:
            out.append(
                f"{g.date[:10]} vs {g.opponent}: total {total:g} "
                f"(linha {linha:g}) - muito abaixo da linha"
            )
    return out


def _bloco_contradicoes(av: Avaliacao, cand: Candidato) -> list[str]:
    snap = cand.snapshot
    out = ["[CONTRADICOES / RISCOS]"]
    fav, contra = _fatos_favor_contra(av, cand)
    out.append(f"  - dado que mais favorece: {fav}")
    out.append(f"  - dado que mais contradiz: {contra}")
    outliers = _outliers_linha(av, cand)
    if outliers:
        for o in outliers[:2]:
            out.append(f"  - outlier relevante na amostra: {o}")
    else:
        parsed = _parse_linha_total(av)
        out.append(
            "  - outliers: nenhum jogo da amostra desvia 3+ da linha"
            if parsed
            else f"  - outliers: {SD} (mercado sem linha de total)"
        )
    ver_h = snap.stats_home.get("Red Cards")
    ver_a = snap.stats_away.get("Red Cards")
    if ver_h is None and ver_a is None:
        out.append(f"  - expulsoes/alteracao estrutural: {SD} (sem dado)")
    else:
        total_v = int(ver_h or 0) + int(ver_a or 0)
        out.append(
            "  - expulsoes/alteracao estrutural: "
            + (f"{total_v} cartao(is) vermelho(s) no jogo" if total_v
               else "nenhuma expulsao nesta leitura")
        )
    n_home = cand.historico.get("n_home") or 0
    n_away = cand.historico.get("n_away") or 0
    n_min = min(n_home, n_away)
    out.append(
        "  - amostra historica: minimo "
        + (f"{n_min} jogos (PEQUENA: abaixo dos 10 por time)" if n_min < 10
           else f"{n_min} jogos por time (adequada)")
    )
    # forma recente (ultimos 5) x amostra inteira
    parsed = _parse_linha_total(av)
    if parsed:
        direcao, linha, unidade = parsed
        gh = cand.historico.get("games_home") or []
        ga = cand.historico.get("games_away") or []
        h5, v5, _ = _freq_recorte(gh[:5] + ga[:5], unidade, direcao, linha)
        ht, vt, _ = _freq_recorte(gh + ga, unidade, direcao, linha)
        if v5 and vt:
            p5, pt = h5 / v5, ht / vt
            if abs(p5 - pt) >= 0.20:
                out.append(
                    "  - forma recente x historico: DIVERGENTE - ultimos 5 "
                    f"({p5:.0%}) vs amostra inteira ({pt:.0%})"
                )
            else:
                out.append(
                    "  - forma recente x historico: consistente - ultimos 5 "
                    f"({p5:.0%}) vs amostra inteira ({pt:.0%})"
                )
        else:
            out.append(f"  - forma recente x historico: {SD} (amostra sem o dado)")
    ausentes: list[str] = []
    if not snap.stats_1h and not snap.stats_2h:
        ausentes.append("estatisticas por tempo (1o/2o)")
    if not snap.events:
        ausentes.append("eventos da partida")
    if cand.benchmark_escanteios is None:
        ausentes.append("benchmark de escanteios da competicao")
    if cand.benchmark_gols is None:
        ausentes.append("benchmark de gols da competicao")
    if cand.h2h_n == 0:
        ausentes.append("h2h com estatisticas")
    if cand.odds is None:
        ausentes.append("odd ao vivo real")
    out.append(
        "  - dados ausentes importantes: "
        + (", ".join(ausentes) if ausentes else "nenhum dado essencial ausente")
    )
    return out


# ----------------------------------------------------------------------
# [ODD]: somente odd REALMENTE ao vivo
# ----------------------------------------------------------------------
def _bloco_odd(av: Avaliacao) -> list[str]:
    """Odd REALMENTE ao vivo - somente se a fonte forneceu. Odd pre-jogo
    nunca e apresentada como live."""
    if av.odd is None:
        return [
            "odd ao vivo: " + SEM_ODD_LIVE,
            "odd pre-jogo nunca e usada como odd ao vivo (regra da plataforma)",
            f"classificacao: {av.classificacao}",
        ]
    frescor = (
        "atual (timestamp dentro da janela de frescor)"
        if av.odd.atual
        else "ANTIGA - timestamp fora da janela; nao e apresentada como "
        "preco atual"
    )
    out = [
        "odd ao vivo real:",
        f"  bookmaker: {av.odd.bookmaker}",
        f"  mercado no feed: {av.odd.mercado_feed} | linha no feed: "
        f"{av.odd.value_feed}",
        f"  odd: {av.odd.odd} (probabilidade implicita {av.odd.implied:.1%})",
        f"  timestamp da fonte (update): {av.odd.update}",
        f"  frescor: {frescor}",
    ]
    if av.odd.atual:
        out.append(
            f"  diferenca para a probabilidade estimada: {av.edge:+.1%} "
            "(probabilidade estimada - implicita; edge > 0 = estimada acima "
            "do preco)"
        )
    else:
        out.append(
            "  diferenca para a probabilidade estimada: nao calculada "
            "(timestamp fora da janela de frescor)"
        )
    out.append(f"classificacao: {av.classificacao}")
    return out


# ----------------------------------------------------------------------
# [INTERPRETACAO]: por que foi aprovada / o que pode fazer dar errado
# ----------------------------------------------------------------------
def _bloco_interpretacao(
    av: Avaliacao, cand: Candidato, fav: str, contra: str
) -> list[str]:
    out = ["[INTERPRETACAO]"]
    out.append("POR QUE FOI APROVADA:")
    ok = sum(1 for _, passed, _ in av.auditoria if passed)
    total = len(av.auditoria)
    pontos = [
        f"probabilidade estimada {av.prob:.1%} >= "
        f"{PROB_MIN_APROVAR:.0%} exigido",
        f"auditoria obrigatoria integral: {ok}/{total} checagens OK",
        f"confianca {av.confianca:.0%} >= {CONF_MIN_TOP1:.0%} exigido",
    ]
    parsed = _parse_linha_total(av)
    if parsed:
        direcao, linha, unidade = parsed
        gh = cand.historico.get("games_home") or []
        ga = cand.historico.get("games_away") or []
        hh, vh, _ = _freq_recorte(gh, unidade, direcao, linha)
        ha, va, _ = _freq_recorte(ga, unidade, direcao, linha)
        if vh + va and (hh + ha) / (vh + va) >= 0.50:
            pct = (hh + ha) / (vh + va)
            pontos.append(
                f"frequencia historica da linha na amostra: {hh + ha}/"
                f"{vh + va} ({pct:.0%})"
            )
    if not fav.startswith(SD) and "nenhum dado isolado" not in fav:
        pontos.append(fav)
    snap = cand.snapshot
    ver_h = snap.stats_home.get("Red Cards")
    ver_a = snap.stats_away.get("Red Cards")
    if not (isinstance(ver_h, int) and ver_h > 0) and not (
        isinstance(ver_a, int) and ver_a > 0
    ):
        pontos.append("sem expulsao estrutural no jogo nesta leitura")
    unicos: list[str] = []
    for p in pontos:
        if p not in unicos:
            unicos.append(p)
    for p in unicos[:5]:
        out.append(f"  - {p}")

    out.append("O QUE PODE FAZER DAR ERRADO:")
    riscos: list[str] = []
    if not contra.startswith(SD) and "nenhum dado isolado" not in contra:
        riscos.append(contra)
    riscos.extend(av.riscos or [])
    n_min = min(
        cand.historico.get("n_home") or 0, cand.historico.get("n_away") or 0
    )
    if n_min < 10:
        riscos.append(f"amostra historica pequena (minimo {n_min} jogos)")
    if (isinstance(ver_h, int) and ver_h > 0) or (
        isinstance(ver_a, int) and ver_a > 0
    ):
        riscos.append("expulsao altera a estrutura do jogo em andamento")
    if cand.odds is None:
        riscos.append(
            "sem odd ao vivo real na fonte: nao ha preco para conferir o "
            "valor (OPORTUNIDADE ESTATISTICA)"
        )
    outliers = _outliers_linha(av, cand)
    if outliers:
        riscos.append(f"outlier na amostra: {outliers[0]}")
    if not riscos:
        riscos.append(
            "mercado ao vivo: placar/estado do jogo muda a qualquer momento"
        )
    if len(riscos) == 1:
        riscos.append(
            "benchmark da liga pode nao refletir o momento atual das equipes"
        )
    for r in riscos[:4]:
        out.append(f"  - {r}")
    return out


# ----------------------------------------------------------------------
# Uma oportunidade (TOP 1 / TOP 2) - saida completa de transparencia
# ----------------------------------------------------------------------
def format_oportunidade(av: Avaliacao, cand: Candidato, titulo: str) -> list[str]:
    """Bloco TOP 1/TOP 2: TODOS os dados que sustentaram a aprovacao."""
    out = [titulo]
    out.append("[FATO - ESTADO ATUAL]")
    out.extend(_bloco_fato_live(cand))
    out.append("")
    out.append("[FATO - CONTEXTO HISTORICO]")
    out.extend(_bloco_contexto_historico(av, cand))
    out.append("")
    out.extend(_bloco_calculo(av, cand))
    out.append("")
    out.extend(_bloco_contradicoes(av, cand))
    out.append("")
    out.append("[ODD]")
    out.extend(_bloco_odd(av))
    out.append("")
    fav, contra = _fatos_favor_contra(av, cand)
    out.extend(_bloco_interpretacao(av, cand, fav, contra))
    out.append("")
    out.append("[FATO] auditoria obrigatoria (12 checagens):")
    for nome, ok, detalhe in av.auditoria:
        out.append(f"  [{'OK' if ok else 'FALHA'}] {nome}: {detalhe}")
    return out


# ----------------------------------------------------------------------
# Varredura completa ("Existe alguma oportunidade ao vivo agora?")
# ----------------------------------------------------------------------
def format_live_opportunities(varredura: Varredura) -> str:
    out: list[str] = []
    out.append(
        f"[FATO] VARREDURA AO VIVO - {varredura.hora} (America/Sao_Paulo)"
    )
    # contagens obrigatorias da TRIAGEM GLOBAL: total ao vivo; total que
    # passou pela triagem barata; total descartado e motivo; candidatos
    # enviados ao deep dive.
    out.append(
        f"jogos ao vivo na fonte: {varredura.total_ao_vivo} | "
        f"passaram pela triagem barata: {varredura.total_triados} | "
        f"com cobertura em ao menos um mercado: "
        f"{len(varredura.sondados)} | "
        f"descartados: {len(varredura.descartados)} | "
        f"enviados ao deep dive: {len(varredura.candidatos)}"
    )
    if varredura.mercados is not None:
        out.append(
            "filtro de mercados desta varredura (pedido do operador): "
            + ", ".join(varredura.mercados)
            + " (as demais familias nao foram avaliadas nesta varredura)"
        )

    out.append("")
    out.append(
        "JOGOS TRIADOS COM COBERTURA DE DADOS NA FONTE - AO MENOS UM "
        "MERCADO COM DADOS SUFICIENTES (ordenados pelo score da triagem "
        "global):"
    )
    if varredura.sondados:
        for s in varredura.sondados:
            out.append(
                f"  - {s['jogo']} | {s['competicao']} | {s['status']} "
                f"{_one(s['minuto'])}' | placar {s['placar']} | "
                f"triagem {_one(s.get('score_triagem'))} | "
                f"fixture {s['fixture_id']}"
            )
    else:
        out.append("  nenhum jogo elegivel nesta varredura")

    out.append("")
    out.append(
        "CANDIDATOS ENVIADOS AO DEEP DIVE (limite aplicado somente "
        "depois da triagem global):"
    )
    if varredura.candidatos:
        for c in varredura.candidatos:
            snap = c.snapshot
            out.append(
                f"  - {snap.home_team_name} x {snap.away_team_name} | "
                f"{snap.status} {_one(snap.elapsed)}' | "
                f"fixture {snap.fixture_id}"
            )
    else:
        out.append("  nenhum candidato aprofundado nesta varredura")

    # MATRIZ DE COBERTURA (camada de elegibilidade): veredicto por
    # competicao x mercado x modo para cada jogo aprofundado, no
    # formato COMPETICAO | MODO | MERCADO | CLASSE | STATUS | MOTIVO.
    from src.cobertura import formatar_relatorio_cobertura

    blocos = []
    for c in varredura.candidatos:
        if not c.cobertura:
            continue
        snap = c.snapshot
        blocos.append(
            f"  {snap.home_team_name} x {snap.away_team_name} "
            f"({snap.league_name}):"
        )
        for linha in formatar_relatorio_cobertura(c.cobertura)[1:]:
            blocos.append("  " + linha)
    if blocos:
        out.append("")
        out.extend(
            ["[COBERTURA] MATRIZ DE ELEGIBILIDADE DOS CANDIDATOS "
             "(COMPETICAO | MODO | MERCADO | CLASSE | STATUS | MOTIVO):"]
            + blocos
        )

    out.append("")
    out.append("CANDIDATOS DESCARTADOS (jogo | minuto | mercado | motivo):")
    if varredura.descartados:
        for d in varredura.descartados:
            minuto = f"{d.minuto}'" if d.minuto is not None else SD
            out.append(f"  - {d.jogo} | {minuto} | {d.mercado} | {d.motivo}")
    else:
        out.append("  nenhum descarte nesta varredura")

    # aprovadas de classe C (OBSERVACAO): exibidas para calibracao,
    # NUNCA registradas como recomendacao automatica
    if varredura.observacao:
        out.append("")
        out.append(
            "[COBERTURA] OBSERVACAO (classe C - sem registro, sem "
            "recomendacao automatica):"
        )
        for av in varredura.observacao:
            out.append(f"  - {av.linha} | {av.rejeicao}")

    if varredura.mensagem_nenhuma:
        out.append("")
        out.append(NENHUMA_MSG)
        out.append("")
        out.append(
            "[INTERPRETACAO] Nenhuma oportunidade atingiu o padrao de "
            "sustentacao exigido (probabilidade estimada >= 70%, "
            "confianca >= 60%, auditoria integral, e margem sobre odd real "
            "quando existir). Nao existe selecao forcada: sem sustentacao "
            "suficiente, a resposta correta e esta mensagem."
        )
        return "\n".join(out)

    # aprovadas: no maximo 2 (TOP 2 somente se tambem for forte)
    for i, av in enumerate(varredura.aprovadas, start=1):
        cand = next(
            (c for c in varredura.candidatos if c.snapshot.fixture_id == av.fixture_id),
            None,
        )
        if cand is None:
            continue
        out.append("")
        out.append("=" * 72)
        out.extend(format_oportunidade(av, cand, f"TOP {i}"))
    return "\n".join(out)


# ----------------------------------------------------------------------
# Um jogo especifico ("Analise este jogo ao vivo.")
# ----------------------------------------------------------------------
def format_live_game_opportunities(cand: Candidato) -> str:
    """Analise profunda de UM jogo: [FATO] estado + [CALCULO] linhas +
    [INTERPRETACAO] leitura das mais sustentadas (pedido explicito)."""
    out: list[str] = []
    out.append("[FATO] ANALISE AO VIVO DE UM JOGO")
    out.append("[FATO - ESTADO ATUAL]")
    out.extend(_bloco_fato_live(cand))
    out.append("")
    out.append("[FATO - CONTEXTO HISTORICO]")
    out.extend(_bloco_historico(cand))
    out.append("")
    out.append(f"odds ao vivo na fonte: {cand.odds_observacao or SEM_ODD_LIVE}")

    # MATRIZ DE COBERTURA: elegibilidade por mercado desta leitura
    if cand.cobertura:
        from src.cobertura import formatar_relatorio_cobertura

        out.append("")
        out.extend(formatar_relatorio_cobertura(cand.cobertura))

    out.append("")
    out.append(
        "[CALCULO] AVALIACOES DE LINHAS (probabilidades estimadas via "
        "Poisson sobre o tempo restante; nunca extrapolacao linear):"
    )
    if not cand.avaliacoes:
        out.append(
            "  nenhuma linha pde ser avaliada com os dados disponiveis "
            "nesta leitura"
        )
    for av in cand.avaliacoes:
        odd_txt = (
            f" | odd live real {av.odd.odd} @{av.odd.bookmaker}"
            if av.odd is not None
            else f" | {SEM_ODD_LIVE}"
        )
        out.append(
            f"  - {av.linha}: prob estimada {av.prob:.1%} | confianca "
            f"{av.confianca:.0%}{odd_txt}"
        )
        if av.sustentacao.get("taxa_combinada_por90") is not None:
            out.append(
                f"      taxa combinada {av.sustentacao.get('taxa_combinada_por90')}"
                f"/90' | peso do observado {av.sustentacao.get('peso_do_observado')}"
                f" | restam {av.sustentacao.get('tempo_restante_min')}'"
                f" | esperado no restante "
                f"{av.sustentacao.get('esperado_no_restante')}"
                f" | projecao final {av.sustentacao.get('projecao_final')}"
            )
        if av.odd is not None and av.odd.atual:
            out.append(
                f"      [CALCULO] edge sobre a implicita: {av.edge:+.1%}"
            )
        if av.sustentacao.get("convencao"):
            out.append(f"      {av.sustentacao['convencao']}")

    out.append("")
    out.append("[INTERPRETACAO] leitura (pedido explicito de analise):")
    if not cand.avaliacoes:
        out.append(
            "  sem sustentacao estatistica suficiente nesta leitura para "
            "qualquer mercado"
        )
    else:
        sustentadas = sorted(
            (a for a in cand.avaliacoes if 0.70 <= a.prob <= 0.97),
            key=lambda a: (a.confianca + a.prob, a.prob),
            reverse=True,
        )
        if not sustentadas:
            out.append(
                "  nenhuma linha atinge o padrao de sustentacao "
                "(probabilidade 70-97% com confianca adequada): "
                + NENHUMA_MSG
            )
        else:
            out.append(
                f"  linhas mais sustentadas deste jogo (de {len(sustentadas)} "
                "no intervalo 70-97%):"
            )
            for av in sustentadas[:3]:
                out.append(
                    f"  - {av.linha}: prob {av.prob:.1%}, confianca "
                    f"{av.confianca:.0%}"
                )
            out.append(
                "  observacao: ranking por sustentacao (confianca + "
                "probabilidade), nunca apenas pela frequencia historica; "
                "aprovacao formal exige auditoria integral e, com odd real, "
                "margem minima sobre a implicita"
            )
    return "\n".join(out)