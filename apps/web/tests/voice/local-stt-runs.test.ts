/**
 * The on-device setting where Chrome's timing is not the fake's (chrome-on-device-stt, second
 * pass; the first is `local-stt-engine.test.ts`).
 *
 * Pinned here, each one RED under its own mutation:
 * - the REAL phrase sources (`browserLocalModeDeps(...).phraseSources`, over a stubbed
 *   `fetch`): the session's device aliases and the capability sentences reach the
 *   recogniser's phrase list, and either list failing alone costs only itself;
 * - Chrome's `end` arrives AFTER `stop()`: nothing is written on a recogniser that is still
 *   started, and a final that arrives in that gap keeps the name of the run that heard it;
 * - `olc` alternates per utterance even when the turn speaks nothing (the recogniser is
 *   then never paused, so the run has to be ended for the other leg);
 * - a probe, a phrase source or an install that answers after its session is gone - stopped,
 *   or stopped and started again - writes nothing.
 *
 * No test waits on a wall clock.
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
import type { PhraseSources } from "../../app/lib/voice/sttPhrases";

const tick = async (rounds = 8): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

type Phrase = { phrase: string; boost: number };
const phraseTexts = (value: unknown): string[] => (value as Phrase[]).map((p) => p.phrase);

const ALIASES = ["çalışma odası", "GMKADIRAKBABA"];

type SetupOptions = {
  /** A function is read at every `start()`: a second session can have another setting. */
  setting: string | (() => string);
  status?: FakeOnDeviceScript<string>;
  install?: FakeOnDeviceScript<boolean>;
  sources?: () => Promise<PhraseSources>;
  /** The tool answers with no `speech`: the turn says nothing and the recogniser is never paused. */
  silent?: boolean;
  /** Chrome's order: `end` is delivered by the test, after `stop()` returned. */
  delayEnd?: boolean;
};

function setup(options: SetupOptions) {
  const core = new FakeCloudCore({
    provider: "local-router",
    transport: LOCAL_TRANSPORT,
    resolveIntents: () => [{ intent: "clock_query", klass: "query", capability: null, tool: "clock.now" }],
    ...(options.silent ? {} : { toolResponses: { "clock.now": { result: { speech: "Saat on iki efendim." } } } }),
  });
  const recognition = new FakeSpeechRecognition();
  recognition.delayEnd = options.delayEnd === true;
  const synthesis = new FakeSpeechSynthesis();
  const onDevice = new FakeOnDevice(options.status ?? "available", options.install ?? true);
  let ids = 0;
  const setting = options.setting;
  const mode = new LocalVoiceMode({
    api: new VoiceSessionApi(core.fetcher),
    recognition: () => recognition,
    synthesis: () => synthesis,
    utterance: fakeUtterance,
    now: () => 1000 + ids,
    newId: () => `id${(ids += 1)}`,
    // The guards are never fired here: an answer is either given by the test or never comes.
    setTimer: () => null,
    clearTimer: () => {},
    onDevice: () => onDevice,
    phrase: () => (text: string, boost: number): Phrase => ({ phrase: text, boost }),
    sttSetting: () => (typeof setting === "function" ? setting() : setting) as never,
    phraseSources: options.sources ?? (async () => ({ deviceAliases: ALIASES, capabilityPhrases: ["Hesap makinesini aç"] })),
  });
  /** One whole SPOKEN turn: the final, the relay, the spoken answer, the restart. */
  const say = async (text: string): Promise<void> => {
    recognition.final(text);
    await tick();
    synthesis.finish();
    await tick();
  };
  /** One whole SILENT turn: the final, the relay, a tool answer with nothing to say. */
  const hear = async (text: string): Promise<void> => {
    recognition.final(text);
    await tick();
  };
  /**
   * Stop, and let the next `start()` open a session again: the fake has ONE session id and
   * stays closed (410) once closed, where the real Cloud Core mints a new session.
   */
  const stopForRestart = async (): Promise<void> => {
    await mode.stop();
    core.closed = null;
  };
  const engines = (): unknown[] =>
    core.requests
      .filter((r) => r.method === "POST" && r.path.endsWith("/events"))
      .flatMap((r) => (r.body as { events: Array<{ kind: string; payload?: Record<string, unknown> }> }).events)
      .filter((e) => e.kind === "utterance")
      .map((e) => e.payload?.stt_engine);
  return { core, recognition, synthesis, onDevice, mode, say, hear, stopForRestart, engines };
}

// ------------------------------------------------------- the real sources

type Route = { status: number; body: unknown } | "network";

