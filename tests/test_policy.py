"""Testes da POLITICA PERMANENTE de universo forte e comparacao de
mercados (src/policy.py).

Sem requisicoes a API: usa objetos sinteticos no formato real.
Regras testadas:
  - universo: ligas prioritarias entram, fracas/obscuras saem,
    variante de reserva/feminino de liga prioritaria sai, times de
    base saem, DADOS != SIM sai;
  - comparacao: linhas fora da disciplina validada nao entram; linhas
    de probabilidade artificialmente alta sao PENALIZADAS (nunca
    vencem por probabilidade); a melhor oportunidade e UMA por jogo;
    utilidade pratica desprezivel => REPROVADO.
"""

from types import SimpleNamespace

from src.policy import (
    LIGAS_EXTENSAO,
    LIGAS_PRIORITARIAS,
    classificar_liga,
    comparar_mercados,
    jogo_elegivel,
    selecionar_melhor,
)


# ----------------------------------------------------------------------
# Universo
# ----------------------------------------------------------------------
def test_ligas_prioritarias_conteudo_minimo():
    # as competicoes da regra do operador estao presentes. Copa
    # Argentina (130) e Copa Colombia (241) foram RETIRADAS em
    # 09/09/2026 (classe E: zero estatisticas de partida).
    esperadas = {39, 140, 135, 78, 61, 2, 3, 848, 71, 94, 88, 203,
                128, 262, 239, 253}
    assert esperadas == set(LIGAS_PRIORITARIAS)


def test_liga_prioritaria_aceita():
    classe, _ = classificar_liga(71, "Serie A")
    assert classe == "PRIORITARIA"


def test_ligas_extensao_promovidas_09_09_2026():
    # promocoes explicitas do operador (auditoria ETAPA 1, 09/09/2026):
    # CONMEBOL de elite + divisoes nacionais BR com cobertura classe A
    esperadas = {13, 11, 72, 73}
    assert esperadas == set(LIGAS_EXTENSAO)
    for lid, nome in ((13, "CONMEBOL Libertadores"),
                      (11, "CONMEBOL Sudamericana"),
                      (72, "Serie B"),
                      (73, "Copa do Brasil")):
        classe, _ = classificar_liga(lid, nome)
        assert classe == "EXTENSAO"
        ok, _ = jogo_elegivel(lid, nome, "Time A", "Time B", "SIM")
        assert ok


def test_copas_sem_cobertura_nao_geram_recomendacao():
    # Copa Argentina (130) e Copa Colombia (241): classe E (zero
    # estatisticas) - RETIRADAS do universo em 09/09/2026. Nunca
    # elegiveis, mesmo com DADOS=SIM (nao geram recomendacoes).
    for lid, nome in ((130, "Copa Argentina"), (241, "Copa Colombia")):
        classe, motivo = classificar_liga(lid, nome)
        assert classe == "EXCLUIDA" and "universo" in motivo
        ok, _ = jogo_elegivel(lid, nome, "Time A", "Time B", "SIM")
        assert not ok
        # cobertura confirma: escanteios/cartoes bloqueados (classe E)
        from src.cobertura import MERCADO_CARDS, MERCADO_CORNERS
        from src.cobertura import MODO_PRE_GAME, classe_estrutural
        cl_esc, _ = classe_estrutural(lid, nome, MERCADO_CORNERS,
                                       MODO_PRE_GAME)
        cl_car, _ = classe_estrutural(lid, nome, MERCADO_CARDS,
                                      MODO_PRE_GAME)
        assert cl_esc == "E" and cl_car in ("D", "E")


def test_liga_fraca_excluida():
    classe, motivo = classificar_liga(204, "1. Lig")  # 2a divisao turca
    assert classe == "EXCLUIDA"
    assert "universo" in motivo


def test_variante_excluida_mesmo_em_liga_prioritaria():
    # reserva / sub / feminino dentro de liga prioritaria: exclui
    classe, _ = classificar_liga(71, "Serie A Reserve")
    assert classe == "EXCLUIDA"
    classe, _ = classificar_liga(140, "La Liga Women")
    assert classe == "EXCLUIDA"


def test_liga_desconhecida_sem_id():
    classe, _ = classificar_liga(None, None)
    assert classe == "EXCLUIDA"


def test_jogo_elegivel_dados():
    ok, _ = jogo_elegivel(135, "Serie A", "Cagliari", "Lecce", "SIM")
    assert ok
    ok, motivo = jogo_elegivel(135, "Serie A", "Cagliari", "Lecce",
                               "PARCIAL")
    assert not ok and "insuficiente" in motivo
    ok, motivo = jogo_elegivel(135, "Serie A", "Cagliari", "Lecce",
                               "INSUFICIENTE")
    assert not ok


