/**
 * Contract v10: `scene.activity` (M25 spec §6).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to a 3D scene in a real editor: **the Core names a tool, a
 * scene, a step and an object count because the Cloud Core published them —
 * in the words the spec gives it — never a step it inferred, never
 * "doğrulandı" before the publisher said `verified`, never a count nobody
 * read back, and never a control over a tool it cannot drive.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v9 (or
 * v8, v7, v6, v5, v4, v3, v2) is read normally, because v10 only added.
 */

import { describe, expect, it } from "vitest";

import {
  APP_STATES,
  ARTIFACT_STATES,
  CALENDAR_STATES,
  DOCUMENT_STATES,
  GENESIS_RUN_STATES,
  GENESIS_STATES,
  GENESIS_STATE_LABEL,
  GENESIS_TTL_MS,
  KNOWN_CONTRACT_VERSION,
  MAIL_STATES,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STATES,
  SCENE_ACTIVITY as SCENE_ACTIVITY_TOKEN,
  SCENE_CAPTION_BARE,
  SCENE_RUN_STATES,
  SCENE_STATES,
  SCENE_STATE_LABEL,
  SCENE_TOOLS,
  SCENE_TOOL_LABEL,
  SCENE_TOOL_LOCATIVE,
  SCENE_TTL_MS,
  SCENE_UNITY_LICENCE,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  asObjectCount,
  contractCompatibility,
  isAppState,
  isArtifactState,
  isCalendarState,
  isCoreChannel,
  isDocumentState,
  isGenesisState,
  isKnownState,
  isMailState,
  isOperatorState,
  isSceneRunState,
  isSceneState,
  isSceneTool,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  SCENE_WORKING_STATES,
  type SceneFacts,
  sceneCaption,
  sceneFacts,
  sceneIsActive,
  sceneIsUnavailable,
  sceneObjectsPhrase,
  scenePosture,
  sceneRunIsWorking,
  sceneStatePhrase,
  sceneToolWord,
  sceneView,
} from "../../app/lib/uistate/scenes";
import {
  KIND_DETAIL,
  KIND_LABEL,
  SCENE_EMPTY,
  SCENE_LABEL,
  SCENE_OBJECTS_UNTOLD,
  SCENE_UNTOLD,
  STATE_LABEL,
  contractLagNote,
  sceneFactsLine,
  sceneStateLine,
  sceneStateWord,
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
  sceneClaim,
} from "../../app/lib/uistate/truth";
import {
  type VisualIntent,
  type VoiceOverlay,
  applyVoiceOverlay,
  isAppBuilding,
  isArtifactMaking,
  isCalendarPlanning,
  isDocumentReading,
  isGenesisWorking,
  isMailReading,
  isOperatorActing,
  isSceneRendering,
  isSceneUnavailable,
  isSceneWorking,
  visualFor,
} from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  ARTIFACT_FACTORY,
  CAPABILITY_GENESIS,
  DOCUMENT_ANALYSIS,
  MAIL_ACTIVITY,
  OPERATOR_RUNNING,
  OWNER_AWAY,
  SCENE_ACTIVITY,
  SCENE_ACTIVITY_BARE,
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
  toolLabel: "3B sahne",
  lastError: null,
};

const CREATING = () => SCENE_ACTIVITY("blender", null, "creating");
const APPLYING = () => SCENE_ACTIVITY("blender", "Kure", "applying");
const RENDERING = () => SCENE_ACTIVITY("blender", "Kure", "rendering");
const INSPECTING = () => SCENE_ACTIVITY("blender", "Kure", "inspecting");
const VERIFIED = () => SCENE_ACTIVITY("blender", "Kure", "verified", 3);
const MISMATCH = () => SCENE_ACTIVITY("blender", "Kure", "mismatch", 2);
const UNAVAILABLE = () => SCENE_ACTIVITY("unity", null, "unavailable");
const FAILED = () => SCENE_ACTIVITY("blender", "Kure", "failed");

/** Facts as the publisher would have produced them, for the pure caption checks. */
function factsOf(overrides: Partial<SceneFacts> = {}): SceneFacts {
  return {
    toolToken: "blender",
    tool: "blender",
    scene: "Kure",
    stateToken: "rendering",
    state: "rendering",
    objects: null,
    ...overrides,
  };
}

