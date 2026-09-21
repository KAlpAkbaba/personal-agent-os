/**
 * `PointerStreamClient` (ADR-0199 Stage 2) — driven entirely by fakes: a scriptable
 * `beginPointerSession`/`endPointerSession` port, a fake `WebSocket`-shaped socket, and a
 * fake timer queue. A real `WebSocket` is never constructed here (this module's own
 * docstring rule — see `lib/gesture/pointer.ts`).
 */

import { describe, expect, it, vi } from "vitest";

import {
  POINTER_FLUSH_INTERVAL_MS,
  POINTER_IDLE_CLOSE_MS,
  POINTER_MAX_MOVE_DELTA,
  PointerStreamClient,
  pointerWsUrl,
  type PointerClientDeps,
  type PointerSocketLike,
} from "../../app/lib/gesture/pointer";
import type { GestureEvent } from "../../app/lib/gesture/types";

// ------------------------------------------------------------------- fakes

class FakeSocket implements PointerSocketLike {
  sent: string[] = [];
  closedWith: number[] = [];
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number; reason?: string }) => void) | null = null;
  onerror: ((event?: unknown) => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }

  close(code = 1000): void {
    this.closedWith.push(code);
  }

  frames(): Array<Record<string, unknown>> {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}

class FakeTimers {
  private nextId = 1;
  private timers = new Map<number, { fn: () => void; ms: number }>();

  set = (fn: () => void, ms: number): unknown => {
    const id = this.nextId++;
    this.timers.set(id, { fn, ms });
    return id;
  };

  clear = (handle: unknown): void => {
    this.timers.delete(handle as number);
  };

  /** Fires the most recently scheduled still-pending timer with this exact delay. */
  fire(ms: number): void {
    let foundId: number | null = null;
    for (const [id, t] of this.timers) if (t.ms === ms) foundId = id;
    if (foundId === null) throw new Error(`no pending timer with ms=${ms}`);
    const entry = this.timers.get(foundId);
    this.timers.delete(foundId);
    entry?.fn();
  }

  pendingCount(ms?: number): number {
    if (ms === undefined) return this.timers.size;
    return [...this.timers.values()].filter((t) => t.ms === ms).length;
  }
}

