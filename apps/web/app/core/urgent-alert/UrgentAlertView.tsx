/**
 * The phone alarm's page - markup only, a pure function of its props (no hooks), so the tests
 * render it with fixtures and press its button through the element tree.
 *
 * 'bağlı / bağlı değil', the last alarm ('görüldü HH:MM' / 'görülmedi'), and the test button -
 * absent, not disabled, while nothing is connected: a button that can only be refused is noise.
 * Below them Aktivra's channel (aktivra-inbound-events): 'Aktivra kanalı: bağlı, son olay HH:MM'.
 */

import {
  aktivraSentence,
  lastSentence,
  type AktivraStatus,
  type UrgentAlertStatus,
} from "./urgentAlertApi";

export type UrgentAlertViewProps = {
  status: UrgentAlertStatus | null;
  /** GET /v1/aktivra/status; null while unknown (the line is left out, never guessed). */
  aktivra?: AktivraStatus | null;
  error: string | null;
  notice: string | null;
  busy: boolean;
  onTest: () => void;
  timeZone?: string;
};

export default function UrgentAlertView({
  status,
  aktivra = null,
  error,
  notice,
  busy,
  onTest,
  timeZone,
}: UrgentAlertViewProps) {
  const last = status ? lastSentence(status, timeZone) : null;
  return (
    <>
      {error && <p role="alert">{error}</p>}
      {!error && status === null && <p className="muted">yükleniyor</p>}
      {notice && <p role="status">{notice}</p>}
      {status && (
        <>
          <p data-urgent-alert-connected={status.configured ? "yes" : "no"}>
            {status.configured ? "bağlı" : "bağlı değil"}
          </p>
          {last && <p data-urgent-alert-last>{last}</p>}
          {status.open_receipts > 0 && (
            <p className="muted" data-urgent-alert-ringing>
              Telefon şu an çalıyor olabilir ({status.open_receipts}).
            </p>
          )}
          {status.configured && (
            <p>
              <button type="button" disabled={busy} onClick={onTest} data-urgent-alert-test>
                Önemli deneme bildirimi gönder
              </button>
            </p>
          )}
        </>
      )}
      {aktivra && (
        <p data-aktivra-channel={aktivra.configured ? "yes" : "no"}>
          {aktivraSentence(aktivra, timeZone)}
        </p>
      )}
    </>
  );
}
