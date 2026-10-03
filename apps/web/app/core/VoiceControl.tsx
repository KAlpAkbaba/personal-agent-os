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

import type { LocalModeSnapshot } from "../lib/voice/localMode";
import { useLocalVoiceMode } from "../lib/voice/useLocalVoiceMode";
import { useVoiceSession } from "../lib/voice/useVoiceSession";
import { useCorePreferences } from "./usePreferences";
import VoiceControlView, { type LocalModeViewProps } from "./VoiceControlView";
import { toggleLocalVoice } from "../lib/voice/localToggle";

export type LocalViewWiring = {
  localVoice: boolean;
  local: LocalModeSnapshot;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  answerPackQuestion: (yes: boolean) => void;
  setLocalVoice: (on: boolean) => void;
  disconnectPaid: () => Promise<void> | void;
};

/**
 * The local mode's view props, built in one pure place so the wiring is tested without a
 * DOM (tests/uistate/voice-control-pack-question.test.tsx). `onAnswerPack` passes the
 * click straight through: Chrome's `install()` needs the click still on the stack.
 */
export function buildLocalViewProps(w: LocalViewWiring): LocalModeViewProps {
  return {
    enabled: w.localVoice,
    snapshot: w.local,
    // A hand-over, never two live channels (security review 2026-09-19): the channel
    // being left is ended first - see lib/voice/localToggle.ts.
    onToggle: (on) =>
      void toggleLocalVoice(on, {
        setLocalVoice: w.setLocalVoice,
        stopLocal: () => w.stop(),
        disconnectPaid: () => w.disconnectPaid(),
      }),
    onStart: () => void w.start(),
    onStop: () => void w.stop(),
    onAnswerPack: (yes) => w.answerPackQuestion(yes),
  };
}

export default function VoiceControl() {
  const { voice, actions } = useVoiceSession();
  const { localVoice, setLocalVoice } = useCorePreferences();
  const { local, start, stop, answerPackQuestion } = useLocalVoiceMode();

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
      local={buildLocalViewProps({
        localVoice,
        local,
        start,
        stop,
        answerPackQuestion,
        setLocalVoice,
        disconnectPaid: () => actions.disconnect(),
      })}
    />
  );
}
