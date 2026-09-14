"""POLITICA DE APROVACAO POR SEGURANCA (pre-jogo + live).

Camada NOVA e ISOLADA de DECISAO/ROTULO: altera SOMENTE a politica
final de decisao de oportunidades reais. Nenhuma matematica validada
e alterada ou recalculada aqui - probabilidades, Poisson, benchmarks,
confianca, historico, H2H, casa/fora, live statistics, minuto, placar,
pressao, finalizacoes, ataques perigosos, escanteios, cartoes, gols,
1X2, Dupla Chance, DNB, Handicap Asiatico, regra de PUSH, regra de
liquidacao, odd justa e calculo de EV (src/valor_aposta.py) sao usados
COMO ESTAO, apenas importados.

PRIORIDADES (nesta ordem):
    1. probabilidade real estimada de acerto
    2. confianca
    3. qualidade/suficiencia dos dados
    4. margem de seguranca da linha (embutida na probabilidade da
       linha escolhida: linha mais folgada => probabilidade maior)
    5. riscos e contradicoes
    6. retorno minimo aceitavel (declarado pelo USUARIO)
    7. EV/value - apenas INFORMACAO complementar

SEPARACAO DE DECISAO (nunca misturadas num unico veredito):
    (A) QUALIDADE/SEGURANCA DA APOSTA
        APROVADA POR SEGURANCA
        APROVADA COM RESSALVA DE SEGURANCA
        REPROVADA POR SEGURANCA
    (B) QUALIDADE FINANCEIRA DA ODD
        RETORNO ACEITAVEL      (odd atende o minimo declarado)
        RETORNO BAIXO          (odd abaixo do minimo declarado)
        RETORNO NAO INFORMADO  (sem odd real disponivel)
        RETORNO NAO VALIDADO   (minimo do usuario nao declarado)

THRESHOLDS: SOMENTE os JA EXISTENTES no motor (src/live_opportunity):
janela de probabilidade PROB_MIN_APROVAR..PROB_MAX_APROVAR e confianca
CONF_MIN_TOP1 / CONF_MIN_TOP2. Nenhum threshold novo e criado aqui.

REGRAS INVIOLAVEIS desta politica:
    - EV NEGATIVO sozinho NUNCA reprova uma selecao aprovada por
      seguranca; EV e odd justa sao INFORMATIVOS (segunda camada).
    - EV POSITIVO sozinho NUNCA aprova uma selecao reprovada por
      seguranca: seguranca vem da analise esportiva, nunca do preco.
    - Ausencia de odd NUNCA reprova a analise esportiva.
    - A odd NUNCA comanda a analise esportiva.
    - Retorno minimo e declarado pelo USUARIO (nenhum minimo
      universal inventado); sem declaracao => RETORNO NAO VALIDADO.
    - MULTIPLAS: cada perna analisada individualmente (probabilidade
      e confianca proprias); probabilidade conjunta = produto, com a
      premissa de INDEPENDENCIA DECLARADA; confianca conjunta = MENOR
      individual (conservadora); riscos/contradicoes = uniao das
      pernas; qualquer perna REPROVADA POR SEGURANCA reprova a
      multipla inteira.
    - Vocabulario: sempre PROBABILIDADE ESTIMADA / CONFIANCA / RISCOS
      / CONTRADICOES - jamais afirmacao absoluta de acerto.

LIVE: o MESMO principio de seguranca primeiro; a probabilidade LIVE
atualizada (minuto, placar, tempo restante, pressao, etc. - motor
validado) e o principal criterio; a odd segue como segunda camada.
Este modulo e generico: recebe a probabilidade/confianca que o motor
validado ja produziu (pre-jogo OU live) e apenas rotula a decisao.

Sem persistencia: calculo e rotulo puros sobre resultados ja
produzidos; nenhum banco e aberto, nada e gravado, nenhum dado
historico muda.
"""

from __future__ import annotations

from math import prod
from typing import Any

# Thresholds EXISTENTES do motor validado - importados, nunca alterados
from src.live_opportunity import (
    CONF_MIN_TOP1,
    CONF_MIN_TOP2,
    PROB_MAX_APROVAR,
    PROB_MIN_APROVAR,
)
# Camada financeira EXISTENTE (EV/odd justa) - usada como informacao
from src.valor_aposta import (
    PREMISSA_INDEPENDENCIA,
    REGRA_NAO_CONFIRMADA,
    avaliar_multipla as _ev_multipla,
    avaliar_simples as _ev_simples,
)

