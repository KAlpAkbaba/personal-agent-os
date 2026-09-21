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
 */

import type { VoiceSessionApi } from "./api";
import { VoiceApiError } from "./api";
import type { EventsResponse, SidebandFrame, ToolCallResponse } from "./contract";
import type { LocalActionPort } from "./ports";
import { eyeLocalActions } from "../eye/local-actions";
import { getEyeStore } from "../eye/store";

/** The server's `TRANSPORT_TEXT` (app/voice/providers.py); `test_voice_local_mode.py` reads this line. */
export const LOCAL_TRANSPORT = "text";
/** Prefix of every call_id this mode issues (fits `ToolCallRequest.call_id`'s pattern). */
export const LOCAL_CALL_ID_PREFIX = "local-";
export const LOCAL_LANGUAGE = "tr-TR";

export const NOT_UNDERSTOOD_TR = "Anlayamadım efendim.";
export const TOOL_FAILED_TR = "Komut yürütülemedi efendim.";
export const UNSUPPORTED_TR = "Bu tarayıcıda konuşma tanıma yok; Chrome gerekir.";

const MAX_LOG = 40;
/** How many repeats of one gesture one coalesced tool call may carry. */
export const GESTURE_COALESCE_MAX = 5;
/** The tools that take ADR-0195's `count`. */
const GESTURE_COUNT_TOOLS = new Set(["operator.key", "operator.pointer"]);

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
  start(): void;
  stop(): void;
  abort(): void;
}

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
    this.patch({ state, listening: false, speaking: false, sessionId: null, provider: null, lastHeard: "", lastSpoken: "", lastError });
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
    try {
      recognizer.start();
    } catch {
      // Chrome throws InvalidStateError when it is already running; that is fine.
    }
    this.patch(this.pending > 0 ? { listening: true } : { state: "listening", listening: true, speaking: false });
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
    if (!this.active) return;
    this.patch({ listening: false });
    if (this.paused) return;
    this.listen();
  }

  private onRecognizerError(error: string | undefined): void {
    if (!this.active) return;
    const code = error ?? "unknown";
    this.log(`recognition.error ${code}`);
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
    this.pending += 1;
    this.patch({ state: "thinking" });
    this.chain = this.chain
      .then(() => this.handle(text))
      .catch(() => {})
      .then(() => {
        this.pending = Math.max(0, this.pending - 1);
        if (this.active) this.listen();
      });
  }

  // --------------------------------------------------------- ADR-0198 gestures

  /**
   * A browser-recognised hand gesture (`lib/gesture/controller.ts`) executes the SAME tool
   * the local voice router would for a spoken command, through the SAME turn/tool plumbing
   * as `turnBody` — but it POSTs a `gesture` client event (no text, no router: the server's
   * `app/voice/gestures.py` resolves it deterministically) and, critically, NEVER speaks.
   * A swipe repeated every few hundred ms must not narrate "Sağ ok tuşuna bastım efendim"
   * each time (owner, 2026-09-21) — `controller.ts`'s HUD shows the last gesture instead.
   * Requires an active session: the caller gates on the eye being enabled, the "El
   * kumandası" toggle being on, AND a local-mode session existing before ever calling this
   * (a gesture has no session of its own) — this method still checks, defensively, and is a
   * silent no-op when there is none, the same as `onFinal` is for a stopped mode.
   */
  /**
   * Gestures are coalesced, never queued without bound (owner, third trial: "belli bir süre
   * sonra komutlar bilgisayara geç geliyor" - each gesture is an events POST, a tool call and
   * a device round trip, ~1 s, and the tracker can emit two a second). While one is in
   * flight, the NEXT is held: the same gesture again raises its count (the tool presses the
   * key that many times in ONE call, ADR-0195's `count`), a different gesture replaces it.
   * So the owner is never more than one round trip behind their hand.
   */
  async dispatchGesture(gesture: string): Promise<void> {
    if (!this.active || !this.sessionId) return;
    if (this.gestureInFlight) {
      if (this.pendingGesture && this.pendingGesture.gesture === gesture) {
        this.pendingGesture.count = Math.min(this.pendingGesture.count + 1, GESTURE_COALESCE_MAX);
      } else {
        this.pendingGesture = { gesture, count: 1 };
      }
      this.log(`gesture:${gesture} held`);
      return;
    }
    this.gestureInFlight = true;
    try {
      await this.runGesture(gesture, 1);
      // Drain what accumulated meanwhile, one coalesced call at a time.
      while (this.pendingGesture && this.active) {
        const next = this.pendingGesture;
        this.pendingGesture = null;
        await this.runGesture(next.gesture, next.count);
      }
    } finally {
      this.gestureInFlight = false;
    }
  }

  private gestureInFlight = false;
  private pendingGesture: { gesture: string; count: number } | null = null;

  private async runGesture(gesture: string, count: number): Promise<void> {
    const sessionId = this.sessionId;
    if (!this.active || !sessionId) return;
    this.turn += 1;
    const turn = this.turn;
    let answer: EventsResponse;
    try {
      answer = await this.deps.api.events(sessionId, [
        { kind: "gesture", t_ms: Math.max(0, Math.round(this.now() - this.t0)), turn, gesture },
      ]);
    } catch (error) {
      this.log(`gesture.failed turn=${turn} ${describe(error)}`);
      return;
    }
    this.log(`gesture:${gesture} turn=${turn}${count > 1 ? ` x${count}` : ""}`);
    const tools: string[] = [];
    for (const intent of answer.resolved_intents ?? []) {
      const tool = toolOf(intent);
      if (tool && !tools.includes(tool)) tools.push(tool);
    }
    for (const name of tools) {
      if (!this.active) return;
      const callId = `${LOCAL_CALL_ID_PREFIX}${this.newId()}`;
      // Only the input tools take a count (operator.key / operator.pointer, ADR-0195).
      const args: Record<string, unknown> = count > 1 && GESTURE_COUNT_TOOLS.has(name) ? { count } : {};
      let response: ToolCallResponse;
      try {
        response = await this.deps.api.toolCall(sessionId, { call_id: callId, name, arguments: args });
      } catch (error) {
        this.log(`tool:${name} request.failed ${describe(error)}`);
        continue;
      }
      this.log(`tool:${name} ${response.status}${response.replayed ? " replayed" : ""}`);
      // Deliberately never spoken — see this method's docstring.
    }
  }

  // -------------------------------------------------------------- a turn

  private async handle(text: string): Promise<void> {
    const sessionId = this.sessionId;
    if (!this.active || !sessionId) return;
    this.turn += 1;
    const turn = this.turn;
    this.patch({ state: "thinking", turn, lastHeard: text, lastError: null });
    await this.turnBody(sessionId, turn, text);
  }

  private async turnBody(sessionId: string, turn: number, text: string): Promise<void> {
    let answer: EventsResponse;
    try {
      answer = await this.deps.api.events(sessionId, [
        { kind: "utterance", t_ms: Math.max(0, Math.round(this.now() - this.t0)), turn, text },
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

/** The real browser ports, read lazily so importing this module on the server is free. */
export function browserLocalModeDeps(api: VoiceSessionApi): LocalModeDeps {
  return {
    api,
    recognition: () => {
      if (typeof window === "undefined") return null;
      const w = window as unknown as { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };
      const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
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
  };
}
