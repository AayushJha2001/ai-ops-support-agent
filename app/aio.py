"""Event-loop helpers.

psycopg's async driver cannot run on Windows' default ProactorEventLoop, so every entry point
(CLI, eval runner, local server) goes through `run()` which picks a SelectorEventLoop there.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def loop_factory():
    return asyncio.SelectorEventLoop if sys.platform == "win32" else None


def run(coro: Coroutine[Any, Any, T]) -> T:
    factory = loop_factory()
    if factory is None:
        return asyncio.run(coro)
    return asyncio.run(coro, loop_factory=factory)
