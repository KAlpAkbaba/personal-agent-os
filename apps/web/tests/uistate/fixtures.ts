/**
 * Fixtures for the Core's state tests.
 *
 * Every event here is shaped exactly as `UiStateEvent.as_dict()` serialises it,
 * with the metadata keys the real publishers actually send (checked against
 * `app/voice/realtime_sessions/service.py`, `app/research/browser_activities.py`,
 * `app/evolution/service.py`, `app/goals/cognitive.py`,
 * `app/selfmodel/progress.py` and, for the M19 operator, `app/operator/service.py`
 * on the core track). Inventing a convenient key here would let the renderer
 * pass its tests while failing against the real bus.
 */

import {
  KNOWN_CONTRACT_VERSION,
  type UiStateEvent,
  type UiStateResponse,
} from "../../app/lib/uistate/contract";

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
    // Follows the client's own version so a contract bump cannot leave every
    // fixture silently exercising the mismatch path instead of the real one.
    contract_version: KNOWN_CONTRACT_VERSION,
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

// -------------------------------------------------------- v2: the room

/**
 * Local perception running. `camera` is a short device token and nothing else:
 * no frame, no thumbnail, no identity — the perception layer emits structured
 * observations only (M18 spec §2).
 */
export const EYE_ACTIVE = () =>
  event({
    state: "eye.active",
    subsystem: "system",
    status: "active",
    metadata: { camera: "cam-0" },
  });

export const EYE_DISABLED = (reason = "owner_command") =>
  event({
    state: "eye.disabled",
    subsystem: "system",
    status: "disabled",
    metadata: { reason },
  });

/**
 * A presence inference. `confidence` is the engine's own figure and every
 * fixture carries one, because a presence state without a confidence is the
 * exception this client has to render specially rather than the normal case.
 */
export const OWNER_PRESENT = (confidence = 0.91) =>
  event({
    state: "owner.present",
    subsystem: "system",
    status: "present",
    metadata: { confidence, signals: 3 },
  });

export const OWNER_LIKELY_ASLEEP = (confidence = 0.86) =>
  event({
    state: "owner.likely_asleep",
    subsystem: "system",
    status: "likely_asleep",
    metadata: { confidence, signals: 4, ttl_s: 1800 },
  });

export const OWNER_AWAY = (confidence = 0.74) =>
  event({
    state: "owner.away",
    subsystem: "system",
    status: "away",
    metadata: { confidence, signals: 2 },
  });

/** A presence state the engine published with no confidence at all. */
export const OWNER_PRESENT_NO_CONFIDENCE = () =>
  event({ state: "owner.present", subsystem: "system", status: "present" });

export const ROUTINE_ARMED = () =>
  event({
    state: "routine.armed",
    subsystem: "system",
    status: "armed",
    label: "Sabah brifingi",
  });

export const ALARM_TRIGGERED = () =>
  event({ state: "alarm.triggered", subsystem: "system", status: "triggered" });

// ------------------------------------------------ v3: the wake alarm (M18.3)

/**
 * The wake alarm's lifecycle as `app/alarms` publishes it: subsystem
 * `routine`, one event per transition, the label as the owner would read it.
 * The ramp level rides on `intensity` while the alarm is actually sounding
 * (spec §7), and is absent everywhere else.
 */
export const ALARM_ARMED = (label = "Alarm 07:30") =>
  event({ state: "alarm.armed", subsystem: "routine", status: "armed", label });

export const ALARM_FIRING = () =>
  event({ state: "alarm.firing", subsystem: "routine", status: "firing", label: "Alarm 07:30" });

export const ALARM_PLAYING = (intensity: number | null = 0.45) =>
  event({
    state: "alarm.playing",
    subsystem: "routine",
    status: "playing",
    label: "Alarm 07:30",
    intensity,
    metadata: { media_kind: "youtube" },
  });

export const ALARM_GREETING = () =>
  event({
    state: "alarm.greeting",
    subsystem: "routine",
    status: "greeting",
    label: "Alarm 07:30",
    intensity: 0.15,
  });

export const ALARM_STOPPED = () =>
  event({ state: "alarm.stopped", subsystem: "routine", status: "stopped" });

export const ALARM_FAILED = () =>
  event({
    state: "alarm.failed",
    subsystem: "routine",
    status: "failed",
    severity: "critical",
    metadata: { error_class: "media_unavailable" },
  });

