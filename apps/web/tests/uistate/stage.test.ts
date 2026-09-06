/**
 * Minimal mode's two spatial promises, held as arithmetic (M18.3 §9).
 *
 * The owner's directive — "the Core must become a full-viewport living visual
 * presence", 60–80 % of the usable viewport — is a claim that a stylesheet
 * edit could quietly falsify. These tests are what stops that: the coverage
 * band is checked across every plausible viewport, portrait through 32:9, and
 * the control cluster's fade is checked as the timer it is.
 */

import { describe, expect, it } from "vitest";

import {
  BAND_TARGET,
  CONTROL_FADED_OPACITY,
  CONTROL_FADE_AFTER_MS,
  COVERAGE_MAX,
  COVERAGE_MIN,
  controlFadeInitial,
  controlFadeReducer,
  controlOpacity,
  controlsAreFaded,
  stageBandFor,
  stageSizeFor,
} from "../../app/lib/uistate/stage";
import { CORE_FILL, CORE_SPAN_RADIUS, VIEW_HALF_EXTENT } from "../../app/lib/uistate/scene";

/** Real viewports, not round numbers: phones, laptops, monitors, a 32:9. */
const VIEWPORTS: Array<[number, number, string]> = [
  [360, 640, "small phone portrait"],
  [390, 844, "phone portrait"],
  [430, 932, "large phone portrait"],
  [844, 390, "phone landscape"],
  [768, 1024, "tablet portrait"],
  [1024, 768, "tablet landscape"],
  [1280, 800, "laptop"],
  [1366, 768, "small laptop"],
  [1440, 900, "laptop"],
  [1920, 1080, "desktop"],
  [2560, 1440, "wide desktop"],
  [3440, 1440, "ultrawide"],
  [3840, 1080, "32:9"],
  [3840, 2160, "4K"],
  [1000, 1000, "square"],
  [900, 1600, "tall portrait monitor"],
];

describe("the Core is given the viewport, at every shape of viewport", () => {
  it("covers 60–80 % of the usable viewport everywhere", () => {
    for (const [w, h, name] of VIEWPORTS) {
      const stage = stageSizeFor(w, h);
      expect(stage.coverage, `${name} ${w}x${h}`).toBeGreaterThanOrEqual(COVERAGE_MIN);
      expect(stage.coverage, `${name} ${w}x${h}`).toBeLessThanOrEqual(COVERAGE_MAX);
    }
  });

  it("never overflows the viewport in either direction", () => {
    for (const [w, h, name] of VIEWPORTS) {
      const stage = stageSizeFor(w, h);
      expect(stage.size, `${name} width`).toBeLessThanOrEqual(w);
      expect(stage.size, `${name} height`).toBeLessThanOrEqual(h);
      expect(stage.size, name).toBeGreaterThan(0);
    }
  });

  it("bands the aspect ratios deliberately", () => {
    expect(stageBandFor(390 / 844)).toBe("portrait");
    expect(stageBandFor(1)).toBe("square");
    expect(stageBandFor(1280 / 800)).toBe("landscape");
    expect(stageBandFor(1920 / 1080)).toBe("landscape");
    expect(stageBandFor(3440 / 1440)).toBe("ultrawide"); // 21:9
    expect(stageBandFor(3840 / 1080)).toBe("ultrawide"); // 32:9
    // Every band aims inside the promised range; the band chooses where in it.
    for (const target of Object.values(BAND_TARGET)) {
      expect(target).toBeGreaterThanOrEqual(COVERAGE_MIN);
      expect(target).toBeLessThanOrEqual(COVERAGE_MAX);
    }
  });

  it("gives a square window more of itself than a 32:9 one", () => {
    // Not a matter of taste: on a very wide window the height is binding, and
    // a Core sized to a square window's share would sit under the caption.
    expect(BAND_TARGET.square).toBeGreaterThan(BAND_TARGET.ultrawide);
    expect(stageSizeFor(1000, 1000).coverage).toBeGreaterThan(
      stageSizeFor(3840, 1080).coverage,
    );
  });

  it("grows with the window rather than sitting in the middle of it", () => {
    const small = stageSizeFor(1280, 800);
    const large = stageSizeFor(2560, 1440);
    expect(large.size).toBeGreaterThan(small.size);
    // A 4K screen gets a 4K Core, not a 1080p one floating in the middle.
    expect(stageSizeFor(3840, 2160).size).toBeGreaterThan(large.size);
  });

  it("answers a degenerate viewport with nothing rather than with a guess", () => {
    expect(stageSizeFor(0, 0).size).toBe(0);
    expect(stageSizeFor(Number.NaN, 800).size).toBe(0);
    expect(stageSizeFor(-100, -100).coverage).toBe(0);
  });

  it("derives the fill from the camera, so the framing and the claim agree", () => {
    expect(CORE_FILL).toBeCloseTo(CORE_SPAN_RADIUS / VIEW_HALF_EXTENT, 10);
    expect(CORE_FILL).toBeGreaterThan(0.8);
    expect(CORE_FILL).toBeLessThan(1);
  });
});

