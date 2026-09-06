/**
 * The voice rig's `LocalActionPort` for the eye (M18_ACTION_CONTRACT.md §5.1,
 * §7.2): when the provider calls `eye.enable` / `eye.disable`, THIS device
 * runs its own camera capability first, through the tab's one `EyeStore`,
 * and the controller relays what was observed as `arguments.observed_after`.
 * The Cloud Core then builds a receipt from the real terminal state and the
 * model reads its `speech` — never "öyle olmuş gibi düşün" again.
 *
 * The relayed object is exactly §5.1's shape plus the store's `changed` flag:
 *
 * ```
 * {local: {state, running, camera_label, error_class, observed_at, changed}}
 * ```
 *
 * `changed` travels because the durable POST is made by THIS store on the
 * voice path too (durable-first on disable, camera-first on enable), so by the
 * time the Cloud Core's handler runs its own idempotent write it finds the
 * flag already flipped and returns "unchanged". The server therefore decides
 * `verified` vs `already` from its read-back AND `local.changed`
 * (`app/voice/realtime_sessions/actions.py`): a camera that really opened or
 * closed in this command is `verified`; one that was already so is `already`.
 * The server never trusts `changed` alone - the read-back must agree. The 8 s
 * bound lives in the store (`LOCAL_EYE_TIMEOUT_MS`), so `run` settles within
 * it by construction and never rejects.
 */

import type { LocalActionPort } from "../voice/ports";
import type { EyeStore, LocalEyeResult } from "./store";

export const EYE_ENABLE_TOOL = "eye.enable";
export const EYE_DISABLE_TOOL = "eye.disable";

/** `EyeActionRequest.reason` is `max_length=200` server-side (`app/presence/routes.py`). */
export const EYE_REASON_MAX_CHARS = 200;

/** The durable reason for a voice-commanded eye action: `voice:<utterance>`, bounded. */
export function voiceReason(args: Record<string, unknown>): string {
  const utterance = typeof args.utterance === "string" ? args.utterance.trim() : "";
  return `voice:${utterance}`.slice(0, EYE_REASON_MAX_CHARS);
}

/** §5.1's `observed_after`, from what the store answered. */
export function observedAfter(result: LocalEyeResult): Record<string, unknown> {
  return {
    local: {
      state: result.state,
      running: result.running,
      camera_label: result.camera_label,
      error_class: result.error_class,
      observed_at: result.observed_at,
      changed: result.changed,
    },
  };
}

/**
 * The port over a store getter (not a store) so that the rig can be built
 * before the eye store is, and so that a test can swap the store per case.
 */
export function eyeLocalActions(store: () => EyeStore): LocalActionPort {
  return {
    async run(name, args) {
      if (name === EYE_ENABLE_TOOL) return observedAfter(await store().enable(voiceReason(args)));
      if (name === EYE_DISABLE_TOOL) return observedAfter(await store().disable(voiceReason(args)));
      return null;
    },
  };
}
