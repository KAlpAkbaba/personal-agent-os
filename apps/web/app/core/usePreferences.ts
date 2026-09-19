"use client";

/**
 * The display preferences and the voice-mode switch, remembered locally.
 *
 * The two display ones are display-only — a tier does not change what is
 * reported, only how much geometry is spent reporting it — so `localStorage` is
 * the right home and a failure to read it is not worth surfacing. "Yerel mod"
 * (ADR-0173) is per browser by decision: the owner flips it on the machine they
 * are testing from and back "when the code side is flawless", and nothing
 * server-side changes with it (the paid path stays the default there).
 */

import { useCallback, useEffect, useState } from "react";

import {
  type QualityTier,
  defaultTier,
  readDeviceHints,
} from "../lib/uistate/quality";

const TIER_KEY = "pagentos.core.tier";
const TWO_D_KEY = "pagentos.core.force2d";
/** ADR-0173: "1" when the owner switched this browser to the free local voice mode. */
export const LOCAL_VOICE_KEY = "pagentos.core.localVoice";

/** The `localStorage` surface these helpers need; injectable so tests need no window. */
export type PreferenceStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

function defaultStorage(): PreferenceStorage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

function readStored(key: string, storage: PreferenceStorage | null = defaultStorage()): string | null {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function write(key: string, value: string, storage: PreferenceStorage | null = defaultStorage()): void {
  try {
    storage?.setItem(key, value);
  } catch {
    /* a preference that cannot be saved is still usable this session */
  }
}

/** Whether this browser is switched to the local voice mode (default: off = the paid path). */
export function readLocalVoice(storage: PreferenceStorage | null = defaultStorage()): boolean {
  return readStored(LOCAL_VOICE_KEY, storage) === "1";
}

export function writeLocalVoice(on: boolean, storage: PreferenceStorage | null = defaultStorage()): void {
  write(LOCAL_VOICE_KEY, on ? "1" : "0", storage);
}

export function useCorePreferences() {
  // Start at `balanced` on both server and client so hydration matches; the
  // stored choice and the device-derived default are applied in an effect.
  const [tier, setTierState] = useState<QualityTier>("balanced");
  const [force2d, setForce2dState] = useState(false);
  const [localVoice, setLocalVoiceState] = useState(false);

  useEffect(() => {
    const stored = readStored(TIER_KEY);
    if (stored === "high" || stored === "balanced" || stored === "low") setTierState(stored);
    else setTierState(defaultTier(readDeviceHints()));
    setForce2dState(readStored(TWO_D_KEY) === "1");
    setLocalVoiceState(readLocalVoice());
  }, []);

  const setTier = useCallback((next: QualityTier) => {
    setTierState(next);
    write(TIER_KEY, next);
  }, []);

  const setForce2d = useCallback((next: boolean) => {
    setForce2dState(next);
    write(TWO_D_KEY, next ? "1" : "0");
  }, []);

  const setLocalVoice = useCallback((next: boolean) => {
    setLocalVoiceState(next);
    writeLocalVoice(next);
  }, []);

  return { tier, setTier, force2d, setForce2d, localVoice, setLocalVoice };
}
