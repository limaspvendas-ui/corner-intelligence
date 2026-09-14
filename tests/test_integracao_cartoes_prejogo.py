"""Testes da INTEGRACAO de TOTAL DE CARTOES (bloco validado e aprovado)
ao comparador pre-jogo da POLITICA PERMANENTE.

O que a integracao promete (e estes testes verificam):
  - linhas de cartoes ENTRAM no comparador com o SEU benchmark real de
    cartoes (nunca o de gols) e os SEUS totais amostrais em pontos;
  - MESMA disciplina das demais familias (janela + confianca): linha
    sem lambda/sustentacao nunca entra;
  - SEM PREFERENCIA ARTIFICIAL: evidencia identica => score identico;
    cartoes PODEM vencer quando o score e superior; gols/escanteios
    continuam vencendo quando sao melhores;
  - estabilidade de cartoes ignora jogos sem dado (nunca zero);
  - recomendacao de cartoes aprovada entra no registro imutavel
    normalmente (dedupe, linha liquidadavel pelo settlement validado);
  - o fluxo LEGADO (prejogoop sem --politica) permanece sem cartoes.

Nenhuma matematica de cartoes (src/cartoes.py) e alterada aqui - o
bloco aprovado e reusado integralmente.
"""

import inspect
from types import SimpleNamespace

import pytest

from src.cartoes import avaliar_cartoes_prejogo
from src.policy import comparar_mercados, selecionar_melhor
from src.prejogo_opportunity import (
    avaliar_pregame,
    registrar_aprovadas_prejogo,
    scan_pregame_opportunities,
)
from src.settlement import _MARCA_CONVENCAO_CARTOES, _parse_linha

# ----------------------------------------------------------------------
# Helpers: historico sintetico no formato REAL do motor
# ----------------------------------------------------------------------
def _game(esc_pro=3, esc_contra=3, gols_pro=2, gols_contra=1,
          yf=3, ya=3, rf=0, ra=0, casa=True):
    return SimpleNamespace(
        played_at_home=casa,
        corners_for=esc_pro, corners_against=esc_contra,
        corners_total=esc_pro + esc_contra,
        goals_for=gols_pro, goals_against=gols_contra,
        yellow_for=yf, yellow_against=ya,
        red_for=rf, red_against=ra,
    )


def _hist(n=10):
    # amarelos VARIADOS por jogo (cv de cartoes real, nao trivial);
    # escanteios e gols constantes
    games_h = [
        _game(yf=3 + (i % 2), ya=3, casa=True) for i in range(n)
    ]
    games_a = [
        _game(yf=2 + (i % 2), ya=2, casa=False) for i in range(n)
    ]
    return {"games_home": games_h, "games_away": games_a,
            "n_home": n, "n_away": n}


_BENCH_ESC = {"partidas_validas": 100, "describe": {"media": 4.0}}
_BENCH_GOLS = {"partidas_validas": 100, "describe": {"media": 2.9}}
_BENCH_CARTOES = {"partidas_validas": 100, "describe": {"media": 5.5}}


def _av(linha, prob, mercado="gols", lam=2.5, conf=0.9, riscos=None):
    sust = {"baseline_pre_jogo": "teste", "modelo": "poisson (teste)"}
    if lam is not None:
        sust["lambda_por90"] = lam
    return SimpleNamespace(
        mercado=mercado, linha=linha, prob=prob, confianca=conf,
        riscos=riscos or [], sustentacao=sust,
    )


def _cv_manual(totais):
    media = sum(totais) / len(totais)
    var = sum((x - media) ** 2 for x in totais) / (len(totais) - 1)
    return round((var ** 0.5) / media, 3)


# ----------------------------------------------------------------------
# Entrada real do bloco no comparador
# ----------------------------------------------------------------------
def test_cartoes_entram_no_comparador_com_benchmark_proprio():
    hist = _hist()
    aves = avaliar_pregame(
        hist, _BENCH_ESC, _BENCH_GOLS, h2h_n=5
    ) + avaliar_cartoes_prejogo(hist, _BENCH_CARTOES, h2h_n=5)
    comparadas = comparar_mercados(
        aves, hist, _BENCH_ESC, _BENCH_GOLS, _BENCH_CARTOES
    )

    cartoes = [c for c in comparadas if c.detalhe["mercado"] == "cartoes"]
    assert cartoes, "linhas de cartoes disciplinadas devem competir"
    for c in cartoes:
        # linha canonica: liquidadavel pelo settlement validado
        parsed = _parse_linha(c.avaliacao.linha)
        assert parsed is not None and parsed[2] == "cartoes"
        assert _MARCA_CONVENCAO_CARTOES in c.avaliacao.linha
        # benchmark usado = o de CARTOES (5.5), nunca o de gols (2.9)
        assert c.detalhe["benchmark_liga"] == pytest.approx(5.5)
        # estabilidade = cv dos TOTAIS EM PONTOS do historico
        totais = [
            float(g.yellow_for) + float(g.yellow_against)
            + 2.0 * (float(g.red_for) + float(g.red_against))
            for g in hist["games_home"] + hist["games_away"]
        ]
        assert c.cv_amostral == _cv_manual(totais)
    # as demais familias continuam presentes na mesma comparacao
    mercados = {c.detalhe["mercado"] for c in comparadas}
    assert {"escanteios", "gols", "cartoes"} <= mercados


