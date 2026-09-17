/**
 * Minimal mode's overlays (M18.3 §9): the control cluster, the connection dot,
 * the ambient strip, and the two cockpit panels for the new subsystems.
 *
 * The properties held here are the ones that would otherwise only be checked
 * by looking at the screen:
 *
 * · fading is not hiding — the cluster keeps its buttons, its labels and its
 *   ARIA state at every opacity;
 * · fullscreen is only ever entered from a user gesture, and there is always a
 *   visible way out;
 * · the camera cell and the screens cell each keep "nobody told us" separate
 *   from "it is off";
 * · a route another track has not built yet renders as "henüz yok", never as
 *   an empty list and never as a fabricated row.
 *
 * `react-dom/server`, in Node. No browser.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AmbientBand from "../../app/core/AmbientBand";
import ConnectionDot from "../../app/core/ConnectionDot";
import CoreControls, { type CoreControlsProps } from "../../app/core/CoreControls";
import { AlarmsPanel, AmbientPanel } from "../../app/core/panels/CockpitPanels";
import type { AmbientPolicy, DeviceStatus, WakeAlarm } from "../../app/lib/cockpit/api";
import {
  alarmView,
  displayView,
  eyeView,
  presenceView,
  releaseView,
} from "../../app/lib/uistate/ambient";
import { CONTROL_FADED_OPACITY } from "../../app/lib/uistate/stage";
import {
  alarmClaim,
  applyResponse,
  displayClaim,
  emptyTruth,
  eyeClaim,
  presenceClaim,
  releaseClaim,
} from "../../app/lib/uistate/truth";
import {
  ALARM_ARMED,
  ALARM_FAILED,
  ALARM_PLAYING,
  DISPLAY_OFF,
  DISPLAY_ON,
  EYE_ACTIVE,
  OWNER_PRESENT,
  T0,
  event,
  resetSequence,
  response,
} from "./fixtures";

const CONTROLS: CoreControlsProps = {
  opacity: 1,
  faded: false,
  onHold: () => {},
  onRelease: () => {},
  voiceConnected: false,
  voiceBusy: false,
  voiceReady: true,
  onVoice: () => {},
  eyeRunning: false,
  eyeBusy: false,
  eyeServerStatus: "untold",
  onEye: () => {},
  tier: "high",
  onTier: () => {},
  force2d: false,
  onForce2d: () => {},
  fullscreen: false,
  fullscreenSupported: true,
  onFullscreen: () => {},
};

function controls(overrides: Partial<typeof CONTROLS> = {}) {
  return renderToStaticMarkup(<CoreControls {...CONTROLS} {...overrides} />);
}

function band(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  const truth = applyResponse(emptyTruth(), response(events), T0);
  return renderToStaticMarkup(
    <AmbientBand
      eye={eyeView(eyeClaim(truth, at))}
      presence={presenceView(presenceClaim(truth, at))}
      release={releaseView(releaseClaim(truth, at))}
      display={displayView(displayClaim(truth, at))}
      alarm={alarmView(alarmClaim(truth, at))}
    />,
  );
}

describe("the control cluster", () => {
  it("offers exactly the six controls the mode promises", () => {
    const html = controls();
    for (const control of ["voice", "eye", "force-2d", "fullscreen", "cockpit"]) {
      expect(html, control).toContain(`data-control="${control}"`);
    }
    // …plus the three quality tiers, as one labelled group.
    expect(html).toContain('data-tier-option="high"');
    expect(html).toContain('data-tier-option="balanced"');
    expect(html).toContain('data-tier-option="low"');
    expect(html).toContain('aria-label="Görüntü kalitesi"');
  });

  it("fades without hiding: same buttons, same labels, same ARIA", () => {
    const present = controls();
    const receded = controls({ opacity: CONTROL_FADED_OPACITY, faded: true });
    expect(receded).toContain('data-controls-faded="yes"');
    expect(receded).toContain(`opacity:${CONTROL_FADED_OPACITY}`);
    // Nothing is removed, nothing is disabled, nothing loses its name.
    const buttons = (html: string) => html.match(/<button/g)?.length ?? 0;
    expect(buttons(receded)).toBe(buttons(present));
    expect(receded).toContain("Tam ekran");
    expect(receded).toContain("Kokpit");
    expect(receded).not.toContain("aria-hidden");
    expect(receded).not.toContain("display:none");
    expect(receded).not.toContain("visibility:hidden");
  });

  it("always offers a visible way out of fullscreen, and names Esc", () => {
    const inside = controls({ fullscreen: true });
    expect(inside).toContain("Tam ekrandan çık");
    expect(inside).toContain("Esc");
    expect(inside).toContain('aria-pressed="true"');
  });

  it("says so rather than lying when the browser has no fullscreen", () => {
    const html = controls({ fullscreenSupported: false });
    expect(html).toContain("Bu tarayıcıda tam ekran kullanılamıyor");
    expect(html).toMatch(/data-control="fullscreen"[^>]*disabled/);
  });

  it("states the voice and eye state it was given, and nothing more", () => {
    expect(controls({ voiceConnected: true })).toContain("Sesi kapat");
    expect(controls({ voiceBusy: true })).toContain("Bağlanıyor…");
    expect(controls({ voiceReady: false })).toMatch(/data-control="voice"[^>]*disabled/);
    expect(controls({ eyeRunning: true })).toContain("Gözü kapat");
    expect(controls({ eyeServerStatus: "active" })).toContain('data-eye-server="active"');
  });
});

describe("the connection dot", () => {
  it("names each connection state", () => {
    expect(
      renderToStaticMarkup(<ConnectionDot connection={{ kind: "live", at: T0 }} />),
    ).toContain('data-connection="live"');
    const lost = renderToStaticMarkup(
      <ConnectionDot connection={{ kind: "unreachable", error: "ağ koptu", since: T0 }} />,
    );
    expect(lost).toContain('data-connection="unreachable"');
    expect(lost).toContain("ağ koptu");
  });

  it("says when the server's contract is older than this build", () => {
    const html = renderToStaticMarkup(
      <ConnectionDot
        connection={{ kind: "live", at: T0 }}
        contractLag="Sunucu durum sözleşmesi v2; bu arayüz v3."
      />,
    );
    expect(html).toContain("data-contract-lag");
    expect(html).toContain("v2");
  });
});

describe("the ambient strip carries the room, the screens and the alarm", () => {
  it("renders the display cell as three distinct answers", () => {
    expect(band([DISPLAY_ON()])).toContain('data-display-state="on"');
    const off = band([DISPLAY_OFF()]);
    expect(off).toContain('data-display-state="off"');
    // The sentence that must never be missing from a display-off indicator.
    expect(off).toContain("Bilgisayar uyutulmadı, kilitlenmedi, kapatılmadı.");
    expect(off).toContain("owner_away");
    const untold = band([]);
    expect(untold).toContain('data-display-state="untold"');
    expect(untold).toContain("Ekran durumu bildirilmedi");
    expect(untold).not.toContain('data-display-state="off"');
  });

  it("renders the alarm cell only as what was published", () => {
    const armed = band([ALARM_ARMED()]);
    expect(armed).toContain('data-alarm-stage="armed"');
    expect(armed).toContain("Alarm 07:30");
    expect(armed).toContain("Henüz çalmıyor");

    const playing = band([ALARM_PLAYING()]);
    expect(playing).toContain('data-alarm-stage="playing"');
    expect(playing).toContain("ses seviyesi %45");

    const unmeasured = band([ALARM_PLAYING(null)]);
    expect(unmeasured).toContain("ses seviyesi bildirilmedi");
    expect(unmeasured).toContain('data-alarm-level="unknown"');

    const none = band([]);
    expect(none).toContain('data-alarm-stage="none"');
    expect(none).toContain("Şu anda kurulu ya da çalan bir alarm bildirilmedi.");
  });

  it("carries the publisher's severity for a failed alarm", () => {
    const html = band([ALARM_FAILED()]);
    expect(html).toContain('data-alarm-stage="failed"');
    expect(html).toContain('data-alarm-severity="critical"');
    expect(html).toContain("Her iki ses yolu da başarısız oldu.");
  });

  it("keeps the camera cell and the presence cell exactly as they were", () => {
    const html = band([EYE_ACTIVE(), OWNER_PRESENT()]);
    expect(html).toContain('data-eye-status="active"');
    expect(html).toContain("Görüntü buluta gönderilmiyor ve kaydedilmiyor.");
    expect(html).toContain("Algı kimlik doğrulaması değildir.");
  });

  it("offers no control at all: the strip shows and never acts", () => {
    const html = band([DISPLAY_OFF(), ALARM_PLAYING()]);
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<form");
    expect(html).not.toContain("<input");
  });
});

// ------------------------------------------------------ the cockpit panels

const POLICY: AmbientPolicy = {
  auto_off_enabled: false,
  off_when_away: true,
  off_when_asleep: true,
  wake_on_return: true,
  keep_on: false,
  away_after_s: 900,
  asleep_after_s: 600,
  asleep_min_confidence: 0.7,
  input_holdoff_s: 600,
  command_holdoff_s: 900,
  alarm_holdoff_s: 1800,
  return_holdoff_s: 600,
  asleep_after_outside_quiet_s: 1800,
  camera_unknown_grace_s: 120,
  quiet_hours: null,
};

const ALARM_ROW: WakeAlarm = {
  id: "alarm-1",
  state: "ARMED",
  local_time: "07:30",
  scheduled_for: new Date(T0 + 3_600_000).toISOString(),
  timezone: "Europe/Istanbul",
  is_test: false,
  recurrence: null,
  media_kind: "youtube",
  media_title: "Hans Zimmer — Time",
  device_id: "dev-1",
  snooze_count: 0,
  terminal_reason: null,
};

const DEVICE: DeviceStatus = {
  device_id: "dev-1",
  label: "masaüstü",
  online: true,
  last_seen_at: null,
  input_idle_s: 42,
  display_state: "on",
  display_observed_at: null,
  alarm_ringing: false,
  armed_alarms: 1,
  statusKnown: true,
};

describe("the cockpit's new panels tell 'not built yet' from 'nothing there'", () => {
  it("says 'henüz yok' for a route this Cloud Core does not have", () => {
    // B24 req 714: on the cockpit an absent route draws nothing — the sentence is on
    // /availability with the other twenty-six families. On /alarms it is the answer.
    expect(
      renderToStaticMarkup(
        <AlarmsPanel state={{ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/alarms yok (HTTP 404)." }} now={T0} />,
      ),
    ).toBe("");

    const html = renderToStaticMarkup(
      <AlarmsPanel state={{ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/alarms yok (HTTP 404)." }} now={T0} always />,
    );
    expect(html).toContain("data-panel-absent");
    expect(html).toContain("Henüz yok.");
    expect(html).toContain("/v1/alarms");
    // Emphatically NOT the empty sentence, which would be a claim.
    expect(html).not.toContain("Kurulu alarm yok.");
  });

  it("says 'no alarms' only when it actually asked and got none — on the page, not the cockpit", () => {
    expect(
      renderToStaticMarkup(<AlarmsPanel state={{ kind: "ok", value: [], at: 0 }} now={T0} />),
    ).toBe("");

    const html = renderToStaticMarkup(
      <AlarmsPanel state={{ kind: "ok", value: [], at: 0 }} now={T0} always />,
    );
    expect(html).toContain("Kurulu alarm yok.");
    expect(html).toContain('data-panel-empty="yes"');
  });

  it("lists an alarm as the publisher described it", () => {
    const html = renderToStaticMarkup(
      <AlarmsPanel state={{ kind: "ok", value: [ALARM_ROW], at: 0 }} now={T0} />,
    );
    expect(html).toContain('data-alarm-state="ARMED"');
    expect(html).toContain("07:30");
    expect(html).toContain("cihazda hazır");
    expect(html).toContain("Hans Zimmer");
    expect(html).toContain("tek seferlik");
  });

  it("does not invent a media source or a time it was not given", () => {
    const bare: WakeAlarm = { ...ALARM_ROW, local_time: null, media_kind: null, media_title: null };
    const html = renderToStaticMarkup(
      <AlarmsPanel state={{ kind: "ok", value: [bare], at: 0 }} now={T0} />,
    );
    expect(html).toContain("saat bildirilmedi");
    expect(html).toContain("ses kaynağı bildirilmedi");
  });

  it("prints the ambient policy and the devices' own account of their screens", () => {
    const html = renderToStaticMarkup(
      <AmbientPanel
        policy={{ kind: "ok", value: POLICY, at: 0 }}
        devices={{ kind: "ok", value: [DEVICE], at: 0 }}
      />,
    );
    expect(html).toContain('data-ambient-auto-off="false"');
    expect(html).toContain("yokken kapat");
    expect(html).toContain("yokluk 900 sn");
    expect(html).toContain('data-device-display="on"');
    expect(html).toContain("42 sn boşta");
    // The rule the whole subsystem rests on, stated on the panel itself.
    expect(html).toContain("hiçbir yol bilgisayarı uyutmaz, kilitlemez veya kapatmaz");
  });

  it("says a device sent no status rather than presuming its screen is on", () => {
    const silent: DeviceStatus = { ...DEVICE, statusKnown: false, display_state: null, input_idle_s: null };
    const html = renderToStaticMarkup(
      <AmbientPanel
        policy={{ kind: "ok", value: POLICY, at: 0 }}
        devices={{ kind: "ok", value: [silent], at: 0 }}
      />,
    );
    expect(html).toContain('data-device-display="untold"');
    expect(html).toContain("cihaz durumu bildirilmedi");
    expect(html).not.toContain("ekran açık");
  });

  it("renders the policy panel's own absence truthfully", () => {
    // req 714 again: quiet on the cockpit, said in words on /settings.
    expect(
      renderToStaticMarkup(
        <AmbientPanel
          policy={{ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/ambient/policy yok (HTTP 404)." }}
          devices={{ kind: "ok", value: [DEVICE], at: 0 }}
        />,
      ),
    ).toBe("");

    const html = renderToStaticMarkup(
      <AmbientPanel
        policy={{ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/ambient/policy yok (HTTP 404)." }}
        devices={{ kind: "ok", value: [DEVICE], at: 0 }}
        always
      />,
    );
    expect(html).toContain("Henüz yok.");
    expect(html).toContain("/v1/ambient/policy");
    expect(html).not.toContain("Ortam politikası bildirilmedi.");
  });
});

// ------------------------------------------------------- structural rules

describe("fullscreen is the owner's gesture, structurally", () => {
  const hooks = readFileSync(resolve(__dirname, "../../app/core/useMinimalStage.ts"), "utf8");
  const page = readFileSync(resolve(__dirname, "../../app/core/page.tsx"), "utf8");
  const cluster = readFileSync(resolve(__dirname, "../../app/core/CoreControls.tsx"), "utf8");
  const css = readFileSync(resolve(__dirname, "../../app/core/core.css"), "utf8");

  it("calls requestFullscreen from exactly one callback, and from no effect", () => {
    const calls = hooks.match(/requestFullscreen\?\.\(/g) ?? [];
    expect(calls.length).toBe(1);
    // The one call site is inside the `toggle` callback…
    const toggle = hooks.slice(hooks.indexOf("const toggle = useCallback("));
    expect(toggle).toContain("requestFullscreen?.(");
    // …and no `useEffect` in the file CALLS it. Reading the property to find
    // out whether the browser has it at all is fine and is what the support
    // flag does; invoking it outside a gesture is what must never happen.
    for (const effect of hooks.split("useEffect(").slice(1)) {
      const body = effect.slice(0, effect.indexOf("}, ["));
      expect(body).not.toContain("requestFullscreen?.(");
      expect(body).not.toContain("requestFullscreen()");
    }
  });

  it("binds the toggle to a button and to nothing else", () => {
    expect(cluster).toContain('data-control="fullscreen"');
    expect(cluster).toContain("onClick={onFullscreen}");
    expect(page).toContain("onFullscreen={fullscreen.toggle}");
    // The page never calls it itself.
    expect(page).not.toContain("fullscreen.toggle()");
    expect(page).not.toContain("requestFullscreen");
  });

  it("uses the document element, so the stage really is the screen", () => {
    expect(hooks).toContain("document.documentElement.requestFullscreen");
    expect(hooks).toContain("document.exitFullscreen");
  });

  it("gives Minimal mode a fixed stage, so there is no page scroll", () => {
    const shell = css.slice(css.indexOf(".core-shell {"), css.indexOf(".core-shell-stage {"));
    expect(shell).toContain("position: fixed");
    expect(shell).toContain("overflow: hidden");
    expect(shell).toContain("var(--core-ground)");
    // The ground is the near-black the spec names.
    expect(css).toContain("--core-ground: #06050a");
  });

  it("keeps the eye's reconcile seam now that EyeControl is not on this page", () => {
    // Minimal mode dropped the full eye cell for the control cluster, and the
    // cell was where `stopLocalIfStale` lived. Without it, "gözünü kapat" said
    // on another device would leave this one's camera running while the Cloud
    // Core believed it was off — the one regression this rewrite could make
    // that the owner would care about most.
    expect(page).toContain("stopLocalIfStale({ status: eyeStatus");
    expect(page).toContain("useActivePerception");
  });

  it("keeps the strip out of the fade, so the privacy cell never dims", () => {
    expect(page).toContain('className="core-strip"');
    // The strip carries no opacity of its own: only the cluster fades.
    const strip = page.slice(page.indexOf('className="core-strip"'));
    expect(strip.slice(0, 120)).not.toContain("fade.opacity");
  });
});
