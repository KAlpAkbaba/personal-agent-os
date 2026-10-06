/**
 * The Ofis page's client for `GET /v1/team/office` (docs/TEAM_PROTOCOL.md).
 *
 * The types are the contract the API task and this page share; neither changes it alone.
 * `createOfficePoller` is the 5-second refresh as a pure controller (timers and visibility
 * are ports), so the "pauses while the tab is hidden" claim is tested with fake timers and
 * no DOM.
 */

import { apiFetch } from "../../lib/session";
import type { PendingApproval } from "../approvals/approvalsApi";

export const OFFICE_PATH = "/v1/team/office";
/** The model policy's setting (ADR-0214 addenda 7 and 14): GET and PUT, owner session. */
export const MODELS_PATH = "/v1/team/queue/models";
export const POLL_MS = 5000;

/**
 * `lead`, `researcher`, `integrator`, `worker-<n>` (four, more when the cycle runs more),
 * `inspector`, `owner` - in the order the API sends them. The page draws what it is sent.
 */
export type SeatId = string;

export type SeatState = "working" | "waiting" | "returned";

export type OfficeRun = {
  task_id: string | null;
  task_title: string | null;
  since: string | null;
  /** The model the run was started on, when the cycle named one. */
  model?: string;
};

/** The three ids, strongest first: that order is the fallback chain and the meaning of "weaker". */
export const MODEL_CHAIN = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"] as const;
export type ModelId = (typeof MODEL_CHAIN)[number];
export const MODEL_ROLES = ["lead", "researcher", "integrator", "worker", "inspector"] as const;
export type ModelRole = (typeof MODEL_ROLES)[number];

export type ModelSetting = {
  roles: Record<ModelRole, ModelId>;
  fallback: boolean;
  updated_at: string;
};

/** One limit window. `used_pct` is null unless a real source gave the number. */
export type LimitWindow = {
  state: "ok" | "limited";
  resets_at: string | null;
  used_pct: number | null;
};

export type LoweredRun = { task: string; role: string; from: string; to: string; at: string };

export type CycleLimits = {
  fable: LimitWindow;
  all: LimitWindow;
  fallback: boolean;
  /** This cycle's downgrades, newest last, at most 20. */
  lowered: LoweredRun[];
};

export type OfficeAgent = {
  seat: SeatId;
  role: string;
  state: SeatState;
  task_id: string | null;
  task_title: string | null;
  since: string | null;
  /** Every live run of the seat in start order; the fields above are the first one's. */
  runs?: OfficeRun[];
  /** The configured model of the seat's role; null for the owner. */
  model?: string | null;
  /** Only while a live run of the seat is on another model than configured. */
  running_model?: string;
  /** How far the first run has got, measured by the cycle from its worktree; an older cycle sends none. */
  progress?: RunProgress;
};

export type RunProgress = {
  area_total: number;
  area_touched: number;
  tests_changed: boolean;
  adr_draft: boolean;
  commits: number;
  last_change_at: string | null;
};

export type OfficeTask = {
  title: string;
  state: string;
  goal: string;
  acceptance: string;
  branch: string;
  sha: string | null;
  reason: string | null;
  report: { role: string; at: string; outcome: string; summary: string[] } | null;
};

export type OfficeCycle = {
  cycle_id: string | null;
  machine: string | null;
  /** The Claude account the team runs under (".claude-hesap3", "varsayilan"); absent from an older API or cycle. */
  account?: string | null;
  started_at: string | null;
  running: boolean;
  running_agents: number;
  capacity: number;
  estimated_usd: number;
  usage_limit: { state: "ok" | "waiting" | "stopped"; resets_at: string | null };
  /** Absent from an API older than the model policy. */
  limits?: CycleLimits;
  updated_at: string | null;
};

export type JarvisState = "have" | "partial" | "missing" | "never" | "unknown";
export type StepState = "done" | "partial" | "open";
export type OrderStep = { n: number; title: string; state: StepState };

