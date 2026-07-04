"""Shared test fixtures.

Grounding tests are pure-Python (no fixtures). Memory tests exercise the real
LocalBackend SQL against the live Postgres (skipped if unreachable) with a
deterministic FakeLLM so they need no OpenAI calls.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Database  # noqa: E402
from app.config import get_settings  # noqa: E402


class FakeLLM:
    """Deterministic stand-in for LLMClients — no network."""

    def __init__(self):
        self.settings = get_settings()

    async def extract_facts(self, text: str) -> list[dict]:
        # Deterministic: one fact per comma-separated clause, capped.
        facts = []
        for clause in [c.strip() for c in text.split(",") if c.strip()]:
            cat = "Matter"
            low = clause.lower()
            if "₹" in clause or "rs" in low or "lakh" in low:
                cat = "Money"
            elif "maharashtra" in low or "delhi" in low or "state" in low:
                cat = "Jurisdiction"
            elif "filed" in low or "no case" in low:
                cat = "Status"
            facts.append({"fact": clause, "category": cat})
        return facts[:6]

    def _vec(self, text: str) -> list[float]:
        # Stable 16-dim "embedding": word-hash bag, so overlap -> higher cosine.
        v = [0.0] * 16
        for w in text.lower().split():
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % 16] += 1.0
        return v

    async def embed(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest_asyncio.fixture
async def db():
    settings = get_settings()
    database = Database(settings)
    await database.connect()
    if not database.available:
        pytest.skip("Postgres not reachable — skipping DB-backed memory tests.")
    yield database
    await database.close()
