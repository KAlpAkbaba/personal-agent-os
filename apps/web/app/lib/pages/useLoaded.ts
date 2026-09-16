"use client";

/**
 * B24: one loader for the family pages.
 *
 * The cockpit's `useCockpitData` fires twenty-seven requests together on a fifteen-second
 * clock because it draws twenty-seven panels at once. A family page draws one family, so
 * it asks once, on arrival, and again when the owner asks — a page about alarms has no
 * business re-fetching the security audit every fifteen seconds.
 *
 * The fetcher must be stable (a `useCallback`, or a module-level function), which is what
 * keeps a page whose fetcher depends on a text box from re-requesting on every keystroke:
 * the caller decides when the closure changes.
 */

import { useEffect, useState } from "react";

import type { Loaded } from "../cockpit/api";

export function useLoaded<T>(fetcher: () => Promise<Loaded<T>>): {
  state: Loaded<T>;
  refresh: () => void;
} {
  const [state, setState] = useState<Loaded<T>>({ kind: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    void fetcher().then((next) => {
      // A page the owner has already left must not write into a dead tree, and an answer
      // that arrives after a newer request must not overwrite it.
      if (alive) setState(next);
    });
    return () => {
      alive = false;
    };
  }, [fetcher, tick]);

  return { state, refresh: () => setTick((n) => n + 1) };
}

/**
 * A clock for the panels that print ages.
 *
 * The cockpit gets `now` from the Core's own feed, which a family page has no reason to
 * open — one WebSocket for a page about alarms would put the renderer on the critical
 * path of the system it is describing. This ticks slowly on purpose: the ages these pages
 * show are minutes and days, and a second-by-second re-render of a static list is work
 * nobody asked for.
 */
export function useNow(everyMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  return now;
}
