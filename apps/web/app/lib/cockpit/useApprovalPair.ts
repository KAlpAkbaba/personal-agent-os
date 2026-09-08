"use client";

/**
 * The approval pair's one piece of state, and the one rule it enforces:
 * one call at a time (M21 spec §3).
 *
 * `runApproval` is the whole of it, and it is pure over its ports so a test
 * can hand it a client double and prove that a press makes exactly one call
 * with the row's id, that a second press while the first is in flight makes
 * none, and that the answer — the receipt's state, or the gate's refusal in
 * the owner's words — is what the panel is then given. The hook below only
 * binds those ports to React state.
 *
 * What the outcome says is bounded by what the receipt said. A 2xx with a
 * state of `sent` is "Gönderildi"; a 2xx with no state is "the approval was
 * delivered; the receipt named no state" — never "sent", because nothing
 * said so.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CALENDAR_PROPOSAL_CAPTION } from "../uistate/calendar";
import { isCalendarProposalState, isMailDraftState } from "../uistate/contract";
import { MAIL_DRAFT_CAPTION } from "../uistate/mail";
import {
  APPROVAL_PAIR_IDLE,
  type ApprovalAction,
  type ApprovalClient,
  type ApprovalOutcome,
  type ApprovalPairProps,
  type ApprovalPairState,
  type ApprovalReceipt,
  approvalErrorText,
} from "./approvals";

/** The sentence for a 2xx whose receipt named no state: what happened, and what was not said. */
export const OUTCOME_NO_STATE_TR: Record<ApprovalAction, string> = {
  confirm_draft: "Onay iletildi; makbuz taslağın durumunu bildirmedi.",
  discard_draft: "Vazgeçme iletildi; makbuz taslağın durumunu bildirmedi.",
  confirm_proposal: "Onay iletildi; makbuz önerinin durumunu bildirmedi.",
  discard_proposal: "Vazgeçme iletildi; makbuz önerinin durumunu bildirmedi.",
};

function isDraftAction(action: ApprovalAction): boolean {
  return action === "confirm_draft" || action === "discard_draft";
}

/** The outcome line from a 2xx receipt: the row's state in the spec's words, then the receipt's own sentence. */
export function outcomeText(action: ApprovalAction, receipt: ApprovalReceipt): string {
  const parts: string[] = [];
  const state = receipt.state;
  if (state !== null && isDraftAction(action) && isMailDraftState(state)) parts.push(MAIL_DRAFT_CAPTION[state]);
  else if (state !== null && !isDraftAction(action) && isCalendarProposalState(state)) {
    parts.push(CALENDAR_PROPOSAL_CAPTION[state]);
  } else if (state !== null) parts.push(`durum: ${state}`);
  else parts.push(OUTCOME_NO_STATE_TR[action]);
  if (receipt.summary) parts.push(receipt.summary);
  return parts.join(" · ");
}

export type ApprovalPorts = {
  client: ApprovalClient;
  /** The current pair state; the one-at-a-time gate reads `busy` from here. */
  read: () => ApprovalPairState;
  write: (state: ApprovalPairState) => void;
  /** Called after every settled call, success or refusal, so the pending list is reloaded. */
  onSettled?: () => void;
  now?: () => number;
};

function call(client: ApprovalClient, action: ApprovalAction, id: string): Promise<ApprovalReceipt> {
  switch (action) {
    case "confirm_draft":
      return client.confirmDraft(id);
    case "discard_draft":
      return client.discardDraft(id);
    case "confirm_proposal":
      return client.confirmProposal(id);
    case "discard_proposal":
      return client.discardProposal(id);
  }
}

/**
 * Run one approval through the client. Returns `false`, having called
 * nothing, when a call is already in flight; `true` once the call settled
 * and its outcome was written.
 */
export async function runApproval(ports: ApprovalPorts, action: ApprovalAction, id: string): Promise<boolean> {
  const before = ports.read();
  if (before.busy !== null) return false;
  const now = ports.now ?? Date.now;
  ports.write({ busy: { action, id }, outcome: before.outcome });
  let outcome: ApprovalOutcome;
  try {
    const receipt = await call(ports.client, action, id);
    outcome = { action, id, ok: true, text: outcomeText(action, receipt), at: now() };
  } catch (err) {
    outcome = { action, id, ok: false, text: approvalErrorText(err), at: now() };
  }
  ports.write({ busy: null, outcome });
  ports.onSettled?.();
  return true;
}

/**
 * The pair bound to React: one state for both families, so a proposal in
 * flight disables the draft pair too — an external mutation is asked for
 * one at a time — and `onSettled` (the cockpit's panel refresh) is called
 * after every answer so the pending lists show what the Cloud Core now holds.
 */
export function useApprovalPair(
  client: ApprovalClient,
  onSettled?: () => void,
): { drafts: ApprovalPairProps; proposals: ApprovalPairProps } {
  const [state, setState] = useState<ApprovalPairState>(APPROVAL_PAIR_IDLE);
  const latest = useRef<ApprovalPairState>(APPROVAL_PAIR_IDLE);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);

  const ports = useMemo<ApprovalPorts>(
    () => ({
      client,
      read: () => latest.current,
      write: (next) => {
        latest.current = next;
        setState(next);
      },
      onSettled: () => settled.current?.(),
    }),
    [client],
  );

  const run = useCallback(
    (action: ApprovalAction, id: string) => {
      void runApproval(ports, action, id);
    },
    [ports],
  );

  return useMemo(
    () => ({
      drafts: {
        busy: state.busy,
        outcome: state.outcome,
        onConfirm: (id: string) => run("confirm_draft", id),
        onDiscard: (id: string) => run("discard_draft", id),
      },
      proposals: {
        busy: state.busy,
        outcome: state.outcome,
        onConfirm: (id: string) => run("confirm_proposal", id),
        onDiscard: (id: string) => run("discard_proposal", id),
      },
    }),
    [state, run],
  );
}
