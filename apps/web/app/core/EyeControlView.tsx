/**
 * The Active Eye control's markup — a pure function of its props, no hooks.
 *
 * Split out from `EyeControl.tsx` (which owns `useActivePerception`, the
 * camera and the network) so this half can be rendered with
 * `react-dom/server` and asserted on across every status/permission
 * combination the same way `tests/uistate/ambient-render.test.tsx` does for
 * `AmbientBand` — the same reasoning `CoreView`/`CoreFallback2D` already
 * split rendering from capability-detection in this directory.
 */

import { ACTIVITY_LEVEL_LABEL, AWAKE_STATE_LABEL, localEyeStatusDetail, localEyeStatusTitle } from "../lib/eye/labels";
import type { PerceptionStatus } from "../lib/eye/perception";
import type { CameraPermission } from "../lib/eye/types";
import type { EyeView } from "../lib/uistate/ambient";
import { EYE_LABEL, formatConfidence } from "../lib/uistate/labels";

export type EyeControlViewProps = {
  /** The server-side truth, computed by the page exactly like `AmbientBand`'s. */
  eye: EyeView;
  status: PerceptionStatus;
  permission: CameraPermission;
  busy: boolean;
  /** An error surfaced by the last owner action (start/stop), if any. */
  error: string | null;
  onStart: () => void;
  onStop: () => void;
};

export default function EyeControlView({
  eye,
  status,
  permission,
  busy,
  error,
  onStart,
  onStop,
}: EyeControlViewProps) {
  const observation = status.lastObservation;

  return (
    <div
      className="ambient-cell"
      data-eye-local-status={status.running ? "running" : "stopped"}
      data-eye-permission={permission}
    >
      <span className="ambient-title">{localEyeStatusTitle(status.running, permission)}</span>
      <span className="muted">{localEyeStatusDetail(status.running, permission)}</span>

      {/* The server-side truth, named explicitly so a mismatch (this device
          running locally while the Cloud Core still says disabled, or vice
          versa — e.g. another device just disabled it) is visible rather than
          silently reconciled into one number. */}
      <span className="muted" data-eye-server-status={eye.status}>
        Sunucu: {EYE_LABEL[eye.status]}
      </span>

      {status.cameraLabel && <span className="muted">Kamera: {status.cameraLabel}</span>}
      {status.motion && (
        // The numbers the verdict rests on, so "why does it think that?" is readable off
        // the Core. Never a frame: a cell fraction and an age.
        <span className="muted" data-eye-motion>
          Son hareket:{" "}
          {status.motion.msSinceLastMotion === null
            ? "henüz yok"
            : `${Math.round(status.motion.msSinceLastMotion / 1000)} sn önce (${ACTIVITY_LEVEL_LABEL[status.motion.lastMotionLevel]})`}
          {" · "}değişen hücre %{Math.round(status.motion.changedCellRatio * 100)}
        </span>
      )}

      {observation && (
        <span className="muted" data-eye-last-observation>
          {ACTIVITY_LEVEL_LABEL[observation.activity_level]} ·{" "}
          {formatConfidence(observation.presence_confidence)} ·{" "}
          {AWAKE_STATE_LABEL[observation.awake_state]}
        </span>
      )}

      <button
        type="button"
        className="core-chip"
        aria-pressed={status.running}
        disabled={busy}
        data-eye-control-action={status.running ? "stop" : "start"}
        onClick={status.running ? onStop : onStart}
      >
        {status.running ? "Gözü kapat" : "Gözü aç"}
      </button>

      {(error || status.lastError) && (
        <span className="muted" data-eye-local-error="yes">
          {error ?? status.lastError}
        </span>
      )}
    </div>
  );
}
