# Rotating Reassurance Loading + Interactive Clarify Card — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a warm phase-driven "hang tight" loading state and a structured, tappable clarify card that fires when a legal query is missing a controlling fact.

**Architecture:** A fast Claude pre-flight `clarify_gate` runs in parallel with retrieval; if it says a controlling fact is missing, the chat stream emits a `clarify` event (preamble + questions + answer chips) instead of an answer, and the React frontend renders an interactive `ClarifyCard`. The loading state is driven off the real `meta`/`sources` stream events already sent.

**Tech Stack:** FastAPI + asyncpg + Anthropic/OpenAI (backend); React 18 + Vite + TypeScript + Tailwind + framer-motion + Zustand (frontend).

## Global Constraints

- Backend: Python 3.12; models come from `Settings` — reasoning `CLAUDE_MODEL_REASONING` (`claude-opus-4-8`), fast `CLAUDE_MODEL_FAST` (`claude-sonnet-4-6`). The clarify gate MUST use the **fast** model via `llm.complete(..., fast=True)`.
- Backend: the clarify gate MUST fall back to `{"needs": False}` on ANY error, malformed JSON, or empty questions — it must never block the answer path.
- Backend tests: `pytest` from `nyaya-memory-chat/`, `asyncio_mode = auto` (plain `async def test_*` — no `@pytest.mark.asyncio` needed).
- No new dependencies (backend or frontend).
- Frontend: all imports must be used (tsc is strict); remove any import left dangling by an edit. Verify every frontend task with `npm run build` (runs `tsc --noEmit && vite build`) from `nyaya-memory-chat/frontend/`.
- Frontend copy is fixed: loading headlines "Hang tight — recalling your matter…" / "Searching 505 judgments & Central Acts…" / "Grounding every citation, then composing…"; clarify header "A bit more context"; button "Send answers".
- All commands below assume the working directory shown in each step.

---

### Task 1: Backend clarify gate (`app/clarify.py`)

Pure, self-contained module: a parse helper, the async gate, and a history-text renderer. Fully unit-testable with no network.

**Files:**
- Create: `nyaya-memory-chat/app/clarify.py`
- Test: `nyaya-memory-chat/tests/test_clarify.py`

**Interfaces:**
- Produces:
  - `async def clarify_gate(llm, message: str, recalled: list[str]) -> dict` → `{"needs": False}` or `{"needs": True, "preamble": str, "questions": [{"q": str, "chips": [str]}]}`
  - `def _parse_clarify(raw: str) -> dict` (same return shape)
  - `def render_clarify_text(preamble: str, questions: list[dict]) -> str`
- Consumes: an `llm` object exposing `async complete(system, user, fast=True, max_tokens=...) -> str` (satisfied by `app.llm.LLMClients`).

- [ ] **Step 1: Write the failing tests**

Create `nyaya-memory-chat/tests/test_clarify.py`:

```python
from app.clarify import _parse_clarify, clarify_gate, render_clarify_text


def test_parse_clarify_false_on_plain_false():
    assert _parse_clarify('{"needs": false}') == {"needs": False}


def test_parse_clarify_extracts_and_caps():
    raw = """```json
    {"needs": true, "preamble": "Need details:",
     "questions": [
       {"q": "Personal law?", "chips": ["Hindu","Muslim","Christian","Sikh","Other"]},
       {"q": "Owned or tenanted?", "chips": ["Owned","Tenanted"]},
       {"q": "Registered deed?", "chips": ["Yes","No"]},
       {"q": "extra fourth?", "chips": ["a"]}
     ]}
    ```"""
    out = _parse_clarify(raw)
    assert out["needs"] is True
    assert out["preamble"] == "Need details:"
    assert len(out["questions"]) == 3                      # capped at 3 questions
    assert out["questions"][0]["chips"] == ["Hindu", "Muslim", "Christian", "Sikh"]  # capped at 4 chips


def test_parse_clarify_malformed_falls_back():
    assert _parse_clarify("not json at all") == {"needs": False}
    assert _parse_clarify('{"needs": true, "questions": "oops"}') == {"needs": False}
    assert _parse_clarify("") == {"needs": False}


async def test_clarify_gate_swallows_llm_error():
    class Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("api down")

    assert await clarify_gate(Boom(), "my father died", []) == {"needs": False}


async def test_clarify_gate_parses_needs():
    class Fake:
        async def complete(self, *a, **k):
            return '{"needs": true, "preamble": "P", "questions": [{"q": "Q?", "chips": ["A", "Not sure"]}]}'

    out = await clarify_gate(Fake(), "q", [])
    assert out["needs"] is True
    assert out["questions"][0]["q"] == "Q?"
    assert out["questions"][0]["chips"] == ["A", "Not sure"]


def test_render_clarify_text():
    txt = render_clarify_text("Need details:", [{"q": "Personal law?", "chips": ["Hindu", "Muslim"]}])
    assert txt.startswith("Need details:")
    assert "1. Personal law? (Hindu / Muslim)" in txt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `nyaya-memory-chat/`): `pytest tests/test_clarify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.clarify'`.

