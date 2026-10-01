/**
 * What the Ofis page draws, derived from the contract and nothing else. Pure: no DOM, no
 * clock beyond formatting the instants it is given, so every claim the page makes is tested
 * on plain objects.
 */

import { GATE_TR } from "../approvals/approvalsApi";
import type { OfficeAgent, OfficeTask, OfficeView, SeatId, SeatState } from "./officeApi";

export const REPORT_LINE_CAP = 40;
export const SHA_SHORT = 12;

export const SEAT_NAME_TR: Record<SeatId, string> = {
  lead: "Hakim",
  researcher: "Araştırmacı",
  integrator: "Entegratör",
  "worker-1": "Çalışan 1",
  "worker-2": "Çalışan 2",
  "worker-3": "Çalışan 3",
  inspector: "Denetleyici",
  owner: "Sahip",
};

export const STATE_TR: Record<SeatState, string> = {
  working: "çalışıyor",
  waiting: "bekliyor",
  returned: "döndü",
};

export type Pose = "typing" | "seated" | "standing";

export type DrawnSeat = {
  seat: SeatId;
  name: string;
  state: SeatState;
  pose: Pose;
  warning: boolean;
  /** The task title above the head; only a working seat has one. */
  label: string | null;
  /** The owner's approval count. */
  badge: string | null;
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

function drawSeat(agent: OfficeAgent, ownerCount: number): DrawnSeat {
  const name = SEAT_NAME_TR[agent.seat] ?? agent.role;
  const owner = agent.seat === "owner";
  const state: SeatState = owner ? "waiting" : agent.state;
  return {
    seat: agent.seat,
    name,
    state,
    pose: POSE[state],
    warning: state === "returned",
    label: state === "working" ? (agent.task_title ?? agent.task_id) : null,
    badge: owner ? String(ownerCount) : null,
    ariaLabel: `${name}, ${STATE_TR[state]}`,
  };
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
    stateText: STATE_TR[drawn.state],
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
