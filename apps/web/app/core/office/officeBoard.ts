/**
 * The test queue on the Ofis desks (owner, 2026-10-03): the seat holding a heavy test slot
 * carries a small "TEST" sign, each waiter its place in the line.
 *
 * The machine's queue (`scripts/lib/TeamTestSlots.ps1`) decides; when it takes, makes wait or
 * frees a slot it writes one `bilgi` note on the team's board with a snapshot of the line
 * (`slot`). This page draws the NEWEST snapshot and nothing else: no clock of its own, no
 * guess (team/plans/team-board-talk-adr.md). A board that cannot be read is no sign.
 */

import { createElement, type ReactElement } from "react";

import { apiFetch } from "../../lib/session";
import { seatName } from "./officeModel";

export const BOARD_PATH = "/v1/team/board/notes";
/** How many of the newest notes the page reads to find the newest snapshot. */
export const BOARD_READ_LIMIT = 100;
/** A snapshot older than this is not shown: a holder that died leaves no sign for ever. */
export const SLOT_SIGN_MAX_AGE_MIN = 180;

export type SlotSnapshot = {
  state: "take" | "wait" | "free";
  kinds: string[];
  holders: string[];
  waiting: string[];
  estimate_min?: number;
};

/** A note as `GET /v1/team/board/notes` sends it (the fields this page reads). */
export type BoardNote = {
  id: string;
  at: string;
  seat: string;
  task: string;
  kind: string;
  to: string;
  reply_to: string;
  text: string;
  slot?: SlotSnapshot;
};

export type SlotSign = { kind: "test"; estimateMin: number | null } | { kind: "queue"; position: number };

const KIND_TR: Record<string, string> = { database: "veritabanı", desktop: "masaüstü", heavy: "ağır" };

/** The desk a board name sits at: `inspector-2` at the inspector's; `gate` at none. */
export function deskOf(name: string): string | null {
  const desk = /^inspector-\d$/.test(name) ? "inspector" : name;
  return seatName(desk) === null ? null : desk;
}

function isNames(v: unknown): boolean {
  return Array.isArray(v) && v.every((x) => typeof x === "string");
}

function isSnapshot(slot: unknown): slot is SlotSnapshot {
  if (typeof slot !== "object" || slot === null) return false;
  const s = slot as Record<string, unknown>;
  return (
    (s.state === "take" || s.state === "wait" || s.state === "free") &&
    isNames(s.kinds) &&
    isNames(s.holders) &&
    isNames(s.waiting)
  );
}

/** The sign of every desk, from the newest snapshot on the board (ids sort in writing order). */
export function slotSigns(notes: BoardNote[], now: Date): Map<string, SlotSign> {
  const signs = new Map<string, SlotSign>();
  const snapshots = notes
    .filter((n) => isSnapshot(n.slot))
    .toSorted((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const newest = snapshots[snapshots.length - 1];
  if (!newest?.slot) return signs;
  const age = (now.getTime() - Date.parse(newest.at)) / 60000;
  if (!(age <= SLOT_SIGN_MAX_AGE_MIN)) return signs;
  for (const holder of newest.slot.holders) {
    const desk = deskOf(holder);
    if (desk === null) continue;
    // the estimate is the take's: the newest take that named this holder
    const take = snapshots.findLast((n) => n.slot?.state === "take" && n.slot.holders.includes(holder));
    const estimate = take?.slot?.estimate_min;
    signs.set(desk, { kind: "test", estimateMin: typeof estimate === "number" && estimate > 0 ? estimate : null });
  }
  newest.slot.waiting.forEach((name, index) => {
    const desk = deskOf(name);
    if (desk !== null && !signs.has(desk)) signs.set(desk, { kind: "queue", position: index + 1 });
  });
  return signs;
}

export function slotSignText(sign: SlotSign): string {
  return sign.kind === "test" ? "TEST" : `Sıra ${sign.position}`;
}

/** The sign's spoken label, for the desk's title and screen readers. */
export function slotSignLabel(seat: string, sign: SlotSign, kinds: string[] = ["heavy"]): string {
  const who = seatName(seat) ?? seat;
  if (sign.kind === "queue") return `${who} test sırasında ${sign.position}.`;
  const what = kinds.map((k) => KIND_TR[k] ?? k).join(", ");
  const estimate = sign.estimateMin !== null ? `, tahmini ${sign.estimateMin} dk` : "";
  return `${who} ${what} test koşuyor${estimate}`;
}

/** The small sign a desk carries; the Ofis view places it on the seat's desk. */
export function SlotSignBadge({ seat, sign }: { seat: string; sign: SlotSign }): ReactElement {
  const label = slotSignLabel(seat, sign);
  return createElement(
    "span",
    { className: "office-slot-sign", "data-slot-sign": sign.kind, title: label, "aria-label": label },
    slotSignText(sign),
  );
}

/** The signs now; an unreachable or refusing board is no sign, never an error on the page. */
export async function fetchSlotSigns(now: Date = new Date()): Promise<Map<string, SlotSign>> {
  try {
    const response = await apiFetch(`${BOARD_PATH}?limit=${BOARD_READ_LIMIT}`);
    if (!response.ok) return new Map();
    const body = (await response.json()) as { notes?: BoardNote[] };
    return slotSigns(Array.isArray(body.notes) ? body.notes : [], now);
  } catch {
    return new Map();
  }
}
