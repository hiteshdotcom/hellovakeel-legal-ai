// Small formatting helpers shared across views.

export const asArr = <T,>(v: T | T[] | null | undefined): T[] =>
  Array.isArray(v) ? v.filter(Boolean) : v ? [v] : [];

export const first = (...xs: Array<unknown>): string => {
  const v = xs.find((x) => x !== undefined && x !== null && String(x).trim() !== "");
  return v == null ? "" : String(v);
};

export const fmtDate = (v?: string | null): string =>
  v ? String(v).slice(0, 10) : "Date unavailable";

export const pretty = (s?: string): string =>
  String(s || "").replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());

export function fmtAgo(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts).getTime();
  const m = Math.round((Date.now() - d) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function initialsOf(nameOrEmail?: string): string {
  const s = (nameOrEmail || "?").trim();
  const parts = s.split(/\s+/);
  if (parts.length > 1 && parts[0] && parts[1])
    return (parts[0][0] + parts[1][0]).toUpperCase();
  return s.slice(0, 2).toUpperCase();
}
