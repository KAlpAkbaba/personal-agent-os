/**
 * The "El kumandası" control's markup — a pure function of its props, no hooks. Split from
 * `GestureControl.tsx` the same way `EyeControlView`/`EyeControl` are split (see that file's
 * own docstring for why): this half renders with `react-dom/server` and is asserted on
 * directly, without touching the eye store, the local voice mode, or a camera.
 *
 * ADR-0199 Stage 2: also shows the pinch-mouse/fist-drag's own state — the active mode
 * ("Fare"/"Sürükleme"), the owner's gain setting (with a ± control), the stream's
 * connection state, and the last socket close reason.
 */

import type { GestureControllerSnapshot } from "../lib/gesture/controller";
import type { PointerStreamSnapshot } from "../lib/gesture/pointer";

export type GestureControlViewProps = {
  gesture: GestureControllerSnapshot;
  pointer: PointerStreamSnapshot;
  onToggle: (on: boolean) => void;
  onGainChange: (value: number) => void;
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
  // ADR-0199 Stage 2.
  mouse_start: "fare modu başladı",
  mouse_move: "fare hareketi",
  mouse_end: "fare modu bitti",
  left_click: "sol tık",
  right_click: "sağ tık",
  drag_start: "sürükleme başladı",
  drag_move: "sürükleme hareketi",
  drag_end: "sürükleme bitti",
};

/** ADR-0199 Stage 2: the pointer mode's own HUD label. */
export const POINTER_MODE_LABEL_TR: Record<"mouse" | "drag", string> = {
  mouse: "Fare",
  drag: "Sürükleme",
};

/** ADR-0199 Stage 2: the stream's connection state, HUD-worded. */
export const POINTER_STATE_LABEL_TR: Record<PointerStreamSnapshot["state"], string> = {
  idle: "kapalı",
  opening: "açılıyor",
  open: "bağlı",
};

const GAIN_STEP = 0.5;

export default function GestureControlView({ gesture, pointer, onToggle, onGainChange }: GestureControlViewProps) {
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
        <span className="muted" data-gesture-tracking="yes" data-gesture-armed={gesture.measure?.armed ? "yes" : "no"}>
          {gesture.measure?.armed ? "HAZIR" : "İzleniyor"}{gesture.trackingFps !== null ? ` · ${gesture.trackingFps.toFixed(0)} kare/sn` : ""}
          {gesture.measure && !gesture.measure.armed ? " · açık avucu 0,4 sn sabit tutun" : ""}
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

      {/* ADR-0199 Stage 2: the pinch-mouse / fist-drag's own state. */}
      {pointer.mode && (
        <span className="muted" data-pointer-mode={pointer.mode} data-pointer-state={pointer.state}>
          {POINTER_MODE_LABEL_TR[pointer.mode]} · akış {POINTER_STATE_LABEL_TR[pointer.state]}
        </span>
      )}

      {pointer.lastCloseReason && (
        <span className="muted" data-pointer-close-reason="yes">
          {pointer.lastCloseReason}
        </span>
      )}

      <span className="muted" data-pointer-gain={pointer.gain}>
        Fare kazancı: {pointer.gain.toFixed(1)}
        <button
          type="button"
          className="core-chip"
          data-pointer-gain-down="yes"
          aria-label="Fare kazancını azalt"
          onClick={() => onGainChange(Math.round((pointer.gain - GAIN_STEP) * 10) / 10)}
        >
          −
        </button>
        <button
          type="button"
          className="core-chip"
          data-pointer-gain-up="yes"
          aria-label="Fare kazancını artır"
          onClick={() => onGainChange(Math.round((pointer.gain + GAIN_STEP) * 10) / 10)}
        >
          +
        </button>
      </span>
    </div>
  );
}
