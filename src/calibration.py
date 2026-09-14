"""RELATORIO DE CALIBRACAO do REGISTRO DE VALIDACAO.

TESTE DE CALIBRACAO (modo observacao): mede, ao longo do tempo, a
relacao entre a probabilidade estimada no momento da recomendacao e a
taxa REAL de acerto - somente a partir de registros LIQUIDADOS.

Regras:
    - taxa de acerto = GANHA / (GANHA + PERDIDA). DEVOLVIDA, MEIA
      VITORIA, MEIA DERROTA e NAO AVALIAVEL sao contabilizadas e
      EXCLUIDAS da taxa (nunca tratadas como acerto ou erro);
    - probabilidade media das avaliaveis e comparada com a taxa real
      (calibracao: 85% estimado deveria acertar ~85%);
    - AMOSTRA PEQUENA e sempre explicita: celulas com menos de 30
      avaliaveis NAO sao evidencia de calibracao (nem a favor, nem
      contra). O resultado inicial 6/6 nao e evidencia suficiente.

Somente [CALCULO] sobre dados do registro - nenhuma projecao ou
recomendacao e feita aqui.
"""

from __future__ import annotations

from typing import Any, Callable

# Bandas de confianca (mesma linguagem do relatorio de oportunidades)
BANDAS_CONF: tuple[tuple[str, float], ...] = (
    # (rotulo, piso) - banda = confianca >= piso, senao a seguinte
    ("ALTA", 0.85),
    ("MEDIA", 0.70),
    # BAIXA: confianca < 0.70
)

# Faixas de minuto de entrada (somente live)
FAIXAS_MINUTO: tuple[tuple[str, int, int], ...] = (
    ("15-30", 15, 30),
    ("31-60", 31, 60),
    ("61-75", 61, 75),
    ("76+", 76, 200),
)

AVALIAVEIS = ("GANHA", "PERDIDA")
MIN_AMOSTRA = 30  # abaixo disso: sem evidencia estatistica suficiente

CAVEAT_PERMANENTE = (
    "TESTE DE CALIBRACAO EM ANDAMENTO: conclusoes exigem amostra madura "
    f"(meta: {MIN_AMOSTRA}+ avaliaveis por celula). O resultado inicial "
    "6/6 NAO e evidencia suficiente de calibracao."
)


def _celula(recs: list[dict[str, Any]]) -> dict[str, Any]:
    """Contagens + taxa de acerto de um recorte de registros."""
    contagem = {"GANHA": 0, "PERDIDA": 0, "DEVOLVIDA": 0,
                "MEIA VITÓRIA": 0, "MEIA DERROTA": 0, "NÃO AVALIÁVEL": 0}
    probs: list[float] = []
    for rec in recs:
        resultado = rec.get("resultado_mercado")
        if resultado in AVALIAVEIS:
            contagem[resultado] += 1
            probs.append(float(rec["probabilidade"]))
        elif resultado in contagem:
            contagem[resultado] += 1
    avaliaveis = contagem["GANHA"] + contagem["PERDIDA"]
    taxa = (
        contagem["GANHA"] / avaliaveis if avaliaveis else None
    )
    prob_media = sum(probs) / len(probs) if probs else None
    return {
        "n": len(recs),
        "avaliaveis": avaliaveis,
        "ganha": contagem["GANHA"],
        "perdida": contagem["PERDIDA"],
        "devolvida": contagem["DEVOLVIDA"],
        "meia_vitoria": contagem["MEIA VITÓRIA"],
        "meia_derrota": contagem["MEIA DERROTA"],
        "nao_avaliavel": contagem["NÃO AVALIÁVEL"],
        "taxa_acerto": taxa,
        "prob_media_estimada": prob_media,
        "amostra_pequena": avaliaveis < MIN_AMOSTRA,
    }


