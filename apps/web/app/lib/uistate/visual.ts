/**
 * The one place where a system state becomes a visual. Pure, synchronous,
 * exhaustively tested (`tests/uistate/visual.test.ts`).
 *
 * ADR-0052 in one sentence: **the Core shows what is TRUE.** Everything in this
 * file follows from that, and the rules are worth stating because they are easy
 * to break by accident later:
 *
 * 1. **Every moving channel traces to an event.** There is no clock-driven
 *    "activity". `topology` is non-zero because `agent.thinking` was published,
 *    not because motion looks alive. If no event says so, the channel is 0.
 * 2. **Counts are counts, never estimates.** `sourceNodes` is the number the
 *    research publisher actually sent. When it sent none, the count is 0 and
 *    `sourceNodesKnown` is false, and the renderer draws nothing and says so.
 *    There is no "looks about right" fallback anywhere in this file.
 * 3. **Silence is not calm.** Not-yet-told, expired, unreachable and unknown-
 *    state each get their own visual kind. None of them is `idle`.
 * 4. **`intensity` is passed through, never synthesised.** It stays `null` when
 *    the publisher did not send one, and channels that depend on it fall back
 *    to a *still* core rather than a default wobble.
 * 5. **Error is controlled.** `agitation` is bounded and slow by construction
 *    (see `ERROR_AGITATION`); there is no strobe channel to abuse.
 */

import {
  type Severity,
  type UiStateEvent,
  isEvolutionState,
  isKnownState,
  metaNumber,
} from "./contract";
import {
  type AlarmStage,
  type ReleaseStage,
  alarmIsSounding,
  alarmView,
  eyeView,
  releaseView,
} from "./ambient";
import {
  type Claim,
  type CoreTruth,
  alarmClaim,
  coreClaim,
  eyeClaim,
  releaseClaim,
} from "./truth";
import { type AppFacts, appCaption, appFacts, appIsServing } from "./apps";
import { type ArtifactFacts, artifactCaption, artifactFacts } from "./artifacts";
import { type CalendarFacts, calendarCaption, calendarFacts } from "./calendar";
import { type DocumentFacts, documentCaption, documentFacts } from "./documents";
import { type GenesisFacts, genesisCaption, genesisFacts, genesisPosture } from "./genesis";
import { type MailFacts, mailCaption, mailFacts } from "./mail";
import { type SceneFacts, sceneCaption, sceneFacts, scenePosture } from "./scenes";
import { type CreativeFacts, creativeCaption, creativeFacts, creativePosture } from "./creative";
import { type NativeFacts, nativeCaption, nativeFacts, nativePosture } from "./native";
import { type ExecutiveFacts, executiveCaption, executiveFacts, executivePosture } from "./executive";
import { operatorCaption, operatorFacts } from "./operator";
import type { VoiceUiState } from "../voice/controller";

export type CoreVisualKind =
  /** No poll has succeeded yet. */
  | "connecting"
  /** Polls succeed, but the bus has never published anything. */
  | "untold"
  /** The API cannot be reached; the last known picture is shown, faded. */
  | "unreachable"
  /** The session was refused. */
  | "unauthorized"
  /** A state arrived that this build does not know how to draw. */
  | "unknown_state"
  /** A transient claim aged out. We know what it *was*, not what it *is*. */
  | "last_known"
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  /** The owner spoke over the assistant; playback is already stopped (local voice only). */
  | "interrupted"
  | "researching"
  | "memory"
  | "tool_running"
  | "waiting_owner"
  | "goal_completed"
  | "error"
  /** evolution.researching | designing | building | testing */
  | "evolution_working"
  | "shadow_ready"
  /**
   * v4 (M19): the Digital Operator acting on the owner's desktop through the
   * companion. The posture is the one the visual language reserves for tool
   * execution — open shells, turning rings, the paths carrying traffic — with
   * the published step as the caption. Three kinds rather than one, because
   * "acting", "re-observing to verify" and "failed" are three different
   * statements and the headline must name the one that was published.
   */
  | "operator_running"
  | "operator_verifying"
  | "operator_failed"
  /**
   * v5 (M20): the Core reading one of the owner's documents — extracting,
   * retrieving, answering from its refs. A reading posture: information
   * drawn in, the structure calm, nothing that could be read as progress,
   * with the published file and place as the caption.
   */
  | "document_analysis"
  /**
   * v6 (M21): the Core reading the owner's mail or holding a draft. The
   * reading posture again — calm, information drawn in, nothing that could
   * be read as progress — with the folder, the draft's step and the subject
   * as the caption. A draft waiting on the owner does not make the Core
   * busy: the posture is the same calm one, and the caption says "bekliyor".
   */
  | "mail_activity"
  /**
   * v6 (M21): the Core reading the owner's calendar or holding a proposal.
   * A planning posture: the structure laid a little open and still, the
   * rings barely turning, nothing flowing inward — arranging, not ingesting
   * — with the range, the proposal's step and its conflicts as the caption.
   */
  | "calendar_activity"
  /**
   * v7 (M22): the Core making a file for the owner — rendering it, then
   * reopening it with an independent parser to check it against what was
   * asked. A making posture: a faint lattice being laid, the paths carrying
   * traffic OUT into a file while the factory writes, still once a verdict
   * was named; calm, nothing that could be read as progress, and the title,
   * the format and the verdict as the caption. An invalid render is worded
   * as invalid with its failing ref — never dressed as done.
   */
  | "artifact_factory"
  /**
   * v8 (M23): the Core making an app for the owner — planning it,
   * scaffolding it into a real project on the owner's machine, running it
   * there in a bounded process, testing it with its own tests. A building
   * posture while the project is planned or scaffolded (the making
   * posture's lattice and outward traffic); a distinct RUNNING posture when
   * the publisher said `running` — the lattice stands, the shells open, and
   * the paths carry steady traffic only when a port was named (a server
   * answering at an address); still once tested, stopped, or failed. Calm,
   * nothing that could be read as progress, and the project, the state, the
   * port and the counts as the caption. A failure is worded as one with its
   * count — never dressed as done.
   */
  | "app_factory"
  /**
   * v9 (M24): the Core acquiring a capability the owner's request needs —
   * researching the interface, writing and testing an adapter, classifying
   * it, rolling it out, registering it, using it and verifying the result
   * through the application. A building posture while the run works (the
   * making posture's lattice and outward traffic); a distinct WAITING
   * posture at `awaiting_approval` — the owner is the thing being waited
   * on, so the Core is held exactly as it is for `agent.waiting_owner`;
   * a settled posture from `available` on — still and bright, the
   * capability what the run says it is; `failed` is held under restraint
   * with no agitation — a failed run is a fact about an adapter, not a
   * fault in the agent. Calm, nothing that could be read as progress, and
   * the capability, the state and the error class as the caption.
   */
  | "capability_genesis"
  /**
   * v10 (M25): the Core building a 3D scene for the owner through the
   * tool's OWN scripting interface — executing a scene plan in Blender or
   * Unity, rendering a still, then READING THE TOOL BACK and comparing it
   * with what was asked. A making posture while the plan runs (the app
   * factory's lattice and outward traffic); a distinct RENDERING posture —
   * the lattice stands still and the paths carry an image out, because
   * writing a picture is not the same act as building the thing in it; a
   * READING posture at `inspecting`, the one step in this family that draws
   * inward (the tool is being asked what it holds); still and bright at
   * `verified`; `mismatch` held under restraint and named; `unavailable`
   * SETTLED and dim with no agitation whatever — a tool that cannot be
   * driven for want of a licence is a fact about a licence, not a fault in
   * the agent (ADR-0088 §5) — and `failed` held under the same restraint.
   * Calm, nothing that could be read as progress, and the tool, the scene,
   * the step and the object count as the caption.
   */
  | "scene_activity"
  /**
   * v11 (M26): the Core carrying a multi-step job for the owner — a
   * validated task graph run step by step across the families, pausable,
   * correctable and cancellable at any moment, ending in an honest state.
   * A planning posture while the graph exists and nothing has run; a
   * WORKING posture while steps run (the making posture's lattice and
   * outward traffic); a HELD posture at `paused`, because the owner said
   * "Bekle" and a run waiting on the owner is held exactly as
   * `agent.waiting_owner` is, with no agitation whatever; still and bright
   * at `completed`; `partial` held and NAMED by what is missing, never
   * rounded up to done (ADR-0089 §3); `cancelled` settled and dim — the
   * owner's own decision, not a fault; `failed` under the same restraint.
   * A bar only when the publisher counted BOTH `done` and `total`, and the
   * step, the state and the counts as the caption.
   */
  | "executive_run"
  /**
   * v12 (M27): the Core making a picture for the owner — the input image
   * analysed, a plan built as DATA, its operations applied through the most
   * structured interface the installed application really offers, the output
   * reopened by an INDEPENDENT reader, exported, and compared with what was
   * asked. A READING posture at `analysing` and `inspecting`, the two steps
   * that draw inward because an image is being read; a planning posture
   * while the plan is data and no file has been touched; a making posture
   * while the operations run; a distinct EXPORTING posture, because writing
   * a file in a format is not the same act as making what goes in it; a
   * COMPARING posture, because that step is where every "doğrulandı" in
   * this family comes from; a `correcting` posture that works but is held,
   * because something has already been found wrong; still and bright at
   * `verified` and at nothing else; `unverified` settled and plain, neither
   * verified nor disagreed with; `mismatch` held under restraint and NAMED
   * by its defect; `unavailable` SETTLED and dim with no agitation whatever
   * — an application that is not installed is a fact about this machine, not
   * a fault in the agent (ADR-0093 decision 3) — and `failed` under the same
   * restraint. Calm, nothing that could be read as progress, and the
   * application, the operation, the step and the comparison as the caption.
   */
  | "creative_activity"
  /**
   * v13 (M28): the Core making a program the owner can install — a project
   * written from a fixed template, compiled by the real toolchain on this
   * machine, its own tests run, packaged, and the produced artefact reopened
   * by a reader that did NOT build it. A planning posture at `planned`,
   * still because a chosen stack is not a build; a making posture while the
   * project is written, compiled and packed; a distinct TESTING posture,
   * because the generated project's own tests are the first thing that can
   * disagree with what was asked; a READING posture at `validating`, drawing
   * inward, because that is where every "doğrulandı" in this family comes
   * from; still and bright at `verified` and at nothing else; `unverified`
   * settled and plain — the artefact is there and nothing could check it;
   * `mismatch` held under restraint and named; `unavailable` SETTLED and dim
   * with no agitation whatever — a machine with no JDK is a fact about the
   * world, not a fault in the agent (ADR-0095 decision 3) — and `failed`
   * under the same restraint. Calm, and no bar: a build publishes steps, not
   * a fraction of itself.
   */
  | "native_build";

