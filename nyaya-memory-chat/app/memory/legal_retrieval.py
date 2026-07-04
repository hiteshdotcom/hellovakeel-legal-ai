"""Statute / legal-knowledge retrieval.

Complements CorpusRetriever (which searches curated *judgments*). This one
searches the ingested *statutes* — the India Central Acts loaded by
scripts/ingest_all_acts.py into memchat.legal_knowledge(_chunks) — so the
assistant can answer "what does <Act> say about <matter>" from the Act text
itself, with act-name + page provenance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import get_settings
from ..db import LegalKnowledgeStore
from ..llm import LLMClients

logger = logging.getLogger("nyaya.memory.legal_retrieval")


@dataclass
class RetrievedStatute:
    chunk_id: str
    knowledge_id: str
    title: str
    act_name: str = ""
    category: str = ""
    heading: str = ""
    content: str = ""
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
        try:
            emb = await self.llm.embed(query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("statute query embed failed: %s", exc)
            return []
        if not emb:
            return []

        rows = await self.store.search_chunks(query, emb, match_count=max(top_n * 2, 12))
        results: list[RetrievedStatute] = []
        for r in rows:
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
                    content=(r.get("content") or "")[:1600],
                    page_start=r.get("page_start"),
                    page_end=r.get("page_end"),
                    score=float(r.get("rrf_score") or 0.0),
                    metadata=meta,
                )
            )

        # Optional Cohere rerank for precision; else keep RRF order.
        if self.settings.use_cohere and results:
            docs = [f"{r.act_name}. {r.heading}. {r.content[:800]}" for r in results]
            try:
                order = await self.llm.rerank(query, docs, top_n)
                results = [results[i] for i in order if i < len(results)]
            except Exception as exc:  # noqa: BLE001
                logger.warning("statute rerank failed: %s", exc)
                results = results[:top_n]
        else:
            results = results[:top_n]

        return results
