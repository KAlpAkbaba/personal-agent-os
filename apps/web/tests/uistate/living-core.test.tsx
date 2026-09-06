/**
 * The Living Core's identity, and the bounds it is drawn inside (M18.3, ADR-0070).
 *
 * The owner's directive for this milestone was spatial and visual — a dense
 * gold cognitive machine that owns the viewport, not a small purple wireframe
 * ball. That is a claim about pixels, and the only parts of it that can be
 * held honestly in Node are these:
 *
 * 1. the layers are all there, at every tier that budgets them, and each one
 *    is dropped WHOLE at the tier below rather than quietly shrunk;
 * 2. the richer structure did not buy itself any new right to move — every new
 *    layer is still at zero when nothing was reported;
 * 3. the scene cannot mount more objects than `sceneBudgetFor` says (read off
 *    the scene's own source, so a new layer added without a budget line fails
 *    here rather than on the owner's laptop);
 * 4. a hidden tab draws nothing at all.
 *
 * `react-dom/server` only. No browser, no WebGL, no Playwright.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import CoreCanvas from "../../app/core/CoreCanvas";
import CoreFallback2D from "../../app/core/CoreFallback2D";
import { QUALITY_TIERS, type QualityTier, TIER_BUDGETS, sceneBudgetFor } from "../../app/lib/uistate/quality";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import { CORE_FILL, ORBITAL_RATES } from "../../app/lib/uistate/scene";
import {
  AGENT_IDLE,
  ALARM_ARMED,
  ALARM_GREETING,
  ALARM_PLAYING,
  ALARM_STOPPED,
  SELFMODEL_THINKING,
  T0,
  VOICE_SPEAKING,
  event,
  resetSequence,
  response,
} from "./fixtures";

function intentFor(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return visualFor(applyResponse(emptyTruth(), response(events), T0), at);
}

function core(events: ReturnType<typeof event>[], tier: QualityTier = "high", still = false) {
  return renderToStaticMarkup(
    <CoreFallback2D intent={intentFor(events)} tier={tier} still={still} />,
  );
}

const SOURCE = readFileSync(
  resolve(__dirname, "../../app/core/CoreScene.tsx"),
  "utf8",
);

describe("the Core is a machine, not a ball", () => {
  it("draws every layer of the identity at the high tier", () => {
    const html = core([AGENT_IDLE()]);
    for (const layer of [
      "core-outer", // outer field
      "core-shells", // containment + topology shells
      "core-orbitals", // independent orbital layers
      "core-circuit", // data / circuit layer
      "core-fragments", // processor structures
      "core-chamber", // energy chamber
      "core-rings", // the topology rings
      "core-body", // the nucleus
      "core-nucleus-halo", // the restrained bloom
    ]) {
      expect(html, layer).toContain(layer);
    }
  });

  it("drops the per-instance layers whole at the low tier, and keeps the machine", () => {
    const low = core([AGENT_IDLE()], "low");
    // Gone entirely rather than drawn small:
    expect(low).not.toContain("core-outer");
    expect(low).not.toContain("core-circuit");
    expect(low).not.toContain("core-fragments");
    expect(low).not.toContain("core-shells");
    expect(low).not.toContain("core-nucleus-halo");
    // Still recognisably the same machine:
    expect(low).toContain('data-orbitals="1"');
    expect(low).toContain('data-rings="1"');
    expect(low).toContain("core-chamber");
    expect(low).toContain("core-body");
  });

  it("gives each tier the orbital count its budget declares", () => {
    expect(core([AGENT_IDLE()], "high")).toContain('data-orbitals="3"');
    expect(core([AGENT_IDLE()], "balanced")).toContain('data-orbitals="2"');
    expect(core([AGENT_IDLE()], "low")).toContain('data-orbitals="1"');
  });

  it("turns the orbital layers only at a reported spin, and against the rings", () => {
    const idle = core([AGENT_IDLE()]);
    expect(idle).toContain('data-orbiting="yes"');
    // 11 s / 0.05 — a minute-scale drift, part of the same reported-idle claim
    // as the breath, and slower than the internal rings' own 140 s figure.
    expect(idle).toMatch(/--orbit-dur:220s/);

    const untold = core([]);
    expect(untold).toContain('data-orbiting="no"');
    expect(untold).toMatch(/--orbit-dur:0s/);

    // Different rates AND different directions: no two layers ever beat together.
    expect(new Set(ORBITAL_RATES).size).toBe(ORBITAL_RATES.length);
    expect(ORBITAL_RATES.some((r) => r < 0)).toBe(true);
    expect(ORBITAL_RATES.some((r) => r > 0)).toBe(true);
  });

  it("is still in every new layer when nothing was reported", () => {
    const untold = core([]);
    expect(untold).toContain('data-glow="0"');
    expect(untold).toContain('data-orbiting="no"');
    expect(untold).toContain('data-spinning="no"');
    expect(untold).toContain('data-breathing="no"');
    expect(untold).toContain('data-wake-surge="0"');
    // The transport dots are a reported flow, not a decoration.
    expect(untold).not.toContain("core-transport");
  });

  it("carries particle transport only at a reported flow", () => {
    expect(core([SELFMODEL_THINKING()])).toContain("core-transport");
    expect(core([AGENT_IDLE()])).not.toContain("core-transport");
  });

  it("freezes every animation when motion is reduced", () => {
    const still = core([SELFMODEL_THINKING()], "high", true);
    expect(still).toContain('data-still="yes"');
    expect(still).toContain('data-orbiting="no"');
    expect(still).toContain('data-spinning="no"');
    expect(still).toContain('data-breathing="no"');
  });
});

describe("the wake surge is drawn beside the Core, never as it", () => {
  it("appears while an alarm sounds and nowhere else", () => {
    expect(core([ALARM_PLAYING()])).toContain("core-wake");
    expect(core([ALARM_GREETING()])).toContain("core-wake");
    expect(core([ALARM_ARMED()])).not.toContain("core-wake");
    expect(core([ALARM_STOPPED()])).not.toContain("core-wake");
    expect(core([AGENT_IDLE()])).not.toContain("core-wake");
  });

  it("states the stage it was drawn from", () => {
    const html = core([ALARM_PLAYING()]);
    expect(html).toContain('data-wake-stage="playing"');
    expect(html).toContain('data-wake-ring="playing"');
    expect(html).toMatch(/data-wake-surge="0\.\d+"/);
  });

  it("does not take the Core's own state away from it", () => {
    const html = core([SELFMODEL_THINKING(), ALARM_PLAYING()]);
    expect(html).toContain('data-core-kind="thinking"');
    expect(html).toContain("core-wake");
    expect(html).toContain("core-lattice"); // the thinking is still drawn
  });

  it("is not the speaking pulse, and does not become one", () => {
    const speaking = core([VOICE_SPEAKING(0.8)]);
    expect(speaking).toContain("core-pulse");
    expect(speaking).not.toContain("core-wake");
    const alarm = core([ALARM_PLAYING()]);
    expect(alarm).toContain("core-wake");
    expect(alarm).not.toContain("core-pulse");
  });
});

// ------------------------------------------------------- structural bounds

/** Drawable three.js elements, as they appear in the scene's JSX. */
const DRAWABLE_TAG = /<(mesh|lineSegments|lineLoop|instancedMesh|points)\b/g;

