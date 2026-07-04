-- ============================================================================
-- nyaya-memory-chat : OUR OWN tables, isolated in the `memchat` schema.
-- We NEVER write to the `public` judgment tables (read-only source of truth).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS memchat;

-- One row per chat session.
CREATE TABLE IF NOT EXISTS memchat.sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    title           TEXT,
    topic           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON memchat.sessions (user_id, last_active_at DESC);

-- Raw transcript / audit log. The Cognee graph is the *intelligent* layer on top.
CREATE TABLE IF NOT EXISTS memchat.messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL,                -- 'user' | 'assistant' | 'recall'
    content     TEXT NOT NULL,
    sources     JSONB NOT NULL DEFAULT '[]',  -- judgment_ids actually used
    warnings    JSONB NOT NULL DEFAULT '[]',  -- unverified / overruled citations
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON memchat.messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_user ON memchat.messages (user_id, created_at);

-- Idempotency log for corpus ingestion into Cognee.
CREATE TABLE IF NOT EXISTS memchat.cognee_ingest_log (
    judgment_id  TEXT PRIMARY KEY,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT NOT NULL DEFAULT 'done'  -- 'done' | 'error' | 'pending'
);

-- ============================================================================
-- Local memory backend tables (used when MEMORY_BACKEND != cognee).
-- A lightweight Postgres-native graph+fact store so the service is fully
-- runnable and testable without the heavy Cognee + pgvector stack. The Cognee
-- backend ignores these entirely.
-- ============================================================================

-- Per-user durable facts extracted from conversation (cross-session memory).
CREATE TABLE IF NOT EXISTS memchat.user_facts (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    fact        TEXT NOT NULL,
    category    TEXT,                          -- Matter / Money / Jurisdiction / Status / Parties / Research
    source_session TEXT,
    embedding   DOUBLE PRECISION[],            -- OpenAI text-embedding-3-large (1536); cosine-ranked in app
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, fact)
);
CREATE INDEX IF NOT EXISTS idx_user_facts_user ON memchat.user_facts (user_id, created_at DESC);

-- Per-user memory graph (nodes + edges) for GET /graph visualisation.
CREATE TABLE IF NOT EXISTS memchat.memory_nodes (
    user_id     TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    label       TEXT NOT NULL,
    sub         TEXT,
    kind        TEXT NOT NULL,                 -- you / fact / good / over / act
    PRIMARY KEY (user_id, node_id)
);

CREATE TABLE IF NOT EXISTS memchat.memory_edges (
    user_id     TEXT NOT NULL,
    src         TEXT NOT NULL,
    dst         TEXT NOT NULL,
    PRIMARY KEY (user_id, src, dst)
);
