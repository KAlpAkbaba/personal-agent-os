/**
 * The UI-state vocabulary, mirrored from `services/api/app/uistate/contract.py`
 * (ADR-0052). This file is a *copy of a contract*, not a second source of truth:
 * the API owns the vocabulary, and `/v1/ui/state/contract` is what the renderer
 * checks itself against at runtime.
 *
 * Two rules govern everything downstream:
 *
 * 1. **A state exists because a subsystem entered it.** Nothing here invents a
 *    state, upgrades one, or keeps one alive past the evidence for it.
 * 2. **Unknown is not idle.** If the renderer has been told nothing, or the
 *    contract has grown a state this build does not know, the honest answer is
 *    "I do not know", never a calm breathing core. A renderer that renders
 *    silence as calm is lying about the most common case.
 */

/** Contract version this client was written against (`CONTRACT_VERSION` in contract.py). */
export const KNOWN_CONTRACT_VERSION = 10;

/**
 * The build marker of the Living Core (M18.3 §12, qualification A). Rendered server-side
 * into `/core`'s document head as `<meta name="pagentos-core-build">` by `app/core/layout.tsx`
 * and onto the Minimal page's root as `data-core-build`, so an owner harness can prove the
 * served build is this one with a plain HTTP fetch and no session. Bump it only when the
 * Core's identity changes in a way an owner qualification must tell apart.
 */
export const CORE_BUILD_ID = "living-core-1";

/**
 * The oldest server contract this build can still read honestly.
 *
 * v3 is purely ADDITIVE over v2 (M18.3 §7): the event shape is unchanged and
 * the only difference is ten new state tokens. v4 is additive over v3 in the
 * same way (M19 spec §4): three `operator.*` tokens and one subsystem. v5 is
 * additive over v4 (M20 spec §3): one `document.analysis` token, one
 * subsystem, and one bounded metadata shape (`refs`) that older publishers
 * never send. v6 is additive over v5 (M21 spec §3): two tokens,
 * `mail.activity` and `calendar.activity`, two subsystems, and metadata made
 * of the short tokens the bus already carried. v7 is additive over v6 (M22
 * spec §6): one token, `artifact.factory`, one subsystem, and metadata of
 * four short tokens (`title`, `format`, `verdict`, `failing_ref`). v8 is
 * additive over v7 (M23 spec §6): one token, `app.factory`, one subsystem,
 * three short tokens (`project`, `state`, `port`) and one bounded structured
 * value (`tests: {passed, failed}`) that older publishers never send. v9 is
 * additive over v8 (M24 spec §8): one token, `capability.genesis`, one
 * subsystem, and metadata of four short tokens (`capability`, `state`,
 * `approval_required`, `error_class`). v10 is additive over v9 (M25 spec §6):
 * one token, `scene.activity`, one subsystem, and metadata of three short
 * tokens (`tool`, `scene`, `state`) and one bounded count (`objects`) that
 * older publishers never send. A v2 to v9 server therefore serves a
 * strict subset of what this build knows, and refusing to draw anything at
 * all because the alarm, operator, document, mail, calendar, artifact, app,
 * genesis or scene states have not shipped yet would be a worse lie than
 * saying so in one line. A server NEWER than this build is a different
 * matter — we do not know its vocabulary, so it stays a mismatch.
 */
export const MIN_SUPPORTED_CONTRACT_VERSION = 2;

export type ContractCompatibility =
  /** The server speaks exactly this build's contract. */
  | "current"
  /** Older, but a subset we can read; the new states simply never arrive. */
  | "older_supported"
  /** Too old, or newer than this build. Nothing is drawn from it. */
  | "unsupported";

export function contractCompatibility(version: number): ContractCompatibility {
  if (version === KNOWN_CONTRACT_VERSION) return "current";
  if (version >= MIN_SUPPORTED_CONTRACT_VERSION && version < KNOWN_CONTRACT_VERSION) {
    return "older_supported";
  }
  return "unsupported";
}

/** Bounds copied from the publisher, used to refuse over-long labels defensively. */
export const MAX_LABEL_CHARS = 64;
export const MAX_METADATA_KEYS = 16;

/**
 * `TAIL_SIZE` in `app/uistate/publisher.py`, and the route's `limit` ceiling.
 * Asking for more is a 422, which on every poll would read as an outage.
 */
export const SERVER_TAIL_SIZE = 64;

/**
 * Every state this build knows how to draw. Ordered as in contract.py.
 *
 * The API may legitimately grow past this list (that is a contract change on
 * its side, not a bug here). `classifyState` below routes anything unrecognised
 * to an explicit "unknown state" presentation rather than a default animation.
 */
export const UI_STATES = [
  // the agent itself
  "agent.idle",
  "agent.listening",
  "agent.thinking",
  "agent.speaking",
  "agent.researching",
  "agent.memory_retrieval",
  "agent.tool_running",
  "agent.waiting_owner",
  "agent.goal_completed",
  "agent.error",
  // the evolution lab
  "evolution.researching",
  "evolution.designing",
  "evolution.building",
  "evolution.testing",
  "evolution.shadow_ready",
  // v2 — the room. Local perception, and whether the owner is there.
  "eye.active",
  "eye.disabled",
  "owner.present",
  "owner.away",
  "owner.returned",
  "owner.resting",
  "owner.likely_asleep",
  "owner.awake",
  // v2 — acting on time rather than on request.
  "routine.armed",
  "routine.triggered",
  "alarm.triggered",
  // v2 — the owner-authorised release path (ADR-0055), made watchable.
  "release.owner_approval_required",
  "release.owner_authorized",
  "release.qualifying",
  "release.deploying",
  "release.verifying",
  "release.live",
  "release.rollback",
  // v3 (M18.3 §7) — the durable wake alarm's own lifecycle. Its own channel:
  // an alarm ringing is not the assistant thinking, and it must never displace
  // a core that is genuinely working.
  "alarm.armed",
  "alarm.firing",
  "alarm.playing",
  "alarm.greeting",
  "alarm.snoozed",
  "alarm.stopped",
  "alarm.completed",
  "alarm.failed",
  // v3 — display power, an ambient fact about the room's screens. Drawn on the
  // ambient strip and NEVER on the Core: a dark monitor says nothing about
  // what the agent is doing.
  "display.on",
  "display.off",
  // v4 (M19 spec §4) — the Digital Operator acting on the owner's desktop
  // through the companion. Published from real OperatorTask transitions with
  // `{step, capability, window_title}` (and `error_class` on failure). Its
  // own channel, and a CORE one: acting on the desktop is the agent's own
  // work, not a fact about the room.
  "operator.running",
  "operator.verifying",
  "operator.failed",
  // v5 (M20 spec §3) — File & Document Intelligence. Published while the Core
  // reads, extracts, retrieves from or answers about one of the owner's
  // documents, with `{file, part, step?, refs?}`: the file's NAME, the
  // reference of the place inside it (`p3`, `s4`, `sheet:Ozet!A5:B5`, …), the
  // step the Core is on, and — on an answer — the refs it cited. The agent's
  // own work, so it stays on the agent channel and drives the core body.
  "document.analysis",
  // v6 (M21 spec §3) — Mail & Calendar. Published while the Core reads the
  // owner's mail or prepares a draft (`{folder?, subject?, draft_state?}`),
  // and while it reads the owner's calendar or prepares a proposal
  // (`{range?, event?, proposal_state?}`). Reading and preparing are the
  // assistant's own work (ADR-0084 tier READ / PREPARE); the one external
  // mutation each family has — sending, committing — is never entered by this
  // client, which only ever asks the Cloud Core to run its own gate.
  "mail.activity",
  "calendar.activity",
  // v7 (M22 spec §6) — the Artifact Factory. Published while a render is
  // being made and while it is reopened by an independent parser and
  // compared to what was asked, with `{title?, format?, verdict?,
  // failing_ref?}`: the artifact's title, the format in hand, the verdict
  // (`rendering` while the factory works, `valid` when the parser found what
  // was asked, `invalid` when it did not — and then the ref that failed).
  // The agent's own work, so it stays on the agent channel; a render whose
  // validation failed is kept and NAMED, never presented as done (ADR-0085 §3).
  "artifact.factory",
  // v8 (M23 spec §6) — the App Factory. Published while an app the owner
  // asked for is planned, scaffolded into a real project on the owner's
  // machine, run there in a bounded process, tested with its own tests,
  // stopped, or failed, with `{project?, state?, port?, tests?}`: the
  // project's name, the `AppProject` state, the port the bounded process is
  // bound to on `127.0.0.1` while it runs, and the counts its own tests gave.
  // The agent's own work, so it stays on the agent channel; a project exists
  // when it runs and its tests pass, and nothing here says so on its own
  // (ADR-0086).
  "app.factory",
  // v9 (M24 spec §8) — Capability Genesis. Published by `GenesisService` at
  // every transition of ONE genesis run, from its row alone, with
  // `{capability?, state?, approval_required?, error_class?}`: the
  // capability the owner's request needs, the run's state in the §5 names,
  // whether authority parked it for the owner, and — on `failed` — the
  // error class. The agent's own work (it lacks a capability and is
  // acquiring one for the owner), so it stays on the agent channel; the
  // capability EXISTS when the run says `verified`, never before, and
  // nothing here says so on its own (ADR-0087).
  "capability.genesis",
  // v10 (M25 spec §6) — 3D creation. Published while a scene is built,
  // changed, rendered or read back through the tool's OWN scripting
  // interface (`blender.exe -b --python …`, `Unity.exe -batchmode
  // -executeMethod …`), with `{tool?, scene?, state?, objects?}`: which of
  // the two tools is being driven, the scene the plan names, the step the
  // run is on in the §4 names, and how many objects the INSPECTION read
  // back. The agent's own work, so it stays on the agent channel; a scene
  // is "doğrulandı" when the read-back matched the plan and never before,
  // a mismatch is named rather than dressed as done, and a tool that
  // cannot be driven at all (Unity without an entitlement, ADR-0088 §5) is
  // `unavailable` — a settled, honest posture, not an error.
  "scene.activity",
] as const;

