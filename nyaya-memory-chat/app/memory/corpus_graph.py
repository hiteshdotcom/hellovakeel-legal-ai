"""Typed Cognee DataPoints for the judgments corpus + a builder that wires our
*curated* citation edges (from public.judgment_citations) into real graph edges.

This is the whole point of dropping to Cognee's low-level API: the `cites` edges
come from our citation table, not from an LLM guess.

Import paths shift between Cognee versions, so `DataPoint`/`Edge` are imported
defensively. If Cognee is not installed the module still imports (the builder
returns plain dicts) so the rest of the service and the tests load fine.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("nyaya.memory.corpus_graph")

# --------------------------------------------------------------------------- #
#  Defensive Cognee imports
# --------------------------------------------------------------------------- #
COGNEE_AVAILABLE = False
DataPoint = object  # type: ignore

try:  # pragma: no cover - exercised only when cognee is installed
    try:
        from cognee.low_level import DataPoint  # type: ignore
    except Exception:  # noqa: BLE001
        from cognee.infrastructure.engine import DataPoint  # type: ignore
    COGNEE_AVAILABLE = True
except Exception:  # noqa: BLE001
    logger.info("cognee not installed — corpus_graph DataPoints unavailable (using dict fallback).")


def _to_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            # Postgres text[] occasionally arrives as a python-list-looking str.
            import ast
            try:
                return [str(x) for x in ast.literal_eval(s)]
            except Exception:  # noqa: BLE001
                return [s]
        return [s] if s else []
    return [str(v)]


def _still_good_law(meta: dict[str, Any]) -> bool:
    cls = meta.get("current_law_status")
    if isinstance(cls, dict):
        return bool(cls.get("ratio_still_good_law", True))
    return True


# --------------------------------------------------------------------------- #
#  DataPoint models — only defined when cognee is present.
# --------------------------------------------------------------------------- #
if COGNEE_AVAILABLE:

    class Court(DataPoint):  # type: ignore[misc]
        name: str
        level: str = ""
        metadata: dict = {"index_fields": ["name"]}

    class Act(DataPoint):  # type: ignore[misc]
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class SubjectTag(DataPoint):  # type: ignore[misc]
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class CitedAuthority(DataPoint):  # type: ignore[misc]
        """An external case referenced by citation string (cited_id is almost
        always NULL in our data, so most authorities are external)."""
        citation: str
        name: str = ""
        metadata: dict = {"index_fields": ["name", "citation"]}

    class Judgment(DataPoint):  # type: ignore[misc]
        judgment_id: str
        case_title: str
        citation: Optional[str] = None
        court: Optional[Court] = None
        judgment_date: Optional[str] = None
        ratio_decidendi: Optional[str] = None
        headnote_excerpt: Optional[str] = None
        subject_tags: list[SubjectTag] = []
        precedential_weight: Optional[str] = None
        still_good_law: bool = True
        acts: list[Act] = []
        cites: list["CitedAuthority"] = []  # curated edges from judgment_citations
        metadata: dict = {
            "index_fields": ["case_title", "ratio_decidendi", "headnote_excerpt"]
        }


def build_judgment_datapoints(
    batch: list[dict[str, Any]],
    citations_by_citing: dict[str, list[dict[str, Any]]],
) -> list[Any]:
    """Map a batch of judgments_metadata rows -> Judgment DataPoints with real
    Court/Act/SubjectTag/cites edges.

    `citations_by_citing` maps judgment_id -> list of citation rows
    (citing_id, cited_id, cited_citation, context, citation_type).
    """
    if not COGNEE_AVAILABLE:
        # Plain-dict fallback so callers/tests can still introspect the shape.
        return [_judgment_as_dict(m, citations_by_citing.get(m["judgment_id"], [])) for m in batch]

    court_cache: dict[str, Court] = {}
    act_cache: dict[str, Act] = {}
    tag_cache: dict[str, SubjectTag] = {}

    def get_court(meta) -> Optional[Court]:
        name = meta.get("court_name") or meta.get("court_type") or meta.get("court_level")
        if not name:
            return None
        if name not in court_cache:
            court_cache[name] = Court(name=name, level=meta.get("court_level") or "")
        return court_cache[name]

    def get_acts(meta) -> list[Act]:
        out = []
        for a in _to_str_list(meta.get("acts_cited")):
            if a not in act_cache:
                act_cache[a] = Act(name=a)
            out.append(act_cache[a])
        return out

    def get_tags(meta) -> list[SubjectTag]:
        out = []
        for t in _to_str_list(meta.get("subject_tags")):
            if t not in tag_cache:
                tag_cache[t] = SubjectTag(name=t)
            out.append(tag_cache[t])
        return out

    points: list[Judgment] = []
    for meta in batch:
        jid = str(meta["judgment_id"])
        cited_authorities: list[CitedAuthority] = []
        for c in citations_by_citing.get(jid, []):
            cite_str = c.get("cited_citation") or c.get("citation_text") or ""
            name = (c.get("context") or "").split(" - ")[0].strip()
            if cite_str or name:
                cited_authorities.append(
                    CitedAuthority(citation=cite_str or name, name=name or cite_str)
                )
        ratio = meta.get("ratio_decidendi") or ""
        headnote = (meta.get("headnotes") or "")[:600]
        points.append(
            Judgment(
                judgment_id=jid,
                case_title=meta.get("case_title", "") or "Untitled",
                citation=meta.get("citation"),
                court=get_court(meta),
                judgment_date=str(meta.get("judgment_date") or "") or None,
                ratio_decidendi=ratio or None,
                headnote_excerpt=headnote or None,
                subject_tags=get_tags(meta),
                precedential_weight=meta.get("precedential_weight"),
                still_good_law=_still_good_law(meta),
                acts=get_acts(meta),
                cites=cited_authorities,
            )
        )
    return points


def _judgment_as_dict(meta: dict[str, Any], cits: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "judgment_id": str(meta["judgment_id"]),
        "case_title": meta.get("case_title"),
        "citation": meta.get("citation"),
        "court": meta.get("court_name") or meta.get("court_level"),
        "still_good_law": _still_good_law(meta),
        "acts": _to_str_list(meta.get("acts_cited")),
        "subject_tags": _to_str_list(meta.get("subject_tags")),
        "cites": [
            (c.get("cited_citation") or (c.get("context") or "").split(" - ")[0].strip())
            for c in cits
        ],
    }
