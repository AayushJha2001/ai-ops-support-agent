from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.rag.search import hybrid_search


class KBSearchInput(BaseModel):
    query: str = Field(
        min_length=3,
        max_length=300,
        description="A focused natural-language search query, e.g. 'refund window annual plan'.",
    )
    top_k: int = Field(default=4, ge=1, le=8, description="Number of passages to return.")


@tool("search_knowledge_base", args_schema=KBSearchInput)
async def search_knowledge_base(query: str, top_k: int = 4) -> str:
    """Search Acme Cloud's help-center articles (policies, pricing, billing, security, SLA, how-tos).
    Use this before answering any policy or product question. Cite sources as [slug#section]."""
    hits = await hybrid_search(query, top_k=top_k)
    if not hits:
        return "No relevant knowledge-base articles found."
    return "\n\n".join(
        f"[{h.citation}] {h.title} > {h.heading or 'Overview'}\n{h.content}" for h in hits
    )
