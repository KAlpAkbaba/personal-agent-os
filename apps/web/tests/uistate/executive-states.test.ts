/**
 * Contract v11: `executive.run` (M26 spec §6).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to a multi-step job the assistant carries on its own:
 * **the Core names a run, a step, a state and a step count because the
 * Cloud Core published them — in the words the spec gives it — never a
 * state it inferred, never "tamamlandı" before the publisher said
 * `completed`, never a fraction nobody counted, and never a control over a
 * run whose state it cannot read.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v10
 * (or v9 … v2) is read normally, because v11 only added.
 *
 * The vocabulary here is held to the publisher's in BOTH directions. This
 * file reads `EXECUTIVE_RUN_STATES` and proves what the renderer does with
 * each word; a test in `services/api` reads the same constant out of
 * `app/lib/uistate/contract.ts` and holds the publisher's list to it. Two
 * halves that each proved only their own belief is exactly how M24 shipped
 * a `cancelled` the Core could not read and M25 shipped `scene.activity`
 * states the web build drew as still running — both suites green.
 */

import { describe, expect, it } from "vitest";

import {
  APP_STATES,
  ARTIFACT_STATES,
  CALENDAR_STATES,
  DOCUMENT_STATES,
  EXECUTIVE_CAPTION_BARE,
  EXECUTIVE_RUN as EXECUTIVE_RUN_TOKEN,
  EXECUTIVE_RUN_STATES,
  EXECUTIVE_RUN_STATE_LABEL,
  EXECUTIVE_STATES,
  EXECUTIVE_TTL_MS,
  GENESIS_RUN_STATES,
  GENESIS_STATES,
  GENESIS_STATE_LABEL,
  GENESIS_TTL_MS,
  KNOWN_CONTRACT_VERSION,
  MAIL_STATES,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STATES,
  SCENE_RUN_STATES,
  SCENE_STATES,
  SCENE_STATE_LABEL,
  SCENE_TTL_MS,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  asStepCount,
  contractCompatibility,
  isAppState,
  isArtifactState,
  isCalendarState,
  isCoreChannel,
  isDocumentState,
  isExecutiveRunState,
  isExecutiveState,
  isGenesisState,
  isKnownState,
  isMailState,
  isOperatorState,
  isSceneState,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  EXECUTIVE_ACTIVE_STATES,
  EXECUTIVE_SETTLED_STATES,
  type ExecutiveFacts,
  executiveCaption,
  executiveFacts,
  executiveIsActive,
  executivePosture,
  executiveRunIsActive,
  executiveRunIsComplete,
  executiveRunIsPartial,
  executiveRunIsSettled,
  executiveStatePhrase,
  executiveStepPhrase,
  executiveStepsPhrase,
  executiveView,
} from "../../app/lib/uistate/executive";
import {
  EXECUTIVE_EMPTY,
  EXECUTIVE_LABEL,
  EXECUTIVE_MISSING_UNTOLD,
  EXECUTIVE_STEPS_UNTOLD,
  EXECUTIVE_UNTOLD,
  KIND_DETAIL,
  KIND_LABEL,
  STATE_LABEL,
  contractLagNote,
  executiveFactsLine,
  executiveStateLine,
  executiveStateWord,
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
  executiveClaim,
  genesisClaim,
  mailClaim,
  sceneClaim,
} from "../../app/lib/uistate/truth";
import {
  type VisualIntent,
  type VoiceOverlay,
  applyVoiceOverlay,
  executiveProgress,
  isAppBuilding,
  isArtifactMaking,
  isCalendarPlanning,
  isDocumentReading,
  isExecutivePaused,
  isExecutiveRunning,
  isGenesisWorking,
  isMailReading,
  isOperatorActing,
  isSceneWorking,
  visualFor,
} from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  ARTIFACT_FACTORY,
  CAPABILITY_GENESIS,
  DOCUMENT_ANALYSIS,
  EXECUTIVE_RUN,
  EXECUTIVE_RUN_BARE,
  MAIL_ACTIVITY,
  OPERATOR_RUNNING,
  OWNER_AWAY,
  SCENE_ACTIVITY,
  T0,
  event,
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
  toolLabel: "Çok adımlı iş",
  lastError: null,
};

const PLANNED = () => EXECUTIVE_RUN("r1", "planned", null, 0, 5);
const RUNNING = () => EXECUTIVE_RUN("r1", "running", "s3", 2, 5);
const PAUSED = () => EXECUTIVE_RUN("r1", "paused", "s3", 2, 5);
const COMPLETED = () => EXECUTIVE_RUN("r1", "completed", null, 5, 5);
const PARTIAL = () => EXECUTIVE_RUN("r1", "partial", null, 3, 5);
const CANCELLED = () => EXECUTIVE_RUN("r1", "cancelled", null, 2, 5);
const FAILED = () => EXECUTIVE_RUN("r1", "failed", "s4", 3, 5);

/** Facts as the publisher would have produced them, for the pure caption checks. */
function factsOf(overrides: Partial<ExecutiveFacts> = {}): ExecutiveFacts {
  return {
    run: "r1",
    step: "s3",
    stateToken: "running",
    state: "running",
    done: 2,
    total: 5,
    ...overrides,
  };
}