/** The İlerleme strip (office-progress): read by the server from the roadmap and the v1.0
 * matrix of the tree it serves. A section it could not read is null. */
export type OfficeProgress = {
  jarvis: {
    have: number;
    partial: number;
    missing: number;
    never: number;
    unknown: string[];
    counted: number;
    percent: number | null;
    rows: { name: string; state: JarvisState }[];
  } | null;
  order: { steps: OrderStep[]; percent: number | null; next: OrderStep | null } | null;
  v1: {
    total: number;
    done: number;
    by_status: Record<string, number>;
    by_proof: Record<string, number>;
    percent_done: number | null;
    percent_proven_real: number | null;
  } | null;
  rule: string;
  as_of: string | null;
};

export type OfficeView = {
  cycle: OfficeCycle;
  agents: OfficeAgent[];
  tasks: Record<string, OfficeTask>;
  approvals: PendingApproval[];
  /** The setting in force; absent from an API older than the model policy. */
  models?: ModelSetting;
  /** Additive (office-progress): an older server does not send it. */
  progress?: OfficeProgress | null;
};

export async function fetchOffice(): Promise<OfficeView> {
  const response = await apiFetch(OFFICE_PATH);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as OfficeView;
}

export type PutModelsAnswer = { ok: true; setting: ModelSetting } | { ok: false; message: string };

/** PUT the whole setting. A refusal is answered with the server's own sentence. */
export async function putModels(setting: ModelSetting): Promise<PutModelsAnswer> {
  let response: Response;
  try {
    response = await apiFetch(MODELS_PATH, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(setting),
    });
  } catch (failure) {
    return { ok: false, message: failure instanceof Error ? failure.message : "gönderilemedi" };
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (response.ok) return { ok: true, setting: body as ModelSetting };
  const detail = (body as { detail?: { message?: unknown } } | null)?.detail;
  const message = typeof detail?.message === "string" ? detail.message : `HTTP ${response.status}`;
  return { ok: false, message };
}

export type ApplySettingPorts = {
  put: (setting: ModelSetting) => Promise<PutModelsAnswer>;
  /** The setting the page draws. */
  show: (setting: ModelSetting) => void;
  /** The sentence under the selector; null clears it. */
  say: (message: string | null) => void;
};

/** Optimistic: show `next` at once; on a refusal put `previous` back with the server's sentence. */
export async function applySetting(
  previous: ModelSetting,
  next: ModelSetting,
  ports: ApplySettingPorts,
): Promise<void> {
  ports.show(next);
  ports.say(null);
  const answer = await ports.put(next);
  if (answer.ok) {
    ports.show(answer.setting);
  } else {
    ports.show(previous);
    ports.say(answer.message);
  }
}

export type OfficePollerPorts = {
  fetch: () => Promise<OfficeView>;
  onData: (view: OfficeView) => void;
  onError: (failure: unknown) => void;
  isHidden: () => boolean;
  setInterval: (fn: () => void, ms: number) => unknown;
  clearInterval: (id: unknown) => void;
  intervalMs?: number;
};

export function createOfficePoller(ports: OfficePollerPorts) {
  const every = ports.intervalMs ?? POLL_MS;
  let timer: unknown = null;
  let running = false;

  const tick = async () => {
    try {
      const view = await ports.fetch();
      if (running) ports.onData(view);
    } catch (failure) {
      if (running) ports.onError(failure);
    }
  };

  const arm = () => {
    if (timer === null) timer = ports.setInterval(() => void tick(), every);
  };
  const disarm = () => {
    if (timer !== null) ports.clearInterval(timer);
    timer = null;
  };

  return {
    start() {
      running = true;
      if (ports.isHidden()) return;
      void tick();
      arm();
    },
    stop() {
      running = false;
      disarm();
    },
    /** Call from `visibilitychange`: hidden stops the timer, shown fetches at once and re-arms. */
    visibilityChanged() {
      if (!running) return;
      if (ports.isHidden()) {
        disarm();
      } else if (timer === null) {
        void tick();
        arm();
      }
    },
  };
}
