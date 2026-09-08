/**
 * The governing test: a visual state exists ONLY because the corresponding
 * UI-state event arrived.
 *
 * ADR-0052's rule is that a renderer which invents activity is worse than none,
 * because it teaches the owner to distrust everything else the system says.
 * That rule is only enforceable if it is checked exhaustively rather than
 * sampled, so the first block below walks every state in the contract and
 * asserts both directions: the right event produces the visual, and no other
 * event does.
 */

import { describe, expect, it } from "vitest";

import { UI_STATES, isCoreChannel } from "../../app/lib/uistate/contract";
import {
  CONSTELLATION_MOTIF,
  CONSTELLATION_REST,
  type CoreVisualKind,
  ERROR_AGITATION,
  IDLE_RING_SPIN,
  type VisualIntent,
  applyVoiceOverlay,
  isLabIntent,
  isLive,
  visualFor,
} from "../../app/lib/uistate/visual";
import { applyError, applyResponse, applyUnauthorized, emptyTruth } from "../../app/lib/uistate/truth";
import {
  AGENT_ERROR,
  AGENT_IDLE,
  EYE_ACTIVE,
  EYE_DISABLED,
  LAB_BUILDING,
  LAB_SHADOW_READY,
  MEMORY_RETRIEVAL,
  RESEARCH_NO_COUNTS,
  RESEARCH_RANKING,
  SELFMODEL_THINKING,
  T0,
  VOICE_LISTENING,
  VOICE_SPEAKING,
  event,
  resetSequence,
  response,
} from "./fixtures";

/** Feed exactly one event and read the visual back. */
function visualAfter(state: string, extra: Parameters<typeof event>[0] | null = null) {
  resetSequence();
  const e = extra ? event(extra) : event({ state });
  const truth = applyResponse(emptyTruth(), response([e]), T0);
  return visualFor(truth, T0);
}

/**
 * Which visual kind each CORE-CHANNEL state must produce, and nothing else.
 *
 * Contract v2 added the room (`eye.*`, `owner.*`) and the release path, which
 * are deliberately not core visuals: the owner going to bed is not the agent
 * changing what it is doing, so those states are drawn beside the core instead
 * (see `coreClaim`). They are asserted separately, and the coverage check below
 * is against the core channel rather than the whole contract.
 */
const CORE_STATES = UI_STATES.filter(isCoreChannel);

const EXPECTED_KIND: Record<string, CoreVisualKind> = {
  "agent.idle": "idle",
  "agent.listening": "listening",
  "agent.thinking": "thinking",
  "agent.speaking": "speaking",
  "agent.researching": "researching",
  "agent.memory_retrieval": "memory",
  "agent.tool_running": "tool_running",
  "agent.waiting_owner": "waiting_owner",
  "agent.goal_completed": "goal_completed",
  "agent.error": "error",
  "evolution.researching": "evolution_working",
  "evolution.designing": "evolution_working",
  "evolution.building": "evolution_working",
  "evolution.testing": "evolution_working",
  "evolution.shadow_ready": "shadow_ready",
  // v4 (M19): the operator is a core channel, and each of its three states is
  // its own deliberate kind — "acting", "verifying" and "failed" are different
  // statements and the headline names the one that was published.
  "operator.running": "operator_running",
  "operator.verifying": "operator_verifying",
  "operator.failed": "operator_failed",
  // v5 (M20): reading one of the owner's documents is the agent's own work —
  // a core state with its own deliberate reading posture.
  "document.analysis": "document_analysis",
  // v6 (M21): reading the owner's mail and calendar, and holding a draft or
  // a proposal for the owner, are the agent's own work — a calm reading
  // posture and a planning posture, each its own deliberate kind.
  "mail.activity": "mail_activity",
  "calendar.activity": "calendar_activity",
  // v7 (M22): making a file for the owner and checking it with an
  // independent parser is the agent's own work — a core state with its own
  // deliberate making posture.
  "artifact.factory": "artifact_factory",
  // v8 (M23): making an app for the owner — scaffolding, running and testing
  // it on the owner's machine — is the agent's own work: a core state with
  // its own deliberate building posture, and a running posture inside it.
  "app.factory": "app_factory",
  // v9 (M24): acquiring a capability the owner's request needs — researching
  // the interface, writing, testing, registering, using and verifying an
  // adapter — is the agent's own work: a core state with its own deliberate
  // building posture, a waiting posture and a settled posture inside it.
  "capability.genesis": "capability_genesis",
};

