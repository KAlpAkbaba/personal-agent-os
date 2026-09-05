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
  KIND_DETAIL,
  KIND_LABEL,
  SEVERITY_LABEL,
  formatAge,
  formatProgress,
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

      {!compact && <p className="core-detail">{KIND_DETAIL[intent.kind]}</p>}

      {/* A publisher-supplied short label: a topic, a goal title. Never prose. */}
      {intent.label && (
        <p className="core-label" data-label>
          {intent.label}
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
        live && (
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

      {intent.constructionLayer > 0 && (
        <p className="core-count" data-construction-layer={intent.constructionLayer}>
          Laboratuvar katmanı {intent.constructionLayer}/4
          {intent.composite !== null && ` · bileşik ${intent.composite.toFixed(2)}`}
        </p>
      )}
    </div>
  );
}