// ------------------------------------------------------------ the contract

describe("contract v11 is v10 plus the executive state, and says so", () => {
  it("is version 11 and still reads a v10, v9, v8, v7, v6, v5, v4, v3 and v2 server", () => {
    // Relative on the upper side, as the v9 file's assertion became when v10
    // landed: a later build must not have to rewrite this file to stay honest
    // about what v11 added.
    expect(KNOWN_CONTRACT_VERSION).toBeGreaterThanOrEqual(11);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION)).toBe("current");
    const built: number = KNOWN_CONTRACT_VERSION;
    expect(contractCompatibility(11)).toBe(built === 11 ? "current" : "older_supported");
    for (const older of [10, 9, 8, 7, 6, 5, 4, 3, 2]) {
      expect(contractCompatibility(older), `v${older}`).toBe("older_supported");
    }
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION + 1)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("names the one token once, and knows it by membership rather than by prefix", () => {
    expect(EXECUTIVE_RUN_TOKEN).toBe("executive.run");
    expect(EXECUTIVE_STATES).toEqual(["executive.run"]);
    expect(isKnownState("executive.run")).toBe(true);
    expect(isExecutiveState("executive.run")).toBe(true);
    // A newer server's word is not a state this build may draw as a run.
    expect(isExecutiveState("executive.step")).toBe(false);
    expect(isExecutiveState("executive.run.started")).toBe(false);
    expect(isExecutiveState("scene.activity")).toBe(false);
    // And no other family's guard claims it, in either direction.
    for (const guard of [isSceneState, isGenesisState, isAppState, isArtifactState, isMailState, isCalendarState, isDocumentState, isOperatorState]) {
      expect(guard("executive.run")).toBe(false);
    }
    expect(isExecutiveState("capability.genesis")).toBe(false);
    expect(isExecutiveState("app.factory")).toBe(false);
  });

  it("appends the token after v10's, never reordering", () => {
    expect(UI_STATES.indexOf("executive.run")).toBe(UI_STATES.indexOf("scene.activity") + 1);
    // Every earlier token keeps the place it had.
    expect(UI_STATES.indexOf("scene.activity")).toBe(UI_STATES.indexOf("capability.genesis") + 1);
    expect(UI_STATES.indexOf("capability.genesis")).toBe(UI_STATES.indexOf("app.factory") + 1);
    expect(UI_STATES.indexOf("app.factory")).toBe(UI_STATES.indexOf("artifact.factory") + 1);
    expect(UI_STATES[0]).toBe("agent.idle");
  });

  it("types the seven run states in the spec's order, and admits nothing outside them", () => {
    // The list the Cloud Core track builds its publisher against, word for
    // word and in this order. A Python test on that side reads this file and
    // holds the two together in both directions; changing a word here without
    // changing it there is meant to break both suites at once.
    expect(EXECUTIVE_RUN_STATES).toEqual([
      "planned",
      "running",
      "paused",
      "completed",
      "partial",
      "cancelled",
      "failed",
    ]);
    for (const s of EXECUTIVE_RUN_STATES) expect(isExecutiveRunState(s), s).toBe(true);
    // Membership, never a prefix: these six letters are not `completed`.
    expect(isExecutiveRunState("completed_with_errors")).toBe(false);
    expect(isExecutiveRunState("complete")).toBe(false);
    expect(isExecutiveRunState("COMPLETED")).toBe(false);
    expect(isExecutiveRunState("done")).toBe(false);
    expect(isExecutiveRunState("pausing")).toBe(false);
    expect(isExecutiveRunState(true)).toBe(false);
    expect(isExecutiveRunState(null)).toBe(false);
    expect(isExecutiveRunState(3)).toBe(false);
  });

  it("counts steps only from whole non-negative numbers, and keeps zero as an answer", () => {
    expect(asStepCount(3)).toBe(3);
    expect(asStepCount(0)).toBe(0);
    expect(asStepCount(-1)).toBeNull();
    expect(asStepCount(2.5)).toBeNull();
    expect(asStepCount("3")).toBeNull();
    expect(asStepCount(null)).toBeNull();
    expect(asStepCount(Number.NaN)).toBeNull();
  });

  it("adds the executive subsystem and names it", () => {
    expect(SUBSYSTEMS).toContain("executive");
    expect(subsystemLabel("executive")).toBe("Görevler");
    // The v10 subsystem is untouched.
    expect(SUBSYSTEMS).toContain("creative3d");
    expect(subsystemLabel("creative3d")).toBe("3B sahne");
  });

  it("keeps the token on the agent channel, which drives the core body", () => {
    expect(stateChannel("executive.run")).toBe("agent");
    expect(isCoreChannel("executive.run")).toBe(true);
    // The room, the lab and the operator are untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("evolution.building")).toBe("lab");
    expect(stateChannel("operator.running")).toBe("operator");
    expect(stateChannel("scene.activity")).toBe("agent");
  });

  it("classifies it as a watched operation, on a horizon cut for one step rather than one run", () => {
    expect(stateKind("executive.run")).toBe("operation");
    expect(EXECUTIVE_TTL_MS).toBe(960_000);
    // The gap between two events is one step, whose own cap is 900 s (spec §1,
    // §4); the horizon is that plus a minute. It is NOT the run's 60-minute
    // ceiling, which would keep drawing "çalışıyor" long after a Cloud Core
    // died mid-run.
    expect(EXECUTIVE_TTL_MS).toBeGreaterThan(900_000);
    expect(EXECUTIVE_TTL_MS).toBeLessThan(60 * 60_000);
    // Longer than every earlier horizon: a research step outlasts a headless
    // render, which outlasts a device round trip.
    expect(EXECUTIVE_TTL_MS).toBeGreaterThan(SCENE_TTL_MS);
    expect(EXECUTIVE_TTL_MS).toBeGreaterThan(GENESIS_TTL_MS);
    expect(EXECUTIVE_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(stateTtlMs("executive.run")).toBe(EXECUTIVE_TTL_MS);
    // The publisher's own ttl_s still beats every figure here.
    expect(stateTtlMs("executive.run", EXECUTIVE_RUN("r1", "running", "s3", null, null, { ttl_s: 120 }))).toBe(120_000);
  });

  it("gives the state the Turkish the spec asks for, spelled once, with one door to 'tamamlandı'", () => {
    expect(EXECUTIVE_CAPTION_BARE).toBe("Çok adımlı iş");
    expect(STATE_LABEL["executive.run"]).toBe(EXECUTIVE_CAPTION_BARE);
    expect(KIND_LABEL.executive_run).toBe(EXECUTIVE_CAPTION_BARE);
    expect(EXECUTIVE_LABEL.active).toBe(EXECUTIVE_CAPTION_BARE);
    expect(EXECUTIVE_LABEL.none).not.toBe("Boşta");
    expect(stateLabel("executive.run")).not.toBe("executive.run");
    expect(EXECUTIVE_EMPTY).toBe("Devam eden bir iş yok.");
    expect(EXECUTIVE_UNTOLD).not.toBe(EXECUTIVE_EMPTY);
    // The seven states, each a constant spelled once.
    expect(EXECUTIVE_RUN_STATE_LABEL).toEqual({
      planned: "planlandı",
      running: "çalışıyor",
      paused: "duraklatıldı",
      completed: "tamamlandı",
      partial: "kısmen bitti",
      cancelled: "iptal edildi",
      failed: "başarısız",
    });
    // "Tamamlandı" is a word for exactly one state, and no other word so much
    // as CONTAINS it — a "kısmen tamamlandı" would read as done at a glance,
    // which is the M25 rounding mistake in another costume.
    const withWord = Object.entries(EXECUTIVE_RUN_STATE_LABEL).filter(([, w]) => w.includes("tamamlandı"));
    expect(withWord).toEqual([["completed", "tamamlandı"]]);
    expect(new Set(Object.values(EXECUTIVE_RUN_STATE_LABEL)).size).toBe(EXECUTIVE_RUN_STATES.length);
    // The detail says which side of "done" the posture is on, and that the
    // owner keeps every lever.
    expect(KIND_DETAIL.executive_run).toContain("tamamlandı sayılır");
    expect(KIND_DETAIL.executive_run).toContain("kısmen bitti");
    expect(KIND_DETAIL.executive_run).toContain("duraklatabiliyor");
  });

  it("names exactly the families an older server will never publish", () => {
    const v10 = contractLagNote(10, 11);
    expect(v10).toContain("v10");
    expect(v10).toContain("v11");
    // The note opens the sentence with the family, so the first letter is up
    // the Turkish way; the lag note's own capitalisation is the assertion.
    expect(v10).toContain("Çok adımlı iş durumu");
    expect(v10).not.toContain("3B sahne");
    expect(v10).toContain("yok demek değil");

    const v9 = contractLagNote(9, 11);
    expect(v9).toContain("3B sahne durumu");
    expect(v9).toContain("çok adımlı iş durumu");

    const v2 = contractLagNote(2, 11);
    for (const family of [
      "Alarm ve ekran durumları",
      "dijital operatör durumları",
      "belge inceleme durumu",
      "posta ve takvim durumları",
      "dosya üretim durumu",
      "uygulama üretim durumu",
      "yeni yetenek durumu",
      "3B sahne durumu",
      "çok adımlı iş durumu",
    ]) {
      expect(v2, family).toContain(family);
    }

    // The v10 wording M25 shipped is unchanged for a v9 server seen from v10.
    expect(contractLagNote(9, 10)).toBe(
      "Sunucu durum sözleşmesi v9; bu arayüz v10. 3B sahne durumu bu sunucudan henüz yayınlanmıyor — yok demek değil.",
    );
  });

  it("leaves every v10 token, kind, horizon and word exactly as it was", () => {
    expect(SCENE_STATES).toEqual(["scene.activity"]);
    expect(SCENE_RUN_STATES).toEqual([
      "creating",
      "applying",
      "rendering",
      "inspecting",
      "verified",
      "unverified",
      "mismatch",
      "unavailable",
      "failed",
    ]);
    expect(SCENE_STATE_LABEL.verified).toBe("doğrulandı");
    expect(stateKind("scene.activity")).toBe("transient");
    expect(stateTtlMs("scene.activity")).toBe(SCENE_TTL_MS);
    expect(SCENE_TTL_MS).toBe(120_000);
    expect(GENESIS_STATES).toEqual(["capability.genesis"]);
    expect(GENESIS_RUN_STATES).toContain("cancelled");
    expect(GENESIS_STATE_LABEL.verified).toBe("doğrulandı");
    expect(APP_STATES).toEqual(["app.factory"]);
    expect(ARTIFACT_STATES).toEqual(["artifact.factory"]);
    expect(MAIL_STATES).toEqual(["mail.activity"]);
    expect(CALENDAR_STATES).toEqual(["calendar.activity"]);
    expect(DOCUMENT_STATES).toEqual(["document.analysis"]);
    expect(OPERATOR_STATES).toEqual(["operator.running", "operator.verifying", "operator.failed"]);
    expect(stateKind("agent.idle")).toBe("steady");
    // A v10 renderer's own reading of a v10 server is untouched: every v10
    // token still draws the body it drew, with the facts it carried.
    const scene = intentOf([SCENE_ACTIVITY("blender", "Kure", "verified", 3)]);
    expect(scene.kind).toBe("scene_activity");
    expect(scene.scene?.state).toBe("verified");
    expect(scene.executive).toBeNull();
  });
});

