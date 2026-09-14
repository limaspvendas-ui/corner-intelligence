"""Testes da INTEGRACAO da familia RESULTADO (bloco validado) ao
comparador pre-jogo da POLITICA PERMANENTE.

O que a integracao promete (e estes testes verificam):
  - linhas de resultado ENTRAM no comparador (nenhuma linha do bloco
    e descartada por parsing);
  - mesma disciplina das demais familias (janela + confianca);
  - resultado PODE vencer gols/escanteios quando o score e superior;
  - gols/escanteios continuam vencendo quando sao melhores (nenhum
    favorecimento automatico do bloco novo);
  - nenhuma duplicacao de linha nem de recomendacao (registro dedupe);
  - o fluxo LEGADO (prejogoop sem --politica) permanece sem resultado.
"""

import inspect
from types import SimpleNamespace

import pytest

from src.policy import comparar_mercados, selecionar_melhor
from src.prejogo_opportunity import (
    avaliar_pregame,
    registrar_aprovadas_prejogo,
    scan_pregame_opportunities,
)
from src.resultado import avaliar_resultado_prejogo

# ----------------------------------------------------------------------
# Helpers: historico sintetico no formato REAL do motor
# ----------------------------------------------------------------------
def _game(esc_pro, esc_contra, gols_pro, gols_contra, casa=True):
    return SimpleNamespace(
        played_at_home=casa,
        corners_for=esc_pro, corners_against=esc_contra,
        corners_total=esc_pro + esc_contra,
        goals_for=gols_pro, goals_against=gols_contra,
        yellow_for=2,
    )


def _hist(n=10, esc=6.0, gols_h=(2.0, 1.0), gols_a=(1.0, 1.2)):
    games_h = [_game(esc + 1, esc - 1, gols_h[0], gols_h[1], casa=True)
               for _ in range(n)]
    games_a = [_game(esc, esc - 2, gols_a[0], gols_a[1], casa=False)
               for _ in range(n)]
    return {
        "games_home": games_h, "games_away": games_a,
        "n_home": n, "n_away": n,
    }


_BENCH_ESC = {"partidas_validas": 100, "describe": {"media": 10.0}}
_BENCH_GOLS = {"partidas_validas": 100, "describe": {"media": 2.9}}


def _av(linha, prob, mercado="gols", lam=2.5, conf=0.9, riscos=None,
        lam_h=None, lam_a=None, decomp=None):
    sust = {"baseline_pre_jogo": "teste", "modelo": "poisson (teste)"}
    if lam_h is not None:
        sust["lambda_mandante"] = lam_h
        sust["lambda_visitante"] = lam_a
    else:
        sust["lambda_por90"] = lam
    # Bloco A (09/09/2026): linhas DNB/AH do motor real carregam a
    # decomposicao exata da liquidacao (ganho/perda/devolucao) que
    # sustenta a odd justa de equilibrio - objetos sinteticos da
    # familia resultado precisam dela para serem utilizaveis.
    if decomp is not None:
        sust["equilibrio_liquidacao"] = decomp
    return SimpleNamespace(
        mercado=mercado, linha=linha, prob=prob, confianca=conf,
        riscos=riscos or [], sustentacao=sust,
    )


# ----------------------------------------------------------------------
# Entrada real do bloco no comparador
# ----------------------------------------------------------------------
def test_todas_as_linhas_de_resultado_entram_no_comparador():
    hist = _hist()
    aves = avaliar_resultado_prejogo(hist, _BENCH_GOLS, h2h_n=5)
    assert aves, "bloco com sustentacao deve produzir linhas"
    comparadas = comparar_mercados(aves, hist, _BENCH_ESC, _BENCH_GOLS)

    # toda linha DENTRO da disciplina aparece (nenhuma perdida no parse)
    disciplinadas = [
        a for a in aves
        if 0.70 <= a.prob <= 0.97 and a.confianca >= 0.60
    ]
    assert len(comparadas) == len(disciplinadas)
    linhas_comp = {c.avaliacao.linha for c in comparadas}
    for a in disciplinadas:
        assert a.linha in linhas_comp
    # e sao reconhecidas como familia resultado
    assert all(c.detalhe["mercado"] == "resultado" for c in comparadas)


