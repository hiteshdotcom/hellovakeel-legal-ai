"""LLM + embedding clients.

Two distinct LLM roles, kept strictly separate (per the build spec):
  * Cognee's *internal* extraction LLM   -> OpenAI (configured in cognee_setup).
  * The user-facing *legal answer*        -> Claude (claude-opus-4-8), HERE.
  * Cheap query-rewrite / fact-extraction -> Claude sonnet OR OpenAI gpt-4o-mini.

Embeddings: OpenAI text-embedding-3-large @ 1536 dims (matches judgment_vectors).
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

from .config import Settings, get_settings

logger = logging.getLogger("nyaya.llm")


class LLMClients:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._anthropic = None
        self._openai = None
        self._cohere = None

    # ----- lazy clients -----
    @property
    def anthropic(self):
        if self._anthropic is None:
            from anthropic import AsyncAnthropic

            self._anthropic = AsyncAnthropic(api_key=self.settings.ANTHROPIC_API_KEY)
        return self._anthropic

    @property
    def openai(self):
        if self._openai is None:
            from openai import AsyncOpenAI

            self._openai = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)
        return self._openai

    @property
    def cohere(self):
        if self._cohere is None and self.settings.use_cohere:
            import cohere

            self._cohere = cohere.AsyncClientV2(api_key=self.settings.COHERE_API_KEY)
        return self._cohere

    # ----- embeddings -----
    async def embed(self, text: str) -> list[float]:
        """Single-text embedding @ 1536 dims."""
        out = await self.embed_many([text])
        return out[0] if out else []

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.settings.OPENAI_API_KEY:
            logger.warning("No OPENAI_API_KEY — returning empty embeddings.")
            return [[] for _ in texts]
        resp = await self.openai.embeddings.create(
            model=self.settings.EMBEDDING_MODEL,
            input=texts,
            dimensions=self.settings.EMBEDDING_DIMENSIONS,
        )
        return [d.embedding for d in resp.data]

    # ----- Claude: the user-facing legal answer (streaming) -----
    async def stream_answer(
        self, system: str, user_prompt: str, max_tokens: int = 1500
    ) -> AsyncIterator[str]:
        """Stream the final legal answer from claude-opus-4-8."""
        async with self.anthropic.messages.stream(
            model=self.settings.CLAUDE_MODEL_REASONING,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def complete(
        self, system: str, user_prompt: str, fast: bool = False, max_tokens: int = 1024
    ) -> str:
        model = self.settings.CLAUDE_MODEL_FAST if fast else self.settings.CLAUDE_MODEL_REASONING
        msg = await self.anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")

    # ----- cheap structured JSON (OpenAI gpt-4o-mini, JSON mode) -----
    async def complete_json(self, system: str, user: str, max_tokens: int = 300) -> dict:
        """Fast, cheap structured JSON via gpt-4o-mini. Returns {} on any failure
        so callers can fall back. Used for latency-sensitive helpers like query
        decomposition where a full Claude call would dominate retrieval time."""
        if not self.settings.OPENAI_API_KEY:
            return {}
        try:
            resp = await self.openai.chat.completions.create(
                model=self.settings.LLM_MODEL,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=max_tokens,
            )
            return json.loads(resp.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("complete_json failed: %s", exc)
            return {}

    # ----- cheap structured fact extraction (OpenAI gpt-4o-mini, JSON mode) -----
    async def extract_facts(self, text: str) -> list[dict]:
        """Pull durable, user-specific facts out of a user message. Returns a
        list of {fact, category}. Falls back to [] on any error (caller may use
        a heuristic instead)."""
        if not self.settings.OPENAI_API_KEY:
            return []
        sys = (
            "You extract durable, user-specific facts from a legal client's message "
            "so an assistant can remember them across future sessions. Only extract "
            "facts about the USER's own matter/situation/preferences — never general "
            "legal knowledge. Categories: Matter, Money, Jurisdiction, Status, Parties, "
            "Research, Preference. Return JSON: {\"facts\":[{\"fact\":\"...\",\"category\":\"...\"}]}. "
            "If there is nothing durable to remember, return {\"facts\":[]}."
        )
        try:
            resp = await self.openai.chat.completions.create(
                model=self.settings.LLM_MODEL,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": sys},
                    {"role": "user", "content": text},
                ],
                temperature=0,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            facts = data.get("facts", [])
            return [
                {"fact": f["fact"].strip(), "category": f.get("category", "Matter")}
                for f in facts
                if isinstance(f, dict) and f.get("fact")
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("extract_facts failed: %s", exc)
            return []

    async def rerank(self, query: str, docs: list[str], top_n: int) -> list[int]:
        """Return indices of the top_n docs (Cohere rerank). Identity order on
        failure / when Cohere is disabled."""
        if not self.cohere or not docs:
            return list(range(min(top_n, len(docs))))
        try:
            resp = await self.cohere.rerank(
                model="rerank-english-v3.0",
                query=query,
                documents=docs,
                top_n=min(top_n, len(docs)),
            )
            return [r.index for r in resp.results]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cohere rerank failed: %s", exc)
            return list(range(min(top_n, len(docs))))


_clients: Optional[LLMClients] = None


def get_llm() -> LLMClients:
    global _clients
    if _clients is None:
        _clients = LLMClients()
    return _clients
