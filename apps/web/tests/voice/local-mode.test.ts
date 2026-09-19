/**
 * "Yerel mod" (ADR-0173), the web half, over the in-memory relay and scripted
 * browser speech ports.
 *
 * Pinned: a FINAL transcript becomes exactly one `utterance` events POST with that
 * text; each tool the router named becomes one tool-calls POST with EMPTY arguments
 * and a `local-` call_id; the tool's `speech` is spoken (tr-TR voice); an unresolved
 * sentence is answered "Anlayamadım efendim." and counted; the recogniser is stopped
 * while the assistant speaks and restarted after; a final arriving mid-speech cancels
 * the speech (barge-in); interim results are ignored; no WebRTC leg is opened and no
 * credential secret is read; the session is closed on stop.
 */

import { describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import {
  FakeCloudCore,
  type FakeCloudCoreOptions,
  FakeSpeechRecognition,
  FakeSpeechSynthesis,
  fakeUtterance,
} from "../../app/lib/voice/fake";
import {
  LOCAL_CALL_ID_PREFIX,
  LOCAL_TRANSPORT,
  LocalVoiceMode,
  NOT_UNDERSTOOD_TR,
  TOOL_FAILED_TR,
  UNSUPPORTED_TR,
  pickTurkishVoice,
  speechGuardMs,
  speechOf,
  toolOf,
} from "../../app/lib/voice/localMode";

const tick = async (rounds = 8): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

/** The deterministic router, as far as these tests need it. */
function router(text: string): Array<Record<string, unknown>> {
  const lower = text.toLowerCase();
  if (lower.includes("youtube")) return [{ intent: "mission_start", klass: "action", capability: "operator.mission", tool: "operator.mission" }];
  if (lower.includes("neler yapabilirsin")) return [{ intent: "capabilities_query", klass: "query", capability: null, tool: "assistant.capabilities" }];
  if (lower.includes("saat kaç")) return [{ intent: "clock_query", klass: "query", capability: null, tool: "clock.now" }];
  if (lower.includes("göz açık")) return [{ intent: "explain", klass: "query", capability: null, tool: null }];
  return [{ intent: "none", klass: "query", capability: null, tool: null }];
}

function setup(options: FakeCloudCoreOptions = {}, extra: { synthesis?: FakeSpeechSynthesis | null; recognition?: FakeSpeechRecognition | null } = {}) {
  const core = new FakeCloudCore({ provider: "local-router", transport: LOCAL_TRANSPORT, resolveIntents: router, ...options });
  const recognition = extra.recognition === undefined ? new FakeSpeechRecognition() : extra.recognition;
  const synthesis = extra.synthesis === undefined ? new FakeSpeechSynthesis() : extra.synthesis;
  let ids = 0;
  // The speech hang guard's timer, fired by hand: no test here waits on a wall clock.
  const timers: Array<{ fn: () => void; ms: number; cleared: boolean }> = [];
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
  });
  return { core, recognition: recognition as FakeSpeechRecognition, synthesis: synthesis as FakeSpeechSynthesis, mode, timers };
}

const posts = (core: FakeCloudCore, suffix: string) =>
  core.requests.filter((r) => r.method === "POST" && r.path.endsWith(suffix));

