-- ============================================================================
-- Legal knowledge base: statutes, rules, explainers, templates, procedures.
-- Kept in memchat so it never writes into the read-only judgment corpus.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memchat.legal_knowledge (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    category        TEXT NOT NULL,
    source_type     TEXT NOT NULL, -- act | rule | procedure | drafting_template | explainer | checklist | definition
    act_name        TEXT,
    section_number  TEXT,
    topic           TEXT,
    jurisdiction    TEXT NOT NULL DEFAULT 'India',
    source_path     TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_legal_knowledge_source_type
ON memchat.legal_knowledge (source_type);

CREATE INDEX IF NOT EXISTS idx_legal_knowledge_act_section
ON memchat.legal_knowledge (act_name, section_number);

CREATE TABLE IF NOT EXISTS memchat.legal_knowledge_chunks (
    id             TEXT PRIMARY KEY,
    knowledge_id   TEXT NOT NULL REFERENCES memchat.legal_knowledge(id) ON DELETE CASCADE,
    chunk_index    INTEGER NOT NULL,
    heading        TEXT,
    content        TEXT NOT NULL,
    page_start     INTEGER,
    page_end       INTEGER,
    embedding      vector(1536),
    content_tsv    TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    metadata       JSONB NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (knowledge_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_legal_knowledge_chunks_knowledge
ON memchat.legal_knowledge_chunks (knowledge_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_legal_knowledge_chunks_tsv
ON memchat.legal_knowledge_chunks USING GIN (content_tsv);

CREATE INDEX IF NOT EXISTS idx_legal_knowledge_chunks_embedding
ON memchat.legal_knowledge_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
