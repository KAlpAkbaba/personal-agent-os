/**
 * The local voice overlay (ADR-0061 §4): this tab's own session drawn onto
 * the Core, in the same `visualFor` pipeline as the bus, and labelled as its
 * own source.
 *
 * Two things are pinned here. The state table — which controller state
 * becomes which visual, and from which measurement — and the honesty rules
 * around it: `idle`/`closed` change nothing, an unmeasured envelope draws no
 * pulse, `interrupted` pulses at zero, and the release orbit (the bus's own
 * channel) survives the overlay untouched.
 */

import { describe, expect, it } from "vitest";

import type { VoiceUiState } from "../../app/lib/voice/controller";
import { speechCaption } from "../../app/lib/voice/labels";
import {
  type VoiceOverlay,
  ERROR_AGITATION,
  VOICE_OVERLAY_STATES,
  applyVoiceOverlay,
  visualFor,
  voiceOverlayApplies,
} from "../../app/lib/uistate/visual";
import { voiceOverlayFrom } from "../../app/lib/uistate/voice-overlay";
import { applyResponse, applyUnauthorized, emptyTruth } from "../../app/lib/uistate/truth";
import { AGENT_IDLE, RELEASE_DEPLOYING, T0, event, resetSequence, response } from "./fixtures";

const ALL_STATES: VoiceUiState[] = [
  "idle",
  "creating",
  "connecting",
  "listening",
  "speaking",
  "tool_running",
  "interrupted",
  "reconnecting",
  "closed",
  "error",
];

function overlay(partial: Partial<VoiceOverlay> & { state: VoiceUiState }): VoiceOverlay {
  return { micLevel: null, outputLevel: null, caption: null, toolLabel: null, lastError: null, ...partial };
}

function idleBus() {
  resetSequence();
  return visualFor(applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0), T0);
}

describe("the state table", () => {
  const EXPECTED: Record<VoiceUiState, string | null> = {
    idle: null,
    closed: null,
    creating: "connecting",
    connecting: "connecting",
    reconnecting: "connecting",
    listening: "listening",
    speaking: "speaking",
    tool_running: "tool_running",
    interrupted: "interrupted",
    error: "error",
  };

  it("covers every controller state", () => {
    expect(Object.keys(EXPECTED).toSorted()).toEqual([...ALL_STATES].toSorted());
    for (const state of ALL_STATES) {
      expect(VOICE_OVERLAY_STATES.has(state)).toBe(EXPECTED[state] !== null);
    }
  });

  for (const state of ALL_STATES) {
    it(`${state} → ${EXPECTED[state] ?? "no overlay (bus shows)"}`, () => {
      const bus = idleBus();
      const out = applyVoiceOverlay(bus, overlay({ state }));
      if (EXPECTED[state] === null) {
        expect(out).toBe(bus);
        expect(out.source).toBe("bus");
        expect(voiceOverlayApplies(overlay({ state }))).toBe(false);
      } else {
        expect(out.kind).toBe(EXPECTED[state]);
        expect(out.source).toBe("voice");
        expect(out.voiceState).toBe(state);
        expect(out.subsystem).toBe("voice");
        // No bus event backs it, and it says so rather than borrowing one.
        expect(out.state).toBeNull();
        expect(out.ageMs).toBeNull();
      }
    });
  }
});