describe("a visual state is entered only by its own event", () => {
  it("covers every core-channel state in the contract", () => {
    // If the API grows a core state, this fails until the table above is
    // updated — which is the point: a new state must be a deliberate visual
    // decision, never a default animation.
    expect(Object.keys(EXPECTED_KIND).toSorted()).toEqual([...CORE_STATES].toSorted());
  });

  for (const state of CORE_STATES) {
    it(`${state} produces ${EXPECTED_KIND[state]} and no other state does`, () => {
      const expected = EXPECTED_KIND[state];
      expect(visualAfter(state).kind).toBe(expected);

      for (const other of CORE_STATES) {
        if (EXPECTED_KIND[other] === expected) continue;
        expect(visualAfter(other).kind).not.toBe(expected);
      }
    });
  }

  const NON_CORE = UI_STATES.filter((state) => !isCoreChannel(state));

  it("has non-core states, or the channel split is not being exercised", () => {
    expect(NON_CORE.length).toBeGreaterThan(0);
  });

  for (const state of NON_CORE) {
    it(`${state} tells the core body nothing`, () => {
      // "Untold" is the correct core reading here: something was published, but
      // nothing was said about what the agent is doing. Drawing idle would be a
      // claim; drawing this state's own visual would put the room where the
      // agent belongs.
      const intent = visualAfter(state);
      expect(intent.kind).toBe("untold");
      expect(intent.breathAmplitude).toBe(0);
      expect(intent.topology).toBe(0);
      expect(intent.pulse).toBe(0);
    });
  }
});

describe("silence is never drawn as activity", () => {
  it("before any poll the core is connecting, not idle", () => {
    const intent = visualFor(emptyTruth(), T0);
    expect(intent.kind).toBe("connecting");
    expect(intent.breathAmplitude).toBe(0);
    expect(isLive(intent)).toBe(false);
  });

  it("a successful poll with an empty bus is 'untold', not idle", () => {
    const truth = applyResponse(emptyTruth(), response([]), T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("untold");
    expect(intent.state).toBeNull();
    // Every motion channel is still: nothing has been reported to move it.
    expect(intent.breathAmplitude).toBe(0);
    expect(intent.topology).toBe(0);
    expect(intent.pulse).toBe(0);
    expect(intent.inwardFlow).toBe(0);
    expect(intent.sourceNodes).toBe(0);
    expect(intent.satelliteComplete).toBe(false);
  });

  it("distinguishes 'told it is idle' from 'told nothing'", () => {
    const idle = visualFor(applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0), T0);
    const untold = visualFor(applyResponse(emptyTruth(), response([]), T0), T0);
    expect(idle.kind).toBe("idle");
    expect(untold.kind).toBe("untold");
    // Only the reported-idle core breathes.
    expect(idle.breathAmplitude).toBeGreaterThan(0);
    expect(untold.breathAmplitude).toBe(0);
  });

  it("an unreachable API keeps the last shape but damps every active channel", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([VOICE_SPEAKING(0.8)]), T0);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unreachable");
    expect(intent.dim).toBeGreaterThan(0);
    expect(intent.pulse).toBe(0);
    expect(intent.inwardFlow).toBe(0);
    expect(intent.agitation).toBe(0);
    expect(isLive(intent)).toBe(false);
  });

  it("a refused session shows nothing at all", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([VOICE_SPEAKING()]), T0);
    truth = applyUnauthorized();
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unauthorized");
    expect(intent.state).toBeNull();
    expect(intent.pulse).toBe(0);
  });
});

