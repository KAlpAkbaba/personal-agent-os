/**
 * Contract v7: `artifact.factory` (M22 spec §6).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to a file the assistant makes for the owner: **the Core
 * names a title, a format, a verdict and a failing ref because the Cloud
 * Core published them — in the words the spec gives it — never a verdict it
 * inferred, never "doğrulandı" for a render whose validation nobody
 * published, never a render presented as done when the parser said it was
 * not.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v6 (or
 * v5, v4, v3, v2) is read normally, because v7 only added.
 */

import { describe, expect, it } from "vitest";

import {
  ARTIFACT_CAPTION_BARE,
  ARTIFACT_FACTORY as ARTIFACT_FACTORY_TOKEN,
  ARTIFACT_FACTORY_TTL_MS,
  ARTIFACT_STATES,
  ARTIFACT_VERDICTS,
  ARTIFACT_VERDICT_LABEL,
  CALENDAR_ACTIVITY_TTL_MS,
  CALENDAR_STATES,
  DOCUMENT_STATES,
  KNOWN_CONTRACT_VERSION,
  MAIL_ACTIVITY_TTL_MS,
  MAIL_DRAFT_STATES,
  MAIL_STATES,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STATES,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  contractCompatibility,
  isArtifactState,
  isArtifactVerdict,
  isCalendarState,
  isCoreChannel,
  isDocumentState,
  isKnownState,
  isMailState,
  isOperatorState,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  artifactCaption,
  artifactFacts,
  artifactFormatLabel,
  artifactIsMaking,
  artifactView,
} from "../../app/lib/uistate/artifacts";
import {
  ARTIFACT_EMPTY,
  ARTIFACT_LABEL,
  ARTIFACT_RENDER_UNVALIDATED,
  ARTIFACT_UNTOLD,
  KIND_DETAIL,
  KIND_LABEL,
  STATE_LABEL,
  artifactFactsLine,
  artifactVerdictWord,
  contractLagNote,
  stateLabel,
  subsystemLabel,
} from "../../app/lib/uistate/labels";
import {
  applyError,
  applyResponse,
  artifactClaim,
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
  isArtifactMaking,
  isCalendarPlanning,
  isDocumentReading,
  isMailReading,
  isOperatorActing,
  visualFor,
} from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  ARTIFACT_FACTORY,
  ARTIFACT_FACTORY_BARE,
  CALENDAR_ACTIVITY,
  DOCUMENT_ANALYSIS,
  MAIL_ACTIVITY,
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
  toolLabel: "Üretim",
  lastError: null,
};

const VALID = () => ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "valid");
const INVALID = () => ARTIFACT_FACTORY("Bütçe 2026", "pdf", "invalid", "sheet:Ozet!B5");

// ------------------------------------------------------------ the contract

