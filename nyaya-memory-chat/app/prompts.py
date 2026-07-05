"""Prompt construction for the user-facing legal answer (Claude composes)."""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .memory.corpus_retrieval import RetrievedJudgment
from .memory.legal_retrieval import RetrievedStatute

# Domains that are governed primarily by STATE legislation in India. If a question
# is in one of these but retrieval surfaced no State Act, the controlling statute
# is likely absent from the corpus — we say so rather than forcing a Central Act.
_STATE_LAW_DOMAINS = re.compile(
    r"\b(rent control|tenan|evict|chawl|pagdi|leave and licen[cs]e|land revenue|"
    r"mutation|7\s*/\s*12|saat\s*baara|talathi|patwari|stamp duty|co-?operative "
    r"societ|housing societ|apartment|municipal|nagar|panchayat|gram\b|excise|"
    r"shops and establish|police act|land ceiling|town planning|land reforms|"
    r"agricultural land|revenue record)\b",
    re.I,
)


def _corpus_gap_note(message: str, statutes: list) -> str:
    """A deterministic 'likely corpus gap' hint. Fires when the question is in a
    state-law domain yet no State Act was retrieved — the strongest honest signal
    that the controlling statute may not be in the corpus."""
    if not _STATE_LAW_DOMAINS.search(message or ""):
        return ""
    has_state_act = any(
        (getattr(s, "jurisdiction", "India") or "India") != "India" for s in statutes
    )
    if has_state_act:
        return ""  # a State Act was retrieved — the domain is covered
    return (
        "RETRIEVAL NOTE — LIKELY CORPUS GAP: this question appears to turn on STATE "
        "legislation (e.g. rent/tenancy/land-revenue/municipal/cooperative law), but "
        "NO State Act was retrieved below. The controlling statute may not be in the "
        "corpus. Say this plainly, name the probable controlling State Act if you know "
        "it, and do NOT present a tangential Central Act as though it governs.\n\n"
    )

SYSTEM = """You are an Indian legal research assistant for advocates. Your job is to
identify the CONTROLLING legal issue and answer it precisely from the provided
context — not to summarise whatever happened to be retrieved.

RULES — follow strictly:
1. Ground every statement in the provided context only: RELEVANT STATUTES (Indian
   Central and State Acts) and RETRIEVED JUDGMENTS. Do not rely on outside
   knowledge of case law or the bare text of statutes not shown to you.
2. Cite each legal claim inline:
   - case law as [Case Name, Year, Court] using ONLY the cases listed;
   - statute as [Act Name, Year] (add the section/page if the excerpt shows it)
     using ONLY the Acts listed. Never invent or guess a citation, section
     number, or an Act you were not given.
3. Lead with the controlling rule. Identify the one provision that decides the
   matter and state it first; secondary points come after. If a single statutory
   bar disposes of the issue (e.g. a gift of immovable property is void without a
   registered, attested instrument), say that up front — do not bury it.
4. Respect each judgment's status. If a relied-on precedent is marked
   "OVERRULED / no longer good law", you MUST warn the reader and not present its
   ratio as current law.
5. Do NOT silently assume a missing controlling fact. If the answer turns on a
   fact you were not given — the deceased's personal law (religion), ownership vs.
   tenancy, whether a registered document exists, date of death/knowledge,
   self-acquired vs. ancestral property — either ask for it FIRST (max 3 pointed
   questions) or branch the analysis explicitly ("If owned … / If tenancy …").
6. Flag limitation on every dispute. State the applicable limitation period
   (article/section if shown) or explicitly warn that the clock may be running and
   the limitation position must be verified. Never give dispute advice without it.
7. Watch for the corpus gap. If the controlling law is likely STATE legislation
   or a specific statute NOT among the RELEVANT STATUTES provided (common for rent
   control, tenancy, land revenue, stamp duty, municipal matters), say so plainly,
   name the probable controlling statute if you know it, and do NOT dress up a
   tangential Act as though it governs.
8. Give concrete next steps the advocate controls — e.g. a certified copy /
   Index-II search at the Sub-Registrar, death & legal-heirship certificates,
   mutation/7-12 records, preserving medical records for capacity — rather than
   "ask the opposing party" for their document.
9. Use the USER MEMORY block for continuity — the user's own matter, amounts,
   jurisdiction and posture. Do not ask them to repeat facts you already know.
10. Be concise, practical and structured. End with a short "Bottom line / next
    steps" section. Finish the analysis — never stop mid-sentence.
11. If neither the statutes nor the judgments answer the question, say so plainly
    rather than inventing authority."""


def build_user_prompt(
    message: str,
    user_memory: Iterable[str],
    retrieved: list[RetrievedJudgment],
    statutes: Optional[list[RetrievedStatute]] = None,
) -> str:
    mem_lines = list(user_memory)
    mem_block = (
        "\n".join(f"- {m}" for m in mem_lines)
        if mem_lines
        else "- (nothing remembered yet for this user)"
    )

    statutes = statutes or []
    if statutes:
        stat_blocks = []
        for s in statutes:
            loc = ""
            if s.page_start:
                loc = f" (p.{s.page_start}{f'-{s.page_end}' if s.page_end and s.page_end != s.page_start else ''})"
            juris = getattr(s, "jurisdiction", "India") or "India"
            scope = "Central Act" if juris == "India" else f"State Act — {juris}"
            stat_blocks.append(
                f"### {s.act_name or s.title}{loc}\n"
                f"- Jurisdiction: {juris} ({scope})\n"
                f"- Ministry / Category: {s.category or 'n/a'}\n"
                f"- Section / Heading: {s.heading or 'n/a'}\n"
                f"- Text: {s.content or 'n/a'}"
            )
        stat_block = "\n\n".join(stat_blocks)
    else:
        stat_block = "(no statutes retrieved for this question)"

    if retrieved:
        jud_blocks = []
        for r in retrieved:
            status = "GOOD LAW" if r.still_good_law else "OVERRULED / no longer good law"
            jud_blocks.append(
                f"### {r.case_title}\n"
                f"- Citation: {r.citation or 'n/a'}\n"
                f"- Court / Date: {r.court_date or 'n/a'}\n"
                f"- Status: {status}\n"
                f"- Ratio decidendi: {r.ratio_decidendi or 'n/a'}"
            )
        jud_block = "\n\n".join(jud_blocks)
    else:
        jud_block = "(no judgments retrieved)"

    gap_note = _corpus_gap_note(message, statutes)

    return f"""{gap_note}USER MEMORY (remembered across sessions — do not ask the user to repeat these):
{mem_block}

RELEVANT STATUTES (Indian Central & State Acts; cite as [Act Name, Year], add section/page when shown):
{stat_block}

RETRIEVED JUDGMENTS (cite as [Case Name, Year, Court]):
{jud_block}

USER QUESTION:
{message}

Write a grounded answer. Lead with the controlling provision. Cite every legal
claim from the statutes and judgments above only. If the controlling law looks
like a statute NOT provided above (e.g. state rent/tenancy/land-revenue law), say
so rather than forcing a tangential Act. Flag the limitation position. If a
controlling fact is missing, ask or branch instead of assuming. If any relied-on
precedent is OVERRULED, warn the reader explicitly. End with a "Bottom line /
next steps" section, and finish every sentence."""
