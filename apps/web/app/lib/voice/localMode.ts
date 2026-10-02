/**
 * "Yerel mod" — the free local voice mode (docs/DECISIONS.md ADR-0173).
 *
 * The browser's own recogniser (Chrome Web Speech, tr-TR, continuous) turns the
 * owner's speech into text; each FINAL transcript is posted to the SAME relay the
 * paid path uses, as an `utterance` event; the deterministic router's answer
 * (`resolved_intents[].tool`, the one tool it names) is executed by posting the
 * SAME `/tool-calls` the model would have issued — with EMPTY arguments, because the
 * tools read the owner's words from the turn record the relay keeps, never from the
 * caller (and the relay refuses text-shaped argument keys anyway); the tool's
 * `speech` is spoken by the browser's own `speechSynthesis` with a tr-TR voice.
 *
 * What there is NOT: a WebRTC leg, an OpenAI credential, a language model. A sentence
 * the router does not resolve is answered "Anlayamadım efendim." and logged as such,
 * never guessed. The paid `VoiceSessionController` is untouched; this module is a
 * separate, smaller state machine the owner switches to per browser.
 *
 * Product invariants kept here: a visible listening indicator (`listening` in the
 * snapshot, drawn by `VoiceControlView`) whenever the microphone is open; raw audio is
 * never touched (the recogniser owns it); transcripts are held only in memory for the
 * turn being handled and the one line the owner sees; the log carries ids/kinds, never
 * words. Recognition is stopped while the assistant speaks (no echo), and a final
 * transcript arriving while it speaks cancels the speech (barge-in).
 *
 * Every browser API is injected (`LocalModeDeps`) so `tests/voice/local-mode.test.ts`
 * drives it with fakes; `browserLocalModeDeps()` is the one place the real
 * `window.SpeechRecognition` / `window.speechSynthesis` are read, lazily.
 *
 * Which recogniser hears (chrome-on-device-stt): Chrome can run the recognition on the
 * device (`processLocally`, with a phrase list) once its Turkish pack is installed. That
 * is behind a setting (`sttSetting.ts`) whose default, `kapali`, starts the recogniser
 * exactly as before; every utterance says which engine heard it (`stt_engine`). See
 * `SttEngine` for what the name can and cannot promise.
 */

import type { VoiceSessionApi } from "./api";
import { VoiceApiError } from "./api";
import type { EventsResponse, SidebandFrame, ToolCallResponse } from "./contract";
import type { LocalActionPort } from "./ports";
import { eyeLocalActions } from "../eye/local-actions";
import { getEyeStore } from "../eye/store";
import { fetchCapabilities } from "../pages/capabilities";
import { listDevices } from "../research/api";
import { aliasesOf } from "../research/model";
import { type PhraseSources, buildPhrases } from "./sttPhrases";
import { STT_SETTING_DEFAULT, type SttSetting, parseSttSetting, readSttSetting } from "./sttSetting";

/** The server's `TRANSPORT_TEXT` (app/voice/providers.py); `test_voice_local_mode.py` reads this line. */
export const LOCAL_TRANSPORT = "text";
/** Prefix of every call_id this mode issues (fits `ToolCallRequest.call_id`'s pattern). */
export const LOCAL_CALL_ID_PREFIX = "local-";
export const LOCAL_LANGUAGE = "tr-TR";

export const NOT_UNDERSTOOD_TR = "Anlayamadım efendim.";
export const TOOL_FAILED_TR = "Komut yürütülemedi efendim.";
export const UNSUPPORTED_TR = "Bu tarayıcıda konuşma tanıma yok; Chrome gerekir.";

/**
 * The one line the shell shows when Chrome says the Turkish pack can be downloaded. The
 * size is not in it because nobody has measured it (the plan found "about 60 MB" in a
 * ship thread and 244 MB in a third-party post), and the last clause is the part the
 * owner cannot guess: Chrome prefers an installed pack even when it was not asked to.
 */
export const PACK_QUESTION_TR =
  "Türkçe paketi indirilsin mi? (C: sürücüsüne iner, boyutu ölçülmedi; indikten sonra Chrome onu bu ayar kapalıyken de kullanır.)";

/**
 * `available()` and `install()` can stay pending for ever (brave-browser#55414). These are
 * hang guards, not paces: when one fires, the answer is "today's path", never a blocked start.
 */
export const PROBE_GUARD_MS = 3_000;
export const PHRASE_SOURCES_GUARD_MS = 3_000;
export const INSTALL_GUARD_MS = 300_000;

/**
 * Errors that, on a run started with `processLocally`, mean "Chrome will not do this
 * on-device" rather than what they mean on today's path. `language-not-supported` is the
 * pack missing after all; `phrases-not-supported` is the phrase list refused;
 * `service-not-allowed` / `not-allowed` are the on-device permissions policy.
 */
const DEVICE_LEG_ERRORS: ReadonlySet<string> = new Set([
  "language-not-supported",
  "phrases-not-supported",
  "service-not-allowed",
  "not-allowed",
]);

