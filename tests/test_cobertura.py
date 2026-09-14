"""MATRIZ DE COBERTURA POR COMPETICAO x MERCADO x MODO (13 casos exigidos).

Camada de ELEGIBILIDADE construida a partir da auditoria da API-Football
de 08/09/2026. Validacoes obrigatorias da especificacao:

  1.  Serie D valida para GOALS PRE se o historico de placar for
      suficiente;
  2.  Serie D bloqueada em CORNERS PRE (sem Corner Kicks historicos);
  3.  Serie D bloqueada em CORNERS LIVE;
  4.  Serie D bloqueada em CARDS LIVE;
  5.  UCL (classe A) passa para analise quando o fixture possui dados;
  6.  competicao A/B com statistics live sem Corner Kicks nao gera
      aposta de corners (o jogo segue para gols);
  7.  competicao com historico de gols mas sem corners permite gols e
      bloqueia corners;
  8.  ausencia de dado NUNCA vira zero (REGRA ABSOLUTA);
  9.  classe C gera somente observacao (nunca recomendacao automatica);
  10. classes D/E nunca geram recomendacao;
  11. fluxo pre-jogo mantem as probabilidades atuais (formulas intactas);
  12. fluxo live mantem as probabilidades atuais (formulas intactas);
  13. o ledger (registro de recomendacoes) permanece inalterado.

Reusa a infraestrutura de cliente falso de test_live_opportunity.py
(mesmo formato real da API) para os casos de integracao.
"""

import inspect

import src.cobertura as cobertura_mod
from src.cobertura import (
    MERCADO_CARDS,
    MERCADO_CORNERS,
    MERCADO_GOALS,
    MODOS,
    MODO_LIVE,
    MODO_PRE_GAME,
    STATUS_BLOQUEADO,
    STATUS_OBSERVACAO,
    STATUS_PERMITIDO,
    TODAS_FAMILIAS,
    VeredictoCobertura,
    avaliar_cobertura_live,
    avaliar_cobertura_pre,
    classe_estrutural,
    familias_para_deep_dive,
    filtrar_avaliacoes_por_cobertura,
    formatar_relatorio_cobertura,
    resumo_matriz,
    status_da_familia,
    veredictos_por_mercado,
)
from src.live import LiveSnapshot, fetch_live_snapshot, now_brt
from src.live_opportunity import (
    Avaliacao,
    deep_dive,
    scan_live_opportunities,
    separar_aprovadas_por_cobertura,
)
from src.match_stats import TeamGameStats
from src.prejogo_opportunity import aprovar_pregame, avaliar_pregame

# infraestrutura de cliente falso do live (mesmo formato da API)
from test_live_opportunity import (
    FID,
    FakeLiveClient,
    _game,
    _routes_mundo,
)

HOME_ID, HOME = 131, "Corinthians"
AWAY_ID, AWAY = 132, "Chapecoense-sc"