- [ ] **Step 3: Write the implementation**

Create `nyaya-memory-chat/app/clarify.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `nyaya-memory-chat/`): `pytest tests/test_clarify.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd nyaya-memory-chat
git add app/clarify.py tests/test_clarify.py
git commit -m "feat(clarify): fast pre-flight gate deciding answer-vs-clarify"
```

---

### Task 2: Wire the clarify gate into the chat stream (`app/api/chat.py`)

Run the gate in parallel with retrieval; when it fires, emit a `clarify` event, persist the turn, and stop before composing.

**Files:**
- Modify: `nyaya-memory-chat/app/api/chat.py` (imports near line 32; `event_stream` body lines 89-171)

**Interfaces:**
- Consumes: `clarify_gate`, `render_clarify_text` from Task 1.
- Produces: a new NDJSON event `{"type": "clarify", "preamble": str, "questions": [{"q": str, "chips": [str]}]}` on `POST /api/chat`.

- [ ] **Step 1: Add the import**

In `nyaya-memory-chat/app/api/chat.py`, add after the existing `from ..prompts import SYSTEM, build_user_prompt` line (line 32):

```python
from ..clarify import clarify_gate, render_clarify_text
```

- [ ] **Step 2: Run the gate in parallel with retrieval**

Replace the retrieval gather block (currently lines 102-109):

```python
        # Judgments (curated corpus) + statutes (ingested Central Acts) in parallel.
        retrieved, statutes = await asyncio.gather(
            retriever.retrieve(retrieval_query),
            statute_retriever.retrieve(retrieval_query),
        )
        # Dedup judgments by id — the same case must never appear twice in the
        # source list (it read as broken citation hygiene: "1 and 4 are the same
        # case, one tagged Good law and its duplicate Unverified").
        retrieved = _dedup_judgments(retrieved)
```

with (the clarify gate joins the same gather — zero added wall-clock):

```python
        # Judgments + statutes retrieval AND the clarify gate all run in parallel,
        # so the gate adds no perceived latency. The gate decides, from the bare
        # question + remembered facts, whether a controlling fact is missing.
        retrieved, statutes, clarify = await asyncio.gather(
            retriever.retrieve(retrieval_query),
            statute_retriever.retrieve(retrieval_query),
            clarify_gate(llm, message, recalled),
        )
        # Dedup judgments by id — the same case must never appear twice in the
        # source list (it read as broken citation hygiene: "1 and 4 are the same
        # case, one tagged Good law and its duplicate Unverified").
        retrieved = _dedup_judgments(retrieved)

        # If a controlling fact is missing, ask instead of answering. Emit the
        # structured clarify card, persist the turn as text, and stop — no
        # sources, no tokens, no grounding.
        if clarify.get("needs"):
            preamble = clarify["preamble"]
            questions = clarify["questions"]
            yield _ndjson(
                {"type": "clarify", "preamble": preamble, "questions": questions}
            )
            await store.ensure_session(session_id, user_id, title=_title_from(message))
            uid_msg = f"m_{uuid.uuid4().hex[:12]}"
            aid_msg = f"m_{uuid.uuid4().hex[:12]}"
            await store.save_message(uid_msg, session_id, user_id, "user", message)
            await store.save_message(
                aid_msg, session_id, user_id, "assistant",
                render_clarify_text(preamble, questions),
            )
            background.add_task(
                user_memory.remember_turn, user_id, session_id, "user", message
            )
            yield _ndjson({"type": "done"})
            return
