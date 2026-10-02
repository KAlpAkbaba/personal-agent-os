/**
 * Chrome's on-device Turkish recognition in the local mode, behind a setting that is OFF
 * by default (chrome-on-device-stt; plan: team/plans/chrome-on-device-stt-integration.md).
 *
 * Pinned, with a scripted recogniser and a scripted `available()` / `install()`:
 * - `kapali` (and no setting at all) starts the recogniser exactly as before: neither
 *   `processLocally` nor `phrases` is ever written, nothing is installed, no question;
 * - `acik` + `available` -> `processLocally = true` and the phrase list, set BEFORE start;
 * - `acik` + `downloadable` -> one question; nothing is installed without a yes, and the
 *   yes calls `install()` in the caller's own stack with `processLocally: true`;
 * - unavailable / downloading / a throw / a hang / no API -> today's path and a recorded reason;
 * - `olc` alternates the two legs between recogniser runs, phrases only on the device leg;
 * - every utterance carries the engine of the run that HEARD it;
 * - a device leg Chrome refuses falls back once, from the error handler, and stays there.
 *
 * No test waits on a wall clock: the guard timers are fired by hand.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import {
  FakeCloudCore,
  FakeOnDevice,
  type FakeOnDeviceScript,
  FakeSpeechRecognition,
  FakeSpeechSynthesis,
  fakeUtterance,
} from "../../app/lib/voice/fake";
import { LOCAL_TRANSPORT, LocalVoiceMode, browserLocalModeDeps } from "../../app/lib/voice/localMode";
import { STT_SETTING_KEY } from "../../app/lib/voice/sttSetting";

const tick = async (rounds = 8): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const QUESTION = "Türkçe paketi indirilsin mi?";
const ALIASES = ["çalışma odası", "GMKADIRAKBABA"];

type Phrase = { phrase: string; boost: number };

type SetupOptions = {
  /** undefined = the dep is not given at all (the default). */
  setting?: string;
  /** null = the browser has no `available()` / `install()`. */
  status?: FakeOnDeviceScript<string> | null;
  install?: FakeOnDeviceScript<boolean>;
  /** false = the browser has no `SpeechRecognitionPhrase`. */
  phraseCtor?: boolean;
  sources?: () => Promise<{ deviceAliases?: string[]; capabilityPhrases?: string[] }>;
};

function setup(options: SetupOptions = {}) {
  const core = new FakeCloudCore({
    provider: "local-router",
    transport: LOCAL_TRANSPORT,
    resolveIntents: () => [{ intent: "clock_query", klass: "query", capability: null, tool: "clock.now" }],
    toolResponses: { "clock.now": { result: { speech: "Saat on iki efendim." } } },
  });
  const recognition = new FakeSpeechRecognition();
  const synthesis = new FakeSpeechSynthesis();
  const onDevice = options.status === null ? null : new FakeOnDevice(options.status ?? "available", options.install ?? true);
  const timers: Array<{ fn: () => void; ms: number; cleared: boolean }> = [];
  let ids = 0;
  const mode = new LocalVoiceMode({
    api: new VoiceSessionApi(core.fetcher),
    recognition: () => recognition,
    synthesis: () => synthesis,
    utterance: fakeUtterance,
    now: () => 1000 + ids,
    newId: () => `id${(ids += 1)}`,
    setTimer: (fn, ms) => {
      const timer = { fn, ms, cleared: false };
      timers.push(timer);
      return timer;
    },
    clearTimer: (handle) => {
      (handle as { cleared: boolean }).cleared = true;
    },
    onDevice: () => onDevice,
    phrase: () => (options.phraseCtor === false ? null : (text: string, boost: number): Phrase => ({ phrase: text, boost })),
    ...(options.setting === undefined ? {} : { sttSetting: () => options.setting as never }),
    phraseSources: options.sources ?? (async () => ({ deviceAliases: ALIASES, capabilityPhrases: ["Hesap makinesini aç"] })),
  });
  /** One whole turn: the final, the relay, the spoken answer, the restart. */
  const say = async (text: string): Promise<void> => {
    recognition.final(text);
    await tick();
    synthesis.finish();
    await tick();
  };
  /** The `stt_engine` of every utterance the Cloud Core received, in order. */
  const engines = (): unknown[] =>
    core.requests
      .filter((r) => r.method === "POST" && r.path.endsWith("/events"))
      .flatMap((r) => (r.body as { events: Array<{ kind: string; payload?: Record<string, unknown> }> }).events)
      .filter((e) => e.kind === "utterance")
      .map((e) => e.payload?.stt_engine);
  /** Fire the guard timers that are still armed (a probe or an install that never answered). */
  const fireGuards = async (): Promise<void> => {
    for (const timer of timers.filter((t) => !t.cleared)) timer.fn();
    await tick();
  };
  return { core, recognition, synthesis, onDevice: onDevice as FakeOnDevice, mode, timers, say, engines, fireGuards };
}

