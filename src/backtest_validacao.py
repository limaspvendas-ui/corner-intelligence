"""ETAPA 5B -- VALIDACAO TEMPORAL FORA DA AMOSTRA DO BACKTEST.

Validacao cronologica FORMAL das regras congeladas do motor (FASE B/G da
Etapa 5). NAO treina, NAO otimiza, NAO ajusta threshold, NAO escolhe cutoff
por resultado. Mede estabilidade temporal das regras COMO ESTAO.

Duas visoes (FASE C):
  1. HOLDOUT CRONOGOLOGICO: cutoff = data mediana dos fixtures considerados
     (criterio mecanico, sem inspectar taxa de acerto). Parte anterior vs
     parte posterior (holdout intocada pelas regras).
  2. WALK-FORWARD / ROLLING ORIGIN: K blocos temporais consecutivos por
     quantil de contagem de fixtures. Em cada bloco, cada previsao usa
     somente informacao disponivel antes do fixture (AS_OF por previsao,
     garantido pelo engine). Regras identicas em todos os blocos.

Equivalencia: a previsao de um fixture X depende apenas de dados com
date < X.date (AS_OF = X.date) e eh INDEPENDENTE de quais outros fixtures
estao no run. Portanto, fatiar as previsoes persistidas do baseline por data
eh matematicamente equivalente a rodar o engine separadamente em cada janela
-- verificado por teste (test_validacao_slice_equivalente_engine).

Denominador honesto: hit = ganhas / (ganhas+perdidas+devolvidas+meia) --
EXCLUI NAO AVALIAVEL (missing nunca vira zero, nunca entra no denominador
de acerto). gap = observado - predito (negativo => superconfianca).

ROI: NAO AVALIAVEL (0 odds em encerrados -- FASE G). Pressao: NAO AVALIAVEL.

Uso:
    python -m src.backtest_validacao run [--run-id ID] [--blocos 6] [--json PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.backtest import (
    BACKTEST_DB_PATH,
    DEC_ENTRAR,
    RES_DEVOLVIDA,
    RES_GANHA,
    RES_MEIA_DER,
    RES_MEIA_VIT,
    NAO_AVALIAVEL,
    VERSAO_BACKTEST_ENGINE,
    _maturidade,
)
from src.prejogo_opportunity import VERSAO_PREJOGO_OP

# Denominador de acerto: settled EXCLUI NAO AVALIAVEL (missing nao conta).
_SETTLED = (RES_GANHA, "PERDIDA", RES_DEVOLVIDA, RES_MEIA_VIT, RES_MEIA_DER)
_MERCADOS = ("gols", "escanteios", "cartoes", "resultado")


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


# ----------------------------------------------------------------------
# Fatiamento temporal (criterios mecanicos, sem inspectar resultado)
# ----------------------------------------------------------------------
def _datas_fixtures_unicas(preds: list[dict]) -> list[str]:
    """Datas distintas de fixtures, ordenadas crescente."""
    datas = sorted({p["date"] for p in preds if p["date"]})
    return datas


def cutoff_mediano(preds: list[dict]) -> str:
    """Data mediana dos fixtures (criterio mecanico, result-blind).

    Usa a data do fixture na posicao central do ordenamento cronologico dos
    fixtures UNICOS. Nao inspecta taxa de acerto nem prob -- apenas cronologia.
    """
    datas = _datas_fixtures_unicas(preds)
    if not datas:
        return ""
    return datas[len(datas) // 2]


def dividir_holdout(preds: list[dict], cutoff: str) -> tuple[list[dict], list[dict]]:
    """(anterior, posterior). anterior = date <= cutoff; posterior = date > cutoff.

    Fixtures cuja data == cutoff ficam no anterior (<=). O posterior e o
    HOLDOUT: fixtures estritamente posteriores ao cutoff mediano.
    """
    ct = _dt(cutoff)
    ant, pos = [], []
    for p in preds:
        d = _dt(p["date"])
        if d is None or ct is None:
            ant.append(p)  # data ausente: nao tem como ser futuro -> anterior
            continue
        if d <= ct:
            ant.append(p)
        else:
            pos.append(p)
    return ant, pos


def blocos_walk_forward(preds: list[dict], k: int) -> list[tuple[str, list[dict]]]:
    """K blocos consecutivos por quantil de contagem de fixtures UNICOS.

    Ordena fixtures unicos por data; divide em K grupos de contagem
    (aproximadamente) igual; cada previsao vai para o bloco do seu fixture.
    Blocos sao consecutivos em data, sem sobreposicao, sem embaralhamento.
    """
    datas = _datas_fixtures_unicas(preds)
    if not datas or k <= 1:
        return [("bloco_1", preds)]
    # Limites (k-1 cortes) sobre as datas ordenadas
    n = len(datas)
    cortes: list[str] = []
    for i in range(1, k):
        idx = (i * n) // k
        cortes.append(datas[idx])
    # Atribui cada previsao ao bloco cujo intervalo contem sua data
    out: list[tuple[str, list[dict]]] = []
    for bi in range(k):
        lo = cortes[bi - 1] if bi > 0 else None
        hi = cortes[bi] if bi < k - 1 else None
        bloc: list[dict] = []
        for p in preds:
            d = p["date"]
            if d is None:
                # sem data -> bloco 0 (nao futuro)
                if bi == 0:
                    bloc.append(p)
                continue
            ok_lo = (lo is None) or (d >= lo)
            ok_hi = (hi is None) or (d < hi)
            if ok_lo and ok_hi:
                bloc.append(p)
        out.append((f"bloco_{bi + 1}", bloc))
    return out


# ----------------------------------------------------------------------
# Metricas por corte (denominador honesto: excl NAO AVALIAVEL)
# ----------------------------------------------------------------------
def _metricas(preds: list[dict]) -> dict[str, Any]:
    """Metricas de um corte. hit EXCLUI NAO AVALIAVEL do denominador.

    Retorna: total, entrar, settled, ganhas, perdidas, devolvidas, meia_v,
    meia_d, nao_avaliavel, hit (excl NA), prob_media_entrar, brier, gap
    (obs - pred), maturidade.
    """
    total = len(preds)
    entrar = [p for p in preds if p["decisao"] == DEC_ENTRAR]
    n_ent = len(entrar)
    settled = [p for p in entrar if p["resultado_final"] in _SETTLED]
    ganhas = [p for p in entrar if p["resultado_final"] == RES_GANHA]
    perdidas = [p for p in entrar if p["resultado_final"] == "PERDIDA"]
    dev = [p for p in entrar if p["resultado_final"] == RES_DEVOLVIDA]
    meia_v = [p for p in entrar if p["resultado_final"] == RES_MEIA_VIT]
    meia_d = [p for p in entrar if p["resultado_final"] == RES_MEIA_DER]
    na = [p for p in entrar if p["resultado_final"] == NAO_AVALIAVEL]
    n_settled = len(settled)
    hit = (len(ganhas) / n_settled) if n_settled else None
    # prob media e Brier sobre ENTRAR settled binario (GANHA/PERDIDA)
    bin_set = [p for p in settled if p["resultado_final"] in (RES_GANHA, "PERDIDA")]
    prob_media = (statistics.mean(p["prob"] for p in bin_set)
                  if bin_set else None)
    brier = None
    if bin_set:
        brier = sum(
            (p["prob"] - (1.0 if p["resultado_final"] == RES_GANHA else 0.0)) ** 2
            for p in bin_set) / len(bin_set)
    gap = None
    if hit is not None and prob_media is not None:
        gap = round(hit - prob_media, 4)
    return {
        "total_previsoes": total,
        "entrar": n_ent,
        "settled": n_settled,
        "ganhas": len(ganhas),
        "perdidas": len(perdidas),
        "devolvidas": len(dev),
        "meia_vitoria": len(meia_v),
        "meia_derrota": len(meia_d),
        "nao_avaliavel": len(na),
        "hit_excl_na": round(hit, 4) if hit is not None else None,
        "prob_media_entrar": round(prob_media, 4) if prob_media is not None else None,
        "brier": round(brier, 4) if brier is not None else None,
        "gap_obs_pred": gap,
        "maturidade": _maturidade(n_settled),
    }


def metricas_por_mercado(preds: list[dict]) -> dict[str, dict]:
    out = {}
    for m in _MERCADOS:
        sub = [p for p in preds if p["mercado"] == m]
        if sub:
            out[m] = _metricas(sub)
    return out


def metricas_por_competicao(preds: list[dict], min_entrar: int = 30) -> dict[str, dict]:
    """Por liga, somente com amostra sufficiente (ENTRAR >= min_entrar)."""
    out = {}
    lids = sorted({p["league_id"] for p in preds if p["league_id"]})
    for lid in lids:
        sub = [p for p in preds if p["league_id"] == lid]
        m = _metricas(sub)
        if m["entrar"] >= min_entrar:
            ln = sub[0]["league_name"] or ""
            out[f"{lid}"] = {"league_name": ln, **m}
    return out


# ----------------------------------------------------------------------
# Orquestracao
# ----------------------------------------------------------------------
def carregar_previsoes(db_path: str, run_id: str | None = None) -> tuple[str, list[dict]]:
    """Carrega previsoes de uma run. Se run_id omitido, usa a mais recente."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if run_id is None:
            row = conn.execute(
                "SELECT run_id FROM bt_runs ORDER BY datahora DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("nenhuma run em " + db_path)
            run_id = row["run_id"]
        rows = conn.execute(
            "SELECT * FROM bt_predictions WHERE run_id = ?", (run_id,)
        ).fetchall()
    finally:
        conn.close()
    preds = [dict(r) for r in rows]
    return run_id, preds


def validar_temporal(
    db_path: str = BACKTEST_DB_PATH,
    run_id: str | None = None,
    blocos: int = 6,
) -> dict[str, Any]:
    """Executa holdout cronologico + walk-forward sobre previsoes persistidas.

    NAO roda o engine de novo: fatia o baseline (equivalente verificada).
    Retorna dict com regras congeladas, cutoff, holdout, walk_forward,
    por_mercado por periodo, e por_competicao no holdout.
    """
    run_id, preds = carregar_previsoes(db_path, run_id)
    cutoff = cutoff_mediano(preds)
    ant, pos = dividir_holdout(preds, cutoff)
    wf = blocos_walk_forward(preds, blocos)

    # Conta fixtures unicos por periodo (para audit)
    def nfx(ps):
        return len({p["fixture_id"] for p in ps})

    resultado = {
        "run_id": run_id,
        "engine_versao": VERSAO_BACKTEST_ENGINE,
        "regras_versao": VERSAO_PREJOGO_OP,
        "blocos_k": blocos,
        "cutoff_holdout": cutoff,
        "criterio_cutoff": (
            "data mediana dos fixtures unicos considerados (criterio mecanico, "
            "sem inspectar taxa de acerto/prob); anterior = date <= cutoff, "
            "posterior (holdout) = date > cutoff"
        ),
        "criterio_walk_forward": (
            f"{blocos} blocos consecutivos por quantil de contagem de fixtures "
            "unicos sobre o ordenamento cronologico; sem sobreposicao; sem "
            "embaralhamento; AS_OF por previsao preserva anti-lookahead"
        ),
        "total": {
            "previsoes": len(preds),
            "fixtures": nfx(preds),
        },
        "holdout": {
            "cutoff": cutoff,
            "anterior": {
                "fixtures": nfx(ant),
                "previsoes": len(ant),
                "geral": _metricas(ant),
                "por_mercado": metricas_por_mercado(ant),
            },
            "posterior": {
                "fixtures": nfx(pos),
                "previsoes": len(pos),
                "geral": _metricas(pos),
                "por_mercado": metricas_por_mercado(pos),
                "por_competicao": metricas_por_competicao(pos),
            },
        },
        "walk_forward": [
            {
                "bloco": nome,
                "fixtures": nfx(bl),
                "previsoes": len(bl),
                "geral": _metricas(bl),
                "por_mercado": metricas_por_mercado(bl),
            }
            for nome, bl in wf
        ],
    }
    return resultado


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _main() -> None:
    p = argparse.ArgumentParser(
        description="Validacao temporal fora da amostra do backtest (Etapa 5B).")
    p.add_argument("cmd", choices=["run"])
    p.add_argument("--run-id", default=None, help="Run do backtest a validar (default: mais recente).")
    p.add_argument("--blocos", type=int, default=6, help="Numero de blocos walk-forward.")
    p.add_argument("--saida", default=BACKTEST_DB_PATH, help="DB do backtest.")
    p.add_argument("--json", default=None, help="Caminho para salvar o resultado JSON.")
    args = p.parse_args()

    res = validar_temporal(args.saida, args.run_id, args.blocos)
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()