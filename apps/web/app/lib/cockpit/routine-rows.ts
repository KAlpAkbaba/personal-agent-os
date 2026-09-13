/**
 * What the Rutinler panel says about one routine (B14 req 295).
 *
 * Pure, because the sentence that matters is a translation and not a render: a trigger is
 * stored as data (`{weekdays: [0,1,2,3,4], time: "07:15"}`) and the owner asked for it in
 * words ("hafta içi 07:15"). Getting that translation wrong is how a panel shows somebody a
 * routine they do not recognise as the one they set up — and then they cannot tell whether
 * the system misheard them or the panel is lying.
 *
 * Nothing here guesses. A trigger shape this file does not know says so, rather than
 * producing a confident sentence about a routine nobody can check.
 */

import type { RoutineRow } from "./routines";

export const STATUS_ARMED = "armed";
export const STATUS_PAUSED = "paused";

/** How many routines the panel lists. The rest are on `/v1/routines`. */
export const ROUTINE_ROWS_SHOWN = 12;

export const STATUS_TR: Record<string, string> = {
  armed: "çalışıyor",
  paused: "duraklatılmış",
  completed: "tamamlandı",
  cancelled: "iptal edildi",
};

export function statusLabel(status: string | null): string {
  if (!status) return "durum bildirilmedi";
  return STATUS_TR[status] ?? status;
}

const WEEKDAY_TR = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"];

function weekdayPhrase(days: number[], time: string): string {
  const sorted = days.toSorted((a, b) => a - b);
  const key = sorted.join(",");
  if (key === "0,1,2,3,4") return `hafta içi ${time}`;
  if (key === "5,6") return `hafta sonu ${time}`;
  if (key === "0,1,2,3,4,5,6") return `her gün ${time}`;
  const named = sorted
    .filter((d) => d >= 0 && d <= 6)
    .map((d) => WEEKDAY_TR[d])
    .join(", ");
  return named ? `${named} ${time}` : time;
}

/**
 * When a routine runs, in the owner's words.
 *
 * The Cloud Core's voice tool builds the same sentence from the same fields
 * (`tools_routines._trigger_phrase`). Two readers of one shape, deliberately — this one is
 * read while looking at a screen and that one while listening, and neither can be the
 * other's source without shipping a translation across the API for every row.
 */
export function triggerPhrase(kind: string | null, trigger: Record<string, unknown>): string {
  if (kind === "schedule") {
    const days = Array.isArray(trigger.weekdays)
      ? (trigger.weekdays as unknown[]).filter((d): d is number => typeof d === "number")
      : [];
    const time = typeof trigger.time === "string" ? trigger.time : "?";
    return weekdayPhrase(days, time);
  }
  if (kind === "at") {
    return typeof trigger.at === "string" ? `bir kez: ${formatWhen(trigger.at)}` : "bir kez";
  }
  if (kind === "presence") {
    return typeof trigger.event === "string" ? `olay: ${trigger.event}` : "bir varlık olayı";
  }
  if (kind === "condition") {
    const seconds = typeof trigger.min_seconds === "number" ? trigger.min_seconds : 0;
    if (trigger.kind === "device_idle") {
      return `bilgisayar ${Math.round(seconds / 60)} dakika boşta kalınca`;
    }
    if (trigger.kind === "device_active") return "bilgisayar tekrar kullanılınca";
    return "bir koşul";
  }
  // Never a confident sentence about a shape this file does not know.
  return kind ?? "tetikleyici bildirilmedi";
}

/** Who set it up. The owner cannot otherwise tell their own routine from the alarm's. */
export const SOURCE_TR: Record<string, string> = {
  voice: "sesle kuruldu",
  alarm: "alarmın kendi rutini",
  api: "arayüzden kuruldu",
  corpus: "test",
};

export function sourceLabel(source: string | null): string | null {
  if (!source) return null;
  return SOURCE_TR[source] ?? source;
}

/** Whether the pause/resume pair may be pressed, and which of the two this row offers. */
export type RoutineControl = { action: "pause" | "resume"; label: string } | null;

export function controlFor(row: Pick<RoutineRow, "status">): RoutineControl {
  if (row.status === STATUS_ARMED) return { action: "pause", label: "Duraklat" };
  if (row.status === STATUS_PAUSED) return { action: "resume", label: "Devam ettir" };
  // A completed or cancelled routine offers nothing: there is no state to return it to,
  // and a button that would 409 is a button that teaches the owner not to trust buttons.
  return null;
}

/** The badge beside the title: how many are actually running, out of how many there are. */
export function routinesBadge(rows: readonly Pick<RoutineRow, "status">[]): string | null {
  if (rows.length === 0) return null;
  const armed = rows.filter((r) => r.status === STATUS_ARMED).length;
  return armed === rows.length ? `${rows.length}` : `${armed}/${rows.length}`;
}

/**
 * The panel's border: a routine the owner paused and may have forgotten.
 *
 * Not "something is wrong" — nothing here is wrong. It is the one state a list can hide:
 * a morning routine turned off in March is invisible in a list of twelve until the owner
 * wonders why their mornings are quiet.
 */
export function needsAttention(rows: readonly Pick<RoutineRow, "status">[]): boolean {
  return rows.some((r) => r.status === STATUS_PAUSED);
}

const TIME_ZONE = "Europe/Istanbul";
const stamp = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: TIME_ZONE,
});

export function formatWhen(iso: string | null): string {
  if (!iso) return "zaman bildirilmedi";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : stamp.format(at);
}
