"""LIQUIDACAO AUTOMATICA das recomendacoes do REGISTRO DE VALIDACAO.

TESTE DE CALIBRACAO (modo observacao): cada recomendacao congelada
ANTES do resultado e liquidada somente depois que ha um resultado
final REAL validado na API-Football.

Regras inviolaveis aplicadas aqui:
    - o snapshot original permanece IMUTAVEL: somente os campos de
      resultado sao atualizados (registrar_resultado + trigger);
    - jogo nao encerrado (status fora de FT/AET/PEN) fica PENDENTE -
      nunca liquidado por antecipacao;
    - estatistica final ausente na fonte => NAO AVALIAVEL, nunca
      zero, nunca inferida;
    - mercado sem regra de liquidacao automatica => NAO AVALIAVEL
      (com nota de auditoria explicando).

Regra de mercado (total over/under):
    - linha fracionaria (.5): total > linha decide Over, total < linha
      decide Under - nunca ha empato, GANHA ou PERDIDA;
    - linha inteira: total == linha => DEVOLVIDA; senao GANHA/PERDIDA.

Familia RESULTADO (bloco novo 07/09/2026, liquidacao valida somente
pelo PLACAR FINAL - nenhum outro dado e usado):
    - 1X2 ("Vitoria mandante (1)" / "Empate (X)" / "Vitoria visitante
      (2)"): GANHA/PERDIDA pelo placar;
    - Dupla Chance ("Dupla chance 1X/12/X2"): GANHA/PERDIDA;
    - DNB ("DNB mandante/visitante (empate anula)"): empate DEVOLVE
      (equivale a AH 0.0);
    - Handicap Asiatico ("AH mandante/visitante <linha> (90 minutos)"):
      MESMO motor validado src/handicap.py => GANHA, PERDIDA,
      DEVOLVIDA, MEIA VITORIA ou MEIA DERROTA (linhas de quarto).

Familia CARTOES (bloco 07/09/2026 - liquidacao validada):
    - total em PONTOS com a convencao DECLARADA NA PROPRIA LINHA
      congelada pelo motor live ("cartoes (amarelo=1, vermelho=2)"):
      total = amarelos + 2*vermelhos da estatistica FINAL da API -
      a recomendacao e liquidada exatamente pela metrica que foi
      recomendada;
    - linha de cartoes SEM convencao declarada => NAO AVALIAVEL
      (pesos de amarelo/vermelho NUNCA sao assumidos);
    - amarelos OU vermelhos de qualquer equipe ausentes na fonte =>
      NAO AVALIAVEL, nunca zero, nunca inferido;
    - regra over/under identica as demais familias de total: .5 decide
      GANHA/PERDIDA; linha inteira pode DEVOLVER.

Placar final ausente (jogo encerrado sem placar valido na fonte) =>
NAO AVALIAVEL, nunca zero, nunca inferido. O fixture e sempre buscado
pelo fixture_id do registro (identidade ancorada, regra 12).
"""

from __future__ import annotations

import re
from typing import Any

from src.exceptions import UserFacingError
from src.fixtures import FINISHED_STATUS, parse_fixture
from src.handicap import settle

# "Over 9.5 escanteios (total do jogo)" / "Under 3.5 gols (total do jogo)"
_RE_LINHA = re.compile(
    r"^(Over|Under)\s+(\d+(?:[.,]\d+)?)\s+(escanteios|gols|cartoes)\b"
)

# Familia RESULTADO: formatos EXATOS produzidos por src/resultado.py
_RE_1X2 = re.compile(
    r"^(Vitoria mandante|Empate|Vitoria visitante) \([12X]\)$"
)
_RE_DC = re.compile(r"^Dupla chance (1X|X2|12)$")
_RE_DNB = re.compile(r"^DNB (mandante|visitante) \(empate anula\)$")
_RE_AH = re.compile(
    r"^AH (mandante|visitante) ([+-]?\d+(?:[.,]\d+)?) \(90 minutos\)$"
)
_RES_REGEXES = (_RE_1X2, _RE_DC, _RE_DNB, _RE_AH)

# Mapeamento dos rotulos validados do motor AH para o registro
_MAPA_AH = {
    "vitoria integral": "GANHA",
    "meia vitoria": "MEIA VITÓRIA",
    "devolucao": "DEVOLVIDA",
    "meia derrota": "MEIA DERROTA",
    "derrota": "PERDIDA",
}

PENDENTE = "PENDENTE"
NAO_AVALIAVEL = "NÃO AVALIÁVEL"


def _parse_linha(linha: str) -> tuple[str, float, str] | None:
    """(direcao, valor, unidade) da linha congelada, ou None quando o
    mercado nao tem regra de liquidacao automatica."""
    m = _RE_LINHA.match((linha or "").strip())
    if not m:
        return None
    return m.group(1), float(m.group(2).replace(",", ".")), m.group(3)