describe("Yerel mod: a final transcript becomes the relay's own calls", () => {
  it("creates a text session with no wire voice and opens the recogniser as tr-TR continuous", async () => {
    const { core, recognition, mode } = setup();
    await mode.start();
    const create = posts(core, "/v1/voice/realtime/sessions")[0];
    expect(create.body).toEqual({ client_kind: "web", transport: LOCAL_TRANSPORT, language: "tr-TR" });
    expect(recognition.lang).toBe("tr-TR");
    expect(recognition.continuous).toBe(true);
    expect(recognition.interimResults).toBe(false);
    expect(recognition.starts).toBe(1);
    const snap = mode.getSnapshot();
    expect(snap.state).toBe("listening");
    expect(snap.listening).toBe(true); // the visible indicator's truth
    expect(snap.provider).toBe("local-router");
    expect(snap.sessionId).toBe("11111111-2222-4333-8444-555555555555");
  });

  it("posts exactly one utterance event, one tool call per named tool with {} and a local- id, then speaks result.speech", async () => {
    const { core, recognition, synthesis, mode } = setup({
      toolResponses: { "operator.mission": { result: { speech: "YouTube'u açıyorum efendim." } } },
    });
    await mode.start();
    recognition.final("YouTube'u aç");
    await tick();

    const events = posts(core, "/events");
    expect(events).toHaveLength(1);
    expect(events[0].body).toEqual({
      events: [{ kind: "utterance", t_ms: expect.any(Number), turn: 1, text: "YouTube'u aç" }],
    });
    const calls = posts(core, "/tool-calls");
    expect(calls).toHaveLength(1);
    const body = calls[0].body as { call_id: string; name: string; arguments: Record<string, unknown> };
    expect(body.name).toBe("operator.mission");
    expect(body.arguments).toEqual({});
    expect(body.call_id.startsWith(LOCAL_CALL_ID_PREFIX)).toBe(true);
    expect(body.call_id).toMatch(/^[A-Za-z0-9_.:-]+$/); // ToolCallRequest.call_id

    expect(synthesis.texts()).toEqual(["YouTube'u açıyorum efendim."]);
    expect(synthesis.spoken[0].lang).toBe("tr-TR");
    expect(synthesis.spoken[0].voice?.lang).toBe("tr-TR");
    expect(mode.getSnapshot().state).toBe("speaking");
    expect(mode.getSnapshot().lastSpoken).toBe("YouTube'u açıyorum efendim.");
    // The log carries kinds and ids, never the owner's words.
    expect(mode.getSnapshot().log.join("\n")).not.toContain("YouTube");
    expect(mode.getSnapshot().log).toContain("tool:operator.mission succeeded");
  });

  it("a query's tool (no capability) is called the same way", async () => {
    const { core, synthesis, recognition, mode } = setup({
      toolResponses: { "clock.now": { result: { speech: "Saat on iki efendim." } } },
    });
    await mode.start();
    recognition.final("saat kaç");
    await tick();
    const calls = posts(core, "/tool-calls");
    expect(calls.map((c) => (c.body as { name: string }).name)).toEqual(["clock.now"]);
    expect(synthesis.texts()).toEqual(["Saat on iki efendim."]);
  });

  it("says Anlayamadım efendim. for a sentence the router did not resolve, posts no tool call, and counts it", async () => {
    const { core, synthesis, recognition, mode } = setup();
    await mode.start();
    recognition.final("bu cümleyi kimse anlamaz");
    await tick();
    expect(posts(core, "/events")).toHaveLength(1);
    expect(posts(core, "/tool-calls")).toHaveLength(0);
    expect(synthesis.texts()).toEqual([NOT_UNDERSTOOD_TR]);
    expect(mode.getSnapshot().unresolved).toBe(1);
    expect(mode.getSnapshot().log).toContain("unresolved turn=1");
    // ...and an EXPLAIN question, which only a model could route, is the same case.
    synthesis.finish();
    await tick();
    recognition.final("göz açık mı");
    await tick();
    expect(posts(core, "/tool-calls")).toHaveLength(0);
    expect(synthesis.texts()).toEqual([NOT_UNDERSTOOD_TR, NOT_UNDERSTOOD_TR]);
    expect(mode.getSnapshot().unresolved).toBe(2);
  });

  it("a failed tool is spoken as an honest failure, never as an answer", async () => {
    const { synthesis, recognition, mode } = setup({
      toolResponses: {
        "assistant.capabilities": { status: "failed", result: undefined, error: { error_class: "validation_error", message: "argument 'x'" } },
      },
    });
    await mode.start();
    recognition.final("neler yapabilirsin");
    await tick();
    expect(synthesis.texts()).toEqual([TOOL_FAILED_TR]);
    expect(mode.getSnapshot().log).toContain("tool:assistant.capabilities failed");
  });

  it("speaks the relay's own say frame (a clarification) instead of guessing", async () => {
    const { core, synthesis, recognition, mode } = setup();
    await mode.start();
    core.queueSideband("say", { text: "Hangisini kastettiniz efendim?", turn: 1, purpose: "clarification" });
    recognition.final("bu cümleyi kimse anlamaz");
    await tick();
    expect(synthesis.texts()).toEqual(["Hangisini kastettiniz efendim?"]);
    expect(mode.getSnapshot().unresolved).toBe(0);
  });
});

