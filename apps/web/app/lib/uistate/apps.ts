/**
 * The App Factory's channel — contract v8 (M23 spec §6).
 *
 * The Cloud Core publishes `app.factory` while an app the owner asked for
 * is planned, scaffolded into a real project on the owner's machine, run
 * there in a bounded process, tested with its own tests, stopped or failed,
 * with `{project?, state?, port?, tests?}` in its metadata: the project's
 * name, its `AppProject` state, the port the companion's child is bound to
 * on `127.0.0.1` while it runs, and the counts the project's own tests gave.
 *
 * The rules are the artifact's, applied to a program being made:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    project is the token the publisher sent; the state is the state it
 *    sent; the port is the number it sent, when it is a port. A missing key
 *    is rendered as "not reported", never filled in.
 * 2. **A state this build does not know is the plain state.** The caption
 *    for a `state` outside the six the contract names is "Uygulama
 *    yapılıyor" and no more: a word we cannot read is not a step we may
 *    narrate — and above all it is never "çalışıyor".
 * 3. **A pass is said only from counts (ADR-0086).** "Testleri geçti (12/12)"
 *    needs the twelve; a `tested` whose counts nobody published is "test
 *    edildi", and a `tested` with a failure among its counts is worded with
 *    the failure. "Başarısız — 2 test" needs the two.
 * 4. **Nothing here can scaffold, run, stop or test.** This channel is
 *    presentation. The projects themselves are rows the Cockpit reads from
 *    the list route (`lib/cockpit/apps.ts`), and the controls there ask the
 *    Cloud Core, which asks the device — never this page.
 */

