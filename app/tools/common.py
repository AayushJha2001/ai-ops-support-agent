from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _default(o: Any):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, datetime):
        return o.strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(o, date):
        return o.isoformat()
    return str(o)


def to_json(data: Any) -> str:
    """Compact, LLM-friendly JSON."""
    return json.dumps(data, default=_default, ensure_ascii=False)
