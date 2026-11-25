"""HTTP-level tests (offline fake LLM, test database)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.api.main import create_app

pytestmark = pytest.mark.integration

ALICE = {"X-API-Key": "demo-alice-key"}
DAVID = {"X-API-Key": "demo-david-key"}
SAM = {"X-API-Key": "demo-agent-key"}


@pytest.fixture(scope="module")
async def client(database):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    # the lifespan closed the shared pools; reopen them for any later tests
    from app.db import open_pools

    await open_pools()


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_auth_required(client):
    assert (await client.post("/v1/chat", json={"message": "hi"})).status_code == 401
    assert (
        await client.post("/v1/chat", json={"message": "hi"}, headers={"X-API-Key": "nope"})
    ).status_code == 401


async def test_chat_and_history(client):
    r = await client.post(
        "/v1/chat", json={"message": "How long is data kept after cancellation?"}, headers=ALICE
    )
    body = r.json()
    assert r.status_code == 200 and body["status"] == "completed"
    assert body["metrics"]["tool_calls"][0]["name"] == "search_knowledge_base"

    h = await client.get(f"/v1/threads/{body['thread_id']}", headers=ALICE)
    assert [m["role"] for m in h.json()["messages"]][:2] == ["human", "ai"]
    # other users cannot read the thread
    assert (await client.get(f"/v1/threads/{body['thread_id']}", headers=DAVID)).status_code == 404


async def test_approval_flow_over_http(client):
    r = await client.post(
        "/v1/chat", json={"message": "Please refund $19 for INV-1004-03"}, headers=DAVID
    )
    body = r.json()
    assert body["status"] == "awaiting_approval"
    approval_id = body["approval"]["approval_id"]

    # thread is locked while waiting
    busy = await client.post(
        "/v1/chat", json={"message": "hello?", "thread_id": body["thread_id"]}, headers=DAVID
    )
    assert busy.status_code == 409
    # customers cannot see or decide approvals
    assert (await client.get("/v1/approvals", headers=DAVID)).status_code == 403

    queue = (await client.get("/v1/approvals", headers=SAM)).json()
    assert any(a["id"] == approval_id for a in queue)

    decided = await client.post(
        f"/v1/approvals/{approval_id}/decision", json={"decision": "approve"}, headers=SAM
    )
    assert decided.status_code == 200 and decided.json()["status"] == "completed"
    again = await client.post(
        f"/v1/approvals/{approval_id}/decision", json={"decision": "approve"}, headers=SAM
    )
    assert again.status_code == 409


async def test_ticket_scoping(client):
    mine = (await client.get("/v1/tickets", headers=ALICE)).json()
    assert mine and all(t["customer_id"] == mine[0]["customer_id"] for t in mine)
    erin_ticket = (
        await client.get("/v1/tickets", params={"status": "escalated"}, headers=SAM)
    ).json()
    other = next(t for t in erin_ticket if t["customer_id"] != mine[0]["customer_id"])
    assert (await client.get(f"/v1/tickets/{other['id']}", headers=ALICE)).status_code == 404


async def test_metrics(client):
    r = await client.get("/v1/metrics/summary", headers=SAM)
    data = r.json()
    assert r.status_code == 200 and data["runs"]["runs"] >= 1
    assert (await client.get("/v1/metrics/summary", headers=ALICE)).status_code == 403


async def test_sse_stream(client):
    events = []
    async with client.stream(
        "POST", "/v1/chat/stream", json={"message": "What plans include SSO?"}, headers=ALICE
    ) as r:
        assert r.status_code == 200
        current = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                current = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and current:
                events.append((current, json.loads(line.split(":", 1)[1])))
    names = [e for e, _ in events]
    assert names[0] == "thread" and names[-1] == "done"
    assert "tool_calls" in names and "tool_result" in names
    assert events[-1][1]["status"] == "completed"