const phraseTexts = (value: unknown): string[] => (value as Phrase[]).map((p) => p.phrase);

describe("kapali: the recogniser is started exactly as before", () => {
  it("never writes processLocally or phrases, installs nothing and asks nothing - even when the pack is usable", async () => {
    const { recognition, onDevice, mode, say, engines } = setup({ setting: "kapali", status: "available" });
    await mode.start();
    await say("saat kaç");
    await say("saat kaç");
    expect(recognition.assignments).toEqual([]);
    expect(recognition.startedWith).toEqual([
      { processLocally: undefined, phrases: undefined },
      { processLocally: undefined, phrases: undefined },
      { processLocally: undefined, phrases: undefined },
    ]);
    expect(recognition.lang).toBe("tr-TR");
    expect(recognition.continuous).toBe(true);
    expect(recognition.interimResults).toBe(false);
    expect(onDevice.installCalls).toEqual([]);
    expect(mode.getSnapshot().packQuestion).toBeNull();
    expect(mode.getSnapshot().sttFallback).toBeNull();
    // The one thing it does: a read-only probe, so the engine name is honest. With a usable
    // pack Chrome may hear on-device WITHOUT being asked (Chromium issue 521896368).
    expect(onDevice.availableCalls).toEqual([{ langs: ["tr-TR"], processLocally: true }]);
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor"]);
  });

  it("is the default: with no setting given at all, a usable pack changes nothing", async () => {
    const { recognition, onDevice, mode, say } = setup({ status: "available" });
    await mode.start();
    await say("saat kaç");
    expect(recognition.assignments).toEqual([]);
    expect(recognition.startedWith[0]).toEqual({ processLocally: undefined, phrases: undefined });
    expect(onDevice.installCalls).toEqual([]);
    expect(mode.getSnapshot().sttEngine).not.toBe("chrome-cihaz-ici");
  });

  it("a setting that cannot be read, or is not one of the three values, is kapali", async () => {
    const { recognition, mode } = setup({ setting: "evet", status: "available" });
    await mode.start();
    expect(recognition.assignments).toEqual([]);
    const thrown = setup({ status: "available" });
    const throwing = new LocalVoiceMode({
      api: new VoiceSessionApi(thrown.core.fetcher),
      recognition: () => thrown.recognition,
      synthesis: () => thrown.synthesis,
      utterance: fakeUtterance,
      onDevice: () => thrown.onDevice,
      sttSetting: () => {
        throw new Error("SecurityError");
      },
    });
    await throwing.start();
    expect(thrown.recognition.assignments).toEqual([]);
    expect(thrown.recognition.starts).toBe(1);
  });

  it("does not wait for the probe: a probe that never answers delays nothing", async () => {
    const { recognition, mode, say, engines } = setup({ setting: "kapali", status: "hang" });
    await mode.start();
    expect(mode.getSnapshot().state).toBe("listening");
    expect(recognition.starts).toBe(1);
    await say("saat kaç");
    expect(engines()).toEqual(["bilinmiyor"]); // nobody knows yet, and it says so
  });

  it("names the cloud only when no pack can be in use: no API, unavailable, or not downloaded", async () => {
    for (const status of [null, "unavailable", "downloadable"] as const) {
      const { recognition, onDevice, mode, say, engines } = setup({ setting: "kapali", status });
      await mode.start();
      expect(recognition.starts).toBe(1); // listening already; the probe's answer lands just after
      await tick();
      expect(mode.getSnapshot().sttEngine, String(status)).toBe("chrome-bulut");
      await say("saat kaç");
      expect(engines(), String(status)).toEqual(["chrome-bulut"]);
      expect(recognition.assignments).toEqual([]);
      expect(mode.getSnapshot().packQuestion).toBeNull(); // kapali never asks
      expect(mode.getSnapshot().sttFallback).toBeNull(); // nothing was wanted, so nothing fell back
      if (onDevice) expect(onDevice.installCalls).toEqual([]);
    }
  });
});

