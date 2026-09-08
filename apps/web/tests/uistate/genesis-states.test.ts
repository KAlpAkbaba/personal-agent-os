/**
 * Contract v9: `capability.genesis` (M24 spec §8).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to a capability the assistant acquires for the owner:
 * **the Core names a capability, a state, an approval flag and an error
 * class because the Cloud Core published them — in the words the spec
 * gives it — never a state it inferred, never "kullanılabilir" for a word
 * it cannot read, never "doğrulandı" before the publisher said `verified`,
 * never a failed run presented as done.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v8 (or
 * v7, v6, v5, v4, v3, v2) is read normally, because v9 only added.
 */

import { describe, expect, it } from "vitest";

import {
  APP_FACTORY_TTL_MS,
  APP_PROJECT_STATES,
  APP_STATES,
  ARTIFACT_STATES,
  CALENDAR_STATES,
  CAPABILITY_GENESIS as CAPABILITY_GENESIS_TOKEN,
  DOCUMENT_STATES,
  GENESIS_CAPTION_BARE,
  GENESIS_RUN_STATES,
  GENESIS_STATES,
  GENESIS_STATE_LABEL,
  GENESIS_TTL_MS,
  KNOWN_CONTRACT_VERSION,
  MAIL_STATES,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STATES,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  contractCompatibility,
  isAppState,
  isArtifactState,
  isCalendarState,
  isCoreChannel,
  isDocumentState,
  isGenesisRunState,
  isGenesisState,
  isKnownState,
  isMailState,
  isOperatorState,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  GENESIS_PREPARING_STATES,
  GENESIS_SETTLED_STATES,
  type GenesisFacts,
  genesisCaption,
  genesisFacts,
  genesisIsActive,
  genesisIsAwaiting,
  genesisPosture,
  genesisRunIsActive,
  genesisStatePhrase,
  genesisView,
} from "../../app/lib/uistate/genesis";
import {
  GENESIS_EMPTY,
  GENESIS_ERROR_CLASS_UNTOLD,
  GENESIS_LABEL,
  GENESIS_UNTOLD,
  KIND_DETAIL,
  KIND_LABEL,
  STATE_LABEL,
  contractLagNote,
  genesisFactsLine,
  genesisStateLine,
  genesisStateWord,
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
  genesisClaim,
  mailClaim,
} from "../../app/lib/uistate/truth";
import {
  type VisualIntent,
  type VoiceOverlay,
  applyVoiceOverlay,
  isAppBuilding,
  isArtifactMaking,
  isCalendarPlanning,
  isDocumentReading,
  isGenesisAwaiting,
  isGenesisWorking,
  isMailReading,
  isOperatorActing,
  visualFor,
} from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  ARTIFACT_FACTORY,
  CALENDAR_ACTIVITY,
  CAPABILITY_GENESIS,
  CAPABILITY_GENESIS_BARE,
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
  toolLabel: "Yeni yetenek",
  lastError: null,
};

const CAP = "counterbox.increment";
const MISSING = () => CAPABILITY_GENESIS(CAP, "capability_missing");
const BUILDING = () => CAPABILITY_GENESIS(CAP, "building");
const TESTING = () => CAPABILITY_GENESIS(CAP, "testing");
const AWAITING = () => CAPABILITY_GENESIS(CAP, "awaiting_approval", null, true);
const REGISTERING = () => CAPABILITY_GENESIS(CAP, "registering");
const AVAILABLE = () => CAPABILITY_GENESIS(CAP, "available");
const USED = () => CAPABILITY_GENESIS(CAP, "used");
const VERIFIED = () => CAPABILITY_GENESIS(CAP, "verified");
const FAILED = () => CAPABILITY_GENESIS(CAP, "failed", "dependency_unavailable");

/** The thirteen states as the caption must word each one, for `counterbox.increment`. */
const EXPECTED_CAPTION: Record<(typeof GENESIS_RUN_STATES)[number], string> = {
  capability_missing: "counterbox.increment için yetenek yok — deneniyor",
  researching: "counterbox.increment için arayüz araştırılıyor",
  designing: "counterbox.increment için bağdaştırıcı tasarlanıyor",
  building: "counterbox.increment için bağdaştırıcı yazılıyor",
  testing: "counterbox.increment sınanıyor",
  classifying: "counterbox.increment sınıflandırılıyor",
  awaiting_approval: "counterbox.increment onay bekliyor",
  rolling_out: "counterbox.increment yayına alınıyor",
  registering: "counterbox.increment kaydediliyor",
  available: "counterbox.increment kullanılabilir",
  used: "counterbox.increment kullanıldı",
  verified: "counterbox.increment doğrulandı",
  failed: "counterbox.increment başarısız",
};

// ------------------------------------------------------------ the contract

