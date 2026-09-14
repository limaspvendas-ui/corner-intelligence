"""TESTE DE CALIBRACAO EM MODO OBSERVACAO - testes.

Valida:
    - produtor pre-jogo: Poisson sobre 90 min, janela de aprovacao,
      cap de 2 por jogo, congelamento tipo prejogo (sem minuto/placar),
      dedupe sem duplicar;
    - liquidacao automatica: .5 decide GANHA/PERDIDA, linha inteira
      DEVOLVIDA, jogo nao encerrado fica PENDENTE, estatistica ausente
      => NAO AVALIAVEL (nunca zero), previsao original intacta;
    - relatorio de calibracao: taxa = GANHA/(GANHA+PERDIDA), exclusoes
      contabilizadas, aviso permanente de amostra pequena;
    - hook live: toda aprovada e congelada no registro no momento da
      producao.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.calibration import (
    CAVEAT_PERMANENTE,
    MIN_AMOSTRA,
    format_calibracao,
    relatorio_calibracao,
)
from src.prejogo_opportunity import (
    MAX_APROVADAS_PREJOGO,
    VERSAO_PREJOGO_OP,
    _confianca_prejogo,
    aprovar_pregame,
    avaliar_pregame,
    registrar_aprovadas_prejogo,
)
from src.registry import RegistroRecomendacoes
from src.settlement import (
    NAO_AVALIAVEL,
    PENDENTE,
    _parse_linha,
    _resultado_do_total,
    liquidar_pendentes,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _game(esc_pro, esc_contra, gols_pro, gols_contra, casa=True):
    return SimpleNamespace(
        played_at_home=casa,
        corners_for=esc_pro, corners_against=esc_contra,
        goals_for=gols_pro, goals_against=gols_contra,
        yellow_for=2,
    )


def _hist(n=10, esc=7.0):
    """Dois times com historico estavel de escanteios/gols."""
    games_h = [_game(esc + 1, esc - 1, 2, 1, casa=True) for _ in range(n)]
    games_a = [_game(esc, esc - 2, 1, 1, casa=False) for _ in range(n)]
    return {
        "games_home": games_h, "games_away": games_a,
        "n_home": len(games_h), "n_away": len(games_a),
    }


def _bench(media, validas=100):
    return {"partidas_validas": validas, "describe": {"media": media}}


def _fx_pre(fixture_id=555001):
    return SimpleNamespace(
        fixture_id=fixture_id, league_name="Serie A", status="NS",
        home_team_id=101, home_team_name="Time A",
        away_team_id=202, away_team_name="Time B",
    )


class _ClientFixo:
    """Cliente fake: responde /fixtures e /fixtures/statistics."""

    def __init__(self, fixture_raw, statistics_raw):
        self._fixture = fixture_raw
        self._stats = statistics_raw
        self.chamadas = []

    def get(self, endpoint, params=None):
        self.chamadas.append((endpoint, dict(params or {})))
        if endpoint == "/fixtures":
            return self._fixture
        if endpoint == "/fixtures/statistics":
            return self._stats
        raise AssertionError(f"endpoint inesperado: {endpoint}")


def _fixture_raw(status="FT", gh=0, ga=2, fid=1000):
    return [{
        "fixture": {"id": fid, "date": "2026-09-06T16:00:00-03:00",
                     "status": {"short": status, "elapsed": 90}},
        "league": {"id": 71, "name": "Serie A", "season": 2026},
        "teams": {"home": {"id": 1, "name": "A"},
                  "away": {"id": 2, "name": "B"}},
        "goals": {"home": gh, "away": ga},
    }]


def _stats_raw(esc_home=8, esc_away=4):
    def bloco(tid, valor):
        return {
            "team": {"id": tid, "name": f"T{tid}"},
            "statistics": [
                {"type": "Total Shots", "value": "10"},
                {"type": "Corner Kicks", "value": str(valor)},
            ],
        }
    return [bloco(1, esc_home), bloco(2, esc_away)]


def _registrar_gol(reg, fixture_id, linha, prob, resultado, conf=0.9):
    rec_id = reg.registrar(
        fixture_id=fixture_id, tipo="live", mercado="gols", linha=linha,
        probabilidade=prob, versao_analise="live-op-2.2-triagem-global",
        confianca=conf,
    )
    if resultado is not None:
        reg.registrar_resultado(rec_id, resultado_mercado=resultado)
    return rec_id


# ----------------------------------------------------------------------
# Produtor pre-jogo
# ----------------------------------------------------------------------
def test_pregame_probabilidade_poisson_complementar():
    """Over X.5 e Under X.5 da mesma linha somam 100% (mesma lambda)."""
    avaliacoes = avaliar_pregame(_hist(), _bench(10.0), _bench(2.5))
    pares = {}
    for av in avaliacoes:
        chave = (av.mercado, av.linha.split(" ")[1])  # valor da linha
        pares.setdefault(chave, {})[av.linha.split(" ")[0]] = av.prob
    for chave, direcoes in pares.items():
        if "Over" in direcoes and "Under" in direcoes:
            assert direcoes["Over"] + direcoes["Under"] == pytest.approx(
                1.0, abs=1e-6
            )


def test_pregame_probabilidade_consiste_com_poisson_direto():
    from src.live_opportunity import poisson_ge, poisson_le

    hist = _hist()
    bench = _bench(10.0)
    # lambda = 0.65*baseline + 0.35*10.0; baseline do hist de esc=7:
    # exp_home = ((8) + (5))/2 = 6.5; exp_away = (7 + 6)/2 = 6.5 => 13.0
    # lambda = 0.65*13 + 0.35*10 = 11.95
    avaliacoes = avaliar_pregame(hist, bench, None)
    lam = 0.65 * 13.0 + 0.35 * 10.0
    over = next(a for a in avaliacoes
                if a.linha == "Over 10.5 escanteios (total do jogo)")
    under = next(a for a in avaliacoes
                 if a.linha == "Under 10.5 escanteios (total do jogo)")
    assert over.prob == pytest.approx(poisson_ge(11, lam), abs=1e-4)
    assert under.prob == pytest.approx(poisson_le(10, lam), abs=1e-4)


def test_pregame_sem_sustentacao_nao_avalia_nada():
    """Sem baselines e sem benchmark: NENHUMA linha e avaliada. Com
    historico dos times mas SEM benchmark, avalia com baseline apenas
    (mesma regra do live) - e o risco fica registrado."""
    vazios = {"games_home": [], "games_away": [], "n_home": 0, "n_away": 0}
    assert avaliar_pregame(vazios, None, None) == []
    so_times = avaliar_pregame(_hist(), None, None)
    assert so_times  # baseline dos times sustenta a avaliacao
    assert all(
        "sem benchmark da liga: apenas historico dos times" in a.riscos
        for a in so_times
    )


def test_pregame_aprovacao_janela_e_cap():
    """Apenas prob 70%-97% e confianca >= 60%; no maximo 2 por jogo."""
    avaliacoes = avaliar_pregame(_hist(), _bench(10.0), _bench(2.5))
    aprovadas = aprovar_pregame(avaliacoes)
    assert 1 <= len(aprovadas) <= MAX_APROVADAS_PREJOGO
    for av in aprovadas:
        assert 0.70 <= av.prob <= 0.97
        assert av.confianca >= 0.60
    probs = [a.prob for a in aprovadas]
    assert probs == sorted(probs, reverse=True)
    # nenhuma aprovada fora da janela foi incluida
    dentro = [a for a in avaliacoes if 0.70 <= a.prob <= 0.97
              and a.confianca >= 0.60]
    assert len(aprovadas) == min(len(dentro), MAX_APROVADAS_PREJOGO)


def test_pregame_confianca_componentes():
    conf, comps = _confianca_prejogo(10, 100, 5)
    assert conf == pytest.approx(0.40 + 0.40 + 0.20)
    conf_parcial, _ = _confianca_prejogo(5, None, 0)
    assert conf_parcial == pytest.approx(0.40 * 0.5)  # amostra 0.5


def test_pregame_registro_congela_tipo_prejogo(tmp_path):
    """Aprovada pre-jogo e congelada SEM minuto/placar (jogo nao
    iniciado) - e a reexecucao NAO duplica."""
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    avaliacoes = avaliar_pregame(_hist(), _bench(10.0), _bench(2.5))
    aprovadas = aprovar_pregame(avaliacoes)
    registros = registrar_aprovadas_prejogo(reg, aprovadas, _fx_pre(), _hist())
    assert registros and all(novo for _id, novo in registros)

    rec = reg.obter(registros[0][0])
    assert rec["tipo"] == "prejogo"
    assert rec["versao_analise"] == VERSAO_PREJOGO_OP
    assert rec["minuto"] is None      # pre-jogo: sem minuto
    assert rec["placar"] is None      # pre-jogo: sem placar
    assert rec["status"] is None
    assert rec["probabilidade"] == aprovadas[0].prob
    assert rec["amostra_n"] == 10
    assert rec["snapshot_api_ts"]  # congelado ANTES do resultado
    assert rec["resultado_mercado"] is None  # liquidadacao so depois

    # reexecucao da MESMA observacao: dedupe, sem duplicar
    registros2 = registrar_aprovadas_prejogo(
        reg, aprovadas, _fx_pre(), _hist()
    )
    assert [i for i, _ in registros2] == [i for i, _ in registros]
    assert all(not novo for _id, novo in registros2)
    assert len(reg.listar(tipo="prejogo")) == len(registros)


# ----------------------------------------------------------------------
# Guarda contra inversao textual em linhas Under
# ----------------------------------------------------------------------
def test_under_linha_sem_inversao_cenario_top1():
    """Under NAO e o espelho invertido na leitura: suporta ate N
    eventos ADICIONAIS para GANHAR e perde a partir de N+1.

    Cenario real (TOP 1, 06/09/2026): Under 12.5 escanteios com 8
    escanteios ja ocorridos aos 61'. GANHA com ate 4 adicionais
    (total final 12 ou menos); PERDE com 5 ou mais (13+). A regra
    direcional do motor (_resultado_do_total) e a referencia unica
    da frase de interpretacao: Under: total < linha => GANHA.
    """
    linha = 12.5
    atuais = 8
    # Under: totais finais de 8 a 12 (0 a 4 escanteios adicionais) GANHAM
    for adicionais in range(0, 5):
        total = atuais + adicionais
        assert total <= 12
        assert _resultado_do_total("Under", total, linha) == "GANHA"
    # Under: 5 ou mais adicionais (total final 13 ou mais) PERDEM
    for adicionais in range(5, 13):
        total = atuais + adicionais
        assert total >= 13
        assert _resultado_do_total("Under", total, linha) == "PERDIDA"
    # espelho Over na MESMA linha: direcao oposta ponto a ponto
    assert _resultado_do_total("Over", 12, linha) == "PERDIDA"
    assert _resultado_do_total("Over", 13, linha) == "GANHA"


# ----------------------------------------------------------------------
# Liquidacao automatica
# ----------------------------------------------------------------------
def test_parse_linha_total():
    assert _parse_linha("Over 9.5 escanteios (total do jogo)") == (
        "Over", 9.5, "escanteios"
    )
    assert _parse_linha("Under 3.5 gols (total do jogo)") == (
        "Under", 3.5, "gols"
    )
    assert _parse_linha("Palmeiras +1.5 AH") is None


def test_liquidacao_gols_meia_linha(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    id_ganha = _registrar_gol(reg, 1000, "Under 3.5 gols (total do jogo)",
                              0.845, None)
    id_perdida = _registrar_gol(reg, 1000, "Over 3.5 gols (total do jogo)",
                                 0.72, None)
    client = _ClientFixo(_fixture_raw(gh=0, ga=2), _stats_raw())
    report = liquidar_pendentes(client, reg)

    por_id = {i["id"]: i for i in report["itens"]}
    assert por_id[id_ganha]["situacao"] == "GANHA"   # total 2 < 3.5
    assert por_id[id_perdida]["situacao"] == "PERDIDA"
    rec = reg.obter(id_ganha)
    assert rec["placar_final"] == "0-2"
    assert rec["stats_finais"]["gols"] == [0, 2]
    assert rec["probabilidade"] == pytest.approx(0.845)  # previsao intacta


def test_liquidacao_escanteios_pela_estatistica_final(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rec_id = reg.registrar(
        fixture_id=1000, tipo="live", mercado="escanteios",
        linha="Over 9.5 escanteios (total do jogo)", probabilidade=0.71,
        versao_analise="live-op-2.2-triagem-global",
    )
    client = _ClientFixo(_fixture_raw(gh=1, ga=1), _stats_raw(8, 4))
    report = liquidar_pendentes(client, reg)

    assert report["itens"][0]["situacao"] == "GANHA"  # 8+4=12 > 9.5
    rec = reg.obter(rec_id)
    assert rec["stats_finais"]["escanteios"] == [8, 4]
    assert rec["placar_final"] == "1-1"


def test_liquidacao_linha_inteira_devolvida(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rec_id = reg.registrar(
        fixture_id=1000, tipo="live", mercado="escanteios",
        linha="Over 10 escanteios (total do jogo)", probabilidade=0.75,
        versao_analise="live-op-2.2-triagem-global",
    )
    client = _ClientFixo(_fixture_raw(), _stats_raw(5, 5))
    report = liquidar_pendentes(client, reg)

    assert report["itens"][0]["situacao"] == "DEVOLVIDA"  # 10 == 10
    assert reg.obter(rec_id)["resultado_mercado"] == "DEVOLVIDA"


def test_liquidacao_jogo_nao_encerrado_fica_pendente(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rec_id = _registrar_gol(reg, 2000, "Under 3.5 gols (total do jogo)",
                            0.90, None)
    client = _ClientFixo(_fixture_raw(status="2H", fid=2000), _stats_raw())
    report = liquidar_pendentes(client, reg)

    item = report["itens"][0]
    assert item["situacao"] == PENDENTE
    assert "ainda nao encerrado" in item["motivo"]
    # nada registrado: liquidacao SOMENTE com resultado final real
    assert reg.obter(rec_id)["resultado_mercado"] is None


def test_liquidacao_sem_estatistica_nao_avaliavel_nunca_zero(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rec_id = reg.registrar(
        fixture_id=1000, tipo="live", mercado="escanteios",
        linha="Over 9.5 escanteios (total do jogo)", probabilidade=0.80,
        versao_analise="live-op-2.2-triagem-global",
    )
    client = _ClientFixo(_fixture_raw(), [])  # sem bloco de estatisticas
    report = liquidar_pendentes(client, reg)

    assert report["itens"][0]["situacao"] == NAO_AVALIAVEL
    rec = reg.obter(rec_id)
    assert rec["resultado_mercado"] == NAO_AVALIAVEL
    esc = rec["stats_finais"]["escanteios"]
    assert esc == [None, None]  # ausente, NUNCA zero


def test_liquidacao_previsao_intacta_e_auditoria(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rec_id = _registrar_gol(reg, 1000, "Under 3.5 gols (total do jogo)",
                            0.938, None)
    antes = reg.obter(rec_id)
    client = _ClientFixo(_fixture_raw(), _stats_raw())
    liquidar_pendentes(client, reg)

    depois = reg.obter(rec_id)
    for campo in ("fixture_id", "criado_em", "tipo", "versao_analise",
                  "mercado", "linha", "probabilidade", "confianca",
                  "minuto", "placar", "snapshot_api_ts"):
        assert depois[campo] == antes[campo], f"{campo} nao pode mudar"
    assert depois["resultado_mercado"] == "GANHA"
    # trilha de auditoria registrou a liquidacao
    assert depois["auditoria"][-1]["acao"] == "resultado registrado"


# ----------------------------------------------------------------------
# Relatorio de calibracao
# ----------------------------------------------------------------------
def test_calibracao_taxa_exclusoes_e_bandas(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    # ALTA (conf >= 0.85): 2 ganhas + 1 perdida
    _registrar_gol(reg, 1, "L1", 0.90, "GANHA", conf=0.93)
    _registrar_gol(reg, 2, "L2", 0.90, "GANHA", conf=0.93)
    _registrar_gol(reg, 3, "L3", 0.85, "PERDIDA", conf=0.87)
    # MEDIA (0.70-0.85): 1 ganha + 1 devolvida
    _registrar_gol(reg, 4, "L4", 0.80, "GANHA", conf=0.81)
    _registrar_gol(reg, 5, "L5", 0.80, "DEVOLVIDA", conf=0.75)
    # NAO AVALIAVEL: excluida da taxa, contabilizada
    _registrar_gol(reg, 6, "L6", 0.80, NAO_AVALIAVEL, conf=0.81)
    # pendente: fora da calibracao
    _registrar_gol(reg, 7, "L7", 0.80, None, conf=0.81)

    rep = relatorio_calibracao(reg)
    geral = rep["geral"]
    assert geral["avaliaveis"] == 4      # 3 GANHA + 1 PERDIDA
    assert geral["ganha"] == 3
    assert geral["perdida"] == 1
    assert geral["taxa_acerto"] == pytest.approx(0.75)
    assert geral["prob_media_estimada"] == pytest.approx(
        (0.90 + 0.90 + 0.85 + 0.80) / 4
    )
    assert geral["devolvida"] == 1
    assert geral["nao_avaliavel"] == 1
    assert geral["amostra_pequena"] is True  # 4 < MIN_AMOSTRA
    assert rep["aguardando_resultado"] == 1

    alta = rep["por_confianca"]["ALTA"]
    assert alta["avaliaveis"] == 3 and alta["ganha"] == 2
    media = rep["por_confianca"]["MEDIA"]
    assert media["avaliaveis"] == 1 and media["devolvida"] == 1
    assert rep["por_confianca"]["BAIXA"]["avaliaveis"] == 0

    texto = format_calibracao(rep)
    assert CAVEAT_PERMANENTE in texto
    assert "AMOSTRA PEQUENA" in texto
    assert "6/6 NAO e evidencia" in texto


def test_calibracao_amostra_madura_sem_aviso_de_celula(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    for i in range(MIN_AMOSTRA):  # 30 avaliaveis: celula madura
        _registrar_gol(reg, 100 + i, f"L{i}", 0.80, "GANHA", conf=0.93)
    rep = relatorio_calibracao(reg)
    assert rep["geral"]["amostra_pequena"] is False
    assert rep["por_confianca"]["ALTA"]["amostra_pequena"] is False


def test_calibracao_por_tipo_e_minuto(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    id_live = reg.registrar(
        fixture_id=1, tipo="live", mercado="gols",
        linha="Under 3.5 gols (total do jogo)", probabilidade=0.9,
        versao_analise="live-op-2.2-triagem-global", minuto=67,
        confianca=0.93,
    )
    id_pre = reg.registrar(
        fixture_id=2, tipo="prejogo", mercado="escanteios",
        linha="Over 9.5 escanteios (total do jogo)", probabilidade=0.72,
        versao_analise=VERSAO_PREJOGO_OP, confianca=0.80,
    )
    reg.registrar_resultado(id_live, resultado_mercado="GANHA")
    reg.registrar_resultado(id_pre, resultado_mercado="PERDIDA")

    rep = relatorio_calibracao(reg)
    assert rep["por_tipo"]["live"]["ganha"] == 1
    assert rep["por_tipo"]["prejogo"]["perdida"] == 1
    assert rep["por_faixa_minuto"]["61-75"]["avaliaveis"] == 1
    assert rep["por_faixa_minuto"]["15-30"]["avaliaveis"] == 0
    # pre-jogo nao tem minuto: nao entra em nenhuma faixa
    assert rep["por_faixa_minuto"]["76+"]["avaliaveis"] == 0


# ----------------------------------------------------------------------
# Hook live: toda aprovada congelada no momento da producao
# ----------------------------------------------------------------------
def test_hook_live_congela_aprovadas_no_momento(monkeypatch, tmp_path):
    from src import app, registry

    monkeypatch.setattr(
        registry, "REGISTRY_DB_PATH", tmp_path / "hook.db"
    )

    av = SimpleNamespace(
        fixture_id=300, mercado="gols", prob=0.845,
        linha="Under 3.5 gols (total do jogo)", minuto=67, status="2H",
        placar="0-2", competicao="Clausura (Paraguay)", confianca=0.93,
        riscos=["gols: placar muda o estado a qualquer momento"],
        sustentacao={"atual_no_jogo": 2, "tempo_restante_min": 23,
                     "esperado_no_restante": 0.7},
        odd=None,
    )
    cand = SimpleNamespace(
        snapshot=SimpleNamespace(
            fixture_id=300, home_team_name="Sportivo Luqueno",
            away_team_name="Sportivo Ameliano",
            collected_at="06/09/2026 19:53:33",
        ),
        historico={"games_home": [], "games_away": [],
                   "n_home": 7, "n_away": 8},
        benchmark_escanteios=None, benchmark_gols=None,
        h2h_stats=None, h2h_n=0,
    )
    varredura = SimpleNamespace(aprovadas=[av], candidatos=[cand])

    app._registrar_aprovadas(varredura)

    reg = RegistroRecomendacoes(db_path=str(tmp_path / "hook.db"))
    recs = reg.listar(fixture_id=300)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["tipo"] == "live"
    assert rec["minuto"] == 67 and rec["placar"] == "0-2"
    assert rec["probabilidade"] == pytest.approx(0.845)
    assert rec["resultado_mercado"] is None  # liquidade so depois

    # reescaneamento da MESMA recomendacao: sem duplicar
    app._registrar_aprovadas(varredura)
    assert len(reg.listar(fixture_id=300)) == 1