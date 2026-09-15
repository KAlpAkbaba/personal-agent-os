/**
 * B20 req 235: "no provider" is a condition, not an error message.
 *
 * The Cloud Core answers 503 with its own taxonomy when there is no voice to be had — a
 * real adapter with no key, an optional local dependency that is not installed, no
 * provider for the capability at all, or every provider failing. The client treated all of
 * it as "the create call failed": the page printed
 * `Oturum oluşturulamadı: POST /v1/voice/realtime/sessions: HTTP 503` and offered a button
 * labelled "Bağlan", which is an invitation to press it until the owner gives up. A key
 * that is missing is missing until somebody adds one, and the only somebody is the owner,
 * and not through this page — the constitution keeps credentials in the DPAPI store.
 *
 * What is pinned: the condition is named, said in Turkish with what the owner can do, and
 * the difference between "not configured" and "temporarily down" reaches the one control
 * that should obey it.
 */
import { describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";

const tick = async (rounds = 6): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

/** The realtime routes' own 503 body: `VoiceError.to_dict()` under FastAPI's `detail`. */
function voiceError(errorClass: string, message: string): unknown {
  return { detail: { error_class: errorClass, message, provider: "openai", retryable: false } };
}

async function setup() {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => new FakeTransport({ now: scheduler.now }),
    playback: new FakePlayback(scheduler.now, () => {}),
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    log: () => {},
  });
  return { controller, core, snap: () => controller.getSnapshot() };
}

describe("a Cloud Core with no voice provider", () => {
  it("names the condition and says who can change it, instead of printing a status line", async () => {
    const t = await setup();
    t.core.failNext(
      "/sessions",
      503,
      voiceError("provider_auth_missing", "no api key configured for openai"),
    );

    await t.controller.connect();
    await tick();

    const snap = t.snap();
    expect(snap.unavailable).toEqual({
      errorClass: "provider_auth_missing",
      message: "Ses sağlayıcısının anahtarı tanımlı değil.",
      remedy: "Anahtarı yalnızca siz ekleyebilirsiniz (scripts/secret-store.ps1).",
      retryable: false,
    });
    // What a person reads is the sentence, not the transport.
    expect(snap.lastError).not.toContain("HTTP 503");
    expect(snap.lastError).toContain("anahtarı tanımlı değil");
    // And the session did not open: every "is this live" guard must still say no.
    expect(snap.state).toBe("error");
  });

  it("separates a provider that is DOWN from one that is not configured", async () => {
    const down = await setup();
    down.core.failNext("/sessions", 503, voiceError("dependency_unavailable", "upstream 502"));
    await down.controller.connect();
    await tick();
    expect(down.snap().unavailable?.retryable).toBe(true);

    const missing = await setup();
    missing.core.failNext("/sessions", 503, voiceError("optional_dependency_missing", "no faster-whisper"));
    await missing.controller.connect();
    await tick();
    expect(missing.snap().unavailable?.retryable).toBe(false);
    expect(missing.snap().unavailable?.message).toBe("Yerel ses bileşeni kurulu değil.");
  });

  it("leaves an ordinary failure as an ordinary failure", async () => {
    // A 500, or a 503 that is not one of the server's unavailable classes, is a fault -
    // the owner should see it as one and retrying it is reasonable.
    const t = await setup();
    t.core.failNext("/sessions", 500, { detail: { error_class: "internal_bug", message: "boom" } });

    await t.controller.connect();
    await tick();

    expect(t.snap().unavailable).toBeNull();
    expect(t.snap().lastError).toContain("Oturum oluşturulamadı");
    expect(t.snap().state).toBe("error");
  });

  it("clears the condition when a session can be created again", async () => {
    const t = await setup();
    t.core.failNext("/sessions", 503, voiceError("dependency_unavailable", "upstream 502"));
    await t.controller.connect();
    await tick();
    expect(t.snap().unavailable).not.toBeNull();

    await t.controller.connect();
    await tick();

    expect(t.snap().unavailable).toBeNull();
    expect(t.snap().state).toBe("listening");
  });

  it("still reports an unknown 503 class as an ordinary failure rather than inventing a sentence", async () => {
    const t = await setup();
    t.core.failNext("/sessions", 503, voiceError("some_new_class", "who knows"));

    await t.controller.connect();
    await tick();

    expect(t.snap().unavailable).toBeNull();
    expect(t.snap().lastError).toContain("Oturum oluşturulamadı");
  });
});
