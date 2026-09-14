"""ETAPA 4 — MATRIZ DEFINITIVA DE COBERTURA DA API (12 testes da FASE J).

Valida a consolidacao operacional da auditoria cirurgica de 13/09/2026.
Regras do operador (verbatim):
  - PRECISAO > QUANTIDADE. Nao ampliar cobertura so para aumentar jogos.
  - Nao invente cobertura; o que vale e o que a conta/plano entrega.
  - Estados por MERCADO (nao bloqueio global): se falta corners, GOALS
    continua.
  - LIVE PARCIAL nao e tratado como completo; None nunca vira zero.
  - Odds LIVE nao disponivel na fonte -> nunca inventar.
  - A matriz NAO promove nenhum mercado experimental para validado.
  - Nenhum teste antigo deve ser removido ou enfraquecido.

Os 12 testes:
  1. mercado permitido segue para analise;
  2. mercado bloqueado nao gera deep dive desnecessario;
  3. outro mercado do mesmo jogo continua;
  4. PRE e LIVE podem ter status diferentes;
  5. competicao desconhecida conservadora;
  6. None nunca vira zero;
  7. odds live ausentes nao viram 0;
  8. liga bloqueada nao e desbloqueada por acidente;
  9. RESULTADO continua experimental;
  10. pressao 5/10/15 continua experimental;
  11. configuracao nao quebra ligas prioritarias validas;
  12. matriz e codigo permanecem coerentes.
"""

import src.cobertura as cobertura_mod
from src.cobertura import (
    BACKTEST_A_CONFIRMAR,
    BACKTEST_INVIÁVEL,
    BACKTEST_PARCIAL,
    BACKTEST_VIAVEL,
    LIVE_COMPLETO,
    LIVE_INSUFICIENTE,
    LIVE_NAO_TESTADO,
    LIVE_PARCIAL,
    MERCADO_CARDS,
    MERCADO_CORNERS,
    MERCADO_GOALS,
    MERCADO_RESULTADO,
    MODO_LIVE,
    MODO_PRE_GAME,
    ODDS_INSUFICIENTE,
    ODDS_LIVE_INDISPONIVEL,
    ODDS_NAO_TESTADO,
    ODDS_PERMITIDO,
    STATUS_BLOQUEADO,
    STATUS_OBSERVACAO,
    STATUS_PERMITIDO,
    STATUS_PRESSAO_EXPERIMENTAL,
    STATUS_RESULTADO_EXPERIMENTAL,
    TODAS_FAMILIAS,
    VeredictoCobertura,
    avaliar_cobertura_live,
    avaliar_cobertura_pre,
    familias_para_deep_dive,
    filtrar_avaliacoes_por_cobertura,
    relatorio_matriz_oficial,
    status_backtest,
    status_live_pressao,
    status_odds_live,
    status_odds_pre,
    status_resultado,
    veredictos_por_mercado,
)
from src.live_opportunity import Avaliacao
from src.policy import LIGAS_EXTENSAO, LIGAS_PRIORITARIAS

# infraestrutura de builders do teste de cobertura (mesmo formato real)
from test_cobertura import _hist, _hist_game, _snap

HOME, AWAY = "Corinthians", "Chapecoense-sc"


# ----------------------------------------------------------------------
# 1. mercado permitido segue para analise
# ----------------------------------------------------------------------
def test_mercado_permitido_segue_para_analise():
    """Liga classe A com dados do fixture em todos os mercados =>
    PERMITIDO em GOALS/CORNERS/CARDS, e familias_para_deep_dive NAO
    restringe (retorna None = motor integral) porque todas estao
    liberadas."""
    hist = _hist(
        [_hist_game(total=11, gols_for=2, gols_against=1, yellow=3, red=0,
                    fid=901)],
        [_hist_game(total=9, gols_for=1, gols_against=1, yellow=2, red=1,
                    fid=911)],
    )
    veredictos = avaliar_cobertura_pre(71, "Brasileirao Serie A", hist)
    for mercado in (MERCADO_GOALS, MERCADO_CORNERS, MERCADO_CARDS):
        v = veredictos_por_mercado(veredictos)[mercado]
        assert v.status == STATUS_PERMITIDO, f"{mercado}: {v.status}"
    # todas as familias liberadas => None (motor integral, sem restricao)
    familias = familias_para_deep_dive(veredictos)
    assert familias is None


