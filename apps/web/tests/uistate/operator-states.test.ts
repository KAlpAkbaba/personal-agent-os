/**
 * Contract v4: the Digital Operator's three states (M19 spec §4).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to a hand on the owner's desktop: **the Core shows a step
 * because the operator published it, with the step, capability and window
 * the publisher sent — never a step it inferred, never progress it was not
 * told, never a click it assumed had worked.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v3 (or
 * v2) is read normally, because v4 only added states.
 */

import { describe, expect, it } from "vitest";

import {
  KNOWN_CONTRACT_VERSION,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STATES,
  OPERATOR_STEP_TTL_MS,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  contractCompatibility,
  isCoreChannel,
  isKnownState,
  isOperatorState,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  KIND_LABEL,
  OPERATOR_LABEL,
  STATE_LABEL,
  contractLagNote,
  operatorErrorLine,
  operatorFactsLine,
  operatorStepPhrase,
  stateLabel,
  subsystemLabel,
} from "../../app/lib/uistate/labels";
import { operatorIsActing, operatorPosition, operatorView } from "../../app/lib/uistate/operator";
import {
  applyError,
  applyResponse,
  coreClaim,
  emptyTruth,
  operatorClaim,
} from "../../app/lib/uistate/truth";
import {
  ERROR_AGITATION,
  type VisualIntent,
  type VoiceOverlay,
  applyVoiceOverlay,
  isOperatorActing,
  visualFor,
} from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  OPERATOR_FAILED,
  OPERATOR_FAILED_TASK,
  OPERATOR_RUNNING,
  OPERATOR_RUNNING_BARE,
  OPERATOR_RUNNING_INDEXED,
  OPERATOR_VERIFYING,
  OWNER_AWAY,
  T0,
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
const MOTION_CHANNELS = [
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
] as const satisfies readonly (keyof VisualIntent)[];

const VOICE_TOOL_RUNNING: VoiceOverlay = {
  state: "tool_running",
  micLevel: null,
  outputLevel: null,
  caption: null,
  toolLabel: "Operatör",
  lastError: null,
};