describe("a transient claim expires instead of becoming idle", () => {
  it("thinking goes to last_known, still, after its TTL", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([SELFMODEL_THINKING()]), T0);

    const live = visualFor(truth, T0 + 1_000);
    expect(live.kind).toBe("thinking");
    expect(live.topology).toBeGreaterThan(0);

    const stale = visualFor(truth, T0 + 60_000);
    expect(stale.kind).toBe("last_known");
    // Crucially NOT idle: nobody said the work stopped.
    expect(stale.kind).not.toBe("idle");
    expect(stale.state).toBe("agent.thinking");
    expect(stale.topology).toBe(0);
    expect(stale.breathAmplitude).toBe(0);
    expect(stale.dim).toBeGreaterThan(0);
  });

  it("steady states never expire", () => {
    resetSequence();
    const idle = applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0);
    expect(visualFor(idle, T0 + 3_600_000).kind).toBe("idle");

    resetSequence();
    const waiting = applyResponse(
      emptyTruth(),
      response([event({ state: "agent.waiting_owner", subsystem: "goal" })]),
      T0,
    );
    expect(visualFor(waiting, T0 + 3_600_000).kind).toBe("waiting_owner");

    resetSequence();
    const ready = applyResponse(emptyTruth(), response([LAB_SHADOW_READY()]), T0);
    const late = visualFor(ready, T0 + 3_600_000);
    expect(late.kind).toBe("shadow_ready");
    expect(late.satelliteComplete).toBe(true);
  });

  it("goal_completed is a moment, not a permanent banner", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([event({ state: "agent.goal_completed" })]), T0);
    expect(visualFor(truth, T0 + 1_000).kind).toBe("goal_completed");
    expect(visualFor(truth, T0 + 120_000).kind).toBe("last_known");
  });
});

describe("counts come from the publisher or not at all", () => {
  it("draws exactly the evidence nodes research reported", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([RESEARCH_RANKING(12, 5)]), T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("researching");
    expect(intent.sourceNodesKnown).toBe(true);
    expect(intent.sourceNodes).toBe(5); // `kept`, what survived the quality gate
  });

  it("draws NO nodes when research published no counts", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([RESEARCH_NO_COUNTS()]), T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("researching");
    expect(intent.sourceNodesKnown).toBe(false);
    expect(intent.sourceNodes).toBe(0);
  });

  it("reports zero kept sources as zero, not as unknown", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([RESEARCH_RANKING(9, 0)]), T0);
    const intent = visualFor(truth, T0);
    expect(intent.sourceNodesKnown).toBe(true);
    expect(intent.sourceNodes).toBe(0);
  });

  it("memory converges only against a real progress figure", () => {
    resetSequence();
    const unknown = visualFor(
      applyResponse(emptyTruth(), response([MEMORY_RETRIEVAL(null)]), T0),
      T0,
    );
    expect(unknown.kind).toBe("memory");
    expect(unknown.convergenceKnown).toBe(false);
    expect(unknown.convergence).toBe(0);
    expect(unknown.progress).toBeNull();

    resetSequence();
    const known = visualFor(
      applyResponse(emptyTruth(), response([MEMORY_RETRIEVAL(0.4)]), T0),
      T0,
    );
    expect(known.convergenceKnown).toBe(true);
    expect(known.convergence).toBeCloseTo(0.4);
  });

  it("reads the self-model indexer's percent as real progress", () => {
    resetSequence();
    const intent = visualFor(
      applyResponse(emptyTruth(), response([SELFMODEL_THINKING(40)]), T0),
      T0,
    );
    expect(intent.progress).toBeCloseTo(0.4);
  });

  it("never invents progress from intensity", () => {
    resetSequence();
    // Voice publishes an intensity and no progress. A bar must not appear.
    const intent = visualFor(applyResponse(emptyTruth(), response([VOICE_LISTENING()]), T0), T0);
    expect(intent.intensity).toBeCloseTo(0.5);
    expect(intent.progress).toBeNull();
  });
});

describe("speaking pulses only from the energy the event carried", () => {
  it("uses the reported bounded energy as the pulse amplitude", () => {
    resetSequence();
    const intent = visualFor(applyResponse(emptyTruth(), response([VOICE_SPEAKING(0.62)]), T0), T0);
    expect(intent.kind).toBe("speaking");
    expect(intent.pulse).toBeCloseTo(0.62);
  });

  it("does not pulse when the publisher sent no energy", () => {
    resetSequence();
    const intent = visualFor(applyResponse(emptyTruth(), response([VOICE_SPEAKING(null)]), T0), T0);
    expect(intent.kind).toBe("speaking");
    expect(intent.intensity).toBeNull();
    expect(intent.pulse).toBe(0);
  });
});

