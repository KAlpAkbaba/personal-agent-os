"use client";

/**
 * React over the tab's one `LocalVoiceMode` (ADR-0173), the way `useVoiceSession`
 * sits over the one `VoiceStore`: a `useSyncExternalStore` subscription and
 * nothing more. The singleton is built lazily and reads the browser's speech APIs
 * only when `start()` is called, so importing this on the server touches nothing.
 */

import { useMemo, useSyncExternalStore } from "react";

import { apiFetch } from "../session";
import { VoiceSessionApi } from "./api";
import { type LocalModeSnapshot, LocalVoiceMode, browserLocalModeDeps } from "./localMode";

let singleton: LocalVoiceMode | null = null;

export function getLocalVoiceMode(): LocalVoiceMode {
  if (singleton) return singleton;
  singleton = new LocalVoiceMode(browserLocalModeDeps(new VoiceSessionApi(apiFetch)));
  // A reload or a closed tab ends the local session on the Cloud Core too (the session
  // never expires by itself, ADR-0105). Registered once, with the singleton.
  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", () => singleton?.closeOnUnload());
  }
  return singleton;
}

/** Tests only: replace the singleton (e.g. with one built from fakes). */
export function installLocalVoiceMode(mode: LocalVoiceMode | null): void {
  singleton = mode;
}

export type LocalVoiceHandle = {
  local: LocalModeSnapshot;
  start: () => Promise<void>;
  stop: () => Promise<void>;
};

export function useLocalVoiceMode(mode: LocalVoiceMode = getLocalVoiceMode()): LocalVoiceHandle {
  const local = useSyncExternalStore(mode.subscribe, mode.getSnapshot, mode.getServerSnapshot);
  const actions = useMemo(() => ({ start: () => mode.start(), stop: () => mode.stop() }), [mode]);
  return { local, ...actions };
}