// ---------------------------------------------------------------- the facts

describe("the published facts, and nothing else", () => {
  it("reads the run, the step, the state and both counts exactly as sent", () => {
    expect(executiveFacts(RUNNING())).toEqual({
      run: "r1",
      step: "s3",
      stateToken: "running",
      state: "running",
      done: 2,
      total: 5,
    });
  });

  it("says nothing for an event that carried nothing, and nothing for no event at all", () => {
    const bare: ExecutiveFacts = { run: null, step: null, stateToken: null, state: null, done: null, total: null };
    expect(executiveFacts(EXECUTIVE_RUN_BARE())).toEqual(bare);
    expect(executiveFacts(null)).toEqual(bare);
  });

  it("keeps a state it cannot read as the token, and never promotes it", () => {
    const facts = executiveFacts(EXECUTIVE_RUN("r1", "completed_with_errors", "s3"));
    expect(facts.stateToken).toBe("completed_with_errors");
    expect(facts.state).toBeNull();
    expect(executiveStateWord("completed_with_errors")).toBe("completed_with_errors");
    expect(executiveStateWord(null)).toBe("durum bildirilmedi");
  });

  it("counts steps only when the publisher counted, and keeps zero", () => {
    expect(executiveFacts(EXECUTIVE_RUN("r1", "planned", null, 0, 5)).done).toBe(0);
    expect(executiveFacts(EXECUTIVE_RUN("r1", "running", "s1")).done).toBeNull();
    expect(executiveFacts(EXECUTIVE_RUN("r1", "running", "s1")).total).toBeNull();
    // A count the boundary cannot read is no count: `metadata.done` is a
    // string here, which `parseEvent` keeps as a token and this refuses.
    expect(executiveFacts(EXECUTIVE_RUN("r1", "running", "s1", null, 5, { done: "2" })).done).toBeNull();
  });

  it("parses a real event through the boundary without inventing a key", () => {
    const parsed = parseEvent(EXECUTIVE_RUN("r1", "paused", "s3", 2, 5));
    expect(parsed).not.toBeNull();
    expect(parsed?.subsystem).toBe("executive");
    expect(parsed?.metadata).toEqual({ run: "r1", step: "s3", state: "paused", done: 2, total: 5 });
    const bare = parseEvent(EXECUTIVE_RUN_BARE());
    expect(bare?.metadata).toEqual({});
    expect(bare?.metadata.done).toBeUndefined();
  });
});

