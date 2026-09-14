"""Testes da ETAPA 2.2 - OPORTUNIDADES DE APOSTA AO VIVO (sem rede).

Valida:
  1. calculos puros: Poisson sobre o TEMPO RESTANTE, mistura
     historico/observado (peso crescente, piso 20% / teto 50%), linhas X.5;
  2. TRIAGEM GLOBAL (correcao validada): TODOS os elegiveis passam pela
     triagem barata (um snapshot por jogo); score com janela de valor
     30-75' nunca e dominado pelo minuto mais avancado; o limite de
     deep dive so e aplicado DEPOIS de todos avaliados;
  3. varredura completa com cliente falso: triagem barata de todos os
     elegiveis, elegibilidade por mercado (MATRIZ DE COBERTURA: jogo sem
     Corner Kicks segue para analise de gols), deep dive apenas dos
     melhores colocados na triagem;
  4. auditoria obrigatoria antes de aprovar (amostra historica minima,
     identidade, frescor, None nunca vira zero);
  5. aprovacao: maximo 2, TOP 2 somente se forte e de jogo diferente,
     mensagem exata NENHUMA quando nada atinge o padrao;
  6. odd live real: anexada somente com timestamp fresco; sem odd =>
     OPORTUNIDADE ESTATISTICA + "ODD AO VIVO NAO DISPONIVEL NA FONTE.";
  7. expulsao altera a analise (confianca penalizada);
  8. identidade divergente INTERROMPE a varredura (nunca outro clube).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.exceptions import IdentityDivergenceError
from src.live import LiveSnapshot, now_brt
from src.live_opportunity import (
    CONF_MIN_TOP1,
    CONF_MIN_TOP2,
    NENHUMA_MSG,
    PROB_MIN_APROVAR,
    Avaliacao,
    Candidato,
    OddUsada,
    RateBlend,
    _anexar_odd_real,
    _aprovar,
    _confianca,
    _linhas_estatisticas,
    _motivo_estagio1,
    _timestamp_recente,
    _triagem_global_score,
    analyze_live_game_opportunities,
    blend_rate,
    poisson_ge,
    poisson_le,
    poisson_pmf,
    scan_live_opportunities,
    tempo_restante,
)
from src.live_opportunity import Varredura
from src.match_stats import TeamGameStats
from src.live_opportunity_report import (
    format_live_game_opportunities,
    format_live_opportunities,
    format_oportunidade,
)
from src.odds import SEM_ODD_LIVE, _parse_response
from src.settlement import _e_linha_resultado

FID = 200
HOME_ID, HOME = 131, "Corinthians"
AWAY_ID, AWAY = 132, "Chapecoense-sc"
LEAGUE_ID = 71


# ----------------------------------------------------------------------
# Infra: cliente falso + builders no formato real da API
# ----------------------------------------------------------------------
class FakeLiveClient:
    """Client falso: devolve a resposta montada e registra as chamadas."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, endpoint, params=None, use_cache=True, ttl=None):
        self.calls.append(
            {"endpoint": endpoint, "params": dict(params or {}),
             "use_cache": use_cache, "ttl": ttl}
        )
        route = self.routes.get(endpoint)
        if route is None:
            return []
        if callable(route):
            return route(dict(params or {}))
        return route


def _game(
    fid=FID, status="2H", elapsed=47, gh=1, ga=1,
    date="2026-09-06T19:30:00-03:00",
    home_id=HOME_ID, home_name=HOME, away_id=AWAY_ID, away_name=AWAY,
):
    return {
        "fixture": {
            "id": fid,
            "date": date,
            "status": {"short": status, "elapsed": elapsed},
            "venue": {"name": "Arena", "city": "Sao Paulo"},
        },
        "league": {
            "id": LEAGUE_ID, "name": "Serie A", "country": "Brazil",
            "round": "Regular Season - 26", "season": 2026,
        },
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "goals": {"home": gh, "away": ga},
        "score": {"halftime": {"home": 0, "away": 1},
                  "fulltime": {"home": None, "away": None}},
    }


def _stat(stat_type, value):
    return {"type": stat_type, "value": value}


def _block(team_id, name, stats, stats_1h=None, stats_2h=None):
    block = {"team": {"id": team_id, "name": name}, "statistics": stats}
    if stats_1h is not None:
        block["statistics_1h"] = stats_1h
    if stats_2h is not None:
        block["statistics_2h"] = stats_2h
    return block


def _teams_route(names):
    def route(params):
        tid = params.get("id")
        if tid in names:
            return [{"team": {"id": tid, "name": names[tid], "country": "Brazil"}}]
        return []

    return route


VALID_TEAMS = {HOME_ID: HOME, AWAY_ID: AWAY}

# fixture encerrado generico (historico dos times / benchmark da liga)
FIM = {"fixture": {"id": 0, "date": "2026-08-20T16:00:00-03:00",
                   "status": {"short": "FT", "elapsed": 90},
                   "venue": {"name": "Arena", "city": "Cidade"}},
       "league": {"id": LEAGUE_ID, "name": "Serie A", "country": "Brazil",
                  "round": "Regular Season - 10", "season": 2026},
       "teams": {"home": {"id": 0, "name": "A"}, "away": {"id": 0, "name": "B"}},
       "goals": {"home": 1, "away": 1}}


def _ft_game(fid, home_id, home_name, away_id, away_name, date=None):
    raw = {
        "fixture": dict(FIM["fixture"]),
        "league": dict(FIM["league"]),
        "teams": {"home": {"id": home_id, "name": home_name},
                  "away": {"id": away_id, "name": away_name}},
        "goals": dict(FIM["goals"]),
    }
    raw["fixture"]["id"] = fid
    raw["fixture"]["date"] = date or "2026-08-20T16:00:00-03:00"
    return raw


def _generic_stats(game, corners=5, corners_opp=5, yellow=2):
    hid = game["teams"]["home"]["id"]
    aid = game["teams"]["away"]["id"]
    return [
        _block(hid, game["teams"]["home"]["name"],
               [_stat("Corner Kicks", corners), _stat("Total Shots", 10),
                _stat("Shots on Goal", 4), _stat("Ball Possession", "50%"),
                _stat("Yellow Cards", yellow), _stat("Fouls", 12)]),
        _block(aid, game["teams"]["away"]["name"],
               [_stat("Corner Kicks", corners_opp), _stat("Total Shots", 9),
                _stat("Shots on Goal", 3), _stat("Ball Possession", "50%"),
                _stat("Yellow Cards", yellow), _stat("Fouls", 11)]),
    ]