```

- [ ] **Step 3: Verify the module imports and existing tests still pass**

Run (from `nyaya-memory-chat/`):
```bash
python -c "import app.api.chat"
pytest -q
```
Expected: the import prints nothing (success); `pytest` shows the same pass/skip result as before plus Task 1's 6 passing tests (no failures).

- [ ] **Step 4: Manual smoke (optional but recommended)**

Start the app (`uvicorn app.main:app --reload --port 8000`), open the UI, sign in, and send a deliberately thin query like `my father passed away, what happens to the house?`. Confirm the server logs stream a `clarify` line rather than an answer. (Full UI rendering is verified in Task 5.)

- [ ] **Step 5: Commit**

```bash
cd nyaya-memory-chat
git add app/api/chat.py
git commit -m "feat(chat): emit clarify event when a controlling fact is missing"
```

---

### Task 3: Frontend streaming types + chat store (`types.ts`, `store/chat.ts`)

Add the `clarify` event to the type union and teach the store to track pipeline `phase` and store a clarify payload on the assistant message.

**Files:**
- Modify: `nyaya-memory-chat/frontend/src/lib/types.ts` (after `DoneEvent`, ~line 89; union ~lines 95-101)
- Modify: `nyaya-memory-chat/frontend/src/store/chat.ts` (`UIMessage` lines 5-14; `openSession` map lines 63-74; message creation lines 95-114; event handlers lines 131-157)

**Interfaces:**
- Produces (consumed by Tasks 4 & 5):
  - `ClarifyQuestion = { q: string; chips: string[] }`
  - `ClarifyEvent = { type: "clarify"; preamble: string; questions: ClarifyQuestion[] }`
  - `UIMessage.phase: "recall" | "retrieve" | "compose"`
  - `UIMessage.clarify?: { preamble: string; questions: ClarifyQuestion[] }`

- [ ] **Step 1: Add the clarify event type**

In `types.ts`, insert after the `DoneEvent` interface (line 89):

```ts
export interface ClarifyQuestion {
  q: string;
  chips: string[];
}
export interface ClarifyEvent {
  type: "clarify";
  preamble: string;
  questions: ClarifyQuestion[];
}
```

Then add `ClarifyEvent` to the `ChatEvent` union (the block at lines 95-101):

```ts
export type ChatEvent =
  | MetaEvent
  | SourcesEvent
  | TokenEvent
  | FinalEvent
  | DoneEvent
  | SourceEvent
  | ClarifyEvent;
