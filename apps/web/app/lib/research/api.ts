"use client";

/**
 * Research REST client (spec §4). Every call goes through `apiFetch`, so the
 * owner session is attached and a 401 clears it in exactly one place.
 *
 * Errors: the API answers non-2xx with a FastAPI-style body whose `detail` is
 * either a Turkish string or `{code, message|detail}`. Both are folded into a
 * `ResearchApiError` with a stable `code` (e.g. `no_capable_device` on 409) and
 * a Turkish `detail` ready for display.
 */

import { apiFetch } from "../session";
import { describeErrorDetail } from "../voice/api";
import {
  BROWSER_CAPABILITY,
  type DeviceInfo,
  type ResearchReport,
  type ResearchTaskDetail,
  type ResearchTaskSummary,
  type SelectionPreview,
  type StartResearchResponse,
  normaliseSelection,
} from "./model";

export class ResearchApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.name = "ResearchApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export const NO_CAPABLE_DEVICE = "no_capable_device";

/** Owner-facing explanation for the one failure the owner can act on. */
export const NO_CAPABLE_DEVICE_HINT =
  "Bu görev için Chrome'u (browser.chrome) duyuran çevrimiçi bir cihaz bulunamadı. " +
  "Windows ajanının çalıştığından ve tarayıcı bileşeninin kurulu olduğundan emin ol; " +
  "ya da listeden açıkça bir cihaz seç.";

type ErrorBody = {
  detail?: unknown;
  error_class?: unknown;
  code?: unknown;
  message?: unknown;
};

async function readErrorBody(response: Response): Promise<ErrorBody> {
  try {
    const text = await response.text();
    if (!text) return {};
    return JSON.parse(text) as ErrorBody;
  } catch {
    return {};
  }
}

/** Build the typed error from a non-2xx response; never throws itself. */
export async function toApiError(response: Response): Promise<ResearchApiError> {
  const body = await readErrorBody(response);
  let code: string | null = null;
  let detail: string | null = null;

  const d = body.detail;
  let validationShaped = false;
  if (typeof d === "string") {
    detail = d;
  } else if (Array.isArray(d)) {
    // FastAPI's validation shape (`[{loc, msg, type}]`): the voice client already
    // renders it as `alan … · neden …` lines without echoing values; reuse it so a
    // rejected field is named instead of degrading to "API hatası".
    validationShaped = true;
    const lines = describeErrorDetail(d);
    if (lines.length) detail = lines.join(" | ");
    code = "validation_error";
  } else if (d && typeof d === "object") {
    const obj = d as Record<string, unknown>;
    if (typeof obj.code === "string") code = obj.code;
    if (typeof obj.error_class === "string") code = obj.error_class;
    if (typeof obj.detail === "string") detail = obj.detail;
    else if (typeof obj.message === "string") detail = obj.message;
  }
  if (!code && typeof body.error_class === "string") code = body.error_class;
  if (!code && typeof body.code === "string") code = body.code;
  if (!detail && typeof body.message === "string") detail = body.message;

  // The spec pins 409 to no_capable_device even when the body only carries
  // the Turkish sentence.
  if (response.status === 409 && !code && !validationShaped) code = NO_CAPABLE_DEVICE;
  if (detail && !code && detail.includes(NO_CAPABLE_DEVICE)) code = NO_CAPABLE_DEVICE;

  return new ResearchApiError(
    response.status,
    code,
    detail ?? `API hatası (HTTP ${response.status})`,
  );
}

/** Text to show the owner for any error raised by this module. */
export function explainError(err: unknown): string {
  if (err instanceof ResearchApiError) {
    if (err.code === NO_CAPABLE_DEVICE) {
      const own = err.detail.trim();
      return own && !own.startsWith("API hatası")
        ? `${own} ${NO_CAPABLE_DEVICE_HINT}`
        : NO_CAPABLE_DEVICE_HINT;
    }
    return err.detail;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

async function okJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

// ------------------------------------------------------------------- devices

export async function listDevices(): Promise<DeviceInfo[]> {
  const data = await okJson<{ devices?: DeviceInfo[] }>(await apiFetch("/v1/devices"));
  return data.devices ?? [];
}

/**
 * "Which device would run this?" — the same selection the research start
 * performs, without starting anything. A 409 is a normal answer here.
 */
export async function previewSelection(target: string | null): Promise<SelectionPreview> {
  const body: Record<string, unknown> = { capability: BROWSER_CAPABILITY };
  if (target) body.target = target;
  const response = await apiFetch("/v1/devices/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.status === 409 || response.status === 404) {
    const err = await toApiError(response);
    return { kind: "none", detail: explainError(err) };
  }
  return normaliseSelection(await okJson<unknown>(response));
}

// ------------------------------------------------------------------ research

export type StartResearchInput = {
  input: string;
  target_device?: string | null;
  recency_days?: number;
  max_sources?: number;
  synthesis?: "auto" | "deterministic";
  /** Owner-handoff mode (spec §5a): the /research page always sends `true`. */
  interactive?: boolean;
  interactive_wait_s?: number;
};

export async function startResearch(req: StartResearchInput): Promise<StartResearchResponse> {
  const body: Record<string, unknown> = { input: req.input };
  if (req.target_device) body.target_device = req.target_device;
  if (req.recency_days != null) body.recency_days = req.recency_days;
  if (req.max_sources != null) body.max_sources = req.max_sources;
  if (req.synthesis) body.synthesis = req.synthesis;
  if (req.interactive != null) body.interactive = req.interactive;
  if (req.interactive_wait_s != null) body.interactive_wait_s = req.interactive_wait_s;
  const response = await apiFetch("/v1/research", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return okJson<StartResearchResponse>(response);
}

export async function listResearchTasks(): Promise<ResearchTaskSummary[]> {
  const data = await okJson<{ tasks?: ResearchTaskSummary[] }>(await apiFetch("/v1/research"));
  return data.tasks ?? [];
}

export async function getResearchTask(taskId: string): Promise<ResearchTaskDetail> {
  return okJson<ResearchTaskDetail>(
    await apiFetch(`/v1/research/${encodeURIComponent(taskId)}`),
  );
}

export async function cancelResearch(taskId: string): Promise<void> {
  const response = await apiFetch(`/v1/research/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
  if (!response.ok) throw await toApiError(response);
}

/** The raw report body, as text, for "Rapor JSON'unu kopyala". */
export async function fetchReportJson(taskId: string): Promise<string> {
  const response = await apiFetch(`/v1/research/${encodeURIComponent(taskId)}/report`);
  if (!response.ok) throw await toApiError(response);
  const text = await response.text();
  try {
    return JSON.stringify(JSON.parse(text) as ResearchReport, null, 2);
  } catch {
    return text;
  }
}