# mundo falso completo para a varredura de um jogo maduro (FID 200)
HIST_FIDS = {
    HOME_ID: [911, 912],
    AWAY_ID: [921, 922],
}
WORLD_FIXTURES = {
    911: _ft_game(911, HOME_ID, HOME, 501, "Bahia"),
    912: _ft_game(912, 601, "Santos", HOME_ID, HOME,
                 date="2026-08-13T16:00:00-03:00"),
    921: _ft_game(921, AWAY_ID, AWAY, 501, "Bahia"),
    922: _ft_game(922, 601, "Santos", AWAY_ID, AWAY,
                 date="2026-08-13T16:00:00-03:00"),
    301: _ft_game(301, 701, "Cruzeiro", 702, "Goias"),
    302: _ft_game(302, 701, "Cruzeiro", 703, "Fortaleza",
                 date="2026-08-27T16:00:00-03:00"),
}
LEAGUE_FIDS = [301, 302]


def _live_stats(fid=FID):
    """Blocos live do jogo FID 200: escanteios 6-4, posse, cartoes e blocos
    reais de 2o tempo (sem blocos 1o tempo)."""
    return [
        _block(
            HOME_ID, HOME,
            [_stat("Corner Kicks", 6), _stat("Total Shots", 12),
             _stat("Shots on Goal", 5), _stat("Ball Possession", "58%"),
             _stat("Yellow Cards", 1), _stat("Red Cards", 0),
             _stat("Fouls", 9)],
            stats_2h=[_stat("Corner Kicks", 3)],
        ),
        _block(
            AWAY_ID, AWAY,
            [_stat("Corner Kicks", 4), _stat("Total Shots", 8),
             _stat("Shots on Goal", 2), _stat("Ball Possession", "42%"),
             _stat("Yellow Cards", 2), _stat("Red Cards", 0),
             _stat("Fouls", 10)],
            stats_2h=[_stat("Corner Kicks", 2)],
        ),
    ]


def _routes_mundo(live_list=None, stats_202_sem_escanteios=True):
    """Rotas completas para a varredura: listagem live + fixture por id +
    historico por time + fixtures da liga + estatisticas por partida."""
    live_list = live_list if live_list is not None else [
        _game(FID, "2H", 47, 1, 1),
        _game(201, "1H", 5, 0, 0),
        _game(202, "2H", 30, 0, 0),
    ]

    def fixtures_route(params):
        if "live" in params:
            return live_list
        if "id" in params:
            fid = params["id"]
            for g in live_list:
                if g["fixture"]["id"] == fid:
                    return [g]
            return []
        if "team" in params:
            return [WORLD_FIXTURES[f] for f in HIST_FIDS.get(params["team"], [])]
        if "league" in params:
            return [WORLD_FIXTURES[f] for f in LEAGUE_FIDS]
        return []

    def stats_route(params):
        fid = params.get("fixture")
        live_ids = {g["fixture"]["id"] for g in live_list}
        if fid in live_ids:
            if fid == 202 and stats_202_sem_escanteios:
                # jogo ao vivo SEM escanteios na fonte nesta leitura
                return [
                    _block(HOME_ID, HOME, [_stat("Total Shots", 3),
                                           _stat("Ball Possession", "60%")]),
                    _block(AWAY_ID, AWAY, [_stat("Total Shots", 1),
                                           _stat("Ball Possession", "40%")]),
                ]
            # qualquer outro jogo ao vivo recebe os blocos live completos
            # (escanteios 6-4, blocos reais de 2o tempo)
            return _live_stats()
        game = WORLD_FIXTURES.get(fid)
        if game is None:
            return []
        return _generic_stats(game)

    return {
        "/fixtures": fixtures_route,
        "/teams": _teams_route(VALID_TEAMS),
        "/fixtures/statistics": stats_route,
        "/fixtures/events": [],
        "/fixtures/headtohead": [],
        "/odds/live": [],
    }


# ----------------------------------------------------------------------
# 1. Calculos puros
# ----------------------------------------------------------------------
def test_poisson_caudas():
    assert poisson_ge(0, 2.5) == 1.0          # nada mais a fazer: garantido
    assert poisson_ge(3, 0.0) == 0.0         # sem tempo esperado
    assert poisson_ge(4, 60.0) < 1e-15        # cauda remota, numerica e estavel
    assert poisson_ge(61, 60.0) == 0.0       # necessidade impossivel: cortada
    # P(X >= 3 | lambda=3) = 1 - P(X<=2) = 1 - 0.4232 = 0.5768
    assert poisson_ge(3, 3.0) == pytest.approx(0.5768, abs=1e-4)
    assert poisson_le(-1, 2.0) == 0.0        # impossivel pelo estado atual
    assert poisson_le(2, 3.0) == pytest.approx(0.4232, abs=1e-4)
    assert poisson_pmf(0, 1.5) == pytest.approx(0.2231, abs=1e-4)
    assert poisson_pmf(2, 0.0) == 0.0


def test_blend_rate_peso_do_observado():
    """Peso do observado: piso 20%, teto 50%; sem minuto => so historico."""
    # minuto 40: w = 40/90 (exibido arredondado); observado 4 -> 9/90
    rb = blend_rate(10.0, 4, 40)
    w = 40 / 90
    assert rb.w_observado == round(w, 2)
    assert rb.observado_per90 == pytest.approx(9.0)
    assert rb.per90 == pytest.approx((1 - w) * 10 + w * 9, abs=1e-3)

    # minuto 10: peso NUNCA cai abaixo de 20%
    rb10 = blend_rate(10.0, 0, 10)
    assert rb10.w_observado == 0.20

    # minuto 89: peso NUNCA passa de 50% (historico nunca e ignorado)
    rb89 = blend_rate(10.0, 15, 89)
    assert rb89.w_observado == 0.50

    # sem minuto informado: apenas taxa historica, sem inventar observacao
    rb_none = blend_rate(10.0, None, None)
    assert rb_none.per90 == 10.0 and rb_none.w_observado == 0.0
    assert "historica" in rb_none.detalhe


def test_tempo_restante():
    assert tempo_restante("HT", 45) == 45
    assert tempo_restante("2H", 47) == 43
    assert tempo_restante("2H", 89) == 1
    assert tempo_restante("1H", 20) == 70
    assert tempo_restante("2H", None) is None


def test_linhas_estatisticas():
    """Linhas X.5 ao redor da projecao: equilibrio e vizinhas."""
    assert _linhas_estatisticas(9.2) == [8.5, 9.5, 10.5]
    assert _linhas_estatisticas(10.0) == [9.5, 10.5, 11.5]


# ----------------------------------------------------------------------
# 2. Triagem: cortes baratos antes de analise pesada
# ----------------------------------------------------------------------
def _live_game(fid, status, elapsed):
    return SimpleNamespace(fixture_id=fid, status=status, elapsed=elapsed,
                           home_team_name="A", away_team_name="B")


