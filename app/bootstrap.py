"""Database bootstrap: schema, least-privilege role, deterministic demo data, KB index."""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from psycopg import sql

from app.config import get_settings
from app.db import get_pool

log = logging.getLogger(__name__)


def _schema_path() -> Path:
    # Working directory first (Docker / repo root), then the source tree (editable installs).
    for candidate in (
        Path("db/schema.sql"),
        Path(__file__).resolve().parent.parent / "db" / "schema.sql",
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("db/schema.sql not found - run from the project root")


APP_TABLES = [
    "agent_runs",
    "audit_log",
    "escalations",
    "approvals",
    "threads",
    "app_users",
    "refunds",
    "ticket_events",
    "tickets",
    "invoices",
    "subscriptions",
    "customers",
    "plans",
    "kb_chunks",
    "kb_documents",
]
CHECKPOINT_TABLES = [
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
]

# Demo credentials (development only - documented in the README).
DEMO_USERS = [
    # username, role, customer external id, api key
    ("alice", "customer", "CUS-1001", "demo-alice-key"),
    ("carla", "customer", "CUS-1003", "demo-carla-key"),
    ("david", "customer", "CUS-1004", "demo-david-key"),
    ("sam", "support_agent", None, "demo-agent-key"),
    ("admin", "admin", None, "demo-admin-key"),
]

PLANS = [
    ("starter", "Starter", 19, 1, "community"),
    ("team", "Team", 79, 5, "standard"),
    ("business", "Business", 249, 25, "priority"),
    ("enterprise", "Enterprise", 999, 100, "premium"),
]
PLAN_PRICE = {p[0]: p[2] for p in PLANS}


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def reset_database() -> None:
    async with get_pool().connection() as conn:
        for table in CHECKPOINT_TABLES + APP_TABLES:
            await conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table))
            )
    log.warning("database reset: all application and checkpoint tables dropped")


async def apply_schema() -> None:
    async with get_pool().connection() as conn:
        await conn.execute(_schema_path().read_text(encoding="utf-8"), prepare=False)


async def ensure_readonly_role() -> None:
    """Create the SELECT-only role used by the SQL tool (defence in depth behind the SQL guard)."""
    s = get_settings()
    role = sql.Identifier(s.readonly_db_user)
    async with get_pool().connection() as conn:
        exists = await (
            await conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (s.readonly_db_user,))
        ).fetchone()
        if exists:
            await conn.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    role, sql.Literal(s.readonly_db_password)
                )
            )
        else:
            await conn.execute(
                sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                    role, sql.Literal(s.readonly_db_password)
                )
            )
        dbname = (await (await conn.execute("SELECT current_database() AS d")).fetchone())["d"]
        await conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), role)
        )
        await conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        await conn.execute(
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role)
        )
        tables = sql.SQL(", ").join(sql.Identifier(t) for t in s.allowed_sql_tables)
        await conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(tables, role))
        await conn.execute(
            sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(
                role, sql.Literal(f"{s.sql_timeout_ms}ms")
            )
        )


