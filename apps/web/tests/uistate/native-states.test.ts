/**
 * Contract v13: `native.build` (M28 spec §4, §6).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to a program the owner could actually install: **the Core
 * names an application, a target, a step and a verdict because the Cloud Core
 * published them — in the words the spec gives it — never a step it inferred,
 * never "doğrulandı" before an INDEPENDENT reader agreed, and never a
 * toolchain this machine does not have drawn as a failure.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v12 (or
 * v11, …, v2) is read normally, because v13 only added.
 */

import { describe, expect, it } from "vitest";

import {
  KNOWN_CONTRACT_VERSION,
  MIN_SUPPORTED_CONTRACT_VERSION,
  NATIVE_BUILD as NATIVE_BUILD_TOKEN,
  NATIVE_BUILD_STATES,
  NATIVE_CAPTION_BARE,
  NATIVE_STACKS,
  NATIVE_STACK_LABEL,
  NATIVE_STATES,
  NATIVE_STATE_LABEL,
  NATIVE_TARGETS,
  NATIVE_TARGET_LABEL,
  NATIVE_TTL_MS,
  OPERATION_TTL_MS,
  SHA256_PREFIX_CHARS,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  asArtifactBytes,
  contractCompatibility,
  isAppState,
  isCoreChannel,
  isCreativeState,
  isExecutiveState,
  isKnownState,
  isNativeBuildState,
  isNativeStack,
  isNativeState,
  isNativeTarget,
  isSceneState,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  NATIVE_WORKING_STATES,
  type NativeFacts,
  nativeCaption,
  nativeFacts,
  nativeIsActive,
  nativeIsUnavailable,
  nativeIsVerified,
  nativeIsWorking,
  nativePosture,
  nativeStackWord,
  nativeStatePhrase,
  nativeTargetWord,
  nativeView,
} from "../../app/lib/uistate/native";
import {
  KIND_DETAIL,
  KIND_LABEL,
  NATIVE_EMPTY,
  NATIVE_LABEL,
  NATIVE_UNTOLD,
  NATIVE_VERDICT_UNTOLD,
  STATE_LABEL,
  nativeFactsLine,
  nativeStateLine,
  nativeStateWord,
  stateLabel,
  subsystemLabel,
} from "../../app/lib/uistate/labels";
import {
  applyResponse,
  coreClaim,
  creativeClaim,
  emptyTruth,
  executiveClaim,
  nativeClaim,
  sceneClaim,
} from "../../app/lib/uistate/truth";
import { type VisualIntent, visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  CREATIVE_ACTIVITY,
  EXECUTIVE_RUN,
  NATIVE_BUILD,
  NATIVE_BUILD_BARE,
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
  "constellationDrift",
] as const satisfies readonly (keyof VisualIntent)[];

const PLANNED = () => NATIVE_BUILD("Notlarim", "windows_exe", "planned");
const GENERATING = () => NATIVE_BUILD("Notlarim", "windows_exe", "generating");
const BUILDING = () => NATIVE_BUILD("Notlarim", "windows_exe", "building");
const TESTING = () => NATIVE_BUILD("Notlarim", "windows_exe", "testing");
const PACKAGING = () => NATIVE_BUILD("Notlarim", "windows_portable", "packaging");
const VALIDATING = () => NATIVE_BUILD("Notlarim", "windows_exe", "validating");
const VERIFIED = () => NATIVE_BUILD("Notlarim", "windows_exe", "verified", "dotnet_wpf", "ok");
const UNVERIFIED = () => NATIVE_BUILD("Notlarim", "windows_msix", "unverified");
const MISMATCH = () =>
  NATIVE_BUILD("Notlarim", "windows_exe", "mismatch", "dotnet_wpf", "sürüm tutmadı");
const UNAVAILABLE = () => NATIVE_BUILD("Sayac", "android_apk", "unavailable", "android_kotlin");
const FAILED = () => NATIVE_BUILD("Notlarim", "windows_exe", "failed");

/** Facts as the publisher would have produced them, for the pure caption checks. */
function factsOf(overrides: Partial<NativeFacts> = {}): NativeFacts {
  return {
    appToken: "Notlarim",
    targetToken: "windows_exe",
    target: "windows_exe",
    stateToken: "building",
    state: "building",
    stackToken: "dotnet_wpf",
    stack: "dotnet_wpf",
    verdictToken: null,
    ...overrides,
  };
}

// ------------------------------------------------------------ the contract

