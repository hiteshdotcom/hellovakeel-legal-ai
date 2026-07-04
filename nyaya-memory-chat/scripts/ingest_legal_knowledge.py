"""Ingest statutes / legal knowledge PDFs into memchat.legal_knowledge.

Example:
  python -m scripts.ingest_legal_knowledge ..\20240716890312078.pdf ^
    --title "Constitution of India" --act-name "Constitution of India" ^
    --category "Constitutional Law" --source-type act
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_db, get_legal_store, init_db  # noqa: E402
from app.llm import get_llm  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_legal_knowledge")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return s or "legal-document"


def _doc_id(title: str, path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{_slug(title)}-{digest}"


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_pages(path: Path) -> list[dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - environment setup guard
        raise SystemExit(
            "Missing PDF dependency. Install with: uv pip install -p .venv\\Scripts\\python.exe pypdf"
        ) from exc

    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = _clean_text(page.extract_text() or "")
        if text:
            pages.append({"page": i, "text": text})
    return pages


def _heading_for(text: str, fallback: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines[:8]:
        if re.search(r"\b(ARTICLE|Article|PART|Part|SCHEDULE|Schedule)\b", ln):
            return ln[:160]
    return fallback


def chunk_pages(
    pages: list[dict],
    *,
    doc_id: str,
    act_name: str,
    max_chars: int = 4500,
    overlap_chars: int = 450,
) -> list[dict]:
    chunks: list[dict] = []
    buf = ""
    start_page = None
    last_page = None

    def flush() -> None:
        nonlocal buf, start_page, last_page
        content = _clean_text(buf)
        if not content:
            return
        idx = len(chunks)
        heading = _heading_for(content, f"{act_name} pages {start_page}-{last_page}")
        chunks.append(
            {
                "id": f"{doc_id}:chunk:{idx:04d}",
                "chunk_index": idx,
                "heading": heading,
                "content": f"{act_name}\n{heading}\n\n{content}",
                "page_start": start_page,
                "page_end": last_page,
                "metadata": {"source": "pdf"},
            }
        )
        tail = content[-overlap_chars:] if overlap_chars else ""
        buf = tail
        start_page = last_page if tail else None

    for p in pages:
        page_no = int(p["page"])
        page_text = f"[Page {page_no}]\n{p['text']}\n\n"
        if start_page is None:
            start_page = page_no
        last_page = page_no
        if len(buf) + len(page_text) > max_chars and buf:
            flush()
        buf += page_text
    flush()
    return chunks


async def _embed_chunks(chunks: list[dict], batch_size: int = 32) -> None:
    llm = get_llm()
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        embeddings = await llm.embed_many([c["content"] for c in batch])
        for chunk, embedding in zip(batch, embeddings):
            chunk["embedding"] = embedding
        logger.info("Embedded chunks %d-%d", i + 1, i + len(batch))


async def run(args: argparse.Namespace) -> None:
    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    pages = extract_pdf_pages(pdf_path)
    if not pages:
        raise SystemExit("No extractable text found in PDF.")

    doc_id = args.id or _doc_id(args.title, pdf_path)
    chunks = chunk_pages(
        pages,
        doc_id=doc_id,
        act_name=args.act_name,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
    )
    logger.info("Extracted %d text pages into %d chunks.", len(pages), len(chunks))

    if not args.no_embeddings:
        await _embed_chunks(chunks)

    db = await init_db()
    if not db.available:
        raise SystemExit("Database unavailable. Check DATABASE_URL.")
    store = get_legal_store()
    await store.upsert_document(
        doc_id=doc_id,
        title=args.title,
        category=args.category,
        source_type=args.source_type,
        act_name=args.act_name,
        jurisdiction=args.jurisdiction,
        source_path=str(pdf_path),
        metadata={
            "pages_extracted": len(pages),
            "pdf_filename": pdf_path.name,
            "chunking": {
                "max_chars": args.max_chars,
                "overlap_chars": args.overlap_chars,
            },
        },
    )
    await store.replace_chunks(doc_id, chunks)
    summary = await store.document_summary(doc_id)
    logger.info("Stored legal knowledge: %s", summary)
    await get_db().close()


def main(argv: Iterable[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Ingest a legal knowledge PDF.")
    ap.add_argument("pdf", help="Path to PDF")
    ap.add_argument("--id", default=None, help="Stable document id")
    ap.add_argument("--title", required=True)
    ap.add_argument("--act-name", required=True)
    ap.add_argument("--category", default="General Law")
    ap.add_argument("--source-type", default="act")
    ap.add_argument("--jurisdiction", default="India")
    ap.add_argument("--max-chars", type=int, default=4500)
    ap.add_argument("--overlap-chars", type=int, default=450)
    ap.add_argument("--no-embeddings", action="store_true", help="Store chunks without OpenAI embeddings")
    args = ap.parse_args(argv)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
