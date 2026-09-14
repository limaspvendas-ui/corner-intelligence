"""Testes dos calculos estatisticos (sem rede - dados sinteticos).

Os dados aqui sao FICTICIOS e servem apenas para validar a matematica;
nunca sao apresentados como estatisticas reais.
"""

import pytest

from src.match_stats import TeamGameStats
from src.stats import (
    METRIC_FIELDS,
    compute_team_stats,
    describe,
    linear_trend,
    metric_report,
    metrics_panel,
    over_frequency,
    xcorners_heuristic,
)


def _game(total_for: int, total_against: int, home: bool, idx: int) -> TeamGameStats:
    return TeamGameStats(
        fixture_id=idx,
        date=f"2026-09-{idx + 1:02d}T15:00:00+00:00",
        league="Test League",
        round="Regular Season",
        status="FT",
        opponent="Opponent FC",
        played_at_home=home,
        corners_for=total_for,
        corners_against=total_against,
        corners_total=total_for + total_against,
        goals_for=None,
        goals_against=None,
        shots_for=None,
        shots_against=None,
        shots_on_goal_for=None,
        shots_on_goal_against=None,
        possession_for=None,
        yellow_for=None,
        red_for=None,
    )


class TestDescribe:
    def test_basico(self):
        d = describe([4, 6, 8, 10, 10])
        assert d["n"] == 5
        assert d["media"] == 7.6
        assert d["mediana"] == 8.0
        assert d["minimo"] == 4
        assert d["maximo"] == 10

    def test_vazio(self):
        assert describe([]) == {"n": 0}


class TestOver:
    def test_linha_9_5(self):
        vals = [10, 9, 8, 11, 7]
        over = over_frequency(vals, [9.5])
        info = over[9.5]
        assert info["hits"] == 2  # 10 e 11
        assert info["total"] == 5
        assert info["pct"] == 40.0

    def test_nao_bate_igual(self):
        # total 9 NAO bate Over 9.5 (precisa ser > 9.5)
        over = over_frequency([9], [9.5])
        assert over[9.5]["hits"] == 0


class TestTrend:
    def test_crescente(self):
        t = linear_trend([4, 6, 8, 10, 12])
        assert t["classificacao"] == "crescente"
        assert t["slope"] > 0

    def test_decrescente(self):
        t = linear_trend([12, 10, 8, 6, 4])
        assert t["classificacao"] == "decrescente"

    def test_estavel(self):
        t = linear_trend([8, 8, 8, 8, 8])
        assert t["classificacao"] == "estavel"

    def test_amostra_insuficiente(self):
        assert linear_trend([5, 6])["slope"] is None


class TestComputeTeamStats:
    def test_painel_completo(self):
        games = [
            _game(5, 4, True, 0),
            _game(3, 6, False, 1),
            _game(6, 2, True, 2),
            _game(2, 5, False, 3),
            _game(4, 4, True, 4),
            _game(1, 7, False, 5),
        ]
        stats = compute_team_stats(games)
        assert stats["n_jogos"] == 6
        # total por jogo: 9, 9, 8, 7, 8, 8
        assert stats["total"]["media"] == 8.17
        assert stats["casa"]["n_jogos"] == 3
        assert stats["fora"]["n_jogos"] == 3
        # janela 5: ultimos 5 jogos (indices 1..5): 9,8,7,8,8
        assert stats["janelas"][5]["describe"]["n"] == 5
        assert stats["janelas"][5]["describe"]["media"] == 8.0
        # over 7.5 nos 6 jogos: 9,9,8,7,8,8 -> 5 batem (>7.5)
        assert stats["over_geral"][7.5]["hits"] == 5
        # tempos: False porque nenhum jogo sintetico traz escanteios de 1oT
        assert stats["tempos_disponiveis"] is False


def _metric_game(home: bool, idx: int, **kwargs) -> TeamGameStats:
    """Jogo sintetico para o motor generico: so os campos passados em
    kwargs sao preenchidos; o resto fica None (dado ausente)."""
    base = dict(
        fixture_id=idx,
        date=f"2026-09-{idx + 1:02d}T15:00:00+00:00",
        league="Test League",
        round="Regular Season",
        status="FT",
        opponent="Opponent FC",
        played_at_home=home,
        corners_for=None,
        corners_against=None,
        corners_total=0,
        goals_for=None,
        goals_against=None,
        shots_for=None,
        shots_against=None,
        shots_on_goal_for=None,
        shots_on_goal_against=None,
        possession_for=None,
        yellow_for=None,
        red_for=None,
    )
    base.update(kwargs)
    return TeamGameStats(**base)


