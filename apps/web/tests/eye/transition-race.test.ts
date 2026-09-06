/**
 * The repeated-sequence race (owner's run of 2026-09-06, session 9df439af,
 * 14:31-14:32Z, contract v2): `eye.enable` verified, `eye.disable` verified,
 * then EVERY later enable came back `unverified / state_mismatch` with local
 * DISABLED and the trace `request:stop_local > superseded:stop >
 * state:ENABLING->DISABLED > superseded:disable`. The bus still carried the
 * previous command's `eye.disabled`; the moment the new loop started (before
 * the durable enable, by design) `EyeControl`'s effect obeyed it and the
 * store let a STALE external event cancel a NEWER transition.
 *
 * This file runs `enable -> disable -> enable -> disable -> enable` through
 * the REAL `BrowserFrameSource` (the browser stubbed at `getUserMedia` and
 * `<video>`/`<canvas>`, exactly as `browser-frame-source.test.ts`) and the
 * REAL `EyeStore`, 24 seeded times, and at each run:
 *
 * - a bus `eye.disabled` view (ageMs 200-3000, not expired) is fed to
 *   `stopLocalIfStale` at a different stage of the second and third enable
 *   (before getUserMedia resolves / after it resolves / after the loop
 *   started with the durable enable still on the wire / after ACTIVE);
 * - in a third of the runs the enable is preceded by an enable that was
 *   superseded while its prompt was open, and that older generation's
 *   getUserMedia resolves LATE, before or after the real prompt is granted;
 * - in half of the runs the disable's durable POST is held while the loop
 *   keeps ticking (status callbacks of the earlier generation arrive during
 *   DISABLING).
 *
 * After every transition: one terminal result per requested action with the
 * requested state; the generation only ever grows and the trace's first stage
 * names it; no enable trace contains `request:stop_local` or a supersede;
 * every enable after a disable opened a NEW stream (`getUserMedia` called
 * again, a different `track:` id in the trace, `readyState === "live"`); at
 * most one live track and one loop at any moment. And, at the end of every
 * run, an ACTIVE store offered a GENUINELY newer `eye.disabled` (another
 * device) DOES stop — the rule declines stale events, not real ones.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserFrameSource } from "../../app/lib/eye/perception";
import { type BusEyeEvent, EyeStore, type LocalEyeResult, eyeInstances } from "../../app/lib/eye/store";

// ------------------------------------------------------------ the browser

type FakeTrack = { kind: "video"; id: string; label: string; readyState: "live" | "ended"; stops: number; stop(): void };
/** `granted`: the prompt was answered and the browser handed the stream over — only then does its track exist for the page. */
type FakeStream = { granted: boolean; tracks: FakeTrack[]; getTracks(): FakeTrack[]; getVideoTracks(): FakeTrack[] };

class FakeCamera {
  calls = 0;
  streams: FakeStream[] = [];
  /** Each open prompt, oldest first, bound to the stream IT will hand over. */
  private pending: Array<() => void> = [];

  getUserMedia = async (_constraints: MediaStreamConstraints): Promise<FakeStream> => {
    this.calls += 1;
    const n = this.calls;
    const track: FakeTrack = {
      kind: "video",
      id: `${String(n).padStart(8, "0")}-4c1e-4a6b-9f00-${String(n).padStart(12, "0")}`,
      label: `Fake cam ${n}`,
      readyState: "live",
      stops: 0,
      stop() {
        this.stops += 1;
        this.readyState = "ended";
      },
    };
    const stream: FakeStream = { granted: false, tracks: [track], getTracks: () => [track], getVideoTracks: () => [track] };
    this.streams.push(stream);
    return new Promise<FakeStream>((resolve) =>
      this.pending.push(() => {
        stream.granted = true;
        resolve(stream);
      }),
    );
  };

  /** The owner answers the prompt at `index` (0 = the oldest still open). */
  grant(index = 0): void {
    this.pending.splice(index, 1)[0]?.();
  }

  get open(): number {
    return this.pending.length;
  }

  /** Live tracks the page actually holds: a prompt not yet answered has handed nothing over. */
  get liveTracks(): number {
    return this.streams
      .filter((s) => s.granted)
      .flatMap((s) => s.tracks)
      .filter((t) => t.readyState === "live").length;
  }

  /** The short id the trace shows for stream `n` (1-based). */
  shortId(n: number): string {
    return this.streams[n - 1].tracks[0].id.slice(0, 8);
  }
}

