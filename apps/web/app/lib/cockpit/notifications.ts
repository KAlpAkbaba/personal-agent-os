"use client";

/**
 * B11 req 368/377: the owner's inbox, and what actually reached them.
 *
 * Two GETs and one POST. `/v1/notifications` is the durable inbox, `/history` is the
 * delivery record including the rows NOTHING carried, and `/{id}/read` marks one read.
 *
 * The distinction this panel exists to keep is `delivered` vs `read`. Before B11 the inbox
 * asked the fake push transport what it remembered, so it was empty in production and lost
 * on every restart; "the owner has been told" was true only while they were already
 * looking. A toast that appeared on a locked screen at 03:00 was delivered and was not
 * read, and the panel must be able to say both — collapsing them is how a system convinces
 * itself it communicated.
 */

import { apiFetch } from "../session";
import { type Loaded, load } from "./api";

export const NOTIFICATIONS_PATH = "/v1/notifications";
export const NOTIFICATIONS_HISTORY_PATH = "/v1/notifications/history";

export function notificationReadPath(notificationId: string): string {
  return `/v1/notifications/${encodeURIComponent(notificationId)}/read`;
}

/** One row of the durable table, every field verbatim or `null`. */
export type NotificationRow = {
  id: string;
  kind: string;
  title: string | null;
  body: string | null;
  /** `urgent` | `normal` | `low`, or whatever the row says. */
  priority: string | null;
  group_key: string | null;
  created_at: string | null;
  /** When a channel carried it. `null` means nothing has — not "not reported". */
  delivered_at: string | null;
  /** Which channel did: `toast` | `sound` | `push` | `inbox`. */
  delivered_via: string | null;
  read_at: string | null;
};

/** The inbox as one answer: the rows, and how many are unread across the whole table. */
export type Inbox = {
  rows: NotificationRow[];
  /** The server's own count, not `rows.filter(...).length` — the list is capped and the badge is not. */
  unread: number;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** One row from a raw object; `null` for anything with no id, which is not a notification. */
export function parseNotification(raw: unknown): NotificationRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.id) ?? str(o.notification_id);
  if (id === null) return null;
  return {
    id,
    kind: str(o.kind) ?? "",
    title: str(o.title),
    body: str(o.body),
    priority: str(o.priority),
    group_key: str(o.group_key),
    created_at: str(o.created_at),
    delivered_at: str(o.delivered_at),
    delivered_via: str(o.delivered_via),
    read_at: str(o.read_at),
  };
}

function isPresent<T>(value: T | null): value is T {
  return value !== null;
}

function rowsAt(raw: unknown, keys: string[]): unknown[] {
  if (Array.isArray(raw)) return raw;
  if (raw && typeof raw === "object") {
    for (const key of keys) {
      const value = (raw as Record<string, unknown>)[key];
      if (Array.isArray(value)) return value;
    }
  }
  return [];
}

export function parseInbox(raw: unknown): Inbox {
  const rows = rowsAt(raw, ["notifications", "items"]).map(parseNotification).filter(isPresent);
  const reported = raw && typeof raw === "object" ? num((raw as Record<string, unknown>).unread) : null;
  // Fall back to counting what we were given rather than showing nothing: a badge that is
  // low because the list was capped is wrong in the safe direction, a missing badge hides
  // that anything is waiting at all.
  return { rows, unread: reported ?? rows.filter((r) => r.read_at === null).length };
}

export const fetchInbox = (): Promise<Loaded<Inbox>> => load<Inbox>(NOTIFICATIONS_PATH, parseInbox);

export const fetchNotificationHistory = (): Promise<Loaded<NotificationRow[]>> =>
  load<NotificationRow[]>(NOTIFICATIONS_HISTORY_PATH, (raw) =>
    rowsAt(raw, ["history", "items"]).map(parseNotification).filter(isPresent),
  );

/** Marking one read. The only write this panel can make, and it changes nothing outside this table. */
export async function markNotificationRead(notificationId: string): Promise<NotificationRow | null> {
  const response = await apiFetch(notificationReadPath(notificationId), { method: "POST" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const text = await response.text();
  return parseNotification(text ? (JSON.parse(text) as unknown) : null);
}

/** The one call the panel can make, as an object so a test can hand it a double. */
export type NotificationClient = {
  markRead: (notificationId: string) => Promise<NotificationRow | null>;
};

export const notificationClient: NotificationClient = { markRead: markNotificationRead };
