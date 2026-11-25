from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    thread_id: str | None = Field(default=None, description="Omit to start a new conversation.")


class ToolCallMetric(BaseModel):
    name: str
    ok: bool
    ms: int
    args: dict[str, Any] | None = None
    error: str | None = None


class TurnMetrics(BaseModel):
    latency_ms: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    est_cost_usd: float
    models: list[str]
    tool_calls: list[ToolCallMetric]


class ChatResponse(BaseModel):
    thread_id: str
    status: Literal["completed", "awaiting_approval", "escalated", "blocked", "error", "active"]
    reply: str
    approval: dict[str, Any] | None = None
    flags: list[str] = []
    metrics: TurnMetrics


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)
