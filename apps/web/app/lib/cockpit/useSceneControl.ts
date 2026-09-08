"use client";

/**
 * The 3B Sahne chips' one piece of state, and the one rule it enforces:
 * one call at a time (M25 spec §6).
 *
 * `runSceneAction` is the whole of it, and it is pure over its ports so a
 * test can hand it a client double and prove that a press makes exactly one
 * call with the scene's id, that a second press while the first is in
 * flight makes none, and that the answer — the receipt's step, or the
 * refusal in the owner's words — is what the panel is then given. The hook
 * below only binds those ports to React state.
 *
 * What the outcome says is bounded by what the receipt said. A 2xx whose
 * receipt names `mismatch` is "Uyuşmazlık — Kure.Kup"; a 2xx with no state
 * is "the request was delivered; the receipt named no result" — never
 * "render alındı" and never "doğrulandı", because nothing said so, and a
 * scene is verified by a read-back that matched or not at all (ADR-0088 §4).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SCENE_STATE_LABEL, isSceneRunState, isSceneTool } from "../uistate/contract";
import { sceneStatePhrase } from "../uistate/scenes";
import {
  SCENE_CONTROL_IDLE,
  type SceneAction,
  type SceneActionOutcome,
  type SceneActionReceipt,
  type SceneClient,
  type SceneControlProps,
  type SceneControlState,
  sceneActionErrorText,
} from "./scenes";

/** The sentence for a 2xx whose receipt named no step: what happened, and what was not said. */
export const SCENE_OUTCOME_NO_STATE_TR: Record<SceneAction, string> = {
  render: "Render isteği iletildi; makbuz durum bildirmedi.",
  inspect: "Okuma isteği iletildi; makbuz durum bildirmedi.",
};

/** The first letter up, the Turkish way (`i` → `İ`), for a step word opening a sentence. */
function sentence(word: string): string {
  return word.charAt(0).toLocaleUpperCase("tr-TR") + word.slice(1);
}

/**
 * The outcome line from a 2xx receipt: the scene's step in the spec's words
 * (with the object count beside `verified` and the object beside
 * `mismatch`), then the receipt's own sentence. A step this build cannot
 * read is printed as the token — still a published fact — and never as one
 * of the eight.
 *
 * `tool` is read from the receipt only so the licence wording can be right
 * for an `unavailable` Unity; it changes nothing else.
 */
export function sceneOutcomeText(action: SceneAction, receipt: SceneActionReceipt, tool: string | null = null): string {
  const parts: string[] = [];
  if (receipt.state === null) parts.push(SCENE_OUTCOME_NO_STATE_TR[action]);
  else if (isSceneRunState(receipt.state)) {
    const phrase = sceneStatePhrase(
      { state: receipt.state, tool: isSceneTool(tool) ? tool : null, objects: receipt.objects },
      receipt.mismatch,
    );
    parts.push(sentence(phrase ?? SCENE_STATE_LABEL[receipt.state]));
  } else parts.push(`durum: ${receipt.state}`);
  if (receipt.summary) parts.push(receipt.summary);
  return parts.join(" · ");
}

export type SceneControlPorts = {
  client: SceneClient;
  /** The current state; the one-at-a-time gate reads `busy` from here. */
  read: () => SceneControlState;
  write: (state: SceneControlState) => void;
  /** Called after every settled call, success or refusal, so the list is reloaded. */
  onSettled?: () => void;
  /** The tool of the scene the call is about, when the caller knows it — for the licence wording alone. */
  toolFor?: (sceneId: string) => string | null;
  now?: () => number;
};

function call(client: SceneClient, action: SceneAction, id: string): Promise<SceneActionReceipt> {
  switch (action) {
    case "render":
      return client.render(id);
    case "inspect":
      return client.inspect(id);
  }
}

/**
 * Run one action through the client. Returns `false`, having called
 * nothing, when a call is already in flight; `true` once the call settled
 * and its outcome was written.
 */
export async function runSceneAction(ports: SceneControlPorts, action: SceneAction, id: string): Promise<boolean> {
  const before = ports.read();
  if (before.busy !== null) return false;
  const now = ports.now ?? Date.now;
  ports.write({ busy: { action, id }, outcome: before.outcome });
  let outcome: SceneActionOutcome;
  try {
    const receipt = await call(ports.client, action, id);
    outcome = { action, id, ok: true, text: sceneOutcomeText(action, receipt, ports.toolFor?.(id) ?? null), at: now() };
  } catch (err) {
    outcome = { action, id, ok: false, text: sceneActionErrorText(err), at: now() };
  }
  ports.write({ busy: null, outcome });
  ports.onSettled?.();
  return true;
}

/**
 * The chips bound to React: one state for the whole panel, so a call in
 * flight disables every other scene's chips too — one editor is started at
 * a time — and `onSettled` (the cockpit's panel refresh) is called after
 * every answer so the list shows what the Cloud Core now holds.
 */
export function useSceneControl(
  client: SceneClient,
  onSettled?: () => void,
  toolFor?: (sceneId: string) => string | null,
): SceneControlProps {
  const [state, setState] = useState<SceneControlState>(SCENE_CONTROL_IDLE);
  const latest = useRef<SceneControlState>(SCENE_CONTROL_IDLE);
  const settled = useRef(onSettled);
  const tool = useRef(toolFor);
  useEffect(() => {
    settled.current = onSettled;
    tool.current = toolFor;
  }, [onSettled, toolFor]);

  const ports = useMemo<SceneControlPorts>(
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
    (action: SceneAction, id: string) => {
      void runSceneAction(ports, action, id);
    },
    [ports],
  );

  return useMemo(
    () => ({
      busy: state.busy,
      outcome: state.outcome,
      onRender: (id: string) => run("render", id),
      onInspect: (id: string) => run("inspect", id),
    }),
    [state, run],
  );
}
