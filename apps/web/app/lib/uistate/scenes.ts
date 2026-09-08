/**
 * 3D creation's channel — contract v10 (M25 spec §6).
 *
 * The Cloud Core publishes `scene.activity` at every step of ONE scene run:
 * a `ScenePlan` is being executed through the tool's own scripting
 * interface, a still is being rendered, the tool is being READ BACK, and
 * the read-back is compared with what the plan asked for — with
 * `{tool?, scene?, state?, objects?}` in its metadata: which of the two
 * tools is being driven, the scene the plan names, the step in the §4
 * names, and how many objects the inspection read.
 *
 * The rules are the genesis channel's, applied to a scene in a real editor:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    tool is the token the publisher sent; the scene is the name it sent;
 *    the count is the number the INSPECTION read. A missing key is
 *    rendered as "not reported", never filled in — and in particular a
 *    scene with no object count is never "0 nesne", because nobody counted.
 * 2. **A state this build does not know is the plain state.** The caption
 *    for a `state` outside the eight the contract names is "3B sahne" and
 *    no more: a word we cannot read is not a step we may narrate — and
 *    above all it is never "doğrulandı".
 * 3. **The read-back is the proof (ADR-0088 §4).** "Doğrulandı" needs
 *    `verified`; a `mismatch` is worded as one and NAMES the object that
 *    did not match; a tool that could not be driven is `unavailable`, said
 *    as a plain inability and never as a fault in the agent.
 * 4. **Nothing here can create, render or inspect.** This channel is
 *    presentation. The scenes themselves are rows the Cockpit reads from
 *    the list route (`lib/cockpit/scenes.ts`), and "Render al" / "Sahneyi
 *    oku" there ask the Cloud Core, which asks the device — never this page.
 */

