"""Legal Q&A evaluation harness — does the AI *fulfil* on the ingested corpus?

The goal this serves: we loaded thousands of Indian Acts into the corpus so the
AI can answer with the right controlling statute. This harness measures whether
that actually happens, end-to-end, against the SAME retrieval + answer pipeline
the live `/chat` endpoint uses (statute retrieval + judgment retrieval → prompt →
Claude answer → grounding).

Three subcommands:

  generate  — sample acts (and judgments) from the corpus and synthesise realistic
              advocate-style questions whose *controlling authority* is the sampled
              source. The question deliberately never names the Act, so retrieval
              has to find it. Written to scripts/eval/questions.jsonl.

  run       — for each question: run the real pipeline, then score it:
                * retrieval_hit  — expected Act is in the retrieved statutes (the
                                   pure ingest+retrieval signal).
                * cited          — the answer text actually cites the expected Act.
                * judge          — a Claude judge rates the answer (controlling
                                   authority, correctness, limitation flag, no
                                   hallucination) 0-100.
                * grounding      — unsupported-claim ratio from grounding.py.
              A question passes when retrieval_hit AND judge>=bar AND no
              hallucination. Fulfillment = pass rate. Results append to
              scripts/eval/history.jsonl so the improvement loop can see the trend.

  report    — print the latest run + the fulfillment trend across runs.

Examples:
  python -m scripts.eval_harness generate --n 40
  python -m scripts.eval_harness run --n 24 --concurrency 4
  python -m scripts.eval_harness report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Keep the eval's connection footprint small: the Supabase pooler (session mode)
# caps total client connections at ~15 across ALL processes, and a full ingest
# already holds ~8. Cap this process before any pool is created so eval + ingest
# coexist. Override with DB_POOL_MAX=<n> if running the eval alone.
os.environ.setdefault("DB_POOL_MAX", "4")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_db, get_legal_store, get_repo, init_db  # noqa: E402
from app.grounding import ground_answer  # noqa: E402
from app.llm import get_llm  # noqa: E402
from app.memory.corpus_retrieval import CorpusRetriever  # noqa: E402
from app.memory.legal_retrieval import LegalKnowledgeRetriever  # noqa: E402
from app.prompts import SYSTEM, build_user_prompt  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("eval_harness")

EVAL_DIR = Path(__file__).resolve().parents[1] / "scripts" / "eval"
QUESTIONS_PATH = EVAL_DIR / "questions.jsonl"
RUNS_DIR = EVAL_DIR / "runs"
HISTORY_PATH = EVAL_DIR / "history.jsonl"

PASS_BAR = 70  # judge score (0-100) at/above which the substantive answer passes.

# The "standardised goal" — the bar at which we call the AI *fulfilling* on the
# ingested corpus. A run meets the standard when every metric clears its target.
STANDARD = {
    "fulfillment": 0.85,             # >= : answers are legally correct + well-cited
    "retrieval_recall_statute": 0.85,  # >= : the right Act is actually surfaced
    "hallucination_rate": 0.05,      # <= : almost never invents law
    "avg_judge_score": 85.0,         # >= : strong average answer quality
}
_LOWER_IS_BETTER = {"hallucination_rate"}


def _meets_standard(report: dict) -> tuple[bool, list[str]]:
    misses = []
    for k, target in STANDARD.items():
        val = report.get(k, 0)
        ok = (val <= target) if k in _LOWER_IS_BETTER else (val >= target)
        if not ok:
            arrow = "<=" if k in _LOWER_IS_BETTER else ">="
            misses.append(f"{k}={val} (need {arrow}{target})")
    return (not misses), misses


# --------------------------------------------------------------------------- #
# Normalisation helpers — so "THE RAJASTHAN FOREST ACT, 1953" and
# "The Rajasthan Forest Act, 1953" compare equal.
# --------------------------------------------------------------------------- #
_STOP = {"the", "act", "of", "and", "for", "an", "a", "to"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _key_tokens(act_name: str) -> set[str]:
    """Distinctive tokens of an act name (drops stopwords, keeps the year)."""
    toks = {t for t in _norm(act_name).split() if t not in _STOP and len(t) > 2}
    return toks


def _act_match(expected: str, candidate: str) -> bool:
    """True if candidate names the same act as expected — robust to case,
    punctuation and a trailing "(12 of 1994)". Requires the distinctive tokens
    of the shorter name to be a subset of the longer, plus a shared year if the
    expected name carries one."""
    e, c = _key_tokens(expected), _key_tokens(candidate)
    if not e or not c:
        return False
    ey = {t for t in e if re.fullmatch(r"(1[89]|20)\d{2}", t)}
    cy = {t for t in c if re.fullmatch(r"(1[89]|20)\d{2}", t)}
    if ey and cy and not (ey & cy):
        return False  # different years -> different act
    small, big = (e, c) if len(e) <= len(c) else (c, e)
    overlap = len(small & big) / max(1, len(small))
    return overlap >= 0.8


# --------------------------------------------------------------------------- #
# GENERATE
# --------------------------------------------------------------------------- #
_GEN_STATUTE_SYSTEM = (
    "You write realistic questions an Indian advocate would actually ask a legal "
    "research assistant, for evaluating a legal AI. You are given an excerpt from a "
    "single Indian Act. \n"
    "FIRST decide if this Act is a good evaluation target. Set \"usable\": false if "
    "the Act is merely a CONSTITUTING statute (sets up a university/commission/board "
    "and little else), a VALIDATING/AMENDING/REPEALING/short-title Act, a taxation "
    "schedule, or otherwise does NOT by itself govern a concrete dispute a citizen "
    "would bring — because then the true controlling authority for any realistic "
    "scenario is a DIFFERENT Act, which would make the evaluation label wrong.\n"
    "If usable, write ONE concrete client-situation question for which THIS Act is "
    "genuinely THE controlling authority (not a Central Act that overrides it, not a "
    "general statute). Rules: (1) do NOT name the Act, its number or year in the "
    "question; (2) a specific factual scenario, not 'what does the law say'; (3) it "
    "must be answerable specifically from THIS Act's operative provisions shown in "
    "the excerpt. Also give the single controlling legal point the answer must make.\n"
    'Return ONLY JSON: {"usable": bool, "question": "...", "controlling_point": "..."}.'
)

_GEN_JUDGMENT_SYSTEM = (
    "You write realistic questions an Indian advocate would ask a legal research "
    "assistant, such that the given case is the leading authority on point. You "
    "are given a case title and its ratio/headnote. Write ONE concrete question "
    "about the legal issue the case decides. Do NOT name the case or its parties. "
    'Return ONLY JSON: {"question": "...", "controlling_point": "..."}.'
)


async def _sample_act_chunks(conn, n: int, state_fraction: float) -> list[dict]:
    """Sample n acts, each with one substantive chunk. Split between state acts
    (jurisdiction != 'India' — the newly ingested corpus) and central acts, so we
    test both. Prefers a mid-document chunk with real operative text."""
    n_state = int(round(n * state_fraction))
    n_central = n - n_state
    rows: list[dict] = []
    for juris_pred, want in (("jurisdiction <> 'India'", n_state),
                             ("jurisdiction = 'India'", n_central)):
        if want <= 0:
            continue
        # Step 1: pick a bounded random set of candidate acts (cheap — a few
        # thousand rows in legal_knowledge).
        acts = await conn.fetch(
            f"""
            SELECT id, act_name, title, jurisdiction, category
            FROM memchat.legal_knowledge
            WHERE source_type = 'act' AND {juris_pred}
            ORDER BY random() LIMIT $1
            """,
            want * 3,
        )
        if not acts:
            continue
        ids = [a["id"] for a in acts]
        # Step 2: one substantive chunk per candidate act.
        chunks = await conn.fetch(
            """
            SELECT DISTINCT ON (knowledge_id) knowledge_id, content
            FROM memchat.legal_knowledge_chunks
            WHERE knowledge_id = ANY($1::text[]) AND length(content) > 700
            ORDER BY knowledge_id, random()
            """,
            ids,
        )
        by_id = {c["knowledge_id"]: c["content"] for c in chunks}
        picked = 0
        for a in acts:
            if picked >= want:
                break
            content = by_id.get(a["id"])
            if not content:
                continue
            rows.append({**dict(a), "content": content})
            picked += 1
    return rows


async def _sample_judgments(conn_repo, n: int) -> list[dict]:
    """Sample n judgments with usable ratio text from the judgments table."""
    db = get_db()
    if not db.pool or n <= 0:
        return []
    async with db.pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT judgment_id::text AS judgment_id, case_title,
                       ratio_decidendi, headnotes
                FROM public.judgments_metadata
                WHERE ratio_decidendi IS NOT NULL AND length(ratio_decidendi) > 200
                ORDER BY random()
                LIMIT $1
                """,
                n,
            )
        except Exception as exc:  # noqa: BLE001 - schema may differ; judgments optional
            logger.warning("judgment sampling skipped: %s", exc)
            return []
    return [dict(r) for r in rows]


