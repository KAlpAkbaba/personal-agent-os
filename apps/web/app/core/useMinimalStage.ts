"use client";

/**
 * The three browser-facing pieces of Minimal mode (M18.3 §9): the stage's
 * size, the control cluster's fade, and fullscreen.
 *
 * All three keep their judgement in `lib/uistate/stage.ts`, which is pure and
 * tested; this file is only the subscription to the browser. The split is the
 * same one `CoreView`/`CoreFallback2D` and `EyeControl`/`EyeControlView` make,
 * and it is what lets the coverage claim and the fade timing be asserted in
 * Node with no browser at all.
 *
 * The fullscreen rule is the one worth stating out loud: `requestFullscreen`
 * is called from exactly one place, a callback this module hands to a button,
 * and never from an effect. The browser would refuse a call outside a user
 * gesture anyway — the point is that we do not try, and that a test can read
 * the source and see that we do not.
 */

import { useCallback, useEffect, useState } from "react";

import {
  type ControlFadeState,
  type StageSize,
  controlFadeInitial,
  controlFadeReducer,
  controlOpacity,
  controlsAreFaded,
  stageSizeFor,
} from "../lib/uistate/stage";

/** How often the fade's timer ticks. Coarse: this is a four-second threshold. */
const FADE_TICK_MS = 500;

/**
 * The stage, measured from the window.
 *
 * `null` until the first measurement, so the server and the first client
 * render agree; the stage falls back to a CSS rule of the same shape until
 * then rather than flashing at some other size.
 */
export function useViewportStage(): StageSize | null {
  const [stage, setStage] = useState<StageSize | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const measure = () => {
      setStage(stageSizeFor(window.innerWidth, window.innerHeight));
    };
    measure();
    window.addEventListener("resize", measure);
    // Rotating a phone, and entering or leaving fullscreen, both change the
    // usable viewport without always firing `resize` first.
    window.addEventListener("orientationchange", measure);
    document.addEventListener("fullscreenchange", measure);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", measure);
      document.removeEventListener("fullscreenchange", measure);
    };
  }, []);

  return stage;
}

export type ControlFadeHandle = {
  state: ControlFadeState;
  faded: boolean;
  opacity: number;
  /** Bind to the cluster: pointer in / focus in. */
  hold: () => void;
  /** Bind to the cluster: pointer out / focus out. */
  release: () => void;
};

/**
 * The control cluster's presence.
 *
 * Pointer movement anywhere on the stage brings it back; four idle seconds
 * take it to a quarter. It never fades while the pointer is over it or the
 * focus is inside it, and it is never removed from the accessibility tree or
 * from the tab order — a faded control is a quiet control, not an absent one.
 */
export function useControlFade(enabled = true): ControlFadeHandle {
  const [state, setState] = useState<ControlFadeState>(controlFadeInitial);
  // Stable for the life of the hook (`setState` is), so the pointer listeners
  // and the timer are subscribed once rather than on every tick.
  const dispatch = useCallback((action: Parameters<typeof controlFadeReducer>[1]) => {
    setState((current) => controlFadeReducer(current, action));
  }, []);

  useEffect(() => {
    if (!enabled || typeof window === "undefined") return;
    const wake = () => dispatch({ kind: "activity" });
    window.addEventListener("pointermove", wake, { passive: true });
    window.addEventListener("pointerdown", wake, { passive: true });
    window.addEventListener("keydown", wake);
    window.addEventListener("focusin", wake);
    const timer = setInterval(() => dispatch({ kind: "tick", dtMs: FADE_TICK_MS }), FADE_TICK_MS);
    return () => {
      window.removeEventListener("pointermove", wake);
      window.removeEventListener("pointerdown", wake);
      window.removeEventListener("keydown", wake);
      window.removeEventListener("focusin", wake);
      clearInterval(timer);
    };
  }, [enabled, dispatch]);

  return {
    state,
    faded: controlsAreFaded(state),
    opacity: controlOpacity(state),
    hold: useCallback(() => dispatch({ kind: "hold" }), [dispatch]),
    release: useCallback(() => dispatch({ kind: "release" }), [dispatch]),
  };
}

export type FullscreenHandle = {
  /** Whether the document is currently in fullscreen. */
  active: boolean;
  /** False where the browser does not offer it at all (older Safari, an iframe). */
  supported: boolean;
  /**
   * Enter or leave. **Only ever passed to a button's `onClick`.** The browser
   * requires a user gesture and we do not attempt to work around that; there
   * is no effect in this file that calls it.
   */
  toggle: () => void;
};

export function useFullscreen(): FullscreenHandle {
  const [active, setActive] = useState(false);
  const [supported, setSupported] = useState(false);

  useEffect(() => {
    if (typeof document === "undefined") return;
    setSupported(
      typeof document.documentElement.requestFullscreen === "function" &&
        document.fullscreenEnabled !== false,
    );
    const read = () => setActive(document.fullscreenElement !== null);
    read();
    document.addEventListener("fullscreenchange", read);
    return () => document.removeEventListener("fullscreenchange", read);
  }, []);

  const toggle = useCallback(() => {
    if (typeof document === "undefined") return;
    // Esc already leaves fullscreen; this is the visible control that does the
    // same, so the owner is never in a mode they cannot see their way out of.
    if (document.fullscreenElement) {
      void document.exitFullscreen?.();
      return;
    }
    void document.documentElement.requestFullscreen?.().catch(() => {
      // A refusal (no gesture, a policy, an iframe) is not an error worth
      // interrupting the owner over: the page simply stays as it was.
    });
  }, []);

  return { active, supported, toggle };
}
