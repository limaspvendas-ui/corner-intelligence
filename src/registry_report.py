"""Formatacao do REGISTRO DE RECOMENDACOES (camada de exibicao).

Somente [FATO] (dados do registro permanente) e [CALCULO] (contagens).
Nenhuma interpretacao ou nova analise aqui - o registro existe justamente
para congelar a previsao original e permitir conferencia posterior.
"""

from __future__ import annotations

from typing import Any

from src.live import now_brt

SD = "s/d"  # sem dado - campo ausente no registro (nunca zero)


def _one(valor: Any) -> str:
    return SD if valor is None else str(valor)


def _pct(valor: Any) -> str:
    return SD if valor is None else f"{valor:.1%}"


def _resumo(recs: list[dict[str, Any]]) -> str:
    contagem: dict[str, int] = {}
    for rec in recs:
        chave = rec.get("resultado_mercado") or "SEM RESULTADO REGISTRADO"
        contagem[chave] = contagem.get(chave, 0) + 1
    partes = [f"total: {len(recs)}"]
    for chave in ("GANHA", "PERDIDA", "DEVOLVIDA", "MEIA VITÓRIA",
                  "MEIA DERROTA", "NÃO AVALIÁVEL",
                  "SEM RESULTADO REGISTRADO"):
        if contagem.get(chave):
            partes.append(f"{chave.lower()}: {contagem[chave]}")
    return " | ".join(partes)


def format_recomendacoes(
    recs: list[dict[str, Any]],
    data: str | None = None,
    fixture_id: int | None = None,
    tipo: str | None = None,
) -> str:
    """Listagem resumida + contagens ("Quantas ganharam? Quantas
    perderam? Quais apostas a plataforma aprovou hoje?")."""
    filtros = []
    if data:
        filtros.append(f"data {data}")
    if fixture_id:
        filtros.append(f"fixture {fixture_id}")
    if tipo:
        filtros.append("ao vivo" if tipo == "live" else "pre-jogo")
    out = [
        f"[FATO] REGISTRO DE RECOMENDACOES - consultado em "
        f"{now_brt().strftime('%d/%m/%Y %H:%M:%S')} (America/Sao_Paulo)",
        "filtro: " + (" | ".join(filtros) if filtros else "nenhum (historico completo)"),
        "[CALCULO] " + _resumo(recs),
    ]
    if not recs:
        out.append("nenhuma recomendacao registrada para este filtro.")
        return "\n".join(out)
    out.append("recomendacoes aprovadas (ordem de registro):")
    for rec in recs:
        jogo = (
            f"{rec.get('mandante') or SD} x {rec.get('visitante') or SD}"
        )
        tipo_txt = "ao vivo" if rec.get("tipo") == "live" else "pre-jogo"
        momento = ""
        if rec.get("tipo") == "live":
            momento = (
                f" | aos {rec.get('minuto') if rec.get('minuto') is not None else SD}'"
                f" ({rec.get('status') or SD}), placar "
                f"{rec.get('placar') or SD} naquele momento"
            )
        odd_txt = (
            f"odd real {rec['odd']} @{rec.get('bookmaker') or SD}"
            if rec.get("odd") is not None else f"odd {SD} (sem odd real na fonte)"
        )
        resultado = rec.get("resultado_mercado") or "SEM RESULTADO REGISTRADO"
        resultado_txt = (
            f"{resultado}"
            + (f" (placar final {rec['placar_final']})"
               if rec.get("placar_final") else "")
        )
        out.append(
            f"  #{rec['id']} | {rec.get('criado_em') or SD} | {tipo_txt} | "
            f"{jogo} | fixture {rec.get('fixture_id')} | "
            f"{rec.get('competicao') or SD} | {rec.get('linha')} | "
            f"prob estimada {_pct(rec.get('probabilidade'))} | confianca "
            f"{_pct(rec.get('confianca'))} | {odd_txt}{momento} | "
            f"resultado: {resultado_txt}"
        )
    return "\n".join(out)


