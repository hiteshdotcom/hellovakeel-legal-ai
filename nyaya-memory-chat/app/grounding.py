"""Citation grounding guard.

The single most important trust feature: every legal citation the model emits is
extracted and matched against the judgments that were actually retrieved as
grounding context. A citation that is NOT present in the retrieved set is flagged
as `unverified` — a likely hallucinated precedent.

Also surfaces `overruled` warnings when a *relied-on* precedent is no longer good
law (`current_law_status.ratio_still_good_law == false`).

No external dependencies — pure-Python, deterministic, unit-tested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------- #
#  Citation shapes (Indian reporters + neutral citations + case-name pairs)
# --------------------------------------------------------------------------- #
# AIR 1978 SC 597  /  AIR 1973 SC 1461  /  AIR1973SC1461 (spaces optional)
_RE_AIR = re.compile(r"\bAIR\s*\d{4}\s*[A-Z]{2,4}\s*\d+\b")
# (2023) 1 SCC 1  /  (1999) 7 SCC 510  /  (2020) 5 SCC 1
_RE_SCC = re.compile(r"\(\d{4}\)\s+\d+\s+[A-Z]{2,5}(?:\s+OnLine\s+[A-Z]{2,5})?\s+\d+\b")
# Neutral citations:  2019:DHC:1234  /  2024 INSC 1  /  2021:SC:456
_RE_NEUTRAL = re.compile(r"\b\d{4}\s*(?::\s*[A-Z]{2,6}\s*:\s*\d+|INSC\s+\d+)\b")
# Case-name pairs:  Kesavananda Bharati v. State of Kerala  /  X v Y
# Capitalised tokens on both sides of a "v" / "v." / "vs" separator.
_RE_CASE = re.compile(
    r"\b([A-Z][A-Za-z.&'()\-]+(?:\s+[A-Z][A-Za-z.&'()\-]+){0,6})"
    r"\s+v(?:s|\.|s\.)?\s+"
    r"([A-Z][A-Za-z.&'()\-]+(?:\s+[A-Z][A-Za-z.&'()\-]+){0,8})"
)

_REPORTER_PATTERNS = [("air", _RE_AIR), ("scc", _RE_SCC), ("neutral", _RE_NEUTRAL)]

# Words that are never the start of a real party name (reduces false positives).
_STOP_LEAD = {"The", "In", "See", "Per", "As", "But", "And", "It", "This", "That", "A", "An"}


@dataclass
class ExtractedCitation:
    kind: str            # 'reporter' | 'case'
    text: str            # the raw matched string
    subtype: str = ""    # air / scc / neutral / case


@dataclass
class GroundingResult:
    sources: list[str] = field(default_factory=list)            # judgment_ids actually grounded
    warnings: list[dict[str, Any]] = field(default_factory=list)
    extracted: list[ExtractedCitation] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)           # citation strings that matched

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources": self.sources,
            "warnings": self.warnings,
            "extracted": [c.text for c in self.extracted],
            "verified": self.verified,
        }


# --------------------------------------------------------------------------- #
#  normalisation helpers
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_cite(s: str) -> str:
    """Normalise a reporter citation: drop punctuation/case/spacing."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
#  extraction
# --------------------------------------------------------------------------- #
def extract_citations(text: str) -> list[ExtractedCitation]:
    out: list[ExtractedCitation] = []
    seen: set[str] = set()

    for sub, pat in _REPORTER_PATTERNS:
        for m in pat.finditer(text):
            raw = re.sub(r"\s+", " ", m.group(0)).strip()
            key = ("reporter", _norm_cite(raw))
            if key not in seen:
                seen.add(key)
                out.append(ExtractedCitation(kind="reporter", text=raw, subtype=sub))

    for m in _RE_CASE.finditer(text):
        left = m.group(1).strip()
        # Skip pairs that obviously start mid-sentence with a stopword.
        if left.split()[0] in _STOP_LEAD:
            continue
        raw = re.sub(r"\s+", " ", m.group(0)).strip().rstrip(".")
        key = ("case", _norm(raw))
        if key not in seen:
            seen.add(key)
            out.append(ExtractedCitation(kind="case", text=raw, subtype="case"))

    return out


