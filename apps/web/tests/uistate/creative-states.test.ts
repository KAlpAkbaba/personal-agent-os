/**
 * Contract v12: `creative.activity` (M27 spec §3, §6).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to a picture made in a real application: **the Core names an
 * application, an operation, a step and a comparison because the Cloud Core
 * published them — in the words the spec gives it — never a step it inferred,
 * never "doğrulandı" before the publisher said `verified`, never a similarity
 * nobody measured, and never a control over an application this machine does
 * not have.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v11 (or
 * v10, …, v2) is read normally, because v12 only added.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  CREATIVE_ACTIVITY as CREATIVE_ACTIVITY_TOKEN,
  CREATIVE_CAPTION_BARE,
  CREATIVE_DEFECTS,
  CREATIVE_DEFECT_LABEL,
  CREATIVE_NOT_INSTALLED,
  CREATIVE_NOT_INSTALLED_TOOLS,
  CREATIVE_OPERATIONS,
  CREATIVE_OPERATION_LABEL,
  CREATIVE_RUN_STATES,
  CREATIVE_STATES,
  CREATIVE_STATE_LABEL,
  CREATIVE_TOOLS,
  CREATIVE_TOOL_LABEL,
  CREATIVE_TOOL_LOCATIVE,
  CREATIVE_TTL_MS,
  EXECUTIVE_RUN_STATES,
  EXECUTIVE_STATES,
  EXECUTIVE_TTL_MS,
  KNOWN_CONTRACT_VERSION,
  MAX_CREATIVE_ROUNDS,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STEP_TTL_MS,
  SCENE_RUN_STATES,
  SCENE_STATES,
  SCENE_TTL_MS,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  asSimilarity,
  contractCompatibility,
  isAppState,
  isArtifactState,
  isCoreChannel,
  isCreativeDefect,
  isCreativeOperation,
  isCreativeRunState,
  isCreativeState,
  isCreativeTool,
  isExecutiveState,
  isGenesisState,
  isKnownState,
  isSceneState,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  CREATIVE_WORKING_STATES,
  type CreativeFacts,
  creativeCaption,
  creativeDefectWord,
  creativeFacts,
  creativeIsActive,
  creativeIsUnavailable,
  creativeIsVerified,
  creativeOperationWord,
  creativePosture,
  creativeRunIsWorking,
  creativeSaysNotInstalled,
  creativeSimilarityPhrase,
  creativeStatePhrase,
  creativeToolWord,
  creativeView,
} from "../../app/lib/uistate/creative";
import {
  CREATIVE_DEFECT_UNTOLD,
  CREATIVE_EMPTY,
  CREATIVE_LABEL,
  CREATIVE_SIMILARITY_UNTOLD,
  CREATIVE_UNTOLD,
  KIND_DETAIL,
  KIND_LABEL,
  STATE_LABEL,
  contractLagNote,
  creativeFactsLine,
  creativeStateLine,
  creativeStateWord,
  stateLabel,
  subsystemLabel,
} from "../../app/lib/uistate/labels";
import {
  appClaim,
  applyError,
  applyResponse,
  artifactClaim,
  coreClaim,
  creativeClaim,
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
  isAppBuilding,
  isArtifactMaking,
  isCreativeUnavailable,
  isCreativeVerified,
  isCreativeWorking,
  isDocumentReading,
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
  CREATIVE_ACTIVITY,
  CREATIVE_ACTIVITY_BARE,
  DOCUMENT_ANALYSIS,
  EXECUTIVE_RUN,
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
  toolLabel: "görsel",
  lastError: null,
};

const ANALYSING = () => CREATIVE_ACTIVITY("paint", "inspect", "analysing");
const PLANNING = () => CREATIVE_ACTIVITY("paint", "draw", "planning");
const EXECUTING = () => CREATIVE_ACTIVITY("paint", "draw", "executing");
const INSPECTING = () => CREATIVE_ACTIVITY("paint", "draw", "inspecting");
const EXPORTING = () => CREATIVE_ACTIVITY("paint", "export", "exporting");
const COMPARING = () => CREATIVE_ACTIVITY("paint", "draw", "comparing");
const CORRECTING = () => CREATIVE_ACTIVITY("paint", "draw", "correcting");
const VERIFIED = () => CREATIVE_ACTIVITY("paint", "draw", "verified", 0.92);
const MISMATCH = () => CREATIVE_ACTIVITY("paint", "draw", "mismatch", 0.41, "wrong_size");
const UNAVAILABLE = () => CREATIVE_ACTIVITY("photoshop", "open", "unavailable");
const FAILED = () => CREATIVE_ACTIVITY("paint", "draw", "failed");

/**
 * True for a line that CLAIMS the comparison's aggregate is SSIM.
 *
 * The whole word, never a substring: `asSimilarity` contains the four letters
 * and is not the claim. A guard that matched it would fire on every honest
 * file and be switched off, which is how a guard dies.
 */
const namesSsim = (line: string) => /\bssim\b/i.test(line) && !/NOT SSIM/.test(line);

/** Facts as the publisher would have produced them, for the pure caption checks. */
function factsOf(overrides: Partial<CreativeFacts> = {}): CreativeFacts {
  return {
    toolToken: "paint",
    tool: "paint",
    operationToken: "draw",
    operation: "draw",
    stateToken: "executing",
    state: "executing",
    similarity: null,
    defectToken: null,
    defect: null,
    ...overrides,
  };
}

// ------------------------------------------------------------ the contract

