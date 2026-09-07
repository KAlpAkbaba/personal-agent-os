/**
 * Contract v3: the wake alarm's own channel, and the display's ambient band.
 *
 * Two rules are what this file exists to hold, and both are ways of saying the
 * same thing — a fact about the ROOM is not a fact about the AGENT:
 *
 * 1. `alarm.*` never displaces a Core that is genuinely thinking or speaking.
 *    It is its own channel, like the release orbit: the surge is drawn beside
 *    the work, never instead of it, and it is zero for every stage that is not
 *    actually making a noise.
 * 2. `display.*` never reaches the Core geometry at all. A dark monitor says
 *    nothing about what the assistant is doing, and there is deliberately no
 *    field on `VisualIntent` in which it could.
 *
 * Plus the boring but load-bearing one: a Cloud Core that still answers v2 is
 * read normally, because v3 only added states.
 */

import { describe, expect, it } from "vitest";

import {
  ALARM_STATES,
  DISPLAY_STATES,
  KNOWN_CONTRACT_VERSION,
  MIN_SUPPORTED_CONTRACT_VERSION,
  UI_STATES,
  contractCompatibility,
  isAlarmLifecycleState,
  isCoreChannel,
  isDisplayState,
  isKnownState,
  stateChannel,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import { alarmIsSounding, alarmView, displayView, releaseView } from "../../app/lib/uistate/ambient";
import {
  alarmClaim,
  applyError,
  applyResponse,
  applyUnauthorized,
  displayClaim,
  emptyTruth,
  releaseClaim,
} from "../../app/lib/uistate/truth";
import { type VisualIntent, hasWakeSurge, visualFor } from "../../app/lib/uistate/visual";
import { ALARM_LABEL, DISPLAY_LABEL, contractLagNote, stateLabel } from "../../app/lib/uistate/labels";
import {
  AGENT_IDLE,
  ALARM_ARMED,
  ALARM_FAILED,
  ALARM_FIRING,
  ALARM_GREETING,
  ALARM_PLAYING,
  ALARM_STOPPED,
  ALARM_TEST_PLAYING,
  ALARM_TRIGGERED,
  DISPLAY_OFF,
  DISPLAY_ON,
  RELEASE_DEPLOYING,
  SELFMODEL_THINKING,
  T0,
  VOICE_SPEAKING,
  event,
  resetSequence,
  response,
} from "./fixtures";

function truthOf(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return applyResponse(emptyTruth(), response(events), at);
}

function intentOf(events: ReturnType<typeof event>[], at = T0) {
  return visualFor(truthOf(events), at);
}

/** Every channel that means "the Core itself is moving". */
const CORE_MOTION = [
  "breathAmplitude",
  "inwardFlow",
  "topology",
  "pulse",
  "agitation",
  "ringSpin",
  "flowRate",
  "ownerVoice",
  "constellationDrift",
  "energy",
  "scale",
] as const satisfies readonly (keyof VisualIntent)[];

describe("contract v3 is v2 plus ten states, and says so", () => {
  it("still reads a v2 server, and a v3 one, from the v4 build", () => {
    // The build moved to v4 with M19 (operator-states.test.ts holds that);
    // what this test guards is that neither older server became unreadable.
    expect(KNOWN_CONTRACT_VERSION).toBeGreaterThanOrEqual(3);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION)).toBe("current");
    expect(contractCompatibility(3)).toBe("older_supported");
    expect(contractCompatibility(2)).toBe("older_supported");
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION + 1)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("knows every new state and gives each one a Turkish name", () => {
    for (const state of [...ALARM_STATES, ...DISPLAY_STATES]) {
      expect(isKnownState(state), state).toBe(true);
      expect(UI_STATES).toContain(state);
      // `stateLabel` falls back to the raw token; a real label is a different string.
      expect(stateLabel(state), state).not.toBe(state);
    }
  });

  it("puts the alarm off the core body and the display in the room", () => {
    for (const state of ALARM_STATES) {
      expect(stateChannel(state), state).toBe("release");
      expect(isCoreChannel(state), state).toBe(false);
      expect(isAlarmLifecycleState(state), state).toBe(true);
    }
    for (const state of DISPLAY_STATES) {
      expect(stateChannel(state), state).toBe("ambient");
      expect(isCoreChannel(state), state).toBe(false);
      expect(isDisplayState(state), state).toBe(true);
    }
    // v2's `alarm.triggered` keeps its old meaning: a routine fired.
    expect(isAlarmLifecycleState("alarm.triggered")).toBe(false);
    expect(stateChannel("alarm.triggered")).toBe("release");
  });

  it("gives each new state the lifetime the spec states", () => {
    expect(stateTtlMs("alarm.armed")).toBe(12 * 60 * 60_000);
    expect(stateTtlMs("alarm.firing")).toBe(120_000);
    expect(stateTtlMs("alarm.playing")).toBe(20 * 60_000);
    expect(stateTtlMs("alarm.greeting")).toBe(60_000);
    for (const state of ["alarm.snoozed", "alarm.stopped", "alarm.completed", "alarm.failed"]) {
      expect(stateTtlMs(state), state).toBe(5 * 60_000);
    }
    expect(stateTtlMs("display.on")).toBe(24 * 60 * 60_000);
    expect(stateTtlMs("display.off")).toBe(24 * 60 * 60_000);
  });

  it("still lets the publisher's own ttl_s win", () => {
    const declared = event({ state: "alarm.playing", subsystem: "routine", metadata: { ttl_s: 30 } });
    expect(stateTtlMs("alarm.playing", declared)).toBe(30_000);
  });

  it("draws a v2 stream normally and records the lag", () => {
    resetSequence();
    const idle = AGENT_IDLE();
    const v2 = applyResponse(
      emptyTruth(),
      { contract_version: 2, current: idle, events: [idle], sequence: idle.sequence },
      T0,
    );
    expect(v2.contractVersion).toBe(2);
    expect(v2.connection.kind).toBe("live");
    expect(visualFor(v2, T0).kind).toBe("idle");
    // And the owner is told what will be missing rather than left to read the
    // absence of an alarm row as "no alarm is set".
    expect(contractLagNote(2, 3)).toContain("v2");
    expect(contractLagNote(2, 3)).toContain("yok demek değil");
  });
});

