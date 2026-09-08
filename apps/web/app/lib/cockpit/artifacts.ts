"use client";

/**
 * The Cockpit's client for the Artifact Factory (M22 spec §4).
 *
 * Three routes and nothing else. One GET lists what exists —
 * `/v1/artifacts`, M13's list, whose render rows the factory extends with a
 * validation `state` (`valid` | `invalid`) and its report. One GET fetches a
 * render's bytes — `/v1/artifacts/{id}/renders/{fmt}`, M13's owner-session-
 * gated download, which this page reaches exactly as the M13 inbox does: the
 * bytes through the session, then a blob URL handed to the browser, because
 * a plain `<a href>` carries no bearer and 401s. One POST asks the Cloud
 * Core to open an artifact on the owner's machine — `/v1/artifacts/{id}/open`
 * — which is the Cloud Core's own `file.fetch` + `file.open` sequence
 * (ADR-0085 §4: its own origin, owner-session-signed, the hash checked before
 * the file is kept). This client decides none of that. It cannot render,
 * cannot validate, cannot reach the device; it can only ask, and print the
 * answer.
 *
 * The Cloud Core half is built on a parallel track, so the response shapes
 * below are the spec's columns read defensively: a field the route does not
 * send is `null` and is rendered as "not reported", never filled in. A 404
 * on the open route is "henüz yok", which is the truthful word until it lands.
 */

import { API_BASE, UnauthorizedError, apiFetch } from "../session";
import { type Loaded, load } from "./api";

// ---------------------------------------------------------------- the routes

export const ARTIFACTS_PATH = "/v1/artifacts";

/** M13's render download: the format is a path segment, never a query. */
export function artifactRenderPath(artifactId: string, format: string): string {
  return `/v1/artifacts/${encodeURIComponent(artifactId)}/renders/${encodeURIComponent(format)}`;
}

/** The render's URL as the browser would address it: the API's origin and the gated path. */
export function artifactRenderUrl(artifactId: string, format: string): string {
  return `${API_BASE}${artifactRenderPath(artifactId, format)}`;
}

export function artifactOpenPath(artifactId: string): string {
  return `/v1/artifacts/${encodeURIComponent(artifactId)}/open`;
}

// ------------------------------------------------------------------ the rows

/**
 * One `artifact_renders` row as the list route describes it (M13's four
 * fields, plus M22's validation state and the ref that failed), every field
 * verbatim or `null`.
 */
export type ArtifactRender = {
  format: string;
  mime_type: string | null;
  size_bytes: number | null;
  content_hash: string | null;
  /**
   * `valid` | `invalid` as the row says (M22 spec §4), or `null` when the
   * route sent no state at all — an M13 render nobody validated. `null` is
   * rendered as "doğrulama bildirilmedi", never as either verdict.
   */
  state: string | null;
  /** The first ref the independent parser could not find, when the row carried one. */
  failing_ref: string | null;
};

/** One artifact as the list route lists it, every field verbatim or `null`. */
export type ArtifactRow = {
  artifact_id: string;
  title: string | null;
  /** `document` | `spreadsheet` | `presentation` | `dataset` | `page`, or whatever the row says. */
  kind: string | null;
  /** The artifact's own state (`READY`, …), as the row says. */
  state: string | null;
  created_at: string | null;
  updated_at: string | null;
  renders: ArtifactRender[];
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/** A size or count the row sent: a finite, non-negative number, or nothing. */
function size(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
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
 * The ref that failed, from the row itself or from the validation report
 * beside it: a `failing_ref` the route named outright, else the report's
 * own `failing_ref`, else the `ref` of the first element the report marked
 * `ok: false` — the report lists every element as `{ref, expected, found,
 * ok}` (spec §3). Nothing here can produce a ref the report did not carry.
 */
function failingRefOf(o: Record<string, unknown>): string | null {
  const direct = str(o.failing_ref);
  if (direct) return direct;
  const report = o.validation ?? o.validation_json ?? o.validation_report;
  if (!report || typeof report !== "object") return null;
  const r = report as Record<string, unknown>;
  const named = str(r.failing_ref);
  if (named) return named;
  const elements = [r.elements, r.items, r.checks, r.results].find(Array.isArray) as unknown[] | undefined;
  if (!elements) return null;
  for (const item of elements) {
    if (!item || typeof item !== "object") continue;
    const element = item as Record<string, unknown>;
    if (element.ok !== false) continue;
    const ref = str(element.ref);
    if (ref) return ref;
  }
  return null;
}

/** One render from a raw row; `null` for a row with no format, which is not a render. */
export function parseRender(raw: unknown): ArtifactRender | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const format = str(o.format);
  if (format === null) return null;
  return {
    format,
    mime_type: str(o.mime_type),
    size_bytes: size(o.size_bytes),
    content_hash: str(o.content_hash),
    state: str(o.state) ?? str(o.validation_state) ?? str(o.verdict),
    failing_ref: failingRefOf(o),
  };
}

/** One artifact from a raw row; `null` for a row with no id, which is not an artifact. */
export function parseArtifact(raw: unknown): ArtifactRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.artifact_id) ?? str(o.id);
  if (id === null) return null;
  return {
    artifact_id: id,
    title: str(o.title),
    kind: str(o.kind),
    state: str(o.state),
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
    renders: listAt(o.available_renders ?? o.renders, []).map(parseRender).filter(isPresent),
  };
}