def test_motivo_estagio1():
    # prorrogacao/penaltis: mercados de 90' nao se aplicam
    for st in ("ET", "BT", "P"):
        assert "prorrogacao" in _motivo_estagio1(_live_game(1, st, 95))
    # suspenso, minuto ausente e jogo muito recente
    assert "suspenso" in _motivo_estagio1(_live_game(2, "SUSP", 40))
    assert "minuto nao informado" in _motivo_estagio1(_live_game(3, "2H", None))
    assert "muito recente" in _motivo_estagio1(_live_game(4, "1H", 5))
    # status fora da janela de jogo regulamentar
    assert "fora da janela" in _motivo_estagio1(_live_game(5, "LIVE", 40))
    # elegidos
    for st, el in (("1H", 25), ("HT", 45), ("2H", 80)):
        assert _motivo_estagio1(_live_game(6, st, el)) is None


# ----------------------------------------------------------------------
# 3. Varredura completa (cliente falso, formato real da API)
# ----------------------------------------------------------------------
def test_varredura_fluxo_e_descartes(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    client = FakeLiveClient(_routes_mundo())

    v = scan_live_opportunities(client)

    # estagio 1 (listagem, gratis): jogo de 5' descartado sem snapshot
    assert v.total_ao_vivo == 3
    assert v.total_elegiveis == 2          # 200 e 202
    assert v.total_triados == 2            # TODOS passaram pela triagem barata
    # MATRIZ DE COBERTURA: 202 (sem Corner Kicks) NAO e mais descartado
    # inteiro - segue para analise de GOLS; so sai o jogo sem NENHUM
    # mercado com dados suficientes
    assert [s["fixture_id"] for s in v.sondados] == [200, 202]
    motivos = {d.fixture_id: d.motivo for d in v.descartados}
    assert "muito recente" in motivos[201]
    assert 202 not in motivos
    veredictos_202 = {
        ver.mercado: ver for ver in v.cobertura[202]}
    assert veredictos_202["CORNERS"].bloqueado
    assert not veredictos_202["GOALS"].bloqueado

    # deep dive dos dois (limite 2): 200 com todas as familias, 202 so
    # com as familias cujo dado essencial existe (gols/resultado)
    assert len(v.candidatos) == 2
    cand = v.candidatos[0]
    assert cand.snapshot.fixture_id == FID
    assert cand.historico["n_home"] == 2 and cand.historico["n_away"] == 2
    assert cand.benchmark_escanteios["partidas_validas"] == 2
    assert cand.benchmark_gols["partidas_validas"] == 2
    assert cand.h2h_n == 0
    # sem odds live na fonte: SEM odd e classificacao estatistica
    assert cand.odds is None
    assert cand.odds_observacao == SEM_ODD_LIVE

    # linhas avaliadas: escanteios e gols (familias com dados) E a
    # familia RESULTADO, que desde a integracao (live-op-2.3) concorre
    # SEM odd como oportunidade estatistica; cartoes continuam exigindo
    # linha real ao vivo
    mercados = {av.mercado for av in cand.avaliacoes}
    assert "escanteios" in mercados and "gols" in mercados
    assert "resultado" in mercados
    assert "cartoes" not in mercados
    for av in cand.avaliacoes:
        assert av.odd is None
        assert av.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"
        assert "Poisson" in av.sustentacao["modelo"]
        if av.mercado == "resultado":
            # linha canonica liquidadavel + estado live na sustentacao
            assert _e_linha_resultado(av.linha)
            assert "minuto" in av.sustentacao["distribuicao"]
            continue
        assert av.sustentacao["tempo_restante_min"] == 43
        assert av.sustentacao["atual_no_jogo"] in (10, 2)  # esc/gols atuais

    # auditoria reprova TUDO por amostra historica insuficiente (n_min 2 < 5)
    assert v.aprovadas == []
    rejeicoes = [av.rejeicao for av in cand.avaliacoes if av.rejeicao]
    assert rejeicoes and all("sustentacao" in r for r in rejeicoes)

    # o segundo candidato (202, sem corners) so avaliou gols/resultado
    cand_202 = v.candidatos[1]
    assert cand_202.snapshot.fixture_id == 202
    assert {av.mercado for av in cand_202.avaliacoes} <= {"gols",
                                                          "resultado"}

    # relatorio: contagens obrigatorias da triagem global + secoes
    text = format_live_opportunities(v)
    assert "jogos ao vivo na fonte: 3" in text
    assert "passaram pela triagem barata: 2" in text
    assert "com cobertura em ao menos um mercado: 2" in text
    assert "enviados ao deep dive: 2" in text
    assert "JOGOS TRIADOS COM COBERTURA DE DADOS NA FONTE" in text
    assert "CANDIDATOS ENVIADOS AO DEEP DIVE" in text
    assert "CANDIDATOS DESCARTADOS" in text
    assert "triagem " in text.split("CANDIDATOS DESCARTADOS")[0]
    assert NENHUMA_MSG in text
    # sem aprovacao: [FATO] (varredura) + [INTERPRETACAO] (motivo); sem
    # [CALCULO] porque nenhuma linha sustentada chega ao relatorio
    assert "[FATO]" in text and "[INTERPRETACAO]" in text
    # mensagem exata exigida pela especificacao
    assert NENHUMA_MSG == "NENHUMA OPORTUNIDADE AO VIVO APROVADA AGORA."


def test_varredura_filtro_mercados_somente_escanteios(monkeypatch, tmp_path):
    """Filtro do operador (--mercado escanteios): a triagem global
    continua avaliando TODOS os jogos elegiveis; apenas as familias
    fora do filtro deixam de ser avaliadas no deep dive. Nenhum
    calculo muda - as linhas de escanteios sao as mesmas do modo
    'todos'."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    client = FakeLiveClient(_routes_mundo())

    v_todos = scan_live_opportunities(client)
    v_esc = scan_live_opportunities(client, mercados=("escanteios",))

    # triagem global IDENTICA (todos os jogos elegiveis triados)
    assert v_esc.total_triados == v_todos.total_triados
    assert [s["fixture_id"] for s in v_esc.sondados] == [
        s["fixture_id"] for s in v_todos.sondados
    ]
    # MATRIZ DE COBERTURA x filtro do operador: 202 (corners bloqueado
    # nesta leitura) nao e aprofundado no modo somente-escanteios; 200
    # (corners liberados) e aprofundado normalmente
    assert [c.snapshot.fixture_id for c in v_esc.candidatos] == [200]
    descarte_202 = next(
        d for d in v_esc.descartados if d.fixture_id == 202)
    assert "cobertura" in descarte_202.motivo

    # somente escanteios avaliados: nenhuma linha de outra familia
    mercados = {av.mercado for c in v_esc.candidatos
                for av in c.avaliacoes}
    assert mercados == {"escanteios"}
    # linhas de escanteios identicas as do modo 'todos' (logica intacta)
    linhas_todos = sorted(
        av.linha for c in v_todos.candidatos for av in c.avaliacoes
        if av.mercado == "escanteios"
    )
    linhas_esc = sorted(
        av.linha for c in v_esc.candidatos for av in c.avaliacoes
    )
    assert linhas_esc == linhas_todos
    # relatorio informa o filtro de forma transparente
    text = format_live_opportunities(v_esc)
    assert "filtro de mercados desta varredura" in text
    assert "escanteios" in text


def test_varredura_limite_deep_apos_triagem_global(monkeypatch, tmp_path):
    """CORRECAO DA TRIAGEM GLOBAL: TODOS os elegiveis sao avaliados
    superficialmente (5 snapshots); o limite de deep dive (2) so e
    aplicado DEPOIS, com o score da triagem no motivo - nunca
    'fora da janela' nem 'jogo menos maduro'."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    jogos = [
        _game(300, "2H", 45, 0, 0),
        _game(301, "2H", 88, 1, 1),
        _game(302, "2H", 60, 0, 1),
        _game(303, "2H", 75, 2, 0),
        _game(304, "2H", 30, 0, 0),
    ]
    client = FakeLiveClient(_routes_mundo(live_list=jogos))

    v = scan_live_opportunities(client)

    # TODOS os 5 elegiveis passaram pela triagem barata
    assert v.total_ao_vivo == 5 and v.total_elegiveis == 5
    assert v.total_triados == 5
    assert len(v.sondados) == 5
    assert {s["fixture_id"] for s in v.sondados} == {300, 301, 302, 303, 304}

    # limite de deep dive aplicado SOMENTE depois da triagem completa
    assert len(v.candidatos) == 2
    triados_fora = [d for d in v.descartados if "triagem global" in d.motivo]
    assert len(triados_fora) == 3
    for d in triados_fora:
        assert "janela" not in d.motivo
        assert "mais maduros" not in d.motivo
        assert "score" in d.motivo
    # relatorio com as quatro contagens obrigatorias
    text = format_live_opportunities(v)
    assert "passaram pela triagem barata: 5" in text
    assert "com cobertura em ao menos um mercado: 5" in text
    assert "enviados ao deep dive: 2" in text


