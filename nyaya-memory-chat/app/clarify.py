"""Pre-flight clarify gate: decide whether a legal query is answerable as-is, or
whether a CONTROLLING fact is missing and we should ask the user first.

Runs in parallel with retrieval (see app/api/chat.py) so it adds no perceived
latency, and falls back to answering on ANY failure so it never blocks."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("nyaya.clarify")

_CLARIFY_SYSTEM = """You are a triage step for an Indian legal research assistant.
Given the user's question and what is already remembered about their matter,
decide whether the question can be answered precisely as-is, or whether a
CONTROLLING fact is missing without which any answer would be guesswork.

Controlling facts change the governing law or the outcome — e.g. the parties'
personal law (religion) for succession, whether property is owned or tenanted,
whether a registered/attested instrument exists, key dates (death, knowledge,
cause of action), self-acquired vs. ancestral property, or the State whose law
applies for rent/tenancy/land-revenue matters.

Rules:
- Ask ONLY when a controlling fact is genuinely missing AND not already in the
  remembered facts. If the question is answerable, do not ask.
- At most 3 questions. Each question gets 2-4 short answer options ("chips"),
  always including an escape option like "Not sure".
- Keep questions and chips short and plain.

Respond with ONLY a JSON object, no prose, in one of these two shapes:
{"needs": false}
or
{"needs": true,
 "preamble": "To answer precisely I need a couple of details:",
 "questions": [
   {"q": "Whose personal law governs the succession?", "chips": ["Hindu","Muslim","Christian","Not sure"]}
 ]}"""


def _parse_clarify(raw: str) -> dict[str, Any]:
    """Extract the clarify decision from the model's raw text. Any malformed or
    unexpected content collapses to {"needs": False} so the caller answers."""
    if not raw:
        return {"needs": False}
    m = re.search(r"\{.*\}", raw, re.DOTALL)  # first {...} block, tolerating fences/prose
    if not m:
        return {"needs": False}
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return {"needs": False}
    if not isinstance(data, dict) or not data.get("needs"):
        return {"needs": False}
    questions: list[dict[str, Any]] = []
    for q in data.get("questions") or []:
        if not isinstance(q, dict):
            continue
        text = str(q.get("q") or "").strip()
        if not text:
            continue
        chips = [str(c).strip() for c in (q.get("chips") or []) if str(c).strip()]
        questions.append({"q": text, "chips": chips[:4]})
    questions = questions[:3]
    if not questions:
        return {"needs": False}
    preamble = str(
        data.get("preamble") or "To answer precisely, I need a little more context:"
    ).strip()
    return {"needs": True, "preamble": preamble, "questions": questions}


async def clarify_gate(llm: Any, message: str, recalled: list[str]) -> dict[str, Any]:
    """Decide answer-vs-clarify. Falls back to answering on any error."""
    mem = "\n".join(f"- {m}" for m in recalled) if recalled else "- (nothing remembered yet)"
    user = f"REMEMBERED FACTS:\n{mem}\n\nUSER QUESTION:\n{message}\n\nReturn the JSON decision."
    try:
        raw = await llm.complete(_CLARIFY_SYSTEM, user, fast=True, max_tokens=400)
    except Exception as exc:  # noqa: BLE001
        logger.warning("clarify_gate failed, defaulting to answer: %s", exc)
        return {"needs": False}
    return _parse_clarify(raw)


def render_clarify_text(preamble: str, questions: list[dict[str, Any]]) -> str:
    """Render the clarify turn as plain text for history persistence."""
    lines = [preamble]
    for i, q in enumerate(questions, 1):
        chips = q.get("chips") or []
        suffix = f" ({' / '.join(chips)})" if chips else ""
        lines.append(f"{i}. {q.get('q', '')}{suffix}")
    return "\n".join(lines)