describe("contract v9 is v8 plus the genesis state, and says so", () => {
  it("is version 9 and still reads a v8, v7, v6, v5, v4, v3 and v2 server", () => {
    expect(KNOWN_CONTRACT_VERSION).toBe(9);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(9)).toBe("current");
    for (const older of [8, 7, 6, 5, 4, 3, 2]) expect(contractCompatibility(older), `v${older}`).toBe("older_supported");
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(10)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("names the one token once, and knows it by membership rather than by prefix", () => {
    expect(CAPABILITY_GENESIS_TOKEN).toBe("capability.genesis");
    expect(GENESIS_STATES).toEqual(["capability.genesis"]);
    expect(isKnownState("capability.genesis")).toBe(true);
    expect(isGenesisState("capability.genesis")).toBe(true);
    // A newer server's word is not a state this build may draw as a run.
    expect(isGenesisState("capability.revoked")).toBe(false);
    expect(isGenesisState("capability.request")).toBe(false);
    expect(isGenesisState("app.factory")).toBe(false);
    expect(isAppState("capability.genesis")).toBe(false);
    expect(isArtifactState("capability.genesis")).toBe(false);
    expect(isMailState("capability.genesis")).toBe(false);
    expect(isCalendarState("capability.genesis")).toBe(false);
    expect(isDocumentState("capability.genesis")).toBe(false);
    expect(isOperatorState("capability.genesis")).toBe(false);
  });

  it("appends the token after v8's, never reordering", () => {
    expect(UI_STATES.indexOf("capability.genesis")).toBe(UI_STATES.indexOf("app.factory") + 1);
    expect(UI_STATES[UI_STATES.length - 1]).toBe("capability.genesis");
  });

  it("types the thirteen run states in the spec's order, and admits nothing outside them", () => {
    expect(GENESIS_RUN_STATES).toEqual([
      "capability_missing",
      "researching",
      "designing",
      "building",
      "testing",
      "classifying",
      "awaiting_approval",
      "rolling_out",
      "registering",
      "available",
      "used",
      "verified",
      "failed",
    ]);
    for (const s of GENESIS_RUN_STATES) expect(isGenesisRunState(s), s).toBe(true);
    expect(isGenesisRunState("approved")).toBe(false);
    expect(isGenesisRunState("VERIFIED")).toBe(false);
    expect(isGenesisRunState("cancelled")).toBe(false);
    expect(isGenesisRunState(true)).toBe(false);
    expect(isGenesisRunState(null)).toBe(false);
  });

  it("adds the genesis subsystem and names it", () => {
    expect(SUBSYSTEMS).toContain("genesis");
    expect(subsystemLabel("genesis")).toBe("Yeni yetenek");
  });

  it("keeps the token on the agent channel, which drives the core body — not the lab's", () => {
    expect(stateChannel("capability.genesis")).toBe("agent");
    expect(isCoreChannel("capability.genesis")).toBe(true);
    // The room and the lab are untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("evolution.building")).toBe("lab");
    expect(stateChannel("operator.running")).toBe("operator");
  });

  it("classifies it as transient, on the device round trip's horizon", () => {
    expect(stateKind("capability.genesis")).toBe("transient");
    expect(GENESIS_TTL_MS).toBe(45_000);
    expect(GENESIS_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(stateTtlMs("capability.genesis")).toBe(GENESIS_TTL_MS);
    // The publisher's own ttl_s still beats every figure here: a parked run
    // may be claimed as waiting for as long as the publisher said.
    expect(stateTtlMs("capability.genesis", CAPABILITY_GENESIS(CAP, "awaiting_approval", null, true, { ttl_s: 600 }))).toBe(600_000);
  });

  it("gives the state the Turkish the spec asks for, spelled once", () => {
    expect(GENESIS_CAPTION_BARE).toBe("Yeni yetenek");
    expect(STATE_LABEL["capability.genesis"]).toBe(GENESIS_CAPTION_BARE);
    expect(KIND_LABEL.capability_genesis).toBe(GENESIS_CAPTION_BARE);
    expect(GENESIS_LABEL.active).toBe(GENESIS_CAPTION_BARE);
    expect(GENESIS_LABEL.none).not.toBe("Boşta");
    expect(stateLabel("capability.genesis")).not.toBe("capability.genesis");
    expect(GENESIS_EMPTY).toBe("Henüz yeni bir yetenek istenmedi.");
    expect(GENESIS_UNTOLD).not.toBe(GENESIS_EMPTY);
    // The thirteen states, each a constant spelled once.
    expect(GENESIS_STATE_LABEL).toEqual({
      capability_missing: "yetenek yok — deneniyor",
      researching: "arayüz araştırılıyor",
      designing: "bağdaştırıcı tasarlanıyor",
      building: "bağdaştırıcı yazılıyor",
      testing: "sınanıyor",
      classifying: "sınıflandırılıyor",
      awaiting_approval: "onay bekliyor",
      rolling_out: "yayına alınıyor",
      registering: "kaydediliyor",
      available: "kullanılabilir",
      used: "kullanıldı",
      verified: "doğrulandı",
      failed: "başarısız",
    });
    // "Doğrulandı" and "kullanılabilir" are words for exactly one state each.
    expect(Object.values(GENESIS_STATE_LABEL).filter((w) => w === "doğrulandı")).toHaveLength(1);
    expect(Object.values(GENESIS_STATE_LABEL).filter((w) => w === "kullanılabilir")).toHaveLength(1);
    // Every word is distinct: two states never read the same.
    expect(new Set(Object.values(GENESIS_STATE_LABEL)).size).toBe(GENESIS_RUN_STATES.length);
    // The detail says which side of "done" the posture is on.
    expect(KIND_DETAIL.capability_genesis).toContain("Doğrulanmamış bir yetenek yapıldı sayılmaz");
    expect(KIND_DETAIL.capability_genesis).toContain("İlerleme bildirilmez");
    expect(KIND_DETAIL.capability_genesis).toContain("sahip onayı");
  });

  it("names exactly the families an older server will never publish", () => {
    const v8 = contractLagNote(8, 9);
    expect(v8).toContain("v8");
    expect(v8).toContain("v9");
    expect(v8).toContain("Yeni yetenek durumu");
    expect(v8).not.toContain("uygulama");
    expect(v8).toContain("yok demek değil");

    const v7 = contractLagNote(7, 9);
    expect(v7).toContain("Uygulama üretim durumu");
    expect(v7).toContain("yeni yetenek durumu");

    const v2 = contractLagNote(2, 9);
    expect(v2).toContain("Alarm ve ekran durumları");
    expect(v2).toContain("dijital operatör durumları");
    expect(v2).toContain("belge inceleme durumu");
    expect(v2).toContain("posta ve takvim durumları");
    expect(v2).toContain("dosya üretim durumu");
    expect(v2).toContain("uygulama üretim durumu");
    expect(v2).toContain("yeni yetenek durumu");

    // The v8 wording M23 shipped is unchanged for a v7 server seen from v8.
    expect(contractLagNote(7, 8)).toBe(
      "Sunucu durum sözleşmesi v7; bu arayüz v8. Uygulama üretim durumu bu sunucudan henüz yayınlanmıyor — yok demek değil.",
    );
  });

  it("leaves every v8 token, kind and horizon exactly as it was", () => {
    expect(APP_STATES).toEqual(["app.factory"]);
    expect(APP_PROJECT_STATES).toEqual(["planned", "scaffolded", "running", "tested", "failed", "stopped"]);
    expect(ARTIFACT_STATES).toEqual(["artifact.factory"]);
    expect(MAIL_STATES).toEqual(["mail.activity"]);
    expect(CALENDAR_STATES).toEqual(["calendar.activity"]);
    expect(DOCUMENT_STATES).toEqual(["document.analysis"]);
    expect(OPERATOR_STATES).toEqual(["operator.running", "operator.verifying", "operator.failed"]);
    expect(stateKind("app.factory")).toBe("transient");
    expect(stateTtlMs("app.factory")).toBe(APP_FACTORY_TTL_MS);
    expect(stateKind("artifact.factory")).toBe("transient");
    expect(stateKind("mail.activity")).toBe("transient");
    expect(stateKind("document.analysis")).toBe("transient");
    expect(stateKind("operator.failed")).toBe("steady");
    expect(stateKind("agent.waiting_owner")).toBe("steady");
    expect(stateTtlMs("agent.thinking")).toBe(TRANSIENT_TTL_MS);
    expect(stateKind("agent.idle")).toBe("steady");
    expect(UI_STATES.indexOf("app.factory")).toBe(UI_STATES.indexOf("artifact.factory") + 1);
    // A v8 app event still parses and draws as it did.
    const running = intentOf([APP_FACTORY("Görev Takip", "running", 8123)]);
    expect(running.kind).toBe("app_factory");
    expect(running.label).toBe("Görev Takip çalışıyor · 127.0.0.1:8123");
    expect(running.genesis).toBeNull();
    const making = intentOf([ARTIFACT_FACTORY("Bütçe 2026", "pdf", "invalid", "sheet:Ozet!B5")]);
    expect(making.kind).toBe("artifact_factory");
    expect(making.label).toBe("Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5)");
    expect(making.genesis).toBeNull();
    const mail = intentOf([MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")]);
    expect(mail.kind).toBe("mail_activity");
    expect(mail.label).toBe("Taslak okundu — onay bekliyor · Proje planı");
    expect(mail.genesis).toBeNull();
  });

  it("reads the v9 metadata through the same bounded parser: four short tokens, nothing content-shaped", () => {
    const e = parseEvent({
      state: "capability.genesis",
      at: iso(0),
      metadata: {
        capability: "counterbox.increment",
        state: "failed",
        approval_required: false,
        error_class: "dependency_unavailable",
        // An interface description or a rendered adapter is content, not a
        // token: dropped at the boundary like every other object.
        interface: { base_url: "http://127.0.0.1:8765", operations: [{ id: "increment" }] },
        source: ["def run(payload):", "    ..."],
      },
    });
    expect(e?.metadata).toEqual({
      capability: "counterbox.increment",
      state: "failed",
      approval_required: false,
      error_class: "dependency_unavailable",
    });
    expect(e).not.toHaveProperty("refs");
    expect(e).not.toHaveProperty("tests");
  });
});

// ------------------------------------------------------------------- facts

describe("genesis: facts, view and caption from published metadata only", () => {
  it("reads the capability, the state, the flag and the error class verbatim", () => {
    expect(genesisFacts(AWAITING())).toEqual({
      capability: CAP,
      stateToken: "awaiting_approval",
      state: "awaiting_approval",
      approvalRequired: true,
      errorClass: null,
    });
    expect(genesisFacts(FAILED())).toEqual({
      capability: CAP,
      stateToken: "failed",
      state: "failed",
      approvalRequired: null,
      errorClass: "dependency_unavailable",
    });
    expect(genesisFacts(BUILDING())).toEqual({ capability: CAP, stateToken: "building", state: "building", approvalRequired: null, errorClass: null });
    expect(genesisFacts(null)).toEqual({ capability: null, stateToken: null, state: null, approvalRequired: null, errorClass: null });
    // The flag is a flag: a string "true" is not one.
    expect(genesisFacts(CAPABILITY_GENESIS(CAP, "awaiting_approval", null, null, { approval_required: "true" })).approvalRequired).toBeNull();
    expect(genesisFacts(CAPABILITY_GENESIS(CAP, "classifying", null, false)).approvalRequired).toBe(false);
  });

  it("captions every one of the thirteen states in the spec's words from the published facts", () => {
    for (const state of GENESIS_RUN_STATES) {
      expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(CAP, state))), state).toBe(EXPECTED_CAPTION[state]);
    }
    // The directive's own example sentence, word for word.
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS("Sayaç kutusu", "capability_missing")))).toBe(
      "Sayaç kutusu için yetenek yok — deneniyor",
    );
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS("Sayaç kutusu", "building")))).toBe("Sayaç kutusu için bağdaştırıcı yazılıyor");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS("Sayaç kutusu", "testing")))).toBe("Sayaç kutusu sınanıyor");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS("Sayaç kutusu", "awaiting_approval")))).toBe("Sayaç kutusu onay bekliyor");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS("Sayaç kutusu", "registering")))).toBe("Sayaç kutusu kaydediliyor");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS("Sayaç kutusu", "available")))).toBe("Sayaç kutusu kullanılabilir");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS("Sayaç kutusu", "used")))).toBe("Sayaç kutusu kullanıldı");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS("Sayaç kutusu", "verified")))).toBe("Sayaç kutusu doğrulandı");
    // The four preparing states say the word FOR the capability; the rest say it OF it.
    expect(GENESIS_PREPARING_STATES).toEqual(["capability_missing", "researching", "designing", "building"]);
    expect(GENESIS_SETTLED_STATES).toEqual(["available", "used", "verified"]);
  });

  it("words a failure with its error class, and without one when none was published — never as done", () => {
    const failed = genesisCaption(genesisFacts(FAILED()));
    expect(failed).toBe("counterbox.increment başarısız — dependency_unavailable");
    expect(failed).not.toContain("doğrulandı");
    expect(failed).not.toContain("kullanılabilir");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(CAP, "failed", "postcondition_failed")))).toBe(
      "counterbox.increment başarısız — postcondition_failed",
    );
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(CAP, "failed")))).toBe("counterbox.increment başarısız");
    // An error class beside a state that is not failed is a fact for the line, not the caption.
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(CAP, "verified", "stale")))).toBe("counterbox.increment doğrulandı");
    expect(genesisStatePhrase({ state: "failed", errorClass: "timeout" })).toBe("başarısız — timeout");
    expect(genesisStatePhrase({ state: "failed", errorClass: null })).toBe("başarısız");
    expect(genesisStatePhrase({ state: "verified", errorClass: null })).toBe("doğrulandı");
    expect(genesisStatePhrase({ state: null, errorClass: "timeout" })).toBeNull();
  });

  it("says the plain state name without a capability, and the bare token for a word it cannot read or no metadata at all", () => {
    // A state with no capability: the state's own name, and nothing named.
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(null, "awaiting_approval")))).toBe("onay bekliyor");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(null, "verified")))).toBe("doğrulandı");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(null, "failed", "timeout")))).toBe("başarısız — timeout");
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(null, "capability_missing")))).toBe("yetenek yok — deneniyor");

    // A newer publisher's `approved`: a word we cannot read is not a step we may narrate.
    const unknown = genesisFacts(CAPABILITY_GENESIS(CAP, "approved"));
    expect(unknown.stateToken).toBe("approved");
    expect(unknown.state).toBeNull();
    expect(genesisCaption(unknown)).toBe(GENESIS_CAPTION_BARE);
    expect(genesisCaption(unknown)).not.toContain("kullanılabilir");
    expect(genesisCaption(unknown)).not.toContain("doğrulandı");
    // The long line still says what WAS published, verbatim.
    expect(genesisFactsLine(unknown)).toBe("yetenek: counterbox.increment · durum: approved");

    // A capability with no state at all is a run in no state: the bare token, never a step.
    expect(genesisCaption(genesisFacts(CAPABILITY_GENESIS(CAP, null)))).toBe(GENESIS_CAPTION_BARE);

    const bare = genesisFacts(CAPABILITY_GENESIS_BARE());
    expect(bare).toEqual({ capability: null, stateToken: null, state: null, approvalRequired: null, errorClass: null });
    expect(genesisCaption(bare)).toBe("Yeni yetenek");
    expect(genesisFactsLine(bare)).toBe("yetenek bildirilmedi · durum bildirilmedi");
  });

  it("the facts line names each fact or its absence, the flag only when sent and the error class only beside failed", () => {
    expect(genesisFactsLine(genesisFacts(BUILDING()))).toBe("yetenek: counterbox.increment · durum: bağdaştırıcı yazılıyor");
    expect(genesisFactsLine(genesisFacts(AWAITING()))).toBe("yetenek: counterbox.increment · durum: onay bekliyor · onay gerekli");
    expect(genesisFactsLine(genesisFacts(CAPABILITY_GENESIS(CAP, "classifying", null, false)))).toBe(
      "yetenek: counterbox.increment · durum: sınıflandırılıyor · onay gerekmiyor",
    );
    expect(genesisFactsLine(genesisFacts(VERIFIED()))).toBe("yetenek: counterbox.increment · durum: doğrulandı");
    expect(genesisFactsLine(genesisFacts(FAILED()))).toBe("yetenek: counterbox.increment · durum: başarısız · hata: dependency_unavailable");
    expect(genesisFactsLine(genesisFacts(CAPABILITY_GENESIS(CAP, "failed")))).toBe(
      `yetenek: counterbox.increment · durum: başarısız · ${GENESIS_ERROR_CLASS_UNTOLD}`,
    );
    expect(GENESIS_ERROR_CLASS_UNTOLD).toBe("hata sınıfı bildirilmedi");
    // The flag's absence is never said: nobody published a fact either way.
    expect(genesisFactsLine(genesisFacts(VERIFIED()))).not.toContain("onay");
    expect(genesisFactsLine(genesisFacts(CAPABILITY_GENESIS(null, "testing")))).toBe("yetenek bildirilmedi · durum: sınanıyor");
  });

  it("the state word is the spec's for the thirteen it knows, the token for one it does not, and the statement that none came", () => {
    for (const s of GENESIS_RUN_STATES) expect(genesisStateWord(s)).toBe(GENESIS_STATE_LABEL[s]);
    expect(genesisStateWord("approved")).toBe("approved");
    expect(genesisStateWord(null)).toBe("durum bildirilmedi");
    expect(genesisStateLine({ state: "failed", stateToken: "failed", errorClass: "timeout" })).toBe("başarısız — timeout");
    expect(genesisStateLine({ state: null, stateToken: "approved", errorClass: null })).toBe("approved");
    expect(genesisStateLine({ state: null, stateToken: null, errorClass: null })).toBe("durum bildirilmedi");
  });

  it("the posture follows the published state: building, waiting at awaiting_approval, settled from available on, failed", () => {
    const expected: Record<(typeof GENESIS_RUN_STATES)[number], ReturnType<typeof genesisPosture>> = {
      capability_missing: "building",
      researching: "building",
      designing: "building",
      building: "building",
      testing: "building",
      classifying: "building",
      awaiting_approval: "waiting",
      rolling_out: "building",
      registering: "building",
      available: "settled",
      used: "settled",
      verified: "settled",
      failed: "failed",
    };
    for (const state of GENESIS_RUN_STATES) expect(genesisPosture(state), state).toBe(expected[state]);
    // No state, or a word we cannot read, is building: nothing settled has been said.
    expect(genesisPosture(null)).toBe("building");
    // A run is active while building or waiting — never once settled or failed, never for no state.
    for (const state of GENESIS_RUN_STATES) {
      expect(genesisRunIsActive(state), state).toBe(expected[state] === "building" || expected[state] === "waiting");
    }
    expect(genesisRunIsActive(null)).toBe(false);
    expect(genesisIsAwaiting({ state: "awaiting_approval" })).toBe(true);
    expect(genesisIsAwaiting({ state: "registering" })).toBe(false);
    expect(genesisIsAwaiting({ state: null })).toBe(false);
  });

  it("the view is active while live, with the caption and the posture, and none-but-nothing before any genesis event", () => {
    const view = genesisView(genesisClaim(truthOf([AWAITING()]), T0));
    expect(view.stage).toBe("active");
    expect(view.lastKnown).toBe("active");
    expect(view.posture).toBe("waiting");
    expect(view.caption).toBe("counterbox.increment onay bekliyor");
    expect(view.approvalRequired).toBe(true);
    expect(view.taskId).toBe("genesis-task-1");
    expect(view.severity).toBe("info");
    expect(genesisIsActive(view)).toBe(true);
    expect(genesisIsAwaiting(view)).toBe(true);

    const none = genesisView(genesisClaim(truthOf([AGENT_IDLE(), OPERATOR_RUNNING(), MAIL_ACTIVITY(), ARTIFACT_FACTORY(), APP_FACTORY()]), T0));
    expect(none.stage).toBe("none");
    expect(none.lastKnown).toBeNull();
    expect(none.capability).toBeNull();
    expect(none.posture).toBe("building");
    expect(none.caption).toBe(GENESIS_CAPTION_BARE);
    expect(genesisIsActive(none)).toBe(false);
    expect(genesisIsAwaiting(none)).toBe(false);
  });

  it("the claim is by membership: a newer server's capability word is not read as a run", () => {
    const truth = truthOf([VERIFIED(), event({ state: "capability.revoked", subsystem: "genesis" })]);
    expect(genesisClaim(truth, T0).event?.state).toBe("capability.genesis");
    expect(visualFor(truth, T0).kind).toBe("unknown_state");
  });
});