describe("acik: on-device when the pack is there", () => {
  it("sets processLocally and the phrase list before the first start, and says which engine heard", async () => {
    const { core, recognition, onDevice, mode, say, engines, timers } = setup({ setting: "acik", status: "available" });
    await mode.start();
    expect(recognition.starts).toBe(1);
    expect(recognition.startedWith[0].processLocally).toBe(true);
    const phrases = phraseTexts(recognition.startedWith[0].phrases);
    for (const alias of ALIASES) expect(phrases).toContain(alias);
    expect(phrases).toContain("ofis");
    expect(phrases).toContain("Hesap makinesini");
    expect(phrases).toContain("aç");
    expect(phrases.length).toBeLessThanOrEqual(64);
    // processLocally first: phrases without it is the `phrases-not-supported` error
    expect(recognition.assignments.map((a) => a.key)).toEqual(["processLocally", "phrases"]);
    expect(onDevice.availableCalls).toEqual([{ langs: ["tr-TR"], processLocally: true }]); // and never `quality`
    expect(onDevice.installCalls).toEqual([]);
    expect(timers.every((t) => t.cleared)).toBe(true); // the probe answered: its guard is disarmed

    await say("saat kaç");
    await say("saat kaç");
    expect(engines()).toEqual(["chrome-cihaz-ici", "chrome-cihaz-ici"]);
    expect(recognition.startedWith.map((s) => s.processLocally)).toEqual([true, true, true]);
    const snap = mode.getSnapshot();
    expect(snap.sttEngine).toBe("chrome-cihaz-ici");
    expect(snap.sttFallback).toBeNull();
    expect(snap.packQuestion).toBeNull();
    // The words stay with the recogniser: not in the log, not in the snapshot, not on the wire.
    const dump = JSON.stringify(snap) + JSON.stringify(core.requests);
    expect(dump).not.toContain("çalışma odası");
    expect(dump).not.toContain("GMKADIRAKBABA");
    // the existing event keeps its shape; the engine is its only payload key
    const posted = core.requests.filter((r) => r.path.endsWith("/events")).map((r) => r.body as { events: unknown[] });
    expect(posted[0].events[0]).toEqual({ kind: "utterance", t_ms: expect.any(Number), turn: 1, text: "saat kaç", payload: { stt_engine: "chrome-cihaz-ici" } });
  });

  it("a Chrome without SpeechRecognitionPhrase hears on-device without phrases", async () => {
    const { recognition, mode, say, engines } = setup({ setting: "acik", status: "available", phraseCtor: false });
    await mode.start();
    expect(recognition.assignments).toEqual([{ key: "processLocally", value: true }]);
    await say("saat kaç");
    expect(engines()).toEqual(["chrome-cihaz-ici"]);
  });

  it("a phrase source that fails or never answers costs the session's names, never the start", async () => {
    const failed = setup({ setting: "acik", status: "available", sources: () => Promise.reject(new Error("HTTP 500")) });
    await failed.mode.start();
    expect(failed.recognition.startedWith[0].processLocally).toBe(true);
    expect(phraseTexts(failed.recognition.startedWith[0].phrases)).toContain("ofis");
    expect(phraseTexts(failed.recognition.startedWith[0].phrases)).not.toContain("GMKADIRAKBABA");

    const hung = setup({ setting: "acik", status: "available", sources: () => new Promise(() => {}) });
    const starting = hung.mode.start();
    await tick();
    expect(hung.recognition.starts).toBe(0);
    await hung.fireGuards();
    await starting;
    expect(hung.recognition.starts).toBe(1);
    expect(phraseTexts(hung.recognition.startedWith[0].phrases)).toContain("ofis");
  });
});

