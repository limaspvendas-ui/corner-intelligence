"""CLI do Corner Intelligence - ponto de entrada para o Claude Code.

Uso:
    python -m src.app hoje [--liga "Serie A"]
    python -m src.app time "Flamengo" [--n 20]
    python -m src.app ultimos20 "Flamengo"
    python -m src.app comparar "Flamengo" "Palmeiras"
    python -m src.app h2h "Flamengo" "Palmeiras" [--n 10]
    python -m src.app jogo "Flamengo x Palmeiras"
    python -m src.app liga "Serie A"
    python -m src.app prejogo "Flamengo x Palmeiras"
    python -m src.app aovivo "Flamengo x Palmeiras"   (ou ID da partida)
    python -m src.app aovivos                            (ETAPA 2: lista os jogos ao vivo)
    python -m src.app aovivodados "Flamengo x Palmeiras" [--atualizar]  (ETAPA 2)
    python -m src.app aovivoop                            (ETAPA 2.2: ha oportunidade ao vivo agora?)
    python -m src.app aovivoanalise "Flamengo x Palmeiras"  (ETAPA 2.2)
    python -m src.app aovivocoleta "Flamengo x Palmeiras" [--intervalo 60] [--max-iter N]  (ETAPA 3: serie temporal imutavel)
    python -m src.app aovivopressao "Flamengo x Palmeiras"  (ETAPA 3: pressao 5/10/15 min, EXPERIMENTAL)
    python -m src.app recomendacoes [--data DD/MM/YYYY] [--fixture ID] [--tipo live|prejogo]
    python -m src.app recomendacao ID
    python -m src.app recomendacaoresultado ID --resultado GANHA --placar-final "2-1" [--stats JSON] [--nota "..."]
    python -m src.app recomendacoesliquidar   (MODO OBSERVACAO: liquida pendentes via API)
    python -m src.app prejogoop "Time A x Time B"   (MODO OBSERVACAO: recomendacao pre-jogo)
    python -m src.app calibracao   (MODO OBSERVACAO: taxa real x prob estimada)
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from src.analysis import (
    analyze_h2h,
    analyze_team,
    analyze_today,
    compare_teams,
    live_analysis,
    pre_match_analysis,
)
from src.exceptions import CornerIntelligenceError, UserFacingError
from src.logging_config import get_logger
from src.report import (
    format_compare,
    format_games_table,
    format_h2h,
    format_league,
    format_live,
    format_metrics,
    format_pre_match,
    format_team_analysis,
    format_today,
)
from src.resolver import resolve_match_teams

# Garante saida UTF-8 no console do Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def cmd_hoje(args: argparse.Namespace) -> str:
    return format_today(analyze_today(args.client, league_name=args.liga))


def cmd_time(args: argparse.Namespace) -> str:
    result = analyze_team(args.client, args.nome, last=args.n)
    table = format_games_table(result["jogos"])
    text = (
        f"{format_team_analysis(result, analise=args.analise)}\n\n{table}"
    )
    if args.metricas:
        text += f"\n\n{format_metrics(result)}"
    return text


def cmd_ultimos20(args: argparse.Namespace) -> str:
    result = analyze_team(args.client, args.nome, last=20)
    return format_games_table(result["jogos"])


def cmd_comparar(args: argparse.Namespace) -> str:
    return format_compare(
        compare_teams(args.client, args.a, args.b, last=args.n),
        analise=args.analise,
    )


def cmd_h2h(args: argparse.Namespace) -> str:
    return format_h2h(
        analyze_h2h(args.client, args.a, args.b, last=args.n),
        analise=args.analise,
    )


def cmd_jogo(args: argparse.Namespace) -> str:
    """Ultimo confronto direto entre os dois times (com detalhes completos)."""
    team_a, team_b = resolve_match_teams(args.client, args.espec)
    return format_h2h(
        analyze_h2h(args.client, team_a.name, team_b.name, last=1),
        analise=args.analise,
    )


def cmd_liga(args: argparse.Namespace) -> str:
    """Media da liga: competicao inteira na temporada (regra de integridade)."""
    from src.analysis import analyze_league

    return format_league(
        analyze_league(args.client, args.nome, country=args.pais)
    )


def cmd_prejogo(args: argparse.Namespace) -> str:
    team_a, team_b = resolve_match_teams(args.client, args.espec)
    return format_pre_match(
        pre_match_analysis(args.client, team_a.name, team_b.name),
        analise=args.analise,
    )


def cmd_aovivo(args: argparse.Namespace) -> str:
    return format_live(
        live_analysis(args.client, args.espec, analise=args.analise)
    )


def cmd_aovivos(args: argparse.Namespace) -> str:
    """ETAPA 2: lista os jogos REALMENTE ao vivo agora."""
    from src.live import list_live_games
    from src.live_report import format_live_list

    return format_live_list(list_live_games(args.client))


def cmd_aovivodados(args: argparse.Namespace) -> str:
    """ETAPA 2: dados completos ao vivo de um jogo (stats + eventos +
    tempos). --atualizar forca refresh na API e mostra as mudancas
    desde a leitura anterior. Apenas [FATO] e [CALCULO]."""
    from src.live import (
        fetch_live_snapshot,
        find_live_fixture_id,
        update_live_snapshot,
    )
    from src.live_report import format_live_snapshot

    fixture_id = find_live_fixture_id(args.client, args.espec)
    if args.atualizar:
        snapshot, _old, changes = update_live_snapshot(args.client, fixture_id)
        return format_live_snapshot(snapshot, changes=changes)
    # leitura normal: grava como ultima leitura (base do proximo "atualize")
    snapshot = fetch_live_snapshot(args.client, fixture_id, record=True)
    return format_live_snapshot(snapshot)


def cmd_aovivoop(args: argparse.Namespace) -> str:
    """ETAPA 2.2: "Existe alguma oportunidade de aposta ao vivo agora?"

    TRIAGEM GLOBAL: TODOS os jogos ao vivo elegiveis sao avaliados
    superficialmente (snapshot barato); o limite de deep dive so e
    aplicado depois, sobre o score da triagem (nunca pelo minuto).
    Auditoria integral e no maximo 1-2 aprovadas. Nao forcamos
    selecao: sem sustentacao => NENHUMA OPORTUNIDADE AO VIVO
    APROVADA AGORA.

    --mercado restringe QUAIS familias sao avaliadas no deep dive
    (ex.: somente escanteios). O filtro NAO altera nenhum calculo: a
    triagem global continua avaliando TODOS os jogos elegiveis.

    Toda APROVADA e congelada no REGISTRO DE VALIDACAO (SQLite
    permanente) para conferencia posterior do resultado. Uma falha
    de registro nunca quebra a varredura (vai para o log tecnico).
    """
    from src.live_opportunity import scan_live_opportunities
    from src.live_opportunity_report import format_live_opportunities

    mercados = None if args.mercado == "todos" else (args.mercado,)
    varredura = scan_live_opportunities(args.client, mercados=mercados)

    # POLITICA PERMANENTE (src/policy.py): aprovadas de competicoes
    # fora do universo forte NAO sao registradas nem exibidas como
    # aprovadas. Os calculos do motor nao mudam; e um filtro de
    # universo aplicado ao resultado.
    from src.policy import classificar_liga

    excluidas = []
    mantidas = []
    for av in varredura.aprovadas:
        cand = next(
            (c for c in varredura.candidatos
             if c.snapshot.fixture_id == av.fixture_id),
            None,
        )
        if cand is None:
            mantidas.append(av)
            continue
        classe, _motivo = classificar_liga(
            cand.snapshot.league_id, cand.snapshot.league_name
        )
        if classe == "EXCLUIDA":
            excluidas.append((cand.snapshot.league_name, av))
        else:
            mantidas.append(av)
    varredura.aprovadas = mantidas

    _registrar_aprovadas(varredura)
    texto = format_live_opportunities(varredura)
    if excluidas:
        texto += (
            "\n[POLITICA] excluidas pelo universo forte (sem registro): "
            + "; ".join(
                f"{liga}: {av.linha}"
                for liga, av in excluidas)
        )
    return texto


def _registrar_aprovadas(varredura) -> None:
    """Congela as aprovadas no registro permanente (validacao futura).

    Dedupe interno: a mesma recomendacao reescaneada nao duplica registro.
    Falha de registro NUNCA quebra a varredura: fica no log tecnico.
    """
    from src.logging_config import get_logger
    from src.registry import RegistroRecomendacoes

    for av in varredura.aprovadas:
        cand = next(
            (c for c in varredura.candidatos
             if c.snapshot.fixture_id == av.fixture_id),
            None,
        )
        if cand is None:
            get_logger().error(
                "Registro de validacao: aprovada do fixture %s sem candidato",
                av.fixture_id,
            )
            continue
        try:
            RegistroRecomendacoes().registrar_de_avaliacao(av, cand)
        except Exception:
            get_logger().exception(
                "Registro de validacao falhou para o fixture %s",
                av.fixture_id,
            )


def cmd_recomendacoes(args: argparse.Namespace) -> str:
    """REGISTRO: "Quais apostas a plataforma aprovou hoje?" /
    "Quantas ganharam? Quantas perderam?" - listagem com contagens."""
    from src.registry import RegistroRecomendacoes
    from src.registry_report import format_recomendacoes

    reg = RegistroRecomendacoes()
    recs = reg.listar(
        data=args.data, fixture_id=args.fixture, tipo=args.tipo
    )
    return format_recomendacoes(
        recs, data=args.data, fixture_id=args.fixture, tipo=args.tipo
    )


def cmd_recomendacao(args: argparse.Namespace) -> str:
    """REGISTRO: detalhe de UMA recomendacao ("Qual era a probabilidade
    estimada quando essa aposta foi indicada?")."""
    from src.registry import RegistroRecomendacoes
    from src.registry_report import format_recomendacao

    return format_recomendacao(RegistroRecomendacoes().obter(args.id))


def cmd_recomendacaoresultado(args: argparse.Namespace) -> str:
    """REGISTRO: liquidacao posterior (GANHA/PERDIDA/DEVOLVIDA/...).
    Atualiza SOMENTE os campos de resultado; a previsao original e
    imutavel (trigger do SQLite garante em nivel de banco)."""
    from src.registry import RegistroRecomendacoes
    from src.registry_report import format_resultado_registrado

    reg = RegistroRecomendacoes()
    rec = reg.registrar_resultado(
        args.id,
        resultado_mercado=args.resultado,
        placar_final=args.placar_final,
        stats_finais=args.stats,
        nota=args.nota,
    )
    return format_resultado_registrado(rec)


def cmd_recomendacoesliquidar(args: argparse.Namespace) -> str:
    """MODO OBSERVACAO: liquidacao automatica das recomendacoes sem
    resultado, SOMENTE quando ha resultado final real validado na API
    (status FT/AET/PEN). Dado ausente => NAO AVALIAVEL, nunca zero; a
    previsao original permanece imutavel."""
    from src.registry import RegistroRecomendacoes
    from src.settlement import format_liquidacao, liquidar_pendentes

    report = liquidar_pendentes(args.client, RegistroRecomendacoes())
    return format_liquidacao(report)


def cmd_prejogoop(args: argparse.Namespace) -> str:
    """MODO OBSERVACAO: recomendacao pre-jogo de UM confronto. Avalia
    linhas ANTES do jogo com a MESMA disciplina do live (janela de
    probabilidade + confianca) e congela as aprovadas no registro
    permanente, no momento da producao - antes de qualquer resultado.

    --politica aplica a POLITICA PERMANENTE DE UNIVERSO E SELECAO
    (src/policy.py): exige competicao do universo forte e registra
    somente a MELHOR oportunidade do jogo (comparacao de TODOS os
    mercados - gols, escanteios, TOTAL de cartoes (validados) E a
    familia resultado: 1X2/Dupla Chance/DNB/AH (bloco EXPERIMENTAL EM
    OBSERVACAO, aguarda validacao estatistica) - em igualdade, sem
    preferencia por familia; sem evidencia forte => REPROVADO).
    Nenhum calculo validado muda.

    MATRIZ DE COBERTURA (08/09/2026): a elegibilidade por mercado
    (src/cobertura.py, competicao x mercado x modo PRE_GAME) e
    consultada ANTES da selecao; somente mercado PERMITIDO (classe
    A/B com dados confirmados no fixture) concorre a recomendacao.
    Classe C (OBSERVACAO) fica em observacao, classe D/E
    (BLOQUEADO) sai do fluxo. Probabilidades, confianca, benchmarks
    e thresholds NAO mudam."""
    from src.prejogo_opportunity import scan_pregame_opportunities
    from src.prejogo_opportunity_report import format_pregame_opportunities

    varredura = scan_pregame_opportunities(
        args.client, args.espec, registrar=not args.politica,
        incluir_resultado=args.politica,
        incluir_cartoes=args.politica,
    )

    if not args.politica:
        return format_pregame_opportunities(varredura)

    # ---- POLITICA PERMANENTE: universo forte + melhor oportunidade ----
    from src.policy import (
        LIGAS_PRIORITARIAS,
        selecionar_melhor,
    )
    from src.prejogo_opportunity import (
        VERSAO_PREJOGO_OP,
        registrar_aprovadas_prejogo,
    )

    out = [f"[POLITICA] versao do universo: ligas fortes "
           f"({len(LIGAS_PRIORITARIAS)} prioritarias)"]

    if varredura.motivo_sem_jogo:
        return "\n".join(
            out + [format_pregame_opportunities(varredura)])
    fixture = varredura.fixture
    from src.policy import classificar_liga, jogo_elegivel

    classe, motivo_liga = classificar_liga(
        fixture.league_id, fixture.league_name)
    elegivel, motivo = jogo_elegivel(
        fixture.league_id, fixture.league_name,
        fixture.home_team_name, fixture.away_team_name, dados="SIM",
    )
    out.append(f"[FATO] competicao: {fixture.league_name} "
               f"(id {fixture.league_id}) => {classe} ({motivo_liga})")
    if not elegivel:
        out.append(f"[POLITICA] JOGO INELEGIVEL: {motivo}")
        return "\n".join(out)

    # ---- MATRIZ DE COBERTURA: elegibilidade por mercado ANTES da ----
    # ---- selecao (ETAPA 9: matriz -> validacao -> suficiencia ->     ----
    # ---- probabilidade/confianca). Somente mercado PERMITIDO (A/B   ----
    # ---- com dados confirmados) concorre a recomendacao; classe C   ----
    # ---- (OBSERVACAO) fica em observacao, D/E (BLOQUEADO) ja saiu   ----
    # ---- do scan. Nenhum calculo de probabilidade/confianca muda.   ----
    from src.cobertura import (
        STATUS_PERMITIDO,
        formatar_relatorio_cobertura,
        status_da_familia,
    )

    if varredura.cobertura:
        out.extend(formatar_relatorio_cobertura(varredura.cobertura))
    permitidas = [
        av for av in varredura.avaliacoes
        if status_da_familia(varredura.cobertura, av.mercado)
        == STATUS_PERMITIDO
    ]
    bloqueadas = varredura.bloqueadas_cobertura
    if bloqueadas:
        out.append(
            f"[COBERTURA] mercados bloqueados fora da analise: "
            f"{len(bloqueadas)} linhas (classe D/E ou dado essencial "
            f"ausente - ausencia nunca vira zero)"
        )
    if not permitidas:
        out.append(
            "[COBERTURA] JOGO REPROVADO: nenhum mercado com cobertura "
            "PERMITIDA (classe A/B com dados confirmados) para este "
            "confronto; os demais estao bloqueados ou em observacao"
        )
        return "\n".join(out)

    selecao = selecionar_melhor(
        permitidas, varredura.historico,
        varredura.benchmark_escanteios, varredura.benchmark_gols,
        varredura.benchmark_cartoes,
    )
    if selecao.reprovacao:
        out.append(f"[POLITICA] JOGO REPROVADO: {selecao.reprovacao}")
        return "\n".join(out)

    melhor = selecao.melhor
    from src.registry import RegistroRecomendacoes
    from src.politica_operacional import odd_justa_avaliacao

    sel_op = getattr(selecao, "operacional", None)
    u_op = getattr(sel_op, "util_operacional", None) if sel_op else None
    oj_melhor = odd_justa_avaliacao(melhor.avaliacao)
    oj_txt = (
        f"{oj_melhor:.2f}" if oj_melhor is not None
        else "NAO CALCULAVEL - DADOS INSUFICIENTES"
    )
    om_txt = ""
    if u_op is not None and u_op.odd_minima is not None:
        om_txt = f" | odd minima aceitavel {u_op.odd_minima:.2f}"

    registros = registrar_aprovadas_prejogo(
        RegistroRecomendacoes(), [melhor.avaliacao], fixture,
        varredura.historico,
        # v3.0: comparador passou a incluir TOTAL DE CARTOES validado
        # (amarelo=1, vermelho=2) competindo com gols/escanteios e a
        # familia resultado (1X2/DC/DNB/AH) - em igualdade.
        # v3.1 (08/09/2026): POLITICA OPERACIONAL - a linha principal e
        # a melhor linha OPERACIONAL (piso de odd efetiva 1.15, odd real
        # quando a fonte fornece; senao odd justa informativa). Nenhum
        # calculo de probabilidade/confianca/benchmark muda.
        # v3.2 (08/09/2026): CORRECAO DA ORDEM - entre as utilizaveis a
        # prioridade e obrigatoria (probabilidade > confianca > amostra
        # > estabilidade > seguranca); o score de politica e consultado
        # apenas nos desempates e nunca escolhe linha de probabilidade
        # menor.
        versao="prejogo-politica-3.2-observacao",
    )
    out.append(
        f"[POLITICA] MELHOR OPORTUNIDADE DO JOGO (1 por jogo): "
        f"{melhor.avaliacao.linha} | prob {melhor.avaliacao.prob:.2%} | "
        f"confianca {melhor.avaliacao.confianca:.2f} | odd justa "
        f"{oj_txt}{om_txt} | score de politica "
        f"{melhor.score} | congelada no registro: {registros}"
    )
    if sel_op is not None and getattr(sel_op, "motivo_operacional", None):
        out.append(
            f"[POLITICA-OPERACIONAL] motivo da linha principal: "
            f"{sel_op.motivo_operacional}"
        )
    out.append(
        f"[CALCULO] comparacao de mercados: {len(selecao.comparadas)} "
        "linhas disciplinadas; componentes da vencedora: utilidade_prob "
        f"{melhor.utilidade_prob} | utilidade_largura "
        f"{melhor.utilidade_largura} | estabilidade {melhor.estabilidade} "
        f"| aderencia {melhor.aderencia} | cobertura {melhor.cobertura} "
        f"| contracoes {melhor.contracoes}"
    )
    _ = VERSAO_PREJOGO_OP  # legado: registro da politica usa versao propria
    return "\n".join(out)


def cmd_calibracao(args: argparse.Namespace) -> str:
    """MODO OBSERVACAO: relatorio de calibracao do registro permanente
    (taxa real de acerto x probabilidade estimada; por confianca,
    mercado, tipo e faixa de minuto). Amostras pequenas sao marcadas
    como SEM evidencia - nenhuma conclusao e forcada."""
    from src.calibration import format_calibracao, relatorio_calibracao
    from src.registry import RegistroRecomendacoes

    return format_calibracao(
        relatorio_calibracao(RegistroRecomendacoes())
    )


def cmd_aovivoanalise(args: argparse.Namespace) -> str:
    """ETAPA 2.2: analise profunda de UM jogo ao vivo ("Analise este
    jogo ao vivo"). [FATO] + [CALCULO] + [INTERPRETACAO] sob pedido
    explicito; nenhuma aprovacao forcada."""
    from src.live_opportunity import analyze_live_game_opportunities
    from src.live_opportunity_report import format_live_game_opportunities

    candidato = analyze_live_game_opportunities(args.client, args.espec)
    return format_live_game_opportunities(candidato)


def cmd_aovivocoleta(args: argparse.Namespace) -> str:
    """ETAPA 3: coleta automatica de snapshots LIVE a cada ~60s ate a
    partida encerrar ou o operador interromper (Ctrl+C). Cada coleta e
    um fato temporal imutavel (serie temporal). Apenas [FATO]/[CALCULO];
    sem interpretacao. Nao cria servidor web; respeita a CLI atual."""
    from src.live import find_live_fixture_id
    from src.live_pressure import collect_live_series

    fixture_id = find_live_fixture_id(args.client, args.espec)
    res = collect_live_series(
        args.client, fixture_id, interval=args.intervalo,
        max_iterations=args.max_iter,
    )
    motivo_txt = {
        "partida_encerrada": "partida encerrada (status FT/AET/PEN)",
        "interrompido_operador": "interrompido pelo operador (Ctrl+C)",
        "limite_iteracoes": "limite de iteracoes alcancado",
    }.get(res["motivo"], res["motivo"])
    return (
        f"[FATO] coleta temporal encerrada: {res['coletas']} snapshot(s) "
        f"gravados para o fixture {res['fixture_id']} "
        f"(intervalo {res['intervalo']}s). Motivo: {motivo_txt}. "
        "Serie imutavel preservada no historico "
        "(live_snapshot_history)."
    )


def cmd_aovivopressao(args: argparse.Namespace) -> str:
    """ETAPA 3: consulta as janelas de PRESSAO 5/10/15 min do historico
    temporal do fixture. Dados objetivos (deltas, taxas, direcao) +
    status EXPERIMENTAL / A CALIBRAR. Sem threshold BAIXA/MODERADA/ALTA,
    sem palpite, sem forcar aposta."""
    from src.live import LiveSnapshotStore, find_live_fixture_id
    from src.live_pressure import format_pressure_windows, query_pressure

    fixture_id = find_live_fixture_id(args.client, args.espec)
    windows = query_pressure(fixture_id)
    snap = LiveSnapshotStore().get(fixture_id)
    return format_pressure_windows(windows, snap=snap)


def cmd_oddscoleta(args: argparse.Namespace) -> str:
    """ETAPA 5F: coleta PROSPECTIVA de odds reais (pre-match e live),
    SEPARADA do motor. NAO altera probabilidade/aprovacao/thresholds/regra
    alguma. NAO calcula ROI. Persiste cotacoes factuais timestampadas em
    odds_snapshot_history (append-only, nunca sobrescreve).

    Subcomandos:
      status  -- estado do odds_snapshot_history (append-only).
      cache   -- ingere as odds JA existentes no api_cache (0 chamadas a API).
      coleta  -- coleta UM fixture na API (/odds; +/odds/live com --live).
      multiprovider -- coleta prospectiva 5Dollar/The Odds API (Etapa 5F-E2).

    AUTO DESABILITADO por padrao: a coleta periodica exige --intervalo > 0
    e --max-iter; sem eles e one-shot. Mercado irreconhecivel => UNMAPPED;
    odd invalida => registrada com motivo, nunca zero."""
    from src.odds_coleta import (
        OddsSnapshotStore,
        coletar_fixture,
        coletar_periodico,
        coletar_multiprovider,
        format_status,
        ingerir_cache,
    )

    store = OddsSnapshotStore()
    sub = args.subcmd

    if sub == "status":
        return format_status(store.status())

    if sub == "cache":
        res = ingerir_cache(store, dry_run=args.dry_run)
        modo = "DRY-RUN (nao gravado)" if args.dry_run else "GRAVADO"
        return (
            f"[FATO] ingestao do api_cache para odds_snapshot_history ({modo}).\n"
            f"  entries /odds pre-match: {res['pre_entries']}\n"
            f"  entries /odds/live: {res['live_entries']}\n"
            f"  snapshots construidos: {res['snapshots']}\n"
            f"  inseridos: {res['inseridos']} | duplicados (dedup hash): "
            f"{res['duplicados']}\n"
            f"  consumo de API: {res['consumo_api']} chamadas (0 - so cache).\n"
            "[CALCULO] ROI: NAO CALCULADO. Etapa 6: BLOQUEADA."
        )

    if sub == "coleta":
        fx = args.fixture
        if args.intervalo and args.intervalo > 0 and args.max_iter:
            r = coletar_periodico(
                args.client, store, [fx],
                intervalo=args.intervalo, max_iter=args.max_iter, live=args.live,
            )
            return (
                f"[FATO] coleta periodica do fixture {fx} encerrada: "
                f"{r['passadas']} passada(s) | {r['inseridos']} inseridos | "
                f"{r['duplicados']} duplicados | consumo API "
                f"{r['consumo_api']} chamadas. Historico append-only "
                f"preservado (odds_snapshot_history)."
            )
        r = coletar_fixture(args.client, store, fx, live=args.live)
        return (
            f"[FATO] coleta one-shot do fixture {fx}: "
            f"{r['snapshots']} snapshots | {r['inseridos']} inseridos | "
            f"{r['duplicados']} duplicados | consumo API "
            f"{r['consumo_api']} chamada(s). Historico append-only "
            f"preservado (odds_snapshot_history)."
        )

    if sub == "multiprovider":
        # Etapa 5F-E2: coleta prospectiva 5Dollar / The Odds API.
        # Respeita HARD_LIMIT do adapter; 402/403/429 => STOP (nunca compra).
        # Nao altera motor de decisao.
        prov = args.provider
        r = coletar_multiprovider(
            prov, store,
            fixture_id_int=getattr(args, "fixture", None),
            sport_key=getattr(args, "sport_key", "soccer_epl"),
            market=getattr(args, "market", "corner"),
        )
        if r.get("erro"):
            return (
                f"[FATO] coleta multiprovider '{prov}' interrompida: "
                f"{r.get('limite') or 'erro'} -> {r['erro']}. "
                f"Plano/limite respeitado (sem compra, sem re-tenta). "
                f"Historico append-only preservado."
            )
        return (
            f"[FATO] coleta prospectiva multiprovider '{prov}': "
            f"{r['snapshots']} snapshots | {r['inseridos']} inseridos | "
            f"{r['duplicados']} duplicados | consumo API "
            f"{r['consumo_api']} chamada(s).\n"
            f"  por market_canonical: {r.get('por_market_canonical', {})}\n"
            f"  por phase: {r.get('por_phase', {})}\n"
            f"  por bookmaker: {r.get('por_bookmaker', {})}\n"
            f"  Historico append-only preservado (odds_snapshot_history). "
            f"[CALCULO] Etapa 6: BLOQUEADA."
        )

    return "Subcomando desconhecido. Use: status, cache, coleta ou multiprovider."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corner-intelligence",
        description="Analise de escanteios via API-Football Pro",
    )
    parser.add_argument("--sem-cache", action="store_true",
                        help="ignora cache (consulta forcada na API)")

    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("hoje", help="jogos de hoje")
    p.add_argument("--liga", default=None, help="filtrar por liga (nome)")
    p.set_defaults(fn=cmd_hoje)

    p = sub.add_parser("time", help="perfil de escanteios de um time")
    p.add_argument("nome")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--analise", action="store_true",
                   help="inclui tendencia (interpretacao, sob pedido)")
    p.add_argument("--metricas", action="store_true",
                   help="painel objetivo de todas as metricas "
                        "(gols, escanteios, chutes, cartoes, posse, faltas, "
                        "impedimentos)")
    p.set_defaults(fn=cmd_time)

    p = sub.add_parser("ultimos20", help="ultimos 20 jogos com escanteios")
    p.add_argument("nome")
    p.set_defaults(fn=cmd_ultimos20)

    p = sub.add_parser("comparar", help="historicos individuais lado a lado")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--analise", action="store_true",
                   help="inclui tendencia e xCorners (sob pedido)")
    p.set_defaults(fn=cmd_comparar)

    p = sub.add_parser("h2h", help="confronto direto (partidas ENTRE os dois times)")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--analise", action="store_true",
                   help="inclui tendencia (sob pedido)")
    p.set_defaults(fn=cmd_h2h)

    p = sub.add_parser("jogo", help="ultimo confronto direto detalhado")
    p.add_argument("espec", help='"Time A x Time B"')
    p.add_argument("--analise", action="store_true",
                   help="inclui tendencia (sob pedido)")
    p.set_defaults(fn=cmd_jogo)

    p = sub.add_parser(
        "liga",
        help="media de escanteios da liga (competicao inteira na temporada)",
    )
    p.add_argument("nome")
    p.add_argument("--pais", default=None,
                   help="desambigua ligas com mesmo nome (ex.: --pais Brazil)")
    p.set_defaults(fn=cmd_liga)

    p = sub.add_parser("prejogo", help="dados pre-jogo (sem interpretacao)")
    p.add_argument("espec", help='"Time A x Time B"')
    p.add_argument("--analise", action="store_true",
                   help="inclui tendencia e xCorners (sob pedido)")
    p.set_defaults(fn=cmd_prejogo)

    p = sub.add_parser("aovivo", help="dados ao vivo (sem interpretacao)")
    p.add_argument("espec", help='"Time A x Time B" ou ID da partida')
    p.add_argument("--analise", action="store_true",
                   help="inclui projecao de ritmo (estimativa, sob pedido)")
    p.set_defaults(fn=cmd_aovivo)

    p = sub.add_parser(
        "aovivos",
        help="ETAPA 2: lista os jogos ao vivo agora (status reais da API)",
    )
    p.set_defaults(fn=cmd_aovivos)

    p = sub.add_parser(
        "aovivodados",
        help="ETAPA 2: dados completos ao vivo de um jogo "
             "(placar, estatisticas, eventos, 1oT/2oT)",
    )
    p.add_argument("espec", help='"Time A x Time B" ou ID da partida')
    p.add_argument("--atualizar", action="store_true",
                   help="consulta forcada na API e mostra o que mudou "
                        "desde a leitura anterior")
    p.set_defaults(fn=cmd_aovivodados)

    p = sub.add_parser(
        "aovivoop",
        help="ETAPA 2.2: existe alguma oportunidade de aposta ao vivo "
             "agora? (triagem global de TODOS os elegiveis, deep dive "
             "limitado a 2, auditoria e no maximo 1-2 aprovadas)",
    )
    p.add_argument(
        "--mercado", default="todos",
        choices=["todos", "escanteios", "gols", "cartoes", "resultado"],
        help="restringe as familias avaliadas no deep dive (a triagem "
             "global continua avaliando TODOS os jogos elegiveis)",
    )
    p.set_defaults(fn=cmd_aovivoop)

    p = sub.add_parser(
        "aovivoanalise",
        help="ETAPA 2.2: analise profunda de UM jogo ao vivo "
             "(escanteios, gols, cartoes e mercados de resultado "
             "1X2/DC/DNB/AH com odd live real quando disponivel; "
             "[FATO]/[CALCULO]/[INTERPRETACAO])",
    )
    p.add_argument("espec", help='"Time A x Time B" ou ID da partida')
    p.set_defaults(fn=cmd_aovivoanalise)

    p = sub.add_parser(
        "aovivocoleta",
        help="ETAPA 3: coleta automatica de snapshots LIVE a cada ~60s "
             "(serie temporal imutavel). Ctrl+C interrompe sem corromper "
             "o banco; para sozinho quando a partida encerra.",
    )
    p.add_argument("espec", help='"Time A x Time B" ou ID da partida')
    p.add_argument("--intervalo", type=int, default=60,
                   help="intervalo entre coletas em segundos (padrao 60)")
    p.add_argument("--max-iter", type=int, default=None,
                   help="limite de coletas (producao: sem limite; testes "
                        "usam valor baixo para nao esperar 60s reais)")
    p.set_defaults(fn=cmd_aovivocoleta)

    p = sub.add_parser(
        "aovivopressao",
        help="ETAPA 3: janelas de pressao temporal 5/10/15 min "
             "(EXPERIMENTAL / A CALIBRAR) do historico do fixture",
    )
    p.add_argument("espec", help='"Time A x Time B" ou ID da partida')
    p.set_defaults(fn=cmd_aovivopressao)

    p = sub.add_parser(
        "recomendacoes",
        help="REGISTRO DE VALIDACAO: recomendacoes aprovadas (filtros por "
             "data, fixture e tipo) com contagem de resultados",
    )
    p.add_argument("--data", default=None,
                   help="data da recomendacao (DD/MM/YYYY ou YYYY-MM-DD)")
    p.add_argument("--fixture", type=int, default=None,
                   help="filtrar por fixture_id")
    p.add_argument("--tipo", default=None, choices=["live", "prejogo"],
                   help="somente ao vivo ou pre-jogo")
    p.set_defaults(fn=cmd_recomendacoes)

    p = sub.add_parser(
        "recomendacao",
        help="REGISTRO DE VALIDACAO: detalhe completo de UMA recomendacao "
             "aprovada (probabilidade estimada no momento, dados, auditoria)",
    )
    p.add_argument("id", type=int, help="id interno do registro")
    p.set_defaults(fn=cmd_recomendacao)

    p = sub.add_parser(
        "recomendacaoresultado",
        help="REGISTRO DE VALIDACAO: registra a liquidacao depois do jogo "
             "(GANHA/PERDIDA/DEVOLVIDA/MEIA VITÓRIA/MEIA DERROTA/"
             "NÃO AVALIÁVEL). A previsao original nunca e alterada.",
    )
    p.add_argument("id", type=int, help="id interno do registro")
    p.add_argument("--resultado", required=True,
                   help="GANHA, PERDIDA, DEVOLVIDA, MEIA VITÓRIA, "
                        "MEIA DERROTA ou NÃO AVALIÁVEL")
    p.add_argument("--placar-final", default=None, help='ex.: "2-1"')
    p.add_argument("--stats", default=None,
                   help="estatisticas finais relevantes (texto/JSON)")
    p.add_argument("--nota", default=None,
                   help="nota de auditoria (correcao/comentario posterior)")
    p.set_defaults(fn=cmd_recomendacaoresultado)

    p = sub.add_parser(
        "recomendacoesliquidar",
        help="MODO OBSERVACAO: liquidacao automatica via API das "
             "recomendacoes sem resultado cujo jogo ja foi encerrado "
             "(FT/AET/PEN). Dado ausente => NAO AVALIAVEL; previsao "
             "original imutavel.",
    )
    p.set_defaults(fn=cmd_recomendacoesliquidar)

    p = sub.add_parser(
        "prejogoop",
        help="MODO OBSERVACAO: recomendacao pre-jogo de um confronto "
             "(linhas over/under avaliadas antes do jogo, mesma "
             "disciplina do live; aprovadas congeladas no registro "
             "permanente antes de qualquer resultado)",
    )
    p.add_argument("espec", help='"Time A x Time B"')
    p.add_argument("--politica", action="store_true",
                   help="aplica a politica permanente de universo forte "
                        "e registra somente a MELHOR oportunidade do "
                        "jogo (comparacao de mercados; sem evidencia "
                        "=> REPROVADO)")
    p.set_defaults(fn=cmd_prejogoop)

    p = sub.add_parser(
        "calibracao",
        help="MODO OBSERVACAO: relatorio de calibracao do registro "
             "(taxa real de acerto x probabilidade estimada, por "
             "confianca, mercado, tipo e faixa de minuto)",
    )
    p.set_defaults(fn=cmd_calibracao)

    p = sub.add_parser(
        "oddscoleta",
        help="ETAPA 5F: coleta prospectiva de odds reais (pre-match e live, "
             "append-only, separada do motor; sem ROI)",
    )
    sp = p.add_subparsers(dest="subcmd", required=True)
    sp.add_parser("status", help="estado do odds_snapshot_history (append-only)")
    p_cache = sp.add_parser(
        "cache", help="ingere as odds ja existentes no api_cache (0 chamadas API)")
    p_cache.add_argument("--dry-run", action="store_true",
                         help="mostra o que seria inserido sem gravar")
    p_col = sp.add_parser(
        "coleta", help="coleta UM fixture na API (/odds; +/odds/live com --live)")
    p_col.add_argument("fixture", type=int, help="fixture_id")
    p_col.add_argument("--live", action="store_true",
                       help="coleta tambem /odds/live (separado de pre-match)")
    p_col.add_argument("--intervalo", type=int, default=0,
                       help="intervalo entre passadas em segundos (periodica; "
                            "padrao 0 = one-shot)")
    p_col.add_argument("--max-iter", type=int, default=None,
                       help="limite de passadas (periodica; exige --intervalo>0)")
    # Etapa 5F-E2: coleta prospectiva multiprovider (5Dollar + The Odds API)
    p_mp = sp.add_parser(
        "multiprovider",
        help="Etapa 5F-E2: coleta prospectiva 5Dollar/The Odds API "
             "(append-only, separada do motor; sem ROI)")
    p_mp.add_argument("--provider", required=True,
                      choices=["five_dollar_football", "the_odds_api"],
                      help="provider de odds a coletar")
    p_mp.add_argument("--fixture", type=int, default=None,
                      help="fixture_id interno (5Dollar exige)")
    p_mp.add_argument("--market", default="corner",
                      help="mercado 5Dollar (corner/corner_asian/goalline/"
                           "cards/cards_asian/asian/1x2/btts)")
    p_mp.add_argument("--sport-key", default="soccer_epl",
                      help="sport_key The Odds API (default soccer_epl)")
    p.set_defaults(fn=cmd_oddscoleta)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Cliente e criado aqui (nao no import) para exigir API_KEY so quando for usar
    from src.api_client import APIFootballClient
    from src.config import require_api_key

    try:
        require_api_key()
        args.client = APIFootballClient()
        if args.sem_cache:
            args.client.cache = None  # type: ignore[assignment]
            # Obs: cache None desativa leitura; gravacao e ignorada no cliente
        return _run(args)
    except UserFacingError as exc:
        print(str(exc))
        return 1
    except CornerIntelligenceError as exc:
        get_logger().exception("Erro da plataforma")
        print(f"Erro: {exc}")
        return 1
    except Exception:
        get_logger().exception("Erro inesperado")
        print(
            "Erro inesperado. Detalhes tecnicos em "
            "logs/corner_intelligence.log."
        )
        return 2


def _run(args: argparse.Namespace) -> int:
    output = args.fn(args)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())