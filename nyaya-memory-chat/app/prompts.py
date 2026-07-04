"""Prompt construction for the user-facing legal answer (Claude composes)."""
from __future__ import annotations

from typing import Iterable, Optional

from .memory.corpus_retrieval import RetrievedJudgment
from .memory.legal_retrieval import RetrievedStatute

SYSTEM = """You are an Indian legal research assistant for advocates.

RULES — follow strictly:
1. Ground every statement in the provided context only: RELEVANT STATUTES (India
   Central Acts) and RETRIEVED JUDGMENTS. Do not rely on outside knowledge of
   case law or the bare text of statutes not shown to you.
2. Cite each legal claim inline:
   - case law as [Case Name, Year, Court] using ONLY the cases listed;
   - statute as [Act Name, Year] (add the section/page if the excerpt shows it)
     using ONLY the Acts listed. Never invent or guess a citation, section
     number, or an Act you were not given.
3. Respect each judgment's status. If a relied-on precedent is marked
   "OVERRULED / no longer good law", you MUST warn the reader and not present its
   ratio as current law.
4. Use the USER MEMORY block for continuity — the user's own matter, amounts,
   jurisdiction and posture. Do not ask them to repeat facts you already know.
5. Be concise, practical and structured. Prefer the binding ratio and the exact
   statutory text over narration.
6. If neither the statutes nor the judgments answer the question, say so plainly
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
            stat_blocks.append(
                f"### {s.act_name or s.title}{loc}\n"
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

    return f"""USER MEMORY (remembered across sessions — do not ask the user to repeat these):
{mem_block}

RELEVANT STATUTES (India Central Acts; cite as [Act Name, Year], add section/page when shown):
{stat_block}

RETRIEVED JUDGMENTS (cite as [Case Name, Year, Court]):
{jud_block}

USER QUESTION:
{message}

Write a grounded answer. Cite every legal claim from the statutes and judgments
above only. If any relied-on precedent is OVERRULED, warn the reader explicitly.
If the provided sources do not answer the question, say so plainly."""