describe("contract v12 is v11 plus the creative state, and says so", () => {
  it("still reads a v12, v11, v10 … v2 server from a build at v12 or later", () => {
    expect(KNOWN_CONTRACT_VERSION).toBeGreaterThanOrEqual(12);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION)).toBe("current");
    const built: number = KNOWN_CONTRACT_VERSION;
    expect(contractCompatibility(12)).toBe(built === 12 ? "current" : "older_supported");
    for (const older of [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]) {
      expect(contractCompatibility(older), `v${older}`).toBe("older_supported");
    }
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION + 1)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("names the one token once, and knows it by membership rather than by prefix", () => {
    expect(CREATIVE_ACTIVITY_TOKEN).toBe("creative.activity");
    expect(CREATIVE_STATES).toEqual(["creative.activity"]);
    expect(isKnownState("creative.activity")).toBe(true);
    expect(isCreativeState("creative.activity")).toBe(true);
    // A newer server's word is not a state this build may draw as a picture.
    expect(isCreativeState("creative.deleted")).toBe(false);
    expect(isCreativeState("creative.exported")).toBe(false);
    // And it belongs to no other family — including M25's, whose name it
    // shares three quarters of. `creative.activity` and `scene.activity` are
    // two milestones, two subsystems and two vocabularies.
    expect(isSceneState("creative.activity")).toBe(false);
    expect(isCreativeState("scene.activity")).toBe(false);
    expect(isExecutiveState("creative.activity")).toBe(false);
    expect(isGenesisState("creative.activity")).toBe(false);
    expect(isAppState("creative.activity")).toBe(false);
    expect(isArtifactState("creative.activity")).toBe(false);
  });

  it("appends the token after v11's, never reordering", () => {
    expect(UI_STATES.indexOf("creative.activity")).toBe(UI_STATES.indexOf("executive.run") + 1);
    expect(UI_STATES.indexOf("executive.run")).toBe(UI_STATES.indexOf("scene.activity") + 1);
    expect(UI_STATES.indexOf("scene.activity")).toBe(UI_STATES.indexOf("capability.genesis") + 1);
    expect(UI_STATES[0]).toBe("agent.idle");
  });

  it("types the four applications the spec names, and admits nothing outside them", () => {
    // §2: `tool ∈ {paint, photoshop, illustrator, figma}`. On this machine
    // (ADR-0093, detection 2026-09-08) only Paint is installed; the other
    // three ship as providers that say so, which is why they are here.
    expect(CREATIVE_TOOLS).toEqual(["paint", "photoshop", "illustrator", "figma"]);
    for (const tool of CREATIVE_TOOLS) expect(isCreativeTool(tool), tool).toBe(true);
    expect(isCreativeTool("Paint")).toBe(false);
    expect(isCreativeTool("gimp")).toBe(false);
    expect(isCreativeTool("blender")).toBe(false);
    expect(isCreativeTool(null)).toBe(false);
    expect(isCreativeTool(3)).toBe(false);
  });

  it("types the twelve plan operations in the spec's order", () => {
    // §2's closed vocabulary, which §4 also names as the `creative.*`
    // capabilities. A thirteenth word is one this build cannot read.
    expect(CREATIVE_OPERATIONS).toEqual([
      "new",
      "open",
      "inspect",
      "draw",
      "add_text",
      "shape",
      "transform",
      "color_adjust",
      "crop",
      "background_remove",
      "layer",
      "export",
    ]);
    for (const op of CREATIVE_OPERATIONS) expect(isCreativeOperation(op), op).toBe(true);
    expect(isCreativeOperation("erase")).toBe(false);
    expect(isCreativeOperation("DRAW")).toBe(false);
    expect(isCreativeOperation(null)).toBe(false);
    // Every one has a word, and no two share it.
    expect(Object.keys(CREATIVE_OPERATION_LABEL).toSorted()).toEqual([...CREATIVE_OPERATIONS].toSorted());
    expect(new Set(Object.values(CREATIVE_OPERATION_LABEL)).size).toBe(CREATIVE_OPERATIONS.length);
  });

  it("types the twelve steps in the spec's order, and admits nothing outside them", () => {
    // The API holds this list to its own by reading this file
    // (services/api/tests/unit/test_uistate_contract_halves.py). M24, M25 and
    // M26 each drifted here while both suites stayed green.
    expect(CREATIVE_RUN_STATES).toEqual([
      "analysing",
      "planning",
      "executing",
      "inspecting",
      "exporting",
      "comparing",
      "correcting",
      "verified",
      "unverified",
      "mismatch",
      "unavailable",
      "failed",
    ]);
    for (const s of CREATIVE_RUN_STATES) expect(isCreativeRunState(s), s).toBe(true);
    expect(isCreativeRunState("verified_partially")).toBe(false);
    expect(isCreativeRunState("VERIFIED")).toBe(false);
    expect(isCreativeRunState("done")).toBe(false);
    expect(isCreativeRunState("exported")).toBe(false);
    expect(isCreativeRunState(true)).toBe(false);
    expect(isCreativeRunState(null)).toBe(false);
  });

  it("types the four objective defects the comparison can name", () => {
    // §3: wrong size, missing region, colour drift beyond tolerance, empty
    // output. Each is its own named defect, because the owner's next question
    // is always WHICH.
    expect(CREATIVE_DEFECTS).toEqual(["wrong_size", "missing_region", "color_drift", "empty_output"]);
    for (const d of CREATIVE_DEFECTS) expect(isCreativeDefect(d), d).toBe(true);
    expect(isCreativeDefect("blurry")).toBe(false);
    expect(isCreativeDefect(null)).toBe(false);
    expect(Object.keys(CREATIVE_DEFECT_LABEL).toSorted()).toEqual([...CREATIVE_DEFECTS].toSorted());
    expect(new Set(Object.values(CREATIVE_DEFECT_LABEL)).size).toBe(CREATIVE_DEFECTS.length);
    for (const word of Object.values(CREATIVE_DEFECT_LABEL)) expect(word).not.toContain("doğrulandı");
  });

  it("reads a similarity only from a bounded fraction, and keeps zero as an answer", () => {
    expect(asSimilarity(0.92)).toBe(0.92);
    expect(asSimilarity(0)).toBe(0);
    expect(asSimilarity(1)).toBe(1);
    // Outside 0..1 it is not a fraction this build will draw.
    expect(asSimilarity(1.01)).toBeNull();
    expect(asSimilarity(-0.01)).toBeNull();
    expect(asSimilarity(92)).toBeNull();
    expect(asSimilarity("0.92")).toBeNull();
    expect(asSimilarity(null)).toBeNull();
    expect(asSimilarity(Number.NaN)).toBeNull();
    expect(asSimilarity(Number.POSITIVE_INFINITY)).toBeNull();
  });

  it("adds the creative subsystem and names it, leaving M25's creative3d alone", () => {
    expect(SUBSYSTEMS).toContain("creative");
    expect(subsystemLabel("creative")).toBe("Yaratıcı");
    // The 3D family's subsystem is a different word for a different thing.
    expect(SUBSYSTEMS).toContain("creative3d");
    expect(subsystemLabel("creative3d")).toBe("3B sahne");
    expect(subsystemLabel("creative")).not.toBe(subsystemLabel("creative3d"));
  });

  it("keeps the token on the agent channel, which drives the core body", () => {
    expect(stateChannel("creative.activity")).toBe("agent");
    expect(isCoreChannel("creative.activity")).toBe(true);
    // The room, the lab and the operator are untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("evolution.building")).toBe("lab");
    expect(stateChannel("operator.running")).toBe("operator");
    expect(stateChannel("executive.run")).toBe("agent");
  });

  it("classifies it as transient, on a horizon sized for what a creative step really does", () => {
    expect(stateKind("creative.activity")).toBe("transient");
    expect(CREATIVE_TTL_MS).toBe(90_000);
    // Longer than the device round trip a step may include (M19's operator
    // opens the application), shorter than M25's whole-editor batch run.
    expect(CREATIVE_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(CREATIVE_TTL_MS).toBeGreaterThan(OPERATOR_STEP_TTL_MS);
    expect(CREATIVE_TTL_MS).toBeLessThan(SCENE_TTL_MS);
    expect(stateTtlMs("creative.activity")).toBe(CREATIVE_TTL_MS);
    // The publisher's own ttl_s still beats every figure here.
    expect(stateTtlMs("creative.activity", CREATIVE_ACTIVITY("paint", "draw", "executing", null, null, { ttl_s: 600 }))).toBe(
      600_000,
    );
  });

  it("gives the state the Turkish the spec asks for, spelled once", () => {
    expect(CREATIVE_CAPTION_BARE).toBe("Görsel çalışması");
    expect(STATE_LABEL["creative.activity"]).toBe(CREATIVE_CAPTION_BARE);
    expect(KIND_LABEL.creative_activity).toBe(CREATIVE_CAPTION_BARE);
    expect(CREATIVE_LABEL.active).toBe(CREATIVE_CAPTION_BARE);
    expect(CREATIVE_LABEL.none).not.toBe("Boşta");
    expect(stateLabel("creative.activity")).not.toBe("creative.activity");
    expect(CREATIVE_EMPTY).toBe("Henüz bir görsel çalışması yapılmadı.");
    expect(CREATIVE_UNTOLD).not.toBe(CREATIVE_EMPTY);
    // The twelve steps, each a constant spelled once.
    expect(CREATIVE_STATE_LABEL).toEqual({
      analysing: "görsel inceleniyor",
      planning: "plan hazırlanıyor",
      executing: "düzenleme uygulanıyor",
      inspecting: "çıktı okunuyor",
      exporting: "dışa aktarılıyor",
      comparing: "karşılaştırılıyor",
      correcting: "düzeltiliyor",
      verified: "doğrulandı",
      unverified: "karşılaştırılacak bir şey yoktu",
      mismatch: "uyuşmazlık",
      unavailable: "yapılamadı",
      failed: "başarısız",
    });
    // "Doğrulandı" is a word for exactly one step, and every word is distinct.
    expect(Object.values(CREATIVE_STATE_LABEL).filter((w) => w === "doğrulandı")).toHaveLength(1);
    expect(new Set(Object.values(CREATIVE_STATE_LABEL)).size).toBe(CREATIVE_RUN_STATES.length);
    // The four applications, in both the forms the spec's own utterances use.
    expect(CREATIVE_TOOL_LABEL).toEqual({
      paint: "Paint",
      photoshop: "Photoshop",
      illustrator: "Illustrator",
      figma: "Figma",
    });
    expect(CREATIVE_TOOL_LOCATIVE).toEqual({
      paint: "Paint'te",
      photoshop: "Photoshop'ta",
      illustrator: "Illustrator'da",
      figma: "Figma'da",
    });
    expect(CREATIVE_NOT_INSTALLED).toBe("kurulu değil");
    expect(MAX_CREATIVE_ROUNDS).toBe(3);
    // The detail says which side of "done" the posture is on, that an
    // uninstalled application is said rather than imitated, and that the
    // owner's own file is not touched.
    expect(KIND_DETAIL.creative_activity).toContain("doğrulanmış sayılmaz");
    expect(KIND_DETAIL.creative_activity).toContain("kurulu değil");
    expect(KIND_DETAIL.creative_activity).toContain("özgün dosyası değiştirilmez");
    expect(KIND_DETAIL.creative_activity).toContain("İlerleme bildirilmez");
  });

  it("names exactly the families an older server will never publish", () => {
    const v11 = contractLagNote(11, 12);
    expect(v11).toContain("v11");
    expect(v11).toContain("v12");
    expect(v11).toContain("Görsel çalışması durumu");
    expect(v11).not.toContain("3B sahne");
    expect(v11).toContain("yok demek değil");

    // The note sentence-cases only its FIRST family, so a v10 server sees
    // "Çok adımlı iş durumu, görsel çalışması durumu".
    const v10 = contractLagNote(10, 12);
    expect(v10).toContain("Çok adımlı iş durumu");
    expect(v10).toContain("görsel çalışması durumu");

    const v2 = contractLagNote(2, 12);
    for (const family of ["3B sahne durumu", "çok adımlı iş durumu", "görsel çalışması durumu"]) {
      expect(v2, family).toContain(family);
    }
    // The v11 wording M26 shipped is unchanged for a v10 server seen from v11.
    expect(contractLagNote(10, 11)).toBe(
      "Sunucu durum sözleşmesi v10; bu arayüz v11. Çok adımlı iş durumu bu sunucudan henüz yayınlanmıyor — yok demek değil.",
    );
  });

  it("leaves every v11 token, kind, horizon and word exactly as it was", () => {
    expect(EXECUTIVE_STATES).toEqual(["executive.run"]);
    expect(EXECUTIVE_RUN_STATES).toEqual([
      "planned",
      "running",
      "paused",
      "completed",
      "partial",
      "cancelled",
      "failed",
    ]);
    expect(SCENE_STATES).toEqual(["scene.activity"]);
    expect(SCENE_RUN_STATES).toHaveLength(9);
    expect(stateKind("executive.run")).toBe("operation");
    expect(stateTtlMs("executive.run")).toBe(EXECUTIVE_TTL_MS);
    expect(stateTtlMs("scene.activity")).toBe(SCENE_TTL_MS);
    expect(stateKind("agent.idle")).toBe("steady");
  });

  /**
   * The other half of the contract has to be able to READ this list.
   *
   * `services/api/tests/unit/test_uistate_contract_halves.py` holds every
   * family's vocabulary to the publisher's in both directions, and it does so
   * by parsing THIS FILE with a deliberately dumb regex:
   *
   *     export const NAME = [ ... ] as const;      (DOTALL)
   *     "([a-z_.]+)"                               (the words inside)
   *
   * That guard exists because M24, M25 and M26 each shipped a vocabulary
   * drift with both suites green. It has one failure mode of its own: if this
   * list ever stops being a literal array of quoted lowercase words — built
   * from a spread, a `map`, a constant, or split across a second declaration —
   * the regex finds nothing, the Python side asserts on an empty set, and the
   * guard goes quiet without failing. So this test runs the SAME regex from
   * this side and demands the same twelve words back. A refactor that blinds
   * the other half fails here first.
   */
  it("keeps every family's list in the literal shape the API's cross-half guard parses", () => {
    const text = readFileSync(join(__dirname, "..", "..", "app", "lib", "uistate", "contract.ts"), "utf-8");
    const readList = (name: string): string[] => {
      const match = new RegExp(`export const ${name} = \\[([\\s\\S]*?)\\] as const;`).exec(text);
      expect(match, `${name} is not a literal array the guard can parse`).not.toBeNull();
      return [...(match?.[1] ?? "").matchAll(/"([a-z_.]+)"/g)].map((m) => m[1]);
    };
    expect(readList("CREATIVE_RUN_STATES")).toEqual([...CREATIVE_RUN_STATES]);
    // And the families that already had this guard keep it: this file added a
    // vocabulary to a file four other lists live in, so it proves it broke none.
    expect(readList("SCENE_RUN_STATES")).toEqual([...SCENE_RUN_STATES]);
    expect(readList("EXECUTIVE_RUN_STATES")).toEqual([...EXECUTIVE_RUN_STATES]);
    expect(readList("GENESIS_RUN_STATES").length).toBeGreaterThan(0);
    expect(readList("APP_PROJECT_STATES").length).toBeGreaterThan(0);
    // The detector is not vacuous: a name that is not there finds nothing.
    expect(new RegExp("export const CREATIVE_NO_SUCH_LIST = \\[([\\s\\S]*?)\\] as const;").exec(text)).toBeNull();
  });

  /**
   * ADR-0093 decision 4, kept as a fact about the SOURCE rather than about a
   * value: what `app/creative/compare.py` measures is a per-tile mean colour
   * distance aggregated over a fixed grid. It is not SSIM. The spec's own
   * draft called it that, the ADR struck the word, and the one way this build
   * can promise the word never comes back is to read its own files and say so.
   */
  it("never calls the comparison's aggregate SSIM, anywhere in this family's source", () => {
    // The detector itself, proven both ways, so this test cannot silently
    // compare nothing (the M24 `_REPO_ROOT` lesson, ADR-0087 addendum 1).
    expect(namesSsim("const ssim = compare(a, b);")).toBe(true);
    expect(namesSsim(" * measured as SSIM over the tiles")).toBe(true);
    expect(namesSsim("  asSimilarity,")).toBe(false);
    expect(namesSsim(" * bounded aggregate. NOT SSIM (ADR-0093 s4).")).toBe(false);

    const root = join(__dirname, "..", "..", "app", "lib");
    const files = [
      join(root, "uistate", "creative.ts"),
      join(root, "uistate", "contract.ts"),
      join(root, "uistate", "labels.ts"),
      join(root, "cockpit", "creative.ts"),
      join(root, "cockpit", "creative-rows.ts"),
      join(root, "cockpit", "useCreativeControl.ts"),
      join(root, "cockpit", "useCreativeImages.ts"),
    ];
    for (const file of files) {
      const text = readFileSync(file, "utf-8");
      expect(text.length, file).toBeGreaterThan(0);
      expect(text.split("\n").filter(namesSsim), file).toEqual([]);
    }
  });
});

