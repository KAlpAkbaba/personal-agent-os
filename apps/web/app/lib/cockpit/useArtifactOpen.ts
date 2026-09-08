"use client";

/**
 * "Aç"'s one piece of state, and the one rule it enforces: one open at a
 * time (M22 spec §4).
 *
 * `runArtifactOpen` is the whole of it, and it is pure over its ports so a
 * test can hand it a client double and prove that a press makes exactly one
 * call with the artifact's id, that a second press while the first is in
 * flight makes none, and that the answer — the receipt's state and the
 * window the companion observed, or the refusal in the owner's words — is
 * what the panel is then given. The hook below only binds those ports to
 * React state.
 *
 * What the outcome says is bounded by what the receipt said. A 2xx whose
 * receipt names `opened` and a window is "Açıldı · pencere: …"; a 2xx with
 * no state is "the request was delivered; the receipt named no result" —
 * never "açıldı", because nothing said so.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ARTIFACT_OPEN_IDLE,
  type ArtifactClient,
  type ArtifactOpenOutcome,
  type ArtifactOpenProps,
  type ArtifactOpenReceipt,
  type ArtifactOpenState,
  artifactOpenErrorText,
} from "./artifacts";

/** The sentence for a 2xx whose receipt named no state: what happened, and what was not said. */
export const OPEN_OUTCOME_NO_STATE_TR = "Açma isteği iletildi; makbuz sonucu bildirmedi.";

/**
 * The states an open receipt may name, in the owner's words. `fetched` is
 * said as fetched and no more: the file reached the device, and whether it
 * opened is a separate observation the receipt makes separately.
 */
export const OPEN_STATE_TR: Record<string, string> = {
  opened: "Açıldı",
  fetched: "Cihaza getirildi; açıldığı bildirilmedi",
  queued: "Sıraya alındı; cihaz bekleniyor",
  dispatched: "Cihaza iletildi; sonucu bekleniyor",
};

/** The outcome line from a 2xx receipt: the state in the owner's words, the window observed, then the receipt's own sentence. */
export function openOutcomeText(receipt: ArtifactOpenReceipt): string {
  const parts: string[] = [];
  if (receipt.state !== null) parts.push(OPEN_STATE_TR[receipt.state] ?? `durum: ${receipt.state}`);
  else parts.push(OPEN_OUTCOME_NO_STATE_TR);
  if (receipt.windowTitle) parts.push(`pencere: ${receipt.windowTitle}`);
  if (receipt.summary) parts.push(receipt.summary);
  return parts.join(" · ");
}

export type ArtifactOpenPorts = {
  client: ArtifactClient;
  /** The current state; the one-at-a-time gate reads `busy` from here. */
  read: () => ArtifactOpenState;
  write: (state: ArtifactOpenState) => void;
  /** Called after every settled call, success or refusal, so the list is reloaded. */
  onSettled?: () => void;
  now?: () => number;
};

/**
 * Run one open through the client. Returns `false`, having called nothing,
 * when an open is already in flight; `true` once the call settled and its
 * outcome was written.
 */
export async function runArtifactOpen(ports: ArtifactOpenPorts, artifactId: string): Promise<boolean> {
  const before = ports.read();
  if (before.busy !== null) return false;
  const now = ports.now ?? Date.now;
  ports.write({ busy: artifactId, outcome: before.outcome });
  let outcome: ArtifactOpenOutcome;
  try {
    const receipt = await ports.client.open(artifactId);
    outcome = { artifactId, ok: true, text: openOutcomeText(receipt), at: now() };
  } catch (err) {
    outcome = { artifactId, ok: false, text: artifactOpenErrorText(err), at: now() };
  }
  ports.write({ busy: null, outcome });
  ports.onSettled?.();
  return true;
}

/**
 * "Aç" bound to React: one state for the whole panel, so an open in flight
 * disables every other artifact's chip too, and `onSettled` (the cockpit's
 * panel refresh) is called after every answer so the list shows what the
 * Cloud Core now holds.
 */
export function useArtifactOpen(client: ArtifactClient, onSettled?: () => void): ArtifactOpenProps {
  const [state, setState] = useState<ArtifactOpenState>(ARTIFACT_OPEN_IDLE);
  const latest = useRef<ArtifactOpenState>(ARTIFACT_OPEN_IDLE);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);

  const ports = useMemo<ArtifactOpenPorts>(
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

  const onOpen = useCallback(
    (artifactId: string) => {
      void runArtifactOpen(ports, artifactId);
    },
    [ports],
  );

  return useMemo(() => ({ busy: state.busy, outcome: state.outcome, onOpen }), [state, onOpen]);
}
