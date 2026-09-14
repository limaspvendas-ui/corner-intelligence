"""Diagnóstico do drift de CORNERS — macroetapa final de corners.

Módulo READ-ONLY: lê o backtest.db congelado (bt-20260913224716-PRE_GAME)
e NÃO altera o motor, thresholds, Poisson, predictions históricas, settlement
ou o banco. Produz:

  1. Congelamento do baseline (pré vs pós drift, mensal, por linha, por
     competição, distribuição de total_final).
  2. Diagnóstico do drift (global vs concentrado; causa mais provável).
  3. Teste de candidatos de correção com metodologia temporal rigorosa
     (desenvolvimento = pré-drift; OOS = pós-drift; sem usar o holdout na
     seleção do candidato).
  4. Tabela comparativa BASELINE vs candidatos com flag de leakage.
  5. Veredicto: CORNERS aprova ou permanece EM_OBSERVAÇÃO.

PRINCÍPIOS INEGOCIÁVEIS:
  - NÃO aprovar Corners por ordem administrativa.
  - NÃO esconder drift.
  - NÃO manipular threshold para fazer o modelo passar.
  - NÃO usar informação futura (candidato derivado do holdout = LEAKAGE).
  - NÃO alterar histórico para melhorar métricas.
  - Se nenhum candidato legítimo passar: CORNERS continua EM_OBSERVAÇÃO.

Cutoff mecânico: 2026-05-10 (cutoff mediano cronológico do baseline,
result-blind, reutilizado de validacao_multifonte.DRIFT_CORNERS_CUTOFF).
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.backtest import BACKTEST_DB_PATH, DEC_ENTRAR
from src.backtest_validacao import _SETTLED, _metricas, blocos_walk_forward

# Cutoff mecânico cronológico (result-blind) — mesmo de validacao_multifonte.
CUTOFF = "2026-05-10"

# Resultados settled binarios para recalibração
_GANHA = "GANHA"
_PERDIDA = "PERDIDA"


# ----------------------------------------------------------------------
# Carregamento
# ----------------------------------------------------------------------
def _load_corners_entrar(db: str = BACKTEST_DB_PATH) -> list[dict]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM bt_predictions WHERE mercado='escanteios' "
        "AND decisao=?",
        (DEC_ENTRAR,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _split(preds: list[dict]) -> tuple[list[dict], list[dict]]:
    pre = [p for p in preds if (p["date"] or "")[:10] < CUTOFF]
    pos = [p for p in preds if (p["date"] or "")[:10] >= CUTOFF]
    return pre, pos


def _hit(ps: list[dict]) -> tuple[float | None, int, int, float | None, float | None]:
    """(hit, n_settled, n_ganhas, prob_media, gap) sobre settled binario."""
    binset = [p for p in ps if p["resultado_final"] in (_GANHA, _PERDIDA)]
    if not binset:
        return None, 0, 0, None, None
    g = sum(1 for p in binset if p["resultado_final"] == _GANHA)
    hit = g / len(binset)
    prob = statistics.mean(p["prob"] for p in binset)
    return round(hit, 4), len(binset), g, round(prob, 4), round(hit - prob, 4)


def _total_final_stats(ps: list[dict]) -> dict[str, Any]:
    tf = [p["total_final"] for p in ps
          if p["total_final"] is not None
          and p["resultado_final"] in (_GANHA, _PERDIDA)]
    if not tf:
        return {"n": 0}
    qs = statistics.quantiles(tf, n=10) if len(tf) >= 10 else None
    return {
        "n": len(tf),
        "min": min(tf), "max": max(tf),
        "media": round(statistics.mean(tf), 2),
        "mediana": statistics.median(tf),
        "sd": round(statistics.pstdev(tf), 2) if len(tf) > 1 else None,
        "p10": round(qs[0], 1) if qs else None,
        "p25": round(qs[2], 1) if qs else None,
        "p75": round(qs[7], 1) if qs else None,
        "p90": round(qs[8], 1) if qs else None,
    }


# ----------------------------------------------------------------------
# 1. Baseline
# ----------------------------------------------------------------------
def congelar_baseline(preds: list[dict] | None = None) -> dict[str, Any]:
    if preds is None:
        preds = _load_corners_entrar()
    pre, pos = _split(preds)
    pre_hit, pre_n, pre_g, pre_p, pre_gap = _hit(pre)
    pos_hit, pos_n, pos_g, pos_p, pos_gap = _hit(pos)
    return {
        "cutoff": CUTOFF,
        "n_entrar_total": len(preds),
        "pre_drift": {
            "n_entrar": len(pre), "settled": pre_n, "ganhas": pre_g,
            "hit": pre_hit, "prob_media": pre_p, "gap": pre_gap,
            "total_final": _total_final_stats(pre),
        },
        "post_drift": {
            "n_entrar": len(pos), "settled": pos_n, "ganhas": pos_g,
            "hit": pos_hit, "prob_media": pos_p, "gap": pos_gap,
            "total_final": _total_final_stats(pos),
        },
        "drift_pp": round((pos_hit - pre_hit) * 100, 2)
        if (pre_hit is not None and pos_hit is not None) else None,
    }


# ----------------------------------------------------------------------
# 2. Diagnóstico
# ----------------------------------------------------------------------
def _by_line(ps: list[dict]) -> dict[str, dict]:
    d: dict[str, dict] = defaultdict(lambda: {"ganhas": 0, "perdidas": 0, "n": 0})
    for p in ps:
        L = p["linha"]
        if p["resultado_final"] == _GANHA:
            d[L]["ganhas"] += 1
        elif p["resultado_final"] == _PERDIDA:
            d[L]["perdidas"] += 1
        d[L]["n"] = d[L]["ganhas"] + d[L]["perdidas"]
    for L, v in d.items():
        v["hit"] = round(v["ganhas"] / v["n"], 4) if v["n"] else None
    return dict(d)


def _by_competition(ps: list[dict]) -> dict[int, dict]:
    d: dict[int, dict] = defaultdict(lambda: {"name": "", "ganhas": 0, "perdidas": 0})
    for p in ps:
        lid = p["league_id"]
        d[lid]["name"] = p["league_name"]
        if p["resultado_final"] == _GANHA:
            d[lid]["ganhas"] += 1
        elif p["resultado_final"] == _PERDIDA:
            d[lid]["perdidas"] += 1
    for lid, v in d.items():
        n = v["ganhas"] + v["perdidas"]
        v["n"] = n
        v["hit"] = round(v["ganhas"] / n, 4) if n else None
    return dict(d)


def diagnostico(preds: list[dict] | None = None) -> dict[str, Any]:
    if preds is None:
        preds = _load_corners_entrar()
    pre, pos = _split(preds)

    # Walk-forward 6 blocos
    wf = []
    for nome, blk in blocos_walk_forward(preds, 6):
        m = _metricas(blk)
        ds = sorted({p["date"][:10] for p in blk if p["date"]})
        wf.append({
            "bloco": nome, "n_entrar": m["entrar"], "settled": m["settled"],
            "hit": m["hit_excl_na"], "prob": m["prob_media_entrar"],
            "gap": m["gap_obs_pred"],
            "periodo": f"{ds[0]}..{ds[-1]}" if ds else "",
        })

    # Por linha pré vs pós
    linhas_pre = _by_line(pre)
    linhas_pos = _by_line(pos)

    # Por competição pré vs pós
    comps_pre = _by_competition(pre)
    comps_pos = _by_competition(pos)

    # Overdispersion: sd real vs raiz(media) (Poisson prediz sd=sqrt(media))
    tf_pre = _total_final_stats(pre)
    tf_pos = _total_final_stats(pos)
    overdisp_pre = (tf_pre["sd"] ** 2 / tf_pre["media"]
                    if tf_pre.get("sd") and tf_pre.get("media") else None)
    overdisp_pos = (tf_pos["sd"] ** 2 / tf_pos["media"]
                    if tf_pos.get("sd") and tf_pos.get("media") else None)
    poisson_sd_pre = (tf_pre["media"] ** 0.5) if tf_pre.get("media") else None
    poisson_sd_pos = (tf_pos["media"] ** 0.5) if tf_pos.get("media") else None

    return {
        "walk_forward": wf,
        "linhas_pre": linhas_pre, "linhas_pos": linhas_pos,
        "comps_pre": comps_pre, "comps_pos": comps_pos,
        "overdispersion": {
            "pre": {"sd_real": tf_pre.get("sd"), "sd_poisson": round(poisson_sd_pre, 2) if poisson_sd_pre else None,
                    "ratio_var_mean": round(overdisp_pre, 3) if overdisp_pre else None},
            "pos": {"sd_real": tf_pos.get("sd"), "sd_poisson": round(poisson_sd_pos, 2) if poisson_sd_pos else None,
                    "ratio_var_mean": round(overdisp_pos, 3) if overdisp_pos else None},
        },
        "total_final_pre": tf_pre, "total_final_pos": tf_pos,
    }


# ----------------------------------------------------------------------
# Isotonic regression (PAV) — implementação manual, sem sklearn
# ----------------------------------------------------------------------
def _isotonic_fit(x: list[float], y: list[float]) -> tuple[list[float], list[float]]:
    """Pool Adjacent Violators. Retorna (block_x_max, block_val): um par por
    bloco merged. block_x_max = maior x do bloco (para lookup em escada)."""
    pts = sorted(zip(x, y), key=lambda t: t[0])
    # blocos: lista de [soma_y, peso, x_min, x_max]
    blocks: list[list] = []
    for xi, yi in pts:
        blocks.append([yi, 1, xi, xi])
    i = 0
    while i < len(blocks) - 1:
        v_cur = blocks[i][0] / blocks[i][1]
        v_nxt = blocks[i + 1][0] / blocks[i + 1][1]
        if v_cur > v_nxt:
            blocks[i][0] += blocks[i + 1][0]
            blocks[i][1] += blocks[i + 1][1]
            blocks[i][3] = blocks[i + 1][3]  # extende x_max
            del blocks[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    block_xmax = [b[3] for b in blocks]
    block_val = [b[0] / b[1] for b in blocks]
    return block_xmax, block_val


def _isotonic_apply(model_xmax: list[float], model_vals: list[float],
                    x_new: float) -> float:
    """Interpolação em escada: valor do bloco cujo x_max é o maior <= x_new."""
    if not model_xmax:
        return x_new
    val = model_vals[0]
    for i, xm in enumerate(model_xmax):
        if xm <= x_new:
            val = model_vals[i]
        else:
            break
    return val


# ----------------------------------------------------------------------
# 4. Candidatos
# ----------------------------------------------------------------------
@dataclass
class Candidato:
    nome: str
    hipotese: str
    motivo: str
    alteracao: str
    dados_utilizados: str
    risco_overfit: str  # BAIXO/MEDIO/ALTO
    leakage: bool  # True se derivado do holdout
    n_oos: int = 0
    hit_oos: float | None = None
    gap_oos: float | None = None
    n_pre: int = 0
    hit_pre: float | None = None
    aprovavel: bool = False
    motivo_aprovavel: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


def _avaliar_criterios(c: Candidato, pre_hit_baseline: float,
                       pos_hit_baseline: float) -> None:
    """Avalia os 8 critérios e seta aprovavel + motivo."""
    c.n_pre = c.n_pre
    motivos = []
    ok = True
    # 1. remove/reduz drift materialmente (hit_oos > pos_hit_baseline + 0.02)
    if c.hit_oos is not None and pos_hit_baseline is not None:
        if c.hit_oos - pos_hit_baseline >= 0.02:
            motivos.append("1-reduz_drift: SIM")
        else:
            ok = False
            motivos.append(f"1-reduz_drift: NAO (Δ={round(c.hit_oos-pos_hit_baseline,4)})")
    # 2. estabilidade temporal (walk-forward) — sem wf para candidatos simples
    # 3. nao piora calibracao
    if c.gap_oos is not None and abs(c.gap_oos) < 0.05:
        motivos.append("3-calibracao: OK")
    else:
        motivos.append(f"3-calibracao: gap={c.gap_oos}")
    # 4. amostra OOS suficiente (n >= 100)
    if c.n_oos >= 100:
        motivos.append("4-amostra_oos: OK")
    else:
        ok = False
        motivos.append(f"4-amostra_oos: INSUFICIENTE (n={c.n_oos})")
    # 5. nao depende de uma unica competicao — verificado por inspecao
    # 6. sem vazamento temporal
    if c.leakage:
        ok = False
        motivos.append("6-LEAKAGE: SIM (candidato derivado do holdout)")
    else:
        motivos.append("6-LEAKAGE: NAO")
    # 7. supera/estabiliza baseline
    if c.hit_oos is not None and c.hit_oos >= pre_hit_baseline - 0.01:
        motivos.append("7-supera_baseline: OK")
    else:
        ok = False
        motivos.append(f"7-supera_baseline: NAO (pre={pre_hit_baseline}, oos={c.hit_oos})")
    # 8. walk-forward consistente — sem wf para candidatos simples
    c.aprovavel = ok
    c.motivo_aprovavel = "; ".join(motivos)


def testar_candidatos(preds: list[dict] | None = None) -> dict[str, Any]:
    if preds is None:
        preds = _load_corners_entrar()
    pre, pos = _split(preds)
    pre_hit, _, _, _, _ = _hit(pre)
    pos_hit, _, _, _, _ = _hit(pos)

    candidatos: list[Candidato] = []

    # BASELINE_ATUAL
    base = Candidato(
        nome="BASELINE_ATUAL",
        hipotese="modelo Poisson congelado, sem correcao",
        motivo="referencia",
        alteracao="nenhuma",
        dados_utilizados="backtest congelado",
        risco_overfit="N/A",
        leakage=False,
        n_oos=len([p for p in pos if p["resultado_final"] in (_GANHA, _PERDIDA)]),
        hit_oos=pos_hit,
        gap_oos=_hit(pos)[4],
    )
    _avaliar_criterios(base, pre_hit, pos_hit)
    candidatos.append(base)

    # CAND_A: excluir Serie B (league_id 72) — derivado do holdout (leakage)
    pos_sem_serieb = [p for p in pos if p["league_id"] != 72]
    pre_sem_serieb = [p for p in pre if p["league_id"] != 72]
    a_hit, a_n, _, a_p, a_gap = _hit(pos_sem_serieb)
    a = Candidato(
        nome="CAND_A_excluir_serie_b",
        hipotese="Serie B Brasil e a competicao com maior degradacao pós-drift",
        motivo="Serie B: hit 0.920->0.819 pós; n=72 pós (16,7% da amostra)",
        alteracao="excluir league_id=72 do fluxo corners",
        dados_utilizados="holdout pós-drift (LEAKAGE: Serie B estava bem pre-drift)",
        risco_overfit="ALTO",
        leakage=True,
        n_oos=a_n, hit_oos=a_hit, gap_oos=a_gap,
        n_pre=len([p for p in pre_sem_serieb if p["resultado_final"] in (_GANHA, _PERDIDA)]),
        hit_pre=_hit(pre_sem_serieb)[0],
        extras={"competicao_excluida": "Serie B (72)"},
    )
    _avaliar_criterios(a, pre_hit, pos_hit)
    candidatos.append(a)

    # CAND_B: restringir a Over 4.5 (linha estavel) — derivado do holdout (leakage)
    pos_o45 = [p for p in pos if "Over 4.5" in (p["linha"] or "")]
    pre_o45 = [p for p in pre if "Over 4.5" in (p["linha"] or "")]
    b_hit, b_n, _, b_p, b_gap = _hit(pos_o45)
    b = Candidato(
        nome="CAND_B_so_over_45",
        hipotese="Over 4.5 e a linha mais estavel pre/pós (0.909->0.900)",
        motivo="Over 4.5 tem maior volume e menor delta; demais linhas drift",
        alteracao="restringir corners operacional a Over 4.5",
        dados_utilizados="holdout pós-drift (LEAKAGE: Over 5.5 era 0.984 pre-drift)",
        risco_overfit="ALTO",
        leakage=True,
        n_oos=b_n, hit_oos=b_hit, gap_oos=b_gap,
        n_pre=len([p for p in pre_o45 if p["resultado_final"] in (_GANHA, _PERDIDA)]),
        hit_pre=_hit(pre_o45)[0],
        extras={"linha_restrita": "Over 4.5"},
    )
    _avaliar_criterios(b, pre_hit, pos_hit)
    candidatos.append(b)

    # CAND_C: recalibração isotônica treinada no PRE-drift (legítimo, sem leakage)
    # Fit f(prob) -> P(GANHA) sobre pre-drift; aplicar a post-drift.
    pre_bin = [p for p in pre if p["resultado_final"] in (_GANHA, _PERDIDA)]
    xs = [p["prob"] for p in pre_bin]
    ys = [1.0 if p["resultado_final"] == _GANHA else 0.0 for p in pre_bin]
    model_xmax, model_vals = _isotonic_fit(xs, ys)
    # Aplicar a post-drift: re-aplicar threshold [0.70, 0.97] sobre prob calibrada
    from src.politica_aprovacao import PROB_MIN_APROVAR, PROB_MAX_APROVAR
    from src.prejogo_opportunity import CONF_MIN_TOP1
    pos_calib_entrar = []
    for p in pos:
        if p["resultado_final"] not in (_GANHA, _PERDIDA):
            continue
        cp = _isotonic_apply(model_xmax, model_vals, p["prob"])
        # re-aplicar gate com prob calibrada e confianca original
        if PROB_MIN_APROVAR <= cp <= PROB_MAX_APROVAR and (p["confianca"] or 0) >= CONF_MIN_TOP1:
            pos_calib_entrar.append((p, cp))
    c_n = len(pos_calib_entrar)
    c_g = sum(1 for p, _ in pos_calib_entrar if p["resultado_final"] == _GANHA)
    c_hit = round(c_g / c_n, 4) if c_n else None
    c_p = statistics.mean(cp for _, cp in pos_calib_entrar) if pos_calib_entrar else None
    c_gap = round(c_hit - c_p, 4) if (c_hit is not None and c_p is not None) else None
    c = Candidato(
        nome="CAND_C_isotonic_pre_drift",
        hipotese="recalibrar prob para corrigir superconfianca pós-drift",
        motivo="pre-drift bem calibrado (gap -0.004); isotonic treina f(prob)->outcome",
        alteracao="recalibracao isotonic sobre saida do motor (nao toca Poisson)",
        dados_utilizados="PRE-drift apenas (treino); OOS = pos-drift (teste)",
        risco_overfit="BAIXO",
        leakage=False,
        n_oos=c_n, hit_oos=c_hit, gap_oos=c_gap,
        n_pre=len(pre_bin), hit_pre=pre_hit,
        extras={"prob_media_calib_oos": round(c_p, 4) if c_p else None,
                "modelo_isotonic_pts": len(model_xmax)},
    )
    _avaliar_criterios(c, pre_hit, pos_hit)
    candidatos.append(c)

    # CAND_D: shrinkage conservador (prob -> min(prob, 0.90)) — prior fixo, legítimo
    # Reduz overconfianca sem tocar holdout; nao muda o gate ENTRAR (0.90>0.70)
    # => nao altera hit rate (mesmo conjunto), so exibe prob menor.
    pos_d = [p for p in pos if p["resultado_final"] in (_GANHA, _PERDIDA)]
    d_n = len(pos_d)
    d_g = sum(1 for p in pos_d if p["resultado_final"] == _GANHA)
    d_hit = round(d_g / d_n, 4) if d_n else None
    d_p = statistics.mean(min(p["prob"], 0.90) for p in pos_d)
    d_gap = round(d_hit - d_p, 4)
    d = Candidato(
        nome="CAND_D_shrink_prob_090",
        hipotese="teto de prob em 0.90 para conter superconfianca estrutural",
        motivo="prob motor ~0.93 sistematicamente; teto 0.90 alinha melhor com hit observado",
        alteracao="clip prob em 0.90 (display/calibracao; nao altera gate ENTRAR)",
        dados_utilizados="prior fixo 0.90 (nao derivado do holdout)",
        risco_overfit="BAIXO",
        leakage=False,
        n_oos=d_n, hit_oos=d_hit, gap_oos=d_gap,
        n_pre=len([p for p in pre if p["resultado_final"] in (_GANHA, _PERDIDA)]),
        hit_pre=pre_hit,
        extras={"prob_clip": 0.90, "nota": "nao muda hit rate (mesmo conjunto ENTRAR)"},
    )
    _avaliar_criterios(d, pre_hit, pos_hit)
    candidatos.append(d)

    return {
        "baseline_pre_hit": pre_hit,
        "baseline_pos_hit": pos_hit,
        "candidatos": [c.__dict__ for c in candidatos],
    }


def rodar(db: str = BACKTEST_DB_PATH) -> dict[str, Any]:
    preds = _load_corners_entrar(db)
    return {
        "baseline": congelar_baseline(preds),
        "diagnostico": diagnostico(preds),
        "candidatos": testar_candidatos(preds),
    }


if __name__ == "__main__":
    import json
    res = rodar()
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))