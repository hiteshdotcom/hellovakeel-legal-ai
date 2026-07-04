# Build Prompt — Nyaya.AI "Memory Chat" (Cognee-powered, standalone service)

> Paste everything below the line into the coding agent of the **new** project.
> It is self-contained: it carries the real DB schema, env vars, model IDs, and
> acceptance criteria from the existing Nyaya.AI / lex-ai backend.

---

## ROLE

You are building a **new, standalone microservice** called `nyaya-memory-chat`: a
legal research **chat** for Indian case law that, unlike a stateless RAG bot,
**never forgets**. It uses **Cognee** as a self-hosted hybrid **graph + vector
memory** layer so it (a) builds a persistent knowledge graph over our judgments
corpus and (b) remembers every user across infinite sessions.

This service is **separate** from the existing FastAPI monolith (`lex-ai/backend`)
but **reuses the same Supabase Postgres** that already holds our judgments. Treat
the judgments tables as a **read-only source of truth**; write your own
chat/memory tables in a separate schema (`memchat`).

Do not modify the existing backend. Do not write to the judgments tables.

---

## TECH CONSTRAINTS

- **Language/stack:** Python 3.11+ (Cognee supports 3.10–3.14), FastAPI, async, `uv` or `pip`.
- **Memory engine:** `cognee` **1.0+**, self-hosted (NOT Cognee Cloud).
  Install with the Postgres extra: `pip install "cognee[postgres]"`.
  Use the **high-level API**: `cognee.remember()`, `cognee.recall()`, `cognee.forget()`.
  (Fall back to the lower-level `cognee.add()` / `cognee.cognify()` / `cognee.search()`
  only if a feature like custom edge injection needs it — see §3.)
- **Two distinct LLM roles — keep them separate:**
  - **Cognee's internal LLM** (graph extraction during `remember`/cognify): use **OpenAI**
    (`LLM_API_KEY` = our `OPENAI_API_KEY`). This is the documented default path and keeps
    embedding/vector compatibility trivial.
  - **The user-facing legal answer:** generated in **OUR** code with **Claude `claude-opus-4-8`**
    (Anthropic), AFTER Cognee returns grounding context. Cognee retrieves; Claude composes.
    Use `claude-sonnet-4-6` for any cheap query-rewrite step.
- **Embeddings:** OpenAI `text-embedding-3-large`, **1536 dims** — MUST match our existing
  `judgment_vectors`. Set Cognee's embedding env vars explicitly (don't rely on its default).
- **Whole memory layer on ONE Postgres (our existing Supabase):** per Cognee 1.0,
  run graph + vectors + sessions + metadata in a single Postgres:
  `DB_PROVIDER=postgres`, `VECTOR_DB_PROVIDER=pgvector`, `GRAPH_DATABASE_PROVIDER=postgres`,
  `CACHE_BACKEND=postgres`. Point Cognee at a **dedicated database/schema** so it never
  collides with our `public` judgment tables.
- **Optional demo visual:** set `GRAPH_DATABASE_PROVIDER=neo4j` to screen-share the live
  graph in Neo4j Browser. Keep it env-swappable; default stays Postgres.
- **Reranking (optional, keep parity):** Cohere rerank if `COHERE_API_KEY` present.
- **Frontend:** minimal — a single streaming chat page (Next.js OR a plain `index.html` + fetch). The graph/memory is the star; keep UI thin.

---

## ENVIRONMENT VARIABLES (reuse the existing ones)

> Variable names below are Cognee 1.0's documented names. Verify against the
> version you install (`cognee.__version__`) and its `.env.template`.

