import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  WebPushApiError,
  currentBrowserSubscription,
  deleteSubscription,
  fetchVapidPublicKey,
  homeScreenState,
  listSubscriptions,
  notificationPermission,
  postSubscription,
  pushSupport,
  readHomeScreenInput,
  registerAndSubscribe,
  unsubscribeBrowser,
  urlBase64ToUint8Array,
} from "../../app/lib/cockpit/webpush";

// Real user-agent strings, one per device the owner might open the settings page on.
const UA = {
  iphone17: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  iphone163: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Mobile/15E148 Safari/604.1",
  iphone164: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
  ipadDesktop: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
  androidChrome: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
  windowsChrome: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
} as const;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// A stand-in constructor for `window.PushManager` - its identity is all `pushSupport()`
// checks (`"PushManager" in window`), so an empty class defined once here is enough.
class FakePushManager {}

beforeEach(() => {
  apiFetch.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------- urlBase64ToUint8Array

describe("urlBase64ToUint8Array", () => {
  it("decodes a plain base64url string with no special characters", () => {
    // base64("\x00\x01\x02\x03") === "AAECAw==" -> stripped padding "AAECAw"
    expect(Array.from(urlBase64ToUint8Array("AAECAw"))).toEqual([0, 1, 2, 3]);
  });

  it("decodes '-' and '_' the same way standard base64 decodes '+' and '/'", () => {
    // Bytes chosen so the STANDARD base64 encoding contains both '+' and '/':
    // 0xfb 0xff 0xbf -> base64 "+/+/" ... constructed directly below instead of relying
    // on btoa's own alphabet, so the fixture is independent of the function under test.
    const bytes = new Uint8Array([0xfb, 0xff, 0xbf]);
    let binary = "";
    for (const b of bytes) binary += String.fromCharCode(b);
    const standardBase64 = btoa(binary); // "+/+/" not guaranteed; assert against itself
    const urlSafe = standardBase64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    expect(Array.from(urlBase64ToUint8Array(urlSafe))).toEqual(Array.from(bytes));
  });

  it("round-trips an arbitrary VAPID-sized key (65 raw bytes)", () => {
    const bytes = new Uint8Array(65);
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = (i * 7) % 256;
    let binary = "";
    for (const b of bytes) binary += String.fromCharCode(b);
    const urlSafe = btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    expect(Array.from(urlBase64ToUint8Array(urlSafe))).toEqual(Array.from(bytes));
  });
});

// ------------------------------------------------------------- fetchVapidPublicKey

describe("fetchVapidPublicKey", () => {
  it("reports supported:true with the key when the server has one configured", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { supported: true, public_key: "abc123" }));
    const state = await fetchVapidPublicKey();
    expect(state).toEqual({ supported: true, publicKey: "abc123" });
  });

  it("reports the server's honest reason when no key is configured", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { supported: false, public_key: null, reason: "no_vapid_key" }));
    const state = await fetchVapidPublicKey();
    expect(state).toEqual({ supported: false, publicKey: null, reason: "no_vapid_key" });
  });

  it("throws WebPushApiError on a non-OK response", async () => {
    apiFetch.mockResolvedValueOnce(json(500, { detail: "kaboom" }));
    await expect(fetchVapidPublicKey()).rejects.toBeInstanceOf(WebPushApiError);
  });
});

// ------------------------------------------------------------- postSubscription

describe("postSubscription", () => {
  const subscriptionJson = {
    endpoint: "https://fcm.googleapis.com/fcm/send/abc",
    keys: { p256dh: "p256dh-value", auth: "auth-value" },
  } as PushSubscriptionJSON;

  it("sends exactly the endpoint/keys/user_agent shape the server expects", async () => {
    apiFetch.mockResolvedValueOnce(
      json(200, {
        id: "sub-1",
        endpoint_host: "fcm.googleapis.com",
        created_at: "2026-09-17T00:00:00Z",
        last_success_at: null,
        last_error_reason: null,
        failure_count: 0,
      }),
    );
    const result = await postSubscription(subscriptionJson, "TestAgent/1.0");
    expect(result).toEqual({
      id: "sub-1",
      endpointHost: "fcm.googleapis.com",
      createdAt: "2026-09-17T00:00:00Z",
      lastSuccessAt: null,
      lastErrorReason: null,
      failureCount: 0,
    });
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/webpush/subscriptions");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      endpoint: "https://fcm.googleapis.com/fcm/send/abc",
      keys: { p256dh: "p256dh-value", auth: "auth-value" },
      user_agent: "TestAgent/1.0",
    });
  });

  it("truncates an oversized user agent to 256 characters before sending", async () => {
    apiFetch.mockResolvedValueOnce(
      json(200, { id: "sub-1", endpoint_host: "fcm.googleapis.com", created_at: null, last_success_at: null, last_error_reason: null, failure_count: 0 }),
    );
    await postSubscription(subscriptionJson, "x".repeat(1000));
    const [, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect((body.user_agent as string).length).toBe(256);
  });

  it("throws WebPushApiError when the server refuses the endpoint", async () => {
    apiFetch.mockResolvedValueOnce(json(422, { detail: "endpoint refused: invalid_endpoint" }));
    await expect(postSubscription(subscriptionJson, "ua")).rejects.toBeInstanceOf(WebPushApiError);
  });
});

