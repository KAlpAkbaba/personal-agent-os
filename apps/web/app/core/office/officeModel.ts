/**
 * What the Ofis page draws, derived from the contract and nothing else. Pure: no DOM, no
 * clock beyond formatting the instants it is given, so every claim the page makes is tested
 * on plain objects.
 */

import { GATE_TR } from "../approvals/approvalsApi";
import {
  MODEL_CHAIN,
  MODEL_ROLES,
  type CycleLimits,
  type LimitWindow,
  type ModelId,
  type ModelRole,
  type ModelSetting,
  type OfficeAgent,
  type OfficeRun,
  type OfficeTask,
  type OfficeView,
  type SeatId,
  type SeatState,
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
/** What a queued seat says where a working one says `çalışıyor`. */
export const QUEUED_TR = "sırada";

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
const MODEL_NAME: Record<ModelId, string> = {
  "claude-fable-5-1": "Fable 5.1",
  "claude-opus-5-5": "Opus 5.5",
  "claude-sonnet-5-5": "Sonnet 5.5",
};

/** The plain name of a model id; an id the page does not know is shown as it is. */
export function modelName(id: string): string {
  return (MODEL_NAME as Record<string, string>)[id] ?? id;
}

/** Whether `a` is weaker than `b`: further down the chain. */
export function isWeaker(a: ModelId, b: ModelId): boolean {
  return MODEL_CHAIN.indexOf(a) > MODEL_CHAIN.indexOf(b);
}

export const WEAKER_REFUSAL = "Denetleyici işçiden zayıf modelde koşamaz";
export const NEXT_RUN_NOTE = "Bir sonraki koşudan itibaren geçerli.";

/**
 * Why the page will not send `model` for `role`, or null. The server is the authority on
 * validity (code inspector_weaker_than_worker); this is the explanation before a PUT.
 */
export function modelRefusal(setting: ModelSetting, role: ModelRole, model: ModelId): string | null {
  if (role === "inspector" && isWeaker(model, setting.roles.worker)) return WEAKER_REFUSAL;
  if (role === "worker" && isWeaker(setting.roles.inspector, model)) return WEAKER_REFUSAL;
  return null;
}

/** The whole setting with `role` on `model`, or the page's refusal. `setting` is not changed. */
export function chooseModel(
  setting: ModelSetting,
  role: ModelRole,
  model: ModelId,
): { setting: ModelSetting } | { refusal: string } {
  const refusal = modelRefusal(setting, role, model);
  if (refusal) return { refusal };
  return { setting: { ...setting, roles: { ...setting.roles, [role]: model } } };
}

/** The role whose model a seat runs on; null for the owner and for a seat the page does not know. */
function modelRole(agent: OfficeAgent): ModelRole | null {
  if (WORKER_SEAT.test(agent.seat)) return "worker";
  const roles: readonly string[] = MODEL_ROLES;
  if (roles.includes(agent.role)) return agent.role as ModelRole;
  return roles.includes(agent.seat) ? (agent.seat as ModelRole) : null;
}

function loweredText(agent: OfficeAgent): string | null {
  return agent.running_model ? `şu an: ${modelName(agent.running_model)} (düşürüldü)` : null;
}

const COUNT_TR = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"];

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
  /** How the character feels (officeMood.ts). */
  mood: Mood;
  /** `şu an: <model> (düşürüldü)` while a live run is on another model than configured. */
  lowered: string | null;
  ariaLabel: string;
};

export type TopBar = {
  cycleId: string;
  startedAt: string;
  runningAgents: string;
  estimated: string;
  limit: string;
  /** `Fable: %NN`, `Fable: bilinmiyor` or `Fable: limitte, <saat>`. */
  fable: string;
  all: string;
  /** The setting's fallback; null when the answer has no setting (an older API). */
  fallback: boolean | null;
  /** The newest downgrade as one line, or null. */
  lowered: string | null;
  /** The status' time when the cycle is not running: its limits are not the present. */
  asOf: string | null;
  /** `Hesap 3`, `Ana hesap`, or null when the cycle does not say. */
  account: string | null;
};