// ------------------------------------------------------------ the contract

describe("contract v10 is v9 plus the scene state, and says so", () => {
  it("is version 10 and still reads a v9, v8, v7, v6, v5, v4, v3 and v2 server", () => {
    expect(KNOWN_CONTRACT_VERSION).toBe(10);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(10)).toBe("current");
    for (const older of [9, 8, 7, 6, 5, 4, 3, 2]) {
      expect(contractCompatibility(older), `v${older}`).toBe("older_supported");
    }
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(11)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("names the one token once, and knows it by membership rather than by prefix", () => {
    expect(SCENE_ACTIVITY_TOKEN).toBe("scene.activity");
    expect(SCENE_STATES).toEqual(["scene.activity"]);
    expect(isKnownState("scene.activity")).toBe(true);
    expect(isSceneState("scene.activity")).toBe(true);
    // A newer server's word is not a state this build may draw as a scene.
    expect(isSceneState("scene.deleted")).toBe(false);
    expect(isSceneState("scene.opened")).toBe(false);
    expect(isSceneState("capability.genesis")).toBe(false);
    expect(isGenesisState("scene.activity")).toBe(false);
    expect(isAppState("scene.activity")).toBe(false);
    expect(isArtifactState("scene.activity")).toBe(false);
    expect(isMailState("scene.activity")).toBe(false);
    expect(isCalendarState("scene.activity")).toBe(false);
    expect(isDocumentState("scene.activity")).toBe(false);
    expect(isOperatorState("scene.activity")).toBe(false);
  });

  it("appends the token after v9's, never reordering", () => {
    expect(UI_STATES.indexOf("scene.activity")).toBe(UI_STATES.indexOf("capability.genesis") + 1);
    expect(UI_STATES[UI_STATES.length - 1]).toBe("scene.activity");
    // Every earlier token keeps the place it had.
    expect(UI_STATES.indexOf("capability.genesis")).toBe(UI_STATES.indexOf("app.factory") + 1);
    expect(UI_STATES.indexOf("app.factory")).toBe(UI_STATES.indexOf("artifact.factory") + 1);
    expect(UI_STATES[0]).toBe("agent.idle");
  });

  it("types the two tools and the eight steps in the spec's order, and admits nothing outside them", () => {
    expect(SCENE_TOOLS).toEqual(["blender", "unity"]);
    for (const tool of SCENE_TOOLS) expect(isSceneTool(tool), tool).toBe(true);
    expect(isSceneTool("Blender")).toBe(false);
    expect(isSceneTool("godot")).toBe(false);
    expect(isSceneTool(null)).toBe(false);
    expect(isSceneTool(3)).toBe(false);

    expect(SCENE_RUN_STATES).toEqual([
      "creating",
      "applying",
      "rendering",
      "inspecting",
      "verified",
      "mismatch",
      "unavailable",
      "failed",
    ]);
    for (const s of SCENE_RUN_STATES) expect(isSceneRunState(s), s).toBe(true);
    expect(isSceneRunState("rendered")).toBe(false);
    expect(isSceneRunState("VERIFIED")).toBe(false);
    expect(isSceneRunState("done")).toBe(false);
    expect(isSceneRunState(true)).toBe(false);
    expect(isSceneRunState(null)).toBe(false);
  });

  it("counts objects only from whole non-negative numbers, and keeps zero as an answer", () => {
    expect(asObjectCount(3)).toBe(3);
    expect(asObjectCount(0)).toBe(0);
    expect(asObjectCount(-1)).toBeNull();
    expect(asObjectCount(2.5)).toBeNull();
    expect(asObjectCount("3")).toBeNull();
    expect(asObjectCount(null)).toBeNull();
    expect(asObjectCount(Number.NaN)).toBeNull();
  });

  it("adds the creative3d subsystem and names it", () => {
    expect(SUBSYSTEMS).toContain("creative3d");
    expect(subsystemLabel("creative3d")).toBe("3B sahne");
    // The v9 subsystem is untouched.
    expect(SUBSYSTEMS).toContain("genesis");
    expect(subsystemLabel("genesis")).toBe("Yeni yetenek");
  });

  it("keeps the token on the agent channel, which drives the core body", () => {
    expect(stateChannel("scene.activity")).toBe("agent");
    expect(isCoreChannel("scene.activity")).toBe(true);
    // The room, the lab and the operator are untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("evolution.building")).toBe("lab");
    expect(stateChannel("operator.running")).toBe("operator");
    expect(stateChannel("capability.genesis")).toBe("agent");
  });

  it("classifies it as transient, on a horizon long enough for a real editor", () => {
    expect(stateKind("scene.activity")).toBe("transient");
    expect(SCENE_TTL_MS).toBe(120_000);
    // Longer than every earlier horizon: a headless render outlasts a device
    // round trip, and reporting a healthy one as lost would be the worse lie.
    expect(SCENE_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(SCENE_TTL_MS).toBeGreaterThan(GENESIS_TTL_MS);
    expect(stateTtlMs("scene.activity")).toBe(SCENE_TTL_MS);
    // The publisher's own ttl_s still beats every figure here.
    expect(stateTtlMs("scene.activity", SCENE_ACTIVITY("blender", "Kure", "rendering", null, { ttl_s: 600 }))).toBe(600_000);
  });

  it("gives the state the Turkish the spec asks for, spelled once", () => {
    expect(SCENE_CAPTION_BARE).toBe("3B sahne");
    expect(STATE_LABEL["scene.activity"]).toBe(SCENE_CAPTION_BARE);
    expect(KIND_LABEL.scene_activity).toBe(SCENE_CAPTION_BARE);
    expect(SCENE_LABEL.active).toBe(SCENE_CAPTION_BARE);
    expect(SCENE_LABEL.none).not.toBe("Boşta");
    expect(stateLabel("scene.activity")).not.toBe("scene.activity");
    expect(SCENE_EMPTY).toBe("Henüz bir sahne yapılmadı.");
    expect(SCENE_UNTOLD).not.toBe(SCENE_EMPTY);
    // The eight steps, each a constant spelled once.
    expect(SCENE_STATE_LABEL).toEqual({
      creating: "sahne kuruluyor",
      applying: "değişiklikler uygulanıyor",
      rendering: "render alınıyor",
      inspecting: "sahne okunuyor",
      verified: "doğrulandı",
      mismatch: "uyuşmazlık",
      unavailable: "yapılamadı",
      failed: "başarısız",
    });
    // "Doğrulandı" is a word for exactly one step, and every word is distinct.
    expect(Object.values(SCENE_STATE_LABEL).filter((w) => w === "doğrulandı")).toHaveLength(1);
    expect(new Set(Object.values(SCENE_STATE_LABEL)).size).toBe(SCENE_RUN_STATES.length);
    // The two tools, in both the forms the spec's own utterances use.
    expect(SCENE_TOOL_LABEL).toEqual({ blender: "Blender", unity: "Unity" });
    expect(SCENE_TOOL_LOCATIVE).toEqual({ blender: "Blender'da", unity: "Unity'de" });
    expect(SCENE_UNITY_LICENCE).toBe("lisans yok");
    // The detail says which side of "done" the posture is on, and that an
    // undriveable tool is said rather than controlled.
    expect(KIND_DETAIL.scene_activity).toContain("doğrulanmış sayılmaz");
    expect(KIND_DETAIL.scene_activity).toContain("İlerleme bildirilmez");
    expect(KIND_DETAIL.scene_activity).toContain("yapılamadığı söylenir");
  });

  it("names exactly the families an older server will never publish", () => {
    const v9 = contractLagNote(9, 10);
    expect(v9).toContain("v9");
    expect(v9).toContain("v10");
    expect(v9).toContain("3B sahne durumu");
    expect(v9).not.toContain("yetenek");
    expect(v9).toContain("yok demek değil");

    const v8 = contractLagNote(8, 10);
    expect(v8).toContain("Yeni yetenek durumu");
    expect(v8).toContain("3B sahne durumu");

    const v2 = contractLagNote(2, 10);
    for (const family of [
      "Alarm ve ekran durumları",
      "dijital operatör durumları",
      "belge inceleme durumu",
      "posta ve takvim durumları",
      "dosya üretim durumu",
      "uygulama üretim durumu",
      "yeni yetenek durumu",
      "3B sahne durumu",
    ]) {
      expect(v2, family).toContain(family);
    }

    // The v9 wording M24 shipped is unchanged for a v8 server seen from v9.
    expect(contractLagNote(8, 9)).toBe(
      "Sunucu durum sözleşmesi v8; bu arayüz v9. Yeni yetenek durumu bu sunucudan henüz yayınlanmıyor — yok demek değil.",
    );
  });

  it("leaves every v9 token, kind, horizon and word exactly as it was", () => {
    expect(GENESIS_STATES).toEqual(["capability.genesis"]);
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
    expect(GENESIS_STATE_LABEL.verified).toBe("doğrulandı");
    expect(APP_STATES).toEqual(["app.factory"]);
    expect(ARTIFACT_STATES).toEqual(["artifact.factory"]);
    expect(MAIL_STATES).toEqual(["mail.activity"]);
    expect(CALENDAR_STATES).toEqual(["calendar.activity"]);
    expect(DOCUMENT_STATES).toEqual(["document.analysis"]);
    expect(OPERATOR_STATES).toEqual(["operator.running", "operator.verifying", "operator.failed"]);
    expect(stateKind("capability.genesis")).toBe("transient");
    expect(stateTtlMs("capability.genesis")).toBe(GENESIS_TTL_MS);
    expect(stateKind("agent.idle")).toBe("steady");
  });
});

// ---------------------------------------------------------------- the facts

describe("the published facts, and nothing else", () => {
  it("reads the tool, the scene, the step and the count exactly as sent", () => {
    const facts = sceneFacts(VERIFIED());
    expect(facts).toEqual({
      toolToken: "blender",
      tool: "blender",
      scene: "Kure",
      stateToken: "verified",
      state: "verified",
      objects: 3,
    });
  });

  it("says nothing for an event that carried nothing, and nothing for no event at all", () => {
    const bare: SceneFacts = {
      toolToken: null,
      tool: null,
      scene: null,
      stateToken: null,
      state: null,
      objects: null,
    };
    expect(sceneFacts(SCENE_ACTIVITY_BARE())).toEqual(bare);
    expect(sceneFacts(null)).toEqual(bare);
  });

  it("keeps a tool or a step it cannot read as the token, and never promotes it", () => {
    const facts = sceneFacts(SCENE_ACTIVITY("godot", "Kure", "exported"));
    expect(facts.toolToken).toBe("godot");
    expect(facts.tool).toBeNull();
    expect(facts.stateToken).toBe("exported");
    expect(facts.state).toBeNull();
    expect(sceneToolWord("godot")).toBe("godot");
    expect(sceneStateWord("exported")).toBe("exported");
  });

  it("counts objects only when the inspection counted, and keeps zero", () => {
    expect(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "verified", 0)).objects).toBe(0);
    expect(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "verified")).objects).toBeNull();
    // A count the boundary cannot read is no count: `metadata.objects` is a
    // string here, which `parseEvent` keeps as a token and this refuses.
    expect(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "verified", null, { objects: "3" })).objects).toBeNull();
    expect(sceneObjectsPhrase(3)).toBe("(3 nesne)");
    expect(sceneObjectsPhrase(0)).toBe("(0 nesne)");
    expect(sceneObjectsPhrase(null)).toBeNull();
  });

  it("parses a real event through the boundary without inventing a key", () => {
    const parsed = parseEvent(SCENE_ACTIVITY("unity", "Arac", "unavailable"));
    expect(parsed).not.toBeNull();
    expect(parsed?.subsystem).toBe("creative3d");
    expect(parsed?.metadata).toEqual({ tool: "unity", scene: "Arac", state: "unavailable" });
    expect(parsed?.metadata.objects).toBeUndefined();
  });
});

