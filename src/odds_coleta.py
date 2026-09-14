"""ETAPA 5F -- COLETA PROSPECTIVA DE ODDS REAIS.

Infraestrutura de coleta e persistencia de odds reais (cotacoes factuais de
bookmaker), SEPARADA do motor. NAO altera probabilidade, aprovacao, thresholds,
settlement, recomendacao ou qualquer regra do motor. NAO calcula ROI.

PRINCIPIOS (FASE 2 / 5 / 6 / 7 / 8):
    - ODD REAL = cotacao factual de bookmaker/API, com bookmaker, mercado, linha
      e timestamp identificaveis. ODD JUSTA = 1/probabilidade do modelo.
      ODD JUSTA NUNCA preenche odd_real. Nenhum bookmaker e fabricado. Nenhuma
      odd de mercado ausente e inferida.
    - Append-only: odds_snapshot_history NUNCA sobrescreve uma odd antiga.
      Cada cotacao observada vira um snapshot temporal imutavel.
    - Anti-leakage: cada odd traz timestamp factual (update_feed) e timestamp
      de coleta (collected_at). Futuro matching so podera usar uma odd se
      timestamp_da_odd <= momento_da_decisao. Nunca odd pos-jogo como entrada.
    - As 288.362 previsoes antigas NAO sao alteradas e NAO recebem odd_real
      retroativa. Este modulo apenas COLETA e ARMAZENA fatos para o futuro.
    - Mercado irreconhecivel => familia 'UNMAPPED' (nunca casamento
      aproximado). Odd invalida (ausente/nao numerica/<=0) => registrada com
      motivo, nunca convertida em zero.

Uso:
    python -m src.app oddscoleta status
    python -m src.app oddscoleta cache [--dry-run]
    python -m src.app oddscoleta coleta <fixture_id> [--live] [--intervalo N] [--max-iter N]
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.config import DB_PATH
from src.odds import (
    BET_IDS_FT_EXATOS,
    BOOKMAKER_DESCONHECIDO,
    _MARCADORES_FORA_FT,
    _to_float,
    _to_int,
    parse_ah_value,
    parse_total_value,
)

# Marcadores de periodo/escopo: presentes no nome => o mercado NAO e o FT de
# total do jogo (reuso da regra de src/odds.py para classificacao EXATA).
_MARCADORES = _MARCADORES_FORA_FT

# Nomes canonicos da familia RESULTADO (FT, total do jogo / resultado final).
_RESULTADO_NOMES = {
    "match winner": ("resultado", "1X2"),
    "double chance": ("resultado", "DC"),
    "asian handicap": ("resultado", "AH"),
    "draw no bet": ("resultado", "DNB"),
}

# Status de cada snapshot armazenado.
ST_OK = "OK"
ST_SUSPENDED = "SUSPENDED"      # live: cotacao existe mas mercado suspenso
ST_UNMAPPED = "UNMAPPED"        # mercado nao reconhecido (familia UNMAPPED)
ST_INVALID = "INVALID"          # value/odd ausente, nao numerica ou <= 0

COLETA_PRE = "pre_match"
COLETA_LIVE = "live"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_snapshot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL,
    coleta_tipo TEXT NOT NULL,          -- 'pre_match' | 'live'
    bookmaker TEXT NOT NULL,
    bet_name TEXT NOT NULL,
    bet_id INTEGER,
    familia TEXT NOT NULL,              -- 'gols'|'escanteios'|'cartoes'|'resultado'|'UNMAPPED'
    subfamilia TEXT,                    -- '1X2'|'DC'|'AH'|'DNB'|NULL
    lado TEXT,                          -- 'Over'|'Under'|'Home'|'Draw'|'Away'|NULL
    linha REAL,                         -- linha numerica quando aplicavel
    value_feed TEXT NOT NULL,           -- value exato do feed (ex.: "Over 2.5")
    odd REAL,                           -- cotacao factual; NULL em INVALID
    suspended INTEGER,                  -- 0/1/NULL (apenas live)
    update_feed TEXT,                   -- timestamp da odd na fonte (response[i].update)
    collected_at REAL NOT NULL,         -- timestamp da coleta (time.time)
    fixture_date TEXT,                  -- kickoff (ISO) quando conhecido
    e_pre_jogo INTEGER,                 -- 1 se coleta <= kickoff (anti-leakage); 0 pos; NULL desconhecido
    status TEXT NOT NULL,               -- OK|SUSPENDED|UNMAPPED|INVALID
    motivo TEXT,                        -- motivo quando status != OK
    snapshot_hash TEXT NOT NULL,
    UNIQUE (snapshot_hash)              -- dedup: cotacao identica => mesma hash
);
CREATE INDEX IF NOT EXISTS idx_odds_hist_fixture
    ON odds_snapshot_history (fixture_id, coleta_tipo);
CREATE INDEX IF NOT EXISTS idx_odds_hist_familia
    ON odds_snapshot_history (familia, status);
"""


