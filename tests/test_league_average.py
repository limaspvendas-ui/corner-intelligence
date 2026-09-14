"""Testes da media de escanteios da LIGA (competicao inteira).

Regra de integridade: media da liga usa TODAS as partidas finalizadas da
competicao na temporada; partida sem escanteios na API e excluida (nunca
zerada); a resposta informa a quantidade de partidas validas.

Usa respostas falsas no formato real da API - nao gasta requisicoes.
"""

import pytest

from src.analysis import league_corner_average


def _fixture_raw(fid, status, home_id, away_id, date):
    return {
        "fixture": {
            "id": fid,
            "date": date,
            "status": {"short": status, "elapsed": 90},
            "venue": {"city": "Cidade"},
        },
        "league": {"id": 999, "name": "Liga Teste", "round": "Regular Season - 1", "season": 2026},
        "teams": {
            "home": {"id": home_id, "name": f"Time {home_id}"},
            "away": {"id": away_id, "name": f"Time {away_id}"},
        },
        "goals": {"home": 1, "away": 1},
    }


def _stats(team_id, corners):
    block = {"team": {"id": team_id, "name": f"Time {team_id}"}, "statistics": []}
    if corners is not None:
        block["statistics"].append({"type": "Corner Kicks", "value": corners})
    return block


class FakeClient:
    """Client falso: /fixtures devolve a temporada; /fixtures/statistics
    devolve a resposta por fixture id."""

    def __init__(self, fixtures, stats_by_fixture):
        self._fixtures = fixtures
        self._stats = stats_by_fixture
        self.calls = []

    def get(self, endpoint, params=None, use_cache=True):
        self.calls.append((endpoint, params))
        if endpoint == "/fixtures":
            return self._fixtures
        fid = (params or {}).get("fixture")
        return self._stats.get(fid, [])


def test_liga_inteira_exclui_sem_escanteios_e_conta_validas():
    # 4 partidas: 3 encerradas + 1 agendada (fora do calculo)
    fixtures = [
        _fixture_raw(1, "FT", 10, 20, "2026-05-01T16:00:00-03:00"),
        _fixture_raw(2, "FT", 30, 40, "2026-05-02T16:00:00-03:00"),
        _fixture_raw(3, "FT", 50, 60, "2026-05-03T16:00:00-03:00"),
        _fixture_raw(4, "NS", 70, 80, "2026-12-01T16:00:00-03:00"),
    ]
    stats = {
        # valida: 6 + 4 = 10
        1: [_stats(10, 6), _stats(20, 4)],
        # SEM escanteios na API: excluida, NUNCA zerada
        2: [],
        # escanteio so de um lado: invalida, excluida (nao vira 6+0)
        3: [_stats(50, 6)],
    }
    client = FakeClient(fixtures, stats)

    result = league_corner_average(client, league_id=999, season=2026, league_name="Liga Teste")

    # partida agendada (NS) nem entrou nas encerradas
    assert result["partidas_encerradas"] == 3
    assert result["partidas_validas"] == 1
    assert result["partidas_sem_escanteios"] == 2
    # media = 10.0 (so a partida valida; as outras nao entram como zero)
    assert result["describe"]["media"] == 10.0
    assert result["describe"]["minimo"] == 10
    assert result["describe"]["maximo"] == 10
    # identificacao obrigatoria
    assert result["liga_id"] == 999
    assert result["temporada"] == 2026
    # busca a temporada INTEIRA (sem "last")
    fixtures_call = client.calls[0]
    assert fixtures_call[0] == "/fixtures"
    assert "last" not in (fixtures_call[1] or {})


def test_liga_sem_partidas_validas_retorna_none():
    fixtures = [_fixture_raw(1, "FT", 10, 20, "2026-05-01T16:00:00-03:00")]
    client = FakeClient(fixtures, {1: []})
    assert league_corner_average(client, 999, 2026, "Liga Teste") is None