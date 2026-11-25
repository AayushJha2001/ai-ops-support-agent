from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field

from app.agent.context import enforce_customer_scope, user_from_config
from app.db import get_pool
from app.tools.common import to_json

_EXT_ID = re.compile(r"^CUS-\d+$", re.IGNORECASE)


class LookupCustomerInput(BaseModel):
    identifier: str | None = Field(
        default=None,
        max_length=200,
        description="Customer email, external id (e.g. CUS-1001) or numeric id. "
        "Omit when a customer asks about their own account.",
    )


async def resolve_customer_id(conn, identifier: str) -> int:
    ident = identifier.strip()
    if _EXT_ID.match(ident):
        q, p = "SELECT id FROM customers WHERE upper(external_id) = upper(%s)", ident
    elif "@" in ident:
        q, p = "SELECT id FROM customers WHERE lower(email) = lower(%s)", ident
    elif ident.isdigit():
        q, p = "SELECT id FROM customers WHERE id = %s", int(ident)
    else:
        raise ToolException(
            "Identifier must be an email, an external id like CUS-1001, or a numeric id."
        )
    row = await (await conn.execute(q, (p,))).fetchone()
    if not row:
        raise ToolException(f"No customer found for '{ident}'.")
    return row["id"]


@tool("lookup_customer", args_schema=LookupCustomerInput)
async def lookup_customer(identifier: str | None = None, *, config: RunnableConfig) -> str:
    """Look up a customer's profile, subscription, recent invoices and open tickets."""
    user = user_from_config(config)
    async with get_pool().connection() as conn:
        if identifier:
            customer_id = await resolve_customer_id(conn, identifier)
        elif user.customer_id is not None:
            customer_id = user.customer_id
        else:
            raise ToolException("Provide an identifier (email or CUS-id) to look up a customer.")
        enforce_customer_scope(user, customer_id)

        profile = await (
            await conn.execute(
                "SELECT id, external_id, name, email, company, country, status, created_at FROM customers WHERE id = %s",
                (customer_id,),
            )
        ).fetchone()
        sub = await (
            await conn.execute(
                """SELECT s.plan_id, p.name AS plan_name, p.monthly_price_usd, p.seats, s.billing_cycle,
                      s.status, s.started_at, s.renews_at, s.canceled_at
               FROM subscriptions s JOIN plans p ON p.id = s.plan_id
               WHERE s.customer_id = %s ORDER BY s.started_at DESC LIMIT 1""",
                (customer_id,),
            )
        ).fetchone()
        invoices = await (
            await conn.execute(
                """SELECT number, amount_usd, refunded_amount_usd, status, issued_at, due_at, paid_at
               FROM invoices WHERE customer_id = %s ORDER BY issued_at DESC LIMIT 6""",
                (customer_id,),
            )
        ).fetchall()
        tickets = await (
            await conn.execute(
                """SELECT id, subject, category, priority, status, created_at FROM tickets
               WHERE customer_id = %s AND status NOT IN ('resolved', 'closed')
               ORDER BY created_at DESC LIMIT 5""",
                (customer_id,),
            )
        ).fetchall()
    return to_json(
        {
            "customer": profile,
            "subscription": sub,
            "recent_invoices": invoices,
            "open_tickets": tickets,
        }
    )
