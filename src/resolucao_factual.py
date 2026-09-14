"""Etapa 5F-E1 — Fundação do fallback factual controlado.

Camada de **RESOLUÇÃO field-level** para dados factuais multi-provider.
Ciclo: **COLETAR / NORMALIZAR / RESOLVER / REGISTRAR** — **NÃO altera o motor
de decisão**. Nenhum módulo decisório (analysis, policy, settlement,
calibration, backtest, prejogo_opportunity, live_pressure, odds, identity,
ao_vivo, app, odds_coleta) importa este módulo.

Reuso direto da infraestrutura 5F-D/5F-D2:
- ``NormalizedFact`` (src/multifonte.py) — moeda factual (provider,
  fixture_corner_id, field, normalized_value, status, motivo).
- ``reconciliar_fixture`` + ``ReconciliationResult`` (src/auditoria_multifonte.py)
  — portão MATCHED/AMBIGUOUS/NOT_MATCHED (thresholds 0.85/0.5).
- ``ConflictRegistry`` + ``ConflictRecord`` — append-only in-memory; neste
  módulo os conflitos são **persistidos** na tabela durable ``source_conflict``.

Regras centrais (permanentes):
1. **NULL != ZERO**: ``None`` = dado ausente/desconhecido; ``0`` = valor factual
   explícito. Nunca ``None`` → ``0`` automaticamente; nunca ausência → zero.
2. **Fallback só preenche quando reconciliação == MATCHED.** AMBIGUOUS ou
   NOT_MATCHED => o fallback NÃO preenche nenhum campo (apenas registra motivo).
3. **Primary explícito (incl. 0) NUNCA é sobrescrito por fallback.**
   Divergência primary≠fallback => **CONFLITO_DE_FONTE**; preserva os dois
   valores originais; primary permanece.
4. **Proveniência completa**: todo valor resolvido registra valor, campo,
   provider original, fixture_id da fonte, fixture interno, timestamp da
   coleta, primário vs fallback, reconciliation_status, motivo. O dado cru
   original nunca é perdido (``raw_values``).
5. **Persistência append-only e idempotente**: tabelas ``factual_resolution`` e
   ``source_conflict`` com hash determinístico + UNIQUE + INSERT OR IGNORE.
   Repetir a mesma resolução não corrompe histórico nem gera mutação silenciosa.

Política oficial de fontes (Seção 3 da 5F-E1):
- PRIMARY_FACTUAL: ``api_football`` (fixture, times, kickoff, status, score,
  estatísticas, corners, cards quando disponíveis).
- FALLBACK_IDENTITY: ``football_data_org`` — somente identidade/times/kickoff/
  status/score. NUNCA corners/cards/shots/estatísticas.
- FALLBACK_CARDS: ``apifootball_com`` — somente campos de cartões, especialmente
  quando api_football retorna NULL. Não substitui valor factual explícito.
- ``statsbomb_open``: histórico/auditoria — NÃO fallback operacional live.
- ``sportmonks``: auditado/limitado pelo plano — NÃO operacional nesta etapa.
- ODDS: NÃO resolvidas nesta E1 (5Dollar / The Odds API → próxima subetapa).
"""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass, field as _dc_field, asdict
from pathlib import Path
from typing import Any

from src.multifonte import NormalizedFact, ST_OK, ST_MISSING
from src.auditoria_multifonte import (
    ConflictRegistry, ConflictRecord,
    ReconciliationResult, REC_MATCHED, REC_AMBIGUOUS, REC_NOT_MATCHED,
)

# ---------------------------------------------------------------------------
# DB_PATH (fallback local; não importa src/config para manter a camada
# isolada dos módulos decisórios — mesmo padrão de auditoria_multifonte).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - depende do ambiente
    from src.config import DB_PATH as _DB_PATH  # type: ignore
    DB_PATH = str(_DB_PATH)
except Exception:  # pragma: no cover
    DB_PATH = os.path.join("data", "corner_intelligence.db")