describe("acik + downloadable: nothing is downloaded without a yes", () => {
  it("shows one question and goes on as before; an unanswered question installs nothing", async () => {
    const { recognition, onDevice, mode, say, engines } = setup({ setting: "acik", status: "downloadable" });
    await mode.start();
    const snap = mode.getSnapshot();
    expect(snap.packQuestion).toContain(QUESTION);
    expect(snap.packQuestion).not.toContain("\n"); // one line
    expect(snap.sttFallback).toBe("pack-downloadable");
    expect(snap.state).toBe("listening");
    await say("saat kaç");
    await say("saat kaç");
    expect(onDevice.installCalls).toEqual([]);
    expect(recognition.assignments).toEqual([]);
    expect(engines()).toEqual(["chrome-bulut", "chrome-bulut"]);
    expect(mode.getSnapshot().packQuestion).toContain(QUESTION);
  });

  it("a no installs nothing, removes the question and is recorded", async () => {
    const { recognition, onDevice, mode, say } = setup({ setting: "acik", status: "downloadable" });
    await mode.start();
    mode.answerPackQuestion(false);
    await tick();
    await say("saat kaç");
    expect(onDevice.installCalls).toEqual([]);
    expect(recognition.assignments).toEqual([]);
    expect(mode.getSnapshot().packQuestion).toBeNull();
    expect(mode.getSnapshot().sttFallback).toBe("pack-declined");
    // a second answer has no question to answer
    mode.answerPackQuestion(true);
    await tick();
    expect(onDevice.installCalls).toEqual([]);
  });

  it("an answer with no question pending does nothing - not before start, not when the pack is there, not after stop", async () => {
    const idle = setup({ setting: "acik", status: "downloadable" });
    idle.mode.answerPackQuestion(true);
    expect(idle.onDevice.installCalls).toEqual([]);

    const ready = setup({ setting: "acik", status: "available" });
    await ready.mode.start();
    ready.mode.answerPackQuestion(true);
    expect(ready.onDevice.installCalls).toEqual([]);

    const stopped = setup({ setting: "acik", status: "downloadable" });
    await stopped.mode.start();
    await stopped.mode.stop();
    expect(stopped.mode.getSnapshot().packQuestion).toBeNull();
    stopped.mode.answerPackQuestion(true);
    await tick();
    expect(stopped.onDevice.installCalls).toEqual([]);
  });

  it("a yes installs in the click's own stack, with processLocally, and the NEXT run is on-device", async () => {
    const { recognition, onDevice, mode, say, engines } = setup({ setting: "acik", status: "downloadable", install: true });
    await mode.start();
    mode.answerPackQuestion(true);
    // Synchronously: Chrome's install() consumes the click's user activation, and without
    // `processLocally: true` it resolves false (the MDN example omits it).
    expect(onDevice.installCalls).toEqual([{ langs: ["tr-TR"], processLocally: true }]);
    expect(mode.getSnapshot().packQuestion).toBeNull();
    await tick();
    expect(mode.getSnapshot().sttFallback).toBeNull();
    // the running recogniser is not touched mid-run; the pack is there now, so this run is no longer provably cloud
    expect(recognition.assignments).toEqual([]);
    await say("saat kaç");
    await say("saat kaç");
    expect(engines()).toEqual(["bilinmiyor", "chrome-cihaz-ici"]);
    expect(recognition.startedWith.map((s) => s.processLocally)).toEqual([undefined, true, true]);
    expect(phraseTexts(recognition.startedWith[1].phrases)).toContain("çalışma odası");
    expect(onDevice.installCalls).toHaveLength(1);
  });

  it("an install that is refused, throws or never ends is a recorded fallback", async () => {
    const cases: Array<[FakeOnDeviceScript<boolean>, string]> = [
      [false, "install-refused"],
      [new Error("NotAllowedError"), "install-threw"],
      ["hang", "install-timeout"],
    ];
    for (const [install, reason] of cases) {
      const { recognition, mode, say, engines, fireGuards } = setup({ setting: "acik", status: "downloadable", install });
      await mode.start();
      mode.answerPackQuestion(true);
      await tick();
      await fireGuards();
      expect(mode.getSnapshot().sttFallback, reason).toBe(reason);
      expect(mode.getSnapshot().log).toContain(`stt.fallback ${reason}`);
      await say("saat kaç");
      expect(recognition.assignments).toEqual([]);
      // a download may have begun: the cloud can no longer be named
      expect(engines()).toEqual(["bilinmiyor"]);
    }
  });
});