describe("contract v13 is v12 plus the native build state, and says so", () => {
  it("still reads a v13, v12, v11 … v2 server from a build at v13 or later", () => {
    expect(KNOWN_CONTRACT_VERSION).toBeGreaterThanOrEqual(13);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION)).toBe("current");
    const built: number = KNOWN_CONTRACT_VERSION;
    expect(contractCompatibility(13)).toBe(built === 13 ? "current" : "older_supported");
    for (const older of [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2]) {
      expect(contractCompatibility(older), `v${older}`).toBe("older_supported");
    }
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION + 1)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("names the one token once, and knows it by membership rather than by prefix", () => {
    expect(NATIVE_BUILD_TOKEN).toBe("native.build");
    expect(NATIVE_STATES).toEqual(["native.build"]);
    expect(isKnownState("native.build")).toBe(true);
    expect(isNativeState("native.build")).toBe(true);
    // A newer server's word is not a state this build may draw as a build.
    expect(isNativeState("native.installed")).toBe(false);
    expect(isNativeState("native.build_started")).toBe(false);
    // And it belongs to no other family — including M23's, whose subject it
    // shares. `app.factory` scaffolds and runs a web app on the owner's
    // machine; `native.build` compiles an EXE. Two milestones, two
    // subsystems, two vocabularies.
    expect(isAppState("native.build")).toBe(false);
    expect(isNativeState("app.factory")).toBe(false);
    expect(isCreativeState("native.build")).toBe(false);
    expect(isExecutiveState("native.build")).toBe(false);
    expect(isSceneState("native.build")).toBe(false);
  });

  it("appends the token after v12's, never reordering", () => {
    expect(UI_STATES.indexOf("native.build")).toBe(UI_STATES.indexOf("creative.activity") + 1);
    expect(UI_STATES.indexOf("creative.activity")).toBe(UI_STATES.indexOf("executive.run") + 1);
    expect(UI_STATES[0]).toBe("agent.idle");
    // The last one, which is what "appended" means when the list is read in
    // order by anything else.
    expect(UI_STATES[UI_STATES.length - 1]).toBe("native.build");
  });

  it("types the five targets the Cloud Core can actually be asked for, and nothing outside them", () => {
    // FIVE, not the six §2's prose lists. `ios_project` is not a target on
    // the Cloud Core either (`app/nativefactory/spec.py` says so in as many
    // words): there is no macOS, no Xcode and no MAUI workload here, and a
    // project that looked like progress towards an iPhone application would
    // be the one thing this milestone exists to refuse.
    expect(NATIVE_TARGETS).toEqual([
      "windows_exe",
      "windows_portable",
      "windows_msix",
      "android_apk",
      "android_aab",
    ]);
    expect(NATIVE_TARGETS).not.toContain("ios_project");
    for (const target of NATIVE_TARGETS) expect(isNativeTarget(target), target).toBe(true);
    expect(isNativeTarget("ios_project")).toBe(false);
    expect(isNativeTarget("windows_msi")).toBe(false);
    expect(isNativeTarget("WINDOWS_EXE")).toBe(false);
    expect(isNativeTarget(null)).toBe(false);
    // Every one has an owner-facing word; a target with none would print a
    // wire token on the panel.
    for (const target of NATIVE_TARGETS) expect(NATIVE_TARGET_LABEL[target]).toBeTruthy();
  });

  it("types the four stacks the selection rule may choose, and refuses MAUI", () => {
    expect(NATIVE_STACKS).toEqual(["dotnet_wpf", "dotnet_winforms", "tauri", "android_kotlin"]);
    // Measured 2026-09-09: `dotnet workload list` reports no MAUI workload,
    // and the assistant starts no downloads (ADR-0095 decision 1).
    expect(NATIVE_STACKS).not.toContain("dotnet_maui");
    for (const stack of NATIVE_STACKS) expect(isNativeStack(stack), stack).toBe(true);
    expect(isNativeStack("dotnet_maui")).toBe(false);
    expect(isNativeStack("electron")).toBe(false);
    for (const stack of NATIVE_STACKS) expect(NATIVE_STACK_LABEL[stack]).toBeTruthy();
  });

  it("types the eleven steps and reads none of them by prefix", () => {
    expect(NATIVE_BUILD_STATES).toEqual([
      "planned",
      "generating",
      "building",
      "testing",
      "packaging",
      "validating",
      "verified",
      "unverified",
      "mismatch",
      "unavailable",
      "failed",
    ]);
    for (const state of NATIVE_BUILD_STATES) expect(isNativeBuildState(state), state).toBe(true);
    // The word that must never be reached by resemblance.
    expect(isNativeBuildState("verified_partially")).toBe(false);
    expect(isNativeBuildState("Verified")).toBe(false);
    expect(isNativeBuildState("signing")).toBe(false);
    expect(isNativeBuildState(12)).toBe(false);
    // Every one has an owner-facing word.
    for (const state of NATIVE_BUILD_STATES) expect(NATIVE_STATE_LABEL[state]).toBeTruthy();
  });

  it("says 'doğrulandı' for exactly one step, and never for the honest middle", () => {
    const verified = NATIVE_STATE_LABEL.verified;
    expect(verified).toBe("doğrulandı");
    for (const state of NATIVE_BUILD_STATES) {
      if (state === "verified") continue;
      expect(NATIVE_STATE_LABEL[state], state).not.toBe(verified);
      // Nor may another word CONTAIN it: "doğrulanamadı" would read as
      // "doğrulandı" to a skimming eye, so `unverified` is worded around it.
      expect(NATIVE_STATE_LABEL[state].startsWith(verified), state).toBe(false);
    }
    // `unavailable` is a fact about this machine, not a fault, and its word
    // must not be a failure word.
    expect(NATIVE_STATE_LABEL.unavailable).not.toBe(NATIVE_STATE_LABEL.failed);
  });

  it("adds the nativefactory subsystem and names it, without touching M23's", () => {
    expect(SUBSYSTEMS).toContain("nativefactory");
    expect(subsystemLabel("nativefactory")).toBe("Yerel uygulamalar");
    // M23's own subsystem stays exactly as the Cloud Core publishes it.
    expect(SUBSYSTEMS).toContain("appfactory");
    expect(subsystemLabel("appfactory")).toBe("Uygulamalar");
  });

  it("keeps the token on the agent channel, which drives the core body", () => {
    expect(stateChannel("native.build")).toBe("agent");
    expect(isCoreChannel("native.build")).toBe(true);
    // The room is untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("release.deploying")).toBe("release");
  });

  it("gives a build a horizon that survives a real compiler run", () => {
    // M28 §5 bounds each build command at twenty minutes of Job Object time,
    // and the publisher speaks once per STEP. The transient horizon would
    // call a healthy `dotnet publish` lost within twelve seconds.
    expect(stateKind("native.build")).toBe("operation");
    expect(NATIVE_TTL_MS).toBeGreaterThan(20 * 60_000);
    expect(NATIVE_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(NATIVE_TTL_MS).toBeGreaterThan(OPERATION_TTL_MS);
    expect(stateTtlMs("native.build")).toBe(NATIVE_TTL_MS);
    // But it IS a horizon: the publisher's own ttl still wins, either way.
    const short = NATIVE_BUILD("Notlarim", "windows_exe", "building", "dotnet_wpf", null, { ttl_s: 30 });
    expect(stateTtlMs("native.build", short)).toBe(30_000);
  });

  it("names the state and the kind in Turkish, with no verb in the bare caption", () => {
    expect(STATE_LABEL["native.build"]).toBe(NATIVE_CAPTION_BARE);
    expect(stateLabel("native.build")).toBe(NATIVE_CAPTION_BARE);
    expect(KIND_LABEL.native_build).toBe(NATIVE_CAPTION_BARE);
    // The bare caption must be true of a build in ANY of the eleven steps,
    // which is why it carries no verb at all.
    expect(NATIVE_CAPTION_BARE).not.toMatch(/yor$|dı$|di$/);
    // The kind's sentence says what "done" means here and what is refused.
    expect(KIND_DETAIL.native_build).toContain("bağımsız");
    expect(KIND_DETAIL.native_build).toContain("İlerleme bildirilmez.");
  });

  it("takes a byte count only when one was really reported", () => {
    expect(asArtifactBytes(69_632_000)).toBe(69_632_000);
    expect(asArtifactBytes(1)).toBe(1);
    // A zero-byte artefact is not a small file; it is a file that is not
    // there, and `validate_against_spec` already calls that a mismatch.
    expect(asArtifactBytes(0)).toBeNull();
    expect(asArtifactBytes(-1)).toBeNull();
    expect(asArtifactBytes(1.5)).toBeNull();
    expect(asArtifactBytes("68 MB")).toBeNull();
    expect(asArtifactBytes(null)).toBeNull();
    expect(SHA256_PREFIX_CHARS).toBeGreaterThanOrEqual(8);
    expect(SHA256_PREFIX_CHARS).toBeLessThan(64);
  });
});

