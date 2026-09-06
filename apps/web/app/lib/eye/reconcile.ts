/**
 * The one decision `EyeControl` makes on its own: whether a bus `eye.disabled`
 * means this device's local perception loop must stop (M18 spec §2:
 * `Gözünü kapat` / `Kamerayı kapat` / `Beni izleme` stop perception).
 *
 * Pure and separate so it can be asserted in Node alongside `PerceptionSession`
 * (see `tests/eye/reconcile.test.ts`), without a React effect runner.
 *
 * The rule is deliberately one-directional: the Cloud Core saying *disabled*
 * stops the camera; the BUS saying *active* never starts it. Starting the
 * camera is an owner action on this device (a `getUserMedia` grant), and
 * another device's `eye.active` is not that — it may have been enabled from
 * a phone whose camera is the one that opened.
 *
 * Since M18_ACTION_CONTRACT.md §5.1 there IS a second way the camera starts
 * without the button: the voice tool `eye.enable`, executed locally by the
 * voice rig's `LocalActionPort` through `EyeStore.enable` BEFORE the tool
 * call is relayed. That is still this device's own command — the owner spoke
 * to the session this tab holds — not a reaction to the bus; the bus rule
 * here is unchanged, and a bus `eye.active` produced by that very enable
 * arrives at a camera that is already open.
 */

import type { EyeView } from "../uistate/ambient";

export function shouldStopLocalPerception(eye: Pick<EyeView, "status">, running: boolean): boolean {
  return running && eye.status === "disabled";
}
