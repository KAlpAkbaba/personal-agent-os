/**
 * The push settings card on an iPhone. Safari gives a plain tab no `PushManager`; iOS web
 * push exists only in a web app added to the Home Screen and opened from there (iOS 16.4+).
 * Without this the card said "Bu tarayıcı push bildirimlerini desteklemiyor." - which the
 * owner reads as "broken", when the one thing to do is "add it to the Home Screen".
 *
 * `react-dom/server`, in Node, like the other component suites here: the phase is resolved
 * through the component's own `resolvePhase` against stubbed browser globals, then rendered
 * through the card's own markup.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { WebPushSettings, WebPushSettingsView, resolvePhase } from "../../app/settings/WebPushSettings";

const IPHONE_17 =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
const IPHONE_163 =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Mobile/15E148 Safari/604.1";
const WINDOWS_CHROME =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36";

// `"PushManager" in window` is all pushSupport() checks (webpush-client.test.ts).
const FakePushManager = function PushManager() {};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** A browser: what Safari's tab, Safari's Home Screen app and desktop Chrome each expose. */
function stubBrowser(opts: { userAgent: string; standalone?: boolean; maxTouchPoints: number; pushManager: boolean }) {
  vi.stubGlobal("navigator", {
    userAgent: opts.userAgent,
    standalone: opts.standalone,
    maxTouchPoints: opts.maxTouchPoints,
    serviceWorker: { getRegistration: vi.fn().mockResolvedValue(undefined) },
  });
  vi.stubGlobal("window", {
    ...(opts.pushManager ? { PushManager: FakePushManager } : {}),
    matchMedia: vi.fn().mockReturnValue({ matches: false }),
  });
  vi.stubGlobal("Notification", { permission: "default", requestPermission: vi.fn() });
}

async function renderCard(): Promise<string> {
  const phase = await resolvePhase();
  return renderToStaticMarkup(<WebPushSettingsView phase={phase} onEnable={() => undefined} onDisable={() => undefined} />);
}

const HOME_SCREEN_TEXT = "Ana Ekrana Ekle";

beforeEach(() => {
  apiFetch.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WebPushSettings on an iPhone", () => {
  it("in a Safari tab: names the Home Screen step, offers no button and never asks the server", async () => {
    stubBrowser({ userAgent: IPHONE_17, standalone: false, maxTouchPoints: 5, pushManager: false });
    const html = await renderCard();
    expect(html).toContain(HOME_SCREEN_TEXT);
    expect(html).toContain('data-phase="needs_home_screen"');
    expect(html).toContain('data-panel="webpush-settings"');
    expect(html).not.toContain("desteklemiyor");
    expect(html).not.toContain("data-webpush-enable");
    expect(apiFetch).toHaveBeenCalledTimes(0);
  });

  it("on iOS 16.3: says 16.4 is needed, offers no button and never asks the server", async () => {
    stubBrowser({ userAgent: IPHONE_163, standalone: false, maxTouchPoints: 5, pushManager: false });
    const html = await renderCard();
    expect(html).toContain("16.4");
    expect(html).toContain('data-phase="ios_too_old"');
    expect(html).not.toContain(HOME_SCREEN_TEXT);
    expect(html).not.toContain("data-webpush-enable");
    expect(apiFetch).toHaveBeenCalledTimes(0);
  });

  it("opened from the Home Screen with no server key: today's no_server_key state", async () => {
    stubBrowser({ userAgent: IPHONE_17, standalone: true, maxTouchPoints: 5, pushManager: true });
    apiFetch.mockResolvedValueOnce(json(200, { supported: false, public_key: null, reason: "no_vapid_key" }));
    const html = await renderCard();
    expect(html).toContain('data-webpush-state="no_server_key"');
    expect(html).toContain("sahip eylemi");
    expect(html).not.toContain(HOME_SCREEN_TEXT);
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it("opened from the Home Screen with a server key: today's enable button", async () => {
    stubBrowser({ userAgent: IPHONE_17, standalone: true, maxTouchPoints: 5, pushManager: true });
    apiFetch.mockResolvedValueOnce(json(200, { supported: true, public_key: "AAECAw" }));
    const html = await renderCard();
    expect(html).toContain("data-webpush-enable");
    expect(html).not.toContain(HOME_SCREEN_TEXT);
  });
});

describe("WebPushSettings on Windows Chrome", () => {
  it("shows none of the iPhone text and keeps today's flow", async () => {
    stubBrowser({ userAgent: WINDOWS_CHROME, maxTouchPoints: 0, pushManager: true });
    apiFetch.mockResolvedValueOnce(json(200, { supported: true, public_key: "AAECAw" }));
    const html = await renderCard();
    expect(html).toContain("data-webpush-enable");
    expect(html).not.toContain(HOME_SCREEN_TEXT);
    expect(html).not.toContain("16.4");
    expect(html).not.toContain("data-phase=\"needs_home_screen\"");
  });

  it("with no PushManager at all is still today's honest 'unsupported'", async () => {
    stubBrowser({ userAgent: WINDOWS_CHROME, maxTouchPoints: 0, pushManager: false });
    const html = await renderCard();
    expect(html).toContain('data-webpush-state="unsupported"');
    expect(html).not.toContain(HOME_SCREEN_TEXT);
  });
});

describe("WebPushSettings first paint", () => {
  it("renders 'checking' and asks nothing before the client effect runs", () => {
    stubBrowser({ userAgent: IPHONE_17, standalone: false, maxTouchPoints: 5, pushManager: false });
    const html = renderToStaticMarkup(<WebPushSettings />);
    expect(html).toContain("Kontrol ediliyor");
    expect(apiFetch).toHaveBeenCalledTimes(0);
  });
});