describe("the shapes the spec asks for", () => {
  it("listening contracts and flows inward; thinking expands with topology", () => {
    resetSequence();
    const listening = visualFor(applyResponse(emptyTruth(), response([VOICE_LISTENING()]), T0), T0);
    expect(listening.scale).toBeLessThan(1);
    expect(listening.inwardFlow).toBeGreaterThan(0);
    expect(listening.topology).toBe(0);

    resetSequence();
    const thinking = visualFor(applyResponse(emptyTruth(), response([SELFMODEL_THINKING()]), T0), T0);
    expect(thinking.scale).toBeGreaterThan(1);
    expect(thinking.topology).toBeGreaterThan(0);
  });

  it("waiting_owner is restrained, not busy", () => {
    resetSequence();
    const intent = visualFor(
      applyResponse(emptyTruth(), response([event({ state: "agent.waiting_owner" })]), T0),
      T0,
    );
    expect(intent.restraint).toBe(1);
    expect(intent.topology).toBe(0);
    expect(intent.pulse).toBe(0);
    expect(intent.breathHz).toBeLessThan(0.15);
  });

  it("error is controlled and slow, never a strobe", () => {
    resetSequence();
    const intent = visualFor(applyResponse(emptyTruth(), response([AGENT_ERROR("critical")]), T0), T0);
    expect(intent.kind).toBe("error");
    expect(intent.agitation).toBe(ERROR_AGITATION);
    expect(intent.agitation).toBeLessThanOrEqual(0.35);
    // A "flash" would need a high breath rate; the state is capped well below.
    expect(intent.breathHz).toBeLessThan(0.25);
    expect(intent.severity).toBe("critical");
  });

  it("severity does not accelerate the error animation", () => {
    resetSequence();
    const warning = visualFor(applyResponse(emptyTruth(), response([AGENT_ERROR("warning")]), T0), T0);
    resetSequence();
    const critical = visualFor(applyResponse(emptyTruth(), response([AGENT_ERROR("critical")]), T0), T0);
    expect(critical.breathHz).toBe(warning.breathHz);
    expect(critical.agitation).toBe(warning.agitation);
  });

  it("the lab's construction layer encodes which phase was published", () => {
    const layers: Record<string, number> = {
      "evolution.researching": 1,
      "evolution.designing": 2,
      "evolution.building": 3,
      "evolution.testing": 4,
    };
    for (const [state, layer] of Object.entries(layers)) {
      resetSequence();
      const intent = visualFor(applyResponse(emptyTruth(), response([event({ state })]), T0), T0);
      expect(intent.kind).toBe("evolution_working");
      expect(intent.constructionLayer).toBe(layer);
      expect(intent.satelliteComplete).toBe(false);
      expect(isLabIntent(intent)).toBe(true);
    }
  });

  it("shadow_ready is a completed satellite and claims nothing about production", () => {
    resetSequence();
    const intent = visualFor(applyResponse(emptyTruth(), response([LAB_SHADOW_READY()]), T0), T0);
    expect(intent.satelliteComplete).toBe(true);
    expect(intent.constructionLayer).toBe(0);
    expect(intent.composite).toBeCloseTo(0.72);
    expect(intent.severity).toBe("notice");
  });

  it("carries the lab's real progress through when it published one", () => {
    resetSequence();
    const intent = visualFor(applyResponse(emptyTruth(), response([LAB_BUILDING()]), T0), T0);
    expect(intent.progress).toBeCloseTo(0.5);
  });
});

describe("states this build does not know", () => {
  it("are shown as unknown, never guessed into an animation", () => {
    resetSequence();
    const truth = applyResponse(
      emptyTruth(),
      response([event({ state: "agent.daydreaming", subsystem: "system" })]),
      T0,
    );
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unknown_state");
    expect(intent.state).toBe("agent.daydreaming");
    expect(intent.breathAmplitude).toBe(0);
    expect(intent.topology).toBe(0);
    expect(intent.pulse).toBe(0);
    expect(intent.sourceNodes).toBe(0);
    expect(intent.ringSpin).toBe(0);
    expect(intent.flowRate).toBe(0);
    expect(intent.glow).toBe(0);
  });
});

// ------------------------------------------------- M18.1: the channel profiles

/**
 * The layered structure's channels (ADR-0065). Two properties are pinned for
 * every core state: it has a channel profile of its own, and with no declared
 * intensity and no measurement it animates no rhythm — the shape is the
 * state's, the motion within it is the evidence's.
 */
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

/** The channels that carry a rhythm from a measurement or a declared figure. */
const RHYTHM_CHANNELS = ["pulse", "ownerVoice", "energy"] as const satisfies readonly (keyof VisualIntent)[];

