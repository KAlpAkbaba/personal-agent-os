"use client";

/**
 * React binding for `PerceptionSession` (M18_HOLOGRAPHIC_CORE_SPEC.md §2).
 *
 * Deliberately thin, like `useCoreState.ts`: every judgement about what a
 * frame means lives in `signal.ts` (pure, unit-tested) and every judgement
 * about the sampling loop's lifecycle lives in `perception.ts` (also
 * unit-tested, via injected fakes). This file only wires that controller into
 * React state and the browser's own Permissions API — nothing here is worth
 * testing beyond what render tests of the component already cover, the same
 * reasoning `useCoreState.ts` and `usePreferences.ts` are untested directly.
 */

import { useCallback, useEffect, useState } from "react";

import { disableEye, enableEye, explainEyeError } from "./client";
import { PerceptionSession, type PerceptionStatus } from "./perception";
import type { CameraPermission } from "./types";

const INITIAL_STATUS: PerceptionStatus = {
  running: false,
  cameraLabel: null,
  lastObservation: null,
  motion: null,
  lastError: null,
  startedAt: null,
};

export type ActivePerceptionHandle = {
  status: PerceptionStatus;
  /** The browser's own camera-permission state; see `types.ts` for the four honest answers. */
  permission: CameraPermission;
  /** True while an enable/disable round-trip is in progress. */
  busy: boolean;
  /** Owner-facing text for the last thing that went wrong, or `null`. */
  error: string | null;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  /**
   * Stops the LOCAL loop only — no `disableEye()` call. For reacting to a
   * disable that already happened elsewhere (another device, or the voice
   * path via `Gözünü kapat`): the Cloud Core is already told, so re-telling
   * it here would just be a second, redundant ledger row. `PerceptionSession`
   * also self-corrects the slower way on its own next tick (a 409 from
   * `postObservation` stops it too), so this only makes that reaction faster
   * — it is a latency improvement, not the safety mechanism itself.
   */
  stopLocalOnly: () => void;
};

export function useActivePerception(
  options: { sampleIntervalMs?: number } = {},
): ActivePerceptionHandle {
  const [status, setStatus] = useState<PerceptionStatus>(INITIAL_STATUS);
  const [permission, setPermission] = useState<CameraPermission>("unknown");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // `useState`'s lazy initialiser runs exactly once, on mount — never per
  // render and never per option change — so this is the one
  // `PerceptionSession` the hook owns for its whole lifetime, and unmount
  // cleanup always stops the same instance it started. (A ref read during
  // render would do the same thing but is not a render-safe pattern; the
  // state slot's value is never itself rendered, so `session` is not part of
  // JSX below.)
  const [session] = useState(
    () =>
      new PerceptionSession({
        sampleIntervalMs: options.sampleIntervalMs,
        onStatusChange: setStatus,
      }),
  );

  // The browser's own answer, where it can give one. Permissions API support
  // for "camera" is inconsistent (notably absent in some WebKit builds), so
  // "unsupported" is a real, distinct answer rather than a fallback to "unknown".
  useEffect(() => {
    let cancelled = false;
    if (typeof navigator === "undefined" || !navigator.permissions?.query) {
      setPermission("unsupported");
      return;
    }
    let handle: PermissionStatus | null = null;
    const onChange = () => {
      if (handle) setPermission(handle.state as CameraPermission);
    };
    navigator.permissions
      .query({ name: "camera" as PermissionName })
      .then((result) => {
        if (cancelled) return;
        handle = result;
        setPermission(result.state as CameraPermission);
        result.addEventListener("change", onChange);
      })
      .catch(() => {
        if (!cancelled) setPermission("unsupported");
      });
    return () => {
      cancelled = true;
      handle?.removeEventListener("change", onChange);
    };
  }, []);

  // Releasing the camera on unmount is the same guarantee `stop()` gives the
  // owner explicitly — a closed tab must turn the camera light off too.
  useEffect(() => {
    return () => {
      session.stop();
    };
  }, [session]);

  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      // Local first: never tell the Cloud Core perception is on before the
      // camera has actually opened on this device.
      await session.start();
      setPermission("granted");
      await enableEye("owner_start");
    } catch (err) {
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setPermission("denied");
      }
      setError(explainEyeError(err));
      session.stop();
    } finally {
      setBusy(false);
    }
  }, [session]);

  const stop = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      // Durable first, local second (see client.ts's `disableEye` doc): a
      // sample already captured before this resolves is refused server-side
      // the instant the flag flips, even before the local loop notices.
      await disableEye("owner_stop");
    } catch (err) {
      setError(explainEyeError(err));
    } finally {
      session.stop();
      setBusy(false);
    }
  }, [session]);

  const stopLocalOnly = useCallback(() => {
    session.stop();
  }, [session]);

  return { status, permission, busy, error, start, stop, stopLocalOnly };
}