# ---------------------------------------------------------------------------
# Papel oficial das fontes (Seção 3)
# ---------------------------------------------------------------------------
PRIMARY_FACTUAL = "PRIMARY_FACTUAL"
FALLBACK_IDENTITY = "FALLBACK_IDENTITY"
FALLBACK_CARDS = "FALLBACK_CARDS"
HISTORICAL_AUDIT = "HISTORICAL_AUDIT"
PLAN_LIMITED = "PLAN_LIMITED"
NOT_OPERATIONAL = "NOT_OPERATIONAL"

SOURCE_POLICY: dict[str, str] = {
    "api_football": PRIMARY_FACTUAL,
    "football_data_org": FALLBACK_IDENTITY,
    "apifootball_com": FALLBACK_CARDS,
    "statsbomb_open": HISTORICAL_AUDIT,
    "sportmonks": PLAN_LIMITED,
    # the_odds_api / five_dollar_football: ODDS — não resolvidos nesta E1
}

# Campos que cada fallback OPERACIONAL pode suprir (Seção 3 — escopo restrito).
# Providers não-listados aqui não são fallback operacional nesta etapa.
FALLBACK_FIELDS: dict[str, frozenset[str]] = {
    "football_data_org": frozenset({
        "home", "away", "kickoff", "status", "score_home", "score_away",
    }),
    "apifootball_com": frozenset({"red_cards", "yellow_cards"}),
}
OPERATIONAL_FALLBACKS: frozenset[str] = frozenset(FALLBACK_FIELDS.keys())

# Ordem de prioridade para preencher NULL (primeiro = preferido).
# Por domínio de campo só há um fallback operacional em E1; a lista existe
# para determinismo quando múltiplos suportam o mesmo campo no futuro.
FALLBACK_PRIORITY: list[str] = ["apifootball_com", "football_data_org"]

# ---------------------------------------------------------------------------
# Status de resolução
# ---------------------------------------------------------------------------
RES_RESOLVIDO_FALLBACK = "RESOLVIDO_FALLBACK"
RES_RESOLVIDO_PRIMARY = "RESOLVIDO_PRIMARY"
RES_NULL_MANTIDO = "NULL_MANTIDO"
RES_CONFLITO_DE_FONTE = "CONFLITO_DE_FONTE"
RES_FALLBACK_BLOQUEADO_RECONCILIACAO = "FALLBACK_BLOQUEADO_RECONCILIACAO"
RES_FALLBACK_SEM_SUPORTE_CAMPO = "FALLBACK_SEM_SUPORTE_CAMPO"
RES_SEM_FALLBACK = "SEM_FALLBACK"

CONFLITO_NATUREZA_VALOR = "valor_divergente"


# ---------------------------------------------------------------------------
# Valor factual: distingue NULL (None) de ZERO (0) explicitamente.
# ---------------------------------------------------------------------------
def _is_null(v: Any) -> bool:
    """NULL = dado ausente/desconhecido (None)."""
    return v is None


def _is_explicit(v: Any) -> bool:
    """Valor factual explícito — qualquer valor não-None, INCLUINDO 0."""
    return v is not None


# ---------------------------------------------------------------------------
# Outcome da resolução field-level (Seções 6 + 8)
# ---------------------------------------------------------------------------
@dataclass
class ResolutionOutcome:
    """Resultado da resolução de UM campo de UM fixture interno."""
    fixture_internal: str
    field: str
    status: str                      # RES_*
    resolved_value: Any              # valor resolvido (None se NULL mantido)
    source_provider: str | None      # quem forneceu o valor resolvido
    is_fallback: bool
    is_primary: bool
    reconciliation_status: str | None   # MATCHED/AMBIGUOUS/NOT_MATCHED/None
    motivo: str | None
    coleta_timestamp: str | None
    fixture_id_source: str | None    # fixture_id do provider fonte
    raw_values: dict[str, Any] = _dc_field(default_factory=dict)
    # providers fallback considerados mas NÃO utilizados (motivo por provider)
    rejeitados: list[dict[str, Any]] = _dc_field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FallbackCandidate:
    """Um fato de fallback + sua reconciliação contra o fixture canônico."""
    fact: NormalizedFact
    reconciliation: ReconciliationResult | None

    @property
    def provider(self) -> str:
        return self.fact.provider

    @property
    def value(self) -> Any:
        return self.fact.normalized_value

    @property
    def status_flag(self) -> str:
        return self.fact.status


