/**
 * Contract v6: `mail.activity` and `calendar.activity` (M21 spec §3).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to the owner's mail and calendar: **the Core names a folder,
 * a subject, a draft's step, a range, a proposal's conflicts because the
 * Cloud Core published them — in the words the spec gives it — never a step
 * it inferred, never a count it was not told, never "gönderildi" for a draft
 * whose send nobody published.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v5 (or
 * v4, v3, v2) is read normally, because v6 only added.
 */

import { describe, expect, it } from "vitest";

import {
  CALENDAR_ACTIVITY as CALENDAR_ACTIVITY_TOKEN,
  CALENDAR_ACTIVITY_TTL_MS,
  CALENDAR_CAPTION_BARE,
  CALENDAR_PROPOSAL_STATES,
  CALENDAR_STATES,
  DOCUMENT_STATES,
  DOCUMENT_STEP_TTL_MS,
  KNOWN_CONTRACT_VERSION,
  MAIL_ACTIVITY as MAIL_ACTIVITY_TOKEN,
  MAIL_ACTIVITY_TTL_MS,
  MAIL_CAPTION_BARE,
  MAIL_DRAFT_STATES,
  MAIL_STATES,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STATES,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  contractCompatibility,
  isCalendarProposalState,
  isCalendarState,
  isCoreChannel,
  isDocumentState,
  isKnownState,
  isMailDraftState,
  isMailState,
  isOperatorState,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  CALENDAR_CAPTION_TODAY,
  CALENDAR_PROPOSAL_CAPTION,
  calendarCaption,
  calendarFacts,
  calendarIsActive,
  calendarRangeIsToday,
  calendarRangePhrase,
  calendarView,
  conflictsPhrase,
} from "../../app/lib/uistate/calendar";
import {
  CALENDAR_EMPTY,
  CALENDAR_LABEL,
  CALENDAR_UNTOLD,
  KIND_LABEL,
  MAIL_EMPTY,
  MAIL_LABEL,
  MAIL_UNTOLD,
  STATE_LABEL,
  calendarFactsLine,
  contractLagNote,
  mailFactsLine,
  stateLabel,
  subsystemLabel,
} from "../../app/lib/uistate/labels";
import {
  MAIL_CAPTION_INBOX,
  MAIL_DRAFT_CAPTION,
  mailCaption,
  mailFacts,
  mailFolderPhrase,
  mailIsActive,
  mailView,
} from "../../app/lib/uistate/mail";
import {
  applyError,
  applyResponse,
  calendarClaim,
  coreClaim,
  documentClaim,
  emptyTruth,
  mailClaim,
} from "../../app/lib/uistate/truth";
import {
  type VisualIntent,
  type VoiceOverlay,
  applyVoiceOverlay,
  isCalendarPlanning,
  isDocumentReading,
  isMailReading,
  isOperatorActing,
  visualFor,
} from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  CALENDAR_ACTIVITY,
  CALENDAR_ACTIVITY_BARE,
  DOCUMENT_ANALYSIS,
  MAIL_ACTIVITY,
  MAIL_ACTIVITY_BARE,
  OPERATOR_RUNNING,
  OWNER_AWAY,
  T0,
  event,
  iso,
  resetSequence,
  response,
} from "./fixtures";

function truthOf(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return applyResponse(emptyTruth(), response(events), at);
}

function intentOf(events: ReturnType<typeof event>[], at = T0) {
  return visualFor(truthOf(events), at);
}

/** Every channel that means "the Core itself is moving". */
const MOTION_CHANNELS = [
  "breathAmplitude",
  "inwardFlow",
  "topology",
  "pulse",
  "agitation",
  "ringSpin",
  "flowRate",
  "ownerVoice",
  "constellationDrift",
  "energy",
] as const satisfies readonly (keyof VisualIntent)[];

const VOICE_TOOL_RUNNING: VoiceOverlay = {
  state: "tool_running",
  micLevel: null,
  outputLevel: null,
  caption: null,
  toolLabel: "Posta",
  lastError: null,
};

// ------------------------------------------------------------ the contract