const MAX_LOG = 40;

/**
 * Chrome does not always fire `onend` for an utterance (a long one is cut off around
 * fifteen seconds with no event at all), and the recogniser is stopped while we wait for
 * it: without a bound the owner would meet that as the assistant going deaf. The guard is
 * generous - it is a hang guard, not a pace - and ends the wait, never the session.
 */
export function speechGuardMs(text: string): number {
  return Math.min(60_000, 4_000 + text.length * 120);
}

// ------------------------------------------------------------------ ports

/** One recognised alternative (`SpeechRecognitionAlternative`). */
export type RecognitionAlternativeLike = { transcript: string };
/** One result (`SpeechRecognitionResult`): final or interim, first alternative at [0]. */
export type RecognitionResultLike = { isFinal: boolean; 0: RecognitionAlternativeLike; length: number };
export type RecognitionEventLike = { resultIndex: number; results: ArrayLike<RecognitionResultLike> };

/** The subset of `SpeechRecognition` this mode drives. */
export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: RecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  /** Chrome 139. Written only when the setting asks for the device leg; never read. */
  processLocally?: boolean;
  /** Chrome 142 (`SpeechRecognitionPhrase[]`). Only ever set together with `processLocally`. */
  phrases?: unknown;
  start(): void;
  stop(): void;
  abort(): void;
}

/** What `available()` / `install()` are asked. `quality` is never sent: any value but the default selects other, multi-GB models. */
export type OnDeviceOptions = { langs: string[]; processLocally: boolean };

/** `SpeechRecognition.available()` / `.install()` - the static pair Chrome 139 added. */
export interface OnDeviceSpeechLike {
  available(options: OnDeviceOptions): Promise<string>;
  install(options: OnDeviceOptions): Promise<boolean>;
}

/**
 * Which engine heard an utterance, as far as this page can know.
 *
 * - `chrome-cihaz-ici`: the run was started with `processLocally = true`. Chrome then
 *   hears on the device or not at all.
 * - `chrome-bulut`: the run was started as always AND no pack can be in use - the browser
 *   has no `available()`, or it answered `unavailable` / `downloadable`.
 * - `bilinmiyor`: the run was started as always and a pack is, or may be, installed.
 *   Chrome then picks on-device by itself (`UseOnDeviceSpeechRecognition` in Chromium's
 *   `speech_recognition_manager_impl.cc`; Chromium issue 521896368) and tells nobody.
 */
export type SttEngine = "chrome-cihaz-ici" | "chrome-bulut" | "bilinmiyor";

/** The last thing known about the Turkish pack; `pending` until the probe answers. */
type PackState = "api-missing" | "pending" | "available" | "downloadable" | "downloading" | "unavailable" | "unknown";

type Guarded<T> = { ok: true; value: T } | { ok: false; reason: "threw" | "timeout" };

export type VoiceLike = { lang: string; name: string; default?: boolean };

/** The subset of `SpeechSynthesisUtterance` this mode sets. */
export interface UtteranceLike {
  text: string;
  lang: string;
  voice: VoiceLike | null;
  onend: (() => void) | null;
  onerror: ((event: unknown) => void) | null;
}

/** The subset of `speechSynthesis` this mode drives. */
export interface SpeechSynthesisLike {
  speak(utterance: UtteranceLike): void;
  cancel(): void;
  getVoices(): VoiceLike[];
}

export type LocalModeDeps = {
  api: VoiceSessionApi;
  /** A fresh recogniser, or null when the browser has none. */
  recognition: () => SpeechRecognitionLike | null;
  /** The synthesiser, or null when the browser has none (then nothing is spoken, only logged). */
  synthesis: () => SpeechSynthesisLike | null;
  utterance: (text: string) => UtteranceLike;
  now?: () => number;
  newId?: () => string;
  /** The speech hang guard's timer; `setTimeout`/`clearTimeout` by default, a manual one in tests. */
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  /**
   * The capabilities that live in THIS tab rather than on the device or the server - the
   * camera (`eye.enable` / `eye.disable`), through the same `LocalActionPort` the paid
   * realtime path uses (`lib/eye/local-actions.ts`). Without it the local mode would post
   * `eye.enable` to a Cloud Core that can only answer "capability_missing": the server
   * never sets the durable flag for a camera nobody opened, so nothing would happen and
   * the owner would be told something did (owner, 2026-09-20: "kamerayı sesli açma kapama
   * yerel modda kapalı").
   */
  localActions?: LocalActionPort;
  /** `SpeechRecognition.available/install`, or null when this browser has none. Absent = none. */
  onDevice?: () => OnDeviceSpeechLike | null;
  /** `new SpeechRecognitionPhrase(text, boost)`, or null when this browser has none (then: on-device without phrases). */
  phrase?: () => ((text: string, boost: number) => unknown) | null;
  /** The owner's setting, read at every `start()`. Absent, unreadable or unknown = `kapali`. */
  sttSetting?: () => SttSetting;
  /** What the phrase list is built from; a failure costs the session's names, never the start. */
  phraseSources?: () => Promise<PhraseSources>;
};