// ---------------------------------------------------------------- the facts

describe("the published facts, and nothing else", () => {
  it("reads the application, the operation, the step, the similarity and the defect exactly as sent", () => {
    expect(creativeFacts(MISMATCH())).toEqual({
      toolToken: "paint",
      tool: "paint",
      operationToken: "draw",
      operation: "draw",
      stateToken: "mismatch",
      state: "mismatch",
      similarity: 0.41,
      defectToken: "wrong_size",
      defect: "wrong_size",
    });
  });

  it("says nothing for an event that carried nothing, and nothing for no event at all", () => {
    const bare: CreativeFacts = {
      toolToken: null,
      tool: null,
      operationToken: null,
      operation: null,
      stateToken: null,
      state: null,
      similarity: null,
      defectToken: null,
      defect: null,
    };
    expect(creativeFacts(CREATIVE_ACTIVITY_BARE())).toEqual(bare);
    expect(creativeFacts(null)).toEqual(bare);
  });

  it("keeps a tool, an operation, a step or a defect it cannot read as the token, and never promotes it", () => {
    const facts = creativeFacts(CREATIVE_ACTIVITY("gimp", "erase", "exported", null, "blurry"));
    expect(facts.toolToken).toBe("gimp");
    expect(facts.tool).toBeNull();
    expect(facts.operationToken).toBe("erase");
    expect(facts.operation).toBeNull();
    expect(facts.stateToken).toBe("exported");
    expect(facts.state).toBeNull();
    expect(facts.defectToken).toBe("blurry");
    expect(facts.defect).toBeNull();
    expect(creativeToolWord("gimp")).toBe("gimp");
    expect(creativeOperationWord("erase")).toBe("erase");
    expect(creativeStateWord("exported")).toBe("exported");
    expect(creativeDefectWord("blurry")).toBe("blurry");
  });

  it("measures a similarity only when the comparison did, and keeps zero", () => {
    expect(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "verified", 0)).similarity).toBe(0);
    expect(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "verified")).similarity).toBeNull();
    // A figure the boundary cannot read is no figure: `metadata.similarity`
    // is a string here, which `parseEvent` keeps as a token and this refuses.
    expect(
      creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "verified", null, null, { similarity: "0.92" })).similarity,
    ).toBeNull();
    // And a percentage sent as a percentage is not a fraction.
    expect(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "verified", null, null, { similarity: 92 })).similarity).toBeNull();
    expect(creativeSimilarityPhrase(0.92)).toBe("(benzerlik %92)");
    expect(creativeSimilarityPhrase(0)).toBe("(benzerlik %0)");
    expect(creativeSimilarityPhrase(1)).toBe("(benzerlik %100)");
    expect(creativeSimilarityPhrase(null)).toBeNull();
  });

  it("parses a real event through the boundary without inventing a key", () => {
    const parsed = parseEvent(UNAVAILABLE());
    expect(parsed).not.toBeNull();
    expect(parsed?.subsystem).toBe("creative");
    expect(parsed?.metadata).toEqual({ tool: "photoshop", operation: "open", state: "unavailable" });
    expect(parsed?.metadata.similarity).toBeUndefined();
    expect(parsed?.metadata.defect).toBeUndefined();
  });
});

