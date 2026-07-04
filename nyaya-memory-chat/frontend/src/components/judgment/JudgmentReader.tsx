import { useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useJudgment, type JudgmentTab } from "@/store/judgment";
import { fmtDate, asArr } from "@/lib/format";
import { mStr, goodLawFlag } from "./meta";
import { Chip } from "@/components/ui";
import { OverviewTab, AnalysisTab, FullTextTab, CitationsTab, AskTab } from "./tabs";
import { ArrowLeft, X, Warning } from "@/lib/icons";
import { cn } from "@/lib/cn";

const TABS: Array<[JudgmentTab, string]> = [
  ["overview", "Overview"],
  ["analysis", "Analysis"],
  ["text", "Full Text"],
  ["citations", "Citations"],
  ["ask", "Ask AI"],
];

export default function JudgmentReader() {
  const { open, loading, detail, error, pendingSource, tab, setTab, close } = useJudgment();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  const title =
    detail && !detail.error
      ? detail.source.case_title || detail.judgment_id
      : pendingSource?.case_title || "Judgment";
  const good = detail && !detail.error ? goodLawFlag(detail.metadata) : null;

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-label="Judgment detail"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={(e) => e.target === e.currentTarget && close()}
          className="fixed inset-0 z-[100] flex items-start justify-center overflow-auto bg-[color:var(--scrim)] px-[4vw] pb-[4vh] pt-16"
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={{ duration: 0.18 }}
            className="ny-sc flex max-h-[calc(100dvh-96px)] w-[min(1040px,92vw)] flex-col gap-3.5 overflow-auto rounded-2xl border border-divider bg-surface p-[18px] shadow-modal"
          >
            {/* header */}
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={close}
                aria-label="Back"
                className="inline-flex items-center gap-1.5 rounded-lg border border-divider px-2.5 py-1.5 text-[11.5px] font-bold text-ink-2 hover:bg-canvas"
              >
                <ArrowLeft size={13} weight="bold" /> Back
              </button>
              <div className="min-w-[120px] flex-1 truncate font-serif text-[16px] font-semibold">
                {title}
              </div>
              {good != null && (
                <Chip tone={good === false ? "bad" : "good"} className="hidden md:inline-flex">
                  <span className={cn("h-1.5 w-1.5 rounded-full", good === false ? "bg-bad" : "bg-good")} />
                  {good === false ? "NO LONGER GOOD LAW" : "GOOD LAW"}
                </Chip>
              )}
              <button
                onClick={close}
                aria-label="Close judgment"
                className="inline-flex items-center gap-1.5 rounded-lg border border-divider px-2.5 py-1.5 text-[11.5px] font-bold text-ink-2 hover:bg-canvas"
              >
                <X size={13} weight="bold" /> Close
              </button>
            </div>

            {loading ? (
              <div className="flex flex-col gap-2 px-0.5 py-3">
                <div className="font-serif text-lg font-semibold leading-tight">
                  {pendingSource?.case_title || "Opening judgment"}
                </div>
                <div className="text-[13px] leading-relaxed text-ink-3">
                  Loading metadata, pages, citations, and analytics…
                </div>
                <div className="mt-2 flex flex-col gap-2.5">
                  {[96, 100, 88, 72].map((w) => (
                    <div key={w} className="skel h-3" style={{ width: `${w}%` }} />
                  ))}
                </div>
              </div>
            ) : error ? (
              <div className="flex items-center gap-2 rounded-xl border border-bad-bd bg-bad-bg px-3 py-3 text-[13px] text-bad">
                <Warning size={16} weight="bold" /> {error}
              </div>
            ) : detail ? (
              <>
                {/* case header */}
                <div className="flex flex-col gap-1.5">
                  <div className="font-serif text-xl font-semibold leading-tight">
                    {detail.source.case_title || detail.judgment_id}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {detail.source.citation && (
                      <span className="rounded-full border border-divider px-2.5 py-1 text-[12px] font-semibold text-ink-2">
                        {detail.source.citation}
                      </span>
                    )}
                    {mStr(detail.metadata, "neutral_citation") && (
                      <span className="rounded-full border border-divider px-2.5 py-1 text-[12px] font-semibold text-ink-2">
                        {mStr(detail.metadata, "neutral_citation")}
                      </span>
                    )}
                  </div>
                  <div className="text-[12.5px] text-ink-3">
                    {[
                      detail.source.court,
                      fmtDate(detail.source.judgment_date),
                      mStr(detail.metadata, "disposition"),
                      asArr(detail.metadata.bench).join(", "),
                    ]
                      .filter(Boolean)
                      .join(" · ") || "Court/date unavailable"}
                  </div>
                </div>

                {/* tab bar */}
                <div
                  role="tablist"
                  className="grid grid-cols-5 gap-1 overflow-auto rounded-xl border border-divider bg-canvas p-1"
                >
                  {TABS.map(([id, label]) => (
                    <button
                      key={id}
                      role="tab"
                      aria-selected={tab === id}
                      onClick={() => setTab(id)}
                      className={cn(
                        "rounded-lg px-1 py-2 text-xs font-bold",
                        tab === id ? "bg-surface text-navy shadow-card" : "text-ink-3",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                {tab === "overview" && <OverviewTab d={detail} />}
                {tab === "analysis" && <AnalysisTab d={detail} />}
                {tab === "text" && <FullTextTab d={detail} />}
                {tab === "citations" && <CitationsTab d={detail} />}
                {tab === "ask" && <AskTab />}

                <div className="flex items-center gap-1.5 border-t border-divider pt-2 text-[11px] text-ink-3">
                  <Warning size={12} /> AI-assisted output · verify before filing or use
                </div>
              </>
            ) : null}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
