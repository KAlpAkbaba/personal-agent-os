"use client";

/**
 * Reading the UI-state surface. Two GETs and nothing else.
 *
 * There is no write path here and there must never be one: ADR-0052 §2 makes
 * the absence of a write endpoint the reason a client cannot claim a state it
 * is not in. A "publish" helper in this file would be the first step to a
 * renderer that animates itself.
 *
 * Everything goes through `apiFetch`, so the owner bearer session is attached
 * and a 401 clears it in exactly one place.
 */

import { UnauthorizedError, apiFetch } from "../session";
import {
  type UiStateContract,
  type UiStateResponse,
  SERVER_TAIL_SIZE,
  parseResponse,
} from "./contract";

export { UnauthorizedError };

/** Thrown for any non-2xx that is not a 401. */
export class UiStateError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "UiStateError";
    this.status = status;
  }
}

/**
 * The current state plus everything after `afterSequence`.
 *
 * `limit` is capped at the server's `TAIL_SIZE`; asking for more is a 422, and
 * a renderer that hits one on every poll would look like an outage.
 */
export async function fetchUiState(afterSequence = 0): Promise<UiStateResponse> {
  const params = new URLSearchParams({
    after_sequence: String(Math.max(0, Math.floor(afterSequence))),
    limit: String(SERVER_TAIL_SIZE),
  });
  const response = await apiFetch(`/v1/ui/state?${params.toString()}`);
  if (!response.ok) {
    throw new UiStateError(response.status, `Durum alınamadı (HTTP ${response.status})`);
  }
  const parsed = parseResponse(await response.json());
  if (!parsed) throw new UiStateError(200, "Durum yanıtı okunamadı");
  return parsed;
}

/**
 * The vocabulary the API is actually serving.
 *
 * Fetched once on mount so the Core can say "the API speaks a contract this
 * build does not know" instead of silently drawing unknown states as if it
 * understood them.
 */
export async function fetchUiStateContract(): Promise<UiStateContract> {
  const response = await apiFetch("/v1/ui/state/contract");
  if (!response.ok) {
    throw new UiStateError(response.status, `Sözleşme alınamadı (HTTP ${response.status})`);
  }
  return (await response.json()) as UiStateContract;
}

/** Owner-facing text for anything thrown above. */
export function explainUiStateError(err: unknown): string {
  if (err instanceof UnauthorizedError) return "Sahip oturumu reddedildi.";
  if (err instanceof UiStateError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
