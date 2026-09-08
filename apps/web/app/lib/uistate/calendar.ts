/**
 * The calendar channel — contract v6 (M21 spec §3).
 *
 * The Cloud Core publishes `calendar.activity` while it reads the owner's
 * calendar (an agenda, a free-slot search, an event) and while it prepares,
 * reads back, commits or discards a proposal, with `{range?, event?,
 * proposal_state?}` in its metadata — and `conflicts`, a count, when the
 * Core counted the existing events a proposal collides with.
 *
 * The mail channel's rules, applied to the calendar:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    range is the token the publisher sent, spoken in the owner's words when
 *    the word is fixed and verbatim when it is not; the conflicts figure is
 *    the number it sent, and a caption names no count nobody published.
 * 2. **A proposal state this build does not know is the plain state.**
 * 3. **Nothing here can write to a calendar.** Committing a proposal is the
 *    Cloud Core's gate, asked through `lib/cockpit/approvals.ts`.
 */

import {
  CALENDAR_CAPTION_BARE,
  type CalendarProposalState,
  type Severity,
  type UiStateEvent,
  isCalendarProposalState,
  isCalendarState,
  isSeverity,
  metaNumber,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every calendar event, read verbatim. */
export type CalendarFacts = {
  /** The range being read (`metadata.range`), or `null` if none was sent. */
  range: string | null;
  /** The event or proposal title in hand (`metadata.event`), or `null`. */
  event: string | null;
  /** `metadata.proposal_state` exactly as sent, or `null` when none was. */
  proposalStateToken: string | null;
  /** The proposal state when it is one of the four this build knows, else `null`. */
  proposalState: CalendarProposalState | null;
  /** The conflicts the Core counted (`metadata.conflicts`, a non-negative integer), or `null`. */
  conflicts: number | null;
};

/** A count the publisher sent: a non-negative integer, or nothing. */
function metaCount(event: UiStateEvent | null, key: string): number | null {
  const value = metaNumber(event, key);
  return value !== null && Number.isInteger(value) && value >= 0 ? value : null;
}

/** The published facts on one event, or explicit nulls for no event. */
export function calendarFacts(event: UiStateEvent | null): CalendarFacts {
  const token = metaToken(event, "proposal_state");
  return {
    range: metaToken(event, "range"),
    event: metaToken(event, "event"),
    proposalStateToken: token,
    proposalState: isCalendarProposalState(token) ? token : null,
    conflicts: metaCount(event, "conflicts"),
  };
}

// ---------------------------------------------------------------- the range

/** The caption for a read of today's calendar: the one the spec names (§3). */
export const CALENDAR_CAPTION_TODAY = "Bugünün takvimi";

/**
 * The range tokens whose Turkish is fixed: the phrase (for a facts line) and
 * the reading caption (for the Core). A token outside this table is shown
 * verbatim, which is still a published fact.
 */
const RANGE_WORDS: Record<string, { phrase: string; caption: string }> = {
  today: { phrase: "Bugün", caption: CALENDAR_CAPTION_TODAY },
  bugün: { phrase: "Bugün", caption: CALENDAR_CAPTION_TODAY },
  tomorrow: { phrase: "Yarın", caption: "Yarının takvimi" },
  yarın: { phrase: "Yarın", caption: "Yarının takvimi" },
  week: { phrase: "Bu hafta", caption: "Bu haftanın takvimi" },
  hafta: { phrase: "Bu hafta", caption: "Bu haftanın takvimi" },
};

function rangeWords(range: string | null): { phrase: string; caption: string } | null {
  if (!range) return null;
  return RANGE_WORDS[range.trim().toLowerCase()] ?? null;
}

/** The range in the owner's words when its token is fixed, the token as sent otherwise, `null` for none. */
export function calendarRangePhrase(range: string | null): string | null {
  if (!range) return null;
  return rangeWords(range)?.phrase ?? range;
}

/** True when the published range names today, by any of its fixed tokens. */
export function calendarRangeIsToday(range: string | null): boolean {
  return rangeWords(range)?.caption === CALENDAR_CAPTION_TODAY;
}

// ------------------------------------------------------------- the captions

/** The caption for each proposal state, in the words the spec gives the Core. */
export const CALENDAR_PROPOSAL_CAPTION: Record<CalendarProposalState, string> = {
  prepared: "Öneri hazır",
  read_back: "Öneri okundu — onay bekliyor",
  committed: "Takvime işlendi",
  discarded: "Öneriden vazgeçildi",
};

/** The proposal state as one word, for a facts line or a row. */
export const CALENDAR_PROPOSAL_STATE_LABEL: Record<CalendarProposalState, string> = {
  prepared: "hazır",
  read_back: "okundu",
  committed: "işlendi",
  discarded: "vazgeçildi",
};

/**
 * The conflicts as the Core counted them: "2 çakışma", "çakışma yok" for an
 * explicit zero — a count of none is a fact the owner wants — and `null`
 * when no count was published.
 */
export function conflictsPhrase(conflicts: number | null): string | null {
  if (conflicts === null) return null;
  return conflicts === 0 ? "çakışma yok" : `${conflicts} çakışma`;
}

/** The caption for a read: today's own sentence, a fixed range's, a verbatim range beside the bare statement, or the bare statement. */
function calendarReadingCaption(range: string | null): string {
  if (!range) return CALENDAR_CAPTION_BARE;
  return rangeWords(range)?.caption ?? `${CALENDAR_CAPTION_BARE} · ${range}`;
}

/**
 * The caption the Core draws under the planning posture: the proposal's
 * lifecycle sentence when a proposal state was published — with the
 * conflicts the Core counted while the proposal is still the owner's to
 * decide — else the range being read, else the bare statement; and the
 * event's title after a separator whenever one was published. A proposal
 * state this build cannot read yields the bare statement alone.
 */
export function calendarCaption(facts: CalendarFacts): string {
  if (facts.proposalStateToken !== null && facts.proposalState === null) return CALENDAR_CAPTION_BARE;
  let head: string;
  if (facts.proposalState !== null) {
    head = CALENDAR_PROPOSAL_CAPTION[facts.proposalState];
    const conflicts = conflictsPhrase(facts.conflicts);
    if (conflicts !== null && facts.proposalState === "prepared") head = `${head} — ${conflicts}`;
    else if (conflicts !== null && facts.proposalState === "read_back") head = `${head} · ${conflicts}`;
  } else {
    head = calendarReadingCaption(facts.range);
  }
  return facts.event ? `${head} · ${facts.event}` : head;
}

// ---------------------------------------------------------------- the view

export type CalendarStage =
  /** `calendar.activity` is current: the Core is reading the calendar or working a proposal. */
  | "active"
  /** Nothing has been published about the calendar, or the claim decayed. */
  | "none";

export type CalendarView = CalendarFacts & {
  stage: CalendarStage;
  /** `"active"` when a calendar event was ever published, regardless of age; `null` when none was. */
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

export function calendarView(claim: Claim): CalendarView {
  const event = claim.event;
  const named = event !== null && isCalendarState(event.state);
  const facts = calendarFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "active" : "none",
    lastKnown: named ? "active" : null,
    caption: calendarCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually doing something with the calendar right now. */
export function calendarIsActive(view: CalendarView): boolean {
  return view.stage === "active";
}
