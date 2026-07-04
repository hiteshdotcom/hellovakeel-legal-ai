"""Grounding guard tests — hallucinated-citation detection (the trust feature).

Pure-Python, no external services.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.grounding import extract_citations, ground_answer

RETRIEVED = [
    {
        "judgment_id": "j_kesava",
        "case_title": "Kesavananda Bharati v. State of Kerala",
        "citation": "AIR 1973 SC 1461",
        "current_law_status": {"ratio_still_good_law": True},
    },
    {
        "judgment_id": "j_golak",
        "case_title": "Golak Nath v. State of Punjab",
        "citation": "AIR 1967 SC 1643",
        "current_law_status": {"ratio_still_good_law": False},  # overruled
    },
    {
        "judgment_id": "j_minerva",
        "case_title": "Minerva Mills v. Union of India",
        "citation": "AIR 1980 SC 1789",
        "still_good_law": True,
    },
]


def test_extract_reporter_citations():
    text = "See AIR 1973 SC 1461 and (2020) 5 SCC 1 and 2024 INSC 1."
    cites = {c.text for c in extract_citations(text)}
    assert "AIR 1973 SC 1461" in cites
    assert any("SCC" in c for c in cites)
    assert any("INSC" in c for c in cites)


def test_extract_case_names():
    text = "The holding in Kesavananda Bharati v. State of Kerala is controlling."
    cites = [c for c in extract_citations(text) if c.kind == "case"]
    assert any("Kesavananda Bharati" in c.text for c in cites)


def test_verified_citation_recorded_as_source():
    answer = "Per [Kesavananda Bharati v. State of Kerala, 1973, SC], amendments cannot damage the basic structure."
    res = ground_answer(answer, RETRIEVED)
    assert "j_kesava" in res.sources
    assert not [w for w in res.warnings if w["kind"] == "unverified_citation"]


def test_hallucinated_citation_flagged():
    # A case NOT in the retrieved corpus must be flagged as unverified.
    answer = "This is settled by Indira Nehru Gandhi v. Raj Narain and by AIR 1999 SC 9999."
    res = ground_answer(answer, RETRIEVED)
    kinds = [w["kind"] for w in res.warnings]
    flagged = [w["text"] for w in res.warnings if w["kind"] == "unverified_citation"]
    assert "unverified_citation" in kinds
    assert any("Raj Narain" in f for f in flagged)
    assert any("9999" in f for f in flagged)


def test_overruled_precedent_warning():
    answer = "Following [Golak Nath v. State of Punjab, 1967, SC], fundamental rights are beyond amendment."
    res = ground_answer(answer, RETRIEVED)
    overruled = [w for w in res.warnings if w["kind"] == "overruled"]
    assert overruled, "should warn that Golak Nath is no longer good law"
    assert overruled[0]["judgment_id"] == "j_golak"
    assert "j_golak" in res.sources  # it WAS cited (and matched), just overruled


def test_clean_answer_has_no_warnings():
    answer = (
        "Per [Kesavananda Bharati v. State of Kerala, 1973, SC] and "
        "[Minerva Mills v. Union of India, 1980, SC], limited amending power is itself basic structure."
    )
    res = ground_answer(answer, RETRIEVED)
    assert res.warnings == []
    assert set(res.sources) == {"j_kesava", "j_minerva"}


def test_reporter_normalisation_matches_spacing():
    # Different spacing/punctuation should still match the same citation.
    answer = "It is reported at AIR1973SC1461."
    res = ground_answer(answer, RETRIEVED)
    assert "j_kesava" in res.sources