/** Layers mounted through a `.map`, and the most copies each can produce. */
const MAPPED_LAYERS: Array<[string, (b: (typeof TIER_BUDGETS)[QualityTier]) => number]> = [
  ["shellGeometries.map", (b) => b.shells],
  ["orbitalGeometries.map", (b) => b.orbitals],
  ["ringGeometries.map", (b) => b.rings],
  ["length: intent.constructionLayer", () => 4],
];

describe("the scene cannot mount more than its budget", () => {
  const render = SOURCE.slice(SOURCE.indexOf("// ------------------------------------------------------------ render"));

  it("mounts at most `sceneBudgetFor(high).drawables` objects", () => {
    const tags = render.match(DRAWABLE_TAG)?.length ?? 0;
    expect(tags).toBeGreaterThan(20); // the machine really is made of parts
    const high = TIER_BUDGETS.high;
    // Each mapped layer contributes its budget rather than the one tag it is
    // written as. This is the number the scene can actually reach.
    let mounted = tags;
    for (const [marker, count] of MAPPED_LAYERS) {
      expect(SOURCE, marker).toContain(marker);
      mounted += count(high) - 1;
    }
    expect(mounted).toBeLessThanOrEqual(sceneBudgetFor("high").drawables);
  });

  it("gates every tier-budgeted layer on its own budget field", () => {
    for (const gate of [
      "budget.outerFieldPoints > 0 &&",
      "budget.circuitSegments > 0 &&",
      "budget.fragments > 0 &&",
      "budget.latticeSegments > 0 &&",
      "{budget.glow && (",
    ]) {
      expect(SOURCE, gate).toContain(gate);
    }
  });

  it("instances the crowds and merges the lines, rather than mounting each one", () => {
    // Particles, fragments and the outer field are instanced; the paths and the
    // circuitry are one merged buffer each. That is what keeps the drawable
    // count a constant while the instance count is in the hundreds.
    for (const marker of [
      "ref={outerFieldRef}",
      "ref={fragmentsRef}",
      "ref={inwardRef}",
      "ref={travellersRef}",
      "geometry={circuitGeometry}",
      "geometry={latticeGeometry}",
    ]) {
      expect(SOURCE, marker).toContain(marker);
    }
    for (const tier of QUALITY_TIERS) {
      const budget = sceneBudgetFor(tier);
      const b = TIER_BUDGETS[tier];
      expect(budget.instances, tier).toBe(
        b.maxParticles + b.maxSatellites * 2 + 8 + b.outerFieldPoints + b.fragments,
      );
    }
  });

  it("uses no post-processing library and no new dependency for the bloom", () => {
    // The "restrained bloom" is two additive/back-face shells and a CSS blur in
    // the 2D view. Nothing here pulls in a post-processing pass.
    expect(SOURCE).not.toContain("postprocessing");
    expect(SOURCE).not.toContain("EffectComposer");
    expect(SOURCE).not.toContain("UnrealBloom");
    expect(SOURCE).toContain("AdditiveBlending");
  });
});