describe("contract v6 is v5 plus the mail and calendar states, and says so", () => {
  it("is version 6 and still reads a v5, v4, v3 and v2 server", () => {
    expect(KNOWN_CONTRACT_VERSION).toBe(6);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(6)).toBe("current");
    expect(contractCompatibility(5)).toBe("older_supported");
    expect(contractCompatibility(4)).toBe("older_supported");
    expect(contractCompatibility(3)).toBe("older_supported");
    expect(contractCompatibility(2)).toBe("older_supported");
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(7)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("names the two tokens once, and knows them by membership rather than by prefix", () => {
    expect(MAIL_ACTIVITY_TOKEN).toBe("mail.activity");
    expect(CALENDAR_ACTIVITY_TOKEN).toBe("calendar.activity");
    expect(MAIL_STATES).toEqual(["mail.activity"]);
    expect(CALENDAR_STATES).toEqual(["calendar.activity"]);
    expect(isKnownState("mail.activity")).toBe(true);
    expect(isKnownState("calendar.activity")).toBe(true);
    expect(isMailState("mail.activity")).toBe(true);
    expect(isCalendarState("calendar.activity")).toBe(true);
    // A newer server's word is not a state this build may draw as a read.
    expect(isMailState("mail.sent")).toBe(false);
    expect(isCalendarState("calendar.committed")).toBe(false);
    expect(isMailState("calendar.activity")).toBe(false);
    expect(isCalendarState("mail.activity")).toBe(false);
    expect(isDocumentState("mail.activity")).toBe(false);
    expect(isOperatorState("calendar.activity")).toBe(false);
  });

  it("appends the two tokens after v5's, never reordering", () => {
    const document = UI_STATES.indexOf("document.analysis");
    expect(UI_STATES.indexOf("mail.activity")).toBe(document + 1);
    expect(UI_STATES.indexOf("calendar.activity")).toBe(document + 2);
    expect(UI_STATES[UI_STATES.length - 1]).toBe("calendar.activity");
  });

  it("types the draft and proposal lifecycles, and admits nothing outside them", () => {
    expect(MAIL_DRAFT_STATES).toEqual(["prepared", "read_back", "sent", "discarded"]);
    expect(CALENDAR_PROPOSAL_STATES).toEqual(["prepared", "read_back", "committed", "discarded"]);
    for (const s of MAIL_DRAFT_STATES) expect(isMailDraftState(s), s).toBe(true);
    for (const s of CALENDAR_PROPOSAL_STATES) expect(isCalendarProposalState(s), s).toBe(true);
    expect(isMailDraftState("committed")).toBe(false);
    expect(isCalendarProposalState("sent")).toBe(false);
    expect(isMailDraftState("SENT")).toBe(false);
    expect(isMailDraftState(3)).toBe(false);
    expect(isMailDraftState(null)).toBe(false);
  });

  it("adds the mail and calendar subsystems and names them", () => {
    expect(SUBSYSTEMS).toContain("mail");
    expect(SUBSYSTEMS).toContain("calendar");
    expect(subsystemLabel("mail")).toBe("Posta");
    expect(subsystemLabel("calendar")).toBe("Takvim");
  });

  it("keeps both on the agent channel, which drives the core body", () => {
    expect(stateChannel("mail.activity")).toBe("agent");
    expect(stateChannel("calendar.activity")).toBe("agent");
    expect(isCoreChannel("mail.activity")).toBe(true);
    expect(isCoreChannel("calendar.activity")).toBe(true);
    // The room is untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("operator.running")).toBe("operator");
  });

  it("classifies both as transient, on the provider round trip's horizon", () => {
    expect(stateKind("mail.activity")).toBe("transient");
    expect(stateKind("calendar.activity")).toBe("transient");
    expect(MAIL_ACTIVITY_TTL_MS).toBe(45_000);
    expect(CALENDAR_ACTIVITY_TTL_MS).toBe(45_000);
    expect(MAIL_ACTIVITY_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(stateTtlMs("mail.activity")).toBe(MAIL_ACTIVITY_TTL_MS);
    expect(stateTtlMs("calendar.activity")).toBe(CALENDAR_ACTIVITY_TTL_MS);
    // The publisher's own ttl_s still beats every figure here — a draft
    // waiting on the owner may be published with a longer life.
    expect(stateTtlMs("mail.activity", MAIL_ACTIVITY("INBOX", null, "read_back", { ttl_s: 600 }))).toBe(600_000);
  });

  it("gives the states the Turkish the spec asks for, spelled once", () => {
    expect(MAIL_CAPTION_BARE).toBe("Posta okunuyor");
    expect(CALENDAR_CAPTION_BARE).toBe("Takvim okunuyor");
    expect(STATE_LABEL["mail.activity"]).toBe(MAIL_CAPTION_BARE);
    expect(STATE_LABEL["calendar.activity"]).toBe(CALENDAR_CAPTION_BARE);
    expect(KIND_LABEL.mail_activity).toBe(MAIL_CAPTION_BARE);
    expect(KIND_LABEL.calendar_activity).toBe(CALENDAR_CAPTION_BARE);
    expect(MAIL_LABEL.active).toBe(MAIL_CAPTION_BARE);
    expect(CALENDAR_LABEL.active).toBe(CALENDAR_CAPTION_BARE);
    expect(MAIL_LABEL.none).not.toBe("Boşta");
    expect(CALENDAR_LABEL.none).not.toBe("Boşta");
    expect(MAIL_EMPTY).toBe("Bekleyen taslak yok.");
    expect(CALENDAR_EMPTY).toBe("Bugün için kayıt yok.");
    expect(MAIL_UNTOLD).not.toBe(MAIL_EMPTY);
    expect(CALENDAR_UNTOLD).not.toBe(CALENDAR_EMPTY);
    expect(stateLabel("mail.activity")).not.toBe("mail.activity");
    expect(stateLabel("calendar.activity")).not.toBe("calendar.activity");
    // The spec's captions, each a constant spelled once.
    expect(MAIL_CAPTION_INBOX).toBe("Gelen kutusu okunuyor");
    expect(MAIL_DRAFT_CAPTION).toEqual({
      prepared: "Taslak hazır — okunmayı bekliyor",
      read_back: "Taslak okundu — onay bekliyor",
      sent: "Gönderildi",
      discarded: "Taslaktan vazgeçildi",
    });
    expect(CALENDAR_CAPTION_TODAY).toBe("Bugünün takvimi");
    expect(CALENDAR_PROPOSAL_CAPTION).toEqual({
      prepared: "Öneri hazır",
      read_back: "Öneri okundu — onay bekliyor",
      committed: "Takvime işlendi",
      discarded: "Öneriden vazgeçildi",
    });
  });

  it("names exactly the families an older server will never publish", () => {
    const v5 = contractLagNote(5, 6);
    expect(v5).toContain("v5");
    expect(v5).toContain("v6");
    expect(v5).toContain("Posta ve takvim durumları");
    expect(v5).not.toContain("belge");
    expect(v5).toContain("yok demek değil");

    const v4 = contractLagNote(4, 6);
    expect(v4).toContain("Belge inceleme durumu");
    expect(v4).toContain("posta ve takvim durumları");

    const v2 = contractLagNote(2, 6);
    expect(v2).toContain("Alarm ve ekran durumları");
    expect(v2).toContain("dijital operatör durumları");
    expect(v2).toContain("belge inceleme durumu");
    expect(v2).toContain("posta ve takvim durumları");

    // The v5 wording M20 shipped is unchanged for a v4 server seen from v5.
    expect(contractLagNote(4, 5)).toBe(
      "Sunucu durum sözleşmesi v4; bu arayüz v5. Belge inceleme durumu bu sunucudan henüz yayınlanmıyor — yok demek değil.",
    );
  });

  it("leaves every v5 token, kind and horizon exactly as it was", () => {
    expect(DOCUMENT_STATES).toEqual(["document.analysis"]);
    expect(OPERATOR_STATES).toEqual(["operator.running", "operator.verifying", "operator.failed"]);
    expect(stateKind("document.analysis")).toBe("transient");
    expect(stateTtlMs("document.analysis")).toBe(DOCUMENT_STEP_TTL_MS);
    expect(stateKind("operator.failed")).toBe("steady");
    expect(stateTtlMs("agent.thinking")).toBe(TRANSIENT_TTL_MS);
    expect(stateKind("agent.idle")).toBe("steady");
    expect(UI_STATES.indexOf("document.analysis")).toBe(UI_STATES.indexOf("operator.failed") + 1);
    // A v5 document event still parses and draws as it did.
    const intent = intentOf([DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")]);
    expect(intent.kind).toBe("document_analysis");
    expect(intent.label).toBe("rapor.pdf · 3. sayfa · yanıtlıyor");
    expect(intent.mail).toBeNull();
    expect(intent.calendar).toBeNull();
  });

  it("reads the v6 metadata through the same bounded parser, dropping nothing it needs and keeping nothing content-shaped", () => {
    const e = parseEvent({
      state: "mail.activity",
      at: iso(0),
      metadata: { folder: "INBOX", subject: "Proje planı", draft_state: "read_back", body: "uzun bir gövde", to: ["a@b"] },
    });
    // `to` is a list — content-shaped, dropped at the boundary like every list.
    expect(e?.metadata).toEqual({ folder: "INBOX", subject: "Proje planı", draft_state: "read_back", body: "uzun bir gövde" });
    expect(e).not.toHaveProperty("refs");
    const c = parseEvent({
      state: "calendar.activity",
      at: iso(0),
      metadata: { range: "today", event: "Ali ile toplantı", proposal_state: "prepared", conflicts: 2 },
    });
    expect(c?.metadata).toEqual({ range: "today", event: "Ali ile toplantı", proposal_state: "prepared", conflicts: 2 });
  });
});

// ------------------------------------------------------------------- mail

describe("mail: facts, view and caption from published metadata only", () => {
  it("reads the folder, the subject and the draft state verbatim", () => {
    const facts = mailFacts(MAIL_ACTIVITY("INBOX", "Proje planı", "read_back"));
    expect(facts).toEqual({ folder: "INBOX", subject: "Proje planı", draftStateToken: "read_back", draftState: "read_back" });
    expect(mailFacts(null)).toEqual({ folder: null, subject: null, draftStateToken: null, draftState: null });
  });

  it("speaks the inbox by its Turkish name whichever token the provider used, and other folders verbatim", () => {
    expect(mailFolderPhrase("INBOX")).toBe("Gelen kutusu");
    expect(mailFolderPhrase("inbox")).toBe("Gelen kutusu");
    expect(mailFolderPhrase("Gelen Kutusu")).toBe("Gelen kutusu");
    expect(mailFolderPhrase("Gönderilmiş")).toBe("Gönderilmiş");
    expect(mailFolderPhrase("Arşiv")).toBe("Arşiv");
    expect(mailFolderPhrase(null)).toBeNull();
    expect(mailFolderPhrase("")).toBeNull();
  });

  it("captions a read of the inbox with the spec's sentence, another folder by name, a subject after it", () => {
    expect(mailCaption(mailFacts(MAIL_ACTIVITY("INBOX")))).toBe("Gelen kutusu okunuyor");
    expect(mailCaption(mailFacts(MAIL_ACTIVITY("Gelen Kutusu")))).toBe("Gelen kutusu okunuyor");
    expect(mailCaption(mailFacts(MAIL_ACTIVITY("Arşiv")))).toBe("Arşiv klasörü okunuyor");
    expect(mailCaption(mailFacts(MAIL_ACTIVITY("INBOX", "Fatura")))).toBe("Gelen kutusu okunuyor · Fatura");
    // A subject with no folder: a message being read, named by its subject.
    expect(mailCaption(mailFacts(MAIL_ACTIVITY(null, "Fatura")))).toBe("Posta okunuyor · Fatura");
  });

  it("captions every draft state with the spec's words, and the subject after them", () => {
    const cases: Array<[string, string]> = [
      ["prepared", "Taslak hazır — okunmayı bekliyor"],
      ["read_back", "Taslak okundu — onay bekliyor"],
      ["sent", "Gönderildi"],
      ["discarded", "Taslaktan vazgeçildi"],
    ];
    for (const [state, expected] of cases) {
      expect(mailCaption(mailFacts(MAIL_ACTIVITY("INBOX", null, state))), state).toBe(expected);
      expect(mailCaption(mailFacts(MAIL_ACTIVITY("INBOX", "Re: Proje planı", state))), state).toBe(
        `${expected} · Re: Proje planı`,
      );
      // The draft's step outranks the folder: a draft is not a read.
      expect(mailCaption(mailFacts(MAIL_ACTIVITY("Arşiv", null, state))), state).toBe(expected);
    }
  });

  it("says only the plain state for a draft state it cannot read, and invents nothing without metadata", () => {
    // A newer publisher's `scheduled`: a word we cannot read is not a step we may narrate.
    const unknown = mailFacts(MAIL_ACTIVITY("INBOX", "Fatura", "scheduled"));
    expect(unknown.draftStateToken).toBe("scheduled");
    expect(unknown.draftState).toBeNull();
    expect(mailCaption(unknown)).toBe(MAIL_CAPTION_BARE);
    // The long line still says what WAS published, verbatim.
    expect(mailFactsLine(unknown)).toBe("klasör: Gelen kutusu · konu: Fatura · taslak: scheduled");

    const bare = mailFacts(MAIL_ACTIVITY_BARE());
    expect(bare).toEqual({ folder: null, subject: null, draftStateToken: null, draftState: null });
    expect(mailCaption(bare)).toBe("Posta okunuyor");
    expect(mailFactsLine(bare)).toBe("klasör bildirilmedi · konu bildirilmedi · taslak bildirilmedi");
  });

  it("the facts line names each fact or its absence", () => {
    expect(mailFactsLine(mailFacts(MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")))).toBe(
      "klasör: Gelen kutusu · konu: Proje planı · taslak: okundu",
    );
    expect(mailFactsLine(mailFacts(MAIL_ACTIVITY("Gönderilmiş", null, "sent")))).toBe(
      "klasör: Gönderilmiş · konu bildirilmedi · taslak: gönderildi",
    );
    expect(mailFactsLine(mailFacts(MAIL_ACTIVITY(null, "Fatura", "prepared")))).toBe(
      "klasör bildirilmedi · konu: Fatura · taslak: hazır",
    );
    expect(mailFactsLine(mailFacts(MAIL_ACTIVITY("INBOX", null, "discarded")))).toContain("taslak: vazgeçildi");
  });

  it("the view is active while live, with the caption, and none-but-nothing before any mail event", () => {
    const view = mailView(mailClaim(truthOf([MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")]), T0));
    expect(view.stage).toBe("active");
    expect(view.lastKnown).toBe("active");
    expect(view.caption).toBe("Taslak okundu — onay bekliyor · Proje planı");
    expect(view.taskId).toBe("mail-task-1");
    expect(view.severity).toBe("info");
    expect(mailIsActive(view)).toBe(true);

    const none = mailView(mailClaim(truthOf([AGENT_IDLE(), OPERATOR_RUNNING(), CALENDAR_ACTIVITY()]), T0));
    expect(none.stage).toBe("none");
    expect(none.lastKnown).toBeNull();
    expect(none.folder).toBeNull();
    expect(none.caption).toBe(MAIL_CAPTION_BARE);
    expect(mailIsActive(none)).toBe(false);
  });

  it("the claim is by membership: a newer server's mail word is not read as mail", () => {
    const truth = truthOf([MAIL_ACTIVITY("INBOX"), event({ state: "mail.sent", subsystem: "mail" })]);
    expect(mailClaim(truth, T0).event?.state).toBe("mail.activity");
    expect(visualFor(truth, T0).kind).toBe("unknown_state");
  });
});

// --------------------------------------------------------------- calendar

describe("calendar: facts, view and caption from published metadata only", () => {
  it("reads the range, the event, the proposal state and the counted conflicts verbatim", () => {
    const facts = calendarFacts(CALENDAR_ACTIVITY("today", "Ali ile toplantı", "prepared", 2));
    expect(facts).toEqual({
      range: "today",
      event: "Ali ile toplantı",
      proposalStateToken: "prepared",
      proposalState: "prepared",
      conflicts: 2,
    });
    expect(calendarFacts(null)).toEqual({ range: null, event: null, proposalStateToken: null, proposalState: null, conflicts: null });
  });

  it("takes a conflicts figure only as a non-negative integer — anything else is not a count", () => {
    expect(calendarFacts(CALENDAR_ACTIVITY("today", null, "prepared", 0)).conflicts).toBe(0);
    expect(calendarFacts(CALENDAR_ACTIVITY("today", null, "prepared", 1.5)).conflicts).toBeNull();
    expect(calendarFacts(CALENDAR_ACTIVITY("today", null, "prepared", -1)).conflicts).toBeNull();
    expect(calendarFacts(CALENDAR_ACTIVITY("today", null, "prepared", null, { conflicts: "2" })).conflicts).toBeNull();
  });

  it("speaks a fixed range in the owner's words and any other range verbatim", () => {
    expect(calendarRangePhrase("today")).toBe("Bugün");
    expect(calendarRangePhrase("Bugün")).toBe("Bugün");
    expect(calendarRangePhrase("tomorrow")).toBe("Yarın");
    expect(calendarRangePhrase("week")).toBe("Bu hafta");
    expect(calendarRangePhrase("2026-09-12")).toBe("2026-09-12");
    expect(calendarRangePhrase(null)).toBeNull();
    expect(calendarRangeIsToday("today")).toBe(true);
    expect(calendarRangeIsToday("bugün")).toBe(true);
    expect(calendarRangeIsToday("tomorrow")).toBe(false);
    expect(calendarRangeIsToday("2026-09-09")).toBe(false);
    expect(calendarRangeIsToday(null)).toBe(false);
  });

  it("captions a read of today with the spec's sentence, a fixed range by its own, a verbatim range beside the bare statement", () => {
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("today")))).toBe("Bugünün takvimi");
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("tomorrow")))).toBe("Yarının takvimi");
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("week")))).toBe("Bu haftanın takvimi");
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("2026-09-12")))).toBe("Takvim okunuyor · 2026-09-12");
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("today", "Ali ile toplantı")))).toBe("Bugünün takvimi · Ali ile toplantı");
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY(null, "Ali ile toplantı")))).toBe("Takvim okunuyor · Ali ile toplantı");
  });

  it("captions every proposal state with the spec's words, the counted conflicts while it is the owner's to decide", () => {
    expect(conflictsPhrase(2)).toBe("2 çakışma");
    expect(conflictsPhrase(0)).toBe("çakışma yok");
    expect(conflictsPhrase(null)).toBeNull();

    const prepared = (conflicts: number | null) => calendarCaption(calendarFacts(CALENDAR_ACTIVITY("today", null, "prepared", conflicts)));
    expect(prepared(2)).toBe("Öneri hazır — 2 çakışma");
    expect(prepared(0)).toBe("Öneri hazır — çakışma yok");
    // No count published: no count named.
    expect(prepared(null)).toBe("Öneri hazır");

    const readBack = (conflicts: number | null) =>
      calendarCaption(calendarFacts(CALENDAR_ACTIVITY("today", "Ali ile toplantı", "read_back", conflicts)));
    expect(readBack(1)).toBe("Öneri okundu — onay bekliyor · 1 çakışma · Ali ile toplantı");
    expect(readBack(null)).toBe("Öneri okundu — onay bekliyor · Ali ile toplantı");

    // Once committed or discarded the conflicts are history, not a caption.
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("today", null, "committed", 2)))).toBe("Takvime işlendi");
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("today", "Ali ile toplantı", "committed")))).toBe(
      "Takvime işlendi · Ali ile toplantı",
    );
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("today", null, "discarded", 2)))).toBe("Öneriden vazgeçildi");
    // The proposal's step outranks the range: a proposal is not a read.
    expect(calendarCaption(calendarFacts(CALENDAR_ACTIVITY("week", null, "prepared")))).toBe("Öneri hazır");
  });

  it("says only the plain state for a proposal state it cannot read, and invents nothing without metadata", () => {
    const unknown = calendarFacts(CALENDAR_ACTIVITY("today", "Ali ile toplantı", "tentative", 2));
    expect(unknown.proposalStateToken).toBe("tentative");
    expect(unknown.proposalState).toBeNull();
    expect(calendarCaption(unknown)).toBe(CALENDAR_CAPTION_BARE);
    expect(calendarFactsLine(unknown)).toBe("aralık: Bugün · etkinlik: Ali ile toplantı · öneri: tentative · 2 çakışma");

    const bare = calendarFacts(CALENDAR_ACTIVITY_BARE());
    expect(bare).toEqual({ range: null, event: null, proposalStateToken: null, proposalState: null, conflicts: null });
    expect(calendarCaption(bare)).toBe("Takvim okunuyor");
    expect(calendarFactsLine(bare)).toBe("aralık bildirilmedi · etkinlik bildirilmedi · öneri bildirilmedi");
  });

  it("the facts line names each fact or its absence, and the conflicts only beside a proposal", () => {
    expect(calendarFactsLine(calendarFacts(CALENDAR_ACTIVITY("today", "Ali ile toplantı", "read_back", 2)))).toBe(
      "aralık: Bugün · etkinlik: Ali ile toplantı · öneri: okundu · 2 çakışma",
    );
    expect(calendarFactsLine(calendarFacts(CALENDAR_ACTIVITY("today", null, "prepared")))).toBe(
      "aralık: Bugün · etkinlik bildirilmedi · öneri: hazır · çakışma bildirilmedi",
    );
    expect(calendarFactsLine(calendarFacts(CALENDAR_ACTIVITY("2026-09-12", "Diş hekimi", "committed", 0)))).toBe(
      "aralık: 2026-09-12 · etkinlik: Diş hekimi · öneri: işlendi · çakışma yok",
    );
    // A plain read has nothing to collide with; no conflicts field is printed.
    expect(calendarFactsLine(calendarFacts(CALENDAR_ACTIVITY("today")))).toBe(
      "aralık: Bugün · etkinlik bildirilmedi · öneri bildirilmedi",
    );
  });

  it("the view is active while live, with the caption, and none-but-nothing before any calendar event", () => {
    const view = calendarView(calendarClaim(truthOf([CALENDAR_ACTIVITY("today", "Ali ile toplantı", "prepared", 2)]), T0));
    expect(view.stage).toBe("active");
    expect(view.lastKnown).toBe("active");
    expect(view.caption).toBe("Öneri hazır — 2 çakışma · Ali ile toplantı");
    expect(view.taskId).toBe("cal-task-1");
    expect(calendarIsActive(view)).toBe(true);

    const none = calendarView(calendarClaim(truthOf([AGENT_IDLE(), MAIL_ACTIVITY()]), T0));
    expect(none.stage).toBe("none");
    expect(none.lastKnown).toBeNull();
    expect(none.caption).toBe(CALENDAR_CAPTION_BARE);
    expect(calendarIsActive(none)).toBe(false);
  });

  it("the claim is by membership: a newer server's calendar word is not read as the calendar", () => {
    const truth = truthOf([CALENDAR_ACTIVITY("today"), event({ state: "calendar.committed", subsystem: "calendar" })]);
    expect(calendarClaim(truth, T0).event?.state).toBe("calendar.activity");
    expect(visualFor(truth, T0).kind).toBe("unknown_state");
  });
});

