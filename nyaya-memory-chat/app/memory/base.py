"""Memory backend interface + shared data shapes.

A `MemoryBackend` provides two kinds of memory:
  * per-user, cross-session chat memory (remember / recall a client's matter), and
  * corpus recall (graph-grounded judgment ids for a research query).

Two implementations exist:
  * CogneeBackend  — the real Cognee 1.0 hybrid graph+vector memory.
  * LocalBackend   — a Postgres-native (memchat schema) fallback that keeps the
                     service fully runnable and testable without the heavy stack.
Both honour strict per-user isolation.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CorpusHit:
    judgment_id: str
    score: float = 0.0
    snippet: str = ""
    source: str = "memory"  # memory | hybrid


@dataclass
class MemoryView:
    """Shape consumed by the right-rail 'What I remember about you' panel and
    GET /graph/{user_id}."""
    sub: str = ""
    tokens: str = "0"
    empty: bool = True
    groups: list[dict[str, Any]] = field(default_factory=list)  # [{label, facts:[{id,label}]}]


class MemoryBackend(abc.ABC):
    name: str = "base"

    # ---- per-user chat memory ----
    @abc.abstractmethod
    async def remember_turn(
        self, user_id: str, session_id: str, role: str, content: str
    ) -> list[dict[str, Any]]:
        """Persist a turn into per-user durable memory. Returns any newly
        extracted facts (for UI). Must be safe to run in a BackgroundTask."""

    @abc.abstractmethod
    async def recall_user(
        self, user_id: str, session_id: str, query: str, k: int = 8
    ) -> list[str]:
        """Recall the user's relevant remembered facts. Session memory first,
        falling through to the permanent per-user partition. Strictly isolated
        to `user_id`."""

    @abc.abstractmethod
    async def memory_view(self, user_id: str) -> MemoryView:
        """Structured snapshot of everything remembered about a user."""

    @abc.abstractmethod
    async def user_graph(self, user_id: str) -> dict[str, Any]:
        """The user's memory subgraph as {nodes:[...], edges:[...]}."""

    # ---- corpus recall ----
    @abc.abstractmethod
    async def recall_corpus(self, query: str, k: int = 10) -> list[CorpusHit]:
        """Graph+vector grounded judgment ids for a research query. May return
        [] for backends that delegate corpus recall to hybrid_search."""

    @abc.abstractmethod
    async def add_corpus_judgments(self, judgments: list[dict[str, Any]]) -> int:
        """Ingest a batch of judgment metadata dicts into the corpus knowledge
        graph. Returns the number of judgments ingested."""

    async def health(self) -> dict[str, Any]:
        return {"backend": self.name, "ok": True}
