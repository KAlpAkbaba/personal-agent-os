"use client";

/**
 * The Cockpit's client for the App Factory (M23 spec §6).
 *
 * Four routes and nothing else. One GET lists what exists — `/v1/apps`,
 * the `app_projects` rows: each project's name, kind, template, state, the
 * port its bounded process is bound to while it runs, and the counts its
 * last test run gave. Three POSTs ask the Cloud Core to act on one project —
 * `/v1/apps/{id}/run`, `/v1/apps/{id}/stop`, `/v1/apps/{id}/test` — which
 * are the Cloud Core's own `project.run` / `project.stop` / `project.test`
 * capabilities on the device (ADR-0086 §3: the companion's own child in a
 * job object, a command KEY from the manifest's allowlist, never a command
 * line from this page). This client decides none of that. It cannot
 * scaffold, cannot start a process, cannot reach the device; it can only
 * ask, and print the answer.
 *
 * The Cloud Core half is built on a parallel track, so the response shapes
 * below are the spec's columns read defensively: a field the route does not
 * send is `null` and is rendered as "not reported", never filled in. A 404
 * on an action route is "henüz yok", which is the truthful word until it lands.
 */

import { API_BASE, UnauthorizedError, apiFetch } from "../session";
import { type AppTestCounts, asPort, parseAppTestCounts } from "../uistate/contract";
import { type Loaded, load } from "./api";

// ---------------------------------------------------------------- the routes

export const APPS_PATH = "/v1/apps";

/** The three things the Cockpit may ask for, in the order the chips are drawn. */
export const APP_ACTIONS = ["run", "stop", "test"] as const;

export type AppAction = (typeof APP_ACTIONS)[number];

/** The action's route: the id is a path segment, never a query; the action is the spec's word. */
export function appActionPath(appId: string, action: AppAction): string {
  return `/v1/apps/${encodeURIComponent(appId)}/${action}`;
}

/** The list's URL as the browser would address it — for the absent notice, never fetched by a link. */
export function appsUrl(): string {
  return `${API_BASE}${APPS_PATH}`;
}

// ------------------------------------------------------------------ the rows

/**
 * One `app_projects` row as the list route describes it (M23 spec §1),
 * every field verbatim or `null`. `port` is `run_port` read as a port;
 * `tests` is `test_report_json`'s two counts, when the row carried them.
 */
