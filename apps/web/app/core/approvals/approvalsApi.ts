/**
 * The Onay Merkezi's client for `/v1/team/approvals` (docs/TEAM_PROTOCOL.md section 3).
 *
 * Two calls and no decision of its own: the Cloud Core lists what waits at the owner's two
 * gates and applies Onayla / Reddet to `team/queue.json` for the NEXT cycle. This file sends
 * what the owner pressed and reports what the Cloud Core answered; a refusal is surfaced with
 * the server's own sentence, never turned into a success.
 */

import { apiFetch } from "../../lib/session";

export const APPROVALS_PATH = "/v1/team/approvals";
export const DECISION_PATH = `${APPROVALS_PATH}/decision`;
export const TRIAL_DECISION_PATH = "/v1/team/trials/decision";

export type Gate = "fikir" | "yayin";

export type PendingApproval = {
  task_id: string;
  title: string;
  gate: Gate;
  state: string;
  goal: string;
  acceptance: string;
  proposal: string | null;
  proposal_text: string | null;
  sha: string | null;
  reports: { cycle: string; role: string; at: string; file: string; summary: string[] }[];
  updated_at: string | null;
};

/** One trial on a real device (ADR-0258): what the owner says, where, and what must happen. */
export type OwnerTrial = {
  id: string;
  sentence: string;
  machine: string;
  expect: string;
  verdict: null | "oldu" | "olmadi";
  said: string | null;
  at: string | null;
};

/** An undecided trial of a released task, as `GET /v1/team/approvals` lists it. */
export type OpenTrial = { task_id: string; title: string; sha: string | null; trial: OwnerTrial };

export type ApprovalsView = {
  approvals: PendingApproval[];
  /** The third gate (ADR-0258); absent from a Cloud Core older than it. */
  trials?: OpenTrial[];
  cycle_report: { file: string; text: string } | null;
  cycle_running: boolean;
  /**
   * Whether the Cloud Core takes a decision right now. Sent by a Cloud Core that queues a
   * decision made during a cycle; absent from an older one, which refuses while a cycle runs.
   */
  decisions_open?: boolean;
};

/** `decisions_open` when the Cloud Core sends it; otherwise the old rule: not while a cycle runs. */
export function decisionsOpen(view: Pick<ApprovalsView, "cycle_running" | "decisions_open">): boolean {
  return view.decisions_open ?? !view.cycle_running;
}

/** What the Onay Merkezi reports as waiting for the owner: both gates' tasks and the open trials. */
export function waitingCount(view: Pick<ApprovalsView, "approvals" | "trials">): number {
  return view.approvals.length + (view.trials?.length ?? 0);
}

export const GATE_TR: Record<Gate, string> = { fikir: "Fikir onayı", yayin: "Yayın onayı" };

export type DecisionResult =
  | { ok: true; state: string }
  | { ok: false; code: string; message: string };

async function refusal(response: Response): Promise<{ code: string; message: string }> {
  try {
    const body = (await response.json()) as { detail?: { code?: string; message?: string } };
    return {
      code: body.detail?.code ?? `http_${response.status}`,
      message: body.detail?.message ?? `HTTP ${response.status}`,
    };
  } catch {
    return { code: `http_${response.status}`, message: `HTTP ${response.status}` };
  }
}

export async function fetchApprovals(): Promise<ApprovalsView> {
  const response = await apiFetch(APPROVALS_PATH);
  if (!response.ok) throw new Error((await refusal(response)).message);
  return (await response.json()) as ApprovalsView;
}

/**
 * `gate` is sent so a page that was open while the task moved on is refused (`gate_mismatch`)
 * rather than approving a different gate than the one the owner read.
 */
export async function decide(
  approval: Pick<PendingApproval, "task_id" | "gate">,
  decision: "approve" | "reject",
  reason?: string,
): Promise<DecisionResult> {
  const response = await apiFetch(DECISION_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task_id: approval.task_id,
      gate: approval.gate,
      decision,
      reason: reason ?? null,
      channel: "shell",
    }),
  });
  if (!response.ok) return { ok: false, ...(await refusal(response)) };
  const body = (await response.json()) as { state: string };
  return { ok: true, state: body.state };
}

export type TrialDecisionResult =
  | { ok: true; fix_task_id: string | null; message: string }
  | { ok: false; code: string; message: string };

/** Oldu / Olmadı on one trial; `said` is the owner's words (the server requires them for "olmadi"). */
export async function decideTrial(
  open: Pick<OpenTrial, "task_id" | "trial">,
  verdict: "oldu" | "olmadi",
  said: string | null,
): Promise<TrialDecisionResult> {
  const response = await apiFetch(TRIAL_DECISION_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: open.task_id, trial_id: open.trial.id, verdict, said }),
  });
  if (!response.ok) return { ok: false, ...(await refusal(response)) };
  const body = (await response.json()) as { fix_task_id?: string | null; message?: string };
  return { ok: true, fix_task_id: body.fix_task_id ?? null, message: body.message ?? "" };
}
