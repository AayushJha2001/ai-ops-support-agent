"""The support agent as a LangGraph state machine.

START -> guard_input -> agent --(no tool calls)--> guard_output -> END
             |            |  ^
          (blocked)       |  |  (results)
             v            v  |
            END     route_tools --(read-only / low-risk)--> tools
                          |
                     (risky write)
                          v
                 request_approval -> await_approval --approve--> tools
                                   (interrupt; resumed  --reject--> agent
                                    by a human via API)
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import psycopg
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import ToolException
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.types import Command, RetryPolicy, interrupt
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout

from app.agent.context import thread_id_from_config, user_from_config
from app.agent.guardrails import check_input, check_output, new_canary
from app.agent.messages import text_of, trim_history
from app.agent.policy import gated_calls
from app.agent.prompts import system_prompt
from app.config import get_settings
from app.db import get_pool
from app.tools import ALL_TOOLS, tools_for_role

log = logging.getLogger(__name__)

TRANSIENT_ERRORS = (psycopg.OperationalError, PoolTimeout, TimeoutError, ConnectionError)


class AgentState(MessagesState):
    status: str  # active | awaiting_approval | escalated | blocked | completed
    steps: int  # LLM calls in the current turn (bounded by MAX_AGENT_STEPS)
    flags: list[str]  # guardrail / policy events for the current turn
    approved_calls: dict[str, str]  # tool_call_id -> reviewer username
    pending_approval_id: int | None


MODEL_FIXABLE_ERRORS = (
    ToolInvocationError,
    ToolException,
    PermissionError,
    ValueError,
    LookupError,
)


def handle_tool_error(e: Exception) -> str:
    """Business / validation errors go back to the model so it can correct itself.
    Infrastructure errors (DB outage, timeouts) propagate so the node's RetryPolicy retries them."""
    if isinstance(e, TRANSIENT_ERRORS) or not isinstance(e, MODEL_FIXABLE_ERRORS):
        raise e
    return f"Error: {e}"