import {
  SCENE_CAPTION_BARE,
  SCENE_STATE_LABEL,
  SCENE_TOOL_LABEL,
  SCENE_TOOL_LOCATIVE,
  SCENE_UNITY_LICENCE,
  type SceneRunState,
  type SceneTool,
  type Severity,
  type UiStateEvent,
  asObjectCount,
  isSceneRunState,
  isSceneState,
  isSceneTool,
  isSeverity,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every scene event, read verbatim. */
export type SceneFacts = {
  /** `metadata.tool` exactly as sent, or `null` when none was. */
  toolToken: string | null;
  /** The tool when it is one of the two this build knows, else `null`. */
  tool: SceneTool | null;
  /** The scene the plan names (`metadata.scene`), or `null` if none was sent. */
  scene: string | null;
  /** `metadata.state` exactly as sent, or `null` when none was. */
  stateToken: string | null;
  /** The step when it is one of the eight this build knows, else `null`. */
  state: SceneRunState | null;
  /** How many objects the inspection read back, when one was read. `0` is an answer. */
  objects: number | null;
};

/** The published facts on one event, or explicit nulls for no event. */
export function sceneFacts(event: UiStateEvent | null): SceneFacts {
  const tool = metaToken(event, "tool");
  const token = metaToken(event, "state");
  return {
    toolToken: tool,
    tool: isSceneTool(tool) ? tool : null,
    scene: metaToken(event, "scene"),
    stateToken: token,
    state: isSceneRunState(token) ? token : null,
    objects: asObjectCount(event?.metadata.objects),
  };
}

// -------------------------------------------------------------- the posture

/**
 * The shape the Core takes for a scene run, from the published state alone:
 *
 *   making      — `creating`, `applying`: the plan is being executed in the
 *                 tool; a structure is being laid
 *   rendering   — `rendering`: its own posture, because writing an image is
 *                 not the same act as building the thing in it
 *   reading     — `inspecting`: the tool is being read back; the one posture
 *                 in this family that draws inward
 *   verified    — `verified`: the read-back matched; still and bright
 *   unverified  — `unverified`: the run did what was asked and there was
 *                 nothing checkable to read back (an empty scene). Settled
 *                 and plain: nothing disagreed, and nothing was verified
 *                 either, so neither word is rounded to
 *   mismatch    — `mismatch`: held under restraint and NAMED; the scene is
 *                 not what was asked for, and nothing rounds that up
 *   unavailable — `unavailable`: settled and dim. A tool that cannot be
 *                 driven is a fact about a licence, not a fault in the
 *                 agent, and it never agitates (ADR-0088 §5)
 *   failed      — `failed`: held under restraint, no agitation
 *
 * A state this build cannot read, or none at all, is the making posture:
 * the only thing a `scene.activity` with no readable state can mean is that
 * a run exists and nothing settled has been said about it.
 */
export type ScenePosture =
  | "making"
  | "rendering"
  | "reading"
  | "verified"
  | "unverified"
  | "mismatch"
  | "unavailable"
  | "failed";

/** The states in which one of the two editors is actually running (M25 spec §4's loop). */
export const SCENE_WORKING_STATES: readonly SceneRunState[] = [
  "creating",
  "applying",
  "rendering",
  "inspecting",
];

const SCENE_WORKING_SET: ReadonlySet<string> = new Set(SCENE_WORKING_STATES);

export function scenePosture(state: SceneRunState | null): ScenePosture {
  switch (state) {
    case "rendering":
      return "rendering";
    case "inspecting":
      return "reading";
    case "verified":
      return "verified";
    case "unverified":
      return "unverified";
    case "mismatch":
      return "mismatch";
    case "unavailable":
      return "unavailable";
    case "failed":
      return "failed";
    default:
      return "making";
  }
}

/**
 * True for a KNOWN state in which one of the editors is running — the one
 * condition under which a second "Render al" would be asking for a tool
 * that is already busy. A settled, mismatched, unavailable or failed run is
 * not working; a state this build cannot read is not known to be working.
 */
export function sceneRunIsWorking(state: SceneRunState | null): boolean {
  return state !== null && SCENE_WORKING_SET.has(state);
}

/**
 * True for the facts of a run the publisher said could not be driven at
 * all: the one state that earns no control anywhere, because there is
 * nothing on the other side to ask.
 */
export function sceneIsUnavailable(facts: Pick<SceneFacts, "state">): boolean {
  return facts.state === "unavailable";
}

// ------------------------------------------------------------- the captions

/** The words, re-exported from the contract where each is spelled once. */
export {
  SCENE_CAPTION_BARE,
  SCENE_STATE_LABEL,
  SCENE_TOOL_LABEL,
  SCENE_TOOL_LOCATIVE,
  SCENE_UNITY_LICENCE,
} from "./contract";

/** The tool in the owner's words: the spec's name for the two, the token verbatim for a third. */
export function sceneToolWord(token: string | null): string | null {
  if (!token) return null;
  return isSceneTool(token) ? SCENE_TOOL_LABEL[token] : token;
}

/** "(3 nesne)" when the inspection counted, `null` when nobody did. `0` is a count. */
export function sceneObjectsPhrase(objects: number | null): string | null {
  return objects === null ? null : `(${objects} nesne)`;
}

/**
 * The step's word with everything the publisher attached to it, and nothing
 * else — the one piece every caption, facts line and row is built from:
 *
 *   creating/applying/rendering/inspecting → the plain word
 *   verified                               → "doğrulandı (3 nesne)" when counted
 *   mismatch                               → "uyuşmazlık — Kure.Kup" when the
 *                                             object is known, "uyuşmazlık" alone
 *                                             when it is not
 *   unavailable                            → "lisans yok — yapılamadı" for Unity
 *                                             (ADR-0088 §5's own words), and
 *                                             "yapılamadı" for anything else
 *   failed                                 → "başarısız"
 *
 * `object` is the object the COMPARISON named, which the bus does not carry
 * (v10's metadata has no such key) and the list route does: the Cockpit's
 * rows pass it, the Core's caption passes nothing, and both spell the
 * sentence from here. `null` for no state at all.
 */
export function sceneStatePhrase(
  facts: Pick<SceneFacts, "state" | "tool" | "objects">,
  object: string | null = null,
): string | null {
  const state = facts.state;
  if (state === null) return null;
  const word = SCENE_STATE_LABEL[state];
  switch (state) {
    case "verified": {
      const objects = sceneObjectsPhrase(facts.objects);
      return objects ? `${word} ${objects}` : word;
    }
    case "mismatch":
      return object ? `${word} — ${object}` : word;
    case "unavailable":
      return facts.tool === "unity" ? `${SCENE_UNITY_LICENCE} — ${word}` : word;
    default:
      return word;
  }
}

/**
 * The caption the Core draws under the posture (M25 spec §6):
 *
 *   creating, no scene named → "Blender'da sahne kuruluyor"
 *   creating, scene named    → "Blender'da Kure kuruluyor"
 *   rendering                → "Blender · Kure · render alınıyor"
 *   inspecting               → "Blender · Kure · sahne okunuyor"
 *   verified                 → "Blender · Kure · doğrulandı (3 nesne)"
 *   mismatch                 → "Blender · Kure · uyuşmazlık"  (the object
 *                               joins it wherever the object is known:
 *                               "uyuşmazlık — Kure.Kup")
 *   unavailable              → "Unity · lisans yok — yapılamadı"
 *   failed                   → "Blender · Kure · başarısız"
 *
 * — each part only if it was published. `creating` is the one step said in
 * the locative, and for a plain reason: it is the only one where the thing
 * being talked about does not exist yet, so the tool is the PLACE rather
 * than the subject. Everything else has a scene to be about.
 *
 * Without a state this build can read the caption is the bare token name
 * and no more (rule 2), which is true of a run in any step; without a tool
 * or a scene it is the plain step (rule 1), which is all that was said.
 */
export function sceneCaption(facts: SceneFacts, object: string | null = null): string {
  const phrase = sceneStatePhrase(facts, object);
  if (phrase === null) return SCENE_CAPTION_BARE;
  if (facts.state === "creating") {
    const place = facts.tool === null ? null : SCENE_TOOL_LOCATIVE[facts.tool];
    if (place === null) return facts.scene ? `${facts.scene} kuruluyor` : phrase;
    return facts.scene ? `${place} ${facts.scene} kuruluyor` : `${place} ${phrase}`;
  }
  const parts: string[] = [];
  const tool = sceneToolWord(facts.toolToken);
  if (tool) parts.push(tool);
  if (facts.scene) parts.push(facts.scene);
  parts.push(phrase);
  return parts.join(" · ");
}

// ---------------------------------------------------------------- the view

export type SceneStage =
  /** `scene.activity` is current: a scene run was published and the claim has not aged out. */
  | "active"
  /** Nothing has been published about a scene run, or the claim decayed. */
  | "none";

export type SceneView = SceneFacts & {
  stage: SceneStage;
  /**
   * `"active"` when a scene event was ever published, regardless of age;
   * `null` when none was — the panel's "nothing reported", as distinct from
   * a run we stopped hearing about.
   */
  lastKnown: "active" | null;
  /** The posture, from the facts alone. */
  posture: ScenePosture;
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

export function sceneView(claim: Claim): SceneView {
  const event = claim.event;
  const named = event !== null && isSceneState(event.state);
  const facts = sceneFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "active" : "none",
    lastKnown: named ? "active" : null,
    posture: scenePosture(facts.state),
    caption: sceneCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually doing something with a scene right now. */
export function sceneIsActive(view: SceneView): boolean {
  return view.stage === "active";
}
