"""Testes do BLOCO RESULTADO (src/resultado.py + liquidacao em
src/settlement.py): 1X2, Dupla Chance, DNB e Handicap Asiatico pre-jogo.

Matriz exigida pela regra de validacao da ampliacao de mercados:
  - favorito e azarao (lambda assimetrico nos dois sentidos);
  - empate;
  - vitoria/derrota por margem exata (1 e 2 gols);
  - linhas inteiras (0.0, 1.0), de meio gol (0.5, 1.5) e de quarto
    (0.25, 0.75) - GANHA/PERDIDA/DEVOLVIDA/MEIA VITORIA/MEIA DERROTA;
  - convencao de sinal (mandante x visitante);
  - None/ausente nunca vira zero (sem historico => sem avaliacao;
    placar final ausente => NAO AVALIAVEL);
  - consistencia avaliacao x liquidacao: TODA linha produzida por
    src/resultado.py e liquidadavel pelo texto EXATO congelado.
"""

from types import SimpleNamespace

import pytest

from src.handicap import settle
from src.live_opportunity import poisson_pmf
from src.resultado import (
    avaliar_resultado_prejogo,
    distribuicao_margem_prejogo,
    fmt_ah,
    lambdas_prejogo,
    linhas_ah_prejogo,
    prob_1x2,
    prob_ah,
    prob_dnb,
    prob_dupla_chance,
)
from src.settlement import _e_linha_resultado, _liquidar_resultado


# ----------------------------------------------------------------------
# Helpers (mesmo formato dos jogos reais do motor)
# ----------------------------------------------------------------------
def _game(gols_pro, gols_contra, casa=True):
    return SimpleNamespace(
        played_at_home=casa,
        corners_for=6, corners_against=4,
        goals_for=gols_pro, goals_against=gols_contra,
        yellow_for=2,
    )


def _hist(n=10, pro_h=2.0, contra_h=1.0, pro_a=1.0, contra_a=1.2):
    """Mandante forte em casa; visitante fraco fora (quando pro_a baixo)."""
    games_h = [_game(pro_h, contra_h, casa=True) for _ in range(n)]
    games_a = [_game(pro_a, contra_a, casa=False) for _ in range(n)]
    return {
        "games_home": games_h, "games_away": games_a,
        "n_home": n, "n_away": n,
    }


_BENCH = {"partidas_validas": 100, "describe": {"media": 2.7}}


# ----------------------------------------------------------------------
# Avaliacao: lambdas e distribuicao
# ----------------------------------------------------------------------
def test_lambdas_cruzamento_e_benchmark():
    lam_h, lam_a, det = lambdas_prejogo(_hist(), _BENCH)
    # cruzamento: mandante (2.0 + 1.2)/2 = 1.6; visitante (1.0+1.0)/2=1.0
    # mix 65/35 com media da liga 2.7 => 1.35 por lado:
    # mandante 0.65*1.6 + 0.35*1.35 = 1.5325
    assert lam_h == pytest.approx(0.65 * 1.6 + 0.35 * 1.35)
    assert lam_a == pytest.approx(0.65 * 1.0 + 0.35 * 1.35)
    assert "cruzamento" in det and "benchmark" in det


def test_lambdas_sem_historico_nunca_inventa():
    lam_h, lam_a, _ = lambdas_prejogo(
        {"games_home": [], "games_away": []}, _BENCH)
    assert lam_h is None and lam_a is None
    # e a avaliacao devolve lista VAZIA (nunca completa a amostra)
    assert avaliar_resultado_prejogo(
        {"games_home": [], "games_away": [], "n_home": 0, "n_away": 0},
        _BENCH,
    ) == []


def test_distribuicao_margem_soma_um_e_simetrica():
    # lambdas iguais => P(mandante) == P(visitante); massa total ~1
    dist = distribuicao_margem_prejogo(1.4, 1.4)
    p1, px, p2 = prob_1x2(dist)
    assert p1 == pytest.approx(p2)
    assert p1 + px + p2 == pytest.approx(1.0, abs=1e-6)