describe("the wake alarm is drawn only while it is actually sounding", () => {
  it("armed is a line on the strip and no light at all", () => {
    const intent = intentOf([ALARM_ARMED()]);
    expect(intent.wakeStage).toBe("armed");
    expect(intent.wakeSurge).toBe(0);
    expect(hasWakeSurge(intent)).toBe(false);
  });

  it("firing, playing and greeting surge; the terminal states release it", () => {
    expect(intentOf([ALARM_FIRING()]).wakeSurge).toBeGreaterThan(0);
    expect(intentOf([ALARM_PLAYING()]).wakeSurge).toBeGreaterThan(0);
    expect(intentOf([ALARM_GREETING()]).wakeSurge).toBeGreaterThan(0);
    for (const make of [ALARM_STOPPED, ALARM_FAILED]) {
      const intent = intentOf([make()]);
      expect(intent.wakeSurge, intent.wakeStage).toBe(0);
    }
    // Failure is not activity: it is loud in words and silent in geometry.
    expect(intentOf([ALARM_FAILED()]).wakeStage).toBe("failed");
  });

  it("the declared ramp level raises the surge, and its absence does not invent one", () => {
    const quiet = intentOf([ALARM_PLAYING(0)]);
    const loud = intentOf([ALARM_PLAYING(0.9)]);
    const untold = intentOf([ALARM_PLAYING(null)]);
    expect(quiet.wakeLevelKnown).toBe(true);
    expect(loud.wakeSurge).toBeGreaterThan(quiet.wakeSurge);
    expect(loud.wakeSurge).toBeLessThanOrEqual(1);
    expect(untold.wakeLevel).toBeNull();
    expect(untold.wakeLevelKnown).toBe(false);
    // Unknown level still draws the stage's own constant — the alarm IS ringing.
    expect(untold.wakeSurge).toBe(quiet.wakeSurge);
  });

  it("expires: an alarm we stopped being told about is not an alarm ringing", () => {
    const truth = truthOf([ALARM_PLAYING()]);
    expect(visualFor(truth, T0 + 60_000).wakeStage).toBe("playing");
    const stale = visualFor(truth, T0 + 21 * 60_000);
    expect(stale.wakeStage).toBe("none");
    expect(stale.wakeSurge).toBe(0);
  });

  it("stops surging when the API cannot be reached, and keeps the stage", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([ALARM_PLAYING()]), T0);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    // We are drawing memory, not observation: the strip still says what the
    // last poll saw, and with what age; the light stops claiming it is now.
    expect(intent.wakeStage).toBe("playing");
    expect(intent.wakeSurge).toBe(0);
    expect(intent.wakeLevel).toBeNull();
    expect(hasWakeSurge(intent)).toBe(false);
  });

  it("draws nothing for a refused session", () => {
    resetSequence();
    applyResponse(emptyTruth(), response([ALARM_PLAYING()]), T0);
    const intent = visualFor(applyUnauthorized(), T0);
    expect(intent.wakeStage).toBe("none");
    expect(intent.wakeSurge).toBe(0);
  });
});

