"""Static validation for model-generated SQL (layer 1 of 3; layers 2-3 are a read-only
transaction and a SELECT-only database role)."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

DENIED_FUNCTIONS = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
    "set_config",
    "current_setting",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "query_to_xml",
}

FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Grant,
    exp.Copy,
    exp.TruncateTable,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)


class UnsafeSQLError(ValueError):
    pass


def validate_sql(sql: str, allowed_tables: set[str], row_limit: int) -> str:
    """Return a safe, LIMIT-ed version of `sql`, or raise UnsafeSQLError explaining why not."""
    if not sql or not sql.strip():
        raise UnsafeSQLError("Empty query.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as e:
        raise UnsafeSQLError(f"Could not parse SQL: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one statement is allowed.")
    stmt = statements[0]

    if not isinstance(stmt, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise UnsafeSQLError("Only SELECT queries are allowed.")
    for node_type in FORBIDDEN_NODES:
        if stmt.find(node_type) is not None:
            raise UnsafeSQLError(f"Statement type not allowed: {node_type.__name__}.")

    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if not name:  # e.g. table-valued function
            raise UnsafeSQLError("Table functions are not allowed.")
        if name in cte_names and not table.db:
            continue
        if table.db and table.db.lower() != "public":
            raise UnsafeSQLError(f"Schema '{table.db}' is not allowed.")
        if name not in allowed_tables:
            raise UnsafeSQLError(
                f"Table '{name}' is not allowed. Allowed tables: {', '.join(sorted(allowed_tables))}."
            )

    for func in stmt.find_all(exp.Func):
        fname = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
        if fname in DENIED_FUNCTIONS or fname.startswith("pg_"):
            raise UnsafeSQLError(f"Function '{fname}' is not allowed.")

    # Enforce a row cap.
    if isinstance(stmt, exp.Select):
        limit = stmt.args.get("limit")
        current = None
        if limit is not None:
            try:
                current = int(limit.expression.name) if limit.expression else None
            except (TypeError, ValueError):
                current = None
        if current is None or current > row_limit:
            stmt = stmt.limit(row_limit)
        return stmt.sql(dialect="postgres")
    return f"SELECT * FROM ({stmt.sql(dialect='postgres')}) AS q LIMIT {row_limit}"
