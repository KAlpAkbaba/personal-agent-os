/**
 * Conversational focus, and the identity of a research run (M18.2).
 *
 * The owner ran "OpenAI son gelişmeler" four times. The four reports have the
 * same title, and a title is presentation metadata — it is not the report.
 * Two consequences run through this file:
 *
 * 1. **Identity is the row's own facts, never its title.** Every research item
 *    carries a second line built from what the server actually reported —
 *    when it finished, which mode ran, how many sources it stood on, what
 *    state it is in. A field the server did not report is omitted, not
 *    guessed: an invented "0 kaynak" would be a claim about the report.
 * 2. **Selection is by id.** The focus chip lands on the row whose task id
 *    equals `current.research_job_id`. Matching on the topic would put the
 *    chip on whichever duplicate happened to be first, which is exactly the
 *    bug this milestone exists to fix.
 *
 * Raw UUIDs are never rendered here. They travel in `data-` attributes so a
 * test (and the click handler) can still say precisely which row it means.
 *
 * Nothing in this module touches the network or the DOM; the client lives in
 * `./api.ts`.
 */

// ------------------------------------------------------------------- clock

/**
 * The owner's wall clock. Pinned rather than read from the host so that
 * "Bugün 20:19" means the same thing on the owner's browser, in CI and on a
 * server-rendered page — a time label that silently changes zone between
 * renders is worse than no time label.
 */
export const OWNER_TIME_ZONE = "Europe/Istanbul";

const dayFormat = new Intl.DateTimeFormat("tr-TR", {
  timeZone: OWNER_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const timeFormat = new Intl.DateTimeFormat("tr-TR", {
  timeZone: OWNER_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
});

const shortDateFormat = new Intl.DateTimeFormat("tr-TR", {
  timeZone: OWNER_TIME_ZONE,
  day: "numeric",
  month: "short",
});

const shortDateYearFormat = new Intl.DateTimeFormat("tr-TR", {
  timeZone: OWNER_TIME_ZONE,
  day: "numeric",
  month: "short",
  year: "numeric",
});

function partsOf(format: Intl.DateTimeFormat, date: Date): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of format.formatToParts(date)) out[part.type] = part.value;
  return out;
}

