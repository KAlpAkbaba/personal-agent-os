/**
 * The one decision `EyeControl` makes on its own: whether a bus `eye.disabled`
 * means this device's local perception loop must stop (M18 spec §2:
 * `Gözünü kapat` / `Kamerayı kapat` / `Beni izleme` stop perception).
 *
 * Pure and separate so it can be asserted in Node alongside `PerceptionSession`
 * (see `tests/eye/reconcile.test.tsx`), without a React effect runner.
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
 *
 * ## The bus event is a DATED request, not a command
 *
 * The page polls `/v1/ui/state`; what it holds is the newest event it has
 * SEEN, which can be older than this device's own newest transition. In the
 * owner's run of 2026-09-06 (session 9df439af) a `eye.disabled` from the
 * previous "Gözünü kapat" was still the page's picture when the next
 * "Gözünü aç" started its loop, and the old rule (`running && disabled`)
 * stopped the new camera every time. So the decision now takes the event's
 * date and the store's own facts, and stops ONLY when all of these hold:
 *
 * - the bus says `disabled`, and the claim is dated (`ageMs` known) and not
 *   `expired` — an undated or decayed claim never stops anything;
 * - the store is `ACTIVE` — never `ENABLING` or `DISABLING`, where a
 *   transition in flight owns the state;
 * - the event (`now - ageMs`) is strictly newer than the moment the current
 *   ACTIVE was committed;
 * - and no durable enable of the current generation was acknowledged after
 *   the event (the Cloud Core learned of THIS camera later than it published
 *   that disable, so the disable is not about this camera).
 *
 * `ageMs` is the client's clock minus the event's server timestamp
 * (`uistate/contract.ts`), so `now - ageMs` recovers the server's date and
 * the comparison is server-date against this device's clock. A clock skew of
 * several seconds between them can make a real other-device disable look
 * older than this enable (declined here; the loop still stops on its own
 * next 409) or — only for a skew larger than the gap between the owner's
 * two commands — an old one look newer. `PerceptionSession`'s 409
 * self-correction is the safety mechanism in both directions; this rule is
 * the fast path.
 */

import type { EyeView } from "../uistate/ambient";
import type { EyeState } from "./store";

/** What the store knows about its own state, for the decision. */
export type LocalEyeFacts = {
  state: EyeState;
  /** When the current ACTIVE was committed (the store's clock); `null` outside ACTIVE. */
  activeSince: number | null;
  /** When the current generation's durable enable was acknowledged; `null` if it was not. */
  durableEnabledAt: number | null;
};

export function shouldStopLocalPerception(
  eye: Pick<EyeView, "status" | "ageMs" | "expired">,
  local: LocalEyeFacts,
  now: number,
): boolean {
  if (eye.status !== "disabled" || eye.expired || eye.ageMs === null) return false;
  if (local.state !== "ACTIVE" || local.activeSince === null) return false;
  const eventAt = now - eye.ageMs;
  if (eventAt <= local.activeSince) return false;
  if (local.durableEnabledAt !== null && eventAt <= local.durableEnabledAt) return false;
  return true;
}
