"""Testes da INTEGRACAO LIVE da familia RESULTADO (1X2, Dupla Chance,
DNB, AH) ao fluxo ao vivo - compete em igualdade com gols/escanteios.

Verifica o que a integracao promete:
  1. o leque de resultado ENTRA na avaliacao live com linhas canonicas
     liquidadaveis (mesma fonte de verdade do registro);
  2. resultado pode vencer gols/escanteios quando tem score superior;
  3. gols/escanteios continuam vencendo quando sao melhores;
  4. placar/minuto/tempo restante ALTERAM a avaliacao live;
  5. a probabilidade PRE-JOGO nunca e reutilizada como probabilidade
     live (o live reflete o estado atual da partida);
  6. odd PRE-JOGO nunca e tratada como odd live (odd so existe via
     candidato.odds = /odds/live; sem correspondencia exata => None);
  7. o registro congela o snapshot (minuto/placar/prob) antes do
     resultado e permanece imutavel (dedupe);
  8. nao existe duplicacao (linhas nem recomendacoes);
  9. ausencia de dado nunca vira zero (sem historico de gols ou placar
     => lista VAZIA, nunca amostra inventada).
"""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.live import LiveSnapshot, now_brt
from src.live_opportunity import Candidato, _aprovar, _avaliar_resultado
from src.odds import _parse_response
from src.registry import VERSAO_LIVE, RegistroRecomendacoes
from src.resultado import avaliar_resultado_prejogo
from src.settlement import _e_linha_resultado

FID = 700
HOME_ID, HOME = 131, "Corinthians"
AWAY_ID, AWAY = 132, "Chapecoense-sc"
LEAGUE_ID = 71

_BENCH_GOLS = {"partidas_validas": 40, "describe": {"media": 2.8}}


# ----------------------------------------------------------------------
# Builders (mesmo formato real dos objetos do motor)
# ----------------------------------------------------------------------
def _snap(status="2H", elapsed=47, gh=1, ga=1):
    return LiveSnapshot(
        fixture_id=FID, league_name="Serie A", country="Brazil",
        season=2026, round="Regular Season - 26",
        date_local="2026-09-06T19:30:00-03:00",
        home_team_id=HOME_ID, home_team_name=HOME,
        away_team_id=AWAY_ID, away_team_name=AWAY,
        goals_home=gh, goals_away=ga, halftime_home=0, halftime_away=1,
        status=status, elapsed=elapsed, league_id=LEAGUE_ID,
        stats_home={"Corner Kicks": 6, "Total Shots": 12,
                    "Ball Possession": "58%", "Red Cards": 0},
        stats_away={"Corner Kicks": 4, "Total Shots": 8,
                    "Ball Possession": "42%", "Red Cards": 0},
        stats_1h={}, stats_2h={}, events=[],
        collected_at=now_brt().strftime("%d/%m/%Y %H:%M:%S"),
        has_stats=True,
    )


def _game(gf, gc, casa):
    return SimpleNamespace(
        played_at_home=casa, goals_for=gf, goals_against=gc,
        corners_for=6, corners_against=4, yellow_for=2,
        date="2026-08-20T16:00:00-03:00",
    )


def _hist(n=10):
    games_h = [_game(1.8, 1.0, True) for _ in range(n)]
    games_a = [_game(1.0, 1.3, False) for _ in range(n)]
    return {"games_home": games_h, "games_away": games_a,
            "n_home": n, "n_away": n}


def _cand(snap, odds=None, n=10):
    cand = Candidato(snapshot=snap)
    cand.historico = _hist(n)
    cand.benchmark_gols = _BENCH_GOLS
    cand.odds = odds
    return cand


_RAW_LIVE = {
    "fixture": {"id": FID, "date": "2026-09-06T19:30:00-03:00",
                "status": {"short": "2H", "elapsed": 47},
                "venue": {"name": "Arena", "city": "Sao Paulo"}},
    "league": {"id": LEAGUE_ID, "name": "Serie A", "country": "Brazil",
               "round": "Regular Season - 26", "season": 2026},
    "teams": {"home": {"id": HOME_ID, "name": HOME},
              "away": {"id": AWAY_ID, "name": AWAY}},
    "goals": {"home": 1, "away": 1},
    "score": {"halftime": {"home": 0, "away": 1}},
}


