"use client";

/**
 * The Cockpit's client for Executive Autonomy (M26 spec §6).
 *
 * Two GETs and the POSTs the owner's levers ride on. `GET /v1/executive/runs`
 * lists what exists — the `executive_runs` rows: each run's goal in the
 * owner's own words, its state, the step it is on and how many of its steps
 * are done. `GET /v1/executive/runs/{id}` answers for ONE run what the
 * workflow's `explain` query answers ("the current step in one sentence:
 * what it is doing and what it waits for", spec §3) and, for a run that
 * ended `partial`, which steps did not verify and why. The POSTs —
 * `pause`, `resume`, `cancel`, `retry`, `amend` — are the same signals the
 * spoken "Bu işi durdur" / "Devam et" / "Bunu iptal et" send (§5): the
 * Cloud Core owns them, and this client can only ask.
 *
 * What this page cannot do is the point. It cannot plan a graph, run a
 * step, reach a family, verify a postcondition or end a run; it holds no
 * authority a graph does not have, and a graph has none of its own (§4).
 * The chips ask; the Cloud Core decides and answers; the answer is printed.
 *
 * The Cloud Core half is built on a parallel track (ADR-0089 §8), so the
 * response shapes below are the spec's columns read defensively: a field
 * the route does not send is `null` and is rendered as "not reported",
 * never filled in. A 404 on a route is "henüz yok", which is the truthful
 * word until it lands.
 */

import { MAX_LABEL_CHARS } from "../uistate/contract";
import { UnauthorizedError, apiFetch } from "../session";
import { type Loaded, load } from "./api";

// ---------------------------------------------------------------- the routes

export const EXECUTIVE_RUNS_PATH = "/v1/executive/runs";

/**
 * Every action route the spec names (§6), spelled once so both halves of
 * the contract spell them the same way. The Cockpit's chips press the first
 * three; `retry` and `amend` are the voice path's ("İkinci adımı tekrar
 * dene", "Sunumu da ekle"), and they are listed here because the route
 * vocabulary is one vocabulary — not because this page draws a button for
 * them, which it deliberately does not: neither can be asked for without
 * naming a step or a new step, and this page has no such words to offer.
 */
export const EXECUTIVE_ACTIONS = ["pause", "resume", "cancel", "retry", "amend", "approve"] as const;

export type ExecutiveAction = (typeof EXECUTIVE_ACTIONS)[number];

/** The three the panel draws, in the order the spec's own sentence names them. */
/** B38 (req 544): `approve` is the fourth chip, drawn only while a row names the step it waits on. */
export const EXECUTIVE_CHIP_ACTIONS = ["pause", "resume", "cancel", "approve"] as const;

export type ExecutiveChipAction = (typeof EXECUTIVE_CHIP_ACTIONS)[number];

/** The action's route: the id is a path segment, never a query; the action is the spec's word. */
export function executiveActionPath(runId: string, action: ExecutiveAction): string {
  return `/v1/executive/runs/${encodeURIComponent(runId)}/${action}`;
}

/** One run's own route, for the explanation and the missing steps. */
export function executiveRunPath(runId: string): string {
  return `${EXECUTIVE_RUNS_PATH}/${encodeURIComponent(runId)}`;
}

// ------------------------------------------------------------------ the rows

/**
 * How many missing steps one partial run may carry into a row. A client
 * bound taken from the graph's own: a plan has ≤ 24 steps (spec §1), so no
 * honest `missing` list is longer, and a publisher that sent more is
 * truncated rather than allowed to fill the panel.
 */
export const MAX_EXECUTIVE_MISSING = 24;

/** One step the run could not verify, as the run's route names it (spec §3). */
export type ExecutiveMissingStep = {
  /** The step id (`s4`), as the route says. */
  step: string;
  /** Why it did not verify (`not_found`, `timeout`, …), when the route said. */
  reason: string | null;
};

/**
 * One `executive_runs` row as the list route describes it (M26 spec §1,
 * §3), every field verbatim or `null`. `goal` is the owner's own words,
 * bounded like every other string that crosses this boundary.
 */
