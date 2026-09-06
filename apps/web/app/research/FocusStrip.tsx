"use client";

import type { Loaded } from "../lib/cockpit/api";
import {
  FOCUS_UNSUPPORTED,
  type FocusState,
  NO_TOPIC,
  focusSourceLabel,
  focusSummary,
  identityLine,
} from "../lib/research/focus";

/**
 * What the owner is talking about, stated above the list.
 *
 * Four different sentences for four different facts, as everywhere else in
 * this app: the focus, "there is no focus yet", "this Cloud Core has no focus"
 * (404) and "could not find out". The 404 is not drawn as an error — nothing
 * broke, the server simply predates the feature — and it is said once, here,
 * rather than on every row.
 *
 * When the system asked which of several same-titled reports the owner meant,
 * the candidates are rows: clicking one IS the answer, and it goes back as a
 * focus selection by id.
 */

export type FocusStripProps = {
  state: Loaded<FocusState>;
  /** Reference instant for "Bugün"/"Dün". */
  now: number;
  /** Inline sentence from the last selection (e.g. the 409). */
  notice?: string | null;
  /** Selecting a clarification candidate. Receives the research job id. */
  onSelect: (taskId: string) => void;
};

export default function FocusStrip({ state, now, notice, onSelect }: FocusStripProps) {
  const body = (() => {
    if (state.kind === "loading") return null;
    if (state.kind === "absent") {
      return (
        <p className="muted" data-focus-strip="absent" style={{ margin: 0 }}>
          {FOCUS_UNSUPPORTED}
        </p>
      );
    }
    if (state.kind === "failed") {
      return (
        <p className="muted" data-focus-strip="failed" style={{ margin: 0 }}>
          Konuşma odağı alınamadı: {state.error}
        </p>
      );
    }

    const { current, previous, pending_clarification: pending } = state.value;
    return (
      <>
        {current ? (
          <p data-focus-strip="current" data-focus-id={current.research_job_id} style={{ margin: 0 }}>
            <strong>Konuşma odağı:</strong> {focusSummary(current, now)}
          </p>
        ) : (
          <p className="muted" data-focus-strip="none" style={{ margin: 0 }}>
            Konuşma odağı yok.
          </p>
        )}

        {current && focusSourceLabel(current.source_of_focus) && (
          <p className="muted" data-focus-source={current.source_of_focus} style={{ margin: "0.15rem 0 0" }}>
            Nasıl seçildi: {focusSourceLabel(current.source_of_focus)}
          </p>
        )}

        {previous && (
          <p className="muted" data-focus-previous={previous.research_job_id} style={{ margin: "0.15rem 0 0" }}>
            Önceki odak: {focusSummary(previous, now)}
          </p>
        )}

        {pending && (
          <div data-focus-clarification style={{ marginTop: "0.5rem" }}>
            <p style={{ margin: 0 }}>{pending.question}</p>
            <ul className="focus-candidates" style={{ listStyle: "none", margin: "0.35rem 0 0", padding: 0 }}>
              {pending.candidates.map((candidate) => (
                <li
                  key={candidate.research_job_id}
                  data-focus-candidate={candidate.research_job_id}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelect(candidate.research_job_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(candidate.research_job_id);
                    }
                  }}
                  style={{ cursor: "pointer", padding: "0.3rem 0" }}
                >
                  <span>{candidate.topic ?? NO_TOPIC}</span>
                  <br />
                  <span className="muted">{identityLine(candidate, now)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </>
    );
  })();

  if (!body && !notice) return null;

  return (
    <div className="panel focus-strip" data-focus-state={state.kind} style={{ padding: "0.75rem 1.25rem" }}>
      {body}
      {notice && (
        <p data-focus-notice style={{ margin: "0.4rem 0 0", color: "var(--warn)" }}>
          {notice}
        </p>
      )}
    </div>
  );
}