/** A `fetch` for the two lists the phrase sources read; anything else is a test bug. */
function stubFetch(routes: { devices: Route; capabilities: Route }): string[] {
  const asked: string[] = [];
  vi.stubGlobal("fetch", async (input: unknown): Promise<Response> => {
    const url = String(input);
    asked.push(url);
    const route = url.endsWith("/v1/devices") ? routes.devices : url.endsWith("/v1/voice/capabilities") ? routes.capabilities : null;
    if (route === null) throw new Error(`unexpected fetch ${url}`);
    if (route === "network") throw new TypeError("Failed to fetch");
    return new Response(JSON.stringify(route.body), { status: route.status, headers: { "Content-Type": "application/json" } });
  });
  return asked;
}

const DEVICES: Route = {
  status: 200,
  body: {
    devices: [
      { device_id: "d1", name: "MAIL", aliases: ["çalışma odası", "  "] },
      { device_id: "d2", name: "HOME", aliases: ["GMKADIRAKBABA"] },
      { device_id: "d3", name: "no aliases at all" },
    ],
  },
};
const CAPABILITIES: Route = {
  status: 200,
  body: {
    capabilities: [
      { name: "app.open", family: "apps", family_tr: "Uygulamalar", summary: "", phrases: ["Hesap makinesini aç", "Not defterini açsana"] },
      { name: "clock.now", family: "clock", family_tr: "Saat", summary: "", phrases: ["Saat kaç"] },
    ],
    families: [],
    speech: "",
  },
};
const FAILED: Route = { status: 500, body: { detail: "boom" } };

const browserSources = (): Promise<PhraseSources> => {
  const sources = browserLocalModeDeps(new VoiceSessionApi(new FakeCloudCore().fetcher)).phraseSources;
  if (!sources) throw new Error("browserLocalModeDeps gives no phraseSources");
  return sources();
};

describe("browserLocalModeDeps().phraseSources: the session's names, read from the Cloud Core", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads the device aliases from /v1/devices and the sentences from /v1/voice/capabilities", async () => {
    const asked = stubFetch({ devices: DEVICES, capabilities: CAPABILITIES });
    const got = await browserSources();
    expect(got.deviceAliases).toEqual(["çalışma odası", "GMKADIRAKBABA"]); // the blank alias is dropped
    expect(got.capabilityPhrases).toEqual(["Hesap makinesini aç", "Not defterini açsana", "Saat kaç"]);
    expect(asked.map((url) => url.replace(/^.*\/v1\//, "/v1/")).toSorted()).toEqual(["/v1/devices", "/v1/voice/capabilities"]);
  });

  it("through the real deps, the aliases and the application names reach the recogniser's phrase list", async () => {
    stubFetch({ devices: DEVICES, capabilities: CAPABILITIES });
    const core = new FakeCloudCore({ provider: "local-router", transport: LOCAL_TRANSPORT });
    const recognition = new FakeSpeechRecognition();
    const mode = new LocalVoiceMode({
      ...browserLocalModeDeps(new VoiceSessionApi(core.fetcher)),
      recognition: () => recognition,
      synthesis: () => new FakeSpeechSynthesis(),
      utterance: fakeUtterance,
      onDevice: () => new FakeOnDevice("available"),
      phrase: () => (text: string, boost: number): Phrase => ({ phrase: text, boost }),
      sttSetting: () => "acik",
    });
    await mode.start();
    expect(recognition.startedWith).toHaveLength(1);
    expect(recognition.startedWith[0].processLocally).toBe(true);
    const phrases = phraseTexts(recognition.startedWith[0].phrases);
    expect(phrases.slice(0, 2)).toEqual(["çalışma odası", "GMKADIRAKBABA"]); // first: the cap can never cut them
    expect(phrases).toContain("Hesap makinesini");
    expect(phrases).toContain("Not defterini");
    expect(phrases).not.toContain("Saat"); // "kaç" is not an open verb
    await mode.stop();
  });

  it("the device list failing alone costs the aliases, not the application names", async () => {
    for (const devices of [FAILED, "network"] as const) {
      stubFetch({ devices, capabilities: CAPABILITIES });
      const got = await browserSources();
      expect(got.deviceAliases, String(devices)).toEqual([]);
      expect(got.capabilityPhrases, String(devices)).toEqual(["Hesap makinesini aç", "Not defterini açsana", "Saat kaç"]);
    }
  });

  it("the capability list failing alone costs the application names, not the aliases", async () => {
    for (const capabilities of [FAILED, { status: 404, body: {} }, "network"] as const) {
      stubFetch({ devices: DEVICES, capabilities });
      const got = await browserSources();
      expect(got.deviceAliases, JSON.stringify(capabilities)).toEqual(["çalışma odası", "GMKADIRAKBABA"]);
      expect(got.capabilityPhrases, JSON.stringify(capabilities)).toEqual([]);
    }
  });
});