```

- [ ] **Step 2: Extend `UIMessage` in the store**

In `store/chat.ts`, add the import of the question type to the existing type import (line 3):

```ts
import type { ClarifyQuestion, SessionRow, Source, Statute, Warning } from "@/lib/types";
```

Extend the `UIMessage` interface (lines 5-14) — add two fields:

```ts
export interface UIMessage {
  role: "user" | "assistant";
  text: string;
  recalled: string[];
  thinking: boolean;
  done: boolean;
  sources: Source[];
  warnings: Warning[];
  verified: string[];
  phase: "recall" | "retrieve" | "compose";
  clarify?: { preamble: string; questions: ClarifyQuestion[] };
}
```

- [ ] **Step 3: Set `phase` on every constructed message**

In `openSession`, the `.map(...)` that builds history messages (lines 65-74) must include `phase`. Add `phase: "compose",` to the returned object (alongside `done: true`).

In `send`, the `user` message object (lines 95-104) add `phase: "recall",`; the `asst` message object (lines 105-114) add `phase: "recall",`.

- [ ] **Step 4: Advance `phase` and handle the clarify event**

In `send`'s event handler (lines 131-157):

Change the `meta` branch to set phase:

```ts
        if (ev.type === "meta") {
          set({ sessionId: ev.session_id });
          patchAsst((m) => {
            m.recalled = ev.recalled || [];
            m.thinking = true;
            m.phase = "retrieve";
          });
        } else if (ev.type === "sources") {
          set({ sources: ev.sources || [], statutes: ev.statutes || [] });
          patchAsst((m) => {
            m.phase = "compose";
          });
        } else if (ev.type === "clarify") {
          patchAsst((m) => {
            m.thinking = false;
            m.done = true;
            m.clarify = { preamble: ev.preamble, questions: ev.questions };
          });
        } else if (ev.type === "token") {
```

(Leave the existing `token` and `final` branches unchanged; the `else if (ev.type === "token")` line above simply reconnects to the current `token` block.)

- [ ] **Step 5: Verify the build**

Run (from `nyaya-memory-chat/frontend/`): `npm run build`
Expected: `tsc --noEmit` passes and `vite build` completes with no type errors.

- [ ] **Step 6: Commit**

```bash
cd nyaya-memory-chat/frontend
git add src/lib/types.ts src/store/chat.ts
git commit -m "feat(chat-store): track pipeline phase and clarify payload"
```

---

### Task 4: Rotating reassurance loading (`ThinkingIndicator.tsx`, `Message.tsx`)

Replace the static "Searching the judgment graph" block with a phase-driven, cross-fading indicator plus a rotating reassurance line.

**Files:**
- Create: `nyaya-memory-chat/frontend/src/components/chat/ThinkingIndicator.tsx`
- Modify: `nyaya-memory-chat/frontend/src/components/chat/Message.tsx` (imports lines 1-9; thinking block lines 45-64)

**Interfaces:**
- Consumes: `UIMessage.phase` (Task 3).
- Produces: `export default function ThinkingIndicator({ phase }: { phase: "recall" | "retrieve" | "compose" })`.

- [ ] **Step 1: Create the component**

Create `nyaya-memory-chat/frontend/src/components/chat/ThinkingIndicator.tsx`:

```tsx
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Brain, Search, Shield, type IconType } from "@/lib/icons";
import { SkeletonLines } from "@/components/ui";

type Phase = "recall" | "retrieve" | "compose";

const HEAD: Record<Phase, { icon: IconType; text: string }> = {
  recall: { icon: Brain, text: "Hang tight — recalling your matter…" },
  retrieve: { icon: Search, text: "Searching 505 judgments & Central Acts…" },
  compose: { icon: Shield, text: "Grounding every citation, then composing…" },
};

const REASSURANCE = [
  "I verify every citation before it reaches you.",
  "Answers come only from real judgments — nothing invented.",
  "This usually takes a few seconds.",
];

export default function ThinkingIndicator({ phase }: { phase: Phase }) {
  const head = HEAD[phase] ?? HEAD.recall;
  const Icon = head.icon;
  const [tip, setTip] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTip((t) => (t + 1) % REASSURANCE.length), 2600);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex flex-col gap-3">
      <AnimatePresence mode="wait">
        <motion.div
          key={phase}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.2 }}
          className="flex items-center gap-2.5 text-[13.5px] font-medium text-ink-2"
        >
          <Icon size={15} className="text-accent-ink" />
          {head.text}
          <span className="inline-flex gap-1">
            {[0, 0.2, 0.4].map((d) => (
              <span
                key={d}
                className="h-1 w-1 rounded-full bg-ink-3"
                style={{ animation: `1.2s ${d}s infinite dot` }}
              />
            ))}
          </span>
        </motion.div>
      </AnimatePresence>

      <AnimatePresence mode="wait">
        <motion.div
          key={tip}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
          className="text-[12px] text-ink-3"
        >
          {REASSURANCE[tip]}
        </motion.div>
      </AnimatePresence>

      <div className="mt-0.5">
        <SkeletonLines />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Use it in `Message.tsx` and drop now-unused imports**

In `Message.tsx`, replace the thinking block (lines 45-64):

```tsx
      {m.thinking && (
        <>
          <div className="flex items-center gap-2.5 text-[13.5px] text-ink-3">
            <Search size={15} className="text-accent-ink" />
            Searching the judgment graph
            <span className="inline-flex gap-1">
              {[0, 0.2, 0.4].map((d) => (
                <span
                  key={d}
                  className="h-1 w-1 rounded-full bg-ink-3"
                  style={{ animation: `1.2s ${d}s infinite dot` }}
                />
              ))}
            </span>
          </div>
          <div className="mt-1.5">
            <SkeletonLines />
          </div>
        </>
      )}
```