// --------------------------------------------------------------- the facts

describe("the facts are what the publisher sent, and nothing else", () => {
  it("reads every metadata token verbatim, and types the four closed ones", () => {
    const facts = nativeFacts(parseEvent(VERIFIED()));
    expect(facts.appToken).toBe("Notlarim");
    expect(facts.targetToken).toBe("windows_exe");
    expect(facts.target).toBe("windows_exe");
    expect(facts.stateToken).toBe("verified");
    expect(facts.state).toBe("verified");
    expect(facts.stackToken).toBe("dotnet_wpf");
    expect(facts.stack).toBe("dotnet_wpf");
    expect(facts.verdictToken).toBe("ok");
  });

  it("keeps a token this build cannot read as a token, never as one of ours", () => {
    const facts = nativeFacts(parseEvent(NATIVE_BUILD("Notlarim", "ios_project", "signing", "dotnet_maui")));
    expect(facts.targetToken).toBe("ios_project");
    expect(facts.target).toBeNull();
    expect(facts.stateToken).toBe("signing");
    expect(facts.state).toBeNull();
    expect(facts.stackToken).toBe("dotnet_maui");
    expect(facts.stack).toBeNull();
    // And nothing about an unreadable step is drawn as settled.
    expect(nativeIsVerified(facts)).toBe(false);
    expect(nativeIsUnavailable(facts)).toBe(false);
    expect(nativeIsWorking(facts.state)).toBe(false);
  });

  it("says nothing at all for an event with no metadata", () => {
    const facts = nativeFacts(parseEvent(NATIVE_BUILD_BARE()));
    expect(facts.appToken).toBeNull();
    expect(facts.targetToken).toBeNull();
    expect(facts.stateToken).toBeNull();
    expect(facts.stackToken).toBeNull();
    expect(facts.verdictToken).toBeNull();
    expect(nativeCaption(facts)).toBe(NATIVE_CAPTION_BARE);
    expect(nativeStatePhrase(facts)).toBeNull();
  });

  it("names the five working steps and only those", () => {
    expect([...NATIVE_WORKING_STATES]).toEqual([
      "generating",
      "building",
      "testing",
      "packaging",
      "validating",
    ]);
    for (const state of NATIVE_WORKING_STATES) expect(nativeIsWorking(state), state).toBe(true);
    // A plan is not work; a settled build is not work; an unknown word is not
    // KNOWN to be work.
    for (const state of ["planned", "verified", "unverified", "mismatch", "unavailable", "failed"] as const) {
      expect(nativeIsWorking(state), state).toBe(false);
    }
    expect(nativeIsWorking(null)).toBe(false);
  });
});