// -------------------------------------------------------------- the posture

describe("the posture, from the published step alone", () => {
  it("gives each step its posture, and an unreadable one the making posture", () => {
    expect(creativePosture("analysing")).toBe("reading");
    expect(creativePosture("inspecting")).toBe("reading");
    expect(creativePosture("planning")).toBe("planning");
    expect(creativePosture("executing")).toBe("making");
    expect(creativePosture("exporting")).toBe("exporting");
    expect(creativePosture("comparing")).toBe("comparing");
    expect(creativePosture("correcting")).toBe("correcting");
    expect(creativePosture("verified")).toBe("verified");
    expect(creativePosture("unverified")).toBe("unverified");
    expect(creativePosture("mismatch")).toBe("mismatch");
    expect(creativePosture("unavailable")).toBe("unavailable");
    expect(creativePosture("failed")).toBe("failed");
    expect(creativePosture(null)).toBe("making");
  });

  /**
   * The rule this milestone is about: the verified posture has ONE door, and
   * neither a high similarity, nor an export that exists, nor a word that
   * merely looks like it can open it.
   */
  it("reaches the verified posture from `verified` and from nothing else", () => {
    expect(creativePosture("verified")).toBe("verified");
    for (const state of CREATIVE_RUN_STATES.filter((s) => s !== "verified")) {
      expect(creativePosture(state), state).not.toBe("verified");
      expect(creativeIsVerified({ state }), state).toBe(false);
    }
    expect(creativeIsVerified({ state: "verified" })).toBe(true);
    expect(creativeIsVerified({ state: null })).toBe(false);
    // A perfect similarity on any other step is still that other step.
    for (const state of CREATIVE_RUN_STATES.filter((s) => s !== "verified")) {
      const facts = creativeFacts(CREATIVE_ACTIVITY("paint", "draw", state, 1));
      expect(creativePosture(facts.state), state).not.toBe("verified");
      expect(creativeCaption(facts), state).not.toContain("doğrulandı");
    }
    // And a word this build cannot read never becomes it, however like it looks.
    for (const near of ["verified_partially", "Verified", "verifed", "done"]) {
      expect(creativePosture(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", near, 1)).state), near).toBe("making");
      expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", near, 1))), near).toBe(CREATIVE_CAPTION_BARE);
    }
  });

  it("never rounds a run with nothing to compare into one that was verified", () => {
    // The third outcome: the run did what was asked and there was nothing to
    // compare it against (a bare export). It is over, so it is not working;
    // it is not `verified`, because nothing was verified; and it is not
    // `mismatch`, because nothing disagreed. Its own posture, its own word.
    expect(creativePosture("unverified")).not.toBe("verified");
    expect(creativePosture("unverified")).not.toBe("mismatch");
    expect(creativePosture("unverified")).not.toBe("making");
    expect(creativeRunIsWorking("unverified")).toBe(false);
    expect(creativeIsUnavailable({ state: "unverified" })).toBe(false);
    expect(CREATIVE_STATE_LABEL.unverified).not.toContain("doğrulandı");
    expect(creativeStatePhrase({ state: "unverified", tool: "paint", similarity: 0.99, defectToken: null })).toBe(
      CREATIVE_STATE_LABEL.unverified,
    );
  });

  it("knows which steps mean the run is actually working", () => {
    expect(CREATIVE_WORKING_STATES).toEqual([
      "analysing",
      "planning",
      "executing",
      "inspecting",
      "exporting",
      "comparing",
      "correcting",
    ]);
    for (const state of CREATIVE_RUN_STATES) {
      expect(creativeRunIsWorking(state), state).toBe(CREATIVE_WORKING_STATES.includes(state));
    }
    expect(creativeRunIsWorking(null)).toBe(false);
  });

  it("marks the one step that means the application could not be driven at all", () => {
    expect(creativeIsUnavailable({ state: "unavailable" })).toBe(true);
    for (const state of CREATIVE_RUN_STATES.filter((s) => s !== "unavailable")) {
      expect(creativeIsUnavailable({ state }), state).toBe(false);
    }
    expect(creativeIsUnavailable({ state: null })).toBe(false);
  });
});

// ------------------------------------------------------------- the captions

describe("the caption says the step and everything published with it, and no more", () => {
  it("says the executing step in the locative — the application is the place, not the subject", () => {
    expect(creativeCaption(creativeFacts(EXECUTING()))).toBe("Paint'te çizim uygulanıyor");
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("photoshop", "crop", "executing")))).toBe(
      "Photoshop'ta kırpma uygulanıyor",
    );
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("illustrator", "shape", "executing")))).toBe(
      "Illustrator'da şekil uygulanıyor",
    );
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("figma", "layer", "executing")))).toBe(
      "Figma'da katman uygulanıyor",
    );
    // No operation named: the place plus the plain step.
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("paint", null, "executing")))).toBe(
      "Paint'te düzenleme uygulanıyor",
    );
    // No application: no place to name, only what was said.
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY(null, "draw", "executing")))).toBe("çizim uygulanıyor");
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY(null, null, "executing")))).toBe("düzenleme uygulanıyor");
  });

  it("says every other step as application · operation · step, each part only if published", () => {
    expect(creativeCaption(creativeFacts(ANALYSING()))).toBe("Paint · inceleme · görsel inceleniyor");
    expect(creativeCaption(creativeFacts(PLANNING()))).toBe("Paint · çizim · plan hazırlanıyor");
    expect(creativeCaption(creativeFacts(INSPECTING()))).toBe("Paint · çizim · çıktı okunuyor");
    expect(creativeCaption(creativeFacts(EXPORTING()))).toBe("Paint · dışa aktarma · dışa aktarılıyor");
    expect(creativeCaption(creativeFacts(COMPARING()))).toBe("Paint · çizim · karşılaştırılıyor");
    expect(creativeCaption(creativeFacts(CORRECTING()))).toBe("Paint · çizim · düzeltiliyor");
    expect(creativeCaption(creativeFacts(FAILED()))).toBe("Paint · çizim · başarısız");
    // Missing metadata is missing: the plain step name and no more.
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY(null, null, "comparing")))).toBe("karşılaştırılıyor");
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("paint", null, "comparing")))).toBe("Paint · karşılaştırılıyor");
    // Words this build cannot read are still published facts, printed verbatim.
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("gimp", "erase", "comparing")))).toBe(
      "gimp · erase · karşılaştırılıyor",
    );
  });

  it("says 'doğrulandı' only for verified, and carries the figure the comparison measured", () => {
    expect(creativeCaption(creativeFacts(VERIFIED()))).toBe("Paint · çizim · doğrulandı (benzerlik %92)");
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "verified", 0)))).toBe(
      "Paint · çizim · doğrulandı (benzerlik %0)",
    );
    // Nobody measured: "doğrulandı" alone, never "(benzerlik %0)".
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "verified")))).toBe("Paint · çizim · doğrulandı");
  });

  it("names the defect a mismatch was about wherever the defect is known", () => {
    expect(creativeCaption(creativeFacts(MISMATCH()))).toBe("Paint · çizim · uyuşmazlık — ölçü tutmadı");
    // The bus carries the defect; a row may pass a better one, and both spell
    // the sentence from the same place.
    expect(creativeStatePhrase({ state: "mismatch", tool: "paint", similarity: null, defectToken: null })).toBe("uyuşmazlık");
    expect(
      creativeStatePhrase({ state: "mismatch", tool: "paint", similarity: null, defectToken: null }, "missing_region"),
    ).toBe("uyuşmazlık — istenen bölge yok");
    // A defect beside a step that is not a mismatch is not a mismatch.
    expect(creativeStatePhrase({ state: "verified", tool: "paint", similarity: 0.92, defectToken: "wrong_size" })).toBe(
      "doğrulandı (benzerlik %92)",
    );
    // A defect word this build cannot read is printed as it came.
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "mismatch", 0.4, "blurry")))).toBe(
      "Paint · çizim · uyuşmazlık — blurry",
    );
  });

  it("says an uninstalled Photoshop or Illustrator so, and every other unavailable plainly", () => {
    expect(creativeCaption(creativeFacts(UNAVAILABLE()))).toBe("Photoshop · açma · kurulu değil — yapılamadı");
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("illustrator", null, "unavailable")))).toBe(
      "Illustrator · kurulu değil — yapılamadı",
    );
    // Paint IS installed on this machine and on the runner (ADR-0093's
    // detection), so its `unavailable` gets no reason invented for it; and
    // Figma has no desktop application to install at all.
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "unavailable")))).toBe(
      "Paint · çizim · yapılamadı",
    );
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("figma", null, "unavailable")))).toBe("Figma · yapılamadı");
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY(null, null, "unavailable")))).toBe("yapılamadı");
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("gimp", null, "unavailable")))).toBe("gimp · yapılamadı");
    expect(CREATIVE_NOT_INSTALLED_TOOLS).toEqual(["photoshop", "illustrator"]);
    expect(creativeSaysNotInstalled("photoshop")).toBe(true);
    expect(creativeSaysNotInstalled("illustrator")).toBe(true);
    expect(creativeSaysNotInstalled("paint")).toBe(false);
    expect(creativeSaysNotInstalled("figma")).toBe(false);
    expect(creativeSaysNotInstalled(null)).toBe(false);
    // It is never worded as a failure either.
    expect(creativeCaption(creativeFacts(UNAVAILABLE()))).not.toContain("başarısız");
  });

  it("falls back to the bare token for a step it cannot read, and for none at all", () => {
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "exported")))).toBe(CREATIVE_CAPTION_BARE);
    expect(creativeCaption(creativeFacts(CREATIVE_ACTIVITY_BARE()))).toBe(CREATIVE_CAPTION_BARE);
    expect(creativeCaption(creativeFacts(null))).toBe(CREATIVE_CAPTION_BARE);
    expect(creativeStatePhrase(factsOf({ state: null, stateToken: null }))).toBeNull();
  });

  it("lines the facts with what was sent and the statement of what was not", () => {
    expect(creativeFactsLine(creativeFacts(EXECUTING()))).toBe("uygulama: Paint · işlem: çizim · durum: düzenleme uygulanıyor");
    expect(creativeFactsLine(creativeFacts(VERIFIED()))).toBe(
      "uygulama: Paint · işlem: çizim · durum: doğrulandı · (benzerlik %92)",
    );
    // The absence of a figure is said only where a comparison was the point.
    expect(creativeFactsLine(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "verified")))).toBe(
      `uygulama: Paint · işlem: çizim · durum: doğrulandı · ${CREATIVE_SIMILARITY_UNTOLD}`,
    );
    expect(creativeFactsLine(creativeFacts(EXECUTING()))).not.toContain(CREATIVE_SIMILARITY_UNTOLD);
    expect(creativeFactsLine(creativeFacts(CREATIVE_ACTIVITY_BARE()))).toBe(
      "uygulama bildirilmedi · işlem bildirilmedi · durum bildirilmedi",
    );
    expect(creativeFactsLine(creativeFacts(CREATIVE_ACTIVITY("gimp", null, "exported")))).toBe(
      "uygulama: gimp · işlem bildirilmedi · durum: exported",
    );
    // The row/caption helper falls back to the word, never to nothing.
    expect(creativeStateLine(creativeFacts(VERIFIED()))).toBe("doğrulandı (benzerlik %92)");
    expect(creativeStateLine(creativeFacts(CREATIVE_ACTIVITY_BARE()))).toBe("durum bildirilmedi");
    expect(creativeStateLine(creativeFacts(CREATIVE_ACTIVITY("paint", "draw", "exported")))).toBe("exported");
    expect(creativeStateWord(null)).toBe("durum bildirilmedi");
    expect(CREATIVE_DEFECT_UNTOLD).not.toBe(CREATIVE_SIMILARITY_UNTOLD);
  });
});

