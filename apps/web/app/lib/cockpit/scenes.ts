"use client";

/**
 * The Cockpit's client for 3D creation (M25 spec §6).
 *
 * Four routes and nothing else. One GET lists what exists — `/v1/scenes`,
 * the scene rows: which tool each scene was made in, the scene's name, the
 * step it is on, how many objects the last INSPECTION read back and what
 * they were called, the object a comparison found wrong, and whether a
 * render exists. One GET fetches that render — `/v1/scenes/{id}/render`,
 * owner-session gated, the PNG the driver wrote and an independent reader
 * validated before it was stored (ADR-0088 §7). Two POSTs ask the Cloud
 * Core to act on one scene — `/v1/scenes/{id}/render`, `/v1/scenes/{id}/inspect`
 * — which are the Cloud Core's own `scene.render` / `scene.inspect`
 * capabilities on the device: a bounded Job Object child running
 * `blender.exe -b … --python <the shipped driver>` or `Unity.exe -batchmode
 * … -executeMethod PagentOS.SceneDriver.Run`, under the 3D root and nowhere
 * else. This client decides none of that. It cannot open an editor, write a
 * plan, render a pixel or reach the device; it can only ask, and print the
 * answer.
 *
 * The Cloud Core half is built on a parallel track (ADR-0088 §8), so the
 * response shapes below are the spec's columns read defensively: a field
 * the route does not send is `null` and is rendered as "not reported",
 * never filled in. A 404 on an action route is "henüz yok", which is the
 * truthful word until it lands.
 */

import { API_BASE, UnauthorizedError, apiFetch } from "../session";
import { MAX_SCENE_OBJECTS, asObjectCount } from "../uistate/contract";
import { type Loaded, load } from "./api";

// ---------------------------------------------------------------- the routes

export const SCENES_PATH = "/v1/scenes";

/** The two things the Cockpit may ask for, in the order the chips are drawn. */
export const SCENE_ACTIONS = ["render", "inspect"] as const;

export type SceneAction = (typeof SCENE_ACTIONS)[number];

/** The action's route: the id is a path segment, never a query; the action is the spec's word. */
export function sceneActionPath(sceneId: string, action: SceneAction): string {
  return `/v1/scenes/${encodeURIComponent(sceneId)}/${action}`;
}

/**
 * The last render's route (a GET on the same path the render POST uses).
 * Owner-session gated like M13's, so it is FETCHED with the session rather
 * than handed to an `<img src>` — a bearer API answers a plain image
 * request with a 401, and a broken image would read as "no render".
 */
export function sceneRenderPath(sceneId: string): string {
  return `/v1/scenes/${encodeURIComponent(sceneId)}/render`;
}

/** The render's URL as the browser would address it — printed, never fetched by a link. */
export function sceneRenderUrl(sceneId: string): string {
  return `${API_BASE}${sceneRenderPath(sceneId)}`;
}

/** The list's URL as the browser would address it — for the absent notice, never fetched by a link. */
export function scenesUrl(): string {
  return `${API_BASE}${SCENES_PATH}`;
}

// ------------------------------------------------------------------ the rows

/**
 * One scene row as the list route describes it (M25 spec §2, §4), every
 * field verbatim or `null`. `objects` is the count the last inspection
 * read; `object_names` are the names it listed, bounded; `mismatch` is the
 * object the comparison named when the read-back did not match.
 */
