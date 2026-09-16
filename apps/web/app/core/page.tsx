"use client";

/**
 * `/core` — Minimal Core Mode, since M18.3 a full-viewport living presence.
 *
 * The owner's directive for this milestone: "the existing small wireframe
 * sphere is no longer acceptable as the primary owner experience; /core must
 * become a full-viewport living visual presence." So the stage IS the
 * viewport — `position: fixed; inset: 0`, so there is no page scroll at all
 * rather than a scroll with nowhere to go, on a near-black ground — and
 * everything else is an overlay on it:
 *
 *   · a tiny semantic caption, bottom centre (the same `StateReadout`, compact)
 *   · a connection dot, because a still Core looks the same live and lost
 *   · a control cluster that recedes to a quarter after four idle seconds
 *   · an ambient strip: presence, the camera, the screens, the alarm, releases
 *
 * What "minimal" does NOT mean here is fewer facts. The connection state, the
 * age of the claim, and the difference between "idle" and "nothing reported"
 * are all still present, because a calm-looking Core with no way to tell those
 * apart is precisely the thing ADR-0052 forbids. The detail moves to the
 * Cockpit; the truth does not.
 *
 * M18 / ADR-0061: this is also the owner's voice surface. The tab's one voice
 * session (`lib/voice/store.ts`) is read here, and its real states overlay the
 * bus in the same `visualFor` pipeline — labelled as this device's own
 * observation, never published as bus events.
 */

import { useEffect, useMemo } from "react";

import OwnerGate from "../components/OwnerGate";
import { alarmView, displayView, eyeView, presenceView, releaseView } from "../lib/uistate/ambient";
import { CORE_BUILD_ID, KNOWN_CONTRACT_VERSION } from "../lib/uistate/contract";
import { contractLagNote } from "../lib/uistate/labels";
import {
  alarmClaim,
  displayClaim,
  eyeClaim,
  presenceClaim,
  releaseClaim,
} from "../lib/uistate/truth";
import { useCoreState } from "../lib/uistate/useCoreState";
import { visualFor } from "../lib/uistate/visual";
import { voiceOverlayFrom } from "../lib/uistate/voice-overlay";
import { useActivePerception } from "../lib/eye/useActivePerception";
import { isBusyState, isLiveState } from "../lib/voice/store";
import { useVoiceLevels, useVoiceSession } from "../lib/voice/useVoiceSession";
import AmbientBand from "./AmbientBand";
import ConnectionDot from "./ConnectionDot";
import CoreControls from "./CoreControls";
import CoreView from "./CoreView";
import StateReadout from "./StateReadout";
import { useControlFade, useFullscreen, useViewportStage } from "./useMinimalStage";
import { useCorePreferences } from "./usePreferences";
import "./core.css";

