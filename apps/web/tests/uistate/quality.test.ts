/**
 * The budget rules that keep the renderer off the critical path.
 *
 * These are cheap tests for a property that is otherwise only observable as
 * "the owner's laptop got hot": the tiers must actually differ, the caps must
 * actually cap, and a failing API must be backed off rather than hammered.
 */

import { describe, expect, it } from "vitest";

import {
  MAX_CAPABILITY_NODES,
  POLL_HIDDEN_MS,
  POLL_VISIBLE_MS,
  QUALITY_TIERS,
  TIER_BUDGETS,
  backoffMs,
  defaultTier,
  detectRenderCapability,
  drawableCount,
  pollIntervalMs,
  sceneBudgetFor,
  shouldRenderFrame,
  tierFor,
} from "../../app/lib/uistate/quality";

describe("tiers", () => {
  it("are strictly ordered in every cost that matters", () => {
    const [high, balanced, low] = QUALITY_TIERS.map((t) => TIER_BUDGETS[t]);
    expect(high.fps).toBeGreaterThan(balanced.fps);
    expect(balanced.fps).toBeGreaterThan(low.fps);
    expect(high.maxSatellites).toBeGreaterThan(balanced.maxSatellites);
    expect(balanced.maxSatellites).toBeGreaterThan(low.maxSatellites);
    expect(high.maxPixelRatio).toBeGreaterThan(low.maxPixelRatio);
    // `low` is a real tier, not a slightly cheaper `high`: it drops whole
    // features rather than scaling them down.
    expect(low.latticeSegments).toBe(0);
    expect(low.glow).toBe(false);
    // M18.1: the layered structure is budgeted the same way.
    expect(high.rings).toBeGreaterThan(balanced.rings);
    expect(balanced.rings).toBeGreaterThanOrEqual(low.rings);
    expect(low.rings).toBeGreaterThanOrEqual(1); // the structure keeps its identity
    expect(high.shells).toBeGreaterThan(balanced.shells);
    expect(high.maxParticles).toBeGreaterThan(balanced.maxParticles);
    expect(low.shells).toBe(0);
    expect(low.maxParticles).toBe(0);
    expect(low.parallax).toBe(false);
    // M18.3: the Living Core's own layers are budgeted the same way, and `low`
    // keeps exactly one orbital layer so the structure stays the same machine.
    expect(high.orbitals).toBeGreaterThan(balanced.orbitals);
    expect(balanced.orbitals).toBeGreaterThan(low.orbitals);
    expect(low.orbitals).toBe(1);
    expect(high.circuitSegments).toBeGreaterThan(balanced.circuitSegments);
    expect(high.fragments).toBeGreaterThan(balanced.fragments);
    expect(high.outerFieldPoints).toBeGreaterThan(balanced.outerFieldPoints);
    // Dropped whole, not shrunk: the three per-instance layers cost nothing at all.
    expect(low.circuitSegments).toBe(0);
    expect(low.fragments).toBe(0);
    expect(low.outerFieldPoints).toBe(0);
  });

  it("declares a scene budget a test can hold the scene to", () => {
    // These are the declared ceilings (M18.3 identity note §5, superseding the
    // M18.1 figures now that the Living Core mounts nine layers). Changing a
    // tier's budget means changing this table on purpose.
    expect(sceneBudgetFor("high")).toEqual({ drawables: 36, instances: 398, maxParticles: 160 });
    expect(sceneBudgetFor("balanced")).toEqual({ drawables: 33, instances: 206, maxParticles: 80 });
    expect(sceneBudgetFor("low")).toEqual({ drawables: 22, instances: 32, maxParticles: 0 });
    for (const tier of QUALITY_TIERS) {
      const budget = sceneBudgetFor(tier);
      const b = TIER_BUDGETS[tier];
      expect(budget.instances).toBe(
        budget.maxParticles +
          b.maxSatellites * 2 +
          MAX_CAPABILITY_NODES +
          b.outerFieldPoints +
          b.fragments,
      );
    }
    expect(sceneBudgetFor("high").drawables).toBeGreaterThan(sceneBudgetFor("low").drawables);
    // The tiers stay strictly ordered in what they may mount, which is the
    // property that makes `low` a real answer for a weak machine.
    expect(sceneBudgetFor("high").instances).toBeGreaterThan(sceneBudgetFor("balanced").instances);
    expect(sceneBudgetFor("balanced").instances).toBeGreaterThan(sceneBudgetFor("low").instances);
  });

  it("caps a reported count without changing what was reported", () => {
    expect(drawableCount(400, "low")).toBe(12);
    expect(drawableCount(400, "high")).toBe(64);
    expect(drawableCount(3, "low")).toBe(3);
    expect(drawableCount(0, "high")).toBe(0);
  });

  it("refuses the high tier where there is no WebGL2", () => {
    expect(tierFor("high", { kind: "webgl2" })).toBe("high");
    expect(tierFor("high", { kind: "webgl1" })).toBe("balanced");
    expect(tierFor("high", { kind: "none", reason: "x" })).toBe("low");
    expect(tierFor("balanced", { kind: "webgl1" })).toBe("balanced");
  });

  it("throttles frames to the tier's budget", () => {
    expect(shouldRenderFrame(0, 10, "high")).toBe(false); // 60fps ≈ 16.7ms
    expect(shouldRenderFrame(0, 20, "high")).toBe(true);
    expect(shouldRenderFrame(0, 20, "balanced")).toBe(false); // 30fps ≈ 33ms
    expect(shouldRenderFrame(0, 40, "balanced")).toBe(true);
    expect(shouldRenderFrame(0, 40, "low")).toBe(false); // 20fps = 50ms
  });

  it("prefers the low tier when motion is reduced or the device is small", () => {
    expect(defaultTier({ reducedMotion: true, deviceMemory: 32, hardwareConcurrency: 32 })).toBe("low");
    expect(defaultTier({ deviceMemory: 16, hardwareConcurrency: 16 })).toBe("high");
    expect(defaultTier({ deviceMemory: 2 })).toBe("low");
    expect(defaultTier({ hardwareConcurrency: 2 })).toBe("low");
    // Unknown hardware is not assumed to be fast.
    expect(defaultTier({})).toBe("balanced");
  });
});