describe("a hidden tab draws nothing", () => {
  it("puts the canvas in demand mode and says so in the markup", () => {
    const intent = intentFor([SELFMODEL_THINKING()]);
    const hidden = renderToStaticMarkup(
      <CoreCanvas intent={intent} tier="high" still={false} hidden />,
    );
    expect(hidden).toContain('data-hidden="yes"');
    expect(hidden).toContain('data-still="yes"');

    const visible = renderToStaticMarkup(
      <CoreCanvas intent={intent} tier="high" still={false} hidden={false} />,
    );
    expect(visible).toContain('data-hidden="no"');
    expect(visible).toContain('data-still="no"');
  });

  it("returns from the frame before any arithmetic", () => {
    // The structural half of the same claim: whatever the canvas mode, the
    // frame body itself refuses to run.
    expect(SOURCE).toContain("if (hidden || still) return;");
    // …and the reduced-motion settle does not fire for a hidden tab either.
    expect(SOURCE).toContain("if (!still || hidden) return;");
  });

  it("keeps the camera framing and the layout's coverage claim in step", () => {
    // `stageSizeFor` sizes the stage from `CORE_FILL`, which is derived from
    // the camera; the canvas must actually use that camera.
    const canvas = readFileSync(resolve(__dirname, "../../app/core/CoreCanvas.tsx"), "utf8");
    expect(canvas).toContain("position: [0, 0, CAMERA_DISTANCE], fov: CAMERA_FOV_DEG");
    expect(CORE_FILL).toBeGreaterThan(0);
  });
});
