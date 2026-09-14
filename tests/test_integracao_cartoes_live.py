"""Testes da INTEGRACAO LIVE da familia TOTAL DE CARTOES ao comparador
ao vivo - compete em igualdade com gols/escanteios/resultado.

O que a integracao promete (e estes testes verificam):
  1. cartoes ENTRAM na avaliacao live SEM exigir odd real (o gate antigo
     de odds e removido): sem odd de cartoes no feed => OPORTUNIDADE
     ESTATISTICA AO VIVO (odd/bookmaker None, nunca odd pre-jogo);
  2. linhas canonicas LIQUIDAVEIS pelo settlement validado, com minuto,
     placar, tempo restante e total em pontos (amarelo=1, vermelho=2);
  3. vermelhos contam 2 PONTOS no estado; expulsao reduz a confianca;
  4. o ESTADO do jogo (cartoes ja ocorridos, minuto) desloca as
     probabilidades: a projecao pre-jogo NUNCA e reutilizada como live;
  5. amarelos OU vermelhos ausentes na fonte => familia ADIADA com
     aviso (nunca zero); sem sustentacao (nem times, nem liga) => [];
  6. o benchmark real de CARTOES da liga entra no blend 65/35 e na
     confianca (nunca o de gols, nunca inventado);
  7. faltas da fonte: CONTEXTO FACTUAL na sustentacao quando existem;
     ausentes => chave nao existe (nunca inventadas);
  8. o filtro --mercado controla a familia no deep dive (benchmark de
     cartoes so e computado quando a familia entra na varredura);
  9. cartoes PODEM vencer gols/escanteios no comparador live; gols
     vencem quando sao melhores (sem preferencia artificial);
 10. recomendacao live aprovada e congelada no registro imutavel
     (snapshot de minuto/placar/prob antes do resultado; dedupe).

Nenhuma matematica aprovada e alterada aqui: o bloco pre-jogo
(src/cartoes.py), a liquidacao (src/settlement.py) e o motor live das
demais familias (_avaliar_total/_aprovar/auditoria) sao reusados COMO
ESTAO.
"""

import copy
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.cartoes import avaliar_cartoes_prejogo
from src.live import LiveSnapshot, now_brt
from src.live_opportunity import (
    Candidato,
    _anexar_odd_real,
    _aprovar,
    _avaliar_cartoes_live,
    deep_dive,
)
from src.odds import _parse_response
from src.registry import VERSAO_LIVE, RegistroRecomendacoes
from src.settlement import _MARCA_CONVENCAO_CARTOES, _parse_linha

FID = 900
HOME_ID, HOME = 131, "Corinthians"
AWAY_ID, AWAY = 132, "Chapecoense-sc"
LEAGUE_ID = 71

# benchmark real de cartoes da liga (formato de league_cards_average)
_BENCH_CARTOES = {
    "liga_id": LEAGUE_ID, "liga": "Serie A", "temporada": 2026,
    "partidas_encerradas": 40, "partidas_validas": 40,
    "partidas_sem_cartoes": 0,
    "describe": {"media": 5.2},
    "convencao": _MARCA_CONVENCAO_CARTOES,
}


# ----------------------------------------------------------------------
# Builders (mesmo formato real dos objetos do motor)
# ----------------------------------------------------------------------
def _snap(status="2H", elapsed=47, gh=1, ga=1,
          yc_h=2, yc_a=2, rc_h=0, rc_a=0, fouls_h=None, fouls_a=None):
    stats_home = {
        "Corner Kicks": 6, "Total Shots": 12, "Ball Possession": "58%",
        "Yellow Cards": yc_h, "Red Cards": rc_h,
    }
    stats_away = {
        "Corner Kicks": 4, "Total Shots": 8, "Ball Possession": "42%",
        "Yellow Cards": yc_a, "Red Cards": rc_a,
    }
    if fouls_h is not None:
        stats_home["Fouls"] = fouls_h
    if fouls_a is not None:
        stats_away["Fouls"] = fouls_a
    return LiveSnapshot(
        fixture_id=FID, league_name="Serie A", country="Brazil",
        season=2026, round="Regular Season - 26",
        date_local="2026-09-06T19:30:00-03:00",
        home_team_id=HOME_ID, home_team_name=HOME,
        away_team_id=AWAY_ID, away_team_name=AWAY,
        goals_home=gh, goals_away=ga, halftime_home=0, halftime_away=1,
        status=status, elapsed=elapsed, league_id=LEAGUE_ID,
        stats_home=stats_home, stats_away=stats_away,
        stats_1h={}, stats_2h={}, events=[],
        collected_at=now_brt().strftime("%d/%m/%Y %H:%M:%S"),
        has_stats=True,
    )


