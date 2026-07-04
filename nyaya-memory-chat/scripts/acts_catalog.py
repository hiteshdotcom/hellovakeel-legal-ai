"""Parse the India Central Acts catalog (india_all_847_acts_complete.txt).

The catalog is a flat text export, one block per Act:

    Act Title: The Institute of Teaching and Research in Ayurveda Act, 2020
    Act Number: 16  |  Enactment Date: 21-Sep-2020  |  Act ID: 202016
    Ministry: Ministry of AYUSH
    Purpose: An Act to provide for ...
    PDF Download: https://www.indiacode.nic.in/bitstream/123456789/15647/1/A2020_16.pdf
    Act Page: https://www.indiacode.nic.in/handle/123456789/15647?view_type=browse
    ------------------------------------------------------------

This module turns that into a list of `ActRecord` — the single source of truth
the ingestion pipeline iterates over. Pure text parsing, no I/O beyond reading
the file, so it is trivially unit-testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

# The catalog ships next to the service dir, at the repo root.
DEFAULT_CATALOG = (
    Path(__file__).resolve().parents[2] / "india_all_847_acts_complete.txt"
)

_FIELD_RE = {
    "title": re.compile(r"^Act Title:\s*(.+?)\s*$"),
    "ministry": re.compile(r"^Ministry:\s*(.+?)\s*$"),
    "purpose": re.compile(r"^Purpose:\s*(.+?)\s*$"),
    "pdf_url": re.compile(r"^PDF Download:\s*(\S+)\s*$"),
    "act_page_url": re.compile(r"^Act Page:\s*(\S+)\s*$"),
}
# "Act Number: 16  |  Enactment Date: 21-Sep-2020  |  Act ID: 202016"
_NUMBER_LINE_RE = re.compile(
    r"^Act Number:\s*(?P<num>.+?)\s*\|\s*"
    r"Enactment Date:\s*(?P<date>.+?)\s*\|\s*"
    r"Act ID:\s*(?P<id>.+?)\s*$"
)


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return s or "act"


@dataclass
class ActRecord:
    title: str
    act_number: str = ""
    enactment_date: str = ""
    act_id: str = ""
    ministry: str = ""
    purpose: str = ""
    pdf_url: str = ""
    act_page_url: str = ""

    @property
    def year(self) -> Optional[str]:
        m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", self.title)
        return m.group(1) if m else None

    @property
    def stable_key(self) -> str:
        """A catalog-stable id for logging / dedup — prefers the Act ID."""
        base = (self.act_id or "").strip()
        if base:
            return re.sub(r"[^A-Za-z0-9_-]+", "-", base)
        return _slug(self.title)[:60]

    def doc_id(self) -> str:
        """Stable document id for memchat.legal_knowledge — deterministic from the
        Act ID so re-ingesting the same act updates in place (idempotent)."""
        return f"act-{self.stable_key}"

    @property
    def pdf_filename(self) -> str:
        tail = self.pdf_url.rstrip("/").split("/")[-1] or f"{self.stable_key}.pdf"
        if not tail.lower().endswith(".pdf"):
            tail += ".pdf"
        # Prefix with the stable key so cached files never collide.
        return f"{self.stable_key}__{tail}"


def parse_catalog(path: str | Path = DEFAULT_CATALOG) -> list[ActRecord]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return list(_iter_records(text))


def _iter_records(text: str) -> Iterator[ActRecord]:
    cur: dict[str, str] = {}

    def _flush() -> Optional[ActRecord]:
        if cur.get("title"):
            return ActRecord(
                title=cur.get("title", ""),
                act_number=cur.get("act_number", ""),
                enactment_date=cur.get("enactment_date", ""),
                act_id=cur.get("act_id", ""),
                ministry=cur.get("ministry", ""),
                purpose=cur.get("purpose", ""),
                pdf_url=cur.get("pdf_url", ""),
                act_page_url=cur.get("act_page_url", ""),
            )
        return None

    ministry = ""
    for raw in text.splitlines():
        line = raw.rstrip()

        # A new "Act Title:" line starts a new record — flush the previous one.
        if line.startswith("Act Title:"):
            rec = _flush()
            if rec:
                yield rec
            cur = {"ministry": ministry}
            cur["title"] = _FIELD_RE["title"].match(line).group(1)
            continue

        m = _NUMBER_LINE_RE.match(line)
        if m:
            cur["act_number"] = m.group("num")
            cur["enactment_date"] = m.group("date")
            cur["act_id"] = m.group("id")
            continue

        for key in ("ministry", "purpose", "pdf_url", "act_page_url"):
            fm = _FIELD_RE[key].match(line)
            if fm:
                cur[key] = fm.group(1)
                if key == "ministry" and not cur.get("title"):
                    # Ministry section header outside any record — remember it so
                    # subsequent acts inherit it if their own line is missing.
                    ministry = fm.group(1)
                break

    rec = _flush()
    if rec:
        yield rec


if __name__ == "__main__":  # pragma: no cover - quick sanity check / CLI
    import argparse
    import collections

    ap = argparse.ArgumentParser(description="Parse the Central Acts catalog.")
    ap.add_argument("catalog", nargs="?", default=str(DEFAULT_CATALOG))
    ap.add_argument("--show", type=int, default=3, help="print N sample records")
    args = ap.parse_args()

    records = parse_catalog(args.catalog)
    with_pdf = [r for r in records if r.pdf_url]
    by_ministry = collections.Counter(r.ministry for r in records)
    print(f"Parsed {len(records)} acts ({len(with_pdf)} with a PDF URL) "
          f"across {len(by_ministry)} ministries.")
    dupe_ids = [k for k, v in collections.Counter(r.stable_key for r in records).items() if v > 1]
    if dupe_ids:
        print(f"WARNING: {len(dupe_ids)} duplicate stable keys (first few): {dupe_ids[:5]}")
    for r in records[: args.show]:
        print("-" * 60)
        print(f"  title:   {r.title}")
        print(f"  act_id:  {r.act_id}  number={r.act_number}  date={r.enactment_date}")
        print(f"  doc_id:  {r.doc_id()}")
        print(f"  ministry:{r.ministry}")
        print(f"  pdf:     {r.pdf_url}")
