from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from app.agent.context import UserContext
from app.agent.service import (
    AgentService,
    ApprovalConflict,
    ApprovalNotFound,
    ThreadBusy,
    ThreadNotFound,
)
from app.api.auth import current_user, staff_user
from app.api.schemas import ChatRequest, ChatResponse, DecisionRequest
from app.db import get_pool
from app.tools.common import to_json

router = APIRouter(prefix="/v1")


def service(request: Request) -> AgentService:
    return request.app.state.agent_service


def _map_errors(e: Exception) -> HTTPException:
    if isinstance(e, ThreadNotFound | ApprovalNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if isinstance(e, ThreadBusy | ApprovalConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(e))
    if isinstance(e, PermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    raise e


def _rows(rows: list[dict]) -> list[dict]:
    return json.loads(to_json(rows))


# ------------------------------------------------------------------ chat
@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(
    body: ChatRequest,
    user: UserContext = Depends(current_user),
    svc: AgentService = Depends(service),
):
    """Run one agent turn and return the final reply (or a pending approval)."""
    try:
        result = await svc.run_turn(user, body.message, body.thread_id)
    except Exception as e:
        raise _map_errors(e) from e
    return result.to_dict()


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(
    body: ChatRequest,
    user: UserContext = Depends(current_user),
    svc: AgentService = Depends(service),
):
    """Server-Sent Events: `thread`, `token`, `tool_calls`, `tool_result`, `done`.
    The `done` payload carries the guard-railed final reply - clients should render it as final."""
    stream = svc.stream_turn(user, body.message, body.thread_id)
    try:
        first = await anext(stream)  # validates thread ownership before the 200 is sent
    except Exception as e:
        raise _map_errors(e) from e

    async def events():
        yield {"event": first["event"], "data": to_json(first["data"])}
        async for item in stream:
            yield {"event": item["event"], "data": to_json(item["data"])}

    return EventSourceResponse(events())


@router.get("/threads", tags=["chat"])
async def list_threads(user: UserContext = Depends(current_user), limit: int = Query(20, le=100)):
    async with get_pool().connection() as conn:
        rows = await (
            await conn.execute(
                """SELECT thread_id, title, status, created_at, updated_at FROM threads
               WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s""",
                (user.id, limit),
            )
        ).fetchall()
    return _rows(rows)


@router.get("/threads/{thread_id}", tags=["chat"])
async def get_thread(
    thread_id: str, user: UserContext = Depends(current_user), svc: AgentService = Depends(service)
):
    try:
        return {"thread_id": thread_id, "messages": await svc.history(user, thread_id)}
    except Exception as e:
        raise _map_errors(e) from e


# ------------------------------------------------------------------ approvals (human in the loop)
@router.get("/approvals", tags=["approvals"])
async def list_approvals(
    _: UserContext = Depends(staff_user),
    status_filter: str = Query(
        "pending", alias="status", pattern="^(pending|approved|rejected|all)$"
    ),
):
    where, params = (
        ("", ()) if status_filter == "all" else ("WHERE a.status = %s", (status_filter,))
    )
    async with get_pool().connection() as conn:
        rows = await (
            await conn.execute(
                f"""SELECT a.*, t.title AS thread_title FROM approvals a
                JOIN threads t ON t.thread_id = a.thread_id {where}
                ORDER BY a.created_at DESC LIMIT 100""",
                params,
            )
        ).fetchall()
    return _rows(rows)


@router.post("/approvals/{approval_id}/decision", response_model=ChatResponse, tags=["approvals"])
async def decide(
    approval_id: int,
    body: DecisionRequest,
    reviewer: UserContext = Depends(staff_user),
    svc: AgentService = Depends(service),
):
    """Approve or reject a pending write action; the paused agent run resumes immediately."""
    try:
        result = await svc.decide(reviewer, approval_id, body.decision == "approve", body.note)
    except Exception as e:
        raise _map_errors(e) from e
    return result.to_dict()


# ------------------------------------------------------------------ tickets
@router.get("/tickets", tags=["tickets"])
async def list_tickets(
    user: UserContext = Depends(current_user),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200),
):
    clauses, params = [], []
    if not user.is_staff:
        clauses.append("customer_id = %s")
        params.append(user.customer_id)
    if status_filter:
        clauses.append("status = %s")
        params.append(status_filter)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with get_pool().connection() as conn:
        rows = await (
            await conn.execute(
                f"""SELECT id, customer_id, subject, category, priority, status, assignee, created_by,
                       created_at, updated_at FROM tickets {where} ORDER BY updated_at DESC LIMIT %s""",
                (*params, limit),
            )
        ).fetchall()
    return _rows(rows)


@router.get("/tickets/{ticket_id}", tags=["tickets"])
async def get_ticket(ticket_id: int, user: UserContext = Depends(current_user)):
    async with get_pool().connection() as conn:
        ticket = await (
            await conn.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        ).fetchone()
        if not ticket or (not user.is_staff and ticket["customer_id"] != user.customer_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        events = await (
            await conn.execute(
                "SELECT actor, event_type, details, created_at FROM ticket_events WHERE ticket_id = %s ORDER BY id",
                (ticket_id,),
            )
        ).fetchall()
    ticket.pop("idempotency_key", None)
    return {"ticket": _rows([ticket])[0], "events": _rows(events)}


# ------------------------------------------------------------------ metrics
@router.get("/metrics/summary", tags=["metrics"])
async def metrics_summary(
    _: UserContext = Depends(staff_user), hours: int = Query(24, ge=1, le=24 * 90)
):
    """Task outcome mix, latency percentiles, tokens and cost over the window."""
    async with get_pool().connection() as conn:
        agg = await (
            await conn.execute(
                """SELECT count(*) AS runs,
                      count(*) FILTER (WHERE status = 'completed') AS completed,
                      count(*) FILTER (WHERE status = 'awaiting_approval') AS awaiting_approval,
                      count(*) FILTER (WHERE status = 'escalated') AS escalated,
                      count(*) FILTER (WHERE status = 'blocked') AS blocked,
                      count(*) FILTER (WHERE status = 'error') AS errors,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
                      coalesce(avg(llm_calls), 0) AS avg_llm_calls,
                      coalesce(sum(input_tokens), 0) AS input_tokens,
                      coalesce(sum(output_tokens), 0) AS output_tokens,
                      coalesce(sum(est_cost_usd), 0) AS est_cost_usd
               FROM agent_runs WHERE created_at > now() - make_interval(hours => %s)""",
                (hours,),
            )
        ).fetchone()
        tools = await (
            await conn.execute(
                """SELECT t->>'name' AS tool, count(*) AS calls,
                      count(*) FILTER (WHERE (t->>'ok')::boolean IS NOT TRUE) AS failures,
                      round(avg((t->>'ms')::int)) AS avg_ms
               FROM agent_runs r, jsonb_array_elements(r.tool_calls) t
               WHERE r.created_at > now() - make_interval(hours => %s)
               GROUP BY 1 ORDER BY calls DESC""",
                (hours,),
            )
        ).fetchall()
        approvals = await (
            await conn.execute(
                """SELECT status, count(*) AS n FROM approvals
               WHERE created_at > now() - make_interval(hours => %s) GROUP BY status""",
                (hours,),
            )
        ).fetchall()
    return {
        "window_hours": hours,
        "runs": _rows([agg])[0],
        "tools": _rows(tools),
        "approvals": {r["status"]: r["n"] for r in approvals},
    }


@router.get("/runs", tags=["metrics"])
async def recent_runs(
    _: UserContext = Depends(staff_user), limit: int = Query(50, le=200)
) -> list[dict[str, Any]]:
    async with get_pool().connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT %s", (limit,)
            )
        ).fetchall()
    return _rows(rows)