def test_triagem_nao_descarta_jogo_pelo_minuto_mais_avancado(
    monkeypatch, tmp_path,
):
    """REGRA 6 da correcao: um jogo aos 45' NAO pode ser descartado
    simplesmente porque existe um jogo mais avancado. Jogo de 45' com
    dados pleno disputa com jogo de 88' (fora da janela de valor: quase
    sem tempo restante) e VENCE a triagem."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    jogos = [
        _game(300, "2H", 88, 1, 1),   # mais avancado, mas sem valor de mercado
        _game(301, "2H", 45, 0, 0),   # Botafogo x Palmeiras da correcao
    ]
    client = FakeLiveClient(_routes_mundo(live_list=jogos))

    v = scan_live_opportunities(client, deep=1)

    # TODOS os dois foram triados - nenhum ficou fora por minuto
    assert v.total_triados == 2 and len(v.sondados) == 2
    # o jogo de 45' recebeu o deep dive; o de 88' perdeu NO SCORE
    assert [c.snapshot.fixture_id for c in v.candidatos] == [301]
    descarte_88 = next(d for d in v.descartados if d.fixture_id == 300)
    assert "triagem global" in descarte_88.motivo
    assert "score" in descarte_88.motivo
    assert "janela" not in descarte_88.motivo


def test_triagem_score_janela_de_valor_e_componentes():
    """Score da triagem global: janela de valor 30-75' plena; decaimento
    apos 75' (pouco tempo restante = pouco valor de mercado); bonus de
    competencia identificada e de blocos por tempo. NUNCA dominado pelo
    minuto mais avancado."""
    # mesmos dados: jogo de 45' vale MAIS que jogo de 88'
    snap_45 = _snapshot(elapsed=45)
    snap_88 = _snapshot(elapsed=88)
    s45 = _triagem_global_score(snap_45)
    s88 = _triagem_global_score(snap_88)
    assert s45 > s88
    # janela de valor plena em 30', 60' e 75'
    for el in (30, 60, 75):
        assert _triagem_global_score(_snapshot(elapsed=el)) >= s45
    # rampa antes dos 30': jogo de 20' ainda pontua maturidade
    assert 0 < _triagem_global_score(_snapshot(elapsed=20)) < s45
    # competencia identificada (league_id + season) pontua; sem elas, menos
    sem_liga = _snapshot(elapsed=45)
    sem_liga.league_id = None
    sem_liga.season = None
    assert _triagem_global_score(sem_liga) < s45
    # blocos por tempo reais pontuam
    sem_tempos = _snapshot(elapsed=45)
    sem_tempos.stats_1h = {}
    sem_tempos.stats_2h = {}
    assert _triagem_global_score(sem_tempos) < s45


def test_varredura_todos_recentes_nada_aprovado(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    recentes = [_game(300 + i, "1H", 3, 0, 0) for i in range(4)]
    client = FakeLiveClient(_routes_mundo(live_list=recentes))

    v = scan_live_opportunities(client)

    assert v.sondados == [] and v.candidatos == [] and v.aprovadas == []
    assert v.total_triados == 0
    assert all("muito recente" in d.motivo for d in v.descartados)
    assert format_live_opportunities(v).count(NENHUMA_MSG) >= 1


def test_varredura_identidade_divergente_interrompe(monkeypatch, tmp_path):
    """Regra de identidade: divergencia INTERROMPE (nunca analisa outro
    clube por engano) - o erro de identidade atravessa a varredura
    MESMO na triagem barata de todos os jogos."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    errados = {HOME_ID: "Remo", AWAY_ID: AWAY}
    routes = _routes_mundo()
    routes["/teams"] = _teams_route(errados)
    client = FakeLiveClient(routes)

    with pytest.raises(IdentityDivergenceError):
        scan_live_opportunities(client)


