import pytest

from app.tools.sql_guard import UnsafeSQLError, validate_sql

ALLOWED = {"customers", "subscriptions", "invoices", "tickets", "plans"}


def ok(sql: str, limit: int = 50) -> str:
    return validate_sql(sql, ALLOWED, limit)


def test_select_gets_row_cap():
    out = ok("SELECT status, count(*) FROM tickets GROUP BY status")
    assert out.upper().endswith("LIMIT 50")


def test_existing_small_limit_kept_large_limit_capped():
    assert "LIMIT 5" in ok("SELECT id FROM customers LIMIT 5")
    assert "LIMIT 50" in ok("SELECT id FROM customers LIMIT 100000")


def test_joins_and_ctes_allowed():
    ok("""WITH overdue AS (SELECT customer_id FROM invoices WHERE status = 'open')
          SELECT c.name FROM customers c JOIN overdue o ON o.customer_id = c.id""")


def test_union_is_wrapped_with_limit():
    out = ok("SELECT id FROM customers UNION SELECT customer_id FROM invoices")
    assert out.startswith("SELECT * FROM (") and out.endswith("LIMIT 50")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM tickets WHERE status = 'closed'",
        "UPDATE invoices SET status = 'paid'",
        "INSERT INTO tickets (subject) VALUES ('x')",
        "DROP TABLE customers",
        "SELECT 1; DROP TABLE customers",
        "SELECT * FROM app_users",
        "SELECT * FROM pg_catalog.pg_roles",
        "SELECT * FROM information_schema.tables",
        "SELECT pg_sleep(10)",
        "SELECT * INTO newtable FROM customers",
        "SELECT * FROM customers FOR UPDATE",
        "SELECT set_config('role', 'ops', false)",
        "COPY customers TO '/tmp/x'",
        "this is not sql at all",
        "",
    ],
)
def test_rejected(sql):
    with pytest.raises(UnsafeSQLError):
        ok(sql)
