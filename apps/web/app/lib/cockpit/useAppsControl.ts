"use client";

/**
 * The Uygulamalar chips' one piece of state, and the one rule it enforces:
 * one call at a time (M23 spec §6).
 *
 * `runAppAction` is the whole of it, and it is pure over its ports so a
 * test can hand it a client double and prove that a press makes exactly one
 * call with the project's id, that a second press while the first is in
 * flight makes none, and that the answer — the receipt's state, port and
 * counts, or the refusal in the owner's words — is what the panel is then
 * given. The hook below only binds those ports to React state.
 *
 * What the outcome says is bounded by what the receipt said. A 2xx whose
 * receipt names `running` and a port is "Çalışıyor · 127.0.0.1:8123"; a 2xx
 * with no state is "the request was delivered; the receipt named no
 * result" — never "çalışıyor", because nothing said so.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { appAddress, appTestsPhrase } from "../uistate/apps";
import { APP_STATE_LABEL, isAppProjectState } from "../uistate/contract";
import {
  APPS_CONTROL_IDLE,
  type AppAction,
  type AppActionOutcome,
  type AppActionReceipt,
  type AppsClient,
  type AppsControlProps,
  type AppsControlState,
  appActionErrorText,
} from "./apps";

/** The sentence for a 2xx whose receipt named no state: what happened, and what was not said. */
export const APP_OUTCOME_NO_STATE_TR: Record<AppAction, string> = {
  run: "Çalıştırma isteği iletildi; makbuz durumu bildirmedi.",
  stop: "Durdurma isteği iletildi; makbuz durumu bildirmedi.",
  test: "Test isteği iletildi; makbuz sonucu bildirmedi.",
};

/** The first letter up, the Turkish way (`i` → `İ`), for a state word opening a sentence. */
function sentence(word: string): string {
  return word.charAt(0).toLocaleUpperCase("tr-TR") + word.slice(1);
}

/**
 * The outcome line from a 2xx receipt: the project's state in the spec's
 * words, the address when a port was named, the counts when they were, then
 * the receipt's own sentence. A state this build cannot read is printed as
 * the token — still a published fact — and never as one of the six.
 */
export function appOutcomeText(action: AppAction, receipt: AppActionReceipt): string {
  const parts: string[] = [];
  if (receipt.state === null) parts.push(APP_OUTCOME_NO_STATE_TR[action]);
  else if (isAppProjectState(receipt.state)) parts.push(sentence(APP_STATE_LABEL[receipt.state]));
  else parts.push(`durum: ${receipt.state}`);
  const address = appAddress(receipt.port);
  if (address) parts.push(address);
  const tests = appTestsPhrase(receipt.tests);
  if (tests) parts.push(`testler: ${tests}`);
  if (receipt.summary) parts.push(receipt.summary);
  return parts.join(" · ");
}

export type AppsControlPorts = {
  client: AppsClient;
  /** The current state; the one-at-a-time gate reads `busy` from here. */
  read: () => AppsControlState;
  write: (state: AppsControlState) => void;
  /** Called after every settled call, success or refusal, so the list is reloaded. */
  onSettled?: () => void;
  now?: () => number;
};

function call(client: AppsClient, action: AppAction, id: string): Promise<AppActionReceipt> {
  switch (action) {
    case "run":
      return client.run(id);
    case "stop":
      return client.stop(id);
    case "test":
      return client.test(id);
  }
}

/**
 * Run one action through the client. Returns `false`, having called
 * nothing, when a call is already in flight; `true` once the call settled
 * and its outcome was written.
 */
export async function runAppAction(ports: AppsControlPorts, action: AppAction, id: string): Promise<boolean> {
  const before = ports.read();
  if (before.busy !== null) return false;
  const now = ports.now ?? Date.now;
  ports.write({ busy: { action, id }, outcome: before.outcome });
  let outcome: AppActionOutcome;
  try {
    const receipt = await call(ports.client, action, id);
    outcome = { action, id, ok: true, text: appOutcomeText(action, receipt), at: now() };
  } catch (err) {
    outcome = { action, id, ok: false, text: appActionErrorText(err), at: now() };
  }
  ports.write({ busy: null, outcome });
  ports.onSettled?.();
  return true;
}

/**
 * The chips bound to React: one state for the whole panel, so a call in
 * flight disables every other project's chips too — the device is asked
 * one thing at a time — and `onSettled` (the cockpit's panel refresh) is
 * called after every answer so the list shows what the Cloud Core now holds.
 */
export function useAppsControl(client: AppsClient, onSettled?: () => void): AppsControlProps {
  const [state, setState] = useState<AppsControlState>(APPS_CONTROL_IDLE);
  const latest = useRef<AppsControlState>(APPS_CONTROL_IDLE);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);

  const ports = useMemo<AppsControlPorts>(
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
    (action: AppAction, id: string) => {
      void runAppAction(ports, action, id);
    },
    [ports],
  );

  return useMemo(
    () => ({
      busy: state.busy,
      outcome: state.outcome,
      onRun: (id: string) => run("run", id),
      onStop: (id: string) => run("stop", id),
      onTest: (id: string) => run("test", id),
    }),
    [state, run],
  );
}
