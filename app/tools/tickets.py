from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, ToolException, tool
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from app.agent.context import enforce_customer_scope, require_staff, user_from_config
from app.db import audit, get_pool
from app.tools.common import to_json

Category = Literal["billing", "technical", "account", "escalation", "other"]
Priority = Literal["low", "normal", "high", "urgent"]
Status = Literal["open", "pending", "escalated", "resolved", "closed"]


class GetTicketInput(BaseModel):
    ticket_id: int = Field(gt=0, description="Ticket id.")


class CreateTicketInput(BaseModel):
    subject: str = Field(min_length=5, max_length=150)
    description: str = Field(
        min_length=10,
        max_length=4000,
        description="Full context, including what was already tried.",
    )
    category: Category
    priority: Priority = "normal"
    customer_id: int | None = Field(
        default=None,
        gt=0,
        description="Required for staff; ignored for customers (their own id is used).",
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class UpdateTicketInput(BaseModel):
    ticket_id: int = Field(gt=0)
    status: Status | None = None
    priority: Priority | None = None
    assignee: str | None = Field(default=None, max_length=50)
    note: str | None = Field(
        default=None,
        min_length=3,
        max_length=2000,
        description="Internal note / reply to add to the ticket history.",
    )
    tool_call_id: Annotated[str, InjectedToolCallId]

    @model_validator(mode="after")
    def at_least_one_change(self):
        if not any([self.status, self.priority, self.assignee, self.note]):
            raise ValueError("Provide at least one of status, priority, assignee or note.")
        return self


async def _load_ticket(conn, ticket_id: int) -> dict:
    row = await (await conn.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))).fetchone()
    if not row:
        raise ToolException(f"Ticket {ticket_id} not found.")
    return row


@tool("get_ticket", args_schema=GetTicketInput)
async def get_ticket(ticket_id: int, *, config: RunnableConfig) -> str:
    """Get a ticket with its full event history."""
    user = user_from_config(config)
    async with get_pool().connection() as conn:
        ticket = await _load_ticket(conn, ticket_id)
        enforce_customer_scope(user, ticket["customer_id"])
        events = await (
            await conn.execute(
                "SELECT actor, event_type, details, created_at FROM ticket_events WHERE ticket_id = %s ORDER BY created_at",
                (ticket_id,),
            )
        ).fetchall()
    ticket.pop("idempotency_key", None)
    return to_json({"ticket": ticket, "events": events})


@tool("create_ticket", args_schema=CreateTicketInput)
async def create_ticket(
    subject: str,
    description: str,
    category: str,
    priority: str = "normal",
    customer_id: int | None = None,
    *,
    tool_call_id: str,
    config: RunnableConfig,
) -> str:
    """Create a support ticket for non-urgent issues that need follow-up by the support team
    (bugs, configuration help, billing questions). NOT for situations that need a human now -
    security compromise, legal threats/chargebacks, outages, or a request for a human:
    use escalate_to_human for those (it creates the ticket automatically)."""
    user = user_from_config(config)
    if user.is_staff:
        if customer_id is None:
            raise ToolException("customer_id is required when staff create a ticket.")
    else:
        customer_id = user.customer_id

    async with get_pool().connection() as conn:
        async with conn.transaction():
            exists = await (
                await conn.execute("SELECT 1 FROM customers WHERE id = %s", (customer_id,))
            ).fetchone()
            if not exists:
                raise ToolException(f"Customer {customer_id} does not exist.")
            # Idempotent on tool_call_id: a retried node never creates a duplicate ticket.
            row = await (
                await conn.execute(
                    """INSERT INTO tickets (customer_id, subject, description, category, priority, created_by, idempotency_key)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
                    (
                        customer_id,
                        subject,
                        description,
                        category,
                        priority,
                        f"agent:{user.username}",
                        tool_call_id,
                    ),
                )
            ).fetchone()
            if row is None:
                row = await (
                    await conn.execute(
                        "SELECT id FROM tickets WHERE idempotency_key = %s", (tool_call_id,)
                    )
                ).fetchone()
                return to_json(
                    {"ticket_id": row["id"], "status": "open", "note": "already created"}
                )
            await conn.execute(
                "INSERT INTO ticket_events (ticket_id, actor, event_type, details) VALUES (%s, %s, 'created', %s)",
                (row["id"], f"agent:{user.username}", Jsonb({"tool_call_id": tool_call_id})),
            )
            await audit(
                user.username,
                "create_ticket",
                f"ticket:{row['id']}",
                {"priority": priority, "category": category},
                conn=conn,
            )
    return to_json(
        {"ticket_id": row["id"], "status": "open", "priority": priority, "category": category}
    )


@tool("update_ticket", args_schema=UpdateTicketInput)
async def update_ticket(
    ticket_id: int,
    status: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    note: str | None = None,
    *,
    tool_call_id: str,
    config: RunnableConfig,
) -> str:
    """Update a ticket's status, priority or assignee, and/or add a note to its history."""
    user = user_from_config(config)
    if not user.is_staff:
        if priority or assignee:
            require_staff(user, "change ticket priority or assignee")
        if status and status != "closed":
            raise PermissionError("Customers can only close their own tickets.")

    async with get_pool().connection() as conn:
        async with conn.transaction():
            ticket = await _load_ticket(conn, ticket_id)
            enforce_customer_scope(user, ticket["customer_id"])
            done = await (
                await conn.execute(
                    "SELECT 1 FROM ticket_events WHERE ticket_id = %s AND details->>'tool_call_id' = %s",
                    (ticket_id, tool_call_id),
                )
            ).fetchone()
            if done:
                return to_json({"ticket_id": ticket_id, "note": "update already applied"})
            changes = {
                k: v
                for k, v in {"status": status, "priority": priority, "assignee": assignee}.items()
                if v
            }
            if changes:
                sets = ", ".join(f"{k} = %({k})s" for k in changes)
                await conn.execute(
                    f"UPDATE tickets SET {sets}, updated_at = now() WHERE id = %(id)s",
                    {**changes, "id": ticket_id},
                )
            await conn.execute(
                "INSERT INTO ticket_events (ticket_id, actor, event_type, details) VALUES (%s, %s, %s, %s)",
                (
                    ticket_id,
                    f"agent:{user.username}",
                    "updated" if changes else "note",
                    Jsonb({"changes": changes, "note": note, "tool_call_id": tool_call_id}),
                ),
            )
            await audit(
                user.username,
                "update_ticket",
                f"ticket:{ticket_id}",
                {"changes": changes},
                conn=conn,
            )
    return to_json({"ticket_id": ticket_id, "applied": changes, "note_added": bool(note)})