describe("contract v7 is v6 plus the artifact state, and says so", () => {
  it("still reads a v7, v6, v5, v4, v3 and v2 server from a build at v7 or later", () => {
    // v8 (M23) bumped the build past this file's contract; the assertion is
    // relative, as the v6 file's became when v7 landed, so the v7 additions
    // stay proven without pinning the build to a version it has left.
    expect(KNOWN_CONTRACT_VERSION).toBeGreaterThanOrEqual(7);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION)).toBe("current");
    // v7 itself: current on a v7 build, a readable subset on any later one.
    const built: number = KNOWN_CONTRACT_VERSION;
    expect(contractCompatibility(7)).toBe(built === 7 ? "current" : "older_supported");
    expect(contractCompatibility(6)).toBe("older_supported");
    expect(contractCompatibility(5)).toBe("older_supported");
    expect(contractCompatibility(4)).toBe("older_supported");
    expect(contractCompatibility(3)).toBe("older_supported");
    expect(contractCompatibility(2)).toBe("older_supported");
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION + 1)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("names the one token once, and knows it by membership rather than by prefix", () => {
    expect(ARTIFACT_FACTORY_TOKEN).toBe("artifact.factory");
    expect(ARTIFACT_STATES).toEqual(["artifact.factory"]);
    expect(isKnownState("artifact.factory")).toBe(true);
    expect(isArtifactState("artifact.factory")).toBe(true);
    // A newer server's word is not a state this build may draw as making.
    expect(isArtifactState("artifact.deleted")).toBe(false);
    expect(isArtifactState("artifact.opened")).toBe(false);
    expect(isArtifactState("document.analysis")).toBe(false);
    expect(isMailState("artifact.factory")).toBe(false);
    expect(isCalendarState("artifact.factory")).toBe(false);
    expect(isDocumentState("artifact.factory")).toBe(false);
    expect(isOperatorState("artifact.factory")).toBe(false);
  });

  it("appends the token after v6's, never reordering", () => {
    expect(UI_STATES.indexOf("artifact.factory")).toBe(UI_STATES.indexOf("calendar.activity") + 1);
    // Appended after the last v6 token, never reordered; v8 appends after it in turn.
  });

  it("types the three verdicts, and admits nothing outside them", () => {
    expect(ARTIFACT_VERDICTS).toEqual(["rendering", "valid", "invalid"]);
    for (const v of ARTIFACT_VERDICTS) expect(isArtifactVerdict(v), v).toBe(true);
    expect(isArtifactVerdict("validated")).toBe(false);
    expect(isArtifactVerdict("VALID")).toBe(false);
    expect(isArtifactVerdict("ok")).toBe(false);
    expect(isArtifactVerdict(true)).toBe(false);
    expect(isArtifactVerdict(null)).toBe(false);
  });

  it("adds the artifacts subsystem and names it", () => {
    expect(SUBSYSTEMS).toContain("artifacts");
    expect(subsystemLabel("artifacts")).toBe("Üretim");
  });

  it("keeps the token on the agent channel, which drives the core body", () => {
    expect(stateChannel("artifact.factory")).toBe("agent");
    expect(isCoreChannel("artifact.factory")).toBe(true);
    // The room is untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("operator.running")).toBe("operator");
  });

  it("classifies it as transient, on the device round trip's horizon", () => {
    expect(stateKind("artifact.factory")).toBe("transient");
    expect(ARTIFACT_FACTORY_TTL_MS).toBe(45_000);
    expect(ARTIFACT_FACTORY_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(stateTtlMs("artifact.factory")).toBe(ARTIFACT_FACTORY_TTL_MS);
    // The publisher's own ttl_s still beats every figure here.
    expect(stateTtlMs("artifact.factory", ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "valid", null, { ttl_s: 600 }))).toBe(600_000);
  });

  it("gives the state the Turkish the spec asks for, spelled once", () => {
    expect(ARTIFACT_CAPTION_BARE).toBe("Dosya üretiliyor");
    expect(STATE_LABEL["artifact.factory"]).toBe(ARTIFACT_CAPTION_BARE);
    expect(KIND_LABEL.artifact_factory).toBe(ARTIFACT_CAPTION_BARE);
    expect(ARTIFACT_LABEL.making).toBe(ARTIFACT_CAPTION_BARE);
    expect(ARTIFACT_LABEL.none).not.toBe("Boşta");
    expect(stateLabel("artifact.factory")).not.toBe("artifact.factory");
    expect(ARTIFACT_EMPTY).toBe("Henüz bir şey üretilmedi.");
    expect(ARTIFACT_UNTOLD).not.toBe(ARTIFACT_EMPTY);
    // The verdicts, each a constant spelled once.
    expect(ARTIFACT_VERDICT_LABEL).toEqual({ rendering: "üretiliyor", valid: "doğrulandı", invalid: "doğrulanamadı" });
    expect(ARTIFACT_RENDER_UNVALIDATED).toBe("doğrulama bildirilmedi");
    expect(ARTIFACT_RENDER_UNVALIDATED).not.toBe(ARTIFACT_VERDICT_LABEL.invalid);
    // The detail says which side of "done" the posture is on.
    expect(KIND_DETAIL.artifact_factory).toContain("Doğrulanmamış bir çıktı bitmiş sayılmaz");
    expect(KIND_DETAIL.artifact_factory).toContain("İlerleme bildirilmez");
  });

  it("names exactly the families an older server will never publish", () => {
    const v6 = contractLagNote(6, 7);
    expect(v6).toContain("v6");
    expect(v6).toContain("v7");
    expect(v6).toContain("Dosya üretim durumu");
    expect(v6).not.toContain("posta");
    expect(v6).toContain("yok demek değil");

    const v5 = contractLagNote(5, 7);
    expect(v5).toContain("Posta ve takvim durumları");
    expect(v5).toContain("dosya üretim durumu");

    const v2 = contractLagNote(2, 7);
    expect(v2).toContain("Alarm ve ekran durumları");
    expect(v2).toContain("dijital operatör durumları");
    expect(v2).toContain("belge inceleme durumu");
    expect(v2).toContain("posta ve takvim durumları");
    expect(v2).toContain("dosya üretim durumu");

    // The v6 wording M21 shipped is unchanged for a v5 server seen from v6.
    expect(contractLagNote(5, 6)).toBe(
      "Sunucu durum sözleşmesi v5; bu arayüz v6. Posta ve takvim durumları bu sunucudan henüz yayınlanmıyor — yok demek değil.",
    );
  });

  it("leaves every v6 token, kind and horizon exactly as it was", () => {
    expect(MAIL_STATES).toEqual(["mail.activity"]);
    expect(CALENDAR_STATES).toEqual(["calendar.activity"]);
    expect(DOCUMENT_STATES).toEqual(["document.analysis"]);
    expect(OPERATOR_STATES).toEqual(["operator.running", "operator.verifying", "operator.failed"]);
    expect(MAIL_DRAFT_STATES).toEqual(["prepared", "read_back", "sending", "sent", "discarded"]);
    expect(stateKind("mail.activity")).toBe("transient");
    expect(stateKind("calendar.activity")).toBe("transient");
    expect(stateTtlMs("mail.activity")).toBe(MAIL_ACTIVITY_TTL_MS);
    expect(stateTtlMs("calendar.activity")).toBe(CALENDAR_ACTIVITY_TTL_MS);
    expect(stateKind("document.analysis")).toBe("transient");
    expect(stateKind("operator.failed")).toBe("steady");
    expect(stateTtlMs("agent.thinking")).toBe(TRANSIENT_TTL_MS);
    expect(stateKind("agent.idle")).toBe("steady");
    expect(UI_STATES.indexOf("mail.activity")).toBe(UI_STATES.indexOf("document.analysis") + 1);
    expect(UI_STATES.indexOf("calendar.activity")).toBe(UI_STATES.indexOf("mail.activity") + 1);
    // A v6 mail event still parses and draws as it did.
    const mail = intentOf([MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")]);
    expect(mail.kind).toBe("mail_activity");
    expect(mail.label).toBe("Taslak okundu — onay bekliyor · Proje planı");
    expect(mail.artifact).toBeNull();
    const calendar = intentOf([CALENDAR_ACTIVITY("today", "Ali ile toplantı", "prepared", 2)]);
    expect(calendar.kind).toBe("calendar_activity");
    expect(calendar.label).toBe("Öneri hazır — 2 çakışma · Ali ile toplantı");
    expect(calendar.artifact).toBeNull();
  });

  it("reads the v7 metadata through the same bounded parser, dropping nothing it needs and keeping nothing content-shaped", () => {
    const e = parseEvent({
      state: "artifact.factory",
      at: iso(0),
      metadata: {
        title: "Bütçe 2026",
        format: "xlsx",
        verdict: "invalid",
        failing_ref: "sheet:Ozet!B5",
        // A report is a row, not a token: content-shaped, dropped at the boundary like every object.
        validation: { elements: [{ ref: "sheet:Ozet!B5", ok: false }] },
        rows: [["kira", 12000]],
      },
    });
    expect(e?.metadata).toEqual({ title: "Bütçe 2026", format: "xlsx", verdict: "invalid", failing_ref: "sheet:Ozet!B5" });
    expect(e).not.toHaveProperty("refs");
  });
});

// ------------------------------------------------------------------- facts

describe("artifacts: facts, view and caption from published metadata only", () => {
  it("reads the title, the format, the verdict and the failing ref verbatim", () => {
    expect(artifactFacts(INVALID())).toEqual({
      title: "Bütçe 2026",
      format: "pdf",
      verdictToken: "invalid",
      verdict: "invalid",
      failingRef: "sheet:Ozet!B5",
    });
    expect(artifactFacts(VALID())).toEqual({ title: "Bütçe 2026", format: "xlsx", verdictToken: "valid", verdict: "valid", failingRef: null });
    expect(artifactFacts(null)).toEqual({ title: null, format: null, verdictToken: null, verdict: null, failingRef: null });
  });

  it("speaks the format upper-cased with the plain ASCII rule, and any token verbatim", () => {
    expect(artifactFormatLabel("xlsx")).toBe("XLSX");
    expect(artifactFormatLabel("pdf")).toBe("PDF");
    expect(artifactFormatLabel("PPTX")).toBe("PPTX");
    expect(artifactFormatLabel(" csv ")).toBe("CSV");
    // Never tr-TR: `i` stays `I`, so a token this build has not seen is still itself.
    expect(artifactFormatLabel("ini")).toBe("INI");
    expect(artifactFormatLabel(null)).toBeNull();
    expect(artifactFormatLabel("")).toBeNull();
    expect(artifactFormatLabel("   ")).toBeNull();
  });

  it("captions the spec's three sentences from the published facts", () => {
    expect(artifactCaption(artifactFacts(ARTIFACT_FACTORY("Bütçe 2026", null, "rendering")))).toBe("Bütçe 2026 üretiliyor");
    expect(artifactCaption(artifactFacts(ARTIFACT_FACTORY("Bütçe 2026", "pdf", "rendering")))).toBe("Bütçe 2026 · PDF üretiliyor");
    expect(artifactCaption(artifactFacts(VALID()))).toBe("Bütçe 2026 · XLSX · doğrulandı");
    expect(artifactCaption(artifactFacts(INVALID()))).toBe("Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5)");
    // No verdict at all is the factory still at work — the only thing it can mean.
    expect(artifactCaption(artifactFacts(ARTIFACT_FACTORY("Bütçe 2026", "xlsx", null)))).toBe("Bütçe 2026 · XLSX üretiliyor");
    // A verdict without a format names no format; an invalid verdict without a ref names no ref.
    expect(artifactCaption(artifactFacts(ARTIFACT_FACTORY("Toplantı notları", null, "valid")))).toBe("Toplantı notları · doğrulandı");
    expect(artifactCaption(artifactFacts(ARTIFACT_FACTORY("Toplantı notları", "docx", "invalid")))).toBe(
      "Toplantı notları · DOCX · doğrulanamadı",
    );
  });

  it("says only the plain state for a verdict it cannot read, and invents nothing without metadata", () => {
    // A newer publisher's `validated`: a word we cannot read is not a verdict we may narrate.
    const unknown = artifactFacts(ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "validated"));
    expect(unknown.verdictToken).toBe("validated");
    expect(unknown.verdict).toBeNull();
    expect(artifactCaption(unknown)).toBe(ARTIFACT_CAPTION_BARE);
    expect(artifactCaption(unknown)).not.toContain("doğrulandı");
    // The long line still says what WAS published, verbatim.
    expect(artifactFactsLine(unknown)).toBe("başlık: Bütçe 2026 · biçim: XLSX · sonuç: validated");

    // A verdict with no title is a verdict on nothing: the bare statement alone.
    expect(artifactCaption(artifactFacts(ARTIFACT_FACTORY(null, "xlsx", "valid")))).toBe(ARTIFACT_CAPTION_BARE);
    expect(artifactCaption(artifactFacts(ARTIFACT_FACTORY(null, "pdf", "invalid", "p3")))).toBe(ARTIFACT_CAPTION_BARE);

    const bare = artifactFacts(ARTIFACT_FACTORY_BARE());
    expect(bare).toEqual({ title: null, format: null, verdictToken: null, verdict: null, failingRef: null });
    expect(artifactCaption(bare)).toBe("Dosya üretiliyor");
    expect(artifactFactsLine(bare)).toBe("başlık bildirilmedi · biçim bildirilmedi · sonuç bildirilmedi");
  });

  it("the facts line names each fact or its absence, and the failing ref only beside an invalid verdict", () => {
    expect(artifactFactsLine(artifactFacts(VALID()))).toBe("başlık: Bütçe 2026 · biçim: XLSX · sonuç: doğrulandı");
    expect(artifactFactsLine(artifactFacts(INVALID()))).toBe(
      "başlık: Bütçe 2026 · biçim: PDF · sonuç: doğrulanamadı · yer: sheet:Ozet!B5",
    );
    expect(artifactFactsLine(artifactFacts(ARTIFACT_FACTORY("Sunum", "pptx", "invalid")))).toBe(
      "başlık: Sunum · biçim: PPTX · sonuç: doğrulanamadı · yer bildirilmedi",
    );
    expect(artifactFactsLine(artifactFacts(ARTIFACT_FACTORY("Sunum", null, "rendering")))).toBe(
      "başlık: Sunum · biçim bildirilmedi · sonuç: üretiliyor",
    );
    // A ref published beside a valid verdict is not a failure and is not printed as one.
    expect(artifactFactsLine(artifactFacts(ARTIFACT_FACTORY("Sunum", "pptx", "valid", "s2")))).toBe(
      "başlık: Sunum · biçim: PPTX · sonuç: doğrulandı",
    );
  });

  it("the verdict word is the spec's for the three it knows, the token for one it does not, and the statement that none came", () => {
    expect(artifactVerdictWord("valid")).toBe("doğrulandı");
    expect(artifactVerdictWord("invalid")).toBe("doğrulanamadı");
    expect(artifactVerdictWord("rendering")).toBe("üretiliyor");
    expect(artifactVerdictWord("validated")).toBe("validated");
    expect(artifactVerdictWord(null)).toBe(ARTIFACT_RENDER_UNVALIDATED);
  });

  it("the view is making while live, with the caption, and none-but-nothing before any factory event", () => {
    const view = artifactView(artifactClaim(truthOf([INVALID()]), T0));
    expect(view.stage).toBe("making");
    expect(view.lastKnown).toBe("making");
    expect(view.caption).toBe("Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5)");
    expect(view.taskId).toBe("art-task-1");
    expect(view.severity).toBe("info");
    expect(artifactIsMaking(view)).toBe(true);

    const none = artifactView(artifactClaim(truthOf([AGENT_IDLE(), OPERATOR_RUNNING(), MAIL_ACTIVITY(), CALENDAR_ACTIVITY()]), T0));
    expect(none.stage).toBe("none");
    expect(none.lastKnown).toBeNull();
    expect(none.title).toBeNull();
    expect(none.caption).toBe(ARTIFACT_CAPTION_BARE);
    expect(artifactIsMaking(none)).toBe(false);
  });

  it("the claim is by membership: a newer server's artifact word is not read as the factory", () => {
    const truth = truthOf([VALID(), event({ state: "artifact.opened", subsystem: "artifacts" })]);
    expect(artifactClaim(truth, T0).event?.state).toBe("artifact.factory");
    expect(visualFor(truth, T0).kind).toBe("unknown_state");
  });
});