def test_distribuicao_confere_com_poisson_direto():
    # P(margem exatamente 0) = soma_i P(i,i) dos Poissons, RENORMALIZADA
    # pela massa truncada em 8 gols por lado
    lam_h, lam_a = 1.8, 0.9
    dist = distribuicao_margem_prejogo(lam_h, lam_a)
    bruto_0 = sum(
        poisson_pmf(i, lam_h) * poisson_pmf(i, lam_a) for i in range(9)
    )
    massa = sum(
        poisson_pmf(i, lam_h) * poisson_pmf(j, lam_a)
        for i in range(9) for j in range(9)
    )
    assert dist[0] == pytest.approx(bruto_0 / massa, abs=1e-12)
    assert sum(dist.values()) == pytest.approx(1.0, abs=1e-12)


# ----------------------------------------------------------------------
# Avaliacao: 1X2 / Dupla Chance / DNB
# ----------------------------------------------------------------------
def test_favorito_mandante_e_simetrico_visitante():
    # mandante forte em casa, visitante fraco fora...
    aves = avaliar_resultado_prejogo(
        _hist(pro_h=2.4, contra_h=1.0, pro_a=0.8, contra_a=1.8), _BENCH)
    por = {a.linha: a.prob for a in aves}
    assert por["Vitoria mandante (1)"] > por["Vitoria visitante (2)"]
    assert por["Dupla chance 1X"] > por["Dupla chance X2"]

    # ...e o ESPELHO EXATO das entradas do cruzamento (pro/contra
    # trocados de lado): visitante forte, mandante fraco
    aves2 = avaliar_resultado_prejogo(
        _hist(pro_h=0.8, contra_h=1.8, pro_a=2.4, contra_a=1.0), _BENCH)
    por2 = {a.linha: a.prob for a in aves2}
    assert por2["Vitoria visitante (2)"] > por2["Vitoria mandante (1)"]
    # simetria do modelo: cenario espelhado inverte 1 e 2
    assert por2["Vitoria visitante (2)"] == pytest.approx(
        por["Vitoria mandante (1)"], abs=1e-9
    )
    assert por2["DNB visitante (empate anula)"] == pytest.approx(
        por["DNB mandante (empate anula)"], abs=1e-9
    )


def test_1x2_soma_um_e_dupla_chance_e_soma_dos_pares():
    dist = distribuicao_margem_prejogo(1.6, 1.1)
    p1, px, p2 = prob_1x2(dist)
    assert p1 + px + p2 == pytest.approx(1.0, abs=1e-6)
    dc = prob_dupla_chance(dist)
    assert dc["1X"] == pytest.approx(p1 + px, abs=1e-9)
    assert dc["12"] == pytest.approx(p1 + p2, abs=1e-9)
    assert dc["X2"] == pytest.approx(px + p2, abs=1e-9)


def test_dnb_par_e_equivale_ah_0():
    dist = distribuicao_margem_prejogo(1.6, 1.1)
    p_casa = prob_dnb(dist, "mandante")
    p_fora = prob_dnb(dist, "visitante")
    p1, px, p2 = prob_1x2(dist)
    # convencao uniforme: empate devolve e NAO conta; o par soma o
    # total MENOS a massa do empate (que devolve o stake dos dois)
    assert p_casa == pytest.approx(p1, abs=1e-12)
    assert p_fora == pytest.approx(p2, abs=1e-12)
    assert p_casa + p_fora == pytest.approx(1.0 - px, abs=1e-12)
    # regra do operador: DNB = AH 0.0 => MESMO numero (mesmo mercado)
    assert p_casa == pytest.approx(prob_ah(dist, "mandante", 0.0), abs=1e-12)
    assert p_fora == pytest.approx(prob_ah(dist, "visitante", 0.0), abs=1e-12)


