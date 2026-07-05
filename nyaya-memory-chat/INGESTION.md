# Collecting & Processing Legal Data (Acts → searchable knowledge)

How to add Indian statutes to the AI's corpus. The pipeline is:

```
find working PDF URL → catalog entry → download → extract text (pypdf)
   → chunk → embed (OpenAI 1536) → store in memchat.legal_knowledge → verify retrieval
```

Everything after "catalog entry" is automated by `scripts/ingest_all_acts.py`.
The only manual part is **collecting a working PDF URL** for each act.

---

## 0. One-time setup

```bash
cd nyaya-memory-chat
. .venv/Scripts/activate          # Windows;  or: source .venv/bin/activate
pip install -r requirements.txt
# .env must have DATABASE_URL and OPENAI_API_KEY (embeddings cost money per run)
```

---

## 1. COLLECT — get a working PDF URL for each act

Source is **indiacode.nic.in** (official). Two cases:

### Central Acts
Their `bitstream` URLs serve the PDF directly. Pattern:
`https://www.indiacode.nic.in/bitstream/123456789/<id>/1/<file>.pdf`
(The 847 Central Acts are already ingested — you rarely need this.)

### State Acts (the ones we're expanding)
**State `bitstream` URLs do NOT work** — they 302 to a JavaScript page with no
PDF. You MUST use the file-server endpoint instead:

```
https://upload.indiacode.nic.in/showfile?actid=<ACTID>&filename=<FILE>.pdf&type=actfile
```

**How to find that URL for an act:**
1. Web-search: `<Act name> indiacode showfile actfile pdf`
   (e.g. `Maharashtra Rent Control Act 1999 indiacode showfile actfile pdf`).
2. In the results, look for a link on **`upload.indiacode.nic.in/showfile...&type=actfile`**.
   That is the one you want. Ignore `bitstream` links and `type=rule`/`type=regulation` links.
3. If no `actfile` link appears, open the act's `handle/…` page in a browser and
   use the download button — copy the resulting `showfile?...type=actfile` URL.

**Always verify the URL is a real PDF before adding it:**
```bash
python - <<'PY'
import asyncio, httpx
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36'
URL='<paste the showfile url>'
async def main():
    async with httpx.AsyncClient(headers={'User-Agent':UA}, timeout=90) as c:
        r=await c.get(URL, follow_redirects=True)
        print(r.status_code, len(r.content), 'bytes  isPDF=', r.content[:5].lstrip().startswith(b'%PDF'))
asyncio.run(main())
PY
```
Keep only URLs that print `isPDF= True`.

---

### Bulk pipe-delimited catalog (the all-India harvest)

For a large harvest we also support a **flat one-line-per-act** catalog:

```
<Act Title> | <PDF URL>
```

(e.g. `acts_pdf_data.txt` at the repo root — ~7.1k acts across every State/UT).
`scripts/acts_catalog.py` auto-detects this format (no `Act Title:` markers) and:
- derives a **stable, unique Act ID** from the bitstream `(handle, seq)` in the URL
  → `ic-<handle>-<seq>` (survives title-slug collisions),
- **detects the State/UT from the title** (incl. historical names: Bombay→
  Maharashtra, Madras→Tamil Nadu, Orissa→Odisha…) to tag jurisdiction; a title
  with no state reads as Central (`India`),
- **de-duplicates** by stable key (repeated URLs never seed the log twice).

Most indiacode `bitstream` URLs DO serve the PDF via a `http→https→www` redirect
chain the httpx client follows (a minority hit a JS interstitial and are logged
`failed` for `--retry-failed`). Run it exactly like any catalog:

```bash
python -m scripts.ingest_all_acts --catalog ../acts_pdf_data.txt --concurrency 10
```

Throughput note: the write path uses a **pipelined `executemany`** for chunks
(one round-trip per act instead of one per chunk) — essential over the Australia
Supabase pooler. Download latency (indiacode) is the bottleneck, so concurrency
scales throughput (~24 acts/min at `--concurrency 10`); keep it ≤~10 to stay
polite. The pooler is **session-mode, ~15 client connections total** across all
processes — see `DB_POOL_MAX` below.

## 2. CATALOG — write the act into a catalog file

Catalogs are plain text, one block per act, blocks separated by a dashed line.
State catalogs live in `scripts/state_acts/<state>.txt`. Format (copy exactly):

```
Act Title: The Maharashtra Rent Control Act, 1999
Act Number: 18  |  Enactment Date: 31-Mar-2000  |  Act ID: MH2000_18
Ministry: Maharashtra State Legislature
Purpose: An Act to ... (one line).
PDF Download: https://upload.indiacode.nic.in/showfile?actid=...&filename=...&type=actfile
Act Page: https://www.indiacode.nic.in/handle/123456789/15817?view_type=browse
------------------------------------------------------------
```

Rules that matter:
- **Act ID** must be unique and clean → use `MH<year>_<number>` (e.g. `MH1963_45`).
  It becomes the resume key and the stable `doc_id` (re-ingesting updates in place).
- **Ministry** must contain the state name (e.g. "Maharashtra State Legislature") —
  the pipeline reads it to tag `jurisdiction=Maharashtra` so answers can tell
  state law from central law.
- **PDF Download** = the verified `showfile ... type=actfile` URL from step 1.

---

## 3. PROCESS — run the ingestion pipeline

Dry-run first (lists what will be processed, downloads nothing):
```bash
python -m scripts.ingest_all_acts --catalog scripts/state_acts/maharashtra.txt --dry-run
```