// -------------------------------------------------------------- the posture

describe("the posture, from the published step alone", () => {
  it("gives each step its own posture, and an unreadable one the making posture", () => {
    expect(scenePosture("creating")).toBe("making");
    expect(scenePosture("applying")).toBe("making");
    expect(scenePosture("rendering")).toBe("rendering");
    expect(scenePosture("inspecting")).toBe("reading");
    expect(scenePosture("verified")).toBe("verified");
    expect(scenePosture("mismatch")).toBe("mismatch");
    expect(scenePosture("unavailable")).toBe("unavailable");
    expect(scenePosture("failed")).toBe("failed");
    expect(scenePosture(null)).toBe("making");
  });

  it("knows which steps mean an editor is actually running", () => {
    expect(SCENE_WORKING_STATES).toEqual(["creating", "applying", "rendering", "inspecting"]);
    for (const state of SCENE_RUN_STATES) {
      expect(sceneRunIsWorking(state), state).toBe(SCENE_WORKING_STATES.includes(state));
    }
    expect(sceneRunIsWorking(null)).toBe(false);
  });

  it("marks the one step that means the tool could not be driven at all", () => {
    expect(sceneIsUnavailable({ state: "unavailable" })).toBe(true);
    for (const state of SCENE_RUN_STATES.filter((s) => s !== "unavailable")) {
      expect(sceneIsUnavailable({ state }), state).toBe(false);
    }
    expect(sceneIsUnavailable({ state: null })).toBe(false);
  });
});

