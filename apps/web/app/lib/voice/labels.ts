/**
 * Turkish surface text for the voice session (constitution: `tr-TR` first).
 *
 * Shared by `/voice` (the diagnostics view) and the Core's voice cell, so the
 * two never disagree about what a state is called. The caption helpers at the
 * bottom exist for the Core alone: while the assistant speaks, the Core shows
 * one short semantic line — the tool being narrated, or where the narration
 * cursor is — never the transcript, which stays on `/voice`.
 */

import { type ControllerSnapshot, type SpeechPhase, type VoiceUiState, describeNarrationCursor } from "./controller";
import type { EnvironmentClass, EnvironmentMode } from "./calibration";
import type { MicrophoneProfile, VoiceChoice } from "./profile";

export const VOICE_STATE_LABEL: Record<VoiceUiState, string> = {
  idle: "Hazır",
  creating: "Oturum oluşturuluyor…",
  connecting: "Bağlanıyor…",
  listening: "Dinliyor",
  speaking: "Konuşuyor",
  tool_running: "Araç çalışıyor",
  interrupted: "Kesildi",
  reconnecting: "Yeniden bağlanıyor…",
  closed: "Kapalı",
  // B20 req 221: what the owner sees INSTEAD of "Dinliyor" when the microphone is
  // gone. It names the thing to fix, because the owner is the only one who can.
  mic_lost: "Mikrofon kapandı",
  error: "Hata",
};

/**
 * ADR-0066: the speech lifecycle phase, worded for the readouts. Only the
 * phases that add something to "Konuşuyor" have words; `draining` is the one
 * the owner should be able to see — generation is over, the audio is not.
 */
export const SPEECH_PHASE_LABEL: Record<SpeechPhase, string> = {
  idle: "",
  generating: "yanıt üretiliyor, ses henüz yok",
  audible: "ses çalıyor",
  draining: "üretim bitti, kalan ses çalıyor",
  done: "",
};

/** The phase note next to the state label while speaking; null otherwise. */
export function speechPhaseNote(snapshot: Pick<ControllerSnapshot, "state" | "speech">): string | null {
  if (snapshot.state !== "speaking") return null;
  return SPEECH_PHASE_LABEL[snapshot.speech.phase] || null;
}

/**
 * B20 req 231: "Konuşuyor" only while something is actually being said.
 *
 * ADR-0066 already separates GENERATION from playback, and the analyser-driven pulse in
 * the Core is honest about it - no audio, no pulse. The words were not: the state token
 * turns `speaking` at `response_started`, and both readouts printed "Konuşuyor" from that
 * instant, which on a slow first token is a second or more of the page claiming the
 * assistant is talking into a silent room. The phase is the measurement; the label follows
 * it. Everything else about the state machine is unchanged - the state is still `speaking`,
 * because the turn IS the assistant's, and that is what the barge-in path needs to know.
 */
export const SPEECH_PREPARING_LABEL = "Yanıt hazırlanıyor…";

export function voiceStateLabel(snapshot: Pick<ControllerSnapshot, "state" | "speech">): string {
  if (snapshot.state === "speaking" && snapshot.speech.phase === "generating") {
    return SPEECH_PREPARING_LABEL;
  }
  return VOICE_STATE_LABEL[snapshot.state];
}

export const MODE_LABEL: Record<EnvironmentMode, string> = {
  auto: "Otomatik",
  quiet: "Sessiz ortam",
  noisy: "Gürültülü ortam",
  very_noisy: "Çok gürültülü ortam",
};

export const ENVIRONMENT_LABEL: Record<EnvironmentClass, string> = {
  quiet: "Sessiz",
  normal: "Normal",
  noisy: "Gürültülü",
  very_noisy: "Çok gürültülü",
};

export const VOICE_LABEL: Record<VoiceChoice, string> = { marin: "Marin", cedar: "Cedar" };

export const SUPPRESSION_LABEL: Record<MicrophoneProfile["noiseSuppressionMode"], string> = {
  auto: "Otomatik",
  browser: "Tarayıcı",
  off: "Kapalı",
};

// ------------------------------------------------------------------- tools

/**
 * The realtime tools Cloud Core exposes to the provider
 * (`services/api/app/voice/realtime_sessions/tools.py`), by name. A name not
 * listed here is shown as itself — a raw token is still a fact, whereas a
 * guessed friendly label would not be.
 */
export const TOOL_LABEL: Record<string, string> = {
  "clock.now": "Saat",
  "voice.intent": "Komut çözümleme",
  "narration.control": "Anlatım denetimi",
  "activity.explain": "Etkinlik açıklaması",
  "research.start": "Araştırma",
  "plan.redirect": "Plan yönlendirme",
};

export function toolLabel(name: string): string {
  return TOOL_LABEL[name] ?? name;
}

/** What the assistant is doing with a tool's result while it speaks. */
const TOOL_SPEAKING_CAPTION: Record<string, string> = {
  "research.start": "Araştırma sonuçlarını anlatıyorum…",
  "activity.explain": "Etkinliği açıklıyorum…",
  "narration.control": "Anlatımı sürdürüyorum…",
  "clock.now": "Saati söylüyorum…",
  "voice.intent": "Komutu uyguluyorum…",
  "plan.redirect": "Planı yönlendiriyorum…",
};

/**
 * The Core's one-line speech caption, or `null` when nothing semantic is
 * known — in which case the Core shows no caption rather than an invented
 * one. Sources, in order: the tool currently running (its Turkish caption,
 * else its name), then the narration cursor Cloud Core last pushed.
 */
export function speechCaption(
  snapshot: Pick<ControllerSnapshot, "state" | "toolsRunning" | "narrationCursor"> &
    Partial<Pick<ControllerSnapshot, "speech">>,
): string | null {
  if (snapshot.state !== "speaking") return null;
  // req 231: "…anlatıyorum" is a claim about speech. While the response is still being
  // generated there is none yet, and the caption waits with the voice.
  if (snapshot.speech && snapshot.speech.phase === "generating") return null;
  const tool = snapshot.toolsRunning.at(-1);
  if (tool) return TOOL_SPEAKING_CAPTION[tool] ?? `${toolLabel(tool)} sonucunu anlatıyorum…`;
  const cursor = snapshot.narrationCursor;
  if (cursor && cursor.state) return `Anlatım · ${describeNarrationCursor(cursor)}`;
  return null;
}

/** The label the Core shows while a tool runs: the newest tool's Turkish name. */
export function runningToolLabel(snapshot: Pick<ControllerSnapshot, "toolsRunning">): string | null {
  const tool = snapshot.toolsRunning.at(-1);
  return tool ? toolLabel(tool) : null;
}