/** `YYYY-MM-DD` of an instant, as the owner's own calendar sees it. */
function dayKey(date: Date): string {
  const p = partsOf(dayFormat, date);
  return `${p.year}-${p.month}-${p.day}`;
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

/** Calendar arithmetic on the key itself, so no time zone is applied twice. */
function shiftDayKey(key: string, days: number): string {
  const [y, m, d] = key.split("-").map((v) => Number(v));
  const t = new Date(Date.UTC(y, m - 1, d));
  t.setUTCDate(t.getUTCDate() + days);
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`;
}

/**
 * "Bugün 20:19" / "Dün 20:19" / "3 Eyl 20:19" (with the year once it differs).
 *
 * `null` when nothing was reported. A value that is not a timestamp is handed
 * back verbatim rather than dropped: the server said something, and hiding it
 * would be the renderer editing the record.
 */
export function formatDayTime(
  value: string | null | undefined,
  now: number | Date = Date.now(),
): string | null {
  if (!value) return null;
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return value;
  const reference = new Date(typeof now === "number" ? now : now.getTime());
  const key = dayKey(at);
  const today = dayKey(reference);
  const clock = timeFormat.format(at);
  if (key === today) return `Bugün ${clock}`;
  if (key === shiftDayKey(today, -1)) return `Dün ${clock}`;
  const sameYear = key.slice(0, 4) === today.slice(0, 4);
  const day = (sameYear ? shortDateFormat : shortDateYearFormat).format(at);
  return `${day} ${clock}`;
}

// ------------------------------------------------------------- identity line

/** The short Turkish state word. `null` when the server reported neither. */
export function statusWord(row: {
  status?: string | null;
  stage?: string | null;
}): string | null {
  const stage = (row.stage ?? "").toLowerCase();
  const status = (row.status ?? "").toLowerCase();
  if (!stage && !status) return null;
  if (stage === "ready" || status === "ready") return "hazır";
  if (stage === "failed" || status.startsWith("failed")) return "başarısız";
  if (stage === "cancelled" || status === "cancelled") return "iptal edildi";
  return "çalışıyor";
}

/** The fields the identity line is built from, on any of the row shapes. */
export type IdentityRow = {
  completed_at?: string | null;
  ready_at?: string | null;
  created_at?: string | null;
  mode?: string | null;
  source_count?: number | null;
  status?: string | null;
  stage?: string | null;
};

/**
 * The parts of "Bugün 20:19 · QUICK · 5 kaynak · hazır", in order.
 *
 * Each part is present only because the server reported the fact behind it.
 * `source_count: 0` is a reported fact and stays; `source_count: null` is not
 * and is dropped — the owner is never shown a number nobody counted.
 */
export function identityParts(row: IdentityRow, now: number | Date = Date.now()): string[] {
  const parts: string[] = [];

  const when = formatDayTime(row.completed_at ?? row.ready_at ?? row.created_at, now);
  if (when) parts.push(when);

  const mode = typeof row.mode === "string" ? row.mode.trim() : "";
  if (mode) parts.push(mode.toUpperCase());

  const count = row.source_count;
  if (typeof count === "number" && Number.isFinite(count) && count >= 0) {
    parts.push(`${count} kaynak`);
  }

  const state = statusWord(row);
  if (state) parts.push(state);

  return parts;
}

/** The identity line itself; empty when the server reported nothing at all. */
export function identityLine(row: IdentityRow, now: number | Date = Date.now()): string {
  return identityParts(row, now).join(" · ");
}

// -------------------------------------------------------------- focus state

/** How the focus came to be what it is (`GET /v1/research/focus`). */
export type FocusSource =
  | "research_just_completed"
  | "result_just_spoken"
  | "owner_selected_in_ui"
  | "owner_selected_by_voice"
  | "followup_reference";

export type FocusEntry = {
  research_job_id: string;
  artifact_id: string | null;
  topic: string | null;
  completed_at: string | null;
  mode: string | null;
  source_count: number | null;
  status: string | null;
  source_of_focus: string | null;
  selected_at: string | null;
};

export type PendingClarification = {
  asked_at: string | null;
  question: string;
  candidates: FocusEntry[];
};

export type FocusState = {
  current: FocusEntry | null;
  previous: FocusEntry | null;
  stack: FocusEntry[];
  pending_clarification: PendingClarification | null;
};

export const EMPTY_FOCUS: FocusState = {
  current: null,
  previous: null,
  stack: [],
  pending_clarification: null,
};

/** Turkish for each way the focus can have been set. */
export const FOCUS_SOURCE_LABEL: Record<FocusSource, string> = {
  research_just_completed: "araştırma tamamlandığında",
  result_just_spoken: "sonuç seslendirildiğinde",
  owner_selected_in_ui: "siz seçtiniz",
  owner_selected_by_voice: "sesle seçildi",
  followup_reference: "önceki araştırmaya dönüldü",
};

/**
 * "OpenAI son gelişmeler · Dün 19:40 · DEEP · 18 kaynak · hazır" — a focus
 * entry as one string, so both surfaces describe the focus identically.
 */
export function focusSummary(entry: FocusEntry, now: number | Date = Date.now()): string {
  const identity = identityLine(entry, now);
  const topic = entry.topic ?? NO_TOPIC;
  return identity ? `${topic} · ${identity}` : topic;
}

/** An unknown source value is printed as-is, never translated into a guess. */
export function focusSourceLabel(value: string | null | undefined): string | null {
  if (!value) return null;
  return (FOCUS_SOURCE_LABEL as Record<string, string>)[value] ?? value;
}

/** Shown where a candidate or entry arrived without a title. */
export const NO_TOPIC = "Başlık bildirilmedi";

/**
 * The sentence for a task with no completed report behind it. The API answers
 * 409 `{"error":"not_completed"}`; this is what the owner reads.
 */
export const FOCUS_NOT_COMPLETED = "Bu araştırma henüz tamamlanmadı; odak olamaz.";

/**
 * The sentence for a Cloud Core that predates the focus routes (404).
 *
 * Deliberately not an error: nothing broke, this server simply does not have
 * the feature. It is stated once, where the focus would have been shown.
 */
export const FOCUS_UNSUPPORTED = "Bu Cloud Core sürümünde odak yok.";

function str(o: Record<string, unknown>, key: string): string | null {
  const value = o[key];
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * One focus entry, or `null` when the payload carries no id — an entry with
 * no research job is not an entry, and rendering it would put a row on the
 * screen that nothing can select.
 */
export function parseFocusEntry(raw: unknown): FocusEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o, "research_job_id") ?? str(o, "task_id");
  if (!id) return null;
  const count = o.source_count;
  return {
    research_job_id: id,
    artifact_id: str(o, "artifact_id"),
    topic: str(o, "topic"),
    completed_at: str(o, "completed_at") ?? str(o, "ready_at"),
    mode: str(o, "mode"),
    source_count: typeof count === "number" && Number.isFinite(count) ? count : null,
    status: str(o, "status"),
    source_of_focus: str(o, "source_of_focus"),
    selected_at: str(o, "selected_at"),
  };
}

function parseClarification(raw: unknown): PendingClarification | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const question = str(o, "question");
  const candidates = Array.isArray(o.candidates)
    ? o.candidates.map(parseFocusEntry).filter((e): e is FocusEntry => e !== null)
    : [];
  // A clarification with neither a question nor anything to choose between is
  // not a question the owner can answer.
  if (!question && candidates.length === 0) return null;
  return { asked_at: str(o, "asked_at"), question: question ?? "", candidates };
}

/** Tolerant by design: the route is being built on another track today. */
export function parseFocusState(raw: unknown): FocusState {
  if (!raw || typeof raw !== "object") return EMPTY_FOCUS;
  const o = raw as Record<string, unknown>;
  return {
    current: parseFocusEntry(o.current),
    previous: parseFocusEntry(o.previous),
    stack: Array.isArray(o.stack)
      ? o.stack.map(parseFocusEntry).filter((e): e is FocusEntry => e !== null)
      : [],
    pending_clarification: parseClarification(o.pending_clarification),
  };
}