/** A test alarm, flagged as one by the publisher. */
export const ALARM_TEST_PLAYING = () =>
  event({
    state: "alarm.playing",
    subsystem: "routine",
    status: "playing",
    label: "Test alarmı",
    intensity: 0.3,
    metadata: { is_test: true },
  });

/** Display power, published by the ambient policy engine. Never machine state. */
export const DISPLAY_ON = () =>
  event({ state: "display.on", subsystem: "ambient", status: "on", metadata: { reason: "owner_input" } });

export const DISPLAY_OFF = () =>
  event({ state: "display.off", subsystem: "ambient", status: "off", metadata: { reason: "owner_away" } });

// ------------------------------------------ v4: the Digital Operator (M19 §4)

/**
 * `OperatorTask` transitions as `app/operator/` publishes them: subsystem
 * `operator`, the task id, the owner's goal as the label, and in metadata the
 * step the planner named, the capability sent to the companion and the
 * window title the companion OBSERVED (never the one the plan expected).
 * `error_class` rides on `operator.failed` only.
 */
export const OPERATOR_RUNNING = (
  step = "open_notepad",
  capability = "app.launch",
  windowTitle: string | null = "Adsız - Not Defteri",
) =>
  event({
    state: "operator.running",
    subsystem: "operator",
    task_id: "op-task-1",
    status: "acting",
    label: "Not Defteri'ni aç",
    metadata: {
      step,
      capability,
      ...(windowTitle === null ? {} : { window_title: windowTitle }),
    },
  });

export const OPERATOR_VERIFYING = (step = "open_notepad", capability = "app.launch") =>
  event({
    state: "operator.verifying",
    subsystem: "operator",
    task_id: "op-task-1",
    status: "verifying",
    label: "Not Defteri'ni aç",
    metadata: { step, capability, window_title: "Adsız - Not Defteri" },
  });

export const OPERATOR_FAILED = (errorClass: string | null = "focus_mismatch") =>
  event({
    state: "operator.failed",
    subsystem: "operator",
    task_id: "op-task-1",
    status: "failed",
    severity: "warning",
    label: "Buraya merhaba yaz",
    metadata: {
      step: "type_text",
      capability: "keyboard.type",
      window_title: "Hesap Makinesi",
      ...(errorClass === null ? {} : { error_class: errorClass }),
    },
  });

/** An operator event whose publisher sent no metadata at all. */
export const OPERATOR_RUNNING_BARE = () =>
  event({ state: "operator.running", subsystem: "operator", task_id: "op-task-2" });

/**
 * A step event in the shape `OperatorService._on_step` publishes: the step as
 * its zero-based INDEX plus the plan's length, the step's name as the label,
 * the capability, and the window title the companion observed.
 */
export const OPERATOR_RUNNING_INDEXED = (
  index = 0,
  count: number | null = 3,
  label: string | null = "open_notepad",
  state: "operator.running" | "operator.verifying" = "operator.running",
) =>
  event({
    state,
    subsystem: "operator",
    task_id: "op-task-3",
    status: state === "operator.running" ? "acting" : "verifying",
    label,
    metadata: {
      step: index,
      ...(count === null ? {} : { step_count: count }),
      capability: "app.launch",
      window_title: "Adsız - Not Defteri",
    },
  });

/**
 * A task-level failure in the shape `OperatorService.start_task` publishes:
 * the plan's name as the label and the error class alone in metadata — no
 * step, capability or window, because the failure is the task's, not a step's.
 */
export const OPERATOR_FAILED_TASK = (errorClass = "timeout") =>
  event({
    state: "operator.failed",
    subsystem: "operator",
    task_id: "op-task-3",
    severity: "warning",
    label: "app_open",
    metadata: { error_class: errorClass },
  });

// ------------------------------ v5: File & Document Intelligence (M20 §3)

/**
 * `document.analysis` as the M20 spec §3 has the Cloud Core publish it:
 * subsystem `documents`, the task id, in metadata the file's NAME, the
 * reference of the place inside it (the §2 scheme), the step, and — on an
 * answer — the refs it cited as `[{ref, path, excerpt}]`. `path` rides beside
 * `file` only when the Core named the file by path (two documents, one title).
 */
