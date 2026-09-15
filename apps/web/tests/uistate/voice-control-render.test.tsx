/**
 * What the Core's voice surface actually puts on the screen (ADR-0061 §3–§5).
 *
 * `VoiceControlView` and `StateReadout` are pure functions of their props, so
 * — like `AmbientBand` and `EyeControlView` — they are rendered with
 * `react-dom/server` in Node: no browser, no Playwright, no jsdom.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import CoreFallback2D from "../../app/core/CoreFallback2D";
import StateReadout from "../../app/core/StateReadout";
import VoiceControlView from "../../app/core/VoiceControlView";
import { type VoiceOverlay, visualFor } from "../../app/lib/uistate/visual";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { EMPTY_SPEECH, type VoiceUiState } from "../../app/lib/voice/controller";
import { type VoiceStore, type VoiceStoreSnapshot } from "../../app/lib/voice/store";
import { AGENT_IDLE, T0, resetSequence, response } from "./fixtures";

/** A store snapshot as the hook would deliver it, with the controller in `state`. */
function snapshot(state: VoiceUiState, overrides: Partial<VoiceStoreSnapshot> = {}): VoiceStoreSnapshot {
  // The server snapshot is the honest starting shape; only what a test names changes.
  const base = (
    { getServerSnapshot: () => ({}) } as unknown as VoiceStore
  ).getServerSnapshot?.() as VoiceStoreSnapshot | undefined;
  void base;
  return {
    ready: true,
    controller: {
      state,
      sessionId: state === "idle" || state === "closed" ? null : "11111111-2222-4333-8444-555555555555",
      provider: "openai_realtime",
      transport: "webrtc",
      voice: "marin",
      voiceProfile: "arbor",
      micMetrics: {
        false_starts: 0,
        false_barge_ins: 0,
        false_turns: 0,
        confirmed_turns: 0,
        early_mutes: 0,
        early_mute_reverts: 0,
        gate_opens: 0,
        gated_out: 0,
        click_rejects: 0,
        calibrations: 0,
        noise_floor_db: null,
        env: null,
        echo_residual_db: null,
        cancel_noop_errors: 0,
        speech_detected: 0,
        potential_barge_in: 0,
        accepted_owner_interruption: 0,
        rejected_background_speech: 0,
        explicit_stop_command: 0,
        false_interruption: 0,
      },
      turn: 0,
      assistantText: "Bu bir transkript ve Çekirdek'te görünmemeli.",
      ownerText: "",
      lastError: state === "error" ? "Medya bağlantısı kurulamadı: ICE failed" : null,
      lastErrorLines: [],
      contract: null,
      contractNotice: null,
      unavailable: null,
      requestLog: [],
      latency: {},
      latencyDetail: {},
      toolsRunning: [],
      sidebandLog: [],
      narrationCursor: null,
      speech: EMPTY_SPEECH,
      hesitation: { held: 0, resumed_within_hold: 0 },
      online: true,
      eventsAccepted: 0,
      eventsPending: 0,
      legs: 1,
    },
    devices: [
      { deviceId: "mic-1", kind: "audioinput", label: "Masa mikrofonu", groupId: "g1" },
      { deviceId: "spk-1", kind: "audiooutput", label: "Stüdyo hoparlörü", groupId: "g1" },
    ],
    micId: "mic-1",
    speakerId: "spk-1",
    voice: "marin",
    profile: null,
    applied: null,
    simulated: false,
    instances: { rigs: 1, controllers: 1, microphonesOpened: 1, transports: 1, sessionsCreated: 1 },
    ...overrides,
  };
}

function cell(state: VoiceUiState, overrides: Partial<VoiceStoreSnapshot> = {}) {
  return renderToStaticMarkup(
    <VoiceControlView voice={snapshot(state, overrides)} onConnect={vi.fn()} onDisconnect={vi.fn()} onReconnect={vi.fn()} />,
  );
}