export type AppProjectRow = {
  app_id: string;
  name: string | null;
  /** `web_static` | `web_api` | `cli`, or whatever the row says. */
  kind: string | null;
  /** `task-tracker` | `static-page` | `cli-tool`, or whatever the row says. */
  template: string | null;
  /** The `AppProject` state, as the row says. */
  state: string | null;
  /** The port the bounded process is bound to on `127.0.0.1`, when the row named one. */
  port: number | null;
  /** The counts the last test run gave, when the row carried them. */
  tests: AppTestCounts | null;
  /** The project's root under the device's `Projects` root, when the row named it. */
  root_path: string | null;
  created_at: string | null;
  updated_at: string | null;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
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

/** A JSON column that may arrive as the object or as its text; anything unreadable is nothing. */
function jsonColumn(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

/**
 * The counts from a row or a receipt: `tests` as the contract shapes it,
 * else the test report's own `passed` / `failed`, else the flat pair a
 * flattening publisher sends — both counts required or there are none.
 */
export function testCountsOf(o: Record<string, unknown>): AppTestCounts | null {
  const direct = parseAppTestCounts(o.tests) ?? parseAppTestCounts(jsonColumn(o.test_report_json ?? o.test_report));
  if (direct) return direct;
  return parseAppTestCounts({ passed: o.tests_passed, failed: o.tests_failed });
}

/** One project from a raw row; `null` for a row with no id, which is not a project. */
export function parseAppRow(raw: unknown): AppProjectRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.app_id) ?? str(o.project_id) ?? str(o.id);
  if (id === null) return null;
  return {
    app_id: id,
    name: str(o.name),
    kind: str(o.kind),
    template: str(o.template),
    state: str(o.state),
    port: asPort(o.run_port) ?? asPort(o.port),
    tests: testCountsOf(o),
    root_path: str(o.root_path),
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

export const fetchApps = (): Promise<Loaded<AppProjectRow[]>> =>
  load<AppProjectRow[]>(APPS_PATH, (raw) => listAt(raw, ["apps", "projects", "items"]).map(parseAppRow).filter(isPresent));

// -------------------------------------------------------------- the receipt

/** What an action answered, read from the receipt the route returns. */
export type AppActionReceipt = {
  /** The project's state after the call (`running`, `stopped`, `tested`, …), when the answer named it. */
  state: string | null;
  /** The port the bounded process is bound to, when the answer named one. */
  port: number | null;
  /** The counts a test run gave, when the answer carried them. */
  tests: AppTestCounts | null;
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
 * receipt as the body; the body carrying `receipt`, `result` and `project`
 * beside each other; the project row itself. Never invents a state: a 2xx
 * with no state is a receipt that named none, and is printed as one.
 */
export function parseAppReceipt(raw: unknown): AppActionReceipt {
  const body = record(raw) ?? {};
  const receipt = record(body.receipt) ?? body;
  const result = record(body.result) ?? record(receipt.result) ?? {};
  const project = record(body.project) ?? record(receipt.project) ?? {};
  return {
    state: first(str(body.state), str(body.status), str(receipt.state), str(receipt.status), str(project.state)),
    port: first(asPort(body.port), asPort(body.run_port), asPort(result.port), asPort(project.run_port), asPort(project.port)),
    // `project.test` answers `{exit_code, passed, failed, report_tail}` (spec §3):
    // the result IS the report, so its own two counts are read as such — after
    // the contract's `tests` shape, before the row's report column.
    tests: first(
      testCountsOf(body),
      parseAppTestCounts(result),
      testCountsOf(result),
      testCountsOf(receipt),
      testCountsOf(project),
      parseAppTestCounts(body),
    ),
    summary: first(
      str(receipt.factual_summary),
      str(receipt.summary),
      str(receipt.speech),
      str(body.message),
      str(body.speech),
      str(result.report_tail),
    ),
    receiptId: first(str(receipt.receipt_id), str(body.receipt_id), str(receipt.id)),
  };
}

// --------------------------------------------------------------- the errors

/** The gate refused, the device could not, or the route is not there. `code` is the Cloud Core's own token when it sent one. */
export class AppActionError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.name = "AppActionError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** The code this client gives a 404 that is the ROUTE missing, not the project. */
export const APP_ROUTE_ABSENT = "route_absent";

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
 * A 404 is read twice, as the artifact client reads it: a body naming a
 * code (or a sentence of the route's own) is the route refusing, and a bare
 * `Not Found` — or no body — is the route not being on this Cloud Core yet,
 * which is a different sentence (`henüz yok`) and is coded `route_absent`
 * so the panel says so.
 */
export async function toAppActionError(response: Response, path: string): Promise<AppActionError> {
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
    return new AppActionError(404, APP_ROUTE_ABSENT, `Bu Cloud Core sürümünde ${path} yok (HTTP 404).`);
  }
  return new AppActionError(response.status, code, detail ?? `HTTP ${response.status}`);
}

/**
 * The refusals in the owner's words, by the code the Cloud Core sends.
 * Every sentence says what did NOT happen: a refusal to run a third project
 * or a command outside the manifest's allowlist is ADR-0086 working, and
 * the owner must never read it as an app that was lost.
 */
export const APP_ACTION_REFUSAL_TR: Record<string, string> = {
  capability_missing: "Cihazdaki ajan bu sürümde proje çalıştıramıyor (projects ailesi yok). Yapılmadı.",
  device_offline: "Cihaz çevrimdışı; yapılmadı.",
  no_device: "Kayıtlı bir cihaz yok; yapılmadı.",
  operator_disabled: "Operatör bu cihazda kapalı; yapılmadı.",
  not_found: "Böyle bir uygulama yok.",
  not_scaffolded: "Projenin iskeleti cihazda kurulu değil; çalıştırılmadı.",
  already_running: "Uygulama zaten çalışıyor; ikinci kez başlatılmadı.",
  not_running: "Uygulama çalışmıyor; durdurulacak süreç yok.",
  too_many_running: "Aynı anda en fazla iki proje çalışır; üçüncüsü başlatılmadı.",
  command_not_allowed: "Komut şablonun izin listesinde değil; süreç başlatılmadı.",
  port_in_use: "Port dolu; süreç başlatılmadı.",
  run_failed: "Süreç başlatılamadı.",
  stop_failed: "Süreç durdurulamadı.",
  test_failed: "Testler çalıştırılamadı.",
  timeout: "Cihaz zamanında yanıt vermedi; sonucu bilinmiyor.",
};

/** One line for the owner from whatever the call threw. */
export function appActionErrorText(err: unknown): string {
  if (err instanceof AppActionError) {
    if (err.code === APP_ROUTE_ABSENT) return `${err.detail} Yapılmadı.`;
    const known = err.code ? APP_ACTION_REFUSAL_TR[err.code] : undefined;
    if (known) return known;
    return err.code ? `${err.code}: ${err.detail}` : err.detail;
  }
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

// -------------------------------------------------------------- the client

/**
 * Ask the Cloud Core to run, stop or test one project on the owner's
 * machine. The body is empty: which command key, on which port, in which
 * job, is the Cloud Core's and the manifest's to decide (ADR-0086 §3) —
 * this page adds nothing it could.
 */
export async function appAction(appId: string, action: AppAction): Promise<AppActionReceipt> {
  const path = appActionPath(appId, action);
  const response = await apiFetch(path, { method: "POST" });
  if (!response.ok) throw await toAppActionError(response, path);
  return parseAppReceipt(await readBody(response));
}

/**
 * The three calls the chips can make, as an object so a test can hand the
 * panel a double and prove each press makes exactly one call, with which id
 * — without a network and without this file's `apiFetch`.
 */
export type AppsClient = {
  run: (appId: string) => Promise<AppActionReceipt>;
  stop: (appId: string) => Promise<AppActionReceipt>;
  test: (appId: string) => Promise<AppActionReceipt>;
};

/** The real client: the three POSTs above, through the owner session. */
export const appsClient: AppsClient = {
  run: (appId) => appAction(appId, "run"),
  stop: (appId) => appAction(appId, "stop"),
  test: (appId) => appAction(appId, "test"),
};

// ---------------------------------------------------------- control state

/** The one call in flight. There is never more than one: the device is asked one thing at a time. */
export type AppsBusy = { action: AppAction; id: string };

/** What the last call answered, in the owner's words, with the project it was about. */
export type AppActionOutcome = {
  action: AppAction;
  id: string;
  /** True when the route answered 2xx. NOT "running": the receipt's state says that, in `text`. */
  ok: boolean;
  text: string;
  at: number;
};

export type AppsControlState = {
  busy: AppsBusy | null;
  outcome: AppActionOutcome | null;
};

export const APPS_CONTROL_IDLE: AppsControlState = { busy: null, outcome: null };

/** What the panel is handed: the state, and the three things a click may ask for. */
export type AppsControlProps = {
  busy: AppsBusy | null;
  outcome: AppActionOutcome | null;
  onRun: (appId: string) => void;
  onStop: (appId: string) => void;
  onTest: (appId: string) => void;
};
