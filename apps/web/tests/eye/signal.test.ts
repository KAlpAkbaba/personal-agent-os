/**
 * The Active Eye's derivation, tested as the pure math it is.
 *
 * No canvas, no video, no browser: `computeGridLuminance` takes a plain
 * `Uint8ClampedArray` the same shape `getImageData` would produce, and every
 * fixture is a generated array (see `fixtures.ts`) — never real imagery, per
 * the M18 task brief.
 */

import { describe, expect, it } from "vitest";

import {
  ABSENCE_CONFIDENCE_CAP,
  GRID_COLS,
  GRID_ROWS,
  LOW_ACTIVITY_MAX,
  MEDIUM_ACTIVITY_MAX,
  NOISE_FLOOR,
  PRESENCE_MOTION_RATIO,
  RESTING_STILL_MS,
  activityLevelFor,
  computeGridLuminance,
  deriveAwakeState,
  deriveObservation,
  derivePosture,
  derivePresence,
  meanLuminance,
  motionEnergyBetween,
} from "../../app/lib/eye/signal";
import { blockFrame, flatFrame } from "./fixtures";

const NOW = new Date("2026-09-06T10:00:00.000Z");

const confidenceAtSampleCount = (n: number) =>
  derivePresence({
    currentMotion: 0.1,
    recentMotionRatio: 0.5,
    sampleCount: n,
    samplesForFullConfidence: 5,
  }).presence_confidence;

