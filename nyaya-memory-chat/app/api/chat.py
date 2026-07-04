"""Chat + history + graph endpoints.

POST /chat streams NDJSON events:
  {"type":"meta",    session_id, recalled:[...facts]}        # what was recalled
  {"type":"sources", sources:[{judgment...}], warnings_pre}  # grounding context
  {"type":"token",   text}                                    # streamed answer
  {"type":"final",   answer, sources:[ids], warnings:[...], memory:{...}}
  {"type":"done"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import Counter
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..auth import resolve_user_id
from ..db import get_legal_store, get_repo, get_store
from ..grounding import ground_answer
from ..llm import get_llm
from ..memory import user_memory
from ..memory.corpus_retrieval import CorpusRetriever
from ..memory.legal_retrieval import LegalKnowledgeRetriever
from ..prompts import SYSTEM, build_user_prompt

logger = logging.getLogger("nyaya.api.chat")
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None  # ignored — the session cookie is authoritative
    session_id: Optional[str] = None


class JudgmentAskRequest(BaseModel):
    message: str
    user_id: Optional[str] = None  # ignored — the session cookie is authoritative


def _ndjson(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


@router.post("/chat")
async def chat(req: ChatRequest, background: BackgroundTasks, request: Request):
    # The user is authoritative from the session cookie — never the request body.
    user_id = await resolve_user_id(request)
    if not user_id:
        return JSONResponse({"error": "authentication required"}, status_code=401)
    message = req.message.strip()
    session_id = req.session_id or f"s_{uuid.uuid4().hex[:10]}"
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    store = get_store()
    repo = get_repo()
    llm = get_llm()
    retriever = CorpusRetriever(repo, llm)
    statute_retriever = LegalKnowledgeRetriever(get_legal_store(), llm)

    async def event_stream() -> AsyncIterator[str]:
        t0 = time.time()
        # 1) recall the user's matter (cross-session memory).
        recalled = await user_memory.recall_user(user_id, session_id, message)
        yield _ndjson({"type": "meta", "session_id": session_id, "recalled": recalled})

        # 2) retrieve corpus grounding context. Fold the user's remembered matter
        #    into the retrieval query so follow-ups ("what should I do next?")
        #    ground against their actual matter, not just the bare question.
        retrieval_query = message
        if recalled:
            retrieval_query = f"{message}\ncontext: " + "; ".join(recalled[:5])
        # Judgments (curated corpus) + statutes (ingested Central Acts) in parallel.
        retrieved, statutes = await asyncio.gather(
            retriever.retrieve(retrieval_query),
            statute_retriever.retrieve(retrieval_query),
        )
        yield _ndjson(
            {
                "type": "sources",
                "sources": [r.as_dict() for r in retrieved],
                "statutes": [s.as_dict() for s in statutes],
                "retrieval_ms": int((time.time() - t0) * 1000),
            }
        )

        # 3) compose with Claude (Cognee retrieved, Claude composes).
        prompt = build_user_prompt(message, recalled, retrieved, statutes)
        answer_parts: list[str] = []
        try:
            async for tok in llm.stream_answer(SYSTEM, prompt):
                answer_parts.append(tok)
                yield _ndjson({"type": "token", "text": tok})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Claude streaming failed")
            yield _ndjson({"type": "token", "text": f"\n\n[answer generation failed: {exc}]"})
        answer = "".join(answer_parts)

        # 4) ground the answer (the trust feature).
        retrieved_sources = [r.as_dict() for r in retrieved]
        grounding = ground_answer(answer, retrieved_sources)

        # 5) refreshed memory view (after this turn will be remembered).
        try:
            mv = await user_memory.memory_view(user_id)
            memory_payload = {
                "sub": mv.sub, "tokens": mv.tokens, "empty": mv.empty, "groups": mv.groups,
            }
        except Exception:  # noqa: BLE001
            memory_payload = {"empty": True, "groups": []}

        yield _ndjson(
            {
                "type": "final",
                "answer": answer,
                "sources": grounding.sources,
                "warnings": grounding.warnings,
                "verified": grounding.verified,
                "memory": memory_payload,
                "total_ms": int((time.time() - t0) * 1000),
            }
        )

        # 6) persist (audit log) + remember in the background (never blocks).
        await store.ensure_session(session_id, user_id, title=_title_from(message))
        uid_msg = f"m_{uuid.uuid4().hex[:12]}"
        aid_msg = f"m_{uuid.uuid4().hex[:12]}"
        await store.save_message(uid_msg, session_id, user_id, "user", message)
        await store.save_message(
            aid_msg, session_id, user_id, "assistant", answer,
            sources=retrieved_sources, warnings=grounding.warnings,
        )
        background.add_task(user_memory.remember_turn, user_id, session_id, "user", message)
        background.add_task(user_memory.remember_turn, user_id, session_id, "assistant", answer)

        yield _ndjson({"type": "done"})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


def _title_from(message: str) -> str:
    t = message.strip().split("\n")[0]
    return (t[:48] + "…") if len(t) > 48 else t


# --------------------------------------------------------------------------- #
#  history / memory / graph
# --------------------------------------------------------------------------- #
async def _require_owner(request: Request, user_id: str):
    """Returns (auth_id, error_response). The session user must equal the
    requested user_id — a logged-in user can only read their own data."""
    auth_id = await resolve_user_id(request)
    if not auth_id:
        return None, JSONResponse({"error": "authentication required"}, status_code=401)
    if auth_id != user_id:
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    return auth_id, None


@router.get("/sessions/{user_id}")
async def get_sessions(user_id: str, request: Request):
    _, err = await _require_owner(request, user_id)
    if err:
        return err
    sessions = await get_store().list_sessions(user_id)
    return {"user_id": user_id, "sessions": sessions}


@router.get("/sessions/{user_id}/{session_id}")
async def get_session_messages(user_id: str, session_id: str, request: Request):
    _, err = await _require_owner(request, user_id)
    if err:
        return err
    msgs = await get_store().list_messages(session_id)
    source_ids: list[str] = []
    for msg in msgs:
        for src in msg.get("sources") or []:
            if isinstance(src, str):
                source_ids.append(src)
            elif isinstance(src, dict) and src.get("judgment_id"):
                source_ids.append(str(src["judgment_id"]))
    hydrated: dict[str, dict[str, Any]] = {}
    if source_ids:
        metas = await get_repo().get_metadata(sorted(set(source_ids)))
        hydrated = {str(m["judgment_id"]): _source_from_meta(m) for m in metas}
    session_sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for msg in msgs:
        normalized_sources = []
        for src in msg.get("sources") or []:
            if isinstance(src, dict):
                source = src
            else:
                source = hydrated.get(str(src))
            if not source:
                continue
            normalized_sources.append(source)
            jid = str(source.get("judgment_id") or "")
            if jid and jid not in seen:
                seen.add(jid)
                session_sources.append(source)
        msg["sources"] = normalized_sources
    return {"session_id": session_id, "messages": msgs, "sources": session_sources}


@router.get("/memory/{user_id}")
async def get_memory(user_id: str, request: Request):
    _, err = await _require_owner(request, user_id)
    if err:
        return err
    mv = await user_memory.memory_view(user_id)
    return {
        "user_id": user_id,
        "sub": mv.sub, "tokens": mv.tokens, "empty": mv.empty, "groups": mv.groups,
    }


@router.get("/graph/{user_id}")
async def get_user_graph(user_id: str, request: Request):
    _, err = await _require_owner(request, user_id)
    if err:
        return err
    g = await user_memory.user_graph(user_id)
    return {"user_id": user_id, **g}


@router.get("/graph-corpus/{judgment_id}")
async def get_corpus_graph(judgment_id: str, depth: int = 1):
    """Citation subgraph around a judgment, built from public.judgment_citations
    (real curated edges) — the corpus knowledge graph."""
    repo = get_repo()
    cits = await repo.get_citations(judgment_id)
    meta = await repo.get_metadata([judgment_id])
    root_title = meta[0]["case_title"] if meta else judgment_id

    nodes = {judgment_id: {"id": judgment_id, "label": _short(root_title), "sub": "root", "kind": "act"}}
    edges = []
    for c in cits:
        citing = str(c["citing_id"])
        cited = str(c["cited_id"]) if c.get("cited_id") else None
        cite_str = (c.get("cited_citation") or "").strip()
        ctx_name = (c.get("context") or "").split(" - ")[0].strip()
        # Prefer a case-name-looking context, else the citation string.
        is_caselike = " v" in ctx_name.lower() and len(ctx_name) <= 48
        label = ctx_name if is_caselike else (cite_str or ctx_name or "cited")
        sub = cite_str if (is_caselike and cite_str) else (c.get("citation_type") or "")
        if cited:
            nodes.setdefault(cited, {"id": cited, "label": _short(label), "sub": sub, "kind": "good"})
            edges.append({"src": citing, "dst": cited})
        else:
            ext_id = "ext_" + str(c["id"])
            nodes[ext_id] = {"id": ext_id, "label": _short(label), "sub": sub, "kind": "good"}
            edges.append({"src": citing, "dst": ext_id})
    return {"judgment_id": judgment_id, "nodes": list(nodes.values()), "edges": edges}


@router.get("/judgments/{judgment_id}")
async def get_judgment(judgment_id: str):
    """Full judgment detail for the source inspector."""
    repo = get_repo()
    meta_rows = await repo.get_metadata([judgment_id])
    if not meta_rows:
        return JSONResponse({"error": "judgment not found"}, status_code=404)
    meta = meta_rows[0]
    pages = await repo.get_pages(judgment_id)
    citations = await repo.get_citations(judgment_id)
    source = _source_from_meta(meta)
    full_text = "\n\n".join(
        f"Page {p.get('page_number')}: {p.get('text') or ''}".strip() for p in pages
    )
    return {
        "judgment_id": judgment_id,
        "source": source,
        "metadata": meta,
        "pages": pages,
        "citations": citations,
        "analytics": _judgment_analytics(judgment_id, meta, pages, citations, full_text),
        "page_count": len(pages),
        "text_preview": full_text[:5000],
        "full_text": full_text,
        "text_chars": len(full_text),
    }


@router.post("/judgments/{judgment_id}/ask")
async def ask_judgment(judgment_id: str, req: JudgmentAskRequest, request: Request):
    """Ask a question against one selected judgment only."""
    user_id = await resolve_user_id(request)
    if not user_id:
        return JSONResponse({"error": "authentication required"}, status_code=401)
    message = req.message.strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    repo = get_repo()
    meta_rows = await repo.get_metadata([judgment_id])
    if not meta_rows:
        return JSONResponse({"error": "judgment not found"}, status_code=404)
    meta = meta_rows[0]
    pages = await repo.get_pages(judgment_id)
    source = _source_from_meta(meta)
    llm = get_llm()

    async def event_stream() -> AsyncIterator[str]:
        yield _ndjson({"type": "source", "source": source})
        prompt = _build_judgment_prompt(message, meta, pages)
        answer_parts: list[str] = []
        try:
            async for tok in llm.stream_answer(SYSTEM, prompt, max_tokens=1400):
                answer_parts.append(tok)
                yield _ndjson({"type": "token", "text": tok})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Judgment Q&A streaming failed")
            yield _ndjson({"type": "token", "text": f"\n\n[answer generation failed: {exc}]"})
        answer = "".join(answer_parts)
        grounding = ground_answer(answer, [source])
        yield _ndjson(
            {
                "type": "final",
                "answer": answer,
                "sources": [judgment_id],
                "warnings": grounding.warnings,
                "verified": grounding.verified,
            }
        )
        yield _ndjson({"type": "done"})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


def _short(s: str, n: int = 16) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _source_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    cls = meta.get("current_law_status")
    still_good = bool(cls.get("ratio_still_good_law", True)) if isinstance(cls, dict) else True
    court = meta.get("court_name") or meta.get("court_level") or meta.get("court_type") or ""
    judgment_date = str(meta.get("judgment_date") or "") or None
    courtdate = " · ".join(b for b in [court, judgment_date[:4] if judgment_date else ""] if b)
    return {
        "judgment_id": str(meta.get("judgment_id") or ""),
        "case_title": meta.get("case_title") or "",
        "citation": meta.get("citation"),
        "neutral_citation": meta.get("neutral_citation"),
        "court": court,
        "courtdate": courtdate,
        "judgment_date": judgment_date,
        "ratio_decidendi": meta.get("ratio_decidendi") or "",
        "ratio": meta.get("ratio_decidendi") or "",
        "headnotes": meta.get("headnotes") or "",
        "still_good_law": still_good,
        "good": still_good,
    }


def _build_judgment_prompt(message: str, meta: dict[str, Any], pages: list[dict[str, Any]]) -> str:
    source = _source_from_meta(meta)
    status = "GOOD LAW" if source["still_good_law"] else "OVERRULED / no longer good law"
    page_text = "\n\n".join(
        f"[Page {p.get('page_number')}]\n{p.get('text') or ''}".strip() for p in pages
    )
    if len(page_text) > 28000:
        page_text = page_text[:28000] + "\n\n[truncated for model context]"
    return f"""You are answering questions about ONE selected Indian judgment.

