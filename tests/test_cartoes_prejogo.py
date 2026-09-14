"""TESTES do BLOCO PRE-JOGO DE CARTOES (calculo + benchmark da liga).

Bloco implementado apos a liquidacao validada: avalia linhas de TOTAL
de cartoes do jogo ANTES do jogo, SEM liberar recomendacao (nada e
aprovado, registrado ou comparado).

Valida:
    - baseline cruzado casa/fora em PONTOS (convencao amarelo=1,
      vermelho=2 - a MESMA da liquidacao);
    - jogo sem amarelos OU vermelhos na fonte: EXCLUIDO e contabilizado
      (nunca zero);
    - sub-amostra do lado com menos de 4 jogos usa a amostra geral;
    - ultimos N jogos (10/20/ate 50) limitam a amostra pela data;
    - linhas canonicas LIQUIDAVEIS (formato exato do settlement) e
      Over+Under complementares (100%);
    - probabilidade bate com Poisson direto sobre o lambda 65/35;
    - sem sustentacao (nem times, nem liga) => [] (lambda nunca
      inventado); apenas benchmark ou apenas times avaliam com risco;
    - confianca usa a amostra EFETIVA (jogos com dado de cartoes);
    - benchmark da liga: TODAS as finalizadas da temporada; partida sem
      cartoes completos excluida e contabilizada; sem dados => None;
    - risco de arbitro: declarado como inexistente na fonte, nunca
      inventado.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.cartoes import (
    UNIDADE_CARTOES,
    avaliar_cartoes_prejogo,
    baseline_cartoes,
    league_cards_average,
    league_cards_average_for_fixture,
)
from src.prejogo_opportunity import _confianca_prejogo
from src.settlement import _MARCA_CONVENCAO_CARTOES, _parse_linha


# ----------------------------------------------------------------------
# Builders
# ----------------------------------------------------------------------
def _game(yf, rf, ya, ra, casa=True, date="2026-08-20T16:00:00-03:00"):
    return SimpleNamespace(
        played_at_home=casa, yellow_for=yf, red_for=rf,
        yellow_against=ya, red_against=ra, date=date,
        goals_for=1, goals_against=1, corners_for=5, corners_against=5,
        corners_total=10,
    )


def _hist(n=10, yf_h=3, rf_h=0, ya_h=2, ra_h=1,
          yf_a=2, rf_a=1, ya_a=2, ra_a=0):
    games_h = [_game(yf_h, rf_h, ya_h, ra_h, casa=True) for _ in range(n)]
    games_a = [_game(yf_a, rf_a, ya_a, ra_a, casa=False) for _ in range(n)]
    return {"games_home": games_h, "games_away": games_a,
            "n_home": n, "n_away": n}


def _bench(media=5.0, validas=100):
    return {"partidas_validas": validas,
            "describe": {"media": media},
            "convencao": _MARCA_CONVENCAO_CARTOES}


# ----------------------------------------------------------------------
# Baseline: cruzamento casa/fora em pontos
# ----------------------------------------------------------------------
def test_baseline_cruzado_casa_fora_em_pontos():
    # mandante: recebe 3+2*0=3 pontos, adversarios dele 2+2*1=4
    # visitante: recebe 2+2*1=4, adversarios dele 2+2*0=2
    base = baseline_cartoes(_hist())
    assert base is not None
    # lado mandante = (3 + 2)/2 = 2.5; visitante = (4 + 4)/2 = 4.0
    assert base["lado_mandante"] == pytest.approx(2.5)
    assert base["lado_visitante"] == pytest.approx(4.0)
    assert base["total"] == pytest.approx(6.5)
    assert base["n_usados_mandante"] == 10
    assert base["n_usados_visitante"] == 10
    assert _MARCA_CONVENCAO_CARTOES in base["detalhe"]


def test_jogo_sem_dado_e_excluido_nunca_zero():
    hist = _hist(n=4)
    # um jogo do mandante SEM amarelos na fonte; outro SEM vermelhos
    hist["games_home"][0].yellow_for = None
    hist["games_home"][1].red_against = None
    base = baseline_cartoes(hist)
    assert base is not None
    assert base["n_usados_mandante"] == 2   # 4 - 2 excluidos
    assert base["n_sem_dado_mandante"] == 2
    # media usa SOMENTE os 2 jogos validos (3+2*0 e 2+2*1... os dois
    # restantes tem os valores padrao do builder)
    assert base["lado_mandante"] == pytest.approx(2.5)
    assert base["total"] == pytest.approx(6.5)


def test_lado_com_menos_de_4_jogos_usa_amostra_geral():
    # mandante so tem 2 jogos NO MANDO com dado: usa os 10 validos
    hist = _hist(n=10)
    hist["games_home"] = [
        _game(3, 0, 2, 1, casa=True), _game(3, 0, 2, 1, casa=True)
    ] + [_game(6, 0, 2, 0, casa=False) for _ in range(8)]
    base = baseline_cartoes(hist)
    assert base is not None
    assert base["n_usados_mandante"] == 10
    # cruzamento: pro geral do mandante = (2*3 + 8*6)/10 = 5.4; contra do
    # visitante = 2 (padrao do builder) => lado mandante = (5.4 + 2)/2
    assert base["lado_mandante"] == pytest.approx(3.7)


def test_sem_medias_de_cartoes_nao_tem_baseline():
    vazios = {"games_home": [], "games_away": [], "n_home": 0, "n_away": 0}
    assert baseline_cartoes(vazios) is None
    # historico existe mas NENHUM jogo tem dado de cartoes
    hist = _hist(n=3)
    for g in hist["games_home"] + hist["games_away"]:
        g.yellow_for = None
    assert baseline_cartoes(hist) is None


def test_last_n_limita_amostra_pelos_mais_recentes():
    # 20 jogos no mando com datas distintas: os 10 MAIS RECENTES (setembro)
    # tem 3 amarelos; os 10 mais antigos (agosto), 9
    games_h = [
        _game(9, 0, 2, 0, casa=True,
              date=f"2026-08-{i + 1:02d}T16:00:00-03:00")
        for i in range(10)
    ] + [
        _game(3, 0, 2, 0, casa=True,
              date=f"2026-09-{i + 1:02d}T16:00:00-03:00")
        for i in range(10)
    ]
    hist = {"games_home": games_h, "games_away": _hist()["games_away"],
            "n_home": 20, "n_away": 10}

    base_todos = baseline_cartoes(hist)                 # n_usados = 20
    base_10 = baseline_cartoes(hist, last_n=10)         # n_usados = 10
    assert base_todos["n_usados_mandante"] == 20
    assert base_10["n_usados_mandante"] == 10
    # amostra truncada usa SO os 10 jogos de setembro (3 amarelos):
    # lado mandante = (3 + 2)/2 = 2.5; na amostra cheia o cruzamento
    # com os jogos de 9 amarelos sobe para (6 + 2)/2 = 4.0
    assert base_10["lado_mandante"] == pytest.approx(2.5)
    assert base_todos["lado_mandante"] == pytest.approx(4.0)
    assert base_10["total"] == pytest.approx(
        base_10["lado_visitante"] + 2.5
    )
    # teto de 50: um pedido acima do teto e travado em 50
    assert baseline_cartoes(hist, last_n=999)["n_usados_mandante"] == 20


# ----------------------------------------------------------------------
# Avaliacao pre-jogo: linhas liquidadaveis + Poisson
# ----------------------------------------------------------------------
def test_linhas_canonicas_liquidadaveis_e_complementares():
    aves = avaliar_cartoes_prejogo(_hist(), _bench())
    assert aves, "com sustentacao completa a familia deve ser avaliada"

    por_chave = {}
    for av in aves:
        assert av.mercado == "cartoes"
        # formato EXATO que o settlement aprovado sabe liquidar
        assert _parse_linha(av.linha) is not None, av.linha
        assert UNIDADE_CARTOES in av.linha
        assert _MARCA_CONVENCAO_CARTOES in av.linha
        chave = av.linha.split(" ")[1]  # valor da linha
        por_chave.setdefault(chave, {})[av.linha.split(" ")[0]] = av.prob
    for chave, direcoes in por_chave.items():
        if "Over" in direcoes and "Under" in direcoes:
            assert direcoes["Over"] + direcoes["Under"] == pytest.approx(
                1.0, abs=1e-4
            )


def test_probabilidade_bate_com_poisson_direto_65_35():
    from src.live_opportunity import poisson_ge, poisson_le

    hist = _hist()
    base = baseline_cartoes(hist)
    bench = _bench(media=4.0)
    lam = 0.65 * base["total"] + 0.35 * 4.0
    aves = avaliar_cartoes_prejogo(hist, bench)
    over = next(a for a in aves if a.linha.startswith("Over 5.5 "))
    under = next(a for a in aves if a.linha.startswith("Under 5.5 "))
    assert over.prob == pytest.approx(poisson_ge(6, lam), abs=1e-4)
    assert under.prob == pytest.approx(poisson_le(5, lam), abs=1e-4)
    assert over.sustentacao["lambda_por90"] == pytest.approx(
        round(lam, 2), abs=1e-9
    )


def test_sem_sustentacao_nao_avalia_nada():
    vazios = {"games_home": [], "games_away": [], "n_home": 0, "n_away": 0}
    assert avaliar_cartoes_prejogo(vazios, None) == []
    # historico SEM dado de cartoes na fonte E sem benchmark => []
    # (lambda nunca e inventado)
    hist = _hist(n=3)
    for g in hist["games_home"] + hist["games_away"]:
        g.yellow_for = None
    assert avaliar_cartoes_prejogo(hist, None) == []


def test_apenas_benchmark_ou_apenas_times_avaliam_com_risco():
    # apenas historico dos times: lambda = baseline, risco declarado
    so_times = avaliar_cartoes_prejogo(_hist(), None)
    assert so_times
    assert all(
        any("sem benchmark de cartoes da liga" in r for r in a.riscos)
        for a in so_times
    )
    base = baseline_cartoes(_hist())
    assert so_times[0].sustentacao["lambda_por90"] == round(
        base["total"], 2
    )

    # apenas benchmark da liga: lambda = media da liga, risco declarado
    sem_times = {"games_home": [], "games_away": [], "n_home": 0, "n_away": 0}
    so_bench = avaliar_cartoes_prejogo(sem_times, _bench(media=4.6))
    assert so_bench
    assert all(
        any("sem medias de cartoes dos times" in r for r in a.riscos)
        for a in so_bench
    )
    assert so_bench[0].sustentacao["lambda_por90"] == 4.6


def test_confianca_usa_amostra_efetiva_com_dado():
    hist = _hist(n=10)
    # 3 jogos do mandante sem dado de cartoes: amostra efetiva 7
    for g in hist["games_home"][:3]:
        g.yellow_for = None
    aves = avaliar_cartoes_prejogo(hist, _bench(validas=120), h2h_n=0)
    esperada, comps = _confianca_prejogo(7, 120, 0)
    assert aves[0].confianca == esperada
    assert aves[0].conf_componentes == comps
    assert aves[0].sustentacao["amostra_valida_mandante"] == 7
    assert aves[0].sustentacao["jogos_sem_dado_cartoes"] == 3
    assert any("3 jogo(s) do historico sem dado" in r for r in aves[0].riscos)


def test_risco_de_arbitro_declarado_nunca_inventado():
    aves = avaliar_cartoes_prejogo(_hist(), _bench())
    assert any("arbitro" in r for r in aves[0].riscos)
    # nenhuma sustentacao menciona estatistica de arbitro como dado
    for av in aves:
        assert "arbitro" not in " ".join(
            k.lower() for k in av.sustentacao
        )


# ----------------------------------------------------------------------
# Benchmark real: media de cartoes da competicao INTEIRA
# ----------------------------------------------------------------------
class _ClientLiga:
    """Cliente fake: /fixtures (lista da temporada) + /fixtures/statistics
    por partida (amarelos/vermelhos finais)."""

    def __init__(self, fixtures_raw, stats_por_fixture):
        self._fixtures = fixtures_raw
        self._stats = stats_por_fixture

    def get(self, endpoint, params=None):
        params = params or {}
        if endpoint == "/fixtures":
            return self._fixtures
        if endpoint == "/fixtures/statistics":
            return self._stats.get(params.get("fixture"), [])
        raise AssertionError(f"endpoint inesperado: {endpoint}")


def _fx_raw(fid, status="FT", gh=1, ga=0):
    return {
        "fixture": {"id": fid, "date": "2026-08-20T16:00:00-03:00",
                     "status": {"short": status, "elapsed": 90}},
        "league": {"id": 71, "name": "Serie A", "season": 2026},
        "teams": {"home": {"id": 1, "name": "A"},
                  "away": {"id": 2, "name": "B"}},
        "goals": {"home": gh, "away": ga},
    }


def _stats_raw(yc_h, yc_a, rc_h=0, rc_a=0, sem_vermelho=False):
    def bloco(tid, yc, rc):
        stats = [{"type": "Corner Kicks", "value": "6"}]
        stats.append({"type": "Yellow Cards", "value": str(yc)})
        if not sem_vermelho:
            stats.append({"type": "Red Cards", "value": str(rc)})
        return {"team": {"id": tid}, "statistics": stats}

    return [bloco(1, yc_h, rc_h), bloco(2, yc_a, rc_a)]


def test_benchmark_liga_integridade_exclusoes_e_media():
    fixtures = [
        _fx_raw(101),                                # valida: 2+2 Y, 1+0 R
        _fx_raw(102),                                # valida: 1+1 Y, 0+0 R
        _fx_raw(103),                                # SEM vermelhos: excluida
        _fx_raw(104, status="NS"),                  # nao encerrada: fora
    ]
    stats = {
        101: _stats_raw(2, 2, 1, 0),                 # 4 + 2 = 6 pontos
        102: _stats_raw(1, 1, 0, 0),                 # 2 + 0 = 2 pontos
        103: _stats_raw(3, 1, 0, 0, sem_vermelho=True),
        104: _stats_raw(2, 2, 0, 0),
    }
    bench = league_cards_average(_ClientLiga(fixtures, stats),
                                 league_id=71, season=2026,
                                 league_name="Serie A")
    assert bench is not None
    assert bench["partidas_encerradas"] == 3      # NS fora
    assert bench["partidas_validas"] == 2         # 103 excluida
    assert bench["partidas_sem_cartoes"] == 1
    assert bench["describe"]["media"] == pytest.approx(4.0)  # (6+2)/2
    assert bench["convencao"] == _MARCA_CONVENCAO_CARTOES
    # frequencia over nas linhas do equilibrio (media 4.0 => linhas
    # 3.5/4.5/5.5): 6 pontos bate as tres; 2 pontos nao bate nenhuma
    assert bench["over"][3.5]["hits"] == 1
    assert bench["over"][5.5]["hits"] == 1
    assert bench["over"][5.5]["total"] == 2


def test_benchmark_sem_dados_retorna_none():
    fixtures = [_fx_raw(101), _fx_raw(102)]
    stats = {101: [], 102: _stats_raw(2, 2, 0, 0, sem_vermelho=True)}
    bench = league_cards_average(_ClientLiga(fixtures, stats),
                                 league_id=71, season=2026,
                                 league_name="Serie A")
    assert bench is None  # nenhuma partida valida: nunca inventa media


def test_benchmark_do_fixture_sem_identificacao_e_none():
    client = _ClientLiga([], {})
    fx_sem = SimpleNamespace(league_id=None, season=2026,
                             league_name="Serie A")
    assert league_cards_average_for_fixture(client, fx_sem) is None
    fx_ok = SimpleNamespace(league_id=71, season=2026,
                            league_name="Serie A")
    assert league_cards_average_for_fixture(client, fx_ok) is None  # vazia