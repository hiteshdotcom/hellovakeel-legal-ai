# Rotating reassurance loading + interactive clarify card

**Date:** 2026-07-04
**Scope:** `nyaya-memory-chat` — chat backend (`app/`) + React frontend (`frontend/`)

## Problem

Two gaps in the chat experience:

1. **Loading feels cold.** When the assistant is working, the thread shows a single
   static "Searching the judgment graph" line + skeleton. Users don't get the
   ChatGPT-style "hang tight, this is working" reassurance.
2. **Thin queries get guessed at.** When a user asks a question missing a
   *controlling* fact (personal law, owned vs. tenanted, whether a registered deed
   exists, dates), the model may answer on assumptions. The system prompt already
   asks Claude to pose up to 3 questions (`prompts.py` rule 5), but they arrive as
   plain prose with no structured, tappable UI, and firing is unreliable.

## Goals

- A warm, honest, rotating "reassurance" loading state driven by the *real*
  pipeline phases already streamed to the client.
- When a query lacks a controlling fact, Claude **structurally decides** to ask
  clarifying questions, rendered as an interactive card with tappable answer chips,
  before composing an answer.

## Non-goals (YAGNI)

- No length/keyword heuristic gate — Claude decides (user choice).
- No multi-select per question — one pick each (radio).
- No persistence of chip selection state across reloads.
- No change to the grounding guard, retrieval, or memory subsystems.

---

## Feature 1 — rotating reassurance loading (frontend only)

The backend already streams the pipeline phases: `meta` (recall done) → `sources`
(retrieval done) → `token` (composing). Drive the loading copy off these real
events so the reassurance is honest, not a fake timer.

### Changes

- **`store/chat.ts`** — add `phase: 'recall' | 'retrieve' | 'compose'` to
  `UIMessage`. Set `recall` when the assistant bubble is created; `retrieve` on the
  `meta` event; `compose` on the `sources` event. Cleared implicitly when
  `thinking` flips false on the first `token`.
- **`components/chat/ThinkingIndicator.tsx`** (new) — replaces the inline block at
  `Message.tsx:45-64`. Renders:
  - A phase-mapped headline + icon that cross-fades on change (`AnimatePresence`):
    - `recall` → "Hang tight — recalling your matter…" (Brain)
    - `retrieve` → "Searching 505 judgments & Central Acts…" (Search)
    - `compose` → "Grounding every citation, then composing…" (Shield)
  - The existing 3-dot pulse (reuse the `dot` keyframe).
  - A calm secondary line rotating every ~2.6s among 2–3 reassurances
    ("I verify every citation before it reaches you", "Answers come only from real
    judgments — nothing invented").
  - The shimmer `SkeletonLines` beneath.
- **`components/chat/Message.tsx`** — render `<ThinkingIndicator phase={m.phase} />`
  while `m.thinking`.

No backend change.

---

## Feature 2 — interactive clarify card (backend + frontend)

Claude structurally decides answer-vs-clarify via a fast pre-flight gate that runs
**in parallel with retrieval** (both start right after recall), adding no perceived
latency and leaving the streaming answer path untouched.

### Backend

- **`app/clarify.py`** (new) — `async def clarify_gate(llm, message, recalled) -> dict`.
  Calls the Claude **fast** model (`CLAUDE_MODEL_FAST`) with a JSON-only prompt.
  Returns either:
  ```json
  {"needs": false}
  ```
  or:
  ```json
  {"needs": true,
   "preamble": "To answer precisely I need a couple of details:",
   "questions": [
     {"q": "Whose personal law governs the succession?",
      "chips": ["Hindu", "Muslim", "Christian", "Not sure"]},
     {"q": "Is the property owned or tenanted?",
      "chips": ["Owned", "Tenanted", "Not sure"]}
   ]}
  ```
  Prompt rules: max 3 questions; fire **only** when a *controlling* fact is
  genuinely missing and not already answered by `recalled`; 2–4 short chips each,
  always including an escape chip ("Not sure"/"Other"). Parse defensively —
  **any error, malformed JSON, or timeout returns `{"needs": false}`** so the
  answer path is never blocked.

- **`app/api/chat.py`** — after `recall_user`, run `clarify_gate` concurrently with
  the existing `retriever.retrieve` / `statute_retriever.retrieve` gather. Then:
  - If `needs` is true → emit `{"type": "clarify", "preamble": …, "questions": […]}`,
    persist the assistant turn (store preamble + questions rendered as text for
    coherent history), emit `{"type": "done"}`, and **return** — no tokens, no
    grounding, no sources event.
  - Else → today's path unchanged (emit `sources`, stream `token`s, ground, `final`).

### Frontend

- **`lib/types.ts`** — add:
  ```ts
  export interface ClarifyEvent {
    type: "clarify";
    preamble: string;
    questions: { q: string; chips: string[] }[];
  }
  ```
  and include it in the `ChatEvent` union.
- **`store/chat.ts`** — `UIMessage` gains
  `clarify?: { preamble: string; questions: { q: string; chips: string[] }[] }`
  and `phase`. In `send()`, handle `ev.type === "clarify"` → set `m.clarify`,
  `m.thinking = false`, `m.done = true` (no answer text). `meta`/`sources` set
  `m.phase`.
- **`components/chat/ClarifyCard.tsx`** (new) — rendered by `Message.tsx` when
  `m.clarify` is set. A distinct gold left-accent card: "A bit more context" header
  (Chat/question icon), the preamble, then each question with its chips as a
  selectable radio-style group (reuse the `Chip` look). A gold **"Send answers"**
  button (disabled until ≥1 pick) composes the selected answers into one follow-up
  message — e.g. `Personal law: Hindu. Property: Owned.` — and calls
  `send(user.id, composed)`. The composer stays fully usable for free-typing.
- **`components/chat/Message.tsx`** — when `m.clarify`, render `<ClarifyCard>`
  instead of `<Answer>`; suppress the `MessageFooter` (no citations to verify).

### History

Clarify turns are persisted as assistant **text** (preamble + questions). Reopening
a session shows them as plain prose; chips are live-turn only. Acceptable.

---

## Testing

- **Backend** — unit test `clarify_gate`'s defensive parsing: malformed JSON,
  missing keys, and a thrown client error all return `{"needs": false}`.
- **Manual** — thin query ("my father died, what about the property?") → clarify
  card with chips; tapping chips + "Send answers" produces a grounded answer.
  Well-specified query → normal streamed answer (no card). Loading headline advances
  recall → retrieve → compose.

## Risks

- **Extra fast-model call per turn.** Runs in parallel with retrieval, so no added
  wall-clock; falls back to answering on any failure. Cost is one cheap fast-model
  call.
- **Over-clarifying.** Mitigated by a conservative prompt (controlling fact only,
  max 3) and the escape chip so users can always proceed.