def _normaliza_nome(nome: str) -> str:
    import re
    return re.sub(r"\s+", " ", (nome or "").strip().lower())


# ----------------------------------------------------------------------
# Classificacao EXATA de mercado (FASE 4: sem casamento aproximado)
# ----------------------------------------------------------------------
def classificar_mercado(bet_name: str, bet_id: int | None) -> tuple[str, str | None]:
    """(familia, subfamilia). NUNCA aproxima: irreconhecivel => ('UNMAPPED', None).

    Ordem: ID estavel do feed (5/45/80) SEM marcador de periodo/escopo; depois
    nome canonico exato da familia resultado. Nome com marcador (1st/2nd/half/
    home/away/team/yellow/player/range/between) rejeita totais mesmo com ID certo.
    """
    nome = _normaliza_nome(bet_name)
    tem_marcador = any(m in nome for m in _MARCADORES)
    if bet_id is not None and not tem_marcador:
        for fam, bid in BET_IDS_FT_EXATOS.items():
            if bet_id == bid:
                return fam, None
    if not tem_marcador and nome in _RESULTADO_NOMES:
        return _RESULTADO_NOMES[nome]
    return "UNMAPPED", None


def extrair_lado_linha(
    familia: str, subfamilia: str | None, value_feed: str
) -> tuple[str | None, float | None]:
    """(lado, linha) a partir do value do feed, conforme a familia."""
    v = (value_feed or "").strip()
    if familia in ("gols", "escanteios", "cartoes"):
        parsed = parse_total_value(v)
        if parsed is not None:
            return parsed[0], parsed[1]
        return None, None
    if familia == "resultado":
        if subfamilia == "1X2":
            if v in ("Home", "Draw", "Away"):
                return v, None
            return None, None
        if subfamilia == "DC":
            return (v or None), None
        if subfamilia == "AH":
            parsed = parse_ah_value(v)
            if parsed is not None:
                return parsed[0], parsed[1]
            return None, None
        if subfamilia == "DNB":
            if v in ("Home", "Away"):
                return v, None
            return None, None
    return None, None


