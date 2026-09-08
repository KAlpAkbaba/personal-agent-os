/**
 * Contract v8: `app.factory` (M23 spec §6).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to a program the assistant makes for the owner: **the Core
 * names a project, a state, a port and two counts because the Cloud Core
 * published them — in the words the spec gives it — never a state it
 * inferred, never "çalışıyor" for a word it cannot read, never "testleri
 * geçti" without the counts that make it a pass, never an app presented as
 * done when its tests said it was not.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v7 (or
 * v6, v5, v4, v3, v2) is read normally, because v8 only added.
 */

import { describe, expect, it } from "vitest";

import {
  APP_CAPTION_BARE,
  APP_FACTORY as APP_FACTORY_TOKEN,
  APP_FACTORY_TTL_MS,
  APP_PROJECT_STATES,
  APP_STATES,
  APP_STATE_LABEL,
  ARTIFACT_FACTORY_TTL_MS,
  ARTIFACT_STATES,
  ARTIFACT_VERDICTS,
  CALENDAR_STATES,
  DOCUMENT_STATES,
  KNOWN_CONTRACT_VERSION,
  MAIL_STATES,
  MAX_PORT,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STATES,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  asPort,
  contractCompatibility,
  isAppProjectState,
  isAppState,
  isArtifactState,
  isCalendarState,
  isCoreChannel,
  isDocumentState,
  isKnownState,
  isMailState,
  isOperatorState,
  parseAppTestCounts,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  APP_HOST,
  appAddress,
  appCaption,
  appFacts,
  appIsActive,
  appIsServing,
  appTestCountsOf,
  appTestsPhrase,
  appTestsRatio,
  appUrl,
  appView,
} from "../../app/lib/uistate/apps";
import {
  APP_EMPTY,
  APP_LABEL,
  APP_UNTOLD,
  KIND_DETAIL,
  KIND_LABEL,
  STATE_LABEL,
  appFactsLine,
  appStateWord,
  contractLagNote,
  stateLabel,
  subsystemLabel,
} from "../../app/lib/uistate/labels";
import {
  appClaim,
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
  isAppBuilding,
  isAppServing,
  isArtifactMaking,
  isCalendarPlanning,
  isDocumentReading,
  isMailReading,
  isOperatorActing,
  visualFor,
} from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  APP_FACTORY_BARE,
  ARTIFACT_FACTORY,
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
  toolLabel: "Uygulamalar",
  lastError: null,
};

const PLANNED = () => APP_FACTORY("Görev Takip", "planned");
const SCAFFOLDED = () => APP_FACTORY("Görev Takip", "scaffolded");
const RUNNING = () => APP_FACTORY("Görev Takip", "running", 8123);
const RUNNING_NO_PORT = () => APP_FACTORY("Görev Takip", "running");
const TESTED = () => APP_FACTORY("Görev Takip", "tested", null, { passed: 12, failed: 0 });
const FAILED = () => APP_FACTORY("Görev Takip", "failed", null, { passed: 10, failed: 2 });
const STOPPED = () => APP_FACTORY("Görev Takip", "stopped");

// ------------------------------------------------------------ the contract

