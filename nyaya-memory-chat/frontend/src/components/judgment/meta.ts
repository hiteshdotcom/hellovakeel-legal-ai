import type { JudgmentMeta } from "@/lib/types";
import { asArr } from "@/lib/format";

/** Safe string read from the loosely-typed metadata blob. */
export function mStr(meta: JudgmentMeta, key: string): string {
  const v = meta[key];
  return v == null ? "" : String(v);
}

/** Read an array field, coercing each entry to a display string. */
export function mArr(meta: JudgmentMeta, key: string): string[] {
  return asArr(meta[key]).map((x) =>
    typeof x === "string" ? x : x == null ? "" : JSON.stringify(x),
  );
}

export function mObj(meta: JudgmentMeta, key: string): Record<string, unknown> {
  const v = meta[key];
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** Backend `current_law_status.ratio_still_good_law` → tri-state good-law flag. */
export function goodLawFlag(meta: JudgmentMeta): boolean | null {
  const cls = mObj(meta, "current_law_status");
  if ("ratio_still_good_law" in cls) return Boolean(cls.ratio_still_good_law);
  return null;
}
