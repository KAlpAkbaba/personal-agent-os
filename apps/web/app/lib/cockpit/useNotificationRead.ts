"use client";

/**
 * B11 req 368: marking one notification read, and nothing else.
 *
 * The smallest control in this cockpit, deliberately. Reading a notification is the owner's
 * own act on their own record — it reaches no provider, changes nothing outside the
 * `notifications` table, and approves nothing. So there is no gate here and no
 * confirmation: the only things to get right are that one call runs at a time, and that a
 * failure is SAID rather than swallowed.
 *
 * A row that silently stayed unread would be the panel claiming an act the server never
 * performed — the same shape as an inbox that says delivered when nothing carried it, which
 * is the defect this whole batch removes.
 *
 * Split into `runNotificationRead` (pure, ports in) and the hook that binds it to React, so
 * both rules can be proven without a renderer — the same shape as `runApproval`.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { UnauthorizedError } from "../session";
import { type NotificationClient, notificationClient as realClient } from "./notifications";

export type NotificationReadState = {
  /** The row whose read call is in flight, or `null`. */
  busyId: string | null;
  /** What the last call said when it did NOT succeed; `null` after a success. */
  error: string | null;
};

export const NOTIFICATION_READ_IDLE: NotificationReadState = { busyId: null, error: null };

/** One line for the owner from whatever the call threw. */
export function readErrorText(err: unknown): string {
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

export type NotificationReadPorts = {
  client: NotificationClient;
  read: () => NotificationReadState;
  write: (next: NotificationReadState) => void;
  /** The cockpit's panel refresh, called only after a call that succeeded. */
  onSettled?: () => void;
};

/**
 * Mark one row read. Returns `false`, having called nothing, when a call is already in
 * flight; `true` once the call settled and its outcome was written.
 *
 * The refresh runs only on success: re-reading the inbox after a failure would replace the
 * error with a list that looks exactly like the one before the click, and the owner would
 * be left thinking the row is read.
 */
export async function runNotificationRead(
  ports: NotificationReadPorts,
  notificationId: string,
): Promise<boolean> {
  if (ports.read().busyId !== null) return false;
  ports.write({ busyId: notificationId, error: null });
  try {
    await ports.client.markRead(notificationId);
  } catch (err) {
    ports.write({ busyId: null, error: readErrorText(err) });
    return true;
  }
  ports.write(NOTIFICATION_READ_IDLE);
  // The row's own answer is not trusted over the table: the next poll reads the inbox, so
  // the count beside the title and the row agree, or neither moves.
  ports.onSettled?.();
  return true;
}

export type NotificationReadControl = NotificationReadState & {
  markRead: (notificationId: string) => void;
};

export function useNotificationRead(
  onSettled?: () => void,
  client: NotificationClient = realClient,
): NotificationReadControl {
  const [state, setState] = useState<NotificationReadState>(NOTIFICATION_READ_IDLE);
  const latest = useRef<NotificationReadState>(NOTIFICATION_READ_IDLE);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);

  const markRead = useCallback(
    (notificationId: string) => {
      void runNotificationRead(
        {
          client,
          read: () => latest.current,
          write: (next) => {
            latest.current = next;
            setState(next);
          },
          onSettled: () => settled.current?.(),
        },
        notificationId,
      );
    },
    [client],
  );

  return { ...state, markRead };
}
