/**
 * B23 req 722/723: the renderer answers to what the machine actually does.
 *
 * Both requirements were filed "kısmi", and both were partial in the same way: the
 * mechanism existed and was decided ONCE.
 *
 * * **722** — three tiers with real budgets, a persisted owner choice, a capability cap
 *   (`tierFor`) and controls on both Core surfaces. Nothing ever compared the tier to the
 *   frames the machine delivered, so a Core that stuttered stayed stuttering until the
 *   owner noticed the control and guessed.
 * * **723** — `CoreFallback2D` is a fallback in fidelity and not in truth, and
 *   `detectRenderCapability()` chooses it correctly… on mount, once. A context lost to a
 *   driver reset, a laptop switching GPUs or a browser reclaiming a backgrounded context
 *   left a dead canvas on screen, still claiming to be the Core.
 */
import { describe, expect, it } from "vitest";

import {
  FRAME_HEALTH_MIN_SAMPLES,
  SLOW_FRAME_FACTOR,
  SLOW_FRAME_SHARE,
  TIER_BUDGETS,
  degradedTier,
  frameHealthFrom,
  tierFor,
} from "../../app/lib/uistate/quality";

/** `n` intervals of `ms`, as a rAF sampler would have collected them. */
function intervals(ms: number, n: number): number[] {
  return Array.from({ length: n }, () => ms);
}

describe("req 722: the tier the machine can hold", () => {
  it("says nothing until it has seen enough frames", () => {
    // A cold start is always slow - shaders compile, the first layout runs - and a tier
    // dropped on the strength of that would punish every launch.
    const tooFew = frameHealthFrom(intervals(200, FRAME_HEALTH_MIN_SAMPLES - 1), "high");
    expect(degradedTier("high", tooFew)).toBeNull();
    expect(tooFew.samples).toBe(FRAME_HEALTH_MIN_SAMPLES - 1);
  });

  it("steps down one level when most frames miss the budget", () => {
    const budgetMs = 1000 / TIER_BUDGETS.high.fps;
    const slow = frameHealthFrom(intervals(budgetMs * (SLOW_FRAME_FACTOR + 0.5), 120), "high");

    expect(slow.slowFrames / slow.samples).toBeGreaterThan(SLOW_FRAME_SHARE);
    expect(degradedTier("high", slow)).toBe("balanced");
    // One step at a time: `balanced` is what this machine gets next, and the measurement
    // taken at that tier decides whether it goes further.
    expect(degradedTier("balanced", frameHealthFrom(intervals(200, 120), "balanced"))).toBe("low");
  });

  it("never goes below low, and never steps back up on its own", () => {
    const terrible = frameHealthFrom(intervals(500, 120), "low");
    expect(degradedTier("low", terrible)).toBeNull();

    // A machine comfortably inside its budget is left where the OWNER put it: stepping up
    // would fight their choice and would oscillate the moment the machine got busy.
    const easy = frameHealthFrom(intervals(4, 120), "balanced");
    expect(degradedTier("balanced", easy)).toBeNull();
  });

  it("a handful of slow frames is not a verdict", () => {
    const budgetMs = 1000 / TIER_BUDGETS.balanced.fps;
    const mostlyFine = [...intervals(budgetMs, 100), ...intervals(budgetMs * 4, 20)];
    const health = frameHealthFrom(mostlyFine, "balanced");

    expect(health.slowFrames).toBe(20);
    expect(degradedTier("balanced", health)).toBeNull();
  });

  it("reports the median a person would call the frame rate", () => {
    const health = frameHealthFrom([10, 16, 16, 17, 100], "balanced");
    expect(health.medianMs).toBe(16);
  });

  it("the capability cap still comes first", () => {
    // The measured degrade is applied to the CAPPED tier, so a WebGL1 machine that also
    // stutters ends at `low` rather than at `balanced`.
    const capped = tierFor("high", { kind: "webgl1" });
    expect(capped).toBe("balanced");
    expect(degradedTier(capped, frameHealthFrom(intervals(200, 120), capped))).toBe("low");
  });
});

describe("req 723: a context that goes away after the probe", () => {
  it("the fallback is chosen for a machine with no WebGL at all", () => {
    expect(tierFor("high", { kind: "none", reason: "WebGL bağlamı oluşturulamadı" })).toBe("low");
  });

  it("CoreView listens for the loss rather than probing once", async () => {
    // The behaviour lives in a browser event, so this reads the source for the two things
    // that make the difference: the listener, and `preventDefault` — without which the
    // browser never restores the context and the fallback would be permanent.
    const fs = await import("node:fs/promises");
    const canvas = await fs.readFile(
      new URL("../../app/core/CoreCanvas.tsx", import.meta.url),
      "utf8",
    );
    expect(canvas).toContain('addEventListener("webglcontextlost"');
    expect(canvas).toContain("event.preventDefault()");
    expect(canvas).toContain('addEventListener("webglcontextrestored"');

    const view = await fs.readFile(new URL("../../app/core/CoreView.tsx", import.meta.url), "utf8");
    // The lost context joins the two conditions that already chose the 2D view.
    expect(view).toContain("contextLost !== null");
    expect(view).toContain("WebGL bağlamı kayboldu");
  });
});
