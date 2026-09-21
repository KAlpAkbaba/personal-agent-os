"use client";

/**
 * React over the tab's one `GestureController` (ADR-0198, Stage 1) — the same
 * `useSyncExternalStore` shape `useActivePerception`/`useLocalVoiceMode` already use. The
 * singleton is built lazily (touches no browser API at import time, same posture as
 * `getEyeStore()`/`getLocalVoiceMode()`), and the effect below is the ONLY place this
 * feature re-evaluates the eye-active / has-session gate — `GestureController` itself does
 * not subscribe to the eye store or the voice mode (see its own module docstring for why:
 * this hook already subscribes to both for rendering, so a second, separate subscription
 * inside the controller would just be redundant machinery watching the same two values).
 */

import { useEffect, useSyncExternalStore } from "react";

import { getEyeStore } from "../eye/store";
import { getLocalVoiceMode } from "../voice/useLocalVoiceMode";
import { GestureController, type GestureControllerSnapshot } from "./controller";
import { GestureTracker } from "./tracker";

let singleton: GestureController | null = null;

export function getGestureController(): GestureController {
  if (singleton) return singleton;
  singleton = new GestureController({
    attachVideoConsumer: (cb) => {
      const session = getEyeStore().peekSession();
      if (!session) {
        cb(null);
        return () => {};
      }
      return session.attachVideoConsumer(cb);
    },
    isEyeActive: () => getEyeStore().getSnapshot().status.running,
    hasLocalSession: () => getLocalVoiceMode().getSnapshot().sessionId !== null,
    dispatchGesture: (gesture) => getLocalVoiceMode().dispatchGesture(gesture),
    createTracker: (handlers) => new GestureTracker(handlers),
  });
  return singleton;
}

/** Tests only: replace the singleton (e.g. with one built from fakes). */
export function installGestureController(controller: GestureController | null): void {
  singleton?.dispose();
  singleton = controller;
}

export type GestureControlHandle = {
  gesture: GestureControllerSnapshot;
  setEnabled: (on: boolean) => void;
};

export function useGestureController(controller: GestureController = getGestureController()): GestureControlHandle {
  const gesture = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getServerSnapshot);
  // Subscribed here (not inside GestureController) purely to know WHEN to call refresh() —
  // this component tree already needs both values to render other things, so this is not an
  // extra subscription so much as reusing the ones that exist.
  const eyeSnapshot = useSyncExternalStore(getEyeStore().subscribe, getEyeStore().getSnapshot, getEyeStore().getServerSnapshot);
  const localSnapshot = useSyncExternalStore(
    getLocalVoiceMode().subscribe,
    getLocalVoiceMode().getSnapshot,
    getLocalVoiceMode().getServerSnapshot,
  );
  const eyeRunning = eyeSnapshot.status.running;
  const sessionId = localSnapshot.sessionId;
  useEffect(() => {
    controller.refresh();
  }, [controller, eyeRunning, sessionId]);

  return { gesture, setEnabled: (on: boolean) => controller.setEnabled(on) };
}