// ------------------------------------------------------------- the captions

describe("the caption says the step and everything published with it, and no more", () => {
  it("says the creating step in the locative — the tool is the place, not the subject", () => {
    expect(sceneCaption(sceneFacts(CREATING()))).toBe("Blender'da sahne kuruluyor");
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("unity", null, "creating")))).toBe("Unity'de sahne kuruluyor");
    // With a scene named, the scene is what is being built, in the same place.
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "creating")))).toBe("Blender'da Kure kuruluyor");
    // Without a tool there is no place to name: only what was said.
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY(null, "Kure", "creating")))).toBe("Kure kuruluyor");
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY(null, null, "creating")))).toBe("sahne kuruluyor");
  });

  it("says every other step as tool · scene · step, each part only if published", () => {
    expect(sceneCaption(sceneFacts(APPLYING()))).toBe("Blender · Kure · değişiklikler uygulanıyor");
    expect(sceneCaption(sceneFacts(RENDERING()))).toBe("Blender · Kure · render alınıyor");
    expect(sceneCaption(sceneFacts(INSPECTING()))).toBe("Blender · Kure · sahne okunuyor");
    expect(sceneCaption(sceneFacts(FAILED()))).toBe("Blender · Kure · başarısız");
    // Missing metadata is missing: the plain step name and no more.
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY(null, null, "rendering")))).toBe("render alınıyor");
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("blender", null, "rendering")))).toBe("Blender · render alınıyor");
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY(null, "Kure", "rendering")))).toBe("Kure · render alınıyor");
    // A tool this build cannot read is still a published fact, printed verbatim.
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("godot", "Kure", "rendering")))).toBe("godot · Kure · render alınıyor");
  });

  it("says 'doğrulandı' only for verified, and carries the count the inspection read", () => {
    expect(sceneCaption(sceneFacts(VERIFIED()))).toBe("Blender · Kure · doğrulandı (3 nesne)");
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "verified", 0)))).toBe("Blender · Kure · doğrulandı (0 nesne)");
    // Nobody counted: "doğrulandı" alone, never "(0 nesne)".
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "verified")))).toBe("Blender · Kure · doğrulandı");
    // No other step reaches the word, however it was published.
    for (const state of SCENE_RUN_STATES.filter((s) => s !== "verified")) {
      const caption = sceneCaption(sceneFacts(SCENE_ACTIVITY("blender", "Kure", state, 3)));
      expect(caption, state).not.toContain("doğrulandı");
    }
    // And neither does a word this build cannot read, however like it looks.
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "verified_ok", 3)))).toBe(SCENE_CAPTION_BARE);
  });

  it("names the object a mismatch was about wherever the object is known", () => {
    expect(sceneCaption(sceneFacts(MISMATCH()))).toBe("Blender · Kure · uyuşmazlık");
    // The comparison's object is not on the bus (v10 carries no such key), so
    // the row passes it and the sentence is spelled from the same place.
    expect(sceneStatePhrase({ state: "mismatch", tool: "blender", objects: null }, "Kure.Kup")).toBe("uyuşmazlık — Kure.Kup");
    expect(sceneCaption(sceneFacts(MISMATCH()), "Kure.Kup")).toBe("Blender · Kure · uyuşmazlık — Kure.Kup");
    // An object beside a step that is not a mismatch is not a mismatch.
    expect(sceneStatePhrase({ state: "verified", tool: "blender", objects: 3 }, "Kure.Kup")).toBe("doğrulandı (3 nesne)");
  });

  it("says an unavailable Unity with the licence, and every other unavailable plainly", () => {
    expect(sceneCaption(sceneFacts(UNAVAILABLE()))).toBe("Unity · lisans yok — yapılamadı");
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("unity", "Arac", "unavailable")))).toBe("Unity · Arac · lisans yok — yapılamadı");
    // The licence is Unity's measured reason (ADR-0088 §5) and nobody else's:
    // a Blender that is merely not installed gets no licence invented for it.
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "unavailable")))).toBe("Blender · Kure · yapılamadı");
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY(null, null, "unavailable")))).toBe("yapılamadı");
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("godot", null, "unavailable")))).toBe("godot · yapılamadı");
    // It is never worded as a failure either.
    expect(sceneCaption(sceneFacts(UNAVAILABLE()))).not.toContain("başarısız");
  });

  it("falls back to the bare token for a step it cannot read, and for none at all", () => {
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "exported")))).toBe(SCENE_CAPTION_BARE);
    expect(sceneCaption(sceneFacts(SCENE_ACTIVITY_BARE()))).toBe(SCENE_CAPTION_BARE);
    expect(sceneCaption(sceneFacts(null))).toBe(SCENE_CAPTION_BARE);
    expect(sceneStatePhrase(factsOf({ state: null, stateToken: null }))).toBeNull();
  });

  it("lines the facts with what was sent and the statement of what was not", () => {
    expect(sceneFactsLine(sceneFacts(RENDERING()))).toBe("araç: Blender · sahne: Kure · durum: render alınıyor");
    expect(sceneFactsLine(sceneFacts(VERIFIED()))).toBe("araç: Blender · sahne: Kure · durum: doğrulandı · nesneler: 3");
    // The absence of a count is said only where a read-back was the point.
    expect(sceneFactsLine(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "verified")))).toBe(
      `araç: Blender · sahne: Kure · durum: doğrulandı · ${SCENE_OBJECTS_UNTOLD}`,
    );
    expect(sceneFactsLine(sceneFacts(RENDERING()))).not.toContain(SCENE_OBJECTS_UNTOLD);
    expect(sceneFactsLine(sceneFacts(SCENE_ACTIVITY_BARE()))).toBe("araç bildirilmedi · sahne bildirilmedi · durum bildirilmedi");
    expect(sceneFactsLine(sceneFacts(SCENE_ACTIVITY("godot", null, "exported")))).toBe(
      "araç: godot · sahne bildirilmedi · durum: exported",
    );
    // The row/caption helper falls back to the word, never to nothing.
    expect(sceneStateLine(sceneFacts(VERIFIED()))).toBe("doğrulandı (3 nesne)");
    expect(sceneStateLine(sceneFacts(SCENE_ACTIVITY_BARE()))).toBe("durum bildirilmedi");
    expect(sceneStateLine(sceneFacts(SCENE_ACTIVITY("blender", "Kure", "exported")))).toBe("exported");
    expect(sceneStateWord(null)).toBe("durum bildirilmedi");
  });
});

