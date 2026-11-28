"""Tiny in-memory, per-client rate limit for the public demo.

It protects the free-tier LLM quota from being drained by anonymous visitors. It is per process
(fine for a single free instance); a multi-instance deployment would use Redis instead.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Only endpoints that spend LLM tokens are limited.
LIMITED_PREFIXES = ("/v1/chat", "/v1/approvals/")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_hour: int, window_s: int = 3600):
        super().__init__(app)
        self.per_hour = per_hour
        self.window_s = window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def client_id(request: Request) -> str:
        # Behind a proxy (Render, Fly...) the real client is the first X-Forwarded-For entry.
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        if (
            self.per_hour <= 0
            or request.method != "POST"
            or not request.url.path.startswith(LIMITED_PREFIXES)
        ):
            return await call_next(request)

        now = time.monotonic()
        q = self.hits[self.client_id(request)]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.per_hour:
            retry = int(self.window_s - (now - q[0])) + 1
            return JSONResponse(
                {
                    "detail": f"Demo rate limit reached ({self.per_hour} requests/hour). "
                    f"Try again in {retry // 60 + 1} min, or run it locally from GitHub."
                },
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        q.append(now)
        return await call_next(request)