def format_recomendacao(rec: dict[str, Any]) -> str:
    """Detalhe completo de UMA recomendacao ("Qual era a probabilidade
    estimada quando essa aposta foi indicada?")."""
    tipo_txt = "ao vivo" if rec.get("tipo") == "live" else "pre-jogo"
    out = [
        f"[FATO] RECOMENDACAO #{rec['id']} (registro PERMANENTE - "
        "previsao original congelada, nunca alterada retroativamente)",
        f"jogo: {rec.get('mandante') or SD} x {rec.get('visitante') or SD}",
        f"competicao: {rec.get('competicao') or SD} | fixture: "
        f"{rec.get('fixture_id')}",
        f"data/hora da recomendacao: {rec.get('criado_em') or SD} "
        "(America/Sao_Paulo)",
        f"tipo: {tipo_txt} | versao/tipo da analise: "
        f"{rec.get('versao_analise') or SD}",
    ]
    if rec.get("tipo") == "live":
        out.append(
            f"momento da recomendacao: minuto {_one(rec.get('minuto'))} | "
            f"status {rec.get('status') or SD} | placar naquele momento: "
            f"{rec.get('placar') or SD}"
        )
    out.extend([
        f"mercado: {rec.get('mercado')}",
        f"linha: {rec.get('linha')}",
        f"equipe/lado: {rec.get('equipe_lado') or SD} "
        "(s/d quando o mercado e de total)",
        f"odd real no momento: {_one(rec.get('odd'))} | bookmaker: "
        f"{rec.get('bookmaker') or SD}",
        f"[CALCULO] probabilidade estimada quando indicada: "
        f"{_pct(rec.get('probabilidade'))}",
        f"[CALCULO] confianca: {_pct(rec.get('confianca'))}",
        f"tamanho da amostra: "
        f"{_one(rec.get('amostra_n'))} jogos (minimo por time)",
        f"timestamp do snapshot da API: {rec.get('snapshot_api_ts') or SD}",
    ])
    favor = rec.get("dados_favoraveis") or []
    out.append("principais dados favoraveis na aprovacao:")
    if favor:
        for item in favor:
            out.append(f"  - {item}")
    else:
        out.append(f"  {SD} (campo nao registrado nesta versao do registro)")
    contra = rec.get("contradicoes_riscos") or []
    out.append("principais contradicoes/riscos na aprovacao:")
    if contra:
        for item in contra:
            out.append(f"  - {item}")
    else:
        out.append(f"  {SD} (campo nao registrado nesta versao do registro)")

    out.append("resultado posterior (liquidacao):")
    resultado = rec.get("resultado_mercado")
    if resultado is None and not rec.get("placar_final"):
        out.append("  SEM RESULTADO REGISTRADO (jogo nao liquidado)")
    else:
        out.append(f"  resultado do mercado: {resultado or SD}")
        out.append(f"  placar final: {rec.get('placar_final') or SD}")
        stats = rec.get("stats_finais")
        if stats:
            out.append(f"  estatisticas finais: {stats}")
        out.append(
            "  registrado em: "
            f"{rec.get('resultado_registrado_em') or SD}"
        )
    auditoria = rec.get("auditoria") or []
    out.append("trilha de auditoria (correcoes/notas - previsao original intacta):")
    if auditoria:
        for nota in auditoria:
            quando = nota.get("quando") if isinstance(nota, dict) else None
            texto = (
                json_texto(nota) if isinstance(nota, dict) else str(nota)
            )
            out.append(f"  - [{quando or SD}] {texto}")
    else:
        out.append("  nenhuma nota de auditoria")
    return "\n".join(out)


def json_texto(nota: dict[str, Any]) -> str:
    """Nota de auditoria -> texto legivel."""
    partes = [str(nota.get("acao") or "nota")]
    if nota.get("resultado"):
        partes.append(f"resultado: {nota['resultado']}")
    if nota.get("valor_anterior"):
        partes.append(f"valor ANTERIOR preservado: {nota['valor_anterior']}")
    if nota.get("placar_final"):
        partes.append(f"placar final: {nota['placar_final']}")
    if nota.get("nota"):
        partes.append(f"nota: {nota['nota']}")
    return " | ".join(partes)


def format_resultado_registrado(rec: dict[str, Any]) -> str:
    """Confirmacao do registro de liquidacao."""
    return "\n".join([
        "[FATO] RESULTADO REGISTRADO (apenas liquidacao; a previsao "
        "original NAO foi alterada):",
        f"  recomendacao #{rec['id']} | {rec.get('mandante')} x "
        f"{rec.get('visitante')} | {rec.get('linha')}",
        f"  resultado: {rec.get('resultado_mercado') or SD} | placar final: "
        f"{rec.get('placar_final') or SD} | registrado em: "
        f"{rec.get('resultado_registrado_em') or SD}",
        f"  probabilidade estimada original (intacta): "
        f"{_pct(rec.get('probabilidade'))}",
    ])