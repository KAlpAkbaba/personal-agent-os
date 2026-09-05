/**
 * The Cognitive Cockpit's panels.
 *
 * Each one answers a question the Core cannot: the Core says what is happening
 * now, these say what exists. Two habits run through all of them.
 *
 * First, **empty means empty and failed means failed** — every panel supplies
 * its own honest empty sentence, and `Panel` refuses to show an empty list for
 * a request that did not succeed.
 *
 * Second, **nothing is summarised into a claim the data does not make.** The
 * world model's four truth kinds stay labelled rather than merged; a lab
 * candidate's status is printed rather than translated into "ready to ship";
 * SHADOW_READY says in as many words that it is not live.
 */

import {
  type CockpitData,
} from "../../lib/cockpit/useCockpitData";
import {
  GOAL_STATUS_LABEL,
  type Goal,
  type Health,
  type LedgerEvent,
  type Lesson,
  type MemoryAuditEvent,
  type Opportunity,
  type PendingBriefing,
  type ResearchTask,
  type ShadowReady,
  TRUTH_KIND_LABEL,
  type World,
  isHealthy,
} from "../../lib/cockpit/api";
import { formatAge, stateLabel, subsystemLabel } from "../../lib/uistate/labels";
import type { CoreTruth } from "../../lib/uistate/truth";
import { liveEventFor, recentDescending } from "../../lib/uistate/truth";
import Panel from "./Panel";

function when(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const at = Date.parse(iso);
  return Number.isNaN(at) ? "" : formatAge(Math.max(0, now - at));
}

// ------------------------------------------------------------------ panels

