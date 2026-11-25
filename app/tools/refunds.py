from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, ToolException, tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from app.agent.context import enforce_customer_scope, user_from_config
from app.config import get_settings
from app.db import audit, get_pool
from app.tools.common import to_json

REFUND_HARD_WINDOW_DAYS = 90


class IssueRefundInput(BaseModel):
    invoice_number: str = Field(
        pattern=r"^INV-\d+-\d+$", description="Invoice number, e.g. INV-1004-04."
    )
    amount_usd: float = Field(gt=0, le=100_000, description="Amount to refund in USD.")
    reason: str = Field(min_length=5, max_length=500)
    tool_call_id: Annotated[str, InjectedToolCallId]
    approved_calls: Annotated[dict, InjectedState("approved_calls")]


@tool("issue_refund", args_schema=IssueRefundInput)
async def issue_refund(
    invoice_number: str,
    amount_usd: float,
    reason: str,
    *,
    tool_call_id: str,
    approved_calls: dict | None,
    config: RunnableConfig,
) -> str:
    """Refund all or part of a paid invoice. ALWAYS requires human approval; the system pauses
    for a specialist automatically. Check eligibility with the refund policy first."""
    user = user_from_config(config)
    reviewer = (approved_calls or {}).get(tool_call_id)
    if not reviewer:
        # Defence in depth: the graph routes refunds through the approval gate, but the tool
        # itself also refuses to move money without a recorded human approval.
        raise ToolException("Refund blocked: no human approval recorded for this action.")

    s = get_settings()
    amount = Decimal(str(round(amount_usd, 2)))
    if amount > Decimal(str(s.refund_max_usd)):
        raise ToolException(
            f"Refunds above {s.refund_max_usd:.0f} USD cannot be issued by the assistant; escalate to billing."
        )

    async with get_pool().connection() as conn:
        async with conn.transaction():
            inv = await (
                await conn.execute(
                    "SELECT * FROM invoices WHERE number = %s FOR UPDATE", (invoice_number,)
                )
            ).fetchone()
            if not inv:
                raise ToolException(f"Invoice {invoice_number} not found.")
            enforce_customer_scope(user, inv["customer_id"])

            existing = await (
                await conn.execute(
                    "SELECT id, amount_usd FROM refunds WHERE idempotency_key = %s", (tool_call_id,)
                )
            ).fetchone()
            if existing:
                return to_json({"refund_id": existing["id"], "note": "refund already issued"})

            if inv["status"] not in ("paid", "partially_refunded"):
                raise ToolException(
                    f"Invoice {invoice_number} has status '{inv['status']}' and cannot be refunded."
                )
            if datetime.now(UTC) - inv["issued_at"] > timedelta(days=REFUND_HARD_WINDOW_DAYS):
                raise ToolException(
                    f"Invoices older than {REFUND_HARD_WINDOW_DAYS} days are not refundable."
                )
            refundable = inv["amount_usd"] - inv["refunded_amount_usd"]
            if amount > refundable:
                raise ToolException(f"Amount exceeds refundable balance ({refundable} USD).")

            new_refunded = inv["refunded_amount_usd"] + amount
            new_status = "refunded" if new_refunded >= inv["amount_usd"] else "partially_refunded"
            row = await (
                await conn.execute(
                    """INSERT INTO refunds (invoice_id, customer_id, amount_usd, reason, approved_by, idempotency_key)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (inv["id"], inv["customer_id"], amount, reason, reviewer, tool_call_id),
                )
            ).fetchone()
            await conn.execute(
                "UPDATE invoices SET refunded_amount_usd = %s, status = %s WHERE id = %s",
                (new_refunded, new_status, inv["id"]),
            )
            await audit(
                user.username,
                "issue_refund",
                f"invoice:{invoice_number}",
                {"amount_usd": str(amount), "approved_by": reviewer, "reason": reason},
                conn=conn,
            )
    return to_json(
        {
            "refund_id": row["id"],
            "invoice": invoice_number,
            "amount_usd": amount,
            "invoice_status": new_status,
            "approved_by": reviewer,
            "eta": "5-10 business days to the original payment method",
        }
    )
