"use client";

/**
 * The Onay Merkezi's body: what waits at the owner's gates, each with "Detay" and its two
 * buttons, and the newest cycle report. `page.tsx` fetches; this renders what it fetched,
 * so it can be rendered - and tested - from a view alone.
 */

import { useState } from "react";

import ProposalDetail from "./ProposalDetail";
import TrialsList from "./TrialsList";
import {
  GATE_TR,
  decide,
  decisionsOpen,
  waitingCount,
  type ApprovalsView,
  type PendingApproval,
} from "./approvalsApi";

/** Onayla and Reddet lock together; Reddet also needs its reason. */
export function cardButtons({ busy, locked, reason }: { busy: boolean; locked: boolean; reason: string }): {
  approveDisabled: boolean;
  rejectDisabled: boolean;
} {
  const held = busy || locked;
  return { approveDisabled: held, rejectDisabled: held || reason.trim() === "" };
}

export function ApprovalCard({
  approval,
  locked,
  onDone,
  defaultOpen = false,
}: {
  approval: PendingApproval;
  locked: boolean;
  onDone: () => void;
  /** "Detay" starts closed; each card keeps its own, so several may be open. */
  defaultOpen?: boolean;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [open, setOpen] = useState(defaultOpen);

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

  // An idea always has a proposal to ask about; a release has one only when it began as an idea.
  const hasDetail = approval.gate === "fikir" || approval.proposal !== null || approval.proposal_text !== null;
  const detailId = `approval-detail-${approval.task_id}`;
  const { approveDisabled, rejectDisabled } = cardButtons({ busy, locked, reason });

  return (
    <li className="detail-row" data-approval={approval.task_id} data-gate={approval.gate}>
      <span className="detail-head">
        {GATE_TR[approval.gate]} · {approval.title}
      </span>
      {approval.goal && <span className="muted">{approval.goal}</span>}
      {hasDetail && (
        // Never locked: reading the proposal is not a decision.
        <button
          type="button"
          aria-expanded={open}
          aria-controls={open ? detailId : undefined}
          onClick={() => setOpen((was) => !was)}
        >
          Detay
        </button>
      )}
      {hasDetail && open && (
        // The card is a wrapping flex row (`detail-row`): the panel takes a row of its own.
        <div id={detailId} data-detail={approval.task_id} style={{ flexBasis: "100%" }}>
          <ProposalDetail text={approval.proposal_text} />
        </div>
      )}
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
      <button type="button" disabled={approveDisabled} onClick={() => void send("approve")}>
        {approval.gate === "yayin" ? "Yayını onayla" : "Onayla"}
      </button>
      <button type="button" disabled={rejectDisabled} onClick={() => void send("reject")}>
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

/** What the page says about deciding right now; null when there is nothing to say. */
function decisionNotice(view: ApprovalsView): string | null {
  const open = decisionsOpen(view);
  if (view.cycle_running) {
    return open
      ? "Bir döngü çalışıyor; kararınız bir sonraki döngüde uygulanır."
      : "Bir döngü çalışıyor; bitene kadar karar verilemez.";
  }
  // The server closed decisions without a cycle: the disabled buttons still get their reason.
  return open ? null : "Şu anda karar verilemez; biraz sonra yeniden deneyin.";
}

export default function ApprovalsList({ view, onDone }: { view: ApprovalsView; onDone: () => void }) {
  const notice = decisionNotice(view);
  const locked = !decisionsOpen(view);
  return (
    <>
      <p data-count="waiting">Sizi bekleyen: {waitingCount(view)}</p>
      {notice && <p className="muted">{notice}</p>}
      {view.approvals.length === 0 && <p className="muted">Bekleyen onay yok.</p>}
      {view.approvals.length > 0 && (
        <ul className="detail-list">
          {view.approvals.map((approval) => (
            <ApprovalCard key={approval.task_id} approval={approval} locked={locked} onDone={onDone} />
          ))}
        </ul>
      )}
      <TrialsList trials={view.trials ?? []} locked={locked} />
      {view.cycle_report && (
        <section data-section="cycle-report">
          <h2>Döngü raporu · {view.cycle_report.file}</h2>
          <pre>{view.cycle_report.text}</pre>
        </section>
      )}
    </>
  );
}
