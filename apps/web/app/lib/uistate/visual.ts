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
import { type Claim, type CoreTruth, currentClaim } from "./truth";

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
  | "researching"
  | "memory"
  | "tool_running"
  | "waiting_owner"
  | "goal_completed"
  | "error"
  /** evolution.researching | designing | building | testing */
  | "evolution_working"
  | "shadow_ready";

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

  palette: PaletteToken;
};

/** Error motion is capped here so no future edit can turn it into a strobe. */
export const ERROR_AGITATION = 0.35;
export const ERROR_BREATH_HZ = 0.18;

/** A completely still, completely unknowing core. Every field starts honest. */
function blank(kind: CoreVisualKind, palette: PaletteToken): VisualIntent {
  return {
    kind,
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
    sourceNodes: 0,
    sourceNodesKnown: false,
    convergence: 0,
    convergenceKnown: false,
    constructionLayer: 0,
    satelliteComplete: false,
    composite: null,
    palette,
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
      // "nothing is running" is itself the reported truth.
      return { ...base("idle", "calm"), breathAmplitude: 0.06, breathHz: 0.14, scale: 1 };

    case "agent.listening":
      // Contracts, and draws energy inward. Depth tracks the reported level.
      return {
        ...base("listening", "inward"),
        scale: 0.88 - 0.05 * e,
        inwardFlow: 0.35 + 0.65 * e,
        breathAmplitude: 0.02,
        breathHz: 0.5,
      };

    case "agent.thinking":
      // Expands, with an active internal topology. Denser with reported load.
      return {
        ...base("thinking", "active"),
        scale: 1.12 + 0.1 * e,
        topology: 0.4 + 0.6 * e,
        breathAmplitude: 0.03,
        breathHz: 0.35,
      };

    case "agent.speaking":
      // Pulses from the bounded energy the event carries — never from owner
      // audio, which this client neither captures nor persists. No intensity
      // means no pulse.
      return {
        ...base("speaking", "voice"),
        scale: 1.04,
        pulse: e,
        breathAmplitude: 0.02,
        breathHz: 0.45,
      };

    case "agent.researching": {
      const nodes = researchNodes(event);
      return {
        ...base("researching", "discovery"),
        scale: 1.06,
        topology: 0.2,
        breathAmplitude: 0.03,
        breathHz: 0.25,
        sourceNodes: nodes.count,
        sourceNodesKnown: nodes.known,
      };
    }

    case "agent.memory_retrieval": {
      // Convergence is drawn only from a real progress figure. Absent one, the
      // core shows that memory work is running without claiming how far along.
      const p = progressOf(event);
      return {
        ...base("memory", "recall"),
        scale: 0.96,
        inwardFlow: 0.4,
        convergence: p ?? 0,
        convergenceKnown: p !== null,
        breathAmplitude: 0.03,
        breathHz: 0.3,
      };
    }

    case "agent.tool_running":
      return {
        ...base("tool_running", "work"),
        scale: 1.02,
        topology: 0.25,
        breathAmplitude: 0.025,
        breathHz: 0.4,
      };

    case "agent.waiting_owner":
      // Restrained: the system is deliberately not working. Showing busy motion
      // here would be the exact lie ADR-0052 exists to prevent.
      return {
        ...base("waiting_owner", "held"),
        scale: 0.94,
        restraint: 1,
        breathAmplitude: 0.015,
        breathHz: 0.09,
      };

    case "agent.goal_completed":
      return {
        ...base("goal_completed", "achieved"),
        scale: 1.1,
        breathAmplitude: 0.04,
        breathHz: 0.2,
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
      };

    case "evolution.shadow_ready":
      // A finished satellite, parked. Explicitly NOT live — the core itself is
      // unchanged, because nothing about production changed.
      return {
        ...base("shadow_ready", "ready"),
        scale: 1,
        breathAmplitude: 0.05,
        breathHz: 0.12,
        satelliteComplete: true,
      };

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
      };

    default:
      // Unreachable while `isKnownState` gates this function, but a new state
      // added to the contract must land somewhere honest rather than here by
      // accident, so it degrades to the explicit unknown presentation.
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
export function visualFor(truth: CoreTruth, now: number): VisualIntent {
  if (truth.connection.kind === "unauthorized") {
    return { ...blank("unauthorized", "unknown"), dim: 0.7 };
  }
  if (truth.connection.kind === "connecting" && truth.polls === 0) {
    return { ...blank("connecting", "unknown"), dim: 0.5 };
  }

  const claim = currentClaim(truth, now);

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
      // A finished lab candidate does not stop existing because time passed,
      // but it is a steady state and never reaches this branch anyway.
      satelliteComplete: false,
    };
  }

  return forLiveState(claim.event, claim);
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
