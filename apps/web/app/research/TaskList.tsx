"use client";

import { type Loaded, isOk } from "../lib/cockpit/api";
import { type FocusState, identityLine } from "../lib/research/focus";
import { type ResearchTaskSummary, stageLabel } from "../lib/research/model";

/**
 * The owner's previous research runs — told apart by what they are, not by
 * what they are called.
 *
 * Four runs can share the title "OpenAI son gelişmeler". The title is
 * presentation metadata; the identity is the second line (when it finished,
 * which mode, how many sources, what state) plus the id, which travels in
 * `data-research-task` and never onto the screen.
 *
 * The focus chip is placed by id — `current.research_job_id` — and by nothing
 * else. Matching on the topic would decorate whichever duplicate sorted first,
 * which is the exact defect this list exists to fix.
 */

export type TaskListProps = {
  tasks: ResearchTaskSummary[];
  focus: Loaded<FocusState>;
  /** Reference instant for "Bugün"/"Dün". */
  now: number;
  /** Opening a report is also selecting it as the conversational focus. */
  onOpen: (taskId: string) => void;
};

export const FOCUS_CHIP = "Konuşma odağı";
export const PREVIOUS_CHIP = "önceki odak";

export default function TaskList({ tasks, focus, now, onOpen }: TaskListProps) {
  const currentId = isOk(focus) ? (focus.value.current?.research_job_id ?? null) : null;
  const previousId = isOk(focus) ? (focus.value.previous?.research_job_id ?? null) : null;

  /**
   * The focus state is the authority. Only when it could not be read at all
   * do we fall back to the flag the list route put on the row itself — still
   * an id-addressed statement by the server, never a title match.
   */
  const isCurrent = (task: ResearchTaskSummary): boolean =>
    isOk(focus) ? task.task_id === currentId : task.is_focus === true;

  if (tasks.length === 0) return <p className="muted">Henüz araştırma yok.</p>;

  return (
    <>
      {tasks.map((t) => {
        const current = isCurrent(t);
        const previous = !current && previousId !== null && t.task_id === previousId;
        const identity = identityLine(t, now);
        return (
          <div
            className="panel task"
            key={t.task_id}
            data-task-id={t.task_id}
            data-research-task={t.task_id}
            data-focus={current ? "current" : previous ? "previous" : undefined}
            aria-current={current ? "true" : undefined}
            style={{ padding: "0.75rem 1.25rem", cursor: "pointer" }}
            role="button"
            tabIndex={0}
            onClick={() => onOpen(t.task_id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpen(t.task_id);
              }
            }}
          >
            <div className="status-row" style={{ borderBottom: "none", padding: 0 }}>
              <span style={{ lineHeight: 1.4 }}>
                {t.topic}
                {current && (
                  <>
                    {" "}
                    <span className="badge focus" data-focus-chip>
                      {FOCUS_CHIP}
                    </span>
                  </>
                )}
                {previous && (
                  <>
                    {" "}
                    <span className="muted" data-focus-previous-chip>
                      ({PREVIOUS_CHIP})
                    </span>
                  </>
                )}
              </span>
              <span
                className={`badge ${t.stage === "ready" ? "ok" : t.stage === "failed" ? "fail" : "unknown"}`}
              >
                {stageLabel(t.stage)}
              </span>
            </div>
            {identity && (
              <div className="muted" data-research-identity style={{ marginTop: "0.25rem" }}>
                {identity}
              </div>
            )}
            {(t.device || t.artifact_id) && (
              <div className="muted" style={{ marginTop: "0.15rem" }}>
                {t.device?.name}
                {t.device && t.artifact_id && " · "}
                {t.artifact_id && "artifact"}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
