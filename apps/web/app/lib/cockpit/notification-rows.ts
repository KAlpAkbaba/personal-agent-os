/**
 * What the Bildirimler panel says about one row (B11 req 368/377/378).
 *
 * Pure, so the two sentences that matter can be proven without a renderer:
 *
 * * **delivered is not read.** A toast that appeared on a locked screen at 03:00 reached
 *   the owner's machine and not the owner. The panel must be able to say "iletildi,
 *   okunmadı"; collapsing the two is how a system convinces itself it communicated.
 * * **nothing carried it** is its own state, not an absence. `delivered_at === null` on a
 *   row that has been sitting there since yesterday is the ladder having run out of rungs,
 *   and it is the part of a delivery history worth reading.
 */

import type { NotificationRow } from "./notifications";

/** How many rows the panel lists. The rest are in the inbox; the panel is a glance, not an archive. */
export const NOTIFICATION_ROWS_SHOWN = 12;

export const PRIORITY_URGENT = "urgent";
export const PRIORITY_NORMAL = "normal";
export const PRIORITY_LOW = "low";

/** The three levels in the owner's words. An unknown value is shown verbatim, never dropped. */
export const PRIORITY_TR: Record<string, string> = {
  [PRIORITY_URGENT]: "acil",
  [PRIORITY_NORMAL]: "normal",
  [PRIORITY_LOW]: "düşük",
};

export function priorityLabel(priority: string | null): string {
  if (!priority) return "normal";
  return PRIORITY_TR[priority] ?? priority;
}

/** The channels the ladder can reach the owner through, in its own order. */
export const CHANNEL_TR: Record<string, string> = {
  toast: "masaüstü bildirimi",
  sound: "ses",
  push: "telefon",
  inbox: "kutu",
};

export function channelLabel(channel: string | null): string | null {
  if (!channel) return null;
  return CHANNEL_TR[channel] ?? channel;
}

export type DeliveryState = "unreached" | "delivered" | "read";

/**
 * The three states of one row, kept apart on purpose.
 *
 * `read` implies delivered, so it is tested first; a row read from this very panel was
 * delivered by the inbox rung whether or not a channel was ever recorded.
 */
export function deliveryState(row: Pick<NotificationRow, "delivered_at" | "read_at">): DeliveryState {
  if (row.read_at !== null) return "read";
  return row.delivered_at !== null ? "delivered" : "unreached";
}

export const DELIVERY_TR: Record<DeliveryState, string> = {
  unreached: "hiçbir kanal taşımadı",
  delivered: "iletildi, okunmadı",
  read: "okundu",
};

/** The delivery line: the state, and which channel carried it when one did. */
export function deliveryLine(row: Pick<NotificationRow, "delivered_at" | "read_at" | "delivered_via">): string {
  const state = deliveryState(row);
  const via = channelLabel(row.delivered_via);
  if (state === "unreached") return DELIVERY_TR.unreached;
  return via ? `${DELIVERY_TR[state]} · ${via}` : DELIVERY_TR[state];
}

/** True while the row is still the owner's to mark read. Marking a read row again is not a thing. */
export function canMarkRead(row: Pick<NotificationRow, "read_at">): boolean {
  return row.read_at === null;
}

/**
 * The panel's border: something urgent that no channel has carried.
 *
 * Deliberately narrow. An urgent row that WAS delivered has already interrupted the owner
 * once, and a panel that shouts about everything is a panel that gets ignored — which is
 * the failure mode this whole batch exists to remove.
 */
export function needsAttention(rows: readonly Pick<NotificationRow, "priority" | "delivered_at" | "read_at">[]): boolean {
  return rows.some((row) => row.priority === PRIORITY_URGENT && deliveryState(row) === "unreached");
}

/** The badge beside the title: unread count, or nothing when there is nothing waiting. */
export function inboxBadge(unread: number): string | null {
  return unread > 0 ? `${unread} okunmamış` : null;
}

// ------------------------------------------------------------------- the time

const TIME_ZONE = "Europe/Istanbul";
const stamp = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: TIME_ZONE,
});

/** When it happened, in the owner's zone; the token verbatim when it cannot be read. */
export function formatWhen(iso: string | null): string {
  if (!iso) return "zaman bildirilmedi";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : stamp.format(at);
}
