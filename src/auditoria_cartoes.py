"""ETAPA 5D -- AUDITORIA CIRURGICA DO MERCADO DE CARTOES.

Modulo de diagnostico SOMENTE LEITURA. NAO altera motor, regras, thresholds,
Poisson, blend, politica, settlement, matriz de cobertura ou qualquer dado.
NAO corrije, NAO recalibra, NAO liquida nada. Apenas descobre POR QUE 207 de
307 decisoes ENTRAR de cartoes ficaram NAO AVALIAVEL no backtest da Etapa 5
(run bt-20260913224716-PRE_GAME: 91 GANHA / 9 PERDIDA / 207 NAO AVALIAVEL).

Caminho auditado (FASE F):
    api_cache (/fixtures/statistics) -> parsing (_parse_side) -> MatchStats
    -> liquidar (settlement.py reusado por backtest.py) -> resultado_final

Regras congeladas confirmadas (FASE E/H):
    - convencao: amarelo=1, vermelho=2 (DECLARADA na linha congelada);
    - total = amarelos + 2*vermelhos (a MESMA da liquidacao);
    - amarelos OU vermelhos de qualquer equipe ausentes (None) na fonte =>
      NAO AVALIAVEL -- nunca zero, nunca inferido;
    - None (ausente) != "0" (zero verdadeiro): o parser preserva ambos.

Uso:
    python -m src.auditoria_cartoes run [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from src.config import DB_PATH
from src.backtest import BACKTEST_DB_PATH

STAT_YELLOW = "Yellow Cards"
STAT_RED = "Red Cards"
RUN_ID = "bt-20260913224716-PRE_GAME"

_LINHA_RE = re.compile(r"(Over|Under)\s+(\d+\.?\d*)", re.IGNORECASE)


def _parse_linha(linha: str) -> tuple[str | None, float | None]:
    if not linha:
        return None, None
    m = _LINHA_RE.search(linha)
    if not m:
        return None, None
    return m.group(1).lower(), float(m.group(2))


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def _is_half_line(x: float) -> bool:
    """True para linhas fracionarias (.5) -- nunca empata."""
    return abs((x % 1) - 0.5) < 1e-9


# ----------------------------------------------------------------------
# Camada de leitura do cache (SOMENTE LEITURA -- nunca escreve)
# ----------------------------------------------------------------------
def _load_stat_blocks(db_path: str) -> dict[int, tuple[bool, list[dict]]]:
    """fixture_id -> (is_half, blocos). Prefer half=true (tem 1h/2h); fallback
    no-half. Replica a prioridade do CacheIndex._load_stats."""
    rows = sqlite3.connect(db_path).execute(
        "SELECT params, response FROM api_cache "
        "WHERE endpoint = '/fixtures/statistics'"
    ).fetchall()
    por_fx: dict[int, list[dict]] = {}
    por_fx_half: dict[int, list[dict]] = {}
    for params, resp in rows:
        try:
            p = json.loads(params)
            fx = int(p.get("fixture"))
            blocos = json.loads(resp)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if not isinstance(blocos, list):
            continue
        if p.get("half") == "true":
            por_fx_half[fx] = blocos
        else:
            por_fx.setdefault(fx, blocos)
    out: dict[int, tuple[bool, list[dict]]] = {}
    for fx, blocos in por_fx_half.items():
        out[fx] = (True, blocos)
    for fx, blocos in por_fx.items():
        if fx not in out:
            out[fx] = (False, blocos)
    return out


def _load_stat_meta(db_path: str) -> dict[int, dict[str, float]]:
    """fixture_id -> {created_at, ttl} da linha usada (half preferida)."""
    rows = sqlite3.connect(db_path).execute(
        "SELECT params, response, created_at, ttl FROM api_cache "
        "WHERE endpoint = '/fixtures/statistics'"
    ).fetchall()
    por_fx: dict[int, dict[str, float]] = {}
    por_fx_half: dict[int, dict[str, float]] = {}
    for params, resp, created_at, ttl in rows:
        try:
            p = json.loads(params)
            fx = int(p.get("fixture"))
        except (TypeError, ValueError, KeyError):
            continue
        meta = {"created_at": created_at, "ttl": ttl}
        if p.get("half") == "true":
            por_fx_half[fx] = meta
        else:
            por_fx.setdefault(fx, meta)
    out = {}
    out.update(por_fx_half)
    for fx, meta in por_fx.items():
        if fx not in out:
            out[fx] = meta
    return out


def _load_fixture_raw(db_path: str) -> dict[int, dict]:
    rows = sqlite3.connect(db_path).execute(
        "SELECT params, response FROM api_cache WHERE endpoint = '/fixtures'"
    ).fetchall()
    fx_by_id: dict[int, dict] = {}
    for params, resp in rows:
        try:
            lista = json.loads(resp)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(lista, list):
            continue
        for raw in lista:
            if not isinstance(raw, dict):
                continue
            fid = (raw.get("fixture") or {}).get("id")
            if fid is None:
                continue
            fid = int(fid)
            if fid not in fx_by_id:
                fx_by_id[fid] = raw
    return fx_by_id


def _load_events(db_path: str) -> dict[int, list[dict]]:
    rows = sqlite3.connect(db_path).execute(
        "SELECT params, response FROM api_cache "
        "WHERE endpoint = '/fixtures/events'"
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for params, resp in rows:
        try:
            p = json.loads(params)
            fx = int(p.get("fixture"))
            evs = json.loads(resp) if resp else []
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if not isinstance(evs, list):
            continue
        if fx not in out:
            out[fx] = evs
    return out


def _team_ids(fx_raw: dict | None) -> tuple[int | None, int | None]:
    if not fx_raw:
        return None, None
    home = ((fx_raw.get("teams") or {}).get("home") or {}).get("id")
    away = ((fx_raw.get("teams") or {}).get("away") or {}).get("id")
    return home, away


def _card_vals(blocos: list[dict] | None, team_id: int | None
               ) -> tuple[Any, Any, bool, bool, int]:
    """(yellow_raw, red_raw, yellow_type_present, red_type_present,
    n_stat_types) para o bloco do time. Se o bloco do time nao existir,
    retorna (None, None, False, False, -1)."""
    if team_id is None:
        return None, None, False, False, -1
    for b in blocos or []:
        tid = (b.get("team") or {}).get("id")
        if tid != team_id:
            continue
        stats = b.get("statistics") or []
        y = r = None
        ypres = rpres = False
        for s in stats:
            t = s.get("type")
            if t == STAT_YELLOW:
                ypres = True
                y = s.get("value")
            elif t == STAT_RED:
                rpres = True
                r = s.get("value")
        return y, r, ypres, rpres, len(stats)
    return None, None, False, False, -1


# ----------------------------------------------------------------------
# Classificacao FASE B/C/F (por fixture NA)
# ----------------------------------------------------------------------
def _classify_fixture(blocos: list[dict] | None, ht: int | None, at: int | None
                      ) -> dict[str, Any]:
    """Classifica um fixture NA quanto a disponibilidade de cartoes."""
    if not blocos:
        return {"categoria": "G_sem_statistics",
                "motivo": "resposta /fixtures/statistics vazia ou ausente",
                "hy": None, "hr": None, "ay": None, "ar": None,
                "hyp": False, "hrp": False, "ayp": False, "arp": False,
                "home_block": False, "away_block": False}
    hy, hr, hyp, hrp, hn = _card_vals(blocos, ht)
    ay, ar, ayp, arp, an = _card_vals(blocos, at)
    hyi, hri, ayi, ari = (_to_int(hy), _to_int(hr), _to_int(ay), _to_int(ar))
    all4 = all(v is not None for v in (hyi, hri, ayi, ari))

    # Categoria FASE B
    if all4:
        cat = "A_todos_4_disponiveis"  # inesperado para NA -- nao ocorre
    elif an == -1 or hn == -1:
        cat = "E_bloco_time_ausente"
    elif hyp and ayp and hrp and arp and not any(
        v is not None for v in (hyi, hri, ayi, ari)
    ):
        cat = "D_tipos_presentes_valores_null"  # todos os 4 tipos, valores null
    elif hrp and arp and (hri is None and ari is None) and (
        hyi is not None and ayi is not None
    ):
        cat = "B_amarelos_disponiveis_vermelhos_ausentes"
    elif hyp and ayp and (hyi is None and ayi is None) and (
        hri is not None and ari is not None
    ):
        cat = "C_vermelhos_disponiveis_amarelos_ausentes"
    elif not (hyp or hrp or ayp or arp):
        cat = "F_todos_tipos_ausentes"
    else:
        cat = "X_parcial_misto"

    return {"categoria": cat,
            "hy": hy, "hr": hr, "ay": ay, "ar": ar,
            "hyi": hyi, "hri": hri, "ayi": ayi, "ari": ari,
            "hyp": hyp, "hrp": hrp, "ayp": ayp, "arp": arp,
            "home_block": hn != -1, "away_block": an != -1,
            "n_types_home": hn, "n_types_away": an}


def _causa_principal_fixture(cls: dict, has_events: bool,
                             events_have_cards: bool) -> str:
    """Mapeia a classificacao do fixture a uma causa FASE I."""
    cat = cls["categoria"]
    if cat == "G_sem_statistics":
        return "1_api_sem_dado"  # sem estatistica alguma
    if cat == "B_amarelos_disponiveis_vermelhos_ausentes":
        # Red Cards value=null na fonte -- gap de fonte em vermelhos
        return "1_api_sem_dado"
    if cat == "C_vermelhos_disponiveis_amarelos_ausentes":
        return "8_inconsistencia_api"
    if cat == "D_tipos_presentes_valores_null":
        return "1_api_sem_dado"
    if cat == "E_bloco_time_ausente":
        return "2_dado_parcial_statistics"
    if cat == "F_todos_tipos_ausentes":
        return "1_api_sem_dado"
    if cat == "A_todos_4_disponiveis":
        # inesperado: se chegou aqui como NA, seria bug -- marcar
        return "10_causa_nao_determinada"
    return "10_causa_nao_determinada"


# ----------------------------------------------------------------------
# FASE K/L: determinabilidade so com Yellow (Red irrelevant)
# ----------------------------------------------------------------------
def _determinavel_yellow(cls: dict, linha: str) -> tuple[bool, str | None]:
    """(determinavel, resultado) quando Yellow total > linha e a aposta e
    Over -> GANHA; ou Under -> PERDIDA. Red Cards (desconhecido, >=0) nao
    pode mudar o veredicto nesses casos. So para .5 ou inteira com Y > x."""
    hyi, ayi = cls.get("hyi"), cls.get("ayi")
    if hyi is None or ayi is None:
        return False, None
    y_total = hyi + ayi
    side, x = _parse_linha(linha)
    if side is None or x is None:
        return False, None
    if y_total > x:
        # total = Y + 2R >= Y > x -> Over ganha, Under perde (Red nao muda)
        return True, ("GANHA" if side == "over" else "PERDIDA")
    return False, None


# ----------------------------------------------------------------------
# Driver de auditoria
# ----------------------------------------------------------------------
def auditar(bt_db: str = BACKTEST_DB_PATH, api_db: str = DB_PATH,
            run_id: str = RUN_ID) -> dict[str, Any]:
    bt = sqlite3.connect(bt_db)
    preds_rows = bt.execute(
        "SELECT fixture_id, league_id, league_name, season, home_team, "
        "away_team, date, linha, prob, confianca, resultado_final, total_final "
        "FROM bt_predictions WHERE run_id = ? AND mercado = 'cartoes' "
        "AND decisao = 'ENTRAR' ORDER BY id",
        (run_id,),
    ).fetchall()
    cols = ["fixture_id", "league_id", "league_name", "season", "home_team",
            "away_team", "date", "linha", "prob", "confianca",
            "resultado_final", "total_final"]
    preds = [dict(zip(cols, r)) for r in preds_rows]

    def is_na(p: dict) -> bool:
        return (p["resultado_final"] or "").strip().upper().startswith("N")

    def is_g(p: dict) -> bool:
        return (p["resultado_final"] or "").strip().upper().startswith("G")

    def is_p(p: dict) -> bool:
        return (p["resultado_final"] or "").strip().upper().startswith("P")

    na = [p for p in preds if is_na(p)]
    liquid = [p for p in preds if is_g(p) or is_p(p)]

    stat_blocks = _load_stat_blocks(api_db)
    stat_meta = _load_stat_meta(api_db)
    fx_raw = _load_fixture_raw(api_db)
    events = _load_events(api_db)

    # ---- FASE B/C/F: classificacao por fixture NA ----
    fids_na = sorted({p["fixture_id"] for p in na})
    cls_by_fid: dict[int, dict] = {}
    for fid in fids_na:
        is_half, blocos = stat_blocks.get(fid, (False, None))
        ht, at = _team_ids(fx_raw.get(fid))
        cls = _classify_fixture(blocos, ht, at)
        cls["is_half"] = is_half
        cls_by_fid[fid] = cls

    # ---- FASE C: zero vs None (no universo NA) ----
    raw_zero = 0
    raw_none = 0
    raw_empty = 0
    for fid in fids_na:
        c = cls_by_fid[fid]
        for k in ("hy", "hr", "ay", "ar"):
            v = c.get(k)
            if v is None:
                raw_none += 1
            elif v == "":
                raw_empty += 1
            elif str(v) == "0":
                raw_zero += 1

    # ---- FASE C global: distribuicao de Red/Yellow no cache inteiro ----
    yellow_vals = Counter()
    red_vals = Counter()
    for fx, (is_half, blocos) in stat_blocks.items():
        for b in blocos:
            for s in (b.get("statistics") or []):
                t = s.get("type")
                v = s.get("value")
                if t == STAT_YELLOW:
                    if v is None:
                        yellow_vals["null"] += 1
                    elif str(v) == "0":
                        yellow_vals["zero"] += 1
                    elif str(v).strip().isdigit() and int(v) > 0:
                        yellow_vals["positive"] += 1
                elif t == STAT_RED:
                    if v is None:
                        red_vals["null"] += 1
                    elif str(v) == "0":
                        red_vals["zero"] += 1
                    elif str(v).strip().isdigit() and int(v) > 0:
                        red_vals["positive"] += 1

    # ---- FASE D: events ----
    ev_types = Counter()
    ev_card_details = Counter()
    ev_fixtures_with_cards = 0
    for fx, evs in events.items():
        has_card = False
        for e in evs:
            t = e.get("type")
            ev_types[t] += 1
            if t == "Card":
                ev_card_details[e.get("detail") or ""] += 1
                has_card = True
        if has_card:
            ev_fixtures_with_cards += 1
    na_fids_with_events = sum(1 for fid in fids_na if fid in events)

    # ---- FASE G: timing do cache ----
    gaps_hours = []
    from datetime import datetime, timezone
    for fid in fids_na:
        meta = stat_meta.get(fid)
        if not meta:
            continue
        pdate = next((p["date"] for p in na if p["fixture_id"] == fid), None)
        if not pdate:
            continue
        try:
            fd = datetime.fromisoformat(pdate)
        except (TypeError, ValueError):
            continue
        if fd.tzinfo is None:
            fd = fd.replace(tzinfo=timezone.utc)
        ca = datetime.fromtimestamp(meta["created_at"], tz=timezone.utc)
        gaps_hours.append((ca - fd).total_seconds() / 3600.0)
    import statistics
    timing = {
        "n": len(gaps_hours),
        "min_hours": round(min(gaps_hours), 1) if gaps_hours else None,
        "med_hours": round(statistics.median(gaps_hours), 1) if gaps_hours else None,
        "max_hours": round(max(gaps_hours), 1) if gaps_hours else None,
        "coletado_antes_partida": sum(1 for g in gaps_hours if g < 0),
        "coletado_pos_partida_mais_2h": sum(1 for g in gaps_hours if g >= 2),
    }

    # ---- FASE H: revalidacao de settlement ----
    def settle(linha: str, total: float | None) -> str:
        if total is None:
            return "NÃO AVALIÁVEL"
        side, x = _parse_linha(linha)
        if side is None:
            return "NÃO AVALIÁVEL"
        if side == "over":
            return "GANHA" if total > x else ("PERDIDA" if total < x else "DEVOLVIDA")
        return "GANHA" if total < x else ("PERDIDA" if total > x else "DEVOLVIDA")

    mism = 0
    loss_mism = 0
    for p in preds:
        exp = settle(p["linha"], p["total_final"])
        act = (p["resultado_final"] or "").strip()
        if exp != act:
            mism += 1
            if act == "PERDIDA" and exp != "PERDIDA":
                loss_mism += 1
    settlement_revalidate = {
        "total_previsoes": len(preds),
        "mismatches": mism,
        "loss_mismatches": loss_mism,
        "settlement_ok": mism == 0,
    }

    # ---- FASE I: classificacao de causa por previsao NA ----
    causa_counter = Counter()
    causa_por_liga: dict[str, Counter] = defaultdict(Counter)
    for p in na:
        fid = p["fixture_id"]
        c = cls_by_fid[fid]
        has_ev = fid in events
        ev_cards = any(e.get("type") == "Card" for e in events.get(fid, []))
        causa = _causa_principal_fixture(c, has_ev, ev_cards)
        causa_counter[causa] += 1
        causa_por_liga[p["league_name"] or "?"][causa] += 1

    # ---- FASE J: por competicao ----
    by_liga: dict[tuple, dict] = {}
    for p in preds:
        key = (p["league_id"], p["league_name"])
        d = by_liga.setdefault(key, {"entrar": 0, "ganha": 0, "perdida": 0,
                                     "nao_avaliavel": 0})
        d["entrar"] += 1
        if is_g(p):
            d["ganha"] += 1
        elif is_p(p):
            d["perdida"] += 1
        elif is_na(p):
            d["nao_avaliavel"] += 1
    por_liga = {}
    for (lid, nome), v in sorted(by_liga.items(),
                                  key=lambda kv: -kv[1]["nao_avaliavel"]):
        causa_princ = (causa_por_liga[nome or "?"].most_common(1)[0][0]
                       if (nome or "?") in causa_por_liga else None)
        pct = round(100 * v["nao_avaliavel"] / v["entrar"], 1) if v["entrar"] else 0.0
        por_liga[str(lid)] = {"league_name": nome, **v, "pct_missing": pct,
                              "causa_principal": causa_princ}

    # ---- FASE K/L: recuperabilidade + simulacao ----
    det_ganha = 0
    det_perdida = 0
    not_det = 0
    for p in na:
        c = cls_by_fid[p["fixture_id"]]
        det, res = _determinavel_yellow(c, p["linha"])
        if det and res == "GANHA":
            det_ganha += 1
        elif det and res == "PERDIDA":
            det_perdida += 1
        else:
            not_det += 1
    recuperaveis = det_ganha + det_perdida
    sem_dado = sum(
        1 for p in na if cls_by_fid[p["fixture_id"]]["categoria"] in
        ("G_sem_statistics", "D_tipos_presentes_valores_null",
         "C_vermelhos_disponiveis_amarelos_ausentes"))
    # not_det = recuperaveis' complemento dentro dos NA; desses, os sem_dado
    # (sem Yellow) nao sao "red-decide" -- sao puramente sem informacao.
    red_decide = not_det - sem_dado
    simulacao = {
        "resultado_atual": {"ganha": sum(1 for p in preds if is_g(p)),
                            "perdida": sum(1 for p in preds if is_p(p)),
                            "nao_avaliavel": len(na)},
        "recuperaveis_dado_existente": recuperaveis,
        "recuperaveis_ganha": det_ganha,
        "recuperaveis_perdida": det_perdida,
        "recuperaveis_via_events_cache": 0,  # 0/207 têm events
        "nao_determinaveis_red_decide": red_decide,
        "sem_dado_cartoes": sem_dado,
        "hipotetico_se_recuperado": {
            "ganha": sum(1 for p in preds if is_g(p)) + det_ganha,
            "perdida": sum(1 for p in preds if is_p(p)) + det_perdida,
            "nao_avaliavel": len(na) - recuperaveis,
        },
        "rotulo": "SIMULAÇÃO DE AUDITORIA -- NÃO É RESULTADO OFICIAL",
    }

    # ---- Distribuicao por categoria (FASE B) ----
    cat_counter = Counter(cls_by_fid[f]["categoria"] for f in fids_na)

    return {
        "run_id": run_id,
        "total_entrar": len(preds),
        "ganhas": sum(1 for p in preds if is_g(p)),
        "perdidas": sum(1 for p in preds if is_p(p)),
        "nao_avaliaveis": len(na),
        "fixtures_na_unicos": len(fids_na),
        "classificacao_fase_b": dict(cat_counter.most_common()),
        "causa_fase_i": dict(causa_counter.most_common()),
        "zero_vs_none_universo_na": {
            "raw_none": raw_none, "raw_zero": raw_zero, "raw_empty": raw_empty,
        },
        "distribuicao_cache_global": {
            "yellow_cards": dict(yellow_vals.most_common()),
            "red_cards": dict(red_vals.most_common()),
        },
        "fase_d_events": {
            "fixtures_com_events_cache": len(events),
            "fixtures_na_com_events": na_fids_with_events,
            "event_types": dict(ev_types.most_common()),
            "card_detail_strings": dict(ev_card_details.most_common()),
            "fixtures_com_card_event": ev_fixtures_with_cards,
        },
        "fase_g_timing_cache": timing,
        "fase_h_settlement_revalidate": settlement_revalidate,
        "fase_j_por_liga": por_liga,
        "fase_k_l_simulacao": simulacao,
        "detalhe_fixtures_na": [
            {"fixture_id": fid,
             "categoria": cls_by_fid[fid]["categoria"],
             "hy": cls_by_fid[fid].get("hy"),
             "hr": cls_by_fid[fid].get("hr"),
             "ay": cls_by_fid[fid].get("ay"),
             "ar": cls_by_fid[fid].get("ar"),
             "hyp": cls_by_fid[fid].get("hyp"),
             "hrp": cls_by_fid[fid].get("hrp"),
             "ayp": cls_by_fid[fid].get("ayp"),
             "arp": cls_by_fid[fid].get("arp")}
            for fid in fids_na
        ],
    }


def _main() -> None:
    p = argparse.ArgumentParser(
        description="Auditoria cirurgica dos dados de cartoes (Etapa 5D).")
    p.add_argument("cmd", choices=["run"])
    p.add_argument("--bt-db", default=BACKTEST_DB_PATH)
    p.add_argument("--api-db", default=DB_PATH)
    p.add_argument("--json", default=None)
    args = p.parse_args()
    res = auditar(args.bt_db, args.api_db)
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()