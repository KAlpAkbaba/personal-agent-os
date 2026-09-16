/**
 * B48 (req 300, 303, 331, 671): the device camera on the owner's panel - the mode choice, the
 * write behind it, and each device's honest camera report. Rendered with `react-dom/server`;
 * the session module is mocked so writes are asserted as requests, never sent.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../../app/lib/session", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

import { AmbientPanel, deviceCameraText } from "../../app/core/panels/CockpitPanels";
import {
  type AmbientPolicy,
  CAMERA_MODES,
  type CameraMode,
  type DeviceStatus,
  fetchAmbientPolicy,
  parseDevice,
  updateAmbientCameraMode,
} from "../../app/lib/cockpit/api";

const POLICY: AmbientPolicy = {
  auto_off_enabled: true,
  off_when_away: true,
  off_when_asleep: true,
  wake_on_return: true,
  away_after_s: 900,
  asleep_after_s: 600,
  input_holdoff_s: 600,
  quiet_hours: null,
  camera_mode: "periodic",
};

function panel(policy: AmbientPolicy, devices: DeviceStatus[] = [], onCameraMode?: (mode: CameraMode) => void) {
  return renderToStaticMarkup(
    <AmbientPanel
      policy={{ kind: "ok", value: policy, at: 0 }}
      devices={{ kind: "ok", value: devices, at: 0 }}
      always
      onCameraMode={onCameraMode}
    />,
  );
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe("the camera mode on the panel (300, 331)", () => {
  it("offers the three modes with the chosen one pressed, only where a handler is given", () => {
    const html = panel(POLICY, [], () => undefined);
    for (const mode of CAMERA_MODES) expect(html).toContain(`data-camera-mode="${mode}"`);
    expect(html).toContain('data-camera-mode="periodic" aria-pressed="true"');
    expect(html).toContain('data-camera-mode="off" aria-pressed="false"');
    expect(html).toContain('data-ambient-camera="periodic"');
    expect(html).toContain("kaydedilmez, gönderilmez");

    expect(panel(POLICY)).not.toContain("data-camera-mode");
  });

  it("offers nothing to a Cloud Core that does not report the mode, and disables an unknown one", () => {
    const { camera_mode: _omit, ...older } = POLICY;
    const html = panel(older, [], () => undefined);
    expect(html).not.toContain("data-camera-mode");
    expect(html).toContain('data-ambient-camera="unknown"');

    const unknown = panel({ ...POLICY, camera_mode: null }, [], () => undefined);
    expect(unknown).toContain('data-camera-mode="continuous" aria-pressed="false" disabled=""');
  });

  it("reads the mode the Cloud Core reports and refuses to invent one", async () => {
    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ policy: { ...POLICY, camera_mode: "continuous" } }), { status: 200 }),
    );
    const loaded = await fetchAmbientPolicy();
    expect(loaded.kind === "ok" && loaded.value.camera_mode).toBe("continuous");

    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ policy: { ...POLICY, camera_mode: "record" } }), { status: 200 }),
    );
    const odd = await fetchAmbientPolicy();
    expect(odd.kind === "ok" && odd.value.camera_mode).toBeNull();
  });
});

describe("the write (331)", () => {
  it("PUTs exactly the camera mode and returns the Cloud Core's sentence", async () => {
    apiFetch.mockResolvedValue(new Response(JSON.stringify({ speech: "Tamam." }), { status: 200 }));
    const result = await updateAmbientCameraMode("continuous");
    expect(result).toEqual({ ok: true, speech: "Tamam." });
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/ambient/policy");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ camera_mode: "continuous" });
  });

  it("never sends a mode no device understands and says a refusal in words", async () => {
    const bogus = await updateAmbientCameraMode("record" as CameraMode);
    expect(bogus.ok).toBe(false);
    expect(apiFetch).not.toHaveBeenCalled();

    apiFetch.mockResolvedValue(new Response("{}", { status: 422 }));
    const refused = await updateAmbientCameraMode("off");
    expect(refused).toEqual({ ok: false, speech: "Kamera kipi değiştirilemedi (HTTP 422)." });
  });
});

describe("each device's camera report (303, 671)", () => {
  it("reads the heartbeat's camera block and says a blocked camera out loud", () => {
    const device = parseDevice({
      id: "d1",
      label: "ev-pc",
      heartbeat_status: {
        input_idle_s: 5,
        display: { state: "on" },
        camera: { mode: "periodic", state: "blocked", indicator: "armed", error: "privacy_desktop_apps_off" },
      },
    });
    expect(device.camera).toEqual({
      mode: "periodic",
      state: "blocked",
      indicator: "armed",
      error: "privacy_desktop_apps_off",
    });
    const html = panel(POLICY, [device]);
    expect(html).toContain("kamera engelli (privacy_desktop_apps_off) - açılmadı");
  });

  it("has a sentence for every state the device can report and none for a device without a camera", () => {
    const states = ["capturing", "idle", "off", "blocked", "unavailable", "busy", "vetoed", "error"];
    const texts = states.map((state) => deviceCameraText({ mode: "continuous", state, indicator: null, error: null }));
    expect(new Set(texts).size).toBe(states.length);
    expect(
      deviceCameraText({ mode: "continuous", state: "blocked", indicator: "armed", error: "consent_unreadable" }),
    ).toBe("kamera engelli (izin okunamadı) - açılmadı");
    expect(deviceCameraText({ mode: "continuous", state: "capturing", indicator: "open", error: null })).toBe(
      "kamera AÇIK (sürekli izleme)",
    );

    const plain = parseDevice({ id: "d2", heartbeat_status: { input_idle_s: 1, display: { state: "on" } } });
    expect(plain.camera).toBeNull();
    expect(panel(POLICY, [plain])).not.toContain("· kamera");
  });
});