// ------------------------------------------------------------ the postures

describe("the posture comes from the published step alone", () => {
  it("maps each of the eleven steps onto its deliberate posture", () => {
    expect(nativePosture("planned")).toBe("planning");
    expect(nativePosture("generating")).toBe("making");
    expect(nativePosture("building")).toBe("making");
    expect(nativePosture("packaging")).toBe("making");
    expect(nativePosture("testing")).toBe("testing");
    expect(nativePosture("validating")).toBe("reading");
    expect(nativePosture("verified")).toBe("verified");
    expect(nativePosture("unverified")).toBe("unverified");
    expect(nativePosture("mismatch")).toBe("mismatch");
    expect(nativePosture("unavailable")).toBe("unavailable");
    expect(nativePosture("failed")).toBe("failed");
    // A step this build cannot read is a build in progress and nothing more.
    expect(nativePosture(null)).toBe("making");
  });

  it("reaches 'verified' from the one published word and from nothing else", () => {
    expect(nativeIsVerified(factsOf({ state: "verified", stateToken: "verified" }))).toBe(true);
    for (const state of NATIVE_BUILD_STATES) {
      if (state === "verified") continue;
      expect(nativeIsVerified(factsOf({ state, stateToken: state })), state).toBe(false);
    }
    // Not from an artefact that exists, not from a reader that said "ok"
    // beside a step that is not `verified`: the STEP is the claim.
    expect(nativeIsVerified(factsOf({ state: "packaging", verdictToken: "ok" }))).toBe(false);
    expect(nativeIsVerified(factsOf({ state: null, stateToken: "signing", verdictToken: "ok" }))).toBe(false);
  });

  it("keeps an unreachable toolchain apart from a failure, in the geometry", () => {
    const unavailable = intentOf([UNAVAILABLE()]);
    const failed = intentOf([FAILED()]);
    expect(unavailable.kind).toBe("native_build");
    expect(failed.kind).toBe("native_build");
    // The one that is a fact about the world is dim and held; the one that
    // is a defect is held too, but they are not the same drawing.
    expect(unavailable.palette).toBe("held");
    expect(failed.palette).not.toBe("held");
    expect(unavailable.glow).toBeLessThan(failed.glow);
    // Neither agitates. A missing JDK is not an alarm, and a failed build is
    // reported rather than performed.
    expect(unavailable.agitation ?? 0).toBe(0);
    expect(failed.agitation ?? 0).toBe(0);
    expect(unavailable.palette).not.toBe("fault");
    expect(failed.palette).not.toBe("fault");
  });

  it("is still and bright only where the independent reader agreed", () => {
    const verified = intentOf([VERIFIED()]);
    expect(verified.palette).toBe("ready");
    expect(verified.flowRate).toBe(0);
    // `unverified` is just as still and deliberately NOT bright: nothing
    // disagreed, and nothing was verified either.
    const unverified = intentOf([UNVERIFIED()]);
    expect(unverified.flowRate).toBe(0);
    expect(unverified.palette).not.toBe("ready");
    expect(unverified.glow).toBeLessThan(verified.glow);
  });

  it("draws no progress bar for any step of any build", () => {
    // A build publishes STEPS. A bar over them would be an estimate of how
    // long a compiler will take, which is exactly the invented claim
    // ADR-0052 §2 forbids.
    for (const make of [PLANNED, GENERATING, BUILDING, TESTING, PACKAGING, VALIDATING, VERIFIED, UNVERIFIED, MISMATCH, UNAVAILABLE, FAILED]) {
      expect(intentOf([make()]).progress, make.name).toBeNull();
    }
  });

  it("leaves the room and the other families alone", () => {
    // A published build must not blank the ambient band, and an ambient
    // event must not claim the Core is building.
    const both = truthOf([NATIVE_BUILD(), OWNER_AWAY()]);
    expect(nativeClaim(both, T0).event?.state).toBe("native.build");
    // And the neighbouring families' claims never pick it up.
    expect(creativeClaim(both, T0).event).toBeNull();
    expect(executiveClaim(both, T0).event).toBeNull();
    expect(sceneClaim(both, T0).event).toBeNull();
    // While each of theirs is invisible to this one.
    const others = truthOf([CREATIVE_ACTIVITY(), EXECUTIVE_RUN(), SCENE_ACTIVITY()]);
    expect(nativeClaim(others, T0).event).toBeNull();
  });

  it("does not let a stale build stand as the core body for ever", () => {
    const stale = truthOf([NATIVE_BUILD()], T0);
    const later = T0 + NATIVE_TTL_MS + 1_000;
    expect(coreClaim(stale, later).expired).toBe(true);
    const intent = visualFor(stale, later);
    expect(intent.kind).toBe("last_known");
    // And a last-known build moves nothing.
    for (const channel of MOTION_CHANNELS) {
      expect(intent[channel] ?? 0, channel).toBe(0);
    }
  });
});

