"""Database layer.

Two distinct roles, never crossed:
  * `public` judgment tables  -> READ ONLY (source of truth).
  * `memchat` schema          -> our own chat / memory / ingest-log writes.

Everything goes through a single asyncpg pool on DATABASE_URL. The Supabase
service client is used only for the documented `hybrid_search` RPC.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Sequence

import asyncpg

from .config import Settings, get_settings

logger = logging.getLogger("nyaya.db")

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class Database:
    """Owns the asyncpg pool and applies the memchat migration."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool: Optional[asyncpg.Pool] = None
        self.available = False

    async def connect(self) -> None:
        dsn = self.settings.database_url
        if not dsn:
            logger.warning("DATABASE_URL not set — DB features disabled.")
            return
        try:
            is_supabase = "supabase" in dsn
            self.pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=8,
                command_timeout=30,
                # Supabase requires SSL; the pgbouncer pooler needs prepared
                # statements disabled.
                ssl="require" if is_supabase else None,
                statement_cache_size=0 if "pooler.supabase" in dsn else 100,
            )
            self.available = True
            logger.info("Connected to Postgres pool.")
            await self.apply_migrations()
        except Exception as exc:  # noqa: BLE001 - boot must not hard-crash
            logger.warning("Could not connect to Postgres (%s). DB features degraded.", exc)
            self.pool = None
            self.available = False

    async def apply_migrations(self) -> None:
        if not self.pool:
            return
        for sql_file in sorted(_MIGRATIONS.glob("*.sql")):
            sql = sql_file.read_text(encoding="utf-8")
            async with self.pool.acquire() as conn:
                await conn.execute(sql)
            logger.info("Applied migration %s", sql_file.name)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()


