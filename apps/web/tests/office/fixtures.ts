import type { OfficeAgent, OfficeView } from "../../app/core/office/officeApi";

export const SEAT_ORDER = [
  "lead",
  "researcher",
  "integrator",
  "worker-1",
  "worker-2",
  "worker-3",
  "worker-4",
  "inspector",
  "owner",
] as const;

const NO_RUN = { state: "waiting" as const, task_id: null, task_title: null, since: null };

/** The contract's example: two workers running, the inspector's last task returned. */
export function twoWorkers(): OfficeView {
  const base = SEAT_ORDER.map((seat) => ({ seat, role: seat, ...NO_RUN }));
  const agents = base.map((agent) => {
    if (agent.seat === "worker-1")
      return {
        ...agent,
        state: "working" as const,
        task_id: "t-one",
        task_title: "Birinci iş",
        since: "2026-10-01T10:00:00Z",
      };
    if (agent.seat === "worker-2")
      return {
        ...agent,
        state: "working" as const,
        task_id: "t-two",
        task_title: "İkinci iş",
        since: "2026-10-01T10:05:00Z",
      };
    if (agent.seat === "inspector")
      return {
        ...agent,
        state: "returned" as const,
        task_id: "t-old",
        task_title: "Eski iş",
        since: null,
      };
    return agent;
  });
  const lines = Array.from({ length: 55 }, (_, i) => `satır ${i + 1}`);
  return {
    cycle: {
      cycle_id: "cycle-2026-10-01",
      machine: "ev-pc",
      started_at: "2026-10-01T09:30:00Z",
      running: true,
      running_agents: 2,
      capacity: 6,
      estimated_usd: 3.5,
      usage_limit: { state: "ok", resets_at: null },
      updated_at: "2026-10-01T10:06:00Z",
    },
    agents,
    tasks: {
      "t-one": {
        title: "Birinci iş",
        state: "implementing",
        goal: "birinci hedef",
        acceptance: "birinci kabul",
        branch: "team/cycle/worker-one",
        sha: "0123456789abcdef0123456789abcdef01234567",
        reason: null,
        report: { role: "worker", at: "2026-10-01T10:30:00Z", outcome: "ok", summary: lines },
      },
      "t-two": {
        title: "İkinci iş",
        state: "implementing",
        goal: "ikinci hedef",
        acceptance: "ikinci kabul",
        branch: "team/cycle/worker-two",
        sha: null,
        reason: null,
        report: null,
      },
      "t-old": {
        title: "Eski iş",
        state: "returned",
        goal: "eski hedef",
        acceptance: "eski kabul",
        branch: "team/cycle/old",
        sha: null,
        reason: "testler kırmızı",
        report: null,
      },
    },
    approvals: [
      {
        task_id: "a-1",
        title: "Fikir bir",
        gate: "fikir",
        state: "awaiting_idea_approval",
        goal: "",
        acceptance: "",
        proposal: null,
        proposal_text: null,
        sha: null,
        reports: [],
        updated_at: null,
      },
      {
        task_id: "a-2",
        title: "Yayın iki",
        gate: "yayin",
        state: "awaiting_release_approval",
        goal: "",
        acceptance: "",
        proposal: null,
        proposal_text: null,
        sha: null,
        reports: [],
        updated_at: null,
      },
    ],
  };
}

const stamp = (minute: number) => `2026-10-01T10:${String(minute).padStart(2, "0")}:00Z`;

function working(agent: OfficeAgent, titles: string[], firstMinute: number): OfficeAgent {
  const runs = titles.map((title, i) => ({
    task_id: `t-${agent.seat}-${i + 1}`,
    task_title: title,
    since: stamp(firstMinute + i),
  }));
  return { ...agent, state: "working", ...runs[0], runs };
}

/**
 * The model policy's answer (ADR-0214 addenda 7 and 14): the defaults as the setting, every
 * seat with its role's model, the workers as the API names their role, limits unknown.
 */
export function modelPolicy(): OfficeView {
  const view = twoWorkers();
  const roles = {
    lead: "claude-fable-5-1",
    researcher: "claude-opus-5-5",
    integrator: "claude-opus-5-5",
    worker: "claude-opus-5-5",
    inspector: "claude-fable-5-1",
  } as const;
  view.models = { roles: { ...roles }, fallback: true, updated_at: "2026-10-01T09:00:00Z" };
  view.agents = view.agents.map((agent) => {
    const role = agent.seat.startsWith("worker-") ? "worker" : agent.seat;
    const model = role in roles ? roles[role as keyof typeof roles] : null;
    return { ...agent, role, model };
  });
  const unknown = { state: "ok" as const, resets_at: null, used_pct: null };
  view.cycle.limits = { fable: { ...unknown }, all: { ...unknown }, fallback: true, lowered: [] };
  return view;
}

/** A cycle with `workers` worker runs and `inspections` inspector runs, as the API sends it. */
export function busyCycle(workers: number, inspections: number): OfficeView {
  const view = twoWorkers();
  const seats = Math.max(4, workers);
  const workerSeats = Array.from({ length: seats }, (_, i) => `worker-${i + 1}`);
  const order = ["lead", "researcher", "integrator", ...workerSeats, "inspector", "owner"];
  view.agents = order.map((seat) => {
    const role = seat.startsWith("worker-") ? "worker" : seat;
    const idle: OfficeAgent = { seat, role, ...NO_RUN, runs: [] };
    const n = Number(seat.split("-")[1]);
    if (role === "worker" && n <= workers) return working(idle, [`Çalışan işi ${n}`], n);
    if (seat === "inspector" && inspections > 0) {
      const titles = Array.from({ length: inspections }, (_, i) => `Denetim ${i + 1}`);
      return working(idle, titles, 20);
    }
    return idle;
  });
  const running = workers + inspections;
  view.cycle = { ...view.cycle, running_agents: running, capacity: Math.max(6, running) };
  view.tasks["t-inspector-1"] = {
    ...view.tasks["t-one"],
    title: "Denetim 1",
    goal: "ilk denetimin hedefi",
    branch: "team/cycle/inspect-one",
  };
  return view;
}