// ------------------------------------------------------------- the captions

describe("the caption says what was published and no more", () => {
  it("names the application, the target and the step, in that order", () => {
    expect(nativeCaption(factsOf())).toBe("Notlarim · Windows EXE · derleniyor");
    expect(nativeCaption(factsOf({ state: "verified", stateToken: "verified" }))).toBe(
      "Notlarim · Windows EXE · doğrulandı",
    );
    expect(
      nativeCaption(factsOf({ appToken: "Sayac", targetToken: "android_apk", target: "android_apk", state: "unavailable", stateToken: "unavailable" })),
    ).toBe("Sayac · Android APK · bu makinede yapılamıyor");
  });

  it("drops the parts nobody published rather than filling them in", () => {
    expect(nativeCaption(factsOf({ appToken: null }))).toBe("Windows EXE · derleniyor");
    expect(nativeCaption(factsOf({ targetToken: null, target: null }))).toBe("Notlarim · derleniyor");
    expect(nativeCaption(factsOf({ appToken: null, targetToken: null, target: null }))).toBe("derleniyor");
  });

  it("prints an unreadable target verbatim and never translates it into one of ours", () => {
    const caption = nativeCaption(factsOf({ targetToken: "ios_project", target: null }));
    expect(caption).toContain("ios_project");
    expect(caption).not.toContain("Windows");
    expect(nativeTargetWord("ios_project")).toBe("ios_project");
    expect(nativeTargetWord("windows_exe")).toBe("Windows EXE");
    expect(nativeTargetWord(null)).toBeNull();
    expect(nativeStackWord("dotnet_maui")).toBe("dotnet_maui");
    expect(nativeStackWord("dotnet_wpf")).toBe("WPF");
  });

  it("carries the reader's own word beside a mismatch, and says nothing when it did not speak", () => {
    expect(nativeStatePhrase({ state: "mismatch", verdictToken: "sürüm tutmadı" })).toBe(
      "uyuşmazlık — sürüm tutmadı",
    );
    expect(nativeStatePhrase({ state: "mismatch", verdictToken: null })).toBe("uyuşmazlık");
    // The verdict is not smuggled onto the other steps: a `verified` says
    // "doğrulandı" and stops, and the artefact's own facts are the ROW's.
    expect(nativeStatePhrase({ state: "verified", verdictToken: "ok" })).toBe("doğrulandı");
  });

  it("says the step in one word, or says that none came", () => {
    expect(nativeStateWord("building")).toBe("derleniyor");
    expect(nativeStateWord("signing")).toBe("signing");
    expect(nativeStateWord(null)).toBe("durum bildirilmedi");
    expect(nativeStateLine({ state: null, stateToken: "signing", verdictToken: null })).toBe("signing");
  });

  it("lays the published facts on one line, each absence said", () => {
    expect(nativeFactsLine(factsOf())).toBe(
      "uygulama: Notlarim · hedef: Windows EXE · durum: derleniyor · yığın: WPF",
    );
    expect(nativeFactsLine(factsOf({ appToken: null, targetToken: null, target: null, stackToken: null, stack: null }))).toBe(
      "uygulama bildirilmedi · hedef bildirilmedi · durum: derleniyor",
    );
    const bare = nativeFacts(parseEvent(NATIVE_BUILD_BARE()));
    expect(nativeFactsLine(bare)).toBe(
      "uygulama bildirilmedi · hedef bildirilmedi · durum bildirilmedi",
    );
  });

  it("never puts an artefact's name, size or hash on the bus line", () => {
    // The bus is content-free (ADR-0052 §3). Those three facts are the ROW's,
    // read from `/v1/native/builds`; a caption carrying a file name would be
    // this channel smuggling content past that rule.
    const line = nativeFactsLine(factsOf({ state: "verified", stateToken: "verified", verdictToken: "ok" }));
    expect(line).not.toContain(".exe");
    expect(line).not.toMatch(/\bMB\b|\bKB\b/);
    expect(line).not.toMatch(/[0-9a-f]{12}/);
  });
});

