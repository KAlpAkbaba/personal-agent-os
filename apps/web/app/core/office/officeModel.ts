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
import { type Mood, moodOf } from "./officeMood";

export const REPORT_LINE_CAP = 40;
export const SHA_SHORT = 12;

const SEAT_NAME_TR = new Map<SeatId, string>([
  ["lead", "Proje Yöneticisi"],
  ["researcher", "Araştırmacı"],
  ["integrator", "Entegratör"],
  ["inspector", "Denetleyici"],
  ["owner", "CTO"],
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

/**
 * The queue's task states (the state enum of team/queue.schema.json, read by the test from
 * that file) as the owner says them. The page has no type for a task state: it arrives as a
 * plain string, so a state added to the queue without a phrase here fails model.test.ts.
 */
export const TASK_STATE_TR: Record<string, string> = {
  proposed: "önerildi, henüz başlamadı",
  awaiting_owner: "sahibin onayını bekliyor",
  approved: "onaylandı, sırada",
  assigned: "bir çalışana verildi, başlıyor",
  in_progress: "yazılıyor",
  inspecting: "denetleniyor",
  returned: "denetleyici geri gönderdi; yeniden yazılacak",
  stopped: "durdu: Proje Yöneticisi bakacak",
  merged: "birleştirildi, yayın bekliyor",
  awaiting_release: "yayın için sahibin onayını bekliyor",
  released: "yayında",
  awaiting_real_evidence: "yayında; gerçek kullanımda kanıt bekliyor",
  done: "bitti",
  rejected: "vazgeçildi",
};

/** The task's state in Turkish; a state this page does not know is shown as it is. */
export function taskStateText(state: string): string {
  return Object.hasOwn(TASK_STATE_TR, state) ? TASK_STATE_TR[state] : state;
}

/** A reason that starts with the lead's marker is technical text for the agents. */
const LEAD_NOTE = /^\s*LEAD/;

export type Pose = "typing" | "seated" | "standing";

export type DrawnSeat = {
  seat: SeatId;
  name: string;
  state: SeatState;
  pose: Pose;
  /** A seat id the page does not know: a desk with its id, nobody at it. */
  plain: boolean;
  warning: boolean;
  /** The task title above the head; only a working seat has one. */
  label: string | null;
  /** The owner's approval count. */
  badge: string | null;
  /** `×3` when the seat has more than one live run. */
  runCount: string | null;
  /** How the character feels (officeMood.ts). */
  mood: Mood;
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
  /** The first view: title, Turkish state, since; the card text for the agents is under the fold. */
  task: {
    title: string;
    stateText: string;
    since: string | null;
    goal: string;
    acceptance: string;
    evidence: string | null;
  } | null;
  /** A reason in the owner's language; a lead note goes to `agentNote`, under the fold. */
  reason: string | null;
  agentNote: string | null;
  /** The last report's outcome line. */
  outcome: string | null;
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

function drawSeat(agent: OfficeAgent, ownerCount: number, task?: OfficeTask, now: Date = new Date()): DrawnSeat {
  const known = seatName(agent.seat);
  const name = known ?? agent.seat;
  const owner = agent.seat === "owner";
  // Only a worker's seat shows a returned task: the inspector (or another non-worker seat) whose
  // last task came back is the one that SENT it back - the worker fixes it (the owner, 2026-10-03:
  // "denetleyici neden hala ünlemde?"). Its panel still names the task it sent back.
  const sentBack = agent.state === "returned" && !WORKER_SEAT.test(agent.seat);
  const state: SeatState = owner || sentBack ? "waiting" : agent.state;
  const runs = state === "working" ? severalRuns(agent).length : 0;
  return {
    seat: agent.seat,
    name,
    state,
    pose: POSE[state],
    plain: known === null,
    warning: state === "returned",
    label: state === "working" ? (agent.task_title ?? agent.task_id) : null,
    badge: owner ? String(ownerCount) : null,
    runCount: runs > 0 ? `×${runs}` : null,
    mood: moodOf({ ...agent, state }, task, now),
    ariaLabel: `${name}, ${STATE_TR[state]}${runs > 0 ? `, ${runs} koşu` : ""}`,
  };
}

export function buildOffice(view: OfficeView, now: Date = new Date()) {
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
    seats: view.agents.map((agent) =>
      drawSeat(agent, view.approvals.length, agent.task_id ? view.tasks[agent.task_id] : undefined, now),
    ),
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
  const reason = task?.reason || null;
  const leadNote = reason !== null && LEAD_NOTE.test(reason);
  // not in the contract's type yet: shown when the API sends it, never asked for
  const evidence = (task as { evidence_expected?: unknown } | undefined)?.evidence_expected;
  return {
    seat: agent.seat,
    role: drawn.name,
    stateText: STATE_TR[drawn.state],
    runs: (drawn.runCount === null ? [] : severalRuns(agent)).map((run) => ({
      title: run.task_title ?? run.task_id ?? "-",
      since: clock(run.since),
    })),
    task: task
      ? {
          title: task.title,
          stateText: taskStateText(task.state),
          since: agent.since ? clock(agent.since) : null,
          goal: task.goal,
          acceptance: task.acceptance,
          evidence: typeof evidence === "string" && evidence ? evidence : null,
        }
      : null,
    reason: leadNote ? null : reason,
    agentNote: leadNote ? reason : null,
    outcome: task?.report?.outcome || null,
    reportLines: (task?.report?.summary ?? []).slice(0, REPORT_LINE_CAP),
    branch: task?.branch ?? null,
    sha: task?.sha ? task.sha.slice(0, SHA_SHORT) : null,
    shaFull: task?.sha ?? null,
  };
}
