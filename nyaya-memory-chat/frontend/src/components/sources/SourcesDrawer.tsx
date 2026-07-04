import { AnimatePresence, motion } from "framer-motion";
import { useChat } from "@/store/chat";
import { useUI } from "@/store/ui";
import { useJudgment } from "@/store/judgment";
import type { Source } from "@/lib/types";
import { cn } from "@/lib/cn";
import { X, Graph, FileText, ArrowRight } from "@/lib/icons";
import { CitationGraphSvg, GraphLegend } from "./CitationGraph";

function SourceCard({ src }: { src: Source }) {
  const openJudgment = useJudgment((s) => s.openJudgment);
  const over = src.good === false;
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-2xl border bg-surface p-3.5",
        over ? "border-bad-bd" : "border-divider",
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10.5px] font-extrabold",
            over ? "bg-bad-bg text-bad" : "bg-good-bg text-good",
          )}
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", over ? "bg-bad" : "bg-good")} />
          {over ? "NO LONGER GOOD LAW" : "GOOD LAW"}
        </span>
        <span className="ml-auto text-right text-[11.5px] text-ink-3">{src.courtdate}</span>
      </div>
      <div className="text-[14.5px] font-semibold leading-snug text-ink">{src.case_title}</div>
      {src.citation && <div className="text-[12px] text-ink-2">{src.citation}</div>}
      {src.ratio && (
        <div className="line-clamp-2 font-serif text-[13px] italic leading-relaxed text-ink-2">
          {src.ratio}
        </div>
      )}
      <button
        aria-label={`Open judgment: ${src.case_title}`}
        onClick={() => void openJudgment(src)}
        className="mt-0.5 inline-flex items-center gap-1.5 self-start rounded-lg border border-divider px-3 py-2 text-[12.5px] font-bold text-accent-ink hover:bg-canvas active:scale-[0.98]"
      >
        Open judgment <ArrowRight size={14} weight="bold" />
      </button>
    </div>
  );
}

export default function SourcesDrawer() {
  const open = useUI((u) => u.sourcesDrawerOpen);
  const setOpen = useUI((u) => u.setSourcesDrawer);
  const tab = useUI((u) => u.sourcesTab);
  const setTab = useUI((u) => u.setSourcesTab);
  const sources = useChat((s) => s.sources);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setOpen(false)}
            className="fixed inset-x-0 bottom-0 top-12 z-40 bg-black/30"
          />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-label="Sources"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 260, damping: 30 }}
            className={cn(
              "ny-sc fixed right-0 top-12 z-50 flex h-[calc(100dvh-48px)] w-[400px] max-w-[92vw] flex-col gap-3.5 overflow-y-auto bg-surface p-[18px] shadow-modal",
            )}
          >
            <div className="flex items-center gap-2">
              <div className="flex-1 font-serif text-[17px] font-semibold">Sources</div>
              <span className="text-[11px] text-ink-3">
                {sources.length ? `${sources.length} judgments` : ""}
              </span>
              <button
                aria-label="Close sources"
                onClick={() => setOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-2 hover:bg-canvas"
              >
                <X size={16} />
              </button>
            </div>

            {sources.length === 0 ? (
              <div className="px-0.5 py-2.5 text-[13px] leading-relaxed text-ink-3">
                No sources yet. Ask a question and the judgments behind the answer appear here.
              </div>
            ) : (
              <>
                <div
                  role="tablist"
                  className="grid grid-cols-2 gap-1 rounded-xl border border-divider bg-canvas p-1"
                >
                  {([
                    ["graph", "Citation Map", Graph],
                    ["list", "List", FileText],
                  ] as const).map(([id, label, Icon]) => (
                    <button
                      key={id}
                      role="tab"
                      aria-selected={tab === id}
                      onClick={() => setTab(id)}
                      className={cn(
                        "flex items-center justify-center gap-1.5 rounded-lg px-1 py-2 text-xs font-bold",
                        tab === id ? "bg-surface text-navy shadow-card" : "text-ink-3",
                      )}
                    >
                      <Icon size={14} />
                      {label}
                    </button>
                  ))}
                </div>

                {tab === "graph" ? (
                  <div className="rounded-2xl border border-divider bg-surface p-4">
                    <div className="mb-3 flex items-center gap-2.5">
                      <Graph size={17} className="text-accent-ink" />
                      <div className="flex-1 font-serif text-[16px] font-semibold">Citation Graph</div>
                      <span className="text-[11px] text-ink-3">{sources.length} judgments</span>
                    </div>
                    <CitationGraphSvg />
                    <GraphLegend />
                  </div>
                ) : (
                  <div className="flex flex-col gap-2.5">
                    {sources.map((src) => (
                      <SourceCard key={src.judgment_id} src={src} />
                    ))}
                  </div>
                )}
              </>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
