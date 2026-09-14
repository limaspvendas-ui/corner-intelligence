"""Testes da POLITICA OPERACIONAL de selecao/apresentacao da linha
principal (src/politica_operacional.py) e da sua integracao na politica
permanente (src/policy.py selecionar_melhor v2).

Sem requisicoes a API: objetos sinteticos no formato real.
Regras testadas (regra do operador 08/09/2026):
  - odd justa e INFORMATIVA (1/prob), nunca apresentada como real;
  - piso operacional 1.15 sobre a odd EFETIVA (real quando existe,
    senao justa); odd real ausente => ODD NAO DISPONIVEL NA FONTE;
  - linhas de probabilidade extrema continuam aprovadas
    ESTATISTICAMENTE, mas marcadas "ALTA PROBABILIDADE, MAS BAIXA
    UTILIDADE OPERACIONAL" e nunca usadas como principal;
  - entre as utilizaveis vence a de MAIOR probabilidade (a seguranca
    nunca e rebaixada artificialmente para conseguir odd maior);
  - nenhuma utilizavel => "NENHUMA LINHA COM UTILIDADE OPERACIONAL
    APROVADA.".
"""

from types import SimpleNamespace

from src.politica_operacional import (
    MARCA_BAIXA_UTILIDADE,
    NENHUMA_UTIL_MSG,
    ODD_MIN_OPERACIONAL,
    ODD_REAL_INDISPONIVEL,
    avaliar_utilidade,
    format_bloco_operacional,
    odd_justa,
    selecionar_linha_operacional,
)
from src.policy import selecionar_melhor


def _av(linha, prob, conf=0.88, lam=3.0, amostra=1.0, mercado="gols"):
    return SimpleNamespace(
        mercado=mercado,
        linha=linha,
        prob=prob,
        confianca=conf,
        riscos=[],
        conf_componentes={"amostra_historica": amostra},
        sustentacao={"lambda_por90": lam},
    )


