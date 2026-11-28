"""FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.graph import build_graph
from app.agent.service import AgentService
from app.api.routes import router
from app.config import get_settings
from app.db import close_pools, get_pool, open_pools

log = logging.getLogger(__name__)


def create_app(graph_factory=None) -> FastAPI:
    """`graph_factory(checkpointer)` lets tests inject a graph with scripted models."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        await open_pools()
        checkpointer = AsyncPostgresSaver(get_pool())
        await checkpointer.setup()
        graph = (graph_factory or (lambda cp: build_graph(checkpointer=cp)))(checkpointer)
        app.state.agent_service = AgentService(graph)
        log.info(
            "agent ready (provider=%s, fallbacks=%s)", settings.llm_provider, settings.llm_fallbacks
        )
        yield
        await close_pools()

    app = FastAPI(
        title="AI Operations & Support Agent",
        version="0.1.0",
        description="LangGraph support agent with tools, human-in-the-loop approvals, guardrails and evals.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/health", tags=["ops"])
    async def health():
        async with get_pool().connection() as conn:
            await conn.execute("SELECT 1")
        s = get_settings()
        return {"status": "ok", "llm_provider": s.llm_provider, "fallbacks": s.fallback_providers}

    return app


app = create_app()