export const fetchArtifacts = (): Promise<Loaded<ArtifactRow[]>> =>
  load<ArtifactRow[]>(ARTIFACTS_PATH, (raw) => listAt(raw, ["artifacts", "items"]).map(parseArtifact).filter(isPresent));

// ------------------------------------------------------------ the download

/**
 * The bytes of one render, through the owner session (M13's pattern): the
 * route answers `Content-Disposition: attachment` and `X-Content-Hash`, and
 * a non-2xx is an error to show, never an empty file.
 */
export async function fetchRenderBlob(artifactId: string, format: string): Promise<Blob> {
  const response = await apiFetch(artifactRenderPath(artifactId, format));
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.blob();
}

/** How long the blob URL handed to the browser stays valid before it is revoked. */
export const RENDER_URL_REVOKE_MS = 60_000;

/** The browser port the download hands the blob to: a new tab, with no referrer. */
export function openInNewTab(url: string): void {
  window.open(url, "_blank", "noreferrer");
}

export type DownloadPorts = {
  open: (url: string) => void;
  revokeAfterMs?: number;
};

/**
 * Fetch one render through the session and hand the browser a blob URL —
 * exactly what the M13 inbox does — then revoke the URL once the browser
 * has had its minute. Pure over its `open` port so a test can prove the
 * URL handed over is a blob of the bytes the route answered with.
 */
export async function downloadRender(
  artifactId: string,
  format: string,
  ports: DownloadPorts = { open: openInNewTab },
): Promise<void> {
  const blob = await fetchRenderBlob(artifactId, format);
  const url = URL.createObjectURL(blob);
  try {
    ports.open(url);
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), ports.revokeAfterMs ?? RENDER_URL_REVOKE_MS);
  }
}

// ----------------------------------------------------------------- the open

/** What an open answered, read from the receipt the route returns. */
export type ArtifactOpenReceipt = {
  /** The request's state after the call (`opened`, `fetched`, …), when the answer named it. */
  state: string | null;
  /** The receipt's own sentence, when it had one. */
  summary: string | null;
  receiptId: string | null;
  /** The window the companion OBSERVED after opening, when the receipt named it. */
  windowTitle: string | null;
};

/**
 * The receipt, read from either shape the route may answer with: the
 * receipt as the body, or the body carrying `receipt` and the observation
 * beside it. Never invents a state: a 2xx with no state is a receipt that
 * named none, and is printed as one.
 */
export function parseOpenReceipt(raw: unknown): ArtifactOpenReceipt {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const receipt = body.receipt && typeof body.receipt === "object" ? (body.receipt as Record<string, unknown>) : body;
  const observed =
    body.observed && typeof body.observed === "object"
      ? (body.observed as Record<string, unknown>)
      : receipt.observed && typeof receipt.observed === "object"
        ? (receipt.observed as Record<string, unknown>)
        : null;
  return {
    state: str(body.state) ?? str(body.status) ?? str(receipt.state) ?? str(receipt.status),
    summary:
      str(receipt.factual_summary) ?? str(receipt.summary) ?? str(receipt.speech) ?? str(body.message) ?? str(body.speech),
    receiptId: str(receipt.receipt_id) ?? str(body.receipt_id) ?? str(receipt.id),
    windowTitle:
      str(body.window_title) ?? str(receipt.window_title) ?? (observed ? str(observed.window_title) ?? str(observed.title) : null),
  };
}