// --------------------------------------------------------------- the Core

describe("the Core draws the posture from the published state", () => {
  it("is its own calm building kind while the adapter is worked on, captioned from the capability and the state", () => {
    const intent = intentOf([BUILDING()]);
    expect(intent.kind).toBe("capability_genesis");
    expect(intent.source).toBe("bus");
    expect(intent.subsystem).toBe("genesis");
    expect(intent.palette).toBe("making");
    expect(intent.label).toBe("counterbox.increment için bağdaştırıcı yazılıyor");
    expect(intent.genesis).toEqual({ capability: CAP, stateToken: "building", state: "building", approvalRequired: null, errorClass: null });
    expect(intent.app).toBeNull();
    expect(intent.artifact).toBeNull();
    expect(intent.mail).toBeNull();
    expect(intent.calendar).toBeNull();
    expect(intent.document).toBeNull();
    expect(isGenesisWorking(intent)).toBe(true);
    expect(isGenesisAwaiting(intent)).toBe(false);
    expect(isAppBuilding(intent)).toBe(false);
    expect(isArtifactMaking(intent)).toBe(false);
    expect(isMailReading(intent)).toBe(false);
    expect(isCalendarPlanning(intent)).toBe(false);
    expect(isDocumentReading(intent)).toBe(false);
    expect(isOperatorActing(intent)).toBe(false);

    // The posture: an adapter being written OUT — nothing flows inward, the
    // paths carry traffic, a faint lattice is laid — and nothing that could
    // be read as the capability existing beyond what was published.
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
    // Every other working state is the same building posture, with its own word.
    for (const state of ["capability_missing", "researching", "designing", "testing", "classifying", "rolling_out", "registering"] as const) {
      const working = intentOf([CAPABILITY_GENESIS(CAP, state)]);
      expect(working.kind, state).toBe("capability_genesis");
      expect(working.palette, state).toBe("making");
      expect(working.label, state).toBe(EXPECTED_CAPTION[state]);
      expect(working.flowRate, state).toBe(intent.flowRate);
      expect(working.restraint, state).toBe(0);
    }
  });

  it("awaiting_approval is a distinct waiting posture: held like the owner's own wait, nothing flows, nothing turns", () => {
    const waiting = intentOf([AWAITING()]);
    const held = intentOf([event({ state: "agent.waiting_owner" })]);
    const building = intentOf([BUILDING()]);
    expect(waiting.kind).toBe("capability_genesis");
    expect(waiting.palette).toBe("held");
    expect(waiting.label).toBe("counterbox.increment onay bekliyor");
    expect(isGenesisAwaiting(waiting)).toBe(true);
    expect(waiting.restraint).toBe(1);
    expect(waiting.restraint).toBe(held.restraint);
    expect(waiting.flowRate).toBe(0);
    expect(waiting.inwardFlow).toBe(0);
    expect(waiting.topology).toBe(0);
    expect(waiting.ringSpin).toBe(held.ringSpin);
    expect(waiting.shellSpread).toBe(held.shellSpread);
    expect(waiting.breathHz).toBe(held.breathHz);
    expect(waiting.scale).toBeLessThan(building.scale);
    expect(waiting.glow).toBeLessThan(building.glow);
    expect(waiting.agitation).toBe(0);
    expect(waiting.pulse).toBe(0);
    expect(waiting.genesis?.approvalRequired).toBe(true);
  });

  it("available, used and verified are a settled posture: still and bright, verified a shade brighter", () => {
    const building = intentOf([BUILDING()]);
    const available = intentOf([AVAILABLE()]);
    const used = intentOf([USED()]);
    const verified = intentOf([VERIFIED()]);
    for (const settled of [available, used, verified]) {
      expect(settled.kind).toBe("capability_genesis");
      expect(settled.palette).toBe("ready");
      expect(settled.flowRate).toBe(0);
      expect(settled.inwardFlow).toBe(0);
      expect(settled.ringSpin).toBeLessThan(building.ringSpin);
      expect(settled.glow).toBeGreaterThan(building.glow);
      expect(settled.restraint).toBe(0);
      expect(settled.agitation).toBe(0);
      expect(settled.pulse).toBe(0);
      expect(isGenesisWorking(settled)).toBe(true);
      expect(isGenesisAwaiting(settled)).toBe(false);
    }
    expect(available.label).toBe("counterbox.increment kullanılabilir");
    expect(used.label).toBe("counterbox.increment kullanıldı");
    expect(verified.label).toBe("counterbox.increment doğrulandı");
    expect(verified.glow).toBeGreaterThan(available.glow);
    expect(used.glow).toBe(available.glow);
  });

  it("failed is held and never dressed as done: still, under restraint, no agitation, dim, with its error class", () => {
    const building = intentOf([BUILDING()]);
    const failed = intentOf([FAILED()]);
    expect(failed.kind).toBe("capability_genesis");
    expect(failed.palette).toBe("making");
    expect(failed.label).toBe("counterbox.increment başarısız — dependency_unavailable");
    expect(failed.label).not.toContain("doğrulandı");
    expect(failed.flowRate).toBe(0);
    expect(failed.ringSpin).toBeLessThan(building.ringSpin);
    expect(failed.glow).toBeLessThan(building.glow);
    expect(failed.restraint).toBeGreaterThan(0);
    expect(failed.restraint).toBeLessThan(1);
    expect(failed.agitation).toBe(0);
    expect(failed.pulse).toBe(0);
    expect(failed.genesis?.errorClass).toBe("dependency_unavailable");
    expect(isGenesisAwaiting(failed)).toBe(false);
  });

  it("never draws progress, a constellation or a candidate: none is published, and the lab's satellites are the lab's", () => {
    for (const state of GENESIS_RUN_STATES) {
      const intent = intentOf([CAPABILITY_GENESIS(CAP, state)]);
      expect(intent.progress, state).toBeNull();
      expect(intent.constellationNodes, state).toBe(0);
      expect(intent.constellationDrift, state).toBe(0);
      expect(intent.capabilityNodes, state).toBe(0);
      expect(intent.satelliteComplete, state).toBe(false);
      expect(intent.constructionLayer, state).toBe(0);
      expect(intent.pulse, state).toBe(0);
    }
    const bare = intentOf([CAPABILITY_GENESIS_BARE()]);
    expect(bare.progress).toBeNull();
    expect(bare.capabilityNodes).toBe(0);
  });

  it("captions the bare statement when the publisher named nothing, and draws a word it cannot read as building", () => {
    expect(intentOf([CAPABILITY_GENESIS_BARE()]).label).toBe("Yeni yetenek");
    expect(intentOf([CAPABILITY_GENESIS_BARE()]).genesis).toEqual({ capability: null, stateToken: null, state: null, approvalRequired: null, errorClass: null });
    const unknown = intentOf([CAPABILITY_GENESIS(CAP, "approved")]);
    expect(unknown.kind).toBe("capability_genesis");
    expect(unknown.palette).toBe("making");
    expect(unknown.label).toBe(GENESIS_CAPTION_BARE);
    expect(isGenesisAwaiting(unknown)).toBe(false);
    expect(unknown.flowRate).toBe(intentOf([BUILDING()]).flowRate);
    expect(unknown.restraint).toBe(0);
  });

  it("does not let the room displace a run in flight", () => {
    expect(intentOf([TESTING(), OWNER_AWAY()]).kind).toBe("capability_genesis");
    expect(intentOf([AWAITING(), OWNER_AWAY()]).kind).toBe("capability_genesis");
  });

  it("leaves every other kind's `genesis` null", () => {
    for (const events of [
      [AGENT_IDLE()],
      [OPERATOR_RUNNING()],
      [DOCUMENT_ANALYSIS()],
      [MAIL_ACTIVITY()],
      [CALENDAR_ACTIVITY()],
      [ARTIFACT_FACTORY()],
      [APP_FACTORY()],
      [event({ state: "agent.tool_running" })],
      [event({ state: "evolution.building", subsystem: "evolution" })],
    ]) {
      expect(intentOf(events).genesis).toBeNull();
    }
  });
});