# --------------------------------------------------------------------------- #
#  matching a single citation against the retrieved corpus
# --------------------------------------------------------------------------- #
def _judgment_cite_strings(j: dict[str, Any]) -> list[str]:
    vals = [j.get("citation"), j.get("neutral_citation")]
    extra = j.get("citations") or j.get("citation_type")
    if isinstance(extra, list):
        vals.extend(extra)
    return [v for v in vals if v]


def _match_reporter(cite: str, retrieved: list[dict]) -> Optional[dict]:
    n = _norm_cite(cite)
    if not n:
        return None
    for j in retrieved:
        for c in _judgment_cite_strings(j):
            nc = _norm_cite(c)
            if nc and (nc == n or n in nc or nc in n):
                return j
    return None


def _match_case(cite: str, retrieved: list[dict], threshold: float = 0.62) -> Optional[dict]:
    # Compare the *full* "X v Y" string and also the petitioner side against each
    # retrieved case_title. Indian titles are long and noisy, so we take the best
    # of full-string ratio and a containment check on the distinctive left party.
    target = _norm(cite)
    left = _norm(cite.split(" v")[0]) if " v" in cite.lower() else target
    best: tuple[float, Optional[dict]] = (0.0, None)
    for j in retrieved:
        title = _norm(j.get("case_title", ""))
        if not title:
            continue
        score = _ratio(target, title)
        # Distinctive-party containment: "kesavananda bharati" ⊂ the title.
        if len(left) >= 6 and left in title:
            score = max(score, 0.9)
        if score > best[0]:
            best = (score, j)
    return best[1] if best[0] >= threshold else None


def _still_good_law(j: dict[str, Any]) -> bool:
    if "still_good_law" in j and j["still_good_law"] is not None:
        return bool(j["still_good_law"])
    cls = j.get("current_law_status")
    if isinstance(cls, dict) and "ratio_still_good_law" in cls:
        return bool(cls["ratio_still_good_law"])
    return True


# --------------------------------------------------------------------------- #
#  the guard
# --------------------------------------------------------------------------- #
def ground_answer(answer: str, retrieved: Iterable[dict[str, Any]]) -> GroundingResult:
    """Cross-check the model's answer against the retrieved judgments.

    `retrieved` rows should expose: judgment_id, case_title, citation,
    (optional) neutral_citation, and current_law_status / still_good_law.
    """
    retrieved = list(retrieved)
    result = GroundingResult()
    result.extracted = extract_citations(answer)

    grounded_ids: set[str] = set()

    for cite in result.extracted:
        match = (
            _match_reporter(cite.text, retrieved)
            if cite.kind == "reporter"
            else _match_case(cite.text, retrieved)
        )
        if match:
            jid = str(match.get("judgment_id", ""))
            if jid:
                grounded_ids.add(jid)
            result.verified.append(cite.text)
            # Overruled precedent the answer *relies on*.
            if not _still_good_law(match):
                if not any(
                    w["kind"] == "overruled" and w.get("judgment_id") == jid
                    for w in result.warnings
                ):
                    result.warnings.append(
                        {
                            "kind": "overruled",
                            "judgment_id": jid,
                            "title": match.get("case_title", cite.text),
                            "text": cite.text,
                            "detail": (
                                f"{match.get('case_title', 'A cited judgment')} is no longer "
                                "good law. Do not rely on its ratio."
                            ),
                        }
                    )
        else:
            result.warnings.append(
                {
                    "kind": "unverified_citation",
                    "text": cite.text,
                    "detail": (
                        "This citation was not found in the retrieved judgments and may be "
                        "inaccurate. Treat it as unverified."
                    ),
                }
            )

    result.sources = sorted(grounded_ids)
    return result
