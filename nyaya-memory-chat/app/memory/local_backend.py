"""Postgres-native memory backend (memchat schema).

A faithful, fully-runnable fallback for Cognee's per-user memory:
  * facts are extracted from user turns (LLM with a heuristic safety net),
  * embedded with the SAME OpenAI model (text-embedding-3-large @ 1536), and
  * recalled by cosine similarity — strictly scoped to one user_id.

Cross-session by construction: facts are keyed by user_id, never session_id, so a
brand-new session recalls them. The context window stays bounded because only the
top-k compact facts are ever injected — never the full transcript.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

from ..db import Database
from ..llm import LLMClients
from .base import CorpusHit, MemoryBackend, MemoryView

logger = logging.getLogger("nyaya.memory.local")

_CATEGORY_ORDER = ["Matter", "Money", "Jurisdiction", "Status", "Parties", "Research", "Preference"]

# Indian-state heuristic safety net (used only if the LLM extractor is unavailable).
_STATES = [
    "Maharashtra", "Delhi", "Karnataka", "Tamil Nadu", "Kerala", "Gujarat",
    "Rajasthan", "Punjab", "Haryana", "Uttar Pradesh", "West Bengal", "Bihar",
    "Telangana", "Andhra Pradesh", "Madhya Pradesh", "Odisha", "Assam", "Goa",
]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class LocalBackend(MemoryBackend):
    name = "local"

    def __init__(self, db: Database, llm: LLMClients):
        self.db = db
        self.llm = llm

    # ------------------------------------------------------------------ #
    #  remember
    # ------------------------------------------------------------------ #
    async def remember_turn(
        self, user_id: str, session_id: str, role: str, content: str
    ) -> list[dict[str, Any]]:
        if role != "user" or not content.strip():
            return []  # we only extract durable facts from the user's own words

        facts = await self.llm.extract_facts(content)
        if not facts:
            facts = self._heuristic_facts(content)
        if not facts:
            return []

        # Embed all new facts in one call.
        texts = [f["fact"] for f in facts]
        embeddings = await self.llm.embed_many(texts)
        stored: list[dict[str, Any]] = []
        if not self.db.pool:
            return facts  # degraded: nothing to persist, but report extraction

        async with self.db.pool.acquire() as conn:
            for f, emb in zip(facts, embeddings):
                row = await conn.fetchrow(
                    """
                    INSERT INTO memchat.user_facts (user_id, fact, category, source_session, embedding)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, fact) DO UPDATE SET category = EXCLUDED.category
                    RETURNING id
                    """,
                    user_id, f["fact"], f.get("category", "Matter"), session_id,
                    emb or None,
                )
                fid = f"f_{row['id']}"
                stored.append({"id": fid, "label": f["fact"], "category": f.get("category", "Matter")})
            await self._add_graph_nodes(conn, user_id, stored)
        return stored

    def _heuristic_facts(self, content: str) -> list[dict[str, Any]]:
        """Deterministic safety net so cross-session memory works even with no
        LLM extractor available."""
        facts: list[dict[str, Any]] = []
        # Money (₹ / Rs / lakh)
        m = re.search(r"(₹|rs\.?\s*)\s*[\d,]+(?:\.\d+)?\s*(?:lakh|lakhs|l|cr|crore)?", content, re.I)
        if m:
            facts.append({"fact": m.group(0).strip(), "category": "Money"})
        for st in _STATES:
            if re.search(rf"\b{re.escape(st)}\b", content, re.I):
                facts.append({"fact": st, "category": "Jurisdiction"})
                break
        if re.search(r"no case (filed|registered)|not (yet )?filed|haven'?t filed", content, re.I):
            facts.append({"fact": "No case filed yet", "category": "Status"})
        # Always keep the raw matter line as a fallback durable fact.
        snippet = content.strip()
        if len(snippet) > 160:
            snippet = snippet[:157] + "…"
        facts.append({"fact": snippet, "category": "Matter"})
        # De-dup by fact text.
        seen, out = set(), []
        for f in facts:
            if f["fact"].lower() not in seen:
                seen.add(f["fact"].lower())
                out.append(f)
        return out

    # ------------------------------------------------------------------ #
    #  recall
    # ------------------------------------------------------------------ #
    async def recall_user(
        self, user_id: str, session_id: str, query: str, k: int = 8
    ) -> list[str]:
        if not self.db.pool:
            return []
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT fact, category, embedding FROM memchat.user_facts WHERE user_id = $1",
                user_id,
            )
        if not rows:
            return []

        q_emb = await self.llm.embed(query) if query else []
        scored: list[tuple[float, str]] = []
        for r in rows:
            emb = list(r["embedding"]) if r["embedding"] else []
            score = _cosine(q_emb, emb) if q_emb and emb else 0.0
            # keyword fallback / boost
            if query and any(w in r["fact"].lower() for w in _norm_words(query)):
                score = max(score, 0.55)
            scored.append((score, r["fact"]))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Return the top-k facts by relevance. We deliberately do NOT drop
        # low-scoring facts below a threshold: a user's durable matter facts are
        # few and must surface for continuity even on generic follow-ups
        # ("what should I do next?"). Bounding at k keeps the context window
        # fixed regardless of how long the conversation grows.
        return [f for _, f in scored][:k]

    async def memory_view(self, user_id: str) -> MemoryView:
        if not self.db.pool:
            return MemoryView(empty=True, sub="Memory store unavailable.")
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, fact, category FROM memchat.user_facts WHERE user_id = $1 ORDER BY id",
                user_id,
            )
            sess = await conn.fetchval(
                "SELECT count(DISTINCT source_session) FROM memchat.user_facts WHERE user_id = $1",
                user_id,
            )
        if not rows:
            return MemoryView(empty=True, sub="No memory yet — start chatting.")

        by_cat: dict[str, list[dict]] = {}
        for r in rows:
            by_cat.setdefault(r["category"] or "Matter", []).append(
                {"id": f"f_{r['id']}", "label": r["fact"]}
            )
        groups = [
            {"label": cat, "facts": by_cat[cat]}
            for cat in _CATEGORY_ORDER
            if cat in by_cat
        ] + [
            {"label": cat, "facts": facts}
            for cat, facts in by_cat.items()
            if cat not in _CATEGORY_ORDER
        ]
        # Bounded-context message: facts are compact regardless of history length.
        approx_tokens = sum(len(r["fact"]) for r in rows) // 4
        return MemoryView(
            empty=False,
            sub=f"Remembered across {sess or 1} session(s) · never re-sent",
            tokens=f"{approx_tokens:,}",
            groups=groups,
        )

    async def user_graph(self, user_id: str) -> dict[str, Any]:
        if not self.db.pool:
            return {"nodes": [], "edges": []}
        async with self.db.pool.acquire() as conn:
            nodes = await conn.fetch(
                "SELECT node_id, label, sub, kind FROM memchat.memory_nodes WHERE user_id = $1",
                user_id,
            )
            edges = await conn.fetch(
                "SELECT src, dst FROM memchat.memory_edges WHERE user_id = $1", user_id
            )
        return {
            "nodes": [
                {"id": n["node_id"], "label": n["label"], "sub": n["sub"] or "", "kind": n["kind"]}
                for n in nodes
            ],
            "edges": [{"src": e["src"], "dst": e["dst"]} for e in edges],
        }

    async def _add_graph_nodes(self, conn, user_id: str, stored: list[dict[str, Any]]) -> None:
        """Incrementally add the 'you' root + the newly-stored fact nodes/edges.
        O(new facts) per turn — no full rebuild."""
        await conn.execute(
            "INSERT INTO memchat.memory_nodes (user_id, node_id, label, sub, kind) "
            "VALUES ($1, 'you', 'You', '', 'you') ON CONFLICT DO NOTHING",
            user_id,
        )
        for f in stored:
            nid = f["id"]
            fact = f["label"]
            label = fact if len(fact) <= 18 else fact[:16] + "…"
            await conn.execute(
                "INSERT INTO memchat.memory_nodes (user_id, node_id, label, sub, kind) "
                "VALUES ($1, $2, $3, $4, 'fact') ON CONFLICT DO NOTHING",
                user_id, nid, label, f.get("category", "") or "",
            )
            await conn.execute(
                "INSERT INTO memchat.memory_edges (user_id, src, dst) VALUES ($1, 'you', $2) "
                "ON CONFLICT DO NOTHING",
                user_id, nid,
            )

    # ------------------------------------------------------------------ #
    #  corpus (delegated to hybrid_search by the retrieval layer)
    # ------------------------------------------------------------------ #
    async def recall_corpus(self, query: str, k: int = 10) -> list[CorpusHit]:
        # The local backend does not maintain a separate corpus graph store; the
        # corpus citation graph already lives in public.judgment_citations and
        # retrieval is handled by hybrid_search in corpus_retrieval.py.
        return []

    async def add_corpus_judgments(self, judgments: list[dict[str, Any]]) -> int:
        # No-op for the local backend beyond the ingest-log bookkeeping done by
        # the ingestion script: the corpus graph is the existing Postgres tables.
        return len(judgments)


def _norm_words(q: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", q.lower()).split() if len(w) > 3]