export type KnownUiState = (typeof UI_STATES)[number];

/**
 * The wake alarm's lifecycle states (v3), in the order the dispatcher enters
 * them. `alarm.triggered` is deliberately NOT here: it is v2's release-band
 * moment ("a routine fired") and keeps its old meaning and its old place.
 */
export const ALARM_STATES = [
  "alarm.armed",
  "alarm.firing",
  "alarm.playing",
  "alarm.greeting",
  "alarm.snoozed",
  "alarm.stopped",
  "alarm.completed",
  "alarm.failed",
] as const;

export type AlarmUiState = (typeof ALARM_STATES)[number];

const ALARM_STATE_SET: ReadonlySet<string> = new Set(ALARM_STATES);

/** True for a v3 wake-alarm lifecycle state this build knows how to draw. */
export function isAlarmLifecycleState(state: string): state is AlarmUiState {
  return ALARM_STATE_SET.has(state);
}

export const DISPLAY_STATES = ["display.on", "display.off"] as const;

export type DisplayUiState = (typeof DISPLAY_STATES)[number];

/** True for a `display.*` state. Ambient band only, by construction. */
export function isDisplayState(state: string): boolean {
  return state.startsWith("display.");
}

/**
 * The Digital Operator's states (v4), in the order a task passes through
 * them: a step is being acted, its result is being re-observed and verified,
 * or the task failed. There is deliberately no `operator.completed`: a task
 * that finished is reported by the receipt and the ledger, and the Core is
 * then whatever the agent publishes next.
 */
export const OPERATOR_STATES = [
  "operator.running",
  "operator.verifying",
  "operator.failed",
] as const;

export type OperatorUiState = (typeof OPERATOR_STATES)[number];

const OPERATOR_STATE_SET: ReadonlySet<string> = new Set(OPERATOR_STATES);

/**
 * True for a v4 operator state this build knows how to draw.
 *
 * Membership, not prefix: a newer server's `operator.cancelled` must not be
 * drawn as a running operator on the strength of a word this build cannot
 * read (it reaches the Core as `unknown_state`, which is the honest reading).
 */
export function isOperatorState(state: string): state is OperatorUiState {
  return OPERATOR_STATE_SET.has(state);
}

/**
 * The document intelligence's states (v5). One token: the spec publishes the
 * whole read → extract → retrieve → answer loop as `document.analysis` and
 * names the phase in `metadata.step`, so there is nothing else to enumerate.
 * Kept as a list, like the operator's, so a second token lands here and
 * nowhere else.
 */
export const DOCUMENT_STATES = ["document.analysis"] as const;

export type DocumentUiState = (typeof DOCUMENT_STATES)[number];

const DOCUMENT_STATE_SET: ReadonlySet<string> = new Set(DOCUMENT_STATES);

/**
 * True for a v5 document state this build knows how to draw.
 *
 * Membership, not prefix, for the operator's reason: a newer server's
 * `document.indexing` must not be drawn as a reading Core on the strength of
 * a word this build cannot read.
 */
export function isDocumentState(state: string): state is DocumentUiState {
  return DOCUMENT_STATE_SET.has(state);
}

/** The one mail token (v6). Spelled here so every reader names the same wire word. */
export const MAIL_ACTIVITY = "mail.activity";
/** The one calendar token (v6). */
export const CALENDAR_ACTIVITY = "calendar.activity";

/**
 * The mail family's states (v6). One token: the spec publishes every mail
 * read and every draft transition as `mail.activity` and names the phase in
 * `metadata.draft_state`, so — as with the document's — there is nothing
 * else to enumerate. Kept as a list so a second token lands here and nowhere
 * else.
 */
export const MAIL_STATES = [MAIL_ACTIVITY] as const;

export type MailUiState = (typeof MAIL_STATES)[number];

const MAIL_STATE_SET: ReadonlySet<string> = new Set(MAIL_STATES);

/**
 * True for a v6 mail state this build knows how to draw. Membership, not
 * prefix: a newer server's `mail.sent` must not be drawn as a mail read on
 * the strength of a word this build cannot read.
 */
export function isMailState(state: string): state is MailUiState {
  return MAIL_STATE_SET.has(state);
}

/** The calendar family's states (v6), for the mail family's reason. */
export const CALENDAR_STATES = [CALENDAR_ACTIVITY] as const;

export type CalendarUiState = (typeof CALENDAR_STATES)[number];

const CALENDAR_STATE_SET: ReadonlySet<string> = new Set(CALENDAR_STATES);

/** True for a v6 calendar state this build knows how to draw. Membership, not prefix. */
export function isCalendarState(state: string): state is CalendarUiState {
  return CALENDAR_STATE_SET.has(state);
}

/** The one artifact token (v7). Spelled here so every reader names the same wire word. */
export const ARTIFACT_FACTORY = "artifact.factory";

/**
 * The Artifact Factory's states (v7). One token: the spec publishes the
 * whole render → reopen → compare loop as `artifact.factory` and names the
 * phase in `metadata.verdict`, so — as with the document's and the mail's —
 * there is nothing else to enumerate. Kept as a list so a second token lands
 * here and nowhere else.
 */
export const ARTIFACT_STATES = [ARTIFACT_FACTORY] as const;

export type ArtifactUiState = (typeof ARTIFACT_STATES)[number];

const ARTIFACT_STATE_SET: ReadonlySet<string> = new Set(ARTIFACT_STATES);

/**
 * True for a v7 artifact state this build knows how to draw. Membership, not
 * prefix: a newer server's `artifact.deleted` must not be drawn as a making
 * Core on the strength of a word this build cannot read.
 */
export function isArtifactState(state: string): state is ArtifactUiState {
  return ARTIFACT_STATE_SET.has(state);
}

/**
 * A render's verdict as the publisher names it in `metadata.verdict` (M22
 * spec §3, §6): `rendering` while the factory writes the bytes, `valid` once
 * an INDEPENDENT parser reopened them and found every element that was
 * asked for, `invalid` when it did not — and then `failing_ref` names the
 * first element it could not find, in M20's reference scheme. A token
 * outside this list is a word this build cannot read and is shown as the
 * plain state, never as one of these.
 */
export const ARTIFACT_VERDICTS = ["rendering", "valid", "invalid"] as const;

export type ArtifactVerdict = (typeof ARTIFACT_VERDICTS)[number];

const ARTIFACT_VERDICT_SET: ReadonlySet<string> = new Set(ARTIFACT_VERDICTS);