/**
 * Which of the two evidence sources produced the intent (ADR-0061 §4).
 *
 * `bus` is `GET /v1/ui/state`, the cloud's account of every subsystem. `voice`
 * is this tab's own `VoiceSessionController` — a direct observation of the
 * session this device is in, which is why it may overlay the bus for the
 * states it actually holds, and why the readout must say which one it is.
 */
export type VisualSource = "bus" | "voice";

export type PaletteToken =
  | "calm"
  | "inward"
  | "active"
  | "voice"
  | "discovery"
  | "recall"
  | "work"
  | "held"
  | "achieved"
  | "fault"
  | "lab"
  | "ready"
  /** v5: the reading Core — a pale parchment gold, calmer than any working tone. */
  | "reading"
  /** v6: the planning Core — straw, the parchment's duller neighbour. */
  | "planning"
  /** v7: the making Core — wheat amber, a shade warmer than parchment: a page being made, not read. */
  | "making"
  | "unknown";

/**
 * Everything a renderer needs, and nothing it could use to invent activity.
 *
 * Every numeric channel is in 0..1 unless noted. A renderer may scale these into
 * its own units but must not add motion of its own on top of a zero channel.
 */
export type VisualIntent = {
  kind: CoreVisualKind;
  /** Who produced this intent: the state bus, or the local voice controller. */
  source: VisualSource;
  /** The local controller's state when `source` is `voice`; `null` otherwise. */
  voiceState: VoiceUiState | null;
  /** The raw state token this came from, `null` when no event backs it. */
  state: string | null;
  subsystem: string | null;
  /** Publisher-supplied short label (a topic, a goal title). Never prose. */
  label: string | null;
  status: string | null;
  severity: Severity;
  /** Age of the backing event, ms. `null` when there is no event. */
  ageMs: number | null;

  // ------------------------------------------------------------- scalars
  /** The publisher's declared intensity, or `null`. Never substituted. */
  intensity: number | null;
  /**
   * Real progress in 0..1, or `null` when the publisher does not know it.
   * A progress bar is drawn if and only if this is non-null (ADR-0052 §2).
   */
  progress: number | null;

  // ------------------------------------------------------------ geometry
  /** Core radius multiplier around 1.0. */
  scale: number;
  /** Breathing depth. 0 means the core is still — the honest default. */
  breathAmplitude: number;
  /** Breaths per second. Meaningless when `breathAmplitude` is 0. */
  breathHz: number;
  /** Listening: how strongly energy is drawn inward. */
  inwardFlow: number;
  /** Thinking: density of the internal lattice. */
  topology: number;
  /**
   * Speaking: amplitude of the output pulse, taken from the event's bounded
   * energy. Zero when the publisher sent no intensity, so the core does not
   * pulse to a rhythm nobody reported.
   */
  pulse: number;
  /** Error: bounded, slow disturbance. Never a strobe. */
  agitation: number;
  /** Waiting on the owner: how much ordinary motion is suppressed. */
  restraint: number;
  /** How faded the whole core is drawn: 0 fully present, 1 nearly gone. */
  dim: number;

  // ------------------------------------------- M18.1: the layered structure
  /**
   * The instantaneous energy behind the state, 0..1: the publisher's declared
   * `intensity` for a bus event, the gate's microphone level while this tab's
   * own session listens, the playback RMS while it speaks. **0 when nothing
   * was declared or measured** — this is the channel the glow answers to, and
   * it must never carry a synthesised figure.
   */
  energy: number;
  /**
   * Light response 0..1: a per-state base plus half the `energy`. The base is
   * an encoding of the published state (a completed goal is brighter than a
   * held one); only the energy part moves within a state, and only because a
   * real figure moved it.
   */
  glow: number;
  /**
   * How far the translucent structural shells stand off the nucleus, 0..1.
   * Thinking and tool work expand it; listening contracts it. A geometry
   * target, not a rhythm: it is approached once and then holds.
   */
  shellSpread: number;
  /**
   * Angular rate of the internal rings and topology layers, 0..1. Zero for
   * every kind that reports no activity; the reported-idle drift is small and
   * belongs to the same claim as the idle breath.
   */
  ringSpin: number;
  /**
   * Speed of the bounded particle travel along the internal connection paths,
   * 0..1. Thinking and tool activity drive it; silence leaves it at zero.
   */
  flowRate: number;
  /**
   * OWNER_SPEAKING: the measured microphone level while this tab's own session
   * is listening, 0..1. Exactly `micLevel`, never an estimate; 0 for a bus
   * listening (whose `intensity` is declared, not measured here) and for
   * every other state.
   */
  ownerVoice: number;

  // -------------------------------------------------------- constellation
  /**
   * Research: how many nodes the source/evidence constellation draws before
   * the tier caps it. The published count when there is one; the fixed
   * restrained motif (`CONSTELLATION_MOTIF`) when research is running but no
   * count was sent — `sourceNodesKnown` says which, and the readout labels the
   * motif as a representation. 0 outside research.
   */
  constellationNodes: number;
  /**
   * Research: the constellation's motion, 0..1. From published progress when
   * there is one; otherwise the fixed restrained `CONSTELLATION_REST`. Never
   * a random or clock-driven figure. 0 outside research.
   */
  constellationDrift: number;
  /**
   * Research: the wider field of candidates seen, when the publisher counted
   * both what it saw and what it kept. Drawn faint around the kept evidence.
   */
  fieldNodes: number;
  fieldNodesKnown: boolean;

  // ---------------------------------------------------------- capabilities
  /**
   * SHADOW_READY: peripheral capability nodes. The published count when the
   * lab sent one (`candidates` / `ready`), else exactly one — the candidate
   * the event itself is about. 0 when nothing is at SHADOW_READY.
   */
  capabilityNodes: number;
  capabilityNodesCounted: boolean;

  // ------------------------------------------------------------ the eye
  /**
   * 1 while the presence channel's `eye.active` is current, else 0. Read from
   * the eye's own claim (never the core claim), and untouched by the voice
   * overlay: the camera being on is a fact about the room, not about the
   * assistant's turn.
   */
  eyeActive: number;

  // ---------------------------------------------------------- satellites
  /** Research evidence nodes. Exactly what the publisher counted. */
  sourceNodes: number;
  /** False when no publisher sent a count; `sourceNodes` is then 0. */
  sourceNodesKnown: boolean;
  /** Memory: how far a recall/consolidation run has converged, 0 when unknown. */
  convergence: number;
  convergenceKnown: boolean;
  /**
   * Evolution: which lab layer is under construction, 1..4 for
   * researching/designing/building/testing, 0 when the lab is not working.
   * This is an encoding of the published state, not a guess at effort.
   */
  constructionLayer: number;
  /** True only while a real candidate sits at `evolution.shadow_ready`. */
  satelliteComplete: boolean;
  /** The lab's composite score when it published one. */
  composite: number | null;

  // ------------------------------------------------------------- release
  /**
   * The owner-authorised release path, drawn on its own orbit (M18 spec §15).
   *
   * This is a SEPARATE channel from everything above: a deployment in flight
   * is not the agent's own activity, and an idle core with a release
   * deploying must show both. `"none"` draws nothing. Routine and alarm
   * stages stay on the ambient band - they are not release geometry.
   */
  releaseStage: ReleaseStage;
  /** True while a production mutation is genuinely in flight. */
  releaseInFlight: boolean;
  /** True while the owner is the thing being waited on. */
  releaseAwaitingOwner: boolean;
  /**
   * Real progress in 0..1, or `null`. The orbit is drawn whenever a release
   * stage is current; it FILLS only against a published progress figure. A
   * release of unknown length gets a ring that says so, never a bar that
   * pretends to know.
   */
  releaseProgress: number | null;

  // ---------------------------------------------------- v3: the wake surge
  /**
   * The wake alarm's own channel (M18.3 §7), drawn as a surge through the
   * structure — never as the core body.
   *
   * An alarm ringing is a fact about the room and the routine engine, not a
   * statement that the assistant is thinking or speaking: a Core that is
   * genuinely working must keep showing that work while the alarm sounds, and
   * a Core that is idle must not be dressed up as busy because a song is
   * playing. So this is a separate channel, like the release orbit, and
   * `"none"` draws nothing at all.
   */
  wakeStage: AlarmStage;
  /**
   * How strongly the surge is drawn, 0..1. A per-stage constant (an encoding
   * of the published state) that the publisher's declared ramp level may
   * raise. Zero for `armed`, for every terminal stage and for `none`: an
   * alarm that is set for the morning is a line on the strip, not a light.
   */
  wakeSurge: number;
  /** The declared ramp level while sounding, or `null` when none was sent. */
  wakeLevel: number | null;
  /** True only when the publisher actually sent a level. */
  wakeLevelKnown: boolean;

  // --------------------------------------- v4: the Digital Operator (M19 §4)
  /**
   * The published facts about the operator's current step, each `null` when
   * the publisher sent none. Set only for the three `operator.*` kinds (and
   * kept on their last-known shape, because they describe what it *was*
   * doing). They are words, not channels: nothing here moves the geometry,
   * and `label` already carries the step as the caption.
   */
  operatorStep: string | null;
  /** The step's zero-based index and the plan's length, when the publisher sent numbers instead. */
  operatorStepIndex: number | null;
  operatorStepCount: number | null;
  operatorCapability: string | null;
  /** The window title the companion OBSERVED, never the one the plan expected. */
  operatorWindow: string | null;
  /** The failure's class on `operator.failed` (`focus_mismatch`, `postcondition_failed`, …). */
  operatorErrorClass: string | null;

  // ------------------------------------ v5: File & Document Intelligence (M20 §3)
  /**
   * The published facts about the document being read — the file's name,
   * its path when the Core named it by path, the place inside it and the
   * step — each `null` when the publisher sent none, and the whole thing
   * `null` outside the `document_analysis` kind (kept on its last-known
   * shape, because it describes what WAS being read). Words, not channels:
   * nothing here moves the geometry, and `label` carries the caption.
   */
  document: DocumentFacts | null;

  // ------------------------------------------- v6: Mail & Calendar (M21 §3)
  /**
   * The published facts about the mail activity — the folder, the subject,
   * the draft's step — each `null` when the publisher sent none, and the
   * whole thing `null` outside the `mail_activity` kind (kept on its
   * last-known shape). Words, not channels.
   */
  mail: MailFacts | null;
  /** The same for the calendar: the range, the event, the proposal's step and its conflicts. */
  calendar: CalendarFacts | null;

  // -------------------------------------------- v7: the Artifact Factory (M22 §6)
  /**
   * The published facts about the file being made — the title, the format,
   * the verdict, the failing ref — each `null` when the publisher sent none,
   * and the whole thing `null` outside the `artifact_factory` kind (kept on
   * its last-known shape). Words, not channels.
   */
  artifact: ArtifactFacts | null;

  // -------------------------------------------- v8: the App Factory (M23 §6)
  /**
   * The published facts about the app being made — the project, the state,
   * the port, the test counts — each `null` when the publisher sent none,
   * and the whole thing `null` outside the `app_factory` kind (kept on its
   * last-known shape). Words, not channels.
   */
  app: AppFacts | null;

  // ------------------------------------------ v9: Capability Genesis (M24 §8)
  /**
   * The published facts about the capability being acquired — the
   * capability, the state, whether approval is required, the error class —
   * each `null` when the publisher sent none, and the whole thing `null`
   * outside the `capability_genesis` kind (kept on its last-known shape).
   * Words, not channels.
   */
  genesis: GenesisFacts | null;

  // ------------------------------------------------ v10: 3D creation (M25 §6)
  /**
   * The published facts about the scene being made — the tool, the scene,
   * the step, the object count the INSPECTION read — each `null` when the
   * publisher sent none, and the whole thing `null` outside the
   * `scene_activity` kind (kept on its last-known shape). Words, not
   * channels.
   */
  scene: SceneFacts | null;

  // ------------------------------------------ v11: Executive Autonomy (M26 §6)
  /**
   * The published facts about the run being carried — the run's id, the
   * step it is on, its state, and how many of its steps are done out of how
   * many there are — each `null` when the publisher sent none, and the
   * whole thing `null` outside the `executive_run` kind (kept on its
   * last-known shape). Words and counts, not channels: the one thing here
   * that moves anything is `progress`, and only when BOTH counts came.
   */
  executive: ExecutiveFacts | null;

  // -------------------------------- v12: the Creative Tools Operator (M27 §6)
  /**
   * The published facts about the picture being made — the application, the
   * plan operation, the step, the comparison's bounded aggregate and the
   * defect it named — each `null` when the publisher sent none, and the
   * whole thing `null` outside the `creative_activity` kind (kept on its
   * last-known shape). Words and one fraction, not channels: `similarity`
   * moves nothing here, because a comparison's score is not a progress bar
   * (ADR-0052 §2) and a run of unknown length gets no bar at all.
   */
  creative: CreativeFacts | null;

  // ------------------------ v13: the Native Application Factory (M28 §4, §6)
  /**
   * The published facts about the application being built — which
   * application, which artefact, the step, the stack the rule chose and the
   * independent reader's own word — each `null` when the publisher sent
   * none, and the whole thing `null` outside the `native_build` kind (kept
   * on its last-known shape). Words only: nothing here moves anything, and
   * in particular there is no bar — a build publishes STEPS, and a bar over
   * five of them would be an invented estimate of the kind ADR-0052 §2
   * forbids. The artefact's name, size and hash are the ROW's, never the
   * bus's.
   */
  native: NativeFacts | null;

  palette: PaletteToken;
};