describe("the control cluster recedes on a timer and returns on a movement", () => {
  const tick = (state: ReturnType<typeof controlFadeInitial>, ms: number) =>
    controlFadeReducer(state, { kind: "tick", dtMs: ms });

  it("is present at first and faded after four idle seconds", () => {
    let state = controlFadeInitial();
    expect(controlsAreFaded(state)).toBe(false);
    expect(controlOpacity(state)).toBe(1);

    state = tick(state, CONTROL_FADE_AFTER_MS - 1);
    expect(controlsAreFaded(state)).toBe(false);

    state = tick(state, 1);
    expect(controlsAreFaded(state)).toBe(true);
    expect(controlOpacity(state)).toBe(CONTROL_FADED_OPACITY);
  });

  it("comes back on pointer movement", () => {
    let state = tick(controlFadeInitial(), 10_000);
    expect(controlsAreFaded(state)).toBe(true);
    state = controlFadeReducer(state, { kind: "activity" });
    expect(controlsAreFaded(state)).toBe(false);
    expect(state.idleMs).toBe(0);
  });

  it("never fades while the pointer is over it or focus is inside it", () => {
    let state = controlFadeReducer(controlFadeInitial(), { kind: "hold" });
    state = tick(state, 60_000);
    expect(controlsAreFaded(state)).toBe(false);
    expect(controlOpacity(state)).toBe(1);
    // …and starts counting again only once it is released.
    state = controlFadeReducer(state, { kind: "release" });
    expect(controlsAreFaded(state)).toBe(false);
    state = tick(state, CONTROL_FADE_AFTER_MS);
    expect(controlsAreFaded(state)).toBe(true);
  });

  it("is a pure reducer: same state in, same state out, no identity churn", () => {
    const state = tick(controlFadeInitial(), 10_000);
    // Already saturated: another tick must not allocate a new object.
    expect(tick(tick(state, 1_000), 1_000)).toBe(tick(state, 1_000));
    const active = controlFadeReducer(state, { kind: "activity" });
    expect(controlFadeReducer(active, { kind: "activity" })).toBe(active);
  });

  it("ignores nonsense elapsed times", () => {
    const state = controlFadeInitial();
    expect(tick(state, Number.NaN).idleMs).toBe(0);
    expect(tick(state, -5_000).idleMs).toBe(0);
  });

  it("keeps the faded controls readable rather than invisible", () => {
    // A control that fades to nothing is a control the owner cannot find; the
    // cluster stays at a quarter and stays keyboard-reachable either way.
    expect(CONTROL_FADED_OPACITY).toBeGreaterThan(0.15);
    expect(CONTROL_FADED_OPACITY).toBeLessThan(0.5);
  });
});