describe("acik: anything but a usable pack is today's path, with the reason on record", () => {
  const cases: Array<[string, FakeOnDeviceScript<string> | null, string, string]> = [
    ["no API in this browser", null, "api-missing", "chrome-bulut"],
    ["unavailable", "unavailable", "unavailable", "chrome-bulut"],
    ["still downloading", "downloading", "pack-downloading", "bilinmiyor"],
    ["a status nobody documented", "maybe", "status-unknown", "bilinmiyor"],
    ["available() throws", new TypeError("langs"), "probe-threw", "bilinmiyor"],
    ["available() never answers", "hang", "probe-timeout", "bilinmiyor"],
  ];
  for (const [name, status, reason, engine] of cases) {
    it(`${name} -> ${reason}`, async () => {
      const { recognition, onDevice, mode, say, engines, fireGuards } = setup({ setting: "acik", status });
      const starting = mode.start();
      await tick();
      await fireGuards();
      await starting;
      expect(mode.getSnapshot().state).toBe("listening");
      expect(recognition.starts).toBe(1);
      expect(recognition.assignments).toEqual([]);
      expect(mode.getSnapshot().sttFallback).toBe(reason);
      expect(mode.getSnapshot().log).toContain(`stt.fallback ${reason}`);
      expect(mode.getSnapshot().packQuestion).toBeNull();
      await say("saat kaç");
      expect(engines()).toEqual([engine]);
      if (onDevice) expect(onDevice.installCalls).toEqual([]);
    });
  }

  it("a stop that races the probe starts no recogniser", async () => {
    const { recognition, mode, fireGuards } = setup({ setting: "acik", status: "hang" });
    const starting = mode.start();
    await tick();
    await mode.stop();
    await fireGuards();
    await starting;
    expect(recognition.starts).toBe(0);
    expect(mode.getSnapshot().state).toBe("off");
    expect(mode.getSnapshot().listening).toBe(false);
  });
});

