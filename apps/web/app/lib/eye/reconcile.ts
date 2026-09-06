/**
 * The one decision `EyeControl` makes on its own: whether a bus `eye.disabled`
 * means this device's local perception loop must stop (M18 spec §2:
 * `Gözünü kapat` / `Kamerayı kapat` / `Beni izleme` stop perception).
 *
 * Pure and separate so it can be asserted in Node alongside `PerceptionSession`
 * (see `tests/eye/reconcile.test.ts`), without a React effect runner.
 *
 * The rule is deliberately one-directional: the Cloud Core saying *disabled*
 * stops the camera; the Cloud Core saying *active* never starts it. Starting
 * the camera is an owner action on this device (a `getUserMedia` grant), and
 * another device's `eye.active` is not that.
 */

import type { EyeView } from "../uistate/ambient";

export function shouldStopLocalPerception(eye: Pick<EyeView, "status">, running: boolean): boolean {
  return running && eye.status === "disabled";
}