class _AuditClient:
    """Cliente minimo para a auditoria: fixture AO VIVO fresco do
    proprio jogo (o id consultado) + nomes validados em /teams."""

    def get(self, endpoint, params=None, use_cache=True, ttl=None):
        params = params or {}
        if endpoint == "/fixtures":
            raw = copy.deepcopy(_RAW_LIVE)
            raw["fixture"]["id"] = params.get("id")
            return [raw]
        if endpoint == "/teams":
            nomes = {HOME_ID: HOME, AWAY_ID: AWAY}
            tid = params.get("id")
            if tid in nomes:
                return [{"team": {"id": tid, "name": nomes[tid],
                                  "country": "Brazil"}}]
            return []
        raise AssertionError(f"endpoint inesperado: {endpoint}")


def _av_total(snap, prob, conf, mercado="escanteios",
              linha="Over 10.5 escanteios (total do jogo)", atual=10):
    return SimpleNamespace(
        jogo=f"{snap.home_team_name} x {snap.away_team_name}",
        fixture_id=snap.fixture_id,
        competicao="Serie A (Brazil)",
        minuto=snap.elapsed, status=snap.status,
        placar=f"{snap.goals_home}-{snap.goals_away}",
        mercado=mercado, linha=linha, prob=prob,
        sustentacao={"atual_no_jogo": atual, "tempo_restante_min": 43,
                     "taxa_combinada_por90": 10.0,
                     "modelo": "Poisson sobre o tempo restante (CALCULO)"},
        confianca=conf, conf_componentes={}, riscos=[],
        odd=None, odds_live_existentes=False, aprovada=False,
        rejeicao=None, auditoria=None,
    )


# ----------------------------------------------------------------------
# 1. Entrada do leque no live (linhas canonicas liquidadaveis)
# ----------------------------------------------------------------------
def test_leque_de_resultado_live_com_linhas_liquidadaveis():
    # SEM odds na fonte: a familia existe mesmo assim (estatistica)
    aves = _avaliar_resultado(_cand(_snap()))
    assert aves and all(av.mercado == "resultado" for av in aves)
    linhas = [av.linha for av in aves]
    assert len(linhas) == len(set(linhas))  # sem duplicacao de linha

    # familias completas do bloco validado
    assert {"Vitoria mandante (1)", "Empate (X)",
            "Vitoria visitante (2)"} <= set(linhas)
    assert {"Dupla chance 1X", "Dupla chance X2",
            "Dupla chance 12"} <= set(linhas)
    assert {"DNB mandante (empate anula)",
            "DNB visitante (empate anula)"} <= set(linhas)
    assert {"AH mandante 0.0 (90 minutos)",
            "AH mandante -0.75 (90 minutos)",
            "AH visitante +1.25 (90 minutos)"} <= set(linhas)

    for av in aves:
        # TODA linha e liquidadavel pelo texto exato congelado
        assert _e_linha_resultado(av.linha), av.linha
        assert av.odd is None  # sem feed: odd/bookmaker permanecem None
        assert av.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"
        # snapshot do estado no momento da avaliacao
        assert av.minuto == 47 and av.placar == "1-1"
        assert "minuto" in av.sustentacao["distribuicao"]


