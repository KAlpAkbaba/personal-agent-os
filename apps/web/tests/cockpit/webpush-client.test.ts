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
  listSubscriptions,
  notificationPermission,
  postSubscription,
  pushSupport,
  registerAndSubscribe,
  unsubscribeBrowser,
  urlBase64ToUint8Array,
} from "../../app/lib/cockpit/webpush";

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