# ---- vereditos de SEGURANCA (analise esportiva) ----
APROVADA_POR_SEGURANCA = "APROVADA POR SEGURANCA"
APROVADA_COM_RESSALVA = "APROVADA COM RESSALVA DE SEGURANCA"
REPROVADA_POR_SEGURANCA = "REPROVADA POR SEGURANCA"

# ---- vereditos de RETORNO (qualidade financeira da odd) ----
RETORNO_ACEITAVEL = "RETORNO ACEITAVEL"
RETORNO_BAIXO = "RETORNO BAIXO"
RETORNO_NAO_INFORMADO = "RETORNO NAO INFORMADO"
RETORNO_NAO_VALIDADO = "RETORNO NAO VALIDADO"

NOTA_EV_INFORMATIVO = (
    "EV/odd justa sao INFORMATIVOS: nao vetam e nao aprovam por si"
)


# ----------------------------------------------------------------------
# (A) SEGURANCA DA APOSTA - thresholds SOMENTE os ja existentes
# ----------------------------------------------------------------------
def classificar_seguranca(
    probabilidade: float | None,
    confianca: float | None,
    riscos: list[Any] | None = None,
    contracoes: list[Any] | None = None,
    tem_sustentacao: bool = True,
) -> dict[str, Any]:
    """Classificacao de SEGURANCA de uma selecao.

    Janela de probabilidade e confianca minimas sao as MESMAS do motor
    validado (PROB_MIN_APROVAR..PROB_MAX_APROVAR, CONF_MIN_TOP1,
    CONF_MIN_TOP2). Riscos/contradicoes sinalizados pelo motor
    rebaixam para RESSALVA no maximo - nunca sao ignorados.
    """
    riscos = list(riscos or [])
    contracoes = list(contracoes or [])
    base = {
        "probabilidade_estimada": probabilidade,
        "confianca": confianca,
        "riscos": riscos,
        "contradicoes": contracoes,
    }
    if not tem_sustentacao:
        return {
            **base,
            "classificacao": REPROVADA_POR_SEGURANCA,
            "motivos": [
                "sem sustentacao estatistica da linha "
                "(dado nao disponivel na fonte)"
            ],
        }
    if probabilidade is None or confianca is None:
        return {
            **base,
            "classificacao": REPROVADA_POR_SEGURANCA,
            "motivos": [
                "probabilidade/confianca indisponiveis: "
                "dado nao disponivel na fonte"
            ],
        }
    if probabilidade < PROB_MIN_APROVAR:
        return {
            **base,
            "classificacao": REPROVADA_POR_SEGURANCA,
            "motivos": [
                f"probabilidade estimada {probabilidade:.2%} abaixo da "
                f"janela existente de aprovacao ({PROB_MIN_APROVAR:.0%})"
            ],
        }
    if probabilidade > PROB_MAX_APROVAR:
        return {
            **base,
            "classificacao": REPROVADA_POR_SEGURANCA,
            "motivos": [
                f"linha praticamente decidida: probabilidade "
                f"{probabilidade:.2%} acima da janela existente "
                f"({PROB_MAX_APROVAR:.0%}) - utilidade pratica desprezivel"
            ],
        }
    if confianca < CONF_MIN_TOP1:
        return {
            **base,
            "classificacao": REPROVADA_POR_SEGURANCA,
            "motivos": [
                f"confianca {confianca:.2f} abaixo do minimo existente "
                f"do motor ({CONF_MIN_TOP1:.2f})"
            ],
        }
    # dentro da janela e acima da confianca minima: checa RESSALVA
    ressalva: list[str] = []
    if confianca < CONF_MIN_TOP2:
        ressalva.append(
            f"confianca {confianca:.2f} na faixa existente entre "
            f"CONF_MIN_TOP1 ({CONF_MIN_TOP1:.2f}) e "
            f"CONF_MIN_TOP2 ({CONF_MIN_TOP2:.2f})"
        )
    if riscos:
        ressalva.append(
            f"{len(riscos)} risco(s) sinalizado(s) pelo motor: "
            + "; ".join(str(r) for r in riscos)
        )
    if contracoes:
        ressalva.append(
            f"{len(contracoes)} contradio(oes) registrada(s): "
            + "; ".join(str(c) for c in contracoes)
        )
    if ressalva:
        return {
            **base,
            "classificacao": APROVADA_COM_RESSALVA,
            "motivos": ressalva,
        }
    return {
        **base,
        "classificacao": APROVADA_POR_SEGURANCA,
        "motivos": [
            f"probabilidade estimada {probabilidade:.2%} dentro da "
            f"janela existente ({PROB_MIN_APROVAR:.0%}-"
            f"{PROB_MAX_APROVAR:.0%}); confianca {confianca:.2f} acima "
            f"de CONF_MIN_TOP2 ({CONF_MIN_TOP2:.2f}); nenhum risco nem "
            "contradicao sinalizada pelo motor"
        ],
    }