async def generate(args: argparse.Namespace) -> int:
    await init_db()
    llm = get_llm()
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    db = get_db()
    async with db.pool.acquire() as conn:
        n_statute = args.n - int(round(args.n * args.judgment_fraction))
        act_rows = await _sample_act_chunks(conn, n_statute, args.state_fraction)
    jud_rows = await _sample_judgments(None, args.n - n_statute)

    logger.info("Sampled %d acts + %d judgments for question generation.",
                len(act_rows), len(jud_rows))

    sem = asyncio.Semaphore(args.concurrency)
    questions: list[dict] = []

    async def _gen_statute(row: dict, idx: int) -> Optional[dict]:
        async with sem:
            excerpt = (row["content"] or "")[:2500]
            user = (
                f"Act: {row['act_name'] or row['title']}\n"
                f"Jurisdiction: {row['jurisdiction']}\n\nExcerpt:\n{excerpt}"
            )
            try:
                data = await llm.complete_json(_GEN_STATUTE_SYSTEM, user, max_tokens=400)
                if not (data.get("question")):
                    out = await llm.complete(_GEN_STATUTE_SYSTEM, user, fast=True, max_tokens=400)
                    data = _loads(out)
            except Exception as exc:  # noqa: BLE001
                logger.warning("gen failed for %s: %s", row["id"], exc)
                return None
            q = (data.get("question") or "").strip()
            if not q or data.get("usable") is False:
                return None
            return {
                "id": f"stat-{idx}",
                "kind": "statute",
                "question": q,
                "controlling_point": (data.get("controlling_point") or "").strip(),
                "expected_act": row["act_name"] or row["title"],
                "expected_jurisdiction": row["jurisdiction"],
                "source_doc_id": row["id"],
            }

    async def _gen_judgment(row: dict, idx: int) -> Optional[dict]:
        async with sem:
            user = (
                f"Case: {row['case_title']}\n\nRatio/Headnote:\n"
                f"{(row.get('ratio_decidendi') or '')[:1500]}"
            )
            try:
                data = await llm.complete_json(_GEN_JUDGMENT_SYSTEM, user, max_tokens=400)
            except Exception as exc:  # noqa: BLE001
                logger.warning("gen judgment failed: %s", exc)
                return None
            q = (data.get("question") or "").strip()
            if not q:
                return None
            return {
                "id": f"jud-{idx}",
                "kind": "judgment",
                "question": q,
                "controlling_point": (data.get("controlling_point") or "").strip(),
                "expected_case": row["case_title"],
                "source_judgment_id": row["judgment_id"],
            }

    tasks = [_gen_statute(r, i) for i, r in enumerate(act_rows)]
    tasks += [_gen_judgment(r, i) for i, r in enumerate(jud_rows)]
    for coro in asyncio.as_completed(tasks):
        q = await coro
        if q:
            questions.append(q)

    # Append to the bank (dedup by question text) so successive generate runs
    # grow a diverse pool rather than overwrite it.
    existing = _read_jsonl(QUESTIONS_PATH)
    seen = {_norm(e["question"]) for e in existing}
    fresh = [q for q in questions if _norm(q["question"]) not in seen]
    with QUESTIONS_PATH.open("a", encoding="utf-8") as fh:
        for q in fresh:
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")
    logger.info("Generated %d questions (%d new) -> %s (bank now %d).",
                len(questions), len(fresh), QUESTIONS_PATH, len(existing) + len(fresh))
    await get_db().close()
    return 0


