/**
 * What the Posta and Takvim panels say about one pending row, and when the
 * approval pair under it may be pressed (M21 spec §3).
 *
 * Pure, so the gate can be proven without a renderer: a row is the owner's
 * to decide while its state is not settled, the pair is enabled only once
 * the row was read back to the owner — the Cloud Core's own precondition,
 * mirrored here so the button does not invite a click the gate will refuse
 * — and only while no other call is in flight.
 */

import type { ApprovalBusy, PendingDraft, PendingProposal } from "./approvals";

/** A row with a lifecycle state and a read-back mark — a draft or a proposal. */
export type ApprovalRow = { state: string | null; read_back_at: string | null };

/** The states past which a row is no longer the owner's to decide. */
const SETTLED_STATES: ReadonlySet<string> = new Set(["sent", "committed", "discarded"]);

/** True while the row is still the owner's to decide. A row with no state came from the pending route, so it is pending. */
export function rowPending(state: string | null): boolean {
  return state === null || !SETTLED_STATES.has(state);
}

/** True once the row was read back to the owner: a timestamp, or the state word itself. */
export function rowReadBack(row: ApprovalRow): boolean {
  return row.read_back_at !== null || row.state === "read_back";
}

export type ApprovalGate = {
  enabled: boolean;
  /** Why the pair is disabled, in the owner's words; `null` when it is enabled or the row is settled. */
  reason: string | null;
  reasonKind: "not_read_back" | "busy" | "settled" | null;
};

export const APPROVAL_REASON_NOT_READ_BACK = "Önce sesli okunması gerekir; bu kayıt henüz okunmadı.";
export const APPROVAL_REASON_BUSY = "Bir istek yanıt bekliyor.";

/** Whether the pair under `row` may be pressed right now, and if not, why. */
export function approvalGate(row: ApprovalRow, busy: ApprovalBusy | null): ApprovalGate {
  if (!rowPending(row.state)) return { enabled: false, reason: null, reasonKind: "settled" };
  if (!rowReadBack(row)) return { enabled: false, reason: APPROVAL_REASON_NOT_READ_BACK, reasonKind: "not_read_back" };
  if (busy !== null) return { enabled: false, reason: APPROVAL_REASON_BUSY, reasonKind: "busy" };
  return { enabled: true, reason: null, reasonKind: null };
}

// ----------------------------------------------------------- the draft row

/** How many characters of a draft's first line the panel shows. The body stays on the Cloud Core. */
export const DRAFT_LINE_MAX_CHARS = 160;

/** The first non-empty line of a body, bounded; `null` when there is none. Never the whole body. */
export function firstLine(body: string | null): string | null {
  if (!body) return null;
  const line = body
    .split(/\r?\n/)
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  if (!line) return null;
  return line.length > DRAFT_LINE_MAX_CHARS ? `${line.slice(0, DRAFT_LINE_MAX_CHARS - 1)}…` : line;
}

export const DRAFT_KIND_TR: Record<string, string> = {
  reply: "yanıt",
  new: "yeni posta",
};

export const PROPOSAL_KIND_TR: Record<string, string> = {
  create: "yeni etkinlik",
  reschedule: "erteleme",
};

/** The kind in Turkish when it is one the spec names, verbatim otherwise, `null` for none. */
export function draftKindLabel(kind: string | null): string | null {
  return kind ? (DRAFT_KIND_TR[kind] ?? kind) : null;
}

export function proposalKindLabel(kind: string | null): string | null {
  return kind ? (PROPOSAL_KIND_TR[kind] ?? kind) : null;
}

/** The addresses line: who the draft goes to, or the statement that none was reported. */
export function draftRecipientsLine(draft: Pick<PendingDraft, "to" | "cc">): string {
  const to = draft.to.length ? `kime: ${draft.to.join(", ")}` : "alıcı bildirilmedi";
  return draft.cc.length ? `${to} · bilgi: ${draft.cc.join(", ")}` : to;
}

// ----------------------------------------------------------- the time

const TIME_ZONE = "Europe/Istanbul";
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const dateFormat = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: TIME_ZONE });
const timeFormat = new Intl.DateTimeFormat("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: TIME_ZONE });

/**
 * When an event or proposal is, in the owner's zone (the spec's default,
 * Europe/Istanbul): "9 Eyl 09:30–10:30" for a timed event on one day, the
 * two ends spelled out across days, "9 Eyl · tüm gün" for an all-day one,
 * the token verbatim when it cannot be read, and the statement that none
 * was reported when there is no start.
 */
export function formatEventWhen(start: string | null, end: string | null, allDay: boolean | null): string {
  if (!start) return "zaman bildirilmedi";
  if (allDay || DATE_ONLY.test(start)) {
    const day = new Date(DATE_ONLY.test(start) ? `${start}T12:00:00+03:00` : start);
    return Number.isNaN(day.getTime()) ? `${start} · tüm gün` : `${dateFormat.format(day)} · tüm gün`;
  }
  const s = new Date(start);
  if (Number.isNaN(s.getTime())) return start;
  const e = end ? new Date(end) : null;
  if (!e || Number.isNaN(e.getTime())) return `${dateFormat.format(s)} ${timeFormat.format(s)}`;
  if (dateFormat.format(s) === dateFormat.format(e)) {
    return `${dateFormat.format(s)} ${timeFormat.format(s)}–${timeFormat.format(e)}`;
  }
  return `${dateFormat.format(s)} ${timeFormat.format(s)} – ${dateFormat.format(e)} ${timeFormat.format(e)}`;
}

/** The conflicts line for a proposal: the count the row carries, or the statement that it carries none. */
export function proposalConflictsLine(proposal: Pick<PendingProposal, "conflicts">): string {
  if (proposal.conflicts === null) return "çakışma bildirilmedi";
  return proposal.conflicts.length === 0 ? "çakışma yok" : `${proposal.conflicts.length} çakışma`;
}