# ----------------------------------------------------------------------
# (B) RETORNO - qualidade financeira da odd (segunda camada)
# ----------------------------------------------------------------------
def classificar_retorno(
    odd: float | None,
    retorno_minimo: float | None,
) -> dict[str, Any]:
    """Classificacao de RETORNO da odd - NUNCA reprova a analise.

    `retorno_minimo` e o que o USUARIO declarou aceitar; nenhum
    minimo universal e inventado. Sem odd => RETORNO NAO INFORMADO;
    sem minimo declarado => RETORNO NAO VALIDADO (nada e decidido por
    odd em qualquer hipotese).
    """
    if odd is None:
        return {
            "classificacao": RETORNO_NAO_INFORMADO,
            "odd": None,
            "retorno_minimo": retorno_minimo,
            "nota": (
                "sem odd real disponivel/informada; a analise esportiva "
                "NAO e reprovada por retorno"
            ),
        }
    if retorno_minimo is None:
        return {
            "classificacao": RETORNO_NAO_VALIDADO,
            "odd": odd,
            "retorno_minimo": None,
            "nota": (
                "retorno minimo aceitavel nao declarado pelo usuario; "
                "nada e aprovado nem reprovado por odd"
            ),
        }
    if odd >= retorno_minimo:
        return {
            "classificacao": RETORNO_ACEITAVEL,
            "odd": odd,
            "retorno_minimo": retorno_minimo,
            "nota": (
                f"odd {odd} atende o retorno minimo declarado "
                f"({retorno_minimo}) - segunda camada, nao altera o "
                "veredito de seguranca"
            ),
        }
    return {
        "classificacao": RETORNO_BAIXO,
        "odd": odd,
        "retorno_minimo": retorno_minimo,
        "nota": (
            f"odd {odd} abaixo do retorno minimo declarado "
            f"({retorno_minimo}) - segunda camada, nao altera o "
            "veredito de seguranca"
        ),
    }


# ----------------------------------------------------------------------
# EV / VALUE - calculo EXISTENTE, usado apenas como INFORMACAO
# ----------------------------------------------------------------------
def camada_financeira(
    p_win: float,
    p_empate: float,
    p_acima: float,
    odd: float,
    regra_liquidacao: str | None,
) -> dict[str, Any] | None:
    """Odd justa / EV / probabilidade implicita - SO informativos."""
    if odd is None or regra_liquidacao is None or (
        regra_liquidacao == REGRA_NAO_CONFIRMADA
    ):
        return None
    res = _ev_simples(p_win, p_empate, p_acima, odd, regra_liquidacao)
    return {
        "prob_implicita": res["prob_implicita"],
        "odd_justa": res["odd_justa"],
        "ev": res["ev"],
        # veredito do EV puro (camada financeira isolada): NUNCA e o
        # veredito final da aposta
        "veredito_do_ev": res["classificacao"],
        "nota": NOTA_EV_INFORMATIVO,
    }


# ----------------------------------------------------------------------
# SELECAO SIMPLES: seguranca + retorno + EV informativo, SEPARADOS
# ----------------------------------------------------------------------
def avaliar_selecao(
    probabilidade: float | None,
    confianca: float | None,
    odd: float | None = None,
    retorno_minimo: float | None = None,
    riscos: list[Any] | None = None,
    contracoes: list[Any] | None = None,
    tem_sustentacao: bool = True,
    desfechos: tuple[float, float, float] | None = None,
    regra_liquidacao: str | None = None,
) -> dict[str, Any]:
    """Avaliacao completa de UMA selecao com vereditos SEPARADOS.

    `probabilidade`/`confianca`/`riscos`/`contracoes` sao os valores
    que o motor validado ja produziu (pre-jogo OU live). `desfechos`
    (win/empate/acima) e `regra_liquidacao` alimentam a camada
    financeira informativa (src/valor_aposta.py, sem alteracao).
    """
    seguranca = classificar_seguranca(
        probabilidade, confianca, riscos, contracoes, tem_sustentacao)
    retorno = classificar_retorno(odd, retorno_minimo)
    financeiro = None
    if desfechos is not None and odd is not None:
        p_win, p_empate, p_acima = desfechos
        financeiro = camada_financeira(
            p_win, p_empate, p_acima, odd, regra_liquidacao)
    return {
        "seguranca": seguranca,
        "retorno": retorno,
        "financeiro_informativo": financeiro,
    }


