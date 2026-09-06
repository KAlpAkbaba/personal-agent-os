/**
 * The REAL camera source through the one EyeStore, with the browser stubbed at
 * exactly two seams — `navigator.mediaDevices.getUserMedia` and
 * `document.createElement` for `<video>`/`<canvas>` — so what is observed is
 * the `MediaStreamTrack` lifecycle the owner asked to see proven (2026-09-06):
 *
 *   DISABLED -enable-> ENABLING -> track live, loop running -> ACTIVE
 *   ACTIVE  -disable-> DISABLING -> track stopped, loop stopped -> DISABLED
 *   DISABLED -enable-> ACTIVE again (a second stream; the first has no live track)
 *
 * and: exactly one `getUserMedia` per real transition, no duplicate on a double
 * enable, no orphan track, a permission refusal surfaced as `permission_denied`
 * with no track left running. No browser is launched; nothing here draws.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserFrameSource } from "../../app/lib/eye/perception";
import { EyeStore, eyeInstances } from "../../app/lib/eye/store";

type FakeTrack = { kind: "video"; label: string; readyState: "live" | "ended"; stops: number; stop(): void };
type FakeStream = { tracks: FakeTrack[]; getTracks(): FakeTrack[]; getVideoTracks(): FakeTrack[] };

class FakeCamera {
  calls = 0;
  streams: FakeStream[] = [];
  failWith: Error | null = null;
  /** Resolve the pending getUserMedia by hand (to observe ENABLING). */
  manual = false;
  /** The next stream's track is already `ended` when getUserMedia resolves (device yanked mid-prompt). */
  endedOnCreate = false;
  private pending: Array<(s: FakeStream) => void> = [];

  getUserMedia = async (_constraints: MediaStreamConstraints): Promise<FakeStream> => {
    this.calls += 1;
    if (this.failWith) throw this.failWith;
    const track: FakeTrack = {
      kind: "video",
      label: `Fake cam ${this.calls}`,
      readyState: this.endedOnCreate ? "ended" : "live",
      stops: 0,
      stop() {
        this.stops += 1;
        this.readyState = "ended";
      },
    };
    const stream: FakeStream = { tracks: [track], getTracks: () => [track], getVideoTracks: () => [track] };
    this.streams.push(stream);
    if (this.manual) return new Promise<FakeStream>((resolve) => this.pending.push(resolve));
    return stream;
  };

  grant(): void {
    const resolve = this.pending.shift();
    if (resolve) resolve(this.streams[this.streams.length - 1]);
  }

  get liveTracks(): number {
    return this.streams.flatMap((s) => s.tracks).filter((t) => t.readyState === "live").length;
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

/** A store over the REAL `BrowserFrameSource`; the camera it reaches is the stubbed `navigator` below. */
function harness() {
  const durable: string[] = [];
  const store = new EyeStore({
    build: () => ({
      frameSource: new BrowserFrameSource(),
      postObservation: async () => ({ status: "posted" as const }),
      sampleIntervalMs: 20,
      cameraAvailable: () => true,
    }),
    durable: {
      enable: async (reason: string) => void durable.push(`enable:${reason}`),
      disable: async (reason: string) => void durable.push(`disable:${reason}`),
    },
  });
  return { store, durable };
}

let camera: FakeCamera;

beforeEach(() => {
  camera = new FakeCamera();
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: camera.getUserMedia } });
  vi.stubGlobal("document", fakeDocument());
  eyeInstances.reset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the real camera source through the one EyeStore", () => {
  it("enable -> ACTIVE (track live, loop running) -> disable -> DISABLED (track stopped) -> enable -> ACTIVE again, one stream at a time", async () => {
    const { store, durable } = harness();
    expect(store.getSnapshot().state).toBe("DISABLED");

    const on = await store.enable("voice:gözünü aç");
    expect(on).toMatchObject({
      state: "ACTIVE",
      running: true,
      camera_label: "Fake cam 1",
      error_class: null,
      changed: true,
      media_track_ready_state: "live", // read off the MediaStreamTrack itself, right after the action
      action_trace: [
        "request:enable",
        "getUserMedia:called",
        "permission:granted",
        "device:Fake cam 1",
        "stream:1 track live",
        "loop:started",
        "durable:enable",
        "state:ENABLING->ACTIVE",
      ],
    });
    expect(store.getSnapshot().state).toBe("ACTIVE");
    expect(store.getSnapshot().status.running).toBe(true);
    expect(camera.calls).toBe(1);
    expect(camera.liveTracks).toBe(1);
    expect(durable).toEqual(["enable:voice:gözünü aç"]);

    const off = await store.disable("voice:gözünü kapat");
    expect(off).toMatchObject({
      state: "DISABLED",
      running: false,
      camera_label: null,
      changed: true,
      media_track_ready_state: "ended", // the light is off: the track was stopped, not forgotten
      action_trace: ["request:disable", "durable:disable", "loop:stopped", "stream:ended", "state:DISABLING->DISABLED"],
    });
    expect(store.getSnapshot().state).toBe("DISABLED");
    expect(store.getSnapshot().status.running).toBe(false);
    expect(camera.liveTracks).toBe(0); // the track itself is ended, not just forgotten
    expect(camera.streams[0].tracks[0].readyState).toBe("ended");
    expect(camera.streams[0].tracks[0].stops).toBe(1);
    expect(durable).toEqual(["enable:voice:gözünü aç", "disable:voice:gözünü kapat"]);

    // Never reuse a stopped track: the second enable is a second getUserMedia and a
    // second stream; the first stream's track stays ended and is not touched again.
    const again = await store.enable("voice:gözünü aç");
    expect(again).toMatchObject({ state: "ACTIVE", running: true, camera_label: "Fake cam 2", changed: true, media_track_ready_state: "live" });
    expect(camera.calls).toBe(2); // a second real transition, a second stream
    expect(camera.streams).toHaveLength(2);
    expect(camera.liveTracks).toBe(1); // and still exactly one live track: no orphan from the first
    expect(camera.streams[0].tracks[0].readyState).toBe("ended");
    expect(camera.streams[0].tracks[0].stops).toBe(1); // not stopped twice, not restarted
    expect(camera.streams[1].tracks[0].readyState).toBe("live");
    expect(camera.streams[1].tracks[0]).not.toBe(camera.streams[0].tracks[0]);
    expect(again.action_trace).toContain("device:Fake cam 2");
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 2, loopsStarted: 2 });

    await store.disable("owner_stop");
    expect(camera.liveTracks).toBe(0);
    store.dispose();
  });

  it("a stream whose track is already ended on arrival → stream_created_but_track_ended: the track is stopped, nothing durable, no loop", async () => {
    camera.endedOnCreate = true;
    const { store, durable } = harness();
    const on = await store.enable("voice:gözünü aç");
    expect(on).toMatchObject({
      state: "ERROR",
      running: false,
      camera_label: null,
      error_class: "stream_created_but_track_ended",
      changed: true,
      media_track_ready_state: "ended",
    });
    expect(on.action_trace).toEqual([
      "request:enable",
      "getUserMedia:called",
      "permission:granted",
      "device:Fake cam 1",
      "stream:track ended",
      "state:ENABLING->ERROR",
    ]);
    expect(camera.calls).toBe(1);
    expect(camera.streams[0].tracks[0].stops).toBe(1); // stopped on the spot, not kept as an "open" camera
    expect(camera.liveTracks).toBe(0);
    expect(durable).toEqual([]);
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 1, loopsStarted: 0 });
    expect(store.getSnapshot().error).toBe("Kamera açıldı ama görüntü akışı hemen kesildi.");

    // The next enable asks the browser again and gets a fresh, live track.
    camera.endedOnCreate = false;
    const retry = await store.enable("voice:gözünü aç");
    expect(retry).toMatchObject({ state: "ACTIVE", camera_label: "Fake cam 2", media_track_ready_state: "live" });
    expect(camera.calls).toBe(2);
    expect(camera.liveTracks).toBe(1);
    store.dispose();
  });

  it("enable while ACTIVE is idempotent: no second getUserMedia, no second stream, changed=false", async () => {
    const { store } = harness();
    await store.enable("a");
    const second = await store.enable("b");
    expect(second).toMatchObject({ state: "ACTIVE", changed: false });
    expect(camera.calls).toBe(1);
    expect(camera.liveTracks).toBe(1);
    store.dispose();
  });

  it("two enables on one gesture join the same in-flight open: one getUserMedia, ENABLING observable in between", async () => {
    camera.manual = true;
    const { store } = harness();
    const a = store.enable("a");
    const b = store.enable("b");
    await Promise.resolve();
    expect(store.getSnapshot().state).toBe("ENABLING");
    expect(camera.calls).toBe(1);
    camera.grant();
    const [ra, rb] = await Promise.all([a, b]);
    expect(ra.state).toBe("ACTIVE");
    expect(rb.state).toBe("ACTIVE");
    expect(camera.calls).toBe(1);
    expect(camera.liveTracks).toBe(1);
    store.dispose();
  });

  it("disable while DISABLED is a no-op: no error, no fake transition, nothing durable", async () => {
    const { store, durable } = harness();
    const off = await store.disable("voice:gözünü kapat");
    expect(off).toMatchObject({ state: "DISABLED", running: false, changed: false, error_class: null });
    expect(camera.calls).toBe(0);
    expect(durable).toEqual([]);
    store.dispose();
  });

  it("a permission refusal is surfaced as permission_denied with no track running and nothing durable", async () => {
    camera.failWith = new DOMException("Permission denied", "NotAllowedError");
    const { store, durable } = harness();
    const on = await store.enable("voice:gözünü aç");
    expect(on).toMatchObject({ state: "ERROR", running: false, error_class: "permission_denied", media_track_ready_state: null });
    expect(on.action_trace).toEqual(["request:enable", "getUserMedia:called", "getUserMedia:NotAllowedError", "state:ENABLING->ERROR"]);
    expect(store.getSnapshot().state).toBe("ERROR");
    expect(camera.calls).toBe(1);
    expect(camera.liveTracks).toBe(0);
    expect(durable).toEqual([]);
    // The next command still works: the error is a state, not a wedge.
    camera.failWith = null;
    const retry = await store.enable("voice:gözünü aç");
    expect(retry.state).toBe("ACTIVE");
    expect(camera.liveTracks).toBe(1);
    store.dispose();
  });

  it("a disable that arrives while the camera is still opening leaves no track running once the grant lands", async () => {
    camera.manual = true;
    const { store } = harness();
    const opening = store.enable("a");
    await Promise.resolve();
    expect(store.getSnapshot().state).toBe("ENABLING");
    const off = await store.disable("b");
    expect(off.state).toBe("DISABLED");
    camera.grant(); // the browser grants after the owner already said stop
    await opening;
    await new Promise((r) => setTimeout(r, 30));
    expect(store.getSnapshot().state).toBe("DISABLED");
    expect(camera.liveTracks).toBe(0); // released by the session's generation check: no orphan
    store.dispose();
  });
});