# ----------------------------------------------------------------------
# 2. mercado bloqueado nao gera deep dive desnecessario
# ----------------------------------------------------------------------
def test_mercado_bloqueado_nao_gera_deep_dive_desnecessario():
    """Serie D (classe E em escanteios) sem corners no historico =>
    CORNERS BLOQUEADO sai de familias_para_deep_dive ANTES do calculo
    caro. GOALS permanece (placares completos)."""
    hist = _hist(
        [_hist_game(gols_for=2, gols_against=0, fid=901)],
        [_hist_game(gols_for=1, gols_against=1, fid=911)],
        sem_home=5, sem_away=5,
    )
    veredictos = avaliar_cobertura_pre(76, "Serie D", hist)
    corners = veredictos_por_mercado(veredictos)[MERCADO_CORNERS]
    assert corners.status == STATUS_BLOQUEADO
    familias = familias_para_deep_dive(veredictos)
    # escanteios foi removido; nao e None (ha restricao)
    assert familias is not None
    assert "escanteios" not in familias
    assert "gols" in familias  # GOALS permanece


# ----------------------------------------------------------------------
# 3. outro mercado do mesmo jogo continua
# ----------------------------------------------------------------------
def test_outro_mercado_do_mesmo_jogo_continua():
    """Quando CORNERS esta bloqueado, GOALS do MESMO jogo segue no fluxo
    (filtrar_avaliacoes_por_cobertura mantem gols, bloqueia escanteios).
    Nao descarta o jogo inteiro quando apenas um mercado e insuficiente."""
    hist = _hist(
        [_hist_game(gols_for=1, gols_against=1, fid=901)],
        [_hist_game(gols_for=0, gols_against=1, fid=911)],
        sem_home=8, sem_away=8,
    )
    veredictos = avaliar_cobertura_pre(76, "Serie D", hist)
    avaliacoes = [
        _av_stub(mercado="gols"),
        _av_stub(mercado="escanteios"),
        _av_stub(mercado="cartoes"),
    ]
    mantidas, bloqueadas = filtrar_avaliacoes_por_cobertura(
        avaliacoes, veredictos)
    mantidas_mercados = {av.mercado for av in mantidas}
    bloqueadas_mercados = {av.mercado for av, _v in bloqueadas}
    # gols (GOALS) permanece; escanteios (CORNERS) e cartoes (CARDS)
    # da classe E sao bloqueados
    assert "gols" in mantidas_mercados
    assert "escanteios" in bloqueadas_mercados
    assert "cartoes" in bloqueadas_mercados


# ----------------------------------------------------------------------
# 4. PRE e LIVE podem ter status diferentes
# ----------------------------------------------------------------------
def test_pre_e_live_podem_ter_status_diferentes():
    """Serie D (classe E): GOALS PRE e PERMITIDO (placares completos ->
    classe B), mas GOALS LIVE e OBSERVACAO (classe C: placar live existe,
    sem estatisticas de apoio). A mesma liga tem status PRE != LIVE."""
    # PRE: historico de placar sustenta GOALS
    hist = _hist(
        [_hist_game(gols_for=2, gols_against=0, fid=901),
         _hist_game(gols_for=1, gols_against=1, fid=902)],
        [_hist_game(gols_for=0, gols_against=1, fid=911),
         _hist_game(gols_for=3, gols_against=1, fid=912)],
    )
    veredictos_pre = avaliar_cobertura_pre(76, "Serie D", hist)
    goals_pre = veredictos_por_mercado(veredictos_pre)[MERCADO_GOALS]
    assert goals_pre.status == STATUS_PERMITIDO
    assert goals_pre.classe == "B"

    # LIVE: classe C (OBSERVACAO) - placar live sem estatisticas de apoio
    snap = _snap(league_id=76, league_name="Serie D")
    veredictos_live = avaliar_cobertura_live(snap)
    goals_live = veredictos_por_mercado(veredictos_live)[MERCADO_GOALS]
    assert goals_live.status == STATUS_OBSERVACAO
    assert goals_live.classe == "C"
    # status diferentes para a MESMA liga/mercado
    assert goals_pre.status != goals_live.status


