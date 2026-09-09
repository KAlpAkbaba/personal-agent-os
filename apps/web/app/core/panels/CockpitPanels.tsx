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
  type Loaded,
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
  type ApprovalGate,
  approvalGate,
  draftKindLabel,
  draftRecipientsLine,
  firstLine,
  formatEventWhen,
  proposalConflictsLine,
  proposalKindLabel,
  rowPending,
  rowReadBack,
} from "../../lib/cockpit/approval-rows";
import type {
  ApprovalPairProps,
  PendingDraft,
  PendingProposal,
} from "../../lib/cockpit/approvals";
import {
  artifactKindLabel,
  artifactOpenGate,
  artifactRenderLine,
  renderIsValid,
  validRenders,
} from "../../lib/cockpit/artifact-rows";
import {
  type ArtifactOpenProps,
  type ArtifactRender,
  type ArtifactRow,
  artifactRenderUrl,
} from "../../lib/cockpit/artifacts";
import {
  APP_ACTION_LABEL,
  APP_ROWS_SHOWN,
  appActionGate,
  appKindLabel,
  appRowLine,
  appRowUrl,
  rowIsRunning,
} from "../../lib/cockpit/app-rows";
import { APP_ACTIONS, type AppAction, type AppProjectRow, type AppsControlProps } from "../../lib/cockpit/apps";
import type { GenesisAction, GenesisControlProps, GenesisRunRow } from "../../lib/cockpit/genesis";
import {
  GENESIS_ACTION_LABEL,
  GENESIS_ROWS_SHOWN,
  genesisActionGate,
  genesisRowActions,
  genesisRowLine,
  rowIsActive as genesisRowIsActive,
  rowIsAwaiting as genesisRowIsAwaiting,
  rowIsFailed as genesisRowIsFailed,
} from "../../lib/cockpit/genesis-rows";
import {
  SCENE_ACTION_LABEL,
  SCENE_ROWS_SHOWN,
  sceneActionGate,
  sceneObjectNamesLine,
  sceneRenderAlt,
  sceneRowActions,
  sceneRowLine,
  rowHasRender as sceneRowHasRender,
  rowIsFailed as sceneRowIsFailed,
  rowIsMismatch as sceneRowIsMismatch,
  rowIsUnavailable as sceneRowIsUnavailable,
  rowIsVerified as sceneRowIsVerified,
} from "../../lib/cockpit/scene-rows";
import type {
  SceneAction,
  SceneControlProps,
  ScenePreviewProps,
  SceneRow,
} from "../../lib/cockpit/scenes";
import type {
  ExecutiveChipAction,
  ExecutiveControlProps,
  ExecutiveDetailProps,
  ExecutiveRunRow,
} from "../../lib/cockpit/executive";
import type {
  CreativeAction,
  CreativeControlProps,
  CreativeImageSide,
  CreativePreviewProps,
  CreativeRunRow,
} from "../../lib/cockpit/creative";
import {
  CREATIVE_ACTION_LABEL,
  CREATIVE_ROWS_SHOWN,
  CREATIVE_SIDE_LABEL,
  creativeActionGate,
  creativeFilesLine,
  creativeHasMetrics,
  creativeImageAlt,
  creativeMetricsLine,
  creativeRoundPhrase,
  creativeRowActions,
  creativeRowLine,
  creativeRowSides,
  rowHasImage as creativeRowHasImage,
  rowIsFailed as creativeRowIsFailed,
  rowIsMismatch as creativeRowIsMismatch,
  rowIsUnavailable as creativeRowIsUnavailable,
  rowIsVerified as creativeRowIsVerified,
} from "../../lib/cockpit/creative-rows";
import {
  EXECUTIVE_ACTION_LABEL,
  EXECUTIVE_ROWS_SHOWN,
  executiveActionGate,
  executiveRowActions,
  executiveRowLine,
  rowIsActive as executiveRowIsActive,
  rowIsComplete as executiveRowIsComplete,
  rowIsFailed as executiveRowIsFailed,
  rowIsPartial as executiveRowIsPartial,
  rowIsPaused as executiveRowIsPaused,
} from "../../lib/cockpit/executive-rows";
import type { NativeBuildRow } from "../../lib/cockpit/native";
import {
  NATIVE_ROWS_SHOWN,
  nativeArtifactLine,
  nativeIdentityLine,
  nativeRowLine,
  nativeVerdictLine,
  nativeVersionDisagrees,
  rowHasArtifact as nativeRowHasArtifact,
  rowIsFailed as nativeRowIsFailed,
  rowIsMismatch as nativeRowIsMismatch,
  rowIsUnavailable as nativeRowIsUnavailable,
  rowIsVerified as nativeRowIsVerified,
} from "../../lib/cockpit/native-rows";
import {
  FOCUS_UNSUPPORTED,
  focusSourceLabel,
  focusSummary,
  identityLine,
} from "../../lib/research/focus";
import { appView } from "../../lib/uistate/apps";
import { artifactView } from "../../lib/uistate/artifacts";
import {
  CALENDAR_PROPOSAL_STATE_LABEL,
  calendarView,
  todaysPublishedEvents,
} from "../../lib/uistate/calendar";
import { isCalendarProposalState, isMailDraftState } from "../../lib/uistate/contract";
import { documentPartPhrase, documentView, lastAnswerRefs, previousDocument } from "../../lib/uistate/documents";
import { genesisView } from "../../lib/uistate/genesis";
import {
  APP_EMPTY,
  APP_UNTOLD,
  ARTIFACT_EMPTY,
  ARTIFACT_UNTOLD,
  CALENDAR_EMPTY,
  CALENDAR_NO_PROPOSAL,
  CALENDAR_UNTOLD,
  DOCUMENT_EMPTY,
  DOCUMENT_LABEL,
  EXECUTIVE_EMPTY,
  EXECUTIVE_UNTOLD,
  GENESIS_EMPTY,
  GENESIS_UNTOLD,
  MAIL_EMPTY,
  MAIL_UNTOLD,
  OPERATOR_EMPTY,
  OPERATOR_LABEL,
  CREATIVE_EMPTY,
  CREATIVE_UNTOLD,
  NATIVE_EMPTY,
  NATIVE_UNTOLD,
  SCENE_EMPTY,
  SCENE_UNTOLD,
  documentFactsLine,
  documentRefLine,
  formatAge,
  operatorErrorLine,
  operatorFactsLine,
  stateLabel,
  subsystemLabel,
} from "../../lib/uistate/labels";
import { sceneView } from "../../lib/uistate/scenes";
import { creativeView } from "../../lib/uistate/creative";
import { nativeView } from "../../lib/uistate/native";
import { executiveView } from "../../lib/uistate/executive";
import { MAIL_DRAFT_STATE_LABEL, mailView } from "../../lib/uistate/mail";
import { operatorPosition, operatorView } from "../../lib/uistate/operator";
import type { CoreTruth } from "../../lib/uistate/truth";
import {
  appClaim,
  artifactClaim,
  calendarClaim,
  creativeClaim,
  documentClaim,
  executiveClaim,
  genesisClaim,
  liveEventFor,
  mailClaim,
  nativeClaim,
  operatorClaim,
  recentDescending,
  sceneClaim,
} from "../../lib/uistate/truth";
import Panel, { LoadedNotice } from "./Panel";

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
 * Read-only, on purpose and permanently. Approving a candidate and
 * authorising a release are owner actions on the surface that owns them
 * (ADR-0052, ADR-0053 §5), and a panel that could perform one would be a
 * second authority surface to keep honest. This one can only show, and says
 * where the real action lives. (M21's approval pair under a draft is not
 * that: it asks the Cloud Core to run its own gate and decides nothing —
 * see `MailPanel`.)
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

/**
 * File & Document Intelligence (M20 spec §3): which of the owner's documents
 * the Core is reading, which it read before, and what its last answer cited —
 * from the same state feed the Core reads.
 *
 * Like the operator's panel, deliberately NOT a REST panel, and for a stronger
 * reason: the owner's files live on the owner's machine, and this page must
 * never fetch one. Everything here is a token the Cloud Core published on the
 * bus — the file's NAME, its path only when the Core chose to name it by path,
 * the place inside it in the owner's words, the step, and the refs of the
 * last answer as `[{ref, path, excerpt}]`. No content is shown that the state
 * did not carry, and no progress is drawn because none is published.
 *
 * Four outcomes: nothing ever published ("henüz bir belge okunmadı"); a live
 * read with its facts; a read whose claim aged out ("son bilinen", with the
 * age — not "finished": we stopped being told); and beneath any of the last
 * three, the previous document the bus itself carried and the refs of the
 * newest answer, each dated by its own event.
 */
export function DocumentsPanel({ truth, now }: { truth: CoreTruth; now: number }) {
  const claim = documentClaim(truth, now);
  const view = documentView(claim);
  const told = view.lastKnown !== null;
  const previous = previousDocument(truth, claim.event);
  const answer = lastAnswerRefs(truth);
  const badge = !told ? "0" : view.stage === "none" ? "son bilinen" : "inceleniyor";

  return (
    <section
      className="panel"
      data-panel="documents"
      data-panel-empty={told ? "no" : "yes"}
      data-document-stage={view.stage}
      data-document-last-known={view.lastKnown ?? ""}
    >
      <h3 className="panel-title">
        <span>Belgeler</span>
        <span className="panel-count">{badge}</span>
      </h3>
      {!told ? (
        <p className="panel-empty">{DOCUMENT_EMPTY}</p>
      ) : (
        <ul>
          <li
            data-document-current
            data-document-file={view.file ?? ""}
            data-document-path={view.path ?? ""}
            data-document-part={view.part ?? ""}
            data-document-step={view.step ?? ""}
          >
            <div className="event-row">
              {/* The file's name as published; without one, the state and no name. */}
              <span>
                {view.stage === "none" ? "Son bilinen: " : ""}
                {view.file ?? DOCUMENT_LABEL.analysing}
              </span>
              <span className="event-when">{formatAge(view.ageMs)}</span>
            </div>
            {/* The path is a published fact or absent; it is never derived from the name. */}
            {view.path && (
              <span className="muted" data-document-path-line>
                {view.path}
              </span>
            )}
            <span className="muted">
              {documentFactsLine(view)}
              {view.label && ` · ${view.label}`}
            </span>
          </li>
          {view.stage === "none" && (
            <li data-document-expired>
              <span className="muted">
                Bu belge için yeni bir bildirim gelmedi. İncelemenin bittiği bildirilmedi — yalnızca haber alınamadı.
              </span>
            </li>
          )}
          {previous && (
            <li
              data-document-previous
              data-document-file={previous.facts.file ?? ""}
              data-document-path={previous.facts.path ?? ""}
            >
              <div className="event-row">
                <span>Önceki belge: {previous.facts.file}</span>
                <span className="event-when">{when(previous.event.at, now)}</span>
              </div>
              {previous.facts.path && (
                <span className="muted" data-document-path-line>
                  {previous.facts.path}
                </span>
              )}
              <span className="muted">
                {documentPartPhrase(previous.facts.part, previous.facts.kind) ?? "yer bildirilmedi"}
              </span>
            </li>
          )}
          {answer && (
            <li data-document-refs={answer.refs.length}>
              <div className="event-row">
                <span>Son yanıtın kaynakları</span>
                <span className="event-when">{when(answer.event.at, now)}</span>
              </div>
              {/* dosya · yer · alıntı, each as the answer published it. */}
              <ul>
                {answer.refs.map((ref, index) => (
                  <li
                    key={`${ref.path ?? ""}#${ref.ref}#${index}`}
                    data-document-ref={ref.ref}
                    data-document-ref-path={ref.path ?? ""}
                  >
                    <span className="muted">{documentRefLine(ref)}</span>
                  </li>
                ))}
              </ul>
            </li>
          )}
        </ul>
      )}
    </section>
  );
}