describe("Yerel mod: listening, speaking and barging in", () => {
  it("stops the recogniser while the assistant speaks and restarts it when the speech ends", async () => {
    const { synthesis, recognition, mode } = setup({
      toolResponses: { "operator.mission": { result: { speech: "Açıyorum." } } },
    });
    await mode.start();
    expect(recognition.running).toBe(true);
    recognition.final("YouTube'u aç");
    await tick();
    // speaking: the recogniser is stopped, the indicator says the microphone is closed
    expect(mode.getSnapshot().state).toBe("speaking");
    expect(recognition.running).toBe(false);
    expect(recognition.stops).toBe(1);
    expect(mode.getSnapshot().listening).toBe(false);
    expect(mode.getSnapshot().speaking).toBe(true);
    // the browser finishes the utterance: listening again
    synthesis.finish();
    await tick();
    expect(recognition.running).toBe(true);
    expect(recognition.starts).toBe(2);
    expect(mode.getSnapshot().state).toBe("listening");
    expect(mode.getSnapshot().listening).toBe(true);
  });

  it("a final transcript arriving mid-speech cancels the speech (barge-in) and is handled next", async () => {
    const { core, synthesis, recognition, mode } = setup({
      toolResponses: {
        "operator.mission": { result: { speech: "Uzun bir cümle söylüyorum efendim." } },
        "clock.now": { result: { speech: "Saat on iki." } },
      },
    });
    await mode.start();
    recognition.final("YouTube'u aç");
    await tick();
    expect(mode.getSnapshot().speaking).toBe(true);
    // Chrome may still deliver a final that was in flight when we stopped it.
    recognition.running = true;
    recognition.final("saat kaç");
    await tick();
    expect(synthesis.cancels).toBe(1);
    expect(mode.getSnapshot().log).toContain("barge_in");
    expect(posts(core, "/events")).toHaveLength(2);
    expect(synthesis.texts()).toEqual(["Uzun bir cümle söylüyorum efendim.", "Saat on iki."]);
    expect(mode.getSnapshot().turn).toBe(2);
  });

  it("ignores interim results and restarts a recogniser that ended on its own", async () => {
    const { core, recognition, mode } = setup();
    await mode.start();
    recognition.interim("you");
    recognition.interim("youtube");
    await tick();
    expect(posts(core, "/events")).toHaveLength(0);
    recognition.endOnItsOwn();
    expect(recognition.running).toBe(true);
    expect(recognition.starts).toBe(2);
    expect(mode.getSnapshot().listening).toBe(true);
  });

  it("stop aborts the recogniser, cancels speech, closes the session and clears the indicator", async () => {
    const { core, synthesis, recognition, mode } = setup({
      toolResponses: { "operator.mission": { result: { speech: "Açıyorum." } } },
    });
    await mode.start();
    recognition.final("YouTube'u aç");
    await tick();
    await mode.stop();
    expect(recognition.aborts).toBe(1);
    expect(synthesis.cancels).toBe(1);
    expect(core.closed).toBe("client_closed");
    const snap = mode.getSnapshot();
    expect(snap.state).toBe("off");
    expect(snap.listening).toBe(false);
    expect(snap.sessionId).toBeNull();
    // a late final after stop does nothing
    recognition.running = true;
    recognition.final("saat kaç");
    await tick();
    expect(posts(core, "/events")).toHaveLength(1);
  });

  it("a speech whose end never arrives is bounded: the guard ends the wait and listening resumes", async () => {
    const { synthesis, recognition, mode, timers } = setup({
      toolResponses: { "operator.mission": { result: { speech: "Açıyorum." } } },
    });
    await mode.start();
    recognition.final("YouTube'u aç");
    await tick();
    expect(mode.getSnapshot().speaking).toBe(true);
    expect(recognition.running).toBe(false);
    // Chrome never fires onend (its long-utterance cut-off). The guard does.
    expect(timers).toHaveLength(1);
    expect(timers[0].ms).toBe(speechGuardMs("Açıyorum."));
    timers[0].fn();
    await tick();
    expect(mode.getSnapshot().log).toContain("speak.guard_fired");
    expect(mode.getSnapshot().speaking).toBe(false);
    expect(recognition.running).toBe(true);
    expect(mode.getSnapshot().state).toBe("listening");
    // ...and a speech that DID end clears its guard, so it can never fire into the next turn.
    recognition.final("YouTube'u aç");
    await tick();
    synthesis.finish();
    await tick();
    expect(timers).toHaveLength(2);
    expect(timers[1].cleared).toBe(true);
  });

  it("a denied microphone is fatal AND closes the server session (it never expires by itself)", async () => {
    const { core, recognition, mode } = setup();
    await mode.start();
    recognition.fail("not-allowed");
    await tick();
    expect(mode.getSnapshot().state).toBe("error");
    expect(mode.getSnapshot().lastError).toBe("Mikrofon izni verilmedi.");
    expect(mode.getSnapshot().listening).toBe(false);
    expect(core.closed).toBe("client_closed");
    expect(recognition.aborts).toBe(1);
  });

  it("a harmless recogniser error (no-speech) changes nothing and closes nothing", async () => {
    const { core, recognition, mode } = setup();
    await mode.start();
    recognition.fail("no-speech");
    await tick();
    expect(mode.getSnapshot().state).toBe("listening");
    expect(core.closed).toBeNull();
  });

  it("the page going away closes the session with its own reason", async () => {
    const { core, mode } = setup();
    mode.closeOnUnload(); // nothing open: nothing sent
    expect(core.requests).toHaveLength(0);
    await mode.start();
    mode.closeOnUnload();
    await tick();
    expect(core.closed).toBe("page_unload");
  });

  it("a recogniser that restarts mid-turn does not repaint the state as listening", async () => {
    const { core, recognition, mode } = setup();
    await mode.start();
    core.failNext("/events", 500);
    const states: string[] = [];
    mode.subscribe(() => states.push(mode.getSnapshot().state));
    recognition.final("saat kaç");
    recognition.endOnItsOwn(); // Chrome's own end, while the turn is still in flight
    expect(mode.getSnapshot().state).toBe("thinking");
    expect(mode.getSnapshot().listening).toBe(true);
    await tick();
    // the failed post is reported, never spoken as an answer, and listening goes on
    expect(mode.getSnapshot().state).toBe("listening");
    expect(mode.getSnapshot().lastError).toContain("HTTP 500");
    expect(states).toContain("thinking");
  });

  it("a session the server has closed (410) ends the mode instead of posting into the void", async () => {
    const { core, recognition, mode } = setup();
    await mode.start();
    core.failNext("/events", 410, { detail: { error_class: "validation_error", details: { state: "closed" } } });
    recognition.final("saat kaç");
    await tick();
    expect(mode.getSnapshot().state).toBe("error");
    expect(mode.getSnapshot().listening).toBe(false);
    expect(recognition.running).toBe(false);
  });

  it("stop forgets what was heard and said", async () => {
    const { recognition, mode } = setup({
      toolResponses: { "operator.mission": { result: { speech: "Açıyorum." } } },
    });
    await mode.start();
    recognition.final("YouTube'u aç");
    await tick();
    expect(mode.getSnapshot().lastHeard).toBe("YouTube'u aç");
    await mode.stop();
    expect(mode.getSnapshot().lastHeard).toBe("");
    expect(mode.getSnapshot().lastSpoken).toBe("");
  });

  it("a browser without SpeechRecognition is told so, and no session is created", async () => {
    const { core, mode } = setup({}, { recognition: null });
    await mode.start();
    expect(mode.getSnapshot().state).toBe("unsupported");
    expect(mode.getSnapshot().lastError).toBe(UNSUPPORTED_TR);
    expect(core.requests).toHaveLength(0);
  });

  it("without speechSynthesis the answer is logged and listening resumes at once", async () => {
    const { recognition, mode } = setup(
      { toolResponses: { "operator.mission": { result: { speech: "Açıyorum." } } } },
      { synthesis: null },
    );
    await mode.start();
    recognition.final("YouTube'u aç");
    await tick();
    expect(mode.getSnapshot().lastSpoken).toBe("Açıyorum.");
    expect(mode.getSnapshot().state).toBe("listening");
    expect(recognition.running).toBe(true);
  });

  it("never opens a media leg: no credential secret is needed and none is read", async () => {
    const { core, mode } = setup();
    await mode.start();
    const create = core.requests.find((r) => r.method === "POST" && r.path === "/v1/voice/realtime/sessions");
    expect(create).toBeDefined();
    // the mode's snapshot and log never carry a credential-shaped value
    const dump = JSON.stringify(mode.getSnapshot());
    expect(dump).not.toContain("ephemeral-");
    expect(dump).not.toContain("secret");
  });
});

