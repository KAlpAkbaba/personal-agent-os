/**
 * What the Active Eye control actually puts on the screen.
 *
 * `EyeControlView` is a pure function of its props (see its own file for why
 * it is split from `EyeControl.tsx`), so — like `AmbientBand` — it is
 * rendered with `react-dom/server` in Node: no browser, no Playwright, no
 * jsdom.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import EyeControlView, { type EyeControlViewProps } from "../../app/core/EyeControlView";
import type { PerceptionStatus } from "../../app/lib/eye/perception";
import type { EyeObservation } from "../../app/lib/eye/types";
import { eyeView } from "../../app/lib/uistate/ambient";
import { applyResponse, emptyTruth, eyeClaim } from "../../app/lib/uistate/truth";
import { EYE_ACTIVE, EYE_DISABLED, T0, event, resetSequence, response } from "../uistate/fixtures";

const IDLE_STATUS: PerceptionStatus = {
  running: false,
  cameraLabel: null,
  lastObservation: null,
  motion: null,
  lastError: null,
  startedAt: null,
};

const OBSERVATION: EyeObservation = {
  person_present: true,
  presence_confidence: 0.73,
  activity_level: "medium",
  posture: "unknown",
  awake_state: "awake",
  observed_at: T0 ? new Date(T0).toISOString() : new Date().toISOString(),
  source: "camera",
};

function serverEye(events: ReturnType<typeof event>[]) {
  resetSequence();
  const truth = applyResponse(emptyTruth(), response(events), T0);
  return eyeView(eyeClaim(truth, T0));
}

function render(overrides: Partial<EyeControlViewProps> = {}) {
  const props: EyeControlViewProps = {
    eye: serverEye([]),
    status: IDLE_STATUS,
    permission: "unknown",
    busy: false,
    error: null,
    onStart: vi.fn(),
    onStop: vi.fn(),
    ...overrides,
  };
  return renderToStaticMarkup(<EyeControlView {...props} />);
}

describe("local status, distinct from the server's eye.* truth", () => {
  it("permission not granted reads differently from disabled and from untold", () => {
    const denied = render({ permission: "denied" });
    expect(denied).toContain("Kamera izni verilmedi");
    expect(denied).not.toContain("Yerel algı durduruldu");
    expect(denied).not.toContain("Yerel algı çalışıyor");
  });

  it("nobody has asked for permission yet reads as its own sentence", () => {
    const html = render({ permission: "prompt" });
    expect(html).toContain("Kamera izni istenmedi");
  });

  it("a browser with no Permissions API says so rather than guessing", () => {
    const html = render({ permission: "unsupported" });
    expect(html).toContain("Tarayıcı izin durumunu bildiremiyor");
  });

  it("granted-but-stopped is 'durduruldu', not 'izni verilmedi'", () => {
    const html = render({ permission: "granted" });
    expect(html).toContain("Yerel algı durduruldu");
  });

  it("running says so and names the privacy guarantee", () => {
    const html = render({
      permission: "granted",
      status: { ...IDLE_STATUS, running: true, cameraLabel: "FaceTime HD Camera" },
    });
    expect(html).toContain('data-eye-local-status="running"');
    expect(html).toContain("Yerel algı çalışıyor");
    expect(html).toContain("Görüntü bu cihazdan çıkmaz");
    expect(html).toContain("FaceTime HD Camera");
  });
});

describe("the server-side truth is shown alongside, never merged into one number", () => {
  it("shows the eye.active state distinctly from the local status", () => {
    const html = render({ eye: serverEye([EYE_ACTIVE()]), status: IDLE_STATUS });
    expect(html).toContain('data-eye-server-status="active"');
    expect(html).toContain("Sunucu: Göz açık");
    // The local device has not started its own loop even though the server says active.
    expect(html).toContain('data-eye-local-status="stopped"');
  });

  it("shows eye.disabled distinctly, e.g. right after a voice command elsewhere", () => {
    const html = render({ eye: serverEye([EYE_DISABLED()]) });
    expect(html).toContain('data-eye-server-status="disabled"');
    expect(html).toContain("Sunucu: Göz kapalı");
  });

  it("shows untold, not a guess, when nothing has been published", () => {
    const html = render({ eye: serverEye([]) });
    expect(html).toContain('data-eye-server-status="untold"');
  });
});

describe("the last observation, exactly as derived", () => {
  it("is absent until one exists", () => {
    expect(render()).not.toContain("data-eye-last-observation");
  });

  it("shows activity, confidence and awake state once one arrives", () => {
    const html = render({ status: { ...IDLE_STATUS, running: true, lastObservation: OBSERVATION } });
    expect(html).toContain("data-eye-last-observation");
    expect(html).toContain("orta hareket");
    expect(html).toContain("güven %73");
    expect(html).toContain("uyanık");
  });
});

describe("the control itself", () => {
  it("offers 'Gözü aç' while stopped and 'Gözü kapat' while running", () => {
    expect(render({ status: IDLE_STATUS })).toContain("Gözü aç");
    expect(render({ status: { ...IDLE_STATUS, running: true } })).toContain("Gözü kapat");
  });

  it("disables the control while an owner action is in flight", () => {
    const html = render({ busy: true });
    expect(html).toContain("disabled=\"\"");
  });

  it("surfaces an owner-action error and a sampler error, without hiding one behind the other", () => {
    const html = render({
      error: "Kamera reddedildi",
      status: { ...IDLE_STATUS, lastError: "kötü alan" },
    });
    // The owner-action error takes precedence when both are present, and is visible.
    expect(html).toContain('data-eye-local-error="yes"');
    expect(html).toContain("Kamera reddedildi");
  });

  it("shows the sampler's own error when there is no owner-action error", () => {
    const html = render({ status: { ...IDLE_STATUS, lastError: "kötü alan" } });
    expect(html).toContain("kötü alan");
  });

  it("is an ambient-cell, styled like the rest of contract v2's band", () => {
    expect(render()).toContain('class="ambient-cell"');
  });
});

describe("the numbers behind the verdict", () => {
  it("are shown on the eye cell - an age and a cell fraction, never a frame", () => {
    const html = render({
      eye: serverEye([EYE_ACTIVE()]),
      permission: "granted",
      status: {
        ...IDLE_STATUS,
        running: true,
        cameraLabel: "cam-0",
        motion: { changedCellRatio: 0.037, maxCellDelta: 0.39, msSinceLastMotion: 12_000, lastMotionLevel: "low" },
      },
    });
    expect(html).toContain("data-eye-motion");
    expect(html).toContain("12 sn");
    expect(html).toContain("%4");
    expect(html).not.toContain("base64");
  });

  it("say 'no movement yet' rather than inventing an age", () => {
    const html = render({
      status: { ...IDLE_STATUS, running: true, motion: { changedCellRatio: 0, maxCellDelta: 0, msSinceLastMotion: null, lastMotionLevel: "none" } },
    });
    expect(html).toContain("henüz yok");
  });
});