/** Error motion is capped here so no future edit can turn it into a strobe. */
export const ERROR_AGITATION = 0.35;
export const ERROR_BREATH_HZ = 0.18;

/**
 * The research constellation when the publisher sent no count: a fixed,
 * restrained motif of this many nodes, labelled as a representation. It is a
 * constant so that "research is running, count unknown" always draws the same
 * figure — a motif that varied would read as a count.
 */
export const CONSTELLATION_MOTIF = 5;
/** The constellation's motion when research published no progress. */
export const CONSTELLATION_REST = 0.2;

/** The idle drift of the rings: part of the same reported-calm claim as the breath. */
export const IDLE_RING_SPIN = 0.05;

/** Half of the measured/declared energy reaches the glow. */
function glowOf(base: number, e: number): number {
  return Math.min(1, base + 0.5 * e);
}

/** A completely still, completely unknowing core. Every field starts honest. */
function blank(kind: CoreVisualKind, palette: PaletteToken): VisualIntent {
  return {
    kind,
    source: "bus",
    voiceState: null,
    state: null,
    subsystem: null,
    label: null,
    status: null,
    severity: "info",
    ageMs: null,
    intensity: null,
    progress: null,
    scale: 1,
    breathAmplitude: 0,
    breathHz: 0,
    inwardFlow: 0,
    topology: 0,
    pulse: 0,
    agitation: 0,
    restraint: 0,
    dim: 0,
    energy: 0,
    glow: 0,
    shellSpread: 0,
    ringSpin: 0,
    flowRate: 0,
    ownerVoice: 0,
    constellationNodes: 0,
    constellationDrift: 0,
    fieldNodes: 0,
    fieldNodesKnown: false,
    capabilityNodes: 0,
    capabilityNodesCounted: false,
    eyeActive: 0,
    sourceNodes: 0,
    sourceNodesKnown: false,
    convergence: 0,
    convergenceKnown: false,
    constructionLayer: 0,
    satelliteComplete: false,
    composite: null,
    releaseStage: "none",
    releaseInFlight: false,
    releaseAwaitingOwner: false,
    releaseProgress: null,
    wakeStage: "none",
    wakeSurge: 0,
    wakeLevel: null,
    wakeLevelKnown: false,
    operatorStep: null,
    operatorStepIndex: null,
    operatorStepCount: null,
    operatorCapability: null,
    operatorWindow: null,
    operatorErrorClass: null,
    document: null,
    mail: null,
    calendar: null,
    artifact: null,
    app: null,
    genesis: null,
    scene: null,
    executive: null,
    creative: null,
    native: null,
    palette,
  };
}

/**
 * The surge each alarm stage draws, before the declared level raises it.
 *
 * Only the three sounding stages light anything: `armed` is a plan, and
 * `snoozed`/`stopped`/`completed`/`failed` are the surge releasing. A failed
 * alarm is loud in words (severity `error` on the strip) and silent in
 * geometry, because failure is not activity.
 */
const WAKE_SURGE: Record<AlarmStage, number> = {
  armed: 0,
  firing: 0.65,
  playing: 0.45,
  greeting: 0.8,
  snoozed: 0,
  stopped: 0,
  completed: 0,
  failed: 0,
  none: 0,
};

/** How much of a declared ramp level reaches the surge while playing. */
const WAKE_LEVEL_GAIN = 0.55;

/** The wake channel's contribution to the intent, read from its own claim. */
function wakeFields(truth: CoreTruth, now: number): Pick<
  VisualIntent,
  "wakeStage" | "wakeSurge" | "wakeLevel" | "wakeLevelKnown"
> {
  const view = alarmView(alarmClaim(truth, now));
  // An unreachable API means we are drawing memory, not observation. The core
  // body already damps every channel there; a surge that kept pulsing would be
  // claiming an alarm is ringing NOW on the strength of a poll that failed. The
  // stage survives — the strip still names it, with its age — and the light
  // does not.
  const observing = truth.connection.kind !== "unreachable";
  const base = observing ? WAKE_SURGE[view.stage] : 0;
  const level = alarmIsSounding(view) && observing ? view.level : null;
  return {
    wakeStage: view.stage,
    // The level only ever raises a stage that is already sounding, and is
    // capped: a publisher sending 1.0 brightens the surge, it does not invent
    // one for a stage that draws nothing.
    wakeSurge: base > 0 && level !== null ? Math.min(1, base + WAKE_LEVEL_GAIN * level) : base,
    wakeLevel: level,
    wakeLevelKnown: level !== null,
  };
}

/** Only the seven release.* stages are geometry; routines and alarms are not. */
const RELEASE_GEOMETRY_STAGES: ReadonlySet<ReleaseStage> = new Set<ReleaseStage>([
  "owner_approval_required",
  "owner_authorized",
  "qualifying",
  "deploying",
  "verifying",
  "live",
  "rollback",
]);

/** The release channel's contribution to the intent, read from its own claim. */
function releaseFields(truth: CoreTruth, now: number): Pick<
  VisualIntent,
  "releaseStage" | "releaseInFlight" | "releaseAwaitingOwner" | "releaseProgress"
> {
  const view = releaseView(releaseClaim(truth, now));
  if (!RELEASE_GEOMETRY_STAGES.has(view.stage)) {
    return {
      releaseStage: "none",
      releaseInFlight: false,
      releaseAwaitingOwner: false,
      releaseProgress: null,
    };
  }
  return {
    releaseStage: view.stage,
    releaseInFlight: view.inFlight,
    releaseAwaitingOwner: view.awaitingOwner,
    releaseProgress: view.progress,
  };
}

/**
 * Real progress for this event, from the two places a publisher may put it.
 *
 * `progress` is the contract field. The self-model indexer instead reports an
 * integer `percent` in metadata (`app/selfmodel/progress.py`), which is just as
 * real, so it is read here and normalised. Nothing else is treated as progress:
 * an intensity is not a progress, and a count is not a progress.
 */
export function progressOf(event: UiStateEvent | null): number | null {
  if (!event) return null;
  if (event.progress !== null) return event.progress;
  const percent = metaNumber(event, "percent");
  if (percent === null) return null;
  return Math.max(0, Math.min(1, percent / 100));
}

/**
 * A run's progress from its COUNTED steps (M26 §6), or `null`.
 *
 * The only figure in this file derived from metadata counts, and it is a
 * count rather than an estimate: `done` and `total` are the numbers the
 * publisher wrote from the `executive_steps` rows. Both must be present —
 * "3 of an unknown number" is not a fraction — and the total must be
 * positive, because a graph with no steps has no progress to draw. Capped
 * at 1 so a publisher that ever reports `done > total` brightens nothing
 * beyond a full bar; the counts themselves are still printed verbatim
 * beside it.
 */
export function executiveProgress(facts: Pick<ExecutiveFacts, "done" | "total">): number | null {
  const { done, total } = facts;
  if (done === null || total === null || total <= 0) return null;
  return Math.min(1, done / total);
}

/** Seed the fields every event-backed intent shares. */
function fromEvent(
  kind: CoreVisualKind,
  palette: PaletteToken,
  event: UiStateEvent,
  claim: Claim,
): VisualIntent {
  return {
    ...blank(kind, palette),
    state: event.state,
    subsystem: event.subsystem,
    label: event.label,
    status: event.status,
    severity: (event.severity as Severity) ?? "info",
    ageMs: claim.ageMs,
    intensity: event.intensity,
    progress: progressOf(event),
    composite: metaNumber(event, "composite"),
  };
}

/**
 * Intensity for a channel that must not move without evidence.
 *
 * Returns 0 — a still core — rather than a plausible default when the publisher
 * sent no intensity. This is the function that keeps rule 4 true.
 */
function energy(event: UiStateEvent): number {
  return event.intensity ?? 0;
}

