"""API-key authentication + role-based access control."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.agent.context import UserContext
from app.bootstrap import hash_api_key
from app.db import get_pool

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def current_user(api_key: str | None = Security(api_key_header)) -> UserContext:
    if not api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header")
    async with get_pool().connection() as conn:
        row = await (
            await conn.execute(
                "SELECT id, username, role, customer_id FROM app_users WHERE api_key_hash = %s",
                (hash_api_key(api_key),),
            )
        ).fetchone()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return UserContext(**row)


async def staff_user(user: UserContext = Depends(current_user)) -> UserContext:
    if not user.is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support staff only")
    return user