// ------------------------------------------------- M21: mail and the calendar

/** What the pair says under a draft: what it would do, and what it would not. */
const MAIL_GATE_NOTE =
  'Onay, sesli "Gönder." ile aynı kapıdan geçer: okunmamış bir taslak gönderilmez, sunucu ayarı kapalıysa gönderilmez, ikinci onay ikinci kez göndermez. Bu ekran posta sunucusuna doğrudan ulaşmaz.';

const CALENDAR_GATE_NOTE =
  'Onay, sesli "Onayla." ile aynı kapıdan geçer: okunmamış bir öneri işlenmez, sunucu ayarı kapalıysa işlenmez, ikinci onay ikinci kez işlemez. Bu ekran takvim sunucusuna doğrudan ulaşmaz.';

/**
 * The Approve / Discard pair under one pending row (M21 spec §3).
 *
 * This is the cockpit's one control that asks the Cloud Core to change the
 * world outside — and it is not a second authority surface, because it
 * decides nothing: the click asks the Cloud Core to run the SAME gate the
 * spoken "Gönder." runs, and the Cloud Core refuses on its own terms (not
 * read back, host flag off, already sent). The pair is disabled here with
 * its reason in words until the row was read back, so the button never
 * invites a click the gate would refuse; it is disabled while any call is
 * in flight, so nothing is asked for twice.
 */
function ApprovalPair({
  id,
  family,
  gate,
  pair,
  confirmLabel,
}: {
  id: string;
  family: "draft" | "proposal";
  gate: ApprovalGate;
  pair: ApprovalPairProps;
  confirmLabel: string;
}) {
  const inFlight = pair.busy !== null && pair.busy.id === id;
  return (
    <div
      className="approval-pair"
      data-approval-pair={id}
      data-approval-family={family}
      data-approval-enabled={gate.enabled ? "yes" : "no"}
      data-approval-in-flight={inFlight ? "yes" : "no"}
    >
      <button
        type="button"
        className="core-chip"
        data-approval-action="confirm"
        data-approval-target={id}
        disabled={!gate.enabled}
        onClick={() => pair.onConfirm(id)}
      >
        {confirmLabel}
      </button>
      <button
        type="button"
        className="core-chip"
        data-approval-action="discard"
        data-approval-target={id}
        disabled={!gate.enabled}
        onClick={() => pair.onDiscard(id)}
      >
        Vazgeç
      </button>
      {gate.reason && (
        <span className="approval-reason" data-approval-reason={gate.reasonKind ?? ""}>
          {gate.reason}
        </span>
      )}
    </div>
  );
}

/** The last answer the pair got, for the family this panel shows, dated. */
function ApprovalOutcomeLine({ pair, family, now }: { pair: ApprovalPairProps; family: "draft" | "proposal"; now: number }) {
  const outcome = pair.outcome;
  if (!outcome) return null;
  const draft = outcome.action === "confirm_draft" || outcome.action === "discard_draft";
  if ((family === "draft") !== draft) return null;
  return (
    <p
      className={`approval-outcome ${outcome.ok ? "muted" : "panel-unknown"}`}
      data-approval-outcome={outcome.action}
      data-approval-ok={outcome.ok ? "yes" : "no"}
      data-approval-target={outcome.id}
    >
      {outcome.text}
      {` · ${formatAge(Math.max(0, now - outcome.at))}`}
    </p>
  );
}

/** The draft's lifecycle line: its state in the spec's word, when it was read back, when confirmed, what was sent. */
function draftStateLine(draft: PendingDraft, now: number): string {
  const pending = rowPending(draft.state);
  const readBack = rowReadBack(draft);
  let word: string;
  if (pending && readBack) word = draft.read_back_at ? `okundu (${when(draft.read_back_at, now)})` : "okundu";
  else if (draft.state === null) word = "durum bildirilmedi";
  else word = isMailDraftState(draft.state) ? MAIL_DRAFT_STATE_LABEL[draft.state] : draft.state;
  const parts = [`taslak: ${word}`];
  if (pending && !readBack) parts.push("henüz okunmadı");
  if (draft.confirmed_at) parts.push(`onaylandı ${when(draft.confirmed_at, now)}`);
  if (draft.sent_message_id) parts.push(`ileti: ${draft.sent_message_id}`);
  return parts.join(" · ");
}

function DraftRow({ draft, now, pair }: { draft: PendingDraft; now: number; pair: ApprovalPairProps }) {
  const pending = rowPending(draft.state);
  const readBack = rowReadBack(draft);
  const line = firstLine(draft.body);
  return (
    <li
      data-draft={draft.draft_id}
      data-draft-state={draft.state ?? ""}
      data-draft-pending={pending ? "yes" : "no"}
      data-draft-read-back={readBack ? "yes" : "no"}
    >
      <div className="event-row">
        <span>{draft.subject ?? "konu bildirilmedi"}</span>
        <span className="event-when">
          {[draftKindLabel(draft.kind), when(draft.created_at, now)].filter(Boolean).join(" · ")}
        </span>
      </div>
      <span className="muted" data-draft-to>
        {draftRecipientsLine(draft)}
      </span>
      {/* The first line and no more: the body stays on the Cloud Core. */}
      <span className="muted" data-draft-first-line>
        {line ?? "gövde bildirilmedi"}
      </span>
      <span className="muted" data-draft-state-line>
        {draftStateLine(draft, now)}
      </span>
      {pending && (
        <ApprovalPair
          id={draft.draft_id}
          family="draft"
          gate={approvalGate(draft, pair.busy)}
          pair={pair}
          confirmLabel="Onayla — gönder"
        />
      )}
    </li>
  );
}

/**
 * Posta (M21 spec §3): what the Core is doing with the owner's mail, from
 * the bus, and the drafts waiting for the owner, from `/v1/mail/drafts/pending`
 * — with the pair that asks the Cloud Core to send or discard one.
 *
 * Two sources, kept apart because they answer different questions. The bus
 * line is "what is happening now" and decays like every bus claim; the rows
 * are "what exists" and are the route's. Nothing here reaches a mail
 * provider: the folder and subject are tokens the Cloud Core published, the
 * draft is the row the Cloud Core holds, and the body is shown to its first
 * line only. The empty sentence is the route's answer, never the bus's
 * silence — and "henüz yok" (no route on this Cloud Core) is neither.
 */
export function MailPanel({
  pending,
  truth,
  now,
  pair,
}: {
  pending: Loaded<PendingDraft[]>;
  truth: CoreTruth;
  now: number;
  pair: ApprovalPairProps;
}) {
  const view = mailView(mailClaim(truth, now));
  const told = view.lastKnown !== null;
  const drafts = pending.kind === "ok" ? pending.value : [];
  const open = drafts.filter((d) => rowPending(d.state));
  const awaiting = open.filter((d) => rowReadBack(d));
  return (
    <section
      className={`panel ${awaiting.length ? "attention" : ""}`}
      data-panel="mail"
      data-panel-state={pending.kind}
      data-panel-empty={pending.kind === "ok" ? (open.length ? "no" : "yes") : ""}
      data-mail-stage={view.stage}
      data-mail-last-known={view.lastKnown ?? ""}
    >
      <h3 className="panel-title">
        <span>Posta</span>
        {pending.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {open.length}
          </span>
        )}
      </h3>
      {/* The bus: the caption the Core draws, with its age; last-known when it aged out. */}
      <p
        className={told ? "muted" : "panel-empty"}
        data-mail-activity={told ? view.stage : "untold"}
        data-mail-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : MAIL_UNTOLD}
      </p>
      <LoadedNotice state={pending} />
      {pending.kind === "ok" && open.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {MAIL_EMPTY}
        </p>
      )}
      {drafts.length > 0 && (
        <ul>
          {drafts.map((draft) => (
            <DraftRow key={draft.draft_id} draft={draft} now={now} pair={pair} />
          ))}
        </ul>
      )}
      <ApprovalOutcomeLine pair={pair} family="draft" now={now} />
      <p className="muted" data-mail-gate-note>
        {MAIL_GATE_NOTE}
      </p>
    </section>
  );
}

/** The proposal's lifecycle line, on the draft's pattern. */
function proposalStateLine(proposal: PendingProposal, now: number): string {
  const pending = rowPending(proposal.state);
  const readBack = rowReadBack(proposal);
  let word: string;
  if (pending && readBack) word = proposal.read_back_at ? `okundu (${when(proposal.read_back_at, now)})` : "okundu";
  else if (proposal.state === null) word = "durum bildirilmedi";
  else word = isCalendarProposalState(proposal.state) ? CALENDAR_PROPOSAL_STATE_LABEL[proposal.state] : proposal.state;
  const parts = [`öneri: ${word}`];
  if (pending && !readBack) parts.push("henüz okunmadı");
  if (proposal.confirmed_at) parts.push(`onaylandı ${when(proposal.confirmed_at, now)}`);
  if (proposal.event_id) parts.push(`etkinlik: ${proposal.event_id}`);
  return parts.join(" · ");
}