/** How many evidence nodes research actually counted, or "not told". */
function researchNodes(event: UiStateEvent): { count: number; known: boolean } {
  // `kept` is what survived the quality gate; `candidates` is what was seen.
  // Both are published by app/research/browser_activities.py at the ranking stage.
  const kept = metaNumber(event, "kept");
  const candidates = metaNumber(event, "candidates");
  const value = kept ?? candidates;
  if (value === null) return { count: 0, known: false };
  return { count: Math.max(0, Math.round(value)), known: true };
}

/**
 * The wider field research saw, drawn faint around what it kept.
 *
 * Only when the publisher counted BOTH: at the ranking stage it sends
 * `candidates` and `kept`, and the difference is the field. A stage that sent
 * one figure has no field to draw — `sourceNodes` already carries that one.
 */
function researchField(event: UiStateEvent): { count: number; known: boolean } {
  const kept = metaNumber(event, "kept");
  const candidates = metaNumber(event, "candidates");
  if (kept === null || candidates === null) return { count: 0, known: false };
  return { count: Math.max(0, Math.round(candidates) - Math.round(kept)), known: true };
}

/**
 * How many capability nodes SHADOW_READY parks around the core.
 *
 * `app/evolution/service.py` publishes one event per candidate with no count,
 * so the honest figure is one: the candidate this event is about. A publisher
 * that later sends a count of ready candidates is read verbatim.
 */
function capabilityCount(event: UiStateEvent): { count: number; counted: boolean } {
  const published = metaNumber(event, "ready", "candidates");
  if (published === null) return { count: 1, counted: false };
  return { count: Math.max(0, Math.round(published)), counted: true };
}

const EVOLUTION_LAYER: Record<string, number> = {
  "evolution.researching": 1,
  "evolution.designing": 2,
  "evolution.building": 3,
  "evolution.testing": 4,
};

/**
 * The visual for one live, known state.
 *
 * Split out from `visualFor` so the state→visual table can be read in one
 * screen and tested state by state.
 */
