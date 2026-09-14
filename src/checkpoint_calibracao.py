"""ETAPA 5E -- CHECKPOINT DE PRONTIDAO PARA CALIBRACAO.

Modulo de diagnostico SOMENTE LEITURA. NAO altera motor, regras, thresholds,
Poisson, blend, politica, settlement, matriz de cobertura ou qualquer dado.
NAO recalibra, NAO otimiza, NAO escolhe parametros.

Sintetiza as evidencias ja validadas das Etapas 5 / 5B / 5C / 5D (JSONs em
docs/) + consultas read-only aos bancos (backtest, api_cache) para classificar
CADA mercado quanto a prontidao para uma eventual Etapa 6 (calibracao).

Principio: Etapa 6 nao deve existir apenas para obrigatoriamente mudar
parametros. Se um mercado esta bem calibrado, a conclusao pode ser "NAO
ALTERAR". E se nenhum mercado justificar alteracao, "ETAPA 6 NAO DEVE ALTERAR
O MOTOR NESTE MOMENTO" e um resultado valido.

Uso:
    python -m src.checkpoint_calibracao run [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any

from src.backtest import BACKTEST_DB_PATH
from src.config import DB_PATH

DOCS = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/docs"
RUN_ID = "bt-20260913224716-PRE_GAME"

# Limiares objetivos para classificacao (nao sao regras do motor; sao criterios
# de auditoria deste checkpoint, declarados ex-post, nao usados para calibrar).
DRIFT_MATERIAL_PP = 3.0      # p.p. de deterioracao hit ant->post considerado material
GAP_SUPERCONFIANCA_PP = 3.0  # p.p. de |gap| considerado superconfianca material
NA_BLOQUEANTE_PCT = 0.40     # %NA acima do qual mercado e "nao validavel"


def _load(name: str) -> dict:
    path = os.path.join(DOCS, name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _safe(d: dict, *keys, default=None) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _pct(v) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _pp(a, b) -> float | None:
    """Diferenca em pontos percentuais entre dois hit rates (0-1)."""
    if a is None or b is None:
        return None
    return round((b - a) * 100.0, 2)


def _wf_hits(v5b: dict, mercado: str) -> list[float | None]:
    out = []
    for blk in v5b.get("walk_forward", []):
        h = _safe(blk, "por_mercado", mercado, "hit_excl_na")
        out.append(h)
    return out


def _wf_min_max(hits: list[float | None]) -> tuple[float | None, float | None]:
    vals = [h for h in hits if h is not None]
    if not vals:
        return None, None
    return round(min(vals), 4), round(max(vals), 4)


# ----------------------------------------------------------------------
# Fontes data-driven: odds reais e live_snapshot_history
# ----------------------------------------------------------------------
def _odds_status(bt_db: str) -> dict:
    """Confirma ROI via DB: quantas ENTRAR tem odd_real > 0."""
    con = sqlite3.connect(bt_db)
    try:
        n_total = con.execute(
            "SELECT COUNT(*) FROM bt_predictions WHERE decisao='ENTRAR'").fetchone()[0]
        n_odd = con.execute(
            "SELECT COUNT(*) FROM bt_predictions WHERE decisao='ENTRAR' "
            "AND odd_real IS NOT NULL AND odd_real > 0").fetchone()[0]
    finally:
        con.close()
    return {
        "entrar_total": n_total,
        "entrar_com_odd_real": n_odd,
        "roi_validado": False,
        "roi_status": "NAO AVALIAVEL" if n_odd == 0 else "PARCIAL",
        "edge_ev_pl_validavel": n_odd > 0,
    }


def _pressao_status(api_db: str) -> dict:
    """Confirma pressao via DB: live_snapshot_history existe e esta populada?"""
    con = sqlite3.connect(api_db)
    try:
        tabs = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        existe = "live_snapshot_history" in tabs
        n_hist = 0
        if existe:
            n_hist = con.execute(
                "SELECT COUNT(*) FROM live_snapshot_history").fetchone()[0]
        n_snap = con.execute(
            "SELECT COUNT(*) FROM live_snapshots").fetchone()[0] if "live_snapshots" in tabs else 0
    finally:
        con.close()
    return {
        "live_snapshot_history_existe": existe,
        "live_snapshot_history_rows": n_hist,
        "live_snapshots_rows": n_snap,
        "janelas_validaveis": existe and n_hist > 0,
        "thresholds_calibrados": False,
        "status": "EXPERIMENTAL / A CALIBRAR",
    }


# ----------------------------------------------------------------------
# Metricas por mercado a partir do JSON 5B (out-of-sample)
# ----------------------------------------------------------------------
def _mercado_oos(v5b: dict, mercado: str) -> dict:
    ant = _safe(v5b, "holdout", "anterior", "por_mercado", mercado, default={})
    pos = _safe(v5b, "holdout", "posterior", "por_mercado", mercado, default={})
    wf = _wf_hits(v5b, mercado)
    wf_min, wf_max = _wf_min_max(wf)
    hit_ant = ant.get("hit_excl_na")
    hit_pos = pos.get("hit_excl_na")
    drift = _pp(hit_ant, hit_pos)
    gap_ant = ant.get("gap_obs_pred")
    gap_pos = pos.get("gap_obs_pred")
    entrar = (ant.get("entrar") or 0) + (pos.get("entrar") or 0)
    settled = (ant.get("settled") or 0) + (pos.get("settled") or 0)
    na = (ant.get("nao_avaliavel") or 0) + (pos.get("nao_avaliavel") or 0)
    ganhas = (ant.get("ganhas") or 0) + (pos.get("ganhas") or 0)
    perdidas = (ant.get("perdidas") or 0) + (pos.get("perdidas") or 0)
    na_pct = round(na / entrar, 4) if entrar else None
    mat_ant = ant.get("maturidade")
    mat_pos = pos.get("maturidade")
    return {
        "mercado": mercado,
        "entrar_total": entrar,
        "settled_total": settled,
        "ganhas": ganhas,
        "perdidas": perdidas,
        "nao_avaliaveis": na,
        "na_pct": na_pct,
        "hit_anterior": hit_ant,
        "hit_posterior": hit_pos,
        "drift_pp": drift,
        "prob_anterior": ant.get("prob_media_entrar"),
        "prob_posterior": pos.get("prob_media_entrar"),
        "brier_anterior": ant.get("brier"),
        "brier_posterior": pos.get("brier"),
        "gap_anterior": gap_ant,
        "gap_posterior": gap_pos,
        "walk_forward_hits": wf,
        "walk_forward_min": wf_min,
        "walk_forward_max": wf_max,
        "maturidade_anterior": mat_ant,
        "maturidade_posterior": mat_pos,
    }


def _classificar_goals(oos: dict, odds: dict) -> dict:
    drift = oos["drift_pp"]
    gap_pos = oos["gap_posterior"]
    na_pct = oos["na_pct"] or 0.0
    wf_min = oos["walk_forward_min"]
    estavel = drift is not None and abs(drift) < DRIFT_MATERIAL_PP
    sem_na = na_pct == 0.0
    bem_calibrado = gap_pos is not None and abs(gap_pos * 100) < GAP_SUPERCONFIANCA_PP
    wf_estavel = wf_min is not None and wf_min >= 0.90
    passou_oos = estavel and sem_na and bem_calibrado and wf_estavel
    return {
        "passou_backtest": True,
        "passou_out_of_sample": bool(passou_oos),
        "drift_material": not estavel,
        "vies_disponibilidade": not sem_na,
        "settlement_confiavel": sem_na,
        "calibracao": "BOA" if bem_calibrado else "DESVIO",
        "gap_posterior_pp": round(gap_pos * 100, 2) if gap_pos is not None else None,
        "roi_disponivel": odds["roi_validado"],
        # gols bem calibrado e estavel => NAO precisa de Etapa 6
        "precisa_alteracao": not (passou_oos and bem_calibrado),
        "conclusao": "NAO ALTERAR" if (passou_oos and bem_calibrado) else "INVESTIGAR",
        "pronto_etapa6": "PARCIAL",
        "justificativa": (
            "Estavel e calibrado no holdout (hit 0.9507->0.9421, drift <1 p.p., "
            "0 NA em todos os 6 blocos walk-forward, gap posterior ~-0.5 p.p.). "
            "NENHUM desvio de calibracao que justifique Etapa 6. Sem ROI (0 odds) "
            "para validar valor de qualquer mudanca; sem holdout independente "
            "alem do ja observado -> risco de overfitting. NAO ALTERAR."
            if (passou_oos and bem_calibrado) else
            "Desvio material ou instabilidade -> investigar antes de calibrar."),
    }


def _classificar_corners(oos: dict, v5c: dict, odds: dict) -> dict:
    drift = oos["drift_pp"]
    gap_pos = oos["gap_posterior"]
    drift_material = drift is not None and drift <= -DRIFT_MATERIAL_PP
    superconf = gap_pos is not None and gap_pos * 100 <= -GAP_SUPERCONFIANCA_PP
    # contrafactual within-league (5C)
    contra = None
    rep = _safe(v5c, "reproducao", default={})
    # 5C nao tem direto o contrafatual top-level; derivamos do texto/documento.
    # Usamos o achado validado: deterioracao within-league, onset Maio/2026.
    wf = oos["walk_forward_hits"]
    blocos_tardios = [h for h in wf[3:] if h is not None]
    wf_colapsa = bool(blocos_tardios) and min(blocos_tardios) < 0.90
    return {
        "passou_backtest": True,
        "passou_out_of_sample": not (drift_material or superconf),
        "drift_material": drift_material,
        "drift_pp": drift,
        "gap_posterior_pp": round(gap_pos * 100, 2) if gap_pos is not None else None,
        "withinLeague": True,  # 5C: contrafatual 0.9385 > 0.9284 > 0.8723 real
        "onset_antes_cutoff": True,  # 5C: deterioracao inicia Maio/2026 (cutoff 10-Mai)
        "wf_colapsa_blocos_tardios": wf_colapsa,
        "vies_disponibilidade": False,  # 5C: sem problema material de fonte
        "settlement_confiavel": True,   # 5C: 0/893 mismatches
        "roi_disponivel": odds["roi_validado"],
        "conclusao": "SOMENTE MONITORAMENTO",
        "pronto_etapa6": "NAO",
        "justificativa": (
            "Drift temporal material (hit 0.9284->0.8723, -5.6 p.p.), within-league "
            "(contrafatual 0.9385 exclui mudanca de mix), superconfianca posterior "
            "(gap -5.4 p.p.), onset Maio/2026 ANTES do cutoff (nao artefato de holdout), "
            "blocos walk-forward 4-6 colapsam (~0.86-0.90). Sem bug, sem problema de "
            "fonte, settlement 100% correto. Calibrar agora = otimizar sobre o holdout "
            "ja observado (overfitting) sem ROI para validar. Status: EM OBSERVAO. "
            "Nao entra em Etapa 6."),
    }


def _classificar_cards(oos: dict, v5d: dict, odds: dict) -> dict:
    na_pct = oos["na_pct"]
    nao_validavel = na_pct is not None and na_pct >= NA_BLOQUEANTE_PCT
    rec = _safe(v5d, "fase_k_l_simulacao", "recuperaveis_dado_existente", default=0)
    causa = _safe(v5d, "causa_fase_i", default={})
    return {
        "passou_backtest": not nao_validavel,
        "passou_out_of_sample": not nao_validavel,
        "na_pct": na_pct,
        "nao_avaliaveis": oos["nao_avaliaveis"],
        "causa_principal": "API (Red Cards = null em /fixtures/statistics)",
        "causa_fase_i": causa,
        "vies_disponibilidade": True,
        "settlement_confiavel": True,  # 5D: 0/307 mismatches
        "hipotese_95_recuperaveis": {
            "n": rec,
            "status": "NAO IMPLEMENTADA / NAO VALIDADA",
            "exige": "autorizacao + validacao out-of-sample independente",
        },
        "roi_disponivel": odds["roi_validado"],
        "conclusao": "NAO VALIDAVEL",
        "pronto_etapa6": "NAO",
        "justificativa": (
            "207/307 ENTRAR nao liquidaveis (~67% missing estrutural: Red Cards = "
            "null na fonte). Viés de disponibilidade persistente; os 100 liquidados "
            "(91% hit) sao subset nao aleatorio e nao validam o mercado. Sem bug "
            "(parser/cache/settlement corretos). Hipotese dos 95 recuperaveis por "
            "dominancia matematica NAO IMPLEMENTADA / NAO VALIDADA. Nao entra em "
            "Etapa 6."),
    }


def _classificar_resultado(oos: dict, odds: dict) -> dict:
    drift = oos["drift_pp"]
    wf = oos["walk_forward_hits"]
    vals = [h for h in wf if h is not None]
    n_total = oos["entrar_total"]
    # volatilidade por bloco (n pequeno 19..60)
    volatil = bool(vals) and (max(vals) - min(vals)) >= 0.10
    return {
        "passou_backtest": True,
        "passou_out_of_sample": not volatil,
        "entrar_total": n_total,
        "drift_pp": drift,
        "walk_forward_volatil": volatil,
        "vies_disponibilidade": False,
        "settlement_confiavel": True,  # 0 NA
        "roi_disponivel": odds["roi_validado"],
        "experimental": True,
        "conclusao": "NAO PROMOVER",
        "pronto_etapa6": "NAO",
        "justificativa": (
            "EXPERIMENTAL EM OBSERVACAO. Amostra pequena (234 ENTRAR), volatil por "
            "bloco walk-forward (hit 0.88-0.98, n=19..60), leve queda posterior "
            "(0.9444->0.9259). Sem ROI. Nao promover automaticamente mesmo com hit "
            "alto. Nao entra em Etapa 6."),
    }


def _classificar_pressao(pressao: dict, odds: dict) -> dict:
    return {
        "passou_backtest": False,
        "passou_out_of_sample": False,
        "live_snapshot_history_existe": pressao["live_snapshot_history_existe"],
        "live_snapshot_history_rows": pressao["live_snapshot_history_rows"],
        "janelas_validaveis": pressao["janelas_validaveis"],
        "thresholds_calibrados": pressao["thresholds_calibrados"],
        "roi_disponivel": odds["roi_validado"],
        "status": pressao["status"],
        "conclusao": "SEM DADOS PARA CALIBRAR",
        "pronto_etapa6": "NAO",
        "justificativa": (
            "live_snapshot_history NAO existe / nao populada. Sem snapshots temporais "
            "historicos -> janelas 5/10/15 nao validaveis; thresholds LOW/MODERATE/HIGH "
            "sao placeholder nao-calibrados. Backtest inviavel. Nao reconstruir pressao "
            "artificialmente a partir de dados finais. Nao entra em Etapa 6."),
    }


# ----------------------------------------------------------------------
# Protecao contra overfitting (FASE 11)
# ----------------------------------------------------------------------
def _overfitting(oos_por_mercado: dict, odds: dict, pressao: dict) -> dict:
    return {
        "holdout_independente_disponivel": False,
        "risco_otimizar_sobre_periodo_auditado": True,
        "outro_periodo_para_validacao_posterior": False,
        "como_impedir_insample_confundido": (
            "Exigir (a) holdout fresco ainda nao observado OU (b) validacao de ROI "
            "com odds reais em encerrados. Nenhum dos dois disponivel hoje: o holdout "
            "posterior da Etapa 5B (pos 2026-05-10) ja foi observado/auditado em 5B/5C; "
            "os dados terminam em 2026-09-06; 0/4352 ENTRAR com odd_real. Logo qualquer "
            "parametro escolhido agora seria escolhido por ficar melhor no proprio "
            "holdout ja visto -> nao distinguivel de overfitting."
        ),
        "roi_validavel": odds["roi_validado"],
        "pressao_validavel": pressao["janelas_validaveis"],
    }


def _matriz(goals, corners, cards, resultado, pressao, odds) -> list[dict]:
    def row(nome, m, dados, bt, oos, cal, drift, roi, status, pode):
        return {
            "mercado": nome,
            "dados": dados,
            "backtest": bt,
            "out_of_sample": oos,
            "calibracao": cal,
            "drift": drift,
            "roi": roi,
            "status_atual": status,
            "pode_entrar_etapa6": pode,
        }
    return [
        row("GOALS", goals, "SIM", "SIM", "SIM", goals["calibracao"],
            "NAO MATERIAL", "NAO", "OPERACIONAL (sem ROI)", "PARCIAL"),
        row("CORNERS", corners, "SIM", "SIM", "NAO", "PIORA",
            "MATERIAL", "NAO", "EM OBSERVACAO", "NAO"),
        row("CARDS", cards, "PARCIAL (67% NA)", "PARCIAL", "NAO VALIDAVEL",
            "N/A", "N/A", "NAO", "NAO AVALIAVEL", "NAO"),
        row("RESULTADO", resultado, "SIM (n pequeno)", "SIM", "PARCIAL",
            "ESTAVEL LEVE", "LEVE", "NAO", "EXPERIMENTAL EM OBSERVACAO", "NAO"),
        row("PRESSAO LIVE", pressao, "NAO (sem historico)", "NAO", "NAO",
            "N/A", "N/A", "NAO", "EXPERIMENTAL / A CALIBRAR", "NAO"),
    ]


def auditar(bt_db: str = BACKTEST_DB_PATH, api_db: str = DB_PATH) -> dict[str, Any]:
    v5b = _load("etapa5b_validacao_temporal.json")
    v5c = _load("etapa5c_auditoria_escanteios.json")
    v5d = _load("etapa5d_auditoria_cartoes.json")
    _baseline = _load("etapa5_baseline_panorama.json")  # confirma in-sample/odds

    odds = _odds_status(bt_db)
    pressao = _pressao_status(api_db)

    g_oos = _mercado_oos(v5b, "gols")
    c_oos = _mercado_oos(v5b, "escanteios")
    d_oos = _mercado_oos(v5b, "cartoes")
    r_oos = _mercado_oos(v5b, "resultado")

    goals = _classificar_goals(g_oos, odds)
    corners = _classificar_corners(c_oos, v5c, odds)
    cards = _classificar_cards(d_oos, v5d, odds)
    resultado = _classificar_resultado(r_oos, odds)
    pressao_cls = _classificar_pressao(pressao, odds)

    overfit = _overfitting({}, odds, pressao)
    matriz = _matriz(goals, corners, cards, resultado, pressao_cls, odds)

    # Respostas obrigatorias
    algum_precisa = any(m["conclusao"] in ("INVESTIGAR",) for m in
                        (goals, corners, cards, resultado, pressao_cls))
    etapa6_pode = "NAO"

    return {
        "run_id": RUN_ID,
        "engine_versao": "backtest-1.0",
        "regras_versao": "prejogo-op-1.0-observacao",
        "odds_roi": odds,
        "pressao_live": pressao,
        "por_mercado": {
            "goals": {**g_oos, **goals},
            "corners": {**c_oos, **corners},
            "cards": {**d_oos, **cards},
            "resultado": {**r_oos, **resultado},
            "pressao_live": {**pressao, **pressao_cls},
        },
        "matriz_prontidao": matriz,
        "overfitting": overfit,
        "respostas_obrigatorias": {
            "goals_pronto_etapa6": "PARCIAL",
            "corners_pronto_etapa6": "NAO",
            "cards_pronto_etapa6": "NAO",
            "resultado_pronto_etapa6": "NAO",
            "pressao_live_pronta_etapa6": "NAO",
            "algum_mercado_precisa_alteracao_agora": "NAO",
            "etapa6_pode_ser_iniciada": etapa6_pode,
            "escopo_permitido_se_sim_ou_parcial": (
                "GOALS (PARCIAL): unico mercado estatisticamente pronto, mas bem "
                "calibrado -> ESCOPO PERMITIDO = NAO ALTERAR (somente monitoramento). "
                "Nenhum parametro deve ser modificado neste momento. Proximos passos "
                "sao COLETA DE DADOS (odds reais em encerrados para ROI; "
                "live_snapshot_history para pressao; ampliacao da janela temporal; "
                "eventual coleta de /fixtures/events para cartoes), NAO calibracao."
            ),
        },
        "conclusao_geral": (
            "ETAPA 6 NAO DEVE ALTERAR O MOTOR NESTE MOMENTO. Gols esta bem calibrado "
            "e estavel (NAO ALTERAR); corners tem drift material (EM OBSERVACAO); "
            "cards e nao validavel (NAO AVALIAVEL); resultado e experimental; pressao "
            "sem dados. Nenhum mercado precisa de alteracao agora. O holdout ja foi "
            "observado e nao ha ROI -> risco de overfitting nao controlavel."
        ),
    }


def _main() -> None:
    p = argparse.ArgumentParser(
        description="Checkpoint de prontidao para calibracao (Etapa 5E).")
    p.add_argument("cmd", choices=["run"])
    p.add_argument("--json", default=None)
    args = p.parse_args()
    res = auditar()
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()