/**
 * The "El kumandası" control's markup — a pure function of its props, no hooks. Split from
 * `GestureControl.tsx` the same way `EyeControlView`/`EyeControl` are split (see that file's
 * own docstring for why): this half renders with `react-dom/server` and is asserted on
 * directly, without touching the eye store, the local voice mode, or a camera.
 */

import type { GestureControllerSnapshot } from "../lib/gesture/controller";

export type GestureControlViewProps = {
  gesture: GestureControllerSnapshot;
  onToggle: (on: boolean) => void;
};

/** Owner-facing Turkish names for the closed gesture vocabulary (HUD only — never spoken). */
export const GESTURE_LABEL_TR: Record<string, string> = {
  swipe_left: "sola kaydırma",
  swipe_right: "sağa kaydırma",
  swipe_up: "yukarı kaydırma",
  swipe_down: "aşağı kaydırma",
  rotate_cw: "sağa çevirme",
  rotate_ccw: "sola çevirme",
  spread: "iki eli açma",
  gather: "iki eli birleştirme",
  pinch_start: "tutma",
  pinch_release: "bırakma",
};

export default function GestureControlView({ gesture, onToggle }: GestureControlViewProps) {
  return (
    <div className="ambient-cell" data-gesture-enabled={gesture.enabled ? "yes" : "no"} data-gesture-running={gesture.running ? "yes" : "no"}>
      <span className="ambient-title">El kumandası</span>

      <button
        type="button"
        className="core-chip"
        aria-pressed={gesture.enabled}
        data-gesture-toggle={gesture.enabled ? "on" : "off"}
        onClick={() => onToggle(!gesture.enabled)}
      >
        {gesture.enabled ? "El kumandasını kapat" : "El kumandasını aç"}
      </button>

      {gesture.enabled && !gesture.running && (
        <span className="muted" data-gesture-waiting="yes">
          Bekleniyor: göz açık ve yerel ses oturumu gerekiyor.
        </span>
      )}

      {gesture.running && (
        <span className="muted" data-gesture-tracking="yes">
          İzleniyor{gesture.trackingFps !== null ? ` · ${gesture.trackingFps.toFixed(0)} kare/sn` : ""}
        </span>
      )}

      {gesture.running && gesture.measure && (
        <span className="muted" data-gesture-measure="yes">
          Kalibrasyon: el {gesture.measure.hands}
          {gesture.measure.openness.length > 0 ? ` · açıklık ${gesture.measure.openness.map((o) => o.toFixed(2)).join(" / ")}` : ""}
          {gesture.measure.pinch.length > 0 ? ` · pinç ${gesture.measure.pinch.map((p) => p.toFixed(2)).join(" / ")}` : ""}
          {gesture.measure.wristDistance !== null ? ` · eller arası ${gesture.measure.wristDistance.toFixed(2)}` : ""}
        </span>
      )}

      {gesture.lastGesture && (
        <span className="muted" data-gesture-last={gesture.lastGesture}>
          Son hareket: {GESTURE_LABEL_TR[gesture.lastGesture] ?? gesture.lastGesture}
        </span>
      )}

      {gesture.lastError && (
        <span className="muted" data-gesture-error="yes">
          {gesture.lastError}
        </span>
      )}
    </div>
  );
}
