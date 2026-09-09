"use client";

/**
 * The Yaratıcı chips' one piece of state, and the one rule it enforces: one
 * call at a time (M27 spec §6).
 *
 * `runCreativeAction` is the whole of it, and it is pure over its ports so a
 * test can hand it a client double and prove that a press makes exactly one
 * call with the run's id, that a second press while the first is in flight
 * makes none, and that the answer — the receipt's step, or the refusal in
 * the owner's words — is what the panel is then given. The hook below only
 * binds those ports to React state.
 *
 * What the outcome says is bounded by what the receipt said. A 2xx whose
 * receipt names `mismatch` is "Uyuşmazlık — ölçü tutmadı"; a 2xx with no
 * state is "the request was delivered; the receipt named no result" — never
 * "dışa aktarıldı" and never "doğrulandı", because nothing said so, and a
 * picture is verified by a comparison that matched or not at all (ADR-0093
 * decision 4).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CREATIVE_STATE_LABEL, isCreativeRunState, isCreativeTool } from "../uistate/contract";
import { creativeStatePhrase } from "../uistate/creative";
import {
  CREATIVE_CONTROL_IDLE,
  type CreativeAction,
  type CreativeActionOutcome,
  type CreativeActionReceipt,
  type CreativeClient,
  type CreativeControlProps,
  type CreativeControlState,
  creativeActionErrorText,
} from "./creative";

/** The sentence for a 2xx whose receipt named no step: what happened, and what was not said. */
export const CREATIVE_OUTCOME_NO_STATE_TR: Record<CreativeAction, string> = {
  export: "Dışa aktarma isteği iletildi; makbuz durum bildirmedi.",
  compare: "Karşılaştırma isteği iletildi; makbuz durum bildirmedi.",
};

/** The first letter up, the Turkish way (`i` → `İ`), for a step word opening a sentence. */
function sentence(word: string): string {
  return word.charAt(0).toLocaleUpperCase("tr-TR") + word.slice(1);
}

/**
 * The outcome line from a 2xx receipt: the run's step in the spec's words
 * (with the similarity beside `verified` and the defect beside `mismatch`),
 * then the receipt's own sentence. A step this build cannot read is printed
 * as the token — still a published fact — and never as one of the twelve.
 *
 * `tool` is read from the row only so the "kurulu değil" wording can be
 * right for an `unavailable` Photoshop; it changes nothing else.
 */
export function creativeOutcomeText(
  action: CreativeAction,
  receipt: CreativeActionReceipt,
  tool: string | null = null,
): string {
  const parts: string[] = [];
  if (receipt.state === null) parts.push(CREATIVE_OUTCOME_NO_STATE_TR[action]);
  else if (isCreativeRunState(receipt.state)) {
    const phrase = creativeStatePhrase(
      {
        state: receipt.state,
        tool: isCreativeTool(tool) ? tool : null,
        similarity: receipt.similarity,
        defectToken: receipt.defect,
      },
      receipt.defect,
    );
    parts.push(sentence(phrase ?? CREATIVE_STATE_LABEL[receipt.state]));
  } else parts.push(`durum: ${receipt.state}`);
  if (receipt.summary) parts.push(receipt.summary);
  return parts.join(" · ");
}

export type CreativeControlPorts = {
  client: CreativeClient;
  /** The current state; the one-at-a-time gate reads `busy` from here. */
  read: () => CreativeControlState;
  write: (state: CreativeControlState) => void;
  /** Called after every settled call, success or refusal, so the list is reloaded. */
  onSettled?: () => void;
  /** The application of the run the call is about, when the caller knows it — for the wording alone. */
  toolFor?: (runId: string) => string | null;
  now?: () => number;
};

function call(client: CreativeClient, action: CreativeAction, id: string): Promise<CreativeActionReceipt> {
  switch (action) {
    case "export":
      return client.export(id);
    case "compare":
      return client.compare(id);
  }
}

/**
 * Run one action through the client. Returns `false`, having called nothing,
 * when a call is already in flight; `true` once the call settled and its
 * outcome was written.
 */
export async function runCreativeAction(
  ports: CreativeControlPorts,
  action: CreativeAction,
  id: string,
): Promise<boolean> {
  const before = ports.read();
  if (before.busy !== null) return false;
  const now = ports.now ?? Date.now;
  ports.write({ busy: { action, id }, outcome: before.outcome });
  let outcome: CreativeActionOutcome;
  try {
    const receipt = await call(ports.client, action, id);
    outcome = {
      action,
      id,
      ok: true,
      text: creativeOutcomeText(action, receipt, ports.toolFor?.(id) ?? null),
      at: now(),
    };
  } catch (err) {
    outcome = { action, id, ok: false, text: creativeActionErrorText(err), at: now() };
  }
  ports.write({ busy: null, outcome });
  ports.onSettled?.();
  return true;
}

/**
 * The chips bound to React: one state for the whole panel, so a call in
 * flight disables every other run's chips too — one application is driven at
 * a time — and `onSettled` (the cockpit's panel refresh) is called after
 * every answer so the list shows what the Cloud Core now holds.
 */
export function useCreativeControl(
  client: CreativeClient,
  onSettled?: () => void,
  toolFor?: (runId: string) => string | null,
): CreativeControlProps {
  const [state, setState] = useState<CreativeControlState>(CREATIVE_CONTROL_IDLE);
  const latest = useRef<CreativeControlState>(CREATIVE_CONTROL_IDLE);
  const settled = useRef(onSettled);
  const tool = useRef(toolFor);
  useEffect(() => {
    settled.current = onSettled;
    tool.current = toolFor;
  }, [onSettled, toolFor]);

  const ports = useMemo<CreativeControlPorts>(
    () => ({
      client,
      read: () => latest.current,
      write: (next) => {
        latest.current = next;
        setState(next);
      },
      onSettled: () => settled.current?.(),
      toolFor: (id) => tool.current?.(id) ?? null,
    }),
    [client],
  );

  const run = useCallback(
    (action: CreativeAction, id: string) => {
      void runCreativeAction(ports, action, id);
    },
    [ports],
  );

  return useMemo(
    () => ({
      busy: state.busy,
      outcome: state.outcome,
      onExport: (id: string) => run("export", id),
      onCompare: (id: string) => run("compare", id),
    }),
    [state, run],
  );
}
