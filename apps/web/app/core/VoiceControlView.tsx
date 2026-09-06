/**
 * The Core's voice cell — markup only, a pure function of its props.
 *
 * Split from `VoiceControl.tsx` (which owns the store subscription) the same
 * way `EyeControlView` is split from `EyeControl`, so it renders under
 * `react-dom/server` and `tests/uistate/voice-control-render.test.tsx` can
 * assert on it without a browser.
 *
 * Deliberately NOT a settings form (M18 brief §3): the selected microphone and
 * speaker are *stated*, not chosen, and the one control is connect /
 * reconnect / disconnect. Choosing devices, voices and noise modes stays on
 * `/voice`, which this cell links to.
 */

import Link from "next/link";

import { MODE_LABEL, VOICE_STATE_LABEL } from "../lib/voice/labels";
import {
  type VoiceStoreSnapshot,
  isBusyState,
  isLiveState,
  microphoneName,
  speakerName,
} from "../lib/voice/store";

export type VoiceControlViewProps = {
  voice: VoiceStoreSnapshot;
  onConnect: () => void;
  onDisconnect: () => void;
  onReconnect: () => void;
};

export default function VoiceControlView({ voice, onConnect, onDisconnect, onReconnect }: VoiceControlViewProps) {
  const { controller } = voice;
  const state = controller.state;
  const live = isLiveState(state);
  const busy = isBusyState(state);
  // Closed or failed once: the owner reconnects from here, without leaving /core.
  const reopenable = state === "closed" || state === "error";
  const connection = live ? "connected" : busy ? "connecting" : "disconnected";

  return (
    <div
      className="ambient-cell voice-cell"
      data-voice-connection={connection}
      data-voice-state={state}
      data-voice-ready={voice.ready ? "yes" : "no"}
      data-voice-simulated={voice.simulated ? "yes" : "no"}
    >
      <span className="ambient-title">
        {live ? "Ses bağlı" : busy ? "Ses bağlanıyor" : "Ses bağlı değil"}
        {" · "}
        {VOICE_STATE_LABEL[state]}
      </span>

      {!voice.ready ? (
        <span className="muted">Ses oturumu bu tarayıcıda henüz kurulmadı.</span>
      ) : (
        <>
          <span className="muted" data-voice-mic>
            Mikrofon: {microphoneName(voice)}
          </span>
          <span className="muted" data-voice-speaker>
            Hoparlör: {speakerName(voice)}
          </span>
          <span className="muted" data-voice-noise-mode={voice.profile?.environmentMode ?? ""}>
            Gürültü modu: {voice.profile ? MODE_LABEL[voice.profile.environmentMode] : "profil yok (mikrofon açılınca belirlenir)"}
          </span>
          {controller.sessionId && (
            <span className="muted" data-voice-provider>
              Sağlayıcı: {controller.provider ?? "—"} · {controller.transport ?? "—"}
              {voice.simulated && " · simülatör (ses yolu yok)"}
            </span>
          )}
          {controller.toolsRunning.length > 0 && (
            <span className="muted" data-voice-tools>
              Çalışan araç: {controller.toolsRunning.join(", ")}
            </span>
          )}
          {controller.lastError && (
            <span className="muted" data-voice-error="yes">
              {controller.lastError}
            </span>
          )}
        </>
      )}

      <div className="voice-cell-actions">
        {live ? (
          <button type="button" className="core-chip" data-voice-action="disconnect" onClick={onDisconnect}>
            Bağlantıyı kes
          </button>
        ) : reopenable ? (
          <button type="button" className="core-chip" data-voice-action="reconnect" disabled={!voice.ready} onClick={onReconnect}>
            Yeniden bağlan
          </button>
        ) : (
          <button type="button" className="core-chip" data-voice-action="connect" disabled={busy || !voice.ready} onClick={onConnect}>
            {busy ? VOICE_STATE_LABEL[state] : "Bağlan"}
          </button>
        )}
        <Link href="/voice" className="voice-cell-link">
          Tanılama →
        </Link>
      </div>
    </div>
  );
}