# --------------------------------------------------------------------------- #
# RUN + JUDGE
# --------------------------------------------------------------------------- #
_JUDGE_SYSTEM = (
    "You are a strict senior Indian advocate grading a junior's answer to a legal "
    "question. You are given the QUESTION, the CONTROLLING POINT the answer had to "
    "make, an EXPECTED controlling authority (a HINT — it may be imperfect), and the "
    "ANSWER.\n"
    "IMPORTANT context so you grade fairly:\n"
    "• India enacted new criminal codes in 2023 that ARE in force — the Bharatiya "
    "Nyaya Sanhita (BNS) 2023 (replaced IPC), Bharatiya Nagarik Suraksha Sanhita "
    "(BNSS) 2023 (replaced CrPC), and Bharatiya Sakshya Adhiniyam (BSA) 2023 "
    "(replaced the Evidence Act). Citing these is CORRECT, never a hallucination.\n"
    "• The EXPECTED authority is only a hint. If the answer identifies a DIFFERENT "
    "but legally-correct controlling authority (e.g. a later Central Act that "
    "repealed/overrides the expected State Act, or the precise constitutional "
    "provision), treat cites_controlling as TRUE.\n"
    "• Honestly stating that the controlling statute is NOT in the provided "
    "materials (a corpus-gap flag) is GOOD practice, NOT a hallucination.\n"
    "Return ONLY JSON with these keys:\n"
    '  "cites_controlling": bool  — does the answer identify/apply a correct '
    "controlling authority for the scenario?\n"
    '  "legally_correct": bool    — is the substantive legal position correct?\n'
    '  "limitation_flagged": bool — does it flag a limitation period where a '
    "dispute is involved (false if N/A)?\n"
    '  "hallucination": bool      — does it INVENT a statute/section/case that does '
    "not exist, or state a confidently wrong rule? (Real 2023 codes and honest "
    "corpus-gap flags are NOT hallucinations.)\n"
    '  "score": int               — overall 0-100 usefulness to the advocate.\n'
    '  "notes": str               — one terse sentence.\n'
    "Reward the correct controlling law + honest scope. Punish invented law and "
    "confidently wrong rules."
)


