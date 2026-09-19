/**
 * One voice channel at a time.
 *
 * Security review 2026-09-19 (MEDIUM, ADR-0173): the "Yerel mod" switch only HID the paid
 * path's buttons. An owner with a live paid WebRTC session who flipped the switch on kept
 * that session - its microphone and its model-driven tool calls - running behind a second,
 * local-router session listening to the same room. One spoken command could then be executed
 * twice, under two call ids (a mission started twice, "Gönder." heard by both). Hiding a
 * control is not closing a session.
 *
 * So the switch is a hand-over, in both directions: whichever channel is being left is
 * ended BEFORE the preference changes, and only then does the other one become available.
 */

export interface VoiceChannels {
  /** Persist the owner's choice (per browser). */
  setLocalVoice: (on: boolean) => void;
  /** End the local-router session, if any. Safe to call when none is open. */
  stopLocal: () => Promise<void> | void;
  /** End the paid realtime session, if any. Safe to call when none is open. */
  disconnectPaid: () => Promise<void> | void;
}

export async function toggleLocalVoice(on: boolean, channels: VoiceChannels): Promise<void> {
  if (on) {
    await channels.disconnectPaid();
  } else {
    await channels.stopLocal();
  }
  channels.setLocalVoice(on);
}