describe("olc: the two legs alternate between recogniser runs", () => {
  it("first run is today's path, then device, then back - and each utterance names the leg that heard it", async () => {
    const { recognition, onDevice, mode, say, engines } = setup({ setting: "olc", status: "available" });
    await mode.start();
    expect(recognition.assignments).toEqual([]); // the first run is untouched
    for (let i = 0; i < 4; i += 1) await say("saat kaç");
    expect(engines()).toEqual(["bilinmiyor", "chrome-cihaz-ici", "bilinmiyor", "chrome-cihaz-ici"]);
    expect(recognition.startedWith.map((s) => s.processLocally)).toEqual([undefined, true, false, true, false]);
    // phrases ride only with processLocally: on the other leg they are an error in Chrome
    const withPhrases = recognition.startedWith.map((s) => Array.isArray(s.phrases) && s.phrases.length > 0);
    expect(withPhrases).toEqual([false, true, false, true, false]);
    // ...and a second pass keeps alternating (a sequence, not a count)
    for (let i = 0; i < 2; i += 1) await say("saat kaç");
    expect(engines().slice(4)).toEqual(["bilinmiyor", "chrome-cihaz-ici"]);
    expect(onDevice.installCalls).toEqual([]);
    expect(onDevice.availableCalls).toHaveLength(1);
  });

  it("the engine is the one of the run that HEARD the sentence, not of the run alive when it is posted", async () => {
    const { recognition, synthesis, mode, engines } = setup({ setting: "olc", status: "available" });
    await mode.start();
    // Two finals from the same (untouched) run; the second waits behind the first one's answer.
    recognition.final("saat kaç");
    recognition.final("saat kaç");
    await tick();
    synthesis.finish(); // the first answer ends: the recogniser restarts, now on the device leg
    await tick();
    expect(recognition.startedWith.map((s) => s.processLocally)).toEqual([undefined, true]);
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor"]);
  });

  it("without a usable pack olc is today's path with the reason, and never alternates", async () => {
    const { recognition, mode, say, engines } = setup({ setting: "olc", status: "unavailable" });
    await mode.start();
    for (let i = 0; i < 3; i += 1) await say("saat kaç");
    expect(recognition.assignments).toEqual([]);
    expect(engines()).toEqual(["chrome-bulut", "chrome-bulut", "chrome-bulut"]);
    expect(mode.getSnapshot().sttFallback).toBe("unavailable");
  });
});

describe("a device leg Chrome refuses falls back once and stays there", () => {
  it("language-not-supported (no onend follows) restarts on today's path from the error handler", async () => {
    const { core, recognition, mode, say, engines } = setup({ setting: "acik", status: "available" });
    await mode.start();
    recognition.refuse("language-not-supported");
    await tick();
    expect(mode.getSnapshot().state).toBe("listening");
    expect(mode.getSnapshot().listening).toBe(true);
    expect(recognition.running).toBe(true); // not deaf
    expect(recognition.starts).toBe(2);
    expect(recognition.startedWith[1].processLocally).toBe(false);
    expect(recognition.startedWith[1].phrases).toEqual([]);
    expect(mode.getSnapshot().sttFallback).toBe("error-language-not-supported");
    expect(mode.getSnapshot().log).toContain("stt.fallback error-language-not-supported");
    expect(core.closed).toBeNull();
    await say("saat kaç");
    await say("saat kaç");
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor"]);
    expect(recognition.startedWith.slice(1).map((s) => s.processLocally)).toEqual([false, false, false]);
  });

  it("phrases-not-supported, service-not-allowed and not-allowed on the device leg are fallbacks, not a denied microphone", async () => {
    for (const code of ["phrases-not-supported", "service-not-allowed", "not-allowed"]) {
      const { core, recognition, mode } = setup({ setting: "acik", status: "available" });
      await mode.start();
      recognition.fail(code);
      recognition.endOnItsOwn();
      await tick();
      expect(mode.getSnapshot().state, code).toBe("listening");
      expect(mode.getSnapshot().sttFallback).toBe(`error-${code}`);
      expect(core.closed).toBeNull();
      expect(recognition.running).toBe(true);
      expect(recognition.processLocally).toBe(false);
      // ...but once on today's path, a denied microphone is what it always was
      recognition.fail("not-allowed");
      await tick();
      expect(mode.getSnapshot().state).toBe("error");
      expect(mode.getSnapshot().lastError).toBe("Mikrofon izni verilmedi.");
      expect(core.closed).toBe("client_closed");
    }
  });

  it("a start() that throws on the device leg is not swallowed into a deaf 'listening'", async () => {
    const { recognition, mode, say, engines } = setup({ setting: "acik", status: "available" });
    recognition.failNextStart = new DOMException("blocked by policy", "NotAllowedError");
    await mode.start();
    expect(recognition.running).toBe(true);
    expect(recognition.startedWith).toEqual([{ processLocally: false, phrases: [] }]);
    expect(mode.getSnapshot().sttFallback).toBe("start-NotAllowedError");
    expect(mode.getSnapshot().listening).toBe(true);
    await say("saat kaç");
    expect(engines()).toEqual(["bilinmiyor"]);
  });

  it("olc stops alternating after a fallback", async () => {
    const { recognition, mode, say, engines } = setup({ setting: "olc", status: "available" });
    await mode.start();
    await say("saat kaç"); // today's leg heard it; the restart is the device leg
    expect(recognition.startedWith[1].processLocally).toBe(true);
    recognition.refuse("language-not-supported");
    await tick();
    for (let i = 0; i < 3; i += 1) await say("saat kaç");
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor", "bilinmiyor", "bilinmiyor"]);
    expect(recognition.startedWith.slice(2).map((s) => s.processLocally)).toEqual([false, false, false, false]);
  });

  it("kapali is untouched by all of this: language-not-supported is today's error line, not a fallback", async () => {
    const { recognition, mode } = setup({ setting: "kapali", status: "available" });
    await mode.start();
    recognition.fail("language-not-supported");
    await tick();
    expect(mode.getSnapshot().lastError).toBe("Tanıma hatası: language-not-supported");
    expect(mode.getSnapshot().sttFallback).toBeNull();
    expect(recognition.assignments).toEqual([]);
    expect(recognition.starts).toBe(1);
  });
});