// ----------------------------------------------------------------- the view

describe("the view over one claim", () => {
  it("is active while the claim holds, with the facts, the posture and the caption", () => {
    const view = creativeView(creativeClaim(truthOf([VERIFIED()]), T0 + 2_000));
    expect(view.stage).toBe("active");
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(false);
    expect(view.posture).toBe("verified");
    expect(view.caption).toBe("Paint · çizim · doğrulandı (benzerlik %92)");
    expect(view.tool).toBe("paint");
    expect(view.operation).toBe("draw");
    expect(view.similarity).toBe(0.92);
    expect(view.ageMs).toBe(2_000);
    expect(creativeIsActive(view)).toBe(true);
  });

  it("stops claiming the step once it ages out, without saying the work stopped", () => {
    const view = creativeView(creativeClaim(truthOf([EXPORTING()]), T0 + CREATIVE_TTL_MS + 1_000));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(true);
    expect(view.caption).toBe("Paint · dışa aktarma · dışa aktarılıyor");
    expect(creativeIsActive(view)).toBe(false);
  });

  it("reports nothing at all when nothing was ever published about a creative run", () => {
    const view = creativeView(creativeClaim(truthOf([AGENT_IDLE()]), T0));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBeNull();
    expect(view.caption).toBe(CREATIVE_CAPTION_BARE);
    expect(view.posture).toBe("making");
    expect(view.ageMs).toBeNull();
  });

  it("reads the newest creative event by membership, and never another family's", () => {
    const truth = truthOf([EXPORTING(), VERIFIED()]);
    expect(creativeView(creativeClaim(truth, T0)).state).toBe("verified");
    for (const other of [
      [DOCUMENT_ANALYSIS()],
      [MAIL_ACTIVITY()],
      [ARTIFACT_FACTORY()],
      [APP_FACTORY()],
      [CAPABILITY_GENESIS()],
      [SCENE_ACTIVITY()],
      [EXECUTIVE_RUN()],
    ]) {
      expect(creativeClaim(truthOf(other), T0).event).toBeNull();
    }
    // And the other families' claims never pick a creative event up — the 3D
    // family's least of all, whose token this one's most resembles.
    const creative = truthOf([VERIFIED()]);
    for (const claim of [documentClaim, mailClaim, artifactClaim, appClaim, genesisClaim, sceneClaim, executiveClaim]) {
      expect(claim(creative, T0).event).toBeNull();
    }
  });
});

