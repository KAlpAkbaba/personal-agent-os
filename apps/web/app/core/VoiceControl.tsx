"use client";

/**
 * The Core's voice cell, wired to the one voice store (ADR-0061 §3) and, since
 * ADR-0173, to the one local voice mode of this tab.
 *
 * Owns nothing: `useVoiceSession` and `useLocalVoiceMode` are subscriptions to
 * tab-wide singletons, so this component mounting, unmounting or existing twice
 * never creates a session, a microphone or a transport. The "Yerel mod" switch
 * is a per-browser preference (`usePreferences`), read once on mount. Every
 * pixel is `VoiceControlView`, tested directly.
 */

import { useEffect } from "react";

import { useLocalVoiceMode } from "../lib/voice/useLocalVoiceMode";
import { useVoiceSession } from "../lib/voice/useVoiceSession";
import { useCorePreferences } from "./usePreferences";
import VoiceControlView from "./VoiceControlView";

export default function VoiceControl() {
  const { voice, actions } = useVoiceSession();
  const { localVoice, setLocalVoice } = useCorePreferences();
  const { local, start, stop } = useLocalVoiceMode();

  // Device labels: known before a grant only for previously-granted origins,
  // and refreshed by the store itself after every connect. `actions` is
  // memoised per store, so this runs once when the rig becomes ready.
  useEffect(() => {
    if (voice.ready) void actions.refreshDevices();
  }, [voice.ready, actions]);

  return (
    <VoiceControlView
      voice={voice}
      onConnect={() => void actions.connect()}
      onDisconnect={() => void actions.disconnect()}
      onReconnect={() => void actions.reconnect()}
      local={{
        enabled: localVoice,
        snapshot: local,
        onToggle: (on) => {
          setLocalVoice(on);
          // Switching back to the paid path ends the local session; nothing else changes.
          if (!on) void stop();
        },
        onStart: () => void start(),
        onStop: () => void stop(),
      }}
    />
  );
}
