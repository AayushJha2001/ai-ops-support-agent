"""PostgreSQL connection pools (main + least-privilege read-only)."""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

_pool: AsyncConnectionPool | None = None
_ro_pool: AsyncConnectionPool | None = None


async def open_pools(min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    """Open both pools. Safe to call more than once."""
    global _pool, _ro_pool
    settings = get_settings()
    if _pool is None:
        # autocommit + dict_row + prepare_threshold=0 are required by LangGraph's PostgresSaver.
        _pool = AsyncConnectionPool(
            settings.database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
            open=False,
        )
        await _pool.open(wait=True, timeout=15)
    if _ro_pool is None:
        _ro_pool = AsyncConnectionPool(
            settings.readonly_database_url,
            min_size=0,
            max_size=4,
            kwargs={"autocommit": False, "row_factory": dict_row},
            open=False,
        )
        await _ro_pool.open(wait=False)
    return _pool


async def close_pools() -> None:
    global _pool, _ro_pool
    if _ro_pool is not None:
        await _ro_pool.close()
        _ro_pool = None
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool is not open; call open_pools() first")
    return _pool


def get_readonly_pool() -> AsyncConnectionPool:
    if _ro_pool is None:
        raise RuntimeError("Read-only pool is not open; call open_pools() first")
    return _ro_pool


async def audit(
    actor: str, action: str, target: str | None = None, details: dict | None = None, conn=None
) -> None:
    """Append an entry to the audit log (optionally inside an existing transaction)."""
    from psycopg.types.json import Jsonb

    sql = "INSERT INTO audit_log (actor, action, target, details) VALUES (%s, %s, %s, %s)"
    params = (actor, action, target, Jsonb(details or {}))
    if conn is not None:
        await conn.execute(sql, params)
        return
    async with get_pool().connection() as c:
        await c.execute(sql, params)
