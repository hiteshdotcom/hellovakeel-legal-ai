"""Corpus retrieval: gather grounding context for a research query.

Combines two retrievers and unions their judgment_ids:
  1. Cognee graph recall over the `corpus` partition (graph+vector auto-routed).
  2. The existing hybrid_search RPC (vector + BM25 + RRF) straight against
     judgment_vectors — a cross-check that always works.
Then fetches full metadata, optionally Cohere-reranks, and keeps the top N.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import get_settings
from ..db import JudgmentRepo
from ..llm import LLMClients
from . import user_memory

logger = logging.getLogger("nyaya.memory.retrieval")


@dataclass
class RetrievedJudgment:
    judgment_id: str
    case_title: str = ""
    citation: Optional[str] = None
    neutral_citation: Optional[str] = None
    court: str = ""
    judgment_date: Optional[str] = None
    ratio_decidendi: str = ""
    headnotes: str = ""
    still_good_law: bool = True
    score: float = 0.0
    sources: list[str] = field(default_factory=list)  # which retriever(s) found it

    @property
    def court_date(self) -> str:
        bits = [b for b in [self.court, _year(self.judgment_date)] if b]
        return " · ".join(bits)

    def as_dict(self) -> dict[str, Any]:
        return {
            "judgment_id": self.judgment_id,
            "case_title": self.case_title,
            "citation": self.citation,
            "neutral_citation": self.neutral_citation,
            "court": self.court,
            "courtdate": self.court_date,
            "judgment_date": self.judgment_date,
            "ratio_decidendi": self.ratio_decidendi,
            "ratio": self.ratio_decidendi,
            "still_good_law": self.still_good_law,
            "good": self.still_good_law,
        }


def _year(d: Optional[str]) -> str:
    return d[:4] if d and len(d) >= 4 else ""


def _still_good_law(meta: dict[str, Any]) -> bool:
    cls = meta.get("current_law_status")
    if isinstance(cls, dict):
        return bool(cls.get("ratio_still_good_law", True))
    return True


def _court(meta: dict[str, Any]) -> str:
    return meta.get("court_name") or meta.get("court_level") or meta.get("court_type") or ""


class CorpusRetriever:
    def __init__(self, repo: JudgmentRepo, llm: LLMClients):
        self.repo = repo
        self.llm = llm
        self.settings = get_settings()

    async def retrieve(self, query: str, top_n: Optional[int] = None) -> list[RetrievedJudgment]:
        top_n = top_n or self.settings.RERANK_TOP_N
        id_sources: dict[str, set[str]] = {}

        # 1) Cognee graph recall over the corpus partition.
        try:
            for hit in await user_memory.recall_corpus(query, k=self.settings.HYBRID_SEARCH_LIMIT):
                id_sources.setdefault(hit.judgment_id, set()).add("memory")
        except Exception as exc:  # noqa: BLE001
            logger.warning("corpus memory recall failed: %s", exc)

        # 2) hybrid_search RPC cross-check (embed the query first).
        try:
            emb = await self.llm.embed(query)
            if emb:
                rows = await self.repo.hybrid_retrieve(
                    query, emb, match_count=self.settings.HYBRID_SEARCH_LIMIT
                )
                for r in rows:
                    jid = str(r.get("judgment_id") or r.get("id") or "")
                    if jid:
                        id_sources.setdefault(jid, set()).add("hybrid")
        except Exception as exc:  # noqa: BLE001
            logger.warning("hybrid_search failed: %s", exc)

        if not id_sources:
            return []

        # 3) fetch full metadata.
        metas = await self.repo.get_metadata(list(id_sources.keys()))
        by_id = {str(m["judgment_id"]): m for m in metas}

        results: list[RetrievedJudgment] = []
        for jid, srcs in id_sources.items():
            m = by_id.get(jid)
            if not m:
                continue
            results.append(
                RetrievedJudgment(
                    judgment_id=jid,
                    case_title=m.get("case_title", ""),
                    citation=m.get("citation"),
                    neutral_citation=m.get("neutral_citation"),
                    court=_court(m),
                    judgment_date=str(m.get("judgment_date") or "") or None,
                    ratio_decidendi=(m.get("ratio_decidendi") or "")[:600],
                    headnotes=(m.get("headnotes") or "")[:400],
                    still_good_law=_still_good_law(m),
                    sources=sorted(srcs),
                    score=float(len(srcs)),  # found-by-both ranks above found-by-one
                )
            )

        # 4) optional Cohere rerank, else by retriever agreement.
        if self.settings.use_cohere and results:
            docs = [f"{r.case_title}. {r.ratio_decidendi}" for r in results]
            order = await self.llm.rerank(query, docs, top_n)
            results = [results[i] for i in order if i < len(results)]
        else:
            results.sort(key=lambda r: r.score, reverse=True)
            results = results[:top_n]

        return results