describe("the voice cell states facts and offers one control", () => {
  it("disconnected: says so, names the selected devices, offers Bağlan", () => {
    const html = cell("idle");
    expect(html).toContain('data-voice-connection="disconnected"');
    expect(html).toContain("Ses bağlı değil");
    expect(html).toContain("Mikrofon: Masa mikrofonu");
    expect(html).toContain("Hoparlör: Stüdyo hoparlörü");
    expect(html).toContain('data-voice-action="connect"');
    expect(html).toContain(">Bağlan<");
  });

  it("connected: says so, shows the state, offers Bağlantıyı kes", () => {
    const html = cell("listening");
    expect(html).toContain('data-voice-connection="connected"');
    expect(html).toContain("Ses bağlı");
    expect(html).toContain("Dinliyor");
    expect(html).toContain('data-voice-action="disconnect"');
    expect(html).toContain("Sağlayıcı: openai_realtime · webrtc");
  });

  it("closed or failed: reconnecting is offered from here, without leaving /core", () => {
    expect(cell("closed")).toContain('data-voice-action="reconnect"');
    const failed = cell("error");
    expect(failed).toContain('data-voice-action="reconnect"');
    expect(failed).toContain('data-voice-error="yes"');
    expect(failed).toContain("Medya bağlantısı kurulamadı");
  });

  it("connecting: the control is disabled and worded as the state", () => {
    const html = cell("creating");
    expect(html).toContain('data-voice-connection="connecting"');
    expect(html).toContain("Oturum oluşturuluyor…");
    expect(html).toMatch(/<button[^>]*disabled=""/);
  });

  it("noise mode is stated from the profile, and its absence is stated too", () => {
    expect(cell("idle")).toContain("profil yok");
    const html = cell("idle", {
      profile: {
        schema: 1,
        deviceId: "mic-1",
        fingerprint: "fp",
        friendlyName: "Masa mikrofonu",
        inputGainStrategy: "browser",
        agcPreference: "auto",
        noiseSuppressionMode: "auto",
        measuredNoiseFloorDb: null,
        preferredVadSensitivity: "auto",
        lastCalibratedAt: null,
        qualificationScore: null,
        environmentMode: "noisy",
        agcBenchmark: null,
        appliedVoiceIsolation: null,
        appliedSettings: null,
        measuredEchoResidualDb: null,
        learned: { marginDb: 0, onsetMs: 0, echoMarginDb: 0, sessions: 0, updatedAt: null },
      },
    });
    expect(html).toContain("Gürültü modu: Gürültülü ortam");
    expect(html).toContain('data-voice-noise-mode="noisy"');
  });

  it("is not a settings form: no select, no input, no form; diagnostics are a link to /voice", () => {
    const html = cell("listening");
    expect(html).not.toContain("<select");
    expect(html).not.toContain("<input");
    expect(html).not.toContain("<form");
    expect(html).toContain('href="/voice"');
    expect(html).toContain("Tanılama");
  });

  it("never shows the transcript", () => {
    expect(cell("speaking")).not.toContain("Bu bir transkript");
  });

  it("speaking while draining says generation is over and the rest is still playing (ADR-0066)", () => {
    const voice = snapshot("speaking");
    voice.controller = {
      ...voice.controller,
      speech: { ...EMPTY_SPEECH, responseId: "r1", phase: "draining", firstAudioAt: 1300, generationDoneAt: 1800 },
    };
    const html = renderToStaticMarkup(
      <VoiceControlView voice={voice} onConnect={vi.fn()} onDisconnect={vi.fn()} onReconnect={vi.fn()} />,
    );
    expect(html).toContain('data-speech-phase="draining"');
    expect(html).toContain("Konuşuyor");
    expect(html).toContain("üretim bitti, kalan ses çalıyor");
    // The note belongs to speaking only; listening carries no phase words.
    expect(cell("listening")).not.toContain("kalan ses çalıyor");
    expect(cell("listening")).toContain('data-speech-phase="idle"');
  });

  it("before the rig exists (server render) it says the session is not set up yet, and offers nothing enabled", () => {
    const html = cell("idle", { ready: false });
    expect(html).toContain('data-voice-ready="no"');
    expect(html).toContain("henüz kurulmadı");
    expect(html).toMatch(/<button[^>]*disabled=""/);
  });
});

// ------------------------------------------------------------- the readout

function overlay(partial: Partial<VoiceOverlay> & { state: VoiceUiState }): VoiceOverlay {
  return { micLevel: null, outputLevel: null, caption: null, toolLabel: null, lastError: null, ...partial };
}

function intentWith(voice: VoiceOverlay | null) {
  resetSequence();
  return visualFor(applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0), T0, voice);
}

