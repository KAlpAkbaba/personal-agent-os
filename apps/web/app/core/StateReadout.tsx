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
  appFactsLine,
  artifactFactsLine,
  calendarFactsLine,
  capabilityNodesLine,
  documentFactsLine,
  formatAge,
  formatProgress,
  genesisFactsLine,
  kindDetail,
  mailFactsLine,
  operatorErrorLine,
  operatorFactsLine,
  stateLabel,
  subsystemLabel,
} from "../lib/uistate/labels";
import { appIsServing } from "../lib/uistate/apps";
import { isOperatorState } from "../lib/uistate/contract";
import { documentPartPhrase } from "../lib/uistate/documents";
import { genesisPosture } from "../lib/uistate/genesis";
import { operatorPosition } from "../lib/uistate/operator";
import type { VisualIntent } from "../lib/uistate/visual";
import { isLive } from "../lib/uistate/visual";

/**
 * True for the three operator states, live or on their last-known shape.
 * Membership, not prefix: an `operator.*` word this build cannot read is drawn
 * as `unknown_state` and gets no facts line it could not have read either.
 */
function isOperatorIntent(intent: VisualIntent): boolean {
  return intent.state !== null && isOperatorState(intent.state);
}

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
      // M24: which of the three postures (and the failure) the genesis body
      // takes, from the published state alone — on the root so a harness can
      // tell "onay bekliyor" from "bağdaştırıcı yazılıyor" in the compact
      // form too, without reading the geometry. Absent for every other kind.
      data-genesis-posture={intent.genesis ? genesisPosture(intent.genesis.state) : undefined}
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

      {/*
        M19: the operator's published facts — the step, the capability sent, the
        window the companion OBSERVED — and, on failure, the error class. Each
        is the token the publisher sent or the statement that none came; the
        error class is short enough to belong in the compact caption too.
      */}
      {isOperatorIntent(intent) && !compact && (
        <p
          className="muted core-count"
          data-operator-facts
          data-operator-step={intent.operatorStep ?? ""}
          data-operator-position={operatorPosition({
            stepIndex: intent.operatorStepIndex,
            stepCount: intent.operatorStepCount,
          }) ?? ""}
          data-operator-capability={intent.operatorCapability ?? ""}
          data-operator-window={intent.operatorWindow ?? ""}
        >
          {operatorFactsLine({
            step: intent.operatorStep,
            stepIndex: intent.operatorStepIndex,
            stepCount: intent.operatorStepCount,
            capability: intent.operatorCapability,
            windowTitle: intent.operatorWindow,
            errorClass: intent.operatorErrorClass,
          })}
        </p>
      )}
      {intent.state === "operator.failed" && (
        <p className="core-count" data-operator-error-class={intent.operatorErrorClass ?? ""}>
          {operatorErrorLine(intent.operatorErrorClass)}
        </p>
      )}

      {/*
        M20: the document's published facts — the file the Core named, the
        place inside it in the owner's words, the step — each the token the
        publisher sent or the statement that none came. Present on the live
        reading posture and on its last-known shape alike: what WAS being read
        is still a fact. The compact caption already carries the same file and
        place as `label`, so the long line is the full form's alone.
      */}
      {intent.document && !compact && (
        <p
          className="muted core-count"
          data-document-facts
          data-document-file={intent.document.file ?? ""}
          data-document-path={intent.document.path ?? ""}
          data-document-part={intent.document.part ?? ""}
          data-document-place={documentPartPhrase(intent.document.part, intent.document.kind) ?? ""}
          data-document-step={intent.document.step ?? ""}
        >
          {documentFactsLine(intent.document)}
        </p>
      )}

      {/*
        M21: the mail activity's published facts — the folder, the subject,
        the draft's step — and the calendar's — the range, the event, the
        proposal's step and its counted conflicts. Each the token the
        publisher sent or the statement that none came; present on the live
        posture and on its last-known shape alike. The compact caption
        already carries the same sentence as `label`, so the long line is the
        full form's alone. No bar under either: nothing publishes progress
        for a read, and a draft waiting on the owner has no progress to draw.
      */}
      {intent.mail && !compact && (
        <p
          className="muted core-count"
          data-mail-facts
          data-mail-folder={intent.mail.folder ?? ""}
          data-mail-subject={intent.mail.subject ?? ""}
          data-mail-draft-state={intent.mail.draftStateToken ?? ""}
        >
          {mailFactsLine(intent.mail)}
        </p>
      )}
      {intent.calendar && !compact && (
        <p
          className="muted core-count"
          data-calendar-facts
          data-calendar-range={intent.calendar.range ?? ""}
          data-calendar-event={intent.calendar.event ?? ""}
          data-calendar-proposal-state={intent.calendar.proposalStateToken ?? ""}
          data-calendar-conflicts={intent.calendar.conflicts ?? ""}
        >
          {calendarFactsLine(intent.calendar)}
        </p>
      )}

      {/*
        M22: the factory's published facts — the title, the format, the
        verdict, and on `invalid` the ref that failed — each the token the
        publisher sent or the statement that none came; present on the live
        making posture and on its last-known shape alike. The compact caption
        already carries the same sentence as `label`, so the long line is the
        full form's alone. No bar: a render of unknown length gets none.
      */}
      {intent.artifact && !compact && (
        <p
          className="muted core-count"
          data-artifact-facts
          data-artifact-title={intent.artifact.title ?? ""}
          data-artifact-format={intent.artifact.format ?? ""}
          data-artifact-verdict={intent.artifact.verdictToken ?? ""}
          data-artifact-failing-ref={intent.artifact.failingRef ?? ""}
        >
          {artifactFactsLine(intent.artifact)}
        </p>
      )}

      {/*
        M23: the App Factory's published facts — the project, the state, the
        port, the counts its own tests gave — each the token the publisher
        sent or the statement that none came; present on the live building
        posture and on its last-known shape alike. `data-app-serving` marks
        the one combination the Core draws as a server answering — `running`
        on a named port — so a harness can tell the running posture from the
        building one without reading the geometry. The compact caption
        already carries the same sentence as `label`, so the long line is the
        full form's alone. No bar: a scaffold of unknown length gets none, and
        a running app has no progress to draw. No link here either — the link
        is the Cockpit's, built from the row on the list route.
      */}
      {intent.app && !compact && (
        <p
          className="muted core-count"
          data-app-facts
          data-app-project={intent.app.project ?? ""}
          data-app-state={intent.app.stateToken ?? ""}
          data-app-port={intent.app.port ?? ""}
          data-app-tests-passed={intent.app.tests?.passed ?? ""}
          data-app-tests-failed={intent.app.tests?.failed ?? ""}
          data-app-serving={appIsServing(intent.app) ? "yes" : "no"}
        >
          {appFactsLine(intent.app)}
        </p>
      )}

      {/*
        M24: Capability Genesis's published facts — the capability, the
        run's state, whether approval is required, and on `failed` the
        error class — each the token the publisher sent or the statement
        that none came; present on the live posture and on its last-known
        shape alike. The compact caption already carries the same sentence
        as `label`, so the long line is the full form's alone. No bar: a
        run of unknown length gets none. No control here either — "Onayla"
        and "Vazgeç" are the Cockpit's, built from the row on the list route.
      */}
      {intent.genesis && !compact && (
        <p
          className="muted core-count"
          data-genesis-facts
          data-genesis-capability={intent.genesis.capability ?? ""}
          data-genesis-state={intent.genesis.stateToken ?? ""}
          data-genesis-approval-required={
            intent.genesis.approvalRequired === null ? "" : intent.genesis.approvalRequired ? "yes" : "no"
          }
          data-genesis-error-class={intent.genesis.errorClass ?? ""}
        >
          {genesisFactsLine(intent.genesis)}
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
