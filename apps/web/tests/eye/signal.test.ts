/**
 * The Active Eye's derivation, tested as the pure math it is.
 *
 * No canvas, no video, no browser: `computeGridLuminance` takes a plain
 * `Uint8ClampedArray` the same shape `getImageData` would produce, and every
 * fixture is a generated array (see `fixtures.ts`) — never real imagery, per
 * the M18 task brief.
 *
 * The presence cases are the 2026-09-06 owner run, turned into assertions: a
 * seated owner moving a few cells at a time must read as present; a large exit
 * burst followed by stillness must read as absent; and a small movement then a
 * long stillness must read as "very still", weakly, before it reads as absent.
 */

import { describe, expect, it } from "vitest";

import {
  ABSENCE_CONFIDENCE_CAP,
  CELL_CHANGE_THRESHOLD,
  GRID_COLS,
  GRID_ROWS,
  LINGER_MS,
  LOW_ACTIVITY_MAX,
  MEDIUM_ACTIVITY_MAX,
  NONE_ACTIVITY_MAX,
  PRESENCE_MEMORY_MS,
  RESTING_STILL_MS,
  activityLevelFor,
  computeGridLuminance,
  deriveAwakeState,
  deriveObservation,
  derivePosture,
  derivePresence,
  meanLuminance,
  measureMotion,
  motionEnergyBetween,
} from "../../app/lib/eye/signal";
import { blockFrame, flatFrame } from "./fixtures";

const NOW = new Date("2026-09-06T10:00:00.000Z");

const evidence = (over: Partial<Parameters<typeof derivePresence>[0]> = {}) => ({
  currentActivity: "none" as const,
  msSinceLastMotion: null as number | null,
  lastMotionLevel: "none" as const,
  sampleCount: 10,
  samplesForFullConfidence: 5,
  ...over,
});

describe("computeGridLuminance", () => {
  it("reduces a flat frame to a uniform, correctly-scaled grid", () => {
    const grid = computeGridLuminance(flatFrame(24, 18, 128), 24, 18);
    expect(grid).toHaveLength(GRID_COLS * GRID_ROWS);
    for (const v of grid) expect(v).toBeCloseTo(128 / 255, 3);
    expect(meanLuminance(grid)).toBeCloseTo(128 / 255, 3);
  });

  it("black is zero, and never negative or above one", () => {
    const grid = computeGridLuminance(flatFrame(24, 18, 0), 24, 18);
    for (const v of grid) expect(v).toBe(0);
    const white = computeGridLuminance(flatFrame(24, 18, 255), 24, 18);
    for (const v of white) expect(v).toBeLessThanOrEqual(1);
  });

  it("degrades to an all-zero grid rather than throwing on a zero-sized frame", () => {
    const grid = computeGridLuminance(new Uint8ClampedArray(0), 0, 0);
    expect(grid).toHaveLength(GRID_COLS * GRID_ROWS);
    for (const v of grid) expect(v).toBe(0);
  });
});

describe("measureMotion", () => {
  const still = computeGridLuminance(flatFrame(24, 18, 100), 24, 18);

  it("is nothing for the first frame — no evidence yet, not a guess", () => {
    expect(measureMotion(still, null)).toEqual({ changedCellRatio: 0, maxCellDelta: 0, meanDelta: 0 });
    expect(motionEnergyBetween(still, null)).toBe(0);
  });

  it("is nothing between two identical frames", () => {
    expect(measureMotion(still, computeGridLuminance(flatFrame(24, 18, 100), 24, 18)).changedCellRatio).toBe(0);
  });

  it("counts CELLS, so a small strong change is seen where the whole-grid mean is not", () => {
    // One 2×2-cell block changing by 100/255 ≈ 0.39: two-ish cells of 108 change a lot.
    const moved = computeGridLuminance(blockFrame(24, 18, 100, { x0: 0, y0: 0, x1: 4, y1: 4, gray: 200 }), 24, 18);
    const m = measureMotion(moved, still);
    expect(m.maxCellDelta).toBeGreaterThan(CELL_CHANGE_THRESHOLD);
    expect(m.changedCellRatio).toBeGreaterThan(0);
    // The old measure: the same movement is under 1% of the whole grid.
    expect(m.meanDelta).toBeLessThan(0.02);
  });

  it("the changed-cell fraction rises with the size of the changed region", () => {
    const small = measureMotion(computeGridLuminance(blockFrame(24, 18, 100, { x0: 0, y0: 0, x1: 4, y1: 4, gray: 200 }), 24, 18), still);
    const large = measureMotion(computeGridLuminance(blockFrame(24, 18, 100, { x0: 0, y0: 0, x1: 16, y1: 12, gray: 200 }), 24, 18), still);
    expect(large.changedCellRatio).toBeGreaterThan(small.changedCellRatio);
  });
});

