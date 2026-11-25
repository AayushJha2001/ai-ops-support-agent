"""Test configuration.

Unit tests need nothing. Integration tests (marked `integration`) need PostgreSQL with pgvector;
they use a separate database (<name>_test) so your dev data is never touched:

    docker compose up -d db
    pytest
"""

from __future__ import annotations

import asyncio
import os

import pytest

# Deterministic, offline configuration - set BEFORE app settings are first read.
os.environ["LLM_PROVIDER"] = "fake"
os.environ["LLM_FALLBACKS"] = ""
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["PRICE_INPUT_PER_MTOK"] = "0"
os.environ["PRICE_OUTPUT_PER_MTOK"] = "0"

from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

_base = os.environ.get("DATABASE_URL", "postgresql://ops:ops@localhost:55432/ops_agent")
_params = conninfo_to_dict(_base)
_ADMIN_URL = make_conninfo(**{**_params, "dbname": "postgres"})
_TEST_DB = os.environ.get("TEST_DB_NAME", f"{_params.get('dbname', 'ops_agent')}_test")
os.environ["DATABASE_URL"] = make_conninfo(**{**_params, "dbname": _TEST_DB})

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


def pytest_asyncio_loop_factories(config, item):
    # psycopg's async driver needs a selector loop (Windows defaults to Proactor; on Linux the
    # selector loop is already the default). The hook must return a non-empty mapping.
    return {"selector": asyncio.SelectorEventLoop}


def _ensure_test_database() -> bool:
    import psycopg

    try:
        with psycopg.connect(_ADMIN_URL, autocommit=True, connect_timeout=3) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB,)
            ).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{_TEST_DB}"')
        return True
    except psycopg.OperationalError:
        return False


_DB_AVAILABLE: bool | None = None


def db_available() -> bool:
    global _DB_AVAILABLE
    if _DB_AVAILABLE is None:
        _DB_AVAILABLE = _ensure_test_database()
    return _DB_AVAILABLE


def pytest_collection_modifyitems(config, items):
    if any("integration" in item.keywords for item in items) and not db_available():
        skip = pytest.mark.skip(reason="PostgreSQL not reachable (run: docker compose up -d db)")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
async def database():
    """Fresh schema + demo data once per session."""
    from app.bootstrap import init_database
    from app.db import close_pools, open_pools

    await open_pools()
    await init_database(reset=True)
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from app.db import get_pool

    await AsyncPostgresSaver(get_pool()).setup()
    yield
    await close_pools()


@pytest.fixture(scope="session")
async def users(database):
    from app.agent.context import UserContext
    from app.db import get_pool

    async with get_pool().connection() as conn:
        rows = await (
            await conn.execute("SELECT id, username, role, customer_id FROM app_users")
        ).fetchall()
    return {r["username"]: UserContext(**r) for r in rows}
