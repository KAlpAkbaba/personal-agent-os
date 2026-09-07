"use client";

/**
 * The polling loop behind the Core.
 *
 * Kept deliberately thin: all the judgement lives in `truth.ts` and
 * `visual.ts`, which are pure and tested. This file only decides *when* to ask.
 *
 * Three properties matter more than the code:
 *
 * 1. **It cannot slow the system down.** One in-flight request at a time, a
 *    hard back-off on failure, and nothing at all while the tab is hidden
 *    beyond a slow keep-alive.
 * 2. **A failure degrades to honesty, not to a blank page.** Errors fold into
 *    `connection`, the last known picture stays, and every consumer draws the
 *    difference.
 * 3. **It never fabricates a tick.** There is no local timer that advances the
 *    state; `now` only moves the *age* of what was received, which is what
 *    makes a claim expire rather than what makes it exist.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { UnauthorizedError } from "../session";
import { contractCompatibility } from "./contract";
import { explainUiStateError, fetchUiState } from "./client";
import {
  type CoreTruth,
  applyContractMismatch,
  applyError,
  applyResponse,
  applyUnauthorized,
  emptyTruth,
} from "./truth";
import { pollIntervalMs } from "./quality";

/**
 * How often the *age* of the current claim is recomputed.
 *
 * Separate from the poll: a claim expires because time passed, not because the
 * API said so, and the owner must see that happen even if the network is fine
 * and nothing new is being published.
 */
const AGE_TICK_MS = 1_000;

export type CoreStateHandle = {
  truth: CoreTruth;
  /** Monotonic-ish wall clock the renderer derives ages from. */
  now: number;
  /** Last error text, for the readout. Cleared on the next success. */
  error: string | null;
  /** Force a poll (the cockpit's refresh control). */
  refresh: () => void;
};

export function useCoreState(options: { enabled?: boolean } = {}): CoreStateHandle {
  const enabled = options.enabled ?? true;
  const [truth, setTruth] = useState<CoreTruth>(emptyTruth);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);

  // Refs, not state: changing these must not re-render, and the loop reads the
  // latest value without being restarted.
  const inFlight = useRef(false);
  const failures = useRef(0);
  const sequence = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopped = useRef(false);

  const poll = useCallback(async () => {
    if (inFlight.current || stopped.current) return;
    inFlight.current = true;
    try {
      const response = await fetchUiState(sequence.current);
      if (stopped.current) return;
      if (contractCompatibility(response.contract_version) === "unsupported") {
        // Draw nothing new rather than guess at a vocabulary we do not know.
        // Unknown individual states are handled gracefully downstream; a whole
        // version bump is a different, louder thing.
        //
        // An OLDER but supported server (v2 or v3 while this build is v4) is
        // not this case: v3 and v4 only added states, so an older stream is a
        // subset we can read. It is drawn normally and `truth.contractVersion`
        // carries the lag so the strip can say which states will never arrive
        // (M18.3 §7; M19 §4).
        failures.current = 0;
        setError(null);
        setTruth((t) => applyContractMismatch(t, response.contract_version));
        return;
      }
      failures.current = 0;
      sequence.current = Math.max(sequence.current, response.sequence);
      setError(null);
      setTruth((t) => applyResponse(t, response, Date.now()));
    } catch (err) {
      if (stopped.current) return;
      failures.current += 1;
      if (err instanceof UnauthorizedError) {
        // The session is gone. Everything known came from it; keep none of it.
        sequence.current = 0;
        setTruth(() => applyUnauthorized());
        setError("Sahip oturumu reddedildi.");
        return;
      }
      const text = explainUiStateError(err);
      setError(text);
      setTruth((t) => applyError(t, text, Date.now()));
    } finally {
      inFlight.current = false;
      setNow(Date.now());
    }
  }, []);

  // The poll loop. Reschedules itself so the interval always reflects the
  // current visibility and failure count, and a slow response never overlaps
  // the next request.
  useEffect(() => {
    if (!enabled) return;
    stopped.current = false;

    const schedule = () => {
      if (stopped.current) return;
      const hidden = typeof document !== "undefined" && document.hidden;
      timer.current = setTimeout(run, pollIntervalMs(hidden, failures.current));
    };
    const run = async () => {
      await poll();
      schedule();
    };

    void run();

    // Coming back to the tab should feel immediate, so poll at once rather
    // than waiting out a hidden-tab interval that may be 20s long.
    const onVisibility = () => {
      if (typeof document === "undefined" || document.hidden) return;
      if (timer.current) clearTimeout(timer.current);
      void run();
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      stopped.current = true;
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, poll]);

  // Age ticker. Cheap, and paused while hidden: an invisible core does not need
  // to know it is getting older.
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      setNow(Date.now());
    }, AGE_TICK_MS);
    return () => clearInterval(id);
  }, [enabled]);

  const refresh = useCallback(() => {
    failures.current = 0;
    void poll();
  }, [poll]);

  return { truth, now, error, refresh };
}

/**
 * Whether the document is currently hidden, as a React value.
 *
 * The render loop subscribes to this to stop drawing entirely when the tab is
 * in the background — `requestAnimationFrame` is already throttled there, but
 * "already throttled" is not "not running".
 */
export function usePageHidden(): boolean {
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    const read = () => setHidden(document.hidden);
    read();
    document.addEventListener("visibilitychange", read);
    return () => document.removeEventListener("visibilitychange", read);
  }, []);

  return hidden;
}

/** `prefers-reduced-motion: reduce`, live. Motion stops; information does not. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const read = () => setReduced(query.matches);
    read();
    query.addEventListener("change", read);
    return () => query.removeEventListener("change", read);
  }, []);

  return reduced;
}
