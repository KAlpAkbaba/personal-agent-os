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
  started_at: string | null;
  running: boolean;
  running_agents: number;
  capacity: number;
  estimated_usd: number;
  usage_limit: { state: "ok" | "waiting" | "stopped"; resets_at: string | null };
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
  /** Additive (office-progress): an older server does not send it. */
  progress?: OfficeProgress | null;
};

export async function fetchOffice(): Promise<OfficeView> {
  const response = await apiFetch(OFFICE_PATH);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as OfficeView;
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
