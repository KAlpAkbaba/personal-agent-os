/**
 * How a character feels, from what the office answer already says (the owner, 2026-10-03:
 * "karakterler yeni iş alacağı zaman hareket etsinler, başarısız işlerde sinirlensinler,
 * yorulsunlar, duyguları olsun"). Pure: the clock comes in as `now`, so every mood is tested
 * on plain objects.
 *
 * - working, for less than TIRED_AFTER_MIN minutes: focused; longer: tired
 * - the seat's task stopped, or its last report says it failed: angry
 * - the seat's task came back from the inspector to be rewritten: sad (dertli)
 * - waiting for work: relaxed (a mug of tea at the desk)
 * - the CTO (the owner's seat): happy
 * - held by the usage limit (a run it cut, or no work can start): sleepy, Zz over the head
 * - stopped only to wait (another task's files, or with the Danışman): waiting, calm
 */

import type { OfficeAgent, OfficeTask, OfficeView } from "./officeApi";

export type Mood = "focused" | "tired" | "angry" | "sad" | "relaxed" | "happy" | "sleepy" | "waiting";

/** A run this long makes its character tired: most runs of the team end within the hour. */
export const TIRED_AFTER_MIN = 45;

export const MOOD_TR: Record<Mood, string> = {
  focused: "odaklanmış",
  tired: "yorgun",
  angry: "sinirli",
  sad: "dertli",
  relaxed: "dinleniyor",
  happy: "keyifli",
  sleepy: "uyukluyor",
  waiting: "sırasını bekliyor",
};

const FAILED = /^\s*başarısız/i;
/** A run the usage limit cut: "başarısız: Max kullanım limiti" (the cycle's own words). */
const CUT_BY_LIMIT = /kullanım limiti|usage limit/i;
/** A stop that only waits: for another task's files, or decided and handed to the Danışman. */
const ONLY_WAITS = /\(alan çakışması: [^)]*\)\s*$|^\s*Danışman'a iletildi:/;

function failed(task: OfficeTask | undefined): boolean {
  if (!task) return false;
  return task.state === "stopped" || FAILED.test(task.report?.outcome ?? "");
}

/**
 * The owner, 2026-10-05: a seat the usage limit holds dozes at its desk - its run was cut by
 * the limit, or no new work can start while it lasts; a stop for a real problem stays angry,
 * so the two can be told apart. A stop that only waits for another task's files (or sits with
 * the Danışman) waits calmly. `limited`: the team's usage limit holds right now.
 */
export function moodOf(agent: OfficeAgent, task: OfficeTask | undefined, now: Date, limited = false): Mood {
  if (agent.seat === "owner") return "happy";
  if (agent.state === "returned") {
    if (task?.state === "stopped" && ONLY_WAITS.test(task.reason ?? "")) return "waiting";
    if (CUT_BY_LIMIT.test(task?.report?.outcome ?? "")) return "sleepy";
    if (task?.state === "stopped") return "angry";
    if (limited) return "sleepy";
    return failed(task) ? "angry" : "sad";
  }
  if (agent.state !== "working" && limited) return "sleepy";
  if (agent.state === "working") {
    const since = agent.since ? new Date(agent.since).getTime() : Number.NaN;
    if (Number.isNaN(since)) return "focused";
    const minutes = (now.getTime() - since) / 60000;
    return minutes >= TIRED_AFTER_MIN ? "tired" : "focused";
  }
  return "relaxed";
}

/**
 * The seats that just took a new task: working now, on a task they did not have in the
 * previous answer. Their characters walk in to the desk. The first answer moves nobody (the
 * page just opened; nothing "arrived").
 */
export function arrivals(previous: OfficeView | null, next: OfficeView): string[] {
  if (previous === null) return [];
  const before = new Map(previous.agents.map((a) => [a.seat, a.state === "working" ? a.task_id : null]));
  return next.agents
    .filter((a) => a.state === "working" && a.task_id !== null && before.get(a.seat) !== a.task_id)
    .map((a) => a.seat);
}