// ------------------------------------------------- `end` after `stop()`

describe("Chrome's end arrives after stop(): the leg changes only between runs", () => {
  it("writes nothing on a recogniser that is still started, and a late final keeps the old leg's name", async () => {
    const { recognition, synthesis, mode, engines } = setup({ setting: "olc", delayEnd: true });
    await mode.start();
    expect(recognition.startedWith).toEqual([{ processLocally: undefined, phrases: undefined }]);

    recognition.final("saat kaç"); // heard by today's leg; the next run is to be the device's
    await tick();
    expect(recognition.endPending).toBe(true); // stopped for the answer; Chrome has not said `end` yet
    synthesis.finish();
    await tick();
    // The answer is over and the mode wants to listen again - on a run Chrome has not ended.
    expect(recognition.assignments).toEqual([]);
    expect(recognition.starts).toBe(1);
    expect(mode.getSnapshot().sttEngine).toBe("bilinmiyor");

    recognition.final("saat kaç"); // what the old run had still heard arrives before its `end`
    await tick();
    synthesis.finish();
    await tick();
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor"]);
    expect(recognition.assignments).toEqual([]);

    recognition.deliverEnd(); // now the run is over: the next one is the device leg
    await tick();
    expect(recognition.assignments.map((a) => a.key)).toEqual(["processLocally", "phrases"]);
    expect(recognition.startedWith.map((s) => s.processLocally)).toEqual([undefined, true]);
    expect(mode.getSnapshot().sttEngine).toBe("chrome-cihaz-ici");
    expect(mode.getSnapshot().listening).toBe(true);

    recognition.final("saat kaç");
    await tick();
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor", "chrome-cihaz-ici"]);
  });
});

// ------------------------------------------------------ a silent turn

describe("olc alternates per utterance even when the turn speaks nothing", () => {
  it("ends the run after a silent turn, so the next sentence is heard by the other leg", async () => {
    const { recognition, synthesis, mode, hear, engines } = setup({ setting: "olc", silent: true });
    await mode.start();
    for (let i = 0; i < 4; i += 1) await hear("saat kaç");
    expect(synthesis.spoken).toHaveLength(0);
    expect(engines()).toEqual(["bilinmiyor", "chrome-cihaz-ici", "bilinmiyor", "chrome-cihaz-ici"]);
    expect(recognition.startedWith.map((s) => s.processLocally)).toEqual([undefined, true, false, true, false]);
    expect(mode.getSnapshot().state).toBe("listening");
    expect(mode.getSnapshot().listening).toBe(true);
    expect(recognition.running).toBe(true);
  });

  it("does so with Chrome's late end too, and never writes on the run it is ending", async () => {
    const { recognition, mode, engines } = setup({ setting: "olc", silent: true, delayEnd: true });
    await mode.start();
    for (let i = 0; i < 3; i += 1) {
      const written = recognition.assignments.length;
      recognition.final("saat kaç");
      await tick();
      expect(recognition.endPending, `turn ${i + 1}`).toBe(true);
      expect(recognition.assignments.length).toBe(written); // nothing while it is still started
      recognition.deliverEnd();
      await tick();
      expect(mode.getSnapshot().listening).toBe(true);
    }
    expect(engines()).toEqual(["bilinmiyor", "chrome-cihaz-ici", "bilinmiyor"]);
    expect(recognition.startedWith.map((s) => s.processLocally)).toEqual([undefined, true, false, true]);
  });

  it("two sentences from one run are both that run's; the leg changes once, after them", async () => {
    const { recognition, mode, engines } = setup({ setting: "olc", silent: true });
    await mode.start();
    recognition.final("saat kaç");
    recognition.final("saat kaç");
    await tick();
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor"]);
    expect(recognition.startedWith.map((s) => s.processLocally)).toEqual([undefined, true]);
  });

  it("nothing else changes: kapali, acik and an olc without a pack never stop the recogniser after a silent turn", async () => {
    const cases: Array<[string, string, unknown]> = [
      ["kapali", "available", "bilinmiyor"],
      ["acik", "available", "chrome-cihaz-ici"],
      ["olc", "unavailable", "chrome-bulut"],
    ];
    for (const [setting, status, engine] of cases) {
      const { recognition, mode, hear, engines } = setup({ setting, status, silent: true });
      await mode.start();
      for (let i = 0; i < 3; i += 1) await hear("saat kaç");
      expect(recognition.stops, setting).toBe(0);
      expect(recognition.starts, setting).toBe(1);
      expect(engines(), setting).toEqual([engine, engine, engine]);
    }
  });
});

// --------------------------------------------------- answers that come late