// --------------------------------------------------------------- the Core

describe("the Core draws the making posture from the published state", () => {
  it("is its own calm making kind, captioned from the title, the format and the verdict", () => {
    const intent = intentOf([ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering")]);
    expect(intent.kind).toBe("artifact_factory");
    expect(intent.source).toBe("bus");
    expect(intent.subsystem).toBe("artifacts");
    expect(intent.palette).toBe("making");
    expect(intent.label).toBe("Bütçe 2026 · XLSX üretiliyor");
    expect(intent.artifact).toEqual({ title: "Bütçe 2026", format: "xlsx", verdictToken: "rendering", verdict: "rendering", failingRef: null });
    expect(intent.mail).toBeNull();
    expect(intent.calendar).toBeNull();
    expect(intent.document).toBeNull();
    expect(isArtifactMaking(intent)).toBe(true);
    expect(isMailReading(intent)).toBe(false);
    expect(isCalendarPlanning(intent)).toBe(false);
    expect(isDocumentReading(intent)).toBe(false);
    expect(isOperatorActing(intent)).toBe(false);

    // The posture: a file being written OUT — nothing flows inward, the
    // paths carry traffic, a faint lattice is laid — and nothing that could
    // be read as work being done for the owner beyond what was published.
    const reading = intentOf([DOCUMENT_ANALYSIS()]);
    const tool = intentOf([event({ state: "agent.tool_running" })]);
    const idle = intentOf([AGENT_IDLE()]);
    expect(intent.inwardFlow).toBe(0);
    expect(intent.flowRate).toBeGreaterThan(0);
    expect(intent.flowRate).toBeLessThan(tool.flowRate);
    expect(intent.topology).toBeGreaterThan(0);
    expect(intent.shellSpread).toBeGreaterThan(idle.shellSpread);
    expect(intent.shellSpread).toBeLessThan(tool.shellSpread);
    expect(intent.ringSpin).toBeGreaterThan(idle.ringSpin);
    expect(intent.ringSpin).toBeLessThan(reading.ringSpin);
    expect(intent.agitation).toBe(0);
    expect(intent.restraint).toBe(0);
    expect(intent.pulse).toBe(0);
  });

  it("a verdict stops the writing: valid glows a shade brighter, invalid is held and never dressed as done", () => {
    const rendering = intentOf([ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering")]);
    const valid = intentOf([VALID()]);
    const invalid = intentOf([INVALID()]);
    for (const settled of [valid, invalid]) {
      expect(settled.kind).toBe("artifact_factory");
      expect(settled.flowRate).toBe(0);
      expect(settled.ringSpin).toBeLessThan(rendering.ringSpin);
      expect(settled.agitation).toBe(0);
      expect(settled.pulse).toBe(0);
    }
    expect(valid.label).toBe("Bütçe 2026 · XLSX · doğrulandı");
    expect(valid.glow).toBeGreaterThan(rendering.glow);
    expect(valid.restraint).toBe(0);
    expect(invalid.label).toBe("Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5)");
    expect(invalid.label).not.toContain("doğrulandı");
    expect(invalid.glow).toBeLessThan(rendering.glow);
    expect(invalid.restraint).toBeGreaterThan(0);
    expect(invalid.artifact?.failingRef).toBe("sheet:Ozet!B5");
  });

  it("never draws progress, a constellation or a candidate: none is published", () => {
    for (const events of [[ARTIFACT_FACTORY()], [VALID()], [INVALID()], [ARTIFACT_FACTORY_BARE()]]) {
      const intent = intentOf(events);
      expect(intent.progress).toBeNull();
      expect(intent.constellationNodes).toBe(0);
      expect(intent.constellationDrift).toBe(0);
      expect(intent.capabilityNodes).toBe(0);
      expect(intent.satelliteComplete).toBe(false);
      expect(intent.pulse).toBe(0);
    }
  });

  it("captions the bare statement when the publisher named nothing", () => {
    expect(intentOf([ARTIFACT_FACTORY_BARE()]).label).toBe("Dosya üretiliyor");
    expect(intentOf([ARTIFACT_FACTORY_BARE()]).artifact).toEqual({ title: null, format: null, verdictToken: null, verdict: null, failingRef: null });
  });

  it("does not let the room displace a render in flight", () => {
    expect(intentOf([ARTIFACT_FACTORY(), OWNER_AWAY()]).kind).toBe("artifact_factory");
  });

  it("leaves every other kind's `artifact` null", () => {
    for (const events of [[AGENT_IDLE()], [OPERATOR_RUNNING()], [DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [CALENDAR_ACTIVITY()], [event({ state: "agent.tool_running" })]]) {
      expect(intentOf(events).artifact).toBeNull();
    }
  });
});

describe("honesty over time", () => {
  it("a factory event is live for its horizon and last-known after it, with its facts kept", () => {
    const truth = truthOf([INVALID()]);
    expect(visualFor(truth, T0 + ARTIFACT_FACTORY_TTL_MS - 1).kind).toBe("artifact_factory");

    const stale = visualFor(truth, T0 + ARTIFACT_FACTORY_TTL_MS + 1);
    expect(stale.kind).toBe("last_known");
    expect(stale.state).toBe("artifact.factory");
    for (const channel of MOTION_CHANNELS) expect(stale[channel], channel).toBe(0);
    // What it WAS doing is still a fact; the readout names it as last-known.
    expect(stale.label).toBe("Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5)");
    expect(stale.artifact?.failingRef).toBe("sheet:Ozet!B5");
    expect(isArtifactMaking(stale)).toBe(false);
  });

  it("an expired factory event is none-but-last-known, never finished and never validated", () => {
    const view = artifactView(artifactClaim(truthOf([ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering")]), T0 + 60_000));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBe("making");
    expect(view.expired).toBe(true);
    expect(view.ageMs).toBe(60_000);
    expect(view.caption).toBe("Bütçe 2026 · XLSX üretiliyor");
    expect(view.caption).not.toContain("doğrulandı");
    expect(artifactIsMaking(view)).toBe(false);
  });

  it("is replaced by the agent's next word, and never outlives it", () => {
    const replaced = truthOf([VALID(), AGENT_IDLE()]);
    expect(visualFor(replaced, T0).kind).toBe("idle");
    expect(coreClaim(replaced, T0).event?.state).toBe("agent.idle");
    // The cockpit's own claim still knows what the factory was doing.
    expect(artifactView(artifactClaim(replaced, T0)).title).toBe("Bütçe 2026");
    expect(mailClaim(replaced, T0).event).toBeNull();
    expect(calendarClaim(replaced, T0).event).toBeNull();
    expect(documentClaim(replaced, T0).event).toBeNull();
  });

  it("an unreachable API keeps the shape and the facts, and stops the motion", () => {
    let truth = truthOf([VALID()]);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unreachable");
    expect(intent.state).toBe("artifact.factory");
    expect(intent.artifact?.title).toBe("Bütçe 2026");
    expect(intent.flowRate).toBe(0);
    expect(intent.ringSpin).toBe(0);
  });
});

describe("older servers", () => {
  it("draws a v6 feed without the artifact token normally, and records the lag", () => {
    resetSequence();
    const mail = MAIL_ACTIVITY("INBOX");
    const v6 = applyResponse(emptyTruth(), { contract_version: 6, current: mail, events: [mail], sequence: mail.sequence }, T0);
    expect(v6.connection.kind).toBe("live");
    expect(v6.contractVersion).toBe(6);
    expect(visualFor(v6, T0).kind).toBe("mail_activity");
    expect(artifactView(artifactClaim(v6, T0)).lastKnown).toBeNull();
    expect(contractLagNote(6, KNOWN_CONTRACT_VERSION)).toContain("yok demek değil");
  });
});

describe("the voice overlay and the making posture", () => {
  it("a local tool_running does not hide a live factory event: the bus knows the title and the verdict", () => {
    const making = intentOf([ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering")]);
    expect(applyVoiceOverlay(making, VOICE_TOOL_RUNNING)).toBe(making);
    const checked = intentOf([INVALID()]);
    expect(applyVoiceOverlay(checked, VOICE_TOOL_RUNNING)).toBe(checked);
  });

  it("but every other local state, and a last-known factory shape, keep the overlay's precedence", () => {
    const making = intentOf([ARTIFACT_FACTORY()]);
    const speaking = applyVoiceOverlay(making, { ...VOICE_TOOL_RUNNING, state: "speaking", outputLevel: 0.4 });
    expect(speaking.kind).toBe("speaking");
    expect(speaking.source).toBe("voice");

    const stale = visualFor(truthOf([ARTIFACT_FACTORY()]), T0 + ARTIFACT_FACTORY_TTL_MS + 1);
    const overStale = applyVoiceOverlay(stale, VOICE_TOOL_RUNNING);
    expect(overStale.kind).toBe("tool_running");
    expect(overStale.source).toBe("voice");
  });
});
