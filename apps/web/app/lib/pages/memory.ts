"use client";

/**
 * The memory page's own controls (B37 req 57-60) and the embedding status (req 51-54).
 *
 * Four calls on one memory, through the Cloud Core's own routes and nothing else:
 * pin (`POST /v1/memory/{id}/pin`), unpin (`POST /v1/memory/{id}/unpin`), forget
 * (`DELETE /v1/memory/{id}` - the one irreversible act, so the page asks twice) and
 * correct (`POST /v1/memory/{id}/supersede {text}` - the old memory is superseded by a
 * new one the owner wrote; nothing is edited in place). One call at a time; the
 * outcome is what the route answered, in the owner's words, never a guess.
 *
 * The embedding status (`GET /v1/memory/embedding`) says which provider serves
 * retrieval and how much of the index it covers; `POST /v1/memory/reindex` rebuilds the
 * rows for the active model.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type Loaded, load } from "../cockpit/api";
import { UnauthorizedError, apiFetch } from "../session";

export const MEMORY_ACTIONS = ["pin", "unpin", "forget", "correct"] as const;
export type MemoryAction = (typeof MEMORY_ACTIONS)[number];

export const MEMORY_EMBEDDING_PATH = "/v1/memory/embedding";
export const MEMORY_REINDEX_PATH = "/v1/memory/reindex";

export function memoryActionPath(memoryId: string, action: MemoryAction): string {
  const id = encodeURIComponent(memoryId);
  switch (action) {
    case "pin":
      return `/v1/memory/${id}/pin`;
    case "unpin":
      return `/v1/memory/${id}/unpin`;
    case "forget":
      return `/v1/memory/${id}`;
    case "correct":
      return `/v1/memory/${id}/supersede`;
  }
}

// ------------------------------------------------------------- the answers

export type MemoryActionReceipt = {
  /** The memory the route answered about (the NEW one for a correction), when it named one. */
  memoryId: string | null;
  pinned: boolean | null;
  /** For a forget: the rows removed, when the route said. */
  removed: number | null;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

export function parseMemoryReceipt(raw: unknown): MemoryActionReceipt {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const removed = body.removed && typeof body.removed === "object" ? (body.removed as Record<string, unknown>) : null;
  return {
    memoryId: str(body.memory_id),
    pinned: typeof body.pinned === "boolean" ? body.pinned : null,
    removed: removed && typeof removed.memories === "number" ? removed.memories : null,
  };
}

export class MemoryActionError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

async function readBody(response: Response): Promise<unknown> {
  try {
    const text = await response.text();
    return text ? (JSON.parse(text) as unknown) : null;
  } catch {
    return null;
  }
}

export async function toMemoryActionError(response: Response): Promise<MemoryActionError> {
  const body = await readBody(response);
  const detail = body && typeof body === "object" ? (body as Record<string, unknown>).detail : null;
  const record = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : {};
  const code = str(record.error_class) ?? str(record.code);
  const message = str(record.message) ?? (typeof detail === "string" ? detail : null) ?? `HTTP ${response.status}`;
  return new MemoryActionError(response.status, code, message);
}

export const MEMORY_REFUSAL_TR: Record<string, string> = {
  not_found: "Böyle bir hatıra yok.",
  explicit_protected: "Siz söylediğiniz için bu hatırayı yalnız siz değiştirebilirsiniz.",
  secret_rejected: "Bu metin bir sır taşıyor; hatıra olarak yazılmadı.",
  validation_error: "İstek anlaşılamadı.",
};

export function memoryActionErrorText(err: unknown): string {
  if (err instanceof MemoryActionError) {
    const known = err.code ? MEMORY_REFUSAL_TR[err.code] : undefined;
    if (known) return known;
    return err.code ? `${err.code}: ${err.message}` : err.message;
  }
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

// -------------------------------------------------------------- the client

export type MemoryClient = {
  pin: (memoryId: string) => Promise<MemoryActionReceipt>;
  unpin: (memoryId: string) => Promise<MemoryActionReceipt>;
  forget: (memoryId: string) => Promise<MemoryActionReceipt>;
  correct: (memoryId: string, text: string) => Promise<MemoryActionReceipt>;
};

async function send(path: string, init: RequestInit): Promise<MemoryActionReceipt> {
  const response = await apiFetch(path, init);
  if (!response.ok) throw await toMemoryActionError(response);
  return parseMemoryReceipt(await readBody(response));
}

export const memoryClient: MemoryClient = {
  pin: (id) => send(memoryActionPath(id, "pin"), { method: "POST" }),
  unpin: (id) => send(memoryActionPath(id, "unpin"), { method: "POST" }),
  forget: (id) => send(memoryActionPath(id, "forget"), { method: "DELETE" }),
  correct: (id, text) =>
    send(memoryActionPath(id, "correct"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, reason: "owner_correction" }),
    }),
};

// ---------------------------------------------------------- control state

export type MemoryBusy = { action: MemoryAction; id: string };

export type MemoryActionOutcome = {
  action: MemoryAction;
  id: string;
  ok: boolean;
  text: string;
  at: number;
};

export type MemoryControlState = { busy: MemoryBusy | null; outcome: MemoryActionOutcome | null };

export const MEMORY_CONTROL_IDLE: MemoryControlState = { busy: null, outcome: null };

export const MEMORY_OUTCOME_TR: Record<MemoryAction, string> = {
  pin: "Sabitlendi; artık kendiliğinden silinmez ve yeniden yazılmaz.",
  unpin: "Sabitleme kaldırıldı.",
  forget: "Unutuldu; kayıt ve gömmesi silindi, deftere içeriksiz bir iz düştü.",
  correct: "Düzeltildi; eskisi yenisiyle değiştirildi.",
};

