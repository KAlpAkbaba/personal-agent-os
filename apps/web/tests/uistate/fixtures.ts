/**
 * Fixtures for the Core's state tests.
 *
 * Every event here is shaped exactly as `UiStateEvent.as_dict()` serialises it,
 * with the metadata keys the real publishers actually send (checked against
 * `app/voice/realtime_sessions/service.py`, `app/research/browser_activities.py`,
 * `app/evolution/service.py`, `app/goals/cognitive.py` and
 * `app/selfmodel/progress.py`). Inventing a convenient key here would let the
 * renderer pass its tests while failing against the real bus.
 */

import type { UiStateEvent, UiStateResponse } from "../../app/lib/uistate/contract";

/** A fixed instant, so every age assertion is exact. */
export const T0 = Date.parse("2026-09-05T20:00:00.000Z");

export function iso(offsetMs: number): string {
  return new Date(T0 + offsetMs).toISOString().replace(".000Z", ".000Z");
}

let seq = 0;
export function resetSequence(): void {
  seq = 0;
}

export function event(partial: Partial<UiStateEvent> & { state: string }): UiStateEvent {
  seq += 1;
  return {
    event_id: `evt-${seq}`,
    sequence: seq,
    subsystem: "system",
    at: iso(0),
    intensity: null,
    progress: null,
    severity: "info",
    status: null,
    task_id: null,
    goal_id: null,
    module_id: null,
    session_id: null,
    label: null,
    metadata: {},
    ...partial,
  };
}

export function response(
  events: UiStateEvent[],
  overrides: Partial<UiStateResponse> = {},
): UiStateResponse {
  const current = events.length ? events[events.length - 1] : null;
  return {
    contract_version: 1,
    current,
    events,
    sequence: current?.sequence ?? 0,
    ...overrides,
  };
}

// --------------------------------------------------- real publisher shapes

/** `mic_speech_start` from the voice control plane, with client-derived energy. */
export const VOICE_LISTENING = () =>
  event({
    state: "agent.listening",
    subsystem: "voice",
    intensity: 0.5,
    session_id: "sess-1",
    status: "listening",
    metadata: { turn: 3 },
  });

/** `first_audio`: the assistant started speaking, at the reported energy. */
export const VOICE_SPEAKING = (intensity: number | null = 0.62) =>
  event({
    state: "agent.speaking",
    subsystem: "voice",
    intensity,
    session_id: "sess-1",
    status: "speaking",
    metadata: { turn: 3 },
  });

/** `end_of_turn`. */
export const VOICE_THINKING = () =>
  event({
    state: "agent.thinking",
    subsystem: "voice",
    intensity: 0.7,
    session_id: "sess-1",
    metadata: { turn: 3 },
  });

/** The ranking stage of a real research run: candidates seen, candidates kept. */
export const RESEARCH_RANKING = (candidates = 12, kept = 5) =>
  event({
    state: "agent.researching",
    subsystem: "research",
    intensity: 0.7,
    task_id: "task-1",
    status: "ranking",
    label: "hetzner nbg1 fiyatları",
    metadata: { candidates, kept },
  });

/** Research that published no counts at all. */
export const RESEARCH_NO_COUNTS = () =>
  event({
    state: "agent.researching",
    subsystem: "research",
    intensity: 0.7,
    task_id: "task-2",
    status: "planning",
  });

/** The self-model indexer: progress lives in `metadata.percent`, 0..100. */
export const SELFMODEL_THINKING = (percent = 40) =>
  event({
    state: "agent.thinking",
    subsystem: "self_model",
    metadata: { phase: "symbols", done: 40, total: 100, percent },
  });

/** The evolution lab, mid-build. */
export const LAB_BUILDING = () =>
  event({
    state: "evolution.building",
    subsystem: "evolution",
    status: "building",
    module_id: "opp-1",
    progress: 0.5,
    metadata: { composite: 0.72 },
  });

/** A real candidate parked at SHADOW_READY, awaiting the owner. */
export const LAB_SHADOW_READY = () =>
  event({
    state: "evolution.shadow_ready",
    subsystem: "evolution",
    status: "shadow_ready",
    module_id: "opp-1",
    severity: "notice",
    metadata: { composite: 0.72 },
  });

export const GOAL_WAITING_OWNER = () =>
  event({
    state: "agent.waiting_owner",
    subsystem: "goal",
    goal_id: "goal-1",
    status: "waiting_owner",
    label: "Yedekleme politikasını gözden geçir",
  });

export const GOAL_COMPLETED = () =>
  event({
    state: "agent.goal_completed",
    subsystem: "goal",
    goal_id: "goal-1",
    status: "achieved",
    label: "Yedekleme politikasını gözden geçir",
  });

export const AGENT_IDLE = () => event({ state: "agent.idle", subsystem: "system" });

export const AGENT_ERROR = (severity = "warning") =>
  event({
    state: "agent.error",
    subsystem: "voice",
    intensity: 0.8,
    severity,
    status: "error",
    metadata: { turn: 4 },
  });

export const TOOL_RUNNING = () =>
  event({
    state: "agent.tool_running",
    subsystem: "goal",
    goal_id: "goal-1",
    status: "active",
    metadata: { attempt: 2 },
  });

export const MEMORY_RETRIEVAL = (progress: number | null = null) =>
  event({
    state: "agent.memory_retrieval",
    subsystem: "experience",
    progress,
    status: "compile_started",
  });
