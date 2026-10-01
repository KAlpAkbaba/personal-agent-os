"use client";

/**
 * `/core/approvals` - the Onay Merkezi.
 *
 * The newest cycle report and every task waiting at the owner's two gates (fikir, yayın),
 * each with its report, a "Detay" that opens its proposal, and two buttons. Onayla / Reddet
 * write the decision for the NEXT cycle; nothing here runs a task or a release, and the page
 * says so beside each button. Reddet needs a reason. The voice path ("fikri onayla" /
 * "yayını onayla") is a later task and uses the same API. This file fetches; `ApprovalsList`
 * renders (a Next page may export nothing but its page, and the tests render the list).
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../../components/FamilyPage";
import ApprovalsList from "./ApprovalsList";
import { fetchApprovals, type ApprovalsView } from "./approvalsApi";

export default function ApprovalsPage() {
  const [view, setView] = useState<ApprovalsView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setView(await fetchApprovals());
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "alınamadı");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <FamilyPage
      id="approvals"
      title="Onay Merkezi"
      lead="Ekibin fikir ve yayın kapılarında sizi bekleyen işler. Kararınız bir sonraki döngüde uygulanır."
    >
      {error && (
        <p role="alert">
          Onaylar alınamadı: {error} <button onClick={() => void refresh()}>Yeniden dene</button>
        </p>
      )}
      {!error && view === null && <p className="muted">yükleniyor</p>}
      {view && <ApprovalsList view={view} onDone={() => void refresh()} />}
    </FamilyPage>
  );
}