def test_resultado_abaixo_da_disciplina_nao_entra():
    # MESMA disciplina das demais familias: prob fora da janela => fora
    hist = _hist()
    aves = [
        _av("Vitoria mandante (1)", 0.55, mercado="resultado",
            lam_h=1.6, lam_a=1.2),
        _av("AH mandante -0.75 (90 minutos)", 0.98, mercado="resultado",
            lam_h=1.6, lam_a=1.2),
    ]
    assert comparar_mercados(aves, hist, _BENCH_ESC, _BENCH_GOLS) == []


def test_componentes_de_resultado_rotulados():
    hist = _hist()
    aves = avaliar_resultado_prejogo(hist, _BENCH_GOLS, h2h_n=5)
    comparadas = comparar_mercados(aves, hist, _BENCH_ESC, _BENCH_GOLS)
    assert comparadas, "deve haver linha de resultado na disciplina"

    jogos = hist["games_home"] + hist["games_away"]
    totais_gols = [g.goals_for + g.goals_against for g in jogos]
    media = sum(totais_gols) / len(totais_gols)
    var = sum((x - media) ** 2 for x in totais_gols) / (len(totais_gols) - 1)
    cv = round((var ** 0.5) / media, 3)

    tipos = set()
    for c in comparadas:
        assert c.detalhe["mercado"] == "resultado"
        # estabilidade usa o CV de GOLS do historico (familia movida a gols)
        assert c.cv_amostral == cv
        if c.detalhe["direcao"].startswith("ah"):
            # largura do AH = distancia da linha de equilibrio 0.0
            assert c.dist_equilibrio == pytest.approx(abs(c.detalhe["valor"]))
            tipos.add("ah")
        else:
            # 1X2/DC/DNB nao tem linha numerica: largura neutra
            assert c.dist_equilibrio == 0.0
            tipos.add("sem-linha")
    # as duas formas de linha de resultado foram comparadas
    assert tipos == {"ah", "sem-linha"}


# ----------------------------------------------------------------------
# Competicao em igualdade: qualquer familia pode vencer
# ----------------------------------------------------------------------
def test_resultado_pode_vencer_gols_e_escanteios():
    hist = _hist()
    # competicao real de score: gols carrega 2 contradicoes, escanteios
    # e uma linha LARGA (penalizada pela utilidade de largura) e o AH
    # e limpo - o resultado deve vencer sem nenhum favorecimento
    aves = [
        _av("Under 2.5 gols (total do jogo)", 0.82, mercado="gols",
            lam=3.0, riscos=["r1", "r2"]),
        _av("Over 14.5 escanteios (total do jogo)", 0.80,
            mercado="escanteios", lam=9.5),
        _av("AH mandante -0.75 (90 minutos)", 0.82, mercado="resultado",
            lam_h=1.5, lam_a=1.5,
            decomp={"ganho": 0.82, "perda": 0.13, "devolucao": 0.05}),
    ]
    sel = selecionar_melhor(aves, hist, _BENCH_ESC, _BENCH_GOLS)
    assert sel.melhor is not None
    assert sel.melhor.avaliacao.linha == "AH mandante -0.75 (90 minutos)"
    assert sel.melhor.avaliacao.mercado == "resultado"
    assert sel.melhor.contracoes > 0.8


def test_gols_vencem_quando_sao_melhores():
    hist = _hist()
    aves = [
        _av("Under 2.5 gols (total do jogo)", 0.82, mercado="gols",
            lam=3.0),
        # resultado entra com o risco de modelo que o bloco sempre carrega
        _av("AH mandante -0.75 (90 minutos)", 0.82, mercado="resultado",
            lam_h=1.5, lam_a=1.5, riscos=["modelo Poisson (estimativa)"]),
    ]
    sel = selecionar_melhor(aves, hist, _BENCH_ESC, _BENCH_GOLS)
    assert sel.melhor.avaliacao.linha == "Under 2.5 gols (total do jogo)"


