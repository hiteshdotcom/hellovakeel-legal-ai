"""Cross-session memory + per-user isolation tests (the headline behaviour).

Exercises the real LocalBackend SQL against the live Postgres with a
deterministic FakeLLM (no OpenAI). Skipped automatically if Postgres is
unreachable.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.memory.local_backend import LocalBackend


async def _clean(db, user_id):
    async with db.pool.acquire() as c:
        await c.execute("DELETE FROM memchat.user_facts WHERE user_id=$1", user_id)
        await c.execute("DELETE FROM memchat.memory_nodes WHERE user_id=$1", user_id)
        await c.execute("DELETE FROM memchat.memory_edges WHERE user_id=$1", user_id)


@pytest.mark.asyncio
async def test_memory_survives_a_new_session(db, fake_llm):
    backend = LocalBackend(db, fake_llm)
    user = f"test_{uuid.uuid4().hex[:8]}"
    await _clean(db, user)
    try:
        # Session A: the user states their matter.
        facts = await backend.remember_turn(
            user, "sessionA", "user",
            "cheque-bounce dispute, ₹2,00,000, Maharashtra, no case filed yet",
        )
        assert facts, "facts should be extracted from the user's message"

        # Brand-new session (different session_id, same user) — facts NOT resent.
        recalled = await backend.recall_user(user, "sessionB_brand_new", "what should I do next?")
        blob = " ".join(recalled).lower()
        assert "maharashtra" in blob
        assert "2,00,000" in blob or "200000" in blob or "₹" in blob
        assert "no case filed yet" in blob
    finally:
        await _clean(db, user)


@pytest.mark.asyncio
async def test_context_stays_bounded(db, fake_llm):
    """Recall returns at most k facts regardless of how much history accrues."""
    backend = LocalBackend(db, fake_llm)
    user = f"test_{uuid.uuid4().hex[:8]}"
    await _clean(db, user)
    try:
        for i in range(15):
            await backend.remember_turn(user, f"s{i}", "user", f"fact number {i} about clause alpha{i}")
        recalled = await backend.recall_user(user, "s_new", "alpha", k=8)
        assert len(recalled) <= 8, "recall must be bounded (context cannot grow unbounded)"
    finally:
        await _clean(db, user)


@pytest.mark.asyncio
async def test_user_isolation(db, fake_llm):
    backend = LocalBackend(db, fake_llm)
    a = f"test_A_{uuid.uuid4().hex[:8]}"
    b = f"test_B_{uuid.uuid4().hex[:8]}"
    await _clean(db, a); await _clean(db, b)
    try:
        await backend.remember_turn(a, "sa", "user", "my secret matter is a cheque dispute in Maharashtra")
        # User B has stored nothing — must recall nothing of A's.
        recalled_b = await backend.recall_user(b, "sb", "cheque dispute Maharashtra")
        assert recalled_b == [], "user B must not see user A's memory"
        # A's own private matter is gated: a bare topical query must NOT surface it.
        generic = await backend.recall_user(a, "sa2", "cheque bounce penalties in India")
        assert generic == [], "a generic topical query must not surface A's private matter"
        # But A still has it when A references the matter (intent / named attribute).
        recalled_a = await backend.recall_user(a, "sa2", "what happened in my Maharashtra matter?")
        assert any("cheque" in r.lower() for r in recalled_a)
    finally:
        await _clean(db, a); await _clean(db, b)


@pytest.mark.asyncio
async def test_memory_view_groups_by_category(db, fake_llm):
    backend = LocalBackend(db, fake_llm)
    user = f"test_{uuid.uuid4().hex[:8]}"
    await _clean(db, user)
    try:
        await backend.remember_turn(
            user, "s1", "user",
            "cheque-bounce dispute, ₹2,00,000, Maharashtra, no case filed yet",
        )
        view = await backend.memory_view(user)
        assert not view.empty
        labels = {g["label"] for g in view.groups}
        assert "Money" in labels and "Jurisdiction" in labels
        graph = await backend.user_graph(user)
        assert any(n["kind"] == "you" for n in graph["nodes"])
        assert graph["edges"], "you -> fact edges should exist"
    finally:
        await _clean(db, user)
