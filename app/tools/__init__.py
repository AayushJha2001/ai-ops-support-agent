"""Agent tools. Every tool validates its input with a Pydantic schema and enforces RBAC itself."""

from app.tools.customers import lookup_customer
from app.tools.escalation import escalate_to_human
from app.tools.knowledge import search_knowledge_base
from app.tools.refunds import issue_refund
from app.tools.sql import run_sql_query
from app.tools.tickets import create_ticket, get_ticket, update_ticket

ALL_TOOLS = [
    search_knowledge_base,
    lookup_customer,
    get_ticket,
    run_sql_query,
    create_ticket,
    update_ticket,
    issue_refund,
    escalate_to_human,
]

STAFF_ONLY_TOOLS = {"run_sql_query"}


def tools_for_role(role: str):
    if role in ("support_agent", "admin"):
        return ALL_TOOLS
    return [t for t in ALL_TOOLS if t.name not in STAFF_ONLY_TOOLS]


__all__ = ["ALL_TOOLS", "STAFF_ONLY_TOOLS", "tools_for_role"]