# ----------------------------------------------------------------------
# Builders (mesmo formato real das estruturas de producao)
# ----------------------------------------------------------------------
def _hist_game(total=None, gols_for=1, gols_against=1, fid=900,
               yellow=None, red=None, date="2026-08-20T16:00:00-03:00"):
    """Jogo de historico real (TeamGameStats). None = dado AUSENTE."""
    return TeamGameStats(
        fixture_id=fid, date=date, league="Liga", round="1",
        status="FT", opponent="Adversario FC", played_at_home=True,
        corners_for=(total // 2 if total is not None else None),
        corners_against=(total - total // 2 if total is not None else None),
        corners_total=total,
        goals_for=gols_for, goals_against=gols_against,
        shots_for=None, shots_against=None,
        shots_on_goal_for=None, shots_on_goal_against=None,
        possession_for=None,
        yellow_for=yellow, red_for=red,
        yellow_against=yellow, red_against=red,
    )


def _hist(games_home, games_away, sem_home=0, sem_away=0):
    return {
        "games_home": games_home, "games_away": games_away,
        "n_home": len(games_home), "n_away": len(games_away),
        "sem_estatisticas_home": sem_home,
        "sem_estatisticas_away": sem_away,
    }


def _snap(league_id=76, league_name="Serie D", status="2H", elapsed=30,
          gh=1, ga=0, corner_h=None, corner_a=None,
          yellow_h=None, yellow_a=None, red_h=None, red_a=None,
          stats=True, events=None, fid=400):
    """Snapshot live real (LiveSnapshot); None = dado AUSENTE."""
    stats_home: dict = {}
    stats_away: dict = {}
    if stats:
        if corner_h is not None:
            stats_home["Corner Kicks"] = corner_h
        if yellow_h is not None:
            stats_home["Yellow Cards"] = yellow_h
        if red_h is not None:
            stats_home["Red Cards"] = red_h
        stats_home["Total Shots"] = 5
        if corner_a is not None:
            stats_away["Corner Kicks"] = corner_a
        if yellow_a is not None:
            stats_away["Yellow Cards"] = yellow_a
        if red_a is not None:
            stats_away["Red Cards"] = red_a
        stats_away["Total Shots"] = 3
    return LiveSnapshot(
        fixture_id=fid, league_name=league_name, country="Brazil",
        season=2026, round="1", date_local="2026-09-08T16:00:00-03:00",
        home_team_id=HOME_ID, home_team_name=HOME,
        away_team_id=AWAY_ID, away_team_name=AWAY,
        goals_home=gh, goals_away=ga, halftime_home=0, halftime_away=0,
        status=status, elapsed=elapsed, league_id=league_id,
        stats_home=stats_home, stats_away=stats_away,
        stats_1h={}, stats_2h={},
        events=events if events is not None else [],
        collected_at=now_brt().strftime("%d/%m/%Y %H:%M:%S"),
        has_stats=bool(stats_home or stats_away),
    )


def _status(veredictos, mercado):
    return veredictos_por_mercado(veredictos)[mercado].status


# ----------------------------------------------------------------------
# 1. Serie D: GOALS PRE valido com historico de placar suficiente
# ----------------------------------------------------------------------
def test_serie_d_gols_pre_permitido_com_historico_de_placar():
    """Serie D (classe E em ESTATISTICAS) tem placares completos: GOALS
    PRE e classe B (PERMITIDO) quando o historico de placar sustenta a
    analise - nunca regra generica 'Serie D bloqueada para tudo'."""
    hist = _hist([_hist_game(gols_for=2, gols_against=0, fid=901),
                  _hist_game(gols_for=1, gols_against=1, fid=902)],
                 [_hist_game(gols_for=0, gols_against=1, fid=911),
                  _hist_game(gols_for=3, gols_against=1, fid=912)])
    veredictos = avaliar_cobertura_pre(76, "Serie D", hist)
    goals = veredictos_por_mercado(veredictos)[MERCADO_GOALS]

    assert goals.status == STATUS_PERMITIDO
    assert goals.classe == "B"
    assert "placares completos" in goals.motivo
    assert goals.detalhe["jogos_com_dados_do_mercado"] == 4


# ----------------------------------------------------------------------
# 2. Serie D: CORNERS PRE bloqueado (sem Corner Kicks historicos)
# ----------------------------------------------------------------------
def test_serie_d_corners_pre_bloqueado_sem_corner_kicks():
    """Sem Corner Kicks no historico, a ausencia NUNCA vira zero: Serie D
    fica BLOQUEADA em escanteios pre-jogo (classe E da auditoria)."""
    hist = _hist([_hist_game(gols_for=1, gols_against=1, fid=901)],
                 [_hist_game(gols_for=1, gols_against=0, fid=911)],
                 sem_home=9, sem_away=8)
    veredictos = avaliar_cobertura_pre(76, "Serie D", hist)
    corners = veredictos_por_mercado(veredictos)[MERCADO_CORNERS]

    assert corners.status == STATUS_BLOQUEADO
    assert corners.classe == "E"
    assert "auditoria" in corners.motivo


# ----------------------------------------------------------------------
# 3. Serie D: CORNERS LIVE bloqueado
# ----------------------------------------------------------------------
def test_serie_d_corners_live_bloqueado():
    """Sem estatisticas de partida na fonte, escanteios live sao
    bloqueados MESMO quando o jogo tem placar ao vivo."""
    snap = _snap()  # Serie D live com placar mas SEM Corner Kicks
    veredictos = avaliar_cobertura_live(snap)
    corners = veredictos_por_mercado(veredictos)[MERCADO_CORNERS]

    assert corners.status == STATUS_BLOQUEADO
    assert corners.classe == "E"
    # bloqueio ESTRUTURAL: mesmo se a fonte entregasse corners agora, a
    # classe E desta liga em escanteios live nao e superada por dado
    com_dados = avaliar_cobertura_live(
        _snap(corner_h=5, corner_a=3, yellow_h=1, yellow_a=2, red_h=0,
              red_a=0))
    assert _status(com_dados, MERCADO_CORNERS) == STATUS_BLOQUEADO


# ----------------------------------------------------------------------
# 4. Serie D: CARDS LIVE bloqueado
# ----------------------------------------------------------------------
def test_serie_d_cards_live_bloqueado():
    snap = _snap()
    veredictos = avaliar_cobertura_live(snap)
    cards = veredictos_por_mercado(veredictos)[MERCADO_CARDS]

    assert cards.status == STATUS_BLOQUEADO
    assert cards.classe == "E"


# ----------------------------------------------------------------------
# 5. UCL (classe A) passa para analise quando o fixture tem dados
# ----------------------------------------------------------------------
def test_ucl_classe_a_passa_para_analise_com_dados_do_fixture():
    """Liga classe A nao e aprovada POR CATEGORIA: exige os dados do
    fixture. Com dados reais presentes => PERMITIDO em todos os mercados."""
    snap = _snap(league_id=2, league_name="UEFA Champions League",
                 corner_h=6, corner_a=4, yellow_h=1, yellow_a=2,
                 red_h=0, red_a=0, gh=1, ga=1)
    veredictos = avaliar_cobertura_live(snap)
    for mercado in (MERCADO_GOALS, MERCADO_CORNERS, MERCADO_CARDS):
        assert _status(veredictos, mercado) == STATUS_PERMITIDO
    # pre-jogo: historico com corners/placar/cartoes completos
    hist = _hist(
        [_hist_game(total=10, yellow=2, red=0, fid=901),
         _hist_game(total=8, yellow=3, red=0, fid=902)],
        [_hist_game(total=12, yellow=1, red=1, fid=911),
         _hist_game(total=9, yellow=2, red=0, fid=912)],
    )
    veredictos_pre = avaliar_cobertura_pre(
        2, "UEFA Champions League", hist)
    for mercado in (MERCADO_GOALS, MERCADO_CORNERS, MERCADO_CARDS):
        v = veredictos_por_mercado(veredictos_pre)[mercado]
        assert v.status == STATUS_PERMITIDO
        assert v.classe == "A"


def test_liga_a_sem_dados_do_fixture_e_bloqueada():
    """Liga classe A com fixture SEM o dado do mercado: classe estrutural
    nao basta - veredicto cai para BLOQUEADO (DADO INSUFICIENTE)."""
    # live: sem Corner Kicks para os dois lados nesta leitura
    veredictos = avaliar_cobertura_live(
        _snap(league_id=71, league_name="Serie A"))
    corners = veredictos_por_mercado(veredictos)[MERCADO_CORNERS]
    assert corners.status == STATUS_BLOQUEADO
    assert "DADO INSUFICIENTE" in corners.motivo
    # pre: historico sem nenhum jogo com cartoes completos
    hist = _hist([_hist_game(total=10, yellow=None, red=None, fid=901)],
                 [_hist_game(total=8, yellow=None, red=None, fid=911)])
    veredictos_pre = avaliar_cobertura_pre(71, "Serie A", hist)
    cards = veredictos_por_mercado(veredictos_pre)[MERCADO_CARDS]
    assert cards.status == STATUS_BLOQUEADO
    assert "DADO INSUFICIENTE" in cards.motivo


# ----------------------------------------------------------------------
# 6. Liga A com statistics live sem corners: nao gera aposta de corners
# ----------------------------------------------------------------------
def test_liga_a_sem_corners_live_nao_gera_aposta_de_corners(
    monkeypatch, tmp_path,
):
    """Liga A com statistics live SEM Corner Kicks (fixture 202 do mundo
    falso: finalizacoes/posse sem escanteios): o jogo NAO e descartado
    inteiro - segue para analise de GOLS - mas nenhuma linha de
    escanteios e avaliada ou aprovada."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    client = FakeLiveClient(
        _routes_mundo(live_list=[_game(202, "2H", 30, 0, 0)]))

    v = scan_live_opportunities(client)

    assert len(v.sondados) == 1  # o jogo NAO foi descartado inteiro
    assert len(v.candidatos) == 1
    cand = v.candidatos[0]
    mercados = {av.mercado for av in cand.avaliacoes}
    assert "escanteios" not in mercados       # corners bloqueado
    assert "gols" in mercados                 # gols segue com dados reais
    assert all(av.mercado != "escanteios" for av in v.aprovadas)
    # veredicto registrado no candidato
    corners = veredictos_por_mercado(cand.cobertura)[MERCADO_CORNERS]
    assert corners.status == STATUS_BLOQUEADO
    assert corners.detalhe["corner_kicks_casa"] is None  # nunca zero


# ----------------------------------------------------------------------
# 7. Historico com gols mas sem corners: gols permitido, corners bloqueado
# ----------------------------------------------------------------------
def test_historico_com_gols_sem_corners_permite_gols_bloqueia_corners():
    """A combinacao e decidida POR MERCADO: o mesmo fixture pode ter
    GOALS liberado e CORNERS bloqueado (nunca tudo-ou-nada)."""
    hist = _hist(
        # jogos COM placar mas SEM Corner Kicks na fonte
        [_hist_game(total=None, gols_for=1, gols_against=1, fid=901),
         _hist_game(total=None, gols_for=2, gols_against=0, fid=902)],
        [_hist_game(total=None, gols_for=0, gols_against=1, fid=911)],
        sem_home=2, sem_away=1,
    )
    # liga classe A: a ausencia e do FIXTURE, nao da liga
    veredictos = avaliar_cobertura_pre(39, "Premier League", hist)

    goals = veredictos_por_mercado(veredictos)[MERCADO_GOALS]
    corners = veredictos_por_mercado(veredictos)[MERCADO_CORNERS]
    assert goals.status == STATUS_PERMITIDO
    assert corners.status == STATUS_BLOQUEADO
    assert "DADO INSUFICIENTE" in corners.motivo
    # as linhas de escanteios deste fixture saem do fluxo pre-jogo
    avaliacoes = avaliar_pregame(
        hist, None, {"describe": {"media": 2.4}}, 0)
    mantidas, bloqueadas = filtrar_avaliacoes_por_cobertura(
        avaliacoes, veredictos)
    assert {av.mercado for av in mantidas} == {"gols"}
    assert all(av.mercado == "escanteios" for av, _v in bloqueadas)


# ----------------------------------------------------------------------
# 8. REGRA ABSOLUTA: dado ausente nunca vira zero
# ----------------------------------------------------------------------
def test_dado_ausente_nunca_vira_zero():
    # live: Corner Kicks ausentes permanecem None no detalhe (nunca 0)
    veredictos = avaliar_cobertura_live(
        _snap(league_id=71, league_name="Serie A"))
    corners = veredictos_por_mercado(veredictos)[MERCADO_CORNERS]
    assert corners.detalhe["corner_kicks_casa"] is None
    assert corners.detalhe["corner_kicks_fora"] is None
    assert corners.status == STATUS_BLOQUEADO
    cards = veredictos_por_mercado(veredictos)[MERCADO_CARDS]
    assert cards.detalhe["amarelos_casa"] is None
    assert cards.detalhe["vermelhos_casa"] is None
    # pre: nenhum jogo com o dado => bloqueio com contagem explicita,
    # nao com media zero inventada
    hist = _hist([], [], sem_home=10, sem_away=10)
    veredictos_pre = avaliar_cobertura_pre(71, "Serie A", hist)
    goals = veredictos_por_mercado(veredictos_pre)[MERCADO_GOALS]
    assert goals.status == STATUS_BLOQUEADO
    assert "0 de 0" in goals.motivo
    assert goals.detalhe["jogos_com_dados_do_mercado"] == 0
    assert goals.detalhe["jogos_sem_estatisticas_na_fonte"] == 20
    # liga desconhecida com amostra VAZIA: classe C, mas sem NENHUM jogo
    # com o dado do mercado => BLOQUEADO por DADO INSUFICIENTE (a
    # ausencia de evidencia nao inventa cobertura nem media zero)
    desconhecida = avaliar_cobertura_pre(999999, "Liga Nova", hist)
    for v in desconhecida:
        assert v.classe == "C"
        assert v.status == STATUS_BLOQUEADO
        assert "DADO INSUFICIENTE" in v.motivo
    # liga desconhecida com dados presentes na amostra: classe C em
    # OBSERVACAO - nunca PERMITIDO (cobertura nunca e inventada)
    hist_cheio = _hist(
        [_hist_game(total=10, gols_for=1, gols_against=1, yellow=2,
                    red=0, fid=901)],
        [_hist_game(total=9, gols_for=2, gols_against=0, yellow=1,
                    red=0, fid=911)],
    )
    desconhecida_com_dados = avaliar_cobertura_pre(
        999999, "Liga Nova", hist_cheio)
    for v in desconhecida_com_dados:
        assert v.classe == "C"
        assert v.status == STATUS_OBSERVACAO


# ----------------------------------------------------------------------
# 9. Classe C gera somente observacao (nunca recomendacao automatica)
# ----------------------------------------------------------------------
def _av_stub(fid=400, mercado="escanteios"):
    return Avaliacao(
        jogo=f"{HOME} x {AWAY}", fixture_id=fid,
        competicao="Liga", minuto=40, status="2H", placar="1-0",
        mercado=mercado, linha="Over 9.5 escanteios (total do jogo)",
        prob=0.85,
        sustentacao={"atual_no_jogo": 8, "tempo_restante_min": 50,
                     "taxa_combinada_por90": 10.0,
                     "modelo": "Poisson sobre o tempo restante (CALCULO)"},
        confianca=0.80, conf_componentes={}, riscos=[], odd=None,
        odds_live_existentes=False,
    )


def test_classe_c_gera_somente_observacao():
    """Aprovada de mercado em OBSERVACAO (classe C) sai da lista de
    aprovadas e nunca vira recomendacao automatica."""
    cobertura = {400: [
        VeredictoCobertura(competicao="Liga", modo=MODO_LIVE,
                           mercado=MERCADO_GOALS, classe="A",
                           status=STATUS_PERMITIDO, motivo="teste"),
        VeredictoCobertura(competicao="Liga", modo=MODO_LIVE,
                           mercado=MERCADO_CORNERS, classe="C",
                           status=STATUS_OBSERVACAO, motivo="teste"),
        VeredictoCobertura(competicao="Liga", modo=MODO_LIVE,
                           mercado=MERCADO_CARDS, classe="E",
                           status=STATUS_BLOQUEADO, motivo="teste"),
    ]}
    aprovadas = [_av_stub(mercado="escanteios"),
                 _av_stub(fid=401, mercado="gols")]

    permitidas, observacao = separar_aprovadas_por_cobertura(
        aprovadas, cobertura)

    assert permitidas == [aprovadas[1]]
    assert observacao == [aprovadas[0]]
    assert aprovadas[0].aprovada is False
    assert "nunca recomendacao automatica" in aprovadas[0].rejeicao
    # veredicto classe C em si: status OBSERVACAO mesmo com dados
    snap_c = _snap(league_id=479, league_name="Canadian Premier League",
                   corner_h=4, corner_a=3, yellow_h=1, yellow_a=1,
                   red_h=0, red_a=0)
    veredictos = avaliar_cobertura_live(snap_c)
    assert _status(veredictos, MERCADO_CORNERS) == STATUS_OBSERVACAO


def test_prejogo_politica_somente_permitidas_concorrem():
    """No fluxo --politica so mercado PERMITIDO (classe A/B com dados
    confirmados) concorre a recomendacao; classe C (OBSERVACAO) de liga
    desconhecida nao e selecionavel."""
    hist = _hist(
        [_hist_game(total=10, yellow=2, red=0, fid=901),
         _hist_game(total=9, yellow=1, red=0, fid=902)],
        [_hist_game(total=11, yellow=2, red=0, fid=911),
         _hist_game(total=8, yellow=1, red=0, fid=912)],
    )
    bench = {"describe": {"media": 9.5}, "partidas_validas": 40}
    avaliacoes = avaliar_pregame(hist, bench, bench, 0)
    assert avaliacoes

    # liga DESCONHECIDA (classe C): nenhum mercado PERMITIDO
    veredictos_c = avaliar_cobertura_pre(999999, "Liga Nova", hist)
    permitidas = [
        av for av in avaliacoes
        if status_da_familia(veredictos_c, av.mercado) == STATUS_PERMITIDO
    ]
    assert permitidas == []
    # a mesma analise em liga classe A: todas as avaliacoes concorrem
    veredictos_a = avaliar_cobertura_pre(71, "Serie A", hist)
    permitidas = [
        av for av in avaliacoes
        if status_da_familia(veredictos_a, av.mercado) == STATUS_PERMITIDO
    ]
    assert permitidas == avaliacoes


# ----------------------------------------------------------------------
# 10. Classes D/E nunca geram recomendacao
# ----------------------------------------------------------------------
def test_classes_d_e_nunca_geram_recomendacao():
    """Mercado BLOQUEADO (classe D/E ou dado insuficiente) sai do fluxo
    antes de qualquer aprovacao - pre e live."""
    hist = _hist(
        [_hist_game(total=None, gols_for=1, gols_against=1, fid=901),
         _hist_game(total=None, gols_for=2, gols_against=0, fid=902)],
        [_hist_game(total=None, gols_for=0, gols_against=1, fid=911)],
    )
    veredictos = avaliar_cobertura_pre(76, "Serie D", hist)
    por = veredictos_por_mercado(veredictos)
    assert por[MERCADO_CORNERS].classe == "E"
    assert por[MERCADO_CARDS].classe == "D"
    assert por[MERCADO_CORNERS].bloqueado
    assert por[MERCADO_CARDS].bloqueado
    assert por[MERCADO_GOALS].status == STATUS_PERMITIDO

    # linhas de corners/cartoes de Serie D saem do fluxo inteiro
    avaliacoes = avaliar_pregame(
        hist, None, {"describe": {"media": 2.4}}, 0)
    mantidas, _bloqueadas = filtrar_avaliacoes_por_cobertura(
        avaliacoes, veredictos)
    assert {av.mercado for av in mantidas} <= {"gols"}
    for av in aprovar_pregame(mantidas):
        assert av.mercado != "escanteios"

    # live: escanteios e cartoes de Serie D nunca entram no deep dive
    familias = familias_para_deep_dive(
        avaliar_cobertura_live(_snap()))
    assert "escanteios" not in (familias or TODAS_FAMILIAS)
    assert "cartoes" not in (familias or TODAS_FAMILIAS)


# ----------------------------------------------------------------------
# 11. Fluxo pre-jogo mantem as probabilidades atuais
# ----------------------------------------------------------------------
def test_fluxo_prejogo_mantem_probabilidades():
    """A camada de cobertura apenas FILTRA: as avaliacoes mantidas sao
    os MESMOS objetos, com probabilidade/confianca identicas - nenhuma
    formula e recalculada."""
    hist = _hist(
        [_hist_game(total=10, yellow=2, red=0, fid=901),
         _hist_game(total=9, yellow=1, red=0, fid=902)],
        [_hist_game(total=11, yellow=2, red=0, fid=911),
         _hist_game(total=8, yellow=1, red=0, fid=912)],
    )
    bench = {"describe": {"media": 9.5}, "partidas_validas": 40}
    bench_gols = {"describe": {"media": 2.3}, "partidas_validas": 40}
    avaliacoes = avaliar_pregame(hist, bench, bench_gols, 4)

    veredictos = avaliar_cobertura_pre(71, "Serie A", hist)
    mantidas, bloqueadas = filtrar_avaliacoes_por_cobertura(
        avaliacoes, veredictos)

    # classe A com dados completos: nada e bloqueado, nada muda
    assert bloqueadas == []
    assert mantidas == avaliacoes
    assert [(av.linha, av.prob, av.confianca) for av in mantidas] == \
        [(av.linha, av.prob, av.confianca) for av in avaliacoes]


# ----------------------------------------------------------------------
# 12. Fluxo live mantem as probabilidades atuais
# ----------------------------------------------------------------------
def test_fluxo_live_mantem_probabilidades(monkeypatch, tmp_path):
    """Jogo da Serie A com cobertura COMPLETA (todas as familias
    liberadas): as avaliacoes da varredura sao IDENTICAS as do motor
    direto (familias_para_deep_dive devolve None - nada restringe - e o
    deep dive e chamado com mercados=None, como sempre foi)."""
    monkeypatch.setattr("src.identity.DB_PATH", str(tmp_path / "id.db"))
    client = FakeLiveClient(_routes_mundo(live_list=[_game()]))  # FID 200

    v = scan_live_opportunities(client)
    assert len(v.candidatos) == 1
    cand = v.candidatos[0]
    assert cand.snapshot.fixture_id == FID
    # cobertura plena: a matriz NADA restringe neste fixture
    assert familias_para_deep_dive(cand.cobertura) is None

    # motor direto, sem a matriz no caminho: mesma chamada do deep dive
    snap = fetch_live_snapshot(client, FID, refresh=True)
    direto = deep_dive(client, snap, mercados=None).avaliacoes
    assert direto
    assert [(av.linha, av.prob, av.confianca, av.mercado)
            for av in cand.avaliacoes] == \
        [(av.linha, av.prob, av.confianca, av.mercado)
         for av in direto]

    # com filtro do operador, a matriz so INTERSECTA: escanteios seguem
    # avaliados com as MESMAS linhas/probabilidades do modo completo
    v_esc = scan_live_opportunities(client, mercados=("escanteios",))
    esc_filtrado = [av for c in v_esc.candidatos for av in c.avaliacoes]
    esc_completo = [av for av in direto if av.mercado == "escanteios"]
    assert [(av.linha, av.prob) for av in esc_filtrado] == \
        [(av.linha, av.prob) for av in esc_completo]


# ----------------------------------------------------------------------
# 13. O ledger (registro de recomendacoes) permanece inalterado
# ----------------------------------------------------------------------
def test_ledger_permanece_inalterado():
    """A camada de cobertura NAO importa nem escreve no registro: o
    codigo do modulo nao referencia o ledger; e como mercados bloqueados
    sao filtrados ANTES da aprovacao, nada bloqueado chega ao registro
    (o scan pre-jogo so registra aprovadas - que nunca incluem mercado
    bloqueado)."""
    fonte = inspect.getsource(cobertura_mod)
    assert "registry" not in fonte
    assert "RegistroRecomendacoes" not in fonte

    hist = _hist(
        [_hist_game(total=None, gols_for=1, gols_against=1, fid=901)],
        [_hist_game(total=None, gols_for=1, gols_against=0, fid=911)],
    )
    veredictos = avaliar_cobertura_pre(76, "Serie D", hist)
    assert veredictos_por_mercado(veredictos)[MERCADO_CORNERS].bloqueado
    assert veredictos_por_mercado(veredictos)[MERCADO_CARDS].bloqueado

    avaliacoes = avaliar_pregame(hist, None, {"describe": {"media": 2.4}},
                                 0)
    mantidas, _bloqueadas = filtrar_avaliacoes_por_cobertura(
        avaliacoes, veredictos)
    assert all(av.mercado != "escanteios" for av in mantidas)
    for av in aprovar_pregame(mantidas):
        assert av.mercado != "escanteios"  # nada bloqueado e aprovado


# ----------------------------------------------------------------------
# Estrutura da matriz e relatorio de elegibilidade (ETAPAS 1 e 8)
# ----------------------------------------------------------------------
def test_mls_classe_a_pos_auditoria_09_09_2026():
    # MLS (253): promovida da classe C padrao para classe A em
    # 09/09/2026 (evidencia fresca: 341/343 partidas com estatisticas
    # na temporada 2026 = 99,4% de entrega)
    for mercado in (MERCADO_GOALS, MERCADO_CORNERS, MERCADO_CARDS):
        for modo in MODOS:
            classe, _ = classe_estrutural(
                253, "Major League Soccer", mercado, modo)
            assert classe == "A"


def test_matriz_estrutura_e_relatorio():
    """A matriz cobre TODAS as combinacoes modo x mercado por competicao
    e o relatorio usa o formato exigido COMPETICAO | MODO | MERCADO |
    CLASSE | STATUS | MOTIVO. Serie D NUNCA e uma regra unica."""
    resumo = resumo_matriz()
    assert resumo["ligas_na_auditoria"] > 30
    # Serie D: cada (modo, mercado) tem a classe que a evidencia sustenta
    classes_sd = {
        (modo, mercado): classe_estrutural(
            76, "Serie D", mercado, modo)[0]
        for modo in MODOS
        for mercado in (MERCADO_GOALS, MERCADO_CORNERS, MERCADO_CARDS)
    }
    assert classes_sd[(MODO_PRE_GAME, MERCADO_GOALS)] == "B"
    assert classes_sd[(MODO_PRE_GAME, MERCADO_CORNERS)] == "E"
    assert classes_sd[(MODO_PRE_GAME, MERCADO_CARDS)] == "D"
    assert classes_sd[(MODO_LIVE, MERCADO_GOALS)] == "C"
    assert classes_sd[(MODO_LIVE, MERCADO_CORNERS)] == "E"
    assert classes_sd[(MODO_LIVE, MERCADO_CARDS)] == "E"

    linhas = formatar_relatorio_cobertura(
        avaliar_cobertura_live(_snap()))
    assert linhas[0].startswith("[COBERTURA]")
    for linha in linhas[1:]:
        partes = linha.split("|")
        assert len(partes) == 6
        assert partes[1].strip() == MODO_LIVE
        assert partes[4].strip() in (
            STATUS_PERMITIDO, STATUS_OBSERVACAO, STATUS_BLOQUEADO)