@dataclass
class QResult:
    qid: str
    kind: str
    question: str
    expected: str
    retrieval_hit: bool = False
    retrieved_acts: list[str] = field(default_factory=list)
    cited: bool = False
    judge: dict = field(default_factory=dict)
    grounding_unsupported: int = 0
    grounding_total: int = 0
    latency_ms: int = 0
    passed: bool = False
    answer_preview: str = ""
    error: str = ""


async def _answer_once(message: str, statute_r, corpus_r, llm) -> tuple[str, list, list]:
    """Mirror the /chat pipeline: retrieve statutes + judgments, build the prompt,
    get Claude's answer (non-streaming here)."""
    statutes, retrieved = await asyncio.gather(
        statute_r.retrieve(message),
        corpus_r.retrieve(message),
    )
    prompt = build_user_prompt(message, [], retrieved, statutes)
    answer = await llm.complete(SYSTEM, prompt, fast=False, max_tokens=2000)
    return answer, statutes, retrieved


async def _judge(llm, q: dict, answer: str) -> dict:
    expected = q.get("expected_act") or q.get("expected_case") or ""
    user = (
        f"QUESTION:\n{q['question']}\n\n"
        f"CONTROLLING POINT:\n{q.get('controlling_point') or '(unspecified)'}\n\n"
        f"EXPECTED CONTROLLING AUTHORITY:\n{expected}\n\n"
        f"ANSWER:\n{answer[:6000]}"
    )
    try:
        data = await llm.complete_json(_JUDGE_SYSTEM, user, max_tokens=400)
        if not data:
            out = await llm.complete(_JUDGE_SYSTEM, user, fast=False, max_tokens=400)
            data = _loads(out)
    except Exception as exc:  # noqa: BLE001
        return {"score": 0, "error": str(exc)}
    return data


