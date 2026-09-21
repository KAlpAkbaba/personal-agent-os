"use client";

/**
 * The Core's gesture cell (ADR-0198, Stage 1), wired to the tab's one `GestureController`.
 * Owns nothing itself: `useGestureController` is a subscription to a tab-wide singleton
 * (the same shape `EyeControl`/`VoiceControl` already use), so mounting, unmounting or
 * mounting this twice never starts a second tracker. Every pixel is `GestureControlView`,
 * tested directly.
 */

import { useGestureController } from "../lib/gesture/useGestureController";
import GestureControlView from "./GestureControlView";

export default function GestureControl() {
  const { gesture, setEnabled } = useGestureController();
  return <GestureControlView gesture={gesture} onToggle={setEnabled} />;
}