export function isArtifactVerdict(value: unknown): value is ArtifactVerdict {
  return typeof value === "string" && ARTIFACT_VERDICT_SET.has(value);
}

/** The one app token (v8). Spelled here so every reader names the same wire word. */
export const APP_FACTORY = "app.factory";

/**
 * The App Factory's states (v8). One token: the spec publishes the whole
 * plan → scaffold → run → test loop as `app.factory` and names the project's
 * state in `metadata.state`, so — as with the artifact's — there is nothing
 * else to enumerate. Kept as a list so a second token lands here and nowhere
 * else.
 */
export const APP_STATES = [APP_FACTORY] as const;

export type AppUiState = (typeof APP_STATES)[number];

const APP_STATE_SET: ReadonlySet<string> = new Set(APP_STATES);

/**
 * True for a v8 app state this build knows how to draw. Membership, not
 * prefix: a newer server's `app.deleted` must not be drawn as a building
 * Core on the strength of a word this build cannot read.
 */
export function isAppState(state: string): state is AppUiState {
  return APP_STATE_SET.has(state);
}

/**
 * An `AppProject`'s state as the publisher names it in `metadata.state`
 * (M23 spec §1): the row exists (`planned`), its files were written into
 * the device's `Projects` root (`scaffolded`), the companion's bounded child
 * is serving it (`running`), its own tests ran (`tested`), a run or a test
 * failed (`failed`), the job was closed (`stopped`). A token outside this
 * list is a word this build cannot read and is shown as the plain state,
 * never as one of these.
 */
export const APP_PROJECT_STATES = ["planned", "scaffolded", "running", "tested", "failed", "stopped"] as const;

export type AppProjectState = (typeof APP_PROJECT_STATES)[number];

const APP_PROJECT_STATE_SET: ReadonlySet<string> = new Set(APP_PROJECT_STATES);

export function isAppProjectState(value: unknown): value is AppProjectState {
  return typeof value === "string" && APP_PROJECT_STATE_SET.has(value);
}

/**
 * The counts a project's own tests gave (M23 spec §3, `project.test` →
 * `{passed, failed}`), as the publisher sent them and never derived: a
 * `tested` state with no counts is "test edildi" downstream, never
 * "12/12".
 */
export type AppTestCounts = {
  passed: number;
  failed: number;
};

/** The highest port a bounded process may be bound to; anything else is not a port. */
export const MAX_PORT = 65_535;

/**
 * A TCP port the publisher actually sent: a whole number in 1..65535, else
 * `null`. The caption and the Cockpit's link are built from this figure
 * alone, so a `port` of `0`, `-1`, `"8123"` or `70000` yields no address —
 * a link to a port nobody could be listening on would be an invitation to
 * nothing.
 */
export function asPort(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= MAX_PORT ? value : null;
}

/** The one genesis token (v9). Spelled here so every reader names the same wire word. */
export const CAPABILITY_GENESIS = "capability.genesis";

/**
 * Capability Genesis's states (v9). One token: the spec publishes the whole
 * gap → research → build → test → classify → approve → roll out → register
 * → use → verify loop as `capability.genesis` and names the run's state in
 * `metadata.state`, so — as with the app's — there is nothing else to
 * enumerate. Kept as a list so a second token lands here and nowhere else.
 */
export const GENESIS_STATES = [CAPABILITY_GENESIS] as const;

export type GenesisUiState = (typeof GENESIS_STATES)[number];

const GENESIS_STATE_SET: ReadonlySet<string> = new Set(GENESIS_STATES);

/**
 * True for a v9 genesis state this build knows how to draw. Membership, not
 * prefix: a newer server's `capability.revoked` must not be drawn as a
 * capability being acquired on the strength of a word this build cannot read.
 */
export function isGenesisState(state: string): state is GenesisUiState {
  return GENESIS_STATE_SET.has(state);
}

/**
 * A `GenesisRun`'s state as the publisher names it in `metadata.state` (M24
 * spec §5), in the order the run passes through them: the registry could
 * not resolve the request, the interface is being researched, the adapter
 * designed and built, the build tested against the running application,
 * its authority and side effects classified, the run parked for the owner
 * (only when authority requires it), rolled out, registered, then the
 * capability available, used for the ORIGINAL request, and verified by the
 * read-back the description declared — or failed, from any state. A token
 * outside this list is a word this build cannot read and is shown as the
 * plain state, never as one of these.
 */
export const GENESIS_RUN_STATES = [
  "capability_missing",
  "researching",
  "designing",
  "building",
  "testing",
  "classifying",
  "awaiting_approval",
  "rolling_out",
  "registering",
  "available",
  "used",
  "verified",
  "failed",
  "cancelled",
] as const;

export type GenesisRunState = (typeof GENESIS_RUN_STATES)[number];

const GENESIS_RUN_STATE_SET: ReadonlySet<string> = new Set(GENESIS_RUN_STATES);

export function isGenesisRunState(value: unknown): value is GenesisRunState {
  return typeof value === "string" && GENESIS_RUN_STATE_SET.has(value);
}

/** The one 3D token (v10). Spelled here so every reader names the same wire word. */
export const SCENE_ACTIVITY = "scene.activity";

/**
 * The 3D family's states (v10). One token: the spec publishes the whole
 * plan → create → render → inspect → compare loop as `scene.activity` and
 * names the step in `metadata.state`, so — as with the genesis token —
 * there is nothing else to enumerate. Kept as a list so a second token
 * lands here and nowhere else.
 */
export const SCENE_STATES = [SCENE_ACTIVITY] as const;

export type SceneUiState = (typeof SCENE_STATES)[number];

const SCENE_STATE_SET: ReadonlySet<string> = new Set(SCENE_STATES);

/**
 * True for a v10 scene state this build knows how to draw. Membership, not
 * prefix: a newer server's `scene.deleted` must not be drawn as a scene
 * being made on the strength of a word this build cannot read.
 */
export function isSceneState(state: string): state is SceneUiState {
  return SCENE_STATE_SET.has(state);
}

/**
 * The two tools M25 drives, and the only two words `metadata.tool` may
 * carry (M25 spec §2: `tool ∈ {blender, unity}`). A third word is one this
 * build cannot read: it is printed verbatim as a published fact and never
 * translated into one of these, because "Blender" and "Unity" are claims
 * about which program is being driven.
 */
export const SCENE_TOOLS = ["blender", "unity"] as const;

export type SceneTool = (typeof SCENE_TOOLS)[number];

const SCENE_TOOL_SET: ReadonlySet<string> = new Set(SCENE_TOOLS);

export function isSceneTool(value: unknown): value is SceneTool {
  return typeof value === "string" && SCENE_TOOL_SET.has(value);
}

/**
 * The step one scene run is on as the publisher names it in
 * `metadata.state` (M25 spec §4's closed loop, plus the two honest
 * terminal words):
 *
 *   creating    — the plan's `create_scene` is running in the tool
 *   applying    — the plan's other operations are being applied
 *   rendering   — a still is being rendered
 *   inspecting  — the tool is being READ BACK (`out.json`)
 *   verified    — the read-back matched the plan's constraints
 *   mismatch    — the read-back did NOT match; the object is named
 *   unavailable — the tool could not be driven at all (ADR-0088 §5)
 *   failed      — the run stopped
 *
 * A token outside this list is a word this build cannot read and is shown
 * as the plain state, never as one of these — and above all never as
 * `verified`.
 */
export const SCENE_RUN_STATES = [
  "creating",
  "applying",
  "rendering",
  "inspecting",
  "verified",
  "unverified",
  "mismatch",
  "unavailable",
  "failed",
] as const;

export type SceneRunState = (typeof SCENE_RUN_STATES)[number];

const SCENE_RUN_STATE_SET: ReadonlySet<string> = new Set(SCENE_RUN_STATES);

export function isSceneRunState(value: unknown): value is SceneRunState {
  return typeof value === "string" && SCENE_RUN_STATE_SET.has(value);
}

/**
 * How many objects the INSPECTION read back: a whole, non-negative number
 * or `null`. Never rounded into being — a count nobody published is not a
 * count, and `0` is a real answer (an empty scene), which is why it is not
 * folded into `null`.
 */
