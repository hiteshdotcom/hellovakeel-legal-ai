import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Check, Warning, Shield, type IconType } from "@/lib/icons";

// -------------------- Button --------------------
export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
  className,
  ariaLabel,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
  ariaLabel?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-xl font-semibold text-sm transition-all duration-150 active:scale-[0.98] disabled:cursor-not-allowed";
  const styles = {
    primary: "bg-gold text-navy hover:brightness-[1.03] disabled:opacity-40",
    secondary: "border border-divider bg-surface text-ink hover:bg-canvas disabled:opacity-50",
    ghost: "text-ink-2 hover:bg-canvas disabled:opacity-50",
  }[variant];
  return (
    <button
      type={type}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={onClick}
      className={cn(base, styles, className)}
    >
      {children}
    </button>
  );
}

// -------------------- IconButton --------------------
export function IconButton({
  icon: Icon,
  label,
  onClick,
  className,
  active,
  size = 18,
}: {
  icon: IconType;
  label: string;
  onClick?: () => void;
  className?: string;
  active?: boolean;
  size?: number;
}) {
  return (
    <button
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "flex-none inline-flex items-center justify-center rounded-lg transition-colors duration-150",
        "h-9 w-9 hover:bg-canvas active:scale-95",
        active ? "text-navy bg-accent-soft" : "text-ink-2",
        className,
      )}
    >
      <Icon size={size} />
    </button>
  );
}

// -------------------- Chip --------------------
export function Chip({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad" | "gold" | "accent";
  className?: string;
}) {
  const tones = {
    neutral: "bg-canvas text-ink-2 border-divider",
    accent: "bg-accent-soft text-accent-ink border-divider",
    gold: "bg-gold-soft text-[color:var(--gold)] border-[color:var(--gold)]/40",
    good: "bg-good-bg text-good border-good-bd",
    warn: "bg-warn-bg text-warn border-warn-bd",
    bad: "bg-bad-bg text-bad border-bad-bd",
  }[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-bold leading-none",
        tones,
        className,
      )}
    >
      {children}
    </span>
  );
}

// -------------------- Good-law badge --------------------
export function GoodLawBadge({ value }: { value: boolean | null | undefined }) {
  const tone = value === true ? "good" : value === false ? "bad" : "warn";
  const label = value === true ? "GOOD LAW" : value === false ? "OVERRULED" : "TREATMENT UNCLEAR";
  return (
    <Chip tone={tone}>
      <Shield size={13} weight="bold" />
      {label}
    </Chip>
  );
}

// -------------------- Banner --------------------
export function Banner({
  tone,
  title,
  detail,
}: {
  tone: "good" | "warn" | "bad";
  title: string;
  detail?: string;
}) {
  const Icon = tone === "good" ? Check : Warning;
  const styles = {
    good: "bg-good-bg border-good-bd text-good",
    warn: "bg-warn-bg border-warn-bd text-warn",
    bad: "bg-bad-bg border-bad-bd text-bad",
  }[tone];
  return (
    <div className={cn("flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5", styles)}>
      <Icon size={16} weight="bold" className="mt-0.5 flex-none" />
      <div className="flex flex-col gap-0.5">
        <div className="text-[12.5px] font-bold">{title}</div>
        {detail && <div className="text-[13px] leading-relaxed text-ink-2">{detail}</div>}
      </div>
    </div>
  );
}

// -------------------- Section title --------------------
export function SectionTitle({ children, icon: Icon }: { children: ReactNode; icon?: IconType }) {
  return (
    <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.07em] text-ink-3">
      {Icon && <Icon size={14} weight="bold" className="text-accent-ink" />}
      {children}
    </div>
  );
}

// -------------------- Card --------------------
export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-2xl border border-divider bg-surface p-4", className)}>{children}</div>
  );
}

// -------------------- Skeleton lines --------------------
export function SkeletonLines({ widths = [96, 100, 88, 72] }: { widths?: number[] }) {
  return (
    <div className="flex flex-col gap-2.5">
      {widths.map((w, i) => (
        <div key={i} className="skel h-3" style={{ width: `${w}%` }} />
      ))}
    </div>
  );
}