// -------------------------------------------------------------- the posture

describe("the posture, from the published state alone", () => {
  it("gives each state its own posture, and a state it cannot read the running posture", () => {
    expect(executivePosture("planned")).toBe("planned");
    expect(executivePosture("running")).toBe("running");
    expect(executivePosture("paused")).toBe("paused");
    expect(executivePosture("completed")).toBe("completed");
    expect(executivePosture("partial")).toBe("partial");
    expect(executivePosture("cancelled")).toBe("cancelled");
    expect(executivePosture("failed")).toBe("failed");
    // A word this build cannot read, and no word at all, draw as a run in
    // progress: something is going and nothing settled has been said.
    expect(executivePosture(null)).toBe("running");
    expect(executivePosture(executiveFacts(EXECUTIVE_RUN("r1", "completed_with_errors")).state)).toBe("running");
  });

  it("settles nothing on a word it cannot read", () => {
    const facts = executiveFacts(EXECUTIVE_RUN("r1", "completed_with_errors", "s3", 5, 5));
    // Not settled, not complete, not partial — and not KNOWN to be going
    // either, which is why the panel offers no chip for it.
    expect(executiveRunIsSettled(facts.state)).toBe(false);
    expect(executiveRunIsComplete(facts)).toBe(false);
    expect(executiveRunIsPartial(facts)).toBe(false);
    expect(executiveRunIsActive(facts.state)).toBe(false);
    // And it says nothing in the owner's words but the token itself.
    expect(executiveCaption(facts)).toBe(EXECUTIVE_CAPTION_BARE);
    expect(executiveStatePhrase(facts)).toBeNull();
  });

  it("knows which states mean the run has not ended, and which mean it has", () => {
    expect(EXECUTIVE_ACTIVE_STATES).toEqual(["planned", "running", "paused"]);
    expect(EXECUTIVE_SETTLED_STATES).toEqual(["completed", "partial", "cancelled", "failed"]);
    for (const state of EXECUTIVE_RUN_STATES) {
      expect(executiveRunIsActive(state), state).toBe(EXECUTIVE_ACTIVE_STATES.includes(state));
      expect(executiveRunIsSettled(state), state).toBe(EXECUTIVE_SETTLED_STATES.includes(state));
      // Every state is one or the other, never both.
      expect(executiveRunIsActive(state) === executiveRunIsSettled(state), state).toBe(false);
    }
    expect(executiveRunIsActive(null)).toBe(false);
    expect(executiveRunIsSettled(null)).toBe(false);
  });

  it("opens the one door to 'done' for `completed` and for nothing else", () => {
    for (const state of EXECUTIVE_RUN_STATES) {
      expect(executiveRunIsComplete({ state }), state).toBe(state === "completed");
      expect(executiveRunIsPartial({ state }), state).toBe(state === "partial");
    }
    expect(executiveRunIsComplete({ state: null })).toBe(false);
    // A partial run is settled, and is NOT the completed one: it is neither
    // rounded up to done nor folded into the failure.
    expect(executivePosture("partial")).not.toBe("completed");
    expect(executivePosture("partial")).not.toBe("failed");
    expect(executivePosture("partial")).not.toBe("running");
  });

  it("draws a paused run as settled and calm rather than as an error", () => {
    expect(executivePosture("paused")).toBe("paused");
    expect(executivePosture("paused")).not.toBe("failed");
    expect(EXECUTIVE_RUN_STATE_LABEL.paused).not.toContain("hata");
    expect(EXECUTIVE_RUN_STATE_LABEL.paused).not.toContain("başarısız");
    // It has not ended, so the owner's levers still mean something.
    expect(executiveRunIsActive("paused")).toBe(true);
    expect(executiveRunIsSettled("paused")).toBe(false);
    // A cancelled run has ended, and is not worded as a failure either.
    expect(executiveRunIsSettled("cancelled")).toBe(true);
    expect(EXECUTIVE_RUN_STATE_LABEL.cancelled).not.toContain("başarısız");
  });
});