```env
# ── OUR app: Supabase / judgments (read-only source of truth) ──
SUPABASE_URL=
SUPABASE_SERVICE_KEY=          # service role — READ judgments, WRITE memchat schema
DATABASE_URL=                  # postgres://...  for our own asyncpg pool + memchat writes

# ── OUR app: AI providers ──
ANTHROPIC_API_KEY=             # Claude — used by OUR code for the final legal answer
OPENAI_API_KEY=               # also handed to Cognee as LLM_API_KEY below
COHERE_API_KEY=                # optional rerank
CLAUDE_MODEL_REASONING=claude-opus-4-8
CLAUDE_MODEL_FAST=claude-sonnet-4-6

# ── COGNEE: LLM for graph extraction (use OpenAI) ──
LLM_API_KEY=                   # set = OPENAI_API_KEY
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini          # cheap extraction model; bump if extraction quality lags

# ── COGNEE: embeddings — MUST be 1536 to match judgment_vectors ──
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSIONS=1536
EMBEDDING_API_KEY=             # set = OPENAI_API_KEY

# ── COGNEE: run the whole memory layer on ONE Postgres ──
# Use a SEPARATE database (or at least a dedicated schema) from our judgment tables.
DB_PROVIDER=postgres
VECTOR_DB_PROVIDER=pgvector
GRAPH_DATABASE_PROVIDER=postgres   # swap to "neo4j" for the visual demo
CACHE_BACKEND=postgres
DB_HOST=
DB_PORT=5432
DB_USERNAME=
DB_PASSWORD=
DB_NAME=cognee_db

# ── COGNEE: optional Neo4j (demo visual only) ──
GRAPH_DATABASE_URL=            # bolt://...  only if GRAPH_DATABASE_PROVIDER=neo4j
GRAPH_DATABASE_USERNAME=
GRAPH_DATABASE_PASSWORD=
```

---

## EXISTING DATABASE — READ-ONLY SOURCE OF TRUTH

All tables below already exist in the shared Supabase Postgres. **Read** them; do
not alter them. Verify exact column types at runtime with
`information_schema.columns` before relying on anything marked "(verify)".

### `judgments_metadata` (one row per judgment — the rich metadata)
Primary key: `judgment_id` (text/uuid). Columns we depend on:

| column | type | notes |
|---|---|---|
| `judgment_id` | text/uuid | PK |
| `case_title` | text | e.g. "Kesavananda Bharati v. State of Kerala" |
| `petitioner_names` | text[] | |
| `respondent_names` | text[] | |
| `bench` | text[] | judge names |
| `citation` | text | e.g. "AIR 1973 SC 1461" |
| `case_number` | text | |
| `cnr_number` | text | |
| `neutral_citation` | text | |
| `court_type` / `court_level` / `court_name` / `court_state` | text | `court_level` ∈ {Supreme Court, High Court, Tribunal, Privy Council} |
| `acts_cited` | text[] | |
| `sections_cited` | text[] | |
| `judgment_date` | date | |
| `disposition` / `final_order` / `operative_order` | text | |
| `ratio_decidendi` | text | the binding legal principle |
| `headnotes` | text | |
| `legal_issues` | text[] | |
| `material_facts` | text[] | |
| `defeated_arguments` | text[] | |
| `open_questions` | text[] | |
| `keywords` | text[] | |
| `subject_tags` | text[] | topic tags — use for graph edges |
| `precedential_weight` | text | |
| `citator_note` | text | |
| `historical_significance` | text | |
| `jurisdiction` | jsonb | |
| `bench_detail` | jsonb | |
| `travel_of_case` | jsonb/array | |
| `acts_and_provisions` | jsonb/array | |
| `ratio_propositions` | jsonb/array | |
| `obiter_dicta` | jsonb/array | |
| `precedents` | jsonb/array | cases this judgment relies on |
| `constitutional_scope` | jsonb | |
| `intent_analysis` | jsonb | |
| `how_to_cite` | jsonb | |
| `current_law_status` | jsonb | has `ratio_still_good_law: bool` — **respect this** |
| `pdf_storage_url` | text | |
| `search_tsv` | tsvector | full-text index (verify) |

### `judgment_pages` (page text)
`judgment_id`, `page_number` (int), `page_width`, `page_height`, `text`.

### `judgment_vectors` (our EXISTING child/parent chunks + embeddings)
Columns (verify): `id`, `judgment_id`, `content` (child chunk), `parent_content`
(section context), `embedding` (vector(1536)), `metadata` (jsonb).
**You may read these to seed Cognee instead of re-chunking from scratch.**