function forLiveState(event: UiStateEvent, claim: Claim): VisualIntent {
  const base = (kind: CoreVisualKind, palette: PaletteToken) =>
    fromEvent(kind, palette, event, claim);
  const e = energy(event);

  switch (event.state) {
    case "agent.idle":
      // Calm breathing: the one place a steady rhythm is honest, because
      // "nothing is running" is itself the reported truth. The rings drift at
      // the same claim's expense; nothing flows and nothing expands.
      return {
        ...base("idle", "calm"),
        breathAmplitude: 0.06,
        breathHz: 0.14,
        scale: 1,
        energy: e,
        glow: glowOf(0.15, e),
        shellSpread: 0.15,
        ringSpin: IDLE_RING_SPIN,
      };

    case "agent.listening":
      // Contracts, and draws energy inward. Depth tracks the reported level.
      // The shells close in; the rings barely turn — attention, not work.
      return {
        ...base("listening", "inward"),
        scale: 0.88 - 0.05 * e,
        inwardFlow: 0.35 + 0.65 * e,
        breathAmplitude: 0.02,
        breathHz: 0.5,
        energy: e,
        glow: glowOf(0.2, e),
        shellSpread: 0.05,
        ringSpin: 0.12,
      };

    case "agent.thinking":
      // Expands, with an active internal topology. Denser with reported load:
      // the shells stand off, the rings turn, and energy travels the paths.
      return {
        ...base("thinking", "active"),
        scale: 1.12 + 0.1 * e,
        topology: 0.4 + 0.6 * e,
        breathAmplitude: 0.03,
        breathHz: 0.35,
        energy: e,
        glow: glowOf(0.4, e),
        shellSpread: 0.55 + 0.25 * e,
        ringSpin: 0.5 + 0.5 * e,
        flowRate: 0.5 + 0.5 * e,
      };

    case "agent.speaking":
      // Pulses from the bounded energy the event carries — never from owner
      // audio, which this client neither captures nor persists. No intensity
      // means no pulse, and the glow answers to the same figure.
      return {
        ...base("speaking", "voice"),
        scale: 1.04,
        pulse: e,
        breathAmplitude: 0.02,
        breathHz: 0.45,
        energy: e,
        glow: glowOf(0.3, e),
        shellSpread: 0.3,
        ringSpin: 0.2,
        flowRate: 0.25,
      };

    case "agent.researching": {
      const nodes = researchNodes(event);
      const field = researchField(event);
      const p = progressOf(event);
      return {
        ...base("researching", "discovery"),
        scale: 1.06,
        topology: 0.2,
        breathAmplitude: 0.03,
        breathHz: 0.25,
        sourceNodes: nodes.count,
        sourceNodesKnown: nodes.known,
        energy: e,
        glow: glowOf(0.3, e),
        shellSpread: 0.4,
        ringSpin: 0.3,
        flowRate: 0.35,
        // The constellation: the count the publisher sent, or the fixed motif.
        // Its motion is published progress or the fixed rest figure — never a
        // number this file made up.
        constellationNodes: nodes.known ? nodes.count : CONSTELLATION_MOTIF,
        constellationDrift: p !== null ? CONSTELLATION_REST + 0.6 * p : CONSTELLATION_REST,
        fieldNodes: field.count,
        fieldNodesKnown: field.known,
      };
    }

    case "agent.memory_retrieval": {
      // Convergence is drawn only from a real progress figure. Absent one, the
      // core shows that memory work is running — information moving inward —
      // without claiming how far along.
      const p = progressOf(event);
      return {
        ...base("memory", "recall"),
        scale: 0.96,
        inwardFlow: 0.4,
        convergence: p ?? 0,
        convergenceKnown: p !== null,
        breathAmplitude: 0.03,
        breathHz: 0.3,
        energy: e,
        glow: glowOf(0.3, e),
        shellSpread: 0.2,
        ringSpin: 0.25,
        flowRate: 0.3,
      };
    }

    case "agent.tool_running":
      // A capability at work: the paths carry the most traffic of any state,
      // the shells open, the rings turn.
      return {
        ...base("tool_running", "work"),
        scale: 1.02,
        topology: 0.25,
        breathAmplitude: 0.025,
        breathHz: 0.4,
        energy: e,
        glow: glowOf(0.35, e),
        shellSpread: 0.45,
        ringSpin: 0.4,
        flowRate: 0.6,
      };

    case "agent.waiting_owner":
      // Restrained: the system is deliberately not working. Showing busy motion
      // here would be the exact lie ADR-0052 exists to prevent. The rings all
      // but stop; the shells sit close; nothing flows.
      return {
        ...base("waiting_owner", "held"),
        scale: 0.94,
        restraint: 1,
        breathAmplitude: 0.015,
        breathHz: 0.09,
        energy: e,
        glow: glowOf(0.12, e),
        shellSpread: 0.1,
        ringSpin: 0.02,
      };

    case "agent.goal_completed":
      return {
        ...base("goal_completed", "achieved"),
        scale: 1.1,
        breathAmplitude: 0.04,
        breathHz: 0.2,
        energy: e,
        glow: glowOf(0.6, e),
        shellSpread: 0.6,
        ringSpin: 0.15,
      };

    case "agent.error":
      // Controlled, slow, bounded. Severity may deepen the colour; it may not
      // speed anything up.
      return {
        ...base("error", "fault"),
        scale: 0.98,
        agitation: ERROR_AGITATION,
        breathAmplitude: 0.02,
        breathHz: ERROR_BREATH_HZ,
        energy: e,
        glow: glowOf(0.25, e),
        shellSpread: 0.25,
        ringSpin: 0.08,
      };

    case "evolution.shadow_ready": {
      // A finished satellite, parked. Explicitly NOT live — the core itself is
      // unchanged, because nothing about production changed. The capability
      // nodes are the candidates the lab said are ready, and nothing more.
      const capabilities = capabilityCount(event);
      return {
        ...base("shadow_ready", "ready"),
        scale: 1,
        breathAmplitude: 0.05,
        breathHz: 0.12,
        satelliteComplete: true,
        energy: e,
        glow: glowOf(0.2, e),
        shellSpread: 0.2,
        ringSpin: IDLE_RING_SPIN,
        capabilityNodes: capabilities.count,
        capabilityNodesCounted: capabilities.counted,
      };
    }

    case "evolution.researching":
    case "evolution.designing":
    case "evolution.building":
    case "evolution.testing":
      return {
        ...base("evolution_working", "lab"),
        scale: 1,
        breathAmplitude: 0.03,
        breathHz: 0.22,
        constructionLayer: EVOLUTION_LAYER[event.state] ?? 0,
        energy: e,
        glow: glowOf(0.25, e),
        shellSpread: 0.3,
        ringSpin: 0.2,
        flowRate: 0.2,
      };

    case "operator.running": {
      // A hand on the owner's desktop (M19 §4): the tool-execution posture —
      // the paths carry the most traffic of any state, the shells open, the
      // rings turn — a shade more open than a plain tool because a step is
      // being acted in the owner's own session. The caption is the step the
      // planner named; without one, the publisher's label; never a guess.
      const facts = operatorFacts(event);
      return {
        ...base("operator_running", "work"),
        label: operatorCaption(event, facts),
        scale: 1.02,
        topology: 0.3,
        breathAmplitude: 0.025,
        breathHz: 0.4,
        energy: e,
        glow: glowOf(0.35, e),
        shellSpread: 0.5,
        ringSpin: 0.45,
        flowRate: 0.6,
        operatorStep: facts.step,
        operatorStepIndex: facts.stepIndex,
        operatorStepCount: facts.stepCount,
        operatorCapability: facts.capability,
        operatorWindow: facts.windowTitle,
      };
    }

    case "operator.verifying": {
      // The second OBSERVE: the result is coming back in to be checked against
      // the postcondition. Still the working posture, but the flow turns
      // inward and the traffic on the paths eases — verification is reading,
      // not acting.
      const facts = operatorFacts(event);
      return {
        ...base("operator_verifying", "work"),
        label: operatorCaption(event, facts),
        scale: 1,
        topology: 0.3,
        inwardFlow: 0.35,
        breathAmplitude: 0.025,
        breathHz: 0.4,
        energy: e,
        glow: glowOf(0.3, e),
        shellSpread: 0.4,
        ringSpin: 0.3,
        flowRate: 0.3,
        operatorStep: facts.step,
        operatorStepIndex: facts.stepIndex,
        operatorStepCount: facts.stepCount,
        operatorCapability: facts.capability,
        operatorWindow: facts.windowTitle,
      };
    }

    case "operator.failed": {
      // The failure posture, with the operator's own restraint: a step's
      // postcondition did not hold (or the focus guard refused), so the task
      // stopped acting. Same bounded, slow agitation as `agent.error`; nothing
      // flows, because failure is not activity. The error class is a published
      // token and is shown as one; the caption stays the step it was on.
      const facts = operatorFacts(event);
      return {
        ...base("operator_failed", "fault"),
        label: operatorCaption(event, facts),
        scale: 0.98,
        agitation: ERROR_AGITATION,
        restraint: 0.5,
        breathAmplitude: 0.02,
        breathHz: ERROR_BREATH_HZ,
        energy: e,
        glow: glowOf(0.22, e),
        shellSpread: 0.2,
        ringSpin: 0.05,
        operatorStep: facts.step,
        operatorStepIndex: facts.stepIndex,
        operatorStepCount: facts.stepCount,
        operatorCapability: facts.capability,
        operatorWindow: facts.windowTitle,
        operatorErrorClass: facts.errorClass,
      };
    }

    case "document.analysis": {
      // The reading posture (M20 §3): the same visual language as a tool at
      // work, turned inward and calmed — text is coming IN to be read, so the
      // inward flow is the one channel that moves with intent; the shells sit
      // closer than a plain tool's and the rings turn slowly. Deliberately no
      // pulse, no constellation and no progress: a document of unknown length
      // gets no bar, and nothing here could honestly say how far along the
      // read is. The caption is the published file and place, else the bare
      // statement that a document is being read.
      const facts = documentFacts(event);
      return {
        ...base("document_analysis", "reading"),
        label: documentCaption(facts),
        scale: 1,
        topology: 0.15,
        inwardFlow: 0.3,
        breathAmplitude: 0.03,
        breathHz: 0.25,
        energy: e,
        glow: glowOf(0.28, e),
        shellSpread: 0.25,
        ringSpin: 0.2,
        flowRate: 0.2,
        document: facts,
      };
    }

    case "mail.activity": {
      // The reading posture once more (M21 §3), a shade calmer than the
      // document's: mail is read a message at a time, and a draft in hand is
      // held, not worked. Information comes in — the inward flow is the one
      // channel that moves with intent — the shells sit close, the rings turn
      // slowly, nothing pulses and nothing is drawn as progress. The caption
      // is the draft's step or the folder being read, then the subject; a
      // draft waiting on the owner is worded as waiting, never as busy.
      const facts = mailFacts(event);
      return {
        ...base("mail_activity", "reading"),
        label: mailCaption(facts),
        scale: 1,
        topology: 0.1,
        inwardFlow: 0.25,
        breathAmplitude: 0.03,
        breathHz: 0.22,
        energy: e,
        glow: glowOf(0.26, e),
        shellSpread: 0.22,
        ringSpin: 0.15,
        flowRate: 0.15,
        mail: facts,
      };
    }

    case "calendar.activity": {
      // The planning posture (M21 §3): the structure laid a little open and
      // held there — a day being arranged — with a faint lattice for the
      // grid of it, the rings barely turning, and NO inward flow: planning
      // arranges what is already known rather than taking anything in. No
      // pulse, no constellation, no progress: a proposal with two conflicts
      // is two conflicts in words, not a bar. The caption is the proposal's
      // step with its counted conflicts, or the range being read.
      const facts = calendarFacts(event);
      return {
        ...base("calendar_activity", "planning"),
        label: calendarCaption(facts),
        scale: 1.02,
        topology: 0.2,
        breathAmplitude: 0.03,
        breathHz: 0.2,
        energy: e,
        glow: glowOf(0.26, e),
        shellSpread: 0.35,
        ringSpin: 0.12,
        flowRate: 0.1,
        calendar: facts,
      };
    }

    case "artifact.factory": {
      // The making posture (M22 §6): a file is being written OUT for the
      // owner, so — unlike every reading kind — nothing flows inward; the
      // paths carry traffic while the factory renders, a faint lattice is
      // the structure being laid, the shells sit a little open and the
      // rings turn slowly. Once the publisher named a verdict the writing
      // is over and the paths go still: `valid` glows a shade brighter (the
      // parser found what was asked), `invalid` is held under restraint
      // (kept, named, not presented as done) with no agitation — a failed
      // validation is a fact about a file, not a fault in the agent. No
      // pulse, no constellation, no progress: a render of unknown length
      // gets no bar. The caption is the title, the format and the verdict.
      const facts = artifactFacts(event);
      const verdict = facts.verdict;
      const settled = verdict === "valid" || verdict === "invalid";
      return {
        ...base("artifact_factory", "making"),
        label: artifactCaption(facts),
        scale: 1.04,
        topology: 0.2,
        breathAmplitude: 0.03,
        breathHz: 0.24,
        energy: e,
        glow: glowOf(verdict === "valid" ? 0.4 : verdict === "invalid" ? 0.22 : 0.3, e),
        shellSpread: 0.3,
        ringSpin: settled ? IDLE_RING_SPIN : 0.18,
        flowRate: settled ? 0 : 0.3,
        restraint: verdict === "invalid" ? 0.5 : 0,
        artifact: facts,
      };
    }

    case "app.factory": {
      // The building posture (M23 §6) while a project is planned or
      // scaffolded: the making posture's shape — a faint lattice being laid,
      // the paths carrying files OUT to the owner's machine, nothing flowing
      // inward, the rings turning slowly. A `running` app is a distinct
      // posture: the lattice STANDS (a program's structure, not a page
      // being laid), the shells open a little more, the glow is a shade
      // brighter — and the paths carry steady traffic only when the
      // publisher named a port, because a server answering at an address is
      // what that traffic would mean; a `running` with no port keeps the
      // shape and the glow, and its paths are still. `tested` is still and
      // bright (the project's own tests ran); `stopped` is still and dim;
      // `failed` is held under restraint with no agitation — a failed test
      // is a fact about a program, not a fault in the agent. A state this
      // build cannot read, or none at all, is the building posture. No
      // pulse, no constellation, no progress: a scaffold of unknown length
      // gets no bar. The caption is the project, the state, the port and the
      // counts, each as published.
      const facts = appFacts(event);
      const state = facts.state;
      const running = state === "running";
      const serving = appIsServing(facts);
      const settled = state === "tested" || state === "failed" || state === "stopped";
      const glowBase = running ? 0.42 : state === "tested" ? 0.4 : state === "failed" ? 0.22 : state === "stopped" ? 0.18 : 0.3;
      return {
        ...base("app_factory", "making"),
        label: appCaption(facts),
        scale: running ? 1.06 : 1.04,
        topology: running ? 0.35 : settled ? 0.15 : 0.2,
        breathAmplitude: 0.03,
        breathHz: running ? 0.2 : 0.24,
        energy: e,
        glow: glowOf(glowBase, e),
        shellSpread: running ? 0.4 : settled ? 0.2 : 0.3,
        ringSpin: running ? 0.25 : settled ? IDLE_RING_SPIN : 0.18,
        flowRate: serving ? 0.35 : running || settled ? 0 : 0.3,
        restraint: state === "failed" ? 0.5 : 0,
        app: facts,
      };
    }

    case "capability.genesis": {
      // Three postures and a failure (M24 §8), each from the published state
      // alone. BUILDING (`capability_missing` through `registering`): the
      // making posture's shape — a faint lattice being laid, the paths
      // carrying an adapter OUT toward the application, nothing flowing
      // inward, the rings turning slowly. WAITING (`awaiting_approval`): the
      // owner is the thing being waited on, so the Core is held exactly as
      // `agent.waiting_owner` holds it — full restraint, the rings all but
      // stopped, the shells close, nothing flows, the held palette — because
      // busy motion under "onay bekliyor" would be the lie ADR-0052 exists to
      // prevent. SETTLED (`available`, `used`, `verified`): still and bright
      // in the ready palette, `verified` a shade brighter — the read-back
      // held. FAILED: held under restraint with no agitation, dim — a failed
      // run is a fact about an adapter, not a fault in the agent, and the
      // gap stays open. A state this build cannot read, or none at all, is
      // the building posture with the bare caption. No pulse, no
      // constellation, no candidate nodes and no progress: a run of unknown
      // length gets no bar, and the M7 lab's own satellites are the lab's.
      const facts = genesisFacts(event);
      const posture = genesisPosture(facts.state);
      const waiting = posture === "waiting";
      const settled = posture === "settled";
      const failed = posture === "failed";
      const still = waiting || settled || failed;
      const palette: PaletteToken = waiting ? "held" : settled ? "ready" : "making";
      const glowBase = facts.state === "verified" ? 0.42 : settled ? 0.38 : failed ? 0.22 : waiting ? 0.12 : 0.3;
      return {
        ...base("capability_genesis", palette),
        label: genesisCaption(facts),
        scale: waiting ? 0.94 : settled ? 1.02 : 1.04,
        topology: waiting ? 0 : settled || failed ? 0.15 : 0.2,
        breathAmplitude: waiting ? 0.015 : 0.03,
        breathHz: waiting ? 0.09 : settled ? 0.14 : 0.24,
        energy: e,
        glow: glowOf(glowBase, e),
        shellSpread: waiting ? 0.1 : settled || failed ? 0.2 : 0.3,
        ringSpin: waiting ? 0.02 : settled || failed ? IDLE_RING_SPIN : 0.18,
        flowRate: still ? 0 : 0.3,
        restraint: waiting ? 1 : failed ? 0.5 : 0,
        genesis: facts,
      };
    }

    case "scene.activity": {
      // Seven postures (M25 §6), each from the published step alone. MAKING
      // (`creating`, `applying`): the app factory's shape — a lattice being
      // laid, the paths carrying a plan OUT to the editor on the owner's
      // machine, nothing flowing inward. RENDERING: its own posture, because
      // writing an image is not building the thing in it — the lattice
      // STANDS, the rings all but stop, and the paths carry a single steady
      // stream out; a still is one long write, not a structure taking shape.
      // READING (`inspecting`): the one step in this family that draws
      // INWARD, on the document Core's shape — the tool is being asked what
      // it holds, and the answer is what every claim downstream rests on.
      // VERIFIED: still and bright in the ready palette — the read-back
      // matched. UNVERIFIED: just as still, because the run is over, but in
      // the plain making palette with no brightness — the plan asked for
      // nothing checkable, so there is nothing to be bright about and
      // nothing to hold under restraint either. MISMATCH: held under
      // restraint, named, never rounded up to done. UNAVAILABLE: settled and
      // dim under restraint with NO agitation
      // and no error palette at all — an editor that cannot be driven for
      // want of a licence is a fact about a licence (ADR-0088 §5), and
      // drawing it as a fault would be the Core telling the owner something
      // broke. FAILED: the same restraint. A step this build cannot read, or
      // none at all, is the making posture with the bare caption. No pulse,
      // no constellation and no progress: a render of unknown length gets no
      // bar.
      const facts = sceneFacts(event);
      const posture = scenePosture(facts.state);
      const rendering = posture === "rendering";
      const reading = posture === "reading";
      const verified = posture === "verified";
      const unverified = posture === "unverified";
      const mismatch = posture === "mismatch";
      const unavailable = posture === "unavailable";
      const failed = posture === "failed";
      const still = verified || unverified || mismatch || unavailable || failed;
      const palette: PaletteToken = unavailable ? "held" : verified ? "ready" : reading ? "reading" : "making";
      const glowBase = verified
        ? 0.42
        : rendering
          ? 0.36
          : mismatch
            ? 0.22
            : failed
              ? 0.22
              : unavailable
                ? 0.12
                : unverified
                  ? 0.2
                  : 0.3;
      return {
        ...base("scene_activity", palette),
        label: sceneCaption(facts),
        scale: unavailable ? 0.96 : rendering ? 1.05 : still ? 1.02 : 1.04,
        topology: reading ? 0.1 : unavailable ? 0 : rendering ? 0.3 : still ? 0.15 : 0.2,
        breathAmplitude: unavailable ? 0.015 : 0.03,
        breathHz: unavailable ? 0.1 : rendering ? 0.18 : verified || unverified ? 0.14 : 0.24,
        energy: e,
        glow: glowOf(glowBase, e),
        inwardFlow: reading ? 0.3 : 0,
        shellSpread: unavailable ? 0.1 : rendering ? 0.35 : still ? 0.2 : 0.3,
        ringSpin: unavailable ? 0.02 : rendering ? 0.04 : still ? IDLE_RING_SPIN : 0.18,
        flowRate: rendering ? 0.4 : still ? 0 : reading ? 0.15 : 0.3,
        restraint: unavailable ? 0.6 : mismatch || failed ? 0.5 : 0,
        scene: facts,
      };
    }

    case "executive.run": {
      // Seven postures (M26 §6), each from the published state alone.
      // PLANNED: the graph exists and nothing has run — the making shape,
      // still, with nothing flowing anywhere yet, because a plan is not
      // work in flight. RUNNING: the app factory's shape — a lattice being
      // laid, the paths carrying steps OUT to the families that do them,
      // nothing flowing inward. PAUSED: held exactly as `agent.waiting_owner`
      // holds the Core — full restraint, the rings all but stopped, the
      // shells close, nothing flowing, the held palette — because the owner
      // said "Bekle", and busy motion under "duraklatıldı" would be the lie
      // ADR-0052 exists to prevent; it is NOT an error and draws no
      // agitation. COMPLETED: still and bright in the ready palette, the one
      // posture the word "tamamlandı" reaches. PARTIAL: settled and held
      // under restraint — part of the job exists and part of it does not,
      // and the caption names which; never rounded up to completed and
      // never dressed as a failure. CANCELLED: settled and dim, with
      // neither restraint nor alarm — the owner's own decision. FAILED:
      // held under restraint, no agitation. A state this build cannot
      // read, or none at all, is the RUNNING posture with the bare caption:
      // a run exists and nothing settled was said about it.
      //
      // The one bar in this family, and the only one drawn from metadata
      // besides the self-model's `percent`: `done`/`total` are counted
      // steps the publisher sent, so a bar from them is a count rather than
      // an estimate — and it appears ONLY when both came and the total is
      // positive. A `done` alone draws nothing (ADR-0052 §2: work of
      // unknown length gets no bar).
      const facts = executiveFacts(event);
      const posture = executivePosture(facts.state);
      const planned = posture === "planned";
      const running = posture === "running";
      const paused = posture === "paused";
      const completed = posture === "completed";
      const partial = posture === "partial";
      const cancelled = posture === "cancelled";
      const failed = posture === "failed";
      const settled = completed || partial || cancelled || failed;
      const still = planned || paused || settled;
      // `held` covers the two stopped states and only those: the owner
      // paused this run, or the owner ended it. Neither is a fault palette,
      // and neither is the ready one — nothing was finished either.
      const palette: PaletteToken = paused || cancelled ? "held" : completed ? "ready" : "making";
      const glowBase = completed ? 0.42 : partial ? 0.26 : failed ? 0.22 : cancelled ? 0.16 : paused ? 0.12 : planned ? 0.2 : 0.3;
      return {
        ...base("executive_run", palette),
        label: executiveCaption(facts),
        progress: executiveProgress(facts) ?? progressOf(event),
        scale: paused ? 0.94 : completed ? 1.02 : running ? 1.05 : 1.02,
        topology: paused ? 0 : running ? 0.25 : planned ? 0.15 : 0.15,
        breathAmplitude: paused ? 0.015 : 0.03,
        breathHz: paused ? 0.09 : completed ? 0.14 : running ? 0.22 : 0.16,
        energy: e,
        glow: glowOf(glowBase, e),
        shellSpread: paused ? 0.1 : running ? 0.35 : 0.2,
        ringSpin: paused ? 0.02 : running ? 0.2 : IDLE_RING_SPIN,
        flowRate: still ? 0 : 0.3,
        restraint: paused ? 1 : partial || failed ? 0.5 : 0,
        executive: facts,
      };
    }

    case "creative.activity": {
      // Eleven postures (M27 §3, §6), each from the published step alone.
      // READING (`analysing`, `inspecting`): the document Core's shape,
      // drawing INWARD — an image is being read, either the owner's input
      // before the plan or the output after it, and every claim downstream
      // rests on what was read. PLANNING: still, in the planning palette,
      // with nothing flowing anywhere — a plan is data, and no file has been
      // touched yet. MAKING (`executing`): the app factory's shape, a
      // lattice being laid with the paths carrying the plan OUT to the
      // provider. EXPORTING: the scene family's rendering posture, and for
      // its reason — the lattice stands and one steady stream goes out,
      // because writing a file in a format is one long write rather than a
      // structure taking shape. COMPARING: inward again but bright, because
      // this is the step every "doğrulandı" in this family comes from.
      // CORRECTING: the making shape held at half restraint — work is
      // running, and something has already been found wrong; drawing it
      // exactly like a first pass would hide that. VERIFIED: still and
      // bright in the ready palette, and reachable from that ONE word.
      // UNVERIFIED: just as still, in the plain making palette with no
      // brightness — nothing was asked that could be measured, so there is
      // nothing to be bright about and nothing to hold under restraint
      // either. MISMATCH: held under restraint and named. UNAVAILABLE:
      // settled and dim under restraint with NO agitation and no error
      // palette at all — an application that is not installed is a fact
      // about this machine (ADR-0093 decision 3), and drawing it as a fault
      // would be the Core telling the owner something broke. FAILED: the
      // same restraint. A step this build cannot read, or none at all, is
      // the making posture with the bare caption. No pulse, no constellation
      // and no progress: `similarity` is a comparison's score, not a
      // fraction of the work done, and a bar from it would be an invented
      // claim of the kind ADR-0052 §2 forbids.
      const facts = creativeFacts(event);
      const posture = creativePosture(facts.state);
      const reading = posture === "reading";
      const planning = posture === "planning";
      const exporting = posture === "exporting";
      const comparing = posture === "comparing";
      const correcting = posture === "correcting";
      const verified = posture === "verified";
      const unverified = posture === "unverified";
      const mismatch = posture === "mismatch";
      const unavailable = posture === "unavailable";
      const failed = posture === "failed";
      const still = planning || verified || unverified || mismatch || unavailable || failed;
      const palette: PaletteToken = unavailable
        ? "held"
        : verified
          ? "ready"
          : reading || comparing
            ? "reading"
            : planning
              ? "planning"
              : "making";
      const glowBase = verified
        ? 0.42
        : comparing
          ? 0.34
          : exporting
            ? 0.36
            : mismatch
              ? 0.22
              : failed
                ? 0.22
                : unavailable
                  ? 0.12
                  : unverified
                    ? 0.2
                    : planning
                      ? 0.24
                      : 0.3;
      return {
        ...base("creative_activity", palette),
        label: creativeCaption(facts),
        scale: unavailable ? 0.96 : exporting ? 1.05 : still ? 1.02 : 1.04,
        topology: reading || comparing ? 0.1 : unavailable ? 0 : exporting ? 0.3 : still ? 0.15 : 0.2,
        breathAmplitude: unavailable ? 0.015 : 0.03,
        breathHz: unavailable ? 0.1 : exporting ? 0.18 : verified || unverified || planning ? 0.14 : 0.24,
        energy: e,
        glow: glowOf(glowBase, e),
        inwardFlow: reading ? 0.3 : comparing ? 0.2 : 0,
        shellSpread: unavailable ? 0.1 : exporting ? 0.35 : still ? 0.2 : 0.3,
        ringSpin: unavailable ? 0.02 : exporting ? 0.04 : still ? IDLE_RING_SPIN : 0.18,
        flowRate: exporting ? 0.4 : still ? 0 : reading || comparing ? 0.15 : 0.3,
        restraint: unavailable ? 0.6 : mismatch || failed ? 0.5 : correcting ? 0.5 : 0,
        creative: facts,
      };
    }

    case "native.build": {
      // Nine postures (M28 §4, §6), each from the published step alone.
      // PLANNING: the spec is accepted and the stack chosen, and NOTHING has
      // been written — still, in the planning palette, with nothing flowing
      // anywhere, because a decision is not a build. MAKING (`generating`,
      // `building`, `packaging`): the app factory's shape, a lattice being
      // laid with the paths carrying work OUT to the toolchain that does it.
      // TESTING: its own posture at a lower flow, because the generated
      // project's own tests are the first thing that can disagree with what
      // was asked, and drawing that exactly like compiling would hide it.
      // READING (`validating`): the document Core's shape, drawing INWARD —
      // a reader that did not build the file is opening it, and every
      // "doğrulandı" in this family comes from what that reader saw.
      // VERIFIED: still and bright in the ready palette, reachable from that
      // ONE word — not from a compiler that exited 0 and not from a file
      // that exists. UNVERIFIED: just as still, in the plain making palette
      // with no brightness — the artefact is there and nothing could check
      // it, so there is nothing to be bright about and nothing to hold under
      // restraint either. MISMATCH: held under restraint and named.
      // UNAVAILABLE: settled and dim under restraint with NO agitation and
      // no fault palette at all — a machine with no JDK is a fact about the
      // world (ADR-0095 decision 3), and drawing it as a failure would be
      // the Core telling the owner something broke. FAILED: the same
      // restraint. A step this build cannot read, or none at all, is the
      // making posture with the bare caption.
      //
      // No progress bar anywhere in this family. A build publishes STEPS,
      // and five of eleven words being "work" does not make a fraction: a
      // bar drawn from step positions would be an estimate of how long a
      // compiler will take, which is exactly the invented claim ADR-0052 §2
      // forbids.
      const facts = nativeFacts(event);
      const posture = nativePosture(facts.state);
      const planning = posture === "planning";
      const testing = posture === "testing";
      const reading = posture === "reading";
      const verified = posture === "verified";
      const unverified = posture === "unverified";
      const mismatch = posture === "mismatch";
      const unavailable = posture === "unavailable";
      const failed = posture === "failed";
      const still = planning || verified || unverified || mismatch || unavailable || failed;
      const palette: PaletteToken = unavailable
        ? "held"
        : verified
          ? "ready"
          : reading
            ? "reading"
            : planning
              ? "planning"
              : "making";
      const glowBase = verified
        ? 0.42
        : reading
          ? 0.34
          : mismatch
            ? 0.22
            : failed
              ? 0.22
              : unavailable
                ? 0.12
                : unverified
                  ? 0.2
                  : planning
                    ? 0.24
                    : 0.3;
      return {
        ...base("native_build", palette),
        label: nativeCaption(facts),
        scale: unavailable ? 0.96 : still ? 1.02 : 1.04,
        topology: reading ? 0.1 : unavailable ? 0 : still ? 0.15 : testing ? 0.2 : 0.25,
        breathAmplitude: unavailable ? 0.015 : 0.03,
        breathHz: unavailable ? 0.1 : verified || unverified || planning ? 0.14 : 0.22,
        energy: e,
        glow: glowOf(glowBase, e),
        inwardFlow: reading ? 0.3 : 0,
        shellSpread: unavailable ? 0.1 : still ? 0.2 : 0.3,
        ringSpin: unavailable ? 0.02 : still ? IDLE_RING_SPIN : 0.18,
        flowRate: still ? 0 : reading ? 0.15 : testing ? 0.2 : 0.3,
        restraint: unavailable ? 0.6 : mismatch || failed ? 0.5 : 0,
        native: facts,
      };
    }

    default:
      // Reached only by a contract state this table has not been taught. Both
      // gates upstream (`isKnownState`, and `coreClaim`'s agent/lab filter)
      // normally keep it empty; a state added to the contract and forgotten
      // here must still land somewhere honest, so it degrades to the explicit
      // unknown presentation rather than to a default animation.
      return { ...base("unknown_state", "unknown"), dim: 0.4 };
  }
}

