import type { OfficeView } from "../officeApi";

export const SEAT_ORDER = [
  "lead",
  "researcher",
  "integrator",
  "worker-1",
  "worker-2",
  "worker-3",
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