function ProposalRow({ proposal, now, pair }: { proposal: PendingProposal; now: number; pair: ApprovalPairProps }) {
  const pending = rowPending(proposal.state);
  const readBack = rowReadBack(proposal);
  return (
    <li
      data-proposal={proposal.proposal_id}
      data-proposal-state={proposal.state ?? ""}
      data-proposal-pending={pending ? "yes" : "no"}
      data-proposal-read-back={readBack ? "yes" : "no"}
      data-proposal-conflicts={proposal.conflicts === null ? "" : proposal.conflicts.length}
    >
      <div className="event-row">
        <span>{proposal.title ?? "başlık bildirilmedi"}</span>
        <span className="event-when">
          {[proposalKindLabel(proposal.kind), when(proposal.created_at, now)].filter(Boolean).join(" · ")}
        </span>
      </div>
      <span className="muted" data-proposal-when>
        {formatEventWhen(proposal.start, proposal.end, proposal.all_day)}
        {proposal.location && ` · ${proposal.location}`}
      </span>
      {/* The conflicts the row carries, each named as the Cloud Core recorded it. */}
      <span className="muted" data-proposal-conflicts-line>
        {proposalConflictsLine(proposal)}
      </span>
      {proposal.conflicts !== null && proposal.conflicts.length > 0 && (
        <ul>
          {proposal.conflicts.map((conflict, index) => (
            <li key={`${conflict.title ?? ""}#${conflict.start ?? ""}#${index}`} data-proposal-conflict={index}>
              <span className="muted">
                {`çakışma: ${conflict.title ?? "başlık bildirilmedi"} · ${formatEventWhen(conflict.start, conflict.end, null)}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      <span className="muted" data-proposal-state-line>
        {proposalStateLine(proposal, now)}
      </span>
      {pending && (
        <ApprovalPair
          id={proposal.proposal_id}
          family="proposal"
          gate={approvalGate(proposal, pair.busy)}
          pair={pair}
          confirmLabel="Onayla — takvime işle"
        />
      )}
    </li>
  );
}

/**
 * Takvim (M21 spec §3): what the Core is doing with the owner's calendar,
 * today's events as the bus itself carried them, and the proposals waiting
 * for the owner from `/v1/calendar/proposals/pending` — with their conflicts
 * and the pair that asks the Cloud Core to commit or discard one.
 *
 * "Today" here is exactly what was published for today: a `calendar.activity`
 * read that named an event and a range meaning today. This page cannot ask
 * a calendar, so it does not claim an agenda it was not given; the empty
 * sentence says there is no published entry, which is the fact.
 */
export function CalendarPanel({
  pending,
  truth,
  now,
  pair,
}: {
  pending: Loaded<PendingProposal[]>;
  truth: CoreTruth;
  now: number;
  pair: ApprovalPairProps;
}) {
  const view = calendarView(calendarClaim(truth, now));
  const told = view.lastKnown !== null;
  const today = todaysPublishedEvents(truth);
  const proposals = pending.kind === "ok" ? pending.value : [];
  const open = proposals.filter((p) => rowPending(p.state));
  const awaiting = open.filter((p) => rowReadBack(p));
  return (
    <section
      className={`panel ${awaiting.length ? "attention" : ""}`}
      data-panel="calendar"
      data-panel-state={pending.kind}
      data-panel-empty={pending.kind === "ok" ? (open.length || today.length ? "no" : "yes") : ""}
      data-calendar-stage={view.stage}
      data-calendar-last-known={view.lastKnown ?? ""}
      data-calendar-today={today.length}
    >
      <h3 className="panel-title">
        <span>Takvim</span>
        {pending.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {open.length}
          </span>
        )}
      </h3>
      <p
        className={told ? "muted" : "panel-empty"}
        data-calendar-activity={told ? view.stage : "untold"}
        data-calendar-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : CALENDAR_UNTOLD}
      </p>
      <p className="panel-subheading" data-calendar-today-heading>
        Bugün — yayınlandığı kadar
      </p>
      {today.length === 0 ? (
        <p className="panel-empty" data-calendar-today-empty>
          {CALENDAR_EMPTY}
        </p>
      ) : (
        <ul>
          {today.map((entry) => (
            <li key={entry.title} data-calendar-event={entry.title}>
              <div className="event-row">
                <span>{entry.title}</span>
                <span className="event-when">{when(entry.event.at, now)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="panel-subheading" data-calendar-proposals-heading>
        Bekleyen öneriler
      </p>
      <LoadedNotice state={pending} />
      {pending.kind === "ok" && open.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {CALENDAR_NO_PROPOSAL}
        </p>
      )}
      {proposals.length > 0 && (
        <ul>
          {proposals.map((proposal) => (
            <ProposalRow key={proposal.proposal_id} proposal={proposal} now={now} pair={pair} />
          ))}
        </ul>
      )}
      <ApprovalOutcomeLine pair={pair} family="proposal" now={now} />
      <p className="muted" data-calendar-gate-note>
        {CALENDAR_GATE_NOTE}
      </p>
    </section>
  );
}

// ------------------------------------------------- M22: the Artifact Factory

/** How many of the list's artifacts the panel shows: the last ones, as the route orders them. */
export const ARTIFACT_ROWS_SHOWN = 6;

/** What the panel says under the rows: where the links go, and what "Aç" asks for. */
const ARTIFACT_NOTE =
  'İndirme bağlantıları sahip oturumundan geçer. "Aç", dosyayı Cloud Core\'un kendi adresinden cihaza getirip açmasını ister; özeti kayıttakiyle eşleşmeyen dosya tutulmaz. Doğrulanmamış bir çıktı açılmaz. Bu ekran dosya üretmez, doğrulamaz, cihaza ulaşmaz.';

/**
 * One render on its line, with a download link when — and only when — the
 * row says the independent parser passed it. The link's `href` is the
 * render's real URL (M13's owner-session-gated route); the click fetches
 * the bytes through the session and hands the browser a blob, because a
 * bare navigation carries no bearer. Without a handler the link is still
 * the honest address.
 */
function ArtifactRenderRow({
  artifactId,
  render,
  onDownload,
}: {
  artifactId: string;
  render: ArtifactRender;
  onDownload?: (artifactId: string, format: string) => void;
}) {
  const valid = renderIsValid(render);
  return (
    <li
      data-artifact-render={render.format}
      data-render-state={render.state ?? ""}
      data-render-failing-ref={render.failing_ref ?? ""}
      data-render-valid={valid ? "yes" : "no"}
    >
      <span className="muted">{artifactRenderLine(render)}</span>
      {valid && (
        <a
          className="artifact-download"
          href={artifactRenderUrl(artifactId, render.format)}
          rel="noreferrer"
          data-artifact-download={render.format}
          data-artifact-download-target={artifactId}
          onClick={
            onDownload
              ? (e) => {
                  e.preventDefault();
                  onDownload(artifactId, render.format);
                }
              : undefined
          }
        >
          İndir
        </a>
      )}
    </li>
  );
}

function ArtifactRowItem({
  row,
  now,
  open,
  onDownload,
}: {
  row: ArtifactRow;
  now: number;
  open: ArtifactOpenProps;
  onDownload?: (artifactId: string, format: string) => void;
}) {
  const gate = artifactOpenGate(row, open.busy);
  const inFlight = open.busy === row.artifact_id;
  const valid = validRenders(row).length;
  return (
    <li
      data-artifact={row.artifact_id}
      data-artifact-state={row.state ?? ""}
      data-artifact-kind={row.kind ?? ""}
      data-artifact-renders={row.renders.length}
      data-artifact-valid-renders={valid}
    >
      <div className="event-row">
        <span>{row.title ?? "başlık bildirilmedi"}</span>
        <span className="event-when">
          {[artifactKindLabel(row.kind), when(row.updated_at ?? row.created_at, now)].filter(Boolean).join(" · ")}
        </span>
      </div>
      {row.renders.length === 0 ? (
        <span className="muted" data-artifact-no-renders>
          çıktı bildirilmedi
        </span>
      ) : (
        <ul>
          {row.renders.map((render, index) => (
            <ArtifactRenderRow
              key={`${render.format}#${index}`}
              artifactId={row.artifact_id}
              render={render}
              onDownload={onDownload}
            />
          ))}
        </ul>
      )}
      {/* "Aç": one chip per artifact, enabled only for one the Cloud Core would open. */}
      <div
        className="approval-pair"
        data-artifact-open={row.artifact_id}
        data-artifact-open-enabled={gate.enabled ? "yes" : "no"}
        data-artifact-open-in-flight={inFlight ? "yes" : "no"}
      >
        <button
          type="button"
          className="core-chip"
          data-artifact-action="open"
          data-artifact-target={row.artifact_id}
          disabled={!gate.enabled}
          onClick={() => open.onOpen(row.artifact_id)}
        >
          Aç
        </button>
        {gate.reason && (
          <span className="approval-reason" data-artifact-open-reason={gate.reasonKind ?? ""}>
            {gate.reason}
          </span>
        )}
      </div>
    </li>
  );
}

/**
 * Üretilenler (M22 spec §4): what the factory is doing, from the bus, and
 * what it made, from M13's list route — each artifact with its renders,
 * each render with the verdict the independent parser gave it and the ref
 * that failed when it did not pass, a download link per VALID render on
 * the owner-session-gated route, and "Aç", which asks the Cloud Core to
 * fetch and open one on the device.
 *
 * Two sources, kept apart because they answer different questions. The bus
 * line is "what is happening now" and decays like every bus claim; the rows
 * are "what exists" and are the route's. Nothing here renders, validates,
 * or reaches the device: a render is valid because its row says so, a link
 * exists because the render is valid, "Aç" is enabled because a valid
 * render exists and nothing else is in flight — and the Cloud Core still
 * refuses on its own terms. The empty sentence is the route's answer, never
 * the bus's silence — and "henüz yok" (no route on this Cloud Core) is neither.
 */
export function ArtifactsPanel({
  artifacts,
  truth,
  now,
  open,
  onDownload,
  notice,
}: {
  artifacts: Loaded<ArtifactRow[]>;
  truth: CoreTruth;
  now: number;
  open: ArtifactOpenProps;
  onDownload?: (artifactId: string, format: string) => void;
  /** A download that could not be fetched, in words; the link itself stays. */
  notice?: string | null;
}) {
  const view = artifactView(artifactClaim(truth, now));
  const told = view.lastKnown !== null;
  const rows = artifacts.kind === "ok" ? artifacts.value : [];
  const shown = rows.slice(0, ARTIFACT_ROWS_SHOWN);
  return (
    <section
      className="panel"
      data-panel="artifacts"
      data-panel-state={artifacts.kind}
      data-panel-empty={artifacts.kind === "ok" ? (rows.length ? "no" : "yes") : ""}
      data-artifact-stage={view.stage}
      data-artifact-last-known={view.lastKnown ?? ""}
    >
      <h3 className="panel-title">
        <span>Üretilenler</span>
        {artifacts.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {rows.length}
          </span>
        )}
      </h3>
      {/* The bus: the caption the Core draws, with its age; last-known when it aged out. */}
      <p
        className={told ? "muted" : "panel-empty"}
        data-artifact-activity={told ? view.stage : "untold"}
        data-artifact-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : ARTIFACT_UNTOLD}
      </p>
      <LoadedNotice state={artifacts} />
      {artifacts.kind === "ok" && rows.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {ARTIFACT_EMPTY}
        </p>
      )}
      {shown.length > 0 && (
        <ul>
          {shown.map((row) => (
            <ArtifactRowItem key={row.artifact_id} row={row} now={now} open={open} onDownload={onDownload} />
          ))}
        </ul>
      )}
      {notice && (
        <p className="panel-unknown" data-artifact-notice>
          {notice}
        </p>
      )}
      {open.outcome && (
        <p
          className={`approval-outcome ${open.outcome.ok ? "muted" : "panel-unknown"}`}
          data-artifact-outcome
          data-artifact-ok={open.outcome.ok ? "yes" : "no"}
          data-artifact-target={open.outcome.artifactId}
        >
          {open.outcome.text}
          {` · ${formatAge(Math.max(0, now - open.outcome.at))}`}
        </p>
      )}
      <p className="muted" data-artifact-note>
        {ARTIFACT_NOTE}
      </p>
    </section>
  );
}