import {
  APP_CAPTION_BARE,
  APP_STATE_LABEL,
  type AppProjectState,
  type AppTestCounts,
  type Severity,
  type UiStateEvent,
  asPort,
  isAppProjectState,
  isAppState,
  isSeverity,
  metaNumber,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every app event, read verbatim. */
export type AppFacts = {
  /** The project's name (`metadata.project`), or `null` if none was sent. */
  project: string | null;
  /** `metadata.state` exactly as sent, or `null` when none was. */
  stateToken: string | null;
  /** The state when it is one of the six this build knows, else `null`. */
  state: AppProjectState | null;
  /** The port (`metadata.port`) when it is one, else `null`. */
  port: number | null;
  /** The test counts the publisher sent, or `null` when it sent none. */
  tests: AppTestCounts | null;
};

/**
 * The counts, from the one structured value the boundary admits
 * (`event.tests`, M23 spec §6) or from the flat pair a publisher that
 * flattens its metadata would send (`tests_passed`, `tests_failed`) — both
 * required, whole and non-negative, or there are no counts. A publisher
 * that sent one figure did not say how many ran.
 */
export function appTestCountsOf(event: UiStateEvent | null): AppTestCounts | null {
  if (!event) return null;
  if (event.tests) return event.tests;
  const passed = metaNumber(event, "tests_passed");
  const failed = metaNumber(event, "tests_failed");
  if (passed === null || failed === null) return null;
  if (!Number.isInteger(passed) || !Number.isInteger(failed) || passed < 0 || failed < 0) return null;
  return { passed, failed };
}

/** The published facts on one event, or explicit nulls for no event. */
export function appFacts(event: UiStateEvent | null): AppFacts {
  const token = metaToken(event, "state");
  return {
    project: metaToken(event, "project"),
    stateToken: token,
    state: isAppProjectState(token) ? token : null,
    port: asPort(event?.metadata.port),
    tests: appTestCountsOf(event),
  };
}

// ----------------------------------------------------------------- the port

/** The loopback host every bounded process is bound to (M23 spec §3). Never anything else. */
export const APP_HOST = "127.0.0.1";

/** The address as the caption says it: `127.0.0.1:8123`, or `null` without a port. */
export function appAddress(port: number | null): string | null {
  return port === null ? null : `${APP_HOST}:${port}`;
}

/**
 * The URL the owner's own browser can open for a running app:
 * `http://127.0.0.1:8123/`. Built from the port alone and only for a port,
 * so it can never point anywhere but the owner's loopback; this page never
 * fetches it.
 */
export function appUrl(port: number | null): string | null {
  return port === null ? null : `http://${APP_HOST}:${port}/`;
}

// ---------------------------------------------------------------- the tests

/** "12 geçti / 0 başarısız" for a facts line or a row, or `null` without counts. */
export function appTestsPhrase(tests: AppTestCounts | null): string | null {
  if (!tests) return null;
  return `${tests.passed} geçti / ${tests.failed} başarısız`;
}

/** "(12/12)": passed over ran, for the caption. `null` without counts. */
export function appTestsRatio(tests: AppTestCounts | null): string | null {
  if (!tests) return null;
  return `(${tests.passed}/${tests.passed + tests.failed})`;
}

// ------------------------------------------------------------- the captions

/** The state in the owner's words, re-exported from the contract where it is spelled once. */
export { APP_CAPTION_BARE, APP_STATE_LABEL } from "./contract";

/**
 * The caption the Core draws under the building posture (spec §6):
 *
 *   planned     → "Görev Takip planlandı"
 *   scaffolded  → "Görev Takip iskeleti kuruluyor"
 *   running     → "Görev Takip çalışıyor · 127.0.0.1:8123"   ("Görev Takip çalışıyor" without a port)
 *   tested      → "Görev Takip testleri geçti (12/12)"       ("Görev Takip test edildi" without counts;
 *                                                             "Görev Takip test edildi (10/12 geçti)" with a failure)
 *   failed      → "Görev Takip başarısız — 2 test"            ("Görev Takip başarısız" without a failing count)
 *   stopped     → "Görev Takip durduruldu"
 *
 * — each part only if it was published. Without a project there is nothing
 * to be in a state, so the caption is the bare statement and no more (rule
 * 1); a state this build cannot read yields the bare statement too (rule 2);
 * a project with no state at all is the factory at work on it, which is the
 * only thing an `app.factory` with no state can mean.
 */
export function appCaption(facts: AppFacts): string {
  if (!facts.project) return APP_CAPTION_BARE;
  if (facts.stateToken !== null && facts.state === null) return APP_CAPTION_BARE;
  const project = facts.project;
  const state = facts.state;
  if (state === null) return `${project} yapılıyor`;
  const word = APP_STATE_LABEL[state];
  switch (state) {
    case "running": {
      const address = appAddress(facts.port);
      return address ? `${project} ${word} · ${address}` : `${project} ${word}`;
    }
    case "tested": {
      const tests = facts.tests;
      if (!tests) return `${project} ${word}`;
      if (tests.failed === 0) return `${project} testleri geçti ${appTestsRatio(tests)}`;
      return `${project} ${word} (${tests.passed}/${tests.passed + tests.failed} geçti)`;
    }
    case "failed": {
      const tests = facts.tests;
      return tests && tests.failed > 0 ? `${project} ${word} — ${tests.failed} test` : `${project} ${word}`;
    }
    default:
      return `${project} ${word}`;
  }
}

// ---------------------------------------------------------------- the view

export type AppStage =
  /** `app.factory` is current: the Core is planning, scaffolding, running or testing a project. */
  | "active"
  /** Nothing has been published about the factory, or the claim decayed. */
  | "none";

export type AppView = AppFacts & {
  stage: AppStage;
  /**
   * `"active"` when an app event was ever published, regardless of age;
   * `null` when none was — the panel's "nothing reported", as distinct from
   * a project we stopped hearing about.
   */
  lastKnown: "active" | null;
  /** The caption, from the facts alone. */
  caption: string;
  /** The publisher's short label. Never prose. */
  label: string | null;
  taskId: string | null;
  severity: Severity;
  ageMs: number | null;
  /** True once the claim has aged out; `stage` is then `none`. */
  expired: boolean;
};

export function appView(claim: Claim): AppView {
  const event = claim.event;
  const named = event !== null && isAppState(event.state);
  const facts = appFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "active" : "none",
    lastKnown: named ? "active" : null,
    caption: appCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually doing something with a project right now. */
export function appIsActive(view: AppView): boolean {
  return view.stage === "active";
}

/**
 * True for the facts of an app the publisher said is RUNNING on a port it
 * named: the one combination the Cockpit links, and the one the Core draws
 * as serving. A `running` without a port is running somewhere nobody said.
 */
export function appIsServing(facts: Pick<AppFacts, "state" | "port">): boolean {
  return facts.state === "running" && facts.port !== null;
}
