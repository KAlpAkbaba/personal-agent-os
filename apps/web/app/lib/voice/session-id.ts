/**
 * The session id as the owner sees and copies it (ADR-0045 addendum).
 *
 * The status line shows an 8-character short form; the owner once transcribed
 * the full UUID from network traffic, transposed two hex characters, and the
 * benchmark fetch answered "unknown realtime session". So the CANONICAL full
 * UUID is what gets copied — lower-case, hyphenated, exactly as the server
 * minted it — never a shortened or re-typed form. The controller keeps the last
 * session id after disconnect until a new session starts, so the copy still
 * works once the session is over (that is when the benchmark is fetched).
 */

export const SHORT_ID_CHARS = 8;

/** `11111111…` for the status line; the full id is one click/hover away. */
export function shortSessionId(id: string): string {
  return id.length > SHORT_ID_CHARS ? `${id.slice(0, SHORT_ID_CHARS)}…` : id;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Canonical form (lower-case, hyphenated); `null` when the value is not a UUID. */
export function canonicalSessionId(id: string | null | undefined): string | null {
  if (!id) return null;
  const trimmed = id.trim();
  return UUID.test(trimmed) ? trimmed.toLowerCase() : null;
}

export type ClipboardWriter = { writeText(text: string): Promise<void> };

export type CopyOptions = {
  /** `navigator.clipboard` in the browser; injected in tests */
  clipboard?: ClipboardWriter | null;
  /** select a read-only input holding the text and run the legacy copy command */
  fallback?: (text: string) => boolean;
};

export type CopyOutcome = "clipboard" | "fallback" | "failed";

/**
 * Copy `text` with the async clipboard API, falling back to the legacy
 * select-and-copy path when the API is missing or refuses (insecure context,
 * permission). Never throws; the outcome says which path worked.
 */
export async function copyText(text: string, options: CopyOptions = {}): Promise<CopyOutcome> {
  if (options.clipboard) {
    try {
      await options.clipboard.writeText(text);
      return "clipboard";
    } catch {
      /* fall through to the legacy path */
    }
  }
  if (options.fallback) {
    try {
      if (options.fallback(text)) return "fallback";
    } catch {
      /* nothing else to try */
    }
  }
  return "failed";
}

/**
 * Copy the canonical full session id. Returns the outcome and the exact text
 * handed to the clipboard (tests pin that it is the full UUID, not the short form).
 */
export async function copySessionId(
  sessionId: string | null | undefined,
  options: CopyOptions = {},
): Promise<{ outcome: CopyOutcome; text: string | null }> {
  const text = canonicalSessionId(sessionId);
  if (text === null) return { outcome: "failed", text: null };
  return { outcome: await copyText(text, options), text };
}