describe("the readout names its source", () => {
  it("a bus intent says the bus", () => {
    const html = renderToStaticMarkup(<StateReadout intent={intentWith(null)} />);
    expect(html).toContain('data-core-source="bus"');
    expect(html).toContain('data-source-line="bus"');
    expect(html).toContain("Kaynak: Cloud Core durum akışı");
  });

  it("a voice-sourced intent says this device's session, and its voice detail", () => {
    const html = renderToStaticMarkup(<StateReadout intent={intentWith(overlay({ state: "listening", micLevel: 0.3 }))} />);
    expect(html).toContain('data-core-source="voice"');
    expect(html).toContain('data-voice-state="listening"');
    expect(html).toContain("Kaynak: bu cihazdaki ses oturumu");
    expect(html).toContain("Mikrofon açık; sahip dinleniyor.");
    expect(html).not.toContain("İlerleme bildirilmedi");
  });

  it("voice connecting is worded as a media leg, not as an unread state stream", () => {
    const html = renderToStaticMarkup(<StateReadout intent={intentWith(overlay({ state: "connecting" }))} />);
    expect(html).toContain("Ses oturumu kuruluyor.");
    expect(html).not.toContain("Durum akışı henüz okunmadı");
  });

  it("speaking shows the caption as the label and nothing of the transcript", () => {
    const html = renderToStaticMarkup(
      <StateReadout intent={intentWith(overlay({ state: "speaking", outputLevel: 0.4, caption: "Araştırma sonuçlarını anlatıyorum…" }))} />,
    );
    expect(html).toContain('data-caption="yes"');
    expect(html).toContain("Araştırma sonuçlarını anlatıyorum…");
    expect(html).not.toContain("data-output-level");
  });

  it("speaking with an unmeasurable output path says so", () => {
    const html = renderToStaticMarkup(<StateReadout intent={intentWith(overlay({ state: "speaking", outputLevel: null }))} />);
    expect(html).toContain('data-output-level="unmeasured"');
    expect(html).toContain("Çıkış seviyesi ölçülemedi.");
  });

  it("interrupted has its own headline", () => {
    const html = renderToStaticMarkup(<StateReadout intent={intentWith(overlay({ state: "interrupted" }))} />);
    expect(html).toContain("Kesildi");
    expect(html).toContain('data-core-kind="interrupted"');
  });

  it("error shows the controller's message", () => {
    const html = renderToStaticMarkup(<StateReadout intent={intentWith(overlay({ state: "error", lastError: "ICE failed" }))} />);
    expect(html).toContain('data-core-kind="error"');
    expect(html).toContain("ICE failed");
  });
});

describe("the 2D core pulses to the measured envelope only", () => {
  it("draws a pulse ring sized by the output level, and none at zero or unmeasured", () => {
    const loud = renderToStaticMarkup(<CoreFallback2D intent={intentWith(overlay({ state: "speaking", outputLevel: 0.6 }))} tier="high" />);
    expect(loud).toContain("core-pulse");
    expect(loud).toContain('data-pulse="0.6"');
    const silent = renderToStaticMarkup(<CoreFallback2D intent={intentWith(overlay({ state: "speaking", outputLevel: 0 }))} tier="high" />);
    expect(silent).toContain('data-core-kind="speaking"');
    expect(silent).not.toContain("core-pulse");
    const unmeasured = renderToStaticMarkup(<CoreFallback2D intent={intentWith(overlay({ state: "speaking", outputLevel: null }))} tier="high" />);
    expect(unmeasured).not.toContain("core-pulse");
  });

  it("interrupted draws no pulse and no breathing", () => {
    const html = renderToStaticMarkup(<CoreFallback2D intent={intentWith(overlay({ state: "interrupted" }))} tier="high" />);
    expect(html).toContain('data-core-kind="interrupted"');
    expect(html).not.toContain("core-pulse");
    expect(html).toContain('data-breathing="no"');
  });

  it("listening from the local session draws the inward ticks like the bus's own listening", () => {
    const html = renderToStaticMarkup(<CoreFallback2D intent={intentWith(overlay({ state: "listening", micLevel: 0.5 }))} tier="high" />);
    expect(html).toContain('data-core-kind="listening"');
    expect(html).toContain("core-inward");
  });

  it("idle and closed leave the bus core exactly as it was", () => {
    const bus = renderToStaticMarkup(<CoreFallback2D intent={intentWith(null)} tier="high" />);
    expect(renderToStaticMarkup(<CoreFallback2D intent={intentWith(overlay({ state: "idle" }))} tier="high" />)).toBe(bus);
    expect(renderToStaticMarkup(<CoreFallback2D intent={intentWith(overlay({ state: "closed" }))} tier="high" />)).toBe(bus);
  });
});
