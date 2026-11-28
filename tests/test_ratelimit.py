from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ratelimit import RateLimitMiddleware


def make_client(per_hour: int) -> TestClient:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, per_hour=per_hour)

    @app.post("/v1/chat")
    def chat():
        return {"ok": True}

    @app.get("/v1/threads")
    def threads():
        return []

    return TestClient(app)


def test_limits_llm_endpoints_per_client():
    client = make_client(per_hour=2)
    headers = {"X-Forwarded-For": "1.2.3.4"}
    assert client.post("/v1/chat", headers=headers).status_code == 200
    assert client.post("/v1/chat", headers=headers).status_code == 200
    blocked = client.post("/v1/chat", headers=headers)
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    # another visitor is unaffected
    assert client.post("/v1/chat", headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200


def test_reads_are_not_limited_and_zero_disables():
    client = make_client(per_hour=1)
    for _ in range(3):
        assert client.get("/v1/threads").status_code == 200
    unlimited = make_client(per_hour=0)
    for _ in range(5):
        assert unlimited.post("/v1/chat").status_code == 200
