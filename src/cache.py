"""Cache local de respostas da API-Football em SQLite.

Evita requisicoes repetidas: cada chamada e guardada por
(endpoint + parametros) com um TTL por tipo de endpoint.
Estatisticas de jogos encerrados sao imutaveis (TTL longo);
jogos ao vivo usam TTL curto.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from src.config import CACHE_TTL, CACHE_TTL_DEFAULT, CACHE_TTL_LIVE, DATA_DIR, DB_PATH


def _ttl_for(endpoint: str, params: dict[str, Any] | None) -> int:
    if params and "live" in params:
        return CACHE_TTL_LIVE
    return CACHE_TTL.get(endpoint, CACHE_TTL_DEFAULT)


class Cache:
    """Cache chaveado por endpoint + parametros ordenados."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = str(db_path or DB_PATH)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_cache (
                    key TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    params TEXT NOT NULL,
                    response TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            # Migracao: coluna de TTL explicito (dados ao vivo). Bases
            # antigas ganham a coluna; NULL => TTL derivado do endpoint.
            try:
                conn.execute("ALTER TABLE api_cache ADD COLUMN ttl INTEGER")
            except sqlite3.OperationalError:
                pass  # coluna ja existe

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _key(endpoint: str, params: dict[str, Any] | None) -> str:
        payload = json.dumps([endpoint, sorted((params or {}).items())], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None,
        ttl: int | None = None,
    ) -> list[dict[str, Any]] | None:
        """Retorna a resposta cacheada se ainda valida; senao None.

        ttl explicito (dados AO VIVO) tem prioridade sobre o TTL do
        endpoint: uma entrada gravada com TTL curto expira em segundos,
        nunca e servida como se fosse estado atual do jogo.
        """
        key = self._key(endpoint, params)
        effective_ttl = ttl if ttl is not None else _ttl_for(endpoint, params)
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response, created_at, ttl FROM api_cache WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        response_json, created_at, stored_ttl = row
        if stored_ttl is not None:
            effective_ttl = stored_ttl
        if now - created_at > effective_ttl:
            return None
        return json.loads(response_json)

    def set(
        self,
        endpoint: str,
        params: dict[str, Any] | None,
        response: list[dict[str, Any]],
        ttl: int | None = None,
    ) -> None:
        """Grava a resposta. ttl explicito (live) acompanha a entrada."""
        key = self._key(endpoint, params)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO api_cache
                    (key, endpoint, params, response, created_at, ttl)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    endpoint,
                    json.dumps(params or {}, sort_keys=True),
                    json.dumps(response, ensure_ascii=False),
                    time.time(),
                    ttl,
                ),
            )

    def clear_expired(self) -> int:
        """Remove entradas expiradas (manutencao). Retorna qtd removida."""
        now = time.time()
        removed = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, endpoint, params, created_at, ttl FROM api_cache"
            ).fetchall()
            for key, endpoint, params_json, created_at, stored_ttl in rows:
                try:
                    params = json.loads(params_json)
                except json.JSONDecodeError:
                    params = {}
                effective = (
                    stored_ttl if stored_ttl is not None
                    else _ttl_for(endpoint, params)
                )
                if now - created_at > effective:
                    conn.execute("DELETE FROM api_cache WHERE key = ?", (key,))
                    removed += 1
        return removed

    def stats(self) -> dict[str, int]:
        """Estatisticas simples do cache."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        return {"entradas": total}