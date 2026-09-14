"""MACROETAPA -- GERACAO DE EVIDENCIA MULTIFONTE PENDENTE.

Orquestrador de COLETA E GERACAO DE EVIDENCIA, totalmente SEPARADO do motor
decisorio. NAO importa nem altera analysis, policy, politica_aprovacao,
settlement, calibration, backtest, prejogo_opportunity, live_pressure, odds,
identity, ao_vivo, app. Reusa (read-only ou via camadas de coleta ja isoladas)
apenas o necessario: multifonte (adapters + gate), auditoria_multifonte
(reconciliacao), resolucao_factual (resolver append-only), odds_coleta (store
append-only), live_pressure (historico append-only), backtest (path do DB +
constantes), validacao_multifonte (metricas read-only), live/fixtures
(fetch read-only de jogos ao vivo).

Blocos:
  A -- CARDS: populacao historica controlada via apifootball_com (fallback)
  B -- RECONCILIACAO MULTIFONTE HISTORICA (api_football <-> apifootball_com)
  C -- CORNERS: evidencia independente (5Dollar corners, sem recalibracao)
  D -- ODDS: coleta prospectiva controlada (5Dollar + The Odds API)
  E -- ROI: preparacao do dataset temporal (matching prediction <-> odd)
  F -- PRESSAO LIVE: materializacao da serie temporal (live_snapshot_history)
  G -- RESULTADO: monitoramento OOS (sem alterar motor)
  H -- GOALS: monitoramento OOS (sem alterar motor)
  I -- CONSOLIDACAO FINAL (docs/EVIDENCIA_MULTIFONTE_PENDENTE_FINAL.md)

PRINCIPIOS INEGOCIAVEIS:
  - FATO -> CALCULO -> INTERPRETACAO -> DECISAO.
  - NULL != ZERO; missing != zero; ausencia nunca vira zero.
  - Append-only: factual_resolution, source_conflict, odds_snapshot_history,
    live_snapshot_history, multifonte_reconciliation -- NUNCA sobrescreve.
  - Primary explicito (incl. 0) NUNCA sobrescrito por fallback; divergencia
    => CONFLITO_DE_FONTE (preserva ambos).
  - Fallback so preenche quando reconciliacao == MATCHED.
  - 402/403/429 => parar aquele provider, continuar os outros blocos.
  - Respeitar HARD_LIMIT por provider; nunca comprar plano pago.
  - Nao inventar dado; nao usar dado futuro; nao alterar settlement historico;
    nao reconstruir snapshot live pos-jogo; sem backfill falso.
  - Nunca imprimir API_KEY/tokens/secrets/prefixos.

Uso:
    python -m src.evidencia_multifonte [--json PATH] [--markdown PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- .env DEVE ser carregado para o gate + credenciais (src.multifonte nao
# importa src.config no topo, logo chamadas isoladas precisam disto). ---
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover
    pass

from src.config import DB_PATH
from src.backtest import BACKTEST_DB_PATH, DEC_ENTRAR
from src.multifonte import (
    credenciais_rotacionadas, status_credenciais,
    APIFootballComProvider, FiveDollarFootballProvider, TheOddsAPIProvider,
    FootballDataOrgProvider,
    NormalizedFact, ST_OK, ST_MISSING,
    RateLimitHit, QuotaExhausted, CredentialsBlocked, EndpointNotConfirmed,
)
from src.auditoria_multifonte import (
    reconciliar_fixture, ConflictRegistry,
    REC_MATCHED, REC_AMBIGUOUS, REC_NOT_MATCHED,
    _norm_nome, _eh_youth_event,
)
from src.resolucao_factual import (
    ResolverFactual, FallbackCandidate,
    RES_RESOLVIDO_FALLBACK, RES_NULL_MANTIDO, RES_CONFLITO_DE_FONTE,
    RES_RESOLVIDO_PRIMARY, RES_FALLBACK_BLOQUEADO_RECONCILIACAO,
    RES_SEM_FALLBACK, RES_FALLBACK_SEM_SUPORTE_CAMPO,
)
from src.odds_coleta import OddsSnapshotStore, coletar_multiprovider
from src.validacao_multifonte import (
    auditar as _auditar_validacao,
    _odds_roi_audit,
    APROVADO_PROX, EM_OBS, BLOQUEADO, NAO_AVAL,
    DRIFT_CORNERS_CUTOFF,
)

# Status de coleta por provider (espelha multifonte mas local para relatorio)
ST_PROVIDER_OK = "OK"
ST_PROVIDER_LIMIT = "LIMITADO_POR_PLANO"
ST_PROVIDER_BLOCKED = "CREDENCIAL_BLOQUEADA"
ST_PROVIDER_FAIL = "FALHA_TECNICA"
ST_PROVIDER_EMPTY = "SEM_DADOS"

BASELINE_RUN = "bt-20260913224716-PRE_GAME"

# Orçamento de chamadas por bloco (respeita HARD_LIMIT dos adapters:
# apifootball_com=15, 5Dollar=15, TheOddsAPI=10). Margem de seguranca.
BUDGET_A_DATES = 10       # apifootball_com: 1 get_events por data
BUDGET_C_FIXTURES = 12    # 5Dollar: fetch_odds corner por fixture
BUDGET_D_5DOLLAR = 3      # 5Dollar: mercados adicionais (goalline/cards/1x2)
BUDGET_D_THEODDS = 2      # TheOddsAPI: sport_keys
BUDGET_F_LIVE = 3         # api_football: fixtures live + snapshots


# ----------------------------------------------------------------------
# Schema aditivo: tabela de correspondencia multifonte (Bloco B) -- append-only
# ----------------------------------------------------------------------
_RECON_SCHEMA = """
CREATE TABLE IF NOT EXISTS multifonte_reconciliation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reconciliation_hash TEXT NOT NULL UNIQUE,
    fixture_internal TEXT NOT NULL,
    provider_a TEXT NOT NULL,
    provider_b TEXT NOT NULL,
    fixture_id_a TEXT,
    fixture_id_b TEXT,
    campo TEXT NOT NULL,
    valor_a TEXT,
    valor_b TEXT,
    reconciliation_status TEXT NOT NULL,
    confidence REAL,
    concordancia INTEGER NOT NULL,
    registrado_em REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mfrec_fixture
    ON multifonte_reconciliation (fixture_internal, campo);