export function asObjectCount(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/**
 * The metadata a `scene.activity` event may carry (M25 spec §6). Every key
 * is optional on the wire and every value is a short token or a bounded
 * count the bus already admits; nothing here is a scene plan, a `.blend`
 * path, a driver log or the render's bytes — the scene is a row on
 * `/v1/scenes`, which the Cockpit reads from the list route, and the render
 * is fetched from the owner-session-gated render route, never from the bus.
 */
export type SceneActivityMetadata = {
  /** Which of the two tools is being driven; absent when the publisher did not say. */
  tool?: SceneTool;
  /** The scene the plan names (`Kure`), as the run names it. */
  scene?: string;
  /** The step the run is on in the §4 names. Absent while the publisher has nothing to say yet. */
  state?: SceneRunState;
  /** How many objects the inspection read back, when one was read. */
  objects?: number;
};

/**
 * A draft's lifecycle as the publisher names it in `metadata.draft_state`
 * (M21 spec §3 with the read-back step made explicit): prepared by the
 * assistant, read back to the owner, sent on the owner's confirmation, or
 * discarded. A token outside this list is a word this build cannot read and
 * is shown as the plain state, never as one of these.
 */
export const MAIL_DRAFT_STATES = ["prepared", "read_back", "sending", "sent", "discarded"] as const;

export type MailDraftState = (typeof MAIL_DRAFT_STATES)[number];

const MAIL_DRAFT_STATE_SET: ReadonlySet<string> = new Set(MAIL_DRAFT_STATES);

export function isMailDraftState(value: unknown): value is MailDraftState {
  return typeof value === "string" && MAIL_DRAFT_STATE_SET.has(value);
}

/** A proposal's lifecycle in `metadata.proposal_state`: the draft's, with `committed` for `sent`. */
export const CALENDAR_PROPOSAL_STATES = ["prepared", "read_back", "committing", "committed", "discarded"] as const;

export type CalendarProposalState = (typeof CALENDAR_PROPOSAL_STATES)[number];

const CALENDAR_PROPOSAL_STATE_SET: ReadonlySet<string> = new Set(CALENDAR_PROPOSAL_STATES);

export function isCalendarProposalState(value: unknown): value is CalendarProposalState {
  return typeof value === "string" && CALENDAR_PROPOSAL_STATE_SET.has(value);
}

/**
 * The metadata a `mail.activity` event may carry (M21 spec §3). Every key is
 * optional on the wire and every value is a short token the bus already
 * admits; nothing here is a message body, an address list or an attachment.
 */
export type MailActivityMetadata = {
  /** The folder being read (`INBOX`, `Gönderilmiş`, `Arşiv`, …). */
  folder?: string;
  /** The subject of the message or draft in hand. */
  subject?: string;
  /** Where the draft in hand is in its lifecycle. Absent on a plain read. */
  draft_state?: MailDraftState;
};

/**
 * The metadata a `calendar.activity` event may carry (M21 spec §3).
 * `conflicts` is the one number: how many existing events a proposal
 * collides with, as the Core counted them — read only when sent, so a
 * caption never names a count nobody published.
 */
export type CalendarActivityMetadata = {
  /** The range being read (`today`, `tomorrow`, `week`, or a date token). */
  range?: string;
  /** The title of the event or proposal in hand. */
  event?: string;
  /** Where the proposal in hand is in its lifecycle. Absent on a plain read. */
  proposal_state?: CalendarProposalState;
  /** How many conflicts the proposal has, when the Core counted them. */
  conflicts?: number;
};

/**
 * The metadata an `artifact.factory` event may carry (M22 spec §6). Every
 * key is optional on the wire and every value is a short token; nothing
 * here is a spec body, a rendered byte or a validation report — the report
 * is a row on `artifact_renders`, and the Cockpit reads it from the list
 * route, never from the bus.
 */
export type ArtifactFactoryMetadata = {
  /** The artifact's title, as the owner named it. */
  title?: string;
  /** The format in hand (`xlsx`, `pdf`, `docx`, `pptx`, `csv`, `json`, `html`, `md`, `txt`). */
  format?: string;
  /** Where the render is in its loop. Absent while the publisher has nothing to say yet. */
  verdict?: ArtifactVerdict;
  /** On `invalid`: the ref of the first element the parser could not find (`sheet:Ozet!B5`, `p3`, `s4`, `h2:Giriş`). */
  failing_ref?: string;
};

/**
 * The metadata an `app.factory` event may carry (M23 spec §6). Every key is
 * optional on the wire; `project`, `state` and `port` are short tokens the
 * bus already admits, and `tests` is the one structured value this family
 * adds — two counts, read only through `parseAppTestCounts` at the boundary
 * and carried on `UiStateEvent.tests`, as `refs` is for v5. Nothing here is
 * a file list, a log tail or a spec body: the project's rows are on
 * `/v1/apps`, which the Cockpit reads from the list route, never from the bus.
 */
export type AppFactoryMetadata = {
  /** The project's name, as the owner named it (`Görev Takip`). */
  project?: string;
  /** The `AppProject` state. Absent while the publisher has nothing to say yet. */
  state?: AppProjectState;
  /** The port the bounded process is bound to on `127.0.0.1`, while `running`. */
  port?: number;
  /** The counts the project's own tests gave, when a test run was published. */
  tests?: AppTestCounts;
};

/**
 * The metadata a `capability.genesis` event may carry (M24 spec §8). Every
 * key is optional on the wire and every value is a short token or a flag
 * the bus already admits; nothing here is an interface description, a
 * rendered adapter or a sandbox log — the run is a row on `genesis_runs`,
 * which the Cockpit reads from `/v1/genesis/runs`, never from the bus.
 */
export type CapabilityGenesisMetadata = {
  /** The capability the request needs (`counterbox.increment`), as the run names it. */
  capability?: string;
  /** The run's state in the §5 names. Absent while the publisher has nothing to say yet. */
  state?: GenesisRunState;
  /** True when authority parked the run for the owner (a mutation on an unauthorized asset). */
  approval_required?: boolean;
  /** On `failed`: the taxonomy class (`dependency_unavailable`, `validation_error`, `postcondition_failed`, …). */
  error_class?: string;
};

/**
 * States that belong to the release band's own vocabulary.
 *
 * `releaseClaim` reads exactly these rather than "the newest event on the
 * release channel", because v3 put the alarm lifecycle on the same channel and
 * an alarm ringing must not blank a deployment that is genuinely in flight.
 */
export function isReleaseBandState(state: string): boolean {
  return (
    state.startsWith("release.") || state.startsWith("routine.") || state === "alarm.triggered"
  );
}

const KNOWN_STATES: ReadonlySet<string> = new Set(UI_STATES);

export function isKnownState(state: string): state is KnownUiState {
  return KNOWN_STATES.has(state);
}

/** Which subsystem published a state (SUBSYSTEMS in contract.py). */
export const SUBSYSTEMS = [
  "voice",
  "research",
  "browser",
  "memory",
  "experience",
  "goal",
  "cognitive",
  "self_model",
  "evolution",
  "deployment",
  "ledger",
  "system",
  "presence",
  // v3: the routine engine publishes the alarm lifecycle, and the ambient
  // policy engine publishes display power.
  "routine",
  "ambient",
  // v4: the Digital Operator (M19 spec §4) publishes its task transitions.
  "operator",
  // v5: the document intelligence (M20 spec §3) publishes `document.analysis`;
  // its receipts and ledger rows carry the same subsystem name.
  "documents",
  // v6: mail and calendar (M21 spec §3) publish their activity under their
  // own names, which are also their receipt subsystems.
  "mail",
  "calendar",
  // v7: the Artifact Factory (M22 spec §6) publishes `artifact.factory`;
  // its receipts and ledger rows carry the same subsystem name.
  "artifacts",
  // v8: the App Factory (M23 spec §6) publishes `app.factory`; its receipts
  // and ledger rows carry the same subsystem name.
  "apps",
  // v9: Capability Genesis (M24 spec §6, §8) publishes `capability.genesis`;
  // its receipts and ledger rows (`genesis.<state>`) carry the same name.
  "genesis",
  // v10: 3D creation (M25 spec §5, §6) publishes `scene.activity`; its
  // receipts, its voice corpus category and its ledger rows carry the same
  // name.
  "creative3d",
] as const;

export type Subsystem = (typeof SUBSYSTEMS)[number];

export const SEVERITIES = ["info", "notice", "warning", "critical"] as const;
export type Severity = (typeof SEVERITIES)[number];

export function isSeverity(value: unknown): value is Severity {
  return typeof value === "string" && (SEVERITIES as readonly string[]).includes(value);
}

/**
 * Metadata values as the publisher permits them: numbers, bools and short
 * tokens. Anything else was already dropped server-side (`_clean_metadata`);
 * this type exists so no consumer can accidentally type a transcript into it.
 */
export type MetadataValue = number | boolean | string;

/**
 * One reference an answer cited (M20 spec §3): the place inside a document
 * (`ref`, in the reference scheme of §2), the file it is in (`path`, by
 * identity — the spec names the path whenever two documents share a title),
 * and the excerpt the answer rests on. Each string is bounded like every
 * other token on the bus; a ref without a `ref` is not a reference and is
 * dropped at the boundary.
 */
export type DocumentRef = {
  ref: string;
  path: string | null;
  excerpt: string | null;
};

/**
 * How many refs one event may carry into the client. A client bound, not a
 * contract figure: the retriever chooses top-k blocks and the spec does not
 * fix k, so this only keeps a misbehaving publisher from filling the tail.
 */
export const MAX_DOCUMENT_REFS = 8;

/**
 * One event exactly as `UiStateEvent.as_dict()` serialises it.
 *
 * `intensity` is the publisher's declared "how much is going on" in 0..1 — for
 * voice it is derived from levels the client already reported. It is NOT an audio
 * sample and must never be described to the owner as one, and it is NOT a
 * confidence: presence publishes its confidence as its own metadata figure,
 * because certainty and activity are different quantities.
 *
 * `progress` is `null` whenever the publisher does not know it, and a renderer
 * must not draw a bar for work of unknown length (ADR-0052 §2).
 */
export type UiStateEvent = {
  event_id: string;
  sequence: number;
  state: string;
  subsystem: string;
  /** ISO-8601 UTC, `Z`-suffixed. */
  at: string;
  intensity: number | null;
  progress: number | null;
  severity: string;
  status: string | null;
  task_id: string | null;
  goal_id: string | null;
  module_id: string | null;
  session_id: string | null;
  label: string | null;
  metadata: Record<string, MetadataValue>;
  /**
   * v5: the refs a `document.analysis` answer cited, read from
   * `metadata.refs` alone (M20 spec §3). Present only when the publisher sent
   * at least one well-formed ref, so a v4 event parses byte for byte as it
   * did before; every other list or object in metadata is still dropped as
   * content-shaped.
   */
  refs?: DocumentRef[];
  /**
   * v8: the counts an `app.factory` event's test run gave, read from
   * `metadata.tests` alone (M23 spec §6). Present only when the publisher
   * sent both counts as whole non-negative numbers, so a v7 event parses
   * byte for byte as it did before; the object itself stays out of
   * `metadata`, like `refs`.
   */
  tests?: AppTestCounts;
};

/** The body of `GET /v1/ui/state`. */
export type UiStateResponse = {
  contract_version: number;
  current: UiStateEvent | null;
  events: UiStateEvent[];
  sequence: number;
};

/** The body of `GET /v1/ui/state/contract`. */
export type UiStateContract = {
  contract_version: number;
  states: string[];
  subsystems: string[];
  severities: string[];
  metadata_rules: {
    max_keys: number;
    value_kinds: string[];
    forbidden: string;
  };
  intensity: string;
  progress: string;
};

// --------------------------------------------------------------- state kinds

/**
 * How long a state's claim stays true after the event that made it.
 *
 * This is the single most important honesty decision in the client. The bus
 * publishes *entries* into states, never exits: nothing ever says "the agent
 * stopped thinking". So a `thinking` event is a statement about a moment, and
 * the only truthful thing to do with an old one is stop claiming it.
 *
 * - `steady`  — the state describes a condition that persists until something
 *               else is published (idle, blocked on the owner, failed, a
 *               candidate sitting at SHADOW_READY). Never expires.
 * - `transient` — the state describes work in flight. If no newer event has
 *               arrived within `TRANSIENT_TTL_MS`, the client stops claiming it
 *               and shows "last known" instead. It does NOT fall back to idle:
 *               we were not told the work stopped, only that we stopped hearing.
 * - `moment`  — a thing that happened at an instant (a goal completing). Shown
 *               prominently for `MOMENT_TTL_MS`, then treated as last-known.
 * - `observation` — v2. A statement about the *room* from local perception: the
 *               owner was present, was likely asleep. These decay, and the
 *               decay is the honest part — a camera observation from forty
 *               minutes ago is not evidence about now, so it expires to
 *               unknown rather than to "still present" (M18 spec §1).
 * - `operation` — v2. A stage of a long, watched operation (a release
 *               qualifying, deploying, verifying). Minutes are normal here, so
 *               a 12-second transient TTL would report a healthy deployment as
 *               lost; but it still expires, because a `deploying` from
 *               yesterday is not a deployment happening now.
 */
export type StateKind = "steady" | "transient" | "moment" | "observation" | "operation";

const STATE_KINDS: Record<KnownUiState, StateKind> = {
  "agent.idle": "steady",
  "agent.listening": "transient",
  "agent.thinking": "transient",
  "agent.speaking": "transient",
  "agent.researching": "transient",
  "agent.memory_retrieval": "transient",
  "agent.tool_running": "transient",
  "agent.waiting_owner": "steady",
  "agent.goal_completed": "moment",
  "agent.error": "steady",
  "evolution.researching": "transient",
  "evolution.designing": "transient",
  "evolution.building": "transient",
  "evolution.testing": "transient",
  "evolution.shadow_ready": "steady",
  // The eye is either running or it is not; that holds until something changes it.
  "eye.active": "steady",
  "eye.disabled": "steady",
  // Presence decays. Every one of these is an inference from evidence with an age.
  "owner.present": "observation",
  "owner.away": "observation",
  "owner.returned": "moment",
  "owner.resting": "observation",
  "owner.likely_asleep": "observation",
  "owner.awake": "observation",
  // An armed routine stays armed; firing is an instant.
  "routine.armed": "steady",
  "routine.triggered": "moment",
  "alarm.triggered": "moment",
  // The release path: waiting-on-owner and terminal stages hold, work stages decay.
  "release.owner_approval_required": "steady",
  "release.owner_authorized": "steady",
  "release.qualifying": "operation",
  "release.deploying": "operation",
  "release.verifying": "operation",
  "release.live": "steady",
  "release.rollback": "steady",
  // v3. An armed alarm is a standing arrangement; the ringing states are a
  // watched operation; the terminal states are moments. All of them are bounded
  // by `STATE_TTL_MS` below, because "armed" from three days ago is not an
  // alarm that is armed now.
  "alarm.armed": "steady",
  "alarm.firing": "operation",
  "alarm.playing": "operation",
  "alarm.greeting": "operation",
  "alarm.snoozed": "moment",
  "alarm.stopped": "moment",
  "alarm.completed": "moment",
  "alarm.failed": "moment",
  // The display is on or off until something changes it.
  "display.on": "steady",
  "display.off": "steady",
  // v4. A step being acted or verified is work in flight and decays like any
  // other transient (with its own horizon, `OPERATOR_STEP_TTL_MS`); a failed
  // task holds until something newer is published, exactly as `agent.error`
  // does — the owner is not told a failure went away because time passed.
  "operator.running": "transient",
  "operator.verifying": "transient",
  "operator.failed": "steady",
  // v5. Reading a document is work in flight: it decays on its own horizon
  // (`DOCUMENT_STEP_TTL_MS`), and a Core that stopped hearing about it says
  // last-known, never "finished" and never idle.
  "document.analysis": "transient",
  // v6. A mail or calendar activity is work in flight on the same footing: a
  // provider round trip bounded by a timeout, published once per step. A
  // draft waiting on the owner is a ROW, not an activity — the Cockpit reads
  // it from `/v1/mail/drafts/pending`, which does not expire — so the bus
  // claim decays on the activity horizon and the publisher's `ttl_s` may
  // lengthen it for a standing condition.
  "mail.activity": "transient",
  "calendar.activity": "transient",
  // v7. Making a file and reopening it with an independent parser is work
  // in flight, bounded like a device round trip (`ARTIFACT_FACTORY_TTL_MS`).
  // A verdict is a MOMENT the publisher named — `valid` or `invalid` — and it
  // decays like the rest: what the factory said a minute ago is last-known,
  // and the render itself is a ROW the Cockpit reads from the list route,
  // which does not expire. The claim never falls to "finished" or idle.
  "artifact.factory": "transient",
  // v8. Scaffolding, running and testing a project on the device is work in
  // flight on the same footing (`APP_FACTORY_TTL_MS`). A `running` app may
  // serve for half an hour, but the bus claim is about the MOMENT it was
  // published: the app's state and port are a ROW the Cockpit reads from
  // `/v1/apps`, which does not expire, and the publisher's `ttl_s` may
  // lengthen a standing condition. The claim never falls to "finished" or idle.
  "app.factory": "transient",
  // v9. Researching, building, testing and rolling out an adapter is work in
  // flight on the same footing (`GENESIS_TTL_MS`). A run parked at
  // `awaiting_approval` is a standing condition, but the bus claim is about
  // the MOMENT it was published: the run itself is a ROW on
  // `/v1/genesis/runs`, which does not expire and is where "Onayla" lives,
  // and the publisher's `ttl_s` may lengthen the claim. The claim never
  // falls to "finished", "verified" or idle.
  "capability.genesis": "transient",
  // v10. Driving Blender or Unity in batch mode is work in flight on the
  // same footing (`SCENE_TTL_MS`), and a headless render is the longest of
  // any of them. A `verified` or an `unavailable` is a standing fact about
  // a scene, but the bus claim is about the MOMENT it was published: the
  // scene itself is a ROW on `/v1/scenes`, which does not expire, and the
  // Cockpit's render image comes from that row. The claim never falls to
  // "finished", "doğrulandı" or idle.
  "scene.activity": "transient",
};

/**
 * How long an operator step may be claimed as current without a newer event.
 *
 * The publisher speaks once per task transition, not on a heartbeat, and the
 * companion's per-command cap is 30 s (M19 spec §3; `app.launch` waits up to
 * 15 s for a window to appear). The twelve-second transient horizon would
 * report a healthy twenty-second step as lost, so the step's own horizon is
 * the cap plus a margin. It is still a horizon: a `running` from a minute ago
 * is drawn as last-known, never as a hand still on the mouse. The publisher's
 * own `ttl_s` beats this figure, as it beats every figure here.
 */
export const OPERATOR_STEP_TTL_MS = 45_000;

/**
 * How long a document step may be claimed as current without a newer event.
 *
 * The same reasoning as the operator's horizon, because the same companion
 * does the reading: `document.extract` runs on the device under the 30 s
 * per-command cap (M20 spec §2 bounds a search at 10 s and an extraction at
 * 64 KB / 200 pages) and the publisher speaks once per step, not on a
 * heartbeat. Twelve seconds would report a healthy twenty-second PDF as lost.
 * Still a horizon: a `document.analysis` from a minute ago is last-known,
 * never a Core still reading. The publisher's own `ttl_s` beats this figure.
 */
export const DOCUMENT_STEP_TTL_MS: number = OPERATOR_STEP_TTL_MS;

/** The Core's one wording for a document analysis with no published file: every label
 *  and caption spells it from here, so a wording change lands once. */
export const DOCUMENT_CAPTION_BARE = "Belge inceleniyor";

/**
 * How long a mail or calendar activity may be claimed as current without a
 * newer event. The operator's horizon, for the document's reason: the Cloud
 * Core speaks once per step, not on a heartbeat, and one IMAP fetch or one
 * CalDAV REPORT can outlast the twelve-second transient. Still a horizon: an
 * activity from a minute ago is last-known, never a Core still reading. The
 * publisher's own `ttl_s` beats this figure.
 */
export const MAIL_ACTIVITY_TTL_MS: number = OPERATOR_STEP_TTL_MS;
export const CALENDAR_ACTIVITY_TTL_MS: number = OPERATOR_STEP_TTL_MS;

/** The Core's one wording for a mail activity whose metadata named nothing (v6). */
export const MAIL_CAPTION_BARE = "Posta okunuyor";
/** The Core's one wording for a calendar activity whose metadata named nothing (v6). */
export const CALENDAR_CAPTION_BARE = "Takvim okunuyor";

/**
 * How long a factory step may be claimed as current without a newer event.
 *
 * The operator's horizon, for the document's reason: the Cloud Core speaks
 * once per render and once per verdict, not on a heartbeat, and rendering a
 * PPTX then reopening it with python-pptx can outlast the twelve-second
 * transient. Still a horizon: a `rendering` from a minute ago is last-known,
 * never a factory still writing. The publisher's own `ttl_s` beats this figure.
 */
export const ARTIFACT_FACTORY_TTL_MS: number = OPERATOR_STEP_TTL_MS;

/**
 * The Core's one wording for a factory event whose metadata named no title
 * (v7): the plain state, and nothing it did not say. A verdict with no title
 * would be a verdict on nothing, so it too yields this line alone.
 */
export const ARTIFACT_CAPTION_BARE = "Dosya üretiliyor";

/**
 * The verdict in the owner's words, spelled once for the caption, the facts
 * line and the Cockpit's rows alike. "Doğrulandı" is said only when the
 * publisher said `valid`: a render whose validation nobody published is
 * "doğrulama bildirilmedi" downstream, never one of these.
 */
export const ARTIFACT_VERDICT_LABEL: Record<ArtifactVerdict, string> = {
  rendering: "üretiliyor",
  valid: "doğrulandı",
  invalid: "doğrulanamadı",
};

/**
 * How long an app step may be claimed as current without a newer event.
 *
 * The operator's horizon, for the factory's reason: the Cloud Core speaks
 * once per project transition, not on a heartbeat, and a scaffold of two
 * hundred files or a test run can outlast the twelve-second transient. Still
 * a horizon: a `running` from a minute ago is last-known on the Core — the
 * app itself is a ROW on `/v1/apps`, and the Cockpit's link comes from that
 * row. The publisher's own `ttl_s` beats this figure.
 */
export const APP_FACTORY_TTL_MS: number = OPERATOR_STEP_TTL_MS;

/**
 * The Core's one wording for an app event whose metadata named no project
 * (v8): the plain state, and nothing it did not say. A state with no project
 * would be a state of nothing, so it too yields this line alone.
 */
export const APP_CAPTION_BARE = "Uygulama yapılıyor";

/**
 * The project's state in the owner's words, spelled once for the caption,
 * the facts line and the Cockpit's rows alike. "Çalışıyor" is said only when
 * the publisher said `running`, and "test edildi" — not "testleri geçti" —
 * for a `tested` whose counts nobody published: the counts, when sent, are
 * what turn it into a pass in words.
 */
export const APP_STATE_LABEL: Record<AppProjectState, string> = {
  planned: "planlandı",
  scaffolded: "iskeleti kuruluyor",
  running: "çalışıyor",
  tested: "test edildi",
  failed: "başarısız",
  stopped: "durduruldu",
};

/**
 * How long a genesis step may be claimed as current without a newer event.
 *
 * The operator's horizon, for the app's reason: the Cloud Core speaks once
 * per run transition, not on a heartbeat, and a sandbox run of the rendered
 * tests against the fixture application can outlast the twelve-second
 * transient. Still a horizon: a `building` from a minute ago is last-known
 * on the Core — the run itself is a ROW on `/v1/genesis/runs`, and the
 * Cockpit's controls come from that row. The publisher's own `ttl_s` beats
 * this figure.
 */
export const GENESIS_TTL_MS: number = OPERATOR_STEP_TTL_MS;

/**
 * The Core's one wording for a genesis event whose metadata named nothing
 * this build can read (v9): the plain name of the token, and nothing it did
 * not say. Deliberately no verb: a run may be building, waiting, verified
 * or failed, and the bare line must be true of every one of them.
 */
export const GENESIS_CAPTION_BARE = "Yeni yetenek";

/**
 * The run's state in the owner's words, spelled once for the caption, the
 * facts line and the Cockpit's rows alike (M24 spec §6's six truths, one
 * word per §5 state). "Doğrulandı" is said only when the publisher said
 * `verified`, and "kullanılabilir" only on `available`: a run that is
 * `registering` is not yet a capability the owner has, and nothing here
 * rounds it up to one.
 */
export const GENESIS_STATE_LABEL: Record<GenesisRunState, string> = {
  capability_missing: "yetenek yok — deneniyor",
  researching: "arayüz araştırılıyor",
  designing: "bağdaştırıcı tasarlanıyor",
  building: "bağdaştırıcı yazılıyor",
  testing: "sınanıyor",
  classifying: "sınıflandırılıyor",
  awaiting_approval: "onay bekliyor",
  rolling_out: "yayına alınıyor",
  registering: "kaydediliyor",
  available: "kullanılabilir",
  used: "kullanıldı",
  verified: "doğrulandı",
  failed: "başarısız",
  cancelled: "vazgeçildi",
};

/**
 * How long a scene step may be claimed as current without a newer event.
 *
 * The longest horizon in this file, and for the plainest reason: the run is
 * a whole editor started in batch mode on the owner's machine. M25 spec §3
 * bounds a Unity run at ten minutes, and even a Workbench still through
 * `blender.exe -b` outlasts the 45 s device round trip the operator's
 * horizon was cut for. Two minutes is long enough that a healthy render is
 * not reported as lost and short enough that a `rendering` from a coffee
 * break is not drawn as a tool still working. Still a horizon: what the run
 * said a while ago is last-known — the scene itself is a ROW on
 * `/v1/scenes`, and the Cockpit's image comes from that row. The
 * publisher's own `ttl_s` beats this figure, as it beats every figure here.
 */
export const SCENE_TTL_MS = 120_000;

/**
 * The Core's one wording for a scene event whose metadata named nothing
 * this build can read (v10): the plain name of the token, and nothing it
 * did not say. Deliberately no verb: a run may be creating, rendering,
 * verified, mismatched or unavailable, and the bare line must be true of
 * every one of them.
 */
export const SCENE_CAPTION_BARE = "3B sahne";

/**
 * The step in the owner's words, spelled once for the caption, the facts
 * line and the Cockpit's rows alike (M25 spec §4). "Doğrulandı" is said
 * only when the publisher said `verified` — a render that was written is
 * not a scene that was read back — and `unavailable` is worded as a plain
 * inability ("yapılamadı"), never as a fault.
 */
export const SCENE_STATE_LABEL: Record<SceneRunState, string> = {
  creating: "sahne kuruluyor",
  applying: "değişiklikler uygulanıyor",
  rendering: "render alınıyor",
  inspecting: "sahne okunuyor",
  verified: "doğrulandı",
  unverified: "doğrulanacak bir şey yoktu",
  mismatch: "uyuşmazlık",
  unavailable: "yapılamadı",
  failed: "başarısız",
};

/** The tool as the owner names it, for a line that talks ABOUT the tool. */
export const SCENE_TOOL_LABEL: Record<SceneTool, string> = {
  blender: "Blender",
  unity: "Unity",
};

/**
 * The tool as the owner names it when it is the PLACE something happens —
 * "Blender'da sahne kuruluyor". The two forms are the spec's own (M25 §5's
 * utterances "Blender'da yeni sahne aç", "Unity'de boş bir sahne oluştur"),
 * and they are written out rather than derived, because a suffix rule
 * guessed from a program's name is not Turkish grammar.
 */
export const SCENE_TOOL_LOCATIVE: Record<SceneTool, string> = {
  blender: "Blender'da",
  unity: "Unity'de",
};

/**
 * Why Unity cannot be driven, in the owner's words — ADR-0088 §5's own
 * wording ("Unity lisansı yok: yapılamadı") and the one reason M25 measured
 * for an `unavailable` (spec §1: the editor exits 198, "No valid Unity
 * Editor license found", until owner item 32).
 *
 * Said ONLY beside an `unavailable` whose publisher named the tool `unity`.
 * An `unavailable` for any other tool, or for a tool nobody named, is
 * "yapılamadı" alone: this build does not know why, and inventing a licence
 * for a Blender that is merely not installed would be the renderer making
 * up a reason.
 */
export const SCENE_UNITY_LICENCE = "lisans yok";

/** How many object names one inspection may carry into a row. A client bound, like `MAX_DOCUMENT_REFS`. */
export const MAX_SCENE_OBJECTS = 32;

/**
 * Per-state lifetimes for v3, in ms, exactly as `docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md`
 * §7 states them.
 *
 * They exist because these states do not fit the five kinds: an armed alarm is
 * steady in nature but must not be claimed for ever (a twelve-hour-old "armed"
 * is the horizon of one night), and the ringing states last minutes rather than
 * the twelve seconds a transient gets. The publisher's own `ttl_s` still beats
 * every figure here.
 */
const STATE_TTL_MS: Partial<Record<KnownUiState, number>> = {
  "alarm.armed": 12 * 60 * 60_000,
  "alarm.firing": 120_000,
  "alarm.playing": 20 * 60_000,
  "alarm.greeting": 60_000,
  "alarm.snoozed": 5 * 60_000,
  "alarm.stopped": 5 * 60_000,
  "alarm.completed": 5 * 60_000,
  "alarm.failed": 5 * 60_000,
  "display.on": 24 * 60 * 60_000,
  "display.off": 24 * 60 * 60_000,
  "operator.running": OPERATOR_STEP_TTL_MS,
  "operator.verifying": OPERATOR_STEP_TTL_MS,
  "document.analysis": DOCUMENT_STEP_TTL_MS,
  "mail.activity": MAIL_ACTIVITY_TTL_MS,
  "calendar.activity": CALENDAR_ACTIVITY_TTL_MS,
  "artifact.factory": ARTIFACT_FACTORY_TTL_MS,
  "app.factory": APP_FACTORY_TTL_MS,
  "capability.genesis": GENESIS_TTL_MS,
  "scene.activity": SCENE_TTL_MS,
};

/**
 * A transient state older than this is no longer claimed as current.
 *
 * Chosen against the real publishers rather than for looks: voice publishes on
 * every turn boundary (sub-second), research once per ranking stage, the
 * self-model indexer once per phase. Twelve seconds is comfortably longer than
 * any of those gaps and short enough that a dropped poll is visible to the
 * owner rather than silently drawn as ongoing work.
 */
export const TRANSIENT_TTL_MS = 12_000;

/** How long `agent.goal_completed` stays a headline before becoming history. */
export const MOMENT_TTL_MS = 20_000;

/**
 * Default lifetime of a perception observation, when the publisher did not say.
 *
 * Deliberately the client's *conservative* guess and nothing more: the presence
 * engine owns the real staleness policy, and when it publishes `ttl_s` that
 * figure wins (see `stateTtlMs`). Five minutes is short enough that an owner who
 * left the room is not still drawn as present, and long enough that a presence
 * state which is only republished on change does not flicker to unknown.
 */
export const OBSERVATION_TTL_MS = 300_000;

/** Default lifetime of a release stage. Deployments take minutes, not seconds. */
export const OPERATION_TTL_MS = 900_000;

export function stateKind(state: string): StateKind {
  return isKnownState(state) ? STATE_KINDS[state] : "transient";
}

const DEFAULT_TTL_MS: Record<StateKind, number> = {
  steady: Number.POSITIVE_INFINITY,
  transient: TRANSIENT_TTL_MS,
  moment: MOMENT_TTL_MS,
  observation: OBSERVATION_TTL_MS,
  operation: OPERATION_TTL_MS,
};

/**
 * How long this state may be claimed as current, in ms; `Infinity` for steady.
 *
 * `event` is optional so callers that only have a token still get the default.
 * When the publisher sent `ttl_s`, it is preferred over every default here: the
 * subsystem that made the observation knows how long it is good for, and the
 * client guessing over the top of that would be the renderer inventing truth.
 */
export function stateTtlMs(state: string, event?: UiStateEvent | null): number {
  const declared = event ? metaNumber(event, "ttl_s") : null;
  if (declared !== null && declared > 0) return declared * 1000;
  const perState = isKnownState(state) ? STATE_TTL_MS[state] : undefined;
  if (perState !== undefined) return perState;
  return DEFAULT_TTL_MS[stateKind(state)];
}

/**
 * Which conversation a state belongs to.
 *
 * v2 put four different kinds of statement on one bus, and they must not
 * displace one another. `owner.likely_asleep` is a fact about the room; it is
 * not the agent going quiet, and publishing it must never blank a core that is
 * genuinely thinking. So the core body draws the `agent`/`lab`/`operator`
 * channels, and ambient and release are drawn as their own bands with their
 * own ages.
 *
 * v4's `operator` is a core channel, not a band: the operator acting on the
 * desktop IS the agent working, and the newest of the three core channels is
 * what the body draws. It is named separately so the cockpit can ask "what is
 * the operator doing" without reading it off the agent's own states.
 */
export type StateChannel = "agent" | "lab" | "ambient" | "release" | "operator";

export function stateChannel(state: string): StateChannel {
  if (state.startsWith("evolution.")) return "lab";
  if (state.startsWith("operator.")) return "operator";
  // v5's `document.analysis` needs no channel of its own: reading the owner's
  // document IS the agent working, and the cockpit asks "which document" by
  // membership (`isDocumentState`), never off a channel. It falls through to
  // `agent` below. v6's `mail.activity` and `calendar.activity` do the same,
  // for the same reason, through `isMailState` / `isCalendarState`; so does
  // v7's `artifact.factory`, through `isArtifactState`: making a file for
  // the owner is the agent working. v8's `app.factory` follows through
  // `isAppState`: building and running an app for the owner is the same.
  // v9's `capability.genesis` follows through `isGenesisState`: acquiring
  // a capability the owner's request needs is the agent's own work — not
  // the lab's channel, although the lab's pipeline does the building.
  // v10's `scene.activity` follows through `isSceneState`: driving Blender
  // or Unity for the owner is the agent working, not a fact about the room.
  // v3 adds `display.*` to the room: whether the screens are lit is a fact
  // about the owner's desk, never about the agent's activity.
  if (state.startsWith("eye.") || state.startsWith("owner.") || state.startsWith("display."))
    return "ambient";
  // v3's alarm lifecycle joins v2's `alarm.triggered` off the core body. The
  // band splits them again by vocabulary (`isReleaseBandState`), so a ringing
  // alarm cannot blank a deployment.
  if (state.startsWith("release.") || state.startsWith("routine.") || state.startsWith("alarm."))
    return "release";
  return "agent";
}

/** States that drive the core body itself. */
export function isCoreChannel(state: string): boolean {
  const channel = stateChannel(state);
  return channel === "agent" || channel === "lab" || channel === "operator";
}

/** States published by the evolution lab. Never mixed with the agent's own. */
export function isEvolutionState(state: string): boolean {
  return state.startsWith("evolution.");
}

/** Presence inferences. Every one of these is probabilistic and carries a confidence. */
export function isPresenceState(state: string): boolean {
  return state.startsWith("owner.");
}

// ------------------------------------------------------------------ parsing

function asFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clamp01(value: number | null): number | null {
  if (value === null) return null;
  return Math.max(0, Math.min(1, value));
}

/**
 * Parse one event defensively.
 *
 * The API is trusted to be well-formed, but this is the boundary where a
 * partially-deployed API or a proxy could hand us something else, and a
 * renderer that throws takes the whole page with it. Anything unusable becomes
 * `null` (unknown) rather than a plausible-looking default.
 */
export function parseEvent(raw: unknown): UiStateEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const state = typeof o.state === "string" ? o.state : null;
  const at = typeof o.at === "string" ? o.at : null;
  if (!state || !at) return null;

  const metadata: Record<string, MetadataValue> = {};
  const rawMeta = o.metadata;
  if (rawMeta && typeof rawMeta === "object") {
    for (const [key, value] of Object.entries(rawMeta as Record<string, unknown>).slice(
      0,
      MAX_METADATA_KEYS,
    )) {
      if (typeof value === "number" && Number.isFinite(value)) metadata[key] = value;
      else if (typeof value === "boolean") metadata[key] = value;
      else if (typeof value === "string") metadata[key] = value.slice(0, MAX_LABEL_CHARS);
      // objects/arrays are content-shaped; the publisher drops them and so do we
    }
  }
  // v5: the one structured value the contract admits, under the one key —
  // and v8's second, under its own key. Everything else object-shaped stays
  // dropped above.
  const rawRecord = rawMeta && typeof rawMeta === "object" ? (rawMeta as Record<string, unknown>) : null;
  const refs = parseDocumentRefs(rawRecord?.refs);
  const tests = parseAppTestCounts(rawRecord?.tests);

  return {
    ...(refs.length ? { refs } : {}),
    ...(tests ? { tests } : {}),
    event_id: typeof o.event_id === "string" ? o.event_id : "",
    sequence: asFiniteNumber(o.sequence) ?? 0,
    state,
    subsystem: typeof o.subsystem === "string" ? o.subsystem : "system",
    at,
    intensity: clamp01(asFiniteNumber(o.intensity)),
    progress: clamp01(asFiniteNumber(o.progress)),
    severity: isSeverity(o.severity) ? o.severity : "info",
    status: typeof o.status === "string" && o.status ? o.status : null,
    task_id: typeof o.task_id === "string" && o.task_id ? o.task_id : null,
    goal_id: typeof o.goal_id === "string" && o.goal_id ? o.goal_id : null,
    module_id: typeof o.module_id === "string" && o.module_id ? o.module_id : null,
    session_id: typeof o.session_id === "string" && o.session_id ? o.session_id : null,
    label: typeof o.label === "string" && o.label ? o.label.slice(0, MAX_LABEL_CHARS) : null,
    metadata,
  };
}

