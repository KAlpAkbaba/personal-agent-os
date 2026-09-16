"use client";

/**
 * The Cockpit's approval pair for mail and the calendar (M21 spec §3).
 *
 * Six routes and nothing else. Two GETs list what waits for the owner —
 * `/v1/mail/drafts/pending`, `/v1/calendar/proposals/pending` — and four
 * POSTs ask the Cloud Core to run ITS gate on one row: `/confirm` and
 * `/discard` under each. `confirm` runs the same gate the spoken "Gönder." /
 * "Onayla." runs (ADR-0084 §1): the Cloud Core refuses a draft that was not
 * read back in this session, refuses when the host flag is off
 * (`send_disabled`), and never sends twice. This client decides none of
 * that. It cannot reach a mail or calendar provider, cannot edit a draft,
 * and cannot make a row pending; it can only ask, and print the answer.
 *
 * The Cloud Core half is built on a parallel track, so the response shapes
 * below are the spec's table columns read defensively: a field the route
 * does not send is `null` and is rendered as "not reported", never filled
 * in. A 404 is `absent` ("henüz yok"), which is the truthful word until
 * the routes land.
 */

import { UnauthorizedError, apiFetch } from "../session";
import { type Loaded, load } from "./api";

// ---------------------------------------------------------------- the routes

export const MAIL_DRAFTS_PENDING_PATH = "/v1/mail/drafts/pending";
export const CALENDAR_PROPOSALS_PENDING_PATH = "/v1/calendar/proposals/pending";

export function mailDraftConfirmPath(draftId: string): string {
  return `/v1/mail/drafts/${encodeURIComponent(draftId)}/confirm`;
}

export function mailDraftDiscardPath(draftId: string): string {
  return `/v1/mail/drafts/${encodeURIComponent(draftId)}/discard`;
}

export function calendarProposalConfirmPath(proposalId: string): string {
  return `/v1/calendar/proposals/${encodeURIComponent(proposalId)}/confirm`;
}

export function calendarProposalDiscardPath(proposalId: string): string {
  return `/v1/calendar/proposals/${encodeURIComponent(proposalId)}/discard`;
}

// B34 (req 160, 162, 166): the third pending source - file changes the Cloud Core proposed
// and has not yet carried out. Same gate, same pair, same read-back rule.
export const DOCUMENT_MUTATIONS_PENDING_PATH = "/v1/documents/mutations/pending";

export function mutationConfirmPath(mutationId: string): string {
  return `/v1/documents/mutations/${encodeURIComponent(mutationId)}/confirm`;
}

export function mutationDiscardPath(mutationId: string): string {
  return `/v1/documents/mutations/${encodeURIComponent(mutationId)}/discard`;
}

// B35 (req 609, 621): the fourth pending source - self-development candidates the
// engine finished and STOPPED on. Same pair; the decision is recorded, nothing is promoted
// (req 624), and the row is read back the moment the list shows it (the run's own
// record is what the owner reads - there is no spoken read-back for a candidate).
export const SELFDEV_PENDING_PATH = "/v1/selfdev/defects/pending";

export function candidateApprovePath(defectId: string): string {
  return `/v1/selfdev/defects/${encodeURIComponent(defectId)}/approve`;
}

export function candidateRejectPath(defectId: string): string {
  return `/v1/selfdev/defects/${encodeURIComponent(defectId)}/reject`;
}

/** A `selfdev_defects` row awaiting the owner (B35), every field verbatim or `null`. */
export type PendingCandidate = {
  defect_id: string;
  /** `bug` | `feature`. */
  kind: string | null;
  /** `owner_voice` | `owner_rest` | `opportunity` | `ci_failure`. */
  source: string | null;
  title: string | null;
  state: string | null;
  branch: string | null;
  candidate_sha: string | null;
  promotion_class: string | null;
  never_auto_promote: boolean;
  risk_tier: number | null;
  security_review_passed: boolean | null;
  gate_state: string | null;
  shadow_state: string | null;
  ci_state: string | null;
  explanation: string | null;
  tokens_used: number | null;
  finished_at: string | null;
  /** The pair's read-back gate: a candidate is read back when its record is on the page. */
  read_back_at: string | null;
};

export const CANDIDATE_STATE_TR: Record<string, string> = {
  queued: "kuyrukta",
  claimed: "çalıştırıcı aldı",
  running: "çalışıyor",
  awaiting_owner: "onayınızı bekliyor",
  approved: "onaylandı — canlıya alınmadı",
  rejected: "reddedildi",
  refused: "reddetti",
  quarantined: "karantinada",
  failed: "başarısız",
};