# ----------------------------------------------------------------------
# MULTIPLAS: pernas individuais + conjunta + dois vereditos
# ----------------------------------------------------------------------
def avaliar_multipla_seguranca(
    pernas: list[dict[str, Any]],
    retorno_minimo: float | None = None,
    odd_informada: float | None = None,
) -> dict[str, Any]:
    """Multipla com vereditos SEPARADOS de seguranca e de retorno.

    Perna (dict): probabilidade, confianca, riscos, contracoes,
    tem_sustentacao, odd, desfechos=(win, empate, acima) e
    regra_liquidacao - tudo produzido pelo motor validado.

    Probabilidade conjunta = produto (PREMISSA DE INDEPENDENCIA
    DECLARADA no retorno). Confianca conjunta = MENOR individual
    (conservadora). Riscos/contradicoes = uniao. Qualquer perna
    REPROVADA POR SEGURANCA reprova a multipla inteira.
    """
    if not pernas:
        raise ValueError("multipla sem pernas")

    individuais = []
    fin_pernas: list[tuple[float, float, float, float, str]] = []
    financeira_completa = True
    for perna in pernas:
        individuais.append(avaliar_selecao(
            probabilidade=perna["probabilidade"],
            confianca=perna["confianca"],
            odd=perna.get("odd"),
            retorno_minimo=perna.get("retorno_minimo"),
            riscos=perna.get("riscos"),
            contracoes=perna.get("contracoes"),
            tem_sustentacao=perna.get("tem_sustentacao", True),
            desfechos=perna.get("desfechos"),
            regra_liquidacao=perna.get("regra_liquidacao"),
        ))
        desfechos = perna.get("desfechos")
        if (desfechos is None or perna.get("odd") is None
                or perna.get("regra_liquidacao") is None):
            financeira_completa = False
        elif financeira_completa:
            fin_pernas.append((
                desfechos[0], desfechos[1], desfechos[2],
                perna["odd"], perna["regra_liquidacao"],
            ))

    # conjunta: produto das probabilidade (INDEPENDENCIA DECLARADA)
    prob_conjunta = prod(p["probabilidade"] for p in pernas)
    # confianca conjunta: MENOR individual (conservadora)
    conf_conjunta = min(p["confianca"] for p in pernas)
    riscos_conjuntos = [
        str(r) for p in pernas for r in (p.get("riscos") or [])
    ]
    contracoes_conjuntas = [
        str(c) for p in pernas for c in (p.get("contracoes") or [])
    ]

    # perna reprovada individualmente reprova a multipla inteira
    reprovadas = [
        i for i, av in enumerate(individuais)
        if av["seguranca"]["classificacao"] == REPROVADA_POR_SEGURANCA
    ]
    if reprovadas:
        seguranca_multipla = {
            "probabilidade_estimada": round(prob_conjunta, 6),
            "confianca": conf_conjunta,
            "riscos": riscos_conjuntos,
            "contradicoes": contracoes_conjuntas,
            "classificacao": REPROVADA_POR_SEGURANCA,
            "motivos": [
                f"perna(s) {reprovadas} REPROVADA(S) POR SEGURANCA "
                "individualmente: a multipla nao pode ser aprovada"
            ],
        }
    else:
        seguranca_multipla = classificar_seguranca(
            prob_conjunta, conf_conjunta,
            riscos_conjuntos, contracoes_conjuntas,
        )
        seguranca_multipla["probabilidade_estimada"] = round(
            prob_conjunta, 6)

    # camada financeira informativa: enumeracao EXATA de desfechos
    financeiro = None
    odd_combinada_calculada = None
    if financeira_completa and fin_pernas:
        fin = _ev_multipla(fin_pernas)
        odd_combinada_calculada = fin["odd_combinada"]
        financeiro = {
            "odd_combinada_calculada": fin["odd_combinada"],
            "odd_justa_vitoria_plena": fin["odd_justa_vitoria_plena"],
            "ev": fin["ev"],
            "veredito_do_ev": fin["classificacao"],
            "nota": NOTA_EV_INFORMATIVO,
        }

    # retorno da multipla: odd informada do bookmaker quando existe;
    # senao a combinada calculada das pernas
    odd_comparar = (
        odd_informada if odd_informada is not None
        else odd_combinada_calculada
    )
    retorno_multipla = classificar_retorno(odd_comparar, retorno_minimo)

    return {
        "premissa": PREMISSA_INDEPENDENCIA,
        "pernas": individuais,
        "probabilidade_conjunta": round(prob_conjunta, 6),
        "confianca_conjunta": conf_conjunta,
        "seguranca_multipla": seguranca_multipla,
        "retorno_multipla": retorno_multipla,
        "financeiro_informativo": financeiro,
    }