// ---------------------------------------------------------------- the view

describe("the view separates 'now' from 'the last thing we heard'", () => {
  it("is active while the claim holds and last-known once it has aged out", () => {
    const truth = truthOf([BUILDING()]);
    const live = nativeView(nativeClaim(truth, T0 + 5_000));
    expect(live.stage).toBe("active");
    expect(live.lastKnown).toBe("active");
    expect(live.posture).toBe("making");
    expect(live.caption).toBe("Notlarim · Windows EXE · derleniyor");
    expect(nativeIsActive(live)).toBe(true);

    const stale = nativeView(nativeClaim(truth, T0 + NATIVE_TTL_MS + 1_000));
    expect(stale.stage).toBe("none");
    // Still last-known: we heard about a build, and then stopped hearing.
    // That is a different sentence from "nothing was ever reported".
    expect(stale.lastKnown).toBe("active");
    expect(stale.expired).toBe(true);
    expect(nativeIsActive(stale)).toBe(false);
  });

  it("reports nothing at all when nothing was ever published", () => {
    const empty = nativeView(nativeClaim(truthOf([AGENT_IDLE()]), T0));
    expect(empty.stage).toBe("none");
    expect(empty.lastKnown).toBeNull();
    expect(empty.caption).toBe(NATIVE_CAPTION_BARE);
    expect(NATIVE_LABEL.none).not.toBe(NATIVE_LABEL.active);
    // The three panel sentences are three different claims and must not be
    // the same string: "no builds exist", "nothing was reported", "the reader
    // said nothing".
    expect(new Set([NATIVE_EMPTY, NATIVE_UNTOLD, NATIVE_VERDICT_UNTOLD]).size).toBe(3);
  });
});
