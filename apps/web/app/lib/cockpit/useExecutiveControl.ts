"use client";

/**
 * The Görevler chips' one piece of state, and the one rule it enforces:
 * one call at a time (M26 spec §6).
 *
 * `runExecutiveAction` is the whole of it, and it is pure over its ports so
 * a test can hand it a client double and prove that a press makes exactly
 * one call with the run's id, that a second press while the first is in
 * flight makes none, and that the answer — the receipt's state, or the
 * refusal in the owner's words — is what the panel is then given. The hook
 * below only binds those ports to React state.
 *
 * What the outcome says is bounded by what the receipt said. A 2xx whose
 * receipt names `paused` is "Duraklatıldı"; a 2xx with no state is "the
 * request was delivered; the receipt named no result" — never
 * "duraklatıldı", because nothing said so, and never "tamamlandı", which
 * only a `completed` may ever produce.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EXECUTIVE_RUN_STATE_LABEL, isExecutiveRunState } from "../uistate/contract";
import {
  EXECUTIVE_CONTROL_IDLE,
  type ExecutiveActionOutcome,
  type ExecutiveActionReceipt,
  type ExecutiveChipAction,
  type ExecutiveClient,
  type ExecutiveControlProps,
  type ExecutiveControlState,
  executiveActionErrorText,
} from "./executive";

/** The sentence for a 2xx whose receipt named no state: what happened, and what was not said. */
export const EXECUTIVE_OUTCOME_NO_STATE_TR: Record<ExecutiveChipAction, string> = {
  pause: "Duraklatma isteği iletildi; makbuz durumu bildirmedi.",
  resume: "Sürdürme isteği iletildi; makbuz durumu bildirmedi.",
  cancel: "İptal isteği iletildi; makbuz durumu bildirmedi.",
  approve: "Onay iletildi; makbuz durumu bildirmedi.",
};

/** The first letter up, the Turkish way (`i` → `İ`), for a state word opening a sentence. */
function sentence(word: string): string {
  return word.charAt(0).toLocaleUpperCase("tr-TR") + word.slice(1);
}

/**
 * The outcome line from a 2xx receipt: the run's state in the spec's words,
 * then the receipt's own sentence. A state this build cannot read is
 * printed as the token — still a published fact — and never as one of the
 * seven; a receipt with no state says exactly that.
 */
export function executiveOutcomeText(action: ExecutiveChipAction, receipt: ExecutiveActionReceipt): string {
  const parts: string[] = [];
  if (receipt.state === null) parts.push(EXECUTIVE_OUTCOME_NO_STATE_TR[action]);
  else if (isExecutiveRunState(receipt.state)) parts.push(sentence(EXECUTIVE_RUN_STATE_LABEL[receipt.state]));
  else parts.push(`durum: ${receipt.state}`);
  if (receipt.summary) parts.push(receipt.summary);
  return parts.join(" · ");
}

export type ExecutiveControlPorts = {
  client: ExecutiveClient;
  /** The current state; the one-at-a-time gate reads `busy` from here. */
  read: () => ExecutiveControlState;
  write: (state: ExecutiveControlState) => void;
  /** Called after every settled call, success or refusal, so the list is reloaded. */
  onSettled?: () => void;
  now?: () => number;
};

function call(client: ExecutiveClient, action: ExecutiveChipAction, id: string): Promise<ExecutiveActionReceipt> {
  switch (action) {
    case "pause":
      return client.pause(id);
    case "resume":
      return client.resume(id);
    case "cancel":
      return client.cancel(id);
    case "approve":
      return client.approve(id);
  }
}

/**
 * Run one action through the client. Returns `false`, having called
 * nothing, when a call is already in flight; `true` once the call settled
 * and its outcome was written.
 */
export async function runExecutiveAction(
  ports: ExecutiveControlPorts,
  action: ExecutiveChipAction,
  id: string,
): Promise<boolean> {
  const before = ports.read();
  if (before.busy !== null) return false;
  const now = ports.now ?? Date.now;
  ports.write({ busy: { action, id }, outcome: before.outcome });
  let outcome: ExecutiveActionOutcome;
  try {
    const receipt = await call(ports.client, action, id);
    outcome = { action, id, ok: true, text: executiveOutcomeText(action, receipt), at: now() };
  } catch (err) {
    outcome = { action, id, ok: false, text: executiveActionErrorText(err), at: now() };
  }
  ports.write({ busy: null, outcome });
  ports.onSettled?.();
  return true;
}

/**
 * The chips bound to React: one state for the whole panel, so a call in
 * flight disables every other run's chips too — the Cloud Core is asked one
 * thing at a time — and `onSettled` (the cockpit's panel refresh) is called
 * after every answer so the list shows what the Cloud Core now holds rather
 * than what this page assumed a click produced.
 */
export function useExecutiveControl(client: ExecutiveClient, onSettled?: () => void): ExecutiveControlProps {
  const [state, setState] = useState<ExecutiveControlState>(EXECUTIVE_CONTROL_IDLE);
  const latest = useRef<ExecutiveControlState>(EXECUTIVE_CONTROL_IDLE);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);

  const ports = useMemo<ExecutiveControlPorts>(
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
    (action: ExecutiveChipAction, id: string) => {
      void runExecutiveAction(ports, action, id);
    },
    [ports],
  );

  return useMemo(
    () => ({
      busy: state.busy,
      outcome: state.outcome,
      onPause: (id: string) => run("pause", id),
      onResume: (id: string) => run("resume", id),
      onCancel: (id: string) => run("cancel", id),
      onApprove: (id: string) => run("approve", id),
    }),
    [state, run],
  );
}