/**
 * The whole mapping: what the client knows → what the core should look like.
 *
 * Order matters. Connection problems outrank state, and an expired claim
 * outranks the state it expired from, because in both cases the honest answer
 * is about our knowledge rather than about the agent.
 */
export function visualFor(truth: CoreTruth, now: number, voice: VoiceOverlay | null = null): VisualIntent {
  if (truth.connection.kind === "unauthorized") {
    // Nothing known, so nothing drawn - on either channel. A refused session
    // is refused for the voice leg too, so no overlay applies here either.
    return { ...blank("unauthorized", "unknown"), dim: 0.7 };
  }
  // The release orbit is independent of what the core body is doing: an idle
  // core with a deployment in flight shows both, and neither hides the other.
  // The eye likewise: the aperture is drawn from the camera's own claim.
  const bus = {
    ...coreVisual(truth, now),
    ...releaseFields(truth, now),
    ...wakeFields(truth, now),
    eyeActive: eyeActiveOf(truth, now),
  };
  return voice ? applyVoiceOverlay(bus, voice) : bus;
}

/**
 * 1 while `eye.active` is the current, unexpired claim on the eye's channel.
 *
 * Read through `eyeView` so the aperture agrees with the ambient band on every
 * render: an `eye.*` state this build cannot read is `untold` there and 0
 * here — never drawn as active on the strength of a token it did not know.
 */