export type ExecutiveRunRow = {
  run_id: string;
  /** The owner's request in the owner's words, as the row holds it. */
  goal: string | null;
  /** The run's state, as the row says. */
  state: string | null;
  /** The step the run is on (`s3`), as the row says. */
  step: string | null;
  /** How many steps have finished, when the row counted. */
  done: number | null;
  /** How many steps the graph has, when the row counted. */
  total: number | null;
  /** The steps that did not verify, as the route named them; empty when none did. */
  missing: ExecutiveMissingStep[];
  /** B38 (req 544): the step waiting for the owner's yes, when the row names one. */
  awaiting_step: string | null;
  /** B38 (req 550): `rule` | `model` | `owner` - who built the plan, when the row says. */
  planner: string | null;
  created_at: string | null;
  updated_at: string | null;
};

/**
 * What `GET /v1/executive/runs/{id}` adds to a row: the workflow's own
 * `explain` sentence and, for a partial run, what is missing. Nothing here
 * repeats the row's state — the list is what says which runs exist and how
 * they ended, and two sources for one fact is how they drift apart.
 */
export type ExecutiveRunDetail = {
  run_id: string;
  explain: string | null;
  missing: ExecutiveMissingStep[];
};

function str(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  return value.slice(0, MAX_LABEL_CHARS);
}

/** A sentence, not a token: the explanation is one line of prose and gets a line's room. */
const MAX_EXPLAIN_CHARS = 240;

function sentence(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  return value.slice(0, MAX_EXPLAIN_CHARS);
}

/** A whole, non-negative count, or `null`. A count is never rounded into being. */
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** The list under one of `keys`, or the body itself when it is the list. */
function listAt(raw: unknown, keys: string[]): unknown[] {
  if (Array.isArray(raw)) return raw;
  if (raw && typeof raw === "object") {
    for (const key of keys) {
      const value = (raw as Record<string, unknown>)[key];
      if (Array.isArray(value)) return value;
    }
  }
  return [];
}

function isPresent<T>(value: T | null): value is T {
  return value !== null;
}

/**
 * The missing steps from whatever shape the route names them in: a list of
 * step ids, or a list of `{step, reason}` objects. An entry with no step id
 * is not a step and is dropped rather than defaulted — "eksik: bilinmiyor"
 * would be this page inventing a gap.
 */
export function parseMissingSteps(raw: unknown): ExecutiveMissingStep[] {
  if (!Array.isArray(raw)) return [];
  const steps: ExecutiveMissingStep[] = [];
  for (const item of raw) {
    if (steps.length >= MAX_EXECUTIVE_MISSING) break;
    if (typeof item === "string") {
      const step = str(item);
      if (step) steps.push({ step, reason: null });
      continue;
    }
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const step = str(o.step) ?? str(o.step_id) ?? str(o.id);
    if (step === null) continue;
    steps.push({ step, reason: str(o.reason) ?? str(o.error_class) ?? str(o.why) });
  }
  return steps;
}