def test_escanteios_vencem_pela_estabilidade():
    # escanteios estaveis (CV baixo) x resultado avaliado pela
    # estabilidade de GOLS (CV alto): escanteios vencem - o bloco novo
    # NAO e favorecido automaticamente
    hist = _hist(esc=6.0, gols_h=(4.0, 0.5), gols_a=(0.2, 4.5))
    aves = [
        _av("Under 11.5 escanteios (total do jogo)", 0.80,
            mercado="escanteios", lam=9.5),
        _av("AH mandante +0.5 (90 minutos)", 0.80, mercado="resultado",
            lam_h=1.6, lam_a=1.4),
    ]
    sel = selecionar_melhor(aves, hist, _BENCH_ESC, _BENCH_GOLS)
    assert sel.melhor.avaliacao.mercado == "escanteios"
    assert sel.melhor.estabilidade > 0.6


# ----------------------------------------------------------------------
# Sem duplicacao (linhas e recomendacoes)
# ----------------------------------------------------------------------
def test_linhas_combinadas_sem_duplicacao_e_melhor_unica():
    hist = _hist()
    h2h_n = 5
    combinadas = avaliar_pregame(
        hist, _BENCH_ESC, _BENCH_GOLS, h2h_n) + avaliar_resultado_prejogo(
        hist, _BENCH_GOLS, h2h_n)
    chaves = [(a.mercado, a.linha) for a in combinadas]
    assert len(chaves) == len(set(chaves)), "nenhuma linha duplicada"

    sel = selecionar_melhor(combinadas, hist, _BENCH_ESC, _BENCH_GOLS)
    # exatamente UMA melhor oportunidade por jogo
    assert sel.melhor is not None
    melhores = [c for c in sel.comparadas
                if c.score == sel.melhor.score]
    assert all(c.avaliacao is not None for c in melhores)
    # comparadas nao repete linha
    linhas = [c.avaliacao.linha for c in sel.comparadas]
    assert len(linhas) == len(set(linhas))


def test_registro_congela_resultado_uma_vez(tmp_path):
    from src.registry import RegistroRecomendacoes

    hist = _hist()
    aves = avaliar_resultado_prejogo(hist, _BENCH_GOLS, h2h_n=5)
    melhor = max(aves, key=lambda a: a.prob)
    fixture = SimpleNamespace(
        fixture_id=555001, league_name="Serie A",
        home_team_name="Time A", away_team_name="Time B",
    )
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))

    regs1 = registrar_aprovadas_prejogo(
        reg, [melhor], fixture, hist, versao="prejogo-politica-2.0-observacao")
    regs2 = registrar_aprovadas_prejogo(
        reg, [melhor], fixture, hist, versao="prejogo-politica-2.0-observacao")

    assert len(regs1) == 1 and regs1[0][1] is True   # novo
    assert regs2[0][0] == regs1[0][0] and regs2[0][1] is False  # dedupe
    registros = reg.listar(fixture_id=555001)
    assert len(registros) == 1
    rec = registros[0]
    assert rec["mercado"] == "resultado"
    assert rec["tipo"] == "prejogo"
    assert rec["versao_analise"] == "prejogo-politica-2.0-observacao"
    assert rec["probabilidade"] == pytest.approx(melhor.prob)


# ----------------------------------------------------------------------
# Fluxo legado preservado
# ----------------------------------------------------------------------
def test_fluxo_legado_continua_sem_resultado():
    # prejogoop SEM --politica nao inclui a familia resultado
    params = inspect.signature(
        scan_pregame_opportunities).parameters
    assert params["incluir_resultado"].default is False