describe("activityLevelFor", () => {
  it("buckets at the documented thresholds", () => {
    expect(activityLevelFor(0)).toBe("none");
    expect(activityLevelFor(NONE_ACTIVITY_MAX - 0.001)).toBe("none");
    expect(activityLevelFor(NONE_ACTIVITY_MAX)).toBe("low");
    expect(activityLevelFor(LOW_ACTIVITY_MAX - 0.001)).toBe("low");
    expect(activityLevelFor(LOW_ACTIVITY_MAX)).toBe("medium");
    expect(activityLevelFor(MEDIUM_ACTIVITY_MAX)).toBe("high");
    expect(activityLevelFor(2)).toBe("high");
    expect(activityLevelFor(-1)).toBe("none");
  });
});

describe("derivePresence", () => {
  it("has seen nothing: absent, at very low confidence, never certain", () => {
    const r = derivePresence(evidence({ sampleCount: 0 }));
    expect(r.person_present).toBe(false);
    expect(r.presence_confidence).toBe(0);
    const later = derivePresence(evidence({ sampleCount: 50 }));
    expect(later.person_present).toBe(false);
    expect(later.presence_confidence).toBeLessThanOrEqual(0.2);
  });

  it("the owner's run: a seated owner who moved a few cells 20 s ago is PRESENT", () => {
    const r = derivePresence(evidence({ msSinceLastMotion: 20_000, lastMotionLevel: "low" }));
    expect(r.person_present).toBe(true);
    expect(r.presence_confidence).toBeGreaterThan(0.5);
  });

  it("moving right now is more confident than having moved a while ago", () => {
    const now = derivePresence(evidence({ msSinceLastMotion: 0, lastMotionLevel: "low", currentActivity: "low" }));
    const ago = derivePresence(evidence({ msSinceLastMotion: PRESENCE_MEMORY_MS - 1, lastMotionLevel: "low" }));
    expect(now.person_present).toBe(true);
    expect(ago.person_present).toBe(true);
    expect(now.presence_confidence).toBeGreaterThan(ago.presence_confidence);
  });

  it("a small movement then a long stillness reads as very still, weakly — not as gone", () => {
    const r = derivePresence(evidence({ msSinceLastMotion: PRESENCE_MEMORY_MS + 60_000, lastMotionLevel: "low" }));
    expect(r.person_present).toBe(true);
    expect(r.presence_confidence).toBeLessThan(0.65);
    expect(r.presence_confidence).toBeGreaterThanOrEqual(0.35);
  });

  it("an exit-sized burst then the same stillness reads as absent", () => {
    const r = derivePresence(evidence({ msSinceLastMotion: PRESENCE_MEMORY_MS + 60_000, lastMotionLevel: "high" }));
    expect(r.person_present).toBe(false);
    expect(r.presence_confidence).toBeGreaterThanOrEqual(0.5);
    expect(r.presence_confidence).toBeLessThanOrEqual(ABSENCE_CONFIDENCE_CAP);
  });

  it("even a still owner is called absent once the stillness outlasts the linger window", () => {
    const r = derivePresence(evidence({ msSinceLastMotion: PRESENCE_MEMORY_MS + LINGER_MS + 1, lastMotionLevel: "low" }));
    expect(r.person_present).toBe(false);
  });

  it("absence confidence grows with the silence and never passes the cap", () => {
    const soon = derivePresence(evidence({ msSinceLastMotion: PRESENCE_MEMORY_MS + 1_000, lastMotionLevel: "high" }));
    const long = derivePresence(evidence({ msSinceLastMotion: PRESENCE_MEMORY_MS * 5, lastMotionLevel: "high" }));
    expect(long.presence_confidence).toBeGreaterThan(soon.presence_confidence);
    expect(long.presence_confidence).toBeLessThanOrEqual(ABSENCE_CONFIDENCE_CAP);
  });

  it("confidence is gated by sample count and always within 0..1", () => {
    const one = derivePresence(evidence({ msSinceLastMotion: 0, lastMotionLevel: "low", sampleCount: 1 }));
    const five = derivePresence(evidence({ msSinceLastMotion: 0, lastMotionLevel: "low", sampleCount: 5 }));
    expect(one.presence_confidence).toBeLessThan(five.presence_confidence);
    for (const age of [null, 0, 1e3, 1e5, 1e6, 1e9]) {
      for (const level of ["none", "low", "medium", "high"] as const) {
        const { presence_confidence } = derivePresence(evidence({ msSinceLastMotion: age, lastMotionLevel: level }));
        expect(presence_confidence).toBeGreaterThanOrEqual(0);
        expect(presence_confidence).toBeLessThanOrEqual(1);
      }
    }
  });
});