describe("contract v4 is v3 plus the operator, and says so", () => {
  it("is version 4 and still reads a v3 and a v2 server", () => {
    expect(KNOWN_CONTRACT_VERSION).toBe(4);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(4)).toBe("current");
    expect(contractCompatibility(3)).toBe("older_supported");
    expect(contractCompatibility(2)).toBe("older_supported");
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(5)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("knows the three operator states, by membership rather than by prefix", () => {
    expect(OPERATOR_STATES).toEqual(["operator.running", "operator.verifying", "operator.failed"]);
    for (const state of OPERATOR_STATES) {
      expect(UI_STATES).toContain(state);
      expect(isKnownState(state), state).toBe(true);
      expect(isOperatorState(state), state).toBe(true);
    }
    // A newer server's word is not a state this build may draw as running.
    expect(isOperatorState("operator.cancelled")).toBe(false);
    expect(isOperatorState("agent.tool_running")).toBe(false);
  });

  it("adds the operator subsystem and names it", () => {
    expect(SUBSYSTEMS).toContain("operator");
    expect(subsystemLabel("operator")).toBe("Operatör");
  });

  it("puts the operator on its own channel, and that channel drives the core body", () => {
    for (const state of OPERATOR_STATES) {
      expect(stateChannel(state), state).toBe("operator");
      expect(isCoreChannel(state), state).toBe(true);
    }
    // The room is untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("agent.thinking")).toBe("agent");
  });

  it("classifies acting and verifying as transient, and failed as held until replaced", () => {
    expect(stateKind("operator.running")).toBe("transient");
    expect(stateKind("operator.verifying")).toBe("transient");
    expect(stateKind("operator.failed")).toBe("steady");
    expect(stateKind("agent.error")).toBe("steady"); // the model `failed` follows
  });

  it("gives a step the companion's command horizon, not the twelve-second one", () => {
    expect(OPERATOR_STEP_TTL_MS).toBe(45_000);
    expect(OPERATOR_STEP_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(stateTtlMs("operator.running")).toBe(OPERATOR_STEP_TTL_MS);
    expect(stateTtlMs("operator.verifying")).toBe(OPERATOR_STEP_TTL_MS);
    expect(stateTtlMs("operator.failed")).toBe(Number.POSITIVE_INFINITY);
    // The publisher's own ttl_s still beats every figure here.
    const declared = event({ state: "operator.running", subsystem: "operator", metadata: { ttl_s: 10 } });
    expect(stateTtlMs("operator.running", declared)).toBe(10_000);
  });

  it("gives every operator state the Turkish the spec asks for", () => {
    expect(STATE_LABEL["operator.running"]).toBe("Operatör çalışıyor");
    expect(STATE_LABEL["operator.verifying"]).toBe("Operatör doğruluyor");
    expect(STATE_LABEL["operator.failed"]).toBe("Operatör başarısız");
    expect(KIND_LABEL.operator_running).toBe("Operatör çalışıyor");
    expect(KIND_LABEL.operator_verifying).toBe("Operatör doğruluyor");
    expect(KIND_LABEL.operator_failed).toBe("Operatör başarısız");
    expect(OPERATOR_LABEL.running).toBe("Operatör çalışıyor");
    expect(OPERATOR_LABEL.none).not.toBe("Boşta");
    for (const state of OPERATOR_STATES) expect(stateLabel(state), state).not.toBe(state);
  });

  it("names exactly the families an older server will never publish", () => {
    const v3 = contractLagNote(3, 4);
    expect(v3).toContain("v3");
    expect(v3).toContain("v4");
    expect(v3).toContain("Dijital operatör durumları");
    expect(v3).not.toContain("larm");
    expect(v3).toContain("yok demek değil");

    const v2 = contractLagNote(2, 4);
    expect(v2).toContain("Alarm ve ekran durumları");
    expect(v2).toContain("dijital operatör durumları");

    // The v3 wording M18.3 shipped is unchanged for a v2 server seen from v3.
    expect(contractLagNote(2, 3)).toContain("Alarm ve ekran durumları bu sunucudan henüz yayınlanmıyor");
  });
});

describe("the Core draws the acting posture from the published state", () => {
  it("running is the tool-execution posture with the published step as the caption", () => {
    const intent = intentOf([OPERATOR_RUNNING("open_notepad", "app.launch", "Adsız - Not Defteri")]);
    expect(intent.kind).toBe("operator_running");
    expect(intent.source).toBe("bus");
    expect(intent.subsystem).toBe("operator");
    expect(intent.palette).toBe("work");
    expect(isOperatorActing(intent)).toBe(true);

    // The caption is the step the planner named, verbatim.
    expect(intent.label).toBe("open_notepad");
    expect(intent.operatorStep).toBe("open_notepad");
    expect(intent.operatorCapability).toBe("app.launch");
    expect(intent.operatorWindow).toBe("Adsız - Not Defteri");
    expect(intent.operatorErrorClass).toBeNull();

    // The posture the visual language reserves for a capability at work: the
    // paths carry as much traffic as a plain tool, the shells stand open, the
    // rings turn, nothing agitates and nothing is held back.
    const tool = intentOf([event({ state: "agent.tool_running" })]);
    const idle = intentOf([AGENT_IDLE()]);
    expect(intent.flowRate).toBe(tool.flowRate);
    expect(intent.shellSpread).toBeGreaterThan(idle.shellSpread);
    expect(intent.ringSpin).toBeGreaterThan(idle.ringSpin);
    expect(intent.topology).toBeGreaterThan(0);
    expect(intent.agitation).toBe(0);
    expect(intent.restraint).toBe(0);
  });

  it("verifying is the same work, turned inward: the result is coming back to be checked", () => {
    const running = intentOf([OPERATOR_RUNNING()]);
    const verifying = intentOf([OPERATOR_VERIFYING()]);
    expect(verifying.kind).toBe("operator_verifying");
    expect(verifying.label).toBe("open_notepad");
    expect(verifying.inwardFlow).toBeGreaterThan(0);
    expect(running.inwardFlow).toBe(0);
    expect(verifying.flowRate).toBeLessThan(running.flowRate);
    expect(verifying.agitation).toBe(0);
    expect(isOperatorActing(verifying)).toBe(true);
  });

  it("failed is the failure posture, held, with the error class the publisher sent", () => {
    const intent = intentOf([OPERATOR_FAILED("focus_mismatch")]);
    expect(intent.kind).toBe("operator_failed");
    expect(intent.palette).toBe("fault");
    expect(intent.agitation).toBe(ERROR_AGITATION);
    expect(intent.restraint).toBeGreaterThan(0);
    expect(intent.flowRate).toBe(0); // failure is not activity
    expect(intent.operatorErrorClass).toBe("focus_mismatch");
    expect(intent.label).toBe("type_text"); // the step it was on
    expect(intent.operatorWindow).toBe("Hesap Makinesi");
    expect(intent.severity).toBe("warning");
    expect(isOperatorActing(intent)).toBe(false);
  });

  it("invents nothing when the publisher sent no metadata", () => {
    const intent = intentOf([OPERATOR_RUNNING_BARE()]);
    expect(intent.kind).toBe("operator_running");
    expect(intent.label).toBeNull();
    expect(intent.operatorStep).toBeNull();
    expect(intent.operatorCapability).toBeNull();
    expect(intent.operatorWindow).toBeNull();
    expect(operatorFactsLine(operatorView(operatorClaim(truthOf([OPERATOR_RUNNING_BARE()]), T0)))).toBe(
      "adım bildirilmedi · yetenek bildirilmedi · pencere bildirilmedi",
    );
    expect(operatorErrorLine(null)).toBe("hata sınıfı bildirilmedi");
  });

  it("reads the step as an index when the publisher sends a number, and captions with the step's name", () => {
    // `OperatorService._on_step` publishes `{step: <index>, step_count, capability,
    // window_title}` with the step's NAME as the label. Both shapes are read;
    // neither is derived from the other.
    const intent = intentOf([OPERATOR_RUNNING_INDEXED(1, 3, "type_text", "operator.verifying")]);
    expect(intent.kind).toBe("operator_verifying");
    expect(intent.operatorStep).toBeNull();
    expect(intent.operatorStepIndex).toBe(1);
    expect(intent.operatorStepCount).toBe(3);
    expect(intent.operatorCapability).toBe("app.launch");
    expect(intent.operatorWindow).toBe("Adsız - Not Defteri");
    // The caption is the publisher's label (the step's name), not the number.
    expect(intent.label).toBe("type_text");
    // The panel's line counts the way the owner counts: index 1 of 3 is "2/3".
    const view = operatorView(operatorClaim(truthOf([OPERATOR_RUNNING_INDEXED(1, 3, "type_text")]), T0));
    expect(operatorPosition(view)).toBe("2/3");
    expect(operatorFactsLine(view)).toBe(
      "adım 2/3 · yetenek: app.launch · pencere: Adsız - Not Defteri",
    );
  });

  it("captions with the place in the plan only when it has neither a name nor a label", () => {
    const named = intentOf([OPERATOR_RUNNING_INDEXED(0, 3, null)]);
    expect(named.label).toBe("adım 1/3");
    expect(named.operatorStepIndex).toBe(0);
    // An index without a length is still an index — "adım 1", never "1/?".
    const unbounded = intentOf([OPERATOR_RUNNING_INDEXED(0, null, null)]);
    expect(unbounded.label).toBe("adım 1");
    expect(unbounded.operatorStepCount).toBeNull();
    // And the task-level start event (`step: 0, step_count: n`, the plan's
    // name as label) captions with the plan's name.
    const started = intentOf([
      event({
        state: "operator.running",
        subsystem: "operator",
        label: "app_open",
        metadata: { step: 0, step_count: 1 },
      }),
    ]);
    expect(started.label).toBe("app_open");
    expect(started.operatorCapability).toBeNull();
  });

  it("phrases the step from exactly what was sent", () => {
    expect(operatorStepPhrase({ step: "open_notepad", stepIndex: 0, stepCount: 3 })).toBe("adım 1/3: open_notepad");
    expect(operatorStepPhrase({ step: "open_notepad", stepIndex: null, stepCount: null })).toBe("adım: open_notepad");
    expect(operatorStepPhrase({ step: null, stepIndex: 2, stepCount: 3 })).toBe("adım 3/3");
    expect(operatorStepPhrase({ step: null, stepIndex: 2, stepCount: null })).toBe("adım 3");
    expect(operatorStepPhrase({ step: null, stepIndex: null, stepCount: 3 })).toBe("adım bildirilmedi");
    // A negative or fractional number is not an index anyone published.
    const odd = event({ state: "operator.running", subsystem: "operator", metadata: { step: -1, step_count: 0.5 } });
    const facts = operatorView(operatorClaim(truthOf([odd]), T0));
    expect(facts.stepIndex).toBeNull();
    expect(facts.stepCount).toBeNull();
  });

  it("a task-level failure carries its error class and nothing it was not sent", () => {
    const intent = intentOf([OPERATOR_FAILED_TASK("timeout")]);
    expect(intent.kind).toBe("operator_failed");
    expect(intent.operatorErrorClass).toBe("timeout");
    expect(intent.label).toBe("app_open"); // the plan's name, as published
    expect(intent.operatorStep).toBeNull();
    expect(intent.operatorCapability).toBeNull();
    expect(intent.operatorWindow).toBeNull();
    const view = operatorView(operatorClaim(truthOf([OPERATOR_FAILED_TASK("timeout")]), T0));
    expect(operatorFactsLine(view)).toBe("adım bildirilmedi · yetenek bildirilmedi · pencere bildirilmedi");
    expect(operatorErrorLine(view.errorClass)).toBe("hata sınıfı: timeout");
  });

  it("never draws progress, because none is published", () => {
    for (const events of [[OPERATOR_RUNNING()], [OPERATOR_VERIFYING()], [OPERATOR_FAILED()]]) {
      const intent = intentOf(events);
      expect(intent.progress).toBeNull();
      expect(intent.constellationNodes).toBe(0);
      expect(intent.capabilityNodes).toBe(0);
    }
  });

  it("does not let the room displace a step in flight", () => {
    // The owner stepping away is a fact about the room; the operator is still
    // acting, and the Core keeps saying so.
    const intent = intentOf([OPERATOR_RUNNING(), OWNER_AWAY()]);
    expect(intent.kind).toBe("operator_running");
  });
});

describe("honesty over time", () => {
  it("a step is live for its horizon and last-known after it, with its facts kept", () => {
    const truth = truthOf([OPERATOR_RUNNING()]);
    const live = visualFor(truth, T0 + OPERATOR_STEP_TTL_MS - 1);
    expect(live.kind).toBe("operator_running");

    const stale = visualFor(truth, T0 + OPERATOR_STEP_TTL_MS + 1);
    expect(stale.kind).toBe("last_known");
    expect(stale.state).toBe("operator.running");
    for (const channel of MOTION_CHANNELS) expect(stale[channel], channel).toBe(0);
    // What it WAS doing is still a fact; the readout names it as last-known.
    expect(stale.label).toBe("open_notepad");
    expect(stale.operatorWindow).toBe("Adsız - Not Defteri");
    expect(isOperatorActing(stale)).toBe(false);
  });

  it("a failure holds until something newer is published, and not a moment longer", () => {
    const held = truthOf([OPERATOR_FAILED()]);
    expect(visualFor(held, T0 + 24 * 60 * 60_000).kind).toBe("operator_failed");

    // Replaced by the agent's next word, exactly as agent.error would be.
    const replaced = truthOf([OPERATOR_FAILED(), AGENT_IDLE()]);
    expect(visualFor(replaced, T0).kind).toBe("idle");
    expect(coreClaim(replaced, T0).event?.state).toBe("agent.idle");
  });

  it("an unreachable API keeps the shape and the facts, and stops the motion", () => {
    let truth = truthOf([OPERATOR_RUNNING()]);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unreachable");
    expect(intent.state).toBe("operator.running");
    expect(intent.operatorStep).toBe("open_notepad");
    expect(intent.flowRate).toBe(0);
    expect(intent.ringSpin).toBe(0);
  });
});

describe("the operator's own claim, for the cockpit", () => {
  it("is none, with nothing last-known, before anything was published", () => {
    const view = operatorView(operatorClaim(truthOf([AGENT_IDLE()]), T0));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBeNull();
    expect(view.step).toBeNull();
    expect(operatorIsActing(view)).toBe(false);
  });

  it("reads the newest operator event whatever else was published since", () => {
    const view = operatorView(operatorClaim(truthOf([OPERATOR_RUNNING(), AGENT_IDLE(), OWNER_AWAY()]), T0));
    expect(view.stage).toBe("running");
    expect(view.step).toBe("open_notepad");
    expect(view.capability).toBe("app.launch");
    expect(view.windowTitle).toBe("Adsız - Not Defteri");
    expect(view.label).toBe("Not Defteri'ni aç");
    expect(view.taskId).toBe("op-task-1");
    expect(operatorIsActing(view)).toBe(true);
  });

  it("the newest operator event wins, in either direction", () => {
    const failedThenRunning = operatorView(operatorClaim(truthOf([OPERATOR_FAILED(), OPERATOR_RUNNING()]), T0));
    expect(failedThenRunning.stage).toBe("running");
    expect(failedThenRunning.errorClass).toBeNull();

    const runningThenFailed = operatorView(operatorClaim(truthOf([OPERATOR_RUNNING(), OPERATOR_FAILED()]), T0));
    expect(runningThenFailed.stage).toBe("failed");
    expect(runningThenFailed.errorClass).toBe("focus_mismatch");
    expect(runningThenFailed.severity).toBe("warning");
  });

  it("an expired step is none-but-last-known, never finished", () => {
    const view = operatorView(operatorClaim(truthOf([OPERATOR_RUNNING()]), T0 + 60_000));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBe("running");
    expect(view.expired).toBe(true);
    expect(view.ageMs).toBe(60_000);
    expect(view.step).toBe("open_notepad");
  });

  it("a newer server's operator word is not read as a stage, and the Core says it cannot read it", () => {
    const unknown = event({ state: "operator.cancelled", subsystem: "operator" });
    const truth = truthOf([OPERATOR_RUNNING(), unknown]);
    // The panel: only the states this build knows are stages.
    expect(operatorView(operatorClaim(truth, T0)).stage).toBe("running");
    // The Core: the newest core event is a word it cannot draw, and it says so
    // rather than drawing the running posture on its strength.
    expect(visualFor(truth, T0).kind).toBe("unknown_state");
  });
});

describe("older servers", () => {
  it("draws a v3 feed without the operator tokens normally, and records the lag", () => {
    resetSequence();
    const idle = AGENT_IDLE();
    const v3 = applyResponse(
      emptyTruth(),
      { contract_version: 3, current: idle, events: [idle], sequence: idle.sequence },
      T0,
    );
    expect(v3.connection.kind).toBe("live");
    expect(v3.contractVersion).toBe(3);
    expect(visualFor(v3, T0).kind).toBe("idle");
    // The operator never ran on that server as far as this client can tell —
    // and the panel's empty sentence is the honest one, with the lag note
    // saying why the states will never arrive.
    const view = operatorView(operatorClaim(v3, T0));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBeNull();
    expect(contractLagNote(3, KNOWN_CONTRACT_VERSION)).toContain("yok demek değil");
  });
});

describe("the voice overlay and the operator", () => {
  it("a local tool_running does not hide a live operator step: the bus knows which step and which window", () => {
    const bus = intentOf([OPERATOR_RUNNING()]);
    const drawn = applyVoiceOverlay(bus, VOICE_TOOL_RUNNING);
    expect(drawn).toBe(bus);
    expect(drawn.kind).toBe("operator_running");
    expect(drawn.source).toBe("bus");
    expect(drawn.label).toBe("open_notepad");
  });

  it("but every other local state, and a last-known operator, keep the overlay's precedence", () => {
    const bus = intentOf([OPERATOR_RUNNING()]);
    const speaking = applyVoiceOverlay(bus, { ...VOICE_TOOL_RUNNING, state: "speaking", outputLevel: 0.4 });
    expect(speaking.kind).toBe("speaking");
    expect(speaking.source).toBe("voice");

    const stale = visualFor(truthOf([OPERATOR_RUNNING()]), T0 + OPERATOR_STEP_TTL_MS + 1);
    const overStale = applyVoiceOverlay(stale, VOICE_TOOL_RUNNING);
    expect(overStale.kind).toBe("tool_running");
    expect(overStale.source).toBe("voice");

    const failed = intentOf([OPERATOR_FAILED()]);
    expect(applyVoiceOverlay(failed, VOICE_TOOL_RUNNING).kind).toBe("tool_running");

    const idle = intentOf([AGENT_IDLE()]);
    expect(applyVoiceOverlay(idle, VOICE_TOOL_RUNNING).kind).toBe("tool_running");
  });
});
