"use client";

/**
 * The two display preferences, remembered locally.
 *
 * They are display-only — a tier does not change what is reported, only how
 * much geometry is spent reporting it — so `localStorage` is the right home and
 * a failure to read it is not worth surfacing.
 */

import { useCallback, useEffect, useState } from "react";

import {
  type QualityTier,
  defaultTier,
  readDeviceHints,
} from "../lib/uistate/quality";

const TIER_KEY = "pagentos.core.tier";
const TWO_D_KEY = "pagentos.core.force2d";

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* a preference that cannot be saved is still usable this session */
  }
}

export function useCorePreferences() {
  // Start at `balanced` on both server and client so hydration matches; the
  // stored choice and the device-derived default are applied in an effect.
  const [tier, setTierState] = useState<QualityTier>("balanced");
  const [force2d, setForce2dState] = useState(false);

  useEffect(() => {
    const stored = readStored(TIER_KEY);
    if (stored === "high" || stored === "balanced" || stored === "low") setTierState(stored);
    else setTierState(defaultTier(readDeviceHints()));
    setForce2dState(readStored(TWO_D_KEY) === "1");
  }, []);

  const setTier = useCallback((next: QualityTier) => {
    setTierState(next);
    write(TIER_KEY, next);
  }, []);

  const setForce2d = useCallback((next: boolean) => {
    setForce2dState(next);
    write(TWO_D_KEY, next ? "1" : "0");
  }, []);

  return { tier, setTier, force2d, setForce2d };
}
