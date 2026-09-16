"use client";

/**
 * Read-only clients for the cockpit's detail panels.
 *
 * The Core is driven entirely by `/v1/ui/state`. These endpoints answer the
 * different question the cockpit asks — not "what is happening now" but "what
 * currently exists": which goals, which candidates, which facts, what the
 * ledger recorded. Every call in this file is a GET. The cockpit's one
 * mutating client — M21's approval pair, `approvals.ts` — asks the Cloud Core
 * to run its own confirmation gate on a draft or proposal and decides
 * nothing; approving a candidate or a goal remains an owner action taken on
 * the surface that owns it.
 *
 * The important type in this file is `Loaded<T>`. A panel must be able to say
 * "there are no goals" and "I could not find out whether there are goals" in
 * different words, because showing an empty list for a failed request is the
 * same class of lie as animating work that is not happening.
 */

import { type Failure, classifyFailure, failureFromBody } from "../errors/failure";
import { type FocusState, parseFocusState } from "../research/focus";
import { UnauthorizedError, apiFetch } from "../session";

export type Loaded<T> =
  | { kind: "loading" }
  | { kind: "ok"; value: T; at: number }
  /**
   * B22 req 705/708-711: `error` is what a reader sees and `failure` is what the panel
   * DECIDES with. The server answers a failure with its own class and a Turkish sentence
   * (`app.errors.catalog`); this used to throw both away and keep "HTTP 500", which tells
   * the owner that something went wrong, not what, and above all not whether pressing
   * anything would help.
   */
  | { kind: "failed"; error: string; failure?: Failure }
  /**
   * The endpoint is not on this server yet (a 404 from a route a parallel
   * track is still building).
   *
   * A fourth outcome, because it is a fourth fact: "there are no alarms",
   * "I could not find out whether there are alarms" and "this Cloud Core does
   * not have alarms yet" are three different sentences, and rendering the
   * third as either of the first two is the same class of lie as an empty list
   * for a failed request.
   */
  | { kind: "absent"; detail: string };

export function isOk<T>(state: Loaded<T>): state is { kind: "ok"; value: T; at: number } {
  return state.kind === "ok";
}

/** Raised so `load` can tell a missing route from a broken one. */
class NotFoundError extends Error {
  constructor(path: string) {
    super(`not found: ${path}`);
    this.name = "NotFoundError";
  }
}