async def seed_demo_data() -> bool:
    """Insert deterministic demo data. Returns False if data already exists."""
    pool = get_pool()
    async with pool.connection() as conn:
        if (await (await conn.execute("SELECT count(*) AS n FROM customers")).fetchone())["n"]:
            return False

        now = datetime.now(UTC).replace(microsecond=0)
        d = lambda days: now - timedelta(days=days)  # noqa: E731

        async with conn.transaction():
            for p in PLANS:
                await conn.execute(
                    "INSERT INTO plans (id, name, monthly_price_usd, seats, support_tier) VALUES (%s,%s,%s,%s,%s)",
                    p,
                )

            async def add_customer(
                ext, name, email, company, country, status="active", created_days=400
            ):
                row = await (
                    await conn.execute(
                        """INSERT INTO customers (external_id, name, email, company, country, status, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (ext, name, email, company, country, status, d(created_days)),
                    )
                ).fetchone()
                return row["id"]

            async def add_sub(cid, plan, cycle, status, started_days, canceled_days=None):
                started = d(started_days)
                period = timedelta(days=365 if cycle == "annual" else 30)
                renews = (
                    None
                    if status == "canceled"
                    else started + period * (1 + (now - started) // period)
                )
                await conn.execute(
                    """INSERT INTO subscriptions (customer_id, plan_id, billing_cycle, status, started_at, renews_at, canceled_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        cid,
                        plan,
                        cycle,
                        status,
                        started,
                        renews,
                        d(canceled_days) if canceled_days is not None else None,
                    ),
                )

            async def add_invoice(cid, number, amount, status, issued_days, refunded=0):
                issued = d(issued_days)
                await conn.execute(
                    """INSERT INTO invoices (customer_id, number, amount_usd, refunded_amount_usd, status, issued_at, due_at, paid_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        cid,
                        number,
                        amount,
                        refunded,
                        status,
                        issued,
                        issued + timedelta(days=7),
                        issued + timedelta(days=1)
                        if status in ("paid", "refunded", "partially_refunded")
                        else None,
                    ),
                )

            async def add_ticket(
                cid, subject, desc, category, priority, status, days, assignee=None
            ):
                row = await (
                    await conn.execute(
                        """INSERT INTO tickets (customer_id, subject, description, category, priority, status, assignee,
                                            created_by, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'seed',%s,%s) RETURNING id""",
                        (
                            cid,
                            subject,
                            desc,
                            category,
                            priority,
                            status,
                            assignee,
                            d(days),
                            d(max(days - 1, 0)),
                        ),
                    )
                ).fetchone()
                await conn.execute(
                    "INSERT INTO ticket_events (ticket_id, actor, event_type, created_at) VALUES (%s,'seed','created',%s)",
                    (row["id"], d(days)),
                )

            # ---- anchor customers (referenced by the eval dataset) --------------------------
            alice = await add_customer(
                "CUS-1001", "Alice Chen", "alice@brightpixel.example", "Bright Pixel Studio", "US"
            )
            await add_sub(alice, "team", "monthly", "active", 200)
            await add_invoice(alice, "INV-1001-07", 79, "paid", 5)
            await add_invoice(alice, "INV-1001-06", 79, "paid", 35)
            await add_ticket(
                alice,
                "Cannot invite a new team member",
                "Invite emails are not arriving for our new designer.",
                "account",
                "normal",
                "open",
                2,
            )

            bob = await add_customer(
                "CUS-1002", "Bob Martins", "bob@northwind-labs.example", "Northwind Labs", "UK"
            )
            await add_sub(bob, "business", "annual", "active", 10)
            await add_invoice(bob, "INV-1002-01", 2490, "paid", 10)
            await add_ticket(
                bob,
                "SSO setup question",
                "How do we configure SAML with Okta?",
                "technical",
                "low",
                "resolved",
                8,
                "sam",
            )

            carla = await add_customer(
                "CUS-1003", "Carla Gomez", "carla@lumen-retail.example", "Lumen Retail", "ES"
            )
            await add_sub(carla, "business", "monthly", "past_due", 120)
            await add_invoice(carla, "INV-1003-04", 249, "open", 16)
            await add_invoice(carla, "INV-1003-03", 249, "paid", 46)

            david = await add_customer(
                "CUS-1004", "David Okafor", "david@okafor-dev.example", "Okafor Dev", "NG"
            )
            await add_sub(david, "starter", "monthly", "active", 90)
            await add_invoice(david, "INV-1004-03", 19, "paid", 3)
            await add_invoice(david, "INV-1004-04", 19, "paid", 3)  # duplicate charge
            await add_invoice(david, "INV-1004-02", 19, "paid", 33)

            erin = await add_customer(
                "CUS-1005", "Erin Walsh", "erin@helix-bio.example", "Helix Bio", "IE"
            )
            await add_sub(erin, "enterprise", "annual", "active", 20)
            await add_invoice(erin, "INV-1005-01", 9990, "paid", 20)
            await add_ticket(
                erin,
                "Production API returning 503",
                "Our EU workloads fail since 09:00 UTC.",
                "technical",
                "urgent",
                "escalated",
                1,
                "sam",
            )

            farid = await add_customer(
                "CUS-1006", "Farid Haddad", "farid@quietdesk.example", "QuietDesk", "AE", "churned"
            )
            await add_sub(farid, "team", "monthly", "canceled", 300, 60)
            await add_invoice(farid, "INV-1006-09", 79, "refunded", 62, refunded=79)

            # ---- generated long tail (deterministic) ------------------------------------------
            rng = random.Random(42)
            first = [
                "Maya",
                "Liam",
                "Priya",
                "Noah",
                "Sofia",
                "Arjun",
                "Emma",
                "Kenji",
                "Zara",
                "Lucas",
                "Ines",
                "Omar",
                "Hana",
                "Mateo",
                "Aisha",
                "Ravi",
                "Chloe",
                "Tomas",
            ]
            last = [
                "Patel",
                "Smith",
                "Kim",
                "Rossi",
                "Nguyen",
                "Silva",
                "Mehta",
                "Kowalski",
                "Haddad",
                "Fischer",
                "Sato",
                "Ali",
                "Moreau",
                "Jensen",
            ]
            nouns = [
                "Labs",
                "Systems",
                "Studio",
                "Analytics",
                "Health",
                "Logistics",
                "Media",
                "Works",
            ]
            words = [
                "Blue",
                "Quantum",
                "Cedar",
                "Atlas",
                "Nova",
                "Summit",
                "Harbor",
                "Pixel",
                "Orbit",
                "Maple",
            ]
            countries = ["US", "IN", "DE", "UK", "FR", "BR", "JP", "CA", "AU", "NL"]
            categories = [
                (
                    "billing",
                    "Question about my latest invoice",
                    "The amount looks higher than expected.",
                ),
                (
                    "technical",
                    "API returns 429 errors",
                    "We hit rate limits during nightly batch jobs.",
                ),
                ("account", "Need to transfer ownership", "Our previous owner left the company."),
                ("technical", "Export job stuck", "The data export has been processing for hours."),
                ("billing", "Update VAT number", "Please add our VAT number to future invoices."),
                ("account", "2FA device lost", "I changed phones and lost my authenticator."),
            ]
            for i in range(54):
                n = 1007 + i
                fn, ln = rng.choice(first), rng.choice(last)
                company = f"{rng.choice(words)} {rng.choice(nouns)}"
                status = rng.choices(["active", "suspended", "churned"], [0.85, 0.05, 0.10])[0]
                cid = await add_customer(
                    f"CUS-{n}",
                    f"{fn} {ln}",
                    f"{fn.lower()}.{ln.lower()}{n}@example.com",
                    company,
                    rng.choice(countries),
                    status,
                    rng.randint(60, 900),
                )
                plan = rng.choices(
                    ["starter", "team", "business", "enterprise"], [0.35, 0.35, 0.22, 0.08]
                )[0]
                cycle = rng.choice(["monthly", "monthly", "annual"])
                sub_status = {
                    "active": rng.choices(["active", "past_due", "trialing"], [0.85, 0.1, 0.05])[0],
                    "suspended": "past_due",
                    "churned": "canceled",
                }[status]
                await add_sub(
                    cid,
                    plan,
                    cycle,
                    sub_status,
                    rng.randint(30, 700),
                    rng.randint(1, 25) if sub_status == "canceled" else None,
                )
                amount = PLAN_PRICE[plan] * (10 if cycle == "annual" else 1)
                for k in range(rng.randint(1, 4)):
                    inv_status = "open" if (k == 0 and sub_status == "past_due") else "paid"
                    await add_invoice(
                        cid,
                        f"INV-{n}-{k + 1:02d}",
                        amount,
                        inv_status,
                        5 + 30 * k + rng.randint(0, 4),
                    )
                for _ in range(rng.choices([0, 1, 2], [0.4, 0.4, 0.2])[0]):
                    cat, subj, desc = rng.choice(categories)
                    await add_ticket(
                        cid,
                        subj,
                        desc,
                        cat,
                        rng.choice(["low", "normal", "normal", "high"]),
                        rng.choice(["open", "pending", "resolved", "closed"]),
                        rng.randint(1, 60),
                        rng.choice([None, "sam", "priya"]),
                    )

            # ---- demo users ---------------------------------------------------------------------
            for username, role, ext, key in DEMO_USERS:
                cust_id = None
                if ext:
                    cust_id = (
                        await (
                            await conn.execute(
                                "SELECT id FROM customers WHERE external_id = %s", (ext,)
                            )
                        ).fetchone()
                    )["id"]
                await conn.execute(
                    "INSERT INTO app_users (username, role, customer_id, api_key_hash) VALUES (%s,%s,%s,%s)",
                    (username, role, cust_id, hash_api_key(key)),
                )
    return True


async def init_database(reset: bool = False, reindex_kb: bool = False) -> dict:
    """Idempotent: safe to run on every container start."""
    from app.rag.ingest import ingest_kb

    if reset:
        await reset_database()
    await apply_schema()
    await ensure_readonly_role()
    seeded = await seed_demo_data()
    async with get_pool().connection() as conn:
        n_chunks = (await (await conn.execute("SELECT count(*) AS n FROM kb_chunks")).fetchone())[
            "n"
        ]
    indexed = 0
    if reindex_kb or n_chunks == 0:
        indexed = await ingest_kb()
    return {"seeded": seeded, "kb_chunks_indexed": indexed}
