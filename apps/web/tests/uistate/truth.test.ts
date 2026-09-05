/**
 * The reducer: what the client may claim to know, and for how long.
 *
 * These tests exist because the failure they guard against is silent. A model
 * that quietly treats an old event as current, or a missing event as calm, does
 * not crash — it just shows the owner a confident picture of something that is
 * not happening, which is the exact failure ADR-0052 was written to prevent.
 */

import { describe, expect, it } from "vitest";

import {
  MOMENT_TTL_MS,
  TRANSIENT_TTL_MS,
  parseEvent,
  parseResponse,
  stateKind,
} from "../../app/lib/uistate/contract";
import {
  applyError,
  applyResponse,
  applyUnauthorized,
  currentClaim,
  emptyTruth,
  hasEverBeenTold,
  liveEventFor,
  liveSeverity,
  recentDescending,
  TAIL_LIMIT,
} from "../../app/lib/uistate/truth";
import {
  AGENT_ERROR,
  AGENT_IDLE,
  LAB_BUILDING,
  RESEARCH_RANKING,
  T0,
  VOICE_LISTENING,
  VOICE_SPEAKING,
  event,
  iso,
  resetSequence,
  response,
} from "./fixtures";

describe("applyResponse", () => {
  it("starts knowing nothing, and says so", () => {
    const truth = emptyTruth();
    expect(truth.connection.kind).toBe("connecting");
    expect(truth.current).toBeNull();
    expect(hasEverBeenTold(truth)).toBe(false);
  });

  it("an empty events list with an unchanged current is information, not a gap", () => {
    resetSequence();
    const idle = AGENT_IDLE();
    let truth = applyResponse(emptyTruth(), response([idle]), T0);
    expect(truth.polls).toBe(1);

    // The next poll returns nothing new; `current` still stands.
    truth = applyResponse(truth, { contract_version: 1, current: idle, events: [], sequence: idle.sequence }, T0 + 1000);
    expect(truth.polls).toBe(2);
    expect(truth.current?.state).toBe("agent.idle");
    expect(truth.recent).toHaveLength(1);
    expect(truth.connection.kind).toBe("live");
  });

  it("merges the tail without duplicating replayed events", () => {
    resetSequence();
    const a = VOICE_LISTENING();
    const b = VOICE_SPEAKING();
    let truth = applyResponse(emptyTruth(), response([a, b]), T0);
    // A replay of the same sequences (e.g. after a reconnect) must not double.
    truth = applyResponse(truth, response([a, b]), T0 + 500);
    expect(truth.recent).toHaveLength(2);
    expect(truth.sequence).toBe(b.sequence);
  });

  it("bounds the tail", () => {
    resetSequence();
    let truth = emptyTruth();
    const many = Array.from({ length: TAIL_LIMIT + 20 }, () => event({ state: "agent.idle" }));
    truth = applyResponse(truth, response(many), T0);
    expect(truth.recent).toHaveLength(TAIL_LIMIT);
    // The newest survive.
    expect(truth.recent[truth.recent.length - 1].sequence).toBe(many[many.length - 1].sequence);
  });

  it("indexes the latest event per state and per subsystem", () => {
    resetSequence();
    const truth = applyResponse(
      emptyTruth(),
      response([VOICE_LISTENING(), RESEARCH_RANKING(), LAB_BUILDING()]),
      T0,
    );
    expect(truth.latestByState["agent.listening"].subsystem).toBe("voice");
    expect(truth.latestByState["evolution.building"].subsystem).toBe("evolution");
    expect(truth.latestBySubsystem["research"].state).toBe("agent.researching");
  });

  it("indexes `current` even when it predates the returned tail", () => {
    resetSequence();
    const old = RESEARCH_RANKING();
    // The API returns no new events but still reports `current`.
    const truth = applyResponse(
      emptyTruth(),
      { contract_version: 1, current: old, events: [], sequence: old.sequence },
      T0,
    );
    expect(truth.latestBySubsystem["research"]?.state).toBe("agent.researching");
    expect(truth.current?.task_id).toBe("task-1");
  });

  it("newest-first stream for the cockpit", () => {
    resetSequence();
    const a = VOICE_LISTENING();
    const b = VOICE_SPEAKING();
    const truth = applyResponse(emptyTruth(), response([a, b]), T0);
    expect(recentDescending(truth).map((e) => e.sequence)).toEqual([b.sequence, a.sequence]);
  });
});