// ---------------------------------------------------- M23: the App Factory

/** What the panel says under the rows: where the link goes, and what the chips ask for. */
const APP_NOTE =
  'Bağlantı yalnızca çalışan bir uygulama için gösterilir ve sahibin makinesindeki tarayıcıda açılır; bu sayfa ona ulaşmaz. "Çalıştır", "Durdur" ve "Testleri çalıştır" Cloud Core\'dan cihazdaki sınırlı süreci ister: komut şablonun izin listesinden gelir, süreç eşlikçinin kendi iş nesnesinde çalışır, sahibin hiçbir süreci durdurulmaz. Bu ekran süreç başlatmaz, durdurmaz, dosya yazmaz.';

/** The three chips under one project, each enabled only when the Cloud Core would not refuse it, with the reason in words when it would. */
function AppControls({ row, control }: { row: AppProjectRow; control: AppsControlProps }) {
  const handlers: Record<AppAction, (id: string) => void> = { run: control.onRun, stop: control.onStop, test: control.onTest };
  const gates = APP_ACTIONS.map((action) => ({ action, gate: appActionGate(row, action, control.busy) }));
  const inFlight = control.busy !== null && control.busy.id === row.app_id;
  // One sentence per distinct reason, naming every chip it refuses: a planned
  // project's "Çalıştır" and "Testleri çalıştır" share one sentence, and three
  // chips disabled for the one in-flight call say it once.
  const reasons = new Map<string, { actions: AppAction[]; kind: string }>();
  for (const { action, gate } of gates) {
    if (gate.reason === null) continue;
    const entry = reasons.get(gate.reason) ?? { actions: [], kind: gate.reasonKind ?? "" };
    entry.actions.push(action);
    reasons.set(gate.reason, entry);
  }
  return (
    <div
      className="approval-pair"
      data-app-controls={row.app_id}
      data-app-in-flight={inFlight ? "yes" : "no"}
      data-app-in-flight-action={inFlight && control.busy ? control.busy.action : ""}
    >
      {gates.map(({ action, gate }) => (
        <button
          key={action}
          type="button"
          className="core-chip"
          data-app-action={action}
          data-app-target={row.app_id}
          data-app-enabled={gate.enabled ? "yes" : "no"}
          disabled={!gate.enabled}
          onClick={() => handlers[action](row.app_id)}
        >
          {APP_ACTION_LABEL[action]}
        </button>
      ))}
      {Array.from(reasons, ([reason, { actions, kind }]) => (
        <span key={reason} className="approval-reason" data-app-reason={kind} data-app-reason-for={actions.join(",")}>
          {kind === "busy" ? reason : `${actions.map((a) => APP_ACTION_LABEL[a]).join(", ")}: ${reason}`}
        </span>
      ))}
    </div>
  );
}

function AppRowItem({ row, now, control }: { row: AppProjectRow; now: number; control: AppsControlProps }) {
  const running = rowIsRunning(row);
  const url = appRowUrl(row);
  return (
    <li
      data-app={row.app_id}
      data-app-state={row.state ?? ""}
      data-app-kind={row.kind ?? ""}
      data-app-template={row.template ?? ""}
      data-app-port={row.port ?? ""}
      data-app-running={running ? "yes" : "no"}
      data-app-tests-passed={row.tests?.passed ?? ""}
      data-app-tests-failed={row.tests?.failed ?? ""}
    >
      <div className="event-row">
        <span>
          {row.name ?? "ad bildirilmedi"}
          {/* The link: only for a RUNNING project with a port, only to the
              loopback, only for the owner's own browser. Never fetched here. */}
          {url && (
            <a
              className="app-link"
              href={url}
              target="_blank"
              rel="noreferrer noopener"
              data-app-link={row.app_id}
              data-app-url={url}
            >
              {url}
            </a>
          )}
        </span>
        <span className="event-when">
          {[appKindLabel(row.kind), when(row.updated_at ?? row.created_at, now)].filter(Boolean).join(" · ")}
        </span>
      </div>
      {/* The state, the port, the last test counts — each as the row says it, or the statement that it did not. */}
      <span className="muted" data-app-line>
        {appRowLine(row)}
      </span>
      {row.root_path && (
        <span className="muted" data-app-root-path>
          {row.root_path}
        </span>
      )}
      <AppControls row={row} control={control} />
    </li>
  );
}

/**
 * Uygulamalar (M23 spec §6): what the App Factory is doing, from the bus,
 * and what it made, from `/v1/apps` — each project with its state, the
 * port its bounded process is bound to while it runs, the counts its last
 * test run gave, a link the owner's own browser can open for a RUNNING
 * project and nothing else, and the three chips that ask the Cloud Core
 * for the device's `project.run` / `project.stop` / `project.test`.
 *
 * Two sources, kept apart because they answer different questions. The bus
 * line is "what is happening now" and decays like every bus claim; the rows
 * are "what exists" and are the route's. Nothing here scaffolds, runs,
 * stops or tests: a project is running because its row says so, a link
 * exists because the project is running on a port the row named, a chip is
 * enabled because the Cloud Core would not refuse it and nothing else is in
 * flight — and the Cloud Core still refuses on its own terms. The empty
 * sentence is the route's answer, never the bus's silence — and "henüz
 * yok" (no route on this Cloud Core) is neither.
 */
export function AppsPanel({
  apps,
  truth,
  now,
  control,
}: {
  apps: Loaded<AppProjectRow[]>;
  truth: CoreTruth;
  now: number;
  control: AppsControlProps;
}) {
  const view = appView(appClaim(truth, now));
  const told = view.lastKnown !== null;
  const rows = apps.kind === "ok" ? apps.value : [];
  const shown = rows.slice(0, APP_ROWS_SHOWN);
  const running = rows.filter(rowIsRunning).length;
  const failed = rows.some((row) => row.state === "failed");
  return (
    <section
      className={`panel ${failed ? "attention" : ""}`}
      data-panel="apps"
      data-panel-state={apps.kind}
      data-panel-empty={apps.kind === "ok" ? (rows.length ? "no" : "yes") : ""}
      data-app-stage={view.stage}
      data-app-last-known={view.lastKnown ?? ""}
      data-apps-running={apps.kind === "ok" ? running : ""}
    >
      <h3 className="panel-title">
        <span>Uygulamalar</span>
        {apps.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {running > 0 ? `${running} çalışıyor / ${rows.length}` : `${rows.length}`}
          </span>
        )}
      </h3>
      {/* The bus: the caption the Core draws, with its age; last-known when it aged out. */}
      <p
        className={told ? "muted" : "panel-empty"}
        data-app-activity={told ? view.stage : "untold"}
        data-app-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : APP_UNTOLD}
      </p>
      <LoadedNotice state={apps} />
      {apps.kind === "ok" && rows.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {APP_EMPTY}
        </p>
      )}
      {shown.length > 0 && (
        <ul>
          {shown.map((row) => (
            <AppRowItem key={row.app_id} row={row} now={now} control={control} />
          ))}
        </ul>
      )}
      {control.outcome && (
        <p
          className={`approval-outcome ${control.outcome.ok ? "muted" : "panel-unknown"}`}
          data-app-outcome={control.outcome.action}
          data-app-ok={control.outcome.ok ? "yes" : "no"}
          data-app-target={control.outcome.id}
        >
          {control.outcome.text}
          {` · ${formatAge(Math.max(0, now - control.outcome.at))}`}
        </p>
      )}
      <p className="muted" data-app-note>
        {APP_NOTE}
      </p>
    </section>
  );
}

// ------------------------------------------------- M24: Capability Genesis

/** What the panel says under the rows: what the two chips ask for, and what this page cannot do. */
const GENESIS_NOTE =
  '"Onayla" yalnızca onay bekleyen bir çalışma için gösterilir ve Cloud Core\'dan sesli "Onaylıyorum" ile aynı kapıdan geçmesini ister: yetkilendirme sahip oturumuna bağlı kaydedilir, çalışma oradan sürer. "Vazgeç" süren bir çalışmayı durdurmasını ister; kayıt bırakmaz. Bir yetenek "doğrulandı" dendiğinde vardır, önce değil. Bu ekran arayüz araştırmaz, bağdaştırıcı yazmaz, yetenek kaydetmez, uygulamaya ulaşmaz.';

/**
 * The chips under one run: "Onayla" ONLY at `awaiting_approval`, "Vazgeç"
 * while the run is active, neither once it settled or failed — each drawn
 * only when the Cloud Core would not refuse it, and disabled with the reason
 * in words while another call is in flight.
 */
function GenesisControls({ row, control }: { row: GenesisRunRow; control: GenesisControlProps }) {
  const actions = genesisRowActions(row);
  if (actions.length === 0) return null;
  const handlers: Record<GenesisAction, (id: string) => void> = { approve: control.onApprove, cancel: control.onCancel };
  const gates = actions.map((action) => ({ action, gate: genesisActionGate(row, action, control.busy) }));
  const inFlight = control.busy !== null && control.busy.id === row.run_id;
  // One sentence per distinct reason, naming every chip it refuses; the one
  // in-flight call disables both chips and is said once.
  const reasons = new Map<string, { actions: GenesisAction[]; kind: string }>();
  for (const { action, gate } of gates) {
    if (gate.reason === null) continue;
    const entry = reasons.get(gate.reason) ?? { actions: [], kind: gate.reasonKind ?? "" };
    entry.actions.push(action);
    reasons.set(gate.reason, entry);
  }
  return (
    <div
      className="approval-pair"
      data-genesis-controls={row.run_id}
      data-genesis-in-flight={inFlight ? "yes" : "no"}
      data-genesis-in-flight-action={inFlight && control.busy ? control.busy.action : ""}
    >
      {gates.map(({ action, gate }) => (
        <button
          key={action}
          type="button"
          className="core-chip"
          data-genesis-action={action}
          data-genesis-target={row.run_id}
          data-genesis-enabled={gate.enabled ? "yes" : "no"}
          disabled={!gate.enabled}
          onClick={() => handlers[action](row.run_id)}
        >
          {GENESIS_ACTION_LABEL[action]}
        </button>
      ))}
      {Array.from(reasons, ([reason, { actions: refused, kind }]) => (
        <span key={reason} className="approval-reason" data-genesis-reason={kind} data-genesis-reason-for={refused.join(",")}>
          {kind === "busy" ? reason : `${refused.map((a) => GENESIS_ACTION_LABEL[a]).join(", ")}: ${reason}`}
        </span>
      ))}
    </div>
  );
}