export type SceneRow = {
  scene_id: string;
  /** `blender` | `unity`, or whatever the row says. */
  tool: string | null;
  /** The scene the plan names (`Kure`), as the row says. */
  scene: string | null;
  /** The project the scene lives in under the 3D root, when the row named it. */
  project: string | null;
  /** The step the scene is on, as the row says. */
  state: string | null;
  /** How many objects the last inspection read back, when it counted. `0` is an answer. */
  objects: number | null;
  /** What the last inspection called them, in the order it listed them. Empty when it named none. */
  object_names: string[];
  /** The object the comparison found wrong, when it named one. */
  mismatch: string | null;
  /** True when the row says a render exists to fetch. Never inferred from anything else. */
  has_render: boolean;
  /** The render's sha256 as the row gave it, when it did. */
  render_sha256: string | null;
  /** The render's size in bytes as the row gave it, when it did. */
  render_bytes: number | null;
  /** On a failure or an unavailable tool: the taxonomy class, as the row says. */
  error_class: string | null;
  /** On a failure or an unavailable tool: the run's own sentence (the licensing client's words, for Unity). */
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

/**
 * The object names one inspection listed: non-empty strings only, bounded,
 * in the order the route gave them. An entry that is not a name is dropped
 * rather than defaulted — the same posture the bus takes with `refs`.
 */
export function parseSceneObjectNames(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const names: string[] = [];
  for (const item of raw) {
    if (names.length >= MAX_SCENE_OBJECTS) break;
    const name = typeof item === "string" ? str(item) : str((item as Record<string, unknown> | null)?.name);
    if (name === null) continue;
    names.push(name);
  }
  return names;
}

/**
 * Whether a render exists, from what the row actually said: the flag when
 * it sent one, else the presence of a stored render's identity (its path or
 * its sha256). Never from the state — a scene that once said `rendering`
 * is not a scene with a render on disk, and offering the owner an image
 * that is not there would be this page inventing one.
 */
export function parseHasRender(o: Record<string, unknown>): boolean {
  const declared = flag(o.has_render) ?? flag(o.render_available);
  if (declared !== null) return declared;
  return str(o.render_path) !== null || str(o.render_sha256) !== null;
}

/** One scene from a raw row; `null` for a row with no id, which is not a scene. */
export function parseSceneRow(raw: unknown): SceneRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.scene_id) ?? str(o.id);
  if (id === null) return null;
  return {
    scene_id: id,
    tool: str(o.tool),
    scene: str(o.scene) ?? str(o.name),
    project: str(o.project),
    state: str(o.state),
    objects: asObjectCount(o.objects ?? o.object_count),
    object_names: parseSceneObjectNames(o.object_names ?? o.objects_list ?? o.inspection_objects),
    mismatch: str(o.mismatch) ?? str(o.mismatch_object),
    has_render: parseHasRender(o),
    render_sha256: str(o.render_sha256),
    render_bytes: asObjectCount(o.render_bytes ?? o.render_size),
    error_class: str(o.error_class),
    error_message: str(o.error_message),
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

export const fetchScenes = (): Promise<Loaded<SceneRow[]>> =>
  load<SceneRow[]>(SCENES_PATH, (raw) => listAt(raw, ["scenes", "items"]).map(parseSceneRow).filter(isPresent));

// -------------------------------------------------------------- the render

/**
 * Fetch one scene's last render through the owner session.
 *
 * The route is gated exactly as M13's render download is, so the bytes are
 * fetched with the session's bearer and handed to the page as a blob —
 * never addressed by a bare `<img src>`, which would 401 and draw a broken
 * image where the owner would read "no render".
 */
export async function fetchSceneRenderBlob(sceneId: string): Promise<Blob> {
  const response = await apiFetch(sceneRenderPath(sceneId));
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.blob();
}

/**
 * What the panel is handed for the images: the source for one scene, or
 * `null` when there is none in hand yet, and a sentence for a render that
 * could not be fetched. Nothing here says a render EXISTS — that is the
 * row's word (`has_render`), and this only carries the bytes it managed
 * to fetch for one.
 */
export type ScenePreviewProps = {
  srcFor: (sceneId: string) => string | null;
  /** A render that could not be fetched, in words; the row's own line stays. */
  notice: string | null;
};

/** No image in hand for anything: the honest default, and what a test uses when it is not about images. */
export const SCENE_PREVIEW_NONE: ScenePreviewProps = { srcFor: () => null, notice: null };

// --------------------------------------------------------------- the receipt

/** What an action answered, read from the receipt the route returns. */
export type SceneActionReceipt = {
  /** The scene's step after the call (`verified`, `mismatch`, …), when the answer named it. */
  state: string | null;
  /** How many objects the inspection read back, when the answer counted them. */
  objects: number | null;
  /** The object the comparison found wrong, when the answer named one. */
  mismatch: string | null;
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
 * receipt as the body; the body carrying `receipt` and `scene` beside each
 * other; the scene row itself. Never invents a step: a 2xx with no state is
 * a receipt that named none, and is printed as one — above all it is never
 * "doğrulandı", which only an inspection that matched may say.
 */
export function parseSceneReceipt(raw: unknown): SceneActionReceipt {
  const body = record(raw) ?? {};
  const receipt = record(body.receipt) ?? body;
  const scene = record(body.scene) ?? record(receipt.scene) ?? {};
  const inspection = record(body.inspection) ?? record(receipt.inspection) ?? {};
  return {
    state: first(str(body.state), str(body.status), str(receipt.state), str(receipt.status), str(scene.state)),
    objects: first(
      asObjectCount(body.objects),
      asObjectCount(receipt.objects),
      asObjectCount(inspection.objects),
      asObjectCount(scene.objects),
      asObjectCount(scene.object_count),
    ),
    mismatch: first(str(body.mismatch), str(receipt.mismatch), str(scene.mismatch), str(scene.mismatch_object)),
    errorClass: first(str(body.error_class), str(receipt.error_class), str(scene.error_class)),
    summary: first(
      str(receipt.factual_summary),
      str(receipt.summary),
      str(receipt.speech),
      str(body.message),
      str(body.speech),
      str(scene.error_message),
    ),
    receiptId: first(str(receipt.receipt_id), str(body.receipt_id), str(receipt.id)),
  };
}

// --------------------------------------------------------------- the errors

/** The device refused, the tool could not, or the route is not there. `code` is the Cloud Core's own token when it sent one. */
export class SceneActionError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.name = "SceneActionError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** The code this client gives a 404 that is the ROUTE missing, not the scene. */
export const SCENE_ROUTE_ABSENT = "route_absent";

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
export async function toSceneActionError(response: Response, path: string): Promise<SceneActionError> {
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
    return new SceneActionError(404, SCENE_ROUTE_ABSENT, `Bu Cloud Core sürümünde ${path} yok (HTTP 404).`);
  }
  return new SceneActionError(response.status, code, detail ?? `HTTP ${response.status}`);
}

/**
 * The refusals in the owner's words, by the code the Cloud Core sends.
 * Every sentence says what did NOT happen. `dependency_unavailable` is the
 * one M25 measured (ADR-0088 §5: Unity exits 198 without an entitlement),
 * and it is worded as an inability rather than as a fault; `path_refused`
 * is the 3D root's guard doing its job, which the owner must never read as
 * a scene that was lost.
 */
export const SCENE_ACTION_REFUSAL_TR: Record<string, string> = {
  not_found: "Böyle bir sahne yok.",
  dependency_unavailable: "Araç sürülemedi; yapılamadı.",
  path_refused: "Sahne 3B kökünün dışında; hiçbir şey çalıştırılmadı.",
  tool_busy: "Araç şu anda çalışıyor; ikinci bir istek gönderilmedi.",
  render_failed: "Render alınamadı; sahne değişmedi.",
  inspect_failed: "Sahne okunamadı; doğrulanmadı.",
  postcondition_failed: "Geri okuma istenenle uyuşmadı; doğrulanmadı.",
  validation_error: "Sahne planı geçerli değil; hiçbir şey çalıştırılmadı.",
  timeout: "Cloud Core zamanında yanıt vermedi; sonucu bilinmiyor.",
};

/** One line for the owner from whatever the call threw. */
export function sceneActionErrorText(err: unknown): string {
  if (err instanceof SceneActionError) {
    if (err.code === SCENE_ROUTE_ABSENT) return `${err.detail} Yapılmadı.`;
    const known = err.code ? SCENE_ACTION_REFUSAL_TR[err.code] : undefined;
    if (known) return known;
    return err.code ? `${err.code}: ${err.detail}` : err.detail;
  }
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

// -------------------------------------------------------------- the client

/**
 * Ask the Cloud Core to render or inspect one scene. The body is empty:
 * which driver runs, with which argv, under which root and inside which job
 * object is the Cloud Core's and the device's to decide (ADR-0088 §3, §7) —
 * this page adds nothing it could, and above all no path and no script of
 * its own.
 */
export async function sceneAction(sceneId: string, action: SceneAction): Promise<SceneActionReceipt> {
  const path = sceneActionPath(sceneId, action);
  const response = await apiFetch(path, { method: "POST" });
  if (!response.ok) throw await toSceneActionError(response, path);
  return parseSceneReceipt(await readBody(response));
}

/**
 * The two calls the chips can make, as an object so a test can hand the
 * panel a double and prove each press makes exactly one call, with which id
 * — without a network and without this file's `apiFetch`.
 */
export type SceneClient = {
  render: (sceneId: string) => Promise<SceneActionReceipt>;
  inspect: (sceneId: string) => Promise<SceneActionReceipt>;
};

/** The real client: the two POSTs above, through the owner session. */
export const sceneClient: SceneClient = {
  render: (sceneId) => sceneAction(sceneId, "render"),
  inspect: (sceneId) => sceneAction(sceneId, "inspect"),
};

// ---------------------------------------------------------- control state

/** The one call in flight. There is never more than one: the Cloud Core is asked one thing at a time. */
export type SceneBusy = { action: SceneAction; id: string };

/** What the last call answered, in the owner's words, with the scene it was about. */
export type SceneActionOutcome = {
  action: SceneAction;
  id: string;
  /** True when the route answered 2xx. NOT "doğrulandı": the receipt's state says that, in `text`. */
  ok: boolean;
  text: string;
  at: number;
};

export type SceneControlState = {
  busy: SceneBusy | null;
  outcome: SceneActionOutcome | null;
};

export const SCENE_CONTROL_IDLE: SceneControlState = { busy: null, outcome: null };

/** What the panel is handed: the state, and the two things a click may ask for. */
export type SceneControlProps = {
  busy: SceneBusy | null;
  outcome: SceneActionOutcome | null;
  onRender: (sceneId: string) => void;
  onInspect: (sceneId: string) => void;
};