# ---------------------------------------------------------------------------
# Migração aditiva (user_version 2 → 3). Padrão de odds_coleta.py.
# ---------------------------------------------------------------------------
_USER_VERSION_5FE1 = 3

_SCHEMA_5FE1 = """
CREATE TABLE IF NOT EXISTS factual_resolution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resolution_hash TEXT NOT NULL UNIQUE,
    fixture_internal TEXT NOT NULL,
    field TEXT NOT NULL,
    resolved_value TEXT,
    source_provider TEXT NOT NULL,
    is_fallback INTEGER NOT NULL,
    is_primary INTEGER NOT NULL,
    reconciliation_status TEXT,
    motivo TEXT,
    coleta_timestamp TEXT,
    fixture_id_source TEXT,
    raw_values TEXT NOT NULL,
    registrado_em REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_factual_res_fixture
    ON factual_resolution (fixture_internal, field);

CREATE TABLE IF NOT EXISTS source_conflict (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conflict_hash TEXT NOT NULL UNIQUE,
    fixture_internal TEXT NOT NULL,
    field TEXT NOT NULL,
    provider_a TEXT NOT NULL,
    value_a TEXT,
    provider_b TEXT NOT NULL,
    value_b TEXT,
    timestamp_a TEXT,
    timestamp_b TEXT,
    reconciliation_status TEXT,
    motivo TEXT,
    natureza TEXT NOT NULL,
    registrado_em REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_conflict_fixture
    ON source_conflict (fixture_internal, field);
"""