function GenesisRowItem({ row, now, control }: { row: GenesisRunRow; now: number; control: GenesisControlProps }) {
  const awaiting = genesisRowIsAwaiting(row);
  const failed = genesisRowIsFailed(row);
  return (
    <li
      data-genesis-run={row.run_id}
      data-genesis-run-state={row.state ?? ""}
      data-genesis-run-capability={row.capability ?? ""}
      data-genesis-run-awaiting={awaiting ? "yes" : "no"}
      data-genesis-run-active={genesisRowIsActive(row) ? "yes" : "no"}
      data-genesis-run-failed={failed ? "yes" : "no"}
      data-genesis-run-approval-required={row.approval_required === null ? "" : row.approval_required ? "yes" : "no"}
      data-genesis-run-error-class={row.error_class ?? ""}
    >
      <div className="event-row">
        <span>{row.capability ?? "yetenek bildirilmedi"}</span>
        <span className="event-when">{when(row.updated_at ?? row.created_at, now)}</span>
      </div>
      {/* The state with its error class beside failed, the approval flag and the two classes — each as the row says it, or the statement that it did not. */}
      <span className="muted" data-genesis-line>
        {genesisRowLine(row)}
      </span>
      {/* The run's own sentence about its failure, when the row carried one — only beside failed. */}
      {failed && row.error_message && (
        <span className="muted" data-genesis-error-message>
          {row.error_message}
        </span>
      )}
      <GenesisControls row={row} control={control} />
    </li>
  );
}

/**
 * Yeni Yetenek (M24 spec §8): what Capability Genesis is doing, from the
 * bus, and the runs that exist, from `/v1/genesis/runs` — each run with its
 * capability, its state, when, and the error when it failed; "Onayla" ONLY
 * for a run at `awaiting_approval` and "Vazgeç" while a run is active, both
 * asking the Cloud Core for its own `capability.approve` / `capability.cancel`.
 *
 * Two sources, kept apart because they answer different questions. The bus
 * line is "what is happening now" and decays like every bus claim; the rows
 * are "what exists" and are the route's. Nothing here researches, builds,
 * registers or reaches the application: a run is waiting because its row
 * says so, a chip is drawn because the Cloud Core would not refuse it and
 * enabled because nothing else is in flight — and the Cloud Core still
 * refuses on its own terms (the approval is bound to the owner's session,
 * ADR-0087 §5). The empty sentence is the route's answer, never the bus's
 * silence — and "henüz yok" (no route on this Cloud Core) is neither. No
 * progress bar and no "improving": fourteen states, each a sentence about
 * a row.
 */
export function GenesisPanel({
  runs,
  truth,
  now,
  control,
}: {
  runs: Loaded<GenesisRunRow[]>;
  truth: CoreTruth;
  now: number;
  control: GenesisControlProps;
}) {
  const view = genesisView(genesisClaim(truth, now));
  const told = view.lastKnown !== null;
  const rows = runs.kind === "ok" ? runs.value : [];
  const shown = rows.slice(0, GENESIS_ROWS_SHOWN);
  const awaiting = rows.filter(genesisRowIsAwaiting).length;
  const failed = rows.some(genesisRowIsFailed);
  return (
    <section
      className={`panel ${awaiting > 0 || failed ? "attention" : ""}`}
      data-panel="genesis"
      data-panel-state={runs.kind}
      data-panel-empty={runs.kind === "ok" ? (rows.length ? "no" : "yes") : ""}
      data-genesis-stage={view.stage}
      data-genesis-last-known={view.lastKnown ?? ""}
      data-genesis-posture={told ? view.posture : ""}
      data-genesis-awaiting={runs.kind === "ok" ? awaiting : ""}
    >
      <h3 className="panel-title">
        <span>Yeni Yetenek</span>
        {runs.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {awaiting > 0 ? `${awaiting} onay bekliyor / ${rows.length}` : `${rows.length}`}
          </span>
        )}
      </h3>
      {/* The bus: the caption the Core draws, with its age; last-known when it aged out. */}
      <p
        className={told ? "muted" : "panel-empty"}
        data-genesis-activity={told ? view.stage : "untold"}
        data-genesis-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : GENESIS_UNTOLD}
      </p>
      <LoadedNotice state={runs} />
      {runs.kind === "ok" && rows.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {GENESIS_EMPTY}
        </p>
      )}
      {shown.length > 0 && (
        <ul>
          {shown.map((row) => (
            <GenesisRowItem key={row.run_id} row={row} now={now} control={control} />
          ))}
        </ul>
      )}
      {control.outcome && (
        <p
          className={`approval-outcome ${control.outcome.ok ? "muted" : "panel-unknown"}`}
          data-genesis-outcome={control.outcome.action}
          data-genesis-ok={control.outcome.ok ? "yes" : "no"}
          data-genesis-target={control.outcome.id}
        >
          {control.outcome.text}
          {` · ${formatAge(Math.max(0, now - control.outcome.at))}`}
        </p>
      )}
      <p className="muted" data-genesis-note>
        {GENESIS_NOTE}
      </p>
    </section>
  );
}

// ---------------------------------------------------- M25: 3D creation

/** What the panel says under the rows: what the two chips ask for, and what this page cannot do. */
const SCENE_NOTE =
  '"Render al" ve "Sahneyi oku" Cloud Core\'dan cihazdaki sınırlı süreci ister: aracın kendi betik arayüzü (Blender için `-b --python`, Unity için `-batchmode -executeMethod`), sabit sürücü dosyası, 3B kökünün içinde ve kendi iş nesnesinde. Sahne, plana uyduğu araçtan geri okunduğunda doğrulanmış olur; önce değil. Sürülemeyen bir araç için düğme gösterilmez. Bu ekran editör açmaz, fare kullanmaz, kod üretmez, sahibin kendi projelerine dokunmaz.';

/** Said under a row that has a render the page has not fetched yet. */
const SCENE_RENDER_PENDING = "Render var; görüntü henüz alınmadı.";

/**
 * One scene's last render.
 *
 * A plain `<img>` on purpose, and `next/image` deliberately not used: `src`
 * here is a `blob:` URL made in this tab from bytes fetched through the
 * owner session (the route is gated, so an optimizer that re-fetches the
 * URL server-side would be answered with a 401 and could not read a blob of
 * this tab's anyway). The dimensions are the driver's, not this page's — a
 * render is up to 1920×1080 (M25 spec §7) and the CSS bounds it without
 * changing its proportions, because the owner reads geometry off it.
 */
function SceneRenderImage({ row, src }: { row: SceneRow; src: string }) {
  /* eslint-disable-next-line next/no-img-element */
  return <img className="scene-render" src={src} alt={sceneRenderAlt(row)} data-scene-render={row.scene_id} data-scene-render-sha={row.render_sha256 ?? ""} />;
}

/**
 * The chips under one scene: both for a scene whose step this build can
 * read, NEITHER for a tool that could not be driven — each drawn only when
 * the Cloud Core would not refuse it, and disabled with the reason in words
 * while another call is in flight.
 */
function SceneControls({ row, control }: { row: SceneRow; control: SceneControlProps }) {
  const actions = sceneRowActions(row);
  if (actions.length === 0) return null;
  const handlers: Record<SceneAction, (id: string) => void> = { render: control.onRender, inspect: control.onInspect };
  const gates = actions.map((action) => ({ action, gate: sceneActionGate(row, action, control.busy) }));
  const inFlight = control.busy !== null && control.busy.id === row.scene_id;
  // One sentence per distinct reason, naming every chip it refuses; the one
  // in-flight call disables both chips and is said once.
  const reasons = new Map<string, { actions: SceneAction[]; kind: string }>();
  for (const { action, gate } of gates) {
    if (gate.reason === null) continue;
    const entry = reasons.get(gate.reason) ?? { actions: [], kind: gate.reasonKind ?? "" };
    entry.actions.push(action);
    reasons.set(gate.reason, entry);
  }
  return (
    <div
      className="approval-pair"
      data-scene-controls={row.scene_id}
      data-scene-in-flight={inFlight ? "yes" : "no"}
      data-scene-in-flight-action={inFlight && control.busy ? control.busy.action : ""}
    >
      {gates.map(({ action, gate }) => (
        <button
          key={action}
          type="button"
          className="core-chip"
          data-scene-action={action}
          data-scene-target={row.scene_id}
          data-scene-enabled={gate.enabled ? "yes" : "no"}
          disabled={!gate.enabled}
          onClick={() => handlers[action](row.scene_id)}
        >
          {SCENE_ACTION_LABEL[action]}
        </button>
      ))}
      {Array.from(reasons, ([reason, { actions: refused, kind }]) => (
        <span key={reason} className="approval-reason" data-scene-reason={kind} data-scene-reason-for={refused.join(",")}>
          {kind === "busy" ? reason : `${refused.map((a) => SCENE_ACTION_LABEL[a]).join(", ")}: ${reason}`}
        </span>
      ))}
    </div>
  );
}

function SceneRowItem({
  row,
  now,
  control,
  preview,
}: {
  row: SceneRow;
  now: number;
  control: SceneControlProps;
  preview: ScenePreviewProps;
}) {
  const unavailable = sceneRowIsUnavailable(row);
  const failed = sceneRowIsFailed(row);
  const hasRender = sceneRowHasRender(row);
  const src = hasRender ? preview.srcFor(row.scene_id) : null;
  return (
    <li
      data-scene={row.scene_id}
      data-scene-row-tool={row.tool ?? ""}
      data-scene-row-name={row.scene ?? ""}
      data-scene-row-state={row.state ?? ""}
      data-scene-row-objects={row.objects ?? ""}
      data-scene-row-mismatch={row.mismatch ?? ""}
      data-scene-row-verified={sceneRowIsVerified(row) ? "yes" : "no"}
      data-scene-row-unavailable={unavailable ? "yes" : "no"}
      data-scene-row-failed={failed ? "yes" : "no"}
      data-scene-row-has-render={hasRender ? "yes" : "no"}
    >
      <div className="event-row">
        <span>{row.scene ?? "sahne adı bildirilmedi"}</span>
        <span className="event-when">{when(row.updated_at ?? row.created_at, now)}</span>
      </div>
      {/* The tool and the step, with the object count beside `verified` and
          the object beside `mismatch` — each as the row says it, or the
          statement that it did not. */}
      <span className="muted" data-scene-line>
        {sceneRowLine(row)}
      </span>
      {/* What the last inspection called the objects it read. Drawn only for
          a row that HAD an inspection (it counted): "never read" and "read
          and named none" are different answers, and only the second gets a line. */}
      {row.objects !== null && (
        <span className="muted" data-scene-objects={row.object_names.length}>
          {sceneObjectNamesLine(row)}
        </span>
      )}
      {/* The run's own sentence — for Unity, the licensing client's words —
          beside the two steps that have one to give. */}
      {(unavailable || failed) && row.error_message && (
        <span className="muted" data-scene-error-message>
          {row.error_message}
        </span>
      )}
      {/* The last render, fetched through the owner session and shown only
          because the ROW says one exists. Until the bytes are in hand the
          row says that, rather than drawing a picture that is not there. */}
      {hasRender &&
        (src ? (
          <SceneRenderImage row={row} src={src} />
        ) : (
          <span className="muted" data-scene-render-pending>
            {SCENE_RENDER_PENDING}
          </span>
        ))}
      <SceneControls row={row} control={control} />
    </li>
  );
}