describe("an answer that arrives after its session is gone writes nothing", () => {
  it("kapali: the probe answers after stop", async () => {
    const probe = deferred<string>();
    const { mode } = setup({ setting: "kapali", status: () => probe.promise });
    await mode.start();
    await mode.stop();
    await tick();
    const before = mode.getSnapshot();
    probe.resolve("unavailable");
    await tick();
    expect(mode.getSnapshot()).toBe(before); // not one patch
    expect(before.sttEngine).toBe("bilinmiyor");
  });

  it("kapali: the first session's probe answers inside the second session", async () => {
    const first = deferred<string>();
    const { mode, say, stopForRestart, engines } = setup({
      setting: "kapali",
      status: (call) => (call === 0 ? first.promise : Promise.resolve("available")),
    });
    await mode.start();
    await stopForRestart();
    await mode.start();
    await tick();
    const before = mode.getSnapshot();
    expect(before.sttEngine).toBe("bilinmiyor"); // the pack is there: Chrome may use it unasked
    first.resolve("unavailable"); // the old answer must not make this session say "cloud"
    await tick();
    expect(mode.getSnapshot()).toBe(before);
    await say("saat kaç");
    await say("saat kaç");
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor"]);
  });

  it("acik: the phrase sources answer after stop - no device leg, no recogniser start", async () => {
    const names = deferred<PhraseSources>();
    const { recognition, mode } = setup({ setting: "acik", sources: () => names.promise });
    const starting = mode.start();
    await tick();
    expect(recognition.starts).toBe(0); // waiting for the names
    await mode.stop();
    await tick();
    const before = mode.getSnapshot();
    names.resolve({ deviceAliases: ALIASES });
    await starting;
    await tick();
    expect(mode.getSnapshot()).toBe(before);
    expect(before.log).not.toContain("stt.device ready phrases=11");
    expect(before.log.some((line) => line.startsWith("stt.device"))).toBe(false);
    expect(recognition.starts).toBe(0);
    expect(recognition.assignments).toEqual([]);
  });

  it("acik: the first session's phrase sources answer inside a second session that has no pack", async () => {
    const names = deferred<PhraseSources>();
    let setting = "acik";
    const { recognition, mode, say, stopForRestart, engines } = setup({
      setting: () => setting,
      status: (call) => Promise.resolve(call === 0 ? "available" : "unavailable"),
      sources: () => names.promise,
    });
    const starting = mode.start();
    await tick();
    await stopForRestart();
    setting = "olc";
    await mode.start();
    expect(mode.getSnapshot().sttFallback).toBe("unavailable");
    names.resolve({ deviceAliases: ALIASES }); // the old session's device leg must not become this one's
    await starting;
    await tick();
    for (let i = 0; i < 3; i += 1) await say("saat kaç");
    expect(engines()).toEqual(["chrome-bulut", "chrome-bulut", "chrome-bulut"]);
    expect(recognition.assignments).toEqual([]);
    expect(mode.getSnapshot().sttFallback).toBe("unavailable");
    expect(mode.getSnapshot().log.some((line) => line.startsWith("stt.device"))).toBe(false);
  });

  it("acik: the install answers after stop", async () => {
    const install = deferred<boolean>();
    const { recognition, onDevice, mode } = setup({ setting: "acik", status: "downloadable", install: () => install.promise });
    await mode.start();
    mode.answerPackQuestion(true);
    expect(onDevice.installCalls).toHaveLength(1);
    await mode.stop();
    await tick();
    const before = mode.getSnapshot();
    install.resolve(true);
    await tick();
    expect(mode.getSnapshot()).toBe(before);
    expect(before.log).not.toContain("stt.pack.installed");
    expect(recognition.assignments).toEqual([]);
  });

  it("acik: the first session's install answers inside the second session, whose own fallback stays", async () => {
    const install = deferred<boolean>();
    const { recognition, onDevice, mode, say, stopForRestart, engines } = setup({
      setting: "acik",
      // What Chrome answers while a download is under way.
      status: (call) => Promise.resolve(call === 0 ? "downloadable" : "downloading"),
      install: () => install.promise,
    });
    await mode.start();
    mode.answerPackQuestion(true);
    await stopForRestart();
    await mode.start();
    await tick();
    const before = mode.getSnapshot();
    expect(before.sttFallback).toBe("pack-downloading");
    install.resolve(true);
    await tick();
    expect(mode.getSnapshot()).toBe(before);
    await say("saat kaç");
    await say("saat kaç");
    expect(engines()).toEqual(["bilinmiyor", "bilinmiyor"]);
    expect(recognition.assignments).toEqual([]);
    expect(mode.getSnapshot().sttFallback).toBe("pack-downloading");
    expect(mode.getSnapshot().log).not.toContain("stt.pack.installed");
    expect(onDevice.installCalls).toHaveLength(1);
  });
});