# ----------------------------------------------------------------------
# Avaliacao: AH (convencao de probabilidade e monotonicidade)
# ----------------------------------------------------------------------
def test_ah_favorito_monotonia_e_margem_exata():
    dist = distribuicao_margem_prejogo(2.2, 0.7)
    # linhas que o favorito RECEBE ficam mais faceis conforme crescem
    # (nao-decrescente: devolucao nao conta, linhas vizinhas podem empatar)
    pos = [prob_ah(dist, "mandante", l)
           for l in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5)]
    assert pos == sorted(pos)
    # linhas que o favorito PAGA ficam mais dificeis conforme crescem
    neg = [prob_ah(dist, "mandante", l)
           for l in (-0.25, -0.5, -0.75, -1.0, -1.25, -1.5)]
    assert neg == sorted(neg, reverse=True)

    # linha -1.0 (vitoria por exatamente 1 devolve): convencao
    # uniforme => probabilidade = P(margem >= 2), devolucao nao conta
    p_m2 = sum(p for d, p in dist.items() if d >= 2)
    assert prob_ah(dist, "mandante", -1.0) == pytest.approx(p_m2, abs=1e-12)
    # AH -1.0 <= AH -0.75 (meia vitoria na margem exata 1)
    assert prob_ah(dist, "mandante", -1.0) <= prob_ah(dist, "mandante", -0.75)


def test_ah_linha_de_quarto_e_meia_sem_devolucao():
    dist = distribuicao_margem_prejogo(2.0, 0.8)
    # -0.75: vitoria por 1 => meia vitoria (metade -1.0 push, metade
    # -0.5 vitoria); prob = P(win por 2+) + 0.5*P(win por exatamente 1)
    p_ge2 = sum(p for d, p in dist.items() if d >= 2)
    p_eq1 = dist.get(1, 0.0)
    assert prob_ah(dist, "mandante", -0.75) == pytest.approx(
        p_ge2 + 0.5 * p_eq1, abs=1e-9
    )
    # -0.5 (meia): nao existe devolucao; prob = P(vitoria mandante)
    p1, _, _ = prob_1x2(dist)
    assert prob_ah(dist, "mandante", -0.5) == pytest.approx(p1, abs=1e-9)


def test_ah_perspectiva_do_lado_azarao():
    dist = distribuicao_margem_prejogo(0.8, 2.0)  # visitante favorito
    # AH visitante -1.0 (pede vitoria do VISITANTE por 2) contra
    # AH mandante +1.0 (mandante recebe +1): liquidacoes espelhadas,
    # probabilidades iguais por simetria do modelo
    assert prob_ah(dist, "visitante", -1.0) == pytest.approx(
        prob_ah(distribuicao_margem_prejogo(2.0, 0.8),
                "mandante", -1.0),
        abs=1e-9,
    )
    # azarao +0.25: empate => meia vitoria (metade 0.0 push, metade
    # +0.5 vitoria); prob inclui 0.5*P(empate)
    px = distribuicao_margem_prejogo(0.8, 2.0).get(0, 0.0)
    p_azarao = prob_ah(dist, "mandante", 0.25)
    assert p_azarao >= 0.5 * px


# ----------------------------------------------------------------------
# Consistencia avaliacao x liquidacao (texto da linha E liquidadavel)
# ----------------------------------------------------------------------
def test_toda_linha_produzida_e_liquidadavel_pelo_texto_exato():
    aves = avaliar_resultado_prejogo(_hist(), _BENCH)
    assert aves, "cenario com sustentacao deve produzir avaliacoes"
    linhas = [a.linha for a in aves]
    # sem duplicatas
    assert len(linhas) == len(set(linhas))
    for linha in linhas:
        assert _e_linha_resultado(linha), f"linha sem regra: {linha!r}"
        # liquidacao empatada deve ser possivel para qualquer placar
        for gh, ga in ((0, 0), (1, 0), (0, 1), (2, 0), (2, 2), (1, 3)):
            res, _ = _liquidar_resultado(linha, gh, ga)
            assert res in ("GANHA", "PERDIDA", "DEVOLVIDA",
                           "MEIA VITÓRIA", "MEIA DERROTA")