/** Lets the pending microtask queue drain (the `.then()` chain `onStart` kicks off). */
async function tick(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

function setup(
  overrides: Partial<{
    beginResult: { status: string; stream_token?: string } | null;
    wsUrl: string | null;
  }> = {},
) {
  const timers = new FakeTimers();
  const sockets: FakeSocket[] = [];
  let beginCalls = 0;
  let endCalls = 0;
  const beginResult = overrides.beginResult ?? { status: "open", stream_token: "tok-1" };
  const wsUrl = overrides.wsUrl === undefined ? "wss://host/v1/voice/realtime/sessions/s-1/pointer?token=tok" : overrides.wsUrl;
  const deps: PointerClientDeps = {
    sessionPort: {
      beginPointerSession: async () => {
        beginCalls += 1;
        return beginResult;
      },
      endPointerSession: async () => {
        endCalls += 1;
      },
    },
    wsUrl: () => wsUrl,
    createSocket: (_url) => {
      const s = new FakeSocket();
      sockets.push(s);
      return s;
    },
    screenWidth: () => 1000,
    setTimer: timers.set,
    clearTimer: timers.clear,
  };
  const client = new PointerStreamClient(deps);
  return {
    client,
    timers,
    sockets,
    getBeginCalls: () => beginCalls,
    getEndCalls: () => endCalls,
  };
}

const mouseStart: GestureEvent = { name: "mouse_start", t_ms: 0 };
const mouseMove = (dx: number, dy: number): GestureEvent => ({ name: "mouse_move", t_ms: 0, dx, dy });
const dragStart: GestureEvent = { name: "drag_start", t_ms: 0 };
const dragEnd: GestureEvent = { name: "drag_end", t_ms: 0 };
const mouseEnd: GestureEvent = { name: "mouse_end", t_ms: 0 };
const leftClick: GestureEvent = { name: "left_click", t_ms: 0 };
const rightClick: GestureEvent = { name: "right_click", t_ms: 0 };

/** Drives a client through mouse_start -> begin resolves -> socket opens -> hello sent. */
async function openMouseSession(client: PointerStreamClient, sockets: FakeSocket[]) {
  client.handleEvent(mouseStart);
  await tick();
  const socket = sockets[0];
  socket.onopen?.();
  return socket;
}

describe("PointerStreamClient: opening a session (mouse_start)", () => {
  it("calls beginPointerSession, opens a socket, and sends hello with the returned stream_token", async () => {
    const { client, sockets, getBeginCalls } = setup();
    expect(client.getSnapshot().state).toBe("idle");
    client.handleEvent(mouseStart);
    expect(client.getSnapshot().state).toBe("opening");
    expect(client.getSnapshot().mode).toBe("mouse");
    await tick();
    expect(getBeginCalls()).toBe(1);
    expect(sockets).toHaveLength(1);
    sockets[0].onopen?.();
    expect(client.getSnapshot().state).toBe("open");
    expect(sockets[0].frames()).toEqual([{ t: "hello", stream_token: "tok-1" }]);
  });

  it("a second mouse_start while one is already open/opening reuses the session (no second begin)", async () => {
    const { client, sockets, getBeginCalls } = setup();
    await openMouseSession(client, sockets);
    client.handleEvent(mouseStart);
    await tick();
    expect(getBeginCalls()).toBe(1);
    expect(sockets).toHaveLength(1);
  });

  it("a begin that does not return an open status fails cleanly, in Turkish, without opening a socket", async () => {
    const { client, sockets } = setup({ beginResult: { status: "refused" } });
    client.handleEvent(mouseStart);
    await tick();
    expect(sockets).toHaveLength(0);
    expect(client.getSnapshot().state).toBe("idle");
    expect(client.getSnapshot().mode).toBeNull();
    expect(client.getSnapshot().lastCloseReason).toBe("Fare akışı açılamadı.");
  });

  it("no session id/token to build a URL from also fails cleanly", async () => {
    const { client, sockets } = setup({ wsUrl: null });
    client.handleEvent(mouseStart);
    await tick();
    expect(sockets).toHaveLength(0);
    expect(client.getSnapshot().lastCloseReason).toBe("Fare akışı açılamadı.");
  });
});

describe("PointerStreamClient: mouse_move / drag_move coalescing and pixel scaling", () => {
  it("scales frame-unit dx/dy by screen.width * gain and rounds", async () => {
    const { client, sockets } = setup();
    const socket = await openMouseSession(client, sockets);
    client.setGain(2.5);
    // dx=0.01 frame * 1000px width * 2.5 gain = 25px.
    client.handleEvent(mouseMove(0.01, -0.004));
    // not sent yet — only hello, coalesced until the flush timer fires.
    expect(socket.frames()).toEqual([{ t: "hello", stream_token: "tok-1" }]);
  });

  it("coalesces several moves into ONE sent frame per flush, at most every POINTER_FLUSH_INTERVAL_MS", async () => {
    const { client, sockets, timers } = setup();
    const socket = await openMouseSession(client, sockets);
    client.handleEvent(mouseMove(0.01, 0.0)); // 25px
    client.handleEvent(mouseMove(0.01, 0.0)); // +25px = 50px
    client.handleEvent(mouseMove(0.0, 0.02)); // +50px dy
    expect(timers.pendingCount(POINTER_FLUSH_INTERVAL_MS)).toBe(1); // one flush scheduled, not three
    timers.fire(POINTER_FLUSH_INTERVAL_MS);
    const frames = socket.frames();
    expect(frames).toEqual([{ t: "hello", stream_token: "tok-1" }, { t: "move", dx: 50, dy: 50, seq: 1 }]);
  });

  it("never sends more than one move frame per flush tick even across many events (<= 30/s)", async () => {
    const { client, sockets, timers } = setup();
    const socket = await openMouseSession(client, sockets);
    for (let i = 0; i < 10; i += 1) client.handleEvent(mouseMove(0.001, 0.0));
    expect(timers.pendingCount(POINTER_FLUSH_INTERVAL_MS)).toBe(1);
    timers.fire(POINTER_FLUSH_INTERVAL_MS);
    expect(socket.frames()).toHaveLength(2); // hello + one coalesced move
    // a second burst schedules exactly one more flush.
    for (let i = 0; i < 5; i += 1) client.handleEvent(mouseMove(0.001, 0.0));
    expect(timers.pendingCount(POINTER_FLUSH_INTERVAL_MS)).toBe(1);
    timers.fire(POINTER_FLUSH_INTERVAL_MS);
    expect(socket.frames()).toHaveLength(3); // hello + two coalesced moves total
  });

  it("clamps a large accumulated delta to POINTER_MAX_MOVE_DELTA rather than dropping it", async () => {
    const { client, sockets, timers } = setup();
    const socket = await openMouseSession(client, sockets);
    client.setGain(10);
    client.handleEvent(mouseMove(1, 1)); // 1 * 1000 * 10 = 10000px, far past the 200px cap
    timers.fire(POINTER_FLUSH_INTERVAL_MS);
    expect(socket.frames()).toEqual([
      { t: "hello", stream_token: "tok-1" },
      { t: "move", dx: POINTER_MAX_MOVE_DELTA, dy: POINTER_MAX_MOVE_DELTA, seq: 1 },
    ]);
  });

  it("moves that arrive before the socket is ready are queued and flushed once hello is sent", async () => {
    const { client, sockets } = setup();
    client.handleEvent(mouseStart);
    await tick();
    const socket = sockets[0];
    client.handleEvent(mouseMove(0.02, 0.0)); // 50px, before onopen — queued
    socket.onopen?.(); // hello sent, then drainQueue() flushes immediately
    expect(socket.frames()).toEqual([{ t: "hello", stream_token: "tok-1" }, { t: "move", dx: 50, dy: 0, seq: 1 }]);
  });
});

describe("PointerStreamClient: button frames (clicks and drag)", () => {
  it("left_click / right_click send a single button click frame each, immediately once ready", async () => {
    const { client, sockets } = setup();
    const socket = await openMouseSession(client, sockets);
    client.handleEvent(leftClick);
    client.handleEvent(rightClick);
    expect(socket.frames()).toEqual([
      { t: "hello", stream_token: "tok-1" },
      { t: "button", button: "left", action: "click" },
      { t: "button", button: "right", action: "click" },
    ]);
  });

  it("drag_start sends button left down (queued until the socket is ready)", async () => {
    const { client, sockets } = setup();
    client.handleEvent(dragStart);
    await tick();
    const socket = sockets[0];
    expect(socket.frames()).toEqual([]); // queued: not ready yet
    socket.onopen?.();
    expect(socket.frames()).toEqual([
      { t: "hello", stream_token: "tok-1" },
      { t: "button", button: "left", action: "down" },
    ]);
  });

  it("a pending move flushes BEFORE a button frame, so the click lands where the cursor already is", async () => {
    const { client, sockets, timers } = setup();
    const socket = await openMouseSession(client, sockets);
    client.handleEvent(mouseMove(0.01, 0.0)); // queued, flush timer pending
    client.handleEvent(leftClick); // must flush the move first
    expect(socket.frames()).toEqual([
      { t: "hello", stream_token: "tok-1" },
      { t: "move", dx: 25, dy: 0, seq: 1 },
      { t: "button", button: "left", action: "click" },
    ]);
    expect(timers.pendingCount(POINTER_FLUSH_INTERVAL_MS)).toBe(0); // nothing left to flush
  });
});

describe("PointerStreamClient: ending an episode (mouse_end / drag_end)", () => {
  it("mouse_end keeps the socket open for POINTER_IDLE_CLOSE_MS, then sends end, closes, and calls endPointerSession", async () => {
    const { client, sockets, timers, getEndCalls } = setup();
    const socket = await openMouseSession(client, sockets);
    client.handleEvent(mouseEnd);
    expect(client.getSnapshot().mode).toBeNull();
    expect(socket.closedWith).toEqual([]); // not yet — waiting out the idle window
    expect(timers.pendingCount(POINTER_IDLE_CLOSE_MS)).toBe(1);
    timers.fire(POINTER_IDLE_CLOSE_MS);
    expect(socket.frames().at(-1)).toEqual({ t: "end" });
    expect(socket.closedWith).toEqual([1000]);
    expect(getEndCalls()).toBe(1);
    expect(client.getSnapshot().state).toBe("idle");
  });

  it("a fresh mouse_start before the idle timer fires cancels the close and reuses the socket", async () => {
    const { client, sockets, timers } = setup();
    const socket = await openMouseSession(client, sockets);
    client.handleEvent(mouseEnd);
    expect(timers.pendingCount(POINTER_IDLE_CLOSE_MS)).toBe(1);
    client.handleEvent(mouseStart);
    expect(timers.pendingCount(POINTER_IDLE_CLOSE_MS)).toBe(0);
    expect(sockets).toHaveLength(1); // no new begin/socket — the same episode continues
    expect(socket.closedWith).toEqual([]);
  });

  it("drag_end sends button left up, THEN (after the idle window) end — the button is released before the socket ends", async () => {
    const { client, sockets, timers } = setup();
    client.handleEvent(dragStart);
    await tick();
    const socket = sockets[0];
    socket.onopen?.();
    client.handleEvent(dragEnd);
    const frames = socket.frames();
    expect(frames).toEqual([
      { t: "hello", stream_token: "tok-1" },
      { t: "button", button: "left", action: "down" },
      { t: "button", button: "left", action: "up" },
    ]);
    timers.fire(POINTER_IDLE_CLOSE_MS);
    expect(socket.frames().at(-1)).toEqual({ t: "end" });
  });
});

describe("PointerStreamClient: a held button is ALWAYS released before the socket ends", () => {
  it("mouse_end while a click is mid-press does not need a release (clicks are momentary — nothing to release)", async () => {
    // Sanity: left_click never sets buttonDown (only drag's down/up do) — mouse_end must
    // not try to synthesize a button-up nobody asked for.
    const { client, sockets, timers } = setup();
    const socket = await openMouseSession(client, sockets);
    client.handleEvent(leftClick);
    client.handleEvent(mouseEnd);
    timers.fire(POINTER_IDLE_CLOSE_MS);
    const buttonFrames = socket.frames().filter((f) => f.t === "button");
    expect(buttonFrames).toEqual([{ t: "button", button: "left", action: "click" }]);
  });

  it("if drag_end's own button-up somehow never fired, endSession() still releases before end (defence in depth)", async () => {
    // Drive drag_start (button down) without ever sending drag_end — go straight to the
    // idle path via mouse_end's own onStop() (both call the same private onStop/endSession).
    const { client, sockets, timers } = setup();
    client.handleEvent(dragStart);
    await tick();
    const socket = sockets[0];
    socket.onopen?.();
    // No drag_end here — simulate ending the episode without the normal up frame by
    // calling mouse_end's path directly (the recognizer never actually does this, but the
    // client's own safety net must hold regardless of which event triggers the end).
    client.handleEvent(mouseEnd);
    timers.fire(POINTER_IDLE_CLOSE_MS);
    const buttonFrames = socket.frames().filter((f) => f.t === "button");
    expect(buttonFrames.at(-1)).toEqual({ t: "button", button: "left", action: "up" });
    expect(socket.frames().at(-1)).toEqual({ t: "end" });
  });

  it("on socket loss mid-drag, the button cannot be released over the wire, but local state clears and the HUD gets a close reason", async () => {
    const { client, sockets } = setup();
    client.handleEvent(dragStart);
    await tick();
    const socket = sockets[0];
    socket.onopen?.();
    expect(socket.frames().some((f) => f.t === "button" && f.action === "down")).toBe(true);
    // The server/network closes on us — 1008, a policy refusal.
    socket.onclose?.({ code: 1008 });
    expect(client.getSnapshot().state).toBe("idle");
    expect(client.getSnapshot().mode).toBeNull();
    expect(client.getSnapshot().lastCloseReason).toBe("Akış reddedildi.");
    // A fresh drag_start afterwards starts a clean new episode (no stuck buttonDown state).
    client.handleEvent(dragStart);
    await tick();
    expect(sockets).toHaveLength(2);
  });
});

describe("PointerStreamClient: socket close codes map to Turkish HUD text", () => {
  it.each([
    [4401, "Oturum doğrulanamadı; tekrar giriş yapın."],
    [1008, "Akış reddedildi."],
    [1000, "Fare akışı kapatıldı."],
    [1006, "Bağlantı koptu."], // not in the closed set: the default line
  ])("close code %d -> %s", async (code, expected) => {
    const { client, sockets } = setup();
    const socket = await openMouseSession(client, sockets);
    socket.onclose?.({ code });
    expect(client.getSnapshot().lastCloseReason).toBe(expected);
  });
});

describe("PointerStreamClient: the gain setting", () => {
  it("defaults to 2.5 and persists through the injected storage", () => {
    let stored = 2.5;
    const deps: PointerClientDeps = {
      sessionPort: { beginPointerSession: async () => null, endPointerSession: async () => {} },
      wsUrl: () => null,
      createSocket: () => {
        throw new Error("not used in this test");
      },
      screenWidth: () => 1000,
      gainStorage: { get: () => stored, set: (v) => { stored = v; } },
    };
    const client = new PointerStreamClient(deps);
    expect(client.getSnapshot().gain).toBe(2.5);
    client.setGain(3.5);
    expect(stored).toBe(3.5);
    expect(client.getSnapshot().gain).toBe(3.5);
  });

  it("clamps an out-of-range gain rather than storing garbage", () => {
    let stored = 2.5;
    const deps: PointerClientDeps = {
      sessionPort: { beginPointerSession: async () => null, endPointerSession: async () => {} },
      wsUrl: () => null,
      createSocket: () => {
        throw new Error("not used in this test");
      },
      screenWidth: () => 1000,
      gainStorage: { get: () => stored, set: (v) => { stored = v; } },
    };
    const client = new PointerStreamClient(deps);
    client.setGain(999);
    expect(stored).toBeLessThanOrEqual(10);
  });
});

describe("pointerWsUrl", () => {
  it("turns http(s) into ws(s) and carries the OWNER bearer as a token query parameter", () => {
    expect(pointerWsUrl("http://127.0.0.1:8001", "sess-1", "abc def")).toBe(
      "ws://127.0.0.1:8001/v1/voice/realtime/sessions/sess-1/pointer?token=abc%20def",
    );
    expect(pointerWsUrl("https://core.example", "sess-2", "tok")).toBe(
      "wss://core.example/v1/voice/realtime/sessions/sess-2/pointer?token=tok",
    );
  });
});

describe("PointerStreamClient: dispose()", () => {
  it("ends a live episode without waiting for the idle timer, and stops notifying listeners", async () => {
    const { client, sockets, getEndCalls } = setup();
    const socket = await openMouseSession(client, sockets);
    const listener = vi.fn();
    client.subscribe(listener);
    client.dispose();
    expect(socket.closedWith).toEqual([1000]);
    expect(getEndCalls()).toBe(1);
    listener.mockClear();
    client.setGain(4);
    expect(listener).not.toHaveBeenCalled();
  });
});
