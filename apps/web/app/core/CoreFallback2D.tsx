/**
 * The Core without a GPU.
 *
 * This is a *fallback in fidelity, not in truth*: it shows every fact the 3D
 * view shows, from the same `VisualIntent`, and the readout beside it is
 * literally the same component. An owner on a machine with no WebGL is not
 * given a lesser account of what the system is doing — and, since M18.1, not
 * a lesser identity either: the same layered structure (nucleus, internal
 * rings, structural shells, connection paths, constellation, capability
 * nodes, aperture) is drawn here in static SVG at the same proportions as the
 * 3D scene, from the same constants.
 *
 * Implementation notes that matter:
 *
 * - **Pure SVG, animated by CSS only.** No `requestAnimationFrame`, no JS
 *   timers. That keeps it off the main thread's critical path, lets it render
 *   identically on the server, and makes the whole thing assertable as static
 *   markup in Node — which is why this file, not the 3D scene, carries the
 *   rendering tests.
 * - **No randomness.** Every node position is a deterministic function of its
 *   index, so server and client markup match and a test can name a coordinate.
 * - **Zero channels draw nothing.** A zero `breathAmplitude` emits no animation
 *   at all rather than an animation of amplitude zero; a zero `ringSpin` emits
 *   no rotation. "Still" is visible in the markup and cannot drift back into
 *   motion by CSS default.
 */

import { type VisualIntent, releasePalette } from "../lib/uistate/visual";
import {
  MAX_CAPABILITY_NODES,
  type QualityTier,
  TIER_BUDGETS,
  drawableCount,
} from "../lib/uistate/quality";
import {
  CAPABILITY_RADIUS,
  CONSTELLATION_RADIUS,
  FIELD_RADIUS,
  INWARD_END,
  INWARD_START,
} from "../lib/uistate/scene";

const VIEW = 320;
const CENTER = VIEW / 2;
/** The nucleus at scale 1, in SVG units. */
const BASE_RADIUS = 48;
/** The 3D scene's nucleus radius; every other radius is stated relative to it. */
const NUCLEUS_WORLD = 0.62;

/** World radii of the 3D scene (see CoreScene.tsx), so the two views agree. */
const RING_WORLD = [0.84, 1.0, 1.16];
const SHELL_WORLD = [1.3, 1.52];
const LATTICE_WORLD = 1.22;
const EYE_WORLD = 1.64;
const HELD_WORLD = 1.28;
const ERROR_WORLD = 1.12;
const RELEASE_WORLD = 1.7;

export type CoreFallback2DProps = {
  intent: VisualIntent;
  tier: QualityTier;
  /** Freeze all motion (prefers-reduced-motion, or a hidden tab). */
  still?: boolean;
  /** Why the 2D view is being used, when it is not merely the chosen tier. */
  reason?: string | null;
};

