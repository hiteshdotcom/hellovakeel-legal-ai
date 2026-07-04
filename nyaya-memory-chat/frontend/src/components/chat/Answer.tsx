import { Fragment, type ReactNode } from "react";
import type { Source, Warning } from "@/lib/types";
import { useJudgment } from "@/store/judgment";
import { cn } from "@/lib/cn";

// Fuzzy-match a citation label (e.g. "Kesavananda v. State") to a retrieved source.
function srcByCitation(label: string, sources: Source[]): Source | null {
  const norm = (s: string) =>
    (s || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
  const left = norm(label.split(/ v\.? /)[0]);
  let best: Source | null = null;
  let score = 0;
  for (const s of sources) {
    const t = norm(s.case_title);
    let sc = 0;
    if (left.length > 5 && t.includes(left)) sc = 0.95;
    if (sc > score) {
      score = sc;
      best = s;
    }
  }
  return score > 0.6 ? best : null;
}

interface Reg {
  list: Array<{ num: number; label: string; src: Source | null; unverified: boolean }>;
  byKey: Map<string, number>;
}

function CiteRef({
  label,
  unverified,
  sources,
  reg,
}: {
  label: string;
  unverified: boolean;
  sources: Source[];
  reg: Reg;
}) {
  const openJudgment = useJudgment((s) => s.openJudgment);
  const src = srcByCitation(label, sources);
  const key = src ? src.judgment_id || src.case_title : "u:" + label.toLowerCase();
  let num = reg.byKey.get(key);
  if (num === undefined) {
    num = reg.list.length + 1;
    reg.byKey.set(key, num);
    reg.list.push({ num, label, src, unverified });
  }
  return (
    <button
      aria-label={`Citation ${num}: ${label}`}
      onClick={() => src && void openJudgment(src)}
      className={cn(
        "mx-0.5 rounded-[5px] border px-1 align-super text-[10px] font-extrabold leading-none",
        unverified
          ? "border-bad-bd bg-bad-bg text-bad"
          : "border-divider bg-accent-soft text-accent-ink",
        src ? "cursor-pointer" : "cursor-default",
      )}
    >
      {num}
    </button>
  );
}

/** Tokenize a run of text into **bold**, [citations], and plain text. */
function inline(
  text: string,
  unver: Set<string>,
  sources: Source[],
  reg: Reg,
  keyPrefix: string,
): ReactNode[] {
  const out: ReactNode[] = [];
  const tokenRe = /(\*\*[^*]+\*\*|\[[^\]]+\])/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = tokenRe.exec(text))) {
    if (m.index > last) out.push(<Fragment key={`${keyPrefix}-t${i}`}>{text.slice(last, m.index)}</Fragment>);
    const tok = m[0];
    if (tok.startsWith("**")) {
      out.push(<strong key={`${keyPrefix}-b${i}`}>{tok.slice(2, -2)}</strong>);
    } else {
      const label = tok.slice(1, -1).trim();
      const looksCite = / v\.? | vs /i.test(label) || /\b(19|20)\d{2}\b/.test(label);
      if (looksCite) {
        out.push(
          <CiteRef
            key={`${keyPrefix}-c${i}`}
            label={label}
            unverified={unver.has(label.toLowerCase())}
            sources={sources}
            reg={reg}
          />,
        );
      } else {
        out.push(<Fragment key={`${keyPrefix}-x${i}`}>{tok}</Fragment>);
      }
    }
    last = tokenRe.lastIndex;
    i++;
  }
  if (last < text.length) out.push(<Fragment key={`${keyPrefix}-end`}>{text.slice(last)}</Fragment>);
  return out;
}

export default function Answer({
  text,
  sources,
  warnings,
  streaming,
  done,
}: {
  text: string;
  sources: Source[];
  warnings: Warning[];
  streaming?: boolean;
  done?: boolean;
}) {
  const unver = new Set(
    (warnings || [])
      .filter((w) => w.kind === "unverified_citation")
      .map((w) => (w.text || "").toLowerCase()),
  );
  const reg: Reg = { list: [], byKey: new Map() };
  const lines = (text || "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let b = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*##\s+/.test(line)) {
      blocks.push(
        <h2 key={`h${b}`} className="mb-1.5 mt-4 text-[19px] font-bold leading-snug first:mt-0.5">
          {inline(line.replace(/^\s*#{2,}\s+/, ""), unver, sources, reg, `h${b}`)}
        </h2>,
      );
      i++;
      b++;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: ReactNode[] = [];
      let li = 0;
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(
          <li key={`li${b}-${li}`} className="my-1.5">
            {inline(lines[i].replace(/^\s*[-*]\s+/, ""), unver, sources, reg, `li${b}-${li}`)}
          </li>,
        );
        i++;
        li++;
      }
      blocks.push(
        <ul key={`ul${b}`} className="my-1.5 list-disc pl-5">
          {items}
        </ul>,
      );
      b++;
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*##\s+/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={`p${b}`} className="my-2">
        {inline(buf.join(" "), unver, sources, reg, `p${b}`)}
      </p>,
    );
    b++;
  }

  return (
    <div className="font-serif text-[16.5px] leading-[1.7] text-ink [max-width:70ch]">
      {blocks}
      {streaming && !done && (
        <span className="ml-0.5 inline-block h-[17px] w-2 animate-blink rounded-[1px] bg-navy align-[-3px]" />
      )}
      {done && reg.list.length > 0 && <Footnotes reg={reg} />}
    </div>
  );
}

function Footnotes({ reg }: { reg: Reg }) {
  const openJudgment = useJudgment((s) => s.openJudgment);
  return (
    <div className="mt-4 flex flex-col gap-1.5 border-t border-divider pt-3 font-sans">
      <div className="text-[10.5px] font-extrabold uppercase tracking-[0.06em] text-ink-3">Sources</div>
      {reg.list.map((e) => {
        const over = e.src && e.src.good === false;
        const tone = over ? "text-bad" : e.src ? "text-good" : "text-warn";
        return (
          <button
            key={e.num}
            onClick={() => e.src && void openJudgment(e.src)}
            className="flex items-baseline gap-2.5 text-left active:scale-[0.99]"
            disabled={!e.src}
          >
            <span className="min-w-4 flex-none text-[11px] font-extrabold text-accent-ink">{e.num}.</span>
            <span className="min-w-0">
              <span className="font-serif text-[13.5px] font-semibold text-ink">
                {e.src ? e.src.case_title : e.label}
              </span>
              {e.src?.citation && <span className="text-[12px] text-ink-3">{"  ·  " + e.src.citation}</span>}
              <span className={cn("ml-2 text-[11px] font-bold", tone)}>
                {over ? "Overruled" : e.src ? "Good law" : "Unverified"}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