def test_formato_e_campos_da_avaliacao():
    aves = avaliar_resultado_prejogo(_hist(), _BENCH)
    for a in aves:
        assert a.mercado == "resultado"
        assert 0.0 <= a.prob <= 1.0
        assert a.confianca > 0
        assert a.sustentacao["modelo"].startswith("Poisson")
        assert any("estimativa" in r for r in a.riscos)
    # leque de AH por lado: 0.0 + 6 magnitudes x 2 sinais = 13
    assert len(linhas_ah_prejogo()) == 13
    assert fmt_ah(0.0) == "0.0"
    assert fmt_ah(-0.75) == "-0.75"
    assert fmt_ah(1.0) == "+1"


# ----------------------------------------------------------------------
# Liquidacao: matriz completa (placares exatos x linhas)
# ----------------------------------------------------------------------
_L = _liquidar_resultado


def test_1x2_ganha_perdida_nos_tres_resultados():
    assert _L("Vitoria mandante (1)", 2, 1)[0] == "GANHA"
    assert _L("Vitoria mandante (1)", 1, 2)[0] == "PERDIDA"
    assert _L("Vitoria mandante (1)", 1, 1)[0] == "PERDIDA"
    assert _L("Empate (X)", 1, 1)[0] == "GANHA"
    assert _L("Empate (X)", 2, 1)[0] == "PERDIDA"
    assert _L("Vitoria visitante (2)", 0, 1)[0] == "GANHA"
    assert _L("Vitoria visitante (2)", 3, 1)[0] == "PERDIDA"


def test_dupla_chance_cobre_empate_e_vitorias():
    assert _L("Dupla chance 1X", 1, 1)[0] == "GANHA"   # empate coberto
    assert _L("Dupla chance 1X", 0, 2)[0] == "PERDIDA"
    assert _L("Dupla chance X2", 1, 3)[0] == "GANHA"
    assert _L("Dupla chance X2", 2, 1)[0] == "PERDIDA"
    assert _L("Dupla chance 12", 2, 1)[0] == "GANHA"
    assert _L("Dupla chance 12", 0, 0)[0] == "PERDIDA"  # empate perde


def test_dnb_empate_devolve_e_equivale_ah_0():
    assert _L("DNB mandante (empate anula)", 2, 1)[0] == "GANHA"
    assert _L("DNB mandante (empate anula)", 1, 2)[0] == "PERDIDA"
    assert _L("DNB mandante (empate anula)", 1, 1)[0] == "DEVOLVIDA"
    assert _L("DNB visitante (empate anula)", 0, 3)[0] == "GANHA"
    assert _L("DNB visitante (empate anula)", 1, 1)[0] == "DEVOLVIDA"
    # mesmo mercado, mesma liquidacao
    assert _L("AH mandante 0.0 (90 minutos)", 1, 1)[0] == "DEVOLVIDA"
    assert _L("AH visitante 0.0 (90 minutos)", 1, 1)[0] == "DEVOLVIDA"


def test_ah_linha_inteira_devolucao_na_margem_exata():
    # favorito -1.0: vitoria por exatamente 1 => DEVOLVIDA
    assert _L("AH mandante -1.0 (90 minutos)", 2, 1)[0] == "DEVOLVIDA"
    # vitoria por 2 => GANHA; empate/derrota => PERDIDA
    assert _L("AH mandante -1.0 (90 minutos)", 3, 1)[0] == "GANHA"
    assert _L("AH mandante -1.0 (90 minutos)", 1, 1)[0] == "PERDIDA"
    assert _L("AH mandante -1.0 (90 minutos)", 0, 1)[0] == "PERDIDA"
    # azarao +1.0: derrota por exatamente 1 => DEVOLVIDA
    assert _L("AH mandante +1.0 (90 minutos)", 0, 1)[0] == "DEVOLVIDA"
    assert _L("AH mandante +1.0 (90 minutos)", 1, 1)[0] == "GANHA"
    assert _L("AH mandante +1.0 (90 minutos)", 0, 2)[0] == "PERDIDA"


