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
import { moodOf } from "./officeMood";

/** stuck: no sign of life for the cycle's bound; queued: waits for another task; parked: the Danışman's. */
export type SeatLiveness = { kind: "stuck" | "queued" | "parked"; label: string };

/** A seat as the API now sends it: a working one carries these when the cycle measured its runs. */
export type MeasuredAgent = OfficeAgent & { stuck?: unknown; idle_minutes?: unknown; stuck_children?: unknown };

/** Whether a stop only waits is officeMood's decision ("waiting"); here only its words are taken. */
const QUEUED = /\(alan çakışması: ([^;()]+)/;
const PARKED = "Danışman'a iletildi: ";
const RETURNED = { seat: "", role: "", state: "returned", task_id: null, task_title: null, since: null } as OfficeAgent;
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
  if (!task?.reason || moodOf(RETURNED, task, new Date(0)) !== "waiting") return null;
  const reason = task.reason.trim();
  if (reason.startsWith(PARKED)) return `Danışman'da: ${few(reason.slice(PARKED.length))}`;
  const queued = QUEUED.exec(reason);
  return queued ? `sırada: ${queued[1].replace(/\s+/g, " ").trim()} bitince` : null;
}

/** The stuck child the seat's minutes are: a test process idle while its run still writes. */
function idleChild(children: unknown, idle: number): string | null {
  if (!Array.isArray(children)) return null;
  for (const child of children as unknown[]) {
    const { name, idle_minutes: minutes } = (child ?? {}) as { name?: unknown; idle_minutes?: unknown };
    if (typeof name === "string" && name && wholeMinutes(minutes) && minutes >= idle) return name;
  }
  return null;
}

/** The seat's liveness label, or null when there is nothing to say beyond its mood. */
export function seatLiveness(agent: MeasuredAgent, task: OfficeTask | undefined): SeatLiveness | null {
  if (agent.state === "working") {
    const { stuck, idle_minutes: idle } = agent;
    if (stuck !== true || !wholeMinutes(idle)) return null;
    const child = idleChild(agent.stuck_children, idle);
    return { kind: "stuck", label: `takılmış olabilir - ${child ? `alt süreç ${child} ` : ""}${idle} dk iz yok` };
  }
  if (agent.state !== "returned") return null;
  const reason = waitingReason(task);
  if (reason === null) return null;
  return { kind: reason.startsWith("Danışman'da") ? "parked" : "queued", label: reason };
}

/** Every seat with something to say, in seat order, for the page's list under the scene. */
export function livenessLines(
  agents: MeasuredAgent[],
  tasks: Record<string, OfficeTask>,
): { seat: string; liveness: SeatLiveness }[] {
  return agents.flatMap((agent) => {
    const liveness = seatLiveness(agent, agent.task_id ? tasks[agent.task_id] : undefined);
    return liveness ? [{ seat: agent.seat, liveness }] : [];
  });
}
