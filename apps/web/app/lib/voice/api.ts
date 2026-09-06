/**
 * Thin client for `/v1/voice/realtime/sessions/*` (spec §4). Every call goes
 * through the injected fetcher — in the shell that is `apiFetch` from
 * ../session (owner bearer attached, 401 handled once); in tests it is a
 * recording fake. Nothing here reads a credential or an audio byte.
 *
 * ADR-0045: the client also (a) asks the server which contract version it
 * speaks (`GET /v1/voice/realtime/contract`), (b) keeps a scrubbed structured
 * log of its last outgoing requests for the diagnostics view, and (c) turns a
 * 422 body — FastAPI's validation list or the API's own VoiceError shape —
 * into field/reason lines instead of a bare status.
 */

import type {
  ClientEvent,
  EventsResponse,
  SessionLegPayload,
  SessionState,
  ToolCallResponse,
} from "./contract";
import { isForbiddenKey } from "./contract";
import { CONTRACT_PATH, type ContractProbe, isContractDocument } from "./session-contract";

export type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

// ------------------------------------------------------------ error detail

/** One owner-readable line of an error body: field names and reasons, never a request value. */
export type ErrorLine = string;

type FastApiItem = { loc?: unknown; msg?: unknown; type?: unknown };

/**
 * Parse an error body into lines. Supported shapes:
 * - FastAPI validation: `{detail: [{loc: ["body","voice"], msg, type}]}` →
 *   `alan: body.voice · neden: extra_forbidden · Extra inputs are not permitted`
 * - the API's VoiceError: `{detail: {error_class, message, details}}` →
 *   `neden: validation_error · <message>` (+ the NAMES of any detail keys)
 * - a plain string detail → `neden: <string>`
 * `input`/`ctx`/`url` of a validation item are never rendered (they carry the value).
 */
export function describeErrorDetail(detail: unknown): ErrorLine[] {
  const body = detail && typeof detail === "object" && "detail" in (detail as object)
    ? (detail as { detail: unknown }).detail
    : detail;
  if (body === null || body === undefined) return [];
  if (typeof body === "string") return [`neden: ${body}`];
  if (Array.isArray(body)) {
    return body.map((item) => describeValidationItem(item)).filter((line): line is string => line !== null);
  }
  if (typeof body === "object") {
    const record = body as Record<string, unknown>;
    if (typeof record.error_class === "string" || typeof record.message === "string") {
      const parts: string[] = [];
      if (typeof record.error_class === "string") parts.push(`neden: ${record.error_class}`);
      if (typeof record.message === "string") parts.push(record.message);
      const names = Object.keys(record).filter((k) => !["error_class", "message", "provider", "retryable"].includes(k));
      const detailNames = [
        ...names.filter((k) => k !== "details"),
        ...(record.details && typeof record.details === "object" ? Object.keys(record.details as object) : []),
      ];
      if (detailNames.length) parts.push(`ayrıntı alanları: ${detailNames.join(", ")}`);
      return [parts.join(" · ")];
    }
    const keys = Object.keys(record);
    return keys.length ? [`alanlar: ${keys.join(", ")}`] : [];
  }
  return [];
}

function describeValidationItem(item: unknown): ErrorLine | null {
  if (!item || typeof item !== "object") return null;
  const { loc, msg, type } = item as FastApiItem;
  const parts: string[] = [];
  if (Array.isArray(loc) && loc.length) parts.push(`alan: ${loc.map((p) => String(p)).join(".")}`);
  if (typeof type === "string") parts.push(`neden: ${type}`);
  if (typeof msg === "string") parts.push(msg);
  return parts.length ? parts.join(" · ") : null;
}

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

  /** The server's reasons, field by field (empty when the body said nothing usable). */
  get lines(): ErrorLine[] {
    return describeErrorDetail(this.detail);
  }
}

// ------------------------------------------------------------- request log

/** One outgoing Cloud Core request, scrubbed: never a header, never a credential-shaped key. */
export type RequestLogEntry = {
  seq: number;
  method: string;
  path: string;
  body: Record<string, unknown> | null;
  /** null when the request never got an HTTP answer (network) */
  status: number | null;
  ok: boolean;
  /** parsed error detail lines (empty on success) */
  detail: ErrorLine[];
  /** the fetcher's failure, when there was no response (its message only) */
  error: string | null;
};

type RequestMeta = { method: string; path: string; body: Record<string, unknown> | null };

const LOG_KEY_PARTS = ["authorization", "credential", "secret", "token"] as const;

/** True for keys the request log must never carry (contract.ts rule + the HTTP-auth family). */
export function isLogForbiddenKey(key: unknown): boolean {
  if (isForbiddenKey(key)) return true;
  const normalized = String(key)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
  return LOG_KEY_PARTS.some((part) => normalized.includes(part));
}

