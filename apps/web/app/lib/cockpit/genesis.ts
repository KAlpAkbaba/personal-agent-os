"use client";

/**
 * The Cockpit's client for Capability Genesis (M24 spec §8).
 *
 * Three routes and nothing else. One GET lists what exists — `/v1/genesis/runs`,
 * the `genesis_runs` rows: each run's capability, state, whether authority
 * parked it for the owner, its authority and side-effect classes, and on
 * `failed` the error class and message. Two POSTs ask the Cloud Core to act
 * on one run — `/v1/genesis/runs/{id}/approve`, `/v1/genesis/runs/{id}/cancel`
 * — which are the Cloud Core's own `capability.approve` / `capability.cancel`
 * (spec §5, §6: the approval is recorded as a `Confirmation` bound to the
 * owner's session, the asset authorized through the existing registry, and
 * the run continues; a cancel leaves no registration). This client decides
 * none of that. It cannot research an interface, render an adapter, register
 * a capability or reach the application; it can only ask, and print the
 * answer.
 *
 * The Cloud Core half is built on a parallel track (ADR-0087 §8), so the
 * response shapes below are the spec's columns read defensively: a field the
 * route does not send is `null` and is rendered as "not reported", never
 * filled in. A 404 on an action route is "henüz yok", which is the truthful
 * word until it lands.
 */

import { API_BASE, UnauthorizedError, apiFetch } from "../session";
import { type Loaded, load } from "./api";

// ---------------------------------------------------------------- the routes

export const GENESIS_RUNS_PATH = "/v1/genesis/runs";

/** The two things the Cockpit may ask for, in the order the chips are drawn. */
export const GENESIS_ACTIONS = ["approve", "cancel"] as const;

export type GenesisAction = (typeof GENESIS_ACTIONS)[number];

/** The action's route: the id is a path segment, never a query; the action is the spec's word. */
export function genesisActionPath(runId: string, action: GenesisAction): string {
  return `/v1/genesis/runs/${encodeURIComponent(runId)}/${action}`;
}

/** The list's URL as the browser would address it — for the absent notice, never fetched by a link. */
export function genesisRunsUrl(): string {
  return `${API_BASE}${GENESIS_RUNS_PATH}`;
}

// ------------------------------------------------------------------ the rows

/**
 * One `genesis_runs` row as the list route describes it (M24 spec §5),
 * every field verbatim or `null`. `capability` is `capability_id`;
 * `approval_required` is the row's flag when it sent one.
 */
export type GenesisRunRow = {
  run_id: string;
  /** The capability the run is acquiring (`counterbox.increment`), as the row names it. */
  capability: string | null;
  /** The run's state, as the row says. */
  state: string | null;
  /** True when authority parked the run for the owner, when the row said so. */
  approval_required: boolean | null;
  /** `read_only` | `mutating_authorized_asset` | `mutating_unauthorized`, or whatever the row says. */
  authority_class: string | null;
  /** `none` | `read` | `mutate_external`, or whatever the row says. */
  side_effect_class: string | null;
  /** On `failed`: the taxonomy class, as the row says. */
  error_class: string | null;
  /** On `failed`: the run's own sentence, as the row says. */
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function flag(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
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

/** One run from a raw row; `null` for a row with no id, which is not a run. */
export function parseGenesisRow(raw: unknown): GenesisRunRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.run_id) ?? str(o.id);
  if (id === null) return null;
  return {
    run_id: id,
    capability: str(o.capability_id) ?? str(o.capability),
    state: str(o.state),
    approval_required: flag(o.approval_required),
    authority_class: str(o.authority_class),
    side_effect_class: str(o.side_effect_class),
    error_class: str(o.error_class),
    error_message: str(o.error_message),
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

export const fetchGenesisRuns = (): Promise<Loaded<GenesisRunRow[]>> =>
  load<GenesisRunRow[]>(GENESIS_RUNS_PATH, (raw) => listAt(raw, ["runs", "items"]).map(parseGenesisRow).filter(isPresent));

// --------------------------------------------------------- B36: the catalogue

/** B36 (req 562/563): the interfaces the owner registered, as `/v1/genesis/catalogue` lists them. */
export const GENESIS_CATALOGUE_PATH = "/v1/genesis/catalogue";

export type CatalogueEntryRow = {
  name: string;
  url: string | null;
  target_phrases: string[];
  operations: Array<{ operation_id: string; verbs: string[] }>;
  /** `owner_rest` | `owner_voice` | `discovery`, or whatever the row says. */
  source: string | null;
  enabled: boolean | null;
  updated_at: string | null;
};

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string" && v !== "") : [];
}

/** One catalogue entry from a raw row; `null` for a row with no name. */
export function parseCatalogueEntry(raw: unknown): CatalogueEntryRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const name = str(o.name);
  if (name === null) return null;
  const operations = Array.isArray(o.operations)
    ? o.operations
        .map((op) => {
          if (!op || typeof op !== "object") return null;
          const id = str((op as Record<string, unknown>).operation_id);
          return id === null ? null : { operation_id: id, verbs: strings((op as Record<string, unknown>).verbs) };
        })
        .filter(isPresent)
    : [];
  return {
    name,
    url: str(o.url),
    target_phrases: strings(o.target_phrases),
    operations,
    source: str(o.source),
    enabled: flag(o.enabled),
    updated_at: str(o.updated_at),
  };
}