def relatorio_calibracao(reg: Any) -> dict[str, Any]:
    """Todas as celulas de calibracao a partir do registro permanente."""
    recs = reg.listar()
    liquidadas = [r for r in recs if r.get("resultado_mercado") is not None]
    aguardando = [r for r in recs if r.get("resultado_mercado") is None]

    def por(predicado: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
        return [r for r in liquidadas if predicado(r)]

    por_confianca = {}
    rotulos = list(BANDAS_CONF) + [("BAIXA", 0.0)]
    for i, (rotulo, piso) in enumerate(rotulos):
        teto = rotulos[i - 1][1] if i else 2.0  # ALTA: sem teto
        if rotulo == "ALTA":
            por_confianca[rotulo] = _celula(
                por(lambda r: (r.get("confianca") or 0.0) >= piso)
            )
        else:
            por_confianca[rotulo] = _celula(por(
                lambda r, p=piso, t=teto: p <= (r.get("confianca") or 0.0) < t
            ))

    por_mercado = {}
    for mercado in sorted({r.get("mercado") for r in liquidadas if r.get("mercado")}):
        por_mercado[mercado] = _celula(por(lambda r, m=mercado: r.get("mercado") == m))

    por_tipo = {}
    for tipo in ("live", "prejogo"):
        por_tipo[tipo] = _celula(por(lambda r, t=tipo: r.get("tipo") == t))

    por_minuto = {}
    for rotulo, piso, teto in FAIXAS_MINUTO:
        por_minuto[rotulo] = _celula(por(
            lambda r, p=piso, t=teto: r.get("tipo") == "live"
            and r.get("minuto") is not None and p <= r["minuto"] <= t
        ))

    return {
        "geral": _celula(liquidadas),
        "aguardando_resultado": len(aguardando),
        "total_registros": len(recs),
        "por_confianca": por_confianca,
        "por_mercado": por_mercado,
        "por_tipo": por_tipo,
        "por_faixa_minuto": por_minuto,
    }


# ----------------------------------------------------------------------
# Exibicao (somente CALCULO sobre o registro)
# ----------------------------------------------------------------------
def _pct(valor: Any) -> str:
    return "s/d" if valor is None else f"{valor:.1%}"


def _linha_celula(rotulo: str, c: dict[str, Any]) -> str:
    extras = []
    if c["devolvida"]:
        extras.append(f"devolvidas {c['devolvida']}")
    if c["meia_vitoria"]:
        extras.append(f"meias vitorias {c['meia_vitoria']}")
    if c["meia_derrota"]:
        extras.append(f"meias derrotas {c['meia_derrota']}")
    if c["nao_avaliavel"]:
        extras.append(f"nao avaliaveis {c['nao_avaliavel']}")
    texto = (
        f"  {rotulo}: avaliaveis {c['avaliaveis']} (ganha {c['ganha']} | "
        f"perdida {c['perdida']}) | taxa real de acerto "
        f"{_pct(c['taxa_acerto'])} | prob media estimada "
        f"{_pct(c['prob_media_estimada'])}"
    )
    if extras:
        texto += " | " + " | ".join(extras)
    if c["amostra_pequena"]:
        texto += (
            f" | AMOSTRA PEQUENA (<{MIN_AMOSTRA} avaliaveis): SEM "
            "evidencia suficiente - nao conclua nada"
        )
    return texto


def format_calibracao(rep: dict[str, Any]) -> str:
    from src.live import now_brt

    out = [
        "[CALCULO] RELATORIO DE CALIBRACAO - consultado em "
        f"{now_brt().strftime('%d/%m/%Y %H:%M:%S')} (America/Sao_Paulo)",
        f"base: registro permanente ({rep['total_registros']} recomendacoes "
        f"congeladas, {rep['aguardando_resultado']} aguardando resultado)",
        CAVEAT_PERMANENTE,
        "",
        "GERAL:",
        _linha_celula("todas", rep["geral"]),
        "",
        "POR CONFIANCA (banda da recomendacao):",
    ]
    for rotulo in ("ALTA", "MEDIA", "BAIXA"):
        out.append(_linha_celula(rotulo, rep["por_confianca"][rotulo]))
    out.append("")
    out.append("POR MERCADO:")
    if rep["por_mercado"]:
        for mercado, cel in rep["por_mercado"].items():
            out.append(_linha_celula(mercado, cel))
    else:
        out.append("  s/d (nenhuma recomendacao liquidada com mercado)")
    out.append("")
    out.append("POR TIPO (pre-jogo x ao vivo):")
    out.append(_linha_celula("ao vivo", rep["por_tipo"]["live"]))
    out.append(_linha_celula("pre-jogo", rep["por_tipo"]["prejogo"]))
    out.append("")
    out.append("POR FAIXA DE MINUTO DE ENTRADA (somente ao vivo):")
    for rotulo, _p, _t in FAIXAS_MINUTO:
        out.append(_linha_celula(rotulo, rep["por_faixa_minuto"][rotulo]))
    out.append("")
    out.append(
        "calibracao das probabilidades: comparar 'prob media estimada' com "
        "'taxa real de acerto' por celula; divergencia forte em amostra "
        "madura indica probabilidade mal calibrada naquela celula."
    )
    return "\n".join(out)