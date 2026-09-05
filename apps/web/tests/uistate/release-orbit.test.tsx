/**
 * The holographic deployment visualisation (M18 spec §15).
 *
 * Three things are asserted, and the third is the one that matters most:
 *
 * 1. a release stage draws the orbit, and `none` draws nothing;
 * 2. the orbit is independent of the core body - an idle core with a
 *    deployment in flight shows both, and neither hides the other;
 * 3. the orbit FILLS only against real published progress. A release of
 *    unknown length gets a ring that says so, never a bar that pretends to know.
 *
 * `react-dom/server` only. No browser.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import CoreFallback2D from "../../app/core/CoreFallback2D";
import { applyResponse, applyUnauthorized, emptyTruth } from "../../app/lib/uistate/truth";
import { hasReleaseOrbit, releasePalette, visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  ALARM_TRIGGERED,
  RELEASE_APPROVAL_REQUIRED,
  RELEASE_DEPLOYING,
  ROUTINE_ARMED,
  T0,
  VOICE_THINKING,
  event,
  resetSequence,
  response,
} from "./fixtures";

function intentOf(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return visualFor(applyResponse(emptyTruth(), response(events), T0), at);
}

function html(events: ReturnType<typeof event>[], at = T0) {
  return renderToStaticMarkup(<CoreFallback2D intent={intentOf(events, at)} tier="high" />);
}

describe("the release orbit exists only when a release stage is current", () => {
  it("draws nothing when nothing was published", () => {
    const intent = intentOf([]);
    expect(intent.releaseStage).toBe("none");
    expect(hasReleaseOrbit(intent)).toBe(false);
    expect(html([])).not.toContain("core-release");
  });

  it("draws the orbit for a deployment in flight", () => {
    const intent = intentOf([RELEASE_DEPLOYING()]);
    expect(intent.releaseStage).toBe("deploying");
    expect(intent.releaseInFlight).toBe(true);
    expect(hasReleaseOrbit(intent)).toBe(true);
    const markup = html([RELEASE_DEPLOYING()]);
    expect(markup).toContain('data-release-stage="deploying"');
    expect(markup).toContain('data-release-in-flight="yes"');
  });

  it("marks a stage that waits on the owner as waiting, not working", () => {
    const intent = intentOf([RELEASE_APPROVAL_REQUIRED()]);
    expect(intent.releaseAwaitingOwner).toBe(true);
    expect(intent.releaseInFlight).toBe(false);
    expect(releasePalette(intent.releaseStage)).toBe("held");
  });

  it("routines and alarms are not release geometry", () => {
    // They share the release channel on the band, but a fired alarm is not a
    // deployment and must not draw a deployment orbit around the core.
    expect(intentOf([ROUTINE_ARMED()]).releaseStage).toBe("none");
    expect(intentOf([ALARM_TRIGGERED()]).releaseStage).toBe("none");
    expect(html([ALARM_TRIGGERED()])).not.toContain("core-release");
  });
});

describe("the orbit is independent of the core body", () => {
  it("an idle core with a deployment in flight shows both", () => {
    const intent = intentOf([AGENT_IDLE(), RELEASE_DEPLOYING()]);
    expect(intent.kind).toBe("idle");
    expect(intent.releaseStage).toBe("deploying");
  });

  it("a thinking core is not hidden by a release, and a release is not hidden by thinking", () => {
    const intent = intentOf([RELEASE_DEPLOYING(), VOICE_THINKING()]);
    expect(intent.kind).toBe("thinking");
    expect(intent.releaseStage).toBe("deploying");
  });

  it("an unauthorized session draws nothing on either channel", () => {
    const intent = visualFor(applyUnauthorized(), T0);
    expect(intent.kind).toBe("unauthorized");
    expect(intent.releaseStage).toBe("none");
  });
});

describe("the orbit fills only against real progress", () => {
  it("carries the published progress through", () => {
    const intent = intentOf([RELEASE_DEPLOYING()]); // fixture publishes 0.4
    expect(intent.releaseProgress).toBeCloseTo(0.4);
    const markup = html([RELEASE_DEPLOYING()]);
    expect(markup).toContain('data-release-progress="40"');
    expect(markup).toContain("core-release-fill");
  });

  it("draws a ring and no fill when the publisher sent no progress", () => {
    resetSequence();
    const unknownLength = event({
      state: "release.deploying",
      subsystem: "deployment",
      status: "deploying",
      module_id: "opp-1",
    });
    const intent = intentOf([unknownLength]);
    expect(intent.releaseProgress).toBeNull();
    const markup = html([unknownLength]);
    expect(markup).toContain('data-release-progress="unknown"');
    expect(markup).toContain("core-release-track");
    expect(markup).not.toContain("core-release-fill");
  });

  it("a live release is drawn as arrived, and a rollback as reversing", () => {
    resetSequence();
    const live = event({ state: "release.live", subsystem: "deployment", status: "live" });
    expect(releasePalette(intentOf([live]).releaseStage)).toBe("achieved");
    resetSequence();
    const back = event({ state: "release.rollback", subsystem: "deployment", status: "rollback" });
    expect(releasePalette(intentOf([back]).releaseStage)).toBe("fault");
  });
});