/** A non-OK answer, carrying what the server said about it. */
class ApiFailure extends Error {
  constructor(readonly failure: Failure) {
    super(failure.message);
    this.name = "ApiFailure";
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await apiFetch(path);
  if (response.status === 404) throw new NotFoundError(path);
  if (!response.ok) {
    // Read the body BEFORE deciding what to say: it holds the class the panel needs to
    // tell "waiting on a key" from "try again" (req 708/709), and the Turkish sentence
    // the owner should read instead of a status line.
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw new ApiFailure(failureFromBody(body, response.status));
  }
  return (await response.json()) as T;
}

/** Never throws; a panel failing must not take the page or its siblings down. */
export async function load<T>(path: string, pick: (raw: unknown) => T): Promise<Loaded<T>> {
  try {
    return { kind: "ok", value: pick(await getJson<unknown>(path)), at: Date.now() };
  } catch (err) {
    if (err instanceof UnauthorizedError) return { kind: "failed", error: "oturum reddedildi" };
    if (err instanceof NotFoundError) {
      return { kind: "absent", detail: `Bu Cloud Core sürümünde ${path} yok (HTTP 404).` };
    }
    if (err instanceof ApiFailure) {
      // `error` keeps the shape every panel already renders; `failure` is the decision.
      return {
        kind: "failed",
        error: err.failure.status ? `HTTP ${err.failure.status}` : err.failure.message,
        failure: err.failure,
      };
    }
    const message = err instanceof Error ? err.message : String(err);
    return { kind: "failed", error: message, failure: classifyFailure("", message, 0) };
  }
}

function arrayAt<T>(raw: unknown, key: string): T[] {
  if (!raw || typeof raw !== "object") return [];
  const value = (raw as Record<string, unknown>)[key];
  return Array.isArray(value) ? (value as T[]) : [];
}

// --------------------------------------------------------------- research

export type ResearchTask = {
  task_id: string;
  topic: string;
  status: string;
  stage: string;
  device: string | null;
  created_at: string | null;
  ready_at: string | null;
  artifact_id: string | null;
  // M18.2 identity metadata. Optional because it is optional on the wire; a
  // field an older Cloud Core does not report is left out of the row's
  // identity line rather than filled in with a plausible value.
  mode?: string | null;
  source_count?: number | null;
  completed_at?: string | null;
  is_focus?: boolean | null;
};

export const fetchResearchTasks = () =>
  load<ResearchTask[]>("/v1/research", (raw) => arrayAt<ResearchTask>(raw, "tasks"));

/**
 * The conversational focus (M18.2): which completed report "Bunu anlat."
 * refers to. A Cloud Core without the route answers `absent`, which the panel
 * says in as many words rather than drawing as "no focus".
 */
export const fetchResearchFocus = () =>
  load<FocusState>("/v1/research/focus", parseFocusState);

// ------------------------------------------------------------------ goals

export type Goal = {
  goal_id: string;
  title: string;
  status: string;
  priority: number;
  horizon: string;
  deadline: string | null;
  requires_owner_approval: boolean;
  approved_at: string | null;
  success_criteria: unknown[];
  blockers: unknown[];
};

export const GOAL_STATUS_LABEL: Record<string, string> = {
  draft: "taslak",
  active: "etkin",
  blocked: "engelli",
  waiting_owner: "sahibi bekliyor",
  achieved: "başarıldı",
  abandoned: "bırakıldı",
  superseded: "yerine geçildi",
};

export const fetchGoals = () =>
  load<Goal[]>("/v1/goals?limit=100", (raw) => arrayAt<Goal>(raw, "goals"));

// -------------------------------------------------------------- evolution

export type Opportunity = {
  opportunity_id: string;
  title: string;
  status: string;
  scores: { composite?: number } & Record<string, number | undefined>;
  candidate_ref: string | null;
  approved_at: string | null;
  updated_at: string | null;
  /**
   * Risk tier 1..5, derived server-side from what the change actually touches,
   * or `null` when it was never derived. `null` is a real answer ("not
   * assessed") and is rendered as one — never as a guessed tier.
   */
  risk_tier?: number | null;
  risk_tier_label?: string | null;
  requires_second_confirmation?: boolean | null;
  risk_reasons?: string[];
  /**
   * M18.4 (ADR-0081): derived by the Evolution Supervisor at creation - the priority
   * from the signal's kind, the promotion class from the risk table. `null` means the
   * row predates the supervisor or was opened by hand; it is said as "sınıflanmadı".
   */
  priority?: string | null;
  promotion_class?: string | null;
};

export const PROMOTION_CLASS_LABEL: Record<string, string> = {
  AUTO_SAFE: "kendi başına güvenli",
  AUTO_CANARY: "kanaryaya kadar kendi başına",
  OWNER_APPROVAL_REQUIRED: "sahip onayı gerekir",
  NEVER_AUTO_PROMOTE: "asla kendi başına canlıya çıkmaz",
};

export const fetchOpportunities = () =>
  load<Opportunity[]>("/v1/evolution/opportunities?limit=50", (raw) =>
    arrayAt<Opportunity>(raw, "opportunities"),
  );

export type ShadowReady = {
  awaiting_approval: Opportunity[];
  count: number;
  note: string;
  /** The tier at and above which the server demands a second confirmation. */
  second_confirmation_floor: number | null;
};

export const fetchShadowReady = () =>
  load<ShadowReady>("/v1/evolution/shadow-ready?limit=50", (raw) => {
    const o = (raw ?? {}) as Record<string, unknown>;
    return {
      awaiting_approval: arrayAt<Opportunity>(raw, "awaiting_approval"),
      count: typeof o.count === "number" ? o.count : 0,
      note: typeof o.note === "string" ? o.note : "",
      second_confirmation_floor:
        typeof o.second_confirmation_floor === "number" ? o.second_confirmation_floor : null,
    };
  });

// ------------------------------------------------------------ world model

export type WorldFact = {
  key: string;
  category: string;
  value: unknown;
  truth_kind: string;
  observed_at: string;
  confidence: number;
  stale: boolean;
  note: string;
};

export type WorldUncertainty = { category: string; subject: string; reason: string };

export type World = {
  facts: WorldFact[];
  uncertainties: WorldUncertainty[];
  generated_at: string | null;
};

/** The four truth kinds are never averaged (ADR-0053 §2); they are labelled. */
export const TRUTH_KIND_LABEL: Record<string, string> = {
  source_truth: "kaynak",
  installed_truth: "kurulu",
  runtime_truth: "çalışan",
  evidence_truth: "kanıt",
};

export const fetchWorld = () =>
  load<World>("/v1/world", (raw) => {
    const o = (raw ?? {}) as Record<string, unknown>;
    return {
      facts: arrayAt<WorldFact>(raw, "facts"),
      uncertainties: arrayAt<WorldUncertainty>(raw, "uncertainties"),
      generated_at: typeof o.generated_at === "string" ? o.generated_at : null,
    };
  });

// ----------------------------------------------------------------- ledger

export type LedgerEvent = {
  event_id: string;
  occurred_at: string | null;
  event_type: string;
  subsystem: string | null;
  status: string | null;
  severity: string | null;
  factual_summary: string | null;
  production_state: string | null;
};

/**
 * "Meaningful" is defined by the ledger's own status vocabulary rather than by
 * this client's taste: anything that failed, or that a subsystem marked as a
 * completion, is worth the owner's attention. Filtering on prose would be the
 * renderer deciding what matters, which is not its job.
 */
export const MEANINGFUL_STATUSES = new Set(["failed", "completed", "pending"]);

export const fetchLedgerEvents = () =>
  load<LedgerEvent[]>("/v1/ledger/events?limit=40", (raw) =>
    arrayAt<LedgerEvent>(raw, "events"),
  );

export type PendingBriefing = {
  briefing_id: string;
  created_at: string;
  priority: string | number;
  speech: string;
  delivered_at: string | null;
};

export const fetchPendingBriefings = () =>
  load<PendingBriefing[]>("/v1/ledger/briefings/pending", (raw) =>
    arrayAt<PendingBriefing>(raw, "briefings"),
  );

// ----------------------------------------------------------------- memory

export type MemoryAuditEvent = {
  id: number;
  action: string;
  memory_class: string | null;
  key: string | null;
  actor: string | null;
  created_at: string;
};

export const fetchMemoryAudit = () =>
  load<MemoryAuditEvent[]>("/v1/memory/audit?limit=12", (raw) =>
    arrayAt<MemoryAuditEvent>(raw, "events"),
  );

// ------------------------------------------------------------- experience

export type Lesson = {
  lesson_id: string;
  title: string;
  status: string;
  score: number;
  confidence: number;
  recurrence: number;
};

export const fetchLessons = () =>
  load<Lesson[]>("/v1/experience/lessons?limit=10", (raw) => arrayAt<Lesson>(raw, "lessons"));

// ----------------------------------------------------------------- health

export type HealthCheck = { status: string; latency_ms?: number; error?: string };
export type Health = { status: string; version: string; checks: Record<string, HealthCheck> };

export const fetchHealth = () =>
  load<Health>("/v1/system/health", (raw) => {
    const o = (raw ?? {}) as Record<string, unknown>;
    return {
      status: typeof o.status === "string" ? o.status : "unknown",
      version: typeof o.version === "string" ? o.version : "",
      checks:
        o.checks && typeof o.checks === "object"
          ? (o.checks as Record<string, HealthCheck>)
          : {},
    };
  });

/** A check reporting `skipped` is healthy: the subsystem is not configured. */
export function isHealthy(status: string): boolean {
  return status === "ok" || status === "skipped";
}

// ------------------------------------------------- M18.3: alarms and ambient

/**
 * One wake alarm, as `docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md` §3.1 shapes
 * it. Every field is optional in the parser rather than in the contract: the
 * route is being built on another track, and a renderer that throws on a
 * field that has not landed yet is a renderer that fails the owner for no
 * reason. What is missing is drawn as missing.
 */
export type WakeAlarm = {
  id: string;
  state: string;
  local_time: string | null;
  scheduled_for: string | null;
  timezone: string | null;
  is_test: boolean;
  recurrence: { weekdays?: number[] } | null;
  media_kind: string | null;
  media_title: string | null;
  device_id: string | null;
  snooze_count: number | null;
  terminal_reason: string | null;
};

/** The alarm lifecycle's Turkish names, for the panel's rows. */
export const ALARM_STATE_LABEL: Record<string, string> = {
  SCHEDULED: "kuruldu",
  ARMED: "cihazda hazır",
  FIRING: "tetiklendi",
  DISPLAY_WAKING: "ekran uyandırılıyor",
  MEDIA_STARTING: "ses başlatılıyor",
  PLAYING: "çalıyor",
  GREETING: "seslendiriyor",
  SNOOZED: "ertelendi",
  STOPPED: "durduruldu",
  COMPLETED: "tamamlandı",
  CANCELLED: "iptal edildi",
  FAILED: "başarısız",
};

function str(o: Record<string, unknown>, key: string): string | null {
  const value = o[key];
  return typeof value === "string" && value ? value : null;
}

function num(o: Record<string, unknown>, key: string): number | null {
  const value = o[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseAlarm(raw: unknown): WakeAlarm {
  const o = (raw ?? {}) as Record<string, unknown>;
  const media = (o.resolved_media_identity ?? o.media_source ?? {}) as Record<string, unknown>;
  return {
    id: str(o, "id") ?? str(o, "alarm_id") ?? "",
    state: str(o, "state") ?? "",
    local_time: str(o, "local_time"),
    scheduled_for: str(o, "scheduled_for"),
    timezone: str(o, "timezone"),
    is_test: o.is_test === true,
    recurrence:
      o.recurrence && typeof o.recurrence === "object"
        ? (o.recurrence as { weekdays?: number[] })
        : null,
    media_kind: str(media, "kind"),
    media_title: str(media, "title") ?? str(media, "name"),
    device_id: str(o, "device_id"),
    snooze_count: num(o, "snooze_count"),
    terminal_reason: str(o, "terminal_reason"),
  };
}

export const fetchAlarms = () =>
  load<WakeAlarm[]>("/v1/alarms", (raw) => arrayAt<unknown>(raw, "alarms").map(parseAlarm));

/**
 * The ambient policy (spec §3.9). Since B48 the owner may flip its four switches from
 * the panel ({@link updateAmbientPolicy}), which writes through the same API the voice
 * tool uses; the Cloud Core decides. **Nothing in the renderer decides physical policy.**
 */
export type AmbientPolicy = {
  auto_off_enabled: boolean | null;
  off_when_away: boolean | null;
  off_when_asleep: boolean | null;
  wake_on_return: boolean | null;
  away_after_s: number | null;
  asleep_after_s: number | null;
  input_holdoff_s: number | null;
  quiet_hours: string | null;
  /**
   * B48 (req 300, 331, 671): the owner's device-camera choice. Absent from a Cloud Core that
   * predates it; null when the value is not one this renderer knows.
   */
  camera_mode?: CameraMode | null;
};

/** B48: the device camera's modes - the camera opens only after the owner picks one. */
export type CameraMode = "off" | "periodic" | "continuous";

export const CAMERA_MODES: readonly CameraMode[] = ["off", "periodic", "continuous"];

function cameraMode(value: unknown): CameraMode | null {
  return typeof value === "string" && (CAMERA_MODES as readonly string[]).includes(value)
    ? (value as CameraMode)
    : null;
}

function flag(o: Record<string, unknown>, key: string): boolean | null {
  const value = o[key];
  return typeof value === "boolean" ? value : null;
}

export const fetchAmbientPolicy = () =>
  load<AmbientPolicy>("/v1/ambient/policy", (raw) => {
    const o = (raw ?? {}) as Record<string, unknown>;
    const p = (o.policy && typeof o.policy === "object" ? o.policy : o) as Record<string, unknown>;
    return {
      auto_off_enabled: flag(p, "auto_off_enabled"),
      off_when_away: flag(p, "off_when_away"),
      off_when_asleep: flag(p, "off_when_asleep"),
      wake_on_return: flag(p, "wake_on_return"),
      away_after_s: num(p, "away_after_s"),
      asleep_after_s: num(p, "asleep_after_s"),
      input_holdoff_s: num(p, "input_holdoff_s"),
      quiet_hours: str(p, "quiet_hours"),
      camera_mode: cameraMode(p.camera_mode),
    };
  });

/** The four ambient switches an owner may flip from the panel (B48 req 331, 332). */
export type AmbientToggle = "auto_off_enabled" | "off_when_away" | "off_when_asleep" | "wake_on_return";

export const AMBIENT_TOGGLES: readonly AmbientToggle[] = [
  "auto_off_enabled",
  "off_when_away",
  "off_when_asleep",
  "wake_on_return",
];

/**
 * PUT one switch to `/v1/ambient/policy` — the same write the voice tool makes. Resolves with
 * the Cloud Core's own sentence, or the failure in words; never throws.
 */
export async function updateAmbientPolicy(
  field: AmbientToggle,
  value: boolean,
): Promise<{ ok: boolean; speech: string }> {
  try {
    const response = await apiFetch("/v1/ambient/policy", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: value }),
    });
    if (!response.ok) return { ok: false, speech: `Politika güncellenemedi (HTTP ${response.status}).` };
    const body = (await response.json()) as { speech?: unknown };
    return { ok: true, speech: typeof body.speech === "string" ? body.speech : "Politika güncellendi." };
  } catch (err) {
    return { ok: false, speech: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * B48: choose the device camera's mode through the same owner-gated PUT the switches use.
 * The Cloud Core relays it to every device with a camera path; the camera itself never
 * opens from this page.
 */
export async function updateAmbientCameraMode(mode: CameraMode): Promise<{ ok: boolean; speech: string }> {
  if (!(CAMERA_MODES as readonly string[]).includes(mode)) {
    return { ok: false, speech: "Bilinmeyen kamera kipi." };
  }
  try {
    const response = await apiFetch("/v1/ambient/policy", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_mode: mode }),
    });
    if (!response.ok) return { ok: false, speech: `Kamera kipi değiştirilemedi (HTTP ${response.status}).` };
    const body = (await response.json()) as { speech?: unknown };
    return { ok: true, speech: typeof body.speech === "string" ? body.speech : "Kamera kipi güncellendi." };
  } catch (err) {
    return { ok: false, speech: err instanceof Error ? err.message : String(err) };
  }
}

