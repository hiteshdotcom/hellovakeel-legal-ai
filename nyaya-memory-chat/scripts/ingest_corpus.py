"""Corpus ingestion job.

Turns our judgments into a Cognee knowledge graph WITH our real citation edges
(from public.judgment_citations), using the low-level Tasks/Pipeline/DataPoints
API. Idempotent (skips already-ingested judgment_ids), demo-friendly (--limit,
--since), batched (~20 judgments / LLM call), and runs OFFLINE — never in the
request path.

With MEMORY_BACKEND=local the corpus graph already lives in Postgres
(judgment_citations); this job then validates connectivity and records the
ingest log so GET /graph-corpus works against real edges.

Usage:
  python -m scripts.ingest_corpus --limit 200
  python -m scripts.ingest_corpus --since 2015-01-01 --limit 500
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow `python scripts/ingest_corpus.py` as well as `-m scripts.ingest_corpus`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import get_repo, get_store, init_db  # noqa: E402
from app.llm import get_llm  # noqa: E402
from app.memory import user_memory  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest")


async def run(limit: int, since: str | None, batch_size: int) -> None:
    settings = get_settings()
    db = await init_db()
    if not db.available:
        logger.error("Database unavailable — cannot ingest. Check DATABASE_URL.")
        return
    backend = user_memory.init_memory(db, get_llm())
    repo = get_repo()
    store = get_store()
    logger.info("Backend=%s · ingesting up to %s judgments (since=%s)", backend.name, limit, since)

    rows = await repo.list_recent(n=limit, since=since)
    logger.info("Fetched %d judgment rows (newest-first).", len(rows))
    if not rows:
        return

    all_ids = [str(r["judgment_id"]) for r in rows]
    done = await store.already_ingested(all_ids)
    pending = [r for r in rows if str(r["judgment_id"]) not in done]
    logger.info("%d already ingested, %d pending.", len(done), len(pending))

    ingested = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        try:
            n = await backend.add_corpus_judgments(batch)
            for r in batch:
                await store.mark_ingested(str(r["judgment_id"]), "done")
            ingested += n
            logger.info("Batch %d: ingested %d (total %d).", i // batch_size + 1, n, ingested)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Batch %d failed: %s", i // batch_size + 1, exc)
            for r in batch:
                await store.mark_ingested(str(r["judgment_id"]), "error")

    # Quick edge summary for the demo.
    edge_rows = await repo.all_citations(all_ids)
    logger.info(
        "Done. Ingested %d judgments; %d citation edges available for the graph.",
        ingested, len(edge_rows),
    )
    await db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest judgments into the Cognee corpus graph.")
    ap.add_argument("--limit", type=int, default=200, help="Max judgments to ingest (newest-first).")
    ap.add_argument("--since", type=str, default=None, help="Only judgments on/after YYYY-MM-DD.")
    ap.add_argument("--batch-size", type=int, default=20, help="Judgments per batch / LLM call.")
    args = ap.parse_args()
    asyncio.run(run(args.limit, args.since, args.batch_size))


if __name__ == "__main__":
    main()
