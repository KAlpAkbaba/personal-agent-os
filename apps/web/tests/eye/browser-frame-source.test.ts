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

type FakeTrack = { kind: "video"; label: string; readyState: "live" | "ended"; stop(): void };
type FakeStream = { tracks: FakeTrack[]; getTracks(): FakeTrack[]; getVideoTracks(): FakeTrack[] };

class FakeCamera {
  calls = 0;
  streams: FakeStream[] = [];
  failWith: Error | null = null;
  /** Resolve the pending getUserMedia by hand (to observe ENABLING). */
  manual = false;
  private pending: Array<(s: FakeStream) => void> = [];

  getUserMedia = async (_constraints: MediaStreamConstraints): Promise<FakeStream> => {
    this.calls += 1;
    if (this.failWith) throw this.failWith;
    const track: FakeTrack = {
      kind: "video",
      label: `Fake cam ${this.calls}`,
      readyState: "live",
      stop() {
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

function harness(camera: FakeCamera) {
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
    const { store, durable } = harness(camera);
    expect(store.getSnapshot().state).toBe("DISABLED");

    const on = await store.enable("voice:gözünü aç");
    expect(on).toMatchObject({ state: "ACTIVE", running: true, camera_label: "Fake cam 1", error_class: null, changed: true });
    expect(store.getSnapshot().state).toBe("ACTIVE");
    expect(store.getSnapshot().status.running).toBe(true);
    expect(camera.calls).toBe(1);
    expect(camera.liveTracks).toBe(1);
    expect(durable).toEqual(["enable:voice:gözünü aç"]);

    const off = await store.disable("voice:gözünü kapat");
    expect(off).toMatchObject({ state: "DISABLED", running: false, camera_label: null, changed: true });
    expect(store.getSnapshot().state).toBe("DISABLED");
    expect(store.getSnapshot().status.running).toBe(false);
    expect(camera.liveTracks).toBe(0); // the track itself is ended, not just forgotten
    expect(camera.streams[0].tracks[0].readyState).toBe("ended");
    expect(durable).toEqual(["enable:voice:gözünü aç", "disable:voice:gözünü kapat"]);

    const again = await store.enable("voice:gözünü aç");
    expect(again).toMatchObject({ state: "ACTIVE", running: true, camera_label: "Fake cam 2", changed: true });
    expect(camera.calls).toBe(2); // a second real transition, a second stream
    expect(camera.liveTracks).toBe(1); // and still exactly one live track: no orphan from the first
    expect(camera.streams[0].tracks[0].readyState).toBe("ended");
    expect(camera.streams[1].tracks[0].readyState).toBe("live");
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 2, loopsStarted: 2 });

    await store.disable("owner_stop");
    expect(camera.liveTracks).toBe(0);
    store.dispose();
  });

  it("enable while ACTIVE is idempotent: no second getUserMedia, no second stream, changed=false", async () => {
    const { store } = harness(camera);
    await store.enable("a");
    const second = await store.enable("b");
    expect(second).toMatchObject({ state: "ACTIVE", changed: false });
    expect(camera.calls).toBe(1);
    expect(camera.liveTracks).toBe(1);
    store.dispose();
  });

  it("two enables on one gesture join the same in-flight open: one getUserMedia, ENABLING observable in between", async () => {
    camera.manual = true;
    const { store } = harness(camera);
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
    const { store, durable } = harness(camera);
    const off = await store.disable("voice:gözünü kapat");
    expect(off).toMatchObject({ state: "DISABLED", running: false, changed: false, error_class: null });
    expect(camera.calls).toBe(0);
    expect(durable).toEqual([]);
    store.dispose();
  });

  it("a permission refusal is surfaced as permission_denied with no track running and nothing durable", async () => {
    camera.failWith = new DOMException("Permission denied", "NotAllowedError");
    const { store, durable } = harness(camera);
    const on = await store.enable("voice:gözünü aç");
    expect(on).toMatchObject({ state: "ERROR", running: false, error_class: "permission_denied" });
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
    const { store } = harness(camera);
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
