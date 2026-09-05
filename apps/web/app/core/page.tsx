"use client";

/**
 * `/core` — Minimal Core Mode.
 *
 * Primarily the living Core, its current state, and the least context that is
 * still honest. Meant to be left open on a second screen, so it does as little
 * work as it can: one poll a second while visible, one every twenty seconds
 * while hidden, and no rendering at all in a background tab.
 *
 * What "minimal" does NOT mean here: it does not mean fewer facts. The
 * connection state, the age of the claim, and the difference between "idle" and
 * "nothing reported" are all present, because a calm-looking core with no way
 * to tell those apart is precisely the thing ADR-0052 forbids.
 */

import { useMemo } from "react";

import OwnerGate from "../components/OwnerGate";
import { useCoreState } from "../lib/uistate/useCoreState";
import { visualFor } from "../lib/uistate/visual";
import CoreBar from "./CoreBar";
import CoreView from "./CoreView";
import StateReadout from "./StateReadout";
import { useCorePreferences } from "./usePreferences";
import "./core.css";

function MinimalCore() {
  const { truth, now, refresh } = useCoreState();
  const { tier, setTier, force2d, setForce2d } = useCorePreferences();

  // Recomputed whenever the truth or the clock moves — and only then. The
  // intent is a pure function of both, so there is no hidden animation state.
  const intent = useMemo(() => visualFor(truth, now), [truth, now]);

  return (
    <div className="core-page">
      <CoreBar
        mode="minimal"
        connection={truth.connection}
        tier={tier}
        onTier={setTier}
        force2d={force2d}
        onForce2d={setForce2d}
        onRefresh={refresh}
      />
      <main className="core-minimal">
        <CoreView intent={intent} tier={tier} force2d={force2d} />
        <StateReadout intent={intent} />
      </main>
    </div>
  );
}

export default function CorePage() {
  return (
    <OwnerGate>
      <MinimalCore />
    </OwnerGate>
  );
}
