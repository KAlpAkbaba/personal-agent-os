/**
 * The mail channel — contract v6 (M21 spec §3).
 *
 * The Cloud Core publishes `mail.activity` while it reads the owner's mail
 * (an inbox summary, a search, a message, a thread) and while it prepares,
 * reads back, sends or discards a draft, with `{folder?, subject?,
 * draft_state?}` in its metadata: the folder being read, the subject of the
 * message or draft in hand, and — for a draft — where it is in its lifecycle.
 *
 * The rules are the document's, applied to mail:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    folder is the token the publisher sent; the subject is the subject it
 *    sent. A missing key is rendered as "not reported", never filled in.
 * 2. **A draft state this build does not know is the plain state.** The
 *    caption for a `draft_state` outside the four the contract names is
 *    "Posta okunuyor" and no more: a word we cannot read is not a lifecycle
 *    step we may narrate.
 * 3. **Nothing here can send.** This channel is presentation. The one write
 *    the Cockpit makes — asking the Cloud Core to run its own confirmation
 *    gate on a draft the owner heard read back — lives in
 *    `lib/cockpit/approvals.ts`, not here.
 */

import {
  MAIL_CAPTION_BARE,
  type MailDraftState,
  type Severity,
  type UiStateEvent,
  isMailDraftState,
  isMailState,
  isSeverity,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every mail event, read verbatim. */
export type MailFacts = {
  /** The folder being read (`metadata.folder`), or `null` if none was sent. */
  folder: string | null;
  /** The subject in hand (`metadata.subject`), or `null`. */
  subject: string | null;
  /** `metadata.draft_state` exactly as sent, or `null` when none was. */
  draftStateToken: string | null;
  /** The draft state when it is one of the four this build knows, else `null`. */
  draftState: MailDraftState | null;
};

/** The published facts on one event, or explicit nulls for no event. */
export function mailFacts(event: UiStateEvent | null): MailFacts {
  const token = metaToken(event, "draft_state");
  return {
    folder: metaToken(event, "folder"),
    subject: metaToken(event, "subject"),
    draftStateToken: token,
    draftState: isMailDraftState(token) ? token : null,
  };
}

// --------------------------------------------------------------- the folder

/** The folder names an IMAP inbox goes by, lower-cased with the plain ASCII rule (never tr-TR: `I` → `ı`). */
const INBOX_TOKENS: ReadonlySet<string> = new Set(["inbox", "gelen kutusu", "gelen"]);

/** The one word for the inbox, whichever name the provider used for it. */
export const MAIL_FOLDER_INBOX = "Gelen kutusu";

/**
 * The folder in the owner's words: the inbox by its Turkish name whichever
 * token the provider used, every other folder verbatim — the provider's name
 * for a folder IS its name — and `null` when none was published.
 */
export function mailFolderPhrase(folder: string | null): string | null {
  if (!folder) return null;
  return INBOX_TOKENS.has(folder.trim().toLowerCase()) ? MAIL_FOLDER_INBOX : folder;
}

// ------------------------------------------------------------- the captions

/** The caption for a read of the inbox: the one the spec names (§3). */
export const MAIL_CAPTION_INBOX = "Gelen kutusu okunuyor";

/**
 * The caption for each draft state, in the words the spec gives the Core.
 * "Gönderildi" is said only when the publisher said `sent`: a confirmed
 * draft whose send was not published is still "onay bekliyor" here.
 */
export const MAIL_DRAFT_CAPTION: Record<MailDraftState, string> = {
  prepared: "Taslak hazır — okunmayı bekliyor",
  read_back: "Taslak okundu — onay bekliyor",
  sent: "Gönderildi",
  discarded: "Taslaktan vazgeçildi",
};

/** The draft state as one word, for a facts line or a row. */
export const MAIL_DRAFT_STATE_LABEL: Record<MailDraftState, string> = {
  prepared: "hazır",
  read_back: "okundu",
  sent: "gönderildi",
  discarded: "vazgeçildi",
};

/** The caption for a read: the inbox's own sentence, another folder named, or the bare statement. */
function mailReadingCaption(folder: string | null): string {
  const phrase = mailFolderPhrase(folder);
  if (phrase === null) return MAIL_CAPTION_BARE;
  return phrase === MAIL_FOLDER_INBOX ? MAIL_CAPTION_INBOX : `${phrase} klasörü okunuyor`;
}

/**
 * The caption the Core draws under the mail posture: the draft's lifecycle
 * sentence when a draft state was published, else the folder being read,
 * else the bare statement — and the subject after a separator whenever one
 * was published. A draft state this build cannot read yields the bare
 * statement alone (rule 2): the subject is a fact, but pinning it to a
 * lifecycle step we did not understand would narrate something we were not
 * told.
 */
export function mailCaption(facts: MailFacts): string {
  if (facts.draftStateToken !== null && facts.draftState === null) return MAIL_CAPTION_BARE;
  const head = facts.draftState !== null ? MAIL_DRAFT_CAPTION[facts.draftState] : mailReadingCaption(facts.folder);
  return facts.subject ? `${head} · ${facts.subject}` : head;
}

// ---------------------------------------------------------------- the view

export type MailStage =
  /** `mail.activity` is current: the Core is reading mail or working a draft. */
  | "active"
  /** Nothing has been published about mail, or the claim decayed. */
  | "none";

export type MailView = MailFacts & {
  stage: MailStage;
  /**
   * `"active"` when a mail event was ever published, regardless of age;
   * `null` when none was — the panel's "nothing reported", as distinct from
   * an activity we stopped hearing about.
   */
  lastKnown: "active" | null;
  /** The caption, from the facts alone. */
  caption: string;
  /** The publisher's short label. Never prose. */
  label: string | null;
  taskId: string | null;
  severity: Severity;
  ageMs: number | null;
  /** True once the claim has aged out; `stage` is then `none`. */
  expired: boolean;
};

export function mailView(claim: Claim): MailView {
  const event = claim.event;
  const named = event !== null && isMailState(event.state);
  const facts = mailFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "active" : "none",
    lastKnown: named ? "active" : null,
    caption: mailCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually doing something with mail right now. */
export function mailIsActive(view: MailView): boolean {
  return view.stage === "active";
}