const browserDeps = () => browserLocalModeDeps(new VoiceSessionApi(new FakeCloudCore().fetcher));
/** A recogniser constructor from before Chrome 139: no `available`, no `install`. */
function OldRecognition(): void {}

describe("browserLocalModeDeps: the one place Chrome's statics are read", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("with no window (the server render) there is no API, no phrase constructor, and the setting is kapali", () => {
    vi.stubGlobal("window", undefined);
    const d = browserDeps();
    expect(d.onDevice?.()).toBeNull();
    expect(d.phrase?.()).toBeNull();
    expect(d.sttSetting?.()).toBe("kapali");
  });

  it("a Chrome older than 139 (a constructor without the statics) has no on-device API", () => {
    vi.stubGlobal("window", { webkitSpeechRecognition: OldRecognition, localStorage: { getItem: () => null } });
    const d = browserDeps();
    expect(d.onDevice?.()).toBeNull();
    expect(d.phrase?.()).toBeNull();
    expect(d.sttSetting?.()).toBe("kapali");
  });

  it("finds available/install on the constructor, the phrase constructor, and the setting in localStorage", async () => {
    const asked: unknown[] = [];
    // The constructor with Chrome 139's two statics. Both read `this`: called detached they
    // would throw, as Chrome's "Illegal invocation" does.
    const Recognition = Object.assign(function Recognition() {}, {
      tag: "static-this",
      async available(this: { tag: string }, options: unknown): Promise<string> {
        asked.push(["available", options, this.tag]);
        return "downloadable";
      },
      async install(this: { tag: string }, options: unknown): Promise<boolean> {
        asked.push(["install", options, this.tag]);
        return true;
      },
    });
    class Phrase {
      constructor(
        readonly phrase: string,
        readonly boost: number,
      ) {}
    }
    vi.stubGlobal("window", {
      SpeechRecognition: Recognition,
      SpeechRecognitionPhrase: Phrase,
      localStorage: { getItem: (key: string) => (key === STT_SETTING_KEY ? "olc" : null) },
    });
    const d = browserDeps();
    const api = d.onDevice?.();
    const options = { langs: ["tr-TR"], processLocally: true };
    expect(await api?.available(options)).toBe("downloadable");
    expect(await api?.install(options)).toBe(true);
    expect(asked).toEqual([
      ["available", options, "static-this"],
      ["install", options, "static-this"],
    ]);
    const made = d.phrase?.()?.("ofis", 2);
    expect(made).toBeInstanceOf(Phrase);
    expect(made).toEqual({ phrase: "ofis", boost: 2 });
    expect(d.sttSetting?.()).toBe("olc");
  });
});