/** B48: what a device says its camera is doing (DEVICE_PROTOCOL.md §6p). */
export type DeviceCamera = {
  mode: CameraMode | null;
  state: string | null;
  indicator: string | null;
  error: string | null;
};

/**
 * A device and the status its heartbeat carried (spec §5.3).
 *
 * `status` is optional in the device protocol — a device with no companion
 * sends none — so "no status" is a real answer and is rendered as one rather
 * than as a screen that is presumed on.
 */
export type DeviceStatus = {
  device_id: string;
  label: string | null;
  online: boolean | null;
  last_seen_at: string | null;
  input_idle_s: number | null;
  display_state: string | null;
  display_observed_at: string | null;
  alarm_ringing: boolean | null;
  armed_alarms: number | null;
  /** B48: the device camera's own report, or null when the device has no camera path. */
  camera?: DeviceCamera | null;
  /** True when the heartbeat carried a `status` block at all. */
  statusKnown: boolean;
};

export function parseDevice(raw: unknown): DeviceStatus {
  const o = (raw ?? {}) as Record<string, unknown>;
  // The heartbeat status rides the inventory row under `heartbeat_status` (the row's own
  // `status` is the ENROLLMENT status string); an object under `status` is accepted for a
  // reader of the older shape.
  const candidate = o.heartbeat_status ?? o.status;
  const status = (candidate && typeof candidate === "object" ? candidate : null) as Record<
    string,
    unknown
  > | null;
  const display = (status?.display && typeof status.display === "object"
    ? status.display
    : {}) as Record<string, unknown>;
  // The companion reports armed alarms as a COUNT (`armed_alarm_count`, or a bare number
  // under `armed_alarms`); the spec's original shape was an id list. Either is a count here.
  const armed = status?.armed_alarms;
  const armedCount =
    status && typeof status.armed_alarm_count === "number"
      ? (status.armed_alarm_count as number)
      : Array.isArray(armed)
        ? armed.length
        : typeof armed === "number"
          ? armed
          : null;
  const online = flag(o, "online") ?? flag(o, "connected");
  const presence = str(o, "presence");
  const rawCamera = status?.camera;
  const cameraBlock =
    rawCamera && typeof rawCamera === "object" ? (rawCamera as Record<string, unknown>) : null;
  return {
    device_id: str(o, "id") ?? str(o, "device_id") ?? "",
    label: str(o, "label") ?? str(o, "name"),
    online: online ?? (presence ? presence === "online" : null),
    last_seen_at: str(o, "last_seen_at"),
    input_idle_s: status ? num(status, "input_idle_s") : null,
    display_state: str(display, "state") ?? (status ? str(status, "display_state") : null),
    display_observed_at:
      str(display, "observed_at") ?? (status ? str(status, "display_observed_at") : null),
    alarm_ringing: status ? flag(status, "alarm_ringing") : null,
    armed_alarms: armedCount,
    camera: cameraBlock
      ? {
          mode: cameraMode(cameraBlock.mode),
          state: str(cameraBlock, "state"),
          indicator: str(cameraBlock, "indicator"),
          error: str(cameraBlock, "error"),
        }
      : null,
    statusKnown: status !== null,
  };
}

