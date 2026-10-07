"use client";

/**
 * `/core/urgent-alert` - 'Önemli olunca telefon çalsın' (urgent-alert-wire): whether the phone
 * alarm (Pushover, through silent mode) is connected, what became of the last alarm, and a
 * test. The page holds the state; everything shown is UrgentAlertView's, everything decided is
 * the Cloud Core's.
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../../components/FamilyPage";
import UrgentAlertView from "./UrgentAlertView";
import {
  fetchAktivraStatus,
  fetchStatus,
  sendTest,
  type AktivraStatus,
  type UrgentAlertStatus,
} from "./urgentAlertApi";

export default function UrgentAlertPage() {
  const [status, setStatus] = useState<UrgentAlertStatus | null>(null);
  const [aktivra, setAktivra] = useState<AktivraStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const result = await fetchStatus();
    if (result.ok) {
      setStatus(result.status);
      setError(null);
    } else {
      setError(result.message);
    }
    // Aktivra's line is a side note: a refusal leaves it out rather than taking the page down.
    const channel = await fetchAktivraStatus();
    setAktivra(channel.ok ? channel.status : null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const test = async () => {
    setBusy(true);
    const result = await sendTest();
    setBusy(false);
    setNotice(
      result.ok
        ? "Deneme gönderildi; telefon bir dakika içinde çalar. 'Gördüm'e basınca burada görünür."
        : result.message,
    );
    void refresh();
  };

  return (
    <FamilyPage
      id="urgent-alert"
      title="Önemli olunca telefon çalsın"
      lead="Önemli bir şey olduğunda telefonun sessizde bile çalar; telefonda yalnız tek bir sözcük görünür, ayrıntı burada."
    >
      <UrgentAlertView
        status={status}
        aktivra={aktivra}
        error={error}
        notice={notice}
        busy={busy}
        onTest={() => void test()}
      />
    </FamilyPage>
  );
}
