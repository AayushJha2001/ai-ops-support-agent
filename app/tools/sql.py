from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import ToolException, tool
from psycopg import errors as pg_errors
from psycopg import sql as pg_sql
from pydantic import BaseModel, Field

from app.agent.context import require_staff, user_from_config
from app.config import get_settings
from app.db import audit, get_readonly_pool
from app.tools.common import to_json
from app.tools.sql_guard import UnsafeSQLError, validate_sql

SCHEMA_HINT = """Tables (PostgreSQL):
plans(id, name, monthly_price_usd, seats, support_tier)
customers(id, external_id, name, email, company, country, status[active|suspended|churned], created_at)
subscriptions(id, customer_id, plan_id, billing_cycle[monthly|annual], status[trialing|active|past_due|canceled], started_at, renews_at, canceled_at)
invoices(id, customer_id, number, amount_usd, refunded_amount_usd, status[open|paid|void|refunded|partially_refunded], issued_at, due_at, paid_at)
tickets(id, customer_id, subject, description, category, priority[low|normal|high|urgent], status[open|pending|escalated|resolved|closed], assignee, created_by, created_at, updated_at)
ticket_events(id, ticket_id, actor, event_type, details, created_at)
refunds(id, invoice_id, customer_id, amount_usd, reason, approved_by, created_at)"""


class SQLQueryInput(BaseModel):
    query: str = Field(
        min_length=10,
        max_length=2000,
        description="A single read-only PostgreSQL SELECT statement.",
    )
    purpose: str = Field(
        min_length=5, max_length=200, description="Why this query is needed (logged for audit)."
    )


@tool("run_sql_query", args_schema=SQLQueryInput)
async def run_sql_query(query: str, purpose: str, *, config: RunnableConfig) -> str:
    """Run a read-only SQL SELECT against the support database for aggregate or ad-hoc
    operational questions (counts, revenue, trends, lists). Staff only. Results are capped.
    """
    user = user_from_config(config)
    require_staff(user, "run SQL queries")
    s = get_settings()
    try:
        safe_sql = validate_sql(query, set(s.allowed_sql_tables), s.sql_row_limit)
    except UnsafeSQLError as e:
        raise ToolException(f"Query rejected by SQL guard: {e}") from e

    try:
        async with get_readonly_pool().connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute(
                    pg_sql.SQL("SET LOCAL statement_timeout = {}").format(
                        pg_sql.Literal(f"{s.sql_timeout_ms}ms")
                    )
                )
                cur = await conn.execute(safe_sql)
                rows = await cur.fetchall()
    except (
        pg_errors.SyntaxError,
        pg_errors.UndefinedColumn,
        pg_errors.UndefinedTable,
        pg_errors.UndefinedFunction,
        pg_errors.DatatypeMismatch,
        pg_errors.GroupingError,
        pg_errors.InsufficientPrivilege,
        pg_errors.QueryCanceled,
        pg_errors.InvalidTextRepresentation,
        pg_errors.AmbiguousColumn,
    ) as e:
        raise ToolException(f"SQL error: {str(e).splitlines()[0]}\n\n{SCHEMA_HINT}") from e

    await audit(
        user.username, "sql_query", None, {"purpose": purpose, "sql": safe_sql, "rows": len(rows)}
    )
    return to_json(
        {"sql_executed": safe_sql, "row_count": len(rows), "row_cap": s.sql_row_limit, "rows": rows}
    )


run_sql_query.description += "\n\n" + SCHEMA_HINT