def _resultado_do_total(direcao: str, total: float, linha: float) -> str:
    """GANHA/PERDIDA/DEVOLVIDA pela regra do total over/under."""
    if direcao == "Over":
        if total > linha:
            return "GANHA"
        if total < linha:
            return "PERDIDA"
    else:  # Under
        if total < linha:
            return "GANHA"
        if total > linha:
            return "PERDIDA"
    return "DEVOLVIDA"  # total exatamente na linha inteira


def _total_escanteios(client: Any, fixture: Any) -> tuple[float | None, dict[str, Any]]:
    """Total real de escanteios da partida pela estatistica FINAL da
    API. Bloco ausente/sem valor => (None, parcial) - nunca inferido."""
    resposta = client.get(
        "/fixtures/statistics", params={"fixture": fixture.fixture_id}
    )
    cantos: dict[int, int | None] = {}
    for bloco in resposta or []:
        try:
            team_id = int(bloco["team"]["id"])
        except (KeyError, TypeError, ValueError):
            continue
        valor = None
        for item in bloco.get("statistics") or []:
            if item.get("type") == "Corner Kicks":
                bruto = item.get("value")
                try:
                    valor = int(str(bruto)) if bruto not in (None, "") else None
                except (TypeError, ValueError):
                    valor = None
                break
        cantos[team_id] = valor

    casa = cantos.get(fixture.home_team_id)
    fora = cantos.get(fixture.away_team_id)
    parcial = {
        "escanteios": [casa, fora],
        "fonte": "/fixtures/statistics (estatistica final da partida)",
    }
    if casa is None or fora is None:
        return None, parcial
    return float(casa + fora), parcial


# Cartoes: total em PONTOS pela convencao DECLARADA na linha congelada
# pelo motor live. A recomendacao e liquidada exatamente pela metrica
# que foi recomendada; linha sem convencao declarada => NAO AVALIAVEL
# (pesos de amarelo/vermelho nunca sao assumidos).
_MARCA_CONVENCAO_CARTOES = "amarelo=1, vermelho=2"


def _total_cartoes(
    client: Any, fixture: Any
) -> tuple[float | None, dict[str, Any]]:
    """Total real de PONTOS de cartoes da partida pela estatistica
    FINAL da API, na convencao declarada na linha congelada pelo motor
    (amarelo=1, vermelho=2). Qualquer valor ausente => (None, parcial)
    - nunca zero, nunca inferido."""
    resposta = client.get(
        "/fixtures/statistics", params={"fixture": fixture.fixture_id}
    )
    amarelos: dict[int, int | None] = {}
    vermelhos: dict[int, int | None] = {}
    for bloco in resposta or []:
        try:
            team_id = int(bloco["team"]["id"])
        except (KeyError, TypeError, ValueError):
            continue
        for item in bloco.get("statistics") or []:
            bruto = item.get("value")
            try:
                valor = int(str(bruto)) if bruto not in (None, "") else None
            except (TypeError, ValueError):
                valor = None
            if item.get("type") == "Yellow Cards":
                amarelos[team_id] = valor
            elif item.get("type") == "Red Cards":
                vermelhos[team_id] = valor
        amarelos.setdefault(team_id, None)
        vermelhos.setdefault(team_id, None)

    casa_y = amarelos.get(fixture.home_team_id)
    fora_y = amarelos.get(fixture.away_team_id)
    casa_r = vermelhos.get(fixture.home_team_id)
    fora_r = vermelhos.get(fixture.away_team_id)
    parcial = {
        "amarelos": [casa_y, fora_y],
        "vermelhos": [casa_r, fora_r],
        "convencao": (
            f"{_MARCA_CONVENCAO_CARTOES} (declarada na linha congelada)"
        ),
        "fonte": "/fixtures/statistics (estatistica final da partida)",
    }
    if None in (casa_y, fora_y, casa_r, fora_r):
        return None, parcial
    return float(casa_y + fora_y + 2 * (casa_r + fora_r)), parcial


def _placar_final(fixture: Any) -> str | None:
    if fixture.goals_home is None or fixture.goals_away is None:
        return None
    return f"{fixture.goals_home}-{fixture.goals_away}"


# ----------------------------------------------------------------------
# Familia RESULTADO (liquidacao exclusivamente pelo placar final)
# ----------------------------------------------------------------------
def _e_linha_resultado(linha: str) -> bool:
    """True quando a linha congelada e da familia resultado
    (1X2/Dupla Chance/DNB/AH) - liquidadavel pelo placar final."""
    texto = (linha or "").strip()
    return any(rx.match(texto) for rx in _RES_REGEXES)