/** The gate refused, the device could not, or the route is not there. `code` is the Cloud Core's own token when it sent one. */
export class ArtifactOpenError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.name = "ArtifactOpenError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** The code this client gives a 404 that is the ROUTE missing, not the artifact. */
export const OPEN_ROUTE_ABSENT = "route_absent";

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
 * A 404 is read twice: a body naming a code (or FastAPI's `unknown
 * artifact`) is the route refusing, and a bare `Not Found` — or no body —
 * is the route not being on this Cloud Core yet, which is a different
 * sentence (`henüz yok`) and is coded `route_absent` so the panel says so.
 */
export async function toArtifactOpenError(response: Response, path: string): Promise<ArtifactOpenError> {
  const body = await readBody(response);
  let code: string | null = null;
  let detail: string | null = null;
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const d = record.detail;
    if (typeof d === "string") detail = d;
    else if (d && typeof d === "object") {
      const inner = d as Record<string, unknown>;
      code = str(inner.code) ?? str(inner.error_class) ?? str(inner.reason);
      detail = str(inner.message) ?? str(inner.detail);
    }
    code ??= str(record.code) ?? str(record.error_class) ?? str(record.reason);
    detail ??= str(record.message);
  }
  if (response.status === 404 && code === null && (detail === null || detail === "Not Found")) {
    return new ArtifactOpenError(404, OPEN_ROUTE_ABSENT, `Bu Cloud Core sürümünde ${path} yok (HTTP 404).`);
  }
  return new ArtifactOpenError(response.status, code, detail ?? `HTTP ${response.status}`);
}

/**
 * The refusals in the owner's words, by the code the Cloud Core sends.
 * Every sentence says what did NOT happen: a refusal to open an invalid
 * render is ADR-0085 §3 working, and the owner must never read it as a
 * file that was lost.
 */
export const ARTIFACT_OPEN_REFUSAL_TR: Record<string, string> = {
  capability_missing: "Cihazdaki ajan bu sürümde dosya getiremiyor (file.fetch yok). Açılmadı.",
  device_offline: "Cihaz çevrimdışı; açılmadı.",
  no_device: "Kayıtlı bir cihaz yok; açılmadı.",
  operator_disabled: "Operatör bu cihazda kapalı; açılmadı.",
  not_found: "Böyle bir çıktı yok.",
  no_valid_render: "Doğrulanmış bir çıktı yok; doğrulanmamış bir çıktı açılmaz.",
  invalid_render: "Bu çıktı doğrulanamadı; doğrulanmamış bir çıktı açılmaz.",
  hash_mismatch: "İndirilen dosyanın özeti kayıttakiyle eşleşmedi; dosya tutulmadı, açılmadı.",
  too_large: "Dosya 50 MiB sınırını aşıyor; açılmadı.",
  origin_refused: "Dosya yalnızca Cloud Core'un kendi adresinden getirilir; açılmadı.",
  fetch_failed: "Dosya cihaza getirilemedi; açılmadı.",
  open_failed: "Dosya cihaza getirildi ancak açılamadı.",
};

/** One line for the owner from whatever the call threw. */
export function artifactOpenErrorText(err: unknown): string {
  if (err instanceof ArtifactOpenError) {
    if (err.code === OPEN_ROUTE_ABSENT) return `${err.detail} Açılmadı.`;
    const known = err.code ? ARTIFACT_OPEN_REFUSAL_TR[err.code] : undefined;
    if (known) return known;
    return err.code ? `${err.code}: ${err.detail}` : err.detail;
  }
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

/**
 * Ask the Cloud Core to fetch and open one artifact on the owner's machine.
 * The body is empty: which render, from where, checked how, is the Cloud
 * Core's to decide (ADR-0085 §4) — this page adds nothing it could.
 */
export async function openArtifact(artifactId: string): Promise<ArtifactOpenReceipt> {
  const path = artifactOpenPath(artifactId);
  const response = await apiFetch(path, { method: "POST" });
  if (!response.ok) throw await toArtifactOpenError(response, path);
  return parseOpenReceipt(await readBody(response));
}

/**
 * The one call "Aç" can make, as an object so a test can hand the panel a
 * double and prove the call was made exactly once, with which id — without
 * a network and without this file's `apiFetch`.
 */
export type ArtifactClient = {
  open: (artifactId: string) => Promise<ArtifactOpenReceipt>;
};

/** The real client: the one POST above, through the owner session. */
export const artifactClient: ArtifactClient = { open: openArtifact };

// ----------------------------------------------------------- open state

/** What the last open answered, in the owner's words, with the artifact it was about. */
export type ArtifactOpenOutcome = {
  artifactId: string;
  /** True when the route answered 2xx. NOT "opened": the receipt's state says that, in `text`. */
  ok: boolean;
  text: string;
  at: number;
};

export type ArtifactOpenState = {
  /** The artifact whose open is in flight. Never more than one: the device is asked one thing at a time. */
  busy: string | null;
  outcome: ArtifactOpenOutcome | null;
};

export const ARTIFACT_OPEN_IDLE: ArtifactOpenState = { busy: null, outcome: null };

/** What the panel is handed: the state, and the one thing a click may ask for. */
export type ArtifactOpenProps = {
  busy: string | null;
  outcome: ArtifactOpenOutcome | null;
  onOpen: (artifactId: string) => void;
};
