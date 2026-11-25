"""Application service around the graph: threads, turns, streaming, approvals, metrics.
Used by both the FastAPI layer and the evaluation runner."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Command

from app.agent.context import UserContext
from app.agent.messages import text_of
from app.db import get_pool
from app.observability.tracker import RunTracker, persist_run

log = logging.getLogger(__name__)


class ThreadNotFound(LookupError):
    pass


class ThreadBusy(RuntimeError):
    pass


class ApprovalNotFound(LookupError):
    pass


class ApprovalConflict(RuntimeError):
    pass


@dataclass
class TurnResult:
    thread_id: str
    status: str
    reply: str
    approval: dict[str, Any] | None = None
    flags: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def load_user(user_id: int) -> UserContext:
    async with get_pool().connection() as conn:
        row = await (
            await conn.execute(
                "SELECT id, username, role, customer_id FROM app_users WHERE id = %s", (user_id,)
            )
        ).fetchone()
    if not row:
        raise LookupError(f"user {user_id} not found")
    return UserContext(**row)


class AgentService:
    def __init__(self, graph):
        self.graph = graph

    # ------------------------------------------------------------------ threads
    async def _prepare_thread(self, user: UserContext, thread_id: str | None, message: str) -> str:
        async with get_pool().connection() as conn:
            if thread_id is None:
                thread_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO threads (thread_id, user_id, title) VALUES (%s, %s, %s)",
                    (thread_id, user.id, message[:80]),
                )
                return thread_id
            row = await (
                await conn.execute(
                    "SELECT user_id, status FROM threads WHERE thread_id = %s", (thread_id,)
                )
            ).fetchone()
        if not row or (row["user_id"] != user.id and user.role != "admin"):
            raise ThreadNotFound(thread_id)
        if row["status"] == "awaiting_approval":
            raise ThreadBusy("This conversation is waiting for a specialist to approve an action.")
        return thread_id

    async def _thread_owner(self, thread_id: str) -> UserContext:
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute("SELECT user_id FROM threads WHERE thread_id = %s", (thread_id,))
            ).fetchone()
        if not row:
            raise ThreadNotFound(thread_id)
        return await load_user(row["user_id"])

    def _config(self, thread_id: str, owner: UserContext, tracker: RunTracker) -> dict:
        return {
            "configurable": {"thread_id": thread_id, "user": owner.to_dict()},
            "callbacks": [tracker],
            "recursion_limit": 50,
            "metadata": {"user": owner.username, "role": owner.role},
        }

    @staticmethod
    def _turn_input(message: str) -> dict:
        return {
            "messages": [HumanMessage(content=message)],
            "steps": 0,
            "flags": [],
            "status": "active",
        }

    # ------------------------------------------------------------------ outcome
    async def _finalize(
        self,
        thread_id: str,
        owner: UserContext,
        tracker: RunTracker,
        kind: str,
        error: str | None = None,
    ) -> TurnResult:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.graph.aget_state(config)
        values = snapshot.values or {}
        interrupts = [i for task in snapshot.tasks for i in (task.interrupts or ())]
        flags = list(values.get("flags") or [])
        approval = None
        if error:
            status, reply = (
                "error",
                "Sorry - something went wrong on our side. Please try again in a moment.",
            )
        elif interrupts:
            approval = interrupts[0].value
            status = "awaiting_approval"
            reasons = "; ".join(approval.get("reasons") or [])
            reply = (
                f"This action needs approval from a support specialist before I can proceed ({reasons}). "
                f"Approval request #{approval.get('approval_id')} has been sent - I'll continue as soon as it's reviewed."
            )
        else:
            status = values.get("status") or "completed"
            last_ai = next(
                (m for m in reversed(values.get("messages", [])) if isinstance(m, AIMessage)), None
            )
            reply = text_of(last_ai) if last_ai else ""

        async with get_pool().connection() as conn:
            await conn.execute(
                "UPDATE threads SET status = %s, updated_at = now() WHERE thread_id = %s",
                ("active" if status == "error" else status, thread_id),
            )
        await persist_run(
            tracker,
            thread_id=thread_id,
            user_id=owner.id,
            kind=kind,
            status=status,
            flags=flags,
            error=error,
        )
        return TurnResult(
            thread_id=thread_id,
            status=status,
            reply=reply,
            approval=approval,
            flags=flags,
            metrics=tracker.summary(),
        )

    async def _invoke(
        self, graph_input, thread_id: str, owner: UserContext, kind: str
    ) -> TurnResult:
        tracker = RunTracker()
        error = None
        try:
            await self.graph.ainvoke(graph_input, self._config(thread_id, owner, tracker))
        except Exception as e:  # surfaced to the caller as status=error, recorded in agent_runs
            log.exception("agent run failed (thread %s)", thread_id)
            error = f"{type(e).__name__}: {e}"
        return await self._finalize(thread_id, owner, tracker, kind, error)

    # ------------------------------------------------------------------ public API
    async def run_turn(
        self, user: UserContext, message: str, thread_id: str | None = None
    ) -> TurnResult:
        thread_id = await self._prepare_thread(user, thread_id, message)
        return await self._invoke(self._turn_input(message), thread_id, user, "turn")

    async def stream_turn(
        self, user: UserContext, message: str, thread_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields {"event": ..., "data": ...} dicts: thread, token, tool_calls, tool_result, done."""
        thread_id = await self._prepare_thread(user, thread_id, message)
        yield {"event": "thread", "data": {"thread_id": thread_id}}
        tracker = RunTracker()
        error = None
        try:
            async for mode, chunk in self.graph.astream(
                self._turn_input(message),
                self._config(thread_id, user, tracker),
                stream_mode=["messages", "updates"],
            ):
                if mode == "messages":
                    msg, meta = chunk
                    if meta.get("langgraph_node") == "agent" and isinstance(msg, AIMessageChunk):
                        text = text_of(msg)
                        if text:
                            yield {"event": "token", "data": {"text": text}}
                    continue
                for node, update in (chunk or {}).items():
                    for msg in _messages_in(update):
                        if node == "agent" and isinstance(msg, AIMessage) and msg.tool_calls:
                            yield {
                                "event": "tool_calls",
                                "data": [
                                    {"name": c["name"], "args": c["args"]} for c in msg.tool_calls
                                ],
                            }
                        elif isinstance(msg, ToolMessage):
                            yield {
                                "event": "tool_result",
                                "data": {
                                    "name": msg.name,
                                    "status": msg.status,
                                    "preview": text_of(msg)[:300],
                                },
                            }
        except Exception as e:
            log.exception("streamed agent run failed (thread %s)", thread_id)
            error = f"{type(e).__name__}: {e}"
        result = await self._finalize(thread_id, user, tracker, "turn", error)
        yield {"event": "done", "data": result.to_dict()}

    async def decide(
        self, reviewer: UserContext, approval_id: int, approve: bool, note: str | None = None
    ) -> TurnResult:
        if not reviewer.is_staff:
            raise PermissionError("Only support staff can review approvals.")
        decision = "approved" if approve else "rejected"
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT id, thread_id, status, requested_by FROM approvals WHERE id = %s",
                    (approval_id,),
                )
            ).fetchone()
            if not row:
                raise ApprovalNotFound(approval_id)
            if row["requested_by"] == reviewer.username and reviewer.role != "admin":
                raise PermissionError("Four-eyes rule: you cannot approve your own request.")
            claimed = await (
                await conn.execute(
                    """UPDATE approvals SET status = %s, reviewed_by = %s, review_note = %s, decided_at = now()
                   WHERE id = %s AND status = 'pending' RETURNING thread_id""",
                    (decision, reviewer.username, note, approval_id),
                )
            ).fetchone()
        if not claimed:
            raise ApprovalConflict(f"Approval {approval_id} was already {row['status']}.")

        thread_id = claimed["thread_id"]
        owner = await self._thread_owner(thread_id)
        resume = Command(
            resume={
                "decision": "approve" if approve else "reject",
                "reviewer": reviewer.username,
                "note": note,
            }
        )
        result = await self._invoke(resume, thread_id, owner, "resume")
        if result.status == "error":  # put the request back in the queue
            async with get_pool().connection() as conn:
                await conn.execute(
                    "UPDATE approvals SET status = 'pending', reviewed_by = NULL, decided_at = NULL WHERE id = %s",
                    (approval_id,),
                )
                await conn.execute(
                    "UPDATE threads SET status = 'awaiting_approval' WHERE thread_id = %s",
                    (thread_id,),
                )
        return result

    async def history(self, user: UserContext, thread_id: str) -> list[dict[str, Any]]:
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute("SELECT user_id FROM threads WHERE thread_id = %s", (thread_id,))
            ).fetchone()
        if not row or (row["user_id"] != user.id and not user.is_staff):
            raise ThreadNotFound(thread_id)
        snapshot = await self.graph.aget_state({"configurable": {"thread_id": thread_id}})
        out = []
        for m in (snapshot.values or {}).get("messages", []):
            item: dict[str, Any] = {"role": m.type, "content": text_of(m)}
            if isinstance(m, AIMessage) and m.tool_calls:
                item["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in m.tool_calls]
            if isinstance(m, ToolMessage):
                item["name"] = m.name
                item["status"] = m.status
            out.append(item)
        return out


def _messages_in(update: Any) -> list:
    """Normalise a stream 'updates' payload (dict, list of dicts or Commands) into messages."""
    items = update if isinstance(update, list) else [update]
    msgs = []
    for item in items:
        if isinstance(item, Command):
            item = item.update
        if isinstance(item, dict):
            value = item.get("messages") or []
            msgs.extend(value if isinstance(value, list) else [value])
    return msgs
