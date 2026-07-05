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

# Recall gate for a user's private matter facts (amount, jurisdiction, parties,
# status, prior queries). A generic legal question — "penalties for cheque
# bounce under Indian law?" — must NOT drag these in, even though it shares the
# topic "cheque bounce" with the user's stored matter. Same topic is not "about
# my case". Matter facts surface only on one of two explicit signals:
#   1. continuity intent — the user is asking about THEIR situation, or
#   2. a distinctive attribute of the matter is named — the state, the amount, a
#      party name (not a generic topic word).
# Preferences are exempt from this gate (see recall_user) and always surface.

# 1) Continuity intent. "my"/"our" covers "my case", "my client", "for my
# matter"; the rest catch follow-ups ("what should I do next", "as I mentioned").
_INTENT_RE = re.compile(
    r"\bmy\b|\bour\b|\bmine\b"
    r"|\bwhat (?:should|do|can|would) i\b|\bwhat'?s next\b|\bnext step"
    r"|\bas i (?:said|mentioned|told|explained)\b|\bwe (?:discussed|talked|spoke)\b"
    r"|\bfor me\b|\bearlier i\b|\blast time\b|\bcontinue\b",
    re.I,
)

# 2) Distinctive tokens: numbers/amounts and proper nouns that identify a
# specific matter — NOT common topic or question words. Overlap on one of these
# means the user named part of their own matter, so recall it.
_NUM_RE = re.compile(r"\d[\d,]{2,}")
_COMMON_CAPS = {
    "what", "when", "where", "which", "who", "why", "how", "should", "would",
    "could", "please", "indian", "india", "under", "section", "act", "law",
    "the", "this", "that", "does", "there", "these", "those", "explain",
}


def _wants_continuity(query: str) -> bool:
    return bool(query and _INTENT_RE.search(query))


def _distinctive_tokens(text: str) -> set[str]:
    """Amounts and proper-noun-ish identifiers in `text`, lowercased. These are
    the words that pin a fact to a specific matter (Maharashtra, 2,00,000, a
    party name) rather than to a legal topic."""
    toks = {m.group(0).replace(",", "") for m in _NUM_RE.finditer(text)}
    for w in re.findall(r"\b[A-Z][a-zA-Z]{3,}\b", text):
        if w.lower() not in _COMMON_CAPS:
            toks.add(w.lower())
    return toks


# Preference / durable-identity detection. These facts describe how the user
# wants answers, or who the user IS — not their case. They are exempt from the
# recall gate (always surface), so we must classify them reliably even when the
# LLM extractor drops them or labels an identity as a case attribute. The regexes
# match both first-person ("I am a lawyer") and the extractor's normalised third
# person ("User is a lawyer"). A matter's own jurisdiction ("the matter is in
# Maharashtra") does NOT match here, so it stays gated.
_STYLE_RE = re.compile(
    r"\b(?:always|never|please|kindly)\b[^.]*\b(?:answer|respond|reply|format|cite|"
    r"citation|bullet|number|numbered|concise|brief|detail|detailed|short|plain|tone|style)"
    r"|\b(?:i|user)\s+(?:prefer|prefers|like|likes|want|wants|expect|expects)\b[^.]*"
    r"\b(?:answer|response|format|cite|citation|bullet|number|concise|brief|detail|tone|style|language)"
    r"|\b(?:answer|respond|reply)\s+in\s+(?:a\s+)?(?:concise|brief|detailed|numbered|bullet|plain|short)",
    re.I,
)
_IDENTITY_RE = re.compile(
    r"\bmy name is\b|\bcall me\b"
    r"|\b(?:i\s*a?m|user is|user's)\s+(?:a\s+|an\s+|the\s+)?(?:lawyer|advocate|attorney|"
    r"counsel|paralegal|law student|solicitor|litigator|judge|in-house)"
    r"|\b(?:i|user)\s+practi[cs]es?\b",
    re.I,
)


def _is_preference(text: str) -> bool:
    return bool(text) and bool(_STYLE_RE.search(text) or _IDENTITY_RE.search(text))

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
        # Promote answer-style / durable-identity facts to 'Preference' so recall
        # always surfaces them, regardless of how the extractor labelled them (it
        # drops some style instructions and files identity under Jurisdiction).
        for f in facts:
            if _is_preference(f["fact"]):
                f["category"] = "Preference"
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
        intent = _wants_continuity(query)
        q_dist = _distinctive_tokens(query)

        scored: list[tuple[float, str, str]] = []
        for r in rows:
            emb = list(r["embedding"]) if r["embedding"] else []
            score = _cosine(q_emb, emb) if q_emb and emb else 0.0
            scored.append((score, r["fact"], (r["category"] or "Matter")))
        # Order by semantic similarity so the most on-point facts come first when
        # we do surface them; the gate below decides *whether* to surface, not the
        # ordering.
        scored.sort(key=lambda x: x[0], reverse=True)

        # Preferences (answer style, who the user is) shape every answer — they
        # are exempt from the gate and always surface.
        prefs = [f for _, f, c in scored if c.lower() == "preference"]

        # Matter facts surface only when the user is asking about THEIR situation
        # (continuity intent) or has named a distinctive attribute of the matter.
        # Bare topical overlap ("cheque bounce") does NOT qualify — that is what
        # kept polluting generic legal questions with the user's private facts.
        matter: list[str] = []
        for _, f, c in scored:
            if c.lower() == "preference":
                continue
            if intent or (q_dist & _distinctive_tokens(f)):
                matter.append(f)

        # Preferences first (they frame the answer), then any relevant matter.
        return (prefs + matter)[:k]

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