describe("capability detection", () => {
  it("reports 'none' rather than throwing when there is no canvas", () => {
    const capability = detectRenderCapability(() => null);
    expect(capability.kind).toBe("none");
  });

  it("reports 'none' when the context probe throws", () => {
    const capability = detectRenderCapability(() => {
      throw new Error("context oluşturulamadı");
    });
    expect(capability).toEqual({ kind: "none", reason: "context oluşturulamadı" });
  });

  it("finds webgl2 and releases the context it probed with", () => {
    let released = false;
    const canvas = {
      getContext: (kind: string) =>
        kind === "webgl2"
          ? { getExtension: () => ({ loseContext: () => (released = true) }) }
          : null,
    } as unknown as HTMLCanvasElement;
    expect(detectRenderCapability(() => canvas)).toEqual({ kind: "webgl2" });
    // A probe that leaks contexts eventually causes the failure it tests for.
    expect(released).toBe(true);
  });

  it("falls back to webgl1 when webgl2 is unavailable", () => {
    const canvas = {
      getContext: (kind: string) =>
        kind === "webgl" ? { getExtension: () => null } : null,
    } as unknown as HTMLCanvasElement;
    expect(detectRenderCapability(() => canvas)).toEqual({ kind: "webgl1" });
  });
});

describe("polling backs off and goes quiet in the background", () => {
  it("keeps the base interval while healthy", () => {
    expect(backoffMs(0)).toBe(POLL_VISIBLE_MS);
    expect(pollIntervalMs(false, 0)).toBe(POLL_VISIBLE_MS);
  });

  it("backs off exponentially and caps", () => {
    expect(backoffMs(1)).toBe(2 * POLL_VISIBLE_MS);
    expect(backoffMs(3)).toBe(8 * POLL_VISIBLE_MS);
    expect(backoffMs(20)).toBe(30_000);
  });

  it("polls a hidden tab far less often, even when healthy", () => {
    expect(pollIntervalMs(true, 0)).toBeGreaterThanOrEqual(POLL_HIDDEN_MS);
    expect(pollIntervalMs(true, 0)).toBeGreaterThan(pollIntervalMs(false, 0));
  });
});