export const fetchGenesisCatalogue = (): Promise<Loaded<CatalogueEntryRow[]>> =>
  load<CatalogueEntryRow[]>(GENESIS_CATALOGUE_PATH, (raw) =>
    listAt(raw, ["entries", "items"]).map(parseCatalogueEntry).filter(isPresent),
  );

// -------------------------------------------------------------- the receipt

/** What an action answered, read from the receipt the route returns. */
export type GenesisActionReceipt = {
  /** The run's state after the call (`rolling_out`, `failed`, …), when the answer named it. */
  state: string | null;
  /** The failure's class, when the answer named one. */
  errorClass: string | null;
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
 * a receipt that named none, and is printed as one.
 */
export function parseGenesisReceipt(raw: unknown): GenesisActionReceipt {
  const body = record(raw) ?? {};
  const receipt = record(body.receipt) ?? body;
  const run = record(body.run) ?? record(receipt.run) ?? {};
  return {
    state: first(str(body.state), str(body.status), str(receipt.state), str(receipt.status), str(run.state)),
    errorClass: first(str(body.error_class), str(receipt.error_class), str(run.error_class)),
    summary: first(
      str(receipt.factual_summary),
      str(receipt.summary),
      str(receipt.speech),
      str(body.message),
      str(body.speech),
      str(run.error_message),
    ),
    receiptId: first(str(receipt.receipt_id), str(body.receipt_id), str(receipt.id)),
  };
}

// --------------------------------------------------------------- the errors

/** The gate refused, the run could not, or the route is not there. `code` is the Cloud Core's own token when it sent one. */
export class GenesisActionError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.name = "GenesisActionError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** The code this client gives a 404 that is the ROUTE missing, not the run. */
export const GENESIS_ROUTE_ABSENT = "route_absent";

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
 * A 404 is read twice, as the apps client reads it: a body naming a code
 * (or a sentence of the route's own) is the route refusing, and a bare
 * `Not Found` — or no body — is the route not being on this Cloud Core yet,
 * which is a different sentence (`henüz yok`) and is coded `route_absent`
 * so the panel says so.
 */
export async function toGenesisActionError(response: Response, path: string): Promise<GenesisActionError> {
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
      detail = str(inner.message) ?? str(inner.detail);
    }
    code ??= str(o.code) ?? str(o.error_class) ?? str(o.reason);
    detail ??= str(o.message);
  }
  if (response.status === 404 && code === null && (detail === null || detail === "Not Found")) {
    return new GenesisActionError(404, GENESIS_ROUTE_ABSENT, `Bu Cloud Core sürümünde ${path} yok (HTTP 404).`);
  }
  return new GenesisActionError(response.status, code, detail ?? `HTTP ${response.status}`);
}