export const fetchDeviceStatus = () =>
  load<DeviceStatus[]>("/v1/devices", (raw) => arrayAt<unknown>(raw, "devices").map(parseDevice));

// ------------------------------------------------- voice routing qualification

/**
 * ADR-0080. The Owner Utterance Suite's latest result, as the Cloud Core recorded
 * it. Five states, each a fact about the record: NOT_YET_RUN, HEALTHY,
 * REGRESSION_FOUND, SELF_HEALING, OWNER_AUDIO_TEST_REQUIRED.
 */
export type VoiceQualificationRun = {
  recorded_at: string | null;
  age_s: number | null;
  summary: string | null;
  corpus_version: number | null;
  total_cases: number | null;
  passed: number | null;
  clarification: number | null;
  failed_routing: number | null;
  forbidden_side_effects: number | null;
  confusion: Array<{
    case_id: string;
    utterance: string;
    expected: string | null;
    resolved: string | null;
  }>;
};

export type VoiceQualification = {
  state: string;
  routing_state: string;
  owner_audio_qualified: boolean;
  open_opportunities: number;
  latest_synthetic_run: VoiceQualificationRun | null;
  latest_owner_audio_run: VoiceQualificationRun | null;
};

export const VOICE_QUALIFICATION_LABEL: Record<string, string> = {
  NOT_YET_RUN: "henüz çalıştırılmadı",
  HEALTHY: "sağlıklı",
  REGRESSION_FOUND: "regresyon bulundu",
  SELF_HEALING: "kendini onarıyor",
  OWNER_AUDIO_TEST_REQUIRED: "sahibin ses testi bekleniyor",
};

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseQualificationRun(raw: unknown): VoiceQualificationRun | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const confusion = Array.isArray(r.confusion) ? (r.confusion as Record<string, unknown>[]) : [];
  return {
    recorded_at: typeof r.recorded_at === "string" ? r.recorded_at : null,
    age_s: numberOrNull(r.age_s),
    summary: typeof r.summary === "string" ? r.summary : null,
    corpus_version: numberOrNull(r.corpus_version),
    total_cases: numberOrNull(r.total_cases),
    passed: numberOrNull(r.passed),
    clarification: numberOrNull(r.clarification),
    failed_routing: numberOrNull(r.failed_routing),
    forbidden_side_effects: numberOrNull(r.forbidden_side_effects),
    confusion: confusion.map((c) => ({
      case_id: String(c.case_id ?? ""),
      utterance: String(c.utterance ?? ""),
      expected: typeof c.expected === "string" ? c.expected : null,
      resolved: typeof c.resolved === "string" ? c.resolved : null,
    })),
  };
}

