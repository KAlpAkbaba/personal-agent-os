/**
 * The band beside the Core: the room, and the release path.
 *
 * Separate from `StateReadout` on purpose. The readout answers "what is the
 * agent doing"; this answers "who is here, is the camera on, and is anything
 * being deployed" — three questions whose answers must never be mistaken for
 * activity, and one of which (the camera) is a privacy indicator.
 *
 * Three obligations this component carries alone:
 *
 * 1. **The camera indicator never claims "off" without evidence.** "Nobody has
 *    told us" and "the eye is disabled" are different sentences, and only one
 *    of them is a privacy assurance.
 * 2. **Presence is stated with its confidence.** A presence with no confidence
 *    says so; a `likely` state is worded as likely. Neither is rounded up.
 * 3. **The release strip shows and never acts.** There is no control here, and
 *    the note says where the authority actually lives (ADR-0055).
 */

import {
  type AlarmView,
  DISPLAY_IS_AMBIENT_ONLY,
  type DisplayView,
  type EyeView,
  type PresenceView,
  RELEASE_AUTHORITY_NOTE,
  type ReleaseView,
} from "../lib/uistate/ambient";
import {
  ALARM_DETAIL,
  ALARM_LABEL,
  DISPLAY_DETAIL,
  DISPLAY_LABEL,
  EYE_DETAIL,
  EYE_LABEL,
  PRESENCE_LABEL,
  RELEASE_LABEL,
  formatAge,
  formatAlarmLevel,
  formatConfidence,
  formatProgress,
} from "../lib/uistate/labels";

export type AmbientBandProps = {
  eye: EyeView;
  presence: PresenceView;
  release: ReleaseView;
  /**
   * v3. Both optional so a caller that has not been taught them renders
   * exactly what it rendered before, rather than two cells full of "unknown"
   * it never asked for.
   */
  display?: DisplayView;
  alarm?: AlarmView;
};

export default function AmbientBand({ eye, presence, release, display, alarm }: AmbientBandProps) {
  return (
    <section className="ambient-band" aria-label="Ortam ve yayın durumu">
      <div className="ambient-cell" data-eye-status={eye.status}>
        <span className="ambient-title">{EYE_LABEL[eye.status]}</span>
        <span className="muted">{EYE_DETAIL[eye.status]}</span>
        {eye.unknownState && (
          <span className="muted" data-eye-unknown="yes">
            Kameranın bildirdiği durum bu sürümde tanınmıyor.
          </span>
        )}
        {eye.camera && <span className="muted">Cihaz: {eye.camera}</span>}
        {eye.status !== "untold" && (
          <span className="muted">Bildirim: {formatAge(eye.ageMs)}</span>
        )}
      </div>

      <div className="ambient-cell" data-presence={presence.kind}>
        <span className="ambient-title">{PRESENCE_LABEL[presence.kind]}</span>
        {presence.kind === "unknown" && presence.lastKnown === null ? (
          // Nothing was ever published. "Unknown because nobody said" is a
          // different sentence from "unknown because the observation aged out".
          <span className="muted">Henüz bir varlık gözlemi bildirilmedi.</span>
        ) : presence.kind === "unknown" && presence.lastKnown !== null ? (
          // The observation decayed. Say what it was and that it is no longer
          // evidence about now — never keep drawing the owner as present.
          <span className="muted">
            Son bilinen: {PRESENCE_LABEL[presence.lastKnown]} ({formatAge(presence.ageMs)}). Bu
            gözlem artık şu an için kanıt sayılmıyor.
          </span>
        ) : (
          <>
            <span className="muted" data-presence-confidence>
              {formatConfidence(presence.confidence)}
              {presence.signals !== null && ` · ${presence.signals} sinyal`}
            </span>
            <span className="muted">Gözlem: {formatAge(presence.ageMs)}</span>
          </>
        )}
        <span className="muted">Algı kimlik doğrulaması değildir.</span>
      </div>

      {/* v3: the screens. Ambient only — a dark monitor says nothing about the
          agent, and this cell is the only place in the UI that says anything
          about display power at all. */}
      {display && (
        <div className="ambient-cell" data-display-state={display.state}>
          <span className="ambient-title">{DISPLAY_LABEL[display.state]}</span>
          <span className="muted">{DISPLAY_DETAIL[display.state]}</span>
          {display.unknownState && (
            <span className="muted" data-display-unknown="yes">
              Bildirilen ekran durumu bu sürümde tanınmıyor.
            </span>
          )}
          {display.reason && <span className="muted">Sebep: {display.reason}</span>}
          {display.state !== "untold" && (
            <span className="muted">Bildirim: {formatAge(display.ageMs)}</span>
          )}
          <span className="muted">{DISPLAY_IS_AMBIENT_ONLY}</span>
        </div>
      )}

      {/* v3: the wake alarm. "Kuruldu" and "çalıyor" are different sentences,
          and a failure is stated as a failure rather than drawn as quiet. */}
      {alarm && (
        <div className="ambient-cell" data-alarm-stage={alarm.stage} data-alarm-severity={alarm.severity}>
          <span className="ambient-title">
            {ALARM_LABEL[alarm.stage]}
            {alarm.isTest && <span className="muted" data-alarm-test="yes"> · test</span>}
          </span>
          <span className="muted">{ALARM_DETAIL[alarm.stage]}</span>
          {alarm.label && <span className="muted" data-alarm-label>{alarm.label}</span>}
          {alarm.stage !== "none" && (
            <>
              <span className="muted" data-alarm-level={alarm.level === null ? "unknown" : alarm.level}>
                {formatAlarmLevel(alarm.level)}
              </span>
              <span className="muted">Bildirim: {formatAge(alarm.ageMs)}</span>
            </>
          )}
        </div>
      )}

      <div className="ambient-cell" data-release-stage={release.stage}>
        <span className="ambient-title">{RELEASE_LABEL[release.stage]}</span>
        {release.stage === "none" ? (
          <span className="muted">Şu anda izlenen bir yayın adımı bildirilmedi.</span>
        ) : (
          <>
            {release.moduleId && <span className="muted">Aday: {release.moduleId}</span>}
            {release.riskTier !== null && (
              <span className="muted">Risk kademesi: {release.riskTier}</span>
            )}
            {release.progress !== null && (
              <span className="muted">İlerleme: {formatProgress(release.progress)}</span>
            )}
            <span className="muted">Bildirim: {formatAge(release.ageMs)}</span>
          </>
        )}
        <span className="muted">{RELEASE_AUTHORITY_NOTE}</span>
      </div>
    </section>
  );
}