// --------------------------------------------------------------- snapshot

export type LocalModeState =
  | "off"
  | "starting"
  | "listening"
  | "thinking"
  | "speaking"
  | "error"
  | "unsupported";

export type LocalModeSnapshot = {
  state: LocalModeState;
  sessionId: string | null;
  provider: string | null;
  /** The microphone is open and the recogniser is running — the visible indicator's truth. */
  listening: boolean;
  speaking: boolean;
  turn: number;
  /** The last FINAL transcript, for the owner to check the recogniser; memory only. */
  lastHeard: string;
  lastSpoken: string;
  lastError: string | null;
  /** Kinds and ids only — never the owner's words. */
  log: string[];
  unresolved: number;
  /** The engine of the recogniser run that is (or was last) listening. */
  sttEngine: SttEngine;
  /** Why the setting asked for the device and did not get it; a code, null when it did or never asked. */
  sttFallback: string | null;
  /** The pack question waiting for a CLICK (`answerPackQuestion`); null when there is none. */
  packQuestion: string | null;
};

export const OFF_SNAPSHOT: LocalModeSnapshot = Object.freeze({
  state: "off",
  sessionId: null,
  provider: null,
  listening: false,
  speaking: false,
  turn: 0,
  lastHeard: "",
  lastSpoken: "",
  lastError: null,
  log: [],
  unresolved: 0,
  sttEngine: "bilinmiyor",
  sttFallback: null,
  packQuestion: null,
}) as LocalModeSnapshot;

export const LOCAL_STATE_LABEL: Record<LocalModeState, string> = {
  off: "kapalı",
  starting: "başlatılıyor",
  listening: "dinliyor",
  thinking: "çözümlüyor",
  speaking: "konuşuyor",
  error: "hata",
  unsupported: "desteklenmiyor",
};

// ------------------------------------------------------------- helpers

/** The one tool the router named for a resolved intent, or null when only a model could choose. */
export function toolOf(intent: Record<string, unknown>): string | null {
  const tool = intent.tool;
  if (typeof tool === "string" && tool) return tool;
  const capability = intent.capability;
  if (typeof capability === "string" && capability) return capability;
  return null;
}

/** What to say for a tool-call answer: the handler's own sentence, or an honest failure line. */
export function speechOf(response: ToolCallResponse): string | null {
  const result = response.result;
  if (result && typeof result.speech === "string" && result.speech) return result.speech;
  if (response.status === "failed") {
    const error = response.error;
    if (error && typeof error.speech === "string" && error.speech) return error.speech;
    return TOOL_FAILED_TR;
  }
  if (typeof response.preamble === "string" && response.preamble) return response.preamble;
  return null;
}

/** The `say` frames the relay attached to an events answer (a clarification question). */
export function saidFrames(frames: SidebandFrame[] | undefined): string[] {
  const out: string[] = [];
  for (const frame of frames ?? []) {
    if (frame.event !== "say") continue;
    const text = frame.payload?.text;
    if (typeof text === "string" && text) out.push(text);
  }
  return out;
}

/** A tr-TR voice when the browser has one (an exact tag first, then any Turkish), else null. */
export function pickTurkishVoice(voices: VoiceLike[]): VoiceLike | null {
  const exact = voices.find((v) => v.lang === LOCAL_LANGUAGE || v.lang === "tr_TR");
  if (exact) return exact;
  return voices.find((v) => v.lang.toLowerCase().startsWith("tr")) ?? null;
}