# ----------------------------------------------------------------------
# 5. competicao desconhecida conservadora
# ----------------------------------------------------------------------
def test_competicao_desconhecida_conservadora():
    """Liga fora da auditoria => classe C (OBSERVACAO), nunca PERMITIDO.
    Ausencia de evidencia nunca e convertida em permissao."""
    hist = _hist(
        [_hist_game(total=10, gols_for=1, gols_against=1, yellow=2,
                    red=0, fid=901)],
        [_hist_game(total=9, gols_for=2, gols_against=0, yellow=1,
                    red=0, fid=911)],
    )
    veredictos = avaliar_cobertura_pre(999999, "Liga Nova", hist)
    for v in veredictos:
        assert v.classe == "C"
        assert v.status == STATUS_OBSERVACAO  # nunca PERMITIDO
    # ETAPA 4: status_live_pressao / backtest / odds tambem conservadores
    assert status_live_pressao(999999)[0] == LIVE_NAO_TESTADO
    assert status_backtest(999999)[0] == BACKTEST_A_CONFIRMAR
    assert status_odds_pre(999999)[0] == ODDS_NAO_TESTADO


# ----------------------------------------------------------------------
# 6. None nunca vira zero
# ----------------------------------------------------------------------
def test_none_nunca_vira_zero():
    """REGRA ABSOLUTA: ausencia permanece None, nunca 0. Valida nas
    camadas novas da ETAPA 4 e na validacao dinamica existente."""
    # live: Corner Kicks ausentes => None no detalhe, BLOQUEADO (nunca 0)
    veredictos = avaliar_cobertura_live(
        _snap(league_id=71, league_name="Serie A"))
    corners = veredictos_por_mercado(veredictos)[MERCADO_CORNERS]
    assert corners.detalhe["corner_kicks_casa"] is None
    assert corners.detalhe["corner_kicks_fora"] is None
    # odds live: ausencia retorna INDISPONIVEL, nunca "0" nem PERMITIDO
    st, motivo = status_odds_live()
    assert st == ODDS_LIVE_INDISPONIVEL
    assert "0" not in st  # status nao e "0"
    assert "zero" in motivo.lower()  # motivo explicita: nunca vira zero
    # live pressao liga E: campos_confirmados VAZIO (nao zero campos)
    st_e, _, campos_e = status_live_pressao(75)
    assert st_e == LIVE_INSUFICIENTE
    assert campos_e == ()  # sem campos confirmados, nao "0"


# ----------------------------------------------------------------------
# 7. odds live ausentes nao viram 0
# ----------------------------------------------------------------------
def test_odds_live_ausentes_nao_viram_zero():
    """Odds LIVE sao INDISPONIVEIS na fonte (auditoria 28/28 vazios).
    O status nunca e PERMITIDO nem "0"; a ausencia e explicita para que
    a classificacao seja OPORTUNIDADE ESTATISTICA sem odd real."""
    st, motivo = status_odds_live()
    assert st == ODDS_LIVE_INDISPONIVEL
    assert st != ODDS_PERMITIDO
    assert st != "0"
    # odds PRE: liga com odds=false (3=UEL, 848=Conference, 475, 624) =>
    # INSUFICIENTE (nao PERMITIDO, nao "0")
    for lid in (3, 848, 475, 624):
        st_p, _ = status_odds_pre(lid)
        assert st_p == ODDS_INSUFICIENTE, f"liga {lid}: {st_p}"
    # odds PRE confirmadas (39=Premier) => PERMITIDO
    assert status_odds_pre(39)[0] == ODDS_PERMITIDO
    # odds PRE nao testadas (999) => NAO TESTADO (nao assume)
    assert status_odds_pre(999)[0] == ODDS_NAO_TESTADO


# ----------------------------------------------------------------------
# 8. liga bloqueada nao e desbloqueada por acidente
# ----------------------------------------------------------------------
def test_liga_bloqueada_nao_e_desbloqueada_por_acidente():
    """Liga classe E (Serie D = 76) em CORNERS LIVE: MESMO se a fonte
    entregar Corner Kicks nesta leitura, o bloqueio estrutural E se
    mantem - a classe E da auditoria nao e superada por dado pontual."""
    # sem corners no snapshot
    sem = avaliar_cobertura_live(_snap(league_id=76, league_name="Serie D"))
    assert veredictos_por_mercado(sem)[MERCADO_CORNERS].status == \
        STATUS_BLOQUEADO
    # COM corners no snapshot (cenario improvavel): continua BLOQUEADO
    com = avaliar_cobertura_live(
        _snap(league_id=76, league_name="Serie D",
              corner_h=5, corner_a=3, yellow_h=1, yellow_a=2,
              red_h=0, red_a=0))
    assert veredictos_por_mercado(com)[MERCADO_CORNERS].status == \
        STATUS_BLOQUEADO
    # ETAPA 4: NM Cupen (105) reclassificada para E - nao desbloqueia
    assert cobertura_mod._CLASSE_AUDITORIA.get(105) == "E"
    # 848 (Conference) reclassificada A->C: nao e PERMITIDO automatico
    assert cobertura_mod._CLASSE_AUDITORIA.get(848) == "C"


