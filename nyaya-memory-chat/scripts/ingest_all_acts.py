"""Bulk-ingest every India Central Act from the catalog into the AI's knowledge.

Pipeline, per act (from india_all_847_acts_complete.txt):

    download PDF  ->  extract text (pypdf)  ->  chunk  ->  embed (OpenAI 1536)
        ->  upsert memchat.legal_knowledge(+_chunks)  ->  mark done

Designed to survive a real 847-file run:
  * Resumable   — skips acts already `done` in memchat.act_ingest_log.
  * Fault-tolerant — a dead PDF URL, a scanned image with no text layer, or a
    network blip fails ONE act (recorded with its reason) and never aborts the run.
  * Bounded concurrency — downloads + embeds run N-at-a-time (default 4) so we
    don't hammer indiacode.nic.in or the OpenAI rate limit.
  * Idempotent — doc_id is derived from the Act ID, so re-running updates in place.
  * PDF cache  — downloaded PDFs are kept under --pdf-dir so a re-run doesn't
    re-download.

Examples:
  # Validate end-to-end on 3 acts (downloads + embeds + stores):
  python -m scripts.ingest_all_acts --limit 3

  # Full run, 6 workers:
  python -m scripts.ingest_all_acts --concurrency 6

  # One ministry only, no embeddings (structure/text only, cheap):
  python -m scripts.ingest_all_acts --ministry "Ministry of Law and Justice" --no-embeddings

  # Retry everything that previously failed:
  python -m scripts.ingest_all_acts --retry-failed
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_act_log, get_db, get_legal_store, init_db  # noqa: E402
from app.llm import get_llm  # noqa: E402
from scripts.acts_catalog import ActRecord, parse_catalog  # noqa: E402
from scripts.ingest_legal_knowledge import chunk_pages, extract_pdf_pages  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_all_acts")

DEFAULT_PDF_DIR = Path(__file__).resolve().parents[1] / "data" / "acts_pdfs"

# indiacode.nic.in is picky about clients; a browser UA avoids 403s.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 nyaya-acts-ingest/1.0"
)


class ActFailure(Exception):
    """Raised to fail a single act with a recorded status + reason."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status  # 'failed' | 'no_text' | 'skipped'
        self.message = message


def _ministry_category(ministry: str) -> str:
    m = (ministry or "").replace("Ministry of ", "").strip()
    return m or "General Law"


async def _download_pdf(client, act: ActRecord, pdf_dir: Path, force: bool) -> Path:
    """Download (and cache) the act PDF. Raises ActFailure on a dead/blocked URL."""
    if not act.pdf_url:
        raise ActFailure("skipped", "no PDF URL in catalog")
    dest = pdf_dir / act.pdf_filename
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return dest

    import httpx  # local import so the parser stays dependency-light

    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            resp = await client.get(act.pdf_url, follow_redirects=True)
            resp.raise_for_status()
            body = resp.content
            head = body[:5].lstrip()
            if not body:
                raise ActFailure("failed", "empty response body")
            if not head.startswith(b"%PDF"):
                # indiacode occasionally serves an HTML error page with 200.
                raise ActFailure("failed", f"not a PDF (starts with {head!r})")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            return dest
        except ActFailure:
            raise
        except httpx.HTTPStatusError as exc:
            # 4xx (dead link) won't fix itself — fail fast; retry 5xx/timeouts.
            if 400 <= exc.response.status_code < 500:
                raise ActFailure("failed", f"HTTP {exc.response.status_code}")
            last_exc = exc
        except Exception as exc:  # noqa: BLE001 - network flake -> retry
            last_exc = exc
        await asyncio.sleep(1.5 * attempt)
    raise ActFailure("failed", f"download failed after retries: {last_exc}")


