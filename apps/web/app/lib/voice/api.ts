/**
 * Thin client for `/v1/voice/realtime/sessions/*` (spec §4). Every call goes
 * through the injected fetcher — in the shell that is `apiFetch` from
 * ../session (owner bearer attached, 401 handled once); in tests it is a
 * recording fake. Nothing here reads a credential or an audio byte.
 */

import type {
  ClientEvent,
  EventsResponse,
  SessionLegPayload,
  SessionState,
  ToolCallResponse,
} from "./contract";

export type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

export class VoiceApiError extends Error {
  constructor(
    readonly status: number,
    readonly path: string,
    readonly detail: unknown,
  ) {
    super(`${path}: HTTP ${status}`);
    this.name = "VoiceApiError";
  }

  /** 409: this owner session no longer holds the media leg — attach first. */
  get legMismatch(): boolean {
    return this.status === 409;
  }

  /** 410: the session is closed or expired; nothing to reattach to. */
  get gone(): boolean {
    return this.status === 410;
  }
}

export type CreateSessionBody = {
  client_kind?: string;
  transport?: string;
  language?: string;
  narration_session_id?: string;
  session_ttl_s?: number;
  /** ADR-0043: one of the provider's supported wire voices (the page offers marin | cedar). */
  voice?: string;
};

export type AttachBody = { client_kind?: string; transport?: string };

const BASE = "/v1/voice/realtime/sessions";

export class VoiceSessionApi {
  constructor(private readonly fetcher: Fetcher) {}

  private async call<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (init.body !== undefined) headers.set("Content-Type", "application/json");
    const response = await this.fetcher(path, { ...init, headers });
    if (!response.ok) {
      let detail: unknown = null;
      try {
        detail = await response.json();
      } catch {
        /* no body */
      }
      throw new VoiceApiError(response.status, path, detail);
    }
    return (await response.json()) as T;
  }

  create(body: CreateSessionBody = {}): Promise<SessionLegPayload> {
    return this.call<SessionLegPayload>(BASE, {
      method: "POST",
      body: JSON.stringify({ client_kind: "web", ...body }),
    });
  }

  state(sessionId: string): Promise<SessionState> {
    return this.call<SessionState>(`${BASE}/${sessionId}`);
  }

  toolCall(
    sessionId: string,
    call: { call_id: string; name: string; arguments: Record<string, unknown> },
  ): Promise<ToolCallResponse> {
    return this.call<ToolCallResponse>(`${BASE}/${sessionId}/tool-calls`, {
      method: "POST",
      body: JSON.stringify(call),
    });
  }

  events(sessionId: string, events: ClientEvent[]): Promise<EventsResponse> {
    return this.call<EventsResponse>(`${BASE}/${sessionId}/events`, {
      method: "POST",
      body: JSON.stringify({ events }),
    });
  }

  attach(sessionId: string, body: AttachBody = {}): Promise<SessionLegPayload> {
    return this.call<SessionLegPayload>(`${BASE}/${sessionId}/attach`, {
      method: "POST",
      body: JSON.stringify({ client_kind: "web", ...body }),
    });
  }

  close(sessionId: string, reason = "client_closed"): Promise<{ session_id: string; state: string }> {
    return this.call(`${BASE}/${sessionId}/close`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  }
}
