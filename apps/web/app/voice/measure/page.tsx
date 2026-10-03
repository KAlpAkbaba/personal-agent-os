"use client";

/**
 * `/voice/measure` - 'Ölçüm kaydı': the owner reads the twenty measurement sentences once
 * per place; the page writes the 16 kHz WAV, uploads it to the Cloud Core and keeps what
 * Chrome's recogniser wrote from the same audio (team/plans/measure-recording-page-adr.md).
 *
 * One microphone per tab (ADR-0061): while the tab's voice session is live, recording is
 * off and the page says why. The probe microphone is opened with the device and
 * constraints the voice rig uses, and every take closes it.
 */

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

import OwnerGate, { SignOutButton } from "../../components/OwnerGate";
import { browserRecorderDeps } from "../../lib/voice/measure/browser";
import { MeasureSession } from "../../lib/voice/measure/session";
import { constraintsFor } from "../../lib/voice/profile";
import { getVoiceStore, isLiveState } from "../../lib/voice/store";
import MeasureView, { bindView } from "./MeasureView";

function MeasureScreen() {
  const voiceStore = getVoiceStore();
  const voice = useSyncExternalStore(voiceStore.subscribe, voiceStore.getSnapshot, voiceStore.getServerSnapshot);
  const live = isLiveState(voice.controller.state);
  const [session] = useState(
    () =>
      new MeasureSession({
        recorder: browserRecorderDeps,
        mic: () => {
          const snap = voiceStore.getSnapshot();
          return {
            deviceId: snap.micId || undefined,
            constraints: snap.profile ? constraintsFor(snap.profile) : {},
          };
        },
        live: () => isLiveState(voiceStore.getSnapshot().controller.state),
      }),
  );
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);

  useEffect(() => {
    void session.load();
    return () => session.dispose();
  }, [session]);

  return <MeasureView {...bindView(session, snapshot, live)} />;
}

export default function MeasurePage() {
  return (
    <OwnerGate>
      <main>
        <div className="status-row" style={{ borderBottom: "none", paddingBottom: 0 }}>
          <h1 style={{ margin: 0 }}>Ölçüm kaydı</h1>
          <SignOutButton />
        </div>
        <p className="subtitle">
          Ekrandaki cümleyi oku; dosyayı, adını ve etiketini sayfa yazar. <Link href="/voice">Sesli Asistan</Link>
        </p>
        <MeasureScreen />
      </main>
    </OwnerGate>
  );
}