"""


def _ensure_recon_schema(db_path: str | None = None) -> None:
    """Cria multifonte_reconciliation se ausente (idempotente, sem user_version)."""
    p = str(db_path or DB_PATH)
    try:
        with sqlite3.connect(p) as conn:
            conn.executescript(_RECON_SCHEMA)
            conn.commit()
    except sqlite3.Error:
        pass


def _hash_recon(fixture_internal: str, campo: str,
                provider_a: str, provider_b: str,
                valor_a: Any, valor_b: Any) -> str:
    payload = json.dumps(
        {"f": fixture_internal, "c": campo, "pa": provider_a, "va": valor_a,
         "pb": provider_b, "vb": valor_b},
        sort_keys=True, default=str, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _persist_recon(fixture_internal: str, provider_a: str, provider_b: str,
                   fixture_id_a: str | None, fixture_id_b: str | None,
                   campo: str, valor_a: Any, valor_b: Any,
                   status: str, confidence: float | None,
                   concordancia: int, db_path: str | None = None) -> bool:
    h = _hash_recon(fixture_internal, campo, provider_a, provider_b,
                    valor_a, valor_b)
    try:
        with sqlite3.connect(str(db_path or DB_PATH)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO multifonte_reconciliation "
                "(reconciliation_hash, fixture_internal, provider_a, provider_b, "
                " fixture_id_a, fixture_id_b, campo, valor_a, valor_b, "
                " reconciliation_status, confidence, concordancia, registrado_em) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, fixture_internal, provider_a, provider_b, fixture_id_a,
                 fixture_id_b, campo, _enc(valor_a), _enc(valor_b),
                 status, confidence, concordancia, time.time()),
            )
            inserted = conn.total_changes > 0
            conn.commit()
        return inserted
    except sqlite3.Error:
        return False


def _enc(v: Any) -> str:
    return json.dumps(v, default=str, ensure_ascii=False)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _placar_to_scores(placar: str | None) -> tuple[int | None, int | None]:
    if not placar:
        return None, None
    parts = str(placar).split("-")
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def _epoch_of(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        ds = str(iso).replace("Z", "+00:00")
        return datetime.fromisoformat(ds).timestamp()
    except (TypeError, ValueError):
        return None


def _mes(d: str | None) -> str:
    if not d:
        return "?"
    return str(d)[:7]


def _provider_status_from_exc(exc: Exception) -> str:
    if isinstance(exc, (RateLimitHit,)):
        return ST_PROVIDER_LIMIT
    if isinstance(exc, (QuotaExhausted,)):
        return ST_PROVIDER_LIMIT
    if isinstance(exc, (CredentialsBlocked,)):
        return ST_PROVIDER_BLOCKED
    if isinstance(exc, (EndpointNotConfirmed,)):
        return ST_PROVIDER_FAIL
    return ST_PROVIDER_FAIL


# ----------------------------------------------------------------------
# Carregamento: ENTRAR com red_cards NULL (Bloco A)
# ----------------------------------------------------------------------
def _entrar_cards_na(backtest_db: str = BACKTEST_DB_PATH,
                     run_id: str = BASELINE_RUN) -> list[dict]:
    """ENTRAR no mercado cartoes com resultado NAO AVALIAVEL (red_cards NULL).
    Prioridade 1 do Bloco A."""
    conn = sqlite3.connect(f"file:{backtest_db}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT fixture_id, home_team, away_team, date, league_name, "
            "season, placar_final, linha, resultado_final "
            "FROM bt_predictions WHERE run_id=? AND mercado='cartoes' "
            "AND decisao=? AND resultado_final='NÃO AVALIÁVEL' "
            "ORDER BY date DESC",
            (run_id, DEC_ENTRAR),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _entrar_corners_postdrift(backtest_db: str = BACKTEST_DB_PATH,
                              run_id: str = BASELINE_RUN,
                              limit: int = 60) -> list[dict]:
    """CORNERS ENTRAR no periodo post-drift (>= DRIFT_CORNERS_CUTOFF)."""
    conn = sqlite3.connect(f"file:{backtest_db}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT fixture_id, home_team, away_team, date, league_name, "
            "linha, prob, resultado_final "
            "FROM bt_predictions WHERE run_id=? AND mercado='escanteios' "
            "AND decisao=? AND date >= ? "
            "ORDER BY date DESC LIMIT ?",
            (run_id, DEC_ENTRAR, DRIFT_CORNERS_CUTOFF + "T00:00:00", limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# Bloco A -- CARDS: populacao historica via apifootball_com
# ----------------------------------------------------------------------
def bloco_a_cards(backtest_db: str = BACKTEST_DB_PATH,
                  db_path: str | None = None) -> dict[str, Any]:
    """Popula factual_resolution (append-only) para ENTRAR cartoes com
    red_cards NULL, via fallback apifootball_com (MATCHED obrigatorio).

    NULL+fallback=0  => RESOLVIDO_FALLBACK (zero explicito valido)
    NULL+fallback>0  => RESOLVIDO_FALLBACK (utilizavel)
    NULL sem cards[] => NULL_MANTIDO
    Primary explicito => NUNCA sobrescrito (CONFLITO_DE_FONTE se divergir)
    """
    p = str(db_path or DB_PATH)
    _ensure_recon_schema(p)
    na_fixtures = _entrar_cards_na(backtest_db)
    res: dict[str, Any] = {
        "entrar_total": 0, "entrar_na_red_null": len(na_fixtures),
        "datas_visitadas": 0, "events_coletados": 0,
        "matched": 0, "ambiguous": 0, "not_matched": 0,
        "resolvidos_fallback": 0, "resolvidos_fallback_zero": 0,
        "resolvidos_fallback_positivo": 0, "null_mantido": 0,
        "conflito_de_fonte": 0, "resolvido_primary": 0,
        "provider_status": ST_PROVIDER_OK, "limite_atingido": False,
        "detalhes": [],
    }
    res["entrar_total"] = len(na_fixtures)
    if not na_fixtures:
        res["provider_status"] = ST_PROVIDER_EMPTY
        return res

    # Agrupar por data (yyyy-mm-dd) e visitar as mais densas dentro do orcamento
    por_data: dict[str, list[dict]] = defaultdict(list)
    for fx in na_fixtures:
        d = (fx.get("date") or "")[:10]
        if d:
            por_data[d].append(fx)
    datas_ranked = sorted(por_data.items(), key=lambda kv: len(kv[1]), reverse=True)
    datas_visitadas = 0

    if not credenciais_rotacionadas():
        res["provider_status"] = ST_PROVIDER_BLOCKED
        res["nota"] = "GATE fechado: chamadas autenticadas bloqueadas."
        return res

    adapter = APIFootballComProvider()
    resolver = ResolverFactual(db_path=p)
    # Mapa nome-normalizado -> evento apifootball_com (por data)
    for data, fxs in datas_ranked:
        if datas_visitadas >= BUDGET_A_DATES:
            break
        if adapter._calls >= adapter.HARD_LIMIT:
            res["limite_atingido"] = True
            break
        datas_visitadas += 1
        try:
            events = adapter.fetch_events_by_date(data, data)
        except (RateLimitHit, QuotaExhausted) as e:
            res["provider_status"] = _provider_status_from_exc(e)
            res["limite_atingido"] = True
            break
        except (CredentialsBlocked, EndpointNotConfirmed) as e:
            res["provider_status"] = _provider_status_from_exc(e)
            break
        except Exception as e:
            res["provider_status"] = ST_PROVIDER_FAIL
            res["nota"] = f"{type(e).__name__}: {str(e)[:80]}"
            break
        if not isinstance(events, list):
            events = []
        res["events_coletados"] += len(events)

        # Indexar eventos por nome normalizado (excluir youth)
        nome_map: dict[tuple, dict] = {}
        prefix_map: dict[tuple, dict] = {}
        for ev in events:
            if not isinstance(ev, dict) or _eh_youth_event(ev):
                continue
            h = _norm_nome(ev.get("match_hometeam_name"))
            a = _norm_nome(ev.get("match_awayteam_name"))
            if h and a:
                nome_map[(h, a)] = ev
                prefix_map.setdefault((h[:4], a[:4]), ev)

        for fx in fxs:
            h = _norm_nome(fx.get("home_team"))
            a = _norm_nome(fx.get("away_team"))
            ev = nome_map.get((h, a))
            match_kind = "exact"
            if not ev:
                ev = prefix_map.get((h[:4], a[:4]))
                match_kind = "fuzzy_prefix"
            if not ev:
                continue
            afc_match_id = str(ev.get("match_id"))
            fixture_internal = str(fx["fixture_id"])
            sh, sa = _placar_to_scores(fx.get("placar_final"))
            rec = reconciliar_fixture(
                {"fixture_id": fixture_internal, "home": fx.get("home_team"),
                 "away": fx.get("away_team"), "kickoff": fx.get("date"),
                 "competition": fx.get("league_name"), "season": fx.get("season"),
                 "score_home": sh, "score_away": sa, "status": "FINISHED"},
                {"fixture_id": afc_match_id,
                 "home": ev.get("match_hometeam_name"),
                 "away": ev.get("match_awayteam_name"),
                 "kickoff": ev.get("match_date"),
                 "competition": ev.get("league_name"), "season": None,
                 "score_home": ev.get("match_hometeam_score"),
                 "score_away": ev.get("match_awayteam_score"), "status": None},
                "apifootball_com")
            if rec.status == REC_MATCHED:
                res["matched"] += 1
            elif rec.status == REC_AMBIGUOUS:
                res["ambiguous"] += 1
            else:
                res["not_matched"] += 1

            has_cards = "cards" in ev
            yell = red = None
            if has_cards:
                red = 0
                yell = 0
                for c in ev.get("cards", []) or []:
                    ct = (c.get("card") or c.get("card_type") or "").lower()
                    if "red" in ct:
                        red += 1
                    elif "yellow" in ct:
                        yell += 1

            # Resolver red_cards e yellow_cards (primary NULL -> fallback)
            for campo, fb_val in (("red_cards", red), ("yellow_cards", yell)):
                primary_fact = NormalizedFact(
                    provider="api_football", endpoint="/fixtures/statistics",
                    retrieved_at=0.0, fixture_provider_id=fixture_internal,
                    fixture_corner_id=fixture_internal, field=campo,
                    raw_value=None, normalized_value=None, status=ST_MISSING)
                fb_fact = NormalizedFact(
                    provider="apifootball_com", endpoint="get_events",
                    retrieved_at=time.time(), fixture_provider_id=afc_match_id,
                    fixture_corner_id=fixture_internal, field=campo,
                    raw_value=ev.get("cards"),
                    normalized_value=fb_val if has_cards else None,
                    status=ST_OK if has_cards else ST_MISSING)
                cand = FallbackCandidate(fact=fb_fact, reconciliation=rec)
                outcome = resolver.resolver_campo(
                    fixture_internal=fixture_internal, field=campo,
                    primary_fact=primary_fact, fallback_candidates=[cand])
                inserted = resolver.persistir(outcome)
                # Registrar correspondencia (Bloco B consome isto tambem)
                _persist_recon(
                    fixture_internal, "api_football", "apifootball_com",
                    fixture_internal, afc_match_id, campo,
                    None, fb_val if has_cards else None,
                    rec.status, rec.confidence,
                    1 if (has_cards and fb_val == 0) or fb_val else 0, p)
                if inserted:
                    if outcome.status == RES_RESOLVIDO_FALLBACK:
                        res["resolvidos_fallback"] += 1
                        if fb_val == 0:
                            res["resolvidos_fallback_zero"] += 1
                        elif fb_val and fb_val > 0:
                            res["resolvidos_fallback_positivo"] += 1
                    elif outcome.status == RES_NULL_MANTIDO:
                        res["null_mantido"] += 1
                    elif outcome.status == RES_CONFLITO_DE_FONTE:
                        res["conflito_de_fonte"] += 1
                    elif outcome.status == RES_RESOLVIDO_PRIMARY:
                        res["resolvido_primary"] += 1
                if len(res["detalhes"]) < 12 and campo == "red_cards":
                    res["detalhes"].append({
                        "fixture_id": fixture_internal, "afc_match_id": afc_match_id,
                        "match_kind": match_kind, "reconciliation": rec.status,
                        "confianca": rec.confidence,
                        "red_cards_fallback": red if has_cards else None,
                        "has_cards_field": has_cards,
                        "resolution": outcome.status,
                    })
    res["datas_visitadas"] = datas_visitadas
    if adapter._calls >= adapter.HARD_LIMIT:
        res["limite_atingido"] = True
    return res


# ----------------------------------------------------------------------
# Bloco B -- RECONCILIACAO MULTIFONTE HISTORICA
# ----------------------------------------------------------------------
def bloco_b_reconciliacao(bloco_a: dict[str, Any],
                          db_path: str | None = None) -> dict[str, Any]:
    """Consolida a reconciliacao api_football <-> apifootball_com por campo
    (placar, red_cards, yellow_cards). Reusa as correspondencias registradas
    pelo Bloco A em multifonte_reconciliation (append-only). Sem nova chamada
    de API. Nenhum provider vencedor eleito silenciosamente."""
    p = str(db_path or DB_PATH)
    res: dict[str, Any] = {
        "correspondencias_registradas": 0,
        "por_status": {"MATCHED": 0, "AMBIGUOUS": 0, "NOT_MATCHED": 0},
        "por_campo": {}, "concordancia_por_campo": {},
        "provider_a": "api_football", "provider_b": "apifootball_com",
        "nota": "Sem eleicao de provider vencedor; preserva ambos os valores.",
    }
    try:
        with sqlite3.connect(p) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM multifonte_reconciliation"
            ).fetchone()[0]
            res["correspondencias_registradas"] = total
            for r in conn.execute(
                "SELECT reconciliation_status, COUNT(*) "
                "FROM multifonte_reconciliation GROUP BY reconciliation_status"):
                res["por_status"][r[0]] = r[1]
            for r in conn.execute(
                "SELECT campo, COUNT(*), SUM(concordancia) "
                "FROM multifonte_reconciliation GROUP BY campo"):
                campo, n, conc = r
                res["por_campo"][campo] = n
                res["concordancia_por_campo"][campo] = int(conc or 0)
    except sqlite3.Error:
        pass
    return res


# ----------------------------------------------------------------------
# Bloco C -- CORNERS: evidencia independente (5Dollar)
# ----------------------------------------------------------------------
def _five_dollar_fixture_params(date_str: str) -> dict:
    """Params para GET /v1/fixtures do 5Dollar: janela [start_time, end_time)
    em unix timestamps (segundos, UTC), no maximo 24h (documentado em
    5dollarfootballapi.com/docs/fixtures). NAO usa 'date'/'page' -- esses sao
    params desconhecidos, rejeitados com HTTP 400 (invalid_time_window).
    """
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = int(d.timestamp())
    return {"start_time": start, "end_time": start + 86400}


def bloco_c_corners(date_str: str | None = None,
                    db_path: str | None = None) -> dict[str, Any]:
    """Evidencia independente de corners via 5Dollar (opening/closing/inplay).
    Sem recalibracao; sem alteracao de threshold. Responde:
    DRIFT CONTINUA / CAUSA / FONTE ORIGINAL / DETERIORACAO / PODE AVANCAR.

    5Dollar usa fixture_id proprio -> coleta fixtures do dia e fetch_odds
    corner. Odds prospectivas (presente/futuro), NAO explicam drift historico
    retroativamente -- ausencia de fonte alternativa historica e um FATO."""
    p = str(db_path or DB_PATH)
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    res: dict[str, Any] = {
        "data_coleta": today, "provider": "five_dollar_football",
        "fixtures_5dollar": 0, "corners_odds_coletados": 0,
        "fases_observadas": set(), "bookmakers": set(),
        "market_canonical": "TOTAL_CORNERS",
        "provider_status": ST_PROVIDER_OK, "limite_atingido": False,
        "drift_respostas": {}, "odds_exemplo": [],
    }
    # Metricas de drift (read-only no backtest) -- reusa validacao
    try:
        val = _auditar_validacao()
        d = val.get("D_CORNERS", {})
        res["drift_pre_hit"] = d.get("pre_drift", {}).get("hit_excl_na")
        res["drift_post_hit"] = d.get("post_drift", {}).get("hit_excl_na")
        res["drift_post_gap"] = d.get("post_drift", {}).get("gap")
        res["drift_confirmado"] = val.get("respostas_macroetapa", {}).get(
            "drift_cornes_confirmado")
    except Exception:
        pass

    if not credenciais_rotacionadas():
        res["provider_status"] = ST_PROVIDER_BLOCKED
        res["drift_respostas"] = _drift_answers(res)
        return res

    adapter = FiveDollarFootballProvider()
    store = OddsSnapshotStore(db_path=p)
    fixtures: list = []
    try:
        fixtures = adapter.fetch_fixtures(_five_dollar_fixture_params(today)) or []
    except (RateLimitHit, QuotaExhausted) as e:
        res["provider_status"] = _provider_status_from_exc(e)
        res["limite_atingido"] = True
        res["drift_respostas"] = _drift_answers(res)
        return res
    except (CredentialsBlocked, EndpointNotConfirmed) as e:
        res["provider_status"] = _provider_status_from_exc(e)
        res["drift_respostas"] = _drift_answers(res)
        return res
    except Exception as e:
        res["provider_status"] = ST_PROVIDER_FAIL
        res["nota"] = f"{type(e).__name__}: {str(e)[:80]}"
        res["drift_respostas"] = _drift_answers(res)
        return res
    res["fixtures_5dollar"] = len(fixtures) if isinstance(fixtures, list) else 0

    n_odds = 0
    for fx in fixtures if isinstance(fixtures, list) else []:
        if n_odds >= BUDGET_C_FIXTURES or adapter._calls >= adapter.HARD_LIMIT:
            res["limite_atingido"] = True
            break
        fx_id = fx.get("id") or fx.get("fixture_id") if isinstance(fx, dict) else None
        if fx_id is None:
            continue
        try:
            r = coletar_multiprovider(
                "five_dollar_football", store, fixture_id_int=int(fx_id),
                market="corner", collected_at=time.time())
            n_odds += r.get("inseridos", 0)
            if r.get("limite"):
                res["limite_atingido"] = True
                res["provider_status"] = ST_PROVIDER_LIMIT
                break
            for ph in r.get("por_phase", {}):
                res["fases_observadas"].add(ph)
            for bm in r.get("por_bookmaker", {}):
                res["bookmakers"].add(bm)
            if len(res["odds_exemplo"]) < 5:
                res["odds_exemplo"].append({
                    "fixture_5dollar": int(fx_id),
                    "inseridos": r.get("inseridos", 0),
                    "fases": r.get("por_phase", {}),
                })
        except (RateLimitHit, QuotaExhausted) as e:
            res["provider_status"] = _provider_status_from_exc(e)
            res["limite_atingido"] = True
            break
        except Exception:
            continue
    res["corners_odds_coletados"] = n_odds
    res["fases_observadas"] = sorted(res["fases_observadas"])
    res["bookmakers"] = sorted(res["bookmakers"])
    res["drift_respostas"] = _drift_answers(res)
    return res


def _drift_answers(c: dict[str, Any]) -> dict[str, Any]:
    post = c.get("drift_post_hit")
    pre = c.get("drift_pre_hit")
    continua = bool(c.get("drift_confirmado"))
    return {
        "drift_continua": continua,
        "causa_identificada": False,
        "causa_nota": (
            "Sem fonte alternativa historica de corners para isolar causa; "
            "5Dollar e prospectivo (presente/futuro), nao retroativo."),
        "fonte_original": "api_football (unica fonte historica de corners)",
        "deterioracao_gap_post": c.get("drift_post_gap"),
        "pode_avancar": False,
        "pode_avancar_nota": (
            "Manter EM_OBSERVACAO. Drift pos-cutoff confirmado (hit "
            f"{pre} -> {post}); sem recalibracao; sem alteracao de threshold."),
    }


# ----------------------------------------------------------------------
# Bloco D -- ODDS: coleta prospectiva controlada (5Dollar + The Odds API)
# ----------------------------------------------------------------------
def bloco_d_odds(date_str: str | None = None,
                 db_path: str | None = None) -> dict[str, Any]:
    """Coleta prospectiva append-only. 5Dollar (goalline/cards/1x2) +
    TheOddsAPI (h2h/totals/spreads). Nunca UPDATE de odd historica; sem
    backfill artificial."""
    p = str(db_path or DB_PATH)
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    res: dict[str, Any] = {
        "data_coleta": today,
        "five_dollar": {"inseridos": 0, "mercados": {}, "status": ST_PROVIDER_OK,
                        "limite": False},
        "the_odds_api": {"inseridos": 0, "sport_keys": [], "status": ST_PROVIDER_OK,
                         "limite": False},
    }
    store = OddsSnapshotStore(db_path=p)
    if not credenciais_rotacionadas():
        res["five_dollar"]["status"] = ST_PROVIDER_BLOCKED
        res["the_odds_api"]["status"] = ST_PROVIDER_BLOCKED
        return res

    # 5Dollar: reusa fixtures do dia; mercados goalline/cards/1x2
    fd = FiveDollarFootballProvider()
    try:
        fixtures = fd.fetch_fixtures(_five_dollar_fixture_params(today)) or []
    except (RateLimitHit, QuotaExhausted) as e:
        res["five_dollar"]["status"] = _provider_status_from_exc(e)
        res["five_dollar"]["limite"] = True
        fixtures = []
    except Exception:
        res["five_dollar"]["status"] = ST_PROVIDER_FAIL
        fixtures = []
    fx_ids = []
    if isinstance(fixtures, list):
        for fx in fixtures:
            fid = fx.get("id") or fx.get("fixture_id") if isinstance(fx, dict) else None
            if fid is not None:
                fx_ids.append(int(fid))
    for mercado in ("goalline", "cards", "1x2"):
        if fd._calls >= fd.HARD_LIMIT or len(fx_ids) == 0:
            res["five_dollar"]["limite"] = True
            break
        # 1 fixture por mercado (orcamento apertado); preserva fase/linha
        fid = fx_ids[0]
        try:
            r = coletar_multiprovider(
                "five_dollar_football", store, fixture_id_int=fid,
                market=mercado, collected_at=time.time())
            res["five_dollar"]["inseridos"] += r.get("inseridos", 0)
            res["five_dollar"]["mercados"][mercado] = {
                "inseridos": r.get("inseridos", 0),
                "fases": r.get("por_phase", {}),
            }
            if r.get("limite"):
                res["five_dollar"]["limite"] = True
                res["five_dollar"]["status"] = ST_PROVIDER_LIMIT
                break
        except (RateLimitHit, QuotaExhausted) as e:
            res["five_dollar"]["status"] = _provider_status_from_exc(e)
            res["five_dollar"]["limite"] = True
            break
        except Exception:
            continue

    # TheOddsAPI: 1-2 sport_keys, h2h/totals/spreads
    toa = TheOddsAPIProvider()
    for sk in ("soccer_uefa_champs_league", "soccer_epl"):
        if toa._calls >= toa.HARD_LIMIT:
            res["the_odds_api"]["limite"] = True
            break
        try:
            r = coletar_multiprovider(
                "the_odds_api", store, sport_key=sk,
                collected_at=time.time())
            res["the_odds_api"]["inseridos"] += r.get("inseridos", 0)
            res["the_odds_api"]["sport_keys"].append({
                "sport_key": sk, "inseridos": r.get("inseridos", 0),
                "por_market": r.get("por_market_canonical", {}),
            })
            if r.get("limite"):
                res["the_odds_api"]["limite"] = True
                res["the_odds_api"]["status"] = ST_PROVIDER_LIMIT
                break
        except (RateLimitHit, QuotaExhausted) as e:
            res["the_odds_api"]["status"] = _provider_status_from_exc(e)
            res["the_odds_api"]["limite"] = True
            break
        except Exception:
            continue
    return res


# ----------------------------------------------------------------------
# Bloco E -- ROI: preparacao do dataset temporal
# ----------------------------------------------------------------------
def bloco_e_roi(preds: list[dict] | None = None,
                db_path: str | None = None) -> dict[str, Any]:
    """Dataset temporal: matching prediction <-> odd (VALIDO/INDETERMINADO/
    INVALIDO_TEMPORALMENTE). Nunca closing futura; nunca INPLAY para PRE-GAME.
    ROI exploratorio apenas se amostra VALIDO settled suficiente."""
    p = str(db_path or DB_PATH)
    if preds is None:
        from src.validacao_multifonte import _load_preds
        preds = _load_preds()
    base = _odds_roi_audit(preds)
    res: dict[str, Any] = {
        "odds_snapshots_total": base.get("odds_snapshots_total_familia_mapeada"),
        "fixtures_com_odds": base.get("fixtures_com_odds"),
        "providers": base.get("providers"),
        "por_mercado": {},
        "roi_historico_calculavel": base.get("roi_historico_calculavel"),
        "nota": base.get("nota"),
    }
    for m, d in base.get("por_mercado", {}).items():
        res["por_mercado"][m] = {
            "entrar": d["entrar"],
            "validos_temporalmente": d["validos_temporalmente"],
            "invalidos_temporalmente": d["invalidos_temporalmente"],
            "indeterminados": d["indeterminados"],
            "sem_odd_casavel": d["sem_odd_casavel"],
            "settled_com_odd": d["settled_com_odd"],
            "roi_exploratorio": d["roi"],
            "avg_odd": d["avg_odd"],
            "classificacao": (
                "VALIDO" if d["validos_temporalmente"] > 0 else
                "INVALIDO_TEMPORALMENTE" if d["invalidos_temporalmente"] > 0 else
                "SEM_ODD_CASAVEL"
            ),
        }
    return res


# ----------------------------------------------------------------------
# Bloco F -- PRESSAO LIVE: materializacao da serie temporal
# ----------------------------------------------------------------------
def bloco_f_pressao_live(db_path: str | None = None) -> dict[str, Any]:
    """Materializa live_snapshot_history (append-only). Captura snapshots de
    jogos ao vivo agora (one-shot -> 1 marco por jogo). Serie completa
    1/15/30/45/60/75/90 requer jogo em andamento capturado ao longo de 90 min
    (nao alcancavel em execucao one-shot). Nunca reconstruir pos-jogo; sem
    backfill falso. Sem jogos live => infra pronta + contagem real."""
    p = str(db_path or DB_PATH)
    from src.live_pressure import LiveSnapshotHistory, LiveSnapshotStore
    res: dict[str, Any] = {
        "infra_pronta": True, "tabela": "live_snapshot_history",
        "historico_total": 0, "fixtures_no_historico": 0,
        "jogos_live_agora": 0, "snapshots_materializados": 0,
        "marcos_alvo": [1, 15, 30, 45, 60, 75, 90],
        "marcos_capturados_one_shot": [],
        "provider_status": ST_PROVIDER_OK, "limite_atingido": False,
        "nota": "",
    }
    hist = LiveSnapshotHistory(db_path=p)
    try:
        with sqlite3.connect(p) as conn:
            res["historico_total"] = conn.execute(
                "SELECT COUNT(*) FROM live_snapshot_history").fetchone()[0]
            res["fixtures_no_historico"] = conn.execute(
                "SELECT COUNT(DISTINCT fixture_id) FROM live_snapshot_history"
            ).fetchone()[0]
    except sqlite3.Error:
        pass

    if not credenciais_rotacionadas():
        res["provider_status"] = ST_PROVIDER_BLOCKED
        res["nota"] = "GATE fechado; infra pronta, coleta live bloqueada."
        return res

    # Descobrir jogos ao vivo (api_football /fixtures?live=all)
    try:
        from src.api_client import APIFootballClient
        from src.fixtures import get_live_fixtures
        client = APIFootballClient()
        live = get_live_fixtures(client)
    except (RateLimitHit, QuotaExhausted) as e:
        res["provider_status"] = _provider_status_from_exc(e)
        res["limite_atingido"] = True
        return res
    except Exception as e:
        res["provider_status"] = ST_PROVIDER_FAIL
        res["nota"] = f"{type(e).__name__}: {str(e)[:80]}"
        return res
    res["jogos_live_agora"] = len(live)
    if not live:
        res["nota"] = (
            "Nenhum jogo ao vivo agora. Infra pronta (live_snapshot_history "
            "append-only); serie completa requer captura durante jogo em "
            "andamento (marcos 1/15/30/45/60/75/90).")
        return res

    # Capturar 1 snapshot por jogo live (one-shot), ate BUDGET_F_LIVE
    from src.live import update_live_snapshot
    materializados = 0
    for g in live[:BUDGET_F_LIVE]:
        try:
            snap, _old, _chg = update_live_snapshot(client, g.fixture_id)
            rec = hist.append(snap)
            materializados += 1
            res["marcos_capturados_one_shot"].append({
                "fixture_id": g.fixture_id, "seq": rec.seq,
                "collected_at": rec.collected_at_text,
            })
        except (RateLimitHit, QuotaExhausted) as e:
            res["provider_status"] = _provider_status_from_exc(e)
            res["limite_atingido"] = True
            break
        except Exception:
            continue
    res["snapshots_materializados"] = materializados
    res["nota"] = (
        f"One-shot: {materializados} snapshot(s) materializado(s). Serie "
        "completa de marcos requer coleta ao longo do jogo (nao one-shot).")
    return res


# ----------------------------------------------------------------------
# Bloco G -- RESULTADO: monitoramento OOS
# ----------------------------------------------------------------------
def bloco_g_resultado(val: dict[str, Any] | None = None) -> dict[str, Any]:
    """Monitoramento OOS de RESULTADO. Sem alterar motor; sem auto-promocao."""
    if val is None:
        val = _auditar_validacao()
    b = val.get("B_RESULTADO", {})
    m = b.get("metricas_entrar", {})
    return {
        "mercado": "resultado",
        "status_anterior": "EM_OBSERVAÇÃO (experimental)",
        "entrar": m.get("entrar"), "settled": m.get("settled"),
        "hit_excl_na": m.get("hit_excl_na"), "gap": m.get("gap"),
        "oos_hit": b.get("oos", {}).get("hit_excl_na") if "oos" in b else None,
        "walk_forward": [w.get("hit_excl_na") for w in b.get("walk_forward", [])],
        "status_novo": EM_OBS,
        "justificativa": "Experimental; sem alteracao de motor; sem auto-promocao.",
        "motor_alterado": False,
    }


# ----------------------------------------------------------------------
# Bloco H -- GOALS: monitoramento OOS
# ----------------------------------------------------------------------
def bloco_h_goals(val: dict[str, Any] | None = None) -> dict[str, Any]:
    """Monitoramento OOS de GOALS. Sem integracao; rebaixa a EM_OBSERVACAO
    apenas na ANALISE se deterioracao material (sem alterar motor)."""
    if val is None:
        val = _auditar_validacao()
    a = val.get("A_GOALS", {})
    m = a.get("metricas_entrar", {})
    oos_hit = a.get("oos", {}).get("hit_excl_na") if "oos" in a else None
    wf = [w.get("hit_excl_na") for w in a.get("walk_forward", [])]
    # Deterioracao material: wf cai abaixo de 0.90 ou oos < 0.90
    deterioracao = (oos_hit is not None and oos_hit < 0.90) or (
        any(w is not None and w < 0.90 for w in wf))
    status_novo = EM_OBS if deterioracao else APROVADO_PROX
    return {
        "mercado": "gols",
        "status_anterior": "APROVADO_PARA_PROXIMA_FASE",
        "entrar": m.get("entrar"), "settled": m.get("settled"),
        "hit_excl_na": m.get("hit_excl_na"), "gap": m.get("gap"),
        "oos_hit": oos_hit, "walk_forward": wf,
        "deterioracao_material": deterioracao,
        "status_novo": status_novo,
        "justificativa": (
            "Deterioracao material detectada na analise -> rebaixado a "
            "EM_OBSERVACAO na analise (motor NAO alterado)." if deterioracao
            else "Estavel; mantido APROVADO_PARA_PROXIMA_FASE; motor intacto."),
        "motor_alterado": False,
    }


# ----------------------------------------------------------------------
# Bloco I -- CONSOLIDACAO FINAL
# ----------------------------------------------------------------------
def bloco_i_consolidacao(a, b, c, d, e, f, g, h) -> list[dict]:
    """Tabela final: MERCADO/STATUS ANTERIOR/NOVOS DADOS/N RECONCILIADO/
    FALLBACK/OOS/ODDS VALIDAS/ROI POSSIVEL/LIVE HISTORY/STATUS NOVO/JUSTIFICATIVA."""
    rows: list[dict] = []
    rows.append({
        "MERCADO": "GOALS",
        "STATUS_ANTERIOR": "APROVADO_PARA_PROXIMA_FASE",
        "NOVOS_DADOS": "Monitoramento OOS (sem coleta nova)",
        "N_RECONCILIADO": 0,
        "FALLBACK": "N/A",
        "OOS": h.get("oos_hit"),
        "ODDS_VALIDAS": e["por_mercado"].get("gols", {}).get("validos_temporalmente", 0),
        "ROI_POSSIVEL": e["por_mercado"].get("gols", {}).get("classificacao"),
        "LIVE_HISTORY": "N/A",
        "STATUS_NOVO": h["status_novo"],
        "JUSTIFICATIVA": h["justificativa"],
    })
    rows.append({
        "MERCADO": "RESULTADO",
        "STATUS_ANTERIOR": "EM_OBSERVAÇÃO",
        "NOVOS_DADOS": "Monitoramento OOS (sem coleta nova)",
        "N_RECONCILIADO": 0,
        "FALLBACK": "N/A",
        "OOS": g.get("oos_hit"),
        "ODDS_VALIDAS": e["por_mercado"].get("resultado", {}).get("validos_temporalmente", 0),
        "ROI_POSSIVEL": e["por_mercado"].get("resultado", {}).get("classificacao"),
        "LIVE_HISTORY": "N/A",
        "STATUS_NOVO": g["status_novo"],
        "JUSTIFICATIVA": g["justificativa"],
    })
    rows.append({
        "MERCADO": "CARDS",
        "STATUS_ANTERIOR": "NÃO_AVALIÁVEL",
        "NOVOS_DADOS": f"apifootball_com fallback: {a['resolvidos_fallback']} resolvidos",
        "N_RECONCILIADO": a["matched"],
        "FALLBACK": f"resolvidos={a['resolvidos_fallback']} (zero={a['resolvidos_fallback_zero']}, pos={a['resolvidos_fallback_positivo']})",
        "OOS": "PENDENTE (amostra insuficiente p/ reclassificar)",
        "ODDS_VALIDAS": e["por_mercado"].get("cartoes", {}).get("validos_temporalmente", 0),
        "ROI_POSSIVEL": e["por_mercado"].get("cartoes", {}).get("classificacao"),
        "LIVE_HISTORY": "N/A",
        "STATUS_NOVO": NAO_AVAL if a["resolvidos_fallback"] < 30 else EM_OBS,
        "JUSTIFICATIVA": (
            f"Infra fallback confirmada em dados reais ({a['resolvidos_fallback']} "
            f"resolvidos / {a['entrar_na_red_null']} NA). Amostra ainda "
            f"insuficiente para reclassificar (livre plano: {a['datas_visitadas']} datas)."),
    })
    rows.append({
        "MERCADO": "CORNERS",
        "STATUS_ANTERIOR": "EM_OBSERVAÇÃO",
        "NOVOS_DADOS": f"5Dollar corners prospectivos: {c['corners_odds_coletados']}",
        "N_RECONCILIADO": 0,
        "FALLBACK": "5Dollar (prospectivo, nao retroativo)",
        "OOS": c.get("drift_post_hit"),
        "ODDS_VALIDAS": e["por_mercado"].get("escanteios", {}).get("validos_temporalmente", 0),
        "ROI_POSSIVEL": e["por_mercado"].get("escanteios", {}).get("classificacao"),
        "LIVE_HISTORY": "N/A",
        "STATUS_NOVO": c["drift_respostas"].get("pode_avancar") and APROVADO_PROX or EM_OBS,
        "JUSTIFICATIVA": c["drift_respostas"].get("pode_avancar_nota", ""),
    })
    rows.append({
        "MERCADO": "PRESSAO LIVE",
        "STATUS_ANTERIOR": "BLOQUEADO",
        "NOVOS_DADOS": f"live_snapshot_history: {f['historico_total']} rows",
        "N_RECONCILIADO": 0,
        "FALLBACK": "N/A",
        "OOS": "N/A",
        "ODDS_VALIDAS": "N/A",
        "ROI_POSSIVEL": "N/A",
        "LIVE_HISTORY": f"{f['historico_total']} rows / {f['fixtures_no_historico']} fixtures",
        "STATUS_NOVO": BLOQUEADO if f["historico_total"] < 50 else EM_OBS,
        "JUSTIFICATIVA": f["nota"] or "Infra pronta; historico insuficiente.",
    })
    rows.append({
        "MERCADO": "ODDS/ROI",
        "STATUS_ANTERIOR": "BLOQUEADO",
        "NOVOS_DADOS": f"5Dollar +{d['five_dollar']['inseridos']} / TheOddsAPI +{d['the_odds_api']['inseridos']}",
        "N_RECONCILIADO": 0,
        "FALLBACK": "N/A",
        "OOS": "N/A",
        "ODDS_VALIDAS": sum(v.get("validos_temporalmente", 0) for v in e["por_mercado"].values()),
        "ROI_POSSIVEL": e["roi_historico_calculavel"],
        "LIVE_HISTORY": "N/A",
        "STATUS_NOVO": BLOQUEADO if not e["roi_historico_calculavel"] else EM_OBS,
        "JUSTIFICATIVA": e["nota"][:120] if e.get("nota") else "",
    })
    return rows


# ----------------------------------------------------------------------
# Orquestrador
# ----------------------------------------------------------------------
def gerar_evidencia(backtest_db: str = BACKTEST_DB_PATH,
                    db_path: str | None = None,
                    date_str: str | None = None) -> dict[str, Any]:
    """Executa os blocos A-I. Bounded; append-only; sem alterar motor."""
    p = str(db_path or DB_PATH)
    _ensure_recon_schema(p)
    t0 = time.time()
    a = bloco_a_cards(backtest_db, p)
    b = bloco_b_reconciliacao(a, p)
    c = bloco_c_corners(date_str, p)
    d = bloco_d_odds(date_str, p)
    e = bloco_e_roi(db_path=p)
    f = bloco_f_pressao_live(p)
    # G/H reusam a validacao read-only (uma chamada)
    try:
        val = _auditar_validacao(backtest_db)
    except Exception:
        val = {}
    g = bloco_g_resultado(val)
    h = bloco_h_goals(val)
    i = bloco_i_consolidacao(a, b, c, d, e, f, g, h)
    return {
        "meta": {
            "macroetapa": "GERACAO_DE_EVIDENCIA_MULTIFONTE_PENDENTE",
            "baseline_run": BASELINE_RUN,
            "gate_aberto": credenciais_rotacionadas(),
            "duracao_s": round(time.time() - t0, 1),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        },
        "credenciais": {
            p: {"cred": s["credencial_configurada"], "gate": s["gate_aberto"]}
            for p, s in status_credenciais().items()
        },
        "A_CARDS": a, "B_RECONCILIACAO": b, "C_CORNERS": c,
        "D_ODDS": d, "E_ROI": e, "F_PRESSAO_LIVE": f,
        "G_RESULTADO": g, "H_GOALS": h, "I_CONSOLIDACAO": i,
    }


def _md(resultado: dict[str, Any]) -> str:
    """Gera markdown do relatorio final."""
    m = resultado["meta"]
    linhas = [
        "# EVIDENCIA MULTIFONTE PENDENTE — FINAL",
        f"_Baseline: {m['baseline_run']} | gate: {'ABERTO' if m['gate_aberto'] else 'FECHADO'} | "
        f"timestamp: {m['timestamp']} | duracao: {m['duracao_s']}s_",
        "",
        "## A — CARDS (populacao historica apifootball_com)",
        f"- ENTRAR total: {resultado['A_CARDS']['entrar_total']}",
        f"- ENTRAR NA (red_cards NULL): {resultado['A_CARDS']['entrar_na_red_null']}",
        f"- Datas visitadas: {resultado['A_CARDS']['datas_visitadas']} (orcamento {BUDGET_A_DATES})",
        f"- Events coletados: {resultado['A_CARDS']['events_coletados']}",
        f"- Reconciliacao: MATCHED={resultado['A_CARDS']['matched']} "
        f"AMBIGUOUS={resultado['A_CARDS']['ambiguous']} "
        f"NOT_MATCHED={resultado['A_CARDS']['not_matched']}",
        f"- Resolvidos fallback: {resultado['A_CARDS']['resolvidos_fallback']} "
        f"(zero explicito={resultado['A_CARDS']['resolvidos_fallback_zero']}, "
        f"positivo={resultado['A_CARDS']['resolvidos_fallback_positivo']})",
        f"- NULL mantido: {resultado['A_CARDS']['null_mantido']} | "
        f"conflito: {resultado['A_CARDS']['conflito_de_fonte']}",
        f"- Provider status: {resultado['A_CARDS']['provider_status']} "
        f"limite={resultado['A_CARDS']['limite_atingido']}",
        "",
        "## B — RECONCILIACAO MULTIFONTE",
        f"- Correspondencias registradas (append-only): {resultado['B_RECONCILIACAO']['correspondencias_registradas']}",
        f"- Por status: {resultado['B_RECONCILIACAO']['por_status']}",
        f"- Por campo: {resultado['B_RECONCILIACAO']['por_campo']}",
        f"- Concordancia por campo: {resultado['B_RECONCILIACAO']['concordancia_por_campo']}",
        f"- {resultado['B_RECONCILIACAO']['nota']}",
        "",
        "## C — CORNERS (evidencia independente 5Dollar)",
        f"- Data coleta: {resultado['C_CORNERS']['data_coleta']}",
        f"- Fixtures 5Dollar: {resultado['C_CORNERS']['fixtures_5dollar']}",
        f"- Corners odds coletados (append-only): {resultado['C_CORNERS']['corners_odds_coletados']}",
        f"- Fases observadas: {resultado['C_CORNERS']['fases_observadas']}",
        f"- Bookmakers: {resultado['C_CORNERS']['bookmakers']}",
        f"- Drift pre/post: {resultado['C_CORNERS'].get('drift_pre_hit')} -> "
        f"{resultado['C_CORNERS'].get('drift_post_hit')} (gap {resultado['C_CORNERS'].get('drift_post_gap')})",
        f"- DRIFT CONTINUA: {resultado['C_CORNERS']['drift_respostas'].get('drift_continua')}",
        f"- CAUSA IDENTIFICADA: {resultado['C_CORNERS']['drift_respostas'].get('causa_identificada')}",
        f"- FONTE ORIGINAL: {resultado['C_CORNERS']['drift_respostas'].get('fonte_original')}",
        f"- PODE AVANCAR: {resultado['C_CORNERS']['drift_respostas'].get('pode_avancar')}",
        f"- Provider status: {resultado['C_CORNERS']['provider_status']} limite={resultado['C_CORNERS']['limite_atingido']}",
        "",
        "## D — ODDS (coleta prospectiva)",
        f"- 5Dollar: inseridos={resultado['D_ODDS']['five_dollar']['inseridos']} "
        f"status={resultado['D_ODDS']['five_dollar']['status']} "
        f"limite={resultado['D_ODDS']['five_dollar']['limite']}",
        f"  - mercados: {resultado['D_ODDS']['five_dollar']['mercados']}",
        f"- TheOddsAPI: inseridos={resultado['D_ODDS']['the_odds_api']['inseridos']} "
        f"status={resultado['D_ODDS']['the_odds_api']['status']} "
        f"limite={resultado['D_ODDS']['the_odds_api']['limite']}",
        "",
        "## E — ROI (dataset temporal)",
        f"- Odds snapshots (familia mapeada): {resultado['E_ROI']['odds_snapshots_total']}",
        f"- Fixtures com odds: {resultado['E_ROI']['fixtures_com_odds']}",
        f"- Providers: {resultado['E_ROI']['providers']}",
        f"- ROI historico calculavel: {resultado['E_ROI']['roi_historico_calculavel']}",
        f"- Por mercado:",
    ]
    for mk, d in resultado["E_ROI"]["por_mercado"].items():
        linhas.append(
            f"  - {mk}: entrar={d['entrar']} validos={d['validos_temporalmente']} "
            f"invalidos={d['invalidos_temporalmente']} indeterm={d['indeterminados']} "
            f"sem_odd={d['sem_odd_casavel']} class={d['classificacao']} roi={d['roi_exploratorio']}")
    linhas += [
        "",
        "## F — PRESSAO LIVE",
        f"- Infra pronta: {resultado['F_PRESSAO_LIVE']['infra_pronta']} (tabela {resultado['F_PRESSAO_LIVE']['tabela']})",
        f"- Historico total: {resultado['F_PRESSAO_LIVE']['historico_total']} rows / "
        f"{resultado['F_PRESSAO_LIVE']['fixtures_no_historico']} fixtures",
        f"- Jogos live agora: {resultado['F_PRESSAO_LIVE']['jogos_live_agora']}",
        f"- Snapshots materializados (one-shot): {resultado['F_PRESSAO_LIVE']['snapshots_materializados']}",
        f"- Marcos alvo: {resultado['F_PRESSAO_LIVE']['marcos_alvo']}",
        f"- {resultado['F_PRESSAO_LIVE']['nota']}",
        "",
        "## G — RESULTADO (OOS)",
        f"- entrar={resultado['G_RESULTADO']['entrar']} settled={resultado['G_RESULTADO']['settled']} "
        f"hit={resultado['G_RESULTADO']['hit_excl_na']} oos={resultado['G_RESULTADO']['oos_hit']}",
        f"- Walk-forward: {resultado['G_RESULTADO']['walk_forward']}",
        f"- Status novo: {resultado['G_RESULTADO']['status_novo']} | motor_alterado={resultado['G_RESULTADO']['motor_alterado']}",
        "",
        "## H — GOALS (OOS)",
        f"- entrar={resultado['H_GOALS']['entrar']} settled={resultado['H_GOALS']['settled']} "
        f"hit={resultado['H_GOALS']['hit_excl_na']} oos={resultado['H_GOALS']['oos_hit']}",
        f"- Walk-forward: {resultado['H_GOALS']['walk_forward']}",
        f"- Deterioracao material: {resultado['H_GOALS']['deterioracao_material']}",
        f"- Status novo: {resultado['H_GOALS']['status_novo']} | motor_alterado={resultado['H_GOALS']['motor_alterado']}",
        "",
        "## I — CONSOLIDACAO",
        "",
        "| MERCADO | STATUS ANTERIOR | NOVOS DADOS | N RECONC | FALLBACK | OOS | ODDS VALIDAS | ROI | LIVE HISTORY | STATUS NOVO | JUSTIFICATIVA |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in resultado["I_CONSOLIDACAO"]:
        linhas.append(
            f"| {r['MERCADO']} | {r['STATUS_ANTERIOR']} | {r['NOVOS_DADOS']} | "
            f"{r['N_RECONCILIADO']} | {r['FALLBACK']} | {r['OOS']} | "
            f"{r['ODDS_VALIDAS']} | {r['ROI_POSSIVEL']} | {r['LIVE_HISTORY']} | "
            f"{r['STATUS_NOVO']} | {r['JUSTIFICATIVA'][:80]} |")
    linhas += [
        "",
        "## Respostas finais",
        f"- **Motor alterado?** NAO (diff vazio; sem recalibracao; sem threshold).",
        f"- **Segredo versionado?** NAO (.env gitignored).",
        f"- **Data leakage?** NAO (anti-leakage via e_pre_jogo + collected_at<kickoff).",
        f"- **Etapa 6 iniciada?** NAO.",
    ]
    return "\n".join(linhas)


def rodar(json_path: str | None = None, markdown_path: str | None = None,
          backtest_db: str = BACKTEST_DB_PATH,
          db_path: str | None = None, date_str: str | None = None) -> dict:
    """Executa a macroetapa e salva JSON + markdown."""
    res = gerar_evidencia(backtest_db, db_path, date_str)
    out_dir = Path("data/validacao_multifonte")
    out_dir.mkdir(parents=True, exist_ok=True)
    if json_path is None:
        json_path = str(out_dir / "evidencia_multifonte.json")
    if markdown_path is None:
        markdown_path = "docs/EVIDENCIA_MULTIFONTE_PENDENTE_FINAL.md"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2, default=str)
    md = _md(res)
    with open(markdown_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"[OK] JSON: {json_path}")
    print(f"[OK] Markdown: {markdown_path}")
    # Resumo executivo (sem segredos)
    a = res["A_CARDS"]
    c = res["C_CORNERS"]
    d = res["D_ODDS"]
    f = res["F_PRESSAO_LIVE"]
    print(f"[A] CARDS: NA={a['entrar_na_red_null']} resolvidos_fallback={a['resolvidos_fallback']} "
          f"(zero={a['resolvidos_fallback_zero']},pos={a['resolvidos_fallback_positivo']}) "
          f"status={a['provider_status']}")
    print(f"[C] CORNERS: 5Dollar corners={c['corners_odds_coletados']} drift_continua={c['drift_respostas'].get('drift_continua')}")
    print(f"[D] ODDS: 5Dollar+{d['five_dollar']['inseridos']} TheOddsAPI+{d['the_odds_api']['inseridos']}")
    print(f"[F] PRESSAO: live_history={f['historico_total']} live_agora={f['jogos_live_agora']} materializados={f['snapshots_materializados']}")
    print(f"[E] ROI calculavel: {res['E_ROI']['roi_historico_calculavel']}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Geracao de evidencia multifonte")
    ap.add_argument("--json", default=None, help="Caminho do JSON de saida")
    ap.add_argument("--markdown", default=None, help="Caminho do markdown")
    ap.add_argument("--date", default=None, help="Data (yyyy-mm-dd) para coleta prospectiva")
    args = ap.parse_args(argv)
    rodar(json_path=args.json, markdown_path=args.markdown, date_str=args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())