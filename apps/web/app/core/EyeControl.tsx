"use client";

/**
 * The Active Eye's owner-facing control (M18_HOLOGRAPHIC_CORE_SPEC.md §2).
 *
 * Deliberately its own component, separate from `AmbientBand`. `AmbientBand`
 * is a pure consumer of the UI-state bus and its own tests assert it renders
 * no `<button>`, no `<form>`, no `<input>` at all — that is the Core's
 * read-only half of contract v2, and this feature must not compromise it.
 * Approving/enabling something is an owner action taken on the surface that
 * owns it (the cockpit panels follow the same rule for goals and SHADOW_READY
 * candidates), so the Active Eye's start/stop control lives here instead.
 *
 * This file binds `useActivePerception` — a subscription to the tab's one
 * `EyeStore`, which owns the camera and the network — and nothing else; every
 * pixel on screen is `EyeControlView`, a pure component tested directly. The
 * split mirrors `CoreView`/`CoreFallback2D` in this same directory:
 * capability/runtime concerns in one file, rendering in another. Mounting or
 * unmounting this component never opens or releases a camera (ADR-0061-style
 * one-owner-per-tab; M18_ACTION_CONTRACT.md §7.1).
 */

import { useEffect } from "react";

import { notifyEyeStreamStopped } from "../lib/eye/client";
import { useActivePerception } from "../lib/eye/useActivePerception";
import type { EyeView } from "../lib/uistate/ambient";
import EyeControlView from "./EyeControlView";

export type EyeControlProps = {
  /** The server-side truth, computed by the page exactly like `AmbientBand`'s. */
  eye: EyeView;
};

export default function EyeControl({ eye }: EyeControlProps) {
  const { status, permission, busy, error, lastActionTrace, start, stop, stopLocalIfStale } = useActivePerception();

  // "Gözünü kapat" spoken elsewhere, another device's owner action, or the
  // eye endpoint's own idempotent default all reach this the same way: the
  // Cloud Core is already told (`eye.disabled` is live), so this device's own
  // camera has no honest reason to keep running. `PerceptionSession` would
  // reach the same conclusion on its own next tick anyway (a 409 stops it);
  // this only makes the local camera light go out sooner, bounded by how
  // often the page polls `/v1/ui/state` rather than by the sampling interval.
  //
  // What is offered is the bus view WITH its date (`ageMs`, `expired`), and
  // the store decides (`shouldStopLocalPerception`): the page's picture can
  // be OLDER than the store's own newest transition — polling lags the
  // owner's next command — and an old `eye.disabled` must never cancel a
  // newer enable. That is exactly what happened on 2026-09-06 (session
  // 9df439af) when this effect also re-ran on `status.running` and obeyed
  // the previous command's `eye.disabled` the moment the new loop started.
  // The effect therefore re-runs on the bus view alone; the store's own
  // state changes are not a reason to re-ask a question the bus already
  // answered.
  const { status: eyeStatus, ageMs: eyeAgeMs, expired: eyeExpired } = eye;
  useEffect(() => {
    stopLocalIfStale({ status: eyeStatus, ageMs: eyeAgeMs, expired: eyeExpired });
  }, [eyeStatus, eyeAgeMs, eyeExpired, stopLocalIfStale]);

  // B48 (req 301): the browser releases the camera with the document; the Cloud Core would
  // otherwise only notice the silence. Registered only while this tab's camera runs.
  const running = status.running;
  useEffect(() => {
    if (!running) return undefined;
    const onPageHide = () => notifyEyeStreamStopped("tab_closed");
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, [running]);

  return (
    <EyeControlView
      eye={eye}
      status={status}
      permission={permission}
      busy={busy}
      error={error}
      lastActionTrace={lastActionTrace}
      onStart={() => void start()}
      onStop={() => void stop()}
    />
  );
}
