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
  ALARM_STATE_LABEL,
  type AmbientPolicy,
  type EvolutionSupervisorStatus,
  GOAL_STATUS_LABEL,
  type Goal,
  type Health,
  type LedgerEvent,
  type Lesson,
  type MemoryAuditEvent,
  type Opportunity,
  type PendingBriefing,
  PROMOTION_CLASS_LABEL,
  type ResearchTask,
  type ShadowReady,
  TRUTH_KIND_LABEL,
  VOICE_QUALIFICATION_LABEL,
  type VoiceQualification,
  type WakeAlarm,
  type World,
  isHealthy,
} from "../../lib/cockpit/api";
import {
  FOCUS_UNSUPPORTED,
  focusSourceLabel,
  focusSummary,
  identityLine,
} from "../../lib/research/focus";
import {
  OPERATOR_EMPTY,
  OPERATOR_LABEL,
  formatAge,
  operatorErrorLine,
  operatorFactsLine,
  stateLabel,
  subsystemLabel,
} from "../../lib/uistate/labels";
import { operatorPosition, operatorView } from "../../lib/uistate/operator";
import type { CoreTruth } from "../../lib/uistate/truth";
import { liveEventFor, operatorClaim, recentDescending } from "../../lib/uistate/truth";
import Panel from "./Panel";

function when(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const at = Date.parse(iso);
  return Number.isNaN(at) ? "" : formatAge(Math.max(0, now - at));
}

// ------------------------------------------------------------------ panels

/**
 * Research runs, told apart by what they are rather than by what they are
 * called (M18.2).
 *
 * Several runs share the title "OpenAI son gelişmeler"; the second line is the
 * run's identity — when it finished, which mode, how many sources, what state
 * — and the id lives in `data-research-task`, never on the screen. The focus
 * chip is placed by `current.research_job_id`, never by topic.
 *
 * The one thing the cockpit writes: which report the owner is talking about.
 * That is the owner's own act on the owner's own surface, not the renderer
 * approving work or changing policy — the panel still decides nothing.
 */
