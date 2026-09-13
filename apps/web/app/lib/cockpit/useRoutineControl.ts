"use client";

/**
 * B14 req 295: pausing and resuming a routine from the Cockpit.
 *
 * The same ports-in shape `runApproval` and `runNotificationRead` use, and for the same
 * reason: one call at a time, and a failure is SAID rather than swallowed. A row that
 * silently stayed armed after the owner pressed Duraklat would be the panel claiming an act
 * the server never performed — and the consequence is a routine that runs tomorrow morning
 * when the owner believes they turned it off.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { UnauthorizedError } from "../session";
import { type RoutineAction, type RoutineClient, routineClient as realClient } from "./routines";

export type RoutineControlState = {
  /** The routine whose call is in flight, or `null`. */
  busyId: string | null;
  /** What the last call said when it did NOT succeed; `null` after a success. */
  error: string | null;
};

export const ROUTINE_CONTROL_IDLE: RoutineControlState = { busyId: null, error: null };

export function controlErrorText(err: unknown): string {
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

export type RoutineControlPorts = {
  client: RoutineClient;
  read: () => RoutineControlState;
  write: (next: RoutineControlState) => void;
  /** The cockpit's panel refresh, called only after a call that succeeded. */
  onSettled?: () => void;
};

/**
 * Run one pause/resume. Returns `false`, having called nothing, when a call is already in
 * flight; `true` once the call settled and its outcome was written.
 *
 * The refresh runs only on success: re-reading the list after a failure would replace the
 * error with a list that looks exactly like the one before the click, and the owner would
 * be left thinking the routine is paused.
 */
export async function runRoutineControl(
  ports: RoutineControlPorts,
  action: RoutineAction,
  routineId: string,
): Promise<boolean> {
  if (ports.read().busyId !== null) return false;
  ports.write({ busyId: routineId, error: null });
  try {
    await (action === "pause" ? ports.client.pause(routineId) : ports.client.resume(routineId));
  } catch (err) {
    ports.write({ busyId: null, error: controlErrorText(err) });
    return true;
  }
  ports.write(ROUTINE_CONTROL_IDLE);
  ports.onSettled?.();
  return true;
}

export type RoutineControlProps = RoutineControlState & {
  onControl: (action: RoutineAction, routineId: string) => void;
};

export function useRoutineControl(
  onSettled?: () => void,
  client: RoutineClient = realClient,
): RoutineControlProps {
  const [state, setState] = useState<RoutineControlState>(ROUTINE_CONTROL_IDLE);
  const latest = useRef<RoutineControlState>(ROUTINE_CONTROL_IDLE);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);

  const onControl = useCallback(
    (action: RoutineAction, routineId: string) => {
      void runRoutineControl(
        {
          client,
          read: () => latest.current,
          write: (next) => {
            latest.current = next;
            setState(next);
          },
          onSettled: () => settled.current?.(),
        },
        action,
        routineId,
      );
    },
    [client],
  );

  return { ...state, onControl };
}
