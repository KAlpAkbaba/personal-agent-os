"use client";

/**
 * The Core's gesture cell (ADR-0198 Stage 1, ADR-0199 Stage 2), wired to the tab's one
 * `GestureController` and one `PointerStreamClient`. Owns nothing itself: `useGestureController`
 * is a subscription to tab-wide singletons (the same shape `EyeControl`/`VoiceControl` already
 * use), so mounting, unmounting or mounting this twice never starts a second tracker or a
 * second pointer client. Every pixel is `GestureControlView`, tested directly.
 */

import { useGestureController } from "../lib/gesture/useGestureController";
import GestureControlView from "./GestureControlView";

export default function GestureControl() {
  const { gesture, pointer, setEnabled, setPointerGain } = useGestureController();
  return <GestureControlView gesture={gesture} pointer={pointer} onToggle={setEnabled} onGainChange={setPointerGain} />;
}
