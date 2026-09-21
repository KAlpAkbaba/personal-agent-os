"use client";

/**
 * React over the tab's one `GestureController` (ADR-0198, Stage 1) and one
 * `PointerStreamClient` (ADR-0199, Stage 2) — the same `useSyncExternalStore` shape
 * `useActivePerception`/`useLocalVoiceMode` already use. Both singletons are built lazily
 * (touch no browser API at import time, same posture as `getEyeStore()`/
 * `getLocalVoiceMode()`), and the effect below is the ONLY place this feature re-evaluates
 * the eye-active / has-session gate — `GestureController` itself does not subscribe to the
 * eye store or the voice mode (see its own module docstring for why: this hook already
 * subscribes to both for rendering, so a second, separate subscription inside the
 * controller would just be redundant machinery watching the same two values).
 */

import { useEffect, useSyncExternalStore } from "react";

import { getEyeStore } from "../eye/store";
import { API_BASE, getToken } from "../session";
import { getLocalVoiceMode } from "../voice/useLocalVoiceMode";
import { GestureController, type GestureControllerSnapshot } from "./controller";
import { PointerStreamClient, pointerWsUrl, type PointerClientDeps, type PointerSocketLike, type PointerStreamSnapshot } from "./pointer";
import { GestureTracker } from "./tracker";

let singleton: GestureController | null = null;
let pointerSingleton: PointerStreamClient | null = null;

function realPointerClientDeps(): PointerClientDeps {
  return {
    sessionPort: {
      beginPointerSession: () => getLocalVoiceMode().beginPointerSession(),
      endPointerSession: () => getLocalVoiceMode().endPointerSession(),
    },
    wsUrl: () => {
      const sessionId = getLocalVoiceMode().getSnapshot().sessionId;
      const token = getToken();
      if (!sessionId || !token) return null;
      return pointerWsUrl(API_BASE, sessionId, token);
    },
    createSocket: (url) => new WebSocket(url) as unknown as PointerSocketLike,
    screenWidth: () => (typeof window !== "undefined" && window.screen ? window.screen.width : 1920),
  };
}

/** ADR-0199 Stage 2: the tab's one pointer-stream client. Lazily built, like
 * `getGestureController()` — never touches `WebSocket`/`localStorage` at import time. */
export function getPointerStreamClient(): PointerStreamClient {
  if (pointerSingleton) return pointerSingleton;
  pointerSingleton = new PointerStreamClient(realPointerClientDeps());
  return pointerSingleton;
}

/** Tests only: replace the pointer-stream singleton (e.g. with one built from fakes). */
export function installPointerStreamClient(client: PointerStreamClient | null): void {
  pointerSingleton?.dispose();
  pointerSingleton = client;
}

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
    pointerClient: { handleEvent: (event) => getPointerStreamClient().handleEvent(event) },
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
  pointer: PointerStreamSnapshot;
  setEnabled: (on: boolean) => void;
  setPointerGain: (value: number) => void;
};

export function useGestureController(
  controller: GestureController = getGestureController(),
  pointerClient: PointerStreamClient = getPointerStreamClient(),
): GestureControlHandle {
  const gesture = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getServerSnapshot);
  const pointer = useSyncExternalStore(pointerClient.subscribe, pointerClient.getSnapshot, pointerClient.getServerSnapshot);
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

  return {
    gesture,
    pointer,
    setEnabled: (on: boolean) => controller.setEnabled(on),
    setPointerGain: (value: number) => pointerClient.setGain(value),
  };
}
