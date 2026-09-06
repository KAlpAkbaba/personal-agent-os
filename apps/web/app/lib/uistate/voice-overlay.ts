/**
 * From the voice store's snapshot to the Core's `VoiceOverlay` (ADR-0061 §4).
 *
 * Pure: the page hands in the controller snapshot and the two levels it
 * sampled; this only reshapes them and derives the caption. Kept apart from
 * `visual.ts` so that file stays free of any knowledge of the controller's
 * snapshot shape.
 */

import type { ControllerSnapshot } from "../voice/controller";
import { runningToolLabel, speechCaption } from "../voice/labels";
import type { VoiceLevels } from "../voice/useVoiceSession";
import type { VoiceOverlay } from "./visual";

export function voiceOverlayFrom(controller: ControllerSnapshot, levels: VoiceLevels): VoiceOverlay {
  return {
    state: controller.state,
    micLevel: levels.micLevel,
    outputLevel: levels.outputLevel,
    caption: speechCaption(controller),
    toolLabel: runningToolLabel(controller),
    lastError: controller.lastError,
  };
}