// ------------------------------------------------------------- the geometry

describe("the Core's body for a creative run", () => {
  it("gives the reading, planning, making, exporting and comparing steps distinct postures", () => {
    const reading = intentOf([ANALYSING()]);
    const planning = intentOf([PLANNING()]);
    const making = intentOf([EXECUTING()]);
    const exporting = intentOf([EXPORTING()]);
    const comparing = intentOf([COMPARING()]);
    for (const intent of [reading, planning, making, exporting, comparing]) {
      expect(intent.kind).toBe("creative_activity");
    }
    // Reading draws inward: an image is being read.
    expect(reading.inwardFlow).toBeGreaterThan(0);
    expect(reading.palette).toBe("reading");
    expect(intentOf([INSPECTING()]).palette).toBe("reading");
    // Planning is still, and nothing flows: a plan is data.
    expect(planning.palette).toBe("planning");
    expect(planning.flowRate).toBe(0);
    expect(planning.inwardFlow).toBe(0);
    // Making pushes the plan out and draws nothing in.
    expect(making.inwardFlow).toBe(0);
    expect(making.flowRate).toBeGreaterThan(0);
    expect(making.palette).toBe("making");
    // Exporting is its own posture: one steady stream out, the structure still.
    expect(exporting.flowRate).toBeGreaterThan(making.flowRate);
    expect(exporting.ringSpin).toBeLessThan(making.ringSpin);
    expect(exporting.inwardFlow).toBe(0);
    // Comparing draws inward too, and brighter than the plain read: this is
    // where "doğrulandı" comes from.
    expect(comparing.inwardFlow).toBeGreaterThan(0);
    expect(comparing.glow).toBeGreaterThan(planning.glow);
  });

  it("settles on verified, holds a mismatch, a correction and a failure under restraint, and never agitates", () => {
    const verified = intentOf([VERIFIED()]);
    expect(verified.palette).toBe("ready");
    expect(verified.flowRate).toBe(0);
    expect(verified.restraint).toBe(0);
    expect(verified.agitation).toBe(0);
    expect(isCreativeVerified(verified)).toBe(true);

    const mismatch = intentOf([MISMATCH()]);
    expect(mismatch.restraint).toBeGreaterThan(0);
    expect(mismatch.flowRate).toBe(0);
    expect(mismatch.agitation).toBe(0);
    expect(mismatch.glow).toBeLessThan(verified.glow);
    expect(isCreativeVerified(mismatch)).toBe(false);

    // A correction is working AND held: something was already found wrong.
    const correcting = intentOf([CORRECTING()]);
    expect(correcting.restraint).toBeGreaterThan(0);
    expect(correcting.flowRate).toBeGreaterThan(0);
    expect(correcting.agitation).toBe(0);

    const failed = intentOf([FAILED()]);
    expect(failed.restraint).toBeGreaterThan(0);
    expect(failed.agitation).toBe(0);
  });

  it("draws an application that is not installed as settled and honest, never as an error", () => {
    const intent = intentOf([UNAVAILABLE()]);
    expect(intent.kind).toBe("creative_activity");
    expect(isCreativeUnavailable(intent)).toBe(true);
    // An application nobody installed is not a fault: no agitation, no fault
    // palette, no error kind — and nothing flowing, because nothing is running.
    expect(intent.agitation).toBe(0);
    expect(intent.palette).toBe("held");
    expect(intent.palette).not.toBe("fault");
    expect(intent.flowRate).toBe(0);
    expect(intent.inwardFlow).toBe(0);
    expect(intent.topology).toBe(0);
    expect(intent.restraint).toBeGreaterThan(0);
    expect(intent.severity).toBe("info");
    // It is the calmest of the eleven, and calmer than the failure.
    const failed = intentOf([FAILED()]);
    expect(intent.ringSpin).toBeLessThan(failed.ringSpin);
    expect(intent.glow).toBeLessThan(failed.glow);
    expect(intent.kind).not.toBe("error");
    expect(isCreativeUnavailable(failed)).toBe(false);
  });

  it("never draws a bar, a constellation or a candidate for a creative run — a similarity is not progress", () => {
    for (const events of [[ANALYSING()], [EXPORTING()], [VERIFIED()], [MISMATCH()], [UNAVAILABLE()], [CREATIVE_ACTIVITY_BARE()]]) {
      const intent = intentOf(events);
      expect(intent.progress).toBeNull();
      expect(intent.constellationNodes).toBe(0);
      expect(intent.capabilityNodes).toBe(0);
      expect(intent.satelliteComplete).toBe(false);
    }
  });

  it("moves no channel for an event whose publisher declared no intensity", () => {
    const intent = intentOf([CREATIVE_ACTIVITY_BARE()]);
    expect(intent.energy).toBe(0);
    expect(intent.intensity).toBeNull();
    expect(intent.creative).not.toBeNull();
    expect(intent.label).toBe(CREATIVE_CAPTION_BARE);
  });

  it("keeps the facts on the last-known shape, and stops every motion channel", () => {
    const intent = intentOf([EXPORTING()], T0 + CREATIVE_TTL_MS + 1_000);
    expect(intent.kind).toBe("last_known");
    expect(intent.creative?.operation).toBe("export");
    expect(intent.creative?.state).toBe("exporting");
    // A last-known shape is not a live one for any of the predicates.
    expect(isCreativeWorking(intent)).toBe(false);
    expect(isCreativeVerified(intent)).toBe(false);
    expect(isCreativeUnavailable(intent)).toBe(false);
    for (const channel of MOTION_CHANNELS) expect(intent[channel], channel).toBe(0);
  });

  it("carries no creative facts on any other kind, and no other family's facts on a creative one", () => {
    for (const events of [
      [AGENT_IDLE()],
      [OPERATOR_RUNNING()],
      [DOCUMENT_ANALYSIS()],
      [ARTIFACT_FACTORY()],
      [APP_FACTORY()],
      [CAPABILITY_GENESIS()],
      [SCENE_ACTIVITY()],
      [EXECUTIVE_RUN()],
    ]) {
      const intent = intentOf(events);
      expect(intent.creative).toBeNull();
      expect(isCreativeWorking(intent)).toBe(false);
    }
    const creative = intentOf([VERIFIED()]);
    expect(creative.scene).toBeNull();
    expect(creative.executive).toBeNull();
    expect(creative.app).toBeNull();
    expect(creative.genesis).toBeNull();
    expect(creative.artifact).toBeNull();
    expect(creative.document).toBeNull();
    expect(creative.mail).toBeNull();
    expect(creative.calendar).toBeNull();
    for (const other of [
      isOperatorActing,
      isDocumentReading,
      isMailReading,
      isArtifactMaking,
      isAppBuilding,
      isGenesisWorking,
      isSceneWorking,
      isExecutiveRunning,
    ]) {
      expect(other(creative)).toBe(false);
    }
  });

  it("does not let a creative event blank the room, or the room blank a creative run", () => {
    const both = truthOf([EXECUTING(), OWNER_AWAY()]);
    expect(coreClaim(both, T0).event?.state).toBe("creative.activity");
    expect(creativeClaim(both, T0).event?.state).toBe("creative.activity");
  });

  it("keeps the more specific bus body under a local tool_running voice leg", () => {
    const bus = intentOf([EXPORTING()]);
    const overlaid = applyVoiceOverlay(bus, VOICE_TOOL_RUNNING);
    expect(overlaid).toBe(bus);
    expect(overlaid.creative?.operation).toBe("export");
    const stale = intentOf([EXPORTING()], T0 + CREATIVE_TTL_MS + 1_000);
    expect(applyVoiceOverlay(stale, VOICE_TOOL_RUNNING).source).toBe("voice");
  });

  it("draws memory rather than observation when the Cloud Core cannot be reached", () => {
    const truth = applyError(truthOf([EXPORTING()]), "ECONNREFUSED", T0 + 1_000);
    const intent = visualFor(truth, T0 + 1_000);
    expect(intent.kind).toBe("unreachable");
    expect(intent.creative?.state).toBe("exporting");
    expect(intent.flowRate).toBe(0);
    expect(intent.energy).toBe(0);
  });
});
