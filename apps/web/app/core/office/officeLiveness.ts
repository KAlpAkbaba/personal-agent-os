/**
 * Is a seat stuck, or only waiting? (pm-stuck-run-check.) Pure, over what the office answer says.
 *
 * The owner, 2026-10-04: "Proje yöneticisine söyle arada gerçekten işte çalışıp çalışmadıklarını
 * da kontrol etsin, iş takılmış olmasın." The cycle MEASURES signs of life per run (a write in its
 * temp folder or worktree, output, CPU of its process tree) and the API sends, on a working seat,
 * `stuck` and `idle_minutes`. A stuck seat is shown as "takılmış olabilir - 34 dk iz yok", not as
 * a tired face: tired only says "long".
 *
 * The owner, 2026-10-05: "Çalışan 4 hala kızgın". A stopped task whose duty return waits for
 * another task's files ("... (alan çakışması: X; o iş bitince)") or that the Danışman holds
 * ("Danışman'a iletildi: ...") is not stuck and not angry: it is calm, with the reason in a few
 * words. Angry stays for a stop nobody has decided yet.
 */

import type { OfficeAgent, OfficeTask } from "./officeApi";

/** stuck: no sign of life for the cycle's bound; queued: waits for another task; parked: the Danışman's. */
export type SeatLiveness = { kind: "stuck" | "queued" | "parked"; label: string };

/** What a working seat carries when the cycle measured its runs; an older API sends neither. */
type Measured = { stuck?: unknown; idle_minutes?: unknown };

const QUEUED = /\(alan çakışması: ([^;()]+); o iş bitince\)\s*$/;
const PARKED = "Danışman'a iletildi: ";
/** "A few words": the Danışman's reason is cut at a word, at most this many characters. */
export const PARKED_WORDS_MAX = 32;

function wholeMinutes(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function few(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= PARKED_WORDS_MAX) return flat;
  const cut = flat.slice(0, PARKED_WORDS_MAX + 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > 0 ? cut.slice(0, space) : flat.slice(0, PARKED_WORDS_MAX)).replace(/[\s,.;:]+$/, "")}…`;
}

/** The calm reason of a stopped task that only waits; null for a stop nobody has decided yet. */
export function waitingReason(task: OfficeTask | undefined): string | null {
  if (!task || task.state !== "stopped" || !task.reason) return null;
  const queued = QUEUED.exec(task.reason);
  if (queued) return `sırada: ${queued[1].replace(/\s+/g, " ").trim()} bitince`;
  if (task.reason.startsWith(PARKED)) return `Danışman'da: ${few(task.reason.slice(PARKED.length))}`;
  return null;
}

/** The seat's liveness label, or null when there is nothing to say beyond its mood. */
export function seatLiveness(agent: OfficeAgent, task: OfficeTask | undefined): SeatLiveness | null {
  if (agent.state === "working") {
    const { stuck, idle_minutes: idle } = agent as OfficeAgent & Measured;
    if (stuck === true && wholeMinutes(idle)) return { kind: "stuck", label: `takılmış olabilir - ${idle} dk iz yok` };
    return null;
  }
  if (agent.state !== "returned") return null;
  const reason = waitingReason(task);
  if (reason === null) return null;
  return { kind: task?.reason?.startsWith(PARKED) ? "parked" : "queued", label: reason };
}