describe("measurements, not rhythms", () => {
  it("speaking pulses at exactly the measured output envelope", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "speaking", outputLevel: 0.37 }));
    expect(out.pulse).toBe(0.37);
    expect(out.intensity).toBe(0.37);
  });

  it("speaking with no audio playing has a zero pulse", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "speaking", outputLevel: 0 }));
    expect(out.pulse).toBe(0);
    expect(out.intensity).toBe(0);
  });

  it("a pause inside an answer is a calm speaking Core, not a state change (ADR-0066: energy is amplitude, the lifecycle is the state)", () => {
    const pause = applyVoiceOverlay(idleBus(), overlay({ state: "speaking", outputLevel: 0 }));
    expect(pause.kind).toBe("speaking");
    expect(pause.voiceState).toBe("speaking");
    expect(pause.pulse).toBe(0);
    expect(pause.energy).toBe(0);
    // Calm, not held: the speaking body keeps its breath and scale, unlike `interrupted`.
    const held = applyVoiceOverlay(idleBus(), overlay({ state: "interrupted", outputLevel: 0 }));
    expect(pause.breathAmplitude).toBeGreaterThan(0);
    expect(held.breathAmplitude).toBe(0);
    expect(pause.scale).toBeGreaterThan(held.scale);
    // And when the speech resumes, only the amplitude changes.
    const resumed = applyVoiceOverlay(idleBus(), overlay({ state: "speaking", outputLevel: 0.6 }));
    expect(resumed.kind).toBe("speaking");
    expect(resumed.pulse).toBe(0.6);
    expect({ ...resumed, pulse: 0, energy: 0, intensity: 0, glow: 0 }).toEqual({ ...pause, glow: 0 });
  });

  it("speaking with an unmeasurable path draws no pulse and keeps intensity null so the readout can say so", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "speaking", outputLevel: null }));
    expect(out.pulse).toBe(0);
    expect(out.intensity).toBeNull();
  });

  it("interrupted stops the speech animation: pulse 0, no breathing", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "interrupted", outputLevel: 0.9 }));
    expect(out.kind).toBe("interrupted");
    expect(out.pulse).toBe(0);
    expect(out.breathAmplitude).toBe(0);
  });

  it("listening contracts and draws inward by the measured microphone level", () => {
    const quiet = applyVoiceOverlay(idleBus(), overlay({ state: "listening", micLevel: 0 }));
    const loud = applyVoiceOverlay(idleBus(), overlay({ state: "listening", micLevel: 1 }));
    expect(quiet.inwardFlow).toBeCloseTo(0.35);
    expect(loud.inwardFlow).toBeCloseTo(1);
    expect(loud.scale).toBeLessThan(quiet.scale);
    expect(quiet.scale).toBeLessThan(1);
    const unmeasured = applyVoiceOverlay(idleBus(), overlay({ state: "listening", micLevel: null }));
    expect(unmeasured.intensity).toBeNull();
    expect(unmeasured.inwardFlow).toBeCloseTo(0.35);
  });

  it("tool_running has the tool topology and the tool's label", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "tool_running", toolLabel: "Araştırma" }));
    expect(out.topology).toBeGreaterThan(0);
    expect(out.pulse).toBe(0);
    expect(out.label).toBe("Araştırma");
  });

  it("error carries the controller's own message and stays bounded", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "error", lastError: "Medya bağlantısı kurulamadı" }));
    expect(out.kind).toBe("error");
    expect(out.label).toBe("Medya bağlantısı kurulamadı");
    expect(out.agitation).toBe(ERROR_AGITATION);
    expect(out.severity).toBe("warning");
  });

  it("connecting is dimmed and still", () => {
    for (const state of ["creating", "connecting", "reconnecting"] as const) {
      const out = applyVoiceOverlay(idleBus(), overlay({ state }));
      expect(out.kind).toBe("connecting");
      expect(out.dim).toBeGreaterThan(0);
      expect(out.breathAmplitude).toBe(0);
      expect(out.pulse).toBe(0);
    }
  });
});