// ------------------------------------------------------------- listSubscriptions / deleteSubscription

describe("listSubscriptions", () => {
  it("parses rows and drops anything without an id", async () => {
    apiFetch.mockResolvedValueOnce(
      json(200, {
        subscriptions: [
          { id: "a", endpoint_host: "fcm.googleapis.com", created_at: null, last_success_at: null, last_error_reason: null, failure_count: 0 },
          { endpoint_host: "no-id-here" },
        ],
      }),
    );
    const rows = await listSubscriptions();
    expect(rows.map((r) => r.id)).toEqual(["a"]);
  });
});

describe("deleteSubscription", () => {
  it("does not throw on 200 or 404", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { removed: true }));
    await expect(deleteSubscription("a")).resolves.toBeUndefined();
    apiFetch.mockResolvedValueOnce(json(404, { detail: "unknown subscription" }));
    await expect(deleteSubscription("b")).resolves.toBeUndefined();
  });

  it("throws on an unexpected failure", async () => {
    apiFetch.mockResolvedValueOnce(json(500, { detail: "boom" }));
    await expect(deleteSubscription("c")).rejects.toBeInstanceOf(WebPushApiError);
  });
});

// ------------------------------------------------------------- browser feature detection

describe("pushSupport", () => {
  it("is unsupported with no navigator/window at all", () => {
    vi.stubGlobal("navigator", undefined);
    vi.stubGlobal("window", undefined);
    expect(pushSupport()).toBe("unsupported");
  });

  it("is unsupported when PushManager is missing", () => {
    vi.stubGlobal("navigator", { serviceWorker: {} });
    vi.stubGlobal("window", {});
    expect(pushSupport()).toBe("unsupported");
  });

  it("is supported when both serviceWorker and PushManager are present", () => {
    vi.stubGlobal("navigator", { serviceWorker: {} });
    vi.stubGlobal("window", { PushManager: FakePushManager });
    expect(pushSupport()).toBe("supported");
  });
});

// ------------------------------------------------------------- homeScreenState / readHomeScreenInput

describe("homeScreenState", () => {
  const tab = { standalone: false, displayModeStandalone: false, maxTouchPoints: 5 };

  it("an iPhone Safari tab on iOS 17 needs the Home Screen, not 'unsupported'", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.iphone17 })).toBe("ios_needs_home_screen");
  });

  it("an iPhone opened from the Home Screen (navigator.standalone) goes on with today's flow", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.iphone17, standalone: true })).toBe("ios_home_screen");
  });

  it("display-mode: standalone alone also counts as the Home Screen app", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.iphone17, displayModeStandalone: true })).toBe("ios_home_screen");
  });

  it("iOS 16.3 is too old for web push, even from the Home Screen", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.iphone163 })).toBe("ios_too_old");
    expect(homeScreenState({ ...tab, userAgent: UA.iphone163, standalone: true })).toBe("ios_too_old");
  });

  it("iOS 16.4 is the first version that is not too old", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.iphone164 })).toBe("ios_needs_home_screen");
  });

  it("an iPad with the desktop UA (Macintosh + touch points) in a tab needs the Home Screen; unknown version is not 'too old'", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.ipadDesktop, maxTouchPoints: 5 })).toBe("ios_needs_home_screen");
  });

  it("a real Mac (Macintosh, no touch points) is not iOS", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.ipadDesktop, maxTouchPoints: 0 })).toBe("not_ios");
  });

  it("Android Chrome is not iOS", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.androidChrome })).toBe("not_ios");
  });

  it("Windows Chrome is not iOS", () => {
    expect(homeScreenState({ ...tab, userAgent: UA.windowsChrome, maxTouchPoints: 0 })).toBe("not_ios");
  });
});

describe("readHomeScreenInput", () => {
  it("falls back to safe defaults with no navigator/window at all (SSR)", () => {
    vi.stubGlobal("navigator", undefined);
    vi.stubGlobal("window", undefined);
    expect(readHomeScreenInput()).toEqual({ userAgent: "", standalone: false, displayModeStandalone: false, maxTouchPoints: 0 });
  });

  it("reads navigator.standalone and the display-mode media query", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true });
    vi.stubGlobal("navigator", { userAgent: UA.iphone17, standalone: true, maxTouchPoints: 5 });
    vi.stubGlobal("window", { matchMedia });
    expect(readHomeScreenInput()).toEqual({ userAgent: UA.iphone17, standalone: true, displayModeStandalone: true, maxTouchPoints: 5 });
    expect(matchMedia).toHaveBeenCalledWith("(display-mode: standalone)");
  });
});