export const PROMOTION_CLASS_TR: Record<string, string> = {
  AUTO_SAFE: "güvenli sınıf",
  AUTO_CANARY: "kanarya sınıfı",
  OWNER_APPROVAL_REQUIRED: "sahip onayı gerekir",
  NEVER_AUTO_PROMOTE: "hiçbir zaman kendiliğinden canlıya alınmaz",
};

/** A `file_mutations` row as the pending route lists it (B34), every field verbatim or `null`. */
export type PendingMutation = {
  mutation_id: string;
  /** `write` | `append` | `edit` | `rename` | `move` | `copy` | `delete` | `restore`, or whatever the row says. */
  kind: string | null;
  /** `low` | `sensitive` | `critical`. */
  risk: string | null;
  name: string | null;
  path_before: string | null;
  path_after: string | null;
  sha_before: string | null;
  sha_after: string | null;
  /** The Turkish sentence the owner heard for this change. */
  summary: string | null;
  /** `proposed` | `applied` | `undone` | `discarded` | `failed`, or whatever the row says. */
  state: string | null;
  read_back_at: string | null;
  confirmed_at: string | null;
  created_at: string | null;
};

/** Turkish for a mutation's kind, for the row's label. */
export const MUTATION_KIND_TR: Record<string, string> = {
  write: "yazma",
  append: "ekleme",
  edit: "düzenleme",
  rename: "yeniden adlandırma",
  move: "taşıma",
  copy: "kopyalama",
  delete: "çöp kutusuna gönderme",
  restore: "geri alma",
};

/** Turkish for a mutation's state, for the outcome line. */
export const MUTATION_STATE_TR: Record<string, string> = {
  proposed: "öneri bekliyor",
  applied: "uygulandı",
  undone: "geri alındı",
  discarded: "vazgeçildi",
  failed: "uygulanamadı",
};

// ------------------------------------------------------------------ the rows

/** A `mail_drafts` row as the pending route lists it (spec §3), every field verbatim or `null`. */
export type PendingDraft = {
  draft_id: string;
  /** `reply` | `new`, or whatever the row says. */
  kind: string | null;
  to: string[];
  cc: string[];
  subject: string | null;
  /** The whole body as sent; the panel shows its first line only. */
  body: string | null;
  in_reply_to: string | null;
  /** `prepared` | `read_back` | `sent` | `discarded`, or whatever the row says. */
  state: string | null;
  read_back_at: string | null;
  confirmed_at: string | null;
  sent_message_id: string | null;
  created_at: string | null;
};

/** One existing event a proposal collides with, as the row names it. */
export type ProposalConflict = {
  title: string | null;
  start: string | null;
  end: string | null;
};

/** A `calendar_proposals` row as the pending route lists it, every field verbatim or `null`. */
export type PendingProposal = {
  proposal_id: string;
  /** `create` | `reschedule`, or whatever the row says. */
  kind: string | null;
  title: string | null;
  start: string | null;
  end: string | null;
  all_day: boolean | null;
  location: string | null;
  /** The conflicts the Core recorded; `null` when the row carried no such field at all. */
  conflicts: ProposalConflict[] | null;
  /** `prepared` | `read_back` | `committed` | `discarded`, or whatever the row says. */
  state: string | null;
  read_back_at: string | null;
  confirmed_at: string | null;
  /** The event a reschedule proposal moves, when the row names it. */
  event_id: string | null;
  created_at: string | null;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/** A list of addresses, whether the row sent a list or one comma-joined string. */
function strList(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === "string" && v.length > 0);
  if (typeof value === "string" && value) {
    return value
      .split(",")
      .map((v) => v.trim())
      .filter((v) => v.length > 0);
  }
  return [];
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

/** One pending draft from a raw row; `null` for a row with no id, which is not a draft. */
export function parseDraft(raw: unknown): PendingDraft | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.draft_id) ?? str(o.id);
  if (id === null) return null;
  return {
    draft_id: id,
    kind: str(o.kind),
    to: strList(o.to),
    cc: strList(o.cc),
    subject: str(o.subject),
    body: str(o.body),
    in_reply_to: str(o.in_reply_to),
    state: str(o.state) ?? str(o.draft_state),
    read_back_at: str(o.read_back_at),
    confirmed_at: str(o.confirmed_at),
    sent_message_id: str(o.sent_message_id),
    created_at: str(o.created_at),
  };
}