describe("what the overlay may not touch", () => {
  it("keeps the release orbit exactly as the bus drew it", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([AGENT_IDLE(), RELEASE_DEPLOYING()]), T0);
    const bus = visualFor(truth, T0);
    expect(bus.releaseStage).toBe("deploying");
    const out = visualFor(truth, T0, overlay({ state: "speaking", outputLevel: 0.5 }));
    expect(out.kind).toBe("speaking");
    expect(out.releaseStage).toBe("deploying");
    expect(out.releaseInFlight).toBe(bus.releaseInFlight);
    expect(out.releaseProgress).toBe(0.4);
  });

  it("a refused bus session draws nothing, overlay or not", () => {
    const out = visualFor(applyUnauthorized(), T0, overlay({ state: "listening", micLevel: 0.5 }));
    expect(out.kind).toBe("unauthorized");
    expect(out.source).toBe("bus");
  });

  it("visualFor without an overlay is unchanged: every bus intent is bus-sourced", () => {
    const bus = idleBus();
    expect(bus.source).toBe("bus");
    expect(bus.voiceState).toBeNull();
    expect(visualFor(applyResponse(emptyTruth(), response([]), T0), T0).source).toBe("bus");
  });

  it("does not depend on the bus body: the overlay is the same over untold and over idle", () => {
    resetSequence();
    const untold = visualFor(applyResponse(emptyTruth(), response([]), T0), T0);
    const a = applyVoiceOverlay(untold, overlay({ state: "listening", micLevel: 0.2 }));
    const b = applyVoiceOverlay(idleBus(), overlay({ state: "listening", micLevel: 0.2 }));
    expect({ ...a, releaseStage: "x" }).toEqual({ ...b, releaseStage: "x" });
  });
});

describe("the caption", () => {
  const base = { toolsRunning: [] as string[], narrationCursor: null };

  it("is null unless speaking", () => {
    expect(speechCaption({ ...base, state: "listening", toolsRunning: ["research.start"] })).toBeNull();
  });

  it("names what the running tool's result is being narrated as, in Turkish", () => {
    expect(speechCaption({ ...base, state: "speaking", toolsRunning: ["research.start"] })).toBe(
      "Araştırma sonuçlarını anlatıyorum…",
    );
  });

  it("falls back to the tool's own name rather than inventing a phrase", () => {
    expect(speechCaption({ ...base, state: "speaking", toolsRunning: ["world.model"] })).toBe(
      "world.model sonucunu anlatıyorum…",
    );
  });

  it("uses the narration cursor when no tool is running", () => {
    expect(
      speechCaption({
        ...base,
        state: "speaking",
        narrationCursor: { state: "narrating", action: null, sectionId: "s2", paragraphId: "p3", sentenceIndex: 2 },
      }),
    ).toBe("Anlatım · s2 ¶p3 3. cümle");
  });

  it("is null when nothing semantic is known — no caption rather than a made-up one", () => {
    expect(speechCaption({ ...base, state: "speaking" })).toBeNull();
  });

  it("voiceOverlayFrom carries the caption, tool label, error and levels through unchanged", () => {
    resetSequence();
    const controller = {
      state: "speaking" as const,
      toolsRunning: ["research.start"],
      narrationCursor: null,
      lastError: null,
    };
    const out = voiceOverlayFrom(
      controller as unknown as Parameters<typeof voiceOverlayFrom>[0],
      { micLevel: null, outputLevel: 0.3 },
    );
    expect(out).toEqual({
      state: "speaking",
      micLevel: null,
      outputLevel: 0.3,
      caption: "Araştırma sonuçlarını anlatıyorum…",
      toolLabel: "Araştırma",
      lastError: null,
    });
  });

  it("is ignored by the overlay in states other than speaking (no transcript leaks onto the Core)", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "listening", caption: "should not appear" }));
    expect(out.label).toBeNull();
  });

  it("appears verbatim on the speaking intent, and only there", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "speaking", outputLevel: 0.1, caption: "World Model" }));
    expect(out.label).toBe("World Model");
    // The controller's transcript is not part of the overlay type at all.
    expect(Object.keys(overlay({ state: "speaking" }))).not.toContain("assistantText");
  });

  it("dropping to interrupted clears the caption with the pulse", () => {
    const out = applyVoiceOverlay(idleBus(), overlay({ state: "interrupted", caption: "World Model" }));
    expect(out.label).toBeNull();
  });
});

describe("event helper sanity", () => {
  it("the fixture module still builds events (guards the import above)", () => {
    resetSequence();
    expect(event({ state: "agent.idle" }).sequence).toBe(1);
  });
});
