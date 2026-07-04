import type { ReactNode } from "react";
import { Star, Warning, type IconType } from "@/lib/icons";
import { Card, SectionTitle, Chip } from "@/components/ui";

export function CoreHolding({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.06em] text-ink-3">
        <Star size={13} weight="fill" className="text-gold" /> Core holding
      </div>
      <div className="rounded-l-none rounded-r-xl border-l-4 border-gold bg-gold-soft px-[18px] py-3.5 font-serif text-lg italic leading-relaxed text-ink">
        {text || "No ratio decidendi returned by the backend."}
      </div>
    </div>
  );
}

export function MetaGrid({ items }: { items: Array<[string, string]> }) {
  const rows = items.filter(([, v]) => v && v.trim() !== "");
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-3">
      {rows.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-[0.06em] text-ink-3">{k}</div>
          <div className="mt-0.5 break-words text-sm font-medium leading-snug text-ink">{v}</div>
        </div>
      ))}
    </div>
  );
}

export function PillTag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full border border-divider bg-canvas px-2.5 py-0.5 text-[12px] text-ink-2">
      {children}
    </span>
  );
}

export function ChipRow({ label, items, tone }: { label: string; items: string[]; tone?: "gold" | "accent" }) {
  return (
    <div className="flex flex-wrap items-start gap-1.5">
      <span className="min-w-16 pt-1 text-[11px] font-bold text-ink-3">{label}</span>
      {items.length ? (
        items.map((x, i) =>
          tone ? (
            <Chip key={i} tone={tone}>
              {x}
            </Chip>
          ) : (
            <PillTag key={i}>{x}</PillTag>
          ),
        )
      ) : (
        <span className="pt-0.5 text-[13px] text-ink-3">No data returned</span>
      )}
    </div>
  );
}

export function ListBlock({
  title,
  items,
  icon,
  numbered,
}: {
  title: string;
  items: string[];
  icon?: IconType;
  numbered?: boolean;
}) {
  return (
    <Card className="flex flex-col gap-2.5">
      <SectionTitle icon={icon}>{title}</SectionTitle>
      {items.length === 0 ? (
        <div className="text-[13px] text-ink-3">No data returned.</div>
      ) : numbered ? (
        <ol className="ml-5 list-decimal">
          {items.map((it, i) => (
            <li key={i} className="my-1 text-[13px] leading-relaxed text-ink-2">
              {it}
            </li>
          ))}
        </ol>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((it, i) => (
            <div key={i} className="text-[13px] leading-relaxed text-ink-2">
              {it}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function Disclaimer() {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-ink-3">
      <Warning size={12} />
      AI-assisted output · verify before filing or use
    </div>
  );
}
