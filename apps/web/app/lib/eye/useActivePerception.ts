"use client";

/**
 * React over the one eye store (M18_ACTION_CONTRACT.md §7.1; the pattern is
 * `lib/voice/useVoiceSession.ts`).
 *
 * `useActivePerception()` is a `useSyncExternalStore` subscription and
 * nothing more: it builds nothing of its own, so a second consumer or a
 * remount is a second listener on the same `PerceptionSession` — the
 * property `tests/eye/store.test.ts` pins with `eyeInstances`.
 *
 * Leaving the page does NOT stop the camera any more. The eye is on until it
 * is told otherwise — by the owner's button, by the owner's voice
 * (`eye.disable`), or by the Cloud Core (`eye.disabled` on the bus, a 409 on
 * the next observation). Navigating from `/core` to `/core/cockpit` and back
 * used to turn the camera off in an unmount effect; that made the eye's
 * state a property of which view happened to be mounted, which is exactly
 * what the store exists to end. A CLOSED tab still releases the camera by
 * itself: the browser stops every `MediaStreamTrack` with the document.
 *
 * The handle keeps the shape `EyeControl` / `EyeControlView` always read.
 */

import { useCallback, useSyncExternalStore } from "react";

import type { PerceptionStatus } from "./perception";
import { type EyeStore, getEyeStore } from "./store";
import type { CameraPermission } from "./types";

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
   * disable that already happened elsewhere; see `EyeStore.stopLocalOnly`.
   */
  stopLocalOnly: () => void;
};

export function useActivePerception(store: EyeStore = getEyeStore()): ActivePerceptionHandle {
  const eye = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getServerSnapshot);

  const start = useCallback(async () => {
    await store.enable("owner_start");
  }, [store]);

  const stop = useCallback(async () => {
    await store.disable("owner_stop");
  }, [store]);

  const stopLocalOnly = useCallback(() => {
    store.stopLocalOnly();
  }, [store]);

  return {
    status: eye.status,
    permission: eye.permission,
    busy: eye.busy,
    error: eye.error,
    start,
    stop,
    stopLocalOnly,
  };
}