function fakeDocument() {
  return {
    createElement(tag: string) {
      if (tag === "video") {
        return {
          muted: false,
          playsInline: false,
          srcObject: null as unknown,
          readyState: 1,
          videoWidth: 320,
          videoHeight: 240,
          play: async () => {},
          addEventListener: () => {},
        };
      }
      if (tag === "canvas") {
        return {
          width: 0,
          height: 0,
          getContext: () => ({
            drawImage: () => {},
            getImageData: (_x: number, _y: number, w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4) }),
          }),
        };
      }
      throw new Error(`unexpected element ${tag}`);
    },
  };
}

// ------------------------------------------------------------- the store

function harness() {
  let now = 1_700_000_000_000;
  const durable: string[] = [];
  let posts = 0;
  const held: { enable: Array<() => void>; disable: Array<() => void> } = { enable: [], disable: [] };
  const hold = { enable: false, disable: false };
  const durableCall = (kind: "enable" | "disable") => (reason: string) => {
    durable.push(`${kind}:${reason}`);
    if (!hold[kind]) return Promise.resolve();
    return new Promise<void>((resolve) => held[kind].push(resolve));
  };
  const store = new EyeStore({
    build: () => ({
      frameSource: new BrowserFrameSource(),
      postObservation: async () => {
        posts += 1;
        return { status: "posted" as const };
      },
      sampleIntervalMs: 20,
      cameraAvailable: () => true,
      now: () => now,
    }),
    durable: { enable: durableCall("enable"), disable: durableCall("disable") },
  });
  return {
    store,
    durable,
    get posts() {
      return posts;
    },
    get now() {
      return now;
    },
    hold: (kind: "enable" | "disable") => {
      hold[kind] = true;
    },
    release: (kind: "enable" | "disable") => {
      hold[kind] = false;
      for (const resolve of held[kind].splice(0)) resolve();
    },
    /** Advance the store's clock and the timers together. */
    advance: async (ms: number) => {
      now += ms;
      await vi.advanceTimersByTimeAsync(ms);
    },
    flush: () => vi.advanceTimersByTimeAsync(0),
  };
}

/** A small deterministic PRNG (mulberry32), so a failing run is reproducible by its seed. */
function prng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Recorded = { kind: "enable" | "disable"; expected: "ACTIVE" | "DISABLED" | "superseded"; result: LocalEyeResult };

/** When, during the second/third enable, the stale bus view is offered. */
const STAGES = ["before-getUserMedia", "after-getUserMedia", "after-loop-before-durable", "after-active"] as const;
type Stage = (typeof STAGES)[number];

let camera: FakeCamera;

beforeEach(() => {
  vi.useFakeTimers();
  camera = new FakeCamera();
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: camera.getUserMedia } });
  vi.stubGlobal("document", fakeDocument());
  eyeInstances.reset();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const RUNS = 24;