def test_time_de_base_exclui():
    ok, motivo = jogo_elegivel(88, "Eredivisie", "Jong PSV U21",
                               "Den Bosch", "SIM")
    assert not ok and "reserva/base" in motivo


# ----------------------------------------------------------------------
# Comparacao de mercados
# ----------------------------------------------------------------------
def _av(linha, prob, mercado="gols", lam=2.5, conf=0.9, riscos=None):
    return SimpleNamespace(
        mercado=mercado,
        linha=linha,
        prob=prob,
        confianca=conf,
        riscos=riscos or [],
        sustentacao={
            "baseline_pre_jogo": "teste",
            "lambda_por90": lam,
            "modelo": "poisson (teste)",
        },
    )


def _hist(n=20, corners=10, goals=3):
    def jogo(i, cf, gf):
        return SimpleNamespace(
            corners_total=cf,
            goals_for=gf,
            goals_against=max(1, 3 - gf),
            played_at_home=(i % 2 == 0),
        )
    games = [jogo(i, corners, goals) for i in range(n)]
    return {
        "games_home": games[: n // 2],
        "games_away": games[n // 2:],
        "n_home": n // 2,
        "n_away": n - n // 2,
    }


_BENCH = {
    "describe": {"media": 2.6},
    "partidas_validas": 100,
}


def test_linha_fora_da_disciplina_nao_compara():
    hist = _hist()
    # prob abaixo da janela e acima do teto: fora
    aves = [_av("Over 1.5 gols (total do jogo)", 0.55),
            _av("Under 3.5 gols (total do jogo)", 0.99),
            _av("Under 4.5 gols (total do jogo)", 0.85)]
    comp = comparar_mercados(aves, hist, _BENCH, _BENCH)
    assert len(comp) == 1
    assert comp[0].avaliacao.prob == 0.85


def test_linha_de_probabilidade_artificialmente_alta_nao_vence():
    hist = _hist()
    # duas linhas dentro da disciplina: a de probabilidade extrema tem
    # utilidade pratica desprezivel e DEVE perder para a mais util
    aves = [
        _av("Over 0.5 gols (total do jogo)", 0.945, lam=2.5),
        _av("Under 4.5 gols (total do jogo)", 0.82, lam=2.5),
    ]
    comp = comparar_mercados(aves, hist, _BENCH, _BENCH)
    assert comp[0].avaliacao.prob == 0.82
    assert comp[0].utilidade_prob > comp[1].utilidade_prob


def test_melhor_oportunidade_unica_por_jogo():
    hist = _hist()
    aves = [
        _av("Under 4.5 gols (total do jogo)", 0.82, lam=2.5),
        _av("Under 5.5 gols (total do jogo)", 0.93, lam=2.5),
        _av("Over 5.5 escanteios (total do jogo)", 0.79, mercado="escanteios",
            lam=9.5),
    ]
    sel = selecionar_melhor(aves, hist, _BENCH, _BENCH)
    assert sel.melhor is not None
    # apenas UMA selecionada
    assert sel.melhor is sel.comparadas[0]


def test_reprovado_sem_linha_disciplinada():
    hist = _hist()
    sel = selecionar_melhor([_av("Over 1.5 gols (total do jogo)", 0.55)],
                            hist, _BENCH, _BENCH)
    assert sel.melhor is None
    assert "disciplina" in sel.reprovacao


def test_reprovado_utilidade_desprezivel():
    hist = _hist()
    # unica linha disponivel: prob 0.96 => odd justa ~1.04
    sel = selecionar_melhor(
        [_av("Over 0.5 gols (total do jogo)", 0.96, lam=3.4)],
        hist, _BENCH, _BENCH)
    assert sel.melhor is None
    assert "utilidade pratica" in sel.reprovacao


def test_componentes_do_score_sao_rotulados():
    hist = _hist()
    comp = comparar_mercados(
        [_av("Under 4.5 gols (total do jogo)", 0.82, lam=2.5)],
        hist, _BENCH, _BENCH)[0]
    # todos os componentes de comparacao exigidos pela regra estao la
    for nome in ("utilidade_prob", "utilidade_largura", "estabilidade",
                 "aderencia", "cobertura", "contracoes", "cv_amostral",
                 "dist_equilibrio"):
        assert hasattr(comp, nome)
    assert 0 < comp.score <= 1