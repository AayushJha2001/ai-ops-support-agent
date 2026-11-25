"""Authenticated-user context that flows through the graph via RunnableConfig["configurable"]."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import ToolException

STAFF_ROLES = {"support_agent", "admin"}


@dataclass(frozen=True)
class UserContext:
    id: int
    username: str
    role: str
    customer_id: int | None = None

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def user_from_config(config: RunnableConfig | None) -> UserContext:
    user = ((config or {}).get("configurable") or {}).get("user")
    if not user:
        raise ToolException("No authenticated user in context.")
    return UserContext(**user) if isinstance(user, dict) else user


def thread_id_from_config(config: RunnableConfig | None) -> str | None:
    return ((config or {}).get("configurable") or {}).get("thread_id")


def require_staff(user: UserContext, action: str) -> None:
    if not user.is_staff:
        raise PermissionError(f"Not authorized: only support staff can {action}.")


def enforce_customer_scope(user: UserContext, customer_id: int) -> None:
    """Customers may only touch their own records. Staff may touch any."""
    if not user.is_staff and user.customer_id != customer_id:
        raise PermissionError("Not authorized: customers can only access their own account.")
