"""ETAPA 5C -- AUDITORIA CIRURGICA DA INSTABILIDADE EM ESCANTEIOS.

Modulo de diagnostico SOMENTE LEITURA. NAO altera motor, regras, thresholds,
Poisson, blend, politica, settlement ou qualquer dado. NAO otimiza, NAO
calibra. Apenas fatia as previsoes persistidas do backtest para responder POR
QUE o mercado de escanteios perdeu estabilidade temporal entre o periodo
anterior (hit ~0.9284) e o holdout posterior (~0.8723).

Reutiliza o fatiamento temporal de src.backtest_validacao (cutoff mediano,
dividir_holdout, blocos_walk_forward) e o denominador honesto de _metricas
(hit exclui NAO AVALIAVEL).

Uso:
    python -m src.auditoria_escanteios run [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from src.backtest import BACKTEST_DB_PATH
from src.backtest_validacao import (
    _metricas,
    blocos_walk_forward,
    carregar_previsoes,
    cutoff_mediano,
    dividir_holdout,
)

_LINHA_RE = re.compile(r"(Over|Under)\s+(\d+\.?\d*)", re.IGNORECASE)


def _parse_linha(linha: str) -> tuple[str | None, float | None]:
    if not linha:
        return None, None
    m = _LINHA_RE.search(linha)
    if not m:
        return None, None
    return m.group(1).lower(), float(m.group(2))


def _settle(linha: str, total: float | None) -> str:
    """Revalida settlement de uma linha de escanteios (total do jogo)."""
    if total is None:
        return "NÃO AVALIÁVEL"
    side, x = _parse_linha(linha)
    if side is None:
        return "NÃO AVALIÁVEL"
    if side == "over":
        return "GANHA" if total > x else "PERDIDA"
    return "GANHA" if total < x else "PERDIDA"


def _mes(d: str) -> str:
    try:
        return datetime.fromisoformat(d).strftime("%Y-%m")
    except (TypeError, ValueError):
        return "sem-data"


def _trimestre(d: str) -> str:
    try:
        dt = datetime.fromisoformat(d)
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"
    except (TypeError, ValueError):
        return "sem-data"


def _corners_entrar(preds: list[dict]) -> list[dict]:
    return [p for p in preds if p["mercado"] == "escanteios" and p["decisao"] == "ENTRAR"]


def _resumo(preds: list[dict]) -> dict:
    """Metricas resumidas de um corte de escanteios ENTRAR."""
    m = _metricas(preds)
    return {k: m[k] for k in (
        "entrar", "ganhas", "perdidas", "devolvidas", "nao_avaliavel",
        "hit_excl_na", "prob_media_entrar", "brier", "gap_obs_pred", "maturidade")}


def _por_tempo(corn: list[dict], keyfn) -> dict[str, dict]:
    grupos: dict[str, list[dict]] = defaultdict(list)
    for p in corn:
        grupos[keyfn(p["date"])].append(p)
    out = {}
    for k in sorted(grupos):
        out[k] = _resumo(grupos[k])
    return out


def _por_liga_anterior_posterior(ant: list[dict], pos: list[dict], min_n: int = 15) -> dict:
    """Por competicao: hit anterior e posterior, so ligas com amostra suficiente."""
    def agg(corn):
        d = defaultdict(list)
        for p in corn:
            d[p["league_id"]].append(p)
        return d
    a, b = agg(ant), agg(pos)
    lids = set(a) | set(b)
    out = {}
    for lid in lids:
        ca, cb = a.get(lid, []), b.get(lid, [])
        n_a, n_b = len(ca), len(cb)
        if n_a + n_b < min_n:
            continue
        nome = (ca[0]["league_name"] if ca else (cb[0]["league_name"] if cb else ""))
        ra = _resumo(ca) if ca else None
        rb = _resumo(cb) if cb else None
        out[str(lid)] = {
            "league_name": nome,
            "n_anterior": n_a, "n_posterior": n_b,
            "hit_anterior": ra["hit_excl_na"] if ra else None,
            "hit_posterior": rb["hit_excl_na"] if rb else None,
            "gap_anterior": ra["gap_obs_pred"] if ra else None,
            "gap_posterior": rb["gap_obs_pred"] if rb else None,
            "prob_anterior": ra["prob_media_entrar"] if ra else None,
            "prob_posterior": rb["prob_media_entrar"] if rb else None,
        }
    return out


def _mix_share(corn: list[dict]) -> dict[str, dict]:
    """Participacao % de cada liga no ENTRAR de escanteios."""
    n = len(corn)
    d = defaultdict(list)
    for p in corn:
        d[p["league_id"]].append(p)
    out = {}
    for lid, ps in sorted(d.items(), key=lambda kv: -len(kv[1])):
        out[str(lid)] = {
            "league_name": ps[0]["league_name"],
            "n": len(ps),
            "share": round(len(ps) / n, 4) if n else 0.0,
        }
    return out


def _por_linha(corn: list[dict]) -> dict[str, dict]:
    d = defaultdict(list)
    for p in corn:
        side, x = _parse_linha(p["linha"])
        if side is None:
            continue
        d[f"{side.capitalize()} {x}"].append(p)
    out = {}
    for k in sorted(d, key=lambda kk: (kk.split()[0], float(kk.split()[1]))):
        out[k] = _resumo(d[k])
    return out


def _over_vs_under(corn: list[dict]) -> dict[str, dict]:
    d = defaultdict(list)
    for p in corn:
        side, _ = _parse_linha(p["linha"])
        if side:
            d["over" if side == "over" else "under"].append(p)
    return {k: _resumo(v) for k, v in d.items()}


def _por_faixa_prob(corn: list[dict]) -> dict[str, dict]:
    bandas = [("70-80", 0.70, 0.80), ("80-90", 0.80, 0.90),
              ("90-95", 0.90, 0.95), ("95-97", 0.95, 0.971)]
    out = {}
    for nome, lo, hi in bandas:
        sub = [p for p in corn if lo <= p["prob"] < hi]
        if sub:
            out[nome] = _resumo(sub)
    return out


def _maturidade(corn: list[dict]) -> dict:
    """Distribuicao de janelas historicas e qualidade da amostra."""
    def stat(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return {"min": min(vals), "med": statistics.median(vals), "max": max(vals),
                "mean": round(statistics.mean(vals), 2)}
    nh = [p.get("n_home") for p in corn]
    na = [p.get("n_away") for p in corn]
    bv = [p.get("bench_validas") for p in corn]
    ssh = [p.get("sem_stats_home") for p in corn]
    ssa = [p.get("sem_stats_away") for p in corn]
    h2h = [p.get("h2h_n") for p in corn]
    # amostra curta: n_home+n_away < 10 (motor requer MIN_AMOSTRA_OBSERVACAO=10)
    curta = sum(1 for p in corn if (p.get("n_home") or 0) + (p.get("n_away") or 0) < 10)
    sem_stats = sum(1 for p in corn if (p.get("sem_stats_home") or 0) > 0 or (p.get("sem_stats_away") or 0) > 0)
    return {
        "n_home": stat(nh), "n_away": stat(na), "bench_validas": stat(bv),
        "sem_stats_home": stat(ssh), "sem_stats_away": stat(ssa), "h2h_n": stat(h2h),
        "n_amostra_curta": curta, "n_sem_stats_algum": sem_stats,
    }


def _drift(ant: list[dict], pos: list[dict]) -> dict:
    def dist(corn):
        tots = [p["total_final"] for p in corn if p["total_final"] is not None]
        if not tots:
            return None
        overs = sum(1 for p in corn if _parse_linha(p["linha"])[0] == "over")
        unders = sum(1 for p in corn if _parse_linha(p["linha"])[0] == "under")
        return {
            "n": len(tots), "media_total": round(statistics.mean(tots), 3),
            "mediana_total": statistics.median(tots),
            "stdev_total": round(statistics.pstdev(tots), 3) if len(tots) > 1 else 0.0,
            "min": min(tots), "max": max(tots),
            "pct_over": round(overs / len(corn), 4) if corn else 0.0,
            "pct_under": round(unders / len(corn), 4) if corn else 0.0,
        }
    return {"anterior": dist(ant), "posterior": dist(pos)}


def _concentracao_fixture(corn: list[dict]) -> dict:
    """Quantos fixtures tem 1 vs 2 ENTRAR; perdas por fixture."""
    por_fx = defaultdict(list)
    for p in corn:
        por_fx[p["fixture_id"]].append(p)
    n1 = sum(1 for v in por_fx.values() if len(v) == 1)
    n2 = sum(1 for v in por_fx.values() if len(v) == 2)
    n3 = sum(1 for v in por_fx.values() if len(v) >= 3)
    perdas_por_fx = [sum(1 for p in v if p["resultado_final"] == "PERDIDA") for v in por_fx.values()]
    fx_com_perda = [x for x in perdas_por_fx if x > 0]
    dist_perdas = Counter(perdas_por_fx)
    return {
        "fixtures_com_entrar": len(por_fx),
        "fx_com_1_entrar": n1, "fx_com_2_entrar": n2, "fx_com_3plus_entrar": n3,
        "fx_com_alguma_perda": len(fx_com_perda),
        "dist_perdas_por_fixture": {str(k): v for k, v in sorted(dist_perdas.items())},
        "max_perdas_num_fixture": max(perdas_por_fx) if perdas_por_fx else 0,
        "total_perdas": sum(perdas_por_fx),
    }


def _settlement_revalidate(corn: list[dict]) -> dict:
    mism = 0
    loss_mism = 0
    for p in corn:
        s = _settle(p["linha"], p["total_final"])
        if s != p["resultado_final"]:
            mism += 1
        if p["resultado_final"] == "PERDIDA" and s != "PERDIDA":
            loss_mism += 1
    return {"total": len(corn), "mismatches": mism, "loss_mismatches": loss_mism,
            "settlement_ok": mism == 0}


def auditar(db_path: str = BACKTEST_DB_PATH,
            run_id: str | None = None,
            blocos: int = 6) -> dict[str, Any]:
    rid, preds = carregar_previsoes(db_path, run_id)
    cutoff = cutoff_mediano(preds)
    ant_all, pos_all = dividir_holdout(preds, cutoff)
    ant = _corners_entrar(ant_all)
    pos = _corners_entrar(pos_all)
    corn = ant + pos

    wf = blocos_walk_forward(preds, blocos)
    wf_corn = [(nome, _corners_entrar(bl)) for nome, bl in wf]

    return {
        "run_id": rid,
        "cutoff": cutoff,
        "reproducao": {
            "anterior": _resumo(ant),
            "posterior": _resumo(pos),
        },
        "por_mes": {**_por_tempo(ant, _mes), **{k: v for k, v in _por_tempo(corn, _mes).items()}},
        "por_mes_anterior": _por_tempo(ant, _mes),
        "por_mes_posterior": _por_tempo(pos, _mes),
        "por_trimestre": _por_tempo(corn, _trimestre),
        "por_bloco_walkforward": {nome: _resumo(bl) for nome, bl in wf_corn if bl},
        "por_liga": _por_liga_anterior_posterior(ant, pos),
        "mix_anterior": _mix_share(ant),
        "mix_posterior": _mix_share(pos),
        "por_linha_anterior": _por_linha(ant),
        "por_linha_posterior": _por_linha(pos),
        "over_vs_under_anterior": _over_vs_under(ant),
        "over_vs_under_posterior": _over_vs_under(pos),
        "por_faixa_prob_anterior": _por_faixa_prob(ant),
        "por_faixa_prob_posterior": _por_faixa_prob(pos),
        "maturidade_anterior": _maturidade(ant),
        "maturidade_posterior": _maturidade(pos),
        "drift": _drift(ant, pos),
        "concentracao_fixture": _concentracao_fixture(corn),
        "settlement_revalidate": _settlement_revalidate(corn),
    }


def _main() -> None:
    p = argparse.ArgumentParser(
        description="Auditoria cirurgica da instabilidade em escanteios (Etapa 5C).")
    p.add_argument("cmd", choices=["run"])
    p.add_argument("--run-id", default=None)
    p.add_argument("--saida", default=BACKTEST_DB_PATH)
    p.add_argument("--json", default=None)
    args = p.parse_args()
    res = auditar(args.saida, args.run_id)
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()