describe("contract v8 is v7 plus the app state, and says so", () => {
  it("is version 8 and still reads a v7, v6, v5, v4, v3 and v2 server", () => {
    expect(KNOWN_CONTRACT_VERSION).toBe(8);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(8)).toBe("current");
    expect(contractCompatibility(7)).toBe("older_supported");
    expect(contractCompatibility(6)).toBe("older_supported");
    expect(contractCompatibility(5)).toBe("older_supported");
    expect(contractCompatibility(4)).toBe("older_supported");
    expect(contractCompatibility(3)).toBe("older_supported");
    expect(contractCompatibility(2)).toBe("older_supported");
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(9)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("names the one token once, and knows it by membership rather than by prefix", () => {
    expect(APP_FACTORY_TOKEN).toBe("app.factory");
    expect(APP_STATES).toEqual(["app.factory"]);
    expect(isKnownState("app.factory")).toBe(true);
    expect(isAppState("app.factory")).toBe(true);
    // A newer server's word is not a state this build may draw as building.
    expect(isAppState("app.deleted")).toBe(false);
    expect(isAppState("app.opened")).toBe(false);
    expect(isAppState("artifact.factory")).toBe(false);
    expect(isArtifactState("app.factory")).toBe(false);
    expect(isMailState("app.factory")).toBe(false);
    expect(isCalendarState("app.factory")).toBe(false);
    expect(isDocumentState("app.factory")).toBe(false);
    expect(isOperatorState("app.factory")).toBe(false);
  });

  it("appends the token after v7's, never reordering", () => {
    expect(UI_STATES.indexOf("app.factory")).toBe(UI_STATES.indexOf("artifact.factory") + 1);
    expect(UI_STATES[UI_STATES.length - 1]).toBe("app.factory");
  });

  it("types the six project states, and admits nothing outside them", () => {
    expect(APP_PROJECT_STATES).toEqual(["planned", "scaffolded", "running", "tested", "failed", "stopped"]);
    for (const s of APP_PROJECT_STATES) expect(isAppProjectState(s), s).toBe(true);
    expect(isAppProjectState("built")).toBe(false);
    expect(isAppProjectState("RUNNING")).toBe(false);
    expect(isAppProjectState("ok")).toBe(false);
    expect(isAppProjectState(true)).toBe(false);
    expect(isAppProjectState(null)).toBe(false);
  });

  it("reads a port as a port, and nothing else as one", () => {
    expect(MAX_PORT).toBe(65_535);
    expect(asPort(8123)).toBe(8123);
    expect(asPort(1)).toBe(1);
    expect(asPort(65_535)).toBe(65_535);
    expect(asPort(0)).toBeNull();
    expect(asPort(-1)).toBeNull();
    expect(asPort(65_536)).toBeNull();
    expect(asPort(8123.5)).toBeNull();
    expect(asPort("8123")).toBeNull();
    expect(asPort(null)).toBeNull();
    expect(asPort(undefined)).toBeNull();
  });

  it("reads the counts only when both were sent as whole non-negative numbers", () => {
    expect(parseAppTestCounts({ passed: 12, failed: 0 })).toEqual({ passed: 12, failed: 0 });
    expect(parseAppTestCounts({ passed: 0, failed: 0 })).toEqual({ passed: 0, failed: 0 });
    // One figure is not a result: nobody said how many ran.
    expect(parseAppTestCounts({ passed: 12 })).toBeNull();
    expect(parseAppTestCounts({ failed: 2 })).toBeNull();
    expect(parseAppTestCounts({ passed: "12", failed: 0 })).toBeNull();
    expect(parseAppTestCounts({ passed: 1.5, failed: 0 })).toBeNull();
    expect(parseAppTestCounts({ passed: -1, failed: 0 })).toBeNull();
    expect(parseAppTestCounts([12, 0])).toBeNull();
    expect(parseAppTestCounts("12/12")).toBeNull();
    expect(parseAppTestCounts(null)).toBeNull();
  });

  it("adds the apps subsystem and names it", () => {
    expect(SUBSYSTEMS).toContain("apps");
    expect(subsystemLabel("apps")).toBe("Uygulamalar");
  });

  it("keeps the token on the agent channel, which drives the core body", () => {
    expect(stateChannel("app.factory")).toBe("agent");
    expect(isCoreChannel("app.factory")).toBe(true);
    // The room is untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("operator.running")).toBe("operator");
  });

  it("classifies it as transient, on the device round trip's horizon", () => {
    expect(stateKind("app.factory")).toBe("transient");
    expect(APP_FACTORY_TTL_MS).toBe(45_000);
    expect(APP_FACTORY_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(stateTtlMs("app.factory")).toBe(APP_FACTORY_TTL_MS);
    // The publisher's own ttl_s still beats every figure here: a running app
    // may be claimed as running for as long as the publisher said.
    expect(stateTtlMs("app.factory", APP_FACTORY("Görev Takip", "running", 8123, null, { ttl_s: 1800 }))).toBe(1_800_000);
  });

  it("gives the state the Turkish the spec asks for, spelled once", () => {
    expect(APP_CAPTION_BARE).toBe("Uygulama yapılıyor");
    expect(STATE_LABEL["app.factory"]).toBe(APP_CAPTION_BARE);
    expect(KIND_LABEL.app_factory).toBe(APP_CAPTION_BARE);
    expect(APP_LABEL.active).toBe(APP_CAPTION_BARE);
    expect(APP_LABEL.none).not.toBe("Boşta");
    expect(stateLabel("app.factory")).not.toBe("app.factory");
    expect(APP_EMPTY).toBe("Henüz bir uygulama yapılmadı.");
    expect(APP_UNTOLD).not.toBe(APP_EMPTY);
    // The six states, each a constant spelled once.
    expect(APP_STATE_LABEL).toEqual({
      planned: "planlandı",
      scaffolded: "iskeleti kuruluyor",
      running: "çalışıyor",
      tested: "test edildi",
      failed: "başarısız",
      stopped: "durduruldu",
    });
    // "Çalışıyor" is a word for exactly one state.
    expect(Object.values(APP_STATE_LABEL).filter((w) => w === "çalışıyor")).toHaveLength(1);
    // The detail says which side of "done" the posture is on.
    expect(KIND_DETAIL.app_factory).toContain("Testleri geçmemiş bir uygulama bitmiş sayılmaz");
    expect(KIND_DETAIL.app_factory).toContain("İlerleme bildirilmez");
    expect(KIND_DETAIL.app_factory).toContain("sahibin makinesinde");
  });

  it("names exactly the families an older server will never publish", () => {
    const v7 = contractLagNote(7, 8);
    expect(v7).toContain("v7");
    expect(v7).toContain("v8");
    expect(v7).toContain("Uygulama üretim durumu");
    expect(v7).not.toContain("dosya");
    expect(v7).toContain("yok demek değil");

    const v6 = contractLagNote(6, 8);
    expect(v6).toContain("Dosya üretim durumu");
    expect(v6).toContain("uygulama üretim durumu");

    const v2 = contractLagNote(2, 8);
    expect(v2).toContain("Alarm ve ekran durumları");
    expect(v2).toContain("dijital operatör durumları");
    expect(v2).toContain("belge inceleme durumu");
    expect(v2).toContain("posta ve takvim durumları");
    expect(v2).toContain("dosya üretim durumu");
    expect(v2).toContain("uygulama üretim durumu");

    // The v7 wording M22 shipped is unchanged for a v6 server seen from v7.
    expect(contractLagNote(6, 7)).toBe(
      "Sunucu durum sözleşmesi v6; bu arayüz v7. Dosya üretim durumu bu sunucudan henüz yayınlanmıyor — yok demek değil.",
    );
  });

  it("leaves every v7 token, kind and horizon exactly as it was", () => {
    expect(ARTIFACT_STATES).toEqual(["artifact.factory"]);
    expect(ARTIFACT_VERDICTS).toEqual(["rendering", "valid", "invalid"]);
    expect(MAIL_STATES).toEqual(["mail.activity"]);
    expect(CALENDAR_STATES).toEqual(["calendar.activity"]);
    expect(DOCUMENT_STATES).toEqual(["document.analysis"]);
    expect(OPERATOR_STATES).toEqual(["operator.running", "operator.verifying", "operator.failed"]);
    expect(stateKind("artifact.factory")).toBe("transient");
    expect(stateTtlMs("artifact.factory")).toBe(ARTIFACT_FACTORY_TTL_MS);
    expect(stateKind("mail.activity")).toBe("transient");
    expect(stateKind("document.analysis")).toBe("transient");
    expect(stateKind("operator.failed")).toBe("steady");
    expect(stateTtlMs("agent.thinking")).toBe(TRANSIENT_TTL_MS);
    expect(stateKind("agent.idle")).toBe("steady");
    expect(UI_STATES.indexOf("artifact.factory")).toBe(UI_STATES.indexOf("calendar.activity") + 1);
    // A v7 factory event still parses and draws as it did.
    const making = intentOf([ARTIFACT_FACTORY("Bütçe 2026", "pdf", "invalid", "sheet:Ozet!B5")]);
    expect(making.kind).toBe("artifact_factory");
    expect(making.label).toBe("Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5)");
    expect(making.app).toBeNull();
    const mail = intentOf([MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")]);
    expect(mail.kind).toBe("mail_activity");
    expect(mail.label).toBe("Taslak okundu — onay bekliyor · Proje planı");
    expect(mail.app).toBeNull();
  });

  it("reads the v8 metadata through the same bounded parser: the tokens in metadata, the counts beside it, nothing content-shaped", () => {
    const e = parseEvent({
      state: "app.factory",
      at: iso(0),
      metadata: {
        project: "Görev Takip",
        state: "tested",
        port: 8123,
        tests: { passed: 12, failed: 0, report_tail: "ok" },
        // A file list is content, not a token: dropped at the boundary like every other object.
        files: [{ path: "index.html", text: "<html>" }],
        manifest: { run: "python -m http.server" },
      },
    });
    expect(e?.metadata).toEqual({ project: "Görev Takip", state: "tested", port: 8123 });
    expect(e?.tests).toEqual({ passed: 12, failed: 0 });
    expect(e).not.toHaveProperty("refs");

    // Counts that are not counts ride nowhere, and a v7 event has no `tests` key at all.
    const half = parseEvent({ state: "app.factory", at: iso(0), metadata: { project: "X", tests: { passed: 12 } } });
    expect(half).not.toHaveProperty("tests");
    const v7 = parseEvent({ state: "artifact.factory", at: iso(0), metadata: { title: "Bütçe 2026" } });
    expect(v7).not.toHaveProperty("tests");
  });
});

// ------------------------------------------------------------------- facts

describe("apps: facts, view and caption from published metadata only", () => {
  it("reads the project, the state, the port and the counts verbatim", () => {
    expect(appFacts(RUNNING())).toEqual({ project: "Görev Takip", stateToken: "running", state: "running", port: 8123, tests: null });
    expect(appFacts(FAILED())).toEqual({
      project: "Görev Takip",
      stateToken: "failed",
      state: "failed",
      port: null,
      tests: { passed: 10, failed: 2 },
    });
    expect(appFacts(null)).toEqual({ project: null, stateToken: null, state: null, port: null, tests: null });
  });

  it("reads the counts from the structured value, or from the flat pair a flattening publisher sends — never from one figure", () => {
    expect(appTestCountsOf(TESTED())).toEqual({ passed: 12, failed: 0 });
    expect(appTestCountsOf(APP_FACTORY("Görev Takip", "tested", null, null, { tests_passed: 12, tests_failed: 0 }))).toEqual({ passed: 12, failed: 0 });
    expect(appTestCountsOf(APP_FACTORY("Görev Takip", "tested", null, null, { tests_passed: 12 }))).toBeNull();
    expect(appTestCountsOf(APP_FACTORY("Görev Takip", "tested", null, null, { tests_passed: 1.5, tests_failed: 0 }))).toBeNull();
    expect(appTestCountsOf(APP_FACTORY("Görev Takip", "tested", null, null, { tests_passed: -1, tests_failed: 0 }))).toBeNull();
    // The structured value wins when both were sent: it is the contract's shape.
    expect(appTestCountsOf(APP_FACTORY("Görev Takip", "tested", null, { passed: 12, failed: 0 }, { tests_passed: 3, tests_failed: 3 }))).toEqual({ passed: 12, failed: 0 });
    expect(appTestCountsOf(SCAFFOLDED())).toBeNull();
    expect(appTestCountsOf(null)).toBeNull();
  });

  it("speaks the address only from a port, on the loopback host and nowhere else", () => {
    expect(APP_HOST).toBe("127.0.0.1");
    expect(appAddress(8123)).toBe("127.0.0.1:8123");
    expect(appUrl(8123)).toBe("http://127.0.0.1:8123/");
    expect(appAddress(null)).toBeNull();
    expect(appUrl(null)).toBeNull();
    // A port that is not one never becomes an address (the parser already refused it).
    expect(appFacts(APP_FACTORY("Görev Takip", "running", 0)).port).toBeNull();
    expect(appFacts(APP_FACTORY("Görev Takip", "running", 70_000)).port).toBeNull();
    expect(appUrl(appFacts(APP_FACTORY("Görev Takip", "running", 70_000)).port)).toBeNull();
  });

  it("captions every state in the spec's words from the published facts", () => {
    expect(appCaption(appFacts(PLANNED()))).toBe("Görev Takip planlandı");
    expect(appCaption(appFacts(SCAFFOLDED()))).toBe("Görev Takip iskeleti kuruluyor");
    expect(appCaption(appFacts(RUNNING()))).toBe("Görev Takip çalışıyor · 127.0.0.1:8123");
    expect(appCaption(appFacts(RUNNING_NO_PORT()))).toBe("Görev Takip çalışıyor");
    expect(appCaption(appFacts(TESTED()))).toBe("Görev Takip testleri geçti (12/12)");
    expect(appCaption(appFacts(FAILED()))).toBe("Görev Takip başarısız — 2 test");
    expect(appCaption(appFacts(STOPPED()))).toBe("Görev Takip durduruldu");
    // A project with no state at all is the factory at work on it.
    expect(appCaption(appFacts(APP_FACTORY("Görev Takip", null)))).toBe("Görev Takip yapılıyor");
  });

  it("says a pass only from counts, and a failure only with its count", () => {
    // `tested` with no counts: tested, not passed — nobody said how many ran.
    const untold = appCaption(appFacts(APP_FACTORY("Görev Takip", "tested")));
    expect(untold).toBe("Görev Takip test edildi");
    expect(untold).not.toContain("geçti");
    // `tested` with a failure among the counts is worded with it, never as a pass.
    const mixed = appCaption(appFacts(APP_FACTORY("Görev Takip", "tested", null, { passed: 10, failed: 2 })));
    expect(mixed).toBe("Görev Takip test edildi (10/12 geçti)");
    expect(mixed).not.toContain("testleri geçti");
    // `tested` with nothing run is not a pass either.
    expect(appCaption(appFacts(APP_FACTORY("Görev Takip", "tested", null, { passed: 0, failed: 0 })))).toBe("Görev Takip testleri geçti (0/0)");
    // `failed` with no counts, or with no failing count, names no number.
    expect(appCaption(appFacts(APP_FACTORY("Görev Takip", "failed")))).toBe("Görev Takip başarısız");
    expect(appCaption(appFacts(APP_FACTORY("Görev Takip", "failed", null, { passed: 12, failed: 0 })))).toBe("Görev Takip başarısız");
    // A port beside a state that is not running is a fact for the line, not the caption.
    expect(appCaption(appFacts(APP_FACTORY("Görev Takip", "stopped", 8123)))).toBe("Görev Takip durduruldu");
  });

  it("says only the plain state for a word it cannot read, and invents nothing without metadata", () => {
    // A newer publisher's `built`: a word we cannot read is not a step we may narrate.
    const unknown = appFacts(APP_FACTORY("Görev Takip", "built", 8123));
    expect(unknown.stateToken).toBe("built");
    expect(unknown.state).toBeNull();
    expect(appCaption(unknown)).toBe(APP_CAPTION_BARE);
    expect(appCaption(unknown)).not.toContain("çalışıyor");
    // The long line still says what WAS published, verbatim.
    expect(appFactsLine(unknown)).toBe("proje: Görev Takip · durum: built · port: 8123");

    // A state with no project is a state of nothing: the bare statement alone.
    expect(appCaption(appFacts(APP_FACTORY(null, "running", 8123)))).toBe(APP_CAPTION_BARE);
    expect(appCaption(appFacts(APP_FACTORY(null, "tested", null, { passed: 12, failed: 0 })))).toBe(APP_CAPTION_BARE);

    const bare = appFacts(APP_FACTORY_BARE());
    expect(bare).toEqual({ project: null, stateToken: null, state: null, port: null, tests: null });
    expect(appCaption(bare)).toBe("Uygulama yapılıyor");
    expect(appFactsLine(bare)).toBe("proje bildirilmedi · durum bildirilmedi");
  });

  it("the facts line names each fact or its absence, the port's absence only beside running and the counts' only beside a result", () => {
    expect(appFactsLine(appFacts(SCAFFOLDED()))).toBe("proje: Görev Takip · durum: iskeleti kuruluyor");
    expect(appFactsLine(appFacts(RUNNING()))).toBe("proje: Görev Takip · durum: çalışıyor · port: 8123");
    expect(appFactsLine(appFacts(RUNNING_NO_PORT()))).toBe("proje: Görev Takip · durum: çalışıyor · port bildirilmedi");
    expect(appFactsLine(appFacts(TESTED()))).toBe("proje: Görev Takip · durum: test edildi · testler: 12 geçti / 0 başarısız");
    expect(appFactsLine(appFacts(FAILED()))).toBe("proje: Görev Takip · durum: başarısız · testler: 10 geçti / 2 başarısız");
    expect(appFactsLine(appFacts(APP_FACTORY("Görev Takip", "tested")))).toBe("proje: Görev Takip · durum: test edildi · test sayısı bildirilmedi");
    expect(appFactsLine(appFacts(APP_FACTORY("Görev Takip", "failed")))).toBe("proje: Görev Takip · durum: başarısız · test sayısı bildirilmedi");
    expect(appFactsLine(appFacts(STOPPED()))).toBe("proje: Görev Takip · durum: durduruldu");
    // A port or counts published beside any state are printed: they are facts.
    expect(appFactsLine(appFacts(APP_FACTORY("Görev Takip", "stopped", 8123, { passed: 12, failed: 0 })))).toBe(
      "proje: Görev Takip · durum: durduruldu · port: 8123 · testler: 12 geçti / 0 başarısız",
    );
  });

  it("the state word is the spec's for the six it knows, the token for one it does not, and the statement that none came", () => {
    for (const s of APP_PROJECT_STATES) expect(appStateWord(s)).toBe(APP_STATE_LABEL[s]);
    expect(appStateWord("built")).toBe("built");
    expect(appStateWord(null)).toBe("durum bildirilmedi");
    expect(appTestsPhrase({ passed: 12, failed: 0 })).toBe("12 geçti / 0 başarısız");
    expect(appTestsPhrase(null)).toBeNull();
    expect(appTestsRatio({ passed: 10, failed: 2 })).toBe("(10/12)");
    expect(appTestsRatio(null)).toBeNull();
  });

  it("the view is active while live, with the caption, and none-but-nothing before any app event", () => {
    const view = appView(appClaim(truthOf([RUNNING()]), T0));
    expect(view.stage).toBe("active");
    expect(view.lastKnown).toBe("active");
    expect(view.caption).toBe("Görev Takip çalışıyor · 127.0.0.1:8123");
    expect(view.taskId).toBe("app-task-1");
    expect(view.severity).toBe("info");
    expect(appIsActive(view)).toBe(true);
    expect(appIsServing(view)).toBe(true);
    expect(appIsServing(appView(appClaim(truthOf([RUNNING_NO_PORT()]), T0)))).toBe(false);
    expect(appIsServing(appView(appClaim(truthOf([APP_FACTORY("Görev Takip", "stopped", 8123)]), T0)))).toBe(false);

    const none = appView(appClaim(truthOf([AGENT_IDLE(), OPERATOR_RUNNING(), MAIL_ACTIVITY(), ARTIFACT_FACTORY()]), T0));
    expect(none.stage).toBe("none");
    expect(none.lastKnown).toBeNull();
    expect(none.project).toBeNull();
    expect(none.caption).toBe(APP_CAPTION_BARE);
    expect(appIsActive(none)).toBe(false);
  });

  it("the claim is by membership: a newer server's app word is not read as the factory", () => {
    const truth = truthOf([RUNNING(), event({ state: "app.opened", subsystem: "apps" })]);
    expect(appClaim(truth, T0).event?.state).toBe("app.factory");
    expect(visualFor(truth, T0).kind).toBe("unknown_state");
  });
});

// --------------------------------------------------------------- the Core

describe("the Core draws the building posture from the published state", () => {
  it("is its own calm building kind while planned or scaffolded, captioned from the project and the state", () => {
    const intent = intentOf([SCAFFOLDED()]);
    expect(intent.kind).toBe("app_factory");
    expect(intent.source).toBe("bus");
    expect(intent.subsystem).toBe("apps");
    expect(intent.palette).toBe("making");
    expect(intent.label).toBe("Görev Takip iskeleti kuruluyor");
    expect(intent.app).toEqual({ project: "Görev Takip", stateToken: "scaffolded", state: "scaffolded", port: null, tests: null });
    expect(intent.artifact).toBeNull();
    expect(intent.mail).toBeNull();
    expect(intent.calendar).toBeNull();
    expect(intent.document).toBeNull();
    expect(isAppBuilding(intent)).toBe(true);
    expect(isAppServing(intent)).toBe(false);
    expect(isArtifactMaking(intent)).toBe(false);
    expect(isMailReading(intent)).toBe(false);
    expect(isCalendarPlanning(intent)).toBe(false);
    expect(isDocumentReading(intent)).toBe(false);
    expect(isOperatorActing(intent)).toBe(false);

    // The posture: files being written OUT — nothing flows inward, the paths
    // carry traffic, a faint lattice is laid — and nothing that could be read
    // as work done for the owner beyond what was published.
    const tool = intentOf([event({ state: "agent.tool_running" })]);
    const idle = intentOf([AGENT_IDLE()]);
    expect(intent.inwardFlow).toBe(0);
    expect(intent.flowRate).toBeGreaterThan(0);
    expect(intent.flowRate).toBeLessThan(tool.flowRate);
    expect(intent.topology).toBeGreaterThan(0);
    expect(intent.shellSpread).toBeGreaterThan(idle.shellSpread);
    expect(intent.ringSpin).toBeGreaterThan(idle.ringSpin);
    expect(intent.agitation).toBe(0);
    expect(intent.restraint).toBe(0);
    expect(intent.pulse).toBe(0);
    // Planned is the same building posture, with its own word.
    const planned = intentOf([PLANNED()]);
    expect(planned.kind).toBe("app_factory");
    expect(planned.label).toBe("Görev Takip planlandı");
    expect(planned.flowRate).toBe(intent.flowRate);
  });

  it("a running app on a named port is a distinct posture: the lattice stands, the paths carry steady traffic, the glow is brighter", () => {
    const building = intentOf([SCAFFOLDED()]);
    const running = intentOf([RUNNING()]);
    expect(running.kind).toBe("app_factory");
    expect(running.label).toBe("Görev Takip çalışıyor · 127.0.0.1:8123");
    expect(isAppServing(running)).toBe(true);
    expect(running.topology).toBeGreaterThan(building.topology);
    expect(running.glow).toBeGreaterThan(building.glow);
    expect(running.shellSpread).toBeGreaterThan(building.shellSpread);
    expect(running.ringSpin).toBeGreaterThan(building.ringSpin);
    expect(running.flowRate).toBeGreaterThan(0);
    expect(running.inwardFlow).toBe(0);
    expect(running.agitation).toBe(0);
    expect(running.restraint).toBe(0);
    expect(running.pulse).toBe(0);
    expect(running.app?.port).toBe(8123);

    // `running` with no port keeps the running shape and glow, and its paths
    // are still: a server nobody gave an address is not drawn as answering.
    const unaddressed = intentOf([RUNNING_NO_PORT()]);
    expect(unaddressed.kind).toBe("app_factory");
    expect(unaddressed.label).toBe("Görev Takip çalışıyor");
    expect(isAppServing(unaddressed)).toBe(false);
    expect(unaddressed.flowRate).toBe(0);
    expect(unaddressed.glow).toBe(running.glow);
    expect(unaddressed.topology).toBe(running.topology);
  });

  it("a result stops the building: tested glows a shade brighter, failed is held and never dressed as done, stopped is still and dim", () => {
    const building = intentOf([SCAFFOLDED()]);
    const tested = intentOf([TESTED()]);
    const failed = intentOf([FAILED()]);
    const stopped = intentOf([STOPPED()]);
    for (const settled of [tested, failed, stopped]) {
      expect(settled.kind).toBe("app_factory");
      expect(settled.flowRate).toBe(0);
      expect(settled.ringSpin).toBeLessThan(building.ringSpin);
      expect(settled.agitation).toBe(0);
      expect(settled.pulse).toBe(0);
    }
    expect(tested.label).toBe("Görev Takip testleri geçti (12/12)");
    expect(tested.glow).toBeGreaterThan(building.glow);
    expect(tested.restraint).toBe(0);
    expect(failed.label).toBe("Görev Takip başarısız — 2 test");
    expect(failed.label).not.toContain("geçti");
    expect(failed.glow).toBeLessThan(building.glow);
    expect(failed.restraint).toBeGreaterThan(0);
    expect(failed.app?.tests).toEqual({ passed: 10, failed: 2 });
    expect(stopped.label).toBe("Görev Takip durduruldu");
    expect(stopped.glow).toBeLessThan(building.glow);
    expect(stopped.restraint).toBe(0);
  });

  it("never draws progress, a constellation or a candidate: none is published", () => {
    for (const events of [[PLANNED()], [SCAFFOLDED()], [RUNNING()], [TESTED()], [FAILED()], [STOPPED()], [APP_FACTORY_BARE()]]) {
      const intent = intentOf(events);
      expect(intent.progress).toBeNull();
      expect(intent.constellationNodes).toBe(0);
      expect(intent.constellationDrift).toBe(0);
      expect(intent.capabilityNodes).toBe(0);
      expect(intent.satelliteComplete).toBe(false);
      expect(intent.pulse).toBe(0);
    }
  });

  it("captions the bare statement when the publisher named nothing, and draws a word it cannot read as building", () => {
    expect(intentOf([APP_FACTORY_BARE()]).label).toBe("Uygulama yapılıyor");
    expect(intentOf([APP_FACTORY_BARE()]).app).toEqual({ project: null, stateToken: null, state: null, port: null, tests: null });
    const unknown = intentOf([APP_FACTORY("Görev Takip", "built", 8123)]);
    expect(unknown.kind).toBe("app_factory");
    expect(unknown.label).toBe(APP_CAPTION_BARE);
    expect(isAppServing(unknown)).toBe(false);
    expect(unknown.flowRate).toBe(intentOf([SCAFFOLDED()]).flowRate);
  });

  it("does not let the room displace a build in flight", () => {
    expect(intentOf([RUNNING(), OWNER_AWAY()]).kind).toBe("app_factory");
  });

  it("leaves every other kind's `app` null", () => {
    for (const events of [
      [AGENT_IDLE()],
      [OPERATOR_RUNNING()],
      [DOCUMENT_ANALYSIS()],
      [MAIL_ACTIVITY()],
      [CALENDAR_ACTIVITY()],
      [ARTIFACT_FACTORY()],
      [event({ state: "agent.tool_running" })],
    ]) {
      expect(intentOf(events).app).toBeNull();
    }
  });
});

describe("honesty over time", () => {
  it("an app event is live for its horizon and last-known after it, with its facts kept", () => {
    const truth = truthOf([RUNNING()]);
    expect(visualFor(truth, T0 + APP_FACTORY_TTL_MS - 1).kind).toBe("app_factory");

    const stale = visualFor(truth, T0 + APP_FACTORY_TTL_MS + 1);
    expect(stale.kind).toBe("last_known");
    expect(stale.state).toBe("app.factory");
    for (const channel of MOTION_CHANNELS) expect(stale[channel], channel).toBe(0);
    // What it WAS doing is still a fact; the readout names it as last-known —
    // and a last-known running shape is not a server answering.
    expect(stale.label).toBe("Görev Takip çalışıyor · 127.0.0.1:8123");
    expect(stale.app?.port).toBe(8123);
    expect(isAppBuilding(stale)).toBe(false);
    expect(isAppServing(stale)).toBe(false);
  });

  it("an expired app event is none-but-last-known, never finished and never passed", () => {
    const view = appView(appClaim(truthOf([SCAFFOLDED()]), T0 + 60_000));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(true);
    expect(view.ageMs).toBe(60_000);
    expect(view.caption).toBe("Görev Takip iskeleti kuruluyor");
    expect(view.caption).not.toContain("geçti");
    expect(appIsActive(view)).toBe(false);
  });

  it("is replaced by the agent's next word, and never outlives it", () => {
    const replaced = truthOf([RUNNING(), AGENT_IDLE()]);
    expect(visualFor(replaced, T0).kind).toBe("idle");
    expect(coreClaim(replaced, T0).event?.state).toBe("agent.idle");
    // The cockpit's own claim still knows what the factory was doing.
    expect(appView(appClaim(replaced, T0)).project).toBe("Görev Takip");
    expect(artifactClaim(replaced, T0).event).toBeNull();
    expect(mailClaim(replaced, T0).event).toBeNull();
    expect(calendarClaim(replaced, T0).event).toBeNull();
    expect(documentClaim(replaced, T0).event).toBeNull();
  });

  it("an unreachable API keeps the shape and the facts, and stops the motion", () => {
    let truth = truthOf([RUNNING()]);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unreachable");
    expect(intent.state).toBe("app.factory");
    expect(intent.app?.project).toBe("Görev Takip");
    expect(intent.flowRate).toBe(0);
    expect(intent.ringSpin).toBe(0);
    expect(isAppServing(intent)).toBe(false);
  });
});

describe("older servers", () => {
  it("draws a v7 feed without the app token normally, and records the lag", () => {
    resetSequence();
    const making = ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering");
    const v7 = applyResponse(emptyTruth(), { contract_version: 7, current: making, events: [making], sequence: making.sequence }, T0);
    expect(v7.connection.kind).toBe("live");
    expect(v7.contractVersion).toBe(7);
    expect(visualFor(v7, T0).kind).toBe("artifact_factory");
    expect(appView(appClaim(v7, T0)).lastKnown).toBeNull();
    expect(contractLagNote(7, KNOWN_CONTRACT_VERSION)).toContain("yok demek değil");
  });
});

describe("the voice overlay and the building posture", () => {
  it("a local tool_running does not hide a live app event: the bus knows the project and the port", () => {
    const building = intentOf([SCAFFOLDED()]);
    expect(applyVoiceOverlay(building, VOICE_TOOL_RUNNING)).toBe(building);
    const running = intentOf([RUNNING()]);
    expect(applyVoiceOverlay(running, VOICE_TOOL_RUNNING)).toBe(running);
  });

  it("but every other local state, and a last-known app shape, keep the overlay's precedence", () => {
    const building = intentOf([SCAFFOLDED()]);
    const speaking = applyVoiceOverlay(building, { ...VOICE_TOOL_RUNNING, state: "speaking", outputLevel: 0.4 });
    expect(speaking.kind).toBe("speaking");
    expect(speaking.source).toBe("voice");

    const stale = visualFor(truthOf([SCAFFOLDED()]), T0 + APP_FACTORY_TTL_MS + 1);
    const overStale = applyVoiceOverlay(stale, VOICE_TOOL_RUNNING);
    expect(overStale.kind).toBe("tool_running");
    expect(overStale.source).toBe("voice");
  });
});