def _liquidar_resultado(
    linha: str, gh: int, ga: int
) -> tuple[str | None, str]:
    """(resultado, nota) da familia resultado pelo placar final.

    None quando a linha nao casa com nenhum formato da familia (o
    chamador trata como mercado sem regra). O AH usa o MESMO motor
    validado (src/handicap.py): margem do ponto de vista do lado
    apostado + linha real.
    """
    texto = (linha or "").strip()

    m = _RE_1X2.match(texto)
    if m:
        alvo = m.group(1)
        if alvo == "Vitoria mandante":
            venceu, alvo_txt = gh > ga, "vitoria do mandante"
        elif alvo == "Empate":
            venceu, alvo_txt = gh == ga, "empate"
        else:
            venceu, alvo_txt = gh < ga, "vitoria do visitante"
        return (
            "GANHA" if venceu else "PERDIDA",
            f"placar {gh}-{ga} x {alvo_txt}",
        )

    m = _RE_DC.match(texto)
    if m:
        par = m.group(1)
        venceu = {"1X": gh >= ga, "X2": ga >= gh, "12": gh != ga}[par]
        return (
            "GANHA" if venceu else "PERDIDA",
            f"placar {gh}-{ga} x dupla chance {par}",
        )

    m = _RE_DNB.match(texto)
    if m:
        lado = m.group(1)
        if gh == ga:
            return "DEVOLVIDA", (
                f"placar {gh}-{ga}: empate devolve o stake "
                "(DNB = AH 0.0)"
            )
        venceu = gh > ga if lado == "mandante" else gh < ga
        return (
            "GANHA" if venceu else "PERDIDA",
            f"placar {gh}-{ga} x DNB {lado} (empate anula)",
        )

    m = _RE_AH.match(texto)
    if m:
        lado = m.group(1)
        linha_ah = float(m.group(2).replace(",", "."))
        margem = (gh - ga) if lado == "mandante" else (ga - gh)
        s = settle(linha_ah, margem)
        return (
            _MAPA_AH[s.rotulo],
            f"placar {gh}-{ga}: margem {margem:+} do {lado}; {s.detalhe}",
        )

    return None, "mercado sem regra de liquidacao automatica"