/**
 * 3B Sahne (M25 spec §6): what 3D creation is doing, from the bus, and the
 * scenes that exist, from `/v1/scenes` — each scene with its tool, its
 * name, the step it is on, how many objects the last INSPECTION read and
 * what they were called, the object a comparison found wrong, and the last
 * render as an image fetched through the owner session; "Render al" and
 * "Sahneyi oku" ask the Cloud Core for the device's bounded `scene.render`
 * / `scene.inspect`.
 *
 * Two sources, kept apart because they answer different questions. The bus
 * line is "what is happening now" and decays like every bus claim; the rows
 * are "what exists" and are the route's. Nothing here opens an editor,
 * renders a pixel or writes a plan: a scene is verified because its row
 * says the read-back matched, an image is drawn because the row says a
 * render exists, a chip is drawn because there is a tool to ask — and the
 * Cloud Core still refuses on its own terms. An `unavailable` row gets no
 * chips at all and says why in the tool's own words: ADR-0088 §5's honest
 * form, never a control over an editor this machine cannot drive. The
 * empty sentence is the route's answer, never the bus's silence — and
 * "henüz yok" (no route on this Cloud Core) is neither.
 */
export function ScenesPanel({
  scenes,
  truth,
  now,
  control,
  preview,
}: {
  scenes: Loaded<SceneRow[]>;
  truth: CoreTruth;
  now: number;
  control: SceneControlProps;
  preview: ScenePreviewProps;
}) {
  const view = sceneView(sceneClaim(truth, now));
  const told = view.lastKnown !== null;
  const rows = scenes.kind === "ok" ? scenes.value : [];
  const shown = rows.slice(0, SCENE_ROWS_SHOWN);
  const verified = rows.filter(sceneRowIsVerified).length;
  const attention = rows.some((row) => sceneRowIsMismatch(row) || sceneRowIsFailed(row));
  return (
    <section
      className={`panel ${attention ? "attention" : ""}`}
      data-panel="scenes"
      data-panel-state={scenes.kind}
      data-panel-empty={scenes.kind === "ok" ? (rows.length ? "no" : "yes") : ""}
      data-scene-stage={view.stage}
      data-scene-last-known={view.lastKnown ?? ""}
      data-scene-posture={told ? view.posture : ""}
      data-scenes-verified={scenes.kind === "ok" ? verified : ""}
    >
      <h3 className="panel-title">
        <span>3B Sahne</span>
        {scenes.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {verified > 0 ? `${verified} doğrulandı / ${rows.length}` : `${rows.length}`}
          </span>
        )}
      </h3>
      {/* The bus: the caption the Core draws, with its age; last-known when it aged out. */}
      <p
        className={told ? "muted" : "panel-empty"}
        data-scene-activity={told ? view.stage : "untold"}
        data-scene-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : SCENE_UNTOLD}
      </p>
      <LoadedNotice state={scenes} />
      {scenes.kind === "ok" && rows.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {SCENE_EMPTY}
        </p>
      )}
      {shown.length > 0 && (
        <ul>
          {shown.map((row) => (
            <SceneRowItem key={row.scene_id} row={row} now={now} control={control} preview={preview} />
          ))}
        </ul>
      )}
      {preview.notice && (
        <p className="panel-unknown" data-scene-render-notice>
          {preview.notice}
        </p>
      )}
      {control.outcome && (
        <p
          className={`approval-outcome ${control.outcome.ok ? "muted" : "panel-unknown"}`}
          data-scene-outcome={control.outcome.action}
          data-scene-ok={control.outcome.ok ? "yes" : "no"}
          data-scene-target={control.outcome.id}
        >
          {control.outcome.text}
          {` · ${formatAge(Math.max(0, now - control.outcome.at))}`}
        </p>
      )}
      <p className="muted" data-scene-note>
        {SCENE_NOTE}
      </p>
    </section>
  );
}

// ---------------------------------------------- M26: Executive Autonomy

/** What the panel says under the rows: what the three chips ask for, and what this page cannot do. */
const EXECUTIVE_NOTE =
  '"Duraklat", "Devam" ve "İptal" Cloud Core\'a sesli "Bu işi durdur", "Devam et", "Bunu iptal et" ile aynı sinyalleri gönderir: çalışan adım biter, yenisi başlamaz; iptalde telafi adımları çalışır ve sahibin hiçbir dosyası silinmez. Bir iş yalnızca her adımı kanıtıyla doğrulandığında tamamlandı sayılır; eksik kalanına kısmen bitti denir ve nesi eksik olduğu yazılır. Hiçbir adım posta göndermez, ödeme yapmaz, silmez, yayımlamaz — taslak ve öneri sahibin onayını bekler. Bu ekran iş planlamaz, adım çalıştırmaz, iş bitirmez.';

/**
 * The chips under one run: "Duraklat" while it is planned or running,
 * "Devam" ONLY while it is paused, "İptal" while it has not ended, none
 * once it has — each drawn only when the Cloud Core would not refuse it,
 * and disabled with the reason in words while another call is in flight.
 */
function ExecutiveControls({ row, control }: { row: ExecutiveRunRow; control: ExecutiveControlProps }) {
  const actions = executiveRowActions(row);
  if (actions.length === 0) return null;
  const handlers: Record<ExecutiveChipAction, (id: string) => void> = {
    pause: control.onPause,
    resume: control.onResume,
    cancel: control.onCancel,
  };
  const gates = actions.map((action) => ({ action, gate: executiveActionGate(row, action, control.busy) }));
  const inFlight = control.busy !== null && control.busy.id === row.run_id;
  // One sentence per distinct reason, naming every chip it refuses; the one
  // in-flight call disables every chip and is said once.
  const reasons = new Map<string, { actions: ExecutiveChipAction[]; kind: string }>();
  for (const { action, gate } of gates) {
    if (gate.reason === null) continue;
    const entry = reasons.get(gate.reason) ?? { actions: [], kind: gate.reasonKind ?? "" };
    entry.actions.push(action);
    reasons.set(gate.reason, entry);
  }
  return (
    <div
      className="approval-pair"
      data-executive-controls={row.run_id}
      data-executive-in-flight={inFlight ? "yes" : "no"}
      data-executive-in-flight-action={inFlight && control.busy ? control.busy.action : ""}
    >
      {gates.map(({ action, gate }) => (
        <button
          key={action}
          type="button"
          className="core-chip"
          data-executive-action={action}
          data-executive-target={row.run_id}
          data-executive-enabled={gate.enabled ? "yes" : "no"}
          disabled={!gate.enabled}
          onClick={() => handlers[action](row.run_id)}
        >
          {EXECUTIVE_ACTION_LABEL[action]}
        </button>
      ))}
      {Array.from(reasons, ([reason, { actions: refused, kind }]) => (
        <span key={reason} className="approval-reason" data-executive-reason={kind} data-executive-reason-for={refused.join(",")}>
          {kind === "busy" ? reason : `${refused.map((a) => EXECUTIVE_ACTION_LABEL[a]).join(", ")}: ${reason}`}
        </span>
      ))}
    </div>
  );
}

function ExecutiveRowItem({
  row,
  now,
  control,
  details,
}: {
  row: ExecutiveRunRow;
  now: number;
  control: ExecutiveControlProps;
  details: ExecutiveDetailProps;
}) {
  const partial = executiveRowIsPartial(row);
  const failed = executiveRowIsFailed(row);
  // The run's own route is the ONLY source for this sentence. It belongs to the run
  // being looked at, and the list is capped at 50 rows, so carrying it per row would buy
  // an N+1 on every Cockpit poll; the detail hook already asks only for the runs still
  // going (at most two, by the spec's own bound). Nothing is written when it said nothing.
  const explain = details.detailFor(row.run_id)?.explain ?? null;
  return (
    <li
      data-executive-run={row.run_id}
      data-executive-run-state={row.state ?? ""}
      data-executive-run-step={row.step ?? ""}
      data-executive-run-active={executiveRowIsActive(row) ? "yes" : "no"}
      data-executive-run-paused={executiveRowIsPaused(row) ? "yes" : "no"}
      data-executive-run-complete={executiveRowIsComplete(row) ? "yes" : "no"}
      data-executive-run-partial={partial ? "yes" : "no"}
      data-executive-run-failed={failed ? "yes" : "no"}
      data-executive-run-done={row.done ?? ""}
      data-executive-run-total={row.total ?? ""}
    >
      <div className="event-row">
        <span>{row.goal ?? "iş bildirilmedi"}</span>
        <span className="event-when">{when(row.updated_at ?? row.created_at, now)}</span>
      </div>
      {/* The step, the state with what is missing beside a partial run, and the counts — each as the row says it, or the statement that it did not. */}
      <span className="muted" data-executive-line>
        {executiveRowLine(row)}
      </span>
      {/* The current step in one sentence, exactly as the route sent it. */}
      {explain && (
        <span className="muted" data-executive-explain>
          {explain}
        </span>
      )}
      <ExecutiveControls row={row} control={control} />
    </li>
  );
}

/**
 * Görevler (M26 spec §6): what the executive is doing, from the bus, and
 * the runs that exist, from `/v1/executive/runs` — each run with the
 * owner's own words for it, its state, the step it is on, how many of its
 * steps are done and, for a run that ended partly, what is missing; plus
 * the current step's explanation from the run's own route, and "Duraklat /
 * Devam / İptal" asking the Cloud Core for its own signals.
 *
 * Two sources, kept apart because they answer different questions. The bus
 * line is "what is happening now" and decays like every bus claim; the rows
 * are "what exists" and are the route's. Nothing here plans a graph, runs a
 * step or ends a run: a run is paused because its row says so, a chip is
 * drawn because the Cloud Core would not refuse it and enabled because
 * nothing else is in flight — and the Cloud Core still decides on its own
 * terms. The empty sentence is the route's answer, never the bus's silence
 * — and "henüz yok" (no route on this Cloud Core) is neither. Only a
 * `completed` row reads as done; a `partial` one names what is missing.
 */