describe("failures degrade to honesty", () => {
  it("keeps the last picture but stops calling it live", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([VOICE_SPEAKING()]), T0);
    truth = applyError(truth, "ağ koptu", T0 + 1000);
    expect(truth.connection).toEqual({ kind: "unreachable", error: "ağ koptu", since: T0 + 1000 });
    expect(truth.current?.state).toBe("agent.speaking");
  });

  it("holds the first failure time across repeated failures", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0);
    truth = applyError(truth, "e1", T0 + 1000);
    truth = applyError(truth, "e2", T0 + 5000);
    expect(truth.connection.kind === "unreachable" && truth.connection.since).toBe(T0 + 1000);
  });

  it("a refused session discards everything it authorised", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([VOICE_SPEAKING()]), T0);
    truth = applyUnauthorized();
    expect(truth.connection.kind).toBe("unauthorized");
    expect(truth.current).toBeNull();
    expect(truth.recent).toHaveLength(0);
    expect(hasEverBeenTold(truth)).toBe(false);
  });
});

describe("claims expire", () => {
  it("a transient claim is live inside its TTL and expired outside it", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([VOICE_SPEAKING()]), T0);
    expect(currentClaim(truth, T0 + TRANSIENT_TTL_MS - 1).expired).toBe(false);
    expect(currentClaim(truth, T0 + TRANSIENT_TTL_MS + 1).expired).toBe(true);
  });

  it("a moment lasts longer than a transient but still ends", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([event({ state: "agent.goal_completed" })]), T0);
    expect(currentClaim(truth, T0 + TRANSIENT_TTL_MS + 1).expired).toBe(false);
    expect(currentClaim(truth, T0 + MOMENT_TTL_MS + 1).expired).toBe(true);
  });

  it("steady claims never expire", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0);
    expect(currentClaim(truth, T0 + 86_400_000).expired).toBe(false);
  });

  it("classifies every state kind deliberately", () => {
    expect(stateKind("agent.idle")).toBe("steady");
    expect(stateKind("agent.waiting_owner")).toBe("steady");
    expect(stateKind("agent.error")).toBe("steady");
    expect(stateKind("evolution.shadow_ready")).toBe("steady");
    expect(stateKind("agent.goal_completed")).toBe("moment");
    expect(stateKind("agent.thinking")).toBe("transient");
    // An unknown state is assumed transient: the safer of the two, because it
    // stops being claimed rather than being asserted forever.
    expect(stateKind("agent.daydreaming")).toBe("transient");
  });

  it("liveEventFor only answers while the evidence is fresh", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([LAB_BUILDING()]), T0);
    expect(liveEventFor(truth, "evolution.building", T0 + 1000)?.module_id).toBe("opp-1");
    expect(liveEventFor(truth, "evolution.building", T0 + 60_000)).toBeNull();
    expect(liveEventFor(truth, "evolution.testing", T0)).toBeNull();
  });

  it("liveSeverity ignores severities that have aged out", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([AGENT_ERROR("critical")]), T0);
    // agent.error is steady, so it stays.
    expect(liveSeverity(truth, T0 + 60_000)).toBe("critical");

    resetSequence();
    const transient = applyResponse(
      emptyTruth(),
      response([event({ state: "agent.thinking", severity: "warning" })]),
      T0,
    );
    expect(liveSeverity(transient, T0 + 1_000)).toBe("warning");
    expect(liveSeverity(transient, T0 + 60_000)).toBe("info");
  });
});

describe("parsing is defensive at the boundary", () => {
  it("refuses an event with no state or timestamp", () => {
    expect(parseEvent(null)).toBeNull();
    expect(parseEvent({ at: iso(0) })).toBeNull();
    expect(parseEvent({ state: "agent.idle" })).toBeNull();
  });

  it("clamps intensity and progress into 0..1", () => {
    const e = parseEvent({ state: "agent.speaking", at: iso(0), intensity: 4.5, progress: -2 });
    expect(e?.intensity).toBe(1);
    expect(e?.progress).toBe(0);
  });

  it("drops content-shaped metadata values", () => {
    const e = parseEvent({
      state: "agent.idle",
      at: iso(0),
      metadata: { kept: 3, ok: true, phase: "symbols", nested: { a: 1 }, list: [1, 2] },
    });
    expect(e?.metadata).toEqual({ kept: 3, ok: true, phase: "symbols" });
  });

  it("keeps a malformed event out of the tail rather than failing the poll", () => {
    const parsed = parseResponse({
      contract_version: 1,
      current: { state: "agent.idle", at: iso(0) },
      events: [{ state: "agent.idle", at: iso(0) }, { nonsense: true }, null],
      sequence: 3,
    });
    expect(parsed?.events).toHaveLength(1);
    expect(parsed?.current?.state).toBe("agent.idle");
  });

  it("survives an unusable timestamp without claiming an age", () => {
    resetSequence();
    const truth = applyResponse(
      emptyTruth(),
      response([event({ state: "agent.thinking", at: "not-a-date" })]),
      T0,
    );
    const claim = currentClaim(truth, T0 + 100_000);
    expect(claim.ageMs).toBeNull();
    expect(claim.expired).toBe(false);
  });
});
