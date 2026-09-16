/**
 * B35 (req 609, 621): the self-development queue's candidates on /selfdev — the facts the
 * owner decides by, printed from the row; the pair; and the sentence that approval is a
 * recorded decision and never a promotion (req 624).
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SelfDevQueuePanel } from "../../app/core/panels/CockpitPanels";
import {
  CANDIDATE_STATE_TR,
  type PendingCandidate,
  SELFDEV_PENDING_PATH,
  approvalFamily,
  candidateApprovePath,
  candidateRejectPath,
  parseCandidate,
  parseReceipt,
} from "../../app/lib/cockpit/approvals";
import { OUTCOME_NO_STATE_TR, outcomeText } from "../../app/lib/cockpit/useApprovalPair";

const ROW = {
  defect_id: "7b1d0c6e-0000-4000-8000-000000000001",
  kind: "bug",
  source: "owner_voice",
  title: "add() subtracts",
  state: "awaiting_owner",
  branch: "selfdev/20260915-120000-add-abc123",
  candidate_sha: "c".repeat(40),
  promotion_class: "OWNER_APPROVAL_REQUIRED",
  never_auto_promote: false,
  risk_tier: 3,
  security_review: { passed: true, findings: [] },
  gate: { state: "passed" },
  shadow: { state: "passed", mismatches: [] },
  ci: { state: "none" },
  explanation: "toplama çıkarma yapıyordu; düzeltildi.",
  tokens_used: 1500,
  finished_at: "2026-09-15T10:00:00Z",
};

const NOW = Date.parse("2026-09-15T10:01:00Z");
const pair = { busy: null, outcome: null, onConfirm: () => {}, onDiscard: () => {} };

describe("the pending candidates", () => {
  it("are read verbatim from the row, refused without an id, and read back by their record", () => {
    const parsed = parseCandidate(ROW);
    expect(parsed).not.toBeNull();
    expect(parsed?.security_review_passed).toBe(true);
    expect(parsed?.gate_state).toBe("passed");
    expect(parsed?.shadow_state).toBe("passed");
    expect(parsed?.read_back_at).toBe(ROW.finished_at);
    expect(parseCandidate({ kind: "bug" })).toBeNull();
    expect(parseCandidate(null)).toBeNull();
  });

  it("approve and reject through the Cloud Core's own routes, and belong to their own family", () => {
    expect(SELFDEV_PENDING_PATH).toBe("/v1/selfdev/defects/pending");
    expect(candidateApprovePath(ROW.defect_id)).toBe(`/v1/selfdev/defects/${ROW.defect_id}/approve`);
    expect(candidateRejectPath("a b")).toBe("/v1/selfdev/defects/a%20b/reject");
    expect(approvalFamily("approve_candidate")).toBe("candidate");
    expect(approvalFamily("reject_candidate")).toBe("candidate");
    expect(approvalFamily("confirm_mutation")).toBe("mutation");
  });

  it("speak the receipt's state in Turkish, read the state from the defect the route answers with, and never say promoted", () => {
    expect(outcomeText("approve_candidate", { state: "approved", summary: null, receiptId: null })).toBe(
      CANDIDATE_STATE_TR.approved,
    );
    expect(CANDIDATE_STATE_TR.approved).toContain("canlıya alınmadı");
    expect(outcomeText("reject_candidate", { state: null, summary: null, receiptId: null })).toBe(
      OUTCOME_NO_STATE_TR.reject_candidate,
    );
    expect(parseReceipt({ defect: { state: "approved" }, promoted: false }).state).toBe("approved");
  });
});

describe("the SelfDev queue panel", () => {
  it("lists a candidate with its facts, its explanation and the pair whose label says it does not promote", () => {
    const value = [parseCandidate(ROW) as PendingCandidate];
    const html = renderToStaticMarkup(
      <SelfDevQueuePanel state={{ kind: "ok", value, at: NOW }} now={NOW} pair={pair} always />,
    );
    expect(html).toContain('id="selfdev-queue"');
    expect(html).toContain(`data-candidate="${ROW.defect_id}"`);
    expect(html).toContain('data-candidate-security="passed"');
    expect(html).toContain("güvenlik incelemesi geçti");
    expect(html).toContain("kapı passed");
    expect(html).toContain("gölge passed");
    expect(html).toContain("sahip onayı gerekir");
    expect(html).toContain("toplama çıkarma yapıyordu");
    expect(html).toContain('data-approval-family="candidate"');
    expect(html).toContain('data-approval-enabled="yes"');
    expect(html).toContain("Onayla — kaydet (canlıya almaz)");
    expect(html).not.toContain("data-candidate-never-auto-line");
  });

  it("says in as many words that a NEVER_AUTO_PROMOTE candidate is never promoted by itself, and flags a failed review", () => {
    const value = [
      parseCandidate({
        ...ROW,
        promotion_class: "NEVER_AUTO_PROMOTE",
        never_auto_promote: true,
        risk_tier: 4,
        security_review: { passed: false, findings: [{ check: "no_secret_literal" }] },
      }) as PendingCandidate,
    ];
    const html = renderToStaticMarkup(
      <SelfDevQueuePanel state={{ kind: "ok", value, at: NOW }} now={NOW} pair={pair} always />,
    );
    expect(html).toContain('data-candidate-never-auto="yes"');
    expect(html).toContain("hiçbir zaman kendiliğinden canlıya alınmaz");
    expect(html).toContain('data-candidate-security="failed"');
    expect(html).toContain("güvenlik incelemesi RED");
  });

  it("says there is nothing when nothing waits, and keeps a settled row out of the count", () => {
    const value = [parseCandidate({ ...ROW, state: "approved" }) as PendingCandidate];
    const html = renderToStaticMarkup(
      <SelfDevQueuePanel state={{ kind: "ok", value, at: NOW }} now={NOW} pair={pair} always />,
    );
    expect(html).toContain("Onayınızı bekleyen aday yok.");
    expect(html).not.toContain("data-approval-pair");
  });
});
