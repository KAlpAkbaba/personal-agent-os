/**
 * The bar under the office: the team's "energy" is what is left of the subscription's usage
 * window the cycle last reported (`cycle.limits.all.used_pct`, ADR-0214 addendum 14), and
 * beside it how many agents work out of the seats. Pure: the view hands in the answer it got.
 * An answer without the field (an older server, or no cycle yet) is "bilinmiyor", never a
 * made-up full bar.
 */

import type { OfficeView } from "./officeApi";

export type Energy = {
  /** 0..100, or null when the answer does not say */
  percent: number | null;
  text: string;
  working: string;
  /** "ok" above 30 %, "low" at or under it, "empty" at 0, "unknown" when not reported */
  level: "ok" | "low" | "empty" | "unknown";
};

type LimitsLike = { all?: { used_pct?: unknown } | null } | null | undefined;

export function energyOf(view: OfficeView): Energy {
  const cycle = view.cycle as OfficeView["cycle"] & { limits?: LimitsLike };
  const used = cycle.limits?.all?.used_pct;
  const working = `Çalışan ajanlar ${cycle.running_agents}/${cycle.capacity}`;
  if (typeof used !== "number" || !Number.isFinite(used)) {
    return { percent: null, text: "Enerji bilinmiyor", working, level: "unknown" };
  }
  const percent = Math.max(0, Math.min(100, Math.round(100 - used)));
  const level = percent === 0 ? "empty" : percent <= 30 ? "low" : "ok";
  return { percent, text: `Enerji %${percent}`, working, level };
}
