"""Exit 0 iff the OpenAI embeddings endpoint is usable (quota available).

Used by the overnight auto-resume watcher: while the account is out of quota the
embedding call raises 429 (insufficient_quota) and this exits non-zero; once the
user tops up billing it returns a vector and this exits 0, signalling the watcher
to resume the bulk ingest. The probe embeds one 5-char string — negligible cost,
and free while quota is exhausted (the request is rejected before billing).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import get_llm  # noqa: E402


async def main() -> int:
    try:
        vecs = await get_llm().embed_many(["probe"])
    except Exception as exc:  # noqa: BLE001
        print(f"blocked: {str(exc)[:120]}", file=sys.stderr)
        return 1
    return 0 if (vecs and vecs[0]) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