describe("computeGridLuminance", () => {
  it("reduces a flat frame to a uniform, correctly-scaled grid", () => {
    const frame = flatFrame(24, 18, 255);
    const grid = computeGridLuminance(frame, 24, 18);
    expect(grid.length).toBe(GRID_COLS * GRID_ROWS);
    for (const v of grid) expect(v).toBeCloseTo(1, 5);
    expect(meanLuminance(grid)).toBeCloseTo(1, 5);
  });

  it("black is zero, and never negative or above one", () => {
    const grid = computeGridLuminance(flatFrame(24, 18, 0), 24, 18);
    for (const v of grid) {
      expect(v).toBeCloseTo(0, 5);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
  });

  it("degrades to an all-zero grid rather than throwing on a zero-sized frame", () => {
    const grid = computeGridLuminance(new Uint8ClampedArray(0), 0, 0);
    expect(grid.length).toBe(GRID_COLS * GRID_ROWS);
    expect(meanLuminance(grid)).toBe(0);
  });
});

describe("motionEnergyBetween", () => {
  it("is zero for the first frame — no evidence yet, not a guess", () => {
    const grid = computeGridLuminance(flatFrame(24, 18, 128), 24, 18);
    expect(motionEnergyBetween(grid, null)).toBe(0);
  });

  it("is zero between two identical frames", () => {
    const a = computeGridLuminance(flatFrame(24, 18, 128), 24, 18);
    const b = computeGridLuminance(flatFrame(24, 18, 128), 24, 18);
    expect(motionEnergyBetween(a, b)).toBe(0);
  });

  it("rises with the size of a changed region, monotonically", () => {
    const still = computeGridLuminance(flatFrame(24, 18, 40), 24, 18);
    const small = computeGridLuminance(
      blockFrame(24, 18, 40, { x0: 0, y0: 0, x1: 4, y1: 4, gray: 220 }),
      24,
      18,
    );
    const large = computeGridLuminance(
      blockFrame(24, 18, 40, { x0: 0, y0: 0, x1: 20, y1: 16, gray: 220 }),
      24,
      18,
    );
    const m1 = motionEnergyBetween(small, still);
    const m2 = motionEnergyBetween(large, still);
    expect(m1).toBeGreaterThan(0);
    expect(m2).toBeGreaterThan(m1);
  });
});

describe("activityLevelFor", () => {
  it("buckets at the documented thresholds", () => {
    expect(activityLevelFor(0)).toBe("none");
    expect(activityLevelFor(NOISE_FLOOR - 0.001)).toBe("none");
    expect(activityLevelFor(NOISE_FLOOR)).toBe("low");
    expect(activityLevelFor(LOW_ACTIVITY_MAX - 0.001)).toBe("low");
    expect(activityLevelFor(LOW_ACTIVITY_MAX)).toBe("medium");
    expect(activityLevelFor(MEDIUM_ACTIVITY_MAX - 0.001)).toBe("medium");
    expect(activityLevelFor(MEDIUM_ACTIVITY_MAX)).toBe("high");
    expect(activityLevelFor(1)).toBe("high");
  });
});

describe("derivePresence", () => {
  it("reports absent, at low confidence, with no evidence at all", () => {
    const { person_present, presence_confidence } = derivePresence({
      currentMotion: 0,
      recentMotionRatio: 0,
      sampleCount: 0,
      samplesForFullConfidence: 5,
    });
    expect(person_present).toBe(false);
    expect(presence_confidence).toBeLessThan(0.3);
  });

  it("never claims a fabricated confidence: absence confidence is capped", () => {
    const { person_present, presence_confidence } = derivePresence({
      currentMotion: 0,
      recentMotionRatio: 0,
      sampleCount: 50,
      samplesForFullConfidence: 5,
    });
    expect(person_present).toBe(false);
    expect(presence_confidence).toBeLessThanOrEqual(ABSENCE_CONFIDENCE_CAP);
  });

  it("reports present once the trailing window clears the motion ratio", () => {
    const { person_present, presence_confidence } = derivePresence({
      currentMotion: 0.1,
      recentMotionRatio: PRESENCE_MOTION_RATIO,
      sampleCount: 5,
      samplesForFullConfidence: 5,
    });
    expect(person_present).toBe(true);
    expect(presence_confidence).toBeGreaterThan(0.3);
  });

  it("a single strong current-frame motion is enough, even with an empty history", () => {
    const { person_present } = derivePresence({
      currentMotion: LOW_ACTIVITY_MAX,
      recentMotionRatio: 0,
      sampleCount: 1,
      samplesForFullConfidence: 5,
    });
    expect(person_present).toBe(true);
  });

  it("confidence rises with sample count for the same signal", () => {
    expect(confidenceAtSampleCount(1)).toBeLessThan(confidenceAtSampleCount(5));
  });

  it("confidence is always within 0..1", () => {
    for (const currentMotion of [0, 0.5, 1, 2, -1]) {
      for (const recentMotionRatio of [0, 0.5, 1, 2, -1]) {
        const { presence_confidence } = derivePresence({
          currentMotion,
          recentMotionRatio,
          sampleCount: 10,
          samplesForFullConfidence: 5,
        });
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
    expect(deriveAwakeState(false, "none", RESTING_STILL_MS * 2)).toBe("uncertain");
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
    expect(deriveAwakeState(true, "low", 0)).toBe("uncertain");
  });
});

describe("deriveObservation", () => {
  it("assembles exactly the seven schema fields, honestly, from strong evidence", () => {
    const obs = deriveObservation(
      {
        currentMotion: 0.2,
        recentMotionRatio: 0.8,
        sampleCount: 10,
        samplesForFullConfidence: 5,
        stillDurationMs: 0,
      },
      NOW,
    );
    expect(Object.keys(obs).toSorted()).toEqual(
      [
        "activity_level",
        "awake_state",
        "observed_at",
        "person_present",
        "posture",
        "presence_confidence",
        "source",
      ].toSorted(),
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
      {
        currentMotion: 0,
        recentMotionRatio: 0,
        sampleCount: 20,
        samplesForFullConfidence: 5,
        stillDurationMs: RESTING_STILL_MS * 3,
      },
      NOW,
    );
    expect(obs.person_present).toBe(false);
    expect(obs.presence_confidence).toBeLessThanOrEqual(ABSENCE_CONFIDENCE_CAP);
    expect(obs.awake_state).toBe("uncertain"); // nobody there to be resting
    expect(obs.posture).toBe("unknown");
  });
});