function parseConflict(raw: unknown): ProposalConflict | null {
  if (typeof raw === "string") return raw ? { title: raw, start: null, end: null } : null;
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const conflict = {
    title: str(o.title) ?? str(o.summary),
    start: str(o.start) ?? str(o.starts_at),
    end: str(o.end) ?? str(o.ends_at),
  };
  return conflict.title === null && conflict.start === null && conflict.end === null ? null : conflict;
}

/**
 * One pending proposal from a raw row. The spec's "event fields" are read
 * from the row itself and from a nested `event` object alike, so either
 * shape the Cloud Core settles on is read the same way.
 */
export function parseProposal(raw: unknown): PendingProposal | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.proposal_id) ?? str(o.id);
  if (id === null) return null;
  const nested = o.event && typeof o.event === "object" ? (o.event as Record<string, unknown>) : null;
  const field = (key: string): unknown => (nested && nested[key] !== undefined ? nested[key] : o[key]);
  return {
    proposal_id: id,
    kind: str(o.kind),
    title: str(field("title")) ?? str(field("summary")),
    start: str(field("start")) ?? str(field("starts_at")),
    end: str(field("end")) ?? str(field("ends_at")),
    all_day: bool(field("all_day")),
    location: str(field("location")),
    conflicts: Array.isArray(o.conflicts) ? o.conflicts.map(parseConflict).filter(isPresent) : null,
    state: str(o.state) ?? str(o.proposal_state),
    read_back_at: str(o.read_back_at),
    confirmed_at: str(o.confirmed_at),
    event_id: str(o.event_id),
    created_at: str(o.created_at),
  };
}

/** One pending file change from a raw row; `null` for a row with no id. */
export function parseMutation(raw: unknown): PendingMutation | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.mutation_id) ?? str(o.id);
  if (id === null) return null;
  return {
    mutation_id: id,
    kind: str(o.kind),
    risk: str(o.risk),
    name: str(o.name),
    path_before: str(o.path_before),
    path_after: str(o.path_after),
    sha_before: str(o.sha_before),
    sha_after: str(o.sha_after),
    summary: str(o.summary),
    state: str(o.state),
    read_back_at: str(o.read_back_at),
    confirmed_at: str(o.confirmed_at),
    created_at: str(o.created_at),
  };
}

/** One pending candidate from a raw row; `null` for a row with no id. */
export function parseCandidate(raw: unknown): PendingCandidate | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.defect_id);
  if (!id) return null;
  const review = o.security_review && typeof o.security_review === "object" ? (o.security_review as Record<string, unknown>) : {};
  const gate = o.gate && typeof o.gate === "object" ? (o.gate as Record<string, unknown>) : {};
  const shadow = o.shadow && typeof o.shadow === "object" ? (o.shadow as Record<string, unknown>) : {};
  const ci = o.ci && typeof o.ci === "object" ? (o.ci as Record<string, unknown>) : {};
  return {
    defect_id: id,
    kind: str(o.kind),
    source: str(o.source),
    title: str(o.title),
    state: str(o.state),
    branch: str(o.branch),
    candidate_sha: str(o.candidate_sha),
    promotion_class: str(o.promotion_class),
    never_auto_promote: o.never_auto_promote === true,
    risk_tier: typeof o.risk_tier === "number" ? o.risk_tier : null,
    security_review_passed: typeof review.passed === "boolean" ? review.passed : null,
    gate_state: str(gate.state),
    shadow_state: str(shadow.state),
    ci_state: str(ci.state),
    explanation: str(o.explanation),
    tokens_used: typeof o.tokens_used === "number" ? o.tokens_used : null,
    finished_at: str(o.finished_at),
    read_back_at: str(o.finished_at),
  };
}

export const fetchPendingCandidates = (): Promise<Loaded<PendingCandidate[]>> =>
  load<PendingCandidate[]>(SELFDEV_PENDING_PATH, (raw) =>
    listAt(raw, ["pending", "defects", "items"]).map(parseCandidate).filter(isPresent),
  );

export const fetchPendingMutations = (): Promise<Loaded<PendingMutation[]>> =>
  load<PendingMutation[]>(DOCUMENT_MUTATIONS_PENDING_PATH, (raw) =>
    listAt(raw, ["pending", "mutations", "items"]).map(parseMutation).filter(isPresent),
  );

export const fetchPendingDrafts = (): Promise<Loaded<PendingDraft[]>> =>
  load<PendingDraft[]>(MAIL_DRAFTS_PENDING_PATH, (raw) =>
    listAt(raw, ["drafts", "items", "pending"]).map(parseDraft).filter(isPresent),
  );