/** A bounded token from a raw ref field, or `null` for anything that is not a non-empty string. */
function refString(value: unknown): string | null {
  return typeof value === "string" && value ? value.slice(0, MAX_LABEL_CHARS) : null;
}

/**
 * `metadata.refs` as `[{ref, path, excerpt}]`, defensively (M20 spec §3).
 *
 * The same posture as the rest of `parseEvent`: an entry that is not an object
 * or has no `ref` is dropped rather than defaulted, every string is cut to the
 * bus's token bound, and the list is capped. Nothing here can produce a ref
 * the publisher did not send.
 */
export function parseDocumentRefs(raw: unknown): DocumentRef[] {
  if (!Array.isArray(raw)) return [];
  const refs: DocumentRef[] = [];
  for (const item of raw) {
    if (refs.length >= MAX_DOCUMENT_REFS) break;
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const ref = refString(o.ref);
    if (ref === null) continue;
    refs.push({ ref, path: refString(o.path), excerpt: refString(o.excerpt) });
  }
  return refs;
}

/** A whole, non-negative count, or `null` for anything else — a count is never rounded into being. */
function asCount(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/**
 * `metadata.tests` as `{passed, failed}`, defensively (M23 spec §6).
 *
 * The same posture as `parseDocumentRefs`: both counts must be present as
 * whole non-negative numbers or there are no counts at all — a `{passed: 12}`
 * with no `failed` is not "12/12", because nobody said how many ran. Nothing
 * here can produce a figure the publisher did not send.
 */
export function parseAppTestCounts(raw: unknown): AppTestCounts | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const passed = asCount(o.passed);
  const failed = asCount(o.failed);
  if (passed === null || failed === null) return null;
  return { passed, failed };
}