function profileOf(intent: VisualIntent): string {
  return JSON.stringify({
    kind: intent.kind,
    scale: intent.scale,
    shellSpread: intent.shellSpread,
    ringSpin: intent.ringSpin,
    flowRate: intent.flowRate,
    inwardFlow: intent.inwardFlow,
    topology: intent.topology,
    glow: intent.glow,
    restraint: intent.restraint,
    agitation: intent.agitation,
    constructionLayer: intent.constructionLayer,
    capabilityNodes: intent.capabilityNodes,
  });
}

describe("M18.1: every core state has its own channel profile", () => {
  it("no two core states share a profile", () => {
    const profiles = new Map<string, string>();
    for (const state of CORE_STATES) {
      const profile = profileOf(visualAfter(state));
      const clash = [...profiles.entries()].find(([, p]) => p === profile);
      expect(clash, `${state} shares a profile with ${clash?.[0]}`).toBeUndefined();
      profiles.set(state, profile);
    }
    expect(profiles.size).toBe(CORE_STATES.length);
  });

  it("every live state carries a glow and a shell spread above zero; silence carries none", () => {
    for (const state of CORE_STATES) {
      const intent = visualAfter(state);
      expect(intent.glow, state).toBeGreaterThan(0);
      expect(intent.shellSpread, state).toBeGreaterThan(0);
    }
    const untold = visualFor(applyResponse(emptyTruth(), response([]), T0), T0);
    expect(untold.glow).toBe(0);
    expect(untold.shellSpread).toBe(0);
  });

  it("the profile is what the spec describes", () => {
    const idle = visualAfter("agent.idle");
    expect(idle.ringSpin).toBe(IDLE_RING_SPIN);
    expect(idle.flowRate).toBe(0);

    const listening = visualAfter("agent.listening");
    expect(listening.shellSpread).toBeLessThan(idle.shellSpread); // closes in
    expect(listening.inwardFlow).toBeGreaterThan(0);
    expect(listening.flowRate).toBe(0);

    const thinking = visualAfter("agent.thinking");
    expect(thinking.shellSpread).toBeGreaterThan(idle.shellSpread); // expands
    expect(thinking.ringSpin).toBeGreaterThan(idle.ringSpin);
    expect(thinking.flowRate).toBeGreaterThan(0);
    expect(thinking.topology).toBeGreaterThan(0);

    const tool = visualAfter("agent.tool_running");
    expect(tool.shellSpread).toBeGreaterThan(idle.shellSpread);
    expect(tool.flowRate).toBeGreaterThan(thinking.flowRate); // most path traffic of any state

    const memory = visualAfter("agent.memory_retrieval");
    expect(memory.inwardFlow).toBeGreaterThan(0); // information converges

    const waiting = visualAfter("agent.waiting_owner");
    expect(waiting.ringSpin).toBeLessThan(idle.ringSpin);
    expect(waiting.flowRate).toBe(0);
    expect(waiting.glow).toBeLessThan(idle.glow);
  });
});

describe("M18.1: no intensity and no measurement means no rhythm", () => {
  for (const state of CORE_STATES) {
    it(`${state} with intensity null animates no measured rhythm`, () => {
      const intent = visualAfter(state, { state, intensity: null });
      expect(intent.intensity).toBeNull();
      for (const channel of RHYTHM_CHANNELS) {
        expect(intent[channel], channel).toBe(0);
      }
    });
  }

  it("a declared intensity is the energy, and the glow answers to it", () => {
    const quiet = visualAfter("agent.thinking", { state: "agent.thinking", intensity: 0 });
    const loud = visualAfter("agent.thinking", { state: "agent.thinking", intensity: 1 });
    expect(quiet.energy).toBe(0);
    expect(loud.energy).toBe(1);
    expect(loud.glow).toBeGreaterThan(quiet.glow);
    expect(loud.glow).toBeLessThanOrEqual(1);
    expect(loud.ringSpin).toBeGreaterThan(quiet.ringSpin);
    expect(loud.shellSpread).toBeGreaterThan(quiet.shellSpread);
  });

  it("an unreachable API leaves the shape and stops every motion channel", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([SELFMODEL_THINKING()]), T0);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unreachable");
    expect(intent.shellSpread).toBeGreaterThan(0); // the shape it had
    expect(intent.ringSpin).toBe(0);
    expect(intent.flowRate).toBe(0);
    expect(intent.energy).toBe(0);
    expect(intent.ownerVoice).toBe(0);
  });

  it("an expired claim stops every motion channel", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([SELFMODEL_THINKING()]), T0);
    const stale = visualFor(truth, T0 + 60_000);
    expect(stale.kind).toBe("last_known");
    for (const channel of MOTION_CHANNELS) {
      expect(stale[channel], channel).toBe(0);
    }
  });

  it("the room's states never move the core body", () => {
    for (const state of UI_STATES.filter((s) => !isCoreChannel(s))) {
      const intent = visualAfter(state);
      for (const channel of MOTION_CHANNELS) {
        expect(intent[channel], `${state}.${channel}`).toBe(0);
      }
    }
  });
});

