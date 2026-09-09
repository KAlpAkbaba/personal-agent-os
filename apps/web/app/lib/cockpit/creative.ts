"use client";

/**
 * The Cockpit's client for the Creative Tools Operator (M27 spec §3, §6).
 *
 * Four routes and nothing else. One GET lists what exists —
 * `/v1/creative/runs`, the creative run rows: which application each run was
 * made in, the source it started from, the operation it is on, the step it
 * reached, what the COMPARISON measured (the produced dimensions, the
 * bounded aggregate, the defect it named), which correction round it is on,
 * and whether a before and an after image exist. One GET fetches those
 * images — `/v1/creative/runs/{id}/image/{before|after}`, owner-session
 * gated, the PNG the run stored and an independent reader validated before
 * it was kept (M27 spec §7). Two POSTs ask the Cloud Core to act on one run
 * — `/v1/creative/runs/{id}/export`, `/v1/creative/runs/{id}/compare` —
 * which are the Cloud Core's own `creative.export` and its comparison over
 * the device's `creative.export_check`: the output is written under the
 * documents family's roots as a NEW file beside the source, reopened by an
 * independent reader, and measured against what was asked. This client
 * decides none of that. It cannot open an application, write a plan, draw a
 * pixel or reach the device; it can only ask, and print the answer.
 *
 * The Cloud Core half is built on a parallel track, so the response shapes
 * below are the spec's columns read defensively: a field the route does not
 * send is `null` and is rendered as "not reported", never filled in. A 404
 * on an action route is "henüz yok", which is the truthful word until it
 * lands.
 */

import { API_BASE, UnauthorizedError, apiFetch } from "../session";
import { MAX_CREATIVE_ROUNDS, asSimilarity } from "../uistate/contract";
import { type Loaded, load } from "./api";

// ---------------------------------------------------------------- the routes

export const CREATIVE_RUNS_PATH = "/v1/creative/runs";

/** The two things the Cockpit may ask for, in the order the chips are drawn. */
export const CREATIVE_ACTIONS = ["export", "compare"] as const;

export type CreativeAction = (typeof CREATIVE_ACTIONS)[number];

/**
 * The two pictures a run has: what the owner gave it and what it produced.
 * Named rather than numbered, because "before" and "after" is the whole
 * claim the panel makes by putting them side by side.
 */
export const CREATIVE_IMAGE_SIDES = ["before", "after"] as const;

export type CreativeImageSide = (typeof CREATIVE_IMAGE_SIDES)[number];

/** The action's route: the id is a path segment, never a query; the action is the spec's word. */
export function creativeActionPath(runId: string, action: CreativeAction): string {
  return `/v1/creative/runs/${encodeURIComponent(runId)}/${action}`;
}

/**
 * One image's route (a GET). Owner-session gated like M13's and M25's, so it
 * is FETCHED with the session rather than handed to an `<img src>` — a
 * bearer API answers a plain image request with a 401, and a broken image
 * would read as "no picture". M25 learned this the hard way; this family has
 * two images per row, so it would have read as "nothing was made at all".
 */
export function creativeImagePath(runId: string, side: CreativeImageSide): string {
  return `/v1/creative/runs/${encodeURIComponent(runId)}/image/${side}`;
}

/** An image's URL as the browser would address it — printed, never fetched by a link. */
export function creativeImageUrl(runId: string, side: CreativeImageSide): string {
  return `${API_BASE}${creativeImagePath(runId, side)}`;
}

/** The list's URL as the browser would address it — for the absent notice, never fetched by a link. */
export function creativeRunsUrl(): string {
  return `${API_BASE}${CREATIVE_RUNS_PATH}`;
}

// ------------------------------------------------------------------ the rows

/**
 * One creative run as the list route describes it (M27 spec §2, §3), every
 * field verbatim or `null`.
 *
 * `source` and `output` are file NAMES, never paths and never bytes: the
 * owner's original is never overwritten and every output is a new file
 * beside it (`<name>-pagentos-<n>.<ext>`, ADR-0093 decision 5), so the two
 * names are what tells the owner that happened. `width`/`height`,
 * `similarity` and `defect` are what the COMPARISON measured; `round` is
 * which of the ≤ 3 correction rounds the run is on.
 */
