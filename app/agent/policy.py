"""Human-in-the-loop approval policy for write actions."""

from __future__ import annotations

from typing import Any

from app.config import get_settings

READ_TOOLS = {"search_knowledge_base", "lookup_customer", "get_ticket", "run_sql_query"}
WRITE_TOOLS = {"create_ticket", "update_ticket", "issue_refund", "escalate_to_human"}
# Escalation is a write, but it must never wait on a human - a human is exactly who it summons.
NEVER_GATED = {"escalate_to_human"}


def approval_reason(tool_name: str, args: dict[str, Any], mode: str | None = None) -> str | None:
    """Return why this call needs human approval, or None if it can run immediately."""
    mode = mode or get_settings().approval_mode
    if tool_name in NEVER_GATED or tool_name in READ_TOOLS:
        return None
    if tool_name == "issue_refund":
        return (
            f"Moves money: refund of {args.get('amount_usd')} USD on {args.get('invoice_number')}."
        )
    if tool_name == "update_ticket":
        if args.get("status") in ("resolved", "closed"):
            return f"Closes ticket #{args.get('ticket_id')} (status -> {args['status']})."
        if args.get("priority") == "urgent":
            return f"Raises ticket #{args.get('ticket_id')} to urgent priority."
    if tool_name == "create_ticket" and args.get("priority") == "urgent":
        return "Creates an urgent ticket (pages the on-call team)."
    if mode == "all_writes" and tool_name in WRITE_TOOLS:
        return "APPROVAL_MODE=all_writes: every write action needs approval."
    return None


def gated_calls(tool_calls: list[dict]) -> list[tuple[dict, str]]:
    out = []
    for call in tool_calls:
        reason = approval_reason(call["name"], call.get("args") or {})
        if reason:
            out.append((call, reason))
    return out