export function ResearchPanel({
  state,
  focus,
  now,
  onSelect,
  notice,
}: {
  state: CockpitData["research"];
  focus?: CockpitData["researchFocus"];
  now: number;
  onSelect?: (taskId: string) => void;
  notice?: string | null;
}) {
  // The focus route's answer is the authority whenever it answered at all;
  // the row's own `is_focus` flag is the fallback for a Core that could not
  // be asked. Neither path ever looks at the topic.
  const known = focus?.kind === "ok";
  const currentId = focus?.kind === "ok" ? (focus.value.current?.research_job_id ?? null) : null;
  const previousId = focus?.kind === "ok" ? (focus.value.previous?.research_job_id ?? null) : null;

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
        <>
          {focus?.kind === "absent" && (
            <p className="panel-unknown" data-focus-strip="absent">
              {FOCUS_UNSUPPORTED}
            </p>
          )}
          {focus?.kind === "ok" && focus.value.current && (
            <p
              className="muted"
              data-focus-strip="current"
              data-focus-id={focus.value.current.research_job_id}
            >
              {[
                `Konuşma odağı: ${focusSummary(focus.value.current, now)}`,
                focusSourceLabel(focus.value.current.source_of_focus),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
          {notice && (
            <p className="panel-unknown" data-focus-notice>
              {notice}
            </p>
          )}
          <ul>
            {tasks.slice(0, 6).map((task) => {
              const current = known ? task.task_id === currentId : task.is_focus === true;
              const previous = !current && previousId !== null && task.task_id === previousId;
              const identity = identityLine(task, now);
              return (
                <li
                  key={task.task_id}
                  data-research-task={task.task_id}
                  data-stage={task.stage}
                  data-focus={current ? "current" : previous ? "previous" : undefined}
                  aria-current={current ? "true" : undefined}
                  role={onSelect ? "button" : undefined}
                  tabIndex={onSelect ? 0 : undefined}
                  onClick={onSelect ? () => onSelect(task.task_id) : undefined}
                  onKeyDown={
                    onSelect
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onSelect(task.task_id);
                          }
                        }
                      : undefined
                  }
                  style={onSelect ? { cursor: "pointer" } : undefined}
                >
                  <div className="event-row">
                    <span>
                      {task.topic}
                      {current && (
                        <>
                          {" "}
                          <span className="focus-chip" data-focus-chip>
                            Konuşma odağı
                          </span>
                        </>
                      )}
                    </span>
                    <span className="event-when">{when(task.completed_at ?? task.ready_at ?? task.created_at, now)}</span>
                  </div>
                  {identity && (
                    <span className="muted" data-research-identity>
                      {identity}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
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
              <span className="muted" data-opportunity-priority={item.priority ?? "unclassified"}>
                {item.status}
                {" · "}
                {item.priority ?? "öncelik sınıflanmadı"}
                {" · "}
                {item.promotion_class
                  ? (PROMOTION_CLASS_LABEL[item.promotion_class] ?? item.promotion_class)
                  : "terfi sınıfı belirlenmedi"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/**
 * ADR-0081: the Evolution Supervisor. Paused or scanning, what the last scan did, what is
 * being built, what waits for the owner, what failed on the way to LIVE, the last incident
 * fixed and the version that is running - each a sentence about rows, none a promise.
 */
export function EvolutionSupervisorPanel({
  state,
  now,
}: {
  state: CockpitData["evolutionSupervisor"];
  now: number;
}) {
  return (
    <Panel<EvolutionSupervisorStatus>
      id="evolution-supervisor"
      title="Evrim gözetmeni"
      state={state}
      empty="Gözetmen henüz taramadı."
      isEmpty={(s) => s.last_scan === null && !s.paused}
      badge={(s) => (s.paused ? "duraklatıldı" : s.enabled ? "tarıyor" : "kapalı")}
      attention={(s) => s.paused || s.release_failures.length > 0}
    >
      {(s) => (
        <ul>
          <li data-supervisor-paused={String(s.paused)}>
            <div className="event-row">
              <span>{s.paused ? "Kendi kendini geliştirme duraklatıldı" : "Kendi kendini geliştirme açık"}</span>
              <span className="event-when">{when(s.last_scan_at, now)}</span>
            </div>
            <span className="muted">
              {s.last_scan
                ? `son tarama: ${s.last_scan.signals} sinyal, ${s.last_scan.opened.length} yeni fırsat, ${s.last_scan.already_tracked} zaten izleniyor`
                : "henüz tarama yok"}
              {" · "}
              {Object.entries(s.open_by_priority)
                .map(([p, n]) => `${p} ${n}`)
                .join(" · ") || "açık fırsat yok"}
            </span>
          </li>
          {s.building.map((b) => (
            <li key={b.opportunity_id} data-supervisor-building={b.opportunity_id}>
              <div className="event-row">
                <span>{b.title}</span>
                <span className="event-when">{b.status}</span>
              </div>
            </li>
          ))}
          {s.pending_candidates.map((b) => (
            <li key={b.opportunity_id} data-supervisor-pending={b.opportunity_id}>
              <div className="event-row">
                <span>{b.title}</span>
                <span className="event-when">sahip kararı bekliyor</span>
              </div>
            </li>
          ))}
          {s.release_failures.map((b) => (
            <li key={b.opportunity_id} data-supervisor-failure={b.opportunity_id}>
              <div className="event-row">
                <span>{b.title}</span>
                <span className="event-when">{b.status}</span>
              </div>
            </li>
          ))}
          <li data-supervisor-last-fix>
            <span className="muted">
              {s.last_fix
                ? `son düzeltilen olay: ${s.last_fix.component ?? "?"} · ${s.last_fix.error_class || "sağlık"}${s.last_fix.fixed_release_id ? ` · sürüm ${s.last_fix.fixed_release_id}` : ""}`
                : "kayıtlı düzeltilmiş olay yok"}
            </span>
          </li>
          <li data-supervisor-running>
            <span className="muted">
              {s.running
                ? `çalışan sürüm: ${s.running.version} (${s.running.version_source === "env" ? "dışa aktarılmış" : "kimliği dışa aktarılmamış"}) · eylem sözleşmesi ${s.running.contracts.action ?? "?"}`
                : "çalışan sürüm bildirilmedi"}
            </span>
          </li>
        </ul>
      )}
    </Panel>
  );
}

/** Turkish for the lifecycle statuses the Approval Center can show. */
const APPROVAL_STATUS_TR: Record<string, string> = {
  shadow_ready: "gölge hazır",
  owner_approval_required: "sahip onayı istendi",
};

/**
 * The risk tier, stated as the server derived it.
 *
 * `null` means the tier was never derived, and that is said in words. A panel
 * that filled in "seviye 1" for an unassessed candidate would be telling the
 * owner a release is safe on no evidence at all — the one thing the Approval
 * Center must never do.
 */
function riskLine(item: Opportunity, floor: number | null): string {
  if (item.risk_tier == null) return "risk kademesi belirlenmedi";
  const base = `risk kademesi ${item.risk_tier}`;
  if (item.requires_second_confirmation) return `${base} · ikinci onay gerekir`;
  if (floor != null && item.risk_tier >= floor) return `${base} · ikinci onay gerekir`;
  return base;
}

/**
 * The Approval Center (M18 spec §16): everything the Evolution Engine has
 * finished and the owner has not yet answered, with the one fact that decides
 * the answer — how risky it is — beside each one.
 *
 * Read-only, on purpose and permanently. The Core has no write path (ADR-0052,
 * ADR-0053 §5): approving and authorising are owner actions on the surface that
 * owns them, and a panel that could perform one would be a second authority
 * surface to keep honest. This one can only show, and says where the real
 * action lives.
 */
export function ShadowReadyPanel({ state }: { state: CockpitData["shadowReady"] }) {
  return (
    <Panel<ShadowReady>
      id="shadow-ready"
      title="Onay merkezi"
      state={state}
      empty="Sahip onayı bekleyen aday yok."
      isEmpty={(value) => value.awaiting_approval.length === 0}
      badge={(value) => `${value.awaiting_approval.length}`}
      attention={(value) => value.awaiting_approval.length > 0}
    >
      {(value) => (
        <>
          <ul>
            {value.awaiting_approval.map((item) => (
              <li
                key={item.opportunity_id}
                data-shadow-ready={item.opportunity_id}
                data-approval-status={item.status}
                data-risk-tier={item.risk_tier ?? "unknown"}
                data-second-confirmation={
                  item.requires_second_confirmation == null
                    ? "unknown"
                    : item.requires_second_confirmation
                      ? "yes"
                      : "no"
                }
              >
                <div className="event-row">
                  <span>{item.title}</span>
                  <span className="event-when">
                    {APPROVAL_STATUS_TR[item.status] ?? item.status}
                  </span>
                </div>
                <div className="event-row muted">
                  <span>{riskLine(item, value.second_confirmation_floor)}</span>
                  <span className="event-when">
                    {item.scores?.composite != null ? item.scores.composite.toFixed(2) : ""}
                  </span>
                </div>
                {item.risk_reasons && item.risk_reasons.length > 0 && (
                  <div className="muted" data-risk-reasons>
                    {item.risk_reasons.slice(0, 3).join(" · ")}
                  </div>
                )}
              </li>
            ))}
          </ul>
          {/* Said in words, every time: passing the gates is not being live, and
              this panel cannot make it so. */}
          <p className="muted" style={{ marginTop: "0.5rem" }}>
            Kapılarını geçti, canlıya alınmadı. Onay ve yetkilendirme bu ekranda değil,
            doğrulanmış sahip oturumunda yapılır.
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

/**
 * The Digital Operator (M19 spec §4): what it is doing on the owner's desktop,
 * from the same state feed the Core reads.
 *
 * Deliberately NOT a REST panel: the operator publishes its transitions to the
 * UI-state bus (`operator.running` / `verifying` / `failed`, with the step,
 * the capability and the window the companion OBSERVED), and there is no
 * separate status route to ask. Reading the bus is also what keeps the panel
 * honest by construction — it can only show a step that was published, with
 * the window title the companion actually saw, and it draws no progress
 * because none is published.
 *
 * Three outcomes, three sentences: nothing ever published ("henüz
 * çalışmadı"), a live stage with its facts, and a stage whose claim aged out
 * ("son bilinen", with the age) — the last one is not "finished", it is "we
 * stopped being told". A held `operator.failed` stays, with its error class,
 * until a newer operator event replaces it.
 */
export function DigitalOperatorPanel({ truth, now }: { truth: CoreTruth; now: number }) {
  const view = operatorView(operatorClaim(truth, now));
  const told = view.lastKnown !== null;
  const failed = view.stage === "failed";
  const badge = !told
    ? "0"
    : view.stage === "none"
      ? "son bilinen"
      : view.stage === "failed"
        ? "başarısız"
        : view.stage === "verifying"
          ? "doğruluyor"
          : "çalışıyor";

  return (
    <section
      className={`panel ${failed ? "attention" : ""}`}
      data-panel="digital-operator"
      data-panel-empty={told ? "no" : "yes"}
      data-operator-stage={view.stage}
      data-operator-last-known={view.lastKnown ?? ""}
    >
      <h3 className="panel-title">
        <span>Dijital operatör</span>
        <span className="panel-count">{badge}</span>
      </h3>
      {!told ? (
        <p className="panel-empty">{OPERATOR_EMPTY}</p>
      ) : (
        <ul>
          <li
            data-operator-step={view.step ?? ""}
            data-operator-position={operatorPosition(view) ?? ""}
            data-operator-capability={view.capability ?? ""}
            data-operator-window={view.windowTitle ?? ""}
          >
            <div className="event-row">
              <span>
                {view.stage === "none" && view.lastKnown
                  ? `Son bilinen: ${OPERATOR_LABEL[view.lastKnown]}`
                  : OPERATOR_LABEL[view.stage]}
              </span>
              <span className="event-when">{formatAge(view.ageMs)}</span>
            </div>
            {/*
              The publisher's own label, as the stream panel prints it: the
              task's goal on the task-level events, the step's name on the
              step-level ones. It is printed bare because which of the two it
              is belongs to the publisher, not to this panel.
            */}
            <span className="muted">
              {operatorFactsLine(view)}
              {view.label && ` · ${view.label}`}
            </span>
          </li>
          {view.stage === "none" && (
            <li data-operator-expired>
              <span className="muted">
                Bu adım için yeni bir bildirim gelmedi. Bittiği bildirilmedi — yalnızca haber alınamadı.
              </span>
            </li>
          )}
          {view.lastKnown === "failed" && (
            <li data-operator-error-class={view.errorClass ?? ""}>
              <span className="muted">{operatorErrorLine(view.errorClass)}</span>
            </li>
          )}
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

// ------------------------------------------------- M18.3: alarms and ambient

/**
 * The wake alarms (spec §3).
 *
 * Read-only, like every panel here: an alarm is created, snoozed or cancelled
 * by voice or by the API that owns it. This lists what exists and what state
 * each one is in, and — when the route is not on this Cloud Core yet — says
 * exactly that rather than an empty list that would read as "no alarms".
 */
export function AlarmsPanel({ state, now }: { state: CockpitData["alarms"]; now: number }) {
  return (
    <Panel<WakeAlarm[]>
      id="alarms"
      title="Alarmlar"
      state={state}
      empty="Kurulu alarm yok."
      isEmpty={(alarms) => alarms.length === 0}
      badge={(alarms) => `${alarms.length}`}
      attention={(alarms) => alarms.some((a) => a.state === "FAILED")}
    >
      {(alarms) => (
        <ul>
          {alarms.slice(0, 10).map((alarm) => (
            <li key={alarm.id} data-alarm-id={alarm.id} data-alarm-state={alarm.state}>
              <div className="event-row">
                <span>
                  {alarm.local_time ?? "saat bildirilmedi"}
                  {alarm.is_test && (
                    <span className="muted" data-alarm-test="yes">
                      {" · test"}
                    </span>
                  )}
                </span>
                <span className="event-when">{ALARM_STATE_LABEL[alarm.state] ?? alarm.state}</span>
              </div>
              <span className="muted">
                {alarm.recurrence?.weekdays?.length
                  ? `haftanın ${alarm.recurrence.weekdays.length} günü`
                  : "tek seferlik"}
                {alarm.media_title
                  ? ` · ${alarm.media_title}`
                  : alarm.media_kind
                    ? ` · ${alarm.media_kind}`
                    : " · ses kaynağı bildirilmedi"}
                {alarm.scheduled_for && ` · ${when(alarm.scheduled_for, now)}`}
              </span>
              {alarm.terminal_reason && (
                <span className="muted" data-alarm-reason>
                  {alarm.terminal_reason}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/**
 * The screens and the ambient policy (spec §3.9, §5.3).
 *
 * Two facts side by side: what the owner's policy says may happen, and what
 * the devices actually reported. **Nothing here decides physical policy** —
 * the renderer has no write path, and display power is decided by Cloud Core
 * and executed on the device.
 */
export function AmbientPanel({
  policy,
  devices,
}: {
  policy: CockpitData["ambientPolicy"];
  devices: CockpitData["devices"];
}) {
  return (
    <Panel<AmbientPolicy>
      id="ambient"
      title="Ekran / Ortam"
      state={policy}
      empty="Ortam politikası bildirilmedi."
      isEmpty={(p) => p.auto_off_enabled === null}
      badge={(p) => (p.auto_off_enabled ? "otomatik açık" : "otomatik kapalı")}
    >
      {(p) => (
        <ul>
          <li
            data-ambient-auto-off={
              p.auto_off_enabled === null ? "unknown" : String(p.auto_off_enabled)
            }
          >
            <div className="event-row">
              <span>Otomatik ekran kapatma</span>
              <span className="event-when">{p.auto_off_enabled ? "açık" : "kapalı"}</span>
            </div>
            <span className="muted">
              {p.off_when_away ? "yokken kapat" : "yokken kapatma"}
              {" · "}
              {p.off_when_asleep ? "uyurken kapat" : "uyurken kapatma"}
              {" · "}
              {p.wake_on_return ? "dönünce aç" : "dönünce açma"}
            </span>
          </li>
          <li data-ambient-thresholds>
            <span className="muted">
              {p.away_after_s === null ? "yokluk eşiği bildirilmedi" : `yokluk ${p.away_after_s} sn`}
              {" · "}
              {p.asleep_after_s === null ? "uyku eşiği bildirilmedi" : `uyku ${p.asleep_after_s} sn`}
              {" · "}
              {p.input_holdoff_s === null
                ? "giriş beklemesi bildirilmedi"
                : `giriş beklemesi ${p.input_holdoff_s} sn`}
            </span>
          </li>
          <li data-ambient-devices={devices.kind}>
            <DeviceDisplayRows devices={devices} />
          </li>
          <li data-ambient-note>
            <span className="muted">
              Ekran gücü makine durumu değildir: hiçbir yol bilgisayarı uyutmaz, kilitlemez veya
              kapatmaz.
            </span>
          </li>
        </ul>
      )}
    </Panel>
  );
}

/** The devices' own account of their screens, or the reason there is none. */
function DeviceDisplayRows({ devices }: { devices: CockpitData["devices"] }) {
  if (devices.kind === "loading") return <span className="muted">cihazlar yükleniyor…</span>;
  if (devices.kind === "failed") {
    return <span className="muted">Cihaz durumu alınamadı: {devices.error}</span>;
  }
  if (devices.kind === "absent") {
    return <span className="muted">Henüz yok. {devices.detail}</span>;
  }
  if (devices.value.length === 0) return <span className="muted">Kayıtlı cihaz yok.</span>;
  return (
    <>
      {devices.value.slice(0, 4).map((device) => (
        <span
          key={device.device_id}
          className="muted"
          data-device={device.device_id}
          data-device-display={device.display_state ?? "untold"}
        >
          {device.label ?? device.device_id}
          {": "}
          {device.statusKnown
            ? `${
                device.display_state === "on"
                  ? "ekran açık"
                  : device.display_state === "off"
                    ? "ekran kapalı"
                    : "ekran durumu bildirilmedi"
              }${
                device.input_idle_s === null ? "" : ` · ${Math.round(device.input_idle_s)} sn boşta`
              }${device.alarm_ringing ? " · alarm çalıyor" : ""}`
            : "cihaz durumu bildirilmedi (eşlik eden süreç yok veya bu sürüm göndermiyor)"}
        </span>
      ))}
    </>
  );
}

/**
 * ADR-0080: VOICE ROUTING QUALIFICATION. The Owner Utterance Suite's latest recorded
 * run, and the state the record supports. No progress bar, no "improving": five
 * states, each one a sentence about rows that exist.
 */
export function VoiceQualificationPanel({
  state,
  now,
}: {
  state: CockpitData["voiceQualification"];
  now: number;
}) {
  return (
    <Panel<VoiceQualification>
      id="voice-qualification"
      title="Ses yönlendirme sınaması"
      state={state}
      empty="Henüz hiç sınama kaydı yok."
      isEmpty={(q) => q.state === "NOT_YET_RUN"}
      badge={(q) => VOICE_QUALIFICATION_LABEL[q.state] ?? q.state}
      attention={(q) => q.state === "REGRESSION_FOUND" || q.state === "SELF_HEALING"}
    >
      {(q) => {
        const run = q.latest_synthetic_run;
        return (
          <ul>
            <li data-voice-qualification-state={q.state}>
              <div className="event-row">
                <span>Durum</span>
                <span className="event-when">
                  {VOICE_QUALIFICATION_LABEL[q.state] ?? q.state}
                </span>
              </div>
              <span className="muted">
                yönlendirme: {VOICE_QUALIFICATION_LABEL[q.routing_state] ?? q.routing_state}
                {" · "}
                {q.owner_audio_qualified
                  ? "sahibin ses testi kayıtlı"
                  : "sahibin ses testi kayıtlı değil"}
                {q.open_opportunities > 0
                  ? ` · ${q.open_opportunities} açık evrim fırsatı`
                  : ""}
              </span>
            </li>
            {run && (
              <li data-voice-qualification-run>
                <div className="event-row">
                  <span>
                    Son sentetik koşu · derlem s{run.corpus_version ?? "?"}
                  </span>
                  <span className="event-when">{when(run.recorded_at, now)}</span>
                </div>
                <span className="muted">
                  {run.total_cases ?? "?"} cümle · {run.passed ?? "?"} doğru ·{" "}
                  {run.clarification ?? "?"} netleştirme · {run.failed_routing ?? "?"} yanlış
                  yönlendirme · {run.forbidden_side_effects ?? "?"} yasak yan etki
                </span>
              </li>
            )}
            {run?.confusion.map((c) => (
              <li key={c.case_id} data-voice-qualification-confusion={c.case_id}>
                <div className="event-row">
                  <span>{c.case_id}</span>
                  <span className="event-when">
                    {c.expected ?? "?"} → {c.resolved ?? "?"}
                  </span>
                </div>
                <span className="muted">{c.utterance}</span>
              </li>
            ))}
            <li data-voice-qualification-note>
              <span className="muted">
                Sentetik cümleler gerçek yönlendiriciden ve araç yolundan geçer; ses donanımı
                sınanmaz. Fiziksel ses testi sahibindir.
              </span>
            </li>
          </ul>
        );
      }}
    </Panel>
  );
}