def test_ah_linha_de_meio_gol_nunca_devolve():
    assert _L("AH mandante -1.5 (90 minutos)", 2, 1)[0] == "PERDIDA"
    assert _L("AH mandante -1.5 (90 minutos)", 3, 1)[0] == "GANHA"
    assert _L("AH mandante -0.5 (90 minutos)", 1, 1)[0] == "PERDIDA"
    assert _L("AH mandante -0.5 (90 minutos)", 2, 1)[0] == "GANHA"


def test_ah_quarto_mandante_meia_vitoria_e_meia_derrota():
    res, nota = _L("AH mandante -0.75 (90 minutos)", 2, 1)
    assert res == "MEIA VITÓRIA"
    assert "-1.0" in nota and "-0.5" in nota  # divisao em duas metades
    # -0.25 empate: metade -0.5 (perde), metade 0.0 (devolve)
    res, _ = _L("AH mandante -0.25 (90 minutos)", 1, 1)
    assert res == "MEIA DERROTA"
    # +0.25 empate: metade 0.0 (devolve), metade +0.5 (ganha)
    res, _ = _L("AH mandante +0.25 (90 minutos)", 1, 1)
    assert res == "MEIA VITÓRIA"
    # +0.75 derrota por 1: metade +0.5 (perde), metade +1.0 (devolve)
    res, _ = _L("AH mandante +0.75 (90 minutos)", 0, 1)
    assert res == "MEIA DERROTA"


def test_ah_quarto_visitante_convencao_de_sinal():
    # visitante +0.75 com derrota do visitante por 1:
    # margem visitante -1 => metade +0.5 (perde), +1.0 (devolve)
    res, nota = _L("AH visitante +0.75 (90 minutos)", 1, 0)
    assert res == "MEIA DERROTA"
    assert "margem -1" in nota  # margem na perspectiva do LADO apostado
    # visitante -0.75 com vitoria do visitante por exatamente 1:
    # MEIA VITÓRIA (vitoria por 2 ja seria vitoria integral)
    res, _ = _L("AH visitante -0.75 (90 minutos)", 0, 1)
    assert res == "MEIA VITÓRIA"
    assert _L("AH visitante -0.75 (90 minutos)", 0, 2)[0] == "GANHA"


def test_liquidacao_bate_com_o_motor_validado_do_handicap():
    # a liquidacao do registro usa o MESMO settle do src/handicap.py
    for linha in (-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0):
        for margem in (-2, -1, 0, 1, 2):
            esperado = settle(linha, margem).rotulo
            res, _ = _L(f"AH mandante {fmt_ah(linha)} (90 minutos)",
                        margem, 0)
            mapa = {"vitoria integral": "GANHA",
                    "meia vitoria": "MEIA VITÓRIA",
                    "devolucao": "DEVOLVIDA",
                    "meia derrota": "MEIA DERROTA",
                    "derrota": "PERDIDA"}
            assert res == mapa[esperado]


def test_linha_fora_da_familia_resultado_nao_liquida_aqui():
    assert not _e_linha_resultado("Over 9.5 escanteios (total do jogo)")
    assert not _e_linha_resultado("Over 2.5 gols (total do jogo)")
    assert not _e_linha_resultado("AH mandante -0.75")  # sem (90 minutos)
    assert not _e_linha_resultado("DNB mandante")      # formato incompleto
    assert _liquidar_resultado("qualquer coisa", 1, 0)[0] is None


