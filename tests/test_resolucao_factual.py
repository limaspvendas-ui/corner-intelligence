"""Testes determinísticos da camada de resolução factual (Etapa 5F-E1).

Sem rede. Cobre os casos A-I da Seção 11 da 5F-E1:
A) primary NULL + fallback 1 + MATCHED => fallback utilizado
B) primary 0 + fallback 1 + MATCHED => CONFLITO_DE_FONTE; primary não sobrescrito
C) primary NULL + fallback 0 + MATCHED => zero válido do fallback
D) primary NULL + fallback 1 + AMBIGUOUS => fallback NÃO utilizado
E) primary NULL + fallback 1 + NOT_MATCHED => fallback NÃO utilizado
F) fallback não suporta o campo => NÃO utilizado
G) dois providers divergentes => preservar ambos + registrar conflito
H) proveniência completa
I) repetir resolução não corrompe histórico nem gera mutação silenciosa
"""
import json
import sqlite3

import pytest

from src.multifonte import NormalizedFact, ST_OK
from src.auditoria_multifonte import (
    ConflictRegistry, ReconciliationResult,
    REC_MATCHED, REC_AMBIGUOUS, REC_NOT_MATCHED,
)
from src.resolucao_factual import (
    ResolverFactual, FallbackCandidate, ResolutionOutcome,
    SOURCE_POLICY, FALLBACK_FIELDS, OPERATIONAL_FALLBACKS,
    PRIMARY_FACTUAL, FALLBACK_IDENTITY, FALLBACK_CARDS,
    RES_RESOLVIDO_FALLBACK, RES_RESOLVIDO_PRIMARY,
    RES_CONFLITO_DE_FONTE, RES_NULL_MANTIDO,
    RES_FALLBACK_BLOQUEADO_RECONCILIACAO, RES_FALLBACK_SEM_SUPORTE_CAMPO,
    _migrar_para_5fe1, _USER_VERSION_5FE1,
)


# ---- helpers ---------------------------------------------------------------
def _fact(provider, field, value, status=ST_OK, fixture_id="999",
          retrieved_at=1000.0, fixture_corner="853153"):
    return NormalizedFact(
        provider=provider, endpoint="/x", retrieved_at=retrieved_at,
        fixture_provider_id=fixture_id, fixture_corner_id=fixture_corner,
        field=field, normalized_value=value, status=status,
    )


def _rec(status, candidate_provider="apifootball_com",
         candidate_fixture_id="afc-1", confidence=0.9):
    return ReconciliationResult(
        status=status, canonical_fixture_id="853153",
        candidate_provider=candidate_provider,
        candidate_fixture_id=candidate_fixture_id,
        confidence=confidence, motivos=[],
    )


@pytest.fixture
def resolver(tmp_path):
    db = str(tmp_path / "test_resolucao.db")
    r = ResolverFactual(db_path=db, conflict_registry=ConflictRegistry())
    return r


