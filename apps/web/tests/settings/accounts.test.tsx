/**
 * Ayarlar > Hesaplar (card mail-accounts-connect): the page shows each named account, the
 * two connect buttons, the exact redirect URL and the owner's setup steps; the client calls
 * the Cloud Core's own routes and only ever sends the browser to Google's or Microsoft's
 * sign-in page.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  ACCOUNTS_CONNECT_PATH,
  ACCOUNTS_PATH,
  type AccountsData,
  accountPath,
  connectAccount,
  disconnectAccount,
  fetchAccounts,
  renameAccount,
} from "../../app/settings/accounts/accounts";
import { AccountsView, type AccountsViewProps } from "../../app/settings/accounts/AccountsView";

const REDIRECT = "https://pagentos-core.tail1234.ts.net/v1/accounts/oauth/callback";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function data(overrides: Partial<AccountsData["setup"]> = {}): AccountsData {
  return {
    accounts: [
      {
        id: "a1",
        name: "Kişisel",
        provider: "gmail",
        address: "kisisel@gmail.com",
        scopes: [],
        state: "connected",
        connected_at: "2026-10-05T12:00:00+00:00",
        last_sync_at: "2026-10-05T12:05:00+00:00",
        last_error: null,
      },
      {
        id: "a2",
        name: "İş",
        provider: "microsoft",
        address: "is@aktivra.com.tr",
        scopes: [],
        state: "error",
        connected_at: "2026-10-05T12:01:00+00:00",
        last_sync_at: null,
        last_error: "refresh_failed",
      },
      {
        id: "a3",
        name: "Aktivra",
        provider: "gmail",
        address: "aktivra@gmail.com",
        scopes: [],
        state: "connected",
        connected_at: "2026-10-05T12:02:00+00:00",
        last_sync_at: null,
        last_error: null,
      },
    ],
    setup: {
      redirect_uri: REDIRECT,
      public_base_configured: true,
      google: { configured: true, console_url: "https://console.cloud.google.com/apis/credentials", steps: ["Gmail API adımı"] },
      microsoft: { configured: false, console_url: "https://entra.microsoft.com/", steps: ["Entra kayıt adımı"] },
      ...overrides,
    },
  };
}

function render(props: Partial<AccountsViewProps> = {}): string {
  return renderToStaticMarkup(
    <AccountsView
      data={data()}
      error={null}
      message={null}
      busy={false}
      newName=""
      onNewName={() => {}}
      onConnect={() => {}}
      onRename={() => {}}
      onDisconnect={() => {}}
      {...props}
    />,
  );
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe("the accounts page", () => {
  it("lists the three named accounts with provider, address, state and sync", () => {
    const html = render();
    expect(html).toContain('data-account="Kişisel"');
    expect(html).toContain('data-account="İş"');
    expect(html).toContain('data-account="Aktivra"');
    expect(html).toContain("Gmail · kisisel@gmail.com · Bağlı · son eşitleme");
    expect(html).toContain("Microsoft 365 · is@aktivra.com.tr · Sorun var (refresh_failed) · henüz eşitlenmedi");
    expect(html).toContain('data-account-rename="a1"');
    expect(html).toContain('data-account-disconnect="a2"');
  });

  it("offers both connect buttons and keeps an unconfigured provider's button off", () => {
    const html = render();
    expect(html).toMatch(/<button[^>]*data-connect="gmail"[^>]*>Gmail bağla<\/button>/);
    expect(html).toMatch(/<button[^>]*data-connect="microsoft"[^>]*disabled=""[^>]*>Microsoft 365 bağla<\/button>/);
    expect(html).not.toMatch(/data-connect="gmail"[^>]*disabled/);
  });

  it("shows the exact redirect URL and opens the steps of the provider still to set up", () => {
    const html = render();
    expect(html).toContain(`<code data-redirect-uri="true">${REDIRECT}</code>`);
    expect(html).toMatch(/<details data-setup="microsoft" open="">/);
    expect(html).toMatch(/<details data-setup="gmail">/);
    expect(html).toContain("Entra kayıt adımı");
  });

  it("says there is nothing yet, and says why connecting is off without a public address", () => {
    const html = render({ data: { ...data({ public_base_configured: false, redirect_uri: "" }), accounts: [] } });
    expect(html).toContain("Henüz bağlı hesap yok.");
    expect(html).toContain("PAGENTOS_ACCOUNTS_PUBLIC_BASE_URL");
    expect(html).toMatch(/data-connect="gmail"[^>]*disabled/);
  });
});

describe("the accounts client", () => {
  it("reads the Cloud Core's own routes", async () => {
    expect(ACCOUNTS_PATH).toBe("/v1/accounts");
    expect(ACCOUNTS_CONNECT_PATH).toBe("/v1/accounts/connect");
    expect(accountPath("a 1")).toBe("/v1/accounts/a%201");
    apiFetch.mockResolvedValueOnce(json(200, data()));
    const loaded = await fetchAccounts();
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/accounts");
    expect(loaded.accounts.map((a) => a.name)).toEqual(["Kişisel", "İş", "Aktivra"]);
  });

  it("connects with the provider and the trimmed name, and goes only to a provider's sign-in page", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" }));
    const ok = await connectAccount("gmail", "  İş ");
    expect(ok).toEqual({ ok: true, url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" });
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe("/v1/accounts/connect");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ provider: "gmail", name: "İş" });

    apiFetch.mockResolvedValueOnce(json(200, { authorize_url: "https://evil.example/phish" }));
    expect((await connectAccount("microsoft", "İş")).ok).toBe(false);
  });

  it("carries the Cloud Core's refusal sentence and refuses an empty name without a call", async () => {
    apiFetch.mockResolvedValueOnce(json(400, { error: "name_taken", speech: "'İş' adında bir hesap zaten var efendim." }));
    expect(await connectAccount("gmail", "İş")).toEqual({ ok: false, speech: "'İş' adında bir hesap zaten var efendim." });
    const before = apiFetch.mock.calls.length;
    expect((await connectAccount("gmail", "   ")).ok).toBe(false);
    expect(apiFetch.mock.calls.length).toBe(before);
  });

  it("renames with PATCH and disconnects with DELETE, speaking the answer", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { name: "Aile" }));
    expect((await renameAccount("a1", " Aile ")).ok).toBe(true);
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/accounts/a1");
    expect(apiFetch.mock.calls[0][1].method).toBe("PATCH");
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ name: "Aile" });

    apiFetch.mockResolvedValueOnce(json(200, { revoked: true, speech: "'Kişisel' hesabının bağlantısı kesildi." }));
    expect(await disconnectAccount("a1")).toEqual({ ok: true, speech: "'Kişisel' hesabının bağlantısı kesildi." });
    expect(apiFetch.mock.calls[1][1].method).toBe("DELETE");
  });
});
