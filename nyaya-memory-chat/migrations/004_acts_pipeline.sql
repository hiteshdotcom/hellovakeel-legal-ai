-- ============================================================================
-- Bulk-acts ingestion bookkeeping.
--
-- One row per Central Act in the india_all_847_acts_complete.txt catalog. The
-- pipeline (scripts/ingest_all_acts.py) uses this table to be resumable and
-- fault-tolerant: it skips acts already `done`, retries `failed`, and records
-- why any act could not be processed (dead PDF URL, scanned image with no text,
-- etc.). The actual statute text lives in memchat.legal_knowledge(_chunks).
-- ============================================================================

CREATE TABLE IF NOT EXISTS memchat.act_ingest_log (
    act_id          TEXT PRIMARY KEY,           -- Act ID from the catalog (e.g. 202016)
    title           TEXT NOT NULL,
    act_number      TEXT,
    enactment_date  TEXT,
    ministry        TEXT,
    pdf_url         TEXT,
    act_page_url    TEXT,
    doc_id          TEXT,                        -- -> memchat.legal_knowledge.id when stored
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | done | failed | no_text | skipped
    pages           INTEGER NOT NULL DEFAULT 0,
    chunks          INTEGER NOT NULL DEFAULT 0,
    chars           INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_act_ingest_log_status
ON memchat.act_ingest_log (status);

CREATE INDEX IF NOT EXISTS idx_act_ingest_log_ministry
ON memchat.act_ingest_log (ministry);