def build_graph(
    checkpointer=None, models: dict[str, Runnable] | None = None, canary: str | None = None
):
    settings = get_settings()
    canary = canary or new_canary()
    if models is None:
        from app.llm import build_tool_model

        models = {
            "customer": build_tool_model(tools_for_role("customer")),
            "staff": build_tool_model(ALL_TOOLS),
        }

    # ------------------------------------------------------------------ nodes
    async def guard_input(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        check = check_input(text_of(last), settings.max_input_chars)
        if not check.allowed:
            return {
                "messages": [AIMessage(content=check.refusal)],
                "status": "blocked",
                "flags": check.flags,
            }
        update: dict[str, Any] = {"flags": check.flags, "status": "active", "steps": 0}
        if check.text != text_of(last):
            update["messages"] = [HumanMessage(content=check.text, id=last.id)]
        return update

    async def agent(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        user = user_from_config(config)
        steps = state.get("steps", 0)
        if steps >= settings.max_agent_steps:
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "I wasn't able to finish this automatically. Please try rephrasing, or ask me to "
                            "connect you with a human specialist."
                        )
                    )
                ],
                "flags": state.get("flags", []) + ["step_limit_reached"],
            }
        model = models["staff" if user.is_staff else "customer"]
        prompt = [SystemMessage(content=system_prompt(user, canary))] + trim_history(
            state["messages"]
        )
        response = await model.ainvoke(prompt, config)
        return {"messages": [response], "steps": steps + 1}

    def route_after_agent(
        state: AgentState,
    ) -> Literal["guard_output", "request_approval", "tools"]:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        if not calls or state.get("steps", 0) > settings.max_agent_steps:
            return "guard_output"
        return "request_approval" if gated_calls(calls) else "tools"

    async def request_approval(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Persist the approval request (separate node, so it is NOT re-run when the graph resumes)."""
        user = user_from_config(config)
        last = state["messages"][-1]
        gated = gated_calls(last.tool_calls)
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute(
                    """INSERT INTO approvals (thread_id, tool_calls, risk_reasons, requested_by)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                    (
                        thread_id_from_config(config),
                        Jsonb(
                            [
                                {"id": c["id"], "name": c["name"], "args": c["args"]}
                                for c, _ in gated
                            ]
                        ),
                        Jsonb([r for _, r in gated]),
                        user.username,
                    ),
                )
            ).fetchone()
        return {"pending_approval_id": row["id"], "status": "awaiting_approval"}

    async def await_approval(state: AgentState) -> Command[Literal["tools", "agent"]]:
        last = state["messages"][-1]
        gated = gated_calls(last.tool_calls)
        decision = interrupt(
            {
                "approval_id": state.get("pending_approval_id"),
                "tool_calls": [
                    {"id": c["id"], "name": c["name"], "args": c["args"]} for c, _ in gated
                ],
                "reasons": [r for _, r in gated],
            }
        )
        reviewer = decision.get("reviewer", "unknown")
        if decision.get("decision") == "approve":
            approved = dict(state.get("approved_calls") or {})
            approved.update({c["id"]: reviewer for c, _ in gated})
            return Command(
                goto="tools",
                update={
                    "approved_calls": approved,
                    "status": "active",
                    "pending_approval_id": None,
                    "flags": state.get("flags", []) + ["approval_granted"],
                },
            )
        gated_ids = {c["id"] for c, _ in gated}
        note = decision.get("note") or "no reason given"
        rejections = [
            ToolMessage(
                content=(
                    f"REJECTED by human reviewer {reviewer}: {note}. The action was NOT performed. "
                    "Do not retry it; explain this to the user and offer alternatives."
                    if c["id"] in gated_ids
                    else "NOT EXECUTED: another action in the same step was rejected by a reviewer."
                ),
                tool_call_id=c["id"],
                name=c["name"],
                status="error",
            )
            for c in last.tool_calls
        ]
        return Command(
            goto="agent",
            update={
                "messages": rejections,
                "status": "active",
                "pending_approval_id": None,
                "flags": state.get("flags", []) + ["approval_rejected"],
            },
        )

    async def guard_output(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        text = text_of(last) if isinstance(last, AIMessage) else ""
        update: dict[str, Any] = {
            "status": "escalated" if state.get("status") == "escalated" else "completed",
        }
        if not text.strip():
            update["messages"] = [
                AIMessage(content="Sorry, I couldn't produce an answer. Could you rephrase?")
            ]
            update["flags"] = state.get("flags", []) + ["empty_model_output"]
            return update
        check = check_output(text, canary)
        if check.flags:
            update["flags"] = state.get("flags", []) + check.flags
        if check.text != text:
            update["messages"] = [AIMessage(content=check.text, id=last.id)]
        return update

    # ------------------------------------------------------------------ wiring
    builder = StateGraph(AgentState)
    builder.add_node("guard_input", guard_input)
    builder.add_node("agent", agent, retry_policy=RetryPolicy(max_attempts=2))
    builder.add_node(
        "request_approval",
        request_approval,
        retry_policy=RetryPolicy(max_attempts=3, retry_on=TRANSIENT_ERRORS),
    )
    builder.add_node("await_approval", await_approval, destinations=("tools", "agent"))
    builder.add_node(
        "tools",
        ToolNode(ALL_TOOLS, handle_tool_errors=handle_tool_error),
        retry_policy=RetryPolicy(max_attempts=3, initial_interval=0.5, retry_on=TRANSIENT_ERRORS),
    )
    builder.add_node("guard_output", guard_output)

    builder.add_edge(START, "guard_input")
    builder.add_conditional_edges(
        "guard_input", lambda s: END if s.get("status") == "blocked" else "agent", ["agent", END]
    )
    builder.add_conditional_edges(
        "agent", route_after_agent, ["guard_output", "request_approval", "tools"]
    )
    builder.add_edge("request_approval", "await_approval")
    builder.add_edge("tools", "agent")
    builder.add_edge("guard_output", END)

    graph = builder.compile(checkpointer=checkpointer)
    graph.canary = canary  # exposed for tests / leak checks
    return graph
