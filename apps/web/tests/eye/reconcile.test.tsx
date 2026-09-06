/**
 * Active Eye ↔ voice (M18 spec §2; ADR-0061 §6).
 *
 * `Gözünü kapat` is resolved server-side (`app/voice/intents.py::EYE_DISABLE`
 * → `disable_eye`), the bus publishes `eye.disabled`, and this device must
 * react: the local perception loop stops and the Core's eye cell reads
 * disabled. The decision is `shouldStopLocalPerception` (pure), the action is
 * `PerceptionSession.stop()` (already proven immediate in
 * `perception.test.ts`), and the cell is `AmbientBand` / `EyeControlView`
 * (pure). This test runs the three together on fakes — no camera, no
 * browser — because the effect in `EyeControl.tsx` is one line joining them.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AmbientBand from "../../app/core/AmbientBand";
import EyeControlView from "../../app/core/EyeControlView";
import { PerceptionSession, type FrameReducer, type FrameSource } from "../../app/lib/eye/perception";
import { shouldStopLocalPerception } from "../../app/lib/eye/reconcile";
import { eyeView, presenceView, releaseView } from "../../app/lib/uistate/ambient";
import { applyResponse, emptyTruth, eyeClaim, presenceClaim, releaseClaim } from "../../app/lib/uistate/truth";
import { EYE_ACTIVE, EYE_DISABLED, T0, event, resetSequence, response } from "../uistate/fixtures";

class FakeFrameSource implements FrameSource {
  startCalls = 0;
  stopCalls = 0;
  private pixels = new Uint8Array(4 * 4 * 4);

  async start(): Promise<void> {
    this.startCalls += 1;
  }

  sample(reduce: FrameReducer): Float32Array | null {
    return reduce(this.pixels, 4, 4);
  }

  stop(): void {
    this.stopCalls += 1;
  }

  label(): string | null {
    return "fake-cam";
  }
}

function busEye(events: ReturnType<typeof event>[]) {
  resetSequence();
  const truth = applyResponse(emptyTruth(), response(events), T0);
  return {
    truth,
    eye: eyeView(eyeClaim(truth, T0)),
    presence: presenceView(presenceClaim(truth, T0)),
    release: releaseView(releaseClaim(truth, T0)),
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("a bus eye.disabled arriving while perception runs", () => {
  it("stops the local loop and releases the camera at once, and the Core's eye cell reads disabled", async () => {
    const frameSource = new FakeFrameSource();
    const session = new PerceptionSession({
      frameSource,
      postObservation: vi.fn(async () => ({ status: "posted" as const })),
      sampleIntervalMs: 1000,
      now: () => Date.now(),
    });
    await session.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(session.getStatus().running).toBe(true);

    // The owner said "Gözünü kapat" on the voice leg; Cloud Core published it.
    const { eye, presence, release } = busEye([EYE_ACTIVE(), EYE_DISABLED("owner_command")]);
    expect(eye.status).toBe("disabled");

    // What EyeControl's effect does with that, exactly.
    const stop = shouldStopLocalPerception(eye, session.getStatus().running);
    expect(stop).toBe(true);
    if (stop) session.stop();

    expect(frameSource.stopCalls).toBe(1);
    expect(session.getStatus().running).toBe(false);
    // No further tick is scheduled.
    await vi.advanceTimersByTimeAsync(5000);
    expect(frameSource.startCalls).toBe(1);

    const band = renderToStaticMarkup(<AmbientBand eye={eye} presence={presence} release={release} />);
    expect(band).toContain('data-eye-status="disabled"');
    expect(band).toContain("Göz kapalı");

    const control = renderToStaticMarkup(
      <EyeControlView eye={eye} status={session.getStatus()} permission="granted" busy={false} error={null} onStart={vi.fn()} onStop={vi.fn()} />,
    );
    expect(control).toContain('data-eye-local-status="stopped"');
    expect(control).toContain('data-eye-server-status="disabled"');
  });

  it("is idempotent: a disabled bus with an already-stopped loop asks for nothing", () => {
    const { eye } = busEye([EYE_DISABLED()]);
    expect(shouldStopLocalPerception(eye, false)).toBe(false);
  });

  it("untold is not disabled: nobody saying anything never stops the owner's camera", () => {
    const { eye } = busEye([]);
    expect(eye.status).toBe("untold");
    expect(shouldStopLocalPerception(eye, true)).toBe(false);
  });
});

describe("enabling the eye is reflected immediately, and never starts a camera by itself", () => {
  it("eye.active on the bus shows as active in the band on the same render", () => {
    const { eye, presence, release } = busEye([EYE_DISABLED(), EYE_ACTIVE()]);
    expect(eye.status).toBe("active");
    const band = renderToStaticMarkup(<AmbientBand eye={eye} presence={presence} release={release} />);
    expect(band).toContain('data-eye-status="active"');
    expect(band).toContain("Göz açık");
    expect(band).toContain("Cihaz: cam-0");
  });

  it("eye.active does not ask this device to do anything: the rule is stop-only", () => {
    const { eye } = busEye([EYE_ACTIVE()]);
    expect(shouldStopLocalPerception(eye, false)).toBe(false);
    expect(shouldStopLocalPerception(eye, true)).toBe(false);
  });

  it("a locally stopped device under a server eye.active shows both facts, unreconciled", () => {
    const { eye } = busEye([EYE_ACTIVE()]);
    const html = renderToStaticMarkup(
      <EyeControlView
        eye={eye}
        status={{ running: false, cameraLabel: null, lastObservation: null, motion: null, lastError: null, startedAt: null }}
        permission="granted"
        busy={false}
        error={null}
        onStart={vi.fn()}
        onStop={vi.fn()}
      />,
    );
    expect(html).toContain('data-eye-local-status="stopped"');
    expect(html).toContain("Sunucu: Göz açık");
  });
});
