/**
 * The 'Aktivra kanalı' line on the phone alarm page (aktivra-inbound-events): connected or not,
 * and the last event's time on the owner's clock. The client reads GET /v1/aktivra/status; the
 * keys are the ones app/aktivra/routes.py returns (that file is read here).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import UrgentAlertView, {
  type UrgentAlertViewProps,
} from "../../app/core/urgent-alert/UrgentAlertView";
import {
  aktivraSentence,
  fetchAktivraStatus,
  type AktivraStatus,
} from "../../app/core/urgent-alert/urgentAlertApi";

const fetchMock = vi.mocked(apiFetch);

const CONNECTED: AktivraStatus = {
  configured: true,
  last_event_at: "2026-10-07T06:42:00Z",
  events_24h: 2,
};

function props(over: Partial<UrgentAlertViewProps> = {}): UrgentAlertViewProps {
  return {
    status: { configured: true, open_receipts: 0, last_seen_at: null, last_outcome: null },
    aktivra: CONNECTED,
    error: null,
    notice: null,
    busy: false,
    onTest: () => {},
    timeZone: "Europe/Istanbul",
    ...over,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("the Aktivra line", () => {
  it("says connected with the last event's HH:MM", () => {
    expect(aktivraSentence(CONNECTED, "Europe/Istanbul")).toBe(
      "Aktivra kanalı: bağlı, son olay 09:42",
    );
  });

  it("says connected without a time before the first event", () => {
    expect(aktivraSentence({ ...CONNECTED, last_event_at: null })).toBe("Aktivra kanalı: bağlı");
  });

  it("says not connected without a token on the Cloud Core", () => {
    expect(
      aktivraSentence({ configured: false, last_event_at: null, events_24h: 0 }),
    ).toBe("Aktivra kanalı: bağlı değil");
  });

  it("is on the page", () => {
    const page = renderToStaticMarkup(<UrgentAlertView {...props()} />);
    expect(page).toContain("data-aktivra-channel");
    expect(page).toContain("Aktivra kanalı: bağlı, son olay 09:42");
  });

  it("is absent while the Aktivra status is unknown", () => {
    const page = renderToStaticMarkup(<UrgentAlertView {...props({ aktivra: null })} />);
    expect(page).not.toContain("Aktivra kanalı");
  });

  it("shows even when the phone alarm itself is not connected", () => {
    const page = renderToStaticMarkup(
      <UrgentAlertView
        {...props({
          status: { configured: false, open_receipts: 0, last_seen_at: null, last_outcome: null },
        })}
      />,
    );
    expect(page).toContain("Aktivra kanalı: bağlı");
  });
});

describe("the Aktivra status call", () => {
  it("GET /v1/aktivra/status", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(CONNECTED), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const result = await fetchAktivraStatus();
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/aktivra/status");
    expect(result).toEqual({ ok: true, status: CONNECTED });
  });

  it("a refusal stays a refusal", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 503 }));
    const result = await fetchAktivraStatus();
    expect(result.ok).toBe(false);
  });

  it("names exactly the keys routes.py returns", () => {
    const routes = readFileSync(
      join(__dirname, "..", "..", "..", "..", "services", "api", "app", "aktivra", "routes.py"),
      "utf8",
    );
    for (const key of Object.keys(CONNECTED)) {
      expect(routes).toContain(`"${key}"`);
    }
  });
});
