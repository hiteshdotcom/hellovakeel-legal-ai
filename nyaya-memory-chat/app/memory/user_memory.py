"""High-level per-user memory facade over the active MemoryBackend.

Holds the process-wide backend instance (wired at startup) and exposes the small
surface the chat endpoint needs. `remember_turn` is always safe to call from a
FastAPI BackgroundTask so the chat response never waits on cognify.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..db import Database
from ..llm import LLMClients
from .base import CorpusHit, MemoryBackend, MemoryView
from .cognee_setup import get_memory_backend

logger = logging.getLogger("nyaya.memory.user")

_backend: Optional[MemoryBackend] = None


def init_memory(db: Database, llm: Optional[LLMClients] = None) -> MemoryBackend:
    global _backend
    _backend = get_memory_backend(db, llm)
    return _backend


def get_backend() -> MemoryBackend:
    assert _backend is not None, "init_memory() not called"
    return _backend


async def remember_turn(user_id: str, session_id: str, role: str, content: str) -> list[dict[str, Any]]:
    try:
        return await get_backend().remember_turn(user_id, session_id, role, content)
    except Exception as exc:  # noqa: BLE001 - background task must never crash the app
        logger.warning("remember_turn failed for %s: %s", user_id, exc)
        return []


async def recall_user(user_id: str, session_id: str, query: str, k: int = 8) -> list[str]:
    try:
        return await get_backend().recall_user(user_id, session_id, query, k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recall_user failed for %s: %s", user_id, exc)
        return []


async def memory_view(user_id: str) -> MemoryView:
    return await get_backend().memory_view(user_id)


async def user_graph(user_id: str) -> dict[str, Any]:
    return await get_backend().user_graph(user_id)


async def recall_corpus(query: str, k: int = 10) -> list[CorpusHit]:
    return await get_backend().recall_corpus(query, k)