async def _process_act(
    act: ActRecord,
    *,
    client,
    sem: asyncio.Semaphore,
    pdf_dir: Path,
    embed: bool,
    max_chars: int,
    overlap_chars: int,
    force_download: bool,
) -> tuple[str, str]:
    """Process one act. Returns (act_id, status). Never raises."""
    log = get_act_log()
    store = get_legal_store()
    llm = get_llm()
    key = act.stable_key  # log/resume key (== act_id when the catalog has one)
    t0 = time.time()
    async with sem:
        try:
            pdf_path = await _download_pdf(client, act, pdf_dir, force_download)

            # pypdf extraction is CPU/IO-bound and sync -> run off the event loop.
            pages = await asyncio.to_thread(extract_pdf_pages, pdf_path)
            if not pages:
                raise ActFailure("no_text", "no extractable text (likely scanned image)")

            doc_id = act.doc_id()
            chunks = chunk_pages(
                pages,
                doc_id=doc_id,
                act_name=act.title,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
            if not chunks:
                raise ActFailure("no_text", "no chunks produced from extracted text")

            if embed:
                for i in range(0, len(chunks), 64):
                    batch = chunks[i : i + 64]
                    vectors = await llm.embed_many([c["content"] for c in batch])
                    if not vectors or not vectors[0]:
                        raise ActFailure("failed", "embedding call returned empty vectors")
                    for c, v in zip(batch, vectors):
                        c["embedding"] = v

            await store.upsert_document(
                doc_id=doc_id,
                title=act.title,
                category=_ministry_category(act.ministry),
                source_type="act",
                act_name=act.title,
                jurisdiction="India",
                source_path=str(pdf_path),
                metadata={
                    "act_id": act.act_id,
                    "act_number": act.act_number,
                    "enactment_date": act.enactment_date,
                    "year": act.year,
                    "ministry": act.ministry,
                    "purpose": act.purpose,
                    "pdf_url": act.pdf_url,
                    "act_page_url": act.act_page_url,
                    "pages_extracted": len(pages),
                    "source": "india_code_catalog",
                },
            )
            await store.replace_chunks(doc_id, chunks)

            chars = sum(len(c["content"]) for c in chunks)
            await log.mark(
                key, "done", doc_id=doc_id,
                pages=len(pages), chunks=len(chunks), chars=chars,
            )
            logger.info(
                "OK   %-9s %-55s %2d pg / %2d ch  %.1fs",
                key, act.title[:55], len(pages), len(chunks), time.time() - t0,
            )
            return key, "done"

        except ActFailure as exc:
            await log.mark(key, exc.status, error=exc.message)
            logger.warning("%-4s %-9s %-45s  %s", exc.status.upper(), key,
                           act.title[:45], exc.message)
            return key, exc.status
        except Exception as exc:  # noqa: BLE001 - one act must never kill the run
            await log.mark(key, "failed", error=repr(exc))
            logger.exception("FAIL %s %s", key, act.title[:45])
            return key, "failed"


def _select_acts(args: argparse.Namespace) -> list[ActRecord]:
    acts = parse_catalog(args.catalog)
    if args.ministry:
        needle = args.ministry.lower()
        acts = [a for a in acts if needle in (a.ministry or "").lower()]
    if args.act_id:
        wanted = {x.strip() for x in args.act_id.split(",")}
        acts = [a for a in acts if a.act_id in wanted]
    return acts


async def run(args: argparse.Namespace) -> int:
    import httpx

    acts = _select_acts(args)
    if not acts:
        logger.error("No acts matched the selection.")
        return 2

    db = await init_db()
    if not db.available:
        raise SystemExit("Database unavailable. Check DATABASE_URL in .env.")

    log = get_act_log()
    # Key every act by its stable_key (synthesised from the title when the catalog
    # entry is missing an Act ID), so even malformed rows are tracked and resumed.
    if not args.dry_run:
        seeded = await log.seed([{**a.__dict__, "act_id": a.stable_key} for a in acts])
        logger.info("Seeded / refreshed %d acts in act_ingest_log.", seeded)

    # Resume: drop acts already done, unless we're re-running them explicitly.
    done = set() if (args.force or args.retry_failed) else await log.done_ids()
    counts = await log.status_counts()
    if args.retry_failed:
        # Keep only acts previously failed / no_text / skipped (plus any never-tried).
        retryable = {"failed", "no_text", "skipped", "pending"}
        statuses = await _statuses_for(log, [a.stable_key for a in acts])
        acts = [a for a in acts if statuses.get(a.stable_key, "pending") in retryable]
    else:
        acts = [a for a in acts if a.stable_key not in done]

    if args.limit:
        acts = acts[: args.limit]

    logger.info(
        "Catalog selection: %d acts to process (skipping %d already done). "
        "Current status counts: %s",
        len(acts), len(done), counts or "{}",
    )
    if args.dry_run:
        for a in acts[:20]:
            logger.info("  would process %-9s %s", a.act_id, a.title[:70])
        logger.info("Dry run: %d acts would be processed.", len(acts))
        await get_db().close()
        return 0
    if not acts:
        logger.info("Nothing to do — everything selected is already ingested.")
        await _print_summary(log)
        await get_db().close()
        return 0

    pdf_dir = Path(args.pdf_dir)
    sem = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(args.timeout, connect=15.0)
    results: dict[str, int] = {}
    t0 = time.time()
    async with httpx.AsyncClient(headers={"User-Agent": _UA}, timeout=timeout) as client:
        tasks = [
            _process_act(
                a, client=client, sem=sem, pdf_dir=pdf_dir,
                embed=not args.no_embeddings,
                max_chars=args.max_chars, overlap_chars=args.overlap_chars,
                force_download=args.force_download,
            )
            for a in acts
        ]
        for i, coro in enumerate(asyncio.as_completed(tasks), start=1):
            _, status = await coro
            results[status] = results.get(status, 0) + 1
            if i % 25 == 0 or i == len(tasks):
                logger.info("Progress %d/%d  %s", i, len(tasks), results)

    logger.info("Run finished in %.1fs. This run: %s", time.time() - t0, results)
    await _print_summary(log)
    await get_db().close()
    return 0


async def _statuses_for(log, act_ids: list[str]) -> dict[str, str]:
    if not log.db.pool or not act_ids:
        return {}
    async with log.db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT act_id, status FROM memchat.act_ingest_log WHERE act_id = ANY($1::text[])",
            act_ids,
        )
    return {r["act_id"]: r["status"] for r in rows}