export function ResearchPanel({ state, now }: { state: CockpitData["research"]; now: number }) {
  return (
    <Panel<ResearchTask[]>
      id="research"
      title="Araştırma"
      state={state}
      empty="Kayıtlı araştırma işi yok."
      isEmpty={(tasks) => tasks.length === 0}
      badge={(tasks) => `${tasks.length}`}
      attention={(tasks) => tasks.some((t) => t.stage === "waiting_for_owner_verification")}
    >
      {(tasks) => (
        <ul>
          {tasks.slice(0, 6).map((task) => (
            <li key={task.task_id} data-research-task={task.task_id} data-stage={task.stage}>
              <div className="event-row">
                <span>{task.topic}</span>
                <span className="event-when">{when(task.ready_at ?? task.created_at, now)}</span>
              </div>
              <span className="muted">
                {task.status} · {task.stage}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function GoalsPanel({ state, now }: { state: CockpitData["goals"]; now: number }) {
  return (
    <Panel<Goal[]>
      id="goals"
      title="Hedefler"
      state={state}
      // The spec's rule, stated plainly: no goals means say so, not draw orbits.
      empty="Hedef yok."
      isEmpty={(goals) => goals.length === 0}
      badge={(goals) => `${goals.filter((g) => g.status === "active").length} etkin / ${goals.length}`}
      attention={(goals) => goals.some((g) => g.status === "waiting_owner")}
    >
      {(goals) => (
        <ul>
          {goals.slice(0, 8).map((goal) => (
            <li key={goal.goal_id} data-goal={goal.goal_id} data-goal-status={goal.status}>
              <div className="event-row">
                <span>{goal.title}</span>
                <span className="event-when">{goal.horizon}</span>
              </div>
              <span className="muted">
                {GOAL_STATUS_LABEL[goal.status] ?? goal.status}
                {goal.requires_owner_approval && !goal.approved_at && " · onay bekliyor"}
                {goal.deadline && ` · ${when(goal.deadline, now)}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function MemoryPanel({ state, now }: { state: CockpitData["memory"]; now: number }) {
  return (
    <Panel<MemoryAuditEvent[]>
      id="memory"
      title="Hafıza"
      state={state}
      empty="Kayıtlı hafıza işlemi yok."
      isEmpty={(events) => events.length === 0}
      badge={(events) => `${events.length}`}
    >
      {(events) => (
        <ul>
          {events.slice(0, 6).map((event) => (
            <li key={event.id} data-memory-audit={event.id}>
              <div className="event-row">
                <span>
                  <code>{event.action}</code>
                  {event.memory_class && ` · ${event.memory_class}`}
                </span>
                <span className="event-when">{when(event.created_at, now)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function LessonsPanel({ state }: { state: CockpitData["lessons"] }) {
  return (
    <Panel<Lesson[]>
      id="lessons"
      title="Dersler"
      state={state}
      empty="Derlenmiş ders yok."
      isEmpty={(lessons) => lessons.length === 0}
      badge={(lessons) => `${lessons.length}`}
    >
      {(lessons) => (
        <ul>
          {lessons.slice(0, 5).map((lesson) => (
            <li key={lesson.lesson_id} data-lesson={lesson.lesson_id}>
              <div className="event-row">
                <span>{lesson.title}</span>
                <span className="event-when">{lesson.score?.toFixed?.(2) ?? ""}</span>
              </div>
              <span className="muted">
                {lesson.status} · güven {lesson.confidence?.toFixed?.(2) ?? "?"} · tekrar{" "}
                {lesson.recurrence ?? "?"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function WorldPanel({ state }: { state: CockpitData["world"] }) {
  return (
    <Panel<World>
      id="world"
      title="Dünya modeli"
      state={state}
      empty="Kayıtlı olgu yok."
      isEmpty={(world) => world.facts.length === 0 && world.uncertainties.length === 0}
      badge={(world) => `${world.facts.length} olgu · ${world.uncertainties.length} belirsiz`}
    >
      {(world) => (
        <>
          <ul>
            {world.facts.slice(0, 6).map((fact) => (
              <li
                key={`${fact.key}-${fact.truth_kind}`}
                data-fact={fact.key}
                data-truth-kind={fact.truth_kind}
                data-stale={fact.stale ? "yes" : "no"}
              >
                <div className="event-row">
                  <span>{fact.key}</span>
                  {/* The four truth kinds are never averaged (ADR-0053 §2). */}
                  <span className="event-when">
                    {TRUTH_KIND_LABEL[fact.truth_kind] ?? fact.truth_kind}
                    {fact.stale && " · bayat"}
                  </span>
                </div>
              </li>
            ))}
          </ul>
          {world.uncertainties.length > 0 && (
            <p className="muted" style={{ marginTop: "0.5rem" }} data-uncertainties>
              {world.uncertainties.length} konuda bilinmeyen var.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

export function EvolutionPanel({ state }: { state: CockpitData["opportunities"] }) {
  return (
    <Panel<Opportunity[]>
      id="evolution"
      title="Evrim"
      state={state}
      empty="Aday yok."
      isEmpty={(items) => items.length === 0}
      badge={(items) => `${items.length}`}
    >
      {(items) => (
        <ul>
          {items.slice(0, 8).map((item) => (
            <li
              key={item.opportunity_id}
              data-opportunity={item.opportunity_id}
              data-opportunity-status={item.status}
            >
              <div className="event-row">
                <span>{item.title}</span>
                <span className="event-when">
                  {item.scores?.composite != null ? item.scores.composite.toFixed(2) : ""}
                </span>
              </div>
              <span className="muted">{item.status}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function ShadowReadyPanel({ state }: { state: CockpitData["shadowReady"] }) {
  return (
    <Panel<ShadowReady>
      id="shadow-ready"
      title="Gölge hazır"
      state={state}
      empty="Onay bekleyen aday yok."
      isEmpty={(value) => value.awaiting_approval.length === 0}
      badge={(value) => `${value.awaiting_approval.length}`}
      attention={(value) => value.awaiting_approval.length > 0}
    >
      {(value) => (
        <>
          <ul>
            {value.awaiting_approval.map((item) => (
              <li key={item.opportunity_id} data-shadow-ready={item.opportunity_id}>
                <div className="event-row">
                  <span>{item.title}</span>
                  <span className="event-when">
                    {item.scores?.composite != null ? item.scores.composite.toFixed(2) : ""}
                  </span>
                </div>
              </li>
            ))}
          </ul>
          {/* Said in words, every time: passing the gates is not being live. */}
          <p className="muted" style={{ marginTop: "0.5rem" }}>
            Kapılarını geçti, canlıya alınmadı. Onay ayrı bir sahip işlemidir.
          </p>
        </>
      )}
    </Panel>
  );
}

export function OwnerActionsPanel({
  briefings,
  goals,
  shadowReady,
  now,
}: {
  briefings: CockpitData["briefings"];
  goals: CockpitData["goals"];
  shadowReady: CockpitData["shadowReady"];
  now: number;
}) {
  const waitingGoals = goals.kind === "ok" ? goals.value.filter((g) => g.status === "waiting_owner") : [];
  const readyCount = shadowReady.kind === "ok" ? shadowReady.value.awaiting_approval.length : 0;

  return (
    <Panel<PendingBriefing[]>
      id="owner-actions"
      title="Sahip işlemleri"
      state={briefings}
      empty={
        waitingGoals.length === 0 && readyCount === 0
          ? "Bekleyen sahip işlemi yok."
          : "Bekleyen brifing yok."
      }
      isEmpty={(items) => items.length === 0 && waitingGoals.length === 0 && readyCount === 0}
      badge={(items) => `${items.length + waitingGoals.length + readyCount}`}
      attention={(items) => items.length + waitingGoals.length + readyCount > 0}
    >
      {(items) => (
        <ul>
          {items.map((b) => (
            <li key={b.briefing_id} data-briefing={b.briefing_id}>
              <div className="event-row">
                <span>{b.speech}</span>
                <span className="event-when">{when(b.created_at, now)}</span>
              </div>
            </li>
          ))}
          {waitingGoals.map((goal) => (
            <li key={goal.goal_id} data-owner-goal={goal.goal_id}>
              <div className="event-row">
                <span>{goal.title}</span>
                <span className="event-when">hedef</span>
              </div>
            </li>
          ))}
          {readyCount > 0 && (
            <li data-owner-shadow-ready={readyCount}>
              <div className="event-row">
                <span>{readyCount} aday onay bekliyor</span>
                <span className="event-when">evrim</span>
              </div>
            </li>
          )}
        </ul>
      )}
    </Panel>
  );
}

export function LedgerPanel({ state, now }: { state: CockpitData["ledger"]; now: number }) {
  return (
    <Panel<LedgerEvent[]>
      id="ledger"
      title="Defter"
      state={state}
      empty="Kayıtlı olay yok."
      isEmpty={(events) => events.length === 0}
      badge={(events) => `${events.length}`}
      attention={(events) => events.some((e) => e.severity === "critical")}
    >
      {(events) => (
        <ul>
          {events.slice(0, 10).map((event) => (
            <li
              key={event.event_id}
              data-ledger-event={event.event_id}
              data-status={event.status ?? ""}
              data-severity={event.severity ?? ""}
            >
              <div className="event-row">
                <span>{event.factual_summary ?? event.event_type}</span>
                <span className="event-when">{when(event.occurred_at, now)}</span>
              </div>
              <span className="muted">
                {event.subsystem ? subsystemLabel(event.subsystem) : ""}
                {event.status && ` · ${event.status}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function HealthPanel({ state }: { state: CockpitData["health"] }) {
  return (
    <Panel<Health>
      id="health"
      title="Sistem sağlığı"
      state={state}
      empty="Sağlık kontrolü bildirilmedi."
      isEmpty={(health) => Object.keys(health.checks).length === 0}
      badge={(health) => health.status}
      attention={(health) => health.status !== "ok"}
    >
      {(health) => (
        <ul>
          {Object.entries(health.checks)
            .filter(([, check]) => !isHealthy(check.status))
            .slice(0, 8)
            .map(([name, check]) => (
              <li key={name} data-health-check={name} data-health-status={check.status}>
                <div className="event-row">
                  <span>{name}</span>
                  <span className="event-when">{check.status}</span>
                </div>
              </li>
            ))}
          {Object.entries(health.checks).every(([, c]) => isHealthy(c.status)) && (
            <li data-health-all-ok="yes">
              <span className="muted">
                Tüm kontroller iyi ({Object.keys(health.checks).length}).
              </span>
            </li>
          )}
        </ul>
      )}
    </Panel>
  );
}

/**
 * Running tools, taken from the UI-state bus rather than from a REST list.
 *
 * "A tool is running" is a *now* fact, so the only honest source is a live
 * `agent.tool_running` event. When none is live the panel says so, rather than
 * listing capabilities that merely exist.
 */
export function RunningToolsPanel({ truth, now }: { truth: CoreTruth; now: number }) {
  const tool = liveEventFor(truth, "agent.tool_running", now);
  const researching = liveEventFor(truth, "agent.researching", now);
  const live = [tool, researching].filter((e): e is NonNullable<typeof e> => e !== null);

  return (
    <section className="panel" data-panel="running-tools" data-panel-empty={live.length ? "no" : "yes"}>
      <h3 className="panel-title">
        <span>Çalışan araçlar</span>
        <span className="panel-count">{live.length}</span>
      </h3>
      {live.length === 0 ? (
        <p className="panel-empty">Çalışan araç bildirilmedi.</p>
      ) : (
        <ul>
          {live.map((event) => (
            <li key={event.event_id} data-running-state={event.state}>
              <div className="event-row">
                <span>{event.label ?? stateLabel(event.state)}</span>
                <span className="event-when">{formatAge(Math.max(0, now - Date.parse(event.at)))}</span>
              </div>
              <span className="muted">
                {subsystemLabel(event.subsystem)}
                {event.status && ` · ${event.status}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The UI-state tail itself: the last things any subsystem reported. */
export function StateStreamPanel({ truth, now }: { truth: CoreTruth; now: number }) {
  const events = recentDescending(truth, 12);
  return (
    <section className="panel" data-panel="state-stream" data-panel-empty={events.length ? "no" : "yes"}>
      <h3 className="panel-title">
        <span>Durum akışı</span>
        <span className="panel-count">{events.length}</span>
      </h3>
      {events.length === 0 ? (
        <p className="panel-empty">Henüz bir durum yayınlanmadı.</p>
      ) : (
        <ul>
          {events.map((event) => (
            <li key={event.event_id} data-stream-state={event.state}>
              <div className="event-row">
                <span>{stateLabel(event.state)}</span>
                <span className="event-when">
                  {formatAge(Math.max(0, now - Date.parse(event.at)))}
                </span>
              </div>
              <span className="muted">
                {subsystemLabel(event.subsystem)}
                {event.label && ` · ${event.label}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