function eyeActiveOf(truth: CoreTruth, now: number): number {
  const view = eyeView(eyeClaim(truth, now));
  return view.status === "active" && !view.expired ? 1 : 0;
}

// ------------------------------------------------------------ voice overlay

/**
 * What this tab's own voice session reports (ADR-0061 §4).
 *
 * Every field is a fact the `VoiceSessionController` holds or a measurement
 * the audio path took. Nothing here is a bus event and nothing is published
 * anywhere: the overlay is drawn from the local controller and labelled as
 * such, which is the difference between "reporting what this device is in"
 * and "fabricating an `agent.speaking` nobody sent".
 */
export type VoiceOverlay = {
  state: VoiceUiState;
  /** Owner microphone level 0..1 from the local gate; `null` when not measured. */
  micLevel: number | null;
  /**
   * The assistant's REAL output envelope 0..1 from the playback analyser;
   * `null` when the path cannot be measured. Sampled at animation rate by the
   * page; 0 the moment playback stops. Never a synthesised rhythm.
   */
  outputLevel: number | null;
  /** The short semantic caption while speaking (a tool, a cursor), or `null`. */
  caption: string | null;
  /** The running tool's Turkish label while `tool_running`, or `null`. */
  toolLabel: string | null;
  /** The controller's last error text while `error`, or `null`. */
  lastError: string | null;
};

/** The voice controller's states that draw the core; `idle`/`closed` do not. */
export const VOICE_OVERLAY_STATES: ReadonlySet<VoiceUiState> = new Set<VoiceUiState>([
  "creating",
  "connecting",
  "reconnecting",
  "listening",
  "speaking",
  "tool_running",
  "interrupted",
  "error",
]);

/** True when the overlay would replace the bus body. */
export function voiceOverlayApplies(voice: VoiceOverlay | null): boolean {
  return voice !== null && VOICE_OVERLAY_STATES.has(voice.state);
}

/**
 * Replace the core body with the local voice session's state, keeping the
 * release orbit (a separate channel) from the bus intent.
 *
 * The state table mirrors `forLiveState` for the states the two share, so a
 * bus `agent.listening` and a local `listening` are drawn identically — the
 * only differences are the `source` and that the scalars here are
 * *measurements* (`micLevel`, `outputLevel`) rather than a publisher's
 * declared `intensity`. No local state is synthesised: `idle` and `closed`
 * return the bus intent untouched, because a closed voice leg says nothing
 * about what the agent is doing elsewhere.
 */
export function applyVoiceOverlay(bus: VisualIntent, voice: VoiceOverlay): VisualIntent {
  if (!VOICE_OVERLAY_STATES.has(voice.state)) return bus;
  // The voice session's `tool_running` and the bus's `operator.running` are
  // two accounts of the SAME moment: the tool that is running is the operator
  // tool, and the bus knows which step and which window. The more specific of
  // two true statements is the one to draw (M19 §4). Only a LIVE operator
  // body earns this — a last-known operator shape yields to the local
  // observation like every other bus state does. v5 extends the same rule to
  // a live reading Core: a spoken "bunu özetle" runs a document tool, and the
  // bus knows which file and which page (M20 §3). v6 extends it to mail and
  // the calendar: "gelen kutumu oku" runs a mail tool, and the bus knows the
  // folder and the draft (M21 §3). v7 extends it to the factory: "bana bir
  // bütçe tablosu yap" runs `artifact.create`, and the bus knows the title
  // and the verdict (M22 §6). v8 extends it to the App Factory: "uygulamayı
  // çalıştır" runs `app.run`, and the bus knows the project and the port
  // (M23 §6). v9 extends it to Capability Genesis: "sayaç kutusunu bir
  // artır" runs `capability.request`, and the bus knows the capability and
  // the run's state (M24 §6, §8). v10 extends it to 3D creation: "render
  // al" runs `scene.render`, and the bus knows the tool, the scene and the
  // step (M25 §5, §6). v11 extends it to Executive Autonomy: "Ne
  // yapıyorsun?" and the start of a run go through the same router, and the
  // bus knows the run, the step and how far along it is (M26 §5, §6). v12
  // extends it to the Creative Tools Operator: "Bu resmi Paint'te yeniden
  // çiz" runs a creative tool through the ONE router, and the bus knows the
  // application, the operation, the step and what the comparison measured
  // (M27 §5, §6) — where the local leg knows only that some tool is running.
  if (
    voice.state === "tool_running" &&
    (isOperatorActing(bus) ||
      isDocumentReading(bus) ||
      isMailReading(bus) ||
      isCalendarPlanning(bus) ||
      isArtifactMaking(bus) ||
      isAppBuilding(bus) ||
      isGenesisWorking(bus) ||
      isSceneWorking(bus) ||
      isExecutiveRunning(bus) ||
      isCreativeWorking(bus))
  )
    return bus;
  const local = (kind: CoreVisualKind, palette: PaletteToken): VisualIntent => ({
    ...blank(kind, palette),
    source: "voice",
    voiceState: voice.state,
    subsystem: "voice",
    // The orbit is the bus's channel and stays exactly as the bus drew it.
    releaseStage: bus.releaseStage,
    releaseInFlight: bus.releaseInFlight,
    releaseAwaitingOwner: bus.releaseAwaitingOwner,
    releaseProgress: bus.releaseProgress,
    // So is the eye: a fact about the room, not about this turn.
    eyeActive: bus.eyeActive,
    // And so is the alarm. A wake surge under a live voice session is two true
    // things at once, and the owner is shown both (M18.3 §7).
    wakeStage: bus.wakeStage,
    wakeSurge: bus.wakeSurge,
    wakeLevel: bus.wakeLevel,
    wakeLevelKnown: bus.wakeLevelKnown,
  });

  switch (voice.state) {
    case "creating":
    case "connecting":
    case "reconnecting":
      // A leg being opened: dimmed and still, like the bus's own connecting.
      return { ...local("connecting", "unknown"), dim: 0.5 };

    case "listening": {
      // Same geometry as `agent.listening`; depth from the measured level,
      // and a still 0.35 when no measurement exists (the gate not yet running).
      // `ownerVoice` is that measurement and nothing else: above zero, the
      // owner is speaking and the pull inward is drawn at their level.
      const e = voice.micLevel ?? 0;
      return {
        ...local("listening", "inward"),
        intensity: voice.micLevel,
        scale: 0.88 - 0.05 * e,
        inwardFlow: 0.35 + 0.65 * e,
        breathAmplitude: 0.02,
        breathHz: 0.5,
        energy: e,
        glow: glowOf(0.2, e),
        shellSpread: 0.05,
        ringSpin: 0.12,
        ownerVoice: e,
      };
    }

    case "tool_running":
      return {
        ...local("tool_running", "work"),
        label: voice.toolLabel,
        scale: 1.02,
        topology: 0.25,
        breathAmplitude: 0.025,
        breathHz: 0.4,
        glow: glowOf(0.35, 0),
        shellSpread: 0.45,
        ringSpin: 0.4,
        flowRate: 0.6,
      };

    case "speaking": {
      // The pulse IS the output envelope. `null` (unmeasurable) draws no
      // pulse and `intensity` stays null so the readout can say so. The glow
      // answers to the same measurement.
      const e = voice.outputLevel ?? 0;
      return {
        ...local("speaking", "voice"),
        intensity: voice.outputLevel,
        label: voice.caption,
        scale: 1.04,
        pulse: e,
        breathAmplitude: 0.02,
        breathHz: 0.45,
        energy: e,
        glow: glowOf(0.3, e),
        shellSpread: 0.3,
        ringSpin: 0.2,
        flowRate: 0.25,
      };
    }

    case "interrupted":
      // Playback is already silenced by the controller (stop-first, ADR-0040).
      // Nothing pulses; the core is drawn as held between turns.
      return {
        ...local("interrupted", "inward"),
        scale: 0.94,
        pulse: 0,
        inwardFlow: 0.2,
        breathAmplitude: 0,
        breathHz: 0,
        glow: 0.15,
        shellSpread: 0.05,
      };

    case "error":
      return {
        ...local("error", "fault"),
        label: voice.lastError,
        severity: "warning",
        scale: 0.98,
        agitation: ERROR_AGITATION,
        breathAmplitude: 0.02,
        breathHz: ERROR_BREATH_HZ,
        glow: 0.25,
        shellSpread: 0.25,
        ringSpin: 0.08,
      };

    default:
      return bus;
  }
}

