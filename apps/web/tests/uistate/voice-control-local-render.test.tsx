/**
 * "Yerel mod" on the screen (ADR-0173): the switch, the mandatory listening
 * indicator, and the switch's persistence.
 *
 * `VoiceControlView` is a pure function of its props, rendered here with
 * `react-dom/server` like every other Core surface; the preference helpers take an
 * injectable storage, so no window is needed.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import VoiceControlView, { LOCAL_SWITCH_LABEL, type LocalModeViewProps } from "../../app/core/VoiceControlView";
import { LOCAL_VOICE_KEY, type PreferenceStorage, readLocalVoice, writeLocalVoice } from "../../app/core/usePreferences";
import { type LocalModeSnapshot, OFF_SNAPSHOT } from "../../app/lib/voice/localMode";
import { VoiceStore, type VoiceStoreSnapshot } from "../../app/lib/voice/store";

/** The honest pre-rig snapshot, from the store itself (constructing a store builds nothing). */
function voiceSnapshot(): VoiceStoreSnapshot {
  const store = new VoiceStore({
    build: (() => {
      throw new Error("the render test must never build a rig");
    }) as never,
    unloadTarget: null,
  });
  return store.getServerSnapshot();
}

function local(enabled: boolean, snapshot: Partial<LocalModeSnapshot> = {}): LocalModeViewProps {
  return {
    enabled,
    snapshot: { ...OFF_SNAPSHOT, ...snapshot },
    onToggle: () => {},
    onStart: () => {},
    onStop: () => {},
  };
}

function render(localProps?: LocalModeViewProps): string {
  return renderToStaticMarkup(
    <VoiceControlView voice={voiceSnapshot()} onConnect={() => {}} onDisconnect={() => {}} onReconnect={() => {}} local={localProps} />,
  );
}

describe("VoiceControlView: Yerel mod", () => {
  it("draws the switch, off by default, and leaves the paid controls exactly where they were", () => {
    const html = render(local(false));
    expect(html).toContain(LOCAL_SWITCH_LABEL);
    expect(html).toContain("data-voice-local-toggle");
    expect(html).not.toMatch(/data-voice-local-toggle[^>]*checked|checked[^>]*data-voice-local-toggle/);
    expect(html).toContain('data-voice-local="off"');
    expect(html).toContain('data-voice-action="connect"');
    expect(html).not.toContain("data-local-listening");
  });

  it("without the local prop nothing of the local mode is drawn (every earlier caller)", () => {
    const html = render();
    expect(html).not.toContain(LOCAL_SWITCH_LABEL);
    expect(html).toContain('data-voice-action="connect"');
  });

  it("switched on: the local start button replaces the paid connect, so two sessions cannot be opened from here", () => {
    const html = render(local(true));
    expect(html).toMatch(/checked/);
    expect(html).toContain('data-voice-local="on"');
    expect(html).toContain('data-local-action="start"');
    expect(html).toContain("Dinlemeye başla");
    expect(html).not.toContain('data-voice-action="connect"');
    expect(html).toContain('data-local-listening="no"');
    expect(html).toContain("Mikrofon kapalı");
  });

  it("the listening indicator is visible whenever the microphone is open", () => {
    const html = render(local(true, { state: "listening", listening: true, provider: "local-router", sessionId: "s" }));
    expect(html).toContain('data-local-listening="yes"');
    expect(html).toContain("● Dinliyor (mikrofon açık)");
    expect(html).toContain('role="status"');
    expect(html).toContain("local-router");
    expect(html).toContain('data-local-action="stop"');
  });

  it("while speaking the indicator says the microphone is not the open thing", () => {
    const html = render(local(true, { state: "speaking", speaking: true, lastSpoken: "Açıyorum efendim.", lastHeard: "YouTube'u aç" }));
    expect(html).toContain('data-local-listening="no"');
    expect(html).toContain('data-local-speaking="yes"');
    expect(html).toContain("Konuşuyor");
    expect(html).toContain("Söylenen: Açıyorum efendim.");
    expect(html).toContain("Duyulan: YouTube&#x27;u aç");
  });

  it("an unsupported browser gets a disabled start and the reason", () => {
    const html = render(local(true, { state: "unsupported", lastError: "Bu tarayıcıda konuşma tanıma yok; Chrome gerekir." }));
    expect(html).toMatch(/data-local-action="start"[^>]*disabled|disabled[^>]*data-local-action="start"/);
    expect(html).toContain("Chrome gerekir");
  });
});

function memory(): PreferenceStorage & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return { map, getItem: (k) => map.get(k) ?? null, setItem: (k, v) => void map.set(k, v) };
}

describe("Yerel mod switch persistence", () => {
  it("is off until the owner flips it, and the flip survives a reload", () => {
    const storage = memory();
    expect(readLocalVoice(storage)).toBe(false);
    writeLocalVoice(true, storage);
    expect(storage.map.get(LOCAL_VOICE_KEY)).toBe("1");
    expect(readLocalVoice(storage)).toBe(true); // what the next page load reads
    writeLocalVoice(false, storage);
    expect(readLocalVoice(storage)).toBe(false);
  });

  it("a storage that throws never breaks the page (private window, blocked site data)", () => {
    const broken: PreferenceStorage = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    expect(readLocalVoice(broken)).toBe(false);
    expect(() => writeLocalVoice(true, broken)).not.toThrow();
    expect(readLocalVoice(null)).toBe(false);
  });
});