/** A local voice overlay with one measured level, for the listening/speaking tests. */
function overlay(state: "listening" | "speaking", level: number | null) {
  return {
    state,
    micLevel: state === "listening" ? level : null,
    outputLevel: state === "speaking" ? level : null,
    caption: null,
    toolLabel: null,
    lastError: null,
  };
}

describe("M18.1: the owner's voice is the measured microphone level", () => {
  it("OWNER_SPEAKING is listening with the gate's level above zero", () => {
    resetSequence();
    const bus = visualFor(applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0), T0);
    const silent = applyVoiceOverlay(bus, overlay("listening", 0));
    const speaking = applyVoiceOverlay(bus, overlay("listening", 0.6));
    expect(silent.kind).toBe("listening");
    expect(silent.ownerVoice).toBe(0);
    expect(speaking.ownerVoice).toBe(0.6);
    expect(speaking.energy).toBe(0.6);
    expect(speaking.inwardFlow).toBeGreaterThan(silent.inwardFlow);
    expect(speaking.glow).toBeGreaterThan(silent.glow);
  });

  it("an unmeasured microphone is a still listening core, not an estimated one", () => {
    resetSequence();
    const bus = visualFor(applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0), T0);
    const unmeasured = applyVoiceOverlay(bus, overlay("listening", null));
    expect(unmeasured.ownerVoice).toBe(0);
    expect(unmeasured.energy).toBe(0);
    expect(unmeasured.intensity).toBeNull();
  });

  it("a bus listening never claims the owner's voice: its intensity is declared, not measured here", () => {
    resetSequence();
    const bus = visualFor(applyResponse(emptyTruth(), response([VOICE_LISTENING()]), T0), T0);
    expect(bus.kind).toBe("listening");
    expect(bus.energy).toBeCloseTo(0.5);
    expect(bus.ownerVoice).toBe(0);
  });

  it("the assistant's speech glows by the real playback envelope", () => {
    resetSequence();
    const bus = visualFor(applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0), T0);
    const quiet = applyVoiceOverlay(bus, overlay("speaking", 0));
    const loud = applyVoiceOverlay(bus, overlay("speaking", 0.8));
    expect(quiet.pulse).toBe(0);
    expect(loud.pulse).toBe(0.8);
    expect(loud.energy).toBe(0.8);
    expect(loud.glow).toBeGreaterThan(quiet.glow);
    expect(loud.ownerVoice).toBe(0);
  });
});