async def _eval_one(q: dict, *, statute_r, corpus_r, llm, sem) -> QResult:
    expected = q.get("expected_act") or q.get("expected_case") or ""
    res = QResult(qid=q["id"], kind=q["kind"], question=q["question"], expected=expected)
    async with sem:
        t0 = time.time()
        try:
            answer, statutes, retrieved = await _answer_once(q["question"], statute_r, corpus_r, llm)
        except Exception as exc:  # noqa: BLE001
            res.error = repr(exc)
            return res
        res.latency_ms = int((time.time() - t0) * 1000)
        res.answer_preview = answer[:280]

        if q["kind"] == "statute":
            res.retrieved_acts = [s.act_name or s.title for s in statutes]
            res.retrieval_hit = any(_act_match(expected, a) for a in res.retrieved_acts)
        else:
            titles = [r.case_title for r in retrieved]
            res.retrieved_acts = titles
            res.retrieval_hit = any(_act_match(expected, t) for t in titles)

        # citation check: a strong majority of the expected act's distinctive
        # tokens (including its year) appear in the answer text.
        res.cited = _cited_in(expected, answer)

        # grounding over the judgment sources (same as live path). `warnings` are
        # citations the answer makes that could NOT be verified against the corpus
        # — the hallucination signal; `verified` are the ones that matched.
        g = ground_answer(answer, [r.as_dict() for r in retrieved])
        res.grounding_total = len(g.verified) + len(g.warnings)
        res.grounding_unsupported = len(g.warnings)

        res.judge = await _judge(llm, q, answer)
        score = int(res.judge.get("score") or 0)
        halluc = bool(res.judge.get("hallucination"))
        # Fulfillment is judged on the ANSWER's legal quality (right controlling
        # authority, correct, grounded), not on exact-matching the possibly-noisy
        # expected label — an answer that cites a MORE correct authority still
        # fulfils. Retrieval recall (did we surface the expected act) is tracked
        # separately as the corpus/ingestion signal.
        res.passed = (
            not halluc
            and bool(res.judge.get("cites_controlling"))
            and bool(res.judge.get("legally_correct"))
            and score >= PASS_BAR
        )
    return res


def _cited_in(expected: str, answer: str) -> bool:
    toks = _key_tokens(expected)
    if not toks:
        return False
    na = _norm(answer)
    present = sum(1 for t in toks if t in na)
    return present / max(1, len(toks)) >= 0.6