def _hist(n=20, corners=10, goals=3):
    def jogo(i, cf, gf):
        return SimpleNamespace(
            corners_total=cf,
            goals_for=gf,
            goals_against=max(1, 3 - gf),
            played_at_home=(i % 2 == 0),
        )

    games = [jogo(i, corners, goals) for i in range(n)]
    return {
        "games_home": games[: n // 2],
        "games_away": games[n // 2:],
        "n_home": n // 2,
        "n_away": n - n // 2,
    }


_BENCH = {"describe": {"media": 2.6}, "partidas_validas": 100}


# ----------------------------------------------------------------------
# Odd justa (informativa) e utilidade
# ----------------------------------------------------------------------
def test_odd_justa_informativa():
    assert odd_justa(0.96) == 1.04
    assert odd_justa(0.5) == 2.0
    assert odd_justa(0) is None  # nunca divide por zero


def test_piso_sobre_odd_justa_quando_sem_odd_real():
    u = avaliar_utilidade(0.96)  # odd justa ~1.04
    assert u.odd_real is None
    assert u.odd_efetiva == u.odd_justa
    assert not u.utilizavel
    assert u.marca == MARCA_BAIXA_UTILIDADE


def test_piso_atingido_por_prob_util():
    u = avaliar_utilidade(0.80)  # odd justa 1.25 >= 1.15
    assert u.utilizavel
    assert u.marca is None


def test_odd_real_comanda_quando_existe():
    # odd real acima do piso: utilizavel mesmo com prob extrema
    u = avaliar_utilidade(0.96, odd_real=1.30)
    assert u.utilizavel and u.marca is None
    # odd real ABAIXO do piso: nao utilizavel mesmo com prob util
    u2 = avaliar_utilidade(0.80, odd_real=1.10)
    assert not u2.utilizavel
    assert u2.marca == MARCA_BAIXA_UTILIDADE


def test_limite_exato_do_piso():
    assert ODD_MIN_OPERACIONAL == 1.15
    # prob 1/1.15 ~ 0.8696: exatamente no piso => utilizavel
    u = avaliar_utilidade(1.0 / 1.15)
    assert u.odd_justa == 1.15
    assert u.utilizavel


# ----------------------------------------------------------------------
# Selecao da melhor linha operacional
# ----------------------------------------------------------------------
def test_separa_maior_probabilidade_de_aposta_pratica():
    aves = [
        _av("Under 7.5 gols (total do jogo)", 0.9681),
        _av("Over 1.5 gols (total do jogo)", 0.8767),
        _av("Under 4.5 gols (total do jogo)", 0.7017),
        _av("Over 2.5 gols (total do jogo)", 0.7016),
    ]
    sel = selecionar_linha_operacional(aves)
    # maior previsao estatistica: a linha extrema, MARCADA
    assert sel.maior_prob.linha.startswith("Under 7.5")
    assert not sel.util_maior_prob.utilizavel
    assert sel.util_maior_prob.marca == MARCA_BAIXA_UTILIDADE
    # aposta pratica: a maior prob ENTRE as utilizaveis (odd justa
    # 1.14 < 1.15 exclui a Over 1.5 de 87.67%)
    assert sel.operacional.linha.startswith("Under 4.5")
    assert sel.util_operacional.odd_justa == 1.43
    assert not sel.nenhuma_operacional
    assert ODD_REAL_INDISPONIVEL in sel.motivo_operacional


def test_seguranca_nao_e_rebaixada_para_conseguir_odd_maior():
    # duas utilizaveis: 0.80 (odd justa 1.25) e 0.75 (1.33). A regra
    # NAO pode escolher a de odd maior derrubando a probabilidade.
    aves = [
        _av("Under 4.5 gols (total do jogo)", 0.75),
        _av("Under 3.5 gols (total do jogo)", 0.80),
    ]
    sel = selecionar_linha_operacional(aves)
    assert sel.operacional.prob == 0.80


def test_desempate_por_confianca_e_amostra():
    a = _av("Under 3.5 gols (total do jogo)", 0.80, conf=0.88)
    b = _av("Over 1.5 gols (total do jogo)", 0.80, conf=0.92)
    sel = selecionar_linha_operacional([a, b])
    assert sel.operacional is b  # mesma prob: confianca decide

    c = _av("Under 2.5 gols (total do jogo)", 0.80, conf=0.92,
            amostra=0.5)
    d = _av("Under 3.5 gols (total do jogo)", 0.80, conf=0.92)
    sel2 = selecionar_linha_operacional([c, d])
    assert sel2.operacional is d  # confianca empatada: amostra decide


def test_nenhuma_utilizavel():
    sel = selecionar_linha_operacional(
        [_av("Over 0.5 gols (total do jogo)", 0.96)]
    )
    assert sel.maior_prob is not None
    assert sel.maior_prob.prob == 0.96
    assert sel.operacional is None
    assert sel.nenhuma_operacional
    assert sel.motivo_operacional is None


def test_fora_da_disciplina_nao_entra_no_pool():
    sel = selecionar_linha_operacional(
        [
            _av("Over 1.5 gols (total do jogo)", 0.55),   # abaixo da janela
            _av("Under 3.5 gols (total do jogo)", 0.99),  # acima do teto
            _av("Under 4.5 gols (total do jogo)", 0.69, conf=0.50),  # conf
        ]
    )
    assert sel.pool == []
    assert sel.maior_prob is None and sel.operacional is None


def test_probabilidade_ultrapassa_o_score():
    # v2 (correcao da ordem, 08/09/2026): o ranking externo por score
    # NAO comanda mais - entre as utilizaveis vence a de MAIOR
    # probabilidade mesmo que o score prefira outra.
    a = _av("Under 4.5 gols (total do jogo)", 0.80)
    b = _av("Under 3.5 gols (total do jogo)", 0.75)
    sel = selecionar_linha_operacional([a, b], ordem=[b, a])
    assert sel.operacional is a
    assert "maior probabilidade" in sel.motivo_operacional


def test_ordem_externa_so_desempata_por_ultimo():
    # mesmo ranking externo com preferencia CONTRARIA: com probabilidades
    # IGUAIS o score desempata; com probabilidades diferentes ele perde.
    a = _av("Under 4.5 gols (total do jogo)", 0.80)
    b = _av("Under 3.5 gols (total do jogo)", 0.80)
    sel = selecionar_linha_operacional([a, b], ordem=[b, a])
    assert sel.operacional is b  # empate total: desempate final pelo score


def test_brugge_over_15_e_principal_sobre_under_45():
    # regressao real apontada pelo operador: Over 1.5 (81.94%, odd justa
    # 1.22) tem probabilidade MAIOR que Under 4.5 (79.30%, 1.26) - mesmo
    # que um ranking de score prefira a Under, a Over e a principal.
    over15 = _av("Over 1.5 gols (total do jogo)", 0.8194, conf=0.92,
                 lam=3.13)
    under45 = _av("Under 4.5 gols (total do jogo)", 0.7930, conf=0.92,
                 lam=3.13)
    sel = selecionar_linha_operacional(
        [over15, under45], ordem=[under45, over15]
    )
    assert sel.operacional is over15
    assert sel.util_operacional.odd_justa == 1.22


def test_dortmund_under_45_e_principal_sobre_over_15():
    # regressao real apontada pelo operador: Under 4.5 (84.69%, odd justa
    # 1.18) tem probabilidade MAIOR que Over 1.5 (76.97%, 1.30) - a
    # Under e a principal mesmo com score preferindo a Over.
    under45 = _av("Under 4.5 gols (total do jogo)", 0.8469, conf=0.84,
                 lam=2.80)
    over15 = _av("Over 1.5 gols (total do jogo)", 0.7697, conf=0.84,
                 lam=2.80)
    sel = selecionar_linha_operacional(
        [under45, over15], ordem=[over15, under45]
    )
    assert sel.operacional is under45
    assert sel.util_operacional.odd_justa == 1.18


def test_odd_real_da_fonte_muda_a_escolha():
    aves = [
        _av("Under 7.5 gols (total do jogo)", 0.9681),
        _av("Under 4.5 gols (total do jogo)", 0.7017),
    ]
    # casa paga 1.30 na linha extrema: ela vira utilizavel e, sendo a de
    # maior probabilidade, vira a principal
    sel = selecionar_linha_operacional(
        aves, odds_reais={"Under 7.5 gols (total do jogo)": 1.30}
    )
    assert sel.operacional is aves[0]
    assert sel.util_operacional.odd_real == 1.30
    # casa paga 1.10 na linha util: ela deixa de ser utilizavel
    sel2 = selecionar_linha_operacional(
        aves, odds_reais={"Under 4.5 gols (total do jogo)": 1.10}
    )
    assert sel2.operacional is None and sel2.nenhuma_operacional


# ----------------------------------------------------------------------
# Integracao com a politica permanente (selecionar_melhor v2)
# ----------------------------------------------------------------------
def test_selecionar_melhor_escolhe_a_linha_operacional():
    hist = _hist()
    aves = [
        _av("Under 7.5 gols (total do jogo)", 0.9681),
        _av("Under 4.5 gols (total do jogo)", 0.7017),
    ]
    sel = selecionar_melhor(aves, hist, _BENCH, _BENCH)
    assert sel.melhor is not None
    assert sel.melhor.avaliacao.prob == 0.7017
    assert sel.operacional is not None
    assert sel.operacional.operacional is aves[1]
    # a linha extrema continua ESTATISTICAMENTE aprovada no ranking
    assert any(
        c.avaliacao.prob == 0.9681 for c in sel.comparadas
    )


def test_selecionar_melhor_reprova_sem_utilidade_operacional():
    hist = _hist()
    sel = selecionar_melhor(
        [_av("Over 0.5 gols (total do jogo)", 0.96, lam=3.4)],
        hist, _BENCH, _BENCH,
    )
    assert sel.melhor is None
    assert NENHUMA_UTIL_MSG in sel.reprovacao
    assert "utilidade pratica" in sel.reprovacao


# ----------------------------------------------------------------------
# Apresentacao
# ----------------------------------------------------------------------
def test_bloco_do_relatorio_mostra_as_duas_linhas():
    aves = [
        _av("Under 7.5 gols (total do jogo)", 0.9681),
        _av("Under 4.5 gols (total do jogo)", 0.7017),
    ]
    linhas = format_bloco_operacional(selecionar_linha_operacional(aves))
    texto = "\n".join(linhas)
    assert "LINHA DE MAIOR PROBABILIDADE" in texto
    assert "Under 7.5" in texto
    assert MARCA_BAIXA_UTILIDADE in texto
    assert "MELHOR LINHA OPERACIONAL" in texto
    assert "Under 4.5" in texto
    assert ODD_REAL_INDISPONIVEL in texto
    assert "odd justa" in texto


def test_bloco_do_relatorio_sem_utilidade():
    sel = selecionar_linha_operacional(
        [_av("Over 0.5 gols (total do jogo)", 0.96)]
    )
    texto = "\n".join(format_bloco_operacional(sel))
    assert NENHUMA_UTIL_MSG in texto
    assert "MELHOR LINHA OPERACIONAL" not in texto


def test_bloco_vazio_sem_linha_disciplinada():
    sel = selecionar_linha_operacional([])
    assert format_bloco_operacional(sel) == []