Real run:
```bash
python -m scripts.ingest_all_acts --catalog scripts/state_acts/maharashtra.txt --concurrency 2
```

What it does per act (fault-tolerant — one bad act never aborts the run):
download PDF → pypdf text extract → chunk (~4500 chars) → embed each chunk
(OpenAI, **costs money**) → upsert into `memchat.legal_knowledge(+_chunks)` →
mark `done` in `memchat.act_ingest_log`.

Useful flags:
- `--dry-run` — preview selection only.
- `--limit N` — process at most N (validate on 1–3 first).
- `--concurrency K` — K acts at a time (default 4; be gentle on indiacode).
- `--no-embeddings` — store text/chunks WITHOUT embeddings (free; not searchable
  by vector — use only to smoke-test extraction).
- `--retry-failed` — re-try acts previously `failed`/`no_text`/`skipped`.
- `--force` — re-ingest even acts marked `done`.

The run is **resumable**: re-running skips acts already `done`, so you can stop
and continue anytime.

Check status any time:
```bash
python - <<'PY'
import asyncio
from app.db import init_db, get_act_log, get_legal_store, get_db
async def main():
    await init_db()
    print('log:', await get_act_log().status_counts())
    print('corpus:', await get_legal_store().corpus_stats())
    await get_db().close()
asyncio.run(main())
PY
```

---

## 4. VERIFY — confirm retrieval surfaces the new act

```bash
python - <<'PY'
import asyncio
from app.db import init_db, get_db, get_legal_store
from app.llm import get_llm
from app.memory.legal_retrieval import LegalKnowledgeRetriever
async def main():
    await init_db()
    r=LegalKnowledgeRetriever(get_legal_store(), get_llm())
    hits=await r.retrieve('<a question that the new act should answer>')
    for h in hits:
        d=h.as_dict(); print(d['jurisdiction'], '::', d['act_name'] or d['title'])
    await get_db().close()
asyncio.run(main())
PY
```
The new act should appear in the top 2–3 for an on-point question.

---

## 5. Scaling reality (read before attempting "all of India")

- There is **no bulk API** on indiacode (no REST/OAI; listings are JS-rendered;
  OpenSearch returns an HTML shell). So collection (step 1) is **per-act** and
  partly manual — web search yields a working `actfile` URL for *most* but not
  all acts.
- Every act embedded **spends OpenAI credits**. 16k chunks ≈ the current corpus;
  a full state-law harvest is hundreds of thousands of chunks → real cost + hours.
- Two ways to go big:
  - **Curated (recommended):** collect the ~60–100 state acts lawyers actually
    use (rent, tenancy, land revenue, cooperative societies, stamp, municipal,
    police, shops & establishments, education, excise) across the major states.
    Bounded cost, covers most real queries.
  - **Full harvest:** drive a browser (Claude-in-Chrome / Playwright) to walk each
    state's act listing, collect handles, derive `showfile` URLs, then batch this
    pipeline. Days of crawling + significant embedding spend.

---

## 6. EVALUATE — is the AI actually *fulfilling* on the corpus?

`scripts/eval_harness.py` measures, end-to-end, whether loading the acts makes the
AI answer with the right controlling statute. It runs the SAME pipeline as
`/chat` (statute + judgment retrieval → prompt → Claude answer → grounding).

```bash
# 1) synthesise questions from the corpus (acts + judgments). The generator skips
#    constituting/validating/amending acts (whose true controlling law is a
#    different Act) so labels stay clean.
python -m scripts.eval_harness generate --n 40 --state-fraction 0.6 --judgment-fraction 0.25
# 2) evaluate the AI against the question bank.
python -m scripts.eval_harness run --n 24 --concurrency 2
# 3) see the fulfillment trend across runs.
python -m scripts.eval_harness report
```

Metrics (written to `scripts/eval/history.jsonl` + a per-run JSON):
- **fulfillment** — judged on the answer's legal quality (cites a correct
  controlling authority, legally correct, no invented law, score≥70). Crediting a
  *more* correct alternative authority than the sampled label — the judge knows
  the 2023 codes (BNS/BNSS/BSA) are real and that honest corpus-gap flags are OK.
- **retrieval_recall** — did retrieval surface the expected Act (the pure
  ingestion/retrieval signal, tracked separately from answer quality).
- citation_rate, hallucination_rate, avg_judge_score, latency.

**DB_POOL_MAX**: the eval defaults its asyncpg pool to 4 connections so it coexists
with a running ingest (8) under the pooler's ~15-connection session cap. Set
`DB_POOL_MAX=8` to run the eval alone. `db.py` reads this env for every pool.

## Quick reference

| Step | Command |
|---|---|
| Preview | `python -m scripts.ingest_all_acts --catalog <file> --dry-run` |
| Validate on 2 | `python -m scripts.ingest_all_acts --catalog <file> --limit 2` |
| Full run | `python -m scripts.ingest_all_acts --catalog <file> --concurrency 10` |
| Retry failures | `python -m scripts.ingest_all_acts --catalog <file> --retry-failed` |
| Status | see step 3 status snippet |
| Verify | see step 4 retrieval snippet |
| Generate eval Qs | `python -m scripts.eval_harness generate --n 40` |
| Run eval | `python -m scripts.eval_harness run --n 24 --concurrency 2` |
| Eval trend | `python -m scripts.eval_harness report` |
