/**
 * Minimal mode's control cluster — markup only, a pure function of its props.
 *
 * M18.3 §9 replaced the old chrome bar on `/core` with this: six small
 * controls floating over the stage, which fade to a quarter after four idle
 * seconds and return on the first movement or focus. Three properties matter
 * more than the appearance, and all three are asserted in
 * `tests/uistate/minimal-mode.test.tsx`:
 *
 * 1. **Fading is not hiding.** The cluster stays in the DOM, in the tab order
 *    and in the accessibility tree at every opacity; only `opacity` changes,
 *    and `data-controls-faded` says which state it is in.
 * 2. **Fullscreen is the owner's gesture.** The handler is bound to a button
 *    and to nothing else. There is no automatic entry, no "immersive on load",
 *    and while fullscreen is on there is a visible, labelled way out (Esc also
 *    works, because the browser guarantees it).
 * 3. **Nothing here writes to the system.** The tier, the 2D switch and
 *    fullscreen are display preferences of this browser; the voice and eye
 *    buttons call the same store actions the cockpit's cells do. The Core has
 *    no write path to the API and this file does not give it one.
 */

import Link from "next/link";

import { QUALITY_TIERS, type QualityTier, TIER_LABEL } from "../lib/uistate/quality";
import type { EyeStatus } from "../lib/uistate/ambient";

export type CoreControlsProps = {
  /** 0.25 while receded, 1 while present. Never 0: an invisible control is gone. */
  opacity: number;
  faded: boolean;
  /** Pointer/focus entering and leaving the cluster: it must not fade under the owner. */
  onHold: () => void;
  onRelease: () => void;

  // voice
  voiceConnected: boolean;
  voiceBusy: boolean;
  voiceReady: boolean;
  onVoice: () => void;

  // the eye
  eyeRunning: boolean;
  eyeBusy: boolean;
  eyeServerStatus: EyeStatus;
  onEye: () => void;

  // display preferences
  tier: QualityTier;
  onTier: (tier: QualityTier) => void;
  force2d: boolean;
  onForce2d: (value: boolean) => void;

  // the viewport
  fullscreen: boolean;
  fullscreenSupported: boolean;
  onFullscreen: () => void;
};

export default function CoreControls({
  opacity,
  faded,
  onHold,
  onRelease,
  voiceConnected,
  voiceBusy,
  voiceReady,
  onVoice,
  eyeRunning,
  eyeBusy,
  eyeServerStatus,
  onEye,
  tier,
  onTier,
  force2d,
  onForce2d,
  fullscreen,
  fullscreenSupported,
  onFullscreen,
}: CoreControlsProps) {
  return (
    <div
      className="core-controls"
      data-core-controls
      data-controls-faded={faded ? "yes" : "no"}
      style={{ opacity }}
      onPointerEnter={onHold}
      onPointerLeave={onRelease}
      onFocus={onHold}
      onBlur={onRelease}
    >
      <button
        type="button"
        className="core-chip"
        data-control="voice"
        aria-pressed={voiceConnected}
        disabled={!voiceReady || voiceBusy}
        onClick={onVoice}
        title={voiceConnected ? "Ses oturumunu kapat" : "Ses oturumunu aç"}
      >
        {voiceConnected ? "Sesi kapat" : voiceBusy ? "Bağlanıyor…" : "Ses"}
      </button>

      <button
        type="button"
        className="core-chip"
        data-control="eye"
        data-eye-server={eyeServerStatus}
        aria-pressed={eyeRunning}
        disabled={eyeBusy}
        onClick={onEye}
        title={eyeRunning ? "Kamerayı kapat" : "Kamerayı aç"}
      >
        {eyeRunning ? "Gözü kapat" : "Göz"}
      </button>

      <span className="core-controls-group" role="group" aria-label="Görüntü kalitesi">
        {QUALITY_TIERS.map((option) => (
          <button
            key={option}
            type="button"
            className="core-chip"
            aria-pressed={tier === option}
            onClick={() => onTier(option)}
            data-tier-option={option}
          >
            {TIER_LABEL[option]}
          </button>
        ))}
      </span>

      <button
        type="button"
        className="core-chip"
        data-control="force-2d"
        aria-pressed={force2d}
        onClick={() => onForce2d(!force2d)}
      >
        2B
      </button>

      {/* The exit is as visible as the entry: an owner who cannot find their
          way out of fullscreen has been trapped by the interface. */}
      <button
        type="button"
        className="core-chip"
        data-control="fullscreen"
        aria-pressed={fullscreen}
        disabled={!fullscreenSupported}
        onClick={onFullscreen}
        title={
          fullscreen
            ? "Tam ekrandan çık (Esc de çıkarır)"
            : fullscreenSupported
              ? "Tam ekran"
              : "Bu tarayıcıda tam ekran kullanılamıyor"
        }
      >
        {fullscreen ? "Tam ekrandan çık" : "Tam ekran"}
      </button>

      <Link href="/core/cockpit" className="core-controls-link" data-control="cockpit">
        Kokpit →
      </Link>
    </div>
  );
}
