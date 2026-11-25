"""Deterministic offline chat models.

* RuleBasedFakeChatModel - keyword router that emits realistic tool calls, so the whole stack
  (graph, approvals, API, evals) runs with zero API keys - used for demos and CI smoke evals.
* ScriptedChatModel      - replays a fixed list of AIMessages; used in unit/integration tests.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_INVOICE = re.compile(r"\bINV-\d+-\d+\b", re.I)
_AMOUNT = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s?(?:usd|dollars)", re.I)
_TICKET = re.compile(r"(?:ticket|#)\s*#?(\d+)", re.I)
_CUST = re.compile(r"\bCUS-\d+\b|\b[\w.+-]+@[\w-]+\.[\w.]+\b", re.I)


def _usage(messages: Sequence[BaseMessage], out: str) -> dict:
    n_in = sum(len(str(m.content)) for m in messages) // 4
    n_out = max(1, len(out) // 4)
    return {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}


def _call(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}


class RuleBasedFakeChatModel(BaseChatModel):
    model_name: str = "rule-based-fake"

    @property
    def _llm_type(self) -> str:
        return "rule-based-fake"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        return self

    # -- routing -------------------------------------------------------------------------
    def _route(self, text: str, is_staff: bool) -> dict | None:
        t = text.lower()
        if any(
            k in t
            for k in (
                "speak to a human",
                "talk to a human",
                "real person",
                "manager",
                "lawyer",
                "legal action",
                "chargeback",
                "hacked",
                "compromised",
                "breach",
            )
        ):
            sev = "critical" if any(k in t for k in ("hacked", "compromised", "breach")) else "high"
            return _call("escalate_to_human", {"reason": text[:300], "severity": sev})
        if "refund" in t and (inv := _INVOICE.search(text)):
            amt = _AMOUNT.search(text)
            amount = float(amt.group(1) or amt.group(2)) if amt else 19.0
            return _call(
                "issue_refund",
                {
                    "invoice_number": inv.group(0).upper(),
                    "amount_usd": amount,
                    "reason": text[:200],
                },
            )
        if (m := _TICKET.search(text)) and any(k in t for k in ("close", "resolve")):
            return _call(
                "update_ticket",
                {"ticket_id": int(m.group(1)), "status": "closed" if "close" in t else "resolved"},
            )
        if (m := _TICKET.search(text)) and any(
            k in t for k in ("status of", "update on", "history")
        ):
            return _call("get_ticket", {"ticket_id": int(m.group(1))})
        if any(
            k in t
            for k in (
                "open a ticket",
                "create a ticket",
                "file a ticket",
                "raise a ticket",
                "report a bug",
            )
        ):
            return _call(
                "create_ticket",
                {
                    "subject": text[:80].strip() or "Support request",
                    "description": text,
                    "category": "technical"
                    if any(k in t for k in ("api", "error", "bug", "down"))
                    else "other",
                    "priority": "normal",
                },
            )
        if is_staff and any(
            k in t for k in ("how many", "count", "total revenue", "top ", "list all", "average")
        ):
            if "past due" in t or "past_due" in t:
                sql = "SELECT count(*) AS past_due_subscriptions FROM subscriptions WHERE status = 'past_due'"
            elif "revenue" in t:
                sql = "SELECT sum(amount_usd - refunded_amount_usd) AS net_revenue_usd FROM invoices WHERE status IN ('paid','partially_refunded')"
            elif "ticket" in t:
                sql = "SELECT status, count(*) AS n FROM tickets GROUP BY status ORDER BY n DESC"
            else:
                sql = "SELECT plan_id, count(*) AS n FROM subscriptions WHERE status = 'active' GROUP BY plan_id"
            return _call("run_sql_query", {"query": sql, "purpose": text[:150]})
        if (m := _CUST.search(text)) or any(
            k in t
            for k in (
                "my account",
                "my subscription",
                "my plan",
                "my invoice",
                "my bill",
                "my tickets",
            )
        ):
            args = {"identifier": m.group(0)} if m else {}
            return _call("lookup_customer", args)
        return _call("search_knowledge_base", {"query": text[:250]})

    def _generate(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> ChatResult:
        system = next((m for m in messages if isinstance(m, SystemMessage)), None)
        is_staff = bool(system and "SUPPORT TEAM MEMBER" in str(system.content))
        last = messages[-1]
        if isinstance(last, HumanMessage):
            call = self._route(str(last.content), is_staff)
            msg = AIMessage(content="", tool_calls=[call])
        elif isinstance(last, ToolMessage):
            msg = AIMessage(content=self._summarise(messages))
        else:
            msg = AIMessage(content="How can I help you with your Acme Cloud account today?")
        msg.usage_metadata = _usage(messages, str(msg.content) or json.dumps(msg.tool_calls))
        msg.response_metadata = {"model_name": self.model_name}
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _summarise(self, messages: list[BaseMessage]) -> str:
        results = []
        for m in reversed(messages):
            if not isinstance(m, ToolMessage):
                break
            results.append(m)
        results.reverse()
        parts = []
        for r in results:
            content = str(r.content)
            if r.status == "error" or content.startswith("Error") or "REJECTED" in content:
                parts.append(f"I couldn't complete `{r.name}`: {content[:300]}")
            elif r.name == "search_knowledge_base":
                parts.append(f"Here's what our help center says:\n{content[:700]}")
            elif r.name == "escalate_to_human":
                d = json.loads(content)
                parts.append(
                    f"I've escalated this to a human specialist (ticket #{d['ticket_id']}). "
                    f"Expected response time: {d['expected_response']}."
                )
            elif r.name == "issue_refund":
                d = json.loads(content)
                parts.append(
                    f"Your refund of {d.get('amount_usd')} USD on {d.get('invoice')} was approved "
                    f"and issued. It will arrive in 5-10 business days."
                )
            elif r.name == "create_ticket":
                d = json.loads(content)
                parts.append(
                    f"I've created ticket #{d['ticket_id']} for you. Our team will follow up."
                )
            else:
                parts.append(f"Here are the details from `{r.name}`:\n{content[:700]}")
        return "\n\n".join(parts) or "Done."

    def with_structured_output(self, schema, **kwargs):  # used by the eval judge
        raise NotImplementedError("Use a real provider for LLM-as-judge grading.")


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted responses in order; tests assert on the resulting graph behaviour."""

    responses: list[AIMessage]
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.index >= len(self.responses):
            raise RuntimeError("ScriptedChatModel ran out of responses")
        msg = self.responses[self.index].model_copy(deep=True)
        self.index += 1
        msg.usage_metadata = msg.usage_metadata or _usage(messages, str(msg.content))
        return ChatResult(generations=[ChatGeneration(message=msg)])
