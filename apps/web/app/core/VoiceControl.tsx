"use client";

/**
 * The Core's voice cell, wired to the one voice store (ADR-0061 §3).
 *
 * Owns nothing: `useVoiceSession` is a subscription to the tab-wide
 * controller, so this component mounting, unmounting or existing twice never
 * creates a session, a microphone or a transport. Every pixel is
 * `VoiceControlView`, tested directly.
 */

import { useEffect } from "react";

import { useVoiceSession } from "../lib/voice/useVoiceSession";
import VoiceControlView from "./VoiceControlView";

export default function VoiceControl() {
  const { voice, actions } = useVoiceSession();

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
    />
  );
}