def _migrar_para_5fe1(conn: sqlite3.Connection) -> None:
    """Migração aditiva 5F-E1: cria factual_resolution + source_conflict.

    Idempotente: se user_version >= 3, não faz nada. Caso contrário, executa
    o schema (CREATE TABLE IF NOT EXISTS — idempotente) e define
    user_version = 3. Não toca em tabelas existentes (api_cache,
    odds_snapshot_history, validated_teams, recomendacoes). As novas tabelas
    são append-only.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA user_version")
    uv = cur.fetchone()[0]
    if uv is not None and uv >= _USER_VERSION_5FE1:
        return
    # executescript faz COMMIT automático de transação pendente; o DDL é
    # idempotente (IF NOT EXISTS), seguro fora de transação explícita.
    conn.executescript(_SCHEMA_5FE1)
    conn.execute(f"PRAGMA user_version = {_USER_VERSION_5FE1}")
    conn.commit()


# ---------------------------------------------------------------------------
# Hash determinístico (idempotência — Seção 9 + caso I)
# ---------------------------------------------------------------------------
def _hash_resolution(fixture_internal: str, field: str,
                     source_provider: str, coleta_timestamp: str | None,
                     resolved_value: Any) -> str:
    """Hash estável da resolução. Repetir a mesma resolução => mesmo hash."""
    payload = json.dumps(
        {"f": fixture_internal, "c": field, "s": source_provider,
         "t": coleta_timestamp, "v": resolved_value},
        sort_keys=True, default=str, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_conflict(fixture_internal: str, field: str,
                   provider_a: str, value_a: Any,
                   provider_b: str, value_b: Any,
                   reconciliation_status: str | None) -> str:
    payload = json.dumps(
        {"f": fixture_internal, "c": field,
         "pa": provider_a, "va": value_a,
         "pb": provider_b, "vb": value_b,
         "r": reconciliation_status},
        sort_keys=True, default=str, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _encode_value(v: Any) -> str:
    """Serializa valor preservando None vs 0 vs tipos (JSON)."""
    return json.dumps(v, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------
class ResolverFactual:
    """Resolver field-level isolado do motor.

    Uso típico:
        resolver = ResolverFactual()
        outcome = resolver.resolver_campo(
            fixture_internal="853153",
            field="red_cards",
            primary_fact=fact_af,            # api_football
            fallback_candidates=[cand_afc],  # apifootball_com + reconciliação
        )
        resolver.persistir(outcome)        # append-only, idempotente

    Nenhum método desta classe altera settlement/backtest/recomendações.
    A persistência é append-only com INSERT OR IGNORE (idempotente).
    """

    def __init__(self, db_path: str | None = None,
                 conflict_registry: ConflictRegistry | None = None) -> None:
        self.db_path = db_path or DB_PATH
        self._registry = conflict_registry  # opcional, in-memory (reuso)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Aplica a migração 5F-E1 (idempotente). Não altera histórico."""
        try:
            conn = sqlite3.connect(self.db_path)
            _migrar_para_5fe1(conn)
            conn.close()
        except Exception:
            # Em testes sem DB real, ignora — a resolução em memória funciona.
            pass

    # ---- núcleo field-level ------------------------------------------------
    def resolver_campo(
        self,
        fixture_internal: str,
        field: str,
        primary_fact: NormalizedFact | None,
        fallback_candidates: list[FallbackCandidate],
    ) -> ResolutionOutcome:
        """Resolve UM campo. Ver regras nas docstrings do módulo.

        - primary_fact: fato da fonte PRIMÁRIA (api_football). None se ausente.
        - fallback_candidates: fatos de fallback + reconciliação (cada um).
        """
        primary_value = primary_fact.normalized_value if primary_fact else None
        raw_values: dict[str, Any] = {}
        if primary_fact is not None:
            raw_values[primary_fact.provider] = primary_value

        rejeitados: list[dict[str, Any]] = []

        # Filtrar candidatos válidos: suporta o campo + MATCHED + tem valor.
        valid_fallbacks: list[FallbackCandidate] = []
        for cand in fallback_candidates:
            prov = cand.provider
            # Case F: fallback não suporta o campo
            if prov not in FALLBACK_FIELDS or field not in FALLBACK_FIELDS.get(prov, frozenset()):
                rejeitados.append({
                    "provider": prov, "motivo": RES_FALLBACK_SEM_SUPORTE_CAMPO,
                    "field": field,
                })
                continue
            raw_values[prov] = cand.value
            rec_status = cand.reconciliation.status if cand.reconciliation else None
            # Cases D, E: reconciliação não-MATCHED => fallback NÃO utilizado
            if rec_status != REC_MATCHED:
                rejeitados.append({
                    "provider": prov,
                    "motivo": RES_FALLBACK_BLOQUEADO_RECONCILIACAO,
                    "reconciliation_status": rec_status,
                })
                continue
            # Fato sem valor explícito (NULL) não oferece nada
            if _is_null(cand.value) or cand.status_flag != ST_OK:
                rejeitados.append({
                    "provider": prov, "motivo": RES_SEM_FALLBACK,
                    "reconciliation_status": rec_status,
                })
                continue
            valid_fallbacks.append(cand)

        # ---- Primary NULL: fallback pode preencher (Cases A, C) ----------
        if _is_null(primary_value):
            if not valid_fallbacks:
                return ResolutionOutcome(
                    fixture_internal=fixture_internal, field=field,
                    status=RES_NULL_MANTIDO, resolved_value=None,
                    source_provider=(primary_fact.provider if primary_fact else "api_football"),
                    is_fallback=False, is_primary=True,
                    reconciliation_status=None,
                    motivo="primary NULL e nenhum fallback valido MATCHED",
                    coleta_timestamp=(primary_fact.retrieved_at if primary_fact else None),
                    fixture_id_source=(primary_fact.fixture_provider_id if primary_fact else None),
                    raw_values=raw_values, rejeitados=rejeitados,
                )
            # Escolher por prioridade (determinístico)
            chosen = self._pick_priority(valid_fallbacks)
            return ResolutionOutcome(
                fixture_internal=fixture_internal, field=field,
                status=RES_RESOLVIDO_FALLBACK, resolved_value=chosen.value,
                source_provider=chosen.provider, is_fallback=True,
                is_primary=False,
                reconciliation_status=chosen.reconciliation.status,
                motivo="primary NULL; fallback MATCHED preencheu (NULL != ZERO)",
                coleta_timestamp=str(chosen.fact.retrieved_at),
                fixture_id_source=chosen.fact.fixture_provider_id,
                raw_values=raw_values, rejeitados=rejeitados,
            )

        # ---- Primary explícito (incl. 0): NUNCA sobrescrito (Cases B, G) -
        conflicts: list[tuple[FallbackCandidate, Any]] = []
        for cand in valid_fallbacks:
            if cand.value != primary_value:
                conflicts.append((cand, primary_value))
        if conflicts:
            # Registrar conflitos (preserve ambos); primary permanece.
            for cand, _pv in conflicts:
                self._registrar_conflito(
                    fixture_internal=fixture_internal, field=field,
                    provider_a=(primary_fact.provider if primary_fact else "api_football"),
                    value_a=primary_value,
                    provider_b=cand.provider, value_b=cand.value,
                    timestamp_a=(str(primary_fact.retrieved_at) if primary_fact else None),
                    timestamp_b=str(cand.fact.retrieved_at),
                    reconciliation_status=cand.reconciliation.status if cand.reconciliation else None,
                    motivo="primary explicito diverge de fallback; primary preservado",
                )
            return ResolutionOutcome(
                fixture_internal=fixture_internal, field=field,
                status=RES_CONFLITO_DE_FONTE, resolved_value=primary_value,
                source_provider=(primary_fact.provider if primary_fact else "api_football"),
                is_fallback=False, is_primary=True,
                reconciliation_status=(valid_fallbacks[0].reconciliation.status
                                        if valid_fallbacks else None),
                motivo=f"{len(conflicts)} conflito(s) de fonte; primary nao sobrescrito",
                coleta_timestamp=(str(primary_fact.retrieved_at) if primary_fact else None),
                fixture_id_source=(primary_fact.fixture_provider_id if primary_fact else None),
                raw_values=raw_values, rejeitados=rejeitados,
            )

        # Primary explícito e concordante com fallbacks (ou sem fallback)
        return ResolutionOutcome(
            fixture_internal=fixture_internal, field=field,
            status=RES_RESOLVIDO_PRIMARY, resolved_value=primary_value,
            source_provider=(primary_fact.provider if primary_fact else "api_football"),
            is_fallback=False, is_primary=True,
            reconciliation_status=(valid_fallbacks[0].reconciliation.status
                                    if valid_fallbacks else None),
            motivo="primary explicito mantido",
            coleta_timestamp=(str(primary_fact.retrieved_at) if primary_fact else None),
            fixture_id_source=(primary_fact.fixture_provider_id if primary_fact else None),
            raw_values=raw_values, rejeitados=rejeitados,
        )

    def _pick_priority(self, cands: list[FallbackCandidate]) -> FallbackCandidate:
        """Determinístico: ordem FALLBACK_PRIORITY; empate => primeiro da lista."""
        order = {p: i for i, p in enumerate(FALLBACK_PRIORITY)}
        return sorted(cands, key=lambda c: order.get(c.provider, 999))[0]

    # ---- conflito (reusa ConflictRecord + persiste) -----------------------
    def _registrar_conflito(
        self, fixture_internal: str, field: str,
        provider_a: str, value_a: Any,
        provider_b: str, value_b: Any,
        timestamp_a: str | None, timestamp_b: str | None,
        reconciliation_status: str | None, motivo: str,
    ) -> ConflictRecord:
        rec = ConflictRecord(
            provider_a=provider_a, provider_b=provider_b,
            fixture_id=str(fixture_internal), field=field,
            value_a=value_a, value_b=value_b,
            timestamp_a=timestamp_a, timestamp_b=timestamp_b,
            natureza=CONFLITO_NATUREZA_VALOR, nota=motivo,
        )
        if self._registry is not None:
            self._registry.registrar(rec)
        # Persistência durable (append-only, idempotente)
        self._persist_conflict(
            fixture_internal=fixture_internal, field=field,
            provider_a=provider_a, value_a=value_a,
            provider_b=provider_b, value_b=value_b,
            timestamp_a=timestamp_a, timestamp_b=timestamp_b,
            reconciliation_status=reconciliation_status, motivo=motivo,
        )
        return rec

    # ---- persistência append-only -----------------------------------------
    def persistir(self, outcome: ResolutionOutcome) -> bool:
        """Persiste a resolução (append-only, idempotente).

        Retorna True se inseriu uma linha nova, False se já existia (mesmo hash).
        Nunca sobrescreve; nunca muta silenciosamente.
        """
        h = _hash_resolution(
            outcome.fixture_internal, outcome.field,
            outcome.source_provider or "", outcome.coleta_timestamp,
            outcome.resolved_value,
        )
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR IGNORE INTO factual_resolution "
                "(resolution_hash, fixture_internal, field, resolved_value, "
                " source_provider, is_fallback, is_primary, "
                " reconciliation_status, motivo, coleta_timestamp, "
                " fixture_id_source, raw_values, registrado_em) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, outcome.fixture_internal, outcome.field,
                 _encode_value(outcome.resolved_value),
                 outcome.source_provider or "",
                 1 if outcome.is_fallback else 0,
                 1 if outcome.is_primary else 0,
                 outcome.reconciliation_status, outcome.motivo,
                 outcome.coleta_timestamp, outcome.fixture_id_source,
                 _encode_value(outcome.raw_values), time.time()),
            )
            inserted = conn.total_changes > 0
            conn.commit()
            conn.close()
            return inserted
        except Exception:
            return False

    def _persist_conflict(
        self, fixture_internal: str, field: str,
        provider_a: str, value_a: Any,
        provider_b: str, value_b: Any,
        timestamp_a: str | None, timestamp_b: str | None,
        reconciliation_status: str | None, motivo: str,
    ) -> bool:
        h = _hash_conflict(fixture_internal, field, provider_a, value_a,
                           provider_b, value_b, reconciliation_status)
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR IGNORE INTO source_conflict "
                "(conflict_hash, fixture_internal, field, provider_a, value_a, "
                " provider_b, value_b, timestamp_a, timestamp_b, "
                " reconciliation_status, motivo, natureza, registrado_em) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, fixture_internal, field, provider_a, _encode_value(value_a),
                 provider_b, _encode_value(value_b), timestamp_a, timestamp_b,
                 reconciliation_status, motivo, CONFLITO_NATUREZA_VALOR,
                 time.time()),
            )
            inserted = conn.total_changes > 0
            conn.commit()
            conn.close()
            return inserted
        except Exception:
            return False

    # ---- leitura (auditoria; nunca altera) ---------------------------------
    def listar_resolucoes(self, fixture_internal: str | None = None) -> list[dict]:
        """Leitura append-only (auditoria). Nunca altera."""
        try:
            conn = sqlite3.connect(self.db_path)
            if fixture_internal:
                rows = conn.execute(
                    "SELECT * FROM factual_resolution WHERE fixture_internal=? "
                    "ORDER BY registrado_em", (fixture_internal,)).fetchall()
                cols = [d[0] for d in conn.execute(
                    "SELECT * FROM factual_resolution LIMIT 0").description]
            else:
                rows = conn.execute(
                    "SELECT * FROM factual_resolution ORDER BY registrado_em"
                ).fetchall()
                cols = [d[0] for d in conn.execute(
                    "SELECT * FROM factual_resolution LIMIT 0").description]
            conn.close()
            return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return []

    def listar_conflitos(self, fixture_internal: str | None = None) -> list[dict]:
        try:
            conn = sqlite3.connect(self.db_path)
            if fixture_internal:
                rows = conn.execute(
                    "SELECT * FROM source_conflict WHERE fixture_internal=? "
                    "ORDER BY registrado_em", (fixture_internal,)).fetchall()
                cols = [d[0] for d in conn.execute(
                    "SELECT * FROM source_conflict LIMIT 0").description]
            else:
                rows = conn.execute(
                    "SELECT * FROM source_conflict ORDER BY registrado_em").fetchall()
                cols = [d[0] for d in conn.execute(
                    "SELECT * FROM source_conflict LIMIT 0").description]
            conn.close()
            return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return []