describe("the alarm never displaces the Core", () => {
  it("a thinking Core stays thinking while an alarm plays, and shows both", () => {
    const intent = intentOf([SELFMODEL_THINKING(), ALARM_PLAYING()]);
    expect(intent.kind).toBe("thinking");
    expect(intent.topology).toBeGreaterThan(0);
    expect(intent.wakeStage).toBe("playing");
    expect(hasWakeSurge(intent)).toBe(true);
  });

  it("a speaking Core stays speaking while the alarm greets", () => {
    const intent = intentOf([VOICE_SPEAKING(0.7), ALARM_GREETING()]);
    expect(intent.kind).toBe("speaking");
    expect(intent.pulse).toBeCloseTo(0.7);
    expect(intent.wakeStage).toBe("greeting");
  });

  it("a live voice session keeps the surge beside it", () => {
    const truth = truthOf([ALARM_PLAYING()]);
    const local = visualFor(truth, T0, {
      state: "speaking",
      micLevel: null,
      outputLevel: 0.4,
      caption: null,
      toolLabel: null,
      lastError: null,
    });
    expect(local.source).toBe("voice");
    expect(local.kind).toBe("speaking");
    expect(local.wakeStage).toBe("playing");
    expect(local.wakeSurge).toBeGreaterThan(0);
  });

  it("an alarm alone moves no core channel and claims no state", () => {
    for (const make of [ALARM_ARMED, ALARM_FIRING, ALARM_PLAYING, ALARM_GREETING, ALARM_FAILED]) {
      const intent = intentOf([make()]);
      expect(intent.kind).toBe("untold");
      for (const channel of CORE_MOTION) {
        if (channel === "scale") {
          expect(intent.scale, `${intent.wakeStage}.scale`).toBe(1);
          continue;
        }
        expect(intent[channel], `${intent.wakeStage}.${channel}`).toBe(0);
      }
    }
  });

  it("does not blank a deployment that is genuinely in flight", () => {
    // The alarm and the release share a band but not a claim: the newest event
    // is the alarm, and the release strip must still name the deployment.
    const truth = truthOf([RELEASE_DEPLOYING(), ALARM_PLAYING()]);
    expect(releaseView(releaseClaim(truth, T0)).stage).toBe("deploying");
    expect(alarmView(alarmClaim(truth, T0)).stage).toBe("playing");
    expect(visualFor(truth, T0).releaseStage).toBe("deploying");
  });

  it("keeps v2's alarm.triggered on the release band, not on the wake channel", () => {
    const truth = truthOf([ALARM_TRIGGERED()]);
    expect(releaseView(releaseClaim(truth, T0)).stage).toBe("alarm_triggered");
    expect(alarmView(alarmClaim(truth, T0)).stage).toBe("none");
    expect(visualFor(truth, T0).wakeSurge).toBe(0);
  });
});

