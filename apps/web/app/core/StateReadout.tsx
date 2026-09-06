/**
 * The words under the Core.
 *
 * The geometry can say "something is happening"; only this can say what, on
 * whose authority, and how long ago. It is rendered in both modes and in both
 * the 3D and 2D views, because the truthfulness of the page must not depend on
 * whether a GPU was available.
 *
 * Nothing here is decorative. Every line is present because it is the only
 * place a particular fact appears:
 *
 * - the headline names the state;
 * - the age names how stale the claim is;
 * - the subsystem names who said so;
 * - a progress bar appears only when a publisher reported real progress;
 * - counted evidence appears only when a publisher counted it.
 */

import {
  CONSTELLATION_MOTIF_NOTE,
  KIND_LABEL,
  SEVERITY_LABEL,
  SOURCE_LABEL,
  capabilityNodesLine,
  formatAge,
  formatProgress,
  kindDetail,
  stateLabel,
  subsystemLabel,
} from "../lib/uistate/labels";
import type { VisualIntent } from "../lib/uistate/visual";
import { isLive } from "../lib/uistate/visual";

export type StateReadoutProps = {
  intent: VisualIntent;
  /** Compact form for Minimal Core Mode; the cockpit uses the full form. */
  compact?: boolean;
  /** The true count, shown even when the tier capped what was drawn. */
  drawnSatellites?: number;
};

/** Severity only ever changes colour and wording; it never changes motion. */
const SEVERITY_CLASS: Record<string, string> = {
  info: "",
  notice: "sev-notice",
  warning: "sev-warning",
  critical: "sev-critical",
};

export default function StateReadout({
  intent,
  compact = false,
  drawnSatellites,
}: StateReadoutProps) {
  const live = isLive(intent);
  const capped =
    drawnSatellites !== undefined &&
    intent.sourceNodesKnown &&
    drawnSatellites < intent.sourceNodes;

  return (
    <div
      className={`core-readout ${compact ? "compact" : ""} ${SEVERITY_CLASS[intent.severity] ?? ""}`}
      data-core-kind={intent.kind}
      data-core-state={intent.state ?? ""}
      data-core-subsystem={intent.subsystem ?? ""}
      data-core-source={intent.source}
      data-voice-state={intent.voiceState ?? ""}
      data-live={live ? "yes" : "no"}
      data-severity={intent.severity}
    >
      <h2 className="core-headline">{KIND_LABEL[intent.kind]}</h2>

      {/*
        For a last-known or unreachable core the headline is about our
        knowledge, so the state it *was* still has to be named explicitly.
      */}
      {!live && intent.state && (
        <p className="core-sub" data-last-state={intent.state}>
          {stateLabel(intent.state)}
          {intent.ageMs !== null && ` · ${formatAge(intent.ageMs)}`}
        </p>
      )}

      {live && intent.ageMs !== null && (
        <p className="core-sub" data-age-ms={intent.ageMs}>
          {formatAge(intent.ageMs)}
          {intent.subsystem && ` · ${subsystemLabel(intent.subsystem)}`}
          {intent.severity !== "info" && ` · ${SEVERITY_LABEL[intent.severity]}`}
        </p>
      )}

      {/*
        ADR-0061 §4: the readout names which of the two sources produced the
        visual, on every render. A voice-sourced intent has no bus event and
        therefore no age or subsystem line above; this line is what it has.
      */}
      <p className="core-sub" data-source-line={intent.source}>
        {SOURCE_LABEL[intent.source]}
      </p>

      {!compact && <p className="core-detail">{kindDetail(intent.kind, intent.source)}</p>}

      {/*
        A publisher-supplied short label: a topic, a goal title. Never prose.
        For a voice-sourced speaking intent this is the speech caption (a tool,
        a narration position) — one semantic line, never the transcript.
      */}
      {intent.label && (
        <p className="core-label" data-label data-caption={intent.source === "voice" ? "yes" : undefined}>
          {intent.label}
        </p>
      )}

      {/* A speaking voice leg whose output path could not be measured says so
          rather than drawing stillness as silence. */}
      {intent.source === "voice" && intent.kind === "speaking" && intent.intensity === null && (
        <p className="muted core-count" data-output-level="unmeasured">
          Çıkış seviyesi ölçülemedi.
        </p>
      )}

      {intent.status && !compact && (
        <p className="core-status" data-status={intent.status}>
          aşama: <code>{intent.status}</code>
        </p>
      )}

      {/*
        A bar exists if and only if the publisher reported progress. Work of
        unknown length gets no bar, which is the whole of ADR-0052 §2.
      */}
      {intent.progress !== null ? (
        <div className="core-progress" data-progress={intent.progress.toFixed(3)}>
          <div className="core-progress-fill" style={{ width: `${intent.progress * 100}%` }} />
          <span className="core-progress-text">{formatProgress(intent.progress)}</span>
        </div>
      ) : (
        !compact &&
        live &&
        // A voice leg never reports progress; "not reported" would be noise there.
        intent.source === "bus" && (
          <p className="muted core-no-progress" data-no-progress>
            İlerleme bildirilmedi.
          </p>
        )
      )}

      {/* Research evidence, counted or explicitly not counted. */}
      {intent.kind === "researching" &&
        (intent.sourceNodesKnown ? (
          <p className="core-count" data-source-nodes={intent.sourceNodes} data-source-nodes-known="yes">
            {intent.sourceNodes === 0
              ? "Kalite kapısından geçen kaynak yok."
              : `${intent.sourceNodes} kaynak`}
            {capped && ` (${drawnSatellites} tanesi çiziliyor)`}
          </p>
        ) : (
          <p className="muted core-count" data-source-nodes="0" data-source-nodes-known="no">
            Kaynak sayısı bildirilmedi.
            {/* The fixed motif is drawn in its place, and said to be one. */}
            {intent.constellationNodes > 0 && ` ${CONSTELLATION_MOTIF_NOTE}`}
          </p>
        ))}

      {intent.kind === "memory" && !intent.convergenceKnown && (
        <p className="muted core-count" data-convergence-known="no">
          Ne kadar ilerlediği bildirilmedi.
        </p>
      )}

      {intent.satelliteComplete && (
        <p className="core-count" data-satellite-complete="yes">
          Aday onay bekliyor — canlıya alınmadı.
          {intent.composite !== null && ` Bileşik skor ${intent.composite.toFixed(2)}.`}
        </p>
      )}

      {intent.capabilityNodes > 0 && !compact && (
        <p
          className="muted core-count"
          data-capability-nodes={intent.capabilityNodes}
          data-capability-counted={intent.capabilityNodesCounted ? "yes" : "no"}
        >
          {capabilityNodesLine(intent.capabilityNodes, intent.capabilityNodesCounted)}
        </p>
      )}

      {intent.constructionLayer > 0 && (
        <p className="core-count" data-construction-layer={intent.constructionLayer}>
          Laboratuvar katmanı {intent.constructionLayer}/4
          {intent.composite !== null && ` · bileşik ${intent.composite.toFixed(2)}`}
        </p>
      )}
    </div>
  );
}