export function ExecutivePanel({
  runs,
  truth,
  now,
  control,
  details,
}: {
  runs: Loaded<ExecutiveRunRow[]>;
  truth: CoreTruth;
  now: number;
  control: ExecutiveControlProps;
  details: ExecutiveDetailProps;
}) {
  const view = executiveView(executiveClaim(truth, now));
  const told = view.lastKnown !== null;
  const rows = runs.kind === "ok" ? runs.value : [];
  const shown = rows.slice(0, EXECUTIVE_ROWS_SHOWN);
  const active = rows.filter(executiveRowIsActive).length;
  // A partial run and a failed one are the two the owner is owed a look at:
  // one ended with something missing, the other stopped. A completed run
  // needs no attention, and a paused one is where the owner put it.
  const attention = rows.some((row) => executiveRowIsPartial(row) || executiveRowIsFailed(row));
  return (
    <section
      className={`panel ${attention ? "attention" : ""}`}
      data-panel="executive"
      data-panel-state={runs.kind}
      data-panel-empty={runs.kind === "ok" ? (rows.length ? "no" : "yes") : ""}
      data-executive-stage={view.stage}
      data-executive-last-known={view.lastKnown ?? ""}
      data-executive-posture={told ? view.posture : ""}
      data-executive-active={runs.kind === "ok" ? active : ""}
    >
      <h3 className="panel-title">
        <span>Görevler</span>
        {runs.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {active > 0 ? `${active} sürüyor / ${rows.length}` : `${rows.length}`}
          </span>
        )}
      </h3>
      {/* The bus: the caption the Core draws, with its age; last-known when it aged out. */}
      <p
        className={told ? "muted" : "panel-empty"}
        data-executive-activity={told ? view.stage : "untold"}
        data-executive-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : EXECUTIVE_UNTOLD}
      </p>
      <LoadedNotice state={runs} />
      {runs.kind === "ok" && rows.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {EXECUTIVE_EMPTY}
        </p>
      )}
      {shown.length > 0 && (
        <ul>
          {shown.map((row) => (
            <ExecutiveRowItem key={row.run_id} row={row} now={now} control={control} details={details} />
          ))}
        </ul>
      )}
      {details.notice && (
        <p className="panel-unknown" data-executive-detail-notice>
          {details.notice}
        </p>
      )}
      {control.outcome && (
        <p
          className={`approval-outcome ${control.outcome.ok ? "muted" : "panel-unknown"}`}
          data-executive-outcome={control.outcome.action}
          data-executive-ok={control.outcome.ok ? "yes" : "no"}
          data-executive-target={control.outcome.id}
        >
          {control.outcome.text}
          {` · ${formatAge(Math.max(0, now - control.outcome.at))}`}
        </p>
      )}
      <p className="muted" data-executive-note>
        {EXECUTIVE_NOTE}
      </p>
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

// ------------------------------------- M27: the Creative Tools Operator

/** What the panel says under the rows: what the two chips ask for, and what this page cannot do. */
const CREATIVE_NOTE =
  '"Dışa aktar" ve "Karşılaştır" Cloud Core\'dan uygulamanın en yapısal arayüzünü ister: Paint için belgenin kendisi (dosya, Pillow ile), Photoshop ve Illustrator için sabit sürücü dosyasının okuduğu JSON plan, Figma için REST. Çıktı sahibin özgün dosyasının yanına YENİ bir dosya olarak yazılır; özgün dosyanın üzerine yazılmaz ve hiçbir şey silinmez. Bir görsel, bağımsız bir okuyucuyla açılıp istenenle karşılaştırıldığında doğrulanmış olur; önce değil. Kurulu olmayan bir uygulama için düğme gösterilmez, taklit de edilmez. Bu ekran uygulama açmaz, fare kullanmaz, piksel çizmez, kod üretmez.';

/** Said under a row that has a picture the page has not fetched yet. */
const CREATIVE_IMAGE_PENDING = "Görsel var; henüz alınmadı.";

/**
 * One side of one run's pictures.
 *
 * A plain `<img>` on purpose, and `next/image` deliberately not used: `src`
 * here is a `blob:` URL made in this tab from bytes fetched through the
 * owner session (the route is gated, so an optimizer that re-fetched the URL
 * server-side would be answered with a 401 and could not read a blob of this
 * tab's anyway). The dimensions are the file's, not this page's — an image
 * is up to 8192×8192 (M27 spec §7) and the CSS bounds it without changing
 * its proportions, because the owner reads geometry off it.
 */
function CreativeImage({
  row,
  side,
  src,
}: {
  row: CreativeRunRow;
  side: CreativeImageSide;
  src: string;
}) {
  const sha = (side === "before" ? row.before_sha256 : row.after_sha256) ?? "";
  /* eslint-disable-next-line next/no-img-element */
  return <img className="creative-image" src={src} alt={creativeImageAlt(row, side)} data-creative-image={row.run_id} data-creative-image-side={side} data-creative-image-sha={sha} />;
}

/**
 * The before and the after, side by side, each drawn ONLY because the row
 * says that picture exists and only once its bytes are in hand. A row with
 * one of the two draws one: "there is no output yet" is a true thing to
 * show, and pairing the source with a blank would say the opposite.
 */
function CreativeImages({ row, preview }: { row: CreativeRunRow; preview: CreativePreviewProps }) {
  const sides = creativeRowSides(row);
  if (sides.length === 0) return null;
  return (
    <div className="creative-images" data-creative-images={row.run_id} data-creative-image-count={sides.length}>
      {sides.map((side) => {
        const src = preview.srcFor(row.run_id, side);
        return (
          <figure key={side} data-creative-figure={side}>
            <figcaption className="muted">{CREATIVE_SIDE_LABEL[side]}</figcaption>
            {src ? (
              <CreativeImage row={row} side={side} src={src} />
            ) : (
              <span className="muted" data-creative-image-pending={side}>
                {CREATIVE_IMAGE_PENDING}
              </span>
            )}
          </figure>
        );
      })}
    </div>
  );
}

/**
 * The chips under one run: "Dışa aktar" for a run whose step this build can
 * read, "Karşılaştır" only ALSO when there is an output to measure, NEITHER
 * for an application that could not be driven — each drawn only when the
 * Cloud Core would not refuse it, and disabled with the reason in words
 * while another call is in flight.
 */
function CreativeControls({ row, control }: { row: CreativeRunRow; control: CreativeControlProps }) {
  const actions = creativeRowActions(row);
  if (actions.length === 0) return null;
  const handlers: Record<CreativeAction, (id: string) => void> = {
    export: control.onExport,
    compare: control.onCompare,
  };
  const gates = actions.map((action) => ({ action, gate: creativeActionGate(row, action, control.busy) }));
  const inFlight = control.busy !== null && control.busy.id === row.run_id;
  // One sentence per distinct reason, naming every chip it refuses; the one
  // in-flight call disables every chip and is said once.
  const reasons = new Map<string, { actions: CreativeAction[]; kind: string }>();
  for (const { action, gate } of gates) {
    if (gate.reason === null) continue;
    const entry = reasons.get(gate.reason) ?? { actions: [], kind: gate.reasonKind ?? "" };
    entry.actions.push(action);
    reasons.set(gate.reason, entry);
  }
  return (
    <div
      className="approval-pair"
      data-creative-controls={row.run_id}
      data-creative-in-flight={inFlight ? "yes" : "no"}
      data-creative-in-flight-action={inFlight && control.busy ? control.busy.action : ""}
    >
      {gates.map(({ action, gate }) => (
        <button
          key={action}
          type="button"
          className="core-chip"
          data-creative-action={action}
          data-creative-target={row.run_id}
          data-creative-enabled={gate.enabled ? "yes" : "no"}
          disabled={!gate.enabled}
          onClick={() => handlers[action](row.run_id)}
        >
          {CREATIVE_ACTION_LABEL[action]}
        </button>
      ))}
      {Array.from(reasons, ([reason, { actions: refused, kind }]) => (
        <span
          key={reason}
          className="approval-reason"
          data-creative-reason={kind}
          data-creative-reason-for={refused.join(",")}
        >
          {kind === "busy" ? reason : `${refused.map((a) => CREATIVE_ACTION_LABEL[a]).join(", ")}: ${reason}`}
        </span>
      ))}
    </div>
  );
}

function CreativeRunItem({
  row,
  now,
  control,
  preview,
}: {
  row: CreativeRunRow;
  now: number;
  control: CreativeControlProps;
  preview: CreativePreviewProps;
}) {
  const unavailable = creativeRowIsUnavailable(row);
  const failed = creativeRowIsFailed(row);
  const files = creativeFilesLine(row);
  const round = creativeRoundPhrase(row);
  return (
    <li
      className="creative-run"
      data-creative-run={row.run_id}
      data-creative-row-tool={row.tool ?? ""}
      data-creative-row-source={row.source ?? ""}
      data-creative-row-output={row.output ?? ""}
      data-creative-row-operation={row.operation ?? ""}
      data-creative-row-state={row.state ?? ""}
      data-creative-row-similarity={row.similarity ?? ""}
      data-creative-row-defect={row.defect ?? ""}
      data-creative-row-round={row.round ?? ""}
      data-creative-row-verified={creativeRowIsVerified(row) ? "yes" : "no"}
      data-creative-row-unavailable={unavailable ? "yes" : "no"}
      data-creative-row-failed={failed ? "yes" : "no"}
      data-creative-row-has-before={creativeRowHasImage(row, "before") ? "yes" : "no"}
      data-creative-row-has-after={creativeRowHasImage(row, "after") ? "yes" : "no"}
    >
      <div className="event-row">
        <span>{files ?? "dosya adı bildirilmedi"}</span>
        <span className="event-when">{when(row.updated_at ?? row.created_at, now)}</span>
      </div>
      {/* The application, the operation and the step, with the similarity
          beside `verified` and the defect beside `mismatch` — each as the row
          says it, or the statement that it did not. */}
      <span className="muted" data-creative-line>
        {creativeRowLine(row)}
      </span>
      {/* What the COMPARISON measured: the produced dimensions, the bounded
          aggregate (not SSIM — ADR-0093 decision 4) and the defect it named.
          Drawn only for a row that has a comparison to report; a run still
          executing has measured nothing yet. */}
      {creativeHasMetrics(row) && (
        <span className="muted" data-creative-metrics={row.similarity ?? ""}>
          {creativeMetricsLine(row)}
        </span>
      )}
      {/* Which of the ≤ 3 correction rounds the run is on, when one ran. */}
      {round && (
        <span className="muted" data-creative-round={row.round ?? ""}>
          {round}
        </span>
      )}
      {/* The run's own sentence, beside the two steps that have one to give. */}
      {(unavailable || failed) && row.error_message && (
        <span className="muted" data-creative-error-message>
          {row.error_message}
        </span>
      )}
      {/* The before and the after, fetched through the owner session and
          shown only because the ROW says they exist. Until the bytes are in
          hand the row says that, rather than drawing a picture that is not
          there. */}
      <CreativeImages row={row} preview={preview} />
      <CreativeControls row={row} control={control} />
    </li>
  );
}