describe("notificationPermission", () => {
  it("is 'unsupported' with no Notification global", () => {
    vi.stubGlobal("Notification", undefined);
    expect(notificationPermission()).toBe("unsupported");
  });

  it("reflects Notification.permission otherwise", () => {
    vi.stubGlobal("Notification", { permission: "granted" });
    expect(notificationPermission()).toBe("granted");
  });
});

// ------------------------------------------------------------- currentBrowserSubscription / unsubscribeBrowser

function stubSupportedBrowser(registration: unknown) {
  vi.stubGlobal("navigator", {
    serviceWorker: { getRegistration: vi.fn().mockResolvedValue(registration) },
  });
  vi.stubGlobal("window", { PushManager: FakePushManager });
}

describe("currentBrowserSubscription", () => {
  it("is null when unsupported", async () => {
    vi.stubGlobal("navigator", undefined);
    vi.stubGlobal("window", undefined);
    expect(await currentBrowserSubscription()).toBeNull();
  });

  it("is null when there is no registration yet", async () => {
    stubSupportedBrowser(undefined);
    expect(await currentBrowserSubscription()).toBeNull();
  });

  it("returns the registration's own subscription when present", async () => {
    const fakeSubscription = { endpoint: "https://fcm.googleapis.com/fcm/send/x" };
    stubSupportedBrowser({ pushManager: { getSubscription: vi.fn().mockResolvedValue(fakeSubscription) } });
    expect(await currentBrowserSubscription()).toBe(fakeSubscription);
  });
});

describe("unsubscribeBrowser", () => {
  it("does nothing when there is no subscription", async () => {
    stubSupportedBrowser({ pushManager: { getSubscription: vi.fn().mockResolvedValue(null) } });
    await expect(unsubscribeBrowser()).resolves.toBeUndefined();
  });

  it("calls unsubscribe() on the existing subscription", async () => {
    const unsubscribe = vi.fn().mockResolvedValue(true);
    stubSupportedBrowser({ pushManager: { getSubscription: vi.fn().mockResolvedValue({ unsubscribe }) } });
    await unsubscribeBrowser();
    expect(unsubscribe).toHaveBeenCalledOnce();
  });
});

// ------------------------------------------------------------- registerAndSubscribe

function stubRegisterFlow(opts: {
  permission: NotificationPermission;
  existing?: unknown;
  subscribeResult?: unknown;
}) {
  const subscribe = vi.fn().mockResolvedValue(opts.subscribeResult ?? { endpoint: "https://fcm.googleapis.com/fcm/send/new" });
  const getSubscription = vi.fn().mockResolvedValue(opts.existing ?? null);
  const register = vi.fn().mockResolvedValue({ pushManager: { getSubscription, subscribe } });
  vi.stubGlobal("navigator", { serviceWorker: { register } });
  vi.stubGlobal("window", { PushManager: FakePushManager });
  vi.stubGlobal("Notification", { requestPermission: vi.fn().mockResolvedValue(opts.permission) });
  return { register, subscribe, getSubscription };
}

describe("registerAndSubscribe", () => {
  it("throws push_unsupported when the browser cannot do Push", async () => {
    vi.stubGlobal("navigator", undefined);
    vi.stubGlobal("window", undefined);
    await expect(registerAndSubscribe("key")).rejects.toThrow("push_unsupported");
  });

  it("registers the service worker, requests permission, and subscribes with the converted key", async () => {
    const { register, subscribe } = stubRegisterFlow({ permission: "granted" });
    await registerAndSubscribe("AAECAw");
    expect(register).toHaveBeenCalledWith("/sw.js");
    expect(subscribe).toHaveBeenCalledOnce();
    const args = subscribe.mock.calls[0][0] as { userVisibleOnly: boolean; applicationServerKey: Uint8Array };
    expect(args.userVisibleOnly).toBe(true);
    expect(Array.from(args.applicationServerKey)).toEqual([0, 1, 2, 3]);
  });

  it("throws permission_denied and never subscribes when the owner declines", async () => {
    const { subscribe } = stubRegisterFlow({ permission: "denied" });
    await expect(registerAndSubscribe("AAECAw")).rejects.toThrow("permission_denied");
    expect(subscribe).not.toHaveBeenCalled();
  });

  it("reuses an existing subscription instead of subscribing again", async () => {
    const existing = { endpoint: "https://fcm.googleapis.com/fcm/send/existing" };
    const { subscribe } = stubRegisterFlow({ permission: "granted", existing });
    const result = await registerAndSubscribe("AAECAw");
    expect(result).toBe(existing);
    expect(subscribe).not.toHaveBeenCalled();
  });
});