def test_analise_de_um_jogo_ao_vivo(monkeypatch, tmp_path):
    """"Analise este jogo ao vivo" (por ID): deep dive completo do jogo."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    client = FakeLiveClient(_routes_mundo(live_list=[_game()]))

    cand = analyze_live_game_opportunities(client, str(FID))

    assert cand.snapshot.fixture_id == FID
    assert cand.snapshot.elapsed == 47
    assert cand.historico["n_home"] == 2
    assert cand.avaliacoes, "deep dive sempre avalia as familias com dados"
    text = format_live_game_opportunities(cand)
    assert "[FATO]" in text and "[CALCULO]" in text
    assert "[INTERPRETACAO]" in text  # analise pedida explicitamente
    assert SEM_ODD_LIVE in text
    # MATRIZ DE COBERTURA: elegibilidade por mercado na analise
    assert "[COBERTURA] MATRIZ DE ELEGIBILIDADE" in text
    assert "| LIVE | GOALS | A | PERMITIDO" in text
    assert "| LIVE | CORNERS | A | PERMITIDO" in text
    # escanteios 2o tempo reais do snapshot aparecem no bloco [FATO]
    assert "2o tempo" in text


# ----------------------------------------------------------------------
# 4 e 5. Aprovacao com auditoria (candidatos construidos a mao)
# ----------------------------------------------------------------------
def _snapshot(fid=FID, status="2H", elapsed=47, gh=1, ga=1,
              corner_h=6, corner_a=4, red_cards=0):
    return LiveSnapshot(
        fixture_id=fid, league_name="Serie A", country="Brazil", season=2026,
        round="Regular Season - 26", date_local="2026-09-06T19:30:00-03:00",
        home_team_id=HOME_ID, home_team_name=HOME,
        away_team_id=AWAY_ID, away_team_name=AWAY,
        goals_home=gh, goals_away=ga, halftime_home=0, halftime_away=1,
        status=status, elapsed=elapsed, league_id=LEAGUE_ID,
        stats_home={"Corner Kicks": corner_h, "Total Shots": 12,
                    "Ball Possession": "58%", "Red Cards": red_cards},
        stats_away={"Corner Kicks": corner_a, "Total Shots": 8,
                    "Ball Possession": "42%", "Red Cards": 0},
        stats_1h={}, stats_2h={HOME_ID: {"Corner Kicks": 3},
                               AWAY_ID: {"Corner Kicks": 2}},
        events=[],
        collected_at=now_brt().strftime("%d/%m/%Y %H:%M:%S"),
        has_stats=True,
    )


def _cand(snap, n_home=10, n_away=10, odds=None):
    cand = Candidato(snapshot=snap)
    cand.historico = {"games_home": [], "games_away": [],
                      "n_home": n_home, "n_away": n_away}
    cand.odds = odds
    return cand


def _av(snap, prob=0.85, conf=0.80, mercado="escanteios",
        linha="Over 9.5 escanteios (total do jogo)", atual=6, odd=None):
    return Avaliacao(
        jogo=f"{snap.home_team_name} x {snap.away_team_name}",
        fixture_id=snap.fixture_id, competicao="Serie A (Brazil)",
        minuto=snap.elapsed, status=snap.status,
        placar=f"{snap.goals_home}-{snap.goals_away}",
        mercado=mercado, linha=linha, prob=prob,
        sustentacao={"atual_no_jogo": atual, "tempo_restante_min": 43,
                     "taxa_combinada_por90": 10.0,
                     "modelo": "Poisson sobre o tempo restante (CALCULO)"},
        confianca=conf, conf_componentes={}, riscos=[], odd=odd,
        odds_live_existentes=odd is not None,
    )


def _audit_client(fid=FID):
    """Cliente minimo para a auditoria: fixture fresco do proprio jogo."""
    return FakeLiveClient({
        "/fixtures": lambda p: [_game(fid=fid)],
        "/teams": _teams_route(VALID_TEAMS),
    })


def test_aprovar_top1_com_auditoria_integral():
    snap = _snapshot()
    cand = _cand(snap)
    av = _av(snap)
    cand.avaliacoes = [av]

    aprovadas = _aprovar(_audit_client(), [cand])

    assert len(aprovadas) == 1
    assert aprovadas[0] is av and av.aprovada is True
    # auditoria completa gravada: 8 checagens do candidato + 4 da avaliacao
    nomes = [nome for nome, _ok, _d in av.auditoria]
    assert "jogo continua ao vivo" in nomes
    assert "identidade correta (IDs do fixture + nomes validados)" in nomes
    assert "dado ausente nunca virou zero" in nomes
    assert "odd pre-jogo nao usada como live" in nomes
    assert "probabilidade com sustentacao explicita" in nomes
    assert all(ok for _n, ok, _d in av.auditoria)
    assert av.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"


def test_aprovar_rejeita_probabilidade_baixa_e_linha_decidida():
    snap = _snapshot()
    cand1 = _cand(_snapshot(201))
    cand1.avaliacoes = [_av(_snapshot(201), prob=0.55)]
    cand2 = _cand(_snapshot(202))
    cand2.avaliacoes = [_av(_snapshot(202), prob=0.99)]

    aprovadas = _aprovar(_audit_client(), [cand1, cand2])

    assert aprovadas == []
    assert "70%" in cand1.avaliacoes[0].rejeicao
    assert PROB_MIN_APROVAR == 0.70
    assert "decidida" in cand2.avaliacoes[0].rejeicao


def test_aprovar_rejeita_confianca_baixa():
    cand = _cand(_snapshot(203))
    cand.avaliacoes = [_av(_snapshot(203), prob=0.85, conf=0.55)]

    aprovadas = _aprovar(_audit_client(), [cand])

    assert aprovadas == []
    assert "60%" in cand.avaliacoes[0].rejeicao
    assert CONF_MIN_TOP1 == 0.60


def test_aprovar_sem_margem_sobre_odd_real():
    """Com odd live REAL e atual: exige edge >= 3 p.p. sobre a implicita."""
    snap = _snapshot(204)
    odds = _parse_response(204, {
        "update": datetime.now(timezone.utc).isoformat(),
        "odds": [{"name": "Corners Over/Under",
                  "values": [{"value": "Over 9.5", "odd": "1.11"}]}],
    })
    # implicita 1/1.11 = 90.1%; prob 85% => edge negativo => reprovada
    cand = _cand(snap, odds=odds)
    av = _av(snap, odd=OddUsada(bookmaker="Bet365",
                                mercado_feed="Corners Over/Under",
                                value_feed="Over 9.5", odd=1.11,
                                update=datetime.now(timezone.utc).isoformat(),
                                implied=round(1 / 1.11, 4), atual=True))
    cand.avaliacoes = [av]

    aprovadas = _aprovar(_audit_client(204), [cand])

    assert aprovadas == []
    assert "sem margem" in av.rejeicao


def test_aprovar_odd_antiga_nao_e_tratada_como_atual():
    """Odd com timestamp fora da janela de frescor: exibida como historica,
    nao bloqueia a aprovacao (nao ha edge a validar contra preco antigo)."""
    snap = _snapshot(205)
    odds = _parse_response(205, {
        "update": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "odds": [{"name": "Corners Over/Under",
                  "values": [{"value": "Over 9.5", "odd": "1.90"}]}],
    })
    cand = _cand(snap, odds=odds)
    av = _av(snap, odd=OddUsada(
        bookmaker="Bet365", mercado_feed="Corners Over/Under",
        value_feed="Over 9.5", odd=1.90,
        update=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        implied=round(1 / 1.90, 4), atual=False))
    cand.avaliacoes = [av]

    aprovadas = _aprovar(_audit_client(205), [cand])

    assert len(aprovadas) == 1
    assert av.odd.atual is False
    assert av.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"


def test_top2_somente_se_forte_e_de_jogo_diferente():
    """TOP 2: jogo DIFERENTE e confianca >= CONF_MIN_TOP2; nunca segunda
    selecao apenas para formar multipla."""
    snap1, snap2 = _snapshot(206), _snapshot(207)
    forte = _cand(snap1)
    forte.avaliacoes = [_av(snap1, prob=0.88, conf=0.80)]
    fraco = _cand(snap2)
    fraco.avaliacoes = [_av(snap2, prob=0.80, conf=0.62)]  # passa TOP1, nao TOP2

    aprovadas = _aprovar(_audit_client(), [forte, fraco])

    assert len(aprovadas) == 1  # TOP 2 fraco e rebaixado
    assert "TOP 2 exige sustentacao forte" in fraco.avaliacoes[0].rejeicao
    assert "68%" in fraco.avaliacoes[0].rejeicao
    assert CONF_MIN_TOP2 == 0.68


def test_top2_do_mesmo_jogo_nunca():
    """Duas linhas do MESMO jogo: so a mais sustentada e aprovada (nunca
    multipla do mesmo jogo)."""
    snap = _snapshot(208)
    cand = _cand(snap)
    cand.avaliacoes = [
        _av(snap, prob=0.85, conf=0.80, linha="Over 9.5 escanteios (total do jogo)"),
        _av(snap, prob=0.84, conf=0.80,
            linha="Under 10.5 escanteios (total do jogo)"),
    ]

    aprovadas = _aprovar(_audit_client(208), [cand])

    assert len(aprovadas) == 1


def test_auditoria_reprova_snapshot_velho():
    """Snapshot com timestamp antigo: aprovacao bloqueada (estado pode
    ter mudado; 'atualizar novamente antes de aprovar' e obrigatoria)."""
    snap = _snapshot(209)
    snap.collected_at = (now_brt() - timedelta(minutes=10)).strftime(
        "%d/%m/%Y %H:%M:%S")
    cand = _cand(snap)
    cand.avaliacoes = [_av(snap)]

    aprovadas = _aprovar(_audit_client(209), [cand])

    assert aprovadas == []
    assert "snapshot com timestamp recente" in cand.avaliacoes[0].rejeicao


def test_auditoria_reprova_jogo_que_encerrou():
    """'Confirmar que o jogo continua ao vivo antes da conclusao': fixture
    fresco voltou FT => nada e aprovado com estado antigo."""
    encerrado = FakeLiveClient({
        "/fixtures": lambda p: [_game(fid=210, status="FT", elapsed=90, gh=2, ga=1)],
        "/teams": _teams_route(VALID_TEAMS),
    })
    snap = _snapshot(210)
    cand = _cand(snap)
    cand.avaliacoes = [_av(snap)]

    aprovadas = _aprovar(encerrado, [cand])

    assert aprovadas == []
    assert "jogo continua ao vivo" in cand.avaliacoes[0].rejeicao


def test_auditoria_reprova_historico_com_partida_posterior():
    """Historico nunca pode conter partida POSTERIOR ao jogo ao vivo."""
    snap = _snapshot(211)
    cand = _cand(snap)
    futuro = SimpleNamespace(date="2026-09-20T16:00:00-03:00")
    cand.historico["games_home"] = [futuro]
    cand.avaliacoes = [_av(snap)]

    aprovadas = _aprovar(_audit_client(211), [cand])

    assert aprovadas == []
    assert "posteriores" in cand.avaliacoes[0].rejeicao


# ----------------------------------------------------------------------
# 6. Odd live real: anexo com frescor
# ----------------------------------------------------------------------
def test_timestamp_recente():
    agora = datetime.now(timezone.utc)
    assert _timestamp_recente(agora.isoformat()) is True
    velha = (agora - timedelta(minutes=30)).isoformat()
    assert _timestamp_recente(velha) is False
    # timestamp ilegivel: nao bloqueia (fonte nao informou), exibido como aviso
    assert _timestamp_recente("") is True


def test_anexar_odd_real_somente_linha_identica():
    snap = _snapshot()
    agora = datetime.now(timezone.utc).isoformat()
    # Casamento EXATO (regra 08/09/2026): somente o mercado FT de TOTAL
    # DO JOGO da familia casa - bet ID estavel (45 = Corners Over Under)
    # ou nome canonico - com lado e linha identicos. Mercados de
    # periodo/escopo diferentes NUNCA casam com a linha FT.
    odds = _parse_response(FID, {
        "update": agora,
        "odds": [
            {"id": 45, "name": "Corners Over Under",
             "values": [{"value": "Over 9.5", "odd": "1.90"},
                        {"value": "Over 17.5", "odd": "9.50"}]},
            {"id": 77, "name": "Total Corners (1st Half)",
             "values": [{"value": "Over 9.5", "odd": "3.40"}]},
            {"id": 57, "name": "Home Corners Over/Under",
             "values": [{"value": "Over 15.5", "odd": "2.80"}]},
        ],
    })
    cand = _cand(snap, odds=odds)
    match = _av(snap, linha="Over 9.5 escanteios (total do jogo)")
    outra = _av(snap, linha="Over 15.5 escanteios (total do jogo)")
    cand.avaliacoes = [match, outra]

    _anexar_odd_real(cand.avaliacoes, cand)

    # linha EXATA do mercado FT exato: anexada com casa, mercado, odd,
    # timestamp e implicita
    assert match.odd is not None
    assert match.odd.bookmaker == "Bet365" or match.odd.bookmaker
    assert match.odd.odd == 1.90
    assert match.odd.mercado_feed == "Corners Over Under"
    assert match.odd.implied == pytest.approx(1 / 1.90, abs=1e-3)
    assert match.odd.atual is True
    assert match.odd.update == agora
    assert match.classificacao == "OPORTUNIDADE COM ODD AO VIVO REAL"
    assert match.edge == pytest.approx(0.85 - 1 / 1.90, abs=1e-3)
    # linha sem correspondencia real no feed: continua estatistica
    assert outra.odd is None
    assert outra.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"


def test_anexar_odd_real_antiga_marca_nao_atual():
    snap = _snapshot()
    velha = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    odds = _parse_response(FID, {
        "update": velha,
        "odds": [{"id": 45, "name": "Corners Over Under",
                  "values": [{"value": "Over 9.5", "odd": "1.90"}]}],
    })
    cand = _cand(snap, odds=odds)
    av = _av(snap, linha="Over 9.5 escanteios (total do jogo)")
    cand.avaliacoes = [av]

    _anexar_odd_real(cand.avaliacoes, cand)

    assert av.odd is not None
    assert av.odd.atual is False  # preco ANTIGO nunca e "atual"
    assert av.edge is None        # sem edge calculado contra preco antigo
    assert av.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"


# ----------------------------------------------------------------------
# 7. Expulsao altera a analise (mudanca estrutural)
# ----------------------------------------------------------------------
def test_expulsao_penaliza_confianca():
    hist = {"n_home": 10, "n_away": 10}
    bench = {"partidas_validas": 30}
    rate = RateBlend(per90=10.0, baseline_per90=10.0, observado_per90=10.0,
                     w_observado=0.5, minuto=47, detalhe="")

    sem_vermelho = _confianca(_snapshot(220, red_cards=0), hist, bench, rate, False)
    com_vermelho = _confianca(_snapshot(221, red_cards=1), hist, bench, rate, False)

    assert sem_vermelho[1]["sem_expulsao_estrutural"] == 1.0
    assert com_vermelho[1]["sem_expulsao_estrutural"] == 0.0
    assert com_vermelho[0] < sem_vermelho[0]  # confianca cai com expulsao


def test_confianca_componentes_pesso():
    """Componentes e pesos da confianca: soma ponderada em [0,1]."""
    hist = {"n_home": 5, "n_away": 5}
    bench = {"partidas_validas": 30}
    rate = RateBlend(per90=10.0, baseline_per90=10.0, observado_per90=10.0,
                     w_observado=0.5, minuto=47, detalhe="")
    conf, comps = _confianca(_snapshot(), hist, bench, rate, False)

    assert 0.0 <= conf <= 1.0
    assert set(comps) == {
        "qualidade_dados_live", "amostra_historica", "benchmark_competicao",
        "maturidade_do_minuto", "coerencia_historico_x_observado",
        "sem_expulsao_estrutural",
    }
    # snapshot completo + benchmark >= 30 validas: componentes cheios
    assert comps["qualidade_dados_live"] == 1.0
    assert comps["benchmark_competicao"] == 1.0
    # amostra historica 5/10 = 0.5
    assert comps["amostra_historica"] == 0.5
    # jogo maduro aos 47': maturidade = 0.4 + 0.6 * 47/75 (arredondada p/ 2)
    assert comps["maturidade_do_minuto"] == pytest.approx(0.78, abs=0.001)

# ----------------------------------------------------------------------
# 8. Transparencia do TOP 1/TOP 2: secoes completas, s/d e frequencias
# ----------------------------------------------------------------------
def _hist_game(total=None, played_at_home=True, gols_for=None,
               gols_against=None, fid=900,
               date="2026-08-20T16:00:00-03:00", opponent="Adversario FC"):
    """Jogo de historico real (TeamGameStats) na unidade do mercado."""
    return TeamGameStats(
        fixture_id=fid, date=date, league="Serie A",
        round="Regular Season - 20", status="FT", opponent=opponent,
        played_at_home=played_at_home,
        corners_for=(total // 2 if total is not None else None),
        corners_against=(total - total // 2 if total is not None else None),
        corners_total=total,
        goals_for=gols_for, goals_against=gols_against,
        shots_for=None, shots_against=None,
        shots_on_goal_for=None, shots_on_goal_against=None,
        possession_for=None, yellow_for=None, red_for=None,
    )


def _cand_transparente(snap):
    """Candidato com recortes reais: 10 jogos por time, benchmark e h2h."""
    cand = _cand(snap)
    cand.historico["games_home"] = [
        _hist_game(total=t, played_at_home=(i % 2 == 0), fid=900 + i)
        for i, t in enumerate([12, 11, 10, 9, 8, 12, 11, 10, 9, 12])
    ]
    cand.historico["games_away"] = [
        _hist_game(total=t, played_at_home=False, fid=950 + i)
        for i, t in enumerate([6, 5, 7, 4, 6, 8, 5, 6, 7, 6])
    ]
    cand.benchmark_escanteios = {
        "liga": "Serie A", "liga_id": LEAGUE_ID, "temporada": 2026,
        "partidas_validas": 217, "partidas_encerradas": 226,
        "partidas_sem_escanteios": 9,
        "describe": {"media": 9.19},
        "over": {9.5: {"hits": 120, "total": 217, "pct": 55.3}},
    }
    cand.benchmark_gols = {
        "liga": "Serie A", "liga_id": LEAGUE_ID, "temporada": 2026,
        "partidas_validas": 226, "describe": {"media": 2.26},
    }
    cand.h2h_stats = {
        "n_confrontos_com_dados": 4, "describe": {"media": 10.5},
        "over": {9.5: {"hits": 2, "total": 4, "pct": 50.0}},
    }
    cand.h2h_n = 4
    return cand


def test_relatorio_top1_transparencia_completa():
    """TOP 1 na varredura: TODAS as secoes exigidas aparecem, com as
    frequencias historicas da linha e o benchmark real."""
    snap = _snapshot()
    cand = _cand_transparente(snap)
    av = _av(snap)  # Over 9.5 escanteios, prob 85%, confianca 80%
    av.sustentacao.update({
        "minuto": 47, "baseline": "cruzamento casa/fora + benchmark da liga",
        "peso_do_observado": 0.39, "esperado_no_restante": 4.8,
        "projecao_final": 14.8,
    })
    cand.avaliacoes = [av]
    aprovadas = _aprovar(_audit_client(), [cand])
    assert len(aprovadas) == 1  # pre-condicao: aprovada com auditoria

    v = Varredura(
        hora="06/09/2026 20:00:00", total_ao_vivo=25,
        total_elegiveis=22, total_triados=22,
        sondados=[{"jogo": av.jogo, "fixture_id": FID, "competicao": "Serie A",
                   "status": "2H", "minuto": 47, "placar": "1-1",
                   "score_triagem": 9.8}],
        candidatos=[cand], aprovadas=aprovadas,
    )
    text = format_live_opportunities(v)

    # seis secoes obrigatorias do TOP 1
    assert "TOP 1" in text
    assert "[FATO - ESTADO ATUAL]" in text
    assert "[FATO - CONTEXTO HISTORICO]" in text
    assert "[CALCULO]" in text
    assert "[CONTRADICOES / RISCOS]" in text
    assert "[ODD]" in text
    assert "[INTERPRETACAO]" in text
    # estado atual: escanteios por equipe E total
    assert "escanteios: 6 (Corinthians) - 4 (Chapecoense-sc) | total: 10" in text
    # interpretacao em linguagem simples: aprovacao + riscos
    assert "POR QUE FOI APROVADA:" in text
    assert "O QUE PODE FAZER DAR ERRADO:" in text
    # frequencia historica da linha nos recortes realmente usados
    assert "frequencia historica da linha" in text
    assert "ultimos 10 (Corinthians): 7/10 (70%)" in text
    assert "ultimos 10 (Chapecoense-sc): 0/10 (0%)" in text
    assert "conjunto dos dois times (ate 20 jogos): 7/20 (35%)" in text
    assert "ate 50 jogos: s/d" in text  # recorte nao coletado: honesto
    assert "benchmark da liga: 55% (120/217 partidas over 9.5)" in text
    assert "h2h (peso menor): 4 com dados" in text
    # mando casa/fora: jogos em casa do mandante e fora do visitante
    assert "Corinthians em casa (mando do fixture)" in text
    assert "Chapecoense-sc fora (mando do fixture)" in text
    # calculo: todos os inputs da probabilidade
    assert "modelo utilizado: Poisson sobre o tempo restante (CALCULO)" in text
    assert "probabilidade estimada: 85.0%" in text
    assert "tamanho efetivo da amostra" in text
    assert "regra de batida" in text
    # contradicoes: mais favorece / mais contradiz
    assert "dado que mais favorece:" in text
    assert "dado que mais contradiz:" in text
    # odd: ausente na fonte + regra contra odd pre-jogo
    assert SEM_ODD_LIVE in text
    assert "odd pre-jogo nunca e usada como odd ao vivo" in text
    # auditoria integral continua no fim do bloco
    assert "auditoria obrigatoria (12 checagens)" in text


def test_relatorio_dado_ausente_aparece_como_s_d_nunca_zero():
    """Dado ausente => "s/d" em TODAS as secoes; o antigo "nd" nao existe
    mais; benchmark/h2h ausentes sao explicitados."""
    snap = _snapshot()
    snap.stats_home.pop("Red Cards")   # vermelhos ausentes => s/d
    snap.stats_away.pop("Red Cards")
    cand = _cand(snap)  # historico e benchmarks vazios
    av = _av(snap)
    cand.avaliacoes = [av]
    aprovadas = _aprovar(_audit_client(), [cand])
    assert len(aprovadas) == 1

    text = "\n".join(format_oportunidade(av, cand, "TOP 1"))

    assert "[FATO - ESTADO ATUAL]" in text
    assert "Faltas: s/d - s/d" in text
    assert "Impedimentos: s/d - s/d" in text
    assert "Cartoes amarelos: s/d - s/d" in text
    assert "Cartoes vermelhos: s/d - s/d" in text
    assert "expulsoes (cartoes vermelhos): s/d - s/d | total: s/d" in text
    assert "s/d (a fonte nao retornou eventos nesta leitura)" in text
    assert "ultimos 0 (Corinthians): s/d (nenhum jogo com este dado na amostra)" in text
    assert "benchmark da competicao: dado nao disponivel na fonte" in text
    assert "h2h (peso menor): s/d (sem confrontos com dados)" in text
    assert "dados ausentes importantes:" in text
    assert "(nd)" not in text  # legado "nd" abolido
    assert ": nd" not in text


def test_relatorio_under_gols_regra_de_batida_e_benchmark_honesto():
    """Under de gols: regra de batida explicita; benchmark de gols so tem
    a media => frequencia por linha aparece como s/d (nunca inventada)."""
    snap = _snapshot()  # placar 1-1
    cand = _cand(snap)
    cand.historico["games_home"] = [
        _hist_game(gols_for=g, gols_against=h, fid=900 + i)
        for i, (g, h) in enumerate([(1, 0), (0, 1), (2, 0), (1, 1), (3, 0)])
    ] + [_hist_game(fid=990)]  # sem gols informados: excluido, nunca zerado
    cand.historico["games_away"] = [
        _hist_game(gols_for=g, gols_against=h, played_at_home=False, fid=950 + i)
        for i, (g, h) in enumerate([(2, 1), (1, 1), (1, 0), (0, 2), (2, 0)])
    ]
    cand.benchmark_gols = {
        "liga": "Serie A", "liga_id": LEAGUE_ID, "temporada": 2026,
        "partidas_validas": 226, "describe": {"media": 2.26},
    }
    av = _av(snap, mercado="gols", linha="Under 5.5 gols (total do jogo)",
             atual=2)
    av.sustentacao.update({"esperado_no_restante": 0.9,
                           "tempo_restante_min": 43})
    cand.avaliacoes = [av]
    aprovadas = _aprovar(_audit_client(), [cand])
    assert len(aprovadas) == 1

    text = "\n".join(format_oportunidade(av, cand, "TOP 1"))

    assert "Under 5.5 permite no maximo 3 no tempo restante" in text
    assert "benchmark da liga: s/d (benchmark de gols coletado: apenas a media 2.26 gols" in text
    assert "ultimos 6 (Corinthians): 5/5 (100%) | 1 sem o dado na fonte (excluidos, nunca zerados)" in text
    assert "ultimos 5 (Chapecoense-sc): 5/5 (100%)" in text


def test_relatorio_exclui_jogo_sem_dado_da_frequencia():
    """Jogo da amostra sem o dado da unidade: sai do denominador e e
    contabilizado como excluido (regra de integridade)."""
    snap = _snapshot()
    cand = _cand(snap)
    cand.historico["games_home"] = [
        _hist_game(total=12, fid=901),
        _hist_game(total=None, fid=902),  # sem escanteios na fonte
        _hist_game(total=8, fid=903),
    ]
    av = _av(snap)  # Over 9.5: 12 bate, 8 nao, None excluido
    cand.avaliacoes = [av]
    aprovadas = _aprovar(_audit_client(), [cand])
    assert len(aprovadas) == 1

    text = "\n".join(format_oportunidade(av, cand, "TOP 1"))
    assert "ultimos 3 (Corinthians): 1/2 (50%) | 1 sem o dado na fonte (excluidos, nunca zerados)" in text


def test_relatorio_odd_real_mostra_diferenca_para_estimada():
    """Com odd REALMENTE ao vivo: casa, linha, odd, implicita e a
    diferenca para a probabilidade estimada (edge)."""
    snap = _snapshot(212)
    agora = datetime.now(timezone.utc).isoformat()
    odds = _parse_response(212, {
        "update": agora,
        "odds": [{"name": "Corners Over/Under",
                  "values": [{"value": "Over 9.5", "odd": "1.50"}]}],
    })
    cand = _cand(snap, odds=odds)  # respaldo real de /odds/live no candidato
    av = _av(snap, odd=OddUsada(
        bookmaker="Bet365", mercado_feed="Corners Over/Under",
        value_feed="Over 9.5", odd=1.50, update=agora,
        implied=round(1 / 1.50, 4), atual=True))
    cand.avaliacoes = [av]
    aprovadas = _aprovar(_audit_client(212), [cand])
    assert len(aprovadas) == 1  # prob 85% vs implicita 66.7%: edge valido

    text = "\n".join(format_oportunidade(av, cand, "TOP 1"))

    assert "bookmaker: Bet365" in text
    assert "linha no feed: Over 9.5" in text
    assert "odd: 1.5 (probabilidade implicita 66.7%)" in text
    assert "diferenca para a probabilidade estimada: +18.3%" in text
    assert "classificacao: OPORTUNIDADE COM ODD AO VIVO REAL" in text
