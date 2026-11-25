"""Per-turn tracing: latency, LLM calls, tokens, estimated cost and tool calls."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from psycopg.types.json import Jsonb

from app.config import get_settings
from app.db import get_pool


class RunTracker(BaseCallbackHandler):
    run_inline = True  # run synchronously in the event loop - no thread hop

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.models: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self._tool_starts: dict[UUID, tuple[str, float, Any]] = {}
        self.error: str | None = None

    # ---- LLM -------------------------------------------------------------------------
    def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        self.llm_calls += 1
        for gens in response.generations:
            for gen in gens:
                msg = getattr(gen, "message", None)
                usage = getattr(msg, "usage_metadata", None) or {}
                self.input_tokens += int(usage.get("input_tokens") or 0)
                self.output_tokens += int(usage.get("output_tokens") or 0)
                meta = getattr(msg, "response_metadata", None) or {}
                model = (
                    meta.get("model_name")
                    or meta.get("model")
                    or (response.llm_output or {}).get("model_name")
                )
                if model and model not in self.models:
                    self.models.append(model)

    # ---- tools -----------------------------------------------------------------------
    def on_tool_start(
        self,
        serialized: dict,
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict | None = None,
        **kwargs: Any,
    ) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._tool_starts[run_id] = (
            name,
            time.perf_counter(),
            inputs if inputs is not None else input_str,
        )

    def _finish_tool(self, run_id: UUID, ok: bool, error: str | None = None) -> None:
        name, t0, args = self._tool_starts.pop(run_id, ("tool", time.perf_counter(), None))
        entry = {"name": name, "ok": ok, "ms": round((time.perf_counter() - t0) * 1000)}
        if isinstance(args, dict):
            entry["args"] = {
                k: v for k, v in args.items() if k not in ("tool_call_id", "approved_calls")
            }
        if error:
            entry["error"] = error[:300]
        self.tool_calls.append(entry)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish_tool(run_id, ok=True)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish_tool(run_id, ok=False, error=str(error))

    # ---- summary ---------------------------------------------------------------------
    @property
    def latency_ms(self) -> int:
        return round((time.perf_counter() - self.started) * 1000)

    @property
    def est_cost_usd(self) -> float:
        s = get_settings()
        return (
            self.input_tokens * s.price_input_per_mtok
            + self.output_tokens * s.price_output_per_mtok
        ) / 1e6

    def summary(self) -> dict[str, Any]:
        return {
            "latency_ms": self.latency_ms,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "est_cost_usd": round(self.est_cost_usd, 6),
            "models": self.models,
            "tool_calls": self.tool_calls,
        }


async def persist_run(
    tracker: RunTracker,
    *,
    thread_id: str,
    user_id: int | None,
    kind: str,
    status: str,
    flags: list[str],
    error: str | None = None,
) -> None:
    s = tracker.summary()
    async with get_pool().connection() as conn:
        await conn.execute(
            """INSERT INTO agent_runs (thread_id, user_id, kind, status, models, latency_ms, llm_calls,
                                       input_tokens, output_tokens, est_cost_usd, tool_calls, guardrail_flags, error)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                thread_id,
                user_id,
                kind,
                status,
                Jsonb(s["models"]),
                s["latency_ms"],
                s["llm_calls"],
                s["input_tokens"],
                s["output_tokens"],
                s["est_cost_usd"],
                Jsonb(s["tool_calls"]),
                Jsonb(flags),
                error,
            ),
        )