describe("the alarm view reads the publisher and nothing else", () => {
  it("carries the label, the level and the test flag as sent", () => {
    const view = alarmView(alarmClaim(truthOf([ALARM_TEST_PLAYING()]), T0));
    expect(view.stage).toBe("playing");
    expect(view.label).toBe("Test alarmı");
    expect(view.level).toBeCloseTo(0.3);
    expect(view.isTest).toBe(true);
    expect(alarmIsSounding(view)).toBe(true);
  });

  it("does not assume a test", () => {
    expect(alarmView(alarmClaim(truthOf([ALARM_PLAYING()]), T0)).isTest).toBe(false);
  });

  it("carries the publisher's severity for a failure", () => {
    const view = alarmView(alarmClaim(truthOf([ALARM_FAILED()]), T0));
    expect(view.stage).toBe("failed");
    expect(view.severity).toBe("critical");
    expect(alarmIsSounding(view)).toBe(false);
  });

  it("names nothing when nothing was published", () => {
    const view = alarmView(alarmClaim(emptyTruth(), T0));
    expect(view.stage).toBe("none");
    expect(ALARM_LABEL[view.stage]).toBe("Bildirilen bir alarm yok");
    expect(view.level).toBeNull();
  });

  it("an alarm state this build cannot read is not drawn as a ringing alarm", () => {
    const truth = truthOf([event({ state: "alarm.rescheduling", subsystem: "routine" })]);
    expect(alarmView(alarmClaim(truth, T0)).stage).toBe("none");
    expect(visualFor(truth, T0).wakeSurge).toBe(0);
  });
});

describe("the display is ambient, and never touches the Core", () => {
  it("has no field on the intent it could reach the geometry through", () => {
    const intent = intentOf([DISPLAY_OFF()]);
    expect(Object.keys(intent).some((key) => key.toLowerCase().includes("display"))).toBe(false);
  });

  it("leaves every core channel exactly where an untold core leaves it", () => {
    const untold = intentOf([]);
    for (const make of [DISPLAY_ON, DISPLAY_OFF]) {
      const intent = intentOf([make()]);
      expect(intent.kind).toBe("untold");
      expect(intent.wakeSurge).toBe(0);
      for (const channel of CORE_MOTION) {
        expect(intent[channel], channel).toBe(untold[channel]);
      }
    }
  });

  it("does not blank a working Core", () => {
    const intent = intentOf([SELFMODEL_THINKING(), DISPLAY_OFF()]);
    expect(intent.kind).toBe("thinking");
  });

  it("reads on, off and untold as three different answers", () => {
    expect(displayView(displayClaim(truthOf([DISPLAY_ON()]), T0)).state).toBe("on");
    const off = displayView(displayClaim(truthOf([DISPLAY_OFF()]), T0));
    expect(off.state).toBe("off");
    expect(off.reason).toBe("owner_away");
    const untold = displayView(displayClaim(emptyTruth(), T0));
    expect(untold.state).toBe("untold");
    expect(untold.state).not.toBe("off");
    expect(DISPLAY_LABEL.untold).toBe("Ekran durumu bildirilmedi");
  });

  it("flags a display state it cannot read instead of guessing", () => {
    const view = displayView(displayClaim(truthOf([event({ state: "display.dimmed" })]), T0));
    expect(view.state).toBe("untold");
    expect(view.unknownState).toBe(true);
  });

  it("is not overwritten by the eye or by presence", () => {
    const truth = truthOf([DISPLAY_OFF(), event({ state: "eye.disabled", subsystem: "system" })]);
    expect(displayView(displayClaim(truth, T0)).state).toBe("off");
  });
});
