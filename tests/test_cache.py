"""Testes do cache SQLite (sem rede)."""

from src.cache import Cache


def _cache(tmp_path):
    return Cache(db_path=str(tmp_path / "test_cache.db"))


def test_set_get_roundtrip(tmp_path):
    cache = _cache(tmp_path)
    payload = [{"id": 1, "name": "Flamengo"}]
    cache.set("/teams", {"search": "fla"}, payload)
    assert cache.get("/teams", {"search": "fla"}) == payload


def test_chaves_diferentes_por_parametros(tmp_path):
    cache = _cache(tmp_path)
    cache.set("/teams", {"search": "a"}, [{"id": 1}])
    assert cache.get("/teams", {"search": "b"}) is None


def test_ttls_diferentes_por_endpoint():
    from src.cache import _ttl_for
    from src.config import CACHE_TTL_LIVE

    assert _ttl_for("/fixtures/statistics", {}) > 86400  # estatisticas: longo
    assert _ttl_for("/fixtures", {"live": "all"}) == CACHE_TTL_LIVE


def test_limpeza_de_expirados(tmp_path):
    cache = _cache(tmp_path)
    cache.set("/teams", {"search": "x"}, [{"id": 1}])
    # forca expiracao alterando o timestamp
    import sqlite3
    import time

    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE api_cache SET created_at = ?", (time.time() - 10 * 86400,)
        )
    removed = cache.clear_expired()
    assert removed == 1
    assert cache.get("/teams", {"search": "x"}) is None