def liquidar_pendentes(client: Any, reg: Any) -> dict[str, Any]:
    """Liquida TODAS as recomendacoes sem resultado registrado cujo
    jogo ja foi encerrado e validado na API. Retorna relatorio de
    item por item (situacao + motivo). Previsoes nunca sao tocadas."""
    pendentes = [r for r in reg.listar() if r.get("resultado_mercado") is None]

    itens: list[dict[str, Any]] = []
    contagem: dict[str, int] = {}
    for rec in pendentes:
        item: dict[str, Any] = {
            "id": rec["id"],
            "fixture_id": rec["fixture_id"],
            "jogo": f"{rec.get('mandante') or '?'} x "
                    f"{rec.get('visitante') or '?'}",
            "linha": rec["linha"],
        }
        try:
            resposta = client.get(
                "/fixtures", params={"id": rec["fixture_id"]}
            )
        except UserFacingError as exc:
            item["situacao"] = PENDENTE
            item["motivo"] = f"consulta a API falhou: {exc}"
            itens.append(item)
            continue

        if not resposta:
            item["situacao"] = PENDENTE
            item["motivo"] = "fixture nao encontrado na API"
            itens.append(item)
            continue

        fx = parse_fixture(resposta[0])
        item["status"] = fx.status
        if not fx.is_finished:
            item["situacao"] = PENDENTE
            item["motivo"] = (
                f"jogo ainda nao encerrado (status {fx.status})"
            )
            itens.append(item)
            continue

        placar = _placar_final(fx)

        # Familia RESULTADO: liquida exclusivamente pelo placar final
        # (nenhuma estatistica adicional e consultada). Placar ausente
        # => NAO AVALIAVEL, nunca zero, nunca inferido.
        if _e_linha_resultado(rec["linha"]):
            if placar is None:
                reg.registrar_resultado(
                    rec["id"], resultado_mercado=NAO_AVALIAVEL,
                    placar_final=None,
                    nota="placar final nao disponivel na fonte; "
                         "nunca inferido",
                )
                item["situacao"] = NAO_AVALIAVEL
                item["motivo"] = "placar final nao disponivel na fonte"
                itens.append(item)
                continue
            resultado, nota = _liquidar_resultado(
                rec["linha"], fx.goals_home, fx.goals_away
            )
            reg.registrar_resultado(
                rec["id"], resultado_mercado=resultado,
                placar_final=placar,
                nota=(
                    f"liquidacao automatica validada na API (status "
                    f"{fx.status}): {nota}"
                ),
            )
            item["situacao"] = resultado
            item["motivo"] = nota
            itens.append(item)
            continue

        parsed = _parse_linha(rec["linha"])
        if parsed is None:
            reg.registrar_resultado(
                rec["id"], resultado_mercado=NAO_AVALIAVEL,
                placar_final=placar,
                nota="mercado sem regra de liquidacao automatica",
            )
            item["situacao"] = NAO_AVALIAVEL
            item["motivo"] = "mercado sem regra de liquidacao automatica"
            itens.append(item)
            continue

        direcao, linha, unidade = parsed
        if unidade == "gols":
            if placar is None:
                total, parcial = None, {"gols": [fx.goals_home, fx.goals_away]}
            else:
                total = float(fx.goals_home + fx.goals_away)
                parcial = {"gols": [fx.goals_home, fx.goals_away]}
        elif unidade == "escanteios":
            total, parcial = _total_escanteios(client, fx)
        elif unidade == "cartoes":
            # Familia CARTOES: liquida SOMENTE pela convencao DECLARADA
            # na propria linha congelada pelo motor. Linha sem convencao
            # declarada => NAO AVALIAVEL (pesos nunca assumidos).
            if _MARCA_CONVENCAO_CARTOES not in (rec["linha"] or ""):
                reg.registrar_resultado(
                    rec["id"], resultado_mercado=NAO_AVALIAVEL,
                    placar_final=placar,
                    nota=(
                        "cartoes sem convencao de contagem declarada na "
                        "linha congelada; pesos de amarelo/vermelho nunca "
                        "sao assumidos"
                    ),
                )
                item["situacao"] = NAO_AVALIAVEL
                item["motivo"] = (
                    "sem convencao de contagem declarada na linha "
                    "congelada"
                )
                itens.append(item)
                continue
            total, parcial = _total_cartoes(client, fx)
        else:  # inalcançavel: o regex so aceita as tres unidades
            reg.registrar_resultado(
                rec["id"], resultado_mercado=NAO_AVALIAVEL,
                placar_final=placar,
                nota="mercado sem regra de liquidacao automatica",
            )
            item["situacao"] = NAO_AVALIAVEL
            item["motivo"] = "mercado sem regra de liquidacao automatica"
            itens.append(item)
            continue

        if total is None:
            reg.registrar_resultado(
                rec["id"], resultado_mercado=NAO_AVALIAVEL,
                placar_final=placar, stats_finais=parcial,
                nota="estatistica final nao disponivel na fonte; "
                     "nunca inferida",
            )
            item["situacao"] = NAO_AVALIAVEL
            item["motivo"] = "estatistica final nao disponivel na fonte"
        else:
            resultado = _resultado_do_total(direcao, total, linha)
            reg.registrar_resultado(
                rec["id"], resultado_mercado=resultado,
                placar_final=placar, stats_finais=parcial,
                nota=(
                    f"liquidacao automatica validada na API (status "
                    f"{fx.status}): total real {total:g} x linha "
                    f"{linha:g}"
                ),
            )
            item["situacao"] = resultado
            item["motivo"] = (
                f"total real {total:g} x linha {linha:g} ({direcao})"
            )
        itens.append(item)

    for item in itens:
        contagem[item["situacao"]] = contagem.get(item["situacao"], 0) + 1
    return {
        "itens": itens,
        "sem_resultado_antes": len(pendentes),
        "contagem": contagem,
    }


# ----------------------------------------------------------------------
# Exibicao (somente FATOS e CALCULO)
# ----------------------------------------------------------------------
def format_liquidacao(report: dict[str, Any]) -> str:
    from src.live import now_brt

    out = [
        "[FATO] LIQUIDACAO AUTOMATICA - consultado em "
        f"{now_brt().strftime('%d/%m/%Y %H:%M:%S')} (America/Sao_Paulo)",
        "recomendacoes SEM resultado antes desta liquidacao: "
        f"{report['sem_resultado_antes']}",
    ]
    if not report["itens"]:
        out.append("nenhuma recomendacao pendente: nada a liquidar.")
        return "\n".join(out)

    contagem = report["contagem"]
    resumo = " | ".join(
        f"{chave.lower()}: {contagem[chave]}"
        for chave in (PENDENTE, "GANHA", "PERDIDA", "DEVOLVIDA",
                      NAO_AVALIAVEL)
        if contagem.get(chave)
    )
    out.append(f"[CALCULO] {resumo}")
    out.append("item por item (previsao original intacta em todos):")
    for item in report["itens"]:
        out.append(
            f"  #{item['id']} | {item['jogo']} | fixture "
            f"{item['fixture_id']} | {item['linha']} | status "
            f"{item.get('status') or 's/d'} | situacao "
            f"{item['situacao']} ({item['motivo']})"
        )
    return "\n".join(out)