### `judgment_citations` (the citation graph — already structured!)
`citing_id`, `cited_id`, `citation_type`, `citation_text`. This is a ready-made
edge list: judgment → cites → judgment. **Feed this directly into the Cognee graph.**

### Existing RPC — hybrid search (vector + BM25 + RRF)
```
hybrid_search(query_text text, query_embedding vector(1536),
              match_count int, filter_criteria jsonb) returns setof rows
```
Call via Supabase RPC. Returns ranked chunks with `judgment_id`. Use it as a
**fallback / cross-check retriever** alongside Cognee's graph retrieval.

---

## COGNEE BUILDING BLOCKS WE RELY ON (verified from docs.cognee.ai)

You will use Cognee at **two levels**. The high-level `remember`/`recall` is fine
for per-user chat memory. But the **corpus** must be built with the **low-level
Tasks → Pipeline → DataPoints** API, because that is the only way to inject our
*curated* citation edges as real graph edges instead of LLM guesses.

**DataPoints** — "atomic units of knowledge", Pydantic models. A custom DataPoint
subclass declares relationships to other DataPoints via **typed fields**, and
`add_data_points()` turns them into graph nodes + edges automatically:
```python
author: Author                              # simple edge  (Book)-[:author]->(Author)
has_items: (Edge(weight=0.8), list[Item])   # edge with metadata
chapters: list[Chapter]                      # one-to-many edges
```
`metadata = {"index_fields": [...]}` marks which text fields get embedded/searchable.

**Tasks** — smallest executable unit; wrap any callable: `Task(fn, task_config={...})`.
Tasks normalize sync/async/generator fns, support batching, and chain so each task's
output is the next task's input. Built-ins include `add_data_points` (persistence),
`extract_graph_from_data`, `summarize_text`, `extract_chunks_from_documents`.

**Pipelines** — `run_pipeline(tasks=[...], data=..., datasets=["..."],
pipeline_name="...", use_pipeline_cache=False)` is an async generator (iterate to
completion). **Never** name a pipeline `cognify_pipeline` or `add_pipeline` (reserved).
A `ctx: PipelineContext = None` param is auto-injected (gives `user`, `dataset`,
`data_item`, `extras`). Errors yield `PipelineRunErrored` then re-raise — wrap in try/except.

> Import paths (e.g. `from cognee.low_level import DataPoint`,
> `from cognee.modules.pipelines import Task, run_pipeline`,
> `from cognee.tasks.storage import add_data_points`, and `Edge`) shift between
> versions — **verify against the installed package** before relying on them.

---

## WHAT TO BUILD

### 1. Config + clients (`app/config.py`, `app/db.py`)
- Pydantic settings reading the env above.
- A Supabase client (service key) and a raw `asyncpg`/SQLAlchemy pool on `DATABASE_URL`.
- A read-only `JudgmentRepo` exposing: `get_metadata(ids)`, `get_pages(id)`,
  `get_citations(id)`, `list_recent(n)`, and a thin wrapper over the `hybrid_search` RPC.

### 2. Cognee configuration (`app/memory/cognee_setup.py`)
Set Cognee's env (see ENV block) BEFORE importing/using cognee, so it picks
OpenAI for the LLM + 1536-dim embeddings and Postgres for graph+vector+cache.
Provide a `configure_cognee()` called once at FastAPI startup that asserts the
embedding dimension is 1536 and that the Postgres target DB is NOT our judgment DB.

**Tenant/partition isolation** — use Cognee's `dataset` to separate knowledge,
and `session_id` for within-conversation fast cache:
  - `dataset="corpus"` — the shared judgments knowledge graph (built once, offline).
  - `dataset=f"user_{user_id}"` — that user's permanent cross-session memory graph.
  - `session_id=f"{user_id}:{session_id}"` — fast session cache for the live chat.

