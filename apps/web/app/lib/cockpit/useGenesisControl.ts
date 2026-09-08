"use client";

/**
 * The Yeni Yetenek chips' one piece of state, and the one rule it enforces:
 * one call at a time (M24 spec §8).
 *
 * `runGenesisAction` is the whole of it, and it is pure over its ports so a
 * test can hand it a client double and prove that a press makes exactly one
 * call with the run's id, that a second press while the first is in flight
 * makes none, and that the answer — the receipt's state, or the refusal in
 * the owner's words — is what the panel is then given. The hook below only
 * binds those ports to React state.
 *
 * What the outcome says is bounded by what the receipt said. A 2xx whose
 * receipt names `rolling_out` is "Yayına alınıyor"; a 2xx with no state is
 * "the request was delivered; the receipt named no result" — never
 * "onaylandı", because nothing said so, and never "kullanılabilir".
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { GENESIS_STATE_LABEL, isGenesisRunState } from "../uistate/contract";
import { genesisStatePhrase } from "../uistate/genesis";
import {
  GENESIS_CONTROL_IDLE,
  type GenesisAction,
  type GenesisActionOutcome,
  type GenesisActionReceipt,
  type GenesisClient,
  type GenesisControlProps,
  type GenesisControlState,
  genesisActionErrorText,
} from "./genesis";

/** The sentence for a 2xx whose receipt named no state: what happened, and what was not said. */
export const GENESIS_OUTCOME_NO_STATE_TR: Record<GenesisAction, string> = {
  approve: "Onay isteği iletildi; makbuz durumu bildirmedi.",
  cancel: "Vazgeçme isteği iletildi; makbuz durumu bildirmedi.",
};

/** The first letter up, the Turkish way (`i` → `İ`), for a state word opening a sentence. */
function sentence(word: string): string {
  return word.charAt(0).toLocaleUpperCase("tr-TR") + word.slice(1);
}

/**
 * The outcome line from a 2xx receipt: the run's state in the spec's words
 * (with its error class beside `failed`), then the receipt's own sentence.
 * A state this build cannot read is printed as the token — still a
 * published fact — and never as one of the thirteen.
 */
export function genesisOutcomeText(action: GenesisAction, receipt: GenesisActionReceipt): string {
  const parts: string[] = [];
  if (receipt.state === null) parts.push(GENESIS_OUTCOME_NO_STATE_TR[action]);
  else if (isGenesisRunState(receipt.state)) {
    parts.push(sentence(genesisStatePhrase({ state: receipt.state, errorClass: receipt.errorClass }) ?? GENESIS_STATE_LABEL[receipt.state]));
  } else parts.push(`durum: ${receipt.state}`);
  if (receipt.summary) parts.push(receipt.summary);
  return parts.join(" · ");
}

export type GenesisControlPorts = {
  client: GenesisClient;
  /** The current state; the one-at-a-time gate reads `busy` from here. */
  read: () => GenesisControlState;
  write: (state: GenesisControlState) => void;
  /** Called after every settled call, success or refusal, so the list is reloaded. */
  onSettled?: () => void;
  now?: () => number;
};

function call(client: GenesisClient, action: GenesisAction, id: string): Promise<GenesisActionReceipt> {
  switch (action) {
    case "approve":
      return client.approve(id);
    case "cancel":
      return client.cancel(id);
  }
}

/**
 * Run one action through the client. Returns `false`, having called
 * nothing, when a call is already in flight; `true` once the call settled
 * and its outcome was written.
 */
export async function runGenesisAction(ports: GenesisControlPorts, action: GenesisAction, id: string): Promise<boolean> {
  const before = ports.read();
  if (before.busy !== null) return false;
  const now = ports.now ?? Date.now;
  ports.write({ busy: { action, id }, outcome: before.outcome });
  let outcome: GenesisActionOutcome;
  try {
    const receipt = await call(ports.client, action, id);
    outcome = { action, id, ok: true, text: genesisOutcomeText(action, receipt), at: now() };
  } catch (err) {
    outcome = { action, id, ok: false, text: genesisActionErrorText(err), at: now() };
  }
  ports.write({ busy: null, outcome });
  ports.onSettled?.();
  return true;
}

/**
 * The chips bound to React: one state for the whole panel, so a call in
 * flight disables every other run's chips too — the Cloud Core is asked
 * one thing at a time — and `onSettled` (the cockpit's panel refresh) is
 * called after every answer so the list shows what the Cloud Core now holds.
 */
export function useGenesisControl(client: GenesisClient, onSettled?: () => void): GenesisControlProps {
  const [state, setState] = useState<GenesisControlState>(GENESIS_CONTROL_IDLE);
  const latest = useRef<GenesisControlState>(GENESIS_CONTROL_IDLE);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);

  const ports = useMemo<GenesisControlPorts>(
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
    (action: GenesisAction, id: string) => {
      void runGenesisAction(ports, action, id);
    },
    [ports],
  );

  return useMemo(
    () => ({
      busy: state.busy,
      outcome: state.outcome,
      onApprove: (id: string) => run("approve", id),
      onCancel: (id: string) => run("cancel", id),
    }),
    [state, run],
  );
}
