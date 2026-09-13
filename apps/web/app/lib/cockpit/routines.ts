"use client";

/**
 * B14 req 295: the routines the owner has, and the two controls over them.
 *
 * The Routine Engine has been complete since M18 and had no surface at all — no panel, no
 * voice tool, nothing but `/v1/routines` and a clock. Every one of the seven routines in
 * production was created by the alarm subsystem on the owner's behalf, and the owner could
 * not see that this was so.
 *
 * Pause and resume are the only writes here, and they are the right two: cancelling is
 * permanent and belongs where the owner is already looking at what they are ending, while
 * "not this week" is exactly the kind of decision somebody makes while scanning a list.
 */

import { apiFetch } from "../session";
import { type Loaded, load } from "./api";

export const ROUTINES_PATH = "/v1/routines";

export function routinePausePath(routineId: string): string {
  return `/v1/routines/${encodeURIComponent(routineId)}/pause`;
}

export function routineResumePath(routineId: string): string {
  return `/v1/routines/${encodeURIComponent(routineId)}/resume`;
}

/** One routine as `/v1/routines` lists it, every field verbatim or `null`. */
export type RoutineRow = {
  routine_id: string;
  name: string;
  /** `armed` | `paused` | `completed` | `cancelled`, or whatever the row says. */
  status: string | null;
  /** `at` | `schedule` | `presence` | `condition`, or whatever the row says. */
  trigger_kind: string | null;
  /** The trigger's own fields; shaped differently per kind, so read defensively. */
  trigger: Record<string, unknown>;
  created_at: string | null;
  paused_at: string | null;
  pause_reason: string | null;
  /** B14 req 294: which side of a condition trigger's edge it was last on. */
  last_condition_met: boolean;
  last_condition_at: string | null;
  source: string | null;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

export function parseRoutine(raw: unknown): RoutineRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.routine_id);
  if (id === null) return null;
  return {
    routine_id: id,
    name: str(o.name) ?? "(adsız)",
    status: str(o.status),
    trigger_kind: str(o.trigger_kind),
    trigger: o.trigger && typeof o.trigger === "object" ? (o.trigger as Record<string, unknown>) : {},
    created_at: str(o.created_at),
    paused_at: str(o.paused_at),
    pause_reason: str(o.pause_reason),
    last_condition_met: o.last_condition_met === true,
    last_condition_at: str(o.last_condition_at),
    source: str(o.source),
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

export const fetchRoutines = (): Promise<Loaded<RoutineRow[]>> =>
  load<RoutineRow[]>(ROUTINES_PATH, (raw) =>
    rowsAt(raw, ["routines", "items"]).map(parseRoutine).filter(isPresent),
  );

export type RoutineAction = "pause" | "resume";

async function post(path: string): Promise<RoutineRow | null> {
  const response = await apiFetch(path, { method: "POST" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const text = await response.text();
  return parseRoutine(text ? (JSON.parse(text) as unknown) : null);
}

/** The two writes this panel can make, as one object so a test can hand it a double. */
export type RoutineClient = {
  pause: (routineId: string) => Promise<RoutineRow | null>;
  resume: (routineId: string) => Promise<RoutineRow | null>;
};

export const routineClient: RoutineClient = {
  pause: (routineId: string) => post(routinePausePath(routineId)),
  resume: (routineId: string) => post(routineResumePath(routineId)),
};