export const fetchVoiceQualification = () =>
  load<VoiceQualification>("/v1/voice/qualification", (raw) => {
    const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    return {
      state: typeof r.state === "string" ? r.state : "NOT_YET_RUN",
      routing_state: typeof r.routing_state === "string" ? r.routing_state : "NOT_YET_RUN",
      owner_audio_qualified: r.owner_audio_qualified === true,
      open_opportunities: numberOrNull(r.open_opportunities) ?? 0,
      latest_synthetic_run: parseQualificationRun(r.latest_synthetic_run),
      latest_owner_audio_run: parseQualificationRun(r.latest_owner_audio_run),
    };
  });

// --------------------------------------------------- the Evolution Supervisor

/** ADR-0081. What the supervisor last did and what the rows say it is working on. */
export type EvolutionSupervisorStatus = {
  paused: boolean;
  enabled: boolean;
  last_scan_at: string | null;
  last_scan: { status: string; signals: number; opened: string[]; already_tracked: number } | null;
  open_by_priority: Record<string, number>;
  building: Array<{ opportunity_id: string; title: string; status: string; priority: string | null }>;
  pending_candidates: Array<{ opportunity_id: string; title: string; status: string }>;
  release_failures: Array<{ opportunity_id: string; title: string; status: string }>;
  last_fix: {
    incident_id: string | null;
    component: string | null;
    fixed_release_id: string | null;
    last_seen_at: string | null;
    error_class: string;
  } | null;
  running: { version: string; version_source: string; contracts: Record<string, number> } | null;
};

