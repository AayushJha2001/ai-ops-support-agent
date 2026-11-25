from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, ToolException, tool
from langgraph.types import Command
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.agent.context import enforce_customer_scope, thread_id_from_config, user_from_config
from app.db import audit, get_pool
from app.tools.common import to_json

RESPONSE_TARGET = {
    "critical": "15 minutes",
    "high": "1 hour",
    "medium": "4 business hours",
    "low": "1 business day",
}


class EscalateInput(BaseModel):
    reason: str = Field(
        min_length=5, max_length=1000, description="Why a human is needed, with relevant context."
    )
    severity: Literal["low", "medium", "high", "critical"]
    ticket_id: int | None = Field(
        default=None, gt=0, description="Existing ticket to escalate, if any."
    )
    customer_id: int | None = Field(
        default=None, gt=0, description="Staff only: customer the escalation is about."
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


@tool("escalate_to_human", args_schema=EscalateInput)
async def escalate_to_human(
    reason: str,
    severity: str,
    ticket_id: int | None = None,
    customer_id: int | None = None,
    *,
    tool_call_id: str,
    config: RunnableConfig,
) -> Command:
    """Hand the conversation to a human support specialist. Use when the customer asks for a
    human, for legal threats/chargebacks, security incidents, production outages on Business or
    Enterprise plans, refunds above the assistant's limit, or when you cannot resolve the issue."""
    user = user_from_config(config)
    thread_id = thread_id_from_config(config)
    async with get_pool().connection() as conn:
        async with conn.transaction():
            if ticket_id is not None:
                t = await (
                    await conn.execute(
                        "SELECT customer_id FROM tickets WHERE id = %s", (ticket_id,)
                    )
                ).fetchone()
                if not t:
                    raise ToolException(f"Ticket {ticket_id} not found.")
                enforce_customer_scope(user, t["customer_id"])
                await conn.execute(
                    "UPDATE tickets SET status = 'escalated', updated_at = now() WHERE id = %s",
                    (ticket_id,),
                )
            else:
                cust = user.customer_id if not user.is_staff else customer_id
                if cust is None:
                    raise ToolException("Staff must pass ticket_id or customer_id to escalate.")
                row = await (
                    await conn.execute(
                        """INSERT INTO tickets (customer_id, subject, description, category, priority, status,
                                            created_by, idempotency_key)
                       VALUES (%s, %s, %s, 'escalation', %s, 'escalated', %s, %s)
                       ON CONFLICT (idempotency_key) DO UPDATE SET updated_at = now()
                       RETURNING id""",
                        (
                            cust,
                            f"Escalation: {reason[:80]}",
                            reason,
                            {"critical": "urgent", "high": "high"}.get(severity, "normal"),
                            f"agent:{user.username}",
                            tool_call_id,
                        ),
                    )
                ).fetchone()
                ticket_id = row["id"]
            await conn.execute(
                "INSERT INTO ticket_events (ticket_id, actor, event_type, details) VALUES (%s, %s, 'escalated', %s)",
                (
                    ticket_id,
                    f"agent:{user.username}",
                    Jsonb({"reason": reason, "severity": severity, "tool_call_id": tool_call_id}),
                ),
            )
            esc = await (
                await conn.execute(
                    "INSERT INTO escalations (thread_id, ticket_id, reason, severity) VALUES (%s, %s, %s, %s) RETURNING id",
                    (thread_id, ticket_id, reason, severity),
                )
            ).fetchone()
            await audit(
                user.username, "escalate", f"ticket:{ticket_id}", {"severity": severity}, conn=conn
            )

    payload = to_json(
        {
            "escalation_id": esc["id"],
            "ticket_id": ticket_id,
            "severity": severity,
            "expected_response": RESPONSE_TARGET[severity],
        }
    )
    return Command(
        update={
            "status": "escalated",
            "messages": [
                ToolMessage(content=payload, tool_call_id=tool_call_id, name="escalate_to_human")
            ],
        }
    )
