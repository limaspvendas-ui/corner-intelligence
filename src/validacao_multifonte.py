"""MACROETAPA -- Validacao estatistica multifonte CONSOLIDADA.

Ferramenta de AUDITORIA read-only que consolida em um unico relatorio a
validacao de TODOS os mercados (GOALS, RESULTADO, CARDS, CORNERS) + ODDS/ROI
+ PRESSAO LIVE, usando:

  - data/backtest.db  (previsoes do baseline congelado -- Etapa 5/5B)
  - data/corner_intelligence.db (odds_snapshot_history multiprovider,
    live_snapshots, factual_resolution, source_conflict, api_cache)

REGRAS INEGOCIAVEIS (espelham o motor e a politica permanente):
  - FATO -> CALCULO -> INTERPRETACAO -> DECISAO.
  - NULL != ZERO; missing != zero; missing nunca entra no denominador de acerto.
  - Nao inventa dado; nao usa informacao futura (AS_OF preservado pelo engine).
  - Nao altera prediction apos resultado; nao recalibra; nao muda threshold.
  - Nao confunde cobertura com validacao; nao confunde odds disponiveis com ROI.
  - ROI somente com odd real temporalmente valida (collected_at < kickoff, phase
    compativel, linha compativel).

Esta camada NAO importa nem altera modulos decisorios (analysis, policy,
settlement, calibration, backtest, prejogo_opportunity, live_pressure, odds,
identity, app). Le bancos em modo read-only onde possivel. Nao escreve em
data/corner_intelligence.db.

Uso:
    python -m src.validacao_multifonte [--json PATH] [--markdown PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from src.backtest import BACKTEST_DB_PATH
from src.config import DB_PATH
from src.backtest_validacao import (
    _SETTLED, _metricas, blocos_walk_forward, cutoff_mediano,
    dividir_holdout, metricas_por_competicao, metricas_por_mercado,
)

# Status propostos (FASE G/consolidacao) -- NUNCA "APROVADO PARA MOTOR"
APROVADO_PROX = "APROVADO_PARA_PROXIMA_FASE"
EM_OBS = "EM_OBSERVACAO"
BLOQUEADO = "BLOQUEADO"
NAO_AVAL = "NÃO_AVALIÁVEL"

# Drift de corners: cutoff mecanico = cutoff mediano do baseline (2026-05).
# Nao inspecta taxa de acerto para escolher; reusa o cutoff cronologico.
DRIFT_CORNERS_CUTOFF = "2026-05-10"  # prefixo ISO; comparado por data do fixture


def _dt(d: str | None) -> datetime | None:
    if not d:
        return None
    try:
        return datetime.fromisoformat(d)
    except (TypeError, ValueError):
        try:
            return datetime.strptime(d[:19], "%Y-%m-%dT%H:%M:%S")
        except (TypeError, ValueError, IndexError):
            return None


def _mes(d: str | None) -> str | None:
    dt = _dt(d)
    return dt.strftime("%Y-%m") if dt else None


# ---------------------------------------------------------------------------
# Carregamento read-only
# ---------------------------------------------------------------------------
def _load_preds(bt_db: str = BACKTEST_DB_PATH) -> list[dict]:
    conn = sqlite3.connect(f"file:{bt_db}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM bt_predictions ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _metrics_entrar(preds: list[dict]) -> dict[str, Any]:
    """Metricas so das previsoes ENTRAR (decisao=ENTRAR)."""
    from src.backtest import DEC_ENTRAR
    entrar = [p for p in preds if p["decisao"] == DEC_ENTRAR]
    return _metricas(entrar)


# ---------------------------------------------------------------------------
# A/B/D -- revalidacao por mercado (mensal, por competicao, drift corners)
# ---------------------------------------------------------------------------
def _monthly(preds: list[dict]) -> dict[str, dict]:
    """Hit/gap/n por mes, sobre ENTRAR settled (excl NAO AVALIAVEL)."""
    from src.backtest import DEC_ENTRAR
    out: dict[str, dict] = {}
    by_month: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        if p["decisao"] != DEC_ENTRAR:
            continue
        m = _mes(p["date"])
        if m is None:
            continue
        by_month[m].append(p)
    for m in sorted(by_month):
        out[m] = _metricas(by_month[m])
    return out


def _by_competition(preds: list[dict], min_entrar: int = 20) -> dict[str, dict]:
    return metricas_por_competicao(preds, min_entrar=min_entrar)


def _drift_corners(preds: list[dict]) -> dict[str, Any]:
    """Investigacao dedicada do drift de escanteios (Etapa 5C aprofundada).

    Compara periodo pre-drift (date < cutoff) vs post-drift (date >= cutoff),
    mensal, distribuicao de linhas e probabilidades, e competicoes.
    Nao recalibra; so descreve.
    """
    from src.backtest import DEC_ENTRAR
    corners = [p for p in preds if p["mercado"] == "escanteios"]
    entrar = [p for p in corners if p["decisao"] == DEC_ENTRAR]

    # Comparacao por prefixo de data ISO (YYYY-MM-DD) -- lexicografica,
    # imune a mistura offset-aware/naive. Cutoff mecanico cronologico.
    cut_date = DRIFT_CORNERS_CUTOFF  # "2026-05-10"
    pre = [p for p in entrar if (p["date"] or "")[:10] < cut_date]
    pos = [p for p in entrar if (p["date"] or "")[:10] >= cut_date]

    # Distribuicao das linhas (totais) e probabilidades no ENTRAR
    def _dist_linhas(ps: list[dict]) -> dict:
        c: dict[str, int] = defaultdict(int)
        for p in ps:
            c[p["linha"]] += 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1])[:8])

    def _dist_prob(ps: list[dict]) -> dict:
        if not ps:
            return {}
        probs = [p["prob"] for p in ps]
        return {
            "n": len(probs),
            "min": round(min(probs), 4),
            "p25": round(statistics.quantiles(probs, n=4)[0], 4),
            "mediana": round(statistics.median(probs), 4),
            "p75": round(statistics.quantiles(probs, n=4)[2], 4),
            "max": round(max(probs), 4),
            "media": round(statistics.mean(probs), 4),
        }

    return {
        "cutoff_mecanico": DRIFT_CORNERS_CUTOFF
        + " (cutoff mediano cronologico do baseline; result-blind)",
        "pre_drift": {"n_entrar": len(pre), **_metricas(pre)},
        "post_drift": {"n_entrar": len(pos), **_metricas(pos)},
        "mensal": _monthly(corners),
        "dist_linhas_pre": _dist_linhas(pre),
        "dist_linhas_pos": _dist_linhas(pos),
        "dist_prob_pre": _dist_prob(pre),
        "dist_prob_pos": _dist_prob(pos),
        "por_competicao_pre": _by_competition(pre, min_entrar=15),
        "por_competicao_pos": _by_competition(pos, min_entrar=15),
    }


# ---------------------------------------------------------------------------
# C -- CARDS com fallback factual controlado
# ---------------------------------------------------------------------------
def _cards_audit(preds: list[dict]) -> dict[str, Any]:
    """CARDS: estado original vs com fallback factual.

    Fallback (apifootball_com red_cards) so e utilizavel quando:
      primary=NULL, fallback=valor explicito, fixture MATCHED.
    A tabela factual_resolution e append-only e esta VAZIA neste baseline
    (infra pronta, coleta historica nao executada -- exige chamadas
    autenticadas, fora do escopo read-only desta auditoria).
    """
    from src.backtest import DEC_ENTRAR
    cards = [p for p in preds if p["mercado"] == "cartoes"]
    entrar = [p for p in cards if p["decisao"] == DEC_ENTRAR]

    # Estado do fallback factual no banco
    n_resolutions = 0
    n_conflicts = 0
    n_resolved_fallback = 0
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        n_resolutions = conn.execute(
            "SELECT COUNT(*) FROM factual_resolution"
        ).fetchone()[0]
        n_resolved_fallback = conn.execute(
            "SELECT COUNT(*) FROM factual_resolution WHERE is_fallback=1 "
            "AND field='red_cards'"
        ).fetchone()[0]
        n_conflicts = conn.execute(
            "SELECT COUNT(*) FROM source_conflict"
        ).fetchone()[0]
        conn.close()
    except sqlite3.OperationalError:
        pass

    # Contagem primaria vs NULL no backtest (entrar)
    na = sum(1 for p in entrar if p["resultado_final"] == "NÃO AVALIÁVEL")
    settled = [p for p in entrar if p["resultado_final"] in _SETTLED]

    return {
        "total_previsoes": len(cards),
        "entrar": len(entrar),
        "entrar settled": len(settled),
        "entrar_nao_avaliavel": na,
        "primary_disponivel_settled": len(settled),
        "primary_null_entrar": na,
        "fallback_resolvido_red_cards": n_resolved_fallback,
        "factual_resolution_total": n_resolutions,
        "source_conflict_total": n_conflicts,
        "fallback_match_utilizavel": 0,  # tabela vazia => 0 recuperados
        "conflitos_de_fonte": n_conflicts,
        "ainda_nao_avaliavel": na - n_resolved_fallback,
        "amostra_final_avaliavel": len(settled) + n_resolved_fallback,
        "oos_possivel": (len(settled) + n_resolved_fallback) >= 30,
        "nota": (
            "Infra de fallback factual pronta (resolucao_factual.py) mas "
            "NUNCA executada sobre historico: factual_resolution=0, "
            "source_conflict=0. Popular exige chamadas autenticadas "
            "apifootball_com por data historica (coleta, nao auditoria "
            "read-only). Dataset derivado com fallback = VAZIO neste "
            "momento; CARDS permanece limitado por red_cards NULL."
        ),
        "metricas_entrar": _metricas(entrar),
        "mensal": _monthly(cards),
    }


# ---------------------------------------------------------------------------
# E -- ODDS / ROI com alinhamento temporal estrito
# ---------------------------------------------------------------------------
def _odds_roi_audit(preds: list[dict]) -> dict[str, Any]:
    """Auditoria de alinhamento temporal odd x prediction.

    Classifica cada ENTRAR como VALIDO/INDETERMINADO/INVALIDO temporalmente.
    ROI somente sobre VALIDO temporally + linha compativel + phase compativel.
    """
    from src.backtest import DEC_ENTRAR

    # 1. Mapa de odds por (fixture_id, familia, value) do snapshot history
    #    So odds com collected_at < fixture_date (pre-match) e phase compativel.
    odds_by_fx: dict[int, list[dict]] = defaultdict(list)
    fixtures_odds: set[int] = set()
    providers_set: set[str] = set()
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT fixture_id, familia, value_feed, odd, bookmaker, provider, "
            "phase, e_pre_jogo, collected_at, fixture_date, subfamilia, linha "
            "FROM odds_snapshot_history WHERE familia != 'UNMAPPED'"
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        rows = []

    for r in rows:
        (fx, fam, val, odd, bm, prov, phase, epre, coll_at, fx_date,
         subf, linha) = r
        if odd is None:
            continue
        odds_by_fx[fx].append({
            "familia": fam, "value": val, "odd": odd, "bookmaker": bm,
            "provider": prov, "phase": phase, "e_pre_jogo": epre,
            "collected_at": coll_at, "fixture_date": fx_date,
            "subfamilia": subf, "linha": linha,
        })
        fixtures_odds.add(fx)
        providers_set.add(prov)

    # 2. Para cada ENTRAR, tentar casar odd temporalmente valida
    #    Validacao temporal: collected_at (epoch) < kickoff (fixture_date).
    #    O backtest nao guarda odd_real (todos NULL); o match e feito aqui
    #    pela linha + mercado.
    def _kickoff_epoch(p: dict) -> float | None:
        dt = _dt(p["date"])
        return dt.timestamp() if dt else None

    def _linha_to_value(linha: str, mercado: str) -> str | None:
        """Converte linha do motor em value_feed comparavel (Over X.5 etc)."""
        from src.settlement import _parse_linha
        parsed = _parse_linha(linha)
        if parsed is not None:
            direcao, valor, _ = parsed
            return f"{direcao} {valor:g}"
        return linha  # 1X2/DC/AH: casa por texto exato

    resultados: dict[str, dict] = {}
    for mercado in ("gols", "escanteios", "cartoes", "resultado"):
        entrar = [p for p in preds if p["mercado"] == mercado
                  and p["decisao"] == DEC_ENTRAR]
        validos = []
        indeterm = 0
        invalidos = 0
        sem_odd = 0
        pl_total = 0.0
        stake = 0
        odds_usadas = []
        for p in entrar:
            fx = p["fixture_id"]
            kick = _kickoff_epoch(p)
            cands = odds_by_fx.get(fx, [])
            if not cands:
                sem_odd += 1
                continue
            alvo = _linha_to_value(p["linha"], mercado)
            # Filtra: pre-match (collected_at < kickoff) + value compativel
            matched = None
            for o in cands:
                if o["familia"] != mercado:
                    continue
                if o["value"] != alvo:
                    continue
                if kick is None or o["collected_at"] is None:
                    indeterm += 1
                    continue
                if o["collected_at"] >= kick:
                    # odd apos kickoff => invalida temporalmente
                    invalidos += 1
                    continue
                # phase: pre-match ou None (api_football) sao validos para
                # decisao pre-jogo; INPLAY e CLOSING futura sao invalidos
                ph = (o["phase"] or "").upper()
                if ph == "INPLAY":
                    invalidos += 1
                    continue
                if ph == "CLOSING":
                    # closing pre-kickoff e valido (odd de fechamento pre-match)
                    pass
                matched = o
                break
            if matched is None:
                if indeterm == 0 and invalidos == 0:
                    sem_odd += 1
                continue
            # ROI: settle pelo resultado_final conhecido
            res = p["resultado_final"]
            odd = matched["odd"]
            pl = None
            if res == "GANHA":
                pl = odd - 1.0
            elif res == "PERDIDA":
                pl = -1.0
            elif res == "DEVOLVIDA":
                pl = 0.0
            elif res == "MEIA VITÓRIA":
                pl = (odd - 1.0) / 2.0
            elif res == "MEIA DERROTA":
                pl = -0.5
            elif res == "NÃO AVALIÁVEL":
                # sem resultado => nao entra em ROI
                validos.append({"fx": fx, "status": "NA", "odd": odd})
                continue
            if pl is not None:
                pl_total += pl
                stake += 1
                odds_usadas.append(odd)
                validos.append({
                    "fx": fx, "status": "SETTLED", "odd": odd,
                    "res": res, "pl": round(pl, 4),
                    "provider": matched["provider"],
                    "bookmaker": matched["bookmaker"],
                    "phase": matched["phase"],
                })
        roi = (pl_total / stake) if stake else None
        resultados[mercado] = {
            "entrar": len(entrar),
            "validos_temporalmente": len(validos),
            "invalidos_temporalmente": invalidos,
            "indeterminados": indeterm,
            "sem_odd_casavel": sem_odd,
            "settled_com_odd": stake,
            "stake_units": stake,
            "pl_total": round(pl_total, 4) if stake else None,
            "roi": round(roi, 4) if roi is not None else None,
            "avg_odd": round(statistics.mean(odds_usadas), 4) if odds_usadas else None,
            "hit_rate_settled": (
                round(sum(1 for v in validos if v["status"] == "SETTLED"
                          and v.get("res") == "GANHA") / stake, 4) if stake else None
            ),
        }

    return {
        "odds_snapshots_total_familia_mapeada": sum(len(v) for v in odds_by_fx.values()),
        "fixtures_com_odds": len(fixtures_odds),
        "providers": sorted(providers_set),
        "por_mercado": resultados,
        "roi_historico_calculavel": any(
            resultados[m]["roi"] is not None for m in resultados),
        "nota": (
            "Odds prospectivas cobrem 2026-09-06..2026-09-11 (91 fixtures). "
            "Backtest cobre 2025-10..2026-09 (3627 fixtures). Sobreposicao "
            "temporal ~ zero: quasi todas as previsoes ENTRAR do backtest "
            "nao tem odd prospectiva casavel. ROI historico = NAO "
            "CALCULAVEL; coleta prospectiva deve continuar."
        ),
    }


# ---------------------------------------------------------------------------
# F -- PRESSAO LIVE
# ---------------------------------------------------------------------------
def _pressao_live_audit() -> dict[str, Any]:
    n = 0
    fixtures = set()
    sample: dict | None = None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT fixture_id, snapshot, updated_at FROM live_snapshots"
        ).fetchall()
        n = len(rows)
        for fx, snap, ua in rows:
            fixtures.add(fx)
            if sample is None:
                try:
                    s = json.loads(snap)
                    sample = {
                        "fixture_id": s.get("fixture_id"),
                        "home": s.get("home_team_name"),
                        "away": s.get("away_team_name"),
                        "status": s.get("status"),
                        "elapsed": s.get("elapsed"),
                        "date": s.get("date_local"),
                        "corners_home": (s.get("stats_home") or {}).get("Corner Kicks"),
                        "corners_away": (s.get("stats_away") or {}).get("Corner Kicks"),
                    }
                except Exception:
                    sample = None
        conn.close()
    except sqlite3.OperationalError:
        pass
    return {
        "snapshots": n,
        "fixtures_distintos": len(fixtures),
        "amostra": sample,
        "historico_suficiente": False,
        "nota": (
            "live_snapshots tem 1 linha (1 fixture, 1 snapshot de min 36). "
            "Nao existe serie temporal imutavel por fixture (live_snapshot_"
            "history nao materializada). Pressao 5/10/15 nao e avaliavel: "
            "exige multiplos snapshots por fixture em minutos distintos. "
            "Manter BLOQUEADO; coletar series temporais live por fixture "
            "(min 1, 15, 30, 45, 60, 75, 90) para cada jogo elegivel ao "
            "vivo. Nao fabricar nem backfillar com dados pos-jogo."
        ),
    }


# ---------------------------------------------------------------------------
# G -- Consolidacao
# ---------------------------------------------------------------------------
def _consolidacao(a: dict, b: dict, c: dict, d: dict, e: dict, f: dict) -> list[dict]:
    rows = [
        {"mercado": "GOALS",
         "cobertura": "VALIDADO (api_football, cache)", "amostra": a["oos"]["settled"],
         "oos": a["oos"]["hit_excl_na"], "estabilidade_temporal": "ESTAVEL (0.93-0.95 wf)",
         "calibracao": f"gap {a['oos']['gap_obs_pred']}", "odds_reais": "0 casavel",
         "roi_validavel": "NAO", "status_atual": "OPERACIONAL",
         "status_proposto": APROVADO_PROX,
         "justificativa": "Estavel OOS, calibrado, amostra madura; sem odd real -> ROI pendente"},
        {"mercado": "RESULTADO",
         "cobertura": "VALIDADO (api_football)", "amostra": b["oos"]["settled"],
         "oos": b["oos"]["hit_excl_na"], "estabilidade_temporal": "VARIAVEL (0.88-0.98 wf)",
         "calibracao": f"gap {b['oos']['gap_obs_pred']}", "odds_reais": "0 casavel",
         "roi_validavel": "NAO", "status_atual": "EXPERIMENTAL",
         "status_proposto": EM_OBS,
         "justificativa": "n OOS=108; hit bom mas variabilidade temporal + amostra pequena; manter observacao"},
        {"mercado": "CARDS",
         "cobertura": "LIMITADO (red_cards NULL 5D)", "amostra": c["amostra_final_avaliavel"],
         "oos": c["metricas_entrar"].get("hit_excl_na"),
         "estabilidade_temporal": "INSTAVEL (0.82-1.0 wf, n pequeno)",
         "calibracao": f"gap {c['metricas_entrar'].get('gap_obs_pred')}",
         "odds_reais": "0 casavel (odds existem mas sem outcome)",
         "roi_validavel": "NAO", "status_atual": "NAO AVALIAVEL",
         "status_proposto": NAO_AVAL,
         "justificativa": "207/307 ENTRAR NA por red_cards NULL; fallback infra pronto mas vazio (0 resolucoes); dataset derivado com fallback VAZIO"},
        {"mercado": "CORNERS",
         "cobertura": "VALIDADO (api_football)", "amostra": d["post_drift"]["settled"],
         "oos": d["post_drift"]["hit_excl_na"],
         "estabilidade_temporal": "DRIFT (0.93->0.87 pos-cutoff)",
         "calibracao": f"gap pos {d['post_drift']['gap_obs_pred']}",
         "odds_reais": "0 casavel",
         "roi_validavel": "NAO", "status_atual": "EM OBSERVACAO (drift)",
         "status_proposto": EM_OBS,
         "justificativa": "Drift confirmado pos-2026-05: hit 0.928->0.872, gap -0.054 (superconfianca); causa nao identificada; nao recalibrar"},
        {"mercado": "PRESSAO LIVE",
         "cobertura": "NAO MATERIALIZADO", "amostra": f["snapshots"],
         "oos": None, "estabilidade_temporal": "N/A",
         "calibracao": "N/A", "odds_reais": "N/A",
         "roi_validavel": "NAO", "status_atual": "AGUARDANDO HISTORICO",
         "status_proposto": BLOQUEADO,
         "justificativa": "1 snapshot/1 fixture; sem serie temporal por fixture; serie imutavel live_snapshot_history nao materializada"},
        {"mercado": "ODDS/ROI",
         "cobertura": "3 providers (api_football/the_odds/5dollar)",
         "amostra": e["fixtures_com_odds"],
         "oos": None, "estabilidade_temporal": "N/A",
         "calibracao": "N/A", "odds_reais": f"{e['fixtures_com_odds']} fixtures",
         "roi_validavel": "NAO (0 casavel)",
         "status_atual": "NAO AVALIAVEL",
         "status_proposto": BLOQUEADO,
         "justificativa": "91 fixtures com odds prospectivas vs 3627 do backtest; sobreposicao ~0; coleta prospectiva deve continuar"},
    ]
    return rows


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------
def auditar(bt_db: str = BACKTEST_DB_PATH) -> dict[str, Any]:
    preds = _load_preds(bt_db)
    cutoff = cutoff_mediano(preds)
    ant, pos = dividir_holdout(preds, cutoff)

    # A -- GOALS
    goals_preds = [p for p in preds if p["mercado"] == "gols"]
    a = {
        "total_previsoes": len(goals_preds),
        "anterior": metricas_por_mercado(ant).get("gols", {}),
        "oos": metricas_por_mercado(pos).get("gols", {}),
        "mensal": _monthly(goals_preds),
        "por_competicao_oos": _by_competition(
            [p for p in pos if p["mercado"] == "gols"]),
        "walk_forward": [
            {"bloco": nome, "gols": metricas_por_mercado(bl).get("gols", {})}
            for nome, bl in blocos_walk_forward(preds, 6)
        ],
    }

    # B -- RESULTADO
    res_preds = [p for p in preds if p["mercado"] == "resultado"]
    b = {
        "total_previsoes": len(res_preds),
        "anterior": metricas_por_mercado(ant).get("resultado", {}),
        "oos": metricas_por_mercado(pos).get("resultado", {}),
        "mensal": _monthly(res_preds),
        "por_competicao_oos": _by_competition(
            [p for p in pos if p["mercado"] == "resultado"], min_entrar=10),
        "walk_forward": [
            {"bloco": nome, "resultado": metricas_por_mercado(bl).get("resultado", {})}
            for nome, bl in blocos_walk_forward(preds, 6)
        ],
    }

    # C -- CARDS
    c = _cards_audit(preds)

    # D -- CORNERS drift
    d = _drift_corners(preds)

    # E -- ODDS/ROI
    e = _odds_roi_audit(preds)

    # F -- PRESSAO
    f = _pressao_live_audit()

    # G -- Consolidacao
    g = _consolidacao(a, b, c, d, e, f)

    return {
        "etapa": "VALIDACAO_ESTATISTICA_MULTIFONTE_FINAL",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "baseline_run": "bt-20260913224716-PRE_GAME",
        "cutoff_holdout": cutoff,
        "total_previsoes": len(preds),
        "total_fixtures": len({p["fixture_id"] for p in preds}),
        "A_GOALS": a,
        "B_RESULTADO": b,
        "C_CARDS": c,
        "D_CORNERS": d,
        "E_ODDS_ROI": e,
        "F_PRESSAO_LIVE": f,
        "G_CONSOLIDACAO": g,
        "respostas_macroetapa": {
            "drift_cornes_confirmado": (
                d["post_drift"]["hit_excl_na"] is not None
                and d["pre_drift"]["hit_excl_na"] is not None
                and d["pre_drift"]["hit_excl_na"] - d["post_drift"]["hit_excl_na"] > 0.03
            ),
            "causa_identificada": False,
            "fonte_invalida_drift": "EVIDENCIA_INSUFICIENTE",
            "modelo_cornes_estavel": False,
            "roi_historico_calculavel": e["roi_historico_calculavel"],
            "roi_prospectivo_continuar": True,
            "pressao_historico_suficiente": False,
            "existe_motivo_alterar_motor": False,
            "infra_multifonte_util": "PARCIAL",
            "pronto_integracao_controlada": False,
        },
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def _md(res: dict) -> str:
    L = []
    L.append("# VALIDACAO ESTATISTICA MULTIFONTE — FINAL\n")
    L.append(f"_Baseline: {res['baseline_run']} | cutoff holdout: {res['cutoff_holdout']}_\n")
    L.append(f"_Previsoes: {res['total_previsoes']} | fixtures: {res['total_fixtures']}_\n\n")
    L.append("---\n\n## A — GOALS\n")
    a = res["A_GOALS"]
    L.append(f"- Anterior: hit={a['anterior'].get('hit_excl_na')} gap={a['anterior'].get('gap_obs_pred')} n={a['anterior'].get('settled')}\n")
    L.append(f"- **OOS (posterior): hit={a['oos'].get('hit_excl_na')} gap={a['oos'].get('gap_obs_pred')} n={a['oos'].get('settled')} brier={a['oos'].get('brier')}**\n")
    wf_gols = ", ".join(str(w["gols"].get("hit_excl_na")) for w in a["walk_forward"])
    L.append(f"- Walk-forward: {wf_gols}\n")
    L.append(f"- Status: **{res['G_CONSOLIDACAO'][0]['status_proposto']}**\n\n")
    L.append("## B — RESULTADO\n")
    b = res["B_RESULTADO"]
    L.append(f"- Anterior: hit={b['anterior'].get('hit_excl_na')} gap={b['anterior'].get('gap_obs_pred')} n={b['anterior'].get('settled')}\n")
    L.append(f"- **OOS: hit={b['oos'].get('hit_excl_na')} gap={b['oos'].get('gap_obs_pred')} n={b['oos'].get('settled')}**\n")
    wf_res = ", ".join(str(w["resultado"].get("hit_excl_na")) for w in b["walk_forward"])
    L.append(f"- Walk-forward: {wf_res}\n")
    L.append(f"- Status: **{res['G_CONSOLIDACAO'][1]['status_proposto']}** (experimental)\n\n")
    L.append("## C — CARDS\n")
    c = res["C_CARDS"]
    L.append(f"- ENTRAR: {c['entrar']} | settled: {c['entrar settled']} | NA: {c['entrar_nao_avaliavel']}\n")
    L.append(f"- Fallback factual: resolucoes={c['factual_resolution_total']} conflitos={c['source_conflict_total']} recuperados_red={c['fallback_resolvido_red_cards']}\n")
    L.append(f"- Amostra final avaliavel: {c['amostra_final_avaliavel']} | OOS possivel: {c['oos_possivel']}\n")
    L.append(f"- Status: **{res['G_CONSOLIDACAO'][2]['status_proposto']}** — {c['nota'][:120]}...\n\n")
    L.append("## D — CORNERS (drift)\n")
    d = res["D_CORNERS"]
    L.append(f"- Pre-drift: hit={d['pre_drift'].get('hit_excl_na')} gap={d['pre_drift'].get('gap_obs_pred')} n={d['pre_drift'].get('settled')}\n")
    L.append(f"- **Post-drift: hit={d['post_drift'].get('hit_excl_na')} gap={d['post_drift'].get('gap_obs_pred')} n={d['post_drift'].get('settled')}**\n")
    L.append(f"- DRIFT CONFIRMADO: **{res['respostas_macroetapa']['drift_cornes_confirmado']}**\n")
    L.append(f"- Causa identificada: {res['respostas_macroetapa']['causa_identificada']} | Fonte invalida hipotese: {res['respostas_macroetapa']['fonte_invalida_drift']}\n")
    L.append(f"- Status: **{res['G_CONSOLIDACAO'][3]['status_proposto']}**\n\n")
    L.append("## E — ODDS/ROI\n")
    e = res["E_ODDS_ROI"]
    L.append(f"- Fixtures com odds prospectivas: {e['fixtures_com_odds']} | providers: {', '.join(e['providers'])}\n")
    for m, v in e["por_mercado"].items():
        L.append(f"  - {m}: entrar={v['entrar']} validos={v['validos_temporalmente']} settled_odd={v['settled_com_odd']} roi={v['roi']}\n")
    L.append(f"- ROI historico calculavel: **{res['respostas_macroetapa']['roi_historico_calculavel']}**\n")
    L.append(f"- Status: **{res['G_CONSOLIDACAO'][5]['status_proposto']}**\n\n")
    L.append("## F — PRESSAO LIVE\n")
    fxt = res["F_PRESSAO_LIVE"]
    L.append(f"- Snapshots: {fxt['snapshots']} | fixtures: {fxt['fixtures_distintos']}\n")
    L.append(f"- Historico suficiente: {fxt['historico_suficiente']} | Status: **{res['G_CONSOLIDACAO'][4]['status_proposto']}**\n\n")
    L.append("## G — CONSOLIDACAO\n\n")
    L.append("| MERCADO | COBERTURA | AMOSTRA | OOS | ESTABILIDADE | CALIBRACAO | ODDS | ROI | STATUS_ATUAL | STATUS_PROPOSTO |\n")
    L.append("|---|---|---|---|---|---|---|---|---|---|\n")
    for r in res["G_CONSOLIDACAO"]:
        L.append(f"| {r['mercado']} | {r['cobertura']} | {r['amostra']} | {r['oos']} | {r['estabilidade_temporal']} | {r['calibracao']} | {r['odds_reais']} | {r['roi_validavel']} | {r['status_atual']} | {r['status_proposto']} |\n")
    L.append("\n---\n\n## Respostas finais\n\n")
    rp = res["respostas_macroetapa"]
    L.append(f"- **QUAIS MERCADOS PASSARAM?** GOALS ({APROVADO_PROX})\n")
    L.append(f"- **EM OBSERVACAO?** RESULTADO, CORNERS\n")
    L.append(f"- **BLOQUEADOS?** PRESSAO LIVE, ODDS/ROI\n")
    L.append(f"- **NAO AVALIAVEIS?** CARDS\n")
    L.append(f"- **EXISTE MOTIVO TECNICO PARA ALTERAR O MOTOR AGORA?** NAO\n")
    L.append(f"- **INFRA MULTIFONTE FOI UTIL?** PARCIAL (infra pronta, populacao historica pendente)\n")
    L.append(f"- **PRONTOS PARA INTEGRACAO CONTROLADA DO APROVADO?** NAO (sem odd real para ROI; integracao exige Etapa 6 autorizada)\n")
    return "".join(L)


def _main() -> None:
    p = argparse.ArgumentParser(
        description="Validacao estatistica multifonte consolidada (macroetapa).")
    p.add_argument("--json", default=None, help="Caminho para salvar JSON.")
    p.add_argument("--markdown", default=None, help="Caminho para salvar Markdown.")
    p.add_argument("--bt-db", default=BACKTEST_DB_PATH)
    args = p.parse_args()
    res = auditar(args.bt_db)
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    if args.markdown:
        os.makedirs(os.path.dirname(args.markdown) or ".", exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(_md(res))
    # Resumo em stdout
    print(json.dumps({
        "A_GOALS_oos": res["A_GOALS"]["oos"],
        "B_RESULTADO_oos": res["B_RESULTADO"]["oos"],
        "C_CARDS": {k: res["C_CARDS"][k] for k in
                    ("entrar", "entrar settled", "entrar_nao_avaliavel",
                     "factual_resolution_total", "amostra_final_avaliavel")},
        "D_CORNERS": {"pre": res["D_CORNERS"]["pre_drift"],
                      "post": res["D_CORNERS"]["post_drift"],
                      "drift_confirmado": res["respostas_macroetapa"]["drift_cornes_confirmado"]},
        "E_ODDS_ROI": {"fixtures_com_odds": res["E_ODDS_ROI"]["fixtures_com_odds"],
                       "por_mercado": res["E_ODDS_ROI"]["por_mercado"]},
        "F_PRESSAO": res["F_PRESSAO_LIVE"],
        "respostas": res["respostas_macroetapa"],
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()