# --------------------------------------------------------------------------- #
#  Read-only repository over the existing judgments tables.
# --------------------------------------------------------------------------- #
class JudgmentRepo:
    """Read-only access to `public` judgment tables + the hybrid_search RPC."""

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self._supabase = None  # lazy

    # ----- supabase client (only for RPC) -----
    def _sb(self):
        if self._supabase is None:
            try:
                from supabase import create_client

                self._supabase = create_client(
                    self.settings.SUPABASE_URL,
                    self.settings.SUPABASE_SERVICE_KEY,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Supabase client unavailable: %s", exc)
                self._supabase = False
        return self._supabase or None

    async def get_metadata(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        if not ids or not self.db.pool:
            return []
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM public.judgments_metadata WHERE judgment_id = ANY($1::text[])",
                [str(i) for i in ids],
            )
        return [_jsonify(dict(r)) for r in rows]

    async def get_pages(self, judgment_id: str) -> list[dict[str, Any]]:
        if not self.db.pool:
            return []
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT page_number, text FROM public.judgment_pages "
                "WHERE judgment_id = $1 ORDER BY page_number",
                str(judgment_id),
            )
        return [dict(r) for r in rows]

    async def get_citations(self, judgment_id: str) -> list[dict[str, Any]]:
        """Outgoing + incoming citation edges for a judgment."""
        if not self.db.pool:
            return []
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, citing_id, cited_id, citation_type, citation_text, "
                "cited_citation, context "
                "FROM public.judgment_citations "
                "WHERE citing_id = $1 OR cited_id = $1",
                str(judgment_id),
            )
        return [dict(r) for r in rows]

    async def all_citations(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        """Citation edges where BOTH endpoints are in `ids` plus edges leaving
        `ids` (used to seed the corpus graph)."""
        if not ids or not self.db.pool:
            return []
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT citing_id, cited_id, citation_type, citation_text "
                "FROM public.judgment_citations WHERE citing_id = ANY($1::text[])",
                [str(i) for i in ids],
            )
        return [dict(r) for r in rows]

    async def list_recent(
        self, n: int = 200, since: Optional[str] = None
    ) -> list[dict[str, Any]]:
        if not self.db.pool:
            return []
        query = "SELECT * FROM public.judgments_metadata"
        args: list[Any] = []
        if since:
            query += " WHERE judgment_date >= $1"
            args.append(since)
        query += " ORDER BY judgment_date DESC NULLS LAST LIMIT $%d" % (len(args) + 1)
        args.append(n)
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [_jsonify(dict(r)) for r in rows]

    async def hybrid_retrieve(
        self, query_text: str, query_embedding: list[float], match_count: int = 20
    ) -> list[dict[str, Any]]:
        """Reliable vector+BM25+RRF retrieval returning ranked judgment_ids.

        Tries the documented `hybrid_search` RPC first (cross-check / parity);
        the deployed RPC has a known bug where an empty `statutes` filter excludes
        every row (`array_length('{}',1)` is NULL, not 0), so we fall back to a
        direct, read-only RRF query against judgment_vectors when the RPC is empty.
        """
        rpc_rows = await self.hybrid_search(query_text, query_embedding, match_count)
        if rpc_rows:
            return rpc_rows
        return await self.direct_hybrid_search(query_text, query_embedding, match_count)

    async def direct_hybrid_search(
        self, query_text: str, query_embedding: list[float], match_count: int = 20
    ) -> list[dict[str, Any]]:
        """Read-only RRF over judgment_vectors: dense (cosine) + sparse (tsvector),
        aggregated to one row per judgment. Touches only `public` read paths."""
        if not self.db.pool or not query_embedding:
            return []
        vec = _vec_literal(query_embedding)
        sql = """
        WITH vec AS (
            SELECT judgment_id,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS rk,
                   1 - (embedding <=> $1::vector) AS sim
            FROM public.judgment_vectors
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        ),
        txt AS (
            SELECT jv.judgment_id,
                   ROW_NUMBER() OVER (ORDER BY ts_rank_cd(jv.content_tsv, q) DESC) AS rk
            FROM public.judgment_vectors jv, plainto_tsquery('english', $3) q
            WHERE jv.content_tsv @@ q
            LIMIT $2
        ),
        unioned AS (
            SELECT judgment_id, rk, sim FROM vec
            UNION ALL
            SELECT judgment_id, rk, 0.0 AS sim FROM txt
        )
        SELECT judgment_id,
               SUM(1.0 / (60 + rk))::float8 AS rrf_score,
               MAX(sim)::float8 AS similarity
        FROM unioned
        GROUP BY judgment_id
        ORDER BY rrf_score DESC
        LIMIT $2
        """
        try:
            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch(sql, vec, match_count, query_text)
            return [_jsonify(dict(r)) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("direct_hybrid_search failed: %s", exc)
            return []

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        match_count: int = 20,
        filter_criteria: Optional[dict] = None,
    ) -> list[dict[str, Any]]:
        """Thin wrapper over the existing `hybrid_search` RPC (vector+BM25+RRF).

        Tries the Supabase RPC first (documented surface); falls back to a direct
        SQL call through asyncpg if the client is unavailable.
        """
        filter_criteria = filter_criteria or {}
        sb = self._sb()
        if sb is not None:
            try:
                resp = sb.rpc(
                    "hybrid_search",
                    {
                        "query_text": query_text,
                        "query_embedding": query_embedding,
                        "match_count": match_count,
                        "filter_criteria": filter_criteria,
                    },
                ).execute()
                return resp.data or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("hybrid_search RPC failed (%s); trying direct SQL.", exc)

        if not self.db.pool:
            return []
        try:
            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM public.hybrid_search($1, $2::vector, $3, $4::jsonb)",
                    query_text,
                    _vec_literal(query_embedding),
                    match_count,
                    json.dumps(filter_criteria),
                )
            return [_jsonify(dict(r)) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("hybrid_search direct SQL failed: %s", exc)
            return []


# --------------------------------------------------------------------------- #
#  memchat writes (sessions / messages / ingest log).
# --------------------------------------------------------------------------- #
class MemChatStore:
    def __init__(self, db: Database):
        self.db = db

    async def ensure_session(
        self, session_id: str, user_id: str, title: str = "New Session", topic: str = ""
    ) -> None:
        if not self.db.pool:
            return
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memchat.sessions (id, user_id, title, topic)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE SET last_active_at = now()
                """,
                session_id, user_id, title, topic,
            )

    async def save_message(
        self,
        msg_id: str,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        sources: Optional[list] = None,
        warnings: Optional[list] = None,
    ) -> None:
        if not self.db.pool:
            return
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memchat.messages
                    (id, session_id, user_id, role, content, sources, warnings)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
                ON CONFLICT (id) DO NOTHING
                """,
                msg_id, session_id, user_id, role, content,
                json.dumps(sources or []), json.dumps(warnings or []),
            )

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        if not self.db.pool:
            return []
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, user_id, title, topic, created_at, last_active_at "
                "FROM memchat.sessions WHERE user_id = $1 ORDER BY last_active_at DESC",
                user_id,
            )
        return [_jsonify(dict(r)) for r in rows]

    async def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        if not self.db.pool:
            return []
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, session_id, user_id, role, content, sources, warnings, created_at "
                "FROM memchat.messages WHERE session_id = $1 ORDER BY created_at",
                session_id,
            )
        return [_jsonify(dict(r)) for r in rows]

    async def already_ingested(self, judgment_ids: Sequence[str]) -> set[str]:
        if not judgment_ids or not self.db.pool:
            return set()
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT judgment_id FROM memchat.cognee_ingest_log "
                "WHERE judgment_id = ANY($1::text[]) AND status = 'done'",
                [str(i) for i in judgment_ids],
            )
        return {r["judgment_id"] for r in rows}

    async def mark_ingested(self, judgment_id: str, status: str = "done") -> None:
        if not self.db.pool:
            return
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memchat.cognee_ingest_log (judgment_id, status)
                VALUES ($1, $2)
                ON CONFLICT (judgment_id) DO UPDATE SET status = $2, ingested_at = now()
                """,
                str(judgment_id), status,
            )


class LegalKnowledgeStore:
    def __init__(self, db: Database):
        self.db = db

    async def upsert_document(
        self,
        *,
        doc_id: str,
        title: str,
        category: str,
        source_type: str,
        act_name: str | None = None,
        jurisdiction: str = "India",
        source_path: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self.db.pool:
            return
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memchat.legal_knowledge
                    (id, title, category, source_type, act_name, jurisdiction, source_path, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    category = EXCLUDED.category,
                    source_type = EXCLUDED.source_type,
                    act_name = EXCLUDED.act_name,
                    jurisdiction = EXCLUDED.jurisdiction,
                    source_path = EXCLUDED.source_path,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """,
                doc_id,
                title,
                category,
                source_type,
                act_name,
                jurisdiction,
                source_path,
                json.dumps(metadata or {}),
            )

    async def replace_chunks(self, doc_id: str, chunks: list[dict[str, Any]]) -> None:
        if not self.db.pool:
            return
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM memchat.legal_knowledge_chunks WHERE knowledge_id = $1",
                    doc_id,
                )
                for c in chunks:
                    emb = c.get("embedding")
                    await conn.execute(
                        """
                        INSERT INTO memchat.legal_knowledge_chunks
                            (id, knowledge_id, chunk_index, heading, content, page_start,
                             page_end, embedding, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector, $9::jsonb)
                        """,
                        c["id"],
                        doc_id,
                        c["chunk_index"],
                        c.get("heading"),
                        c["content"],
                        c.get("page_start"),
                        c.get("page_end"),
                        _vec_literal(emb) if emb else None,
                        json.dumps(c.get("metadata") or {}),
                    )

    async def document_summary(self, doc_id: str) -> dict[str, Any]:
        if not self.db.pool:
            return {}
        async with self.db.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT lk.id, lk.title, lk.source_type, lk.act_name,
                       COUNT(lkc.id)::int AS chunks,
                       COUNT(lkc.embedding)::int AS embedded_chunks
                FROM memchat.legal_knowledge lk
                LEFT JOIN memchat.legal_knowledge_chunks lkc ON lkc.knowledge_id = lk.id
                WHERE lk.id = $1
                GROUP BY lk.id, lk.title, lk.source_type, lk.act_name
                """,
                doc_id,
            )
        return dict(row) if row else {}

    async def corpus_stats(self) -> dict[str, Any]:
        """How much statute knowledge is loaded — for the ingest summary / health."""
        if not self.db.pool:
            return {}
        async with self.db.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM memchat.legal_knowledge)::int          AS documents,
                    (SELECT COUNT(*) FROM memchat.legal_knowledge_chunks)::int    AS chunks,
                    (SELECT COUNT(*) FROM memchat.legal_knowledge_chunks
                        WHERE embedding IS NOT NULL)::int                          AS embedded_chunks
                """
            )
        return dict(row) if row else {}

    async def search_chunks(
        self,
        query_text: str,
        query_embedding: list[float],
        match_count: int = 8,
        source_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Hybrid (vector + BM25) RRF search over legal_knowledge_chunks.

        Mirrors the judgment `direct_hybrid_search` design so statute retrieval
        behaves consistently: dense cosine + sparse tsvector, fused with reciprocal
        rank fusion, one row per chunk, joined back to its parent document so the
        answer layer gets act name / section / page provenance.
        """
        if not self.db.pool or not query_embedding:
            return []
        vec = _vec_literal(query_embedding)
        type_filter = "AND lk.source_type = $4" if source_type else ""
        sql = f"""
        WITH vec AS (
            SELECT c.id AS chunk_id,
                   ROW_NUMBER() OVER (ORDER BY c.embedding <=> $1::vector) AS rk,
                   1 - (c.embedding <=> $1::vector) AS sim
            FROM memchat.legal_knowledge_chunks c
            JOIN memchat.legal_knowledge lk ON lk.id = c.knowledge_id
            WHERE c.embedding IS NOT NULL {type_filter}
            ORDER BY c.embedding <=> $1::vector
            LIMIT $2
        ),
        txt AS (
            SELECT c.id AS chunk_id,
                   ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, q) DESC) AS rk
            FROM memchat.legal_knowledge_chunks c
            JOIN memchat.legal_knowledge lk ON lk.id = c.knowledge_id,
                 plainto_tsquery('english', $3) q
            WHERE c.content_tsv @@ q {type_filter}
            LIMIT $2
        ),
        unioned AS (
            SELECT chunk_id, rk FROM vec
            UNION ALL
            SELECT chunk_id, rk FROM txt
        ),
        fused AS (
            SELECT chunk_id, SUM(1.0 / (60 + rk))::float8 AS rrf_score
            FROM unioned
            GROUP BY chunk_id
            ORDER BY rrf_score DESC
            LIMIT $2
        )
        SELECT c.id AS chunk_id, c.knowledge_id, c.chunk_index, c.heading,
               c.content, c.page_start, c.page_end,
               lk.title, lk.act_name, lk.category, lk.source_type,
               lk.section_number, lk.source_path, lk.metadata AS doc_metadata,
               f.rrf_score
        FROM fused f
        JOIN memchat.legal_knowledge_chunks c ON c.id = f.chunk_id
        JOIN memchat.legal_knowledge lk ON lk.id = c.knowledge_id
        ORDER BY f.rrf_score DESC
        """
        args: list[Any] = [vec, match_count, query_text]
        if source_type:
            args.append(source_type)
        try:
            async with self.db.pool.acquire() as conn:
                # The chunks live behind an ivfflat index (lists=100). Its default
                # of probing ONE list badly under-recalls a small/medium corpus, so
                # widen the probe count for this query only (SET LOCAL in a txn).
                async with conn.transaction():
                    await conn.execute("SET LOCAL ivfflat.probes = 20")
                    rows = await conn.fetch(sql, *args)
            return [_jsonify(dict(r)) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("legal search_chunks failed: %s", exc)
            return []


class ActIngestLog:
    """Resumable bookkeeping for the bulk Central-Acts pipeline.

    One row per catalog act (memchat.act_ingest_log). Lets the pipeline skip acts
    already `done`, retry `failed`, and leave a durable audit of every act it
    could not process and why.
    """

    def __init__(self, db: Database):
        self.db = db

    async def seed(self, acts: list[dict[str, Any]]) -> int:
        """Register catalog acts as `pending` without disturbing existing rows.

        Uses a single pipelined `executemany` — seeding all 847 acts one-round-trip
        -at-a-time over the (Australia) pooler would blow the command timeout.
        """
        if not self.db.pool or not acts:
            return 0
        rows = [
            (
                a["act_id"], a["title"], a.get("act_number"),
                a.get("enactment_date"), a.get("ministry"),
                a.get("pdf_url"), a.get("act_page_url"),
            )
            for a in acts
            if a.get("act_id")
        ]
        async with self.db.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO memchat.act_ingest_log
                    (act_id, title, act_number, enactment_date, ministry,
                     pdf_url, act_page_url, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
                ON CONFLICT (act_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    act_number = EXCLUDED.act_number,
                    enactment_date = EXCLUDED.enactment_date,
                    ministry = EXCLUDED.ministry,
                    pdf_url = EXCLUDED.pdf_url,
                    act_page_url = EXCLUDED.act_page_url,
                    updated_at = now()
                """,
                rows,
            )
        return len(rows)

    async def done_ids(self) -> set[str]:
        if not self.db.pool:
            return set()
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT act_id FROM memchat.act_ingest_log WHERE status = 'done'"
            )
        return {r["act_id"] for r in rows}

    async def mark(
        self,
        act_id: str,
        status: str,
        *,
        doc_id: Optional[str] = None,
        pages: int = 0,
        chunks: int = 0,
        chars: int = 0,
        error: Optional[str] = None,
    ) -> None:
        if not self.db.pool:
            return
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE memchat.act_ingest_log
                   SET status = $2, doc_id = COALESCE($3, doc_id),
                       pages = $4, chunks = $5, chars = $6,
                       error = $7, attempts = attempts + 1, updated_at = now()
                 WHERE act_id = $1
                """,
                act_id, status, doc_id, pages, chunks, chars,
                (error or "")[:2000] or None,
            )

    async def status_counts(self) -> dict[str, int]:
        if not self.db.pool:
            return {}
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*)::int AS n FROM memchat.act_ingest_log GROUP BY status"
            )
        return {r["status"]: r["n"] for r in rows}


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _jsonify(row: dict[str, Any]) -> dict[str, Any]:
    """asyncpg returns JSON columns as strings — decode them so the API layer
    sees real dicts/lists. Also coerce datetime/date to isoformat strings."""
    import datetime as _dt

    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, str) and v and v[0] in "{[":
            try:
                out[k] = json.loads(v)
                continue
            except (ValueError, TypeError):
                pass
        if isinstance(v, (_dt.date, _dt.datetime)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _vec_literal(embedding: list[float]) -> str:
    """pgvector accepts a textual literal like '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


# --------------------------------------------------------------------------- #
#  singletons wired at startup
# --------------------------------------------------------------------------- #
_db: Optional[Database] = None
_repo: Optional[JudgmentRepo] = None
_store: Optional[MemChatStore] = None
_legal_store: Optional[LegalKnowledgeStore] = None
_act_log: Optional[ActIngestLog] = None


async def init_db() -> Database:
    global _db, _repo, _store, _legal_store, _act_log
    settings = get_settings()
    _db = Database(settings)
    await _db.connect()
    _repo = JudgmentRepo(_db, settings)
    _store = MemChatStore(_db)
    _legal_store = LegalKnowledgeStore(_db)
    _act_log = ActIngestLog(_db)
    return _db


def get_db() -> Database:
    assert _db is not None, "init_db() not called"
    return _db


def get_repo() -> JudgmentRepo:
    assert _repo is not None, "init_db() not called"
    return _repo


def get_store() -> MemChatStore:
    assert _store is not None, "init_db() not called"
    return _store


def get_legal_store() -> LegalKnowledgeStore:
    assert _legal_store is not None, "init_db() not called"
    return _legal_store


def get_act_log() -> ActIngestLog:
    assert _act_log is not None, "init_db() not called"
    return _act_log