/** One run from a raw row; `null` for a row with no id, which is not a run. */
export function parseExecutiveRow(raw: unknown): ExecutiveRunRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  // The names are the route's, exactly — no `??` chain of plausible spellings. A
  // fallback makes a mismatch invisible, which is how the two halves of M25's scene row
  // ended up in different languages with every suite green.
  const id = str(o.run_id);
  if (id === null) return null;
  return {
    run_id: id,
    goal: str(o.goal),
    state: str(o.state),
    step: str(o.step),
    done: count(o.done),
    total: count(o.total),
    missing: parseMissingSteps(o.missing),
    awaiting_step: str(o.awaiting_step),
    planner: str(o.planner),
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

/** One run's detail from the run's own route; `null` for a body with no id. */
export function parseExecutiveDetail(raw: unknown): ExecutiveRunDetail | null {
  if (!raw || typeof raw !== "object") return null;
  const body = raw as Record<string, unknown>;
  const id = str(body.run_id);
  if (id === null) return null;
  return {
    run_id: id,
    explain: sentence(body.explain),
    missing: parseMissingSteps(body.missing),
  };
}

export const fetchExecutiveRuns = (): Promise<Loaded<ExecutiveRunRow[]>> =>
  load<ExecutiveRunRow[]>(EXECUTIVE_RUNS_PATH, (raw) =>
    listAt(raw, ["runs", "items"]).map(parseExecutiveRow).filter(isPresent),
  );

export const fetchExecutiveRun = (runId: string): Promise<Loaded<ExecutiveRunDetail | null>> =>
  load<ExecutiveRunDetail | null>(executiveRunPath(runId), parseExecutiveDetail);

// -------------------------------------------------------------- the receipt

/** What an action answered, read from the receipt the route returns. */
export type ExecutiveActionReceipt = {
  /** The run's state after the call (`paused`, `cancelled`, …), when the answer named it. */
  state: string | null;
  /** The receipt's own sentence, when it had one. */
  summary: string | null;
  receiptId: string | null;
};

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/** The first value that was actually sent, in the order the shapes are tried. */
function first<T>(...values: (T | null)[]): T | null {
  return values.find((v): v is T => v !== null) ?? null;
}

/**
 * The receipt, read from any of the shapes the route may answer with: the
 * receipt as the body; the body carrying `receipt` and `run` beside each
 * other; the run row itself. Never invents a state: a 2xx with no state is
 * a receipt that named none, and is printed as one — a pressed "Duraklat"
 * is not a paused run until something says the run is paused.
 */
export function parseExecutiveReceipt(raw: unknown): ExecutiveActionReceipt {
  const body = record(raw) ?? {};
  const receipt = record(body.receipt) ?? body;
  const run = record(body.run) ?? record(receipt.run) ?? {};
  return {
    state: first(str(body.state), str(body.status), str(receipt.state), str(receipt.status), str(run.state)),
    summary: first(
      sentence(receipt.factual_summary),
      sentence(receipt.summary),
      sentence(receipt.speech),
      sentence(body.message),
      sentence(body.speech),
    ),
    receiptId: first(str(receipt.receipt_id), str(body.receipt_id), str(receipt.id)),
  };
}

// --------------------------------------------------------------- the errors

/** The gate refused, the run could not, or the route is not there. `code` is the Cloud Core's own token when it sent one. */
export class ExecutiveActionError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.name = "ExecutiveActionError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** The code this client gives a 404 that is the ROUTE missing, not the run. */
export const EXECUTIVE_ROUTE_ABSENT = "route_absent";

async function readBody(response: Response): Promise<unknown> {
  try {
    const text = await response.text();
    return text ? (JSON.parse(text) as unknown) : null;
  } catch {
    return null;
  }
}

/**
 * The typed refusal from a non-2xx answer; never throws itself.
 *
 * A 404 is read twice, as the genesis client reads it: a body naming a code
 * (or a sentence of the route's own) is the route refusing, and a bare
 * `Not Found` — or no body — is the route not being on this Cloud Core yet,
 * which is a different sentence (`henüz yok`) and is coded `route_absent`
 * so the panel says so.
 */
export async function toExecutiveActionError(response: Response, path: string): Promise<ExecutiveActionError> {
  const body = await readBody(response);
  let code: string | null = null;
  let detail: string | null = null;
  if (body && typeof body === "object") {
    const o = body as Record<string, unknown>;
    const d = o.detail;
    if (typeof d === "string") detail = d;
    else if (d && typeof d === "object") {
      const inner = d as Record<string, unknown>;
      code = str(inner.code) ?? str(inner.error_class) ?? str(inner.reason);
      detail = sentence(inner.message) ?? sentence(inner.detail);
    }
    code ??= str(o.code) ?? str(o.error_class) ?? str(o.reason);
    detail ??= sentence(o.message);
  }
  if (response.status === 404 && code === null && (detail === null || detail === "Not Found")) {
    return new ExecutiveActionError(404, EXECUTIVE_ROUTE_ABSENT, `Bu Cloud Core sürümünde ${path} yok (HTTP 404).`);
  }
  return new ExecutiveActionError(response.status, code, detail ?? `HTTP ${response.status}`);
}

/**
 * The refusals in the owner's words, by the code the Cloud Core sends.
 * Every sentence says what did NOT happen: a refusal to pause a run that
 * already ended, or to resume one nobody paused, is the Cloud Core holding
 * its own state machine (spec §3), and the owner must never read it as a
 * run that was lost.
 */
export const EXECUTIVE_ACTION_REFUSAL_TR: Record<string, string> = {
  not_found: "Böyle bir iş yok.",
  not_running: "İş sürmüyor; duraklatılacak bir şey yok.",
  not_paused: "İş duraklatılmış değil; sürdürülecek bir şey yok.",
  not_active: "İş sürmüyor; iptal edilecek bir şey yok.",
  already_paused: "Zaten duraklatılmış; ikinci kez duraklatılmadı.",
  already_cancelled: "Zaten iptal edilmiş; ikinci kez iptal edilmedi.",
  already_finished: "İş bitmiş; durumu değişmedi.",
  run_limit: "Aynı anda en çok iki iş yürütülür; bu istek uygulanmadı.",
  confirmation_required: "Bu istek doğrulanmış sahip oturumundan gelmeli; uygulanmadı.",
  timeout: "Cloud Core zamanında yanıt vermedi; sonucu bilinmiyor.",
};

/** One line for the owner from whatever the call threw. */
export function executiveActionErrorText(err: unknown): string {
  if (err instanceof ExecutiveActionError) {
    if (err.code === EXECUTIVE_ROUTE_ABSENT) return `${err.detail} Yapılmadı.`;
    const known = err.code ? EXECUTIVE_ACTION_REFUSAL_TR[err.code] : undefined;
    if (known) return known;
    return err.code ? `${err.code}: ${err.detail}` : err.detail;
  }
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

// -------------------------------------------------------------- the client

/**
 * Ask the Cloud Core to pause, resume or cancel one run. The body is empty:
 * which run, on whose session and turn, is the Cloud Core's and the
 * router's to decide (spec §4) — this page adds nothing it could, and above
 * all no argument of its own.
 */
export async function executiveAction(runId: string, action: ExecutiveChipAction): Promise<ExecutiveActionReceipt> {
  const path = executiveActionPath(runId, action);
  const response = await apiFetch(path, { method: "POST" });
  if (!response.ok) throw await toExecutiveActionError(response, path);
  return parseExecutiveReceipt(await readBody(response));
}

/**
 * The three calls the chips can make, as an object so a test can hand the
 * panel a double and prove each press makes exactly one call, with which id
 * — without a network and without this file's `apiFetch`.
 */
export type ExecutiveClient = {
  pause: (runId: string) => Promise<ExecutiveActionReceipt>;
  resume: (runId: string) => Promise<ExecutiveActionReceipt>;
  cancel: (runId: string) => Promise<ExecutiveActionReceipt>;
  /** B38: the owner's yes for the step the run waits on (the Cloud Core knows which). */
  approve: (runId: string) => Promise<ExecutiveActionReceipt>;
};

/** The real client: the four POSTs above, through the owner session. */
export const executiveClient: ExecutiveClient = {
  pause: (runId) => executiveAction(runId, "pause"),
  resume: (runId) => executiveAction(runId, "resume"),
  cancel: (runId) => executiveAction(runId, "cancel"),
  approve: (runId) => executiveAction(runId, "approve"),
};

// ---------------------------------------------------------- control state

/** The one call in flight. There is never more than one: the Cloud Core is asked one thing at a time. */
export type ExecutiveBusy = { action: ExecutiveChipAction; id: string };

/** What the last call answered, in the owner's words, with the run it was about. */
export type ExecutiveActionOutcome = {
  action: ExecutiveChipAction;
  id: string;
  /** True when the route answered 2xx. NOT "paused": the receipt's state says that, in `text`. */
  ok: boolean;
  text: string;
  at: number;
};

export type ExecutiveControlState = {
  busy: ExecutiveBusy | null;
  outcome: ExecutiveActionOutcome | null;
};

export const EXECUTIVE_CONTROL_IDLE: ExecutiveControlState = { busy: null, outcome: null };

/** What the panel is handed: the state, and the three things a click may ask for. */
export type ExecutiveControlProps = {
  busy: ExecutiveBusy | null;
  outcome: ExecutiveActionOutcome | null;
  onPause: (runId: string) => void;
  onResume: (runId: string) => void;
  onCancel: (runId: string) => void;
  onApprove: (runId: string) => void;
};

/**
 * What the panel is handed for the run details: one sentence and one
 * missing-step list per run, or nothing. A run the detail route said
 * nothing about is a run this page says nothing about — never one with an
 * invented explanation.
 */
export type ExecutiveDetailProps = {
  detailFor: (runId: string) => ExecutiveRunDetail | null;
  /** Said when a detail could not be fetched: the run exists and this page could not ask about it. */
  notice: string | null;
};

export const EXECUTIVE_DETAILS_NONE: ExecutiveDetailProps = { detailFor: () => null, notice: null };