function defaultId(): string {
  const c = globalThis.crypto as { randomUUID?: () => string } | undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function describe(error: unknown): string {
  if (error instanceof VoiceApiError) return `${error.path}: HTTP ${error.status}`;
  if (error instanceof Error) return error.message;
  return String(error);
}

/** `chrome-bulut` only when no pack can be in use; see `SttEngine`. */
export function engineOf(deviceLeg: boolean, pack: string): SttEngine {
  if (deviceLeg) return "chrome-cihaz-ici";
  return pack === "api-missing" || pack === "unavailable" || pack === "downloadable" ? "chrome-bulut" : "bilinmiyor";
}

function errorName(error: unknown): string {
  const name = (error as { name?: unknown } | null)?.name;
  return typeof name === "string" && name ? name : "Error";
}

// ------------------------------------------------------------------ mode

export class LocalVoiceMode {
  private snapshot: LocalModeSnapshot = OFF_SNAPSHOT;
  private readonly listeners = new Set<() => void>();
  private recognizer: SpeechRecognitionLike | null = null;
  private sessionId: string | null = null;
  /** True between `start()` and `stop()`: the recogniser is restarted whenever it ends on its own. */
  private active = false;
  /** True while the assistant speaks: the recogniser is stopped and must not restart yet. */
  private paused = false;
  private speaking = false;
  private t0 = 0;
  private turn = 0;
  /** Finals are handled one after another; a new one still cancels ongoing speech at once. */
  private chain: Promise<void> = Promise.resolve();
  /**
   * Finals accepted and not yet finished. Counted the moment a final ARRIVES (not when its
   * chained handler starts): Chrome can end the recogniser in the same tick, and a restart
   * must not repaint "thinking" as "listening".
   */
  private pending = 0;
  /** Counts `start()`s: a probe or an install that answers after its session is gone writes nothing. */
  private generation = 0;
  private setting: SttSetting = STT_SETTING_DEFAULT;
  private pack: PackState = "api-missing";
  /** The pack is usable and no device run has been refused in this session. */
  private deviceUsable = false;
  /** The leg the NEXT recogniser run should use; applied only between runs. */
  private wantDevice = false;
  /** The leg of the run that is (or was last) started: what `stt_engine` is read from. */
  private runDevice = false;
  /** Our own account of "the recogniser is started": properties are written only while it is not. */
  private running = false;
  /** The recogniser's `processLocally` was written at least once (so it must be written back). */
  private touched = false;
  private phrasesSet = false;
  private phrases: unknown[] = [];
  private readonly now: () => number;
  private readonly newId: () => string;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (handle: unknown) => void;

  constructor(private readonly deps: LocalModeDeps) {
    this.now = deps.now ?? (() => Date.now());
    this.newId = deps.newId ?? defaultId;
    this.setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = deps.clearTimer ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  }

  // ----------------------------------------------------------- observers

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): LocalModeSnapshot => this.snapshot;

  getServerSnapshot = (): LocalModeSnapshot => OFF_SNAPSHOT;

  private patch(partial: Partial<LocalModeSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...partial };
    for (const listener of this.listeners) listener();
  }

  private log(line: string): void {
    const log = [...this.snapshot.log, line].slice(-MAX_LOG);
    this.patch({ log });
  }

  // ----------------------------------------------------------- lifecycle

  /** Create the text session and open the recogniser. A no-op while already on. */
  async start(): Promise<void> {
    if (this.active) return;
    const recognizer = this.deps.recognition();
    if (!recognizer) {
      this.patch({ ...OFF_SNAPSHOT, state: "unsupported", lastError: UNSUPPORTED_TR });
      return;
    }
    this.active = true;
    this.paused = false;
    this.speaking = false;
    this.turn = 0;
    this.t0 = this.now();
    this.generation += 1;
    this.setting = STT_SETTING_DEFAULT;
    this.pack = "api-missing";
    this.deviceUsable = false;
    this.wantDevice = false;
    this.runDevice = false;
    this.running = false;
    this.touched = false;
    this.phrasesSet = false;
    this.phrases = [];
    this.patch({ ...OFF_SNAPSHOT, state: "starting" });
    let created: { session_id: string; provider: string; transport: string };
    try {
      // `test_voice_local_mode.py` reads this call: the transport constant must be the
      // server's, and no wire `voice` may travel (the local router vets none).
      created = await this.deps.api.create({ client_kind: "web", transport: LOCAL_TRANSPORT, language: LOCAL_LANGUAGE });
    } catch (error) {
      this.active = false;
      this.patch({ state: "error", lastError: `Yerel oturum açılamadı: ${describe(error)}` });
      this.log("session.create.failed");
      return;
    }
    if (!this.active) {
      // stop() raced the create: close what was just made.
      void this.deps.api.close(created.session_id, "client_closed").catch(() => {});
      return;
    }
    this.sessionId = created.session_id;
    this.recognizer = recognizer;
    recognizer.lang = LOCAL_LANGUAGE;
    recognizer.continuous = true;
    recognizer.interimResults = false;
    recognizer.onresult = (event) => this.onResult(event);
    recognizer.onend = () => this.onRecognizerEnd();
    recognizer.onerror = (event) => this.onRecognizerError(event?.error);
    this.patch({ sessionId: created.session_id, provider: created.provider });
    this.log(`session.created provider=${created.provider} transport=${created.transport}`);
    const preparing = this.prepareStt();
    if (preparing) {
      // Only `acik` / `olc` wait for Chrome's answer, and never longer than the guard.
      const generation = this.generation;
      await preparing;
      if (!this.active || generation !== this.generation || this.recognizer !== recognizer) return; // stop() raced the probe
    }
    this.listen();
  }

  /** Close the recogniser, the speech and the session. */
  async stop(): Promise<void> {
    await this.end("off", null);
  }

  /**
   * The page is going away (reload, closed tab): tell the Cloud Core, with a request that
   * outlives the page. The session never expires by itself (ADR-0105), so without this
   * every reload would leave one open - the paid store's 2026-09-06 incident, not repeated.
   */
  closeOnUnload(): void {
    const sessionId = this.sessionId;
    if (!sessionId) return;
    void this.deps.api.close(sessionId, "page_unload", { keepalive: true }).catch(() => {
      /* the page is going away; there is nobody to tell */
    });
  }

  /** One teardown for the owner's stop and for a fatal error: the session is ALWAYS closed. */
  private async end(state: "off" | "error", lastError: string | null): Promise<void> {
    if (!this.active && !this.sessionId) return;
    this.active = false;
    this.pending = 0;
    this.running = false;
    const recognizer = this.recognizer;
    this.recognizer = null;
    if (recognizer) {
      recognizer.onresult = null;
      recognizer.onend = null;
      recognizer.onerror = null;
      try {
        recognizer.abort();
      } catch {
        /* already stopped */
      }
    }
    this.cancelSpeech();
    const sessionId = this.sessionId;
    this.sessionId = null;
    // What was heard and said is dropped with the session: memory only, and not past it.
    this.patch({ state, listening: false, speaking: false, sessionId: null, provider: null, lastHeard: "", lastSpoken: "", lastError, packQuestion: null });
    if (sessionId) {
      try {
        await this.deps.api.close(sessionId, "client_closed");
        this.log("session.closed");
      } catch (error) {
        this.log(`session.close.failed ${describe(error)}`);
      }
    }
  }

  // ------------------------------------------------------------ listening

  private listen(): void {
    const recognizer = this.recognizer;
    if (!recognizer || !this.active || this.paused) return;
    // A leg is chosen only BETWEEN runs: writing `phrases` on a started recogniser is an
    // error event in Chrome, and the engine name must stay the one of the run that hears.
    if (!this.running) this.configure(recognizer);
    try {
      recognizer.start();
      this.running = true;
    } catch (error) {
      if (errorName(error) === "InvalidStateError") {
        // Chrome throws InvalidStateError when it is already running; that is fine.
        this.running = true;
      } else if (this.runDevice) {
        // With `processLocally` Chrome can refuse the start itself (NotAllowedError for the
        // on-device permissions policy). Swallowed, that is a mode that says "dinliyor" and
        // hears nothing: go back to today's path and start that.
        this.leaveDevice(`start-${errorName(error)}`);
        this.configure(recognizer);
        try {
          recognizer.start();
          this.running = true;
        } catch {
          /* today's path, as it always was */
        }
      }
    }
    this.patch(this.pending > 0 ? { listening: true } : { state: "listening", listening: true, speaking: false });
  }

  /** Write the leg the next run should use onto the recogniser. `kapali` writes nothing, ever. */
  private configure(recognizer: SpeechRecognitionLike): void {
    const device = this.wantDevice && this.deviceUsable;
    if (device) {
      // `processLocally` first: phrases without it is `phrases-not-supported`.
      recognizer.processLocally = true;
      this.touched = true;
      if (this.phrases.length > 0) {
        recognizer.phrases = this.phrases;
        this.phrasesSet = true;
      }
    } else if (this.touched) {
      if (this.phrasesSet) recognizer.phrases = [];
      recognizer.processLocally = false;
    }
    this.runDevice = device;
    this.showEngine();
  }

  // ---------------------------------------------------------- which engine

  private engine(): SttEngine {
    return engineOf(this.runDevice, this.pack);
  }

  private showEngine(): void {
    const sttEngine = this.engine();
    if (sttEngine !== this.snapshot.sttEngine) this.patch({ sttEngine });
  }

  /** The setting asked for the device and this is why it is not used. A code, never words. */
  private fallback(reason: string): void {
    this.deviceUsable = false;
    this.wantDevice = false;
    this.patch({ sttFallback: reason });
    this.log(`stt.fallback ${reason}`);
  }

  /** A device run was refused: today's path for the rest of the session (no retry loop). */
  private leaveDevice(reason: string): void {
    this.fallback(reason);
    this.running = false;
  }

  /** Race a browser promise against the injected timer; a throw and a hang are both answers. */
  private guarded<T>(run: () => Promise<T>, ms: number): Promise<Guarded<T>> {
    return new Promise<Guarded<T>>((resolve) => {
      let done = false;
      let guard: unknown = null;
      const settle = (result: Guarded<T>) => {
        if (done) return;
        done = true;
        this.clearTimer(guard);
        resolve(result);
      };
      guard = this.setTimer(() => settle({ ok: false, reason: "timeout" }), ms);
      try {
        // Called here, synchronously: `install()` needs the click that is still on the stack.
        run().then(
          (value) => settle({ ok: true, value }),
          () => settle({ ok: false, reason: "threw" }),
        );
      } catch {
        settle({ ok: false, reason: "threw" });
      }
    });
  }

  /**
   * Read the setting and ask Chrome about the Turkish pack. Returns null when the start
   * must not wait (`kapali`, or no API): the probe then only names the engine, later.
   */
  private prepareStt(): Promise<void> | null {
    let setting: SttSetting = STT_SETTING_DEFAULT;
    try {
      setting = parseSttSetting(this.deps.sttSetting?.());
    } catch {
      /* a setting that cannot be read is the default */
    }
    this.setting = setting;
    let api: OnDeviceSpeechLike | null = null;
    try {
      api = this.deps.onDevice?.() ?? null;
    } catch {
      api = null;
    }
    if (!api) {
      this.pack = "api-missing";
      if (setting !== "kapali") this.fallback("api-missing");
      this.showEngine();
      return null;
    }
    const generation = this.generation;
    const found = api;
    this.pack = "pending";
    this.showEngine();
    // Read-only, in every setting: without it `kapali` could never say more than "bilinmiyor".
    const probing = this.guarded(() => found.available({ langs: [LOCAL_LANGUAGE], processLocally: true }), PROBE_GUARD_MS).then(
      (answer) => {
        if (generation !== this.generation || !this.active) return null;
        if (answer.ok) {
          const status = answer.value;
          this.pack =
            status === "available" || status === "downloadable" || status === "downloading" || status === "unavailable" ? status : "unknown";
        } else {
          this.pack = "unknown";
        }
        this.showEngine();
        return answer;
      },
    );
    if (setting === "kapali") return null;
    return probing.then(async (answer) => {
      if (!answer) return;
      if (!answer.ok) {
        this.fallback(`probe-${answer.reason}`);
        return;
      }
      if (this.pack === "available") {
        await this.useDevice(generation);
      } else if (this.pack === "downloadable") {
        // Nothing is downloaded here. `answerPackQuestion(true)` - a click - is the only way.
        this.fallback("pack-downloadable");
        this.patch({ packQuestion: PACK_QUESTION_TR });
        this.log("stt.pack.question");
      } else if (this.pack === "downloading") {
        this.fallback("pack-downloading");
      } else if (this.pack === "unavailable") {
        this.fallback("unavailable");
      } else {
        this.fallback("status-unknown");
      }
    });
  }

  /** The pack is usable: build the phrase list and let the next run(s) use the device. */
  private async useDevice(generation: number): Promise<void> {
    const phrases = await this.buildPhraseObjects();
    if (generation !== this.generation || !this.active) return;
    this.phrases = phrases;
    this.deviceUsable = true;
    // `olc` starts on today's path and flips after each final; `acik` is the device from the next run on.
    this.wantDevice = this.setting === "acik";
    this.log(`stt.device ready phrases=${phrases.length}`);
  }

  private async buildPhraseObjects(): Promise<unknown[]> {
    let make: ((text: string, boost: number) => unknown) | null = null;
    try {
      make = this.deps.phrase?.() ?? null;
    } catch {
      make = null;
    }
    if (!make) return []; // Chrome 139-141: on-device, without phrases
    const sources = this.deps.phraseSources;
    const fetched = sources ? await this.guarded(() => sources(), PHRASE_SOURCES_GUARD_MS) : null;
    const out: unknown[] = [];
    for (const item of buildPhrases(fetched && fetched.ok ? fetched.value : null)) {
      try {
        out.push(make(item.phrase, item.boost));
      } catch {
        /* one phrase Chrome refuses is one phrase less */
      }
    }
    return out;
  }

  /**
   * The owner's answer to `packQuestion`. It MUST be called from a click handler: Chrome's
   * `install()` consumes transient user activation and rejects without one, so a spoken
   * "evet" cannot be the yes. Nothing is downloaded on any other path.
   */
  answerPackQuestion(yes: boolean): void {
    if (!this.active || this.snapshot.packQuestion === null) return;
    this.patch({ packQuestion: null });
    if (!yes) {
      this.fallback("pack-declined");
      return;
    }
    let api: OnDeviceSpeechLike | null = null;
    try {
      api = this.deps.onDevice?.() ?? null;
    } catch {
      api = null;
    }
    if (!api) return;
    const found = api;
    const generation = this.generation;
    // From here on a pack may exist: today's path can no longer be called the cloud.
    this.pack = "downloading";
    this.showEngine();
    this.log("stt.pack.install requested");
    // `processLocally: true` is required: without it Chrome resolves false and installs nothing.
    void this.guarded(() => found.install({ langs: [LOCAL_LANGUAGE], processLocally: true }), INSTALL_GUARD_MS).then(async (answer) => {
      if (generation !== this.generation || !this.active) return;
      if (!answer.ok) {
        this.fallback(`install-${answer.reason}`);
        return;
      }
      if (answer.value !== true) {
        this.fallback("install-refused");
        return;
      }
      this.pack = "available";
      this.patch({ sttFallback: null });
      this.log("stt.pack.installed");
      // The running recogniser is left alone; the device leg begins at its next restart.
      await this.useDevice(generation);
    });
  }

  private pauseListening(): void {
    this.paused = true;
    const recognizer = this.recognizer;
    if (recognizer) {
      try {
        recognizer.stop();
      } catch {
        /* already stopped */
      }
    }
    this.patch({ listening: false });
  }

  private onRecognizerEnd(): void {
    // Chrome ends a continuous session on its own after silence or about a minute;
    // while the owner has not stopped us and the assistant is not speaking, listen again.
    this.running = false;
    if (!this.active) return;
    this.patch({ listening: false });
    if (this.paused) return;
    this.listen();
  }

  private onRecognizerError(error: string | undefined): void {
    if (!this.active) return;
    const code = error ?? "unknown";
    this.log(`recognition.error ${code}`);
    if (this.runDevice && DEVICE_LEG_ERRORS.has(code)) {
      // No `onend` follows `language-not-supported`, so the restart is issued from here;
      // when one does follow, the second start is the InvalidStateError `listen()` expects.
      this.leaveDevice(`error-${code}`);
      this.listen();
      return;
    }
    if (code === "no-speech" || code === "aborted" || code === "network") return; // onend restarts
    if (code === "not-allowed" || code === "service-not-allowed") {
      // Fatal: nothing will ever be heard. The session is closed, not abandoned.
      void this.end("error", "Mikrofon izni verilmedi.");
      return;
    }
    this.patch({ lastError: `Tanıma hatası: ${code}` });
  }

  private onResult(event: RecognitionEventLike): void {
    const results = event.results;
    for (let i = event.resultIndex; i < results.length; i += 1) {
      const result = results[i];
      if (!result || !result.isFinal) continue; // interim results are ignored on purpose
      const text = (result[0]?.transcript ?? "").trim();
      if (!text) continue;
      this.onFinal(text);
    }
  }

  /** A final transcript: barge in at once, then handle it after the previous one. */
  onFinal(text: string): void {
    if (!this.active || !this.sessionId) return;
    if (this.speaking) {
      this.cancelSpeech();
      this.log("barge_in");
    }
    // The engine of the run that HEARD it, taken now: finals queue behind speech, and by
    // the time this one is posted the recogniser may have restarted on the other leg.
    const engine = this.engine();
    if (this.setting === "olc" && this.deviceUsable) this.wantDevice = !this.runDevice;
    this.pending += 1;
    this.patch({ state: "thinking" });
    this.chain = this.chain
      .then(() => this.handle(text, engine))
      .catch(() => {})
      .then(() => {
        this.pending = Math.max(0, this.pending - 1);
        if (this.active) this.listen();
      });
  }

  // -------------------------------------------------------------- a turn

  private async handle(text: string, engine: SttEngine): Promise<void> {
    const sessionId = this.sessionId;
    if (!this.active || !sessionId) return;
    this.turn += 1;
    const turn = this.turn;
    this.patch({ state: "thinking", turn, lastHeard: text, lastError: null });
    await this.turnBody(sessionId, turn, text, engine);
  }

  private async turnBody(sessionId: string, turn: number, text: string, engine: SttEngine): Promise<void> {
    let answer: EventsResponse;
    try {
      answer = await this.deps.api.events(sessionId, [
        // `stt_engine` is not a forbidden payload key (`isForbiddenKey`) and the server's
        // `ClientEvent.payload` is an open object: a server that does not read it yet accepts it.
        { kind: "utterance", t_ms: Math.max(0, Math.round(this.now() - this.t0)), turn, text, payload: { stt_engine: engine } },
      ]);
    } catch (error) {
      this.log(`utterance.failed turn=${turn} ${describe(error)}`);
      this.patch({ lastError: `Cümle iletilemedi: ${describe(error)}` });
      if (error instanceof VoiceApiError && error.gone) {
        await this.end("error", "Yerel oturum sunucuda kapanmış; yeniden başlatın.");
      }
      return;
    }
    this.log(`utterance.sent turn=${turn} chars=${text.length}`);
    const tools: string[] = [];
    for (const intent of answer.resolved_intents ?? []) {
      const tool = toolOf(intent);
      if (tool && !tools.includes(tool)) tools.push(tool);
    }
    const said = saidFrames(answer.pending_sideband);
    if (tools.length === 0 && said.length === 0) {
      this.log(`unresolved turn=${turn}`);
      this.patch({ unresolved: this.snapshot.unresolved + 1 });
      await this.speak(NOT_UNDERSTOOD_TR);
      return;
    }
    for (const line of said) await this.speak(line);
    for (const name of tools) {
      if (!this.active) return;
      let response: ToolCallResponse;
      const callId = `${LOCAL_CALL_ID_PREFIX}${this.newId()}`;
      // A capability that lives in this tab runs HERE first, and what it observed travels
      // with the call: the server builds its receipt from the camera's real state, never
      // from the fact that a tool was called. Anything else keeps the empty arguments.
      const observed = await this.runLocally(name, callId, sessionId, text);
      try {
        response = await this.deps.api.toolCall(sessionId, {
          call_id: callId,
          name,
          arguments: observed ? { observed_after: observed } : {},
        });
      } catch (error) {
        this.log(`tool:${name} request.failed ${describe(error)}`);
        this.patch({ lastError: `Araç çağrısı iletilemedi: ${describe(error)}` });
        await this.speak(TOOL_FAILED_TR);
        continue;
      }
      this.log(`tool:${name} ${response.status}${response.replayed ? " replayed" : ""}`);
      const speech = speechOf(response);
      if (speech) await this.speak(speech);
    }
  }

  /** The local half of a tool, or null when this tool has none (or it failed). */
  private async runLocally(
    name: string,
    callId: string,
    sessionId: string,
    said: string,
  ): Promise<Record<string, unknown> | null> {
    const port = this.deps.localActions;
    if (!port) return null;
    try {
      return await port.run(name, { utterance: said, call_id: callId, session_id: sessionId });
    } catch (error) {
      // The call still goes: the server's own read-back decides, and a receipt that says
      // the camera did not open is worth more than a turn that vanishes.
      this.log(`local:${name} failed ${describe(error)}`);
      return null;
    }
  }

  // ------------------------------------------------------------- speaking

  /** Speak one line with the browser's tr-TR voice; the recogniser is stopped meanwhile. */
  private speak(text: string): Promise<void> {
    if (!this.active) return Promise.resolve();
    const synthesis = this.deps.synthesis();
    this.pauseListening();
    this.speaking = true;
    this.patch({ state: "speaking", speaking: true, lastSpoken: text });
    this.log(`speak chars=${text.length}`);
    if (!synthesis) {
      this.speaking = false;
      this.paused = false;
      this.patch({ speaking: false });
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      const utterance = this.deps.utterance(text);
      utterance.lang = LOCAL_LANGUAGE;
      utterance.voice = pickTurkishVoice(synthesis.getVoices());
      let done = false;
      const guard = this.setTimer(() => {
        this.log("speak.guard_fired");
        finish();
      }, speechGuardMs(text));
      const finish = () => {
        if (done) return;
        done = true;
        this.clearTimer(guard);
        if (this.currentFinish === finish) this.currentFinish = null;
        this.speaking = false;
        this.paused = false;
        this.patch({ speaking: false });
        resolve();
      };
      utterance.onend = finish;
      utterance.onerror = finish;
      this.currentFinish = finish;
      synthesis.speak(utterance);
    });
  }

  private currentFinish: (() => void) | null = null;

  private cancelSpeech(): void {
    const synthesis = this.deps.synthesis();
    if (synthesis && this.speaking) synthesis.cancel();
    const finish = this.currentFinish;
    this.currentFinish = null;
    this.speaking = false;
    this.paused = false;
    if (finish) finish();
    this.patch({ speaking: false });
  }
}