describe("Yerel mod helpers", () => {
  it("toolOf prefers the router's tool and falls back to an action's capability", () => {
    expect(toolOf({ tool: "clock.now", capability: null })).toBe("clock.now");
    expect(toolOf({ capability: "eye.disable" })).toBe("eye.disable");
    expect(toolOf({ tool: null, capability: null })).toBeNull();
    expect(toolOf({})).toBeNull();
  });

  it("speechOf: the handler's sentence, the failure's own sentence, or the honest line", () => {
    const base = { call_id: "c", name: "n", long_running: false, replayed: false } as const;
    expect(speechOf({ ...base, status: "succeeded", result: { speech: "Tamam." } })).toBe("Tamam.");
    expect(speechOf({ ...base, status: "succeeded", result: { ok: true } })).toBeNull();
    expect(speechOf({ ...base, status: "failed", error: { error_class: "x", speech: "Olmadı efendim." } })).toBe("Olmadı efendim.");
    expect(speechOf({ ...base, status: "failed", error: { error_class: "x" } })).toBe(TOOL_FAILED_TR);
    expect(speechOf({ ...base, status: "running", preamble: "Bakıyorum." })).toBe("Bakıyorum.");
    expect(speechOf({ ...base, status: "needs_clarification", result: { speech: "Hangisi?" } })).toBe("Hangisi?");
  });

  it("pickTurkishVoice prefers an exact tr-TR tag, then any Turkish, else none", () => {
    const en = { lang: "en-US", name: "English" };
    const tr = { lang: "tr", name: "Türkçe" };
    const trTR = { lang: "tr-TR", name: "Türkçe TR" };
    expect(pickTurkishVoice([en, tr, trTR])).toBe(trTR);
    expect(pickTurkishVoice([en, tr])).toBe(tr);
    expect(pickTurkishVoice([en])).toBeNull();
    expect(pickTurkishVoice([])).toBeNull();
  });
});