export type PanelModel = {
  role: ModelRole;
  value: ModelId;
  options: { id: ModelId; name: string; disabled: boolean }[];
  /** Why some options are disabled; null when none is. */
  refusal: string | null;
  /** The worker seats' shared role, said once. */
  shared: string | null;
  lowered: string | null;
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
  /** The role's model selector; null for the owner, an unknown seat, or no setting. */
  model: PanelModel | null;
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

/** A null used_pct is not known: it is never drawn as %0 (addendum 7). */
function windowText(window: LimitWindow | undefined): string {
  if (!window) return "bilinmiyor";
  if (window.state === "limited") {
    return window.resets_at ? `limitte, ${clock(window.resets_at)}` : "limitte";
  }
  const used = window.used_pct;
  return typeof used === "number" && Number.isFinite(used) ? `%${Math.round(used)}` : "bilinmiyor";
}

function loweredLine(view: OfficeView, limits: CycleLimits | undefined): string | null {
  const newest = limits?.lowered?.at(-1);
  if (!newest) return null;
  const task = view.tasks[newest.task]?.title ?? newest.task;
  return `model düşürüldü: ${modelName(newest.from)} → ${modelName(newest.to)}, ${task}`;
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
  const queued = state === "waiting" && agent.queued === true;
  const lowered = owner ? null : loweredText(agent);
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
    mood: moodOf({ ...agent, state }, task, now),
    lowered,
    ariaLabel: `${name}, ${stateText(state, queued)}${runs > 0 ? `, ${runs} koşu` : ""}${lowered ? `, ${lowered}` : ""}`,
  };
}

function stateText(state: SeatState, queued: boolean): string {
  return queued ? QUEUED_TR : STATE_TR[state];
}

/** The account folder as the owner says it: ".claude-hesap3" -> "Hesap 3", "varsayilan" -> "Ana hesap". */
export function accountLabel(account: string | null | undefined): string | null {
  if (!account) return null;
  if (account === "varsayilan") return "Ana hesap";
  const numbered = /hesap[-_]?(\d+)$/i.exec(account);
  if (numbered) return `Hesap ${numbered[1]}`;
  return account.replace(/^\.?claude-?/i, "") || account;
}

export function buildOffice(view: OfficeView, now: Date = new Date()) {
  const cycle = view.cycle;
  const topBar: TopBar = {
    cycleId: cycle.cycle_id ?? "döngü yok",
    startedAt: startedAt(cycle.started_at),
    runningAgents: `koşan ajan ${cycle.running_agents}/${cycle.capacity}`,
    estimated: `tahmini $${cycle.estimated_usd.toFixed(2)}`,
    limit: limitText(cycle.usage_limit),
    fable: `Fable: ${windowText(cycle.limits?.fable)}`,
    all: `Tüm modeller: ${windowText(cycle.limits?.all)}`,
    fallback: view.models ? view.models.fallback : null,
    lowered: loweredLine(view, cycle.limits),
    asOf: cycle.running ? null : `${startedAt(cycle.updated_at)} itibarıyla`,
    account: accountLabel(cycle.account),
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
    stateText: stateText(drawn.state, drawn.queued),
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
    model: panelModel(view, agent),
  };
}

function panelModel(view: OfficeView, agent: OfficeAgent): PanelModel | null {
  const role = modelRole(agent);
  const setting = view.models;
  if (role === null || !setting || agent.seat === "owner") return null;
  const options = MODEL_CHAIN.map((id) => ({
    id,
    name: modelName(id),
    disabled: modelRefusal(setting, role, id) !== null,
  }));
  const workers = view.agents.filter((a) => WORKER_SEAT.test(a.seat)).length;
  return {
    role,
    value: setting.roles[role],
    options,
    refusal: options.some((o) => o.disabled) ? WEAKER_REFUSAL : null,
    shared:
      role === "worker" ? `${COUNT_TR[workers] ?? workers} çalışan aynı modeli kullanır` : null,
    lowered: loweredText(agent),
  };
}
