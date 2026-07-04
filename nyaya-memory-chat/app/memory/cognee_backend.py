"""Real Cognee 1.0 memory backend.

  * Per-user chat memory      -> cognee.remember() / cognee.recall()
                                 (session_id for fast cache + per-user dataset for
                                  durable cross-session recall).
  * Corpus knowledge graph    -> low-level Tasks -> Pipeline -> DataPoints, so our
                                 curated citation edges land as real graph edges.

The high-level remember/recall surface is 1.0+. Older installs only expose
add/cognify/search — every call is wrapped so we degrade gracefully and the
structured UI panels keep working via a mirrored LocalBackend.

API symbol names shift between versions; everything is looked up defensively.
"""
from __future__ import annotations

import inspect
import logging
import re
from typing import Any, Optional

from ..config import Settings
from ..db import Database
from ..llm import LLMClients
from .base import CorpusHit, MemoryBackend, MemoryView
from .corpus_graph import build_judgment_datapoints
from .local_backend import LocalBackend

logger = logging.getLogger("nyaya.memory.cognee")

_JID_RE = re.compile(r"JUDGMENT_ID\s*[:=]\s*([0-9a-f\-]{8,})", re.I)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _drain(gen):
    """Run an async generator (pipeline) to completion."""
    out = []
    async for item in gen:
        out.append(item)
    return out