describe("honesty over time", () => {
  it("a genesis event is live for its horizon and last-known after it, with its facts kept", () => {
    const truth = truthOf([AWAITING()]);
    expect(visualFor(truth, T0 + GENESIS_TTL_MS - 1).kind).toBe("capability_genesis");

    const stale = visualFor(truth, T0 + GENESIS_TTL_MS + 1);
    expect(stale.kind).toBe("last_known");
    expect(stale.state).toBe("capability.genesis");
    for (const channel of MOTION_CHANNELS) expect(stale[channel], channel).toBe(0);
    // What it WAS doing is still a fact; the readout names it as last-known —
    // and a last-known wait is not a live one: the row is where "Onayla" lives.
    expect(stale.label).toBe("counterbox.increment onay bekliyor");
    expect(stale.genesis?.state).toBe("awaiting_approval");
    expect(isGenesisWorking(stale)).toBe(false);
    expect(isGenesisAwaiting(stale)).toBe(false);
  });

  it("an expired genesis event is none-but-last-known, never finished and never verified", () => {
    const view = genesisView(genesisClaim(truthOf([REGISTERING()]), T0 + 60_000));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(true);
    expect(view.ageMs).toBe(60_000);
    expect(view.caption).toBe("counterbox.increment kaydediliyor");
    expect(view.caption).not.toContain("doğrulandı");
    expect(genesisIsActive(view)).toBe(false);
  });

  it("is replaced by the agent's next word, and never outlives it", () => {
    const replaced = truthOf([MISSING(), AGENT_IDLE()]);
    expect(visualFor(replaced, T0).kind).toBe("idle");
    expect(coreClaim(replaced, T0).event?.state).toBe("agent.idle");
    // The cockpit's own claim still knows what the run was doing.
    expect(genesisView(genesisClaim(replaced, T0)).capability).toBe(CAP);
    expect(appClaim(replaced, T0).event).toBeNull();
    expect(artifactClaim(replaced, T0).event).toBeNull();
    expect(mailClaim(replaced, T0).event).toBeNull();
    expect(calendarClaim(replaced, T0).event).toBeNull();
    expect(documentClaim(replaced, T0).event).toBeNull();
  });

  it("an unreachable API keeps the shape and the facts, and stops the motion", () => {
    let truth = truthOf([BUILDING()]);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unreachable");
    expect(intent.state).toBe("capability.genesis");
    expect(intent.genesis?.capability).toBe(CAP);
    expect(intent.flowRate).toBe(0);
    expect(intent.ringSpin).toBe(0);
    expect(isGenesisWorking(intent)).toBe(false);
  });
});

