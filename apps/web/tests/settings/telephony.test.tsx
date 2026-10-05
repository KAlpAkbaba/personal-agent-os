/**
 * jarvis-calls-owner: the settings page's phone panel - "bağlı / bağlı değil", the owner's
 * number (masked by the Cloud Core), the Twilio number, the trial's English preamble said
 * out loud, and the 'Test araması yap' button that POSTs once.
 *
 * The page never sees a Twilio credential: the status route has none to give, and nothing
 * here would show one.
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
  TELEPHONY_STATUS_PATH,
  TELEPHONY_TEST_CALL_PATH,
  TelephonyPanel,
  fetchTelephonyStatus,
  parseTelephonyStatus,
  requestTestCall,
  testCallSentence,
} from "../../app/settings/TelephonySettings";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const CONNECTED = {
  connected: true,
  owner_number: "+90*******33",
  from_number: "+15005550006",
  speaks_with: "audio",
  max_calls_per_hour: 3,
  quiet_hours: "23:00-07:30",
  trial_note: "Twilio deneme (trial) hesabında her arama önce İngilizce bir uyarıyla başlar.",
};

beforeEach(() => {
  apiFetch.mockReset();
});

describe("the telephony client", () => {
  it("reads the Cloud Core's status route", async () => {
    apiFetch.mockResolvedValueOnce(json(200, CONNECTED));

    const status = await fetchTelephonyStatus();

    expect(apiFetch.mock.calls[0][0]).toBe(TELEPHONY_STATUS_PATH);
    expect(TELEPHONY_STATUS_PATH).toBe("/v1/telephony/status");
    expect(status.connected).toBe(true);
    expect(status.ownerNumber).toBe("+90*******33");
    expect(status.fromNumber).toBe("+15005550006");
  });

  it("POSTs the test call once and reads its outcome", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { status: "placed", reason: "telephony.test", call_sid: "CA1", spoken: "audio", attempt: 1 }));

    const outcome = await requestTestCall();

    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][0]).toBe(TELEPHONY_TEST_CALL_PATH);
    expect(TELEPHONY_TEST_CALL_PATH).toBe("/v1/telephony/test-call");
    expect((apiFetch.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(outcome).toEqual({ ok: true, status: "placed", reason: "telephony.test", callSid: "CA1", spoken: "audio" });
  });

  it("carries the Cloud Core's Turkish refusal when it is not connected", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { detail: "Telefon araması bağlı değil (Twilio bilgileri eksik)." }));

    const outcome = await requestTestCall();

    expect(outcome.ok).toBe(false);
    expect(testCallSentence(outcome)).toBe("Telefon araması bağlı değil (Twilio bilgileri eksik).");
  });

  it("says what happened to the test call in Turkish", () => {
    expect(testCallSentence({ ok: true, status: "placed", reason: "telephony.test", callSid: "CA1", spoken: "audio" })).toBe(
      "Arama başladı - telefonun çalacak (kayıt CA1).",
    );
    expect(testCallSentence({ ok: true, status: "skipped", reason: "hourly_cap", callSid: null, spoken: null })).toBe(
      "Arama yapılmadı: bu saatteki arama sınırı doldu.",
    );
  });

  it("never keeps a field it does not know - a credential could not ride along", () => {
    const status = parseTelephonyStatus({ ...CONNECTED, auth_token: "secret", account_sid: "AC1" });

    expect(JSON.stringify(status)).not.toContain("secret");
    expect(JSON.stringify(status)).not.toContain("AC1");
  });
});

describe("the phone panel", () => {
  it("says bağlı with both numbers, the trial note and the test button", () => {
    const html = renderToStaticMarkup(
      <TelephonyPanel status={parseTelephonyStatus(CONNECTED)} result={null} busy={false} onTestCall={() => undefined} />,
    );

    expect(html).toContain('data-telephony-state="connected"');
    expect(html).toContain("Bağlı");
    expect(html).toContain("+90*******33");
    expect(html).toContain("+15005550006");
    expect(html).toContain("İngilizce bir uyarıyla");
    expect(html).toContain("data-telephony-test");
    expect(html).toContain("Test araması yap");
    expect(html).not.toContain("disabled");
  });

  it("says bağlı değil and offers no test call", () => {
    const html = renderToStaticMarkup(
      <TelephonyPanel
        status={parseTelephonyStatus({ ...CONNECTED, connected: false, owner_number: null, from_number: null })}
        result={null}
        busy={false}
        onTestCall={() => undefined}
      />,
    );

    expect(html).toContain('data-telephony-state="not_connected"');
    expect(html).toContain("Bağlı değil");
    expect(html).not.toContain("data-telephony-test");
  });
});