/**
 * The refusals in the owner's words, by the code the Cloud Core sends.
 * Every sentence says what did NOT happen: a refusal to approve a run that
 * is not waiting, or to record an authorization outside the owner's own
 * session, is ADR-0087 §5 working, and the owner must never read it as a
 * capability that was lost.
 */
export const GENESIS_ACTION_REFUSAL_TR: Record<string, string> = {
  not_found: "Böyle bir yetenek çalışması yok.",
  not_awaiting_approval: "Çalışma onay beklemiyor; onaylanmadı.",
  not_active: "Çalışma sürmüyor; vazgeçilecek bir şey yok.",
  already_approved: "Zaten onaylanmış; ikinci kez onaylanmadı.",
  already_cancelled: "Zaten vazgeçilmiş; ikinci kez vazgeçilmedi.",
  authorization_failed: "Yetkilendirme kaydedilemedi; çalışma sürmedi.",
  confirmation_required: "Onay, doğrulanmış sahip oturumundan verilmeli; kaydedilmedi.",
  run_failed: "Çalışma başarısız oldu; yetenek kaydedilmedi.",
  timeout: "Cloud Core zamanında yanıt vermedi; sonucu bilinmiyor.",
};

/** One line for the owner from whatever the call threw. */
export function genesisActionErrorText(err: unknown): string {
  if (err instanceof GenesisActionError) {
    if (err.code === GENESIS_ROUTE_ABSENT) return `${err.detail} Yapılmadı.`;
    const known = err.code ? GENESIS_ACTION_REFUSAL_TR[err.code] : undefined;
    if (known) return known;
    return err.code ? `${err.code}: ${err.detail}` : err.detail;
  }
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

// -------------------------------------------------------------- the client

/**
 * Ask the Cloud Core to approve or cancel one run. The body is empty: which
 * asset is authorized, bound to which session and turn, is the Cloud
 * Core's and the router's to decide (ADR-0087 §5) — this page adds nothing
 * it could, and above all no argument of its own.
 */
export async function genesisAction(runId: string, action: GenesisAction): Promise<GenesisActionReceipt> {
  const path = genesisActionPath(runId, action);
  const response = await apiFetch(path, { method: "POST" });
  if (!response.ok) throw await toGenesisActionError(response, path);
  return parseGenesisReceipt(await readBody(response));
}

/**
 * The two calls the chips can make, as an object so a test can hand the
 * panel a double and prove each press makes exactly one call, with which id
 * — without a network and without this file's `apiFetch`.
 */
export type GenesisClient = {
  approve: (runId: string) => Promise<GenesisActionReceipt>;
  cancel: (runId: string) => Promise<GenesisActionReceipt>;
};

/** The real client: the two POSTs above, through the owner session. */
export const genesisClient: GenesisClient = {
  approve: (runId) => genesisAction(runId, "approve"),
  cancel: (runId) => genesisAction(runId, "cancel"),
};

// ---------------------------------------------------------- control state

/** The one call in flight. There is never more than one: the Cloud Core is asked one thing at a time. */
export type GenesisBusy = { action: GenesisAction; id: string };

/** What the last call answered, in the owner's words, with the run it was about. */
export type GenesisActionOutcome = {
  action: GenesisAction;
  id: string;
  /** True when the route answered 2xx. NOT "approved": the receipt's state says that, in `text`. */
  ok: boolean;
  text: string;
  at: number;
};

export type GenesisControlState = {
  busy: GenesisBusy | null;
  outcome: GenesisActionOutcome | null;
};

export const GENESIS_CONTROL_IDLE: GenesisControlState = { busy: null, outcome: null };

/** What the panel is handed: the state, and the two things a click may ask for. */
export type GenesisControlProps = {
  busy: GenesisBusy | null;
  outcome: GenesisActionOutcome | null;
  onApprove: (runId: string) => void;
  onCancel: (runId: string) => void;
};