> API note: the high-level `cognee.remember(text, session_id=...)` and
> `cognee.recall(query, session_id=...)` are the documented surface. Confirm whether
> your installed version also accepts a `dataset=` kwarg on `remember`/`recall`
> (it does on `forget`). If not, use the lower-level
> `cognee.add(text, dataset_name=...)` + `cognee.cognify(datasets=[...])` +
> `cognee.search(query_text=..., datasets=[...])` for the `corpus` partition, and
> reserve `remember`/`recall` + `session_id` for per-user chat memory.

### 3. Corpus ingestion job (`scripts/ingest_corpus.py` + `app/memory/corpus_graph.py`)
Turn our judgments into a Cognee knowledge graph **with our real citation edges**,
using the low-level Tasks/Pipeline/DataPoints API.

**(a) Define typed DataPoints** (`app/memory/corpus_graph.py`) so relationships
become real graph edges — this is the whole point of using the low-level API:
```python
from cognee.low_level import DataPoint          # verify import path
# from cognee.modules.engine.models import Edge  # if you need weighted edges

class Court(DataPoint):
    name: str
    level: str                                  # Supreme Court / High Court / ...
    metadata: dict = {"index_fields": ["name"]}

class Act(DataPoint):
    name: str
    metadata: dict = {"index_fields": ["name"]}

class Judgment(DataPoint):
    judgment_id: str                            # our PK — keep it
    case_title: str
    citation: str | None = None
    court: Court | None = None
    judgment_date: str | None = None
    ratio_decidendi: str | None = None
    headnote_excerpt: str | None = None
    subject_tags: list[str] = []
    precedential_weight: str | None = None
    still_good_law: bool = True                 # from current_law_status
    acts: list[Act] = []                        # (Judgment)-[:acts]->(Act)
    cites: list["Judgment"] = []                # (Judgment)-[:cites]->(Judgment)  ← curated edges
    metadata: dict = {"index_fields": ["case_title", "ratio_decidendi", "headnote_excerpt"]}
```

**(b) Build a custom pipeline** that maps DB rows → `Judgment` DataPoints → graph.
First materialize a `{judgment_id: Judgment}` map for the batch so `cites` can point
at real objects; resolve citation targets from `judgment_citations` (`citing_id`
→ `cited_id`). Cited judgments not in this batch: create a stub `Judgment` with just
`judgment_id`/`case_title`/`citation` so the edge still lands.
```python
from cognee.modules.pipelines import Task, run_pipeline   # verify path
from cognee.tasks.storage import add_data_points           # verify path

async def rows_to_judgment_points(batch):       # batch = list of metadata dicts
    points = build_judgment_datapoints(batch)   # wires court/acts/cites per the model above
    return points

async for _ in run_pipeline(
    tasks=[Task(rows_to_judgment_points), Task(add_data_points)],
    data=batches,                               # list of ~20-row batches
    datasets=["corpus"],
    pipeline_name="judgment_graph_pipeline",    # NOT cognify_pipeline / add_pipeline
    use_pipeline_cache=False,
):
    pass
```
Optionally append `Task(extract_graph_from_data)` / `Task(summarize_text)` if you
also want LLM-derived links *on top of* the curated ones — but the curated `cites`
edges must come from our `judgment_citations` table, not the LLM.

**(c) Mark overruled law:** set `still_good_law=False` when
`current_law_status.ratio_still_good_law is False`; retrieval/answer layers must surface this.

**(d) Idempotent & demo-friendly:** skip already-ingested `judgment_id`s (track in
`memchat.cognee_ingest_log`); page `judgments_metadata` newest-first; expose
`--limit` and `--since` flags so the demo can ingest a few hundred landmark cases fast.

> If the low-level `DataPoint`/`run_pipeline`/`add_data_points` imports differ in your
> installed version, that's the ONE thing to reconcile first — everything else here
> (high-level remember/recall) is stable. Do not silently fall back to dumping plain
> text; the curated citation graph is the differentiator.

