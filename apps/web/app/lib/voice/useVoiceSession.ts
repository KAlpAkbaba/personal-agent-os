"use client";

/**
 * React over the one voice store (M18; ADR-0061 §1).
 *
 * `useVoiceSession()` is a `useSyncExternalStore` subscription and nothing
 * more: it does not build anything of its own, so a second consumer or a
 * remount is a second listener on the same controller — the property
 * `tests/voice/store.test.ts` pins with the instance registry.
 *
 * `useVoiceLevels()` is the only place the Core samples audio, and it samples
 * *measurements* (the playback analyser, the local gate) at animation rate
 * while the state calls for them. It never runs a timer that advances state.
 */

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import type { EnvironmentMode } from "./calibration";
import type { VoiceUiState } from "./controller";
import type { MicrophoneProfile } from "./profile";
import { type VoiceStore, type VoiceStoreSnapshot, getVoiceStore } from "./store";

export type VoiceActions = {
  connect: () => Promise<void>;
  disconnect: () => Promise<void>;
  reconnect: () => Promise<void>;
  refreshDevices: () => Promise<void>;
  setMicrophone: (deviceId: string) => Promise<void>;
  setSpeaker: (deviceId: string) => Promise<void>;
  setVoice: (value: string) => void;
  setNoiseMode: (mode: EnvironmentMode) => void;
  patchProfile: (partial: Partial<MicrophoneProfile>) => void;
  recalibrate: () => void;
};

export type VoiceSessionHandle = {
  voice: VoiceStoreSnapshot;
  actions: VoiceActions;
  /** The store itself, for diagnostics that read measurements directly. */
  store: VoiceStore;
};

export function useVoiceSession(store: VoiceStore = getVoiceStore()): VoiceSessionHandle {
  const voice = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getServerSnapshot);

  const actions = useMemo<VoiceActions>(
    () => ({
      connect: () => store.connect(),
      disconnect: () => store.disconnect(),
      reconnect: () => store.reconnect(),
      refreshDevices: () => store.refreshDevices(),
      setMicrophone: (deviceId) => store.setMicrophone(deviceId),
      setSpeaker: (deviceId) => store.setSpeaker(deviceId),
      setVoice: (value) => store.setVoice(value),
      setNoiseMode: (mode) => store.setNoiseMode(mode),
      patchProfile: (partial) => store.patchProfile(partial),
      recalibrate: () => store.recalibrate(),
    }),
    [store],
  );

  return { voice, actions, store };
}

export type VoiceLevels = {
  /** Owner microphone level 0..1 from the local gate, `null` when not measured. */
  micLevel: number | null;
  /** Assistant output envelope 0..1 from the playback analyser, `null` when not measurable. */
  outputLevel: number | null;
};

const STILL: VoiceLevels = { micLevel: null, outputLevel: null };

/** Below this change a frame is not worth a React render. */
const LEVEL_EPSILON = 0.02;

function differs(a: number | null, b: number | null): boolean {
  if (a === null || b === null) return a !== b;
  return Math.abs(a - b) >= LEVEL_EPSILON;
}

/**
 * Sample the two real levels at animation rate while the state needs them.
 *
 * Listening samples the microphone; speaking samples the output. Every other
 * state yields `null`s — including `interrupted`, where the analyser would
 * read 0 anyway because the controller already silenced the path, but the
 * absence of a sample is the more honest report of "nothing measured".
 */
export function useVoiceLevels(state: VoiceUiState, store: VoiceStore = getVoiceStore()): VoiceLevels {
  const [levels, setLevels] = useState<VoiceLevels>(STILL);
  const wantMic = state === "listening";
  const wantOutput = state === "speaking";

  const read = useCallback(
    (): VoiceLevels => ({
      micLevel: wantMic ? store.micLevel() : null,
      outputLevel: wantOutput ? store.outputLevel() : null,
    }),
    [store, wantMic, wantOutput],
  );

  useEffect(() => {
    if (!wantMic && !wantOutput) {
      setLevels(STILL);
      return;
    }
    if (typeof requestAnimationFrame !== "function") return;
    let frame = 0;
    let last: VoiceLevels = STILL;
    const tick = () => {
      const next = read();
      if (differs(next.micLevel, last.micLevel) || differs(next.outputLevel, last.outputLevel)) {
        last = next;
        setLevels(next);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      setLevels(STILL);
    };
  }, [wantMic, wantOutput, read]);

  return levels;
}