function MinimalCore() {
  const { truth, now } = useCoreState();
  const { tier, setTier, force2d, setForce2d } = useCorePreferences();
  const { voice, actions } = useVoiceSession();
  const levels = useVoiceLevels(voice.controller.state);
  const perception = useActivePerception();

  const stage = useViewportStage();
  const fade = useControlFade();
  const fullscreen = useFullscreen();

  // Recomputed whenever the truth, the clock or the local voice session moves —
  // and only then. The intent is a pure function of all three, so there is no
  // hidden animation state; the output envelope is a sampled measurement.
  const overlay = useMemo(() => voiceOverlayFrom(voice.controller, levels), [voice.controller, levels]);
  const intent = useMemo(() => visualFor(truth, now, overlay), [truth, now, overlay]);
  // Every ambient channel is read from its own claim so none of them can
  // overwrite another (see `prefixClaim`).
  const eye = useMemo(() => eyeView(eyeClaim(truth, now)), [truth, now]);
  const presence = useMemo(() => presenceView(presenceClaim(truth, now)), [truth, now]);
  const release = useMemo(() => releaseView(releaseClaim(truth, now)), [truth, now]);
  const display = useMemo(() => displayView(displayClaim(truth, now)), [truth, now]);
  const alarm = useMemo(() => alarmView(alarmClaim(truth, now)), [truth, now]);

  // The eye's reconcile seam, kept exactly as `EyeControl` holds it in the
  // cockpit: "gözünü kapat" spoken elsewhere, another device's owner action,
  // or the endpoint's own idempotent default all reach this the same way, and
  // this device's camera has no honest reason to keep running. The store
  // decides (`shouldStopLocalPerception`) from the bus view WITH its date, so
  // an old `eye.disabled` can never cancel a newer enable. Minimal mode no
  // longer renders `EyeControl`, so the seam lives here instead — losing it
  // would leave a camera running that the Cloud Core believes is off.
  const { status: eyeStatus, ageMs: eyeAgeMs, expired: eyeExpired } = eye;
  const { stopLocalIfStale } = perception;
  useEffect(() => {
    stopLocalIfStale({ status: eyeStatus, ageMs: eyeAgeMs, expired: eyeExpired });
  }, [eyeStatus, eyeAgeMs, eyeExpired, stopLocalIfStale]);

  const voiceState = voice.controller.state;
  const voiceLive = isLiveState(voiceState);
  const stageStyle = stage && stage.size > 0 ? { width: stage.size, height: stage.size } : undefined;
  // A server this build is ahead of never publishes the alarm, display,
  // operator or document states. Saying so is the difference between "nothing
  // is set" and "this server cannot tell you whether anything is set".
  const contractLag =
    truth.contractVersion !== null && truth.contractVersion < KNOWN_CONTRACT_VERSION
      ? contractLagNote(truth.contractVersion, KNOWN_CONTRACT_VERSION)
      : null;

  // B23 req 720: Minimal mode means the stage is alone with the owner. The control
  // cluster has faded after four idle seconds since M18.3; the site nav (req 685) is new
  // here and would otherwise be the one piece of chrome that never goes away. It fades
  // with the same state rather than on a second timer, and it fades rather than
  // disappearing: a deliberate move of the pointer brings it back, which is the whole
  // difference between quiet and gone.
  useEffect(() => {
    if (typeof document === "undefined") return;
    document.body.dataset.coreFaded = fade.faded ? "yes" : "no";
    return () => {
      delete document.body.dataset.coreFaded;
    };
  }, [fade.faded]);

  return (
    // B25 req 724: the Core is a landmark and has a heading, like every other page. The
    // heading is for screen readers only — a visible title would be exactly the chrome the
    // manifest refuses around a full-viewport presence, and "no chrome" was being paid for
    // by a page a screen reader could not enter.
    <main
      className="core-shell"
      data-core-mode="minimal"
      data-core-build={CORE_BUILD_ID}
      data-fullscreen={fullscreen.active ? "yes" : "no"}
    >
      <h1 className="visually-hidden">Çekirdek</h1>
      <div
        className="core-shell-stage"
        data-core-stage
        data-stage-size={stage?.size ?? ""}
        data-stage-coverage={stage ? stage.coverage.toFixed(3) : ""}
        data-stage-band={stage?.band ?? ""}
        style={stageStyle}
      >
        <CoreView intent={intent} tier={tier} force2d={force2d} />
      </div>

      <ConnectionDot connection={truth.connection} contractLag={contractLag} />

      <CoreControls
        opacity={fade.opacity}
        faded={fade.faded}
        onHold={fade.hold}
        onRelease={fade.release}
        voiceConnected={voiceLive}
        voiceBusy={isBusyState(voiceState)}
        voiceReady={voice.ready}
        onVoice={() => void (voiceLive ? actions.disconnect() : actions.connect())}
        eyeRunning={perception.status.running}
        eyeBusy={perception.busy}
        eyeServerStatus={eye.status}
        onEye={() => void (perception.status.running ? perception.stop() : perception.start())}
        tier={tier}
        onTier={setTier}
        force2d={force2d}
        onForce2d={setForce2d}
        fullscreen={fullscreen.active}
        fullscreenSupported={fullscreen.supported}
        onFullscreen={fullscreen.toggle}
      />

      {/* The caption: one short semantic line over the Core's lower edge. */}
      <div className="core-caption" data-core-caption>
        <StateReadout intent={intent} compact />
      </div>

      {/* The ambient strip. It does NOT fade with the control cluster, and
          that is deliberate: a parent's opacity cannot be undone by a child,
          so a fading strip would fade the camera cell with it — and a faded
          privacy assurance is not one. The strip is facts, not controls. */}
      <div className="core-strip" data-core-strip>
        <AmbientBand
          eye={eye}
          presence={presence}
          release={release}
          display={display}
          alarm={alarm}
        />
      </div>
    </main>
  );
}

export default function CorePage() {
  return (
    <OwnerGate>
      <MinimalCore />
    </OwnerGate>
  );
}