/**
 * Yaratıcı (M27 spec §3, §6): what the Creative Tools Operator is doing,
 * from the bus, and the runs that exist, from `/v1/creative/runs` — each run
 * with its application, the operation it is on and the step it reached, what
 * the COMPARISON measured (the produced dimensions, the bounded aggregate,
 * the defect it named), which correction round it is on, and the owner's
 * original beside what was produced from it, both fetched through the owner
 * session; "Dışa aktar" and "Karşılaştır" ask the Cloud Core.
 *
 * Two sources, kept apart because they answer different questions. The bus
 * line is "what is happening now" and decays like every bus claim; the rows
 * are "what exists" and are the route's. Nothing here opens an application,
 * draws a pixel or writes a plan: a run is verified because its row says the
 * comparison matched, a picture is drawn because the row says it exists, a
 * chip is drawn because there is an application to ask — and the Cloud Core
 * still refuses on its own terms. An `unavailable` row gets no chips at all
 * and says why in the application's own words: ADR-0093 decision 3's honest
 * form, never a control over an application this machine does not have. The
 * empty sentence is the route's answer, never the bus's silence — and
 * "henüz yok" (no route on this Cloud Core) is neither.
 */
export function CreativePanel({
  runs,
  truth,
  now,
  control,
  preview,
}: {
  runs: Loaded<CreativeRunRow[]>;
  truth: CoreTruth;
  now: number;
  control: CreativeControlProps;
  preview: CreativePreviewProps;
}) {
  const view = creativeView(creativeClaim(truth, now));
  const told = view.lastKnown !== null;
  const rows = runs.kind === "ok" ? runs.value : [];
  const shown = rows.slice(0, CREATIVE_ROWS_SHOWN);
  const verified = rows.filter(creativeRowIsVerified).length;
  const attention = rows.some((row) => creativeRowIsMismatch(row) || creativeRowIsFailed(row));
  return (
    <section
      className={`panel ${attention ? "attention" : ""}`}
      data-panel="creative"
      data-panel-state={runs.kind}
      data-panel-empty={runs.kind === "ok" ? (rows.length ? "no" : "yes") : ""}
      data-creative-stage={view.stage}
      data-creative-last-known={view.lastKnown ?? ""}
      data-creative-posture={told ? view.posture : ""}
      data-creative-verified={runs.kind === "ok" ? verified : ""}
    >
      <h3 className="panel-title">
        <span>Yaratıcı</span>
        {runs.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {verified > 0 ? `${verified} doğrulandı / ${rows.length}` : `${rows.length}`}
          </span>
        )}
      </h3>
      {/* The bus: the caption the Core draws, with its age; last-known when it aged out. */}
      <p
        className={told ? "muted" : "panel-empty"}
        data-creative-activity={told ? view.stage : "untold"}
        data-creative-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : CREATIVE_UNTOLD}
      </p>
      <LoadedNotice state={runs} />
      {runs.kind === "ok" && rows.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {CREATIVE_EMPTY}
        </p>
      )}
      {shown.length > 0 && (
        <ul>
          {shown.map((row) => (
            <CreativeRunItem key={row.run_id} row={row} now={now} control={control} preview={preview} />
          ))}
        </ul>
      )}
      {preview.notice && (
        <p className="panel-unknown" data-creative-image-notice>
          {preview.notice}
        </p>
      )}
      {control.outcome && (
        <p
          className={`approval-outcome ${control.outcome.ok ? "muted" : "panel-unknown"}`}
          data-creative-outcome={control.outcome.action}
          data-creative-ok={control.outcome.ok ? "yes" : "no"}
          data-creative-target={control.outcome.id}
        >
          {control.outcome.text}
          {` · ${formatAge(Math.max(0, now - control.outcome.at))}`}
        </p>
      )}
      <p className="muted" data-creative-note>
        {CREATIVE_NOTE}
      </p>
    </section>
  );
}

function NativeBuildItem({ row, now }: { row: NativeBuildRow; now: number }) {
  const unavailable = nativeRowIsUnavailable(row);
  const failed = nativeRowIsFailed(row);
  const mismatch = nativeRowIsMismatch(row);
  const verdict = nativeVerdictLine(row);
  return (
    <li
      className="native-build"
      data-native-build={row.build_id}
      data-native-row-app={row.app ?? ""}
      data-native-row-target={row.target ?? ""}
      data-native-row-stack={row.stack ?? ""}
      data-native-row-state={row.state ?? ""}
      data-native-row-artifact={row.artifact_name ?? ""}
      data-native-row-bytes={row.artifact_bytes ?? ""}
      data-native-row-sha256={row.artifact_sha256 ?? ""}
      data-native-row-verdict={row.verdict_ok === null ? "" : row.verdict_ok ? "ok" : "mismatch"}
      data-native-row-verified={nativeRowIsVerified(row) ? "yes" : "no"}
      data-native-row-unavailable={unavailable ? "yes" : "no"}
      data-native-row-failed={failed ? "yes" : "no"}
      data-native-row-has-artifact={nativeRowHasArtifact(row) ? "yes" : "no"}
    >
      <div className="event-row">
        <span>{nativeIdentityLine(row)}</span>
        <span className="event-when">{when(row.updated_at ?? row.created_at, now)}</span>
      </div>
      {/* The step, exactly as the row named it — "derleniyor", "doğrulandı",
          "bu makinede yapılamıyor" — or the token verbatim for a word this
          build cannot read. */}
      <span className="muted" data-native-line>
        {nativeRowLine(row)}
      </span>
      {/* The artefact, drawn only because the ROW named one: its file name,
          its size and the first characters of the sha256 a reader computed
          from the bytes. Never from the spec, and never from a step. */}
      {nativeRowHasArtifact(row) && (
        <span className="muted" data-native-artifact>
          {nativeArtifactLine(row)}
        </span>
      )}
      {/* What the INDEPENDENT reader said. Its own line, because it is its
          own statement: the step says where the build rests, this says what
          something that did not build the file found when it opened it. */}
      {verdict && (
        <span className="muted" data-native-verdict={row.verdict_ok === null ? "untold" : row.verdict_ok ? "ok" : "mismatch"}>
          {verdict}
        </span>
      )}
      {/* Two published versions that disagree, printed side by side on a
          mismatch: what was asked for, and what the reader read out of the
          file. Nothing is decided here — both figures are the row's. */}
      {mismatch && nativeVersionDisagrees(row) && (
        <span className="muted" data-native-version-disagrees="yes">
          {`istenen sürüm ${row.version} · çıktıdaki sürüm ${row.artifact_version}`}
        </span>
      )}
      {/* The build's own sentence, beside the two steps that have one to
          give. An `unavailable` says which toolchain is missing in the Cloud
          Core's words; this page never guesses one. */}
      {(unavailable || failed) && row.error_message && (
        <span className="muted" data-native-error-message>
          {row.error_message}
        </span>
      )}
    </li>
  );
}

/**
 * Yerel Uygulamalar (M28 spec §4, §6): what the Native Application Factory
 * is doing, from the bus, and the builds that exist, from
 * `/v1/native/builds` — each with its application, target and stack, the
 * step it reached, and for a build that produced something the artefact's
 * name, its size, the first characters of its sha256, and what the
 * INDEPENDENT reader said when it opened the file.
 *
 * Two sources, kept apart because they answer different questions. The bus
 * line is "what is happening now" and decays like every bus claim; the rows
 * are "what exists" and are the route's. Every fact drawn here was
 * published: the size is what a reader measured, the hash is what a reader
 * computed, the verdict is what a reader concluded — this page computes
 * nothing and re-reads no file. A build is "doğrulandı" because its row
 * says `verified`, never because an artefact exists or a compiler exited 0;
 * an `unavailable` row says this MACHINE cannot reach that target and is
 * never drawn as a failure (ADR-0095 decision 3).
 *
 * There are no controls at all, unlike every other factory panel. Starting a
 * twenty-minute compiler and installing a signed package are asked for by
 * voice through the ONE router, which gates them; a chip here would be a
 * second authority surface for the same act. The empty sentence is the
 * route's answer, never the bus's silence — and "henüz yok" (no route on
 * this Cloud Core) is neither.
 */
export function NativePanel({
  builds,
  truth,
  now,
}: {
  builds: Loaded<NativeBuildRow[]>;
  truth: CoreTruth;
  now: number;
}) {
  const view = nativeView(nativeClaim(truth, now));
  const told = view.lastKnown !== null;
  const rows = builds.kind === "ok" ? builds.value : [];
  const shown = rows.slice(0, NATIVE_ROWS_SHOWN);
  const verified = rows.filter(nativeRowIsVerified).length;
  const attention = rows.some((row) => nativeRowIsMismatch(row) || nativeRowIsFailed(row));
  return (
    <section
      className={`panel ${attention ? "attention" : ""}`}
      data-panel="native"
      data-panel-state={builds.kind}
      data-panel-empty={builds.kind === "ok" ? (rows.length ? "no" : "yes") : ""}
      data-native-stage={view.stage}
      data-native-last-known={view.lastKnown ?? ""}
      data-native-posture={told ? view.posture : ""}
      data-native-verified={builds.kind === "ok" ? verified : ""}
    >
      <h3 className="panel-title">
        <span>Yerel Uygulamalar</span>
        {builds.kind === "ok" && (
          <span className="panel-count" data-panel-badge>
            {verified > 0 ? `${verified} doğrulandı / ${rows.length}` : `${rows.length}`}
          </span>
        )}
      </h3>
      {/* The bus: the caption the Core draws, with its age; last-known when it aged out. */}
      <p
        className={told ? "muted" : "panel-empty"}
        data-native-activity={told ? view.stage : "untold"}
        data-native-caption={told ? view.caption : ""}
      >
        {told ? `${view.stage === "none" ? "Son bilinen: " : ""}${view.caption} · ${formatAge(view.ageMs)}` : NATIVE_UNTOLD}
      </p>
      <LoadedNotice state={builds} />
      {builds.kind === "ok" && rows.length === 0 && (
        <p className="panel-empty" data-panel-empty-text>
          {NATIVE_EMPTY}
        </p>
      )}
      {shown.length > 0 && (
        <ul>
          {shown.map((row) => (
            <NativeBuildItem key={row.build_id} row={row} now={now} />
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