describe("derivePosture", () => {
  it("is always unknown — this module does no body-pose estimation", () => {
    expect(derivePosture()).toBe("unknown");
  });
});

describe("deriveAwakeState", () => {
  it("is uncertain when nobody is present", () => {
    expect(deriveAwakeState(false, "high", 0)).toBe("uncertain");
  });
  it("is awake on medium/high activity while present", () => {
    expect(deriveAwakeState(true, "medium", 0)).toBe("awake");
    expect(deriveAwakeState(true, "high", 0)).toBe("awake");
  });
  it("is resting only after sustained stillness, not one still moment", () => {
    expect(deriveAwakeState(true, "none", RESTING_STILL_MS - 1)).toBe("uncertain");
    expect(deriveAwakeState(true, "none", RESTING_STILL_MS)).toBe("resting");
  });
  it("low activity is uncertain — genuinely ambiguous, not guessed", () => {
    expect(deriveAwakeState(true, "low", RESTING_STILL_MS * 2)).toBe("uncertain");
  });
});

describe("deriveObservation", () => {
  it("assembles exactly the seven schema fields, honestly, from strong evidence", () => {
    const obs = deriveObservation(
      { ...evidence({ currentActivity: "high", msSinceLastMotion: 0, lastMotionLevel: "high" }), stillDurationMs: 0 },
      NOW,
    );
    expect(Object.keys(obs).toSorted()).toEqual(
      ["activity_level", "awake_state", "observed_at", "person_present", "posture", "presence_confidence", "source"].toSorted(),
    );
    expect(obs.person_present).toBe(true);
    expect(obs.activity_level).toBe("high");
    expect(obs.awake_state).toBe("awake");
    expect(obs.posture).toBe("unknown");
    expect(obs.source).toBe("camera");
    expect(obs.observed_at).toBe(NOW.toISOString());
    expect(obs.presence_confidence).toBeGreaterThan(0);
    expect(obs.presence_confidence).toBeLessThanOrEqual(1);
  });

  it("an empty room over time: absent, low confidence, never fabricated as certain", () => {
    const obs = deriveObservation(
      { ...evidence({ sampleCount: 20 }), stillDurationMs: RESTING_STILL_MS * 3 },
      NOW,
    );
    expect(obs.person_present).toBe(false);
    expect(obs.presence_confidence).toBeLessThanOrEqual(ABSENCE_CONFIDENCE_CAP);
    expect(obs.awake_state).toBe("uncertain"); // nobody there to be resting
    expect(obs.posture).toBe("unknown");
  });

  it("a present owner still for five minutes after a small movement is resting", () => {
    const obs = deriveObservation(
      { ...evidence({ msSinceLastMotion: RESTING_STILL_MS, lastMotionLevel: "low" }), stillDurationMs: RESTING_STILL_MS },
      NOW,
    );
    expect(obs.person_present).toBe(true);
    expect(obs.awake_state).toBe("resting");
  });
});