// --------------------------------------------------------------- the Core

describe("the Core draws the mail and calendar postures from the published state", () => {
  it("mail is its own calm reading kind, captioned from the folder, the draft's step and the subject", () => {
    const intent = intentOf([MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")]);
    expect(intent.kind).toBe("mail_activity");
    expect(intent.source).toBe("bus");
    expect(intent.subsystem).toBe("mail");
    expect(intent.palette).toBe("reading");
    expect(intent.label).toBe("Taslak okundu — onay bekliyor · Proje planı");
    expect(intent.mail).toEqual({ folder: "INBOX", subject: "Proje planı", draftStateToken: "read_back", draftState: "read_back" });
    expect(intent.calendar).toBeNull();
    expect(intent.document).toBeNull();
    expect(isMailReading(intent)).toBe(true);
    expect(isCalendarPlanning(intent)).toBe(false);
    expect(isDocumentReading(intent)).toBe(false);
    expect(isOperatorActing(intent)).toBe(false);

    // The posture: a shade calmer than the document's reading posture, and
    // nothing that could be read as work being done or as progress.
    const reading = intentOf([DOCUMENT_ANALYSIS()]);
    const idle = intentOf([AGENT_IDLE()]);
    expect(intent.inwardFlow).toBeGreaterThan(0);
    expect(intent.inwardFlow).toBeLessThanOrEqual(reading.inwardFlow);
    expect(intent.flowRate).toBeLessThan(reading.flowRate);
    expect(intent.shellSpread).toBeLessThan(reading.shellSpread);
    expect(intent.shellSpread).toBeGreaterThan(idle.shellSpread);
    expect(intent.ringSpin).toBeLessThan(reading.ringSpin);
    expect(intent.agitation).toBe(0);
    expect(intent.restraint).toBe(0);
    expect(intent.pulse).toBe(0);
  });

  it("the calendar is its own planning kind: laid open and still, arranging rather than taking in", () => {
    const intent = intentOf([CALENDAR_ACTIVITY("today", "Ali ile toplantı", "prepared", 2)]);
    expect(intent.kind).toBe("calendar_activity");
    expect(intent.subsystem).toBe("calendar");
    expect(intent.palette).toBe("planning");
    expect(intent.label).toBe("Öneri hazır — 2 çakışma · Ali ile toplantı");
    expect(intent.calendar).toEqual({
      range: "today",
      event: "Ali ile toplantı",
      proposalStateToken: "prepared",
      proposalState: "prepared",
      conflicts: 2,
    });
    expect(intent.mail).toBeNull();
    expect(isCalendarPlanning(intent)).toBe(true);
    expect(isMailReading(intent)).toBe(false);

    const mail = intentOf([MAIL_ACTIVITY()]);
    const tool = intentOf([event({ state: "agent.tool_running" })]);
    const idle = intentOf([AGENT_IDLE()]);
    expect(intent.inwardFlow).toBe(0); // planning arranges what is known; nothing comes in
    expect(intent.shellSpread).toBeGreaterThan(mail.shellSpread);
    expect(intent.shellSpread).toBeLessThan(tool.shellSpread);
    expect(intent.ringSpin).toBeLessThan(mail.ringSpin);
    expect(intent.ringSpin).toBeGreaterThan(idle.ringSpin);
    expect(intent.flowRate).toBeLessThan(mail.flowRate);
    expect(intent.topology).toBeGreaterThan(0);
    expect(intent.agitation).toBe(0);
    expect(intent.restraint).toBe(0);
    expect(intent.pulse).toBe(0);
  });

  it("never draws progress, a constellation or a candidate: none is published", () => {
    for (const events of [
      [MAIL_ACTIVITY()],
      [MAIL_ACTIVITY("INBOX", "Fatura", "sent")],
      [MAIL_ACTIVITY_BARE()],
      [CALENDAR_ACTIVITY()],
      [CALENDAR_ACTIVITY("today", "Ali ile toplantı", "read_back", 2)],
      [CALENDAR_ACTIVITY_BARE()],
    ]) {
      const intent = intentOf(events);
      expect(intent.progress).toBeNull();
      expect(intent.constellationNodes).toBe(0);
      expect(intent.constellationDrift).toBe(0);
      expect(intent.capabilityNodes).toBe(0);
      expect(intent.satelliteComplete).toBe(false);
      expect(intent.pulse).toBe(0);
    }
  });

  it("a draft or proposal waiting on the owner is worded as waiting and drawn calm, never busy", () => {
    const draft = intentOf([MAIL_ACTIVITY("INBOX", "Fatura", "read_back")]);
    expect(draft.label).toContain("onay bekliyor");
    const proposal = intentOf([CALENDAR_ACTIVITY("today", "Diş hekimi", "read_back", 0)]);
    expect(proposal.label).toContain("onay bekliyor");
    const tool = intentOf([event({ state: "agent.tool_running" })]);
    for (const intent of [draft, proposal]) {
      expect(intent.flowRate).toBeLessThan(tool.flowRate);
      expect(intent.ringSpin).toBeLessThan(tool.ringSpin);
      expect(intent.agitation).toBe(0);
    }
  });

  it("captions the bare statement when the publisher named nothing", () => {
    expect(intentOf([MAIL_ACTIVITY_BARE()]).label).toBe("Posta okunuyor");
    expect(intentOf([CALENDAR_ACTIVITY_BARE()]).label).toBe("Takvim okunuyor");
  });

  it("does not let the room displace a read in flight", () => {
    expect(intentOf([MAIL_ACTIVITY(), OWNER_AWAY()]).kind).toBe("mail_activity");
    expect(intentOf([CALENDAR_ACTIVITY(), OWNER_AWAY()]).kind).toBe("calendar_activity");
  });

  it("leaves every other kind's `mail` and `calendar` null", () => {
    for (const events of [[AGENT_IDLE()], [OPERATOR_RUNNING()], [DOCUMENT_ANALYSIS()], [event({ state: "agent.tool_running" })]]) {
      const intent = intentOf(events);
      expect(intent.mail).toBeNull();
      expect(intent.calendar).toBeNull();
    }
  });
});

describe("honesty over time", () => {
  it("an activity is live for its horizon and last-known after it, with its facts kept", () => {
    const truth = truthOf([MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")]);
    expect(visualFor(truth, T0 + MAIL_ACTIVITY_TTL_MS - 1).kind).toBe("mail_activity");

    const stale = visualFor(truth, T0 + MAIL_ACTIVITY_TTL_MS + 1);
    expect(stale.kind).toBe("last_known");
    expect(stale.state).toBe("mail.activity");
    for (const channel of MOTION_CHANNELS) expect(stale[channel], channel).toBe(0);
    // What it WAS doing is still a fact; the readout names it as last-known.
    expect(stale.label).toBe("Taslak okundu — onay bekliyor · Proje planı");
    expect(stale.mail?.subject).toBe("Proje planı");
    expect(isMailReading(stale)).toBe(false);

    const cal = truthOf([CALENDAR_ACTIVITY("today", "Ali ile toplantı", "prepared", 2)]);
    const calStale = visualFor(cal, T0 + CALENDAR_ACTIVITY_TTL_MS + 1);
    expect(calStale.kind).toBe("last_known");
    expect(calStale.calendar?.conflicts).toBe(2);
    expect(isCalendarPlanning(calStale)).toBe(false);
  });

  it("an expired activity is none-but-last-known, never finished and never sent", () => {
    const view = mailView(mailClaim(truthOf([MAIL_ACTIVITY("INBOX", "Fatura", "read_back")]), T0 + 60_000));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(true);
    expect(view.ageMs).toBe(60_000);
    expect(view.caption).toBe("Taslak okundu — onay bekliyor · Fatura");
    expect(view.caption).not.toContain("Gönderildi");
    expect(mailIsActive(view)).toBe(false);
  });

  it("is replaced by the agent's next word, and never outlives it", () => {
    const replaced = truthOf([MAIL_ACTIVITY(), CALENDAR_ACTIVITY(), AGENT_IDLE()]);
    expect(visualFor(replaced, T0).kind).toBe("idle");
    expect(coreClaim(replaced, T0).event?.state).toBe("agent.idle");
    // The cockpit's own claims still know what each channel was doing.
    expect(mailView(mailClaim(replaced, T0)).folder).toBe("INBOX");
    expect(calendarView(calendarClaim(replaced, T0)).range).toBe("today");
    expect(documentClaim(replaced, T0).event).toBeNull();
  });

  it("an unreachable API keeps the shape and the facts, and stops the motion", () => {
    let truth = truthOf([CALENDAR_ACTIVITY("today", "Ali ile toplantı", "read_back", 1)]);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unreachable");
    expect(intent.state).toBe("calendar.activity");
    expect(intent.calendar?.event).toBe("Ali ile toplantı");
    expect(intent.flowRate).toBe(0);
    expect(intent.ringSpin).toBe(0);
  });
});

describe("older servers", () => {
  it("draws a v5 feed without the mail and calendar tokens normally, and records the lag", () => {
    resetSequence();
    const reading = DOCUMENT_ANALYSIS();
    const v5 = applyResponse(
      emptyTruth(),
      { contract_version: 5, current: reading, events: [reading], sequence: reading.sequence },
      T0,
    );
    expect(v5.connection.kind).toBe("live");
    expect(v5.contractVersion).toBe(5);
    expect(visualFor(v5, T0).kind).toBe("document_analysis");
    expect(mailView(mailClaim(v5, T0)).lastKnown).toBeNull();
    expect(calendarView(calendarClaim(v5, T0)).lastKnown).toBeNull();
    expect(contractLagNote(5, KNOWN_CONTRACT_VERSION)).toContain("yok demek değil");
  });
});

describe("the voice overlay and the mail and calendar postures", () => {
  it("a local tool_running does not hide a live mail or calendar activity: the bus knows the folder and the draft", () => {
    const mail = intentOf([MAIL_ACTIVITY("INBOX", "Fatura")]);
    expect(applyVoiceOverlay(mail, VOICE_TOOL_RUNNING)).toBe(mail);
    const calendar = intentOf([CALENDAR_ACTIVITY("today")]);
    expect(applyVoiceOverlay(calendar, VOICE_TOOL_RUNNING)).toBe(calendar);
  });

  it("but every other local state, and a last-known activity, keep the overlay's precedence", () => {
    const mail = intentOf([MAIL_ACTIVITY()]);
    const speaking = applyVoiceOverlay(mail, { ...VOICE_TOOL_RUNNING, state: "speaking", outputLevel: 0.4 });
    expect(speaking.kind).toBe("speaking");
    expect(speaking.source).toBe("voice");

    const stale = visualFor(truthOf([CALENDAR_ACTIVITY()]), T0 + CALENDAR_ACTIVITY_TTL_MS + 1);
    const overStale = applyVoiceOverlay(stale, VOICE_TOOL_RUNNING);
    expect(overStale.kind).toBe("tool_running");
    expect(overStale.source).toBe("voice");
  });
});