export function memoryOutcomeText(action: MemoryAction, receipt: MemoryActionReceipt): string {
  const parts = [MEMORY_OUTCOME_TR[action]];
  if (action === "forget" && receipt.removed !== null) parts.push(`${receipt.removed} kayıt`);
  if (action === "correct" && receipt.memoryId) parts.push(`yeni kayıt ${receipt.memoryId.slice(0, 8)}`);
  return parts.join(" · ");
}

export type MemoryControlPorts = {
  client: MemoryClient;
  read: () => MemoryControlState;
  write: (state: MemoryControlState) => void;
  onSettled?: () => void;
  now?: () => number;
};

function call(client: MemoryClient, action: MemoryAction, id: string, text: string | undefined): Promise<MemoryActionReceipt> {
  switch (action) {
    case "pin":
      return client.pin(id);
    case "unpin":
      return client.unpin(id);
    case "forget":
      return client.forget(id);
    case "correct":
      return client.correct(id, text ?? "");
  }
}

/** One action through the client; `false` when one is already in flight. */
export async function runMemoryAction(
  ports: MemoryControlPorts,
  action: MemoryAction,
  id: string,
  text?: string,
): Promise<boolean> {
  const before = ports.read();
  if (before.busy !== null) return false;
  if (action === "correct" && !(text ?? "").trim()) {
    ports.write({
      busy: null,
      outcome: { action, id, ok: false, text: "Düzeltme için yeni metni yazın.", at: (ports.now ?? Date.now)() },
    });
    return true;
  }
  const now = ports.now ?? Date.now;
  ports.write({ busy: { action, id }, outcome: before.outcome });
  let outcome: MemoryActionOutcome;
  try {
    const receipt = await call(ports.client, action, id, text);
    outcome = { action, id, ok: true, text: memoryOutcomeText(action, receipt), at: now() };
  } catch (err) {
    outcome = { action, id, ok: false, text: memoryActionErrorText(err), at: now() };
  }
  ports.write({ busy: null, outcome });
  ports.onSettled?.();
  return true;
}

export type MemoryControlProps = {
  busy: MemoryBusy | null;
  outcome: MemoryActionOutcome | null;
  run: (action: MemoryAction, id: string, text?: string) => void;
};

export function useMemoryControl(client: MemoryClient, onSettled?: () => void): MemoryControlProps {
  const [state, setState] = useState<MemoryControlState>(MEMORY_CONTROL_IDLE);
  const latest = useRef<MemoryControlState>(MEMORY_CONTROL_IDLE);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);
  const ports = useMemo<MemoryControlPorts>(
    () => ({
      client,
      read: () => latest.current,
      write: (next) => {
        latest.current = next;
        setState(next);
      },
      onSettled: () => settled.current?.(),
    }),
    [client],
  );
  const run = useCallback(
    (action: MemoryAction, id: string, text?: string) => {
      void runMemoryAction(ports, action, id, text);
    },
    [ports],
  );
  return useMemo(() => ({ busy: state.busy, outcome: state.outcome, run }), [state, run]);
}

// ------------------------------------------------------------ the embedding

export type EmbeddingStatus = {
  requested: string | null;
  active: string | null;
  model_id: string | null;
  semantic: boolean | null;
  fallback_reason: string | null;
  memories: number | null;
  embedded: number | null;
  missing: number | null;
};

export function parseEmbeddingStatus(raw: unknown): EmbeddingStatus {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const provider = body.provider && typeof body.provider === "object" ? (body.provider as Record<string, unknown>) : {};
  const coverage = body.coverage && typeof body.coverage === "object" ? (body.coverage as Record<string, unknown>) : {};
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  return {
    requested: str(provider.requested),
    active: str(provider.active),
    model_id: str(provider.model_id),
    semantic: typeof provider.semantic === "boolean" ? provider.semantic : null,
    fallback_reason: str(provider.fallback_reason),
    memories: num(coverage.memories),
    embedded: num(coverage.embedded),
    missing: num(coverage.missing),
  };
}

export const fetchEmbeddingStatus = (): Promise<Loaded<EmbeddingStatus>> =>
  load<EmbeddingStatus>(MEMORY_EMBEDDING_PATH, parseEmbeddingStatus);

/** Req 54: rebuild the embedding rows for the active model; answers the row count. */
export async function reindexMemory(onlyMissing: boolean): Promise<number> {
  const response = await apiFetch(MEMORY_REINDEX_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ only_missing: onlyMissing }),
  });
  if (!response.ok) throw await toMemoryActionError(response);
  const body = (await readBody(response)) as Record<string, unknown> | null;
  return body && typeof body.rows === "number" ? body.rows : 0;
}

/** What the status says, in the owner's words: a hash is not meaning. */
export function embeddingSentence(status: EmbeddingStatus): string {
  const provider = status.semantic ? `anlamsal gömme açık (${status.model_id ?? status.active ?? "sağlayıcı"})` : `anlamsal gömme yok — ${status.model_id ?? "deterministic-ngram"} (sözcük karması) hizmet veriyor`;
  const coverage =
    status.memories !== null && status.missing !== null
      ? status.missing === 0
        ? `${status.memories} hatıranın hepsi indekste`
        : `${status.missing} / ${status.memories} hatıra bu model için indekslenmemiş`
      : "kapsam bildirilmedi";
  const reason = status.fallback_reason ? ` · ${status.fallback_reason}` : "";
  return `${provider} · ${coverage}${reason}`;
}
