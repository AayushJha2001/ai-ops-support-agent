-- AI Operations & Support Agent schema (PostgreSQL 16+ with pgvector)
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------- business data
CREATE TABLE IF NOT EXISTS plans (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    monthly_price_usd   NUMERIC(10, 2) NOT NULL,
    seats               INT NOT NULL,
    support_tier        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id           SERIAL PRIMARY KEY,
    external_id  TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    email        TEXT UNIQUE NOT NULL,
    company      TEXT,
    country      TEXT,
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'churned')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id           SERIAL PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers(id),
    plan_id      TEXT NOT NULL REFERENCES plans(id),
    billing_cycle TEXT NOT NULL DEFAULT 'monthly' CHECK (billing_cycle IN ('monthly', 'annual')),
    status       TEXT NOT NULL CHECK (status IN ('trialing', 'active', 'past_due', 'canceled')),
    started_at   TIMESTAMPTZ NOT NULL,
    renews_at    TIMESTAMPTZ,
    canceled_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS invoices (
    id                   SERIAL PRIMARY KEY,
    customer_id          INT NOT NULL REFERENCES customers(id),
    number               TEXT UNIQUE NOT NULL,
    amount_usd           NUMERIC(10, 2) NOT NULL,
    refunded_amount_usd  NUMERIC(10, 2) NOT NULL DEFAULT 0,
    status               TEXT NOT NULL CHECK (status IN ('open', 'paid', 'void', 'refunded', 'partially_refunded')),
    issued_at            TIMESTAMPTZ NOT NULL,
    due_at               TIMESTAMPTZ NOT NULL,
    paid_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS tickets (
    id               SERIAL PRIMARY KEY,
    customer_id      INT NOT NULL REFERENCES customers(id),
    subject          TEXT NOT NULL,
    description      TEXT NOT NULL,
    category         TEXT NOT NULL,
    priority         TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'pending', 'escalated', 'resolved', 'closed')),
    assignee         TEXT,
    created_by       TEXT NOT NULL,
    idempotency_key  TEXT UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ticket_events (
    id          SERIAL PRIMARY KEY,
    ticket_id   INT NOT NULL REFERENCES tickets(id),
    actor       TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS refunds (
    id               SERIAL PRIMARY KEY,
    invoice_id       INT NOT NULL REFERENCES invoices(id),
    customer_id      INT NOT NULL REFERENCES customers(id),
    amount_usd       NUMERIC(10, 2) NOT NULL CHECK (amount_usd > 0),
    reason           TEXT NOT NULL,
    approved_by      TEXT NOT NULL,
    idempotency_key  TEXT UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- platform data
CREATE TABLE IF NOT EXISTS app_users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('customer', 'support_agent', 'admin')),
    customer_id   INT REFERENCES customers(id),
    api_key_hash  TEXT UNIQUE NOT NULL,
    CHECK (role <> 'customer' OR customer_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS threads (
    thread_id   TEXT PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES app_users(id),
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'awaiting_approval', 'escalated', 'blocked', 'completed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approvals (
    id            SERIAL PRIMARY KEY,
    thread_id     TEXT NOT NULL REFERENCES threads(thread_id),
    tool_calls    JSONB NOT NULL,
    risk_reasons  JSONB NOT NULL DEFAULT '[]'::jsonb,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_by  TEXT NOT NULL,
    reviewed_by   TEXT,
    review_note   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS approvals_status_idx ON approvals (status, created_at);

CREATE TABLE IF NOT EXISTS escalations (
    id          SERIAL PRIMARY KEY,
    thread_id   TEXT,
    ticket_id   INT REFERENCES tickets(id),
    reason      TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acknowledged', 'closed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per agent turn: the source of truth for latency / token / cost / success metrics.
CREATE TABLE IF NOT EXISTS agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    user_id         INT,
    kind            TEXT NOT NULL DEFAULT 'turn' CHECK (kind IN ('turn', 'resume')),
    status          TEXT NOT NULL,
    models          JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms      INT NOT NULL,
    llm_calls       INT NOT NULL DEFAULT 0,
    input_tokens    INT NOT NULL DEFAULT 0,
    output_tokens   INT NOT NULL DEFAULT 0,
    est_cost_usd    NUMERIC(12, 6) NOT NULL DEFAULT 0,
    tool_calls      JSONB NOT NULL DEFAULT '[]'::jsonb,
    guardrail_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_runs_created_idx ON agent_runs (created_at);

-- ---------------------------------------------------------------- knowledge base
CREATE TABLE IF NOT EXISTS kb_documents (
    id           SERIAL PRIMARY KEY,
    slug         TEXT UNIQUE NOT NULL,
    title        TEXT NOT NULL,
    source_path  TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id           SERIAL PRIMARY KEY,
    document_id  INT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    chunk_index  INT NOT NULL,
    heading      TEXT,
    content      TEXT NOT NULL,
    embedding    vector(384) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (
                    to_tsvector('english', coalesce(heading, '') || ' ' || content)
                 ) STORED,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx ON kb_chunks USING gin (tsv);
