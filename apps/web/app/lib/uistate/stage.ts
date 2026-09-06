/**
 * Minimal mode's geometry, as pure arithmetic (M18.3 §9).
 *
 * Named `stage.ts` and not `layout.ts` for one prosaic reason worth writing
 * down so nobody renames it back: `layout` is a reserved filename anywhere
 * under `app/` in the Next.js App Router, and a module called that is compiled
 * as a route layout and fails the build. Only `next build` catches it - the
 * test suite, `tsc --noEmit` and `oxlint` all pass happily.
 *
 * The owner's directive for this milestone is a spatial one — "the Core must
 * become a full-viewport living visual presence" — and a spatial claim is
 * exactly the kind that quietly stops being true when a stylesheet changes.
 * So the two rules that carry it live here as functions with tests rather than
 * as CSS nobody can assert on:
 *
 * 1. **`stageSizeFor` sizes the stage so the Core covers 60–80 % of the usable
 *    viewport, at every aspect ratio.** Portrait, square, laptop and ultrawide
 *    each get their own target, because "60 % of the shorter side" reads very
 *    differently on a phone and on a 32:9 monitor; the band is the invariant,
 *    the target within it is the judgement.
 * 2. **The controls fade to a quarter after four idle seconds and come back on
 *    the first movement or focus.** That is a timer, and a timer written into
 *    a component is a timer nobody can test, so it is a reducer here.
 *
 * Nothing in this file touches the DOM: it takes numbers and returns numbers,
 * which is what lets `tests/uistate/stage.test.ts` run in Node.
 */

import { CORE_FILL } from "./scene";

/** The Core must cover at least this fraction of the usable viewport's short side. */
export const COVERAGE_MIN = 0.6;
/** …and at most this much, so the outer field is never cropped by the edges. */
export const COVERAGE_MAX = 0.8;

/**
 * How much of the viewport the overlays are allowed to claim back.
 *
 * The caption, the strip and the control cluster float OVER the stage rather
 * than beside it (that is what makes the mode full-viewport), so this is not
 * layout space — it is the margin the Core is kept out of so the overlays have
 * something other than the nucleus to sit on.
 */
export const OVERLAY_MARGIN_Y = 0.1;

/** Below this the viewport is a phone in portrait and the Core takes the width. */
export const ASPECT_PORTRAIT = 0.9;
/** Above this it is a wide desktop; above `ASPECT_ULTRAWIDE`, a very wide one. */
export const ASPECT_LANDSCAPE = 1.35;
export const ASPECT_ULTRAWIDE = 2.1;

export type StageBand = "portrait" | "square" | "landscape" | "ultrawide";

/**
 * The share of the usable short side the Core's principal structure aims for,
 * per band.
 *
 * A square-ish window can give the Core the most, because there is room around
 * it in both directions for the caption and the strip. An ultrawide one gives
 * it less, because the height is the binding constraint and a Core sized to
 * 78 % of it leaves the caption sitting on the structure.
 */
export const BAND_TARGET: Record<StageBand, number> = {
  portrait: 0.74,
  square: 0.78,
  landscape: 0.72,
  ultrawide: 0.66,
};

export function stageBandFor(aspect: number): StageBand {
  if (!Number.isFinite(aspect) || aspect <= 0) return "square";
  if (aspect < ASPECT_PORTRAIT) return "portrait";
  if (aspect <= ASPECT_LANDSCAPE) return "square";
  if (aspect <= ASPECT_ULTRAWIDE) return "landscape";
  return "ultrawide";
}

export type StageSize = {
  /** The square stage's side, in CSS pixels. */
  size: number;
  /**
   * The fraction of the usable viewport's shorter side that the Core's drawn
   * structure spans. Always within [`COVERAGE_MIN`, `COVERAGE_MAX`].
   */
  coverage: number;
  aspect: number;
  band: StageBand;
  /** The shorter side of the usable viewport, after the overlay margin. */
  usableShort: number;
};

/**
 * The square stage for a viewport of `width` × `height` CSS pixels.
 *
 * The Core's drawn structure spans `CORE_FILL` of the stage (a fact about the
 * camera, derived in `scene.ts`), so the stage is sized backwards from the
 * coverage we want and then clamped so the claim stays true: never wider than
 * the viewport, never below the 60 % floor, never above the 80 % ceiling.
 */