async def run(args: argparse.Namespace) -> int:
    db = await init_db()
    llm = get_llm()
    # Initialise the memory backend so judgment retrieval's corpus-recall step
    # works (otherwise it logs "init_memory() not called" and falls back to
    # hybrid-only, weakening judgment questions).
    try:
        from app.memory import user_memory

        user_memory.init_memory(db, llm)
    except Exception as exc:  # noqa: BLE001 - non-fatal; hybrid still runs
        logger.warning("init_memory failed (judgment recall degraded): %s", exc)
    statute_r = LegalKnowledgeRetriever(get_legal_store(), llm)
    corpus_r = CorpusRetriever(get_repo(), llm)

    bank = _read_jsonl(QUESTIONS_PATH)
    if not bank:
        logger.error("No questions in %s — run `generate` first.", QUESTIONS_PATH)
        return 2
    import random as _r

    _r.shuffle(bank)
    if args.kind:
        bank = [q for q in bank if q["kind"] == args.kind]
    sample = bank[: args.n] if args.n else bank

    sem = asyncio.Semaphore(args.concurrency)
    logger.info("Evaluating %d questions (concurrency %d, pass bar %d)…",
                len(sample), args.concurrency, PASS_BAR)
    tasks = [_eval_one(q, statute_r=statute_r, corpus_r=corpus_r, llm=llm, sem=sem)
             for q in sample]
    results: list[QResult] = []
    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        r = await coro
        results.append(r)
        if i % 5 == 0 or i == len(tasks):
            logger.info("  %d/%d done", i, len(tasks))

    report = _summarise(results)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = int(time.time())
    run_path = RUNS_DIR / f"run_{run_id}.json"
    run_path.write_text(json.dumps({
        "run_id": run_id,
        "summary": report,
        "results": [r.__dict__ for r in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    with HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_id": run_id, **report}) + "\n")

    _print_report(report, results)
    logger.info("Full run written to %s", run_path)
    await get_db().close()
    return 0


def _summarise(results: list[QResult]) -> dict:
    n = len(results) or 1
    ok = [r for r in results if not r.error]
    stat = [r for r in ok if r.kind == "statute"]
    scores = [int(r.judge.get("score") or 0) for r in ok]
    return {
        "n": len(results),
        "errors": sum(1 for r in results if r.error),
        "fulfillment": round(sum(1 for r in results if r.passed) / n, 3),
        "retrieval_recall": round(sum(1 for r in results if r.retrieval_hit) / n, 3),
        "retrieval_recall_statute": round(
            (sum(1 for r in stat if r.retrieval_hit) / len(stat)) if stat else 0, 3),
        "citation_rate": round(sum(1 for r in results if r.cited) / n, 3),
        "hallucination_rate": round(
            sum(1 for r in ok if r.judge.get("hallucination")) / max(1, len(ok)), 3),
        "avg_judge_score": round(sum(scores) / max(1, len(scores)), 1),
        "avg_latency_ms": int(sum(r.latency_ms for r in ok) / max(1, len(ok))),
    }


def _print_report(report: dict, results: list[QResult]) -> None:
    print("\n" + "=" * 68)
    print("  FULFILLMENT REPORT")
    print("=" * 68)
    for k in ("n", "errors", "fulfillment", "retrieval_recall",
              "retrieval_recall_statute", "citation_rate", "hallucination_rate",
              "avg_judge_score", "avg_latency_ms"):
        print(f"  {k:26} : {report[k]}")
    print("-" * 68)
    fails = [r for r in results if not r.passed and not r.error]
    print(f"  {len(fails)} failures (first 8):")
    for r in fails[:8]:
        reason = ("retrieval-miss" if not r.retrieval_hit
                  else "hallucination" if r.judge.get("hallucination")
                  else f"low-score({r.judge.get('score')})")
        print(f"   [{reason:16}] {r.expected[:44]:44} :: {r.question[:60]}")
    print("-" * 68)
    met, misses = _meets_standard(report)
    print("  STANDARD: " + ("[MET] the AI is fulfilling."
                            if met else f"[NOT MET] gaps: {'; '.join(misses)}"))
    print("=" * 68 + "\n")


# --------------------------------------------------------------------------- #
# REPORT
# --------------------------------------------------------------------------- #
def report_cmd(args: argparse.Namespace) -> int:
    hist = _read_jsonl(HISTORY_PATH)
    if not hist:
        print("No eval history yet. Run `generate` then `run`.")
        return 0
    print("\nFulfillment trend (most recent last):")
    print(f"  {'run':>12}  {'n':>4}  {'fulfil':>7}  {'recall':>7}  {'cite':>6}  "
          f"{'halluc':>7}  {'judge':>6}")
    for h in hist[-15:]:
        print(f"  {h['run_id']:>12}  {h.get('n',0):>4}  "
              f"{h.get('fulfillment',0):>7}  {h.get('retrieval_recall',0):>7}  "
              f"{h.get('citation_rate',0):>6}  {h.get('hallucination_rate',0):>7}  "
              f"{h.get('avg_judge_score',0):>6}")
    latest = hist[-1]
    met, misses = _meets_standard(latest)
    print("\nSTANDARD (fulfilling bar): " +
          ", ".join(f"{k}{'<=' if k in _LOWER_IS_BETTER else '>='}{v}"
                    for k, v in STANDARD.items()))
    if met:
        print(f"\n[STANDARD MET] on latest run ({latest['run_id']}). The AI is "
              f"fulfilling on the ingested corpus.")
    else:
        print(f"\n[NOT YET MET] gaps: {'; '.join(misses)}")
    return 0


# --------------------------------------------------------------------------- #
# util
# --------------------------------------------------------------------------- #
def _loads(text: str) -> dict:
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    return out


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Legal Q&A fulfillment eval harness.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="synthesise questions from the corpus")
    g.add_argument("--n", type=int, default=40)
    g.add_argument("--concurrency", type=int, default=6)
    g.add_argument("--state-fraction", type=float, default=0.6,
                   help="fraction of statute questions drawn from State acts")
    g.add_argument("--judgment-fraction", type=float, default=0.25,
                   help="fraction of questions drawn from judgments")
    g.set_defaults(func=lambda a: asyncio.run(generate(a)))

    r = sub.add_parser("run", help="evaluate the AI against the question bank")
    r.add_argument("--n", type=int, default=24)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--kind", choices=["statute", "judgment"], default=None)
    r.set_defaults(func=lambda a: asyncio.run(run(a)))

    rp = sub.add_parser("report", help="print the fulfillment trend")
    rp.set_defaults(func=report_cmd)

    args = ap.parse_args(argv)
    rc = args.func(args)
    raise SystemExit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
