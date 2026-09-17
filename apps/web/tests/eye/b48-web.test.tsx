/**
 * B48 (req 301-303, 331, 332): the owner's switches on the ambient panel, the camera tab's
 * honest notice, and the two writes behind them.
 *
 * Rendered with `react-dom/server` like the rest of these suites; the session module is
 * mocked so the writes are asserted as requests, never sent.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../../app/lib/session", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

import EyeControlView, { type EyeControlViewProps } from "../../app/core/EyeControlView";
import { AmbientPanel } from "../../app/core/panels/CockpitPanels";
import { type AmbientPolicy, AMBIENT_TOGGLES, updateAmbientPolicy } from "../../app/lib/cockpit/api";
import { notifyEyeStreamStopped } from "../../app/lib/eye/client";
import type { PerceptionStatus } from "../../app/lib/eye/perception";
import { eyeView } from "../../app/lib/uistate/ambient";
import { applyResponse, emptyTruth, eyeClaim } from "../../app/lib/uistate/truth";
import { EYE_ACTIVE, EYE_DISABLED, T0, type event, resetSequence, response } from "../uistate/fixtures";

const IDLE: PerceptionStatus = {
  running: false,
  cameraLabel: null,
  lastObservation: null,
  motion: null,
  lastError: null,
  startedAt: null,
};

function serverEye(events: ReturnType<typeof event>[]) {
  resetSequence();
  return eyeView(eyeClaim(applyResponse(emptyTruth(), response(events), T0), T0));
}

function eyeHtml(overrides: Partial<EyeControlViewProps>) {
  const props: EyeControlViewProps = {
    eye: serverEye([]),
    status: IDLE,
    permission: "granted",
    busy: false,
    error: null,
    onStart: () => undefined,
    onStop: () => undefined,
    ...overrides,
  };
  return renderToStaticMarkup(<EyeControlView {...props} />);
}

const POLICY: AmbientPolicy = {
  auto_off_enabled: true,
  off_when_away: true,
  off_when_asleep: false,
  wake_on_return: true,
  keep_on: false,
  away_after_s: 600,
  asleep_after_s: 1200,
  asleep_min_confidence: 0.7,
  input_holdoff_s: 120,
  command_holdoff_s: 900,
  alarm_holdoff_s: 1800,
  return_holdoff_s: 600,
  asleep_after_outside_quiet_s: 1800,
  camera_unknown_grace_s: 120,
  quiet_hours: null,
};

function panelHtml(onToggle?: (field: (typeof AMBIENT_TOGGLES)[number], value: boolean) => void) {
  return renderToStaticMarkup(
    <AmbientPanel
      policy={{ kind: "ok", value: POLICY, at: 0 }}
      devices={{ kind: "ok", value: [], at: 0 }}
      always
      onToggle={onToggle}
    />,
  );
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe("the camera tab notice (302, 303)", () => {
  it("says the camera is not running here when the server says the eye is on", () => {
    const html = eyeHtml({ eye: serverEye([EYE_ACTIVE()]) });
    expect(html).toContain('data-eye-tab-notice="not-running"');
    expect(html).toContain("kendiliğinden açılmaz");
  });

  it("is silent while this tab's camera runs", () => {
    const html = eyeHtml({ eye: serverEye([EYE_ACTIVE()]), status: { ...IDLE, running: true } });
    expect(html).not.toContain("data-eye-tab-notice");
  });

  it("is silent when the owner turned the eye off", () => {
    expect(eyeHtml({ eye: serverEye([EYE_DISABLED()]) })).not.toContain("data-eye-tab-notice");
  });
});

describe("the ambient switches (331, 332)", () => {
  it("renders one pressed-state button per switch where the owner may change them", () => {
    const html = panelHtml(() => undefined);
    for (const field of AMBIENT_TOGGLES) expect(html).toContain(`data-ambient-toggle="${field}"`);
    expect(html).toContain('data-ambient-toggle="auto_off_enabled" aria-pressed="true"');
    expect(html).toContain('data-ambient-toggle="off_when_asleep" aria-pressed="false"');
  });

  it("stays read-only where no handler is given", () => {
    expect(panelHtml()).not.toContain("data-ambient-toggle");
  });
});

describe("the writes", () => {
  it("PUTs exactly the one switch and returns the Cloud Core's sentence", async () => {
    apiFetch.mockResolvedValue(new Response(JSON.stringify({ speech: "Uyurken kapatma açıldı." }), { status: 200 }));
    const result = await updateAmbientPolicy("off_when_asleep", true);
    expect(result).toEqual({ ok: true, speech: "Uyurken kapatma açıldı." });
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/ambient/policy");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ off_when_asleep: true });
  });

  it("answers a refusal in words and never throws", async () => {
    apiFetch.mockResolvedValue(new Response("{}", { status: 422 }));
    expect(await updateAmbientPolicy("auto_off_enabled", false)).toEqual({
      ok: false,
      speech: "Politika güncellenemedi (HTTP 422).",
    });
    apiFetch.mockRejectedValue(new Error("ağ yok"));
    expect(await updateAmbientPolicy("auto_off_enabled", false)).toEqual({ ok: false, speech: "ağ yok" });
  });

  it("tells the Cloud Core a closing tab's stream stopped, with keepalive, and never disables", () => {
    apiFetch.mockResolvedValue(new Response("{}", { status: 200 }));
    notifyEyeStreamStopped();
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/presence/eye/stream-stopped");
    expect(init.keepalive).toBe(true);
    expect(JSON.parse(String(init.body))).toEqual({ reason: "tab_closed" });
    expect(path).not.toContain("disable");
  });
});