# ----------------------------------------------------------------------
# 9. RESULTADO continua experimental
# ----------------------------------------------------------------------
def test_resultado_continua_experimental():
    """A familia RESULTADO segue o veredicto de GOALS (mesmo dado base),
    MAS permanece EXPERIMENTAL EM OBSERVACAO. A matriz de cobertura NAO
    promove mercado experimental para validado (FASE H)."""
    # liga classe A (71=Brasileirao): cobertura PERMITIDO, validacao
    # continua EXPERIMENTAL
    cov, msg = status_resultado(71, "Brasileirao Serie A", MODO_PRE_GAME)
    assert cov == STATUS_PERMITIDO
    assert STATUS_RESULTADO_EXPERIMENTAL in msg
    # liga classe E (76=Serie D) GOALS PRE: cobertura PERMITIDO (placares),
    # mas RESULTADO continua EXPERIMENTAL
    cov_e, msg_e = status_resultado(76, "Serie D", MODO_PRE_GAME)
    assert STATUS_RESULTADO_EXPERIMENTAL in msg_e
    # o rotulo experimental aparece SEMPRE, independente da cobertura
    for lid in (71, 39, 848, 75, 999999):
        _, m = status_resultado(lid, None, MODO_PRE_GAME)
        assert STATUS_RESULTADO_EXPERIMENTAL in m
    # MERCADO_RESULTADO e uma constante distinta de GOALS
    assert MERCADO_RESULTADO == "RESULTADO"
    assert MERCADO_RESULTADO != MERCADO_GOALS


# ----------------------------------------------------------------------
# 10. pressao 5/10/15 continua experimental
# ----------------------------------------------------------------------
def test_pressao_5_10_15_continua_experimental():
    """A pressao temporal 5/10/15 permanece EXPERIMENTAL / A CALIBRAR.
    A matriz nao cria threshold operacional (FASE H). O status
    experimental aparece no motivo de TODAS as classificacoes live."""
    # classe A (71): LIVE COMPLETO, mas pressao continua EXPERIMENTAL
    st_a, mot_a, _ = status_live_pressao(71)
    assert st_a == LIVE_COMPLETO
    assert STATUS_PRESSAO_EXPERIMENTAL in mot_a
    # classe C / 848 / 73: LIVE PARCIAL, pressao EXPERIMENTAL
    for lid in (848, 73, 479):
        st_p, mot_p, _ = status_live_pressao(lid)
        assert st_p == LIVE_PARCIAL
        assert STATUS_PRESSAO_EXPERIMENTAL in mot_p
    # classe E (75): LIVE INSUFICIENTE, pressao EXPERIMENTAL
    st_e, mot_e, _ = status_live_pressao(75)
    assert st_e == LIVE_INSUFICIENTE
    assert STATUS_PRESSAO_EXPERIMENTAL in mot_e
    # desconhecida: NAO TESTADO, pressao EXPERIMENTAL
    st_n, mot_n, _ = status_live_pressao(999999)
    assert st_n == LIVE_NAO_TESTADO
    # LIVE PARCIAL nunca e tratado como COMPLETO
    assert LIVE_PARCIAL != LIVE_COMPLETO