export function parseResponse(raw: unknown): UiStateResponse | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const events = Array.isArray(o.events)
    ? o.events.map(parseEvent).filter((e): e is UiStateEvent => e !== null)
    : [];
  return {
    contract_version: asFiniteNumber(o.contract_version) ?? 0,
    current: parseEvent(o.current),
    events,
    sequence: asFiniteNumber(o.sequence) ?? 0,
  };
}

/** Milliseconds since `at`, or `null` when the timestamp is unusable. */
export function ageMs(event: UiStateEvent, now: number): number | null {
  const at = Date.parse(event.at);
  if (Number.isNaN(at)) return null;
  return Math.max(0, now - at);
}

/**
 * A number the publisher actually sent, under one of the given keys.
 *
 * The single accessor for every "how many sources / how many lessons" question
 * in the renderer, and the reason the empty states are truthful: when nothing
 * published a count, this returns `null` and the caller draws nothing. There is
 * deliberately no default-to-something overload.
 */
export function metaNumber(
  event: UiStateEvent | null,
  ...keys: string[]
): number | null {
  if (!event) return null;
  for (const key of keys) {
    const value = event.metadata[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

/** A short token the publisher actually sent, under one of the given keys. */
export function metaToken(event: UiStateEvent | null, ...keys: string[]): string | null {
  if (!event) return null;
  for (const key of keys) {
    const value = event.metadata[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}