export const fetchPendingProposals = (): Promise<Loaded<PendingProposal[]>> =>
  load<PendingProposal[]>(CALENDAR_PROPOSALS_PENDING_PATH, (raw) =>
    listAt(raw, ["proposals", "items", "pending"]).map(parseProposal).filter(isPresent),
  );

// --------------------------------------------------------------- the answer

/** What a confirm or discard answered, read from the receipt the route returns. */
export type ApprovalReceipt = {
  /** The row's state after the call (`sent`, `committed`, `discarded`, …), when the answer named it. */
  state: string | null;
  /** The receipt's own sentence, when it had one. */
  summary: string | null;
  receiptId: string | null;
};

/**
 * The receipt, read from either shape the route may answer with: the
 * receipt as the body, or the body carrying `receipt` and the row beside it.
 */
export function parseReceipt(raw: unknown): ApprovalReceipt {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const receipt = body.receipt && typeof body.receipt === "object" ? (body.receipt as Record<string, unknown>) : body;
  const row =
    body.draft && typeof body.draft === "object"
      ? (body.draft as Record<string, unknown>)
      : body.proposal && typeof body.proposal === "object"
        ? (body.proposal as Record<string, unknown>)
        : body.defect && typeof body.defect === "object"
          ? (body.defect as Record<string, unknown>)
          : null;
  return {
    state:
      str(body.state) ??
      str(body.draft_state) ??
      str(body.proposal_state) ??
      (row ? str(row.state) : null) ??
      str(receipt.status),
    summary:
      str(receipt.factual_summary) ?? str(receipt.summary) ?? str(receipt.speech) ?? str(body.message) ?? str(body.speech),
    receiptId: str(receipt.receipt_id) ?? str(body.receipt_id) ?? str(receipt.id),
  };
}

/** The gate refused, or the route failed. `code` is the Cloud Core's own token when it sent one. */
export class ApprovalError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;

  constructor(status: number, code: string | null, detail: string) {
    super(detail);
    this.name = "ApprovalError";
    this.status = status;
    this.code = code;
    this.detail = detail;
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

/** The typed refusal from a non-2xx answer; never throws itself. */
export async function toApprovalError(response: Response): Promise<ApprovalError> {
  const body = await readBody(response);
  let code: string | null = null;
  let detail: string | null = null;
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const d = record.detail;
    if (typeof d === "string") detail = d;
    else if (d && typeof d === "object") {
      const inner = d as Record<string, unknown>;
      code = str(inner.code) ?? str(inner.error_class) ?? str(inner.reason);
      detail = str(inner.message) ?? str(inner.detail);
    }
    code ??= str(record.code) ?? str(record.error_class) ?? str(record.reason);
    detail ??= str(record.message);
  }
  return new ApprovalError(response.status, code, detail ?? `HTTP ${response.status}`);
}

/**
 * The gate's refusals in the owner's words, by the code the Cloud Core
 * sends. Every sentence says what did NOT happen: a refusal to send is the
 * gate working, and the owner must never read it as a failure to deliver.
 */
export const APPROVAL_REFUSAL_TR: Record<string, string> = {
  send_disabled: "Gönderme bu sunucuda kapalı (PAGENTOS_MAIL_SEND_ENABLED açık değil). Taslak duruyor; gönderilmedi.",
  write_disabled: "Takvime yazma bu sunucuda kapalı (PAGENTOS_CALENDAR_WRITE_ENABLED açık değil). Öneri duruyor; işlenmedi.",
  calendar_write_disabled:
    "Takvime yazma bu sunucuda kapalı (PAGENTOS_CALENDAR_WRITE_ENABLED açık değil). Öneri duruyor; işlenmedi.",
  not_read_back: "Önce sesli okunması gerekir; bu kayıt henüz okunmadı. Gönderilmedi.",
  read_back_required: "Önce sesli okunması gerekir; bu kayıt henüz okunmadı. Gönderilmedi.",
  already_sent: "Bu taslak zaten gönderilmiş; ikinci kez gönderilmedi.",
  already_committed: "Bu öneri zaten takvime işlenmiş; ikinci kez işlenmedi.",
  already_discarded: "Bu kayıttan zaten vazgeçilmiş.",
  not_found: "Bekleyen böyle bir kayıt yok.",
  account_missing: "Tanımlı bir posta hesabı yok.",
};

