/**
 * What the Ofis page draws, derived from the contract and nothing else. Pure: no DOM, no
 * clock beyond formatting the instants it is given, so every claim the page makes is tested
 * on plain objects.
 */

import { GATE_TR } from "../approvals/approvalsApi";
import type {
  OfficeAgent,
  OfficeRun,
  OfficeTask,
  OfficeView,
  SeatId,
  SeatState,
} from "./officeApi";

export const REPORT_LINE_CAP = 40;
export const SHA_SHORT = 12;

const SEAT_NAME_TR = new Map<SeatId, string>([
  ["lead", "Hakim"],
  ["researcher", "Araştırmacı"],
  ["integrator", "Entegratör"],
  ["inspector", "Denetleyici"],
  ["owner", "Sahip"],
]);
const WORKER_SEAT = /^worker-([1-9]\d*)$/;

/** The seat's Turkish name by its id pattern; `null` for an id this page does not know. */
export function seatName(seat: SeatId): string | null {
  const worker = WORKER_SEAT.exec(seat);
  return worker ? `Çalışan ${worker[1]}` : (SEAT_NAME_TR.get(seat) ?? null);
}

export const STATE_TR: Record<SeatState, string> = {
  working: "çalışıyor",
  waiting: "bekliyor",
  returned: "döndü",
};
/** What a queued seat says where a working one says `çalışıyor`. */
export const QUEUED_TR = "sırada";

export type Pose = "typing" | "seated" | "standing";

export type DrawnSeat = {
  seat: SeatId;
  name: string;
  state: SeatState;
  pose: Pose;
  /** A seat id the page does not know: a desk with its id, nobody at it. */
  plain: boolean;
  warning: boolean;
  /** A waiting seat whose task waits for its next run: seated, no warning, `sırada`. */
  queued: boolean;
  /** The task title above the head: a working seat's, or (muted) a queued seat's. */
  label: string | null;
  /** The owner's approval count. */
  badge: string | null;
  /** `×3` when the seat has more than one live run. */
  runCount: string | null;
  ariaLabel: string;
};

export type TopBar = {
  cycleId: string;
  startedAt: string;
  runningAgents: string;
  estimated: string;
  limit: string;
};

export type Panel = {
  seat: SeatId;
  role: string;
  stateText: string;
  /** Every live run of a seat that has more than one; the card below is the first one's. */
  runs: { title: string; since: string }[];
  task: { title: string; state: string; goal: string; acceptance: string } | null;
  reason: string | null;
  reportLines: string[];
  branch: string | null;
  sha: string | null;
  shaFull: string | null;
};

const POSE: Record<SeatState, Pose> = { working: "typing", waiting: "seated", returned: "standing" };

function clock(iso: string | null): string {
  if (!iso) return "-";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "-";
  return at.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });
}

function startedAt(iso: string | null): string {
  if (!iso) return "-";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "-";
  return at.toLocaleString("tr-TR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function limitText(limit: OfficeView["cycle"]["usage_limit"]): string {
  if (limit.state === "stopped") return "Durdu";
  if (limit.state === "waiting") return `Bekliyor ${clock(limit.resets_at)}`;
  return "Açık";
}

/** The seat's runs when there is more than one to tell apart, else none. */
function severalRuns(agent: OfficeAgent): OfficeRun[] {
  const runs = agent.runs ?? [];
  return runs.length > 1 ? runs : [];
}

function drawSeat(agent: OfficeAgent, ownerCount: number): DrawnSeat {
  const known = seatName(agent.seat);
  const name = known ?? agent.seat;
  const owner = agent.seat === "owner";
  const state: SeatState = owner ? "waiting" : agent.state;
  const runs = state === "working" ? severalRuns(agent).length : 0;
  const queued = state === "waiting" && agent.queued === true;
  return {
    seat: agent.seat,
    name,
    state,
    pose: POSE[state],
    plain: known === null,
    // for what came back to a person only: a queued task needs nobody
    warning: state === "returned",
    queued,
    label: state === "working" || queued ? (agent.task_title ?? agent.task_id) : null,
    badge: owner ? String(ownerCount) : null,
    runCount: runs > 0 ? `×${runs}` : null,
    ariaLabel: `${name}, ${stateText(state, queued)}${runs > 0 ? `, ${runs} koşu` : ""}`,
  };
}

function stateText(state: SeatState, queued: boolean): string {
  return queued ? QUEUED_TR : STATE_TR[state];
}

export function buildOffice(view: OfficeView) {
  const cycle = view.cycle;
  const topBar: TopBar = {
    cycleId: cycle.cycle_id ?? "döngü yok",
    startedAt: startedAt(cycle.started_at),
    runningAgents: `koşan ajan ${cycle.running_agents}/${cycle.capacity}`,
    estimated: `tahmini $${cycle.estimated_usd.toFixed(2)}`,
    limit: limitText(cycle.usage_limit),
  };
  return {
    topBar,
    seats: view.agents.map((agent) => drawSeat(agent, view.approvals.length)),
    approvals: view.approvals.map((a) => ({
      taskId: a.task_id,
      title: a.title,
      gate: GATE_TR[a.gate] ?? a.gate,
    })),
  };
}

export function selectSeat(current: string | null, clicked: string): string | null {
  return current === clicked ? null : clicked;
}

export function buildPanel(view: OfficeView, seat: string): Panel | null {
  const agent = view.agents.find((a) => a.seat === seat);
  if (!agent) return null;
  const drawn = drawSeat(agent, view.approvals.length);
  const owner = agent.seat === "owner";
  const task: OfficeTask | undefined =
    !owner && agent.task_id ? view.tasks[agent.task_id] : undefined;
  return {
    seat: agent.seat,
    role: drawn.name,
    stateText: stateText(drawn.state, drawn.queued),
    runs: (drawn.runCount === null ? [] : severalRuns(agent)).map((run) => ({
      title: run.task_title ?? run.task_id ?? "-",
      since: clock(run.since),
    })),
    task: task
      ? { title: task.title, state: task.state, goal: task.goal, acceptance: task.acceptance }
      : null,
    reason: task?.reason ?? null,
    reportLines: (task?.report?.summary ?? []).slice(0, REPORT_LINE_CAP),
    branch: task?.branch ?? null,
    sha: task?.sha ? task.sha.slice(0, SHA_SHORT) : null,
    shaFull: task?.sha ?? null,
  };
}