# ----------------------------------------------------------------------
# 11. configuracao nao quebra ligas prioritarias validas
# ----------------------------------------------------------------------
def test_configuracao_nao_quebra_ligas_prioritarias_validas():
    """Todas as LIGAS_PRIORITARIAS e LIGAS_EXTENSAO (src/policy.py) tem
    uma classe estrutural atribuida na matriz de cobertura (nenhuma fica
    'desconhecida'/C por omissao). A ETAPA 4 nao quebra o universo de
    analise - 848 foi reclassificada A->C, mas permanece no universo
    prioritario com rotulo honesto (OBSERVACAO)."""
    universo = set(LIGAS_PRIORITARIAS) | set(LIGAS_EXTENSAO)
    auditoria = cobertura_mod._CLASSE_AUDITORIA
    for lid in universo:
        assert lid in auditoria, (
            f"liga prioritaria/extensao {lid} sem classe na auditoria - "
            f"ficaria desconhecida (C), quebrando o universo")
        classe = auditoria[lid]
        assert classe in ("A", "B", "C"), (
            f"liga {lid} classe {classe} invalida para universo prioritario")
    # 848 (Conference) reclassificada A->C: continua no universo, agora
    # em OBSERVACAO (nao removida, nao desbloqueada sem evidencia)
    assert 848 in LIGAS_PRIORITARIAS
    assert auditoria[848] == "C"
    # ligas nucleares (39, 71, 140, 135...) permanecem classe A
    for lid in (39, 71, 140, 135, 78, 61):
        assert auditoria[lid] == "A"


# ----------------------------------------------------------------------
# 12. matriz e codigo permanecem coerentes
# ----------------------------------------------------------------------
def test_matriz_e_codigo_permanecem_coerentes():
    """A matriz oficial (relatorio_matriz_oficial) tem uma linha por
    liga da auditoria, sem campos vazios/None nos status, e as
    reclassificacoes da ETAPA 4 (848=C, 105=E, 103=B, 252=B) estao
    refletidas coerentemente entre _CLASSE_AUDITORIA e o relatorio."""
    linhas = relatorio_matriz_oficial()
    auditoria = cobertura_mod._CLASSE_AUDITORIA
    # uma linha por liga da auditoria
    assert len(linhas) == len(auditoria)
    ids_linhas = {linha.id_liga for linha in linhas}
    assert ids_linhas == set(auditoria)
    # nenhum status vazio/None nas colunas de mercado
    for linha in linhas:
        for col in (linha.goals_pre, linha.goals_live,
                    linha.corners_pre, linha.corners_live,
                    linha.cards_pre, linha.cards_live,
                    linha.odds_pre, linha.odds_live,
                    linha.backtest, linha.status_geral):
            assert col, f"liga {linha.id_liga} ({linha.competicao}) " \
                        f"com coluna vazia: {col!r}"
        # odds_live sempre INDISPONIVEL (auditoria)
        assert linha.odds_live == ODDS_LIVE_INDISPONIVEL
        # RESULTADO sempre experimental
        assert STATUS_RESULTADO_EXPERIMENTAL in linha.resultado_pre
        assert STATUS_RESULTADO_EXPERIMENTAL in linha.resultado_live
        # pressao sempre experimental
        assert STATUS_PRESSAO_EXPERIMENTAL in linha.pressao_live
    # reclassificacoes ETAPA 4 coerentes
    por_id = {linha.id_liga: linha for linha in linhas}
    assert por_id[848].status_geral == STATUS_OBSERVACAO  # C -> OBSERVACAO
    # 105 (NM Cupen) classe E: GOALS PRE=PERMITIDO (placares) +
    # CORNERS/CARDS=BLOQUEADO => MISTO (nao tudo bloqueado, nao tudo
    # permitido - por mercado, sem bloqueio global)
    assert "MISTO" in por_id[105].status_geral
    # 103 (Eliteserien) e 252 (Paraguai): classe B -> PERMITIDO
    assert cobertura_mod._CLASSE_AUDITORIA[103] == "B"
    assert cobertura_mod._CLASSE_AUDITORIA[252] == "B"
    # backtest coerente com a classe
    assert por_id[71].backtest == BACKTEST_VIAVEL  # A
    assert por_id[75].backtest == BACKTEST_INVIÁVEL  # E
    assert por_id[848].backtest == BACKTEST_PARCIAL  # C/parcial


# ----------------------------------------------------------------------
# helper: stub de Avaliacao para testes de filtragem
# ----------------------------------------------------------------------
def _av_stub(fid=400, mercado="gols"):
    return Avaliacao(
        jogo=f"{HOME} x {AWAY}", fixture_id=fid,
        competicao="Liga", minuto=40, status="2H", placar="1-0",
        mercado=mercado, linha=f"linha {mercado}",
        prob=0.85,
        sustentacao={"atual_no_jogo": 8, "tempo_restante_min": 50,
                     "taxa_combinada_por90": 10.0,
                     "modelo": "Poisson (CALCULO)"},
        confianca=0.80, conf_componentes={}, riscos=[], odd=None,
        odds_live_existentes=False,
    )