/** The eye router entries the deterministic router really returns for these sentences. */
function eyeRouter(text: string): Array<Record<string, unknown>> {
  const lower = text.toLowerCase();
  if (lower.includes("kamerayı aç"))
    return [{ intent: "eye_enable", klass: "action", capability: "eye.enable", tool: "eye.enable" }];
  if (lower.includes("kamerayı kapat"))
    return [{ intent: "eye_disable", klass: "action", capability: "eye.disable", tool: "eye.disable" }];
  return [{ intent: "none", klass: "query", capability: null, tool: null }];
}

describe("Yerel mod: the camera is this tab's, so the local action runs before the call", () => {
  function eyeSetup() {
    const ran: Array<{ name: string; args: Record<string, unknown> }> = [];
    const core = new FakeCloudCore({
      provider: "local-router",
      transport: LOCAL_TRANSPORT,
      resolveIntents: eyeRouter,
      toolResponses: {
        "eye.enable": { result: { speech: "Kamerayı açtım efendim." } },
        "eye.disable": { result: { speech: "Kamerayı kapattım efendim." } },
      },
    });
    const recognition = new FakeSpeechRecognition();
    let ids = 0;
    const mode = new LocalVoiceMode({
      api: new VoiceSessionApi(core.fetcher),
      recognition: () => recognition,
      synthesis: () => new FakeSpeechSynthesis(),
      utterance: fakeUtterance,
      now: () => 1000 + ids,
      newId: () => `id${(ids += 1)}`,
      setTimer: (fn, ms) => ({ fn, ms }),
      clearTimer: () => {},
      localActions: {
        async run(name, args) {
          ran.push({ name, args });
          if (name === "eye.enable")
            return { local: { state: "ACTIVE", running: true, camera_label: "Integrated Camera", error_class: null, observed_at: "2026-09-20T00:00:00Z", changed: true, media_track_ready_state: "live", action_trace: ["requested", "active"] } };
          if (name === "eye.disable")
            return { local: { state: "DISABLED", running: false, camera_label: null, error_class: null, observed_at: "2026-09-20T00:00:00Z", changed: true, media_track_ready_state: "ended", action_trace: ["requested", "disabled"] } };
          return null;
        },
      },
    });
    return { core, recognition, mode, ran };
  }

  it("opens the camera in this tab and relays what it observed", async () => {
    const { core, recognition, mode, ran } = eyeSetup();
    await mode.start();
    recognition.final("Kamerayı aç");
    await tick();

    expect(ran.map((r) => r.name)).toEqual(["eye.enable"]);
    expect(ran[0].args.utterance).toBe("Kamerayı aç");
    const calls = posts(core, "/tool-calls");
    expect(calls).toHaveLength(1);
    const body = calls[0].body as { name: string; arguments: Record<string, unknown> };
    expect(body.name).toBe("eye.enable");
    const observed = body.arguments.observed_after as { local: Record<string, unknown> };
    expect(observed.local.state).toBe("ACTIVE");
    expect(observed.local.media_track_ready_state).toBe("live");
  });

  it("closes the camera before the server is told it closed", async () => {
    const { core, recognition, mode, ran } = eyeSetup();
    await mode.start();
    recognition.final("Kamerayı kapat");
    await tick();

    expect(ran.map((r) => r.name)).toEqual(["eye.disable"]);
    const body = posts(core, "/tool-calls")[0].body as { arguments: Record<string, unknown> };
    const observed = body.arguments.observed_after as { local: Record<string, unknown> };
    expect(observed.local.state).toBe("DISABLED");
  });

  it("leaves every other tool's arguments empty", async () => {
    const { core, recognition, mode, ran } = eyeSetup();
    await mode.start();
    recognition.final("bir şey");
    await tick();
    expect(ran).toEqual([]);
    expect(posts(core, "/tool-calls")).toHaveLength(0);
  });
});