async def _print_summary(log) -> None:
    counts = await log.status_counts()
    stats = await get_legal_store().corpus_stats()
    logger.info("=" * 64)
    logger.info("act_ingest_log status : %s", counts)
    logger.info("legal_knowledge total : %s", stats)
    logger.info("=" * 64)


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Bulk-ingest India Central Acts.")
    ap.add_argument("--catalog", default=None, help="Path to the acts catalog txt")
    ap.add_argument("--pdf-dir", default=str(DEFAULT_PDF_DIR), help="PDF cache dir")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="process at most N acts")
    ap.add_argument("--ministry", default=None, help="filter: ministry substring")
    ap.add_argument("--act-id", default=None, help="filter: comma-separated Act IDs")
    ap.add_argument("--max-chars", type=int, default=4500)
    ap.add_argument("--overlap-chars", type=int, default=450)
    ap.add_argument("--timeout", type=float, default=60.0, help="per-download timeout (s)")
    ap.add_argument("--no-embeddings", action="store_true",
                    help="store text/chunks without OpenAI embeddings (cheap dry test)")
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-process acts previously failed/no_text/skipped")
    ap.add_argument("--force", action="store_true", help="ignore 'done' and reprocess all selected")
    ap.add_argument("--force-download", action="store_true", help="ignore the PDF cache")
    ap.add_argument("--dry-run", action="store_true", help="list what would be processed")
    args = ap.parse_args(argv)
    if args.catalog is None:
        from scripts.acts_catalog import DEFAULT_CATALOG
        args.catalog = str(DEFAULT_CATALOG)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
