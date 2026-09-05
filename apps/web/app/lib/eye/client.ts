"use client";

/**
 * The Active Eye's REST client: `POST /v1/presence/observations` and the
 * `eye/enable` / `eye/disable` owner actions
 * (M18_HOLOGRAPHIC_CORE_SPEC.md §2; `services/api/app/presence/routes.py`).
 *
 * Every call goes through `apiFetch`, like every other client in this shell,
 * so the owner session is attached and a 401 clears it in exactly one place.
 * This file sends exactly the seven fields `EyeObservation` carries — nothing
 * more is ever added to the request body here, which matters because the
 * server-side boundary (`app/presence/observations.py`) refuses an unknown
 * key outright rather than dropping it.
 */

import { UnauthorizedError, apiFetch } from "../session";
import type { EyeObservation } from "./types";

export class EyeApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "EyeApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* no JSON body, or not the shape we expect */
  }
  return `API hatası (HTTP ${response.status})`;
}

/**
 * Posts one observation. Resolves to `"posted"`, `"rejected"` (a 422 —
 * malformed or imagery-shaped, which should never happen from this client but
 * is not treated as a network failure if it does) or `"eye_disabled"` (a 409
 * — the server-side flag was turned off, possibly by another device, since
 * the last time this client checked).
 *
 * `signal` lets the caller abort a request that is still in flight when local
 * perception is stopped — part of `perception.ts`'s "impossible for an
 * in-flight sample to post after disable" guarantee. Aborting is
 * best-effort (bytes already on the wire cannot be recalled); the server's
 * own fresh-read `is_eye_enabled` check is the authoritative backstop
 * (`app/presence/service.py`, `M18_THREAT_MODEL.md` §3).
 */
export async function postObservation(
  observation: EyeObservation,
  options: { signal?: AbortSignal } = {},
): Promise<{ status: "posted" } | { status: "rejected"; detail: string } | { status: "eye_disabled" }> {
  // Network errors (including an aborted request — the caller stopped us)
  // propagate as-is; the caller decides what an abort means to it.
  const response = await apiFetch("/v1/presence/observations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(observation),
    signal: options.signal,
  });
  if (response.status === 409) return { status: "eye_disabled" };
  if (response.status === 422) return { status: "rejected", detail: await readDetail(response) };
  if (!response.ok) throw new EyeApiError(response.status, await readDetail(response));
  return { status: "posted" };
}

async function postEyeAction(path: string, reason: string): Promise<void> {
  const response = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(reason ? { reason } : {}),
  });
  if (!response.ok) throw new EyeApiError(response.status, await readDetail(response));
}

/** Owner action: resume perception server-side (spec §2's enable path). */
export const enableEye = (reason = "") => postEyeAction("/v1/presence/eye/enable", reason);

/**
 * Owner action: stop perception immediately, durably. Callers in this app
 * pair this with `PerceptionSession#stop()` and call this FIRST — see
 * `useActivePerception.ts` — so that even a sample already in flight when the
 * local stop happens is refused server-side the moment this resolves.
 */
export const disableEye = (reason = "") => postEyeAction("/v1/presence/eye/disable", reason);

/** Owner-facing text for anything thrown above. */
export function explainEyeError(err: unknown): string {
  if (err instanceof UnauthorizedError) return "Sahip oturumu reddedildi.";
  if (err instanceof EyeApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return String(err);
}