def _game(casa, yf=3, ya=2, rf=0, ra=0):
    return SimpleNamespace(
        played_at_home=casa, goals_for=1.5, goals_against=1.2,
        corners_for=5, corners_against=4,
        yellow_for=yf, yellow_against=ya, red_for=rf, red_against=ra,
        date="2026-08-20T16:00:00-03:00",
    )


def _hist(n=10):
    # baseline de pontos: mandante (3+2)/lado... cruzado => total 4.5
    games_h = [_game(True) for _ in range(n)]
    games_a = [_game(False, yf=2, ya=2) for _ in range(n)]
    return {"games_home": games_h, "games_away": games_a,
            "n_home": n, "n_away": n}


def _cand(snap, bench=_BENCH_CARTOES, odds=None, n=10):
    cand = Candidato(snapshot=snap)
    cand.historico = _hist(n)
    cand.benchmark_cartoes = bench
    cand.odds = odds
    return cand


_RAW_LIVE = {
    "fixture": {"id": FID, "date": "2026-09-06T19:30:00-03:00",
                "status": {"short": "2H", "elapsed": 80},
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


def _av_total(snap, prob, conf, mercado="gols",
              linha="Under 2.5 gols (total do jogo)", atual=1):
    return SimpleNamespace(
        jogo=f"{snap.home_team_name} x {snap.away_team_name}",
        fixture_id=snap.fixture_id,
        competicao="Serie A (Brazil)",
        minuto=snap.elapsed, status=snap.status,
        placar=f"{snap.goals_home}-{snap.goals_away}",
        mercado=mercado, linha=linha, prob=prob,
        sustentacao={"atual_no_jogo": atual, "tempo_restante_min": 43,
                     "taxa_combinada_por90": 2.0,
                     "modelo": "Poisson sobre o tempo restante (CALCULO)"},
        confianca=conf, conf_componentes={}, riscos=[],
        odd=None, odds_live_existentes=False, aprovada=False,
        rejeicao=None, auditoria=None,
    )


# ----------------------------------------------------------------------
# 1. Familia avaliada sem exigir odd; linhas canonicas liquidadaveis
# ----------------------------------------------------------------------
def test_cartoes_live_sem_odds_com_linhas_liquidadaveis():
    """Core da integracao: SEM odds na fonte a familia e avaliada
    (o gate antigo 'somente com linha real ao vivo' e removido)."""
    cand = _cand(_snap(elapsed=47, yc_h=2, yc_a=2))
    _avaliar_cartoes_live(cand)

    aves = [av for av in cand.avaliacoes if av.mercado == "cartoes"]
    assert aves, "cartoes completos na fonte devem ser avaliados"
    linhas = [av.linha for av in aves]
    assert len(linhas) == len(set(linhas))  # sem duplicacao de linha

    for av in aves:
        # linha canonica: liquidadavel pelo settlement validado
        parsed = _parse_linha(av.linha)
        assert parsed is not None and parsed[2] == "cartoes", av.linha
        assert _MARCA_CONVENCAO_CARTOES in av.linha
        # sem odd de cartoes no feed => OPORTUNIDADE ESTATISTICA AO VIVO
        assert av.odd is None
        assert av.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"
        # snapshot do estado no momento da avaliacao
        assert av.minuto == 47 and av.placar == "1-1"
        assert av.sustentacao["atual_no_jogo"] == 4
        assert av.sustentacao["minuto"] == 47
        assert av.sustentacao["tempo_restante_min"] == 43
        # convencao e arbitro declarados como risco (nunca inventados)
        assert any("amarelo=1, vermelho=2" in r for r in av.riscos)
        assert any("arbitro" in r for r in av.riscos)


def test_odd_do_feed_de_cartoes_nunca_e_anexada_a_linha():
    """O feed pode ter linha de bookings, mas a convencao de contagem da
    casa e desconhecida: anexar a odd a uma linha calculada em pontos
    amarelo=1/vermelho=2 enganaria - a linha permanece ESTATISTICA."""
    odds = _parse_response(FID, {
        "update": datetime.now(timezone.utc).isoformat(),
        "odds": [
            {"name": "Bookings Points",
             "values": [{"value": "Over 5.5", "odd": "1.90"},
                        {"value": "Under 5.5", "odd": "1.95"}]},
        ],
    })
    cand = _cand(_snap(elapsed=47, yc_h=2, yc_a=2), odds=odds)
    _avaliar_cartoes_live(cand)
    _anexar_odd_real(cand.avaliacoes, cand)

    aves = [av for av in cand.avaliacoes if av.mercado == "cartoes"]
    assert aves
    for av in aves:
        assert av.odd is None  # mercado de bookings NUNCA mapeado
        assert av.classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"
        assert av.odds_live_existentes  # a fonte TEM odds live (de outras)


# ----------------------------------------------------------------------
# 2. Vermelhos contam 2 pontos; expulsao reduz a confianca
# ----------------------------------------------------------------------
def test_vermelhos_contam_2_pontos_e_expulsao_penaliza():
    # amarelos 2+1 = 3 pontos; 1 vermelho = +2 => total 5
    cand = _cand(_snap(yc_h=2, yc_a=1, rc_h=1, rc_a=0))
    _avaliar_cartoes_live(cand)
    assert cand.avaliacoes
    assert all(av.sustentacao["atual_no_jogo"] == 5 for av in cand.avaliacoes)
    # evento estrutural (expulsao) reduz a confianca declaradamente
    assert all(
        av.conf_componentes["sem_expulsao_estrutural"] == 0.0
        for av in cand.avaliacoes
    )

    # controle: mesmos amarelos sem vermelho => 3 pontos, sem penalidade
    cand2 = _cand(_snap(yc_h=2, yc_a=1))
    _avaliar_cartoes_live(cand2)
    assert all(av.sustentacao["atual_no_jogo"] == 3 for av in cand2.avaliacoes)
    assert all(
        av.conf_componentes["sem_expulsao_estrutural"] == 1.0
        for av in cand2.avaliacoes
    )


# ----------------------------------------------------------------------
# 3. Estado do jogo desloca as probabilidades (pre-jogo nunca e live)
# ----------------------------------------------------------------------
def test_estado_do_jogo_desloca_probabilidades():
    hist = _hist()
    # pre-jogo (bloco validado) sobre o MESMO historico/benchmark
    pre = {a.linha: a.prob
           for a in avaliar_cartoes_prejogo(hist, _BENCH_CARTOES)}
    alvo = "Over 5.5 cartoes (amarelo=1, vermelho=2) (total do jogo)"
    assert alvo in pre

    # live aos 47' com 2 cartoes: projecao deslocada pelo estado
    _avaliar_cartoes_live(
        (c1 := _cand(_snap(elapsed=47, yc_h=1, yc_a=1)))
    )
    l1 = {av.linha: av.prob
          for av in c1.avaliacoes if av.mercado == "cartoes"}
    # live aos 47' com 4 cartoes: MESMA linha, probabilidade MAIOR
    _avaliar_cartoes_live(
        (c2 := _cand(_snap(elapsed=47, yc_h=2, yc_a=2)))
    )
    l2 = {av.linha: av.prob
          for av in c2.avaliacoes if av.mercado == "cartoes"}

    assert alvo in l1 and alvo in l2
    assert l2[alvo] > l1[alvo] + 1e-3   # cartoes ja ocorridos deslocam
    # e nenhuma das duas e a probabilidade pre-jogo reciclada
    assert abs(l1[alvo] - pre[alvo]) > 1e-3
    assert abs(l2[alvo] - pre[alvo]) > 1e-3


# ----------------------------------------------------------------------
# 4/5. Dado ausente nunca vira zero; sem sustentacao nao avalia
# ----------------------------------------------------------------------
def test_cartoes_incompletos_na_fonte_adiam_a_familia():
    # vermelho ausente de um lado: familia ADIADA com aviso (nunca zero)
    snap = _snap(yc_h=2, yc_a=2)
    del snap.stats_away["Red Cards"]
    cand = _cand(snap)
    _avaliar_cartoes_live(cand)
    assert not [av for av in cand.avaliacoes if av.mercado == "cartoes"]
    assert any("cartoes ao vivo incompletos" in a for a in cand.avisos)

    # amarelo ausente do outro lado: mesma disciplina
    snap2 = _snap(yc_h=2, yc_a=2)
    del snap2.stats_home["Yellow Cards"]
    cand2 = _cand(snap2)
    _avaliar_cartoes_live(cand2)
    assert not [av for av in cand2.avaliacoes if av.mercado == "cartoes"]
    assert any("cartoes ao vivo incompletos" in a for a in cand2.avisos)


def test_sem_sustentacao_nao_avalia_nada():
    # historico SEM dado de cartoes E sem benchmark => [] (nunca inventa)
    hist = _hist()
    for g in hist["games_home"] + hist["games_away"]:
        g.yellow_for = None
    cand = _cand(_snap(), bench=None)
    cand.historico = hist
    _avaliar_cartoes_live(cand)
    assert cand.avaliacoes == []

    # sem benchmark: avalia com baseline dos times e risco declarado
    cand2 = _cand(_snap(elapsed=47, yc_h=2, yc_a=2), bench=None)
    _avaliar_cartoes_live(cand2)
    aves = [av for av in cand2.avaliacoes if av.mercado == "cartoes"]
    assert aves
    assert all(
        any("benchmark de cartoes da liga indisponivel" in r
            for r in av.riscos)
        for av in aves
    )
    assert all(
        av.conf_componentes["benchmark_competicao"] == 0.0 for av in aves
    )


# ----------------------------------------------------------------------
# 6. Benchmark real de CARTOES no blend 65/35 e na confianca
# ----------------------------------------------------------------------
def test_benchmark_de_cartoes_no_blend_e_na_confianca():
    cand = _cand(_snap(elapsed=47, yc_h=2, yc_a=2))
    _avaliar_cartoes_live(cand)
    av = next(a for a in cand.avaliacoes if a.mercado == "cartoes")
    # o SEU benchmark (5.2 em pontos de cartoes), nos MESMOS pesos 65/35
    assert "benchmark da liga 5.2" in av.sustentacao["baseline"]
    assert "(65% times + 35% liga)" in av.sustentacao["baseline"]
    assert av.conf_componentes["benchmark_competicao"] == 1.0  # 40 validas

    # benchmark com poucas partidas validas: componente intermediaria
    bench_peq = dict(_BENCH_CARTOES, partidas_validas=10)
    cand2 = _cand(_snap(elapsed=47, yc_h=2, yc_a=2), bench=bench_peq)
    _avaliar_cartoes_live(cand2)
    av2 = next(a for a in cand2.avaliacoes if a.mercado == "cartoes")
    assert av2.conf_componentes["benchmark_competicao"] == 0.6


# ----------------------------------------------------------------------
# 7. Faltas da fonte: contexto factual na sustentacao
# ----------------------------------------------------------------------
def test_faltas_da_fonte_vao_na_sustentacao_quando_existem():
    cand = _cand(_snap(elapsed=47, fouls_h=10, fouls_a=12))
    _avaliar_cartoes_live(cand)
    av = next(a for a in cand.avaliacoes if a.mercado == "cartoes")
    assert "faltas_na_fonte_ate_o_minuto" in av.sustentacao
    ctx = av.sustentacao["faltas_na_fonte_ate_o_minuto"]
    assert "mandante 10 x visitante 12" in ctx
    assert "total 22" in ctx and "minuto 47" in ctx
    assert "sem modelo faltas-cartoes" in ctx  # dado exibido, nao modelo

    # fonte SEM faltas: a chave NAO existe (nunca inventadas)
    cand2 = _cand(_snap(elapsed=47))
    _avaliar_cartoes_live(cand2)
    av2 = next(a for a in cand2.avaliacoes if a.mercado == "cartoes")
    assert "faltas_na_fonte_ate_o_minuto" not in av2.sustentacao


# ----------------------------------------------------------------------
# 8. Filtro --mercado controla a familia no deep dive
# ----------------------------------------------------------------------
def _deep_dive_patched(monkeypatch, snap, mercados):
    import src.live_opportunity as lo

    monkeypatch.setattr(lo, "_historico_teams", lambda client, sn: _hist())
    monkeypatch.setattr(lo, "_benchmark_escanteios",
                        lambda client, sn: None)
    monkeypatch.setattr(lo, "_benchmark_gols", lambda client, sn: None)
    monkeypatch.setattr(lo, "_h2h_peso_menor", lambda client, sn: ({}, 0))
    monkeypatch.setattr("src.odds.fetch_live_odds",
                        lambda client, fid, refresh=True: None)
    chamadas = []
    monkeypatch.setattr(
        "src.cartoes.league_cards_average_for_fixture",
        lambda client, fx: (chamadas.append(fx.fixture_id)
                            or dict(_BENCH_CARTOES)),
    )
    cand = lo.deep_dive(None, snap, mercados=mercados)
    return cand, chamadas


def test_filtro_de_mercado_controla_a_familia(monkeypatch):
    snap = _snap(elapsed=47, yc_h=2, yc_a=2)

    # varredura SOMENTE de cartoes: benchmark computado, familia avaliada,
    # escanteios/gols fora
    cand, chamadas = _deep_dive_patched(monkeypatch, snap, ("cartoes",))
    assert chamadas == [FID]
    assert [av for av in cand.avaliacoes if av.mercado == "cartoes"]
    assert not [av for av in cand.avaliacoes if av.mercado == "escanteios"]

    # varredura SEM cartoes: benchmark NEM computado, familia fora
    cand2, chamadas2 = _deep_dive_patched(monkeypatch, snap,
                                          ("escanteios",))
    assert chamadas2 == []
    assert cand2.benchmark_cartoes is None
    assert not [av for av in cand2.avaliacoes if av.mercado == "cartoes"]
    assert [av for av in cand2.avaliacoes if av.mercado == "escanteios"]


# ----------------------------------------------------------------------
# 9. Comparador live: sem preferencia artificial
# ----------------------------------------------------------------------
# cenario: 2H aos 80' com 5 pontos de cartoes => Under 6.5 na janela
def _cand_aprovavel():
    return _cand(_snap(elapsed=80, yc_h=3, yc_a=2))


def _melhor_cartoes(cand):
    """A melhor linha de cartoes DENTRO da janela de aprovacao (a linha
    ja decidida tem prob 100% e e rejeitada pela propria janela)."""
    na_janela = [
        av for av in cand.avaliacoes
        if av.mercado == "cartoes" and 0.70 <= av.prob <= 0.97
    ]
    return max(na_janela, key=lambda a: a.confianca + a.prob)


def test_cartoes_podem_vencer_o_comparador_live(monkeypatch, tmp_path):
    """Demonstracao controlada: linha de cartoes SEM odd ao vivo real
    vence um concorrente de gols com score inferior (antes era pulada
    por exigir odds)."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    cand = _cand_aprovavel()
    _avaliar_cartoes_live(cand)
    alvo = _melhor_cartoes(cand)
    assert 0.70 <= alvo.prob <= 0.97  # cenario dentro da janela

    fraca = _av_total(cand.snapshot, prob=0.71, conf=0.62)
    cand.avaliacoes.append(fraca)

    aprovadas = _aprovar(_AuditClient(), [cand])

    assert aprovadas, "alguma linha de cartoes deve ser aprovada"
    assert aprovadas[0].mercado == "cartoes"
    assert aprovadas[0].linha == alvo.linha
    assert aprovadas[0].odd is None
    assert aprovadas[0].classificacao == "OPORTUNIDADE ESTATISTICA AO VIVO"
    assert aprovadas[0].auditoria  # auditoria integral rodou
    # a linha aprovada e liquidadavel pelo settlement validado
    assert _parse_linha(aprovadas[0].linha) is not None


def test_gols_vencem_quando_sao_melhores(monkeypatch, tmp_path):
    # mesma probabilidade window: concorrente de gols com score SUPERIOR
    # vence - a familia nova NAO e favorecida automaticamente
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    cand = _cand_aprovavel()
    _avaliar_cartoes_live(cand)
    forte = _av_total(cand.snapshot, prob=0.94, conf=0.95)
    cand.avaliacoes.append(forte)

    aprovadas = _aprovar(_AuditClient(), [cand])

    assert aprovadas
    assert aprovadas[0].mercado == "gols"
    assert aprovadas[0].linha == forte.linha
    # nenhuma linha de cartoes foi aprovada: perdeu em igualdade, sem
    # favorecimento da familia nova
    assert not [
        av for av in cand.avaliacoes
        if av.mercado == "cartoes" and av.aprovada
    ]


# ----------------------------------------------------------------------
# 10. Registro imutavel: congela o snapshot live antes do resultado
# ----------------------------------------------------------------------
def test_registro_congela_cartoes_live_sem_duplicacao(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))

    snap = _snap(elapsed=80, yc_h=3, yc_a=2)
    cand = _cand(snap)
    _avaliar_cartoes_live(cand)
    alvo = _melhor_cartoes(cand)

    rec_id, novo = reg.registrar_de_avaliacao(alvo, cand)
    assert novo is True
    rec = reg.obter(rec_id)
    # snapshot congelado: mercado, linha liquidadavel, minuto, placar
    assert rec["mercado"] == "cartoes"
    assert _parse_linha(rec["linha"]) is not None
    assert _MARCA_CONVENCAO_CARTOES in rec["linha"]
    assert rec["minuto"] == 80 and rec["placar"] == "1-1"
    assert rec["probabilidade"] == pytest.approx(alvo.prob)
    assert rec["confianca"] == pytest.approx(alvo.confianca)
    assert rec["odd"] is None and rec["bookmaker"] is None
    assert rec["versao_analise"] == VERSAO_LIVE

    # reescaneio IDENTICO: dedupe, nunca segunda recomendacao
    rec_id2, novo2 = reg.registrar_de_avaliacao(alvo, cand)
    assert novo2 is False and rec_id2 == rec_id
    assert len(reg.listar(fixture_id=FID, tipo="live")) == 1

    # estado mudou (2-1 aos 85'): observacao NOVA, nao duplicata
    snap2 = _snap(elapsed=85, gh=2, ga=1, yc_h=4, yc_a=2)
    cand2 = _cand(snap2)
    _avaliar_cartoes_live(cand2)
    alvo2 = _melhor_cartoes(cand2)
    rec_id3, novo3 = reg.registrar_de_avaliacao(alvo2, cand2)
    assert novo3 is True and rec_id3 != rec_id
    assert len(reg.listar(fixture_id=FID, tipo="live")) == 2

    # o registro original permanece IMUTAVEL (snapshot de 80' intacto)
    original = reg.obter(rec_id)
    assert original["minuto"] == 80 and original["placar"] == "1-1"
    with sqlite3.connect(str(tmp_path / "reg.db")) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE recomendacoes SET probabilidade = 0.99 "
                "WHERE id = ?",
                (rec_id,),
            )