/** One line for the owner from whatever the call threw. */
export function approvalErrorText(err: unknown): string {
  if (err instanceof ApprovalError) {
    const known = err.code ? APPROVAL_REFUSAL_TR[err.code] : undefined;
    if (known) return known;
    return err.code ? `${err.code}: ${err.detail}` : err.detail;
  }
  if (err instanceof UnauthorizedError) return "oturum reddedildi";
  return err instanceof Error ? err.message : String(err);
}

async function post(path: string): Promise<ApprovalReceipt> {
  const response = await apiFetch(path, { method: "POST" });
  if (!response.ok) throw await toApprovalError(response);
  return parseReceipt(await readBody(response));
}

export const confirmDraft = (draftId: string): Promise<ApprovalReceipt> => post(mailDraftConfirmPath(draftId));
export const discardDraft = (draftId: string): Promise<ApprovalReceipt> => post(mailDraftDiscardPath(draftId));
export const confirmProposal = (proposalId: string): Promise<ApprovalReceipt> =>
  post(calendarProposalConfirmPath(proposalId));
export const discardProposal = (proposalId: string): Promise<ApprovalReceipt> =>
  post(calendarProposalDiscardPath(proposalId));

/**
 * The four calls the pair can make, as one object so a test can hand the
 * panel a double and prove which call was made, how many times, with which
 * id — without a network and without this file's `apiFetch`.
 */
export type ApprovalClient = {
  confirmDraft: (draftId: string) => Promise<ApprovalReceipt>;
  discardDraft: (draftId: string) => Promise<ApprovalReceipt>;
  confirmProposal: (proposalId: string) => Promise<ApprovalReceipt>;
  discardProposal: (proposalId: string) => Promise<ApprovalReceipt>;
  confirmMutation: (mutationId: string) => Promise<ApprovalReceipt>;
  discardMutation: (mutationId: string) => Promise<ApprovalReceipt>;
  approveCandidate: (defectId: string) => Promise<ApprovalReceipt>;
  rejectCandidate: (defectId: string) => Promise<ApprovalReceipt>;
};

export const confirmMutation = (mutationId: string): Promise<ApprovalReceipt> =>
  post(mutationConfirmPath(mutationId));
export const discardMutation = (mutationId: string): Promise<ApprovalReceipt> =>
  post(mutationDiscardPath(mutationId));
export const approveCandidate = (defectId: string): Promise<ApprovalReceipt> =>
  post(candidateApprovePath(defectId));
export const rejectCandidate = (defectId: string): Promise<ApprovalReceipt> =>
  post(candidateRejectPath(defectId));

/** The real client: the eight POSTs above, through the owner session. */
export const approvalClient: ApprovalClient = {
  confirmDraft,
  discardDraft,
  confirmProposal,
  discardProposal,
  confirmMutation,
  discardMutation,
  approveCandidate,
  rejectCandidate,
};

// ------------------------------------------------------------- pair state

export type ApprovalAction =
  | "confirm_draft"
  | "discard_draft"
  | "confirm_proposal"
  | "discard_proposal"
  | "confirm_mutation"
  | "discard_mutation"
  | "approve_candidate"
  | "reject_candidate";

export type ApprovalFamily = "draft" | "proposal" | "mutation" | "candidate";

/** Which pending family an action belongs to. */
export function approvalFamily(action: ApprovalAction): ApprovalFamily {
  if (action === "confirm_draft" || action === "discard_draft") return "draft";
  if (action === "confirm_proposal" || action === "discard_proposal") return "proposal";
  if (action === "approve_candidate" || action === "reject_candidate") return "candidate";
  return "mutation";
}

/** The one call in flight. There is never more than one: an external mutation is asked for one at a time. */
export type ApprovalBusy = { action: ApprovalAction; id: string };

/** What the last call answered, in the owner's words, with the row it was about. */
export type ApprovalOutcome = {
  action: ApprovalAction;
  id: string;
  /** True when the route answered 2xx. NOT "sent": the receipt's state says that, in `text`. */
  ok: boolean;
  text: string;
  at: number;
};

export type ApprovalPairState = {
  busy: ApprovalBusy | null;
  outcome: ApprovalOutcome | null;
};

export const APPROVAL_PAIR_IDLE: ApprovalPairState = { busy: null, outcome: null };

/** What a panel is handed: the state, and the two things a click may ask for. */
export type ApprovalPairProps = {
  busy: ApprovalBusy | null;
  outcome: ApprovalOutcome | null;
  onConfirm: (id: string) => void;
  onDiscard: (id: string) => void;
};