// ------------------------------------------------------------- the captions

describe("the caption says the state and everything published with it, and no more", () => {
  it("says a running run as step · state · counts, each part only if published", () => {
    expect(executiveCaption(executiveFacts(RUNNING()))).toBe("adım s3 · çalışıyor · 2/5 adım");
    expect(executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", "running", "s3")))).toBe("adım s3 · çalışıyor");
    expect(executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", "running", null, 2, 5)))).toBe("çalışıyor · 2/5 adım");
    expect(executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", "running", null)))).toBe("çalışıyor");
    // The run's id is not a sentence and never joins the caption.
    expect(executiveCaption(executiveFacts(RUNNING()))).not.toContain("r1");
  });

  it("draws the counts only when BOTH were published", () => {
    expect(executiveStepsPhrase(2, 5)).toBe("2/5 adım");
    expect(executiveStepsPhrase(0, 5)).toBe("0/5 adım");
    expect(executiveStepsPhrase(2, null)).toBeNull();
    expect(executiveStepsPhrase(null, 5)).toBeNull();
    expect(executiveStepsPhrase(null, null)).toBeNull();
    // A `done` alone is not a fraction, and nothing in the caption pretends it is.
    const half = executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", "running", "s3", 2, null)));
    expect(half).toBe("adım s3 · çalışıyor");
    expect(half).not.toContain("2");
    expect(executiveStepPhrase("s3")).toBe("adım s3");
    expect(executiveStepPhrase(null)).toBeNull();
  });

  it("says 'tamamlandı' for completed, and for no other state however it was published", () => {
    expect(executiveCaption(executiveFacts(COMPLETED()))).toBe("tamamlandı · 5/5 adım");
    // Every other state, published with the same step and the same full
    // counts, still refuses the word — the counts do not finish a run.
    for (const state of EXECUTIVE_RUN_STATES.filter((s) => s !== "completed")) {
      const caption = executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", state, "s5", 5, 5)));
      expect(caption, state).not.toContain("tamamlandı");
    }
    // And neither does a word this build cannot read, however like it looks.
    expect(executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", "completed_with_errors", "s5", 5, 5)))).toBe(EXECUTIVE_CAPTION_BARE);
    expect(executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", "complete", "s5", 5, 5)))).toBe(EXECUTIVE_CAPTION_BARE);
  });

  it("names what a partial run is missing wherever the missing steps are known", () => {
    expect(executiveCaption(executiveFacts(PARTIAL()))).toBe("kısmen bitti · 3/5 adım");
    // What did not verify is not on the bus (v11 carries no such key), so the
    // row passes it and the sentence is spelled from the same place.
    expect(executiveStatePhrase({ state: "partial" }, ["s4", "s5"])).toBe("kısmen bitti — eksik: s4, s5");
    expect(executiveCaption(executiveFacts(PARTIAL()), ["s4 (belge bulunamadı)"])).toBe(
      "kısmen bitti — eksik: s4 (belge bulunamadı) · 3/5 adım",
    );
    // A missing list beside a state that is not partial is not a partial run.
    expect(executiveStatePhrase({ state: "completed" }, ["s4"])).toBe("tamamlandı");
    expect(executiveStatePhrase({ state: "failed" }, ["s4"])).toBe("başarısız");
  });

  it("says the step only while the run is still on one", () => {
    // A step beside an ended run would read as a run that stopped mid-step.
    for (const state of EXECUTIVE_SETTLED_STATES) {
      const caption = executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", state, "s4", 3, 5)));
      expect(caption, state).not.toContain("adım s4");
    }
    for (const state of EXECUTIVE_ACTIVE_STATES) {
      const caption = executiveCaption(executiveFacts(EXECUTIVE_RUN("r1", state, "s4", 3, 5)));
      expect(caption, state).toContain("adım s4");
    }
  });

  it("falls back to the bare token for a state it cannot read, and for none at all", () => {
    expect(executiveCaption(executiveFacts(EXECUTIVE_RUN_BARE()))).toBe(EXECUTIVE_CAPTION_BARE);
    expect(executiveCaption(executiveFacts(null))).toBe(EXECUTIVE_CAPTION_BARE);
    expect(executiveStatePhrase(factsOf({ state: null, stateToken: null }))).toBeNull();
  });

  it("lines the facts with what was sent and the statement of what was not", () => {
    expect(executiveFactsLine(executiveFacts(RUNNING()))).toBe("iş: r1 · adım: s3 · durum: çalışıyor · 2/5 adım");
    expect(executiveFactsLine(executiveFacts(EXECUTIVE_RUN_BARE()))).toBe("iş bildirilmedi · adım bildirilmedi · durum bildirilmedi");
    expect(executiveFactsLine(executiveFacts(EXECUTIVE_RUN("r1", "completed_with_errors", null)))).toBe(
      "iş: r1 · adım bildirilmedi · durum: completed_with_errors",
    );
    // The absence of a count is said only where how much got done is the question.
    expect(executiveFactsLine(executiveFacts(EXECUTIVE_RUN("r1", "partial", null)))).toBe(
      `iş: r1 · adım bildirilmedi · durum: kısmen bitti · ${EXECUTIVE_STEPS_UNTOLD}`,
    );
    expect(executiveFactsLine(executiveFacts(EXECUTIVE_RUN("r1", "running", "s3")))).not.toContain(EXECUTIVE_STEPS_UNTOLD);
    // The row/caption helper falls back to the word, never to nothing.
    expect(executiveStateLine(executiveFacts(COMPLETED()))).toBe("tamamlandı");
    expect(executiveStateLine(executiveFacts(PARTIAL()), ["s4"])).toBe("kısmen bitti — eksik: s4");
    expect(executiveStateLine(executiveFacts(EXECUTIVE_RUN_BARE()))).toBe("durum bildirilmedi");
    expect(executiveStateLine(executiveFacts(EXECUTIVE_RUN("r1", "completed_with_errors")))).toBe("completed_with_errors");
    expect(EXECUTIVE_MISSING_UNTOLD).not.toBe(EXECUTIVE_STEPS_UNTOLD);
  });
});

// ----------------------------------------------------------------- the view

describe("the view over one claim", () => {
  it("is active while the claim holds, with the facts, the posture and the caption", () => {
    const view = executiveView(executiveClaim(truthOf([RUNNING()]), T0 + 2_000));
    expect(view.stage).toBe("active");
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(false);
    expect(view.posture).toBe("running");
    expect(view.caption).toBe("adım s3 · çalışıyor · 2/5 adım");
    expect(view.run).toBe("r1");
    expect(view.step).toBe("s3");
    expect(view.done).toBe(2);
    expect(view.total).toBe(5);
    expect(view.ageMs).toBe(2_000);
    expect(executiveIsActive(view)).toBe(true);
  });

  it("stops claiming the state once it ages out, without saying the run ended", () => {
    const view = executiveView(executiveClaim(truthOf([RUNNING()]), T0 + EXECUTIVE_TTL_MS + 1_000));
    expect(view.stage).toBe("none");
    // What WAS published is still a fact, and the panel words it as last-known.
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(true);
    expect(view.caption).toBe("adım s3 · çalışıyor · 2/5 adım");
    expect(view.caption).not.toContain("tamamlandı");
    expect(executiveIsActive(view)).toBe(false);
  });

  it("reports nothing at all when nothing was ever published about a run", () => {
    const view = executiveView(executiveClaim(truthOf([AGENT_IDLE()]), T0));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBeNull();
    expect(view.caption).toBe(EXECUTIVE_CAPTION_BARE);
    expect(view.posture).toBe("running");
    expect(view.ageMs).toBeNull();
  });

  it("reads the newest executive event by membership, and never another family's", () => {
    const truth = truthOf([RUNNING(), PAUSED()]);
    expect(executiveView(executiveClaim(truth, T0)).state).toBe("paused");
    for (const other of [[DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [ARTIFACT_FACTORY()], [APP_FACTORY()], [CAPABILITY_GENESIS()], [SCENE_ACTIVITY()]]) {
      expect(executiveClaim(truthOf(other), T0).event).toBeNull();
    }
    // And the other families' claims never pick an executive event up.
    const run = truthOf([RUNNING()]);
    for (const claim of [documentClaim, mailClaim, calendarClaim, artifactClaim, appClaim, genesisClaim, sceneClaim]) {
      expect(claim(run, T0).event).toBeNull();
    }
  });
});

// ------------------------------------------------------------- the geometry

describe("the Core's body for one run", () => {
  it("gives the planned, running and paused states distinct postures", () => {
    const planned = intentOf([PLANNED()]);
    const running = intentOf([RUNNING()]);
    const paused = intentOf([PAUSED()]);
    for (const intent of [planned, running, paused]) expect(intent.kind).toBe("executive_run");
    // Planned: the graph exists and nothing is flowing anywhere yet.
    expect(planned.flowRate).toBe(0);
    expect(planned.inwardFlow).toBe(0);
    // Running: steps going out to the families that do them.
    expect(running.flowRate).toBeGreaterThan(0);
    expect(running.inwardFlow).toBe(0);
    expect(running.ringSpin).toBeGreaterThan(planned.ringSpin);
    expect(isExecutiveRunning(running)).toBe(true);
    expect(isExecutivePaused(running)).toBe(false);
  });

  it("holds a paused run exactly as it holds a Core waiting on the owner, and never agitates", () => {
    const paused = intentOf([PAUSED()]);
    expect(isExecutivePaused(paused)).toBe(true);
    expect(paused.palette).toBe("held");
    expect(paused.palette).not.toBe("fault");
    expect(paused.restraint).toBe(1);
    expect(paused.agitation).toBe(0);
    expect(paused.flowRate).toBe(0);
    expect(paused.inwardFlow).toBe(0);
    expect(paused.topology).toBe(0);
    expect(paused.severity).toBe("info");
    expect(paused.kind).not.toBe("error");
    // Calmer than the run it came from, and quieter than the failure.
    const running = intentOf([RUNNING()]);
    const failed = intentOf([FAILED()]);
    expect(paused.ringSpin).toBeLessThan(running.ringSpin);
    expect(paused.glow).toBeLessThan(failed.glow);
  });

  it("settles on completed, holds a partial and a failure under restraint, and never agitates", () => {
    const completed = intentOf([COMPLETED()]);
    expect(completed.palette).toBe("ready");
    expect(completed.flowRate).toBe(0);
    expect(completed.restraint).toBe(0);
    expect(completed.agitation).toBe(0);

    const partial = intentOf([PARTIAL()]);
    expect(partial.restraint).toBeGreaterThan(0);
    expect(partial.flowRate).toBe(0);
    expect(partial.agitation).toBe(0);
    expect(partial.palette).not.toBe("ready");
    expect(partial.glow).toBeLessThan(completed.glow);
    expect(partial.label).not.toContain("tamamlandı");

    const failed = intentOf([FAILED()]);
    expect(failed.restraint).toBeGreaterThan(0);
    expect(failed.agitation).toBe(0);

    // A cancelled run is settled and dim: the owner's own decision, held
    // rather than faulted, and never dressed as a failure.
    const cancelled = intentOf([CANCELLED()]);
    expect(cancelled.palette).toBe("held");
    expect(cancelled.palette).not.toBe("fault");
    expect(cancelled.agitation).toBe(0);
    expect(cancelled.flowRate).toBe(0);
    expect(cancelled.label).not.toContain("başarısız");
  });

  it("draws a bar only from two published counts, and never from one", () => {
    expect(executiveProgress({ done: 2, total: 5 })).toBe(0.4);
    expect(executiveProgress({ done: 0, total: 5 })).toBe(0);
    expect(executiveProgress({ done: 2, total: null })).toBeNull();
    expect(executiveProgress({ done: null, total: 5 })).toBeNull();
    // A graph with no steps has no progress to draw, and a publisher that
    // over-counts brightens nothing past a full bar.
    expect(executiveProgress({ done: 0, total: 0 })).toBeNull();
    expect(executiveProgress({ done: 7, total: 5 })).toBe(1);

    expect(intentOf([RUNNING()]).progress).toBe(0.4);
    expect(intentOf([EXECUTIVE_RUN("r1", "running", "s3", 2, null)]).progress).toBeNull();
    expect(intentOf([EXECUTIVE_RUN("r1", "running", "s3")]).progress).toBeNull();
    expect(intentOf([EXECUTIVE_RUN_BARE()]).progress).toBeNull();
    // The bar is the COUNT the publisher sent, and the count is a fact even
    // beside a state this build cannot read: five of five steps finished is
    // what the publisher said. What the bar does not do is finish the run —
    // the caption stays the bare token, because the state is the part we
    // cannot read, and a full bar is not a claim that anything is done.
    const unknown = intentOf([EXECUTIVE_RUN("r1", "completed_with_errors", "s3", 5, 5)]);
    expect(unknown.kind).toBe("executive_run");
    expect(unknown.label).toBe(EXECUTIVE_CAPTION_BARE);
    expect(unknown.label).not.toContain("tamamlandı");
    expect(unknown.progress).toBe(1);
  });

  it("never draws a constellation or a candidate for a run", () => {
    for (const events of [[PLANNED()], [RUNNING()], [COMPLETED()], [PAUSED()], [EXECUTIVE_RUN_BARE()]]) {
      const intent = intentOf(events);
      expect(intent.constellationNodes).toBe(0);
      expect(intent.capabilityNodes).toBe(0);
      expect(intent.satelliteComplete).toBe(false);
    }
  });

  it("moves no channel for an event whose publisher declared no intensity", () => {
    const intent = intentOf([EXECUTIVE_RUN_BARE()]);
    expect(intent.energy).toBe(0);
    expect(intent.intensity).toBeNull();
    expect(intent.executive).not.toBeNull();
    expect(intent.label).toBe(EXECUTIVE_CAPTION_BARE);
  });

  it("keeps the facts on the last-known shape, and stops every motion channel", () => {
    const intent = intentOf([RUNNING()], T0 + EXECUTIVE_TTL_MS + 1_000);
    expect(intent.kind).toBe("last_known");
    expect(intent.executive?.run).toBe("r1");
    expect(intent.executive?.state).toBe("running");
    // A last-known shape is not a live one for any of the predicates.
    expect(isExecutiveRunning(intent)).toBe(false);
    expect(isExecutivePaused(intent)).toBe(false);
    for (const channel of MOTION_CHANNELS) expect(intent[channel], channel).toBe(0);
  });

  it("carries no executive facts on any other kind", () => {
    for (const events of [[AGENT_IDLE()], [OPERATOR_RUNNING()], [DOCUMENT_ANALYSIS()], [ARTIFACT_FACTORY()], [APP_FACTORY()], [CAPABILITY_GENESIS()], [SCENE_ACTIVITY()]]) {
      const intent = intentOf(events);
      expect(intent.executive).toBeNull();
      expect(isExecutiveRunning(intent)).toBe(false);
    }
    // And an executive event carries no other family's facts.
    const run = intentOf([RUNNING()]);
    expect(run.scene).toBeNull();
    expect(run.app).toBeNull();
    expect(run.genesis).toBeNull();
    expect(run.artifact).toBeNull();
    expect(run.document).toBeNull();
    expect(run.mail).toBeNull();
    expect(run.calendar).toBeNull();
    for (const other of [isOperatorActing, isDocumentReading, isMailReading, isCalendarPlanning, isArtifactMaking, isAppBuilding, isGenesisWorking, isSceneWorking]) {
      expect(other(run)).toBe(false);
    }
  });

  it("does not let a run event blank the room, or the room blank a run", () => {
    const both = truthOf([RUNNING(), OWNER_AWAY()]);
    // The room's newest event is not the core body's.
    expect(coreClaim(both, T0).event?.state).toBe("executive.run");
    expect(executiveClaim(both, T0).event?.state).toBe("executive.run");
  });

  it("keeps the more specific bus body under a local tool_running voice leg", () => {
    const bus = intentOf([RUNNING()]);
    const overlaid = applyVoiceOverlay(bus, VOICE_TOOL_RUNNING);
    // The bus knows the run, the step and the counts; the local leg only
    // knows that a tool is running. The more specific of two true statements
    // wins.
    expect(overlaid).toBe(bus);
    expect(overlaid.executive?.step).toBe("s3");
    // A last-known executive shape yields to the local observation, as every
    // other aged bus body does.
    const stale = intentOf([RUNNING()], T0 + EXECUTIVE_TTL_MS + 1_000);
    expect(applyVoiceOverlay(stale, VOICE_TOOL_RUNNING).source).toBe("voice");
  });

  it("draws memory rather than observation when the Cloud Core cannot be reached", () => {
    const truth = applyError(truthOf([RUNNING()]), "ECONNREFUSED", T0 + 1_000);
    const intent = visualFor(truth, T0 + 1_000);
    expect(intent.kind).toBe("unreachable");
    expect(intent.executive?.state).toBe("running");
    expect(intent.flowRate).toBe(0);
    expect(intent.energy).toBe(0);
  });
});