/** The core body alone: agent and lab channels, as before contract v2. */
function coreVisual(truth: CoreTruth, now: number): VisualIntent {
  if (truth.connection.kind === "connecting" && truth.polls === 0) {
    return { ...blank("connecting", "unknown"), dim: 0.5 };
  }

  // The core body draws the AGENT/LAB channel, not the API's `current`. Contract
  // v2 shares the bus with the room and the release path, and an `owner.away`
  // published while research runs must not blank a core that is genuinely
  // working (see `coreClaim`). Those channels are drawn beside the core instead.
  const claim = coreClaim(truth, now);

  if (truth.connection.kind === "unreachable") {
    // Keep the shape of what was last known, but visibly faded and with every
    // active channel damped: we are drawing memory, not observation.
    if (!claim.event) return { ...blank("unreachable", "unknown"), dim: 0.6 };
    const last = forLiveState(claim.event, claim);
    return {
      ...last,
      kind: "unreachable",
      dim: 0.6,
      breathAmplitude: last.breathAmplitude * 0.3,
      pulse: 0,
      inwardFlow: 0,
      topology: last.topology * 0.3,
      agitation: 0,
      // Memory of a shape, not observation of one: nothing turns, nothing
      // flows, nothing glows from an energy nobody is measuring any more.
      energy: 0,
      glow: last.glow * 0.3,
      ringSpin: 0,
      flowRate: 0,
      ownerVoice: 0,
      constellationDrift: 0,
    };
  }

  if (!claim.event) {
    // Polls are succeeding and the bus is empty. Not idle: nothing has ever
    // been published, and saying "calm" would be a claim we cannot support.
    return { ...blank("untold", "unknown"), dim: 0.35 };
  }

  if (!isKnownState(claim.event.state)) {
    return {
      ...fromEvent("unknown_state", "unknown", claim.event, claim),
      dim: 0.4,
    };
  }

  if (claim.expired) {
    // The claim aged out. Show the shape it had, dimmed and still, labelled as
    // last-known by the readout. Never fall back to idle: we were not told the
    // work stopped, only that we stopped being told anything.
    const last = forLiveState(claim.event, claim);
    return {
      ...last,
      kind: "last_known",
      dim: 0.5,
      breathAmplitude: 0,
      breathHz: 0,
      pulse: 0,
      inwardFlow: 0,
      topology: 0,
      agitation: 0,
      energy: 0,
      glow: last.glow * 0.3,
      ringSpin: 0,
      flowRate: 0,
      ownerVoice: 0,
      constellationDrift: 0,
      // A finished lab candidate does not stop existing because time passed,
      // but it is a steady state and never reaches this branch anyway.
      satelliteComplete: false,
      capabilityNodes: 0,
      capabilityNodesCounted: false,
    };
  }

  return forLiveState(claim.event, claim);
}

/** True when a release stage is drawn on the orbit. */
export function hasReleaseOrbit(intent: VisualIntent): boolean {
  return intent.releaseStage !== "none";
}

/**
 * True when the wake surge is drawn through the structure.
 *
 * Note what this is NOT: a claim that the Core is doing anything. An idle Core
 * with an alarm playing surges and stays idle; a thinking Core with an alarm
 * playing surges and stays thinking.
 */
export function hasWakeSurge(intent: VisualIntent): boolean {
  return intent.wakeSurge > 0;
}

/** Palette for the release orbit: waiting, working, arrived, or reversing. */
export function releasePalette(stage: ReleaseStage): PaletteToken {
  switch (stage) {
    case "owner_approval_required":
    case "owner_authorized":
      return "held";
    case "qualifying":
    case "deploying":
    case "verifying":
      return "work";
    case "live":
      return "achieved";
    case "rollback":
      return "fault";
    default:
      return "unknown";
  }
}

/** True when the intent describes observed, current activity. */
export function isLive(intent: VisualIntent): boolean {
  return (
    intent.kind !== "connecting" &&
    intent.kind !== "untold" &&
    intent.kind !== "unreachable" &&
    intent.kind !== "unauthorized" &&
    intent.kind !== "last_known"
  );
}

/**
 * Whether the evolution lab, not the agent, published this state.
 *
 * The cockpit keeps the two apart: the lab building something is not the agent
 * doing work for the owner, and conflating them would overstate what is
 * happening on the owner's behalf.
 */
export function isLabIntent(intent: VisualIntent): boolean {
  return intent.state !== null && isEvolutionState(intent.state);
}

/**
 * True while the Core body is a LIVE operator step: acting, or re-observing
 * to verify. `operator_failed` is deliberately not here (a failure is not
 * activity), and neither is an operator shape that aged into last-known.
 */
export function isOperatorActing(intent: VisualIntent): boolean {
  return intent.kind === "operator_running" || intent.kind === "operator_verifying";
}

/**
 * True while the Core body is a LIVE document read (v5). A reading shape that
 * aged into last-known is not here: we stopped being told, which is not the
 * same as still reading.
 */
export function isDocumentReading(intent: VisualIntent): boolean {
  return intent.kind === "document_analysis";
}

/** True while the Core body is a LIVE mail activity (v6); a last-known mail shape is not. */
export function isMailReading(intent: VisualIntent): boolean {
  return intent.kind === "mail_activity";
}

/** True while the Core body is a LIVE calendar activity (v6); a last-known calendar shape is not. */
export function isCalendarPlanning(intent: VisualIntent): boolean {
  return intent.kind === "calendar_activity";
}

/** True while the Core body is a LIVE factory event (v7); a last-known making shape is not. */
export function isArtifactMaking(intent: VisualIntent): boolean {
  return intent.kind === "artifact_factory";
}

/** True while the Core body is a LIVE app event (v8) — building, running, tested, failed or stopped; a last-known shape is not. */
export function isAppBuilding(intent: VisualIntent): boolean {
  return intent.kind === "app_factory";
}

/** True while the Core body is a LIVE app event whose publisher said `running` on a named port. */
export function isAppServing(intent: VisualIntent): boolean {
  return isAppBuilding(intent) && intent.app !== null && appIsServing(intent.app);
}

/** True while the Core body is a LIVE genesis event (v9) — building, waiting, settled or failed; a last-known shape is not. */
export function isGenesisWorking(intent: VisualIntent): boolean {
  return intent.kind === "capability_genesis";
}

/** True while the Core body is a LIVE genesis event whose publisher said `awaiting_approval`: the owner is the thing being waited on. */
export function isGenesisAwaiting(intent: VisualIntent): boolean {
  return isGenesisWorking(intent) && intent.genesis !== null && genesisPosture(intent.genesis.state) === "waiting";
}

/** True while the Core body is a LIVE scene event (v10) — making, rendering, reading, verified, mismatched, unavailable or failed; a last-known shape is not. */
export function isSceneWorking(intent: VisualIntent): boolean {
  return intent.kind === "scene_activity";
}

/** True while the Core body is a LIVE scene event whose publisher said `rendering`: the one posture that draws an image being written out. */
export function isSceneRendering(intent: VisualIntent): boolean {
  return isSceneWorking(intent) && intent.scene !== null && scenePosture(intent.scene.state) === "rendering";
}

/**
 * True while the Core body is a LIVE scene event whose publisher said the
 * tool could not be driven at all. Named separately so a harness — and the
 * cockpit — can tell "Unity cannot be driven" from "something failed"
 * without reading the geometry, which is exactly the distinction ADR-0088
 * §5 asks the Core to keep.
 */
export function isSceneUnavailable(intent: VisualIntent): boolean {
  return isSceneWorking(intent) && intent.scene !== null && scenePosture(intent.scene.state) === "unavailable";
}

/** True while the Core body is a LIVE executive event (v11) — planned, running, paused, ended or unreadable; a last-known shape is not. */
export function isExecutiveRunning(intent: VisualIntent): boolean {
  return intent.kind === "executive_run";
}

/**
 * True while the Core body is a LIVE executive event whose publisher said
 * `paused`. Named separately so a harness — and the cockpit — can tell "the
 * owner stopped this run" from "something failed" without reading the
 * geometry, which is exactly the distinction M26 §3 asks the Core to keep.
 */
export function isExecutivePaused(intent: VisualIntent): boolean {
  return isExecutiveRunning(intent) && intent.executive !== null && executivePosture(intent.executive.state) === "paused";
}

/** True while the Core body is a LIVE creative event (v12) — reading, planning, making, exporting, comparing, correcting, settled or unreadable; a last-known shape is not. */
export function isCreativeWorking(intent: VisualIntent): boolean {
  return intent.kind === "creative_activity";
}

/**
 * True while the Core body is a LIVE creative event whose publisher said
 * `verified`. The one door to "doğrulandı" on the Core, and it is a
 * membership test on the published word rather than a reading of the
 * geometry or of the similarity figure — ADR-0093 decision 4 makes the
 * comparison the proof, and a renderer that decided verification from a
 * score would be inventing the claim.
 */
export function isCreativeVerified(intent: VisualIntent): boolean {
  return isCreativeWorking(intent) && intent.creative !== null && creativePosture(intent.creative.state) === "verified";
}

/**
 * True while the Core body is a LIVE creative event whose publisher said the
 * application could not be driven at all. Named separately so a harness —
 * and the cockpit — can tell "Photoshop is not installed" from "something
 * failed" without reading the geometry, which is exactly the distinction
 * ADR-0093 decision 3 asks the Core to keep.
 */
export function isCreativeUnavailable(intent: VisualIntent): boolean {
  return isCreativeWorking(intent) && intent.creative !== null && creativePosture(intent.creative.state) === "unavailable";
}
