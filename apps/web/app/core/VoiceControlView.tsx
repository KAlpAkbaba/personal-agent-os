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

import { MODE_LABEL, VOICE_STATE_LABEL, speechPhaseNote, voiceStateLabel } from "../lib/voice/labels";
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
  // B20 req 235: there is no provider, and pressing a button will not produce one. A
  // condition the owner has to act on OUTSIDE the page (a key in the DPAPI store) is the
  // one case where the control is disabled rather than hopeful; a provider that is merely
  // down stays retryable, because that one can come back on its own.
  const blocked = controller.unavailable !== null && !controller.unavailable.retryable;
  // Closed or failed once: the owner reconnects from here, without leaving /core.
  const reopenable = (state === "closed" || state === "error") && !blocked;
  const connection = live ? "connected" : busy ? "connecting" : "disconnected";

  return (
    <div
      className="ambient-cell voice-cell"
      data-voice-connection={connection}
      data-voice-state={state}
      data-speech-phase={controller.speech.phase}
      data-voice-ready={voice.ready ? "yes" : "no"}
      data-voice-simulated={voice.simulated ? "yes" : "no"}
    >
      <span className="ambient-title">
        {live ? "Ses bağlı" : busy ? "Ses bağlanıyor" : "Ses bağlı değil"}
        {" · "}
        {voiceStateLabel(controller)}
        {/* ADR-0066: the lifecycle, not the energy — "draining" is speaking with the generation over. */}
        {speechPhaseNote(controller) && <span className="muted" data-speech-note>{` (${speechPhaseNote(controller)})`}</span>}
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
          {/* B20 req 235: a standing condition, said as one. Not an error line, and not a
              button that cannot work: a missing key stays missing however often it is
              pressed, and only the owner can change that. */}
          {controller.unavailable ? (
            <span className="muted" data-voice-unavailable={controller.unavailable.errorClass || "unknown"}>
              {controller.unavailable.message}
              {controller.unavailable.remedy ? ` ${controller.unavailable.remedy}` : ""}
            </span>
          ) : (
            controller.lastError && (
              <span className="muted" data-voice-error="yes">
                {controller.lastError}
              </span>
            )
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
          <button
            type="button"
            className="core-chip"
            data-voice-action="connect"
            disabled={busy || !voice.ready || blocked}
            onClick={onConnect}
          >
            {busy ? VOICE_STATE_LABEL[state] : blocked ? "Ses kullanılamıyor" : "Bağlan"}
          </button>
        )}
        <Link href="/voice" className="voice-cell-link">
          Tanılama →
        </Link>
      </div>
    </div>
  );
}