# ----------------------------------------------------------------------
# Anti-leakage: e_pre_jogo (coleta <= kickoff?)
# ----------------------------------------------------------------------
def _epoch_de_iso(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        from datetime import datetime
        # suporta Z e +00:00
        ds = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(ds).timestamp()
    except (TypeError, ValueError):
        return None


def _e_pre_jogo(collected_at: float, fixture_date_iso: str | None) -> int | None:
    kickoff = _epoch_de_iso(fixture_date_iso)
    if kickoff is None:
        return None
    return 1 if collected_at <= kickoff else 0


# ----------------------------------------------------------------------
# Snapshot unitario + dedup hash (FASE 11)
# ----------------------------------------------------------------------
@dataclass
class OddSnapshot:
    """Uma cotacao factual unitaria, pronta para append."""

    fixture_id: int
    coleta_tipo: str
    bookmaker: str
    bet_name: str
    bet_id: int | None
    familia: str
    subfamilia: str | None
    lado: str | None
    linha: float | None
    value_feed: str
    odd: float | None
    suspended: bool | None
    update_feed: str
    collected_at: float
    fixture_date: str | None
    status: str
    motivo: str | None

    def hash(self) -> str:
        """Hash de identidade+cotacao. Cotacao identica => mesma hash (dedup).
        1.90 -> 1.89 e hash diferente (novo snapshot)."""
        payload = json.dumps(
            [
                self.fixture_id, self.coleta_tipo, self.bookmaker,
                self.bet_name, self.bet_id, self.familia, self.subfamilia,
                self.lado, self.linha, self.value_feed,
                None if self.odd is None else round(self.odd, 6),
                None if self.suspended is None else int(self.suspended),
                self.status,
            ],
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _construir_snapshots(
    fixture_id: int,
    coleta_tipo: str,
    raw_entry: dict[str, Any],
    *,
    collected_at: float,
    fixture_date: str | None,
) -> list[OddSnapshot]:
    """Constrói snapshots a partir de UMA entry da resposta (pre ou live)."""
    update_feed = str(raw_entry.get("update") or "")
    pre = coleta_tipo == COLETA_PRE
    out: list[OddSnapshot] = []

    # Shape pre-jogo: bookmakers[].bets[].values[]
    if pre:
        for book in raw_entry.get("bookmakers") or []:
            bm_name = str(book.get("name") or "casa nao informada")
            for bet in book.get("bets") or []:
                bet_name = str(bet.get("name") or "")
                if not bet_name:
                    continue
                bet_id = _to_int(bet.get("id"))
                familia, sub = classificar_mercado(bet_name, bet_id)
                for v in bet.get("values") or []:
                    out.append(
                        _unit(fixture_id, coleta_tipo, bm_name, bet_name,
                              bet_id, familia, sub, v, update_feed,
                              collected_at, fixture_date, suspended=None)
                    )
        return out

    # Shape live: odds[].values[] (casa agregada; pode nao vir bookmaker)
    book_name = str(
        (raw_entry.get("bookmaker") or {}).get("name") or BOOKMAKER_DESCONHECIDO
    )
    for bet in raw_entry.get("odds") or []:
        bet_name = str(bet.get("name") or "")
        if not bet_name:
            continue
        bet_id = _to_int(bet.get("id"))
        familia, sub = classificar_mercado(bet_name, bet_id)
        for v in bet.get("values") or []:
            suspended = bool(v.get("suspended")) if "suspended" in v else None
            out.append(
                _unit(fixture_id, coleta_tipo, book_name, bet_name, bet_id,
                      familia, sub, v, update_feed, collected_at,
                      fixture_date, suspended=suspended)
            )
    return out


def _unit(
    fixture_id: int, coleta_tipo: str, bm_name: str, bet_name: str,
    bet_id: int | None, familia: str, sub: str | None, v: dict[str, Any],
    update_feed: str, collected_at: float, fixture_date: str | None,
    *, suspended: bool | None,
) -> OddSnapshot:
    value_feed = str(v.get("value") or "")
    odd = _to_float(v.get("odd"))
    # Validacao (FASE 12): invalido => registrada com motivo, nunca zero.
    if not value_feed:
        status, motivo, odd_v = ST_INVALID, "value ausente no feed", None
    elif odd is None:
        status, motivo, odd_v = ST_INVALID, "odd ausente ou nao numerica", None
    elif odd <= 0:
        status, motivo, odd_v = ST_INVALID, "odd nao positiva", None
    elif suspended is True:
        status, motivo, odd_v = ST_SUSPENDED, "mercado suspenso (live)", odd
    elif familia == "UNMAPPED":
        status, motivo, odd_v = ST_UNMAPPED, "mercado nao mapeado", odd
    else:
        status, motivo, odd_v = ST_OK, None, odd

    lado, linha = (None, None)
    if familia != "UNMAPPED":
        lado, linha = extrair_lado_linha(familia, sub, value_feed)

    return OddSnapshot(
        fixture_id=fixture_id, coleta_tipo=coleta_tipo, bookmaker=bm_name,
        bet_name=bet_name, bet_id=bet_id, familia=familia, subfamilia=sub,
        lado=lado, linha=linha, value_feed=value_feed, odd=odd_v,
        suspended=suspended, update_feed=update_feed, collected_at=collected_at,
        fixture_date=fixture_date, status=status, motivo=motivo,
    )


# ----------------------------------------------------------------------
# Persistencia append-only (FASE 6: nunca sobrescreve)
# ----------------------------------------------------------------------
class OddsSnapshotStore:
    """Append-only. INSERT OR IGNORE deduplica cotacao identica (mesma hash)."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or DB_PATH)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def append_many(self, snapshots: Iterable[OddSnapshot]) -> dict[str, int]:
        """Insere snapshots. Dedup por hash: cotacao identica => ignorada.
        Retorna {'inseridos': N, 'duplicados': N}."""
        inseridos = 0
        duplicados = 0
        snaps = list(snapshots)
        if not snaps:
            return {"inseridos": 0, "duplicados": 0}
        with self._connect() as conn:
            for s in snaps:
                h = s.hash()
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO odds_snapshot_history
                        (fixture_id, coleta_tipo, bookmaker, bet_name, bet_id,
                         familia, subfamilia, lado, linha, value_feed, odd,
                         suspended, update_feed, collected_at, fixture_date,
                         e_pre_jogo, status, motivo, snapshot_hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        s.fixture_id, s.coleta_tipo, s.bookmaker, s.bet_name,
                        s.bet_id, s.familia, s.subfamilia, s.lado, s.linha,
                        s.value_feed, s.odd,
                        None if s.suspended is None else int(s.suspended),
                        s.update_feed, s.collected_at, s.fixture_date,
                        _e_pre_jogo(s.collected_at, s.fixture_date),
                        s.status, s.motivo, h,
                    ),
                )
                if cur.rowcount == 1:
                    inseridos += 1
                else:
                    duplicados += 1
        return {"inseridos": inseridos, "duplicados": duplicados}

    # ---------------------------- leitura ----------------------------
    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM odds_snapshot_history").fetchone()[0]
            por_tipo = dict(conn.execute(
                "SELECT coleta_tipo, COUNT(*) FROM odds_snapshot_history "
                "GROUP BY coleta_tipo").fetchall())
            por_familia = dict(conn.execute(
                "SELECT familia, COUNT(*) FROM odds_snapshot_history "
                "GROUP BY familia").fetchall())
            por_status = dict(conn.execute(
                "SELECT status, COUNT(*) FROM odds_snapshot_history "
                "GROUP BY status").fetchall())
            n_fixtures = conn.execute(
                "SELECT COUNT(DISTINCT fixture_id) FROM odds_snapshot_history"
            ).fetchone()[0]
            n_pre_jogo = conn.execute(
                "SELECT COUNT(*) FROM odds_snapshot_history "
                "WHERE e_pre_jogo = 1").fetchone()[0]
        return {
            "total_snapshots": total,
            "fixtures_distintos": n_fixtures,
            "por_coleta_tipo": por_tipo,
            "por_familia": por_familia,
            "por_status": por_status,
            "snapshots_pre_jogo": n_pre_jogo,
        }


# ----------------------------------------------------------------------
# Coleta: cache-first (FASE 3/13) e API (FASE 9)
# ----------------------------------------------------------------------
def _fixture_date_from_cache(conn: sqlite3.Connection, fixture_id: int) -> str | None:
    row = conn.execute(
        "SELECT response FROM api_cache WHERE endpoint='/fixtures' "
        "AND json_extract(params,'$.id') = ?",
        (fixture_id,),
    ).fetchone()
    if row:
        try:
            lista = json.loads(row[0])
            if isinstance(lista, list) and lista:
                fx = (lista[0].get("fixture") or {})
                d = fx.get("date")
                return str(d) if d else None
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    # fallback: procura em qualquer resposta /fixtures que contenha o id
    return None


def ingerir_cache(
    store: OddsSnapshotStore, *, db_path: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """FASE 13: ingere as odds JA existentes no api_cache (0 chamadas a API).

    Le /odds (pre_match) e /odds/live do cache, constroi snapshots e armazena.
    O timestamp de coleta usado e o created_at REAL da entrada no cache (nunca
    inventado). 0 consumo de API.
    """
    db = str(db_path or DB_PATH)
    coletados: list[OddSnapshot] = []
    resumo = {"pre_entries": 0, "live_entries": 0, "snapshots": 0,
              "consumo_api": 0, "dry_run": dry_run}

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # /odds pre-match
        rows = con.execute(
            "SELECT params, response, created_at FROM api_cache "
            "WHERE endpoint='/odds'").fetchall()
        for params, resp, created_at in rows:
            try:
                p = json.loads(params)
                fx = int(p.get("fixture"))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            try:
                lista = json.loads(resp)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(lista, list) or not lista:
                continue
            resumo["pre_entries"] += 1
            entry = lista[0]
            fx_date = _fixture_date_from_cache(con, fx)
            snaps = _construir_snapshots(
                fx, COLETA_PRE, entry,
                collected_at=float(created_at), fixture_date=fx_date,
            )
            coletados.extend(snaps)
        # /odds/live
        rows = con.execute(
            "SELECT params, response, created_at FROM api_cache "
            "WHERE endpoint='/odds/live'").fetchall()
        for params, resp, created_at in rows:
            try:
                p = json.loads(params)
                fx = int(p.get("fixture"))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            try:
                lista = json.loads(resp)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(lista, list) or not lista:
                continue
            resumo["live_entries"] += 1
            entry = lista[0]
            fx_date = _fixture_date_from_cache(con, fx)
            snaps = _construir_snapshots(
                fx, COLETA_LIVE, entry,
                collected_at=float(created_at), fixture_date=fx_date,
            )
            coletados.extend(snaps)
    finally:
        con.close()

    resumo["snapshots"] = len(coletados)
    if dry_run:
        # nao escreve; reporta o que seria inserido (com dedup simulado)
        vistos: set[str] = set()
        unicos = 0
        for s in coletados:
            h = s.hash()
            if h not in vistos:
                vistos.add(h)
                unicos += 1
        resumo["inseridos"] = unicos
        resumo["duplicados"] = len(coletados) - unicos
    else:
        grav = store.append_many(coletados)
        resumo.update(grav)
    return resumo


def coletar_fixture(
    client: Any, store: OddsSnapshotStore, fixture_id: int, *,
    live: bool = False, collected_at: float | None = None,
) -> dict[str, Any]:
    """FASE 9: coleta one-shot de UM fixture na API (e grava no cache).

    Pre-match: 1 chamada /odds. Live (se --live): +1 chamada /odds/live.
    Retorna consumo de API e contagem de snapshots.
    """
    from src.config import CACHE_TTL_LIVE

    epoch = float(collected_at if collected_at is not None else time.time())
    consumo = 0
    snaps: list[OddSnapshot] = []
    fx_date: str | None = None

    # data do fixture (anti-leakage) -- tenta cache /fixtures primeiro
    try:
        con = sqlite3.connect(str(DB_PATH))
        fx_date = _fixture_date_from_cache(con, fixture_id)
        con.close()
    except sqlite3.Error:
        pass

    # pre-match /odds
    resp_pre = client.get("/odds", params={"fixture": fixture_id})
    consumo += 1
    if resp_pre:
        snaps.extend(_construir_snapshots(
            fixture_id, COLETA_PRE, resp_pre[0],
            collected_at=epoch, fixture_date=fx_date,
        ))

    # live /odds/live (separado, FASE 16)
    if live:
        resp_live = client.get(
            "/odds/live", params={"fixture": fixture_id}, ttl=CACHE_TTL_LIVE,
        )
        consumo += 1
        if resp_live:
            snaps.extend(_construir_snapshots(
                fixture_id, COLETA_LIVE, resp_live[0],
                collected_at=epoch, fixture_date=fx_date,
            ))

    grav = store.append_many(snaps)
    return {
        "fixture_id": fixture_id, "live": live,
        "snapshots": len(snaps), "consumo_api": consumo, **grav,
    }


def coletar_periodico(
    client: Any, store: OddsSnapshotStore, fixture_ids: Iterable[int], *,
    intervalo: int = 60, max_iter: int | None = None, live: bool = False,
) -> dict[str, Any]:
    """FASE 9: coleta periodica. AUTO DESABILITADO por padrao (requer chamada
    explicita com intervalo). Para a cada max_iter ou quando atingir limite.
    intervalo=0 => iteracoes instantaneas (uso em testes)."""
    passadas = 0
    total_consumo = 0
    total_inseridos = 0
    total_duplicados = 0
    fxs = list(fixture_ids)
    while True:
        passadas += 1
        for fx in fxs:
            r = coletar_fixture(client, store, fx, live=live)
            total_consumo += r["consumo_api"]
            total_inseridos += r["inseridos"]
            total_duplicados += r["duplicados"]
        if max_iter is not None and passadas >= max_iter:
            break
        if intervalo and intervalo > 0:
            time.sleep(intervalo)
        else:
            break  # sem intervalo => uma unica passada (one-shot equivalente)
    return {
        "passadas": passadas, "fixtures": len(fxs),
        "consumo_api": total_consumo, "inseridos": total_inseridos,
        "duplicados": total_duplicados,
    }


# ----------------------------------------------------------------------
# Relatorio (FATO/CALCULO, sem ROI)
# ----------------------------------------------------------------------
def format_status(s: dict[str, Any]) -> str:
    linhas = [
        "[FATO] odds_snapshot_history (append-only)",
        f"  total de snapshots: {s['total_snapshots']}",
        f"  fixtures distintos: {s['fixtures_distintos']}",
        f"  snapshots pre-jogo (e_pre_jogo=1): {s['snapshots_pre_jogo']}",
        f"  por coleta_tipo: {s['por_coleta_tipo']}",
        f"  por familia: {s['por_familia']}",
        f"  por status: {s['por_status']}",
        "",
        "[CALCULO] ROI: NAO CALCULADO nesta etapa (Etapa 5F = coleta).",
        "[CALCULO] Etapa 6: BLOQUEADA (nao alterada por esta etapa).",
    ]
    return "\n".join(linhas)