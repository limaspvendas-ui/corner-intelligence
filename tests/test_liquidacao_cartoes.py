"""TESTES da LIQUIDACAO AUTOMATICA de CARTOES (total over/under).

Bloco implementado nesta etapa (prioridade: liquidação correta):
a familia cartoes passa a ter LIQUIDACAO VALIDADA - total em PONTOS
pela convencao DECLARADA NA PROPRIA LINHA congelada pelo motor live
("cartoes (amarelo=1, vermelho=2)"), lido da estatistica FINAL da API.

Nenhuma logica de gols/escanteios/resultado/registro e alterada.

Valida:
    - parse da linha do motor (formato exato congelado);
    - linha de meio ponto: GANHA / PERDIDA nas duas direcoes;
    - linha inteira: DEVOLVIDA;
    - vermelho conta 2 pontos (convencao declarada);
    - estatistica ausente => NAO AVALIAVEL, nunca zero;
    - linha SEM convencao declarada => NAO AVALIAVEL (pesos nunca
      assumidos);
    - jogo nao encerrado => PENDENTE (liquidacao so depois do fim real);
    - previsao original IMUTAVEL + trilha de auditoria;
    - dedupe do registro: mesma recomendacao nunca duplicada.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.registry import RegistroRecomendacoes
from src.settlement import (
    NAO_AVALIAVEL,
    PENDENTE,
    _parse_linha,
    liquidar_pendentes,
)

# Linha EXATA produzida pelo motor live (deep_dive -> _avaliar_total
# com unidade "cartoes (amarelo=1, vermelho=2)")
_LINHA_MOTOR = (
    "Over 5.5 cartoes (amarelo=1, vermelho=2) (total do jogo)"
)
_VERSAO = "live-op-2.3-familia-resultado"


# ----------------------------------------------------------------------
# Helpers (mesmo padrao do teste de modo observacao)
# ----------------------------------------------------------------------
class _ClientFixo:
    """Cliente fake: responde /fixtures e /fixtures/statistics."""

    def __init__(self, fixture_raw, statistics_raw):
        self._fixture = fixture_raw
        self._stats = statistics_raw

    def get(self, endpoint, params=None):
        if endpoint == "/fixtures":
            return self._fixture
        if endpoint == "/fixtures/statistics":
            return self._stats
        raise AssertionError(f"endpoint inesperado: {endpoint}")


def _fixture_raw(status="FT", gh=1, ga=1, fid=1000):
    return [{
        "fixture": {"id": fid, "date": "2026-09-06T16:00:00-03:00",
                     "status": {"short": status, "elapsed": 90}},
        "league": {"id": 71, "name": "Serie A", "season": 2026},
        "teams": {"home": {"id": 1, "name": "A"},
                  "away": {"id": 2, "name": "B"}},
        "goals": {"home": gh, "away": ga},
    }]


def _stats_cartoes(yc_home=2, yc_away=2, rc_home=0, rc_away=0,
                   sem_vermelho=False, amarelo_home_nulo=False):
    """Blocos FINAIS de estatistica: amarelos e vermelhos por equipe."""
    def _valor(v):
        return None if v is None else str(v)

    def bloco(tid, yc, rc):
        stats = [{"type": "Fouls", "value": "10"}]
        if yc is not SEM:
            stats.append({"type": "Yellow Cards", "value": _valor(yc)})
        if rc is not SEM and not sem_vermelho:
            stats.append({"type": "Red Cards", "value": _valor(rc)})
        return {"team": {"id": tid, "name": f"T{tid}"},
                "statistics": stats}

    yc_h = None if amarelo_home_nulo else yc_home
    return [bloco(1, yc_h, rc_home), bloco(2, yc_away, rc_away)]


SEM = object()  # marcador: bloco de amarelos ausente da resposta


def _registrar_cartao(reg, fixture_id, linha, prob=0.78, minuto=63,
                      placar="1-1", conf=0.81):
    return reg.registrar(
        fixture_id=fixture_id, tipo="live", mercado="cartoes",
        linha=linha, probabilidade=prob, versao_analise=_VERSAO,
        minuto=minuto, status="2H", placar=placar, confianca=conf,
    )


# ----------------------------------------------------------------------
# Formato da linha do motor
# ----------------------------------------------------------------------
def test_parse_linha_cartoes_do_motor():
    """A linha congelada pelo motor live casa com a regra de total."""
    assert _parse_linha(_LINHA_MOTOR) == ("Over", 5.5, "cartoes")
    assert _parse_linha(
        "Under 6.5 cartoes (amarelo=1, vermelho=2) (total do jogo)"
    ) == ("Under", 6.5, "cartoes")


# ----------------------------------------------------------------------
# Meio ponto: GANHA / PERDIDA nas duas direcoes
# ----------------------------------------------------------------------
def test_meio_ponto_ganha_e_perde(tmp_path):
    # amarelos 2-2 (4 pontos) + 1 vermelho no mandante (2) => total 6
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    id_over_ganha = _registrar_cartao(reg, 1000, _LINHA_MOTOR)
    id_under_perde = _registrar_cartao(
        reg, 1000, "Under 5.5 cartoes (amarelo=1, vermelho=2) (total do jogo)"
    )
    id_over_perde = _registrar_cartao(
        reg, 1000, "Over 6.5 cartoes (amarelo=1, vermelho=2) (total do jogo)"
    )
    id_under_ganha = _registrar_cartao(
        reg, 1000, "Under 6.5 cartoes (amarelo=1, vermelho=2) (total do jogo)"
    )
    client = _ClientFixo(_fixture_raw(), _stats_cartoes(rc_home=1))
    report = liquidar_pendentes(client, reg)

    por_id = {i["id"]: i for i in report["itens"]}
    assert por_id[id_over_ganha]["situacao"] == "GANHA"    # 6 > 5.5
    assert por_id[id_under_perde]["situacao"] == "PERDIDA"  # 6 > 5.5
    assert por_id[id_over_perde]["situacao"] == "PERDIDA"   # 6 < 6.5
    assert por_id[id_under_ganha]["situacao"] == "GANHA"   # 6 < 6.5


# ----------------------------------------------------------------------
# Vermelho conta 2 pontos (convencao declarada na linha)
# ----------------------------------------------------------------------
def test_vermelho_conta_dois_pontos(tmp_path):
    # mesmos amarelos 3-2 (5 pontos): sem vermelho total 5 PERDE;
    # com 1 vermelho total 7 GANHA - o vermelho vale exatamente 2
    reg_sem = RegistroRecomendacoes(db_path=str(tmp_path / "sem.db"))
    rid_sem = _registrar_cartao(reg_sem, 1000, _LINHA_MOTOR)
    liquidar_pendentes(
        _ClientFixo(_fixture_raw(), _stats_cartoes(3, 2, 0, 0)), reg_sem
    )
    assert reg_sem.obter(rid_sem)["resultado_mercado"] == "PERDIDA"

    reg_com = RegistroRecomendacoes(db_path=str(tmp_path / "com.db"))
    rid_com = _registrar_cartao(reg_com, 1000, _LINHA_MOTOR)
    liquidar_pendentes(
        _ClientFixo(_fixture_raw(), _stats_cartoes(3, 2, 1, 0)), reg_com
    )
    rec = reg_com.obter(rid_com)
    assert rec["resultado_mercado"] == "GANHA"
    # componentes crus preservados para auditoria (3+2=5 amarelos,
    # 1 vermelho: 5 + 2*1 = 7)
    assert rec["stats_finais"]["amarelos"] == [3, 2]
    assert rec["stats_finais"]["vermelhos"] == [1, 0]
    assert rec["stats_finais"]["convencao"] == (
        "amarelo=1, vermelho=2 (declarada na linha congelada)"
    )


# ----------------------------------------------------------------------
# Linha inteira: DEVOLVIDA
# ----------------------------------------------------------------------
def test_linha_inteira_devolvida(tmp_path):
    # total 6 (amarelos 2-2 + 1 vermelho): linha inteira 6 empata
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    id_over = _registrar_cartao(
        reg, 1000, "Over 6 cartoes (amarelo=1, vermelho=2) (total do jogo)"
    )
    id_under = _registrar_cartao(
        reg, 1000, "Under 6 cartoes (amarelo=1, vermelho=2) (total do jogo)"
    )
    client = _ClientFixo(_fixture_raw(), _stats_cartoes(rc_home=1))
    report = liquidar_pendentes(client, reg)

    por_id = {i["id"]: i for i in report["itens"]}
    assert por_id[id_over]["situacao"] == "DEVOLVIDA"   # 6 == 6
    assert por_id[id_under]["situacao"] == "DEVOLVIDA"
    assert reg.obter(id_over)["resultado_mercado"] == "DEVOLVIDA"


# ----------------------------------------------------------------------
# Dados ausentes: NAO AVALIAVEL, nunca zero
# ----------------------------------------------------------------------
def test_estatistica_ausente_nao_avaliavel_nunca_zero(tmp_path):
    # (a) resposta SEM nenhum bloco de estatisticas
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "a.db"))
    rid = _registrar_cartao(reg, 1000, _LINHA_MOTOR)
    report = liquidar_pendentes(_ClientFixo(_fixture_raw(), []), reg)
    assert report["itens"][0]["situacao"] == NAO_AVALIAVEL
    rec = reg.obter(rid)
    assert rec["stats_finais"]["amarelos"] == [None, None]
    assert rec["stats_finais"]["vermelhos"] == [None, None]

    # (b) amarelos presentes, bloco de VERMELHOS ausente: nunca vira 0
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "b.db"))
    rid = _registrar_cartao(reg, 1000, _LINHA_MOTOR)
    report = liquidar_pendentes(
        _ClientFixo(_fixture_raw(), _stats_cartoes(4, 2, 1, 0,
                                                   sem_vermelho=True)),
        reg,
    )
    assert report["itens"][0]["situacao"] == NAO_AVALIAVEL
    rec = reg.obter(rid)
    assert rec["stats_finais"]["amarelos"] == [4, 2]
    assert rec["stats_finais"]["vermelhos"] == [None, None]

    # (c) valor de amarelo nulo para o mandante: ausente, nunca zero
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "c.db"))
    rid = _registrar_cartao(reg, 1000, _LINHA_MOTOR)
    report = liquidar_pendentes(
        _ClientFixo(_fixture_raw(),
                    _stats_cartoes(amarelo_home_nulo=True)),
        reg,
    )
    assert report["itens"][0]["situacao"] == NAO_AVALIAVEL
    assert reg.obter(rid)["stats_finais"]["amarelos"] == [None, 2]


# ----------------------------------------------------------------------
# Linha SEM convencao declarada: pesos nunca assumidos
# ----------------------------------------------------------------------
def test_linha_sem_convencao_declarada_nao_avaliavel(tmp_path):
    """Estatistica existir NAO basta: sem a convencao declarada na linha
    congelada, o total em pontos nao pode ser assumido."""
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rid = _registrar_cartao(
        reg, 1000, "Over 4.5 cartoes (total do jogo)"
    )
    client = _ClientFixo(_fixture_raw(), _stats_cartoes(4, 2, 1, 0))
    report = liquidar_pendentes(client, reg)

    item = report["itens"][0]
    assert item["situacao"] == NAO_AVALIAVEL
    assert "convencao" in item["motivo"]
    rec = reg.obter(rid)
    assert rec["resultado_mercado"] == NAO_AVALIAVEL
    assert "nunca sao assumidos" in rec["auditoria"][-1]["nota"]


# ----------------------------------------------------------------------
# Jogo nao encerrado: PENDENTE (nunca liquidado por antecipacao)
# ----------------------------------------------------------------------
def test_jogo_nao_encerrado_fica_pendente(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rid = _registrar_cartao(reg, 2000, _LINHA_MOTOR)
    client = _ClientFixo(_fixture_raw(status="2H", fid=2000),
                         _stats_cartoes(rc_home=1))
    report = liquidar_pendentes(client, reg)

    item = report["itens"][0]
    assert item["situacao"] == PENDENTE
    assert "ainda nao encerrado" in item["motivo"]
    assert reg.obter(rid)["resultado_mercado"] is None


# ----------------------------------------------------------------------
# Previsao original imutavel + trilha de auditoria
# ----------------------------------------------------------------------
def test_previsao_original_intacta_na_liquidacao(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))
    rid = _registrar_cartao(reg, 1000, _LINHA_MOTOR, prob=0.78,
                            minuto=63, placar="1-1", conf=0.81)
    antes = reg.obter(rid)
    client = _ClientFixo(_fixture_raw(), _stats_cartoes(rc_home=1))
    liquidar_pendentes(client, reg)

    depois = reg.obter(rid)
    for campo in ("fixture_id", "criado_em", "tipo", "versao_analise",
                  "mercado", "linha", "probabilidade", "confianca",
                  "minuto", "placar", "status", "snapshot_api_ts"):
        assert depois[campo] == antes[campo], f"{campo} nao pode mudar"
    assert depois["resultado_mercado"] == "GANHA"   # 6 > 5.5
    assert depois["placar_final"] == "1-1"
    assert depois["auditoria"][-1]["acao"] == "resultado registrado"
    # o trigger de imutabilidade segue ativo para a previsao
    import sqlite3
    with sqlite3.connect(str(tmp_path / "reg.db")) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE recomendacoes SET probabilidade = 0.99 "
                "WHERE id = ?",
                (rid,),
            )


# ----------------------------------------------------------------------
# Dedupe do registro: mesma recomendacao nunca duplicada
# ----------------------------------------------------------------------
def test_registro_cartoes_sem_duplicacao(tmp_path):
    reg = RegistroRecomendacoes(db_path=str(tmp_path / "reg.db"))

    av = SimpleNamespace(
        fixture_id=300, mercado="cartoes", prob=0.78, linha=_LINHA_MOTOR,
        minuto=63, status="2H", placar="1-1",
        competicao="Serie A (Brazil)", confianca=0.81,
        riscos=["convencao de contagem da casa nao informada pela fonte; "
                "estimativa assume amarelo=1, vermelho=2"],
        sustentacao={"atual_no_jogo": 4, "tempo_restante_min": 27,
                     "esperado_no_restante": 1.2,
                     "equipe_lado": None},
        odd=None,
    )
    game = SimpleNamespace(
        played_at_home=True, yellow_for=2, yellow_against=3,
        red_for=0, red_against=1, goals_for=1, goals_against=1,
        corners_for=5, corners_against=5, corners_total=10,
    )
    cand = SimpleNamespace(
        snapshot=SimpleNamespace(
            fixture_id=300, home_team_name="Corinthians",
            away_team_name="Palmeiras",
            collected_at="07/09/2026 10:00:00",
        ),
        historico={"games_home": [game], "games_away": [game],
                   "n_home": 10, "n_away": 10},
    )

    rec_id, novo = reg.registrar_de_avaliacao(av, cand, tipo="live",
                                             versao_analise=_VERSAO)
    assert novo is True
    rec = reg.obter(rec_id)
    assert rec["mercado"] == "cartoes" and rec["linha"] == _LINHA_MOTOR
    assert rec["minuto"] == 63 and rec["placar"] == "1-1"
    assert rec["odd"] is None and rec["bookmaker"] is None

    # reescaneio IDENTICO: dedupe, nunca segunda recomendacao
    rec_id2, novo2 = reg.registrar_de_avaliacao(av, cand, tipo="live",
                                                versao_analise=_VERSAO)
    assert novo2 is False and rec_id2 == rec_id
    assert len(reg.listar(fixture_id=300, tipo="live")) == 1