"""Statute / legal-knowledge retrieval.

Complements CorpusRetriever (which searches curated *judgments*). This one
searches the ingested *statutes* — the India Central Acts loaded by
scripts/ingest_all_acts.py into memchat.legal_knowledge(_chunks) — so the
assistant can answer "what does <Act> say about <matter>" from the Act text
itself, with act-name + page provenance.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import get_settings
from ..db import LegalKnowledgeStore
from ..llm import LLMClients

logger = logging.getLogger("nyaya.memory.legal_retrieval")

# States/UTs (incl. common historical names) for jurisdiction-aware re-ranking.
# When a query names a State, that State's own statute should outrank the same
# statute enacted by a different State — the decomposition strips the State out of
# the sub-queries, so near-identical sibling-State acts otherwise compete blindly.
_STATE_CANON: dict[str, str] = {
    "andhra pradesh": "Andhra Pradesh", "arunachal": "Arunachal Pradesh",
    "assam": "Assam", "bihar": "Bihar", "chhattisgarh": "Chhattisgarh",
    "chattisgarh": "Chhattisgarh", "goa": "Goa", "gujarat": "Gujarat",
    "haryana": "Haryana", "himachal": "Himachal Pradesh",
    "jammu": "Jammu and Kashmir", "jharkhand": "Jharkhand",
    "karnataka": "Karnataka", "mysore": "Karnataka", "kerala": "Kerala",
    "madhya pradesh": "Madhya Pradesh", "maharashtra": "Maharashtra",
    "bombay": "Maharashtra", "manipur": "Manipur", "meghalaya": "Meghalaya",
    "mizoram": "Mizoram", "nagaland": "Nagaland", "odisha": "Odisha",
    "orissa": "Odisha", "punjab": "Punjab", "rajasthan": "Rajasthan",
    "sikkim": "Sikkim", "tamil nadu": "Tamil Nadu", "tamilnadu": "Tamil Nadu",
    "madras": "Tamil Nadu", "telangana": "Telangana", "tripura": "Tripura",
    "uttar pradesh": "Uttar Pradesh", "uttarakhand": "Uttarakhand",
    "uttaranchal": "Uttarakhand", "west bengal": "West Bengal", "bengal": "West Bengal",
    "delhi": "Delhi", "puducherry": "Puducherry", "pondicherry": "Puducherry",
}
_STATE_QUERY_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in _STATE_CANON), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _query_state(text: str) -> str:
    """The State/UT named in a query, canonicalised, or "" (Central-only query)."""
    m = _STATE_QUERY_RE.search(text or "")
    return _STATE_CANON[m.group(1).lower()] if m else ""


_DECOMPOSE_SYSTEM = (
    "You are a legal research router for Indian law. Rewrite a client's situation "
    "into 2-4 SHORT, focused statute-search queries, each naming the controlling "
    "legal doctrine or statutory concept — NOT the client's story. Prefer the "
    "operative rule over the facts. Examples of good queries: 'gift of immovable "
    "property void without registered deed attested by two witnesses'; 'limitation "
    "period to cancel a registered instrument'; 'devolution of self-acquired "
    "property on intestate death under Hindu law'; 'transfer of tenancy rights on "
    "death of tenant'. Return ONLY JSON: {\"queries\":[\"...\"]}. No commentary."
)


@dataclass
class RetrievedStatute:
    chunk_id: str
    knowledge_id: str
    title: str
    act_name: str = ""
    category: str = ""
    heading: str = ""
    content: str = ""
    jurisdiction: str = "India"
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def year(self) -> str:
        y = self.metadata.get("year")
        return str(y) if y else ""

    @property
    def citation_label(self) -> str:
        """How the answer should cite this statute inline, e.g.
        `The Companies Act, 2013` (+ page when available)."""
        label = self.act_name or self.title
        if self.page_start:
            return f"{label}, p.{self.page_start}"
        return label

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "knowledge_id": self.knowledge_id,
            "kind": "statute",
            "title": self.title,
            "act_name": self.act_name,
            "category": self.category,
            "heading": self.heading,
            "jurisdiction": self.jurisdiction,
            "citation": self.citation_label,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "year": self.year,
            "score": self.score,
        }


class LegalKnowledgeRetriever:
    def __init__(self, store: LegalKnowledgeStore, llm: LLMClients):
        self.store = store
        self.llm = llm
        self.settings = get_settings()

    async def retrieve(self, query: str, top_n: int = 6) -> list[RetrievedStatute]:
        if not query.strip():
            return []

        # 1) Decompose the narrative into focused legal-issue queries. Embedding a
        #    long client story pulls toward whatever the story is ABOUT (a death →
        #    succession acts) and buries the controlling rule (a disputed gift →
        #    Transfer of Property Act s.123). Retrieving per-issue surfaces the
        #    statute that actually governs, not just the topical neighbours.
        subqueries = await self._sub_issues(query)
        try:
            embeds = await self.llm.embed_many(subqueries)
        except Exception as exc:  # noqa: BLE001
            logger.warning("statute query embed failed: %s", exc)
            return []

        pairs = [(sq, emb) for sq, emb in zip(subqueries, embeds) if emb]
        if not pairs:
            return []

        # 2) Search each sub-issue concurrently. Pull a wide candidate pool so the
        #    right act is present even when it is not the top topical match.
        match_count = max(top_n * 5, 30)
        searches = await asyncio.gather(
            *[self.store.search_chunks(sq, emb, match_count=match_count) for sq, emb in pairs],
            return_exceptions=True,
        )

        # 3) Fuse to ONE row per act (fixes duplicate citations) via act-level RRF
        #    across the sub-issue result lists — an act that ranks high for ANY
        #    genuine issue rises, and an act relevant to several issues rises more.
        best_chunk: dict[str, dict[str, Any]] = {}
        act_rrf: dict[str, float] = {}
        for rows in searches:
            if isinstance(rows, BaseException) or not rows:
                if isinstance(rows, BaseException):
                    logger.warning("statute sub-search failed: %s", rows)
                continue
            seen_here: set[str] = set()
            for rank, r in enumerate(rows):
                act = (r.get("act_name") or r.get("title") or "").strip().lower()
                if not act:
                    continue
                if act not in seen_here:  # rank the act by its best chunk in this list
                    seen_here.add(act)
                    act_rrf[act] = act_rrf.get(act, 0.0) + 1.0 / (60 + rank)
                cur = best_chunk.get(act)
                if cur is None or float(r.get("rrf_score") or 0) > float(cur.get("rrf_score") or 0):
                    best_chunk[act] = r

        # 3b) Jurisdiction-aware boost. If the query names a State, prefer that
        #     State's own act and demote OTHER States' near-identical acts (Central
        #     acts always apply, so they are left unchanged). This fixes the
        #     "Bihar sugarcane query returns the Karnataka/Punjab/TN sugarcane acts"
        #     failure — the controlling State statute now outranks its siblings.
        q_state = _query_state(query)
        if q_state:
            for act in list(act_rrf):
                juris = (best_chunk[act].get("jurisdiction") or "India").strip()
                if juris == q_state:
                    act_rrf[act] *= 1.8
                elif juris and juris != "India":
                    act_rrf[act] *= 0.45

        ordered = sorted(act_rrf.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        # NB: we deliberately do NOT Cohere-rerank here. Rerank scores the act's
        # single best-by-RRF chunk, which is often boilerplate (a schedule or
        # amendment page) rather than the operative section, and it was demoting
        # the obviously-correct statute (e.g. dropping the Negotiable Instruments
        # Act for a cheque-bounce query). The multi-issue fused order is stronger.
        results: list[RetrievedStatute] = []
        for act, fused in ordered:
            r = best_chunk[act]
            meta = r.get("doc_metadata") or {}
            if isinstance(meta, str):  # asyncpg may hand back raw json
                meta = {}
            results.append(
                RetrievedStatute(
                    chunk_id=str(r.get("chunk_id") or ""),
                    knowledge_id=str(r.get("knowledge_id") or ""),
                    title=r.get("title") or "",
                    act_name=r.get("act_name") or "",
                    category=r.get("category") or "",
                    heading=r.get("heading") or "",
                    jurisdiction=r.get("jurisdiction") or "India",
                    content=(r.get("content") or "")[:1600],
                    page_start=r.get("page_start"),
                    page_end=r.get("page_end"),
                    score=float(fused),
                    metadata=meta,
                )
            )
        return results

    async def _sub_issues(self, query: str) -> list[str]:
        """Original query + 2-4 focused legal-issue queries from a cheap LLM.
        Always includes the original so nothing is lost if decomposition fails.
        Uses fast gpt-4o-mini JSON (dominates retrieval latency otherwise); falls
        back to the Claude fast model only if the cheap path returns nothing."""
        subs = [query.strip()]
        try:
            data = await self.llm.complete_json(_DECOMPOSE_SYSTEM, query, max_tokens=250)
            if not (isinstance(data, dict) and data.get("queries")):
                out = await self.llm.complete(_DECOMPOSE_SYSTEM, query, fast=True, max_tokens=250)
                data = _loads_lenient(out)
            for q in data.get("queries", []) if isinstance(data, dict) else []:
                q = (q or "").strip() if isinstance(q, str) else ""
                if q and q.lower() not in {s.lower() for s in subs}:
                    subs.append(q)
        except Exception as exc:  # noqa: BLE001 - decomposition is best-effort
            logger.warning("sub-issue decomposition failed: %s", exc)
        return subs[:5]


def _loads_lenient(text: str) -> dict:
    """Parse a JSON object out of an LLM reply that may wrap it in prose/fences."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return {}
    return {}