class TestMetricReport:
    def test_somente_jogos_com_valor_real(self):
        """None e ignorado e contabilizado - nunca tratado como zero."""
        games = [
            _metric_game(True, 0, fouls_for=10),
            _metric_game(False, 1, fouls_for=None),   # sem dado
            _metric_game(True, 2, fouls_for=14),
            _metric_game(False, 3, fouls_for=4),
        ]
        r = metric_report(games, "fouls_for")
        assert r["jogos_com_dado"] == 3
        assert r["jogos_sem_dado"] == 1
        # media de 10, 14, 4 = 9.33 (o jogo sem dado NAO entra como 0)
        assert r["geral"]["media"] == 9.33
        assert r["geral"]["mediana"] == 10
        assert r["geral"]["minimo"] == 4
        assert r["geral"]["maximo"] == 14
        # casa: 10, 14 / fora: 4
        assert r["casa"]["media"] == 12
        assert r["fora"]["media"] == 4

    def test_sem_dado_nenhum(self):
        games = [_metric_game(True, 0), _metric_game(False, 1)]
        r = metric_report(games, "offsides_for")
        assert r["jogos_com_dado"] == 0
        assert r["jogos_sem_dado"] == 2
        assert r["geral"] == {"n": 0}
        assert r["casa"] == {"n": 0}
        assert r["fora"] == {"n": 0}


class TestMetricsPanel:
    def test_cobertura_das_metricas(self):
        assert set(METRIC_FIELDS) == {
            "gols", "escanteios", "finalizacoes", "finalizacoes_no_alvo",
            "cartoes_amarelos", "cartoes_vermelhos", "posse",
            "faltas", "impedimentos",
        }

    def test_painel_do_time_adversario_e_total(self):
        games = [
            _metric_game(True, 0, goals_for=2, goals_against=1,
                          yellow_for=3, yellow_against=1),
            _metric_game(False, 1, goals_for=1, goals_against=1,
                         yellow_for=None, yellow_against=None),
            _metric_game(True, 2, goals_for=3, goals_against=0,
                          yellow_for=2, yellow_against=2),
        ]
        panel = metrics_panel(games)

        gols = panel["gols"]
        assert gols["do_time"]["geral"]["media"] == 2.0          # 2,1,3
        assert gols["do_adversario"]["geral"]["media"] == pytest.approx(0.67)
        # total por jogo: 3, 2, 3 -> media 2.67
        assert gols["total"]["geral"]["media"] == 2.67
        assert gols["total"]["jogos_com_dado"] == 3

        amarelos = panel["cartoes_amarelos"]
        # jogo 1 sem cartoes na fonte: total so nos jogos 0 e 2 (4 e 4)
        assert amarelos["do_time"]["jogos_com_dado"] == 2
        assert amarelos["do_time"]["geral"]["media"] == 2.5
        assert amarelos["total"]["geral"]["media"] == pytest.approx(4.0)
        assert amarelos["total"]["jogos_com_dado"] == 2

    def test_posse_nao_e_somavel(self):
        """Total da partida nao se aplica a posse (sempre soma 100%)."""
        games = [
            _metric_game(True, 0, possession_for=55, possession_against=45),
            _metric_game(False, 1, possession_for=60, possession_against=40),
        ]
        panel = metrics_panel(games)
        assert panel["posse"]["do_time"]["geral"]["media"] == 57.5
        assert panel["posse"]["total"] is None

    def test_total_exige_ambos_os_valores(self):
        """Jogo com so um lado preenchido nao entra no total (nao vira zero)."""
        games = [
            _metric_game(True, 0, fouls_for=8, fouls_against=None),
            _metric_game(False, 1, fouls_for=6, fouls_against=9),
        ]
        panel = metrics_panel(games)
        assert panel["faltas"]["do_time"]["jogos_com_dado"] == 2
        assert panel["faltas"]["do_adversario"]["jogos_com_dado"] == 1
        assert panel["faltas"]["total"]["jogos_com_dado"] == 1
        assert panel["faltas"]["total"]["geral"]["media"] == 15


class TestXCorners:
    def test_heuristica(self):
        a = {"favor": {"media": 6.0}, "contra": {"media": 4.0}}
        b = {"favor": {"media": 5.0}, "contra": {"media": 3.0}}
        xc = xcorners_heuristic(a, b)
        assert xc["disponivel"] is True
        # x_for_A = (6.0 + 3.0)/2 = 4.5 ; x_for_B = (5.0 + 4.0)/2 = 4.5
        assert xc["x_escanteios_A"] == 4.5
        assert xc["x_escanteios_B"] == 4.5
        assert xc["x_total_estimado"] == 9.0
        assert "heuristica" in xc["metodo"]

    def test_sem_dados(self):
        a = {"favor": {"media": None}, "contra": {"media": 4.0}}
        b = {"favor": {"media": 5.0}, "contra": {"media": 3.0}}
        assert xcorners_heuristic(a, b)["disponivel"] is False