# ----------------------------------------------------------------------
# End-to-end: liquidar_pendentes com o REGISTRO real (snapshot intacto)
# ----------------------------------------------------------------------
def _fixture_raw(status="FT", gh=None, ga=None, fid=1000):
    return [{
        "fixture": {"id": fid, "date": "2026-09-07T16:00:00-03:00",
                     "status": {"short": status, "elapsed": 90}},
        "league": {"id": 135, "name": "Serie A", "season": 2026},
        "teams": {"home": {"id": 1, "name": "A"},
                  "away": {"id": 2, "name": "B"}},
        "goals": {"home": gh, "away": ga},
    }]


class _ClientFixo:
    """Cliente fake: /fixtures (placar) e /fixtures/statistics."""

    def __init__(self, fixture_raw, statistics_raw=None):
        self._fixture = fixture_raw
        self._stats = statistics_raw or []

    def get(self, endpoint, params=None):
        if endpoint == "/fixtures":
            return self._fixture
        if endpoint == "/fixtures/statistics":
            return self._stats
        raise AssertionError(f"endpoint inesperado: {endpoint}")


def test_liquidacao_resultado_no_registro_meia_vitoria(tmp_path):
    from src.registry import RegistroRecomendacoes
    from src.settlement import liquidar_pendentes

    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rec_id = reg.registrar(
        fixture_id=1000, tipo="prejogo", mercado="resultado",
        linha="AH mandante -0.75 (90 minutos)", probabilidade=0.62,
        versao_analise="bloco-resultado-validacao",
    )
    # vitoria do mandante por exatamente 1: meia vitoria
    client = _ClientFixo(_fixture_raw(gh=2, ga=1))
    report = liquidar_pendentes(client, reg)

    assert report["itens"][0]["situacao"] == "MEIA VITÓRIA"
    rec = reg.obter(rec_id)
    assert rec["resultado_mercado"] == "MEIA VITÓRIA"
    assert rec["placar_final"] == "2-1"
    assert rec["probabilidade"] == pytest.approx(0.62)  # previsao intacta


def test_liquidacao_resultado_placar_ausente_nao_avaliavel(tmp_path):
    from src.registry import RegistroRecomendacoes
    from src.settlement import liquidar_pendentes

    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rec_id = reg.registrar(
        fixture_id=1000, tipo="prejogo", mercado="resultado",
        linha="Vitoria mandante (1)", probabilidade=0.55,
        versao_analise="bloco-resultado-validacao",
    )
    # jogo encerrado SEM placar valido na fonte: nunca zero, nunca
    # inferido => NAO AVALIAVEL
    client = _ClientFixo(_fixture_raw(gh=None, ga=None))
    report = liquidar_pendentes(client, reg)

    assert report["itens"][0]["situacao"] == "NÃO AVALIÁVEL"
    assert reg.obter(rec_id)["resultado_mercado"] == "NÃO AVALIÁVEL"


def test_liquidacao_mista_resultado_e_total_sem_interferencia(tmp_path):
    from src.registry import RegistroRecomendacoes
    from src.settlement import liquidar_pendentes

    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    id_res = reg.registrar(
        fixture_id=1000, tipo="prejogo", mercado="resultado",
        linha="Dupla chance X2", probabilidade=0.61,
        versao_analise="bloco-resultado-validacao",
    )
    id_ou = reg.registrar(
        fixture_id=1000, tipo="prejogo", mercado="gols",
        linha="Under 3.5 gols (total do jogo)", probabilidade=0.845,
        versao_analise="prejogo-op-1.0-observacao",
    )
    stats = [
        {"team": {"id": 1, "name": "A"},
         "statistics": [{"type": "Corner Kicks", "value": "5"}]},
        {"team": {"id": 2, "name": "B"},
         "statistics": [{"type": "Corner Kicks", "value": "4"}]},
    ]
    # 1-1: X2 ganha (empate coberto) e Under 3.5 ganha (total 2)
    client = _ClientFixo(_fixture_raw(gh=1, ga=1), stats)
    report = liquidar_pendentes(client, reg)

    por_id = {i["id"]: i for i in report["itens"]}
    assert por_id[id_res]["situacao"] == "GANHA"
    assert por_id[id_ou]["situacao"] == "GANHA"