class CogneeBackend(MemoryBackend):
    name = "cognee"

    def __init__(self, db: Database, llm: LLMClients, settings: Settings):
        self.db = db
        self.llm = llm
        self.settings = settings
        # Structured mirror for the UI memory panel + user graph + a recall
        # backstop. Cognee remains the intelligent layer on top.
        self._mirror = LocalBackend(db, llm)
        import cognee  # noqa: F401 - validated by the factory before we get here

        self.cognee = cognee

    # ------------------------------------------------------------------ #
    #  per-user memory
    # ------------------------------------------------------------------ #
    async def remember_turn(
        self, user_id: str, session_id: str, role: str, content: str
    ) -> list[dict[str, Any]]:
        facts = await self._mirror.remember_turn(user_id, session_id, role, content)
        text = f"{role}: {content}"
        sid = f"{user_id}:{session_id}"
        # Fast session memory (remember runs add+cognify+improve in the bg).
        await self._call_remember(text, session_id=sid)
        # Durable per-user partition for cross-session recall.
        await self._call_remember(content, dataset=f"user_{user_id}")
        return facts

    async def recall_user(
        self, user_id: str, session_id: str, query: str, k: int = 8
    ) -> list[str]:
        sid = f"{user_id}:{session_id}"
        hits = await self._call_recall(query, session_id=sid)
        if len(hits) < 2:
            hits += await self._call_recall(query, dataset=f"user_{user_id}")
        out = _dedup_strings(hits)
        # Backstop with the structured mirror so memory is never silently empty.
        if len(out) < 2:
            out = _dedup_strings(out + await self._mirror.recall_user(user_id, session_id, query, k))
        return out[:k]

    async def memory_view(self, user_id: str) -> MemoryView:
        return await self._mirror.memory_view(user_id)

    async def user_graph(self, user_id: str) -> dict[str, Any]:
        return await self._mirror.user_graph(user_id)

    # ------------------------------------------------------------------ #
    #  corpus
    # ------------------------------------------------------------------ #
    async def recall_corpus(self, query: str, k: int = 10) -> list[CorpusHit]:
        results = await self._call_search(query, dataset="corpus")
        hits: list[CorpusHit] = []
        seen: set[str] = set()
        for r in results:
            text = r if isinstance(r, str) else str(r)
            for m in _JID_RE.finditer(text):
                jid = m.group(1)
                if jid not in seen:
                    seen.add(jid)
                    hits.append(CorpusHit(judgment_id=jid, snippet=text[:240], source="memory"))
            # Some versions return dicts with a judgment_id field.
            if isinstance(r, dict):
                jid = str(r.get("judgment_id") or r.get("id") or "")
                if jid and jid not in seen:
                    seen.add(jid)
                    hits.append(CorpusHit(judgment_id=jid, snippet=str(r)[:240], source="memory"))
        return hits[:k]

    async def add_corpus_judgments(self, judgments: list[dict[str, Any]]) -> int:
        """Low-level Tasks -> Pipeline -> DataPoints ingestion with curated cites."""
        if not judgments:
            return 0
        from ..db import get_repo

        ids = [str(j["judgment_id"]) for j in judgments]
        cit_rows = await get_repo().all_citations(ids)
        by_citing: dict[str, list[dict]] = {}
        for c in cit_rows:
            by_citing.setdefault(str(c["citing_id"]), []).append(c)

        try:
            from cognee.modules.pipelines import Task, run_pipeline
            from cognee.tasks.storage import add_data_points
        except Exception as exc:  # noqa: BLE001
            logger.error("Cognee low-level pipeline imports failed: %s", exc)
            raise

        async def rows_to_points(batch: list[dict]):
            return build_judgment_datapoints(batch, by_citing)

        # Chunk into ~20-judgment batches.
        batches = [judgments[i : i + 20] for i in range(0, len(judgments), 20)]
        await _drain(
            run_pipeline(
                tasks=[Task(rows_to_points), Task(add_data_points)],
                data=batches,
                datasets=["corpus"],
                pipeline_name="judgment_graph_pipeline",
                use_pipeline_cache=False,
            )
        )
        return len(judgments)

    # ------------------------------------------------------------------ #
    #  defensive cognee call wrappers
    # ------------------------------------------------------------------ #
    async def _call_remember(self, text: str, **kwargs) -> None:
        fn = getattr(self.cognee, "remember", None)
        if callable(fn):
            try:
                await _maybe_await(_filter_call(fn, text, **kwargs))
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("cognee.remember failed (%s) — trying add/cognify.", exc)
        # Fallback: add + cognify.
        try:
            add = getattr(self.cognee, "add")
            dataset = kwargs.get("dataset") or "default"
            await _maybe_await(_filter_call(add, text, dataset_name=dataset))
            cognify = getattr(self.cognee, "cognify")
            await _maybe_await(_filter_call(cognify, datasets=[dataset]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("cognee add/cognify fallback failed: %s", exc)

    async def _call_recall(self, query: str, **kwargs) -> list[str]:
        fn = getattr(self.cognee, "recall", None)
        if callable(fn):
            try:
                res = await _maybe_await(_filter_call(fn, query, **kwargs))
                return _as_strings(res)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cognee.recall failed (%s) — trying search.", exc)
        return await self._call_search(query, **kwargs)

    async def _call_search(self, query: str, **kwargs) -> list[str]:
        fn = getattr(self.cognee, "search", None)
        if not callable(fn):
            return []
        try:
            res = await _maybe_await(_filter_call(fn, query_text=query, **kwargs))
            return _as_strings(res)
        except TypeError:
            try:
                res = await _maybe_await(_filter_call(fn, query, **kwargs))
                return _as_strings(res)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cognee.search failed: %s", exc)
                return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("cognee.search failed: %s", exc)
            return []


def _filter_call(fn, *args, **kwargs):
    """Call fn keeping only kwargs it actually accepts (handles version drift in
    remember/recall/search signatures: dataset vs dataset_name vs datasets)."""
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            kwargs = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        pass
    return fn(*args, **kwargs)


def _as_strings(res: Any) -> list[str]:
    if res is None:
        return []
    if isinstance(res, str):
        return [res]
    if isinstance(res, dict):
        return [str(res.get("text") or res.get("content") or res)]
    if isinstance(res, (list, tuple)):
        out = []
        for r in res:
            if isinstance(r, str):
                out.append(r)
            elif isinstance(r, dict):
                out.append(str(r.get("text") or r.get("content") or r))
            else:
                out.append(str(r))
        return out
    return [str(res)]


def _dedup_strings(items: list[str]) -> list[str]:
    seen, out = set(), []
    for s in items:
        key = s.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(s.strip())
    return out