/** Evenly spaced points on a circle. Deterministic by construction. */
function ring(count: number, radius: number, phase = 0) {
  const points: Array<{ x: number; y: number; angle: number }> = [];
  for (let i = 0; i < count; i += 1) {
    const angle = phase + (i / Math.max(1, count)) * Math.PI * 2;
    points.push({
      x: CENTER + Math.cos(angle) * radius,
      y: CENTER + Math.sin(angle) * radius,
      angle,
    });
  }
  return points;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

export default function CoreFallback2D({
  intent,
  tier,
  still = false,
  reason = null,
}: CoreFallback2DProps) {
  const budget = TIER_BUDGETS[tier];
  const radius = round(BASE_RADIUS * intent.scale);
  /** SVG radius for a 3D world radius, at the intent's scale. */
  const world = (r: number) => round((r / NUCLEUS_WORLD) * radius);
  const opacity = round(1 - intent.dim * 0.75);
  const animate = !still && intent.breathAmplitude > 0 && intent.breathHz > 0;
  const spinning = !still && intent.ringSpin > 0;

  const drawnNodes = intent.sourceNodesKnown ? drawableCount(intent.sourceNodes, tier) : 0;
  const drawnConstellation = drawableCount(intent.constellationNodes, tier);
  const drawnField = drawableCount(intent.fieldNodes, tier);
  const drawnCapabilities = Math.min(MAX_CAPABILITY_NODES, Math.max(0, Math.floor(intent.capabilityNodes)));
  const constellation = ring(drawnConstellation, world(CONSTELLATION_RADIUS));
  const field = ring(drawnField, world(FIELD_RADIUS), 0.37);
  const capabilities = ring(MAX_CAPABILITY_NODES, world(CAPABILITY_RADIUS)).slice(0, drawnCapabilities);

  // The internal rings: the tier's count, innermost first, each contracted by
  // the inward flow as in the 3D scene.
  const ringRadii = RING_WORLD.slice(0, budget.rings).map((r) => round(world(r) * (1 - 0.22 * intent.inwardFlow)));
  // The structural shells stand off by the reported spread.
  const shellRadii = SHELL_WORLD.slice(0, budget.shells).map((r, i) =>
    round(world(r) * (1 + intent.shellSpread * (0.16 + i * 0.1))),
  );

  // The connection paths: chords across the interior, count bounded by the
  // tier. `low` has zero lattice segments, so the tier genuinely does less
  // work rather than doing the same work with smaller numbers.
  const latticeCount =
    intent.topology > 0 ? Math.round(intent.topology * (budget.latticeSegments / 4)) : 0;
  const lattice = ring(latticeCount * 2, world(LATTICE_WORLD));

  // Listening / recall: inward ticks from the outer start towards the nucleus.
  // Length tracks the reported inward flow; the owner's measured voice is
  // stated on the group.
  const tickCount = intent.inwardFlow > 0 ? Math.min(16, budget.maxSatellites) : 0;
  const tickStart = world(INWARD_START);
  const tickEnd = round(tickStart - intent.inwardFlow * (tickStart - world(INWARD_END)) * 0.7);
  const ticks = ring(tickCount, tickStart);

  const style: React.CSSProperties & Record<string, string | number> = {
    "--breath-amp": intent.breathAmplitude,
    "--breath-dur": intent.breathHz > 0 ? `${round(1 / intent.breathHz)}s` : "0s",
    "--core-opacity": opacity,
    "--core-glow": round(intent.glow),
    // One turn per (1 / ringSpin) * 7 s: the idle drift is a slow minute-scale
    // turn, full thinking a few seconds. Zero spin emits no animation at all.
    "--ring-dur": intent.ringSpin > 0 ? `${round(7 / intent.ringSpin)}s` : "0s",
  };

  return (
    <div
      className="core-2d"
      data-render-mode="2d"
      data-tier={tier}
      data-still={still ? "yes" : "no"}
      data-core-kind={intent.kind}
      style={style}
    >
      <svg
        viewBox={`0 0 ${VIEW} ${VIEW}`}
        role="img"
        aria-label={`Ajan çekirdeği: ${intent.kind}`}
        className={`core-svg palette-${intent.palette}`}
        data-radius={radius}
        data-opacity={opacity}
        data-glow={round(intent.glow)}
        data-shell-spread={round(intent.shellSpread)}
        data-ring-spin={round(intent.ringSpin)}
        data-flow-rate={round(intent.flowRate)}
      >
        {/* The structural shells: translucent, standing off by the spread. */}
        {shellRadii.length > 0 && (
          <g className="core-shells" data-shells={shellRadii.length}>
            {shellRadii.map((r, i) => (
              <circle key={`shell-${i}`} cx={CENTER} cy={CENTER} r={r} strokeDasharray={i === 0 ? undefined : "2 5"} />
            ))}
          </g>
        )}

        {/* Speaking: an outer pulse ring, sized by the reported energy only. */}
        {intent.pulse > 0 && (
          <circle
            className="core-pulse"
            cx={CENTER}
            cy={CENTER}
            r={round(world(1.28) + intent.pulse * world(0.45))}
            data-pulse={round(intent.pulse)}
          />
        )}

        {/* Error: one offset ring. Bounded, static offset — never a strobe. */}
        {intent.agitation > 0 && (
          <circle
            className="core-agitation"
            cx={round(CENTER + intent.agitation * 6)}
            cy={CENTER}
            r={world(ERROR_WORLD)}
            data-agitation={round(intent.agitation)}
          />
        )}

        {/* Waiting on the owner: a held, dashed boundary. */}
        {intent.restraint > 0 && (
          <circle
            className="core-restraint"
            cx={CENTER}
            cy={CENTER}
            r={world(HELD_WORLD)}
            data-restraint={round(intent.restraint)}
          />
        )}

        {/* The eye's aperture: one thin ring while eye.active is current. */}
        {intent.eyeActive > 0 && (
          <ellipse
            className="core-eye"
            cx={CENTER}
            cy={CENTER}
            rx={world(EYE_WORLD)}
            ry={round(world(EYE_WORLD) * 0.36)}
            transform={`rotate(-22 ${CENTER} ${CENTER})`}
            data-eye-active="yes"
          />
        )}

        {/* The internal rings: the topology layers, each on its own tilt,
            turning at the reported spin. */}
        <g
          className={`core-rings ${spinning ? "spinning" : ""}`}
          data-rings={ringRadii.length}
          data-spinning={spinning ? "yes" : "no"}
        >
          {ringRadii.map((r, i) => (
            <ellipse
              key={`ring-${i}`}
              cx={CENTER}
              cy={CENTER}
              rx={r}
              ry={round(r * (i === 0 ? 1 : i === 1 ? 0.62 : 0.8))}
              transform={`rotate(${i * 55 - 30} ${CENTER} ${CENTER})`}
            />
          ))}
        </g>

        {/* The core itself: the nucleus, breathing only when told to. */}
        <circle
          className={`core-body ${animate ? "breathing" : ""}`}
          cx={CENTER}
          cy={CENTER}
          r={radius}
          data-breathing={animate ? "yes" : "no"}
        />

        {/* Thinking / work: the connection paths, and the flow along them
            stated as a figure on the group. */}
        {latticeCount > 0 && (
          <g className="core-lattice" data-lattice-segments={latticeCount} data-flow-rate={round(intent.flowRate)}>
            {Array.from({ length: latticeCount }, (_, i) => {
              const a = lattice[i];
              const b = lattice[(i + latticeCount) % lattice.length];
              return (
                <line
                  key={`lat-${i}`}
                  x1={round(a.x)}
                  y1={round(a.y)}
                  x2={round(b.x)}
                  y2={round(b.y)}
                />
              );
            })}
          </g>
        )}

        {/* Listening / recall: energy drawn inward. */}
        {tickCount > 0 && (
          <g
            className="core-inward"
            data-inward-ticks={tickCount}
            data-inward-flow={round(intent.inwardFlow)}
            data-owner-voice={round(intent.ownerVoice)}
          >
            {ticks.map((p, i) => (
              <line
                key={`tick-${i}`}
                x1={round(p.x)}
                y1={round(p.y)}
                x2={round(CENTER + Math.cos(p.angle) * tickEnd)}
                y2={round(CENTER + Math.sin(p.angle) * tickEnd)}
              />
            ))}
          </g>
        )}

        {/* Memory: a convergence ring, drawn only against real progress. */}
        {intent.convergenceKnown && (
          <circle
            className="core-convergence"
            cx={CENTER}
            cy={CENTER}
            r={world(1.45 - intent.convergence * 0.9)}
            data-convergence={round(intent.convergence)}
          />
        )}

        {/* Research: the constellation. Counted, it is one node per source the
            publisher counted (`core-sources`); uncounted, it is the fixed
            motif, marked as a representation and never as a count. */}
        {drawnConstellation > 0 && (
          <g
            className="core-constellation"
            data-constellation={intent.sourceNodesKnown ? "counted" : "motif"}
            data-constellation-drift={round(intent.constellationDrift)}
          >
            <g className="core-spokes">
              {constellation.map((p, i) => (
                <line
                  key={`spoke-${i}`}
                  x1={round(p.x)}
                  y1={round(p.y)}
                  x2={round(CENTER + (p.x - CENTER) * 0.86)}
                  y2={round(CENTER + (p.y - CENTER) * 0.86)}
                />
              ))}
            </g>
            {intent.sourceNodesKnown ? (
              <g className="core-sources" data-drawn-nodes={drawnNodes}>
                {constellation.map((p, i) => (
                  <circle key={`src-${i}`} cx={round(p.x)} cy={round(p.y)} r={3.5} />
                ))}
              </g>
            ) : (
              <g className="core-motif" data-motif-nodes={drawnConstellation}>
                {constellation.map((p, i) => (
                  <circle key={`motif-${i}`} cx={round(p.x)} cy={round(p.y)} r={3} />
                ))}
              </g>
            )}
          </g>
        )}

        {/* Research: the wider field of candidates the kept evidence came from. */}
        {drawnField > 0 && (
          <g className="core-field" data-field-nodes={drawnField}>
            {field.map((p, i) => (
              <circle key={`field-${i}`} cx={round(p.x)} cy={round(p.y)} r={2} />
            ))}
          </g>
        )}

        {/* Evolution: the construction layers reached so far. */}
        {intent.constructionLayer > 0 && (
          <g className="core-construction" data-construction-layer={intent.constructionLayer}>
            {Array.from({ length: intent.constructionLayer }, (_, i) => (
              <circle
                key={`layer-${i}`}
                cx={CENTER}
                cy={CENTER}
                r={world(1.35 + i * 0.16)}
                strokeDasharray="6 10"
              />
            ))}
          </g>
        )}

        {/* The release orbit (M18 spec §15): drawn whenever a release stage is
            current; it FILLS only against real progress. An unknown-length
            release is a dashed ring, never a bar that pretends to know. */}
        {intent.releaseStage !== "none" && (
          <g
            className={`core-release palette-${releasePalette(intent.releaseStage)}`}
            data-release-stage={intent.releaseStage}
            data-release-in-flight={intent.releaseInFlight ? "yes" : "no"}
            data-release-progress={
              intent.releaseProgress == null ? "unknown" : round(intent.releaseProgress * 100)
            }
          >
            <circle
              className="core-release-track"
              cx={CENTER}
              cy={CENTER}
              r={world(RELEASE_WORLD)}
              strokeDasharray={intent.releaseProgress == null ? "3 7" : undefined}
            />
            {intent.releaseProgress != null && (
              <circle
                className="core-release-fill"
                cx={CENTER}
                cy={CENTER}
                r={world(RELEASE_WORLD)}
                pathLength={100}
                strokeDasharray={`${round(intent.releaseProgress * 100)} 100`}
                transform={`rotate(-90 ${CENTER} ${CENTER})`}
              />
            )}
          </g>
        )}

        {/* SHADOW_READY: the parked capability nodes, the first under the
            completed satellite's halo. Nothing here moves. */}
        {drawnCapabilities > 0 && (
          <g
            className="core-capabilities"
            data-capability-nodes={drawnCapabilities}
            data-capability-counted={intent.capabilityNodesCounted ? "yes" : "no"}
          >
            {capabilities.map((p, i) => (
              <circle key={`cap-${i}`} cx={round(p.x)} cy={round(p.y)} r={6} />
            ))}
          </g>
        )}
        {intent.satelliteComplete && (
          <g className="core-satellite" data-satellite="complete">
            <circle cx={round(CENTER + world(CAPABILITY_RADIUS))} cy={CENTER} r={11} />
            <circle
              className="core-satellite-halo"
              cx={round(CENTER + world(CAPABILITY_RADIUS))}
              cy={CENTER}
              r={18}
            />
          </g>
        )}
      </svg>

      {reason && (
        <p className="muted core-fallback-reason" data-fallback-reason>
          {reason}
        </p>
      )}
    </div>
  );
}
