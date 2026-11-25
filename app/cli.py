"""Command-line entry point:  ops-agent <init-db|ingest-kb|chat|serve|eval>"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from app.aio import run


async def _init_db(reset: bool, reindex: bool) -> None:
    from app.bootstrap import init_database
    from app.db import close_pools, open_pools

    await open_pools()
    try:
        result = await init_database(reset=reset, reindex_kb=reindex)
        # LangGraph checkpoint tables
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        from app.db import get_pool

        await AsyncPostgresSaver(get_pool()).setup()
        print(json.dumps(result))
    finally:
        await close_pools()


async def _chat(username: str) -> None:
    """Interactive terminal chat - handy for demos without the API."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from app.agent.context import UserContext
    from app.agent.graph import build_graph
    from app.agent.service import AgentService
    from app.db import close_pools, get_pool, open_pools

    await open_pools()
    try:
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT id, username, role, customer_id FROM app_users WHERE username = %s",
                    (username,),
                )
            ).fetchone()
        if not row:
            raise SystemExit(f"Unknown user '{username}'. Try: alice, carla, david, sam, admin")
        user = UserContext(**row)
        reviewer = UserContext(id=0, username="admin", role="admin")
        service = AgentService(build_graph(checkpointer=AsyncPostgresSaver(get_pool())))
        thread_id = None
        print(f"Chatting as {user.username} ({user.role}). Ctrl+C to exit.\n")
        while True:
            try:
                text = (await asyncio.to_thread(input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not text:
                continue
            result = await service.run_turn(user, text, thread_id)
            thread_id = result.thread_id
            print(f"\nagent [{result.status}]> {result.reply}")
            while result.status == "awaiting_approval":
                prompt = "\n[approval] approve this action as admin? (y/n) "
                ans = (await asyncio.to_thread(input, prompt)).strip().lower()
                result = await service.decide(
                    reviewer,
                    result.approval["approval_id"],
                    ans.startswith("y"),
                    None if ans.startswith("y") else "Rejected in terminal demo",
                )
                print(f"\nagent [{result.status}]> {result.reply}")
            tools = ", ".join(t["name"] for t in result.metrics["tool_calls"]) or "-"
            print(
                f"   (tools: {tools} | {result.metrics['latency_ms']} ms | "
                f"{result.metrics['input_tokens']}+{result.metrics['output_tokens']} tokens)\n"
            )
    finally:
        await close_pools()


def _serve(host: str, port: int) -> None:
    import uvicorn

    from app.aio import loop_factory

    config = uvicorn.Config("app.api.main:app", host=host, port=port, loop="none")
    server = uvicorn.Server(config)
    factory = loop_factory()
    if factory is None:
        asyncio.run(server.serve())
    else:
        asyncio.run(server.serve(), loop_factory=factory)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "psycopg.pool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(prog="ops-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init-db", help="create schema, roles, demo data and KB index (idempotent)")
    p.add_argument("--reset", action="store_true", help="DROP all tables first")
    p.add_argument("--reindex-kb", action="store_true")
    sub.add_parser("ingest-kb", help="re-index data/kb/*.md")
    p = sub.add_parser("chat", help="interactive terminal chat")
    p.add_argument("--user", default="alice")
    p = sub.add_parser("serve", help="run the API (Windows-safe event loop)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p = sub.add_parser("eval", help="run the evaluation dataset")
    p.add_argument("--dataset", default="evals/datasets/support_v1.yaml")
    p.add_argument("--filter", default=None, help="only run cases whose id contains this text")
    p.add_argument(
        "--delay", type=float, default=None, help="seconds between cases (free-tier rate limits)"
    )
    p.add_argument("--judge", action="store_true", help="also grade replies with an LLM judge")
    p.add_argument("--no-reset", action="store_true", help="do not reset demo data first")
    p.add_argument("--tags", nargs="*", default=None, help="only run cases with any of these tags")
    p.add_argument(
        "--min-pass-rate", type=float, default=None, help="exit non-zero below this rate (CI)"
    )

    args = parser.parse_args()
    if args.cmd == "init-db":
        run(_init_db(args.reset, args.reindex_kb))
    elif args.cmd == "ingest-kb":
        run(_init_db(False, True))
    elif args.cmd == "chat":
        run(_chat(args.user))
    elif args.cmd == "serve":
        _serve(args.host, args.port)
    elif args.cmd == "eval":
        from evals.runner import run_evals

        run(
            run_evals(
                dataset=args.dataset,
                case_filter=args.filter,
                delay=args.delay,
                judge=args.judge,
                reset=not args.no_reset,
                tags=args.tags,
                min_pass_rate=args.min_pass_rate,
            )
        )


if __name__ == "__main__":
    main()
