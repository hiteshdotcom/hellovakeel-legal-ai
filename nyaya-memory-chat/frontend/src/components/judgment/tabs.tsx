import { Fragment, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { useJudgment } from "@/store/judgment";
import type { Citation, JudgmentDetail } from "@/lib/types";
import { asArr, fmtDate } from "@/lib/format";
import { Card, SectionTitle, Chip, GoodLawBadge, Button } from "@/components/ui";
import { CoreHolding, MetaGrid, ChipRow, ListBlock, Disclaimer } from "./blocks";
import { mStr, mArr, mObj, goodLawFlag } from "./meta";
import { FileText, Scales, Warning, Star, Shield, Lightbulb, Sparkle, Search, Graph } from "@/lib/icons";
import Answer from "@/components/chat/Answer";

// ---------------------------------------------------------------- Overview
export function OverviewTab({ d }: { d: JudgmentDetail }) {
  const m = d.metadata;
  const s = d.source;
  return (
    <div className="flex flex-col gap-4">
      <CoreHolding text={s.ratio_decidendi || ""} />
      <Card className="flex flex-col gap-3">
        <SectionTitle icon={FileText}>Case details</SectionTitle>
        <MetaGrid
          items={[
            ["Court", s.court || mStr(m, "court_name")],
            ["Level", mStr(m, "court_level")],
            ["Date", fmtDate(s.judgment_date)],
            ["Disposition", mStr(m, "disposition")],
            ["Final order", mStr(m, "final_order")],
            ["Bench", mArr(m, "bench").join(", ") || mStr(m, "bench_strength")],
            ["Petitioner / Appellant", mArr(m, "petitioner_names").join(", ")],
            ["Respondent", mArr(m, "respondent_names").join(", ")],
            ["Case number", mStr(m, "case_number")],
            ["CNR", mStr(m, "cnr_number")],
          ]}
        />
      </Card>
      <ListBlock title="Legal Issues" items={mArr(m, "legal_issues")} icon={Scales} numbered />
      <Card className="flex flex-col gap-3">
        <SectionTitle icon={FileText}>Acts, Sections, Keywords</SectionTitle>
        <ChipRow label="Acts" items={mArr(m, "acts_cited").slice(0, 8)} />
        <ChipRow label="Sections" items={mArr(m, "sections_cited").slice(0, 10)} />
        <ChipRow
          label="Keywords"
          items={(mArr(m, "keywords").length ? mArr(m, "keywords") : mArr(m, "subject_tags")).slice(0, 10)}
        />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- Analysis
function authorityScore(m: JudgmentDetail["metadata"]): number {
  const rank: Record<string, number> = {
    Supreme: 5,
    "Supreme Court": 5,
    "Privy Council": 4,
    "High Court": 3,
    Tribunal: 1,
  };
  const level = mStr(m, "court_level") || mStr(m, "court_name");
  const key = Object.keys(rank).find((k) => level.includes(k));
  let base = key ? rank[key] : 2;
  const unanimous = JSON.stringify(m.bench_detail || m.unanimity_weight || "")
    .toLowerCase()
    .includes("unanim");
  if (unanimous) base += 1;
  const y = parseInt(mStr(m, "judgment_date").slice(0, 4), 10);
  if (y >= 1947) base += Math.min(1, (y - 1947) / 80);
  return Math.max(0, Math.min(8, base));
}

function AuthorityMeter({ score }: { score: number }) {
  return (
    <Card className="flex flex-col gap-2.5">
      <SectionTitle icon={Scales}>Authority Score</SectionTitle>
      <div className="grid grid-cols-8 gap-1">
        {Array.from({ length: 8 }, (_, i) => (
          <span
            key={i}
            className={`h-2.5 rounded border border-divider ${
              i < Math.round(score) ? "bg-navy" : "bg-canvas"
            }`}
          />
        ))}
      </div>
      <div className="tnum text-[13px] font-extrabold text-ink-2">{score.toFixed(1)} / 8</div>
    </Card>
  );
}

function RiskFlags({ flags }: { flags: Array<Record<string, unknown>> }) {
  return (
    <Card className="flex flex-col gap-2.5">
      <SectionTitle icon={Warning}>Risk Flags</SectionTitle>
      {flags.length === 0 ? (
        <div className="text-[13px] text-ink-3">No risk flags returned for this judgment.</div>
      ) : (
        flags.map((f, i) => {
          const sev = String(f.severity || "");
          const tone = sev === "high" ? "bad" : "warn";
          return (
            <div
              key={i}
              className={`flex items-start gap-2 rounded-xl border px-2.5 py-2.5 ${
                tone === "bad" ? "border-bad-bd bg-bad-bg" : "border-warn-bd bg-warn-bg"
              }`}
            >
              <Warning size={14} weight="bold" className={tone === "bad" ? "text-bad" : "text-warn"} />
              <div>
                <div className={`text-[12.5px] font-extrabold ${tone === "bad" ? "text-bad" : "text-warn"}`}>
                  {String(f.label || f.code || "risk")}
                </div>
                <div className="mt-0.5 text-[12px] leading-snug text-ink-2">
                  {String(f.detail || "Review manually.")}
                </div>
              </div>
            </div>
          );
        })
      )}
    </Card>
  );
}

function SummaryCard({ d }: { d: JudgmentDetail }) {
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<Record<string, string> | null>(null);

  async function generate() {
    if (loading) return;
    setLoading(true);
    setSummary(null);
    try {
      setSummary(await api.summariseJudgment(d.judgment_id));
    } catch {
      const m = d.metadata;
      setSummary({
        what_happened: mStr(m, "headnotes") || "The backend did not return a generated summary yet.",
        what_was_decided:
          mStr(m, "disposition") || mStr(m, "final_order") || "Decision details were not returned.",
        key_legal_principle: d.source.ratio_decidendi || "No ratio decidendi returned.",
        why_it_matters:
          mStr(m, "precedential_weight") ||
          "Use the analysis and citations tabs to assess precedential value.",
      });
    } finally {
      setLoading(false);
    }
  }

  const rows: Array<[string, string]> = [
    ["What happened", "what_happened"],
    ["What was decided", "what_was_decided"],
    ["Key principle", "key_legal_principle"],
    ["Why it matters", "why_it_matters"],
  ];

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <SectionTitle icon={Sparkle}>AI Summary</SectionTitle>
        <div className="ml-auto">
          <Button variant="primary" onClick={generate} className="px-3 py-2 text-[12px]">
            <Sparkle size={14} weight="fill" />
            {loading ? "Generating…" : "Generate"}
          </Button>
        </div>
      </div>
      <Disclaimer />
      {loading ? (
        <div className="skel h-[72px] rounded-xl" />
      ) : summary ? (
        rows.map(([label, key]) => (
          <div key={key} className="border-t border-divider pt-2.5">
            <div className="text-[11px] font-extrabold uppercase tracking-[0.07em] text-accent-ink">
              {label}
            </div>
            <div className="mt-0.5 text-[13px] leading-relaxed text-ink-2">{summary[key] || "Not returned"}</div>
          </div>
        ))
      ) : (
        <div className="text-[13px] leading-relaxed text-ink-3">
          Generate a structured summary from the judgment fields returned by the backend.
        </div>
      )}
    </Card>
  );
}

export function AnalysisTab({ d }: { d: JudgmentDetail }) {
  const m = d.metadata;
  const cls = mObj(m, "current_law_status");
  return (
    <div className="flex flex-col gap-3">
      <SummaryCard d={d} />
      <AuthorityMeter score={authorityScore(m)} />
      <RiskFlags flags={asArr(m.risk_flags) as Array<Record<string, unknown>>} />
      <ListBlock title="Material Facts" items={mArr(m, "material_facts")} icon={FileText} numbered />
      <Card className="flex flex-col gap-2.5">
        <SectionTitle icon={Shield}>Current Law Status</SectionTitle>
        <GoodLawBadge value={goodLawFlag(m)} />
        <div className="text-[13px] leading-relaxed text-ink-2">
          {String(cls.subsequent_treatment_summary || "No subsequent-treatment summary returned.")}
        </div>
        {cls.governing_statute_today ? (
          <Chip tone="accent">Governing statute today: {String(cls.governing_statute_today)}</Chip>
        ) : null}
      </Card>
      <HowToCite h={mObj(m, "how_to_cite")} />
    </div>
  );
}

function HowToCite({ h }: { h: Record<string, unknown> }) {
  return (
    <Card className="flex flex-col gap-2.5">
      <SectionTitle icon={Scales}>How to Cite</SectionTitle>
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <ListBlock title="Safe to cite for" items={asArr(h.safe_to_cite_for).map(String)} icon={Star} numbered />
        <ListBlock title="Do not cite for" items={asArr(h.do_not_cite_for).map(String)} icon={Warning} numbered />
      </div>
      {h.best_used_by ? (
        <div className="text-[13px] leading-snug text-ink-2">
          <b>Best used by: </b>
          {String(h.best_used_by)}
        </div>
      ) : null}
    </Card>
  );
}

// ---------------------------------------------------------------- Full text
function Highlighted({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "ig");
  const out: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(<Fragment key={`t${i}`}>{text.slice(last, m.index)}</Fragment>);
    out.push(
      <mark key={`m${i}`} className="rounded-sm bg-gold-soft px-0.5 text-ink">
        {m[0]}
      </mark>,
    );
    last = re.lastIndex;
    i++;
  }
  out.push(<Fragment key="end">{text.slice(last)}</Fragment>);
  return <>{out}</>;
}

export function FullTextTab({ d }: { d: JudgmentDetail }) {
  const query = useJudgment((s) => s.textQuery);
  const setQuery = useJudgment((s) => s.setTextQuery);
  const pages = d.pages || [];
  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-wrap items-center gap-2">
        <Search size={15} className="text-accent-ink" />
        <input
          value={query}
          placeholder="Search judgment text"
          onChange={(e) => setQuery(e.target.value)}
          className="min-w-[180px] flex-1 rounded-lg border border-divider bg-surface px-2.5 py-2 outline-none focus:border-gold"
        />
        <Chip tone="neutral">{pages.length || d.page_count || 0} pages</Chip>
      </Card>
      {pages.length === 0 ? (
        <Card>
          <SectionTitle icon={FileText}>Judgment Text</SectionTitle>
          <div className="ny-sc mt-2 max-h-[520px] overflow-auto whitespace-pre-wrap font-mono text-[13px] leading-relaxed text-ink-2">
            {d.full_text || d.text_preview || "No page text available."}
          </div>
        </Card>
      ) : (
        <Card className="ny-sc flex max-h-[620px] flex-col gap-3 overflow-auto">
          <SectionTitle icon={FileText}>Structured Page Text</SectionTitle>
          {pages.map((p) => (
            <section key={p.page_number} className="border-t border-divider pt-2.5">
              <div className="mb-2">
                <Chip tone="gold">Page {p.page_number}</Chip>
              </div>
              <div className="whitespace-pre-wrap text-[13px] leading-[1.72] text-ink-2">
                <Highlighted text={p.text || ""} q={query} />
              </div>
            </section>
          ))}
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Citations
function CitationCard({ c, direction }: { c: Citation; direction: string }) {
  const openJudgment = useJudgment((s) => s.openJudgment);
  const good = "good_law" in c ? (c.good_law as boolean | null) : null;
  const title =
    c.case_title || c.cited_citation || c.citation_text || String(c.cited_id || "Cited authority");
  const score = c.applicability_score;
  const clickable = !!c.cited_id;
  return (
    <button
      className="flex flex-col gap-2.5 rounded-2xl border border-divider bg-surface p-3.5 text-left disabled:opacity-80"
      disabled={!clickable}
      onClick={() => c.cited_id && void openJudgment(String(c.cited_id))}
    >
      <div className="flex items-start gap-2.5">
        <Scales size={17} className="text-accent-ink" />
        <div className="min-w-0 flex-1">
          <div className="font-serif text-[16px] font-semibold leading-snug text-ink">{title}</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Chip tone="gold">{direction}</Chip>
            <Chip tone="neutral">{c.citation_type || "Citation edge"}</Chip>
            <GoodLawBadge value={good} />
          </div>
        </div>
        {score != null && <div className="tnum text-[13px] font-extrabold text-accent-ink">{score}%</div>}
      </div>
      {c.ratio_decidendi && (
        <div className="text-[12.5px] italic leading-relaxed text-ink-2">{c.ratio_decidendi}</div>
      )}
    </button>
  );
}

export function CitationsTab({ d }: { d: JudgmentDetail }) {
  const all = d.citations || [];
  const id = String(d.judgment_id);
  const citing = all.filter((c) => String(c.citing_id) === id);
  const citedBy = all.filter((c) => String(c.cited_id) === id);
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {(
        [
          ["Citing", citing],
          ["Cited by", citedBy],
        ] as const
      ).map(([label, items]) => (
        <Card key={label} className="flex flex-col gap-2.5">
          <SectionTitle icon={Graph}>{label}</SectionTitle>
          {items.length === 0 ? (
            <div className="text-[13px] text-ink-3">No {label.toLowerCase()} cases found yet.</div>
          ) : (
            items.map((c, i) => <CitationCard key={i} c={c} direction={label} />)
          )}
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Ask
export function AskTab() {
  const user = useAuth((a) => a.user);
  const { askDraft, setAskDraft, ask, asking, askAnswer, askWarnings } = useJudgment();
  const can = askDraft.trim().length > 0 && !asking;
  return (
    <div className="flex flex-col gap-2.5 border-t border-divider pt-3">
      <div className="text-[11px] font-bold uppercase tracking-[0.07em] text-ink-3">
        Ask About This Judgment
      </div>
      <Disclaimer />
      <textarea
        rows={2}
        value={askDraft}
        placeholder="Ask a question about this judgment…"
        onChange={(e) => setAskDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && user) {
            e.preventDefault();
            void ask(user.id);
          }
        }}
        className="min-h-16 w-full resize-y rounded-xl border border-divider bg-surface p-2.5 text-[13.5px] leading-relaxed text-ink outline-none focus:border-gold"
      />
      <div className="flex items-center gap-2">
        <Button onClick={() => user && void ask(user.id)} disabled={!can} className="px-3 py-2 text-[12.5px]">
          <Lightbulb size={14} />
          {asking ? "Reading…" : "Ask"}
        </Button>
        <div className="text-[11.5px] leading-snug text-ink-3">Answers use this selected judgment only.</div>
      </div>
      {(askAnswer || asking) && (
        <div className="rounded-xl border border-divider bg-canvas p-3">
          <Answer text={askAnswer} sources={[]} warnings={askWarnings} done={!asking} streaming={asking} />
        </div>
      )}
    </div>
  );
}
