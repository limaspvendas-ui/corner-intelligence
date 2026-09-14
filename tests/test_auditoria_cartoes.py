"""Testes da auditoria cirurgica de cartoes (Etapa 5D).

Somente leitura: a auditoria NAO altera DB, NAO altera regras, NAO liquida.
Valida: zero != None, eventos interpretados sem inventar cartao, contagens
deterministicas, settlement oficial intacto, regras do motor congeladas.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest

from src.auditoria_cartoes import (
    RUN_ID,
    _card_vals,
    _classify_fixture,
    _determinavel_yellow,
    _is_half_line,
    _parse_linha,
    _to_int,
    auditar,
)
from src.backtest import BACKTEST_DB_PATH
from src.config import DB_PATH
from src.settlement import _MARCA_CONVENCAO_CARTOES


# ----------------------------------------------------------------------
# Parsing / zero vs None
# ----------------------------------------------------------------------
def test_parse_linha_over_under():
    assert _parse_linha("Over 3.5 cartoes (amarelo=1, vermelho=2) (total do jogo)") == ("over", 3.5)
    assert _parse_linha("Under 2.5 cartoes (amarelo=1, vermelho=2) (total do jogo)") == ("under", 2.5)
    assert _parse_linha("") == (None, None)
    assert _parse_linha("sem linha") == (None, None)


def test_to_int_none_vs_zero():
    # REGRA PERMANENTE: None nunca vira zero; "0" vira 0.
    assert _to_int(None) is None
    assert _to_int("0") == 0
    assert _to_int(0) == 0
    assert _to_int("4") == 4
    assert _to_int("") is None
    assert _to_int("abc") is None


def test_is_half_line():
    assert _is_half_line(3.5) is True
    assert _is_half_line(2.5) is True
    assert _is_half_line(3.0) is False
    assert _is_half_line(4.0) is False


# ----------------------------------------------------------------------
# Classificacao FASE B
# ----------------------------------------------------------------------
def _blocos(yellow_home, red_home, yellow_away, red_away,
            ht=100, at=200, include_types=True):
    """Constroi blocos de /fixtures/statistics para teste. Simula a fonte
    real: o tipo SEMPRE aparece (com value possivelmente None = null)."""
    def side(tid, y, r):
        stats = []
        if include_types:
            stats.append({"type": "Yellow Cards", "value": y})
            stats.append({"type": "Red Cards", "value": r})
        return {"team": {"id": tid}, "statistics": stats}
    return [side(ht, yellow_home, red_home), side(at, yellow_away, red_away)]


def test_classify_categoria_B_amarelos_vermelhos_ausentes():
    # Yellow presentes, Red = None (null na fonte) -- o caso dominante (195)
    blocos = _blocos(4, None, 3, None)
    c = _classify_fixture(blocos, 100, 200)
    assert c["categoria"] == "B_amarelos_disponiveis_vermelhos_ausentes"
    assert c["hyi"] == 4 and c["ayi"] == 3
    assert c["hri"] is None and c["ari"] is None
    assert c["hrp"] is True and c["arp"] is True  # tipo presente, valor null


def test_classify_categoria_A_todos_disponiveis():
    blocos = _blocos(4, 0, 3, 1)
    c = _classify_fixture(blocos, 100, 200)
    assert c["categoria"] == "A_todos_4_disponiveis"
    assert c["hri"] == 0 and c["ari"] == 1  # zero verdadeiro preservado


def test_classify_categoria_D_todos_null():
    # Tipos presentes mas todos valores null
    blocos = _blocos(None, None, None, None)
    c = _classify_fixture(blocos, 100, 200)
    assert c["categoria"] == "D_tipos_presentes_valores_null"


def test_classify_categoria_C_vermelhos_amarelos_ausentes():
    blocos = _blocos(None, 0, None, 1)  # yellow null, red presente
    c = _classify_fixture(blocos, 100, 200)
    assert c["categoria"] == "C_vermelhos_disponiveis_amarelos_ausentes"


def test_classify_categoria_G_sem_statistics():
    c = _classify_fixture(None, 100, 200)
    assert c["categoria"] == "G_sem_statistics"
    c2 = _classify_fixture([], 100, 200)
    assert c2["categoria"] == "G_sem_statistics"


def test_classify_categoria_E_bloco_ausente():
    # so o bloco do mandante; visitante nao tem bloco
    blocos = [{"team": {"id": 100}, "statistics": [
        {"type": "Yellow Cards", "value": 3},
        {"type": "Red Cards", "value": 0}]}]
    c = _classify_fixture(blocos, 100, 200)
    assert c["categoria"] == "E_bloco_time_ausente"
    assert c["away_block"] is False


def test_card_vals_team_block_missing():
    # bloco do time nao existe -> (None, None, False, False, -1)
    y, r, yp, rp, n = _card_vals([], 999)
    assert n == -1
    assert y is None and r is None and yp is False and rp is False


# ----------------------------------------------------------------------
# Determinabilidade FASE K/L -- sem inventar cartao
# ----------------------------------------------------------------------
def test_determinavel_yellow_over_ganha():
    # Over 3.5, Yellow total = 7 -> total >= 7 > 3.5 -> GANHA (Red irrelevante)
    cls = {"hyi": 4, "ayi": 3}
    det, res = _determinavel_yellow(cls, "Over 3.5 cartoes (amarelo=1, vermelho=2) (total do jogo)")
    assert det is True and res == "GANHA"


def test_determinavel_yellow_under_perdida():
    # Under 3.5, Yellow total = 5 -> total >= 5 > 3.5 -> Under PERDIDA
    cls = {"hyi": 3, "ayi": 2}
    det, res = _determinavel_yellow(cls, "Under 3.5 cartoes (amarelo=1, vermelho=2) (total do jogo)")
    assert det is True and res == "PERDIDA"


def test_nao_determinavel_yellow_abaixo_linha():
    # Over 9.5, Yellow total = 4 -> Red decide (4+2R pode ou nao passar 9.5)
    cls = {"hyi": 2, "ayi": 2}
    det, res = _determinavel_yellow(cls, "Over 9.5 cartoes (amarelo=1, vermelho=2) (total do jogo)")
    assert det is False and res is None


def test_nao_determinavel_sem_yellow():
    # Sem Yellow (null) -> nunca determinavel
    cls = {"hyi": None, "ayi": 3}
    det, res = _determinavel_yellow(cls, "Over 3.5 cartoes (amarelo=1, vermelho=2) (total do jogo)")
    assert det is False


# ----------------------------------------------------------------------
# Auditoria end-to-end: determinismo, read-only, contagens
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def resultado_auditoria():
    return auditar(BACKTEST_DB_PATH, DB_PATH, RUN_ID)


def test_auditoria_reproduz_contagens_5b(resultado_auditoria):
    r = resultado_auditoria
    assert r["total_entrar"] == 307
    assert r["ganhas"] == 91
    assert r["perdidas"] == 9
    assert r["nao_avaliaveis"] == 207
    assert r["fixtures_na_unicos"] == 204


def test_auditoria_deterministica():
    a = auditar(BACKTEST_DB_PATH, DB_PATH, RUN_ID)
    b = auditar(BACKTEST_DB_PATH, DB_PATH, RUN_ID)
    # Contagens principais estaveis entre execucoes
    assert a["classificacao_fase_b"] == b["classificacao_fase_b"]
    assert a["causa_fase_i"] == b["causa_fase_i"]
    assert a["fase_k_l_simulacao"]["recuperaveis_dado_existente"] == \
        b["fase_k_l_simulacao"]["recuperaveis_dado_existente"]


def test_auditoria_nao_altera_db():
    """Read-only: nenhuma linha inserida/removida nos bancos; previsoes
    oficiais do backtest permanecem identicas apos a auditoria."""
    bt = sqlite3.connect(BACKTEST_DB_PATH)
    antes = bt.execute(
        "SELECT id, resultado_final, total_final FROM bt_predictions "
        "WHERE run_id = ? AND mercado = 'cartoes' ORDER BY id",
        (RUN_ID,),
    ).fetchall()
    bt.close()

    auditar(BACKTEST_DB_PATH, DB_PATH, RUN_ID)

    bt = sqlite3.connect(BACKTEST_DB_PATH)
    depois = bt.execute(
        "SELECT id, resultado_final, total_final FROM bt_predictions "
        "WHERE run_id = ? AND mercado = 'cartoes' ORDER BY id",
        (RUN_ID,),
    ).fetchall()
    bt.close()
    assert antes == depois

    # api_cache: contador de linhas inalterado
    api = sqlite3.connect(DB_PATH)
    n_antes = api.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
    api.close()

    auditar(BACKTEST_DB_PATH, DB_PATH, RUN_ID)

    api = sqlite3.connect(DB_PATH)
    n_depois = api.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
    api.close()
    assert n_antes == n_depois


def test_auditoria_zero_diferente_none(resultado_auditoria):
    # No universo NA ha zeros verdadeiros (34) e nulls (424) -- nunca
    # confundidos. O parser preserva ambos.
    z = resultado_auditoria["zero_vs_none_universo_na"]
    assert z["raw_zero"] > 0  # existem zeros verdadeiros
    assert z["raw_none"] > z["raw_zero"]  # nulls dominam (ausencia)
    # cache global: Red Cards tem null, zero e positive distintos
    red = resultado_auditoria["distribuicao_cache_global"]["red_cards"]
    assert "null" in red and "zero" in red and "positive" in red
    assert red["null"] > red["zero"]  # null e a maioria dos vermelhos


def test_auditoria_events_nao_inventam_cartao(resultado_auditoria):
    # 0 dos 207 NA fixtures tem events em cache -> recuperacao via events = 0
    ev = resultado_auditoria["fase_d_events"]
    assert ev["fixtures_na_com_events"] == 0
    assert resultado_auditoria["fase_k_l_simulacao"]["recuperaveis_via_events_cache"] == 0
    # Card detail strings so trazem "Yellow Card" / "Red Card" -- nenhum
    # cartao e inventado pelo modulo.
    details = ev["card_detail_strings"]
    for k in details:
        assert k in ("Yellow Card", "Red Card")


def test_auditoria_settlement_oficial_intacto(resultado_auditoria):
    # Revalidacao: 0 mismatches -- settlement 100% correto, nao ha bug.
    h = resultado_auditoria["fase_h_settlement_revalidate"]
    assert h["mismatches"] == 0
    assert h["loss_mismatches"] == 0
    assert h["settlement_ok"] is True


def test_auditoria_cache_coletado_pos_partida(resultado_auditoria):
    # Fase G: nenhum fixture coletado antes da partida -- nao e cache prematuro
    g = resultado_auditoria["fase_g_timing_cache"]
    assert g["coletado_antes_partida"] == 0
    assert g["med_hours"] > 24  # mediana > 1 dia apos kickoff


def test_auditoria_simulacao_consistencia(resultado_auditoria):
    # 95 recuperaveis (todos GANHA); 103 red-decide; 9 sem dado; soma = 207
    s = resultado_auditoria["fase_k_l_simulacao"]
    assert s["recuperaveis_ganha"] == 95
    assert s["recuperaveis_perdida"] == 0
    total = (s["recuperaveis_dado_existente"]
             + s["nao_determinaveis_red_decide"]
             + s["sem_dado_cartoes"])
    assert total == 207
    # hipotetico: 186 G / 9 P / 112 NA
    hip = s["hipotetico_se_recuperado"]
    assert hip == {"ganha": 186, "perdida": 9, "nao_avaliavel": 112}


def test_auditoria_regras_motor_congeladas():
    # A auditoria nao importa nem muta nenhuma constante de regra do motor.
    # Convencao de cartoes permanece amarelo=1, vermelho=2.
    assert _MARCA_CONVENCAO_CARTOES == "amarelo=1, vermelho=2"
    # Importar settlement de novo nao deve ter side-effect nas previsoes.
    from src.settlement import _total_cartoes  # noqa: F401  (so pra confirmar import estavel)
    # Contagem total de cartoes da run e estavel (antes == depois da audit).
    bt = sqlite3.connect(BACKTEST_DB_PATH)
    antes = bt.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE run_id=? AND mercado='cartoes'",
        (RUN_ID,)).fetchone()[0]
    bt.close()
    auditar(BACKTEST_DB_PATH, DB_PATH, RUN_ID)
    bt = sqlite3.connect(BACKTEST_DB_PATH)
    depois = bt.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE run_id=? AND mercado='cartoes'",
        (RUN_ID,)).fetchone()[0]
    bt.close()
    assert antes == depois  # nenhuma previsao adicionada/removida


def test_auditoria_json_serializavel(tmp_path):
    # O resultado deve ser JSON-serializavel (sem objetos vivos).
    r = auditar(BACKTEST_DB_PATH, DB_PATH, RUN_ID)
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    r2 = json.loads(path.read_text(encoding="utf-8"))
    assert r2["nao_avaliaveis"] == 207