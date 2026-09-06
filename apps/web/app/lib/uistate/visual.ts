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
import { type ReleaseStage, eyeView, releaseView } from "./ambient";
import { type Claim, type CoreTruth, coreClaim, eyeClaim, releaseClaim } from "./truth";
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
  | "shadow_ready";

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
    palette,
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
