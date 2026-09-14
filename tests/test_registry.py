"""REGISTRO DE VALIDACAO DAS RECOMENDACOES - testes.

Valida (regra critica): o snapshot da recomendacao aprovada e
PERMANENTE e IMUTAVEL; somente a liquidacao posterior e atualizavel;
None/ausente nunca vira zero; recomendacao antiga nunca e duplicada.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from src.exceptions import NotFoundError, UserFacingError
from src.registry import (
    RegistroRecomendacoes,
    VERSAO_LIVE,
    VERSAO_PREJOGO,
    normalizar_resultado,
)


@pytest.fixture()
def reg(tmp_path):
    """Registro isolado por teste (nunca toca data/recomendacoes.db)."""
    return RegistroRecomendacoes(db_path=str(tmp_path / "registro.db"))


def _av_live(fixture_id=300, minuto=67, placar="0-2", prob=0.845,
             linha="Under 3.5 gols (total do jogo)", odd=None):
    """Avaliacao aprovada no formato real (atributos usados pelo store)."""
    return SimpleNamespace(
        fixture_id=fixture_id, mercado="gols", linha=linha, prob=prob,
        minuto=minuto, status="2H", placar=placar,
        competicao="Division Profesional - Clausura (Paraguay)",
        confianca=0.93, riscos=["gols: placar muda o estado a qualquer momento"],
        sustentacao={"atual_no_jogo": 2, "tempo_restante_min": 23,
                     "esperado_no_restante": 0.7},
        odd=odd,
    )


def _cand(n_home=7, n_away=8):
    return SimpleNamespace(
        snapshot=SimpleNamespace(
            home_team_name="Sportivo Luqueno",
            away_team_name="Sportivo Ameliano",
            collected_at="06/09/2026 19:53:33",
        ),
        historico={"games_home": [], "games_away": [],
                  "n_home": n_home, "n_away": n_away},
        benchmark_escanteios=None, benchmark_gols=None,
        h2h_stats=None, h2h_n=0,
    )


# ----------------------------------------------------------------------
# 1. Criacao do registro
# ----------------------------------------------------------------------
def test_criacao_do_registro(reg):
    rec_id = reg.registrar(
        fixture_id=1632000, tipo="live", mercado="gols",
        linha="Under 3.5 gols (total do jogo)", probabilidade=0.845,
        versao_analise=VERSAO_LIVE, competicao="Clausura (Paraguay)",
        mandante="Sportivo Luqueno", visitante="Sportivo Ameliano",
        minuto=67, status="2H", placar="0-2", confianca=0.93,
        amostra_n=7, snapshot_api_ts="06/09/2026 19:53:33",
    )
    assert rec_id >= 1
    rec = reg.obter(rec_id)
    assert rec["fixture_id"] == 1632000
    assert rec["tipo"] == "live"
    assert rec["versao_analise"] == VERSAO_LIVE
    assert rec["mercado"] == "gols"
    assert rec["linha"] == "Under 3.5 gols (total do jogo)"
    assert rec["probabilidade"] == pytest.approx(0.845)
    assert rec["minuto"] == 67
    assert rec["placar"] == "0-2"
    assert rec["criado_em"]  # America/Sao_Paulo preenchido automaticamente


# ----------------------------------------------------------------------
# 2. Persistencia apos reiniciar (processo novo, mesmo arquivo)
# ----------------------------------------------------------------------
def test_persistencia_apos_reiniciar(reg, tmp_path):
    rec_id = reg.registrar(
        fixture_id=1492360, tipo="live", mercado="gols",
        linha="Under 1.5 gols (total do jogo)", probabilidade=0.938,
        versao_analise=VERSAO_LIVE, mandante="Botafogo", visitante="Palmeiras",
        minuto=61, placar="0-0",
    )
    # "reinicia": nova instancia apontando o MESMO arquivo
    reg2 = RegistroRecomendacoes(db_path=str(tmp_path / "registro.db"))
    rec = reg2.obter(rec_id)
    assert rec["probabilidade"] == pytest.approx(0.938)
    assert rec["minuto"] == 61
    assert rec["placar"] == "0-0"


# ----------------------------------------------------------------------
# 3. Imutabilidade da previsao original (trigger em nivel de banco)
# ----------------------------------------------------------------------
def test_previsao_original_imutavel(reg, tmp_path):
    rec_id = reg.registrar(
        fixture_id=300, tipo="live", mercado="gols",
        linha="Under 3.5 gols (total do jogo)", probabilidade=0.845,
        versao_analise=VERSAO_LIVE, minuto=67, placar="0-2",
    )
    # qualquer UPDATE em campo da previsao => ABORT (mesmo SQL direto)
    with sqlite3.connect(str(tmp_path / "registro.db")) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE recomendacoes SET probabilidade = 0.99 WHERE id = ?",
                (rec_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE recomendacoes SET linha = 'Outra linha' WHERE id = ?",
                (rec_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE recomendacoes SET placar = '9-9' WHERE id = ?",
                (rec_id,),
            )
    # a liquidacao (campos permitidos) atualiza normalmente
    reg.registrar_resultado(rec_id, resultado_mercado="GANHA",
                            placar_final="0-2")
    rec = reg.obter(rec_id)
    assert rec["probabilidade"] == pytest.approx(0.845)  # intacta
    assert rec["resultado_mercado"] == "GANHA"


# ----------------------------------------------------------------------
# 4. Recomendacao live preserva minuto e placar do momento
# ----------------------------------------------------------------------
def test_live_preserva_minuto_e_placar(reg):
    rec_id, novo = reg.registrar_de_avaliacao(_av_live(), _cand())
    assert novo is True
    rec = reg.obter(rec_id)
    assert rec["tipo"] == "live"
    assert rec["minuto"] == 67
    assert rec["status"] == "2H"
    assert rec["placar"] == "0-2"  # placar NAQUELE momento, congelado
    assert rec["snapshot_api_ts"] == "06/09/2026 19:53:33"
    # depois da liquidacao, minuto/placar do momento seguem intactos
    reg.registrar_resultado(rec_id, resultado_mercado="PERDIDA",
                            placar_final="0-4")
    rec = reg.obter(rec_id)
    assert rec["minuto"] == 67 and rec["placar"] == "0-2"
    assert rec["placar_final"] == "0-4"


# ----------------------------------------------------------------------
# 5. Recomendacao pre-jogo (sem minuto/placar)
# ----------------------------------------------------------------------
def test_recomendacao_prejogo(reg):
    rec_id = reg.registrar(
        fixture_id=999001, tipo="prejogo", mercado="escanteios",
        linha="Over 9.5 escanteios (total do jogo)", probabilidade=0.72,
        versao_analise=VERSAO_PREJOGO, competicao="Serie A",
        mandante="Flamengo", visitante="Palmeiras",
    )
    rec = reg.obter(rec_id)
    assert rec["tipo"] == "prejogo"
    assert rec["versao_analise"] == VERSAO_PREJOGO
    assert rec["minuto"] is None     # pre-jogo: nao ha minuto
    assert rec["placar"] is None     # pre-jogo: nao ha placar
    assert rec["status"] is None


# ----------------------------------------------------------------------
# 6. Odd ausente permanece NULL
# ----------------------------------------------------------------------
def test_odd_ausente_permanece_null(reg):
    rec_id, _ = reg.registrar_de_avaliacao(_av_live(odd=None), _cand())
    rec = reg.obter(rec_id)
    assert rec["odd"] is None
    assert rec["bookmaker"] is None
    # odd 0.0 seria inventado: NULL e a unica representacao de ausencia
    assert rec["odd"] is not 0 and rec["odd"] is not 0.0


# ----------------------------------------------------------------------
# 7. None nunca vira zero
# ----------------------------------------------------------------------
def test_none_nunca_vira_zero(reg):
    rec_id = reg.registrar(
        fixture_id=111, tipo="prejogo", mercado="gols",
        linha="Under 3.5 gols (total do jogo)", probabilidade=0.80,
        versao_analise=VERSAO_PREJOGO,
    )
    rec = reg.obter(rec_id)
    for campo in ("competicao", "mandante", "visitante", "minuto", "status",
                  "placar", "equipe_lado", "odd", "bookmaker", "confianca",
                  "amostra_n", "dados_favoraveis",
                  "contradicoes_riscos", "snapshot_api_ts",
                  "placar_final", "stats_finais", "resultado_mercado"):
        assert rec[campo] is None, f"{campo} deveria ser NULL, nao zero"
        assert rec[campo] != 0 and rec[campo] != 0.0


# ----------------------------------------------------------------------
# 8. Consulta por data
# ----------------------------------------------------------------------
def test_consulta_por_data(reg):
    reg.registrar(
        fixture_id=1, tipo="live", mercado="gols", linha="L1",
        probabilidade=0.80, versao_analise=VERSAO_LIVE,
        criado_em="06/09/2026 19:14:55",
    )
    reg.registrar(
        fixture_id=2, tipo="live", mercado="gols", linha="L2",
        probabilidade=0.81, versao_analise=VERSAO_LIVE,
        criado_em="05/09/2026 20:00:00",
    )
    hoje = reg.listar(data="06/09/2026")
    assert [r["fixture_id"] for r in hoje] == [1]
    # formato alternativo ISO tambem aceito
    assert [r["fixture_id"] for r in reg.listar(data="2026-09-06")] == [1]
    assert [r["fixture_id"] for r in reg.listar()] == [1, 2]


# ----------------------------------------------------------------------
# 9. Consulta por fixture
# ----------------------------------------------------------------------
def test_consulta_por_fixture(reg):
    reg.registrar(
        fixture_id=1549768, tipo="live", mercado="gols", linha="L1",
        probabilidade=0.85, versao_analise=VERSAO_LIVE,
        criado_em="06/09/2026 19:14:55",
    )
    reg.registrar(
        fixture_id=1549768, tipo="live", mercado="gols", linha="L1",
        probabilidade=0.93, versao_analise=VERSAO_LIVE,
        criado_em="06/09/2026 19:36:49",  # recomendaca POSTERIOR do mesmo jogo
    )
    reg.registrar(
        fixture_id=1519481, tipo="live", mercado="gols", linha="L2",
        probabilidade=0.87, versao_analise=VERSAO_LIVE,
        criado_em="06/09/2026 19:14:58",
    )
    recs = reg.listar(fixture_id=1549768)
    assert len(recs) == 2  # dois MOMENTOS distintos = dois registros
    assert all(r["fixture_id"] == 1549768 for r in recs)
    assert reg.obter(recs[0]["id"])["probabilidade"] == pytest.approx(0.85)


# ----------------------------------------------------------------------
# 10. Liquidacao altera SOMENTE os campos de resultado
# ----------------------------------------------------------------------
def test_liquidacao_somente_campos_de_resultado(reg):
    rec_id, _ = reg.registrar_de_avaliacao(_av_live(), _cand())
    original = reg.obter(rec_id)

    rec = reg.registrar_resultado(
        rec_id, resultado_mercado="GANHA", placar_final="0-2",
        stats_finais={"escanteios": [5, 2], "gols": [0, 2]},
        nota="encerrado 0-2; under 3.5 gols bateu",
    )
    # previsao original intacta, campo a campo
    for campo in ("fixture_id", "criado_em", "tipo", "versao_analise",
                  "competicao", "mandante", "visitante", "minuto", "status",
                  "placar", "mercado", "linha", "equipe_lado", "odd",
                  "bookmaker", "probabilidade", "confianca", "amostra_n",
                  "dados_favoraveis", "contradicoes_riscos",
                  "snapshot_api_ts"):
        assert rec[campo] == original[campo], f"{campo} nao pode mudar"
    # somente liquidacao preenchida
    assert rec["resultado_mercado"] == "GANHA"
    assert rec["placar_final"] == "0-2"
    assert rec["stats_finais"]["gols"] == [0, 2]
    assert rec["resultado_registrado_em"]

    # correcao do resultado: valor ANTERIOR preservado na auditoria
    rec2 = reg.registrar_resultado(rec_id, resultado_mercado="PERDIDA",
                                    nota="correcao")
    auditoria = rec2["auditoria"]
    assert auditoria[-1]["valor_anterior"] == "GANHA"
    assert rec2["resultado_mercado"] == "PERDIDA"
    # e a previsao continua intacta
    assert rec2["probabilidade"] == original["probabilidade"]

    # nota de auditoria avulsa: anexa, nao sobrescreve
    rec3 = reg.anexar_auditoria(rec_id, "conferencia manual concluida")
    assert len(rec3["auditoria"]) == 3

    # id inexistente => erro claro, nunca silencioso
    with pytest.raises(NotFoundError):
        reg.registrar_resultado(99999, resultado_mercado="GANHA")


# ----------------------------------------------------------------------
# 11. Registros permanecem consultaveis apos reinicio
# ----------------------------------------------------------------------
def test_registros_consultaveis_apos_reinicio(reg, tmp_path):
    id1, _ = reg.registrar_de_avaliacao(_av_live(), _cand())
    id2 = reg.registrar(
        fixture_id=42, tipo="prejogo", mercado="escanteios",
        linha="Over 10.5 escanteios (total do jogo)", probabilidade=0.71,
        versao_analise=VERSAO_PREJOGO,
    )
    reg.registrar_resultado(id1, resultado_mercado="GANHA", placar_final="0-2")

    reg2 = RegistroRecomendacoes(db_path=str(tmp_path / "registro.db"))
    todos = reg2.listar()
    assert [r["id"] for r in todos] == sorted([id1, id2])
    assert reg2.obter(id1)["resultado_mercado"] == "GANHA"  # liquidacao persistiu
    assert reg2.obter(id2)["tipo"] == "prejogo"


# ----------------------------------------------------------------------
# 12. Recomendacao antiga nao e duplicada silenciosamente
# ----------------------------------------------------------------------
def test_dedupe_nao_duplica_recomendacao(reg):
    av, cand = _av_live(), _cand()
    id1, novo1 = reg.registrar_de_avaliacao(av, cand)
    assert novo1 is True
    # mesma varredura reexecutada (mesmo estado, mesma probabilidade):
    # MESMA recomendacao => mesmo registro, sem duplicar
    id2, novo2 = reg.registrar_de_avaliacao(av, cand)
    assert novo2 is False
    assert id2 == id1
    assert len(reg.listar()) == 1
    # estado DIFERENTE (minuto/placar/probabilidade novos) e outra
    # recomendacao real => novo registro legitimo
    id3, novo3 = reg.registrar_de_avaliacao(
        _av_live(minuto=73, placar="0-1", prob=0.83), cand
    )
    assert novo3 is True
    assert id3 != id1
    assert len(reg.listar()) == 2


# ----------------------------------------------------------------------
# Rotulos de resultado: canonicos e rejeicao clara
# ----------------------------------------------------------------------
def test_normalizar_resultado_aceita_variantes_e_rejeita():
    assert normalizar_resultado("ganha") == "GANHA"
    assert normalizar_resultado("MEIA VITORIA") == "MEIA VITÓRIA"
    assert normalizar_resultado("nao avaliavel") == "NÃO AVALIÁVEL"
    assert normalizar_resultado("não avaliável") == "NÃO AVALIÁVEL"
    with pytest.raises(UserFacingError):
        normalizar_resultado("aprovada")