export const DOCUMENT_ANALYSIS = (
  file: string | null = "rapor.pdf",
  part: string | null = "p3",
  step: string | null = "answer",
  extra: Record<string, unknown> = {},
) =>
  event({
    state: "document.analysis",
    subsystem: "documents",
    task_id: "doc-task-1",
    status: "analysing",
    metadata: {
      ...(file === null ? {} : { file }),
      ...(part === null ? {} : { part }),
      ...(step === null ? {} : { step }),
      ...(extra as Record<string, string | number | boolean>),
    },
  });

/** The same event with the refs an answer cited, in the shape the bus carries them. */
export const DOCUMENT_ANSWERED = (
  refs: Array<{ ref: string; path?: string | null; excerpt?: string | null }> = [
    { ref: "p3", path: "C:\\Users\\alpak\\Documents\\rapor.pdf", excerpt: "Üçüncü sayfada yer alan bu cümle referans testidir." },
  ],
  file = "rapor.pdf",
  part = "p3",
) => ({
  ...DOCUMENT_ANALYSIS(file, part, "answer"),
  refs: refs.map((r) => ({ ref: r.ref, path: r.path ?? null, excerpt: r.excerpt ?? null })),
});

/** A document event whose publisher sent no metadata at all. */
export const DOCUMENT_ANALYSIS_BARE = () =>
  event({ state: "document.analysis", subsystem: "documents", task_id: "doc-task-2" });

// ------------------------------------------- v6: Mail & Calendar (M21 §3)

/**
 * `mail.activity` as the M21 spec §3 has the Cloud Core publish it:
 * subsystem `mail`, the task id, and in metadata the folder being read, the
 * subject in hand and — for a draft — its lifecycle step as `draft_state`.
 * A plain read carries no `draft_state` at all.
 */
export const MAIL_ACTIVITY = (
  folder: string | null = "INBOX",
  subject: string | null = null,
  draftState: string | null = null,
  extra: Record<string, unknown> = {},
) =>
  event({
    state: "mail.activity",
    subsystem: "mail",
    task_id: "mail-task-1",
    status: draftState === null ? "reading" : "draft",
    metadata: {
      ...(folder === null ? {} : { folder }),
      ...(subject === null ? {} : { subject }),
      ...(draftState === null ? {} : { draft_state: draftState }),
      ...(extra as Record<string, string | number | boolean>),
    },
  });

/** A mail event whose publisher sent no metadata at all. */
export const MAIL_ACTIVITY_BARE = () => event({ state: "mail.activity", subsystem: "mail", task_id: "mail-task-2" });

/**
 * `calendar.activity` as the spec has the Cloud Core publish it: subsystem
 * `calendar`, in metadata the range being read, the event or proposal title
 * in hand, the proposal's step as `proposal_state`, and the conflicts the
 * Core counted for a proposal.
 */
export const CALENDAR_ACTIVITY = (
  range: string | null = "today",
  title: string | null = null,
  proposalState: string | null = null,
  conflicts: number | null = null,
  extra: Record<string, unknown> = {},
) =>
  event({
    state: "calendar.activity",
    subsystem: "calendar",
    task_id: "cal-task-1",
    status: proposalState === null ? "reading" : "proposal",
    metadata: {
      ...(range === null ? {} : { range }),
      ...(title === null ? {} : { event: title }),
      ...(proposalState === null ? {} : { proposal_state: proposalState }),
      ...(conflicts === null ? {} : { conflicts }),
      ...(extra as Record<string, string | number | boolean>),
    },
  });

/** A calendar event whose publisher sent no metadata at all. */
export const CALENDAR_ACTIVITY_BARE = () =>
  event({ state: "calendar.activity", subsystem: "calendar", task_id: "cal-task-2" });

/** The owner-authorised release path, mid-deployment (ADR-0055). */
export const RELEASE_DEPLOYING = () =>
  event({
    state: "release.deploying",
    subsystem: "deployment",
    status: "deploying",
    module_id: "opp-1",
    progress: 0.4,
    metadata: { risk_tier: 2 },
  });

export const RELEASE_APPROVAL_REQUIRED = () =>
  event({
    state: "release.owner_approval_required",
    subsystem: "evolution",
    status: "owner_approval_required",
    module_id: "opp-1",
    severity: "notice",
    metadata: { risk_tier: 3 },
  });