with:

```tsx
      {m.thinking && <ThinkingIndicator phase={m.phase} />}
```

Then fix the imports. Add the new import near the top (after the `Answer` import, line 6):

```tsx
import ThinkingIndicator from "./ThinkingIndicator";
```

`Search` and `SkeletonLines` are now unused in `Message.tsx`. Update line 8 to drop `Search`:

```tsx
import { Brain, Check, Warning, Copy, ThumbsUp, ThumbsDown, CaretRight } from "@/lib/icons";
```

and delete the `import { SkeletonLines } from "@/components/ui";` line (line 9).

- [ ] **Step 3: Verify the build**

Run (from `nyaya-memory-chat/frontend/`): `npm run build`
Expected: passes with no "declared but never read" errors for `Search`/`SkeletonLines`.

- [ ] **Step 4: Commit**

```bash
cd nyaya-memory-chat/frontend
git add src/components/chat/ThinkingIndicator.tsx src/components/chat/Message.tsx
git commit -m "feat(chat): phase-driven rotating reassurance loading"
```

---

### Task 5: Interactive clarify card (`ClarifyCard.tsx`, `Message.tsx`, `icons.tsx`)

Render the clarify payload as a distinct card with per-question chip groups and a "Send answers" button that composes one follow-up turn.

**Files:**
- Modify: `nyaya-memory-chat/frontend/src/lib/icons.tsx` (export list, add `Question`)
- Create: `nyaya-memory-chat/frontend/src/components/chat/ClarifyCard.tsx`
- Modify: `nyaya-memory-chat/frontend/src/components/chat/Message.tsx` (assistant render branch lines 66-76)

**Interfaces:**
- Consumes: `UIMessage.clarify` (Task 3), `ClarifyQuestion` (Task 3), `useChat().send`, `useAuth().user`.
- Produces: `export default function ClarifyCard({ preamble, questions }: { preamble: string; questions: ClarifyQuestion[] })`.

- [ ] **Step 1: Export the `Question` icon**

In `icons.tsx`, add `Question,` to the Phosphor re-export block (e.g. after `MagnifyingGlass as Search,` on line 6):

```tsx
  Question,
```

- [ ] **Step 2: Create the ClarifyCard**

Create `nyaya-memory-chat/frontend/src/components/chat/ClarifyCard.tsx`:

```tsx
import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { useAuth } from "@/store/auth";
import { useChat } from "@/store/chat";
import type { ClarifyQuestion } from "@/lib/types";
import { Question, Send } from "@/lib/icons";
import { cn } from "@/lib/cn";

function stripQ(q: string): string {
  return q.replace(/\?+\s*$/, "").trim();
}

export default function ClarifyCard({
  preamble,
  questions,
}: {
  preamble: string;
  questions: ClarifyQuestion[];
}) {
  const user = useAuth((a) => a.user);
  const { send, streaming } = useChat();
  const [picked, setPicked] = useState<Record<number, string>>({});

  const composed = useMemo(
    () =>
      questions
        .map((q, i) => (picked[i] ? `${stripQ(q.q)}: ${picked[i]}` : null))
        .filter(Boolean)
        .join(". "),
    [picked, questions],
  );
  const canSend = composed.length > 0 && !streaming && !!user;

  function sendAnswers() {
    if (!canSend || !user) return;
    void send(user.id, composed + ".");
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="flex flex-col gap-3.5"
    >
      <div className="inline-flex items-center gap-2 self-start rounded-full border border-[color:var(--gold)]/40 bg-gold-soft px-2.5 py-1">
        <Question size={14} weight="bold" className="text-[color:var(--gold)]" />
        <span className="text-[11.5px] font-bold text-[color:var(--gold)]">A bit more context</span>
      </div>

      <div className="text-[14.5px] leading-relaxed text-ink">{preamble}</div>

      <div className="flex flex-col gap-3">
        {questions.map((q, i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="text-[13.5px] font-semibold text-ink-2">{q.q}</div>
            <div className="flex flex-wrap gap-1.5">
              {q.chips.map((c) => {
                const active = picked[i] === c;
                return (
                  <button
                    key={c}
                    onClick={() => setPicked((p) => ({ ...p, [i]: active ? "" : c }))}
                    className={cn(
                      "rounded-full border px-3 py-1.5 text-[12.5px] font-semibold transition-colors",
                      active
                        ? "border-navy bg-navy text-white"
                        : "border-divider bg-canvas text-ink-2 hover:border-navy/40 hover:text-ink",
                    )}
                  >
                    {c}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <button
        disabled={!canSend}
        onClick={sendAnswers}
        className={cn(
          "inline-flex h-10 items-center justify-center gap-2 self-start rounded-xl px-4 text-[13px] font-bold transition-colors",
          canSend
            ? "cursor-pointer bg-gold text-navy active:scale-95"
            : "cursor-not-allowed bg-divider text-ink-3 opacity-50",
        )}
      >
        <Send size={16} weight="bold" />
        Send answers
      </button>
      <div className="text-[11.5px] text-ink-3">Or just type your answer below.</div>
    </motion.div>
  );
}
```