def test_linha_de_cartoes_sem_lambda_nunca_entra():
    hist = _hist()
    aves = [_av(
        "Over 4.5 cartoes (amarelo=1, vermelho=2) (total do jogo)",
        0.80, mercado="cartoes", lam=None,
    )]
    # sem sustentacao (lambda) => fora: nada e inventado
    assert comparar_mercados(
        aves, hist, _BENCH_ESC, _BENCH_GOLS, _BENCH_CARTOES
    ) == []


def test_sem_benchmark_de_cartoes_nunca_usa_o_de_gols():
    hist = _hist()
    aves = [
        _av("Over 4.5 cartoes (amarelo=1, vermelho=2) (total do jogo)",
            0.80, mercado="cartoes", lam=4.0),
        _av("Under 2.5 gols (total do jogo)", 0.80, mercado="gols",
            lam=3.0),
    ]
    # benchmark de cartoes INDISPONIVEL: evidencia unica, nunca o de gols
    comparadas = comparar_mercados(aves, hist, _BENCH_ESC, _BENCH_GOLS,
                                   None)
    por_mercado = {c.detalhe["mercado"]: c for c in comparadas}
    assert por_mercado["cartoes"].detalhe["benchmark_liga"] is None
    assert por_mercado["cartoes"].aderencia == 0.85  # evidencia unica
    assert por_mercado["gols"].detalhe["benchmark_liga"] == pytest.approx(
        2.9
    )


def test_estabilidade_de_cartoes_exclui_jogos_sem_dado():
    hist = _hist()
    # 3 jogos do mandante SEM amarelos na fonte: fora da amostra (nunca
    # zero); escanteios continuam usando TODOS os 20 jogos
    for g in hist["games_home"][:3]:
        g.yellow_for = None

    aves = [_av("Over 2.5 cartoes (amarelo=1, vermelho=2) (total do jogo)",
                0.80, mercado="cartoes", lam=2.5),
            _av("Over 4.5 escanteios (total do jogo)", 0.80,
                mercado="escanteios", lam=4.0)]
    comparadas = comparar_mercados(
        aves, hist, _BENCH_ESC, _BENCH_GOLS, _BENCH_CARTOES
    )
    por_mercado = {c.detalhe["mercado"]: c for c in comparadas}

    validos = [
        float(g.yellow_for) + float(g.yellow_against)
        + 2.0 * (float(g.red_for) + float(g.red_against))
        for g in hist["games_home"] + hist["games_away"]
        if g.yellow_for is not None
    ]
    assert len(validos) == 17  # 20 - 3 excluidos
    assert por_mercado["cartoes"].cv_amostral == _cv_manual(validos)
    # escanteios: amostra cheia (o jogo sem CARTOES tem escanteios)
    assert por_mercado["escanteios"].cv_amostral == _cv_manual(
        [6.0] * 20
    )


# ----------------------------------------------------------------------
# Competicao em igualdade: SEM preferencia artificial
# ----------------------------------------------------------------------
def test_evidencia_identica_score_identico():
    """Demonstracao controlada: cartoes e escanteios com a MESMA
    probabilidade, MESMA distancia do equilibrio, MESMO benchmark
    (media identica), MESMA amostra (totais identicos por jogo) =>
    scores IDENTICOS. Nenhum bonus/penalidade por familia."""
    hist = _hist()
    # totais identicos por jogo nos dois mercados (6 por jogo): cv=0
    for g in hist["games_home"] + hist["games_away"]:
        g.corners_for, g.corners_against = 3, 3
        g.corners_total = 6
        g.yellow_for, g.yellow_against = 3, 3
        g.red_for = g.red_against = 0

    bench_igual = {"partidas_validas": 100, "describe": {"media": 4.0}}
    aves = [
        _av("Over 4.5 cartoes (amarelo=1, vermelho=2) (total do jogo)",
            0.80, mercado="cartoes", lam=4.0),
        _av("Over 4.5 escanteios (total do jogo)", 0.80,
            mercado="escanteios", lam=4.0),
    ]
    comparadas = comparar_mercados(
        aves, hist, bench_igual, _BENCH_GOLS, bench_igual
    )
    assert len(comparadas) == 2
    por_mercado = {c.detalhe["mercado"]: c for c in comparadas}
    assert por_mercado["cartoes"].score == por_mercado["escanteios"].score