RULES:
1. Use ONLY this selected judgment. Do not use outside case law.
2. If the selected judgment does not answer the question, say so plainly.
3. Cite the selected judgment inline as [{source['case_title']}, {source.get('judgment_date') or 'n.d.'}, {source.get('court') or 'Court'}].
4. Mention page numbers when the page text supports a specific point.
5. If the judgment is marked overruled or no longer good law, warn the reader.

SELECTED JUDGMENT:
- Case title: {source['case_title']}
- Citation: {source.get('citation') or 'n/a'}
- Neutral citation: {source.get('neutral_citation') or 'n/a'}
- Court / Date: {source.get('courtdate') or 'n/a'}
- Status: {status}
- Ratio decidendi: {source.get('ratio_decidendi') or 'n/a'}
- Headnotes: {source.get('headnotes') or 'n/a'}

JUDGMENT TEXT:
{page_text or '(No page text available.)'}

USER QUESTION:
{message}

Write a practical, grounded answer from this judgment only."""


def _judgment_analytics(
    judgment_id: str,
    meta: dict[str, Any],
    pages: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    full_text: str,
) -> dict[str, Any]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", full_text.lower())
    stop = {
        "the", "and", "for", "that", "this", "with", "from", "were", "been", "have",
        "has", "not", "are", "was", "his", "her", "their", "there", "which", "court",
        "appellant", "respondent", "petitioner", "case", "judgment", "order", "shall",
    }
    terms = [
        {"term": term, "count": count}
        for term, count in Counter(w for w in words if w not in stop).most_common(10)
    ]
    outgoing = [c for c in citations if str(c.get("citing_id")) == str(judgment_id)]
    incoming = [c for c in citations if str(c.get("cited_id")) == str(judgment_id)]
    external = [c for c in citations if not c.get("cited_id")]
    current_status = meta.get("current_law_status") if isinstance(meta.get("current_law_status"), dict) else {}
    return {
        "pages": len(pages),
        "characters": len(full_text),
        "words": len(words),
        "citations_total": len(citations),
        "citations_outgoing": len(outgoing),
        "citations_incoming": len(incoming),
        "citations_external": len(external),
        "has_ratio": bool(meta.get("ratio_decidendi")),
        "has_headnotes": bool(meta.get("headnotes")),
        "ratio_still_good_law": bool(current_status.get("ratio_still_good_law", True)),
        "overruled_by": current_status.get("overruled_by"),
        "top_terms": terms,
    }
