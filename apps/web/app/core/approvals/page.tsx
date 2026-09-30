"use client";

/**
 * `/core/approvals` - the Onay Merkezi.
 *
 * The newest cycle report and every task waiting at the owner's two gates (fikir, yayın),
 * each with its proposal or report and two buttons. Onayla / Reddet write the decision for
 * the NEXT cycle; nothing here runs a task or a release, and the page says so beside each
 * button. Reddet needs a reason. The voice path ("fikri onayla" / "yayını onayla") is a later
 * task and uses the same API.
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../../components/FamilyPage";
import {
  GATE_TR,
  decide,
  fetchApprovals,
  type ApprovalsView,
  type PendingApproval,
} from "./approvalsApi";

function ApprovalCard({
  approval,
  locked,
  onDone,
}: {
  approval: PendingApproval;
  locked: boolean;
  onDone: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);

  const send = async (decision: "approve" | "reject") => {
    setBusy(true);
    const result = await decide(approval, decision, decision === "reject" ? reason : undefined);
    setBusy(false);
    if (result.ok) {
      setAnswer(null);
      onDone();
    } else {
      setAnswer(result.message);
    }
  };

  return (
    <li className="detail-row" data-approval={approval.task_id} data-gate={approval.gate}>
      <span className="detail-head">
        {GATE_TR[approval.gate]} · {approval.title}
      </span>
      {approval.goal && <span className="muted">{approval.goal}</span>}
      {approval.proposal_text && <pre>{approval.proposal_text}</pre>}
      {approval.reports.length > 0 && (
        <ul>
          {approval.reports.map((report) => (
            <li key={`${report.cycle}-${report.role}-${report.at}`}>
              <strong>
                {report.role} · {report.cycle}
              </strong>
              <pre>{report.summary.join("\n")}</pre>
            </li>
          ))}
        </ul>
      )}
      <input
        aria-label={`Red gerekçesi: ${approval.task_id}`}
        placeholder="Reddedersen gerekçe (zorunlu)"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      />
      <button type="button" disabled={busy || locked} onClick={() => void send("approve")}>
        {approval.gate === "yayin" ? "Yayını onayla" : "Onayla"}
      </button>
      <button
        type="button"
        disabled={busy || locked || reason.trim() === ""}
        onClick={() => void send("reject")}
      >
        Reddet
      </button>
      <span className="muted">
        {approval.gate === "yayin"
          ? "Onay yalnızca kuyruğa yazılır; yayını başlatmaz."
          : "Karar bir sonraki döngüde uygulanır."}
      </span>
      {answer && <span role="alert">{answer}</span>}
    </li>
  );
}

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
      {view?.cycle_running && (
        <p className="muted">Bir döngü çalışıyor; bitene kadar karar verilemez.</p>
      )}
      {view && view.approvals.length === 0 && <p className="muted">Bekleyen onay yok.</p>}
      {view && view.approvals.length > 0 && (
        <ul className="detail-list">
          {view.approvals.map((approval) => (
            <ApprovalCard
              key={approval.task_id}
              approval={approval}
              locked={view.cycle_running}
              onDone={() => void refresh()}
            />
          ))}
        </ul>
      )}
      {view?.cycle_report && (
        <section data-section="cycle-report">
          <h2>Döngü raporu · {view.cycle_report.file}</h2>
          <pre>{view.cycle_report.text}</pre>
        </section>
      )}
    </FamilyPage>
  );
}