/** Drop forbidden keys recursively; bytes-like values never make it in. */
export function sanitizeForLog(value: unknown): unknown {
  if (value === null || value === undefined) return value ?? null;
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return "[bytes]";
  if (Array.isArray(value)) return value.map((v) => sanitizeForLog(v));
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, inner] of Object.entries(value as Record<string, unknown>)) {
      if (isLogForbiddenKey(key)) continue;
      if (inner === undefined) continue;
      out[key] = sanitizeForLog(inner);
    }
    return out;
  }
  return value;
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
  private readonly requestSinks = new Set<(entry: RequestLogEntry) => void>();
  private seq = 0;

  constructor(private readonly fetcher: Fetcher) {}

  /** Observe every outgoing request as a scrubbed log entry (diagnostics, dev console). */
  onRequest(sink: (entry: RequestLogEntry) => void): () => void {
    this.requestSinks.add(sink);
    return () => {
      this.requestSinks.delete(sink);
    };
  }

  private record(entry: Omit<RequestLogEntry, "seq">): RequestLogEntry {
    const full: RequestLogEntry = { seq: ++this.seq, ...entry };
    for (const sink of this.requestSinks) sink(full);
    return full;
  }

  /**
   * Fetch with the log metadata for this request (method, path, scrubbed
   * body). A fetcher failure (network, or apiFetch's own 401 error) is logged
   * here and rethrown; an HTTP answer is logged by the caller once its body
   * has been read.
   */
  private async send(path: string, init: RequestInit = {}): Promise<{ response: Response; meta: RequestMeta }> {
    const headers = new Headers(init.headers);
    if (init.body !== undefined) headers.set("Content-Type", "application/json");
    const method = (init.method ?? "GET").toUpperCase();
    let body: Record<string, unknown> | null = null;
    if (typeof init.body === "string") {
      try {
        body = sanitizeForLog(JSON.parse(init.body)) as Record<string, unknown>;
      } catch {
        body = null;
      }
    }
    const meta: RequestMeta = { method, path, body };
    try {
      const response = await this.fetcher(path, { ...init, headers });
      return { response, meta };
    } catch (error) {
      this.record({ ...meta, status: isUnauthorizedError(error) ? 401 : null, ok: false, detail: [], error: messageOf(error) });
      throw error;
    }
  }

  private async call<T>(path: string, init: RequestInit = {}): Promise<T> {
    const { response, meta } = await this.send(path, init);
    if (!response.ok) {
      let detail: unknown = null;
      try {
        detail = await response.json();
      } catch {
        /* no body */
      }
      const error = new VoiceApiError(response.status, path, detail);
      this.record({ ...meta, status: response.status, ok: false, detail: error.lines, error: null });
      throw error;
    }
    this.record({ ...meta, status: response.status, ok: true, detail: [], error: null });
    return (await response.json()) as T;
  }

  /**
   * `GET /v1/voice/realtime/contract`. 200 → the server's own contract (may be
   * newer than the bundled one); 404 → a v1 server (the route did not exist
   * yet); 401 → not signed in; anything else → unknown, never thrown.
   */
  async contract(): Promise<ContractProbe> {
    let response: Response;
    let meta: RequestMeta;
    try {
      ({ response, meta } = await this.send(CONTRACT_PATH));
    } catch (error) {
      if (isUnauthorizedError(error)) return { outcome: "unauthorized" };
      return { outcome: "unknown", reason: messageOf(error) };
    }
    const note = (status: number, ok: boolean, line: string | null): void => {
      this.record({ ...meta, status, ok, detail: line ? [line] : [], error: null });
    };
    if (response.status === 404) {
      note(404, false, "sözleşme uç noktası yok: sunucu v1");
      return { outcome: "legacy" };
    }
    if (response.status === 401) {
      note(401, false, "oturum açık değil");
      return { outcome: "unauthorized" };
    }
    if (!response.ok) {
      note(response.status, false, null);
      return { outcome: "unknown", reason: `HTTP ${response.status}` };
    }
    let document: unknown = null;
    try {
      document = await response.json();
    } catch {
      note(response.status, false, "sözleşme gövdesi JSON değil");
      return { outcome: "unknown", reason: "sözleşme gövdesi JSON değil" };
    }
    if (!isContractDocument(document)) {
      note(response.status, false, "sözleşme gövdesi tanınmadı");
      return { outcome: "unknown", reason: "sözleşme gövdesi tanınmadı" };
    }
    note(response.status, true, null);
    return { outcome: "served", version: document.contract_version, document };
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

  close(
    sessionId: string,
    reason = "client_closed",
    options: { keepalive?: boolean } = {},
  ): Promise<{ session_id: string; state: string }> {
    // `keepalive` lets the request outlive the page: a reload or a closed tab
    // still tells the Cloud Core the session is over (owner run 2026-09-06: a
    // reload left session a2ac0716 "active" while the next one was created).
    return this.call(`${BASE}/${sessionId}/close`, {
      method: "POST",
      body: JSON.stringify({ reason }),
      ...(options.keepalive ? { keepalive: true } : {}),
    });
  }
}

/** `apiFetch` throws its own `UnauthorizedError` on 401 (session.ts); recognised by name, not import. */
export function isUnauthorizedError(error: unknown): boolean {
  return error instanceof Error && error.name === "UnauthorizedError";
}

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