### 4. Per-user memory (`app/memory/user_memory.py`)
- `remember_turn(user_id, session_id, role, content)`:
  `await cognee.remember(f"{role}: {content}", session_id=f"{user_id}:{session_id}")`.
  `remember` runs add+cognify+improve and syncs session memory to the permanent graph
  in the background, so this is the one call you need — but still invoke it from a
  FastAPI `BackgroundTask` so the chat response never waits on it.
  Also write the turn into a per-user permanent partition for durable cross-session
  recall: `await cognee.remember(content, dataset=f"user_{user_id}")` (or the
  add+cognify fallback if `dataset` isn't accepted).
- `recall_user(user_id, session_id, query)`: try session memory first, fall through
  to the permanent graph:
  `await cognee.recall(query, session_id=f"{user_id}:{session_id}")` → if thin,
  `await cognee.recall(query)` scoped to `dataset=f"user_{user_id}"`.
- Store the raw transcript too, in `memchat.messages`, as an audit log
  (the Cognee graph is the *intelligent* layer on top of the raw log).

### 5. Retrieval over the corpus (`app/memory/corpus_retrieval.py`)
Given a user query, gather grounding context by combining:
- `await cognee.recall(query)` scoped to the `corpus` partition (graph + vector
  auto-routed) — returns graph-grounded passages. Parse the `JUDGMENT_ID:` headers
  (and any matched Judgment nodes) back to `judgment_id`s.
- The existing `hybrid_search` RPC (embed query with OpenAI `text-embedding-3-large`
  first) — vector/BM25/RRF cross-check straight against `judgment_vectors`.
- Union the `judgment_id`s, fetch full metadata via `JudgmentRepo.get_metadata(ids)`,
  optionally Cohere-rerank, keep top N.

### 6. Chat endpoint (`app/api/chat.py`)
`POST /chat` — body `{ user_id, session_id?, message }`, **streaming** response.

Flow:
1. `user_mem = await recall_user(user_id, session_id, message)` (their case facts / preferences).
2. `corpus_ctx = retrieve(message)` (relevant judgments + ratios + citation graph).
3. Build a Claude (`claude-opus-4-8`) prompt — **Cognee retrieved, Claude composes**:
   - System: "You are an Indian legal research assistant. Answer ONLY from the
     provided judgments. Cite every legal claim as `[Case Name, Year, Court]`.
     Never invent a citation. Respect `ratio_still_good_law`: warn if a relied-on
     precedent is overruled. Use the user's remembered case facts for continuity."
   - Context blocks: `USER MEMORY`, `RETRIEVED JUDGMENTS` (each with title,
     citation, court, date, ratio_decidendi, status).
4. Stream the answer.
5. **Ground it** (port the existing guard, see §7). Append a `sources[]` array
   (the judgment_ids actually used) and a `warnings[]` array (unverified citations).
6. `BackgroundTasks`: `remember_turn(user_id, "user", message)` and
   `remember_turn(user_id, "assistant", answer)`; insert into `memchat.messages`.

Also: `GET /sessions/{user_id}` (history), `GET /graph/{user_id}` (return the
user's memory subgraph as nodes/edges JSON for a visualization), `GET /healthz`.

### 7. Citation grounding guard (`app/grounding.py`)
Port the existing guard's behavior: regex-extract case citations from the model's
answer (Indian reporter shapes: `AIR 1978 SC 597`, `(2023) 1 SCC 1`,
`2019:DHC:1234`, `X v Y`), fuzzy-match each against the `case_title`/`citation`
of the **retrieved** judgments. Any citation not present in retrieved context →
flag as `unverified` (a hallucinated precedent). Return these in `warnings[]`.
This is the single most important trust feature — do not skip it.

### 8. New tables (`migrations/001_memchat.sql`) — schema `memchat`
- `memchat.sessions(id, user_id, created_at, last_active_at, title)`
- `memchat.messages(id, session_id, user_id, role, content, sources jsonb, warnings jsonb, created_at)`
- `memchat.cognee_ingest_log(judgment_id pk, ingested_at, status)`
Keep everything in the `memchat` schema. Never touch `public` judgment tables.

---

## DELIVERABLES / FILE LAYOUT

```
nyaya-memory-chat/
  README.md                 # setup, env, how to run ingestion, how to demo
  requirements.txt
  .env.example
  migrations/001_memchat.sql
  scripts/ingest_corpus.py
  app/
    main.py                 # FastAPI app, startup: cognee_setup + db pool
    config.py
    db.py                   # JudgmentRepo (read-only) + memchat writes
    memory/
      cognee_setup.py
      user_memory.py
      corpus_retrieval.py
    api/chat.py
    grounding.py
    llm.py                  # Claude client (opus reasoning / sonnet fast)
  web/                      # minimal streaming chat UI + graph view
  tests/
    test_grounding.py       # hallucinated-citation detection
    test_recall.py          # cross-session memory survives a new session
```

---

## ACCEPTANCE CRITERIA (must demonstrate)

1. **Corpus graph built:** `ingest_corpus.py --limit 200` ingests 200 judgments
   into Cognee; `GET /graph` (corpus) shows Judgment/Act/Section/SubjectTag nodes
   and real `CITES` edges from `judgment_citations`.
2. **Grounded answers:** asking "What did the Supreme Court hold on the basic
   structure doctrine?" returns an answer citing real retrieved judgments, with a
   `sources[]` list of judgment_ids and **zero** unverified citations.
3. **Overruled-law guard:** if a retrieved precedent has
   `current_law_status.ratio_still_good_law = false`, the answer warns it is no
   longer good law.
4. **Cross-session memory (the headline demo):** In session A, user says "I have a
   cheque-bounce dispute, ₹2L, Maharashtra, no case filed yet." Start a **brand
   new session** (new session_id, same user_id), ask "what should I do next?" —
   the bot recalls the dispute, amount, state, and filing status **without** the
   facts being resent. Context window does **not** grow with history length
   (verify token count stays bounded as the conversation gets long).
5. **No blocking:** chat responses stream immediately; `cognify` runs in the
   background.
6. **Isolation:** user A's memory never appears in user B's recall
   (per-user `dataset` / `session_id` namespacing verified by a test).

---

## NOTES / GOTCHAS

- **Install:** `pip install "cognee[postgres]"`. Pin the version in
  `requirements.txt` and record `cognee.__version__` in the README — the high-level
  `remember`/`recall`/`forget` API is 1.0+; older installs only have
  `add`/`cognify`/`search`. Code defensively for both (see §2 fallback note).
- **Cognee LLM ≠ answer LLM.** Cognee uses OpenAI internally (extraction/cognify);
  the user-facing legal answer is generated by *our* Claude call. Don't let Cognee
  generate the final legal text — we need Claude + the grounding guard for that.
- Embedding dims **must** be 1536 (`text-embedding-3-large`) to stay compatible
  with `judgment_vectors`; a mismatch silently breaks vector search. Assert it at startup.
- **Two Postgres roles, don't cross them:** our judgments live in the existing
  Supabase `public` schema (READ-ONLY here); Cognee's graph+vector+cache live in a
  **separate database** (`DB_NAME=cognee_db`) or dedicated schema. Our own chat
  tables live in `memchat`. Never let Cognee write into the judgment tables.
- `remember`/`cognify` calls an LLM per batch — batch judgments (~20 at a time) and
  run corpus ingestion **offline**, never in the request path. Per-user `remember`
  during chat goes through a `BackgroundTask`.
- Respect Supabase rate limits; page through `judgments_metadata` with
  `range()` and `order("judgment_date", desc=True)`.
- Keep the existing monolith untouched; this is a sibling service that shares
  only the database and API keys.
- For the most impressive demo, set `GRAPH_DATABASE_PROVIDER=neo4j` and screen-share
  the Neo4j Browser showing the live citation graph + the user's growing memory.
  (Default stays single-Postgres; Neo4j is purely the visual.)
- Cognee 1.0 also ships a Claude Code plugin and an MCP server — not needed for this
  service, but if you'd rather not write the FastAPI memory glue, the MCP server
  (`cognee/cognee-mcp`) exposes remember/recall as tools you could call instead.
```
