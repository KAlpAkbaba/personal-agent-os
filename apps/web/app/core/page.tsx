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
 *
 * M18 / ADR-0061: this is also the owner's voice surface. The tab's one voice
 * session (`lib/voice/store.ts`) is read here, and its real states overlay the
 * bus in the same `visualFor` pipeline — labelled as this device's own
 * observation, never published as bus events.
 */

import { useMemo } from "react";

import OwnerGate from "../components/OwnerGate";
import { eyeView, presenceView, releaseView } from "../lib/uistate/ambient";
import { eyeClaim, presenceClaim, releaseClaim } from "../lib/uistate/truth";
import { useCoreState } from "../lib/uistate/useCoreState";
import { visualFor } from "../lib/uistate/visual";
import { voiceOverlayFrom } from "../lib/uistate/voice-overlay";
import { useVoiceLevels, useVoiceSession } from "../lib/voice/useVoiceSession";
import AmbientBand from "./AmbientBand";
import CoreBar from "./CoreBar";
import CoreView from "./CoreView";
import EyeControl from "./EyeControl";
import StateReadout from "./StateReadout";
import VoiceControl from "./VoiceControl";
import { useCorePreferences } from "./usePreferences";
import "./core.css";

function MinimalCore() {
  const { truth, now, refresh } = useCoreState();
  const { tier, setTier, force2d, setForce2d } = useCorePreferences();
  const { voice } = useVoiceSession();
  const levels = useVoiceLevels(voice.controller.state);

  // Recomputed whenever the truth, the clock or the local voice session moves —
  // and only then. The intent is a pure function of all three, so there is no
  // hidden animation state; the output envelope is a sampled measurement.
  const overlay = useMemo(() => voiceOverlayFrom(voice.controller, levels), [voice.controller, levels]);
  const intent = useMemo(() => visualFor(truth, now, overlay), [truth, now, overlay]);
  // Contract v2's other channels, each read from its own claim so none of them
  // can overwrite another (see `prefixClaim`).
  const eye = useMemo(() => eyeView(eyeClaim(truth, now)), [truth, now]);
  const presence = useMemo(() => presenceView(presenceClaim(truth, now)), [truth, now]);
  const release = useMemo(() => releaseView(releaseClaim(truth, now)), [truth, now]);

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
        <section className="ambient-band" aria-label="Ses oturumu">
          <VoiceControl />
        </section>
        <AmbientBand eye={eye} presence={presence} release={release} />
        <section className="ambient-band" aria-label="Göz kontrolü">
          <EyeControl eye={eye} />
        </section>
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