# ----------------------------------------------------------------------
# 2/3. Comparador live: qualquer familia pode vencer
# ----------------------------------------------------------------------
def test_resultado_sem_odd_pode_ser_aprovado(monkeypatch, tmp_path):
    """Core da integracao: linha de resultado SEM odd ao vivo real e
    aprovada como OPORTUNIDADE ESTATISTICA AO VIVO (antes era pulada)."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    cand = _cand(_snap(elapsed=70, gh=2, ga=0))
    cand.avaliacoes = _avaliar_resultado(cand)

    aprovadas = _aprovar(_AuditClient(), [cand])

    assert aprovadas, "alguma linha de resultado deve ser aprovada"
    assert aprovadas[0].mercado == "resultado"
    assert aprovadas[0].odd is None
    assert aprovadas[0].classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"
    assert aprovadas[0].auditoria  # auditoria integral rodou
    assert _e_linha_resultado(aprovadas[0].linha)


def test_resultado_pode_vencer_escanteios(monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    snap = _snap(elapsed=70, gh=2, ga=0)
    cand = _cand(snap)
    # concorrente fraco no MESMO jogo + leque real de resultado
    fraca = _av_total(snap, prob=0.71, conf=0.62)
    cand.avaliacoes = [fraca] + _avaliar_resultado(cand)

    aprovadas = _aprovar(_AuditClient(), [cand])

    assert aprovadas
    assert aprovadas[0].mercado == "resultado"
    assert aprovadas[0].prob > fraca.prob


def test_escanteios_continuam_vencendo_quando_melhores(
        monkeypatch, tmp_path):
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    snap = _snap()  # 1-1 aos 47': nenhuma linha de resultado domina
    cand = _cand(snap)
    forte = _av_total(snap, prob=0.88, conf=0.93)
    # linhas de resultado REAIS fracas no empate (prob <= 0.75)
    fracas = [av for av in _avaliar_resultado(cand) if av.prob <= 0.75]
    assert fracas, "cenario deve ter linhas de resultado na disputa"
    cand.avaliacoes = [forte] + fracas

    aprovadas = _aprovar(_AuditClient(), [cand])

    assert aprovadas
    assert aprovadas[0].mercado == "escanteios"
    assert aprovadas[0].linha == forte.linha


# ----------------------------------------------------------------------
# 4. Estado do jogo (placar/minuto/tempo restante) altera a avaliacao
# ----------------------------------------------------------------------
def test_placar_minuto_e_tempo_restante_alteram_a_avaliacao():
    linha = "AH mandante -0.5 (90 minutos)"
    # 0-0 aos 15' x 1-0 aos 85' (mesmo historico): placar muda tudo
    ini = {av.linha: av.prob
           for av in _avaliar_resultado(_cand(_snap("1H", 15, 0, 0)))}
    fim = {av.linha: av.prob
           for av in _avaliar_resultado(_cand(_snap("2H", 85, 1, 0)))}
    assert fim[linha] > ini[linha]

    # tempo restante: 1-0 aos 20' x 1-0 aos 85' (mesmo placar)
    meio = {av.linha: av.prob
            for av in _avaliar_resultado(_cand(_snap("1H", 20, 1, 0)))}
    assert fim[linha] > meio[linha]

    # a sustentacao carrega minuto, placar e tempo restante do estado
    av_fim = next(av for av in _avaliar_resultado(_cand(_snap("2H", 85, 1, 0)))
                  if av.linha == linha)
    d = av_fim.sustentacao["distribuicao"]
    assert "minuto 85" in d and "1-0" in d and "5' restantes" in d


# ----------------------------------------------------------------------
# 5. Probabilidade pre-jogo NUNCA e reutilizada como live
# ----------------------------------------------------------------------
def test_probabilidade_prejogo_nunca_e_reutilizada_como_live():
    hist = _hist()
    # bloco pre-jogo validado sobre o MESMO historico
    pre = {a.linha: a.prob
           for a in avaliar_resultado_prejogo(hist, _BENCH_GOLS)}
    # avaliacao live aos 85' com 1-0: o ESTADO desloca as linhas
    live = {av.linha: av.prob
            for av in _avaliar_resultado(_cand(_snap("2H", 85, 1, 0)))}

    # linhas identicas, numeros DIFERENTES: o live reflete o placar e
    # o tempo restante, nunca a projecao integral pre-jogo.
    # mandante vencendo por 1 aos 85': AH mandante -0.5 (vitoria
    # simples) dispara; AH visitante -1.5 (vitoria por 2 do visitante)
    # colapsa - as duas direcoes provam que o ESTADO deslocou as linhas
    assert live["AH mandante -0.5 (90 minutos)"] > (
        pre["AH mandante -0.5 (90 minutos)"] + 1e-3)
    assert live["AH visitante -1.5 (90 minutos)"] < (
        pre["AH visitante -1.5 (90 minutos)"] - 1e-3)
    # e o snapshot live carrega minuto/placar na sustentacao
    av = next(a for a in _avaliar_resultado(_cand(_snap("2H", 85, 1, 0)))
              if a.linha == "AH mandante -0.5 (90 minutos)")
    assert av.minuto == 85 and av.placar == "1-0"


# ----------------------------------------------------------------------
# 6. Odd live real: convencao de sinal + pre-jogo nunca e live
# ----------------------------------------------------------------------
def test_odd_live_real_anexada_pela_convencao_de_sinal():
    odds = _parse_response(FID, {
        "update": datetime.now(timezone.utc).isoformat(),
        "odds": [
            {"name": "Asian Handicap",
             "values": [{"value": "Home -0.75", "odd": "1.85"},
                        {"value": "Away +0.5", "odd": "2.10"}]},
            {"name": "Match Winner",
             "values": [{"value": "Home", "odd": "2.40"}]},
            {"name": "Draw No Bet",
             "values": [{"value": "Home", "odd": "1.55"}]},
            {"name": "Double Chance",
             "values": [{"value": "Home/Draw", "odd": "1.20"}]},
        ],
    })
    por = {av.linha: av for av in _avaliar_resultado(_cand(_snap(),
                                                         odds=odds))}

    # convencao validada: feed Home -0.75 => linha real -0.75 mandante
    ah = por["AH mandante -0.75 (90 minutos)"]
    assert ah.odd is not None and ah.odd.odd == 1.85 and ah.odd.atual
    assert ah.classificacao == "OPORTUNIDADE COM ODD AO VIVO REAL"
    # feed Away +0.5: sinal INVERTE na linha real => -0.5 visitante
    away = por["AH visitante -0.5 (90 minutos)"]
    assert away.odd is not None and away.odd.odd == 2.10
    # 1X2 / DNB / DC do feed mapeiam para as linhas canonicas
    assert por["Vitoria mandante (1)"].odd is not None
    assert por["DNB mandante (empate anula)"].odd is not None
    assert por["Dupla chance 1X"].odd is not None
    # sem correspondencia EXATA no feed: linha fica SEM odd (nunca a
    # odd de outra linha, nunca odd pre-jogo)
    assert por["AH mandante -1.5 (90 minutos)"].odd is None
    assert por["Empate (X)"].odd is None
    # toda avaliacao sabe se a fonte TEM odds live (mesmo sem par)
    assert all(av.odds_live_existentes for av in por.values())


def test_odd_live_antiga_nao_e_tratada_como_atual():
    odds = _parse_response(FID, {
        "update": (datetime.now(timezone.utc)
                   - timedelta(hours=1)).isoformat(),
        "odds": [{"name": "Asian Handicap",
                  "values": [{"value": "Home -0.75", "odd": "1.85"}]}],
    })
    ah = next(av for av in _avaliar_resultado(_cand(_snap(), odds=odds))
              if av.linha == "AH mandante -0.75 (90 minutos)")
    # timestamp fora da janela de frescor: exibida, mas NAO e "atual"
    assert ah.odd is not None and ah.odd.atual is False
    assert ah.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"


# ----------------------------------------------------------------------
# 7/8. Registro: congela o snapshot antes do resultado; sem duplicacao
# ----------------------------------------------------------------------
def test_registro_congela_snapshot_live_sem_duplicacao(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))

    def _melhor(snap):
        cand = _cand(snap)
        aves = _avaliar_resultado(cand)
        return cand, max(aves, key=lambda a: a.prob)

    snap = _snap(elapsed=47, gh=1, ga=1)
    cand, alvo = _melhor(snap)
    rec_id, novo = reg.registrar_de_avaliacao(alvo, cand)
    assert novo is True

    rec = reg.obter(rec_id)
    # snapshot congelado: minuto, placar, mercado, linha e probabilidade
    assert rec["mercado"] == "resultado"
    assert _e_linha_resultado(rec["linha"])
    assert rec["minuto"] == 47 and rec["placar"] == "1-1"
    assert rec["probabilidade"] == pytest.approx(alvo.prob)
    assert rec["confianca"] == pytest.approx(alvo.confianca)
    assert rec["odd"] is None and rec["bookmaker"] is None
    assert rec["versao_analise"] == VERSAO_LIVE

    # reescaneio IDENTICO: dedupe, nunca segunda recomendacao
    rec_id2, novo2 = reg.registrar_de_avaliacao(alvo, cand)
    assert novo2 is False and rec_id2 == rec_id
    assert len(reg.listar(fixture_id=FID, tipo="live")) == 1

    # estado do jogo mudou (2-1 aos 60'): observacao NOVA, nao duplicata
    snap2 = _snap(elapsed=60, gh=2, ga=1)
    cand2, alvo2 = _melhor(snap2)
    rec_id3, novo3 = reg.registrar_de_avaliacao(alvo2, cand2)
    assert novo3 is True and rec_id3 != rec_id
    # o registro original permanece IMUTAVEL (snapshot de 47' intacto)
    original = reg.obter(rec_id)
    assert original["minuto"] == 47 and original["placar"] == "1-1"
    assert len(reg.listar(fixture_id=FID, tipo="live")) == 2


# ----------------------------------------------------------------------
# 9. Ausencia de dado nunca vira zero
# ----------------------------------------------------------------------
def test_sem_dado_nunca_vira_zero():
    # sem historico de gols: distribuicao impossivel => lista VAZIA
    cand = _cand(_snap())
    cand.historico = {"games_home": [], "games_away": [],
                      "n_home": 0, "n_away": 0}
    assert _avaliar_resultado(cand) == []

    # placar ausente na fonte: nunca interpretado como 0-0
    snap = _snap()
    snap.goals_home = None
    assert _avaliar_resultado(_cand(snap)) == []

    # minutos decorridos ausentes: sem tempo restante => sem avaliacao
    snap2 = _snap()
    snap2.elapsed = None
    assert _avaliar_resultado(_cand(snap2)) == []