// ----------------------------------------------------------------- the view

describe("the view over one claim", () => {
  it("is active while the claim holds, with the facts, the posture and the caption", () => {
    const view = sceneView(sceneClaim(truthOf([VERIFIED()]), T0 + 2_000));
    expect(view.stage).toBe("active");
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(false);
    expect(view.posture).toBe("verified");
    expect(view.caption).toBe("Blender · Kure · doğrulandı (3 nesne)");
    expect(view.tool).toBe("blender");
    expect(view.scene).toBe("Kure");
    expect(view.objects).toBe(3);
    expect(view.ageMs).toBe(2_000);
    expect(sceneIsActive(view)).toBe(true);
  });

  it("stops claiming the step once it ages out, without saying the work stopped", () => {
    const view = sceneView(sceneClaim(truthOf([RENDERING()]), T0 + SCENE_TTL_MS + 1_000));
    expect(view.stage).toBe("none");
    // What WAS published is still a fact, and the panel words it as last-known.
    expect(view.lastKnown).toBe("active");
    expect(view.expired).toBe(true);
    expect(view.caption).toBe("Blender · Kure · render alınıyor");
    expect(sceneIsActive(view)).toBe(false);
  });

  it("reports nothing at all when nothing was ever published about a scene", () => {
    const view = sceneView(sceneClaim(truthOf([AGENT_IDLE()]), T0));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBeNull();
    expect(view.caption).toBe(SCENE_CAPTION_BARE);
    expect(view.posture).toBe("making");
    expect(view.ageMs).toBeNull();
  });

  it("reads the newest scene event by membership, and never another family's", () => {
    const truth = truthOf([RENDERING(), VERIFIED()]);
    expect(sceneView(sceneClaim(truth, T0)).state).toBe("verified");
    for (const other of [[DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [ARTIFACT_FACTORY()], [APP_FACTORY()], [CAPABILITY_GENESIS()]]) {
      expect(sceneClaim(truthOf(other), T0).event).toBeNull();
    }
    // And the other families' claims never pick a scene event up.
    const scene = truthOf([VERIFIED()]);
    for (const claim of [documentClaim, mailClaim, calendarClaim, artifactClaim, appClaim, genesisClaim]) {
      expect(claim(scene, T0).event).toBeNull();
    }
  });
});

// ------------------------------------------------------------- the geometry

describe("the Core's body for a scene run", () => {
  it("gives the making, rendering and reading steps distinct postures", () => {
    const making = intentOf([APPLYING()]);
    const rendering = intentOf([RENDERING()]);
    const reading = intentOf([INSPECTING()]);
    for (const intent of [making, rendering, reading]) expect(intent.kind).toBe("scene_activity");
    // Making pushes a plan out and draws nothing in.
    expect(making.inwardFlow).toBe(0);
    expect(making.flowRate).toBeGreaterThan(0);
    // Rendering is its own posture: the structure stands still and one steady
    // stream goes out. Writing an image is not building the thing in it.
    expect(rendering.flowRate).toBeGreaterThan(making.flowRate);
    expect(rendering.ringSpin).toBeLessThan(making.ringSpin);
    expect(rendering.inwardFlow).toBe(0);
    expect(isSceneRendering(rendering)).toBe(true);
    expect(isSceneRendering(making)).toBe(false);
    // Reading is the one step that draws inward: the tool is being asked.
    expect(reading.inwardFlow).toBeGreaterThan(0);
    expect(reading.palette).toBe("reading");
    for (const intent of [making, rendering]) expect(intent.palette).toBe("making");
  });

  it("settles on verified, holds a mismatch and a failure under restraint, and never agitates", () => {
    const verified = intentOf([VERIFIED()]);
    expect(verified.palette).toBe("ready");
    expect(verified.flowRate).toBe(0);
    expect(verified.restraint).toBe(0);
    expect(verified.agitation).toBe(0);

    const mismatch = intentOf([MISMATCH()]);
    expect(mismatch.restraint).toBeGreaterThan(0);
    expect(mismatch.flowRate).toBe(0);
    expect(mismatch.agitation).toBe(0);
    expect(mismatch.glow).toBeLessThan(verified.glow);

    const failed = intentOf([FAILED()]);
    expect(failed.restraint).toBeGreaterThan(0);
    expect(failed.agitation).toBe(0);
  });

  it("draws an undriveable tool as settled and honest, never as an error", () => {
    const intent = intentOf([UNAVAILABLE()]);
    expect(intent.kind).toBe("scene_activity");
    expect(isSceneUnavailable(intent)).toBe(true);
    // A licence nobody has is not a fault: no agitation, no fault palette, no
    // error kind — and nothing flowing anywhere, because nothing is running.
    expect(intent.agitation).toBe(0);
    expect(intent.palette).toBe("held");
    expect(intent.palette).not.toBe("fault");
    expect(intent.flowRate).toBe(0);
    expect(intent.inwardFlow).toBe(0);
    expect(intent.topology).toBe(0);
    expect(intent.restraint).toBeGreaterThan(0);
    expect(intent.severity).toBe("info");
    // It is the calmest of the seven, and calmer than the failure.
    const failed = intentOf([FAILED()]);
    expect(intent.ringSpin).toBeLessThan(failed.ringSpin);
    expect(intent.glow).toBeLessThan(failed.glow);
    // And it is not the error kind, however the owner's Unity is licensed.
    expect(intent.kind).not.toBe("error");
    expect(isSceneUnavailable(intentOf([FAILED()]))).toBe(false);
  });

  it("never draws a bar, a constellation or a candidate for a scene run", () => {
    for (const events of [[CREATING()], [RENDERING()], [VERIFIED()], [UNAVAILABLE()], [SCENE_ACTIVITY_BARE()]]) {
      const intent = intentOf(events);
      expect(intent.progress).toBeNull();
      expect(intent.constellationNodes).toBe(0);
      expect(intent.capabilityNodes).toBe(0);
      expect(intent.satelliteComplete).toBe(false);
    }
  });

  it("moves no channel for an event whose publisher declared no intensity", () => {
    const intent = intentOf([SCENE_ACTIVITY_BARE()]);
    expect(intent.energy).toBe(0);
    expect(intent.intensity).toBeNull();
    expect(intent.scene).not.toBeNull();
    expect(intent.label).toBe(SCENE_CAPTION_BARE);
  });

  it("keeps the facts on the last-known shape, and stops every motion channel", () => {
    const intent = intentOf([RENDERING()], T0 + SCENE_TTL_MS + 1_000);
    expect(intent.kind).toBe("last_known");
    expect(intent.scene?.scene).toBe("Kure");
    expect(intent.scene?.state).toBe("rendering");
    // A last-known shape is not a live one for any of the predicates.
    expect(isSceneWorking(intent)).toBe(false);
    expect(isSceneRendering(intent)).toBe(false);
    for (const channel of MOTION_CHANNELS) expect(intent[channel], channel).toBe(0);
  });

  it("carries no scene facts on any other kind", () => {
    for (const events of [[AGENT_IDLE()], [OPERATOR_RUNNING()], [DOCUMENT_ANALYSIS()], [ARTIFACT_FACTORY()], [APP_FACTORY()], [CAPABILITY_GENESIS()]]) {
      const intent = intentOf(events);
      expect(intent.scene).toBeNull();
      expect(isSceneWorking(intent)).toBe(false);
    }
    // And a scene event carries no other family's facts.
    const scene = intentOf([VERIFIED()]);
    expect(scene.app).toBeNull();
    expect(scene.genesis).toBeNull();
    expect(scene.artifact).toBeNull();
    expect(scene.document).toBeNull();
    expect(scene.mail).toBeNull();
    expect(scene.calendar).toBeNull();
    for (const other of [isOperatorActing, isDocumentReading, isMailReading, isCalendarPlanning, isArtifactMaking, isAppBuilding, isGenesisWorking]) {
      expect(other(scene)).toBe(false);
    }
  });

  it("does not let a scene event blank the room, or the room blank a scene", () => {
    const both = truthOf([RENDERING(), OWNER_AWAY()]);
    // The room's newest event is not the core body's.
    expect(coreClaim(both, T0).event?.state).toBe("scene.activity");
    expect(sceneClaim(both, T0).event?.state).toBe("scene.activity");
  });

  it("keeps the more specific bus body under a local tool_running voice leg", () => {
    const bus = intentOf([RENDERING()]);
    const overlaid = applyVoiceOverlay(bus, VOICE_TOOL_RUNNING);
    // The bus knows the tool, the scene and the step; the local leg only knows
    // that a tool is running. The more specific of two true statements wins.
    expect(overlaid).toBe(bus);
    expect(overlaid.scene?.scene).toBe("Kure");
    // A last-known scene shape yields to the local observation, as every other
    // aged bus body does.
    const stale = intentOf([RENDERING()], T0 + SCENE_TTL_MS + 1_000);
    expect(applyVoiceOverlay(stale, VOICE_TOOL_RUNNING).source).toBe("voice");
  });

  it("draws memory rather than observation when the Cloud Core cannot be reached", () => {
    const truth = applyError(truthOf([RENDERING()]), "ECONNREFUSED", T0 + 1_000);
    const intent = visualFor(truth, T0 + 1_000);
    expect(intent.kind).toBe("unreachable");
    expect(intent.scene?.state).toBe("rendering");
    expect(intent.flowRate).toBe(0);
    expect(intent.energy).toBe(0);
  });
});