export type CreativeRunRow = {
  run_id: string;
  /** `paint` | `photoshop` | `illustrator` | `figma`, or whatever the row says. */
  tool: string | null;
  /** The source file's name, as the row says. Never a path. */
  source: string | null;
  /** The output file's name, as the row says — a NEW file beside the source. */
  output: string | null;
  /** The plan operation the run is on, as the row says. */
  operation: string | null;
  /** The step the run is on, as the row says. */
  state: string | null;
  /** The comparison's bounded aggregate in 0..1, when one was measured. NOT SSIM (ADR-0093 §4). */
  similarity: number | null;
  /** The defect the comparison named, when it named one. */
  defect: string | null;
  /** Which correction round the run is on, when the row counted. */
  round: number | null;
  /** How many rounds the run is allowed, when the row said. */
  rounds: number | null;
  /** The produced image's width in pixels, as the comparison measured it. */
  width: number | null;
  /** The produced image's height in pixels, as the comparison measured it. */
  height: number | null;
  /** True when the row says a source image exists to fetch. Never inferred from anything else. */
  has_before: boolean;
  /** True when the row says an output image exists to fetch. Never inferred from anything else. */
  has_after: boolean;
  /** The source image's sha256 as the row gave it, when it did. */
  before_sha256: string | null;
  /** The output image's sha256 as the row gave it, when it did. */
  after_sha256: string | null;
  /** On a failure or an undriveable application: the taxonomy class, as the row says. */
  error_class: string | null;
  /** On a failure or an undriveable application: the run's own sentence. */
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

/** A whole, non-negative count, or `null` — never rounded into being. */
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/**
 * A correction round the row actually reported: a whole number from 1 to the
 * spec's ceiling. A `0` is not a round (no correction has run), and anything
 * above the ceiling is a figure this build will not print as one of three.
 */
export function asCreativeRound(value: unknown): number | null {
  const n = count(value);
  return n !== null && n >= 1 && n <= MAX_CREATIVE_ROUNDS ? n : null;
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
 * Whether one side's image exists, from what the row actually said: the flag
 * when it sent one, else the presence of a stored image's identity (its name
 * or its sha256). Never from the state — a run that once said `exporting` is
 * not a run with a file on disk, and offering the owner a picture that is
 * not there would be this page inventing one.
 */
export function parseHasImage(o: Record<string, unknown>, side: CreativeImageSide): boolean {
  const declared =
    side === "before"
      ? (flag(o.has_before) ?? flag(o.has_source_image) ?? flag(o.source_available))
      : (flag(o.has_after) ?? flag(o.has_output_image) ?? flag(o.output_available));
  if (declared !== null) return declared;
  const sha = side === "before" ? str(o.before_sha256) ?? str(o.source_sha256) : str(o.after_sha256) ?? str(o.output_sha256);
  if (sha !== null) return true;
  return side === "before" ? str(o.source) !== null : str(o.output) !== null;
}

/** One run from a raw row; `null` for a row with no id, which is not a run. */
export function parseCreativeRunRow(raw: unknown): CreativeRunRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.run_id) ?? str(o.id);
  if (id === null) return null;
  return {
    run_id: id,
    tool: str(o.tool),
    source: str(o.source) ?? str(o.source_name),
    output: str(o.output) ?? str(o.output_name),
    operation: str(o.operation),
    state: str(o.state),
    similarity: asSimilarity(o.similarity),
    defect: str(o.defect),
    round: asCreativeRound(o.round),
    rounds: count(o.rounds ?? o.max_rounds),
    width: count(o.width),
    height: count(o.height),
    has_before: parseHasImage(o, "before"),
    has_after: parseHasImage(o, "after"),
    before_sha256: str(o.before_sha256) ?? str(o.source_sha256),
    after_sha256: str(o.after_sha256) ?? str(o.output_sha256),
    error_class: str(o.error_class),
    error_message: str(o.error_message),
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

export const fetchCreativeRuns = (): Promise<Loaded<CreativeRunRow[]>> =>
  load<CreativeRunRow[]>(CREATIVE_RUNS_PATH, (raw) =>
    listAt(raw, ["runs", "creative_runs", "items"]).map(parseCreativeRunRow).filter(isPresent),
  );

// --------------------------------------------------------------- the images

/**
 * Fetch one side of one run's pictures through the owner session.
 *
 * The route is gated exactly as M13's and M25's downloads are, so the bytes
 * are fetched with the session's bearer and handed to the page as a blob —
 * never addressed by a bare `<img src>`, which would 401 and draw two broken
 * pictures where the owner would read "nothing was made".
 */
export async function fetchCreativeImageBlob(runId: string, side: CreativeImageSide): Promise<Blob> {
  const response = await apiFetch(creativeImagePath(runId, side));
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.blob();
}

/**
 * What the panel is handed for the pictures: the source for one side of one
 * run, or `null` when there is none in hand yet, and a sentence for an image
 * that could not be fetched. Nothing here says an image EXISTS — that is the
 * row's word (`has_before` / `has_after`), and this only carries the bytes it
 * managed to fetch for one.
 */
export type CreativePreviewProps = {
  srcFor: (runId: string, side: CreativeImageSide) => string | null;
  /** An image that could not be fetched, in words; the row's own line stays. */
  notice: string | null;
};

/** No image in hand for anything: the honest default, and what a test uses when it is not about images. */
export const CREATIVE_PREVIEW_NONE: CreativePreviewProps = { srcFor: () => null, notice: null };

// --------------------------------------------------------------- the receipt

/** What an action answered, read from the receipt the route returns. */
export type CreativeActionReceipt = {
  /** The run's step after the call (`verified`, `mismatch`, …), when the answer named it. */
  state: string | null;
  /** The comparison's bounded aggregate, when the answer measured one. */
  similarity: number | null;
  /** The defect the comparison named, when the answer named one. */
  defect: string | null;
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
 * other; the run row itself. Never invents a step: a 2xx with no state is a
 * receipt that named none, and is printed as one — above all it is never
 * "doğrulandı", which only a comparison that matched may say.
 */
export function parseCreativeReceipt(raw: unknown): CreativeActionReceipt {
  const body = record(raw) ?? {};
  const receipt = record(body.receipt) ?? body;
  const run = record(body.run) ?? record(receipt.run) ?? {};
  const comparison = record(body.comparison) ?? record(receipt.comparison) ?? {};
  return {
    state: first(str(body.state), str(body.status), str(receipt.state), str(receipt.status), str(run.state)),
    similarity: first(
      asSimilarity(body.similarity),
      asSimilarity(receipt.similarity),
      asSimilarity(comparison.similarity),
      asSimilarity(run.similarity),
    ),
    defect: first(str(body.defect), str(receipt.defect), str(comparison.defect), str(run.defect)),
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

/** The application refused, could not be driven, or the route is not there. `code` is the Cloud Core's own token when it sent one. */
export class CreativeActionError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.name = "CreativeActionError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** The code this client gives a 404 that is the ROUTE missing, not the run. */
export const CREATIVE_ROUTE_ABSENT = "route_absent";

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
 * A 404 is read twice, as the scene client reads it: a body naming a code
 * (or a sentence of the route's own) is the route refusing, and a bare
 * `Not Found` — or no body — is the route not being on this Cloud Core yet,
 * which is a different sentence (`henüz yok`) and is coded `route_absent` so
 * the panel says so.
 */
export async function toCreativeActionError(response: Response, path: string): Promise<CreativeActionError> {
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
    return new CreativeActionError(404, CREATIVE_ROUTE_ABSENT, `Bu Cloud Core sürümünde ${path} yok (HTTP 404).`);
  }
  return new CreativeActionError(response.status, code, detail ?? `HTTP ${response.status}`);
}

/**
 * The refusals in the owner's words, by the code the Cloud Core sends. Every
 * sentence says what did NOT happen. `dependency_unavailable` is the one
 * ADR-0093 measured (Photoshop and Illustrator are not installed on this
 * machine), and it is worded as an inability rather than as a fault;
 * `path_refused` is the documents roots' guard doing its job, which the
 * owner must never read as a picture that was lost; and `overwrite_refused`
 * is the promise that the original is never touched, kept out loud.
 */
export const CREATIVE_ACTION_REFUSAL_TR: Record<string, string> = {
  not_found: "Böyle bir görsel çalışması yok.",
  dependency_unavailable: "Uygulama sürülemedi; yapılamadı.",
  capability_missing: "Kurulu uygulamaların hiçbiri bunu yapamıyor; hiçbir şey çalıştırılmadı.",
  path_refused: "Dosya izin verilen köklerin dışında; hiçbir şey çalıştırılmadı.",
  overwrite_refused: "Özgün dosyanın üzerine yazılmaz; hiçbir şey değiştirilmedi.",
  unlicensed: "Uygulamanın lisansı yok; yapılamadı.",
  tool_busy: "Uygulama şu anda çalışıyor; ikinci bir istek gönderilmedi.",
  export_failed: "Dışa aktarılamadı; özgün dosya değişmedi.",
  compare_failed: "Karşılaştırma yapılamadı; doğrulanmadı.",
  postcondition_failed: "Çıktı istenenle uyuşmadı; doğrulanmadı.",
  validation_error: "Görsel planı geçerli değil; hiçbir şey çalıştırılmadı.",
  timeout: "Cloud Core zamanında yanıt vermedi; sonucu bilinmiyor.",
};

/** One line for the owner from whatever the call threw. */
export function creativeActionErrorText(err: unknown): string {
  if (err instanceof CreativeActionError) {
    if (err.code === CREATIVE_ROUTE_ABSENT) return `${err.detail} Yapılmadı.`;
    const known = err.code ? CREATIVE_ACTION_REFUSAL_TR[err.code] : undefined;
    if (known) return known;
    return err.code ? `${err.code}: ${err.detail}` : err.detail;
  }
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

// -------------------------------------------------------------- the client

/**
 * Ask the Cloud Core to export or compare one run. The body is empty: which
 * provider runs, with which fixed driver, under which root and against which
 * reference is the Cloud Core's and the device's to decide (ADR-0093
 * decisions 1, 5, 6) — this page adds nothing it could, and above all no
 * path, no text to draw and no script of its own.
 */
export async function creativeAction(runId: string, action: CreativeAction): Promise<CreativeActionReceipt> {
  const path = creativeActionPath(runId, action);
  const response = await apiFetch(path, { method: "POST" });
  if (!response.ok) throw await toCreativeActionError(response, path);
  return parseCreativeReceipt(await readBody(response));
}

/**
 * The two calls the chips can make, as an object so a test can hand the
 * panel a double and prove each press makes exactly one call, with which id
 * — without a network and without this file's `apiFetch`.
 */
export type CreativeClient = {
  export: (runId: string) => Promise<CreativeActionReceipt>;
  compare: (runId: string) => Promise<CreativeActionReceipt>;
};

/** The real client: the two POSTs above, through the owner session. */
export const creativeClient: CreativeClient = {
  export: (runId) => creativeAction(runId, "export"),
  compare: (runId) => creativeAction(runId, "compare"),
};

// ---------------------------------------------------------- control state

/** The one call in flight. There is never more than one: the Cloud Core is asked one thing at a time. */
export type CreativeBusy = { action: CreativeAction; id: string };

/** What the last call answered, in the owner's words, with the run it was about. */
export type CreativeActionOutcome = {
  action: CreativeAction;
  id: string;
  /** True when the route answered 2xx. NOT "doğrulandı": the receipt's state says that, in `text`. */
  ok: boolean;
  text: string;
  at: number;
};

export type CreativeControlState = {
  busy: CreativeBusy | null;
  outcome: CreativeActionOutcome | null;
};

export const CREATIVE_CONTROL_IDLE: CreativeControlState = { busy: null, outcome: null };

/** What the panel is handed: the state, and the two things a click may ask for. */
export type CreativeControlProps = {
  busy: CreativeBusy | null;
  outcome: CreativeActionOutcome | null;
  onExport: (runId: string) => void;
  onCompare: (runId: string) => void;
};