export function stageSizeFor(width: number, height: number): StageSize {
  const w = Number.isFinite(width) && width > 0 ? width : 0;
  const h = Number.isFinite(height) && height > 0 ? height : 0;
  const aspect = h > 0 ? w / h : 1;
  const band = stageBandFor(aspect);

  // The margin comes off the height only: the overlays sit above and below the
  // Core, and taking it off the width as well would shrink the Core on a phone
  // for space nothing uses.
  const usableH = h * (1 - OVERLAY_MARGIN_Y * 2);
  const usableShort = Math.max(0, Math.min(w, usableH));
  if (usableShort <= 0) return { size: 0, coverage: 0, aspect, band, usableShort: 0 };

  const target = BAND_TARGET[band];
  const preferred = (usableShort * target) / CORE_FILL;
  // The floor and the ceiling are on the COVERAGE, not on the pixels, because
  // the coverage is what was promised. Rounding goes inward on both ends so a
  // half-pixel cannot put the result outside the band it claims to be in. The
  // `w`/`h` guard is arithmetically a no-op at every band target — it is here
  // so that a future target above the ceiling still cannot overflow the page.
  const minSize = Math.ceil((usableShort * COVERAGE_MIN) / CORE_FILL);
  const maxSize = Math.floor(Math.min((usableShort * COVERAGE_MAX) / CORE_FILL, w, h));
  const size = Math.max(minSize, Math.min(Math.round(preferred), maxSize));

  return {
    size,
    coverage: (size * CORE_FILL) / usableShort,
    aspect,
    band,
    usableShort,
  };
}

// ------------------------------------------------------ the control cluster

/** Idle time after which the control cluster recedes. */
export const CONTROL_FADE_AFTER_MS = 4_000;
/** How far it recedes. Still legible, still focusable, still announced. */
export const CONTROL_FADED_OPACITY = 0.25;
export const CONTROL_PRESENT_OPACITY = 1;

export type ControlFadeState = {
  /** Milliseconds since the last pointer movement or focus change. */
  idleMs: number;
  /**
   * True while the pointer is over the cluster or the focus is inside it.
   *
   * A held cluster never fades, however long it is idle: a control the owner
   * is currently pointing at or has tabbed into must not dim under them.
   */
  held: boolean;
};

export type ControlFadeAction =
  /** The pointer moved, or something in the cluster took focus. */
  | { kind: "activity" }
  /** The pointer entered the cluster, or focus moved into it. */
  | { kind: "hold" }
  /** The pointer left and focus went elsewhere. */
  | { kind: "release" }
  /** Time passed. */
  | { kind: "tick"; dtMs: number };

export function controlFadeInitial(): ControlFadeState {
  return { idleMs: 0, held: false };
}

/** Pure, and the only place the fade's timing is decided. */
export function controlFadeReducer(
  state: ControlFadeState,
  action: ControlFadeAction,
): ControlFadeState {
  switch (action.kind) {
    case "activity":
      return state.idleMs === 0 ? state : { ...state, idleMs: 0 };
    case "hold":
      return state.held && state.idleMs === 0 ? state : { idleMs: 0, held: true };
    case "release":
      return state.held ? { ...state, held: false } : state;
    case "tick": {
      if (state.held) return state.idleMs === 0 ? state : { ...state, idleMs: 0 };
      const dt = Number.isFinite(action.dtMs) && action.dtMs > 0 ? action.dtMs : 0;
      // Capped so a tab left open all night does not accumulate a number
      // nothing reads; past the threshold the value stops meaning anything.
      const idleMs = Math.min(state.idleMs + dt, CONTROL_FADE_AFTER_MS * 2);
      return idleMs === state.idleMs ? state : { ...state, idleMs };
    }
    default:
      return state;
  }
}

export function controlsAreFaded(state: ControlFadeState): boolean {
  return !state.held && state.idleMs >= CONTROL_FADE_AFTER_MS;
}

export function controlOpacity(state: ControlFadeState): number {
  return controlsAreFaded(state) ? CONTROL_FADED_OPACITY : CONTROL_PRESENT_OPACITY;
}
