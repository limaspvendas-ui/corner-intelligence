"""Exibicao da VARREDURA PRE-JOGO (modo observacao de calibracao).

Somente [FATO] (jogo, historico, benchmark) e [CALCULO] (baseline,
Poisson, contagens). As aprovadas sao FATOS do motor de observacao -
congeladas no registro ANTES do jogo para conferencia posterior.
Nenhuma interpretacao/opiniao aqui.
"""

from __future__ import annotations

from typing import Any

from src.live import now_brt

SD = "s/d"

NENHUMA_MSG = "NENHUMA OPORTUNIDADE PRE-JOGO APROVADA AGORA."
MOTIVO_PADRAO = (
    "motivo: linhas fora da janela de aprovacao (prob 70%-97% e "
    "confianca minima 60%) ou sem sustentacao basica"
)


def _one(valor: Any) -> str:
    return SD if valor is None else str(valor)


def _pct(valor: Any) -> str:
    return SD if valor is None else f"{valor:.1%}"


def format_pregame_opportunities(varredura: Any) -> str:
    out: list[str] = [
        f"[FATO] VARREDURA PRE-JOGO (MODO OBSERVACAO) - {varredura.espec} "
        f"- consultado em {now_brt().strftime('%d/%m/%Y %H:%M:%S')} "
        "(America/Sao_Paulo)",
    ]

    fx = varredura.fixture
    if fx is None:
        out.append(f"motivo: {varredura.motivo_sem_jogo}")
        out.append(NENHUMA_MSG)
        return "\n".join(out)

    out.extend([
        f"jogo: {fx.home_team_name} x {fx.away_team_name} | fixture "
        f"{fx.fixture_id} | {fx.league_name} | data {fx.date} | "
        f"status {fx.status}",
        "[FATO] registro de observacao: recomendacoes aprovadas sao "
        "congeladas ANTES do jogo (tipo pre-jogo, sem minuto/placar) e "
        "liquidadas somente depois do encerramento, validadas na API.",
    ])

    hist = varredura.historico
    out.append(
        f"historico: {_one(hist.get('n_home'))} jogos do mandante | "
        f"{_one(hist.get('n_away'))} jogos do visitante (ultimos jogos "
        "com estatistica na fonte)"
    )
    bench_esc = varredura.benchmark_escanteios or {}
    bench_gols = varredura.benchmark_gols or {}
    out.append(
        "benchmark escanteios da liga: "
        f"{_one((bench_esc.get('describe') or {}).get('media'))} "
        f"(partidas validas {_one(bench_esc.get('partidas_validas'))}) | "
        "benchmark gols da liga: "
        f"{_one((bench_gols.get('describe') or {}).get('media'))} "
        f"(partidas validas {_one(bench_gols.get('partidas_validas'))})"
    )

    out.append(
        f"[CALCULO] linhas avaliadas: {len(varredura.avaliacoes)} | "
        f"aprovadas: {len(varredura.aprovadas)} | congeladas no registro: "
        f"{len(varredura.registros)}"
    )

    # MATRIZ DE COBERTURA (camada de elegibilidade): veredicto por
    # competicao x mercado x modo PRE_GAME no formato exigido
    # (COMPETICAO | MODO | MERCADO | CLASSE | STATUS | MOTIVO) + as
    # avaliacoes retiradas do fluxo por mercado bloqueado.
    if getattr(varredura, "cobertura", None):
        from src.cobertura import formatar_relatorio_cobertura

        out.append("")
        out.extend(formatar_relatorio_cobertura(varredura.cobertura))
    if getattr(varredura, "bloqueadas_cobertura", None):
        out.append(
            f"[COBERTURA] linhas bloqueadas fora da analise: "
            f"{len(varredura.bloqueadas_cobertura)} (classe D/E ou dado "
            f"essencial ausente - ausencia nunca vira zero)"
        )

    if varredura.aprovadas:
        out.append("aprovadas (congeladas antes do jogo):")
        for av in varredura.aprovadas:
            out.append(
                f"  - {av.linha} | prob estimada {_pct(av.prob)} | "
                f"confianca {_pct(av.confianca)} | lambda/90min "
                f"{av.sustentacao.get('lambda_por90')}"
            )
    else:
        out.append(f"{NENHUMA_MSG} ({MOTIVO_PADRAO})")

    # [POLITICA-OPERACIONAL] separa a melhor previsao estatistica da
    # melhor aposta pratica (piso de odd efetiva 1.15; odd real so
    # quando a fonte fornece; senao odd justa INFORMATIVA, rotulada).
    # Nenhuma probabilidade/confianca e recalculada aqui. Ordem entre
    # as utilizaveis OBRIGATORIA (v2): probabilidade > confianca >
    # amostra > estabilidade > seguranca; o score de politica entra
    # apenas como desempate final (mesma regra do --politica).
    from src.policy import comparar_mercados
    from src.politica_operacional import (
        format_bloco_operacional,
        selecionar_linha_operacional,
    )

    comparadas = comparar_mercados(
        varredura.avaliacoes, varredura.historico,
        varredura.benchmark_escanteios, varredura.benchmark_gols,
        varredura.benchmark_cartoes,
    )
    out.extend(format_bloco_operacional(
        selecionar_linha_operacional(
            varredura.avaliacoes,
            ordem=comparadas,
        )
    ))

    if varredura.registros:
        novas = sum(1 for _, novo in varredura.registros if novo)
        duplicadas = len(varredura.registros) - novas
        parte = f"novas: {novas}"
        if duplicadas:
            parte += (
                " | ja registradas antes (dedupe, sem duplicar): "
                f"{duplicadas}"
            )
        out.append(f"REGISTRO DE VALIDACAO: {parte}")
    return "\n".join(out)