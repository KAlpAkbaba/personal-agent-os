"use client";

/**
 * jarvis-calls-owner: "önemli bir şey olunca JARVIS beni arasın" - the phone panel.
 *
 * What the Cloud Core reports, and one button. The Twilio Account SID and auth token live in
 * the Cloud Core's env file and nowhere else; the status route has none to give, and
 * `parseTelephonyStatus` keeps only the fields named below, so a credential could not ride
 * along even if a server ever sent one. The numbers are set in the same env file - the owner
 * number is the ONE number a call may go to, so it is not something a browser changes.
 *
 * The Twilio trial opens every call with an English notice and asks for a key press before
 * JARVIS speaks; the page says so, because the owner will hear it.
 */

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../lib/session";

export const TELEPHONY_STATUS_PATH = "/v1/telephony/status";
export const TELEPHONY_TEST_CALL_PATH = "/v1/telephony/test-call";

export type TelephonyStatus = {
  connected: boolean;
  ownerNumber: string | null;
  fromNumber: string | null;
  speaksWith: "audio" | "say";
  maxCallsPerHour: number;
  quietHours: string;
  trialNote: string;
};

export type TestCallOutcome =
  | { ok: true; status: string; reason: string; callSid: string | null; spoken: string | null }
  | { ok: false; message: string };

const text = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

export function parseTelephonyStatus(raw: unknown): TelephonyStatus {
  const body = (raw ?? {}) as Record<string, unknown>;
  return {
    connected: body.connected === true,
    ownerNumber: text(body.owner_number),
    fromNumber: text(body.from_number),
    speaksWith: body.speaks_with === "audio" ? "audio" : "say",
    maxCallsPerHour: typeof body.max_calls_per_hour === "number" ? body.max_calls_per_hour : 0,
    quietHours: text(body.quiet_hours) ?? "",
    trialNote: text(body.trial_note) ?? "",
  };
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* no JSON body */
  }
  return `API hatası (HTTP ${response.status})`;
}

export async function fetchTelephonyStatus(): Promise<TelephonyStatus> {
  const response = await apiFetch(TELEPHONY_STATUS_PATH);
  if (!response.ok) throw new Error(await readDetail(response));
  return parseTelephonyStatus(await response.json());
}

export async function requestTestCall(): Promise<TestCallOutcome> {
  const response = await apiFetch(TELEPHONY_TEST_CALL_PATH, { method: "POST" });
  if (!response.ok) return { ok: false, message: await readDetail(response) };
  const body = (await response.json()) as Record<string, unknown>;
  return {
    ok: true,
    status: text(body.status) ?? "",
    reason: text(body.reason) ?? "",
    callSid: text(body.call_sid),
    spoken: text(body.spoken),
  };
}

const SKIP_REASON_TR: Record<string, string> = {
  hourly_cap: "bu saatteki arama sınırı doldu",
  quiet_hours: "sessiz saatler",
};

export function testCallSentence(outcome: TestCallOutcome): string {
  if (!outcome.ok) return outcome.message;
  if (outcome.status === "placed") {
    return `Arama başladı - telefonun çalacak (kayıt ${outcome.callSid ?? "?"}).`;
  }
  return `Arama yapılmadı: ${SKIP_REASON_TR[outcome.reason] ?? outcome.reason}.`;
}

export function TelephonyPanel({
  status,
  result,
  busy,
  onTestCall,
}: {
  status: TelephonyStatus | null;
  result: string | null;
  busy: boolean;
  onTestCall: () => void;
}) {
  return (
    <section className="panel" data-panel="telephony-settings">
      <h3 className="panel-title">
        <span>Telefon araması</span>
        <span className="panel-count">Twilio</span>
      </h3>
      <p className="muted">
        Önemli bir şey olunca (kritik güvenlik olayı, bozulan sürüm, &apos;beni ara&apos; dediğin alarm,
        cevapsız harcama sorusu) JARVIS seni arar ve ne olduğunu Türkçe anlatır. Açmazsan 5 dakika
        sonra bir kez daha arar, sonra bildirim bırakır.
      </p>
      {status === null ? (
        <p className="muted">Kontrol ediliyor…</p>
      ) : (
        <>
          <p data-telephony-state={status.connected ? "connected" : "not_connected"}>
            {status.connected ? "Bağlı" : "Bağlı değil"}
          </p>
          <ul className="muted">
            <li>Senin numaran: {status.ownerNumber ?? "ayarlanmamış"}</li>
            <li>Twilio numarası: {status.fromNumber ?? "ayarlanmamış"}</li>
            <li>
              Ses: {status.speaksWith === "audio" ? "JARVIS'in kendi sesi" : "Twilio'nun Türkçe sesi"} · saatte en çok{" "}
              {status.maxCallsPerHour} arama · sessiz saatler {status.quietHours} (yalnız kritik olanlar arar)
            </li>
          </ul>
          {!status.connected && (
            <p className="muted">
              Twilio bilgileri (Account SID, auth token) Cloud Core&apos;un ortam dosyasına girilir; bu sayfa
              onları hiç görmez.
            </p>
          )}
          <p className="muted">{status.trialNote}</p>
          {status.connected && (
            <button type="button" className="core-chip" onClick={onTestCall} disabled={busy} data-telephony-test>
              Test araması yap
            </button>
          )}
        </>
      )}
      {result && (
        <p className="muted" data-telephony-result>
          {result}
        </p>
      )}
    </section>
  );
}

export function TelephonySettings() {
  const [status, setStatus] = useState<TelephonyStatus | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchTelephonyStatus().then(setStatus, (error: unknown) =>
      setResult(error instanceof Error ? error.message : String(error)),
    );
  }, []);

  const testCall = useCallback(async () => {
    setBusy(true);
    try {
      setResult(testCallSentence(await requestTestCall()));
    } catch (error) {
      setResult(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, []);

  return <TelephonyPanel status={status} result={result} busy={busy} onTestCall={() => void testCall()} />;
}