describe("M18.1: the constellation is counted or it is a labelled motif", () => {
  it("draws the published count, and its motion from published progress only", () => {
    resetSequence();
    const counted = visualFor(applyResponse(emptyTruth(), response([RESEARCH_RANKING(12, 5)]), T0), T0);
    expect(counted.constellationNodes).toBe(5);
    expect(counted.sourceNodesKnown).toBe(true);
    // No progress was published, so the drift is the fixed rest figure.
    expect(counted.constellationDrift).toBe(CONSTELLATION_REST);
    // The wider field: what was seen minus what was kept.
    expect(counted.fieldNodesKnown).toBe(true);
    expect(counted.fieldNodes).toBe(7);
  });

  it("draws the fixed motif when no count was sent, and says it is not a count", () => {
    resetSequence();
    const motif = visualFor(applyResponse(emptyTruth(), response([RESEARCH_NO_COUNTS()]), T0), T0);
    expect(motif.sourceNodesKnown).toBe(false);
    expect(motif.sourceNodes).toBe(0);
    expect(motif.constellationNodes).toBe(CONSTELLATION_MOTIF);
    expect(motif.constellationDrift).toBe(CONSTELLATION_REST);
    expect(motif.fieldNodesKnown).toBe(false);
    expect(motif.fieldNodes).toBe(0);
  });

  it("the motif is the same figure every time", () => {
    resetSequence();
    const a = visualFor(applyResponse(emptyTruth(), response([RESEARCH_NO_COUNTS()]), T0), T0);
    resetSequence();
    const b = visualFor(applyResponse(emptyTruth(), response([RESEARCH_NO_COUNTS()]), T0), T0);
    expect(a.constellationNodes).toBe(b.constellationNodes);
    expect(a.constellationDrift).toBe(b.constellationDrift);
  });

  it("published progress moves the constellation, and a zero kept count draws none", () => {
    resetSequence();
    const moving = visualFor(
      applyResponse(
        emptyTruth(),
        response([event({ state: "agent.researching", subsystem: "research", progress: 0.5, metadata: { candidates: 9, kept: 3 } })]),
        T0,
      ),
      T0,
    );
    expect(moving.constellationDrift).toBeCloseTo(CONSTELLATION_REST + 0.3);

    resetSequence();
    const none = visualFor(applyResponse(emptyTruth(), response([RESEARCH_RANKING(9, 0)]), T0), T0);
    expect(none.constellationNodes).toBe(0);
    expect(none.sourceNodesKnown).toBe(true);
  });

  it("nothing but research has a constellation", () => {
    for (const state of CORE_STATES) {
      if (state === "agent.researching") continue;
      const intent = visualAfter(state);
      expect(intent.constellationNodes, state).toBe(0);
      expect(intent.constellationDrift, state).toBe(0);
    }
  });
});

describe("M18.1: capability nodes are the candidates the lab said are ready", () => {
  it("one candidate per event when the lab sent no count, and says it was not counted", () => {
    resetSequence();
    const intent = visualFor(applyResponse(emptyTruth(), response([LAB_SHADOW_READY()]), T0), T0);
    expect(intent.capabilityNodes).toBe(1);
    expect(intent.capabilityNodesCounted).toBe(false);
  });

  it("reads a published ready count verbatim", () => {
    resetSequence();
    const intent = visualFor(
      applyResponse(
        emptyTruth(),
        response([event({ state: "evolution.shadow_ready", subsystem: "evolution", metadata: { ready: 3, composite: 0.7 } })]),
        T0,
      ),
      T0,
    );
    expect(intent.capabilityNodes).toBe(3);
    expect(intent.capabilityNodesCounted).toBe(true);
  });

  it("nothing but shadow_ready parks capability nodes", () => {
    for (const state of CORE_STATES) {
      if (state === "evolution.shadow_ready") continue;
      expect(visualAfter(state).capabilityNodes, state).toBe(0);
    }
  });
});

describe("M18.1: the eye's aperture is read from the eye's own claim", () => {
  it("is 1 only while eye.active is current", () => {
    resetSequence();
    const active = visualFor(applyResponse(emptyTruth(), response([EYE_ACTIVE()]), T0), T0);
    expect(active.eyeActive).toBe(1);
    // The core body is still untold: the camera says nothing about the agent.
    expect(active.kind).toBe("untold");

    resetSequence();
    const disabled = visualFor(applyResponse(emptyTruth(), response([EYE_DISABLED()]), T0), T0);
    expect(disabled.eyeActive).toBe(0);

    const untold = visualFor(applyResponse(emptyTruth(), response([]), T0), T0);
    expect(untold.eyeActive).toBe(0);
  });

  it("stays on the intent alongside a working core and through the voice overlay", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([EYE_ACTIVE(), SELFMODEL_THINKING()]), T0);
    const bus = visualFor(truth, T0);
    expect(bus.kind).toBe("thinking");
    expect(bus.eyeActive).toBe(1);
    const local = visualFor(truth, T0, {
      state: "speaking",
      micLevel: null,
      outputLevel: 0.4,
      caption: null,
      toolLabel: null,
      lastError: null,
    });
    expect(local.kind).toBe("speaking");
    expect(local.eyeActive).toBe(1);
  });

  it("is never drawn for a refused session", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([EYE_ACTIVE()]), T0);
    truth = applyUnauthorized();
    expect(visualFor(truth, T0).eyeActive).toBe(0);
  });
});