def test_cartoes_podem_vencer_gols_e_escanteios():
    hist = _hist()
    # competicao real de score: gols carrega 2 contradicoes + aderencia
    # ruim (lambda 4.5 longe do benchmark 2.9); escanteios tem linha
    # LARGA (penalizada) e aderencia ruim; cartoes e limido e aderente
    # (lambda 4.0 = benchmark 4.0) - vence SEM favorecimento: o score e
    # objetivamente superior.
    bench_cartoes_aderente = {
        "partidas_validas": 100, "describe": {"media": 4.0},
    }
    aves = [
        _av("Under 2.5 gols (total do jogo)", 0.82, mercado="gols",
            lam=4.5, riscos=["r1", "r2"]),
        _av("Over 14.5 escanteios (total do jogo)", 0.80,
            mercado="escanteios", lam=9.5),
        _av("Over 4.5 cartoes (amarelo=1, vermelho=2) (total do jogo)",
            0.82, mercado="cartoes", lam=4.0),
    ]
    sel = selecionar_melhor(
        aves, hist, _BENCH_ESC, _BENCH_GOLS, bench_cartoes_aderente
    )
    assert sel.melhor is not None
    assert sel.melhor.avaliacao.mercado == "cartoes"
    assert sel.melhor.avaliacao.linha == (
        "Over 4.5 cartoes (amarelo=1, vermelho=2) (total do jogo)"
    )
    # o placar do score confirma: sem contradicoes e com aderencia cheia
    assert sel.melhor.contracoes == 1.0
    assert sel.melhor.aderencia == 1.0


def test_gols_vencem_quando_sao_melhores():
    # mesma probabilidade: cartoes com 2 contradicoes x gols limpo -
    # gols vence; a familia nova NAO e favorecida automaticamente
    hist = _hist()
    aves = [
        _av("Over 4.5 cartoes (amarelo=1, vermelho=2) (total do jogo)",
            0.80, mercado="cartoes", lam=4.0, riscos=["r1", "r2"]),
        _av("Under 2.5 gols (total do jogo)", 0.80, mercado="gols",
            lam=3.0),
    ]
    sel = selecionar_melhor(
        aves, hist, _BENCH_ESC, _BENCH_GOLS, _BENCH_CARTOES
    )
    assert sel.melhor.avaliacao.mercado == "gols"
    assert sel.melhor.contracoes == 1.0


# ----------------------------------------------------------------------
# Registro imutavel: recomendacao de cartoes aprovada entra normalmente
# ----------------------------------------------------------------------
def test_registro_congela_cartoes_uma_vez(tmp_path):
    from src.registry import RegistroRecomendacoes

    hist = _hist()
    aves = avaliar_cartoes_prejogo(hist, _BENCH_CARTOES, h2h_n=5)
    melhor = max(aves, key=lambda a: a.prob)
    fixture = SimpleNamespace(
        fixture_id=556001, league_name="Serie A",
        home_team_name="Time A", away_team_name="Time B",
    )
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))

    regs1 = registrar_aprovadas_prejogo(
        reg, [melhor], fixture, hist,
        versao="prejogo-politica-3.0-observacao")
    regs2 = registrar_aprovadas_prejogo(
        reg, [melhor], fixture, hist,
        versao="prejogo-politica-3.0-observacao")

    assert len(regs1) == 1 and regs1[0][1] is True   # novo
    assert regs2[0][0] == regs1[0][0] and regs2[0][1] is False  # dedupe
    registros = reg.listar(fixture_id=556001)
    assert len(registros) == 1
    rec = registros[0]
    assert rec["mercado"] == "cartoes"
    assert rec["tipo"] == "prejogo"
    assert rec["versao_analise"] == "prejogo-politica-3.0-observacao"
    assert rec["probabilidade"] == pytest.approx(melhor.prob)
    # linha congelada e liquidadavel pelo settlement validado
    parsed = _parse_linha(rec["linha"])
    assert parsed is not None and parsed[2] == "cartoes"
    assert _MARCA_CONVENCAO_CARTOES in rec["linha"]
    # a previsao original e imutavel (trigger do registro)
    import sqlite3
    with sqlite3.connect(str(tmp_path / "reg.db")) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE recomendacoes SET probabilidade = 0.99 "
                "WHERE id = ?",
                (rec["id"],),
            )


# ----------------------------------------------------------------------
# Fluxo legado preservado
# ----------------------------------------------------------------------
def test_fluxo_legado_continua_sem_cartoes():
    # prejogoop SEM --politica nao inclui a familia cartoes
    params = inspect.signature(
        scan_pregame_opportunities).parameters
    assert params["incluir_cartoes"].default is False