// ------------------------------------------------------------- browser

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function recognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** The session's names, from the two lists the shell already reads; either may fail alone. */
async function browserPhraseSources(): Promise<PhraseSources> {
  const [devices, capabilities] = await Promise.allSettled([listDevices(), fetchCapabilities()]);
  return {
    deviceAliases: devices.status === "fulfilled" ? devices.value.flatMap((device) => aliasesOf(device)) : [],
    capabilityPhrases:
      capabilities.status === "fulfilled" && capabilities.value.kind === "ok"
        ? capabilities.value.value.rows.flatMap((row) => row.phrases)
        : [],
  };
}

/** The real browser ports, read lazily so importing this module on the server is free. */
export function browserLocalModeDeps(api: VoiceSessionApi): LocalModeDeps {
  return {
    api,
    recognition: () => {
      const Ctor = recognitionCtor();
      return Ctor ? new Ctor() : null;
    },
    synthesis: () => {
      if (typeof window === "undefined" || !("speechSynthesis" in window)) return null;
      const real = window.speechSynthesis;
      return {
        speak: (utterance) => real.speak(utterance as unknown as SpeechSynthesisUtterance),
        cancel: () => real.cancel(),
        getVoices: () => real.getVoices(),
      };
    },
    utterance: (text) => new SpeechSynthesisUtterance(text) as unknown as UtteranceLike,
    // The camera is in this browser in the local mode exactly as it is in the paid one,
    // and it is the SAME port and the same `EyeStore` - one store per tab, so the eye
    // panel and a spoken "kamerayı aç" can never disagree about what the camera is doing.
    localActions: eyeLocalActions(getEyeStore),
    // Chrome 139's static pair, feature-detected on whichever constructor the browser has.
    onDevice: () => {
      const statics = recognitionCtor() as unknown as Partial<OnDeviceSpeechLike> | null;
      if (!statics || typeof statics.available !== "function" || typeof statics.install !== "function") return null;
      const found = statics as OnDeviceSpeechLike;
      return { available: (options) => found.available(options), install: (options) => found.install(options) };
    },
    // Chrome 142 per MDN's compat data, 140 per its ship intent: detected, never assumed.
    phrase: () => {
      if (typeof window === "undefined") return null;
      const Phrase = (window as unknown as { SpeechRecognitionPhrase?: new (phrase: string, boost: number) => unknown }).SpeechRecognitionPhrase;
      return typeof Phrase === "function" ? (text, boost) => new Phrase(text, boost) : null;
    },
    sttSetting: () => readSttSetting(typeof window === "undefined" ? null : window.localStorage),
    phraseSources: browserPhraseSources,
  };
}