- [ ] **Step 3: Render it from `Message.tsx`**

In `Message.tsx`, add the import (after the `ThinkingIndicator` import from Task 4):

```tsx
import ClarifyCard from "./ClarifyCard";
```

Replace the answer render block (lines 66-76):

```tsx
      {m.text && (
        <Answer
          text={m.text}
          sources={m.sources}
          warnings={m.warnings}
          streaming={!m.done}
          done={m.done}
        />
      )}

      {m.done && <MessageFooter m={m} />}
```

with (clarify card takes over, and its turn shows no verify footer):

```tsx
      {m.clarify ? (
        <ClarifyCard preamble={m.clarify.preamble} questions={m.clarify.questions} />
      ) : (
        m.text && (
          <Answer
            text={m.text}
            sources={m.sources}
            warnings={m.warnings}
            streaming={!m.done}
            done={m.done}
          />
        )
      )}

      {m.done && !m.clarify && <MessageFooter m={m} />}
```

- [ ] **Step 4: Verify the build**

Run (from `nyaya-memory-chat/frontend/`): `npm run build`
Expected: `tsc --noEmit` + `vite build` pass with no errors.

- [ ] **Step 5: Manual end-to-end check**

Start backend (`uvicorn app.main:app --reload --port 8000`) and frontend (`npm run dev`), sign in, and:
1. Send a thin query (`my father passed away, what happens to the house?`) → the assistant bubble shows the rotating loading, then a **"A bit more context"** card with chip groups. Pick chips → **Send answers** → a grounded answer follows.
2. Send a well-specified query → normal streamed answer, no card.
3. Watch the loading headline advance recall → retrieve → compose on a normal answer.

- [ ] **Step 6: Commit**

```bash
cd nyaya-memory-chat/frontend
git add src/lib/icons.tsx src/components/chat/ClarifyCard.tsx src/components/chat/Message.tsx
git commit -m "feat(chat): interactive clarify card with answer chips"
```

---

## Self-Review

**Spec coverage:**
- Rotating reassurance loading (phase off real events) → Tasks 3 (phase tracking) + 4 (component). ✓
- Structured clarify decision, fast model, parallel with retrieval, defensive fallback → Tasks 1 + 2. ✓
- `ClarifyEvent` in the `ChatEvent` union → Task 3. ✓
- Interactive card with chips + "Send answers" composing one follow-up → Task 5. ✓
- Persist clarify turn as text; footer suppressed → Task 2 (persist) + Task 5 (suppress footer). ✓
- Backend defensive-parse unit tests → Task 1. ✓
- YAGNI guards (radio not multi-select, no heuristic, no chip persistence) → honored in Tasks 1/5. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `clarify_gate`/`_parse_clarify`/`render_clarify_text` signatures match across Tasks 1-2; `ClarifyQuestion`/`ClarifyEvent`/`UIMessage.phase`/`UIMessage.clarify` names identical across Tasks 3-5; the `clarify` NDJSON event shape (`preamble`, `questions:[{q,chips}]`) matches between backend emit (Task 2) and frontend handler (Task 3) and card props (Task 5). ✓