describe("older servers", () => {
  it("draws a v8 feed without the genesis token normally, and records the lag", () => {
    resetSequence();
    const running = APP_FACTORY("Görev Takip", "running", 8123);
    const v8 = applyResponse(emptyTruth(), { contract_version: 8, current: running, events: [running], sequence: running.sequence }, T0);
    expect(v8.connection.kind).toBe("live");
    expect(v8.contractVersion).toBe(8);
    expect(visualFor(v8, T0).kind).toBe("app_factory");
    expect(genesisView(genesisClaim(v8, T0)).lastKnown).toBeNull();
    expect(contractLagNote(8, KNOWN_CONTRACT_VERSION)).toContain("yok demek değil");
  });
});

describe("the voice overlay and the genesis posture", () => {
  it("a local tool_running does not hide a live genesis event: the bus knows the capability and the state", () => {
    const building = intentOf([BUILDING()]);
    expect(applyVoiceOverlay(building, VOICE_TOOL_RUNNING)).toBe(building);
    const waiting = intentOf([AWAITING()]);
    expect(applyVoiceOverlay(waiting, VOICE_TOOL_RUNNING)).toBe(waiting);
  });

  it("but every other local state, and a last-known genesis shape, keep the overlay's precedence", () => {
    const building = intentOf([BUILDING()]);
    const speaking = applyVoiceOverlay(building, { ...VOICE_TOOL_RUNNING, state: "speaking", outputLevel: 0.4 });
    expect(speaking.kind).toBe("speaking");
    expect(speaking.source).toBe("voice");

    const stale = visualFor(truthOf([BUILDING()]), T0 + GENESIS_TTL_MS + 1);
    const overStale = applyVoiceOverlay(stale, VOICE_TOOL_RUNNING);
    expect(overStale.kind).toBe("tool_running");
    expect(overStale.source).toBe("voice");
  });
});

/** The facts type is the contract's: a compile-time check that nothing here reads a key the type does not name. */
const _typed: GenesisFacts = genesisFacts(null);
void _typed;