# ---- A: primary NULL + fallback 1 + MATCHED => fallback utilizado ----------
def test_A_primary_null_fallback_preenche(resolver):
    primary = _fact("api_football", "red_cards", None)
    fb = FallbackCandidate(
        fact=_fact("apifootball_com", "red_cards", 1, fixture_id="afc-1"),
        reconciliation=_rec(REC_MATCHED),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    assert out.status == RES_RESOLVIDO_FALLBACK
    assert out.resolved_value == 1
    assert out.source_provider == "apifootball_com"
    assert out.is_fallback is True
    assert out.is_primary is False
    assert out.reconciliation_status == REC_MATCHED


# ---- B: primary 0 + fallback 1 + MATCHED => CONFLITO; primary mantido -----
def test_B_primary_zero_fallback_um_conflito(resolver):
    primary = _fact("api_football", "red_cards", 0)
    fb = FallbackCandidate(
        fact=_fact("apifootball_com", "red_cards", 1),
        reconciliation=_rec(REC_MATCHED),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    assert out.status == RES_CONFLITO_DE_FONTE
    # primary NÃO sobrescrito
    assert out.resolved_value == 0
    assert out.is_primary is True
    assert out.source_provider == "api_football"
    # ambos os valores preservados
    assert out.raw_values["api_football"] == 0
    assert out.raw_values["apifootball_com"] == 1
    # conflito registrado no registry in-memory
    assert len(resolver._registry.items()) == 1
    c = resolver._registry.items()[0]
    assert c.value_a == 0 and c.value_b == 1
    assert c.field == "red_cards"


# ---- C: primary NULL + fallback 0 + MATCHED => zero válido do fallback ----
def test_C_zero_valido_do_fallback(resolver):
    primary = _fact("api_football", "red_cards", None)
    fb = FallbackCandidate(
        fact=_fact("apifootball_com", "red_cards", 0),
        reconciliation=_rec(REC_MATCHED),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    assert out.status == RES_RESOLVIDO_FALLBACK
    # ZERO explícito do fallback é mantido como 0 — NUNCA virou None
    assert out.resolved_value == 0
    assert out.resolved_value is not None
    assert out.is_fallback is True


# ---- D: AMBIGUOUS => fallback NÃO utilizado -------------------------------
def test_D_ambiguous_bloqueia_fallback(resolver):
    primary = _fact("api_football", "red_cards", None)
    fb = FallbackCandidate(
        fact=_fact("apifootball_com", "red_cards", 1),
        reconciliation=_rec(REC_AMBIGUOUS, confidence=0.6),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    assert out.status == RES_NULL_MANTIDO
    assert out.resolved_value is None
    # rejeitado por reconciliação
    assert any(r["motivo"] == RES_FALLBACK_BLOQUEADO_RECONCILIACAO
               and r["reconciliation_status"] == REC_AMBIGUOUS
               for r in out.rejeitados)


# ---- E: NOT_MATCHED => fallback NÃO utilizado -----------------------------
def test_E_not_matched_bloqueia_fallback(resolver):
    primary = _fact("api_football", "red_cards", None)
    fb = FallbackCandidate(
        fact=_fact("apifootball_com", "red_cards", 1),
        reconciliation=_rec(REC_NOT_MATCHED, confidence=0.2),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    assert out.status == RES_NULL_MANTIDO
    assert out.resolved_value is None
    assert any(r["reconciliation_status"] == REC_NOT_MATCHED
               for r in out.rejeitados)


# ---- F: fallback não suporta o campo => NÃO utilizado --------------------
def test_F_fallback_sem_suporte_campo(resolver):
    # football_data_org NÃO suporta red_cards
    primary = _fact("api_football", "red_cards", None)
    fb = FallbackCandidate(
        fact=_fact("football_data_org", "red_cards", 1),
        reconciliation=_rec(REC_MATCHED, candidate_provider="football_data_org"),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    assert out.status == RES_NULL_MANTIDO
    assert out.resolved_value is None
    assert any(r["motivo"] == RES_FALLBACK_SEM_SUPORTE_CAMPO
               and r["provider"] == "football_data_org"
               for r in out.rejeitados)


# ---- G: dois providers divergentes => preservar ambos + conflito ---------
def test_G_divergentes_preserva_ambos(resolver):
    primary = _fact("api_football", "red_cards", 2)
    fb = FallbackCandidate(
        fact=_fact("apifootball_com", "red_cards", 5),
        reconciliation=_rec(REC_MATCHED),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    assert out.status == RES_CONFLITO_DE_FONTE
    # primary preservado (2); fallback (5) NÃO sobrescreve
    assert out.resolved_value == 2
    assert out.raw_values == {"api_football": 2, "apifootball_com": 5}
    # conflito durable persistido
    persisted = resolver.listar_conflitos("853153")
    assert len(persisted) == 1
    assert json.loads(persisted[0]["value_a"]) == 2
    assert json.loads(persisted[0]["value_b"]) == 5
    assert persisted[0]["field"] == "red_cards"


# ---- H: proveniência completa ---------------------------------------------
def test_H_proveniencia_completa(resolver):
    primary = _fact("api_football", "red_cards", None,
                    fixture_id="af-fix", retrieved_at=12345.0)
    fb_fact = _fact("apifootball_com", "red_cards", 1,
                    fixture_id="afc-fix", retrieved_at=67890.0)
    fb = FallbackCandidate(fact=fb_fact, reconciliation=_rec(REC_MATCHED))
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    # todos os campos de proveniência exigidos pela Seção 8
    assert out.field == "red_cards"
    assert out.resolved_value == 1
    assert out.source_provider == "apifootball_com"
    assert out.fixture_id_source == "afc-fix"
    assert out.fixture_internal == "853153"
    assert out.coleta_timestamp == "67890.0"
    assert out.is_fallback is True
    assert out.is_primary is False
    assert out.reconciliation_status == REC_MATCHED
    assert out.motivo is not None
    # dado cru original preservado (ambos providers)
    assert out.raw_values["api_football"] is None
    assert out.raw_values["apifootball_com"] == 1


# ---- I: repetir resolução não corrompe histórico --------------------------
def test_I_idempotente_nao_corrompe(resolver):
    primary = _fact("api_football", "red_cards", None)
    fb = FallbackCandidate(
        fact=_fact("apifootball_com", "red_cards", 1),
        reconciliation=_rec(REC_MATCHED),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    inserted1 = resolver.persistir(out)
    inserted2 = resolver.persistir(out)
    assert inserted1 is True      # primeira inserção
    assert inserted2 is False     # idempotente: mesmo hash, sem mutação
    rows = resolver.listar_resolucoes("853153")
    assert len(rows) == 1         # não duplicou
    # conflito também idempotente
    out_conf = resolver.resolver_campo(
        "853153", "red_cards", _fact("api_football", "red_cards", 0),
        [FallbackCandidate(fact=_fact("apifootball_com", "red_cards", 1),
                           reconciliation=_rec(REC_MATCHED))])
    n_before = len(resolver.listar_conflitos("853153"))
    # resolver de novo gera o mesmo conflito -> mesmo hash -> INSERT OR IGNORE
    resolver.resolver_campo(
        "853153", "red_cards", _fact("api_football", "red_cards", 0),
        [FallbackCandidate(fact=_fact("apifootball_com", "red_cards", 1),
                           reconciliation=_rec(REC_MATCHED))])
    n_after = len(resolver.listar_conflitos("853153"))
    assert n_before == n_after == 1


# ---- política de fontes (Seção 3) -----------------------------------------
def test_politica_fontes_papeis_oficiais():
    assert SOURCE_POLICY["api_football"] == PRIMARY_FACTUAL
    assert SOURCE_POLICY["football_data_org"] == FALLBACK_IDENTITY
    assert SOURCE_POLICY["apifootball_com"] == FALLBACK_CARDS
    assert SOURCE_POLICY["statsbomb_open"] == "HISTORICAL_AUDIT"
    assert SOURCE_POLICY["sportmonks"] == "PLAN_LIMITED"
    # 5Dollar / The Odds API NÃO são resolvidos em E1 (odds → E2)
    assert "five_dollar_football" not in OPERATIONAL_FALLBACKS
    assert "the_odds_api" not in OPERATIONAL_FALLBACKS


def test_football_data_nao_inventa_corners_cards():
    # football_data_org NÃO pode suprir corners/cards/shots
    campos = FALLBACK_FIELDS["football_data_org"]
    for proibido in ("corners", "red_cards", "yellow_cards", "shots", "possession"):
        assert proibido not in campos
    # só identidade/fixture/score
    assert {"home", "away", "kickoff", "status", "score_home", "score_away"} <= campos


# ---- NULL != ZERO (regra central, Seção 4) --------------------------------
def test_null_distinto_de_zero_nao_conflita(resolver):
    # primary NULL + fallback 0 => NÃO é conflito; zero válido é aceito
    primary = _fact("api_football", "red_cards", None)
    fb = FallbackCandidate(
        fact=_fact("apifootball_com", "red_cards", 0),
        reconciliation=_rec(REC_MATCHED),
    )
    out = resolver.resolver_campo("853153", "red_cards", primary, [fb])
    assert out.status != RES_CONFLITO_DE_FONTE
    assert out.resolved_value == 0
    assert out.resolved_value is not None  # não virou None


# ---- isolamento: motor não importa resolucao_factual ----------------------
def test_motor_nao_importa_resolucao_factual():
    import os
    motor = ["analysis", "politica_aprovacao", "policy", "settlement",
             "calibration", "backtest", "prejogo_opportunity", "live_pressure",
             "odds", "identity", "ao_vivo", "app", "odds_coleta"]
    base = os.path.join("src")
    hits = []
    for m in motor:
        path = os.path.join(base, m + ".py")
        if not os.path.exists(path):
            continue
        txt = open(path, encoding="utf-8", errors="ignore").read()
        if "resolucao_factual" in txt:
            hits.append(m)
    assert hits == [], f"motor importa resolucao_factual: {hits}"


# ---- migração aditiva + preserva histórico --------------------------------
def test_migracao_cria_tabelas_e_preserva_historico(tmp_path):
    db = str(tmp_path / "hist.db")
    conn = sqlite3.connect(db)
    # simula tabelas existentes com dados
    conn.executescript(
        "CREATE TABLE api_cache (k TEXT); INSERT INTO api_cache VALUES ('x');"
        "CREATE TABLE odds_snapshot_history (id INTEGER); "
        "INSERT INTO odds_snapshot_history VALUES (1);")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    # aplica migração 5F-E1
    conn = sqlite3.connect(db)
    _migrar_para_5fe1(conn)
    conn.close()
    conn = sqlite3.connect(db)
    uv = conn.execute("PRAGMA user_version").fetchone()[0]
    tabs = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    ac = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
    osh = conn.execute("SELECT COUNT(*) FROM odds_snapshot_history").fetchone()[0]
    conn.close()
    assert uv == _USER_VERSION_5FE1
    assert {"factual_resolution", "source_conflict"} <= tabs
    assert ac == 1 and osh == 1  # histórico preservado