describe("enable -> disable -> enable -> disable -> enable, under every interleaving a stale bus can produce", () => {
  for (let run = 0; run < RUNS; run += 1) {
    const stage: Stage = STAGES[run % STAGES.length];
    const latePrompt = run % 3 === 1;
    const lateAfterNewGrant = run % 6 === 4; // the old prompt lands AFTER the new one was granted
    const holdDisable = run % 2 === 1;
    const seed = run + 1;

    it(`run ${run + 1}/${RUNS} (seed ${seed}): stale bus ${stage}${latePrompt ? `, previous generation's prompt lands late${lateAfterNewGrant ? " after the new grant" : ""}` : ""}${holdDisable ? ", disable's durable held while the loop ticks" : ""}`, async () => {
      const rnd = prng(seed);
      const t = harness();
      const recorded: Recorded[] = [];
      let lastGen = 0;
      let lastTrack: string | null = null;
      let openedStreams = 0;
      let loops = 0;

      const stale = (): BusEyeEvent => ({ status: "disabled", ageMs: 200 + Math.floor(rnd() * 2800), expired: false });

      /** What must hold at EVERY checkpoint, whatever is in flight. */
      const invariants = () => {
        expect(camera.liveTracks).toBeLessThanOrEqual(1);
        const snap = t.store.getSnapshot();
        expect(snap.generation).toBeGreaterThanOrEqual(lastGen);
        if (snap.state === "ACTIVE") {
          expect(camera.liveTracks).toBe(1);
          expect(snap.status.running).toBe(true);
        }
        if (snap.state === "DISABLED") {
          expect(camera.liveTracks).toBe(0);
          expect(snap.status.running).toBe(false);
        }
      };

      /** Offer the stale view; it must change nothing, and must never leave `request:stop_local` behind. */
      const offerStale = () => {
        const before = t.store.getSnapshot();
        expect(t.store.stopLocalIfStale(stale())).toBe(false);
        const after = t.store.getSnapshot();
        expect(after.state).toBe(before.state);
        expect(after.generation).toBe(before.generation);
        expect(after.status.running).toBe(before.status.running);
        expect(after.lastActionTrace).not.toContain("request:stop_local");
        invariants();
      };

      const settledEnable = (result: LocalEyeResult, expected: "ACTIVE" | "superseded") => {
        recorded.push({ kind: "enable", expected, result });
        expect(result.action_trace).not.toContain("request:stop_local");
        if (expected === "superseded") {
          expect(result).toMatchObject({ state: "DISABLED", running: false, changed: false });
          expect(result.action_trace).toContain("superseded:disable");
          return;
        }
        expect(result).toMatchObject({ state: "ACTIVE", running: true, changed: true, media_track_ready_state: "live", error_class: null });
        expect(result.action_trace.some((s) => s.startsWith("superseded:"))).toBe(false);
        const gen = Number(result.action_trace[0].replace("gen:", ""));
        expect(gen).toBeGreaterThan(lastGen);
        lastGen = gen;
        // A NEW stream: a new getUserMedia, a new track id in the trace, and it is the live one.
        const track = result.action_trace.find((s) => s.startsWith("track:"))?.slice("track:".length) ?? null;
        expect(track).not.toBeNull();
        expect(track).not.toBe(lastTrack);
        expect(track).toBe(camera.shortId(camera.streams.length));
        expect(camera.streams[camera.streams.length - 1].tracks[0].readyState).toBe("live");
        lastTrack = track;
        loops += 1;
        expect(eyeInstances.snapshot().loopsStarted).toBe(loops);
        expect(camera.liveTracks).toBe(1);
        expect(t.store.getSnapshot().generation).toBe(gen);
        invariants();
      };

      const settledDisable = (result: LocalEyeResult) => {
        recorded.push({ kind: "disable", expected: "DISABLED", result });
        expect(result).toMatchObject({ state: "DISABLED", running: false, changed: true, error_class: null });
        const gen = Number(result.action_trace[0].replace("gen:", ""));
        expect(gen).toBeGreaterThan(lastGen);
        lastGen = gen;
        expect(result.action_trace).toContain("durable:disable");
        expect(result.action_trace).toContain("loop:stopped");
        if (lastTrack) {
          expect(result).toMatchObject({ media_track_ready_state: "ended" });
          expect(result.action_trace).toContain(`track:${lastTrack}`);
          expect(result.action_trace).toContain("stream:ended");
        }
        expect(camera.liveTracks).toBe(0);
        expect(t.store.getSnapshot().generation).toBe(gen);
        invariants();
      };

      /** One enable, with the stale bus offered at `at`, and optionally a superseded prelude whose prompt lands late. */
      const enable = async (label: string, at: Stage | null, withLatePrompt: boolean) => {
        let oldPromptOpen = false;
        if (withLatePrompt) {
          // A prelude enable, superseded while its prompt is open; that prompt is answered during the real enable below.
          const prelude = t.store.enable(`${label}:prelude`);
          await t.flush();
          expect(t.store.getSnapshot().state).toBe("ENABLING");
          openedStreams += 1;
          expect(camera.calls).toBe(openedStreams);
          const off = await t.store.disable(`${label}:prelude-stop`);
          settledDisable(off);
          settledEnable(await prelude, "superseded");
          oldPromptOpen = true;
          expect(camera.open).toBe(1);
        }

        const pending = t.store.enable(label);
        await t.flush();
        expect(t.store.getSnapshot().state).toBe("ENABLING");
        openedStreams += 1;
        expect(camera.calls).toBe(openedStreams);
        if (at === "before-getUserMedia") offerStale();

        if (oldPromptOpen && !lateAfterNewGrant) {
          camera.grant(0); // the OLD prompt, first: generation N-2's camera arrives mid-enable
          await t.flush();
          expect(t.store.getSnapshot().state).toBe("ENABLING");
          expect(camera.liveTracks).toBe(0); // released by the source; the real prompt is still open
          oldPromptOpen = false;
          invariants();
        }

        if (at === "after-loop-before-durable") t.hold("enable");
        camera.grant(oldPromptOpen ? 1 : 0); // the real prompt
        if (at === "after-getUserMedia") {
          await Promise.resolve();
          offerStale();
        }
        await t.flush();
        if (at === "after-loop-before-durable") {
          expect(t.store.getSnapshot().state).toBe("ENABLING");
          expect(t.store.getSnapshot().status.running).toBe(true); // the exact moment the old effect fired stop_local
          offerStale();
          t.release("enable");
        }
        if (oldPromptOpen) {
          // The OLD prompt lands after the new one was granted: it must release only its own stream.
          camera.grant(0);
          await t.flush();
          oldPromptOpen = false;
        }
        const result = await pending;
        settledEnable(result, "ACTIVE");
        if (withLatePrompt) expect(t.store.getSnapshot().lastActionTrace.some((s) => /^ignored:gen\d+$/.test(s))).toBe(true);
        if (at === "after-active") offerStale();
        // Exactly one loop: one sample per interval, never two.
        await t.flush();
        const before = t.posts;
        await t.advance(40);
        expect(t.posts - before).toBeGreaterThanOrEqual(1);
        expect(t.posts - before).toBeLessThanOrEqual(2);
        invariants();
      };

      const disable = async (label: string) => {
        if (holdDisable) t.hold("disable");
        const pending = t.store.disable(label);
        await t.flush();
        if (holdDisable) {
          expect(t.store.getSnapshot().state).toBe("DISABLING");
          const gen = t.store.getSnapshot().generation;
          // The loop of the earlier generation keeps ticking while the durable disable is on the wire:
          // its status callbacks arrive during DISABLING and move nothing.
          const before = t.posts;
          await t.advance(60);
          expect(t.posts).toBeGreaterThan(before);
          expect(t.store.getSnapshot().state).toBe("DISABLING");
          expect(t.store.getSnapshot().generation).toBe(gen);
          expect(t.store.stopLocalIfStale(stale())).toBe(false); // a bus stop during DISABLING: nothing
          expect(t.store.getSnapshot().state).toBe("DISABLING");
          t.release("disable");
        }
        settledDisable(await pending);
        // DISABLED: the stale view has nothing to stop, and the previous generation's loop never returns.
        expect(t.store.stopLocalIfStale(stale())).toBe(false);
        const before = t.posts;
        await t.advance(60);
        expect(t.posts).toBe(before);
        invariants();
      };

      // ---- the owner's sequence
      await enable("voice:Gözünü aç.", null, false);
      await t.advance(1000 + Math.floor(rnd() * 5000));
      await disable("voice:Gözünü kapat.");
      await t.advance(1000 + Math.floor(rnd() * 35000)); // 6 s, 16 s, 35 s later in the owner's run
      await enable("voice:Gözünü aç.", stage, latePrompt);
      await t.advance(1000 + Math.floor(rnd() * 5000));
      await disable("voice:Gözünü kapat.");
      await t.advance(1000 + Math.floor(rnd() * 35000));
      await enable("voice:Gözünü aç.", STAGES[(run + 1) % STAGES.length], latePrompt);

      // ---- one terminal result per requested action, with the requested state
      const enables = recorded.filter((r) => r.kind === "enable");
      const disables = recorded.filter((r) => r.kind === "disable");
      expect(enables.filter((r) => r.expected === "ACTIVE")).toHaveLength(3);
      expect(disables).toHaveLength(latePrompt ? 4 : 2);
      expect(enables.filter((r) => r.expected === "superseded")).toHaveLength(latePrompt ? 2 : 0);
      for (const r of recorded) {
        if (r.expected === "superseded") continue;
        expect(r.result.state).toBe(r.expected);
      }
      expect(camera.calls).toBe(openedStreams);
      expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: openedStreams, loopsStarted: 3 });
      expect(t.durable.filter((d) => d.startsWith("enable:"))).toHaveLength(3); // the superseded preludes never told the Cloud Core
      expect(camera.liveTracks).toBe(1);
      expect(t.store.getSnapshot().state).toBe("ACTIVE");

      // ---- and a GENUINELY newer eye.disabled (another device, 200 ms ago) DOES stop the ACTIVE store
      await t.advance(1000);
      const disablesBefore = t.durable.filter((d) => d.startsWith("disable:")).length;
      expect(t.store.stopLocalIfStale({ status: "disabled", ageMs: 200, expired: false })).toBe(true);
      const snap = t.store.getSnapshot();
      expect(snap.state).toBe("DISABLED");
      expect(snap.status.running).toBe(false);
      expect(snap.generation).toBe(lastGen + 1);
      expect(snap.lastActionTrace).toEqual([
        `gen:${lastGen + 1}`,
        "request:stop_local",
        "loop:stopped",
        "stream:ended",
        `track:${lastTrack}`,
        "state:ACTIVE->DISABLED",
      ]);
      expect(camera.liveTracks).toBe(0);
      expect(t.durable.filter((d) => d.startsWith("disable:"))).toHaveLength(disablesBefore); // no durable call: the Cloud Core already knows
      const before = t.posts;
      await t.advance(60);
      expect(t.posts).toBe(before);
      t.store.dispose();
    });
  }
});