function rows(raw: unknown): Array<Record<string, unknown>> {
  return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : [];
}

export const fetchEvolutionSupervisor = () =>
  load<EvolutionSupervisorStatus>("/v1/evolution/supervisor", (raw) => {
    const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    const scan = (r.last_scan && typeof r.last_scan === "object" ? r.last_scan : null) as Record<
      string,
      unknown
    > | null;
    const fix = (r.last_fix && typeof r.last_fix === "object" ? r.last_fix : null) as Record<
      string,
      unknown
    > | null;
    const running = (r.running && typeof r.running === "object" ? r.running : null) as Record<
      string,
      unknown
    > | null;
    const counts = (r.open_by_priority && typeof r.open_by_priority === "object"
      ? r.open_by_priority
      : {}) as Record<string, unknown>;
    return {
      paused: r.paused === true,
      enabled: r.enabled !== false,
      last_scan_at: typeof r.last_scan_at === "string" ? r.last_scan_at : null,
      last_scan: scan
        ? {
            status: String(scan.status ?? ""),
            signals: numberOrNull(scan.signals) ?? 0,
            opened: Array.isArray(scan.opened) ? scan.opened.map(String) : [],
            already_tracked: numberOrNull(scan.already_tracked) ?? 0,
          }
        : null,
      open_by_priority: Object.fromEntries(
        Object.entries(counts).map(([k, v]) => [k, numberOrNull(v) ?? 0]),
      ),
      building: rows(r.building).map((b) => ({
        opportunity_id: String(b.opportunity_id ?? ""),
        title: String(b.title ?? ""),
        status: String(b.status ?? ""),
        priority: typeof b.priority === "string" ? b.priority : null,
      })),
      pending_candidates: rows(r.pending_candidates).map((b) => ({
        opportunity_id: String(b.opportunity_id ?? ""),
        title: String(b.title ?? ""),
        status: String(b.status ?? ""),
      })),
      release_failures: rows(r.release_failures).map((b) => ({
        opportunity_id: String(b.opportunity_id ?? ""),
        title: String(b.title ?? ""),
        status: String(b.status ?? ""),
      })),
      last_fix: fix
        ? {
            incident_id: typeof fix.incident_id === "string" ? fix.incident_id : null,
            component: typeof fix.component === "string" ? fix.component : null,
            fixed_release_id:
              typeof fix.fixed_release_id === "string" ? fix.fixed_release_id : null,
            last_seen_at: typeof fix.last_seen_at === "string" ? fix.last_seen_at : null,
            error_class: String(fix.error_class ?? ""),
          }
        : null,
      running: running
        ? {
            version: String(running.version ?? "unknown"),
            version_source: String(running.version_source ?? "unknown"),
            contracts: Object.fromEntries(
              Object.entries(
                (running.contracts && typeof running.contracts === "object"
                  ? running.contracts
                  : {}) as Record<string, unknown>,
              ).map(([k, v]) => [k, numberOrNull(v) ?? 0]),
            ),
          }
        : null,
    };
  });
