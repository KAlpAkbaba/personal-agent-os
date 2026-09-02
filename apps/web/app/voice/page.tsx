"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import OwnerGate, { SignOutButton } from "../components/OwnerGate";
import { apiFetch } from "../lib/session";
import { VoiceSessionApi } from "../lib/voice/api";
import {
  BrowserMicrophone,
  BrowserNetworkMonitor,
  RmsSpeechDetector,
  WebAudioPlayback,
  listAudioDevices,
} from "../lib/voice/audio";
import { type ControllerSnapshot, VoiceSessionController } from "../lib/voice/controller";
import { FakeTransport } from "../lib/voice/fake";
import type { AudioDevice } from "../lib/voice/ports";
import {
  type RealtimeTransport,
  type TransportDescriptor,
  TransportConfigError,
} from "../lib/voice/transport";
import { WebRtcTransport } from "../lib/voice/webrtc";

/**
 * /voice — the browser leg of a realtime voice session (M12 track D).
 *
 * Functional, not styled: connect, pick devices, watch the state and the
 * client-measured latencies. The owner judges quality later on a real
 * microphone against a real provider; what this page proves is the wiring.
 *
 * When Cloud Core selects a provider whose transport is `simulated` (the
 * offline simulator, i.e. no real adapter is configured yet) there is no
 * browser media path. The page then drives a scripted fake transport so the
 * session, event, tool-relay and close paths still run against the real API.
 */

const STATE_LABEL: Record<ControllerSnapshot["state"], string> = {
  idle: "Hazır",
  creating: "Oturum oluşturuluyor…",
  connecting: "Bağlanıyor…",
  listening: "Dinliyor",
  speaking: "Konuşuyor",
  tool_running: "Araç çalışıyor",
  interrupted: "Kesildi",
  reconnecting: "Yeniden bağlanıyor…",
  closed: "Kapalı",
  error: "Hata",
};

const LATENCY_LABEL: Array<[keyof ControllerSnapshot["latency"], string]> = [
  ["barge_in_to_stop_ms", "Söze girme → ses kesildi"],
  ["eot_to_first_audio_ms", "Söz bitti → ilk ses"],
  ["mic_to_uplink_ms", "Mikrofon → sağlayıcı duydu"],
  ["tool_preamble_ms", "Araç çağrısı → ön cümle"],
  ["tool_done_to_speech_ms", "Araç bitti → konuşma"],
];

class ScriptedSimulatorTransport extends FakeTransport {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private calls = 0;

  constructor() {
    super({ now: () => performance.now() });
  }

  override async connect(
    ...args: Parameters<FakeTransport["connect"]>
  ): Promise<void> {
    await super.connect(...args);
    this.timer = setTimeout(() => this.demoTurn("Simülatör bağlandı; bu sağlayıcının tarayıcı ses yolu yok."), 800);
  }

  demoTurn(text: string): void {
    const at = performance.now();
    this.emit({ type: "response_started", at });
    this.emit({ type: "response_text", at, text, final: true });
    this.emit({ type: "audio_started", at: at + 120 });
    setTimeout(() => this.emit({ type: "response_done", at: performance.now() }), 1200);
  }

  demoToolCall(): void {
    this.calls += 1;
    this.emit({
      type: "tool_call",
      at: performance.now(),
      callId: `sim-web-${Date.now()}-${this.calls}`,
      name: "clock.now",
      arguments: {},
    });
  }

  override close(reason?: string): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    super.close(reason);
  }
}

function VoiceConsole() {
  const controllerRef = useRef<VoiceSessionController | null>(null);
  const playbackRef = useRef<WebAudioPlayback | null>(null);
  const simulatorRef = useRef<ScriptedSimulatorTransport | null>(null);
  const [snapshot, setSnapshot] = useState<ControllerSnapshot | null>(null);
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [micId, setMicId] = useState<string>("");
  const [speakerId, setSpeakerId] = useState<string>("");
  const [simulated, setSimulated] = useState(false);

  useEffect(() => {
    const playback = new WebAudioPlayback();
    playbackRef.current = playback;
    const controller = new VoiceSessionController({
      api: new VoiceSessionApi(apiFetch),
      transportFactory: (descriptor: TransportDescriptor): RealtimeTransport => {
        if (descriptor.kind === "webrtc") {
          setSimulated(false);
          return new WebRtcTransport();
        }
        if (descriptor.kind === "simulated") {
          setSimulated(true);
          const transport = new ScriptedSimulatorTransport();
          simulatorRef.current = transport;
          return transport;
        }
        throw new TransportConfigError(`tarayıcı ${descriptor.kind} taşıyıcısını açamaz`);
      },
      playback,
      microphone: new BrowserMicrophone(),
      localSpeech: new RmsSpeechDetector(),
      network: new BrowserNetworkMonitor(),
      now: () => performance.now(),
    });
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setSnapshot);
    return () => {
      unsubscribe();
      controller.dispose();
      playback.dispose();
    };
  }, []);

  const refreshDevices = useCallback(async () => {
    setDevices(await listAudioDevices());
  }, []);

  useEffect(() => {
    void refreshDevices();
  }, [refreshDevices]);

  const connect = useCallback(async () => {
    await controllerRef.current?.connect({ deviceId: micId || undefined });
    // Labels are only revealed after a getUserMedia grant.
    void refreshDevices();
  }, [micId, refreshDevices]);

  const disconnect = useCallback(async () => {
    await controllerRef.current?.disconnect();
  }, []);

  const changeMic = useCallback(async (deviceId: string) => {
    setMicId(deviceId);
    if (snapshot && ["listening", "speaking", "tool_running", "interrupted"].includes(snapshot.state)) {
      await controllerRef.current?.switchMicrophone(deviceId);
    }
  }, [snapshot]);

  const changeSpeaker = useCallback(async (deviceId: string) => {
    setSpeakerId(deviceId);
    await playbackRef.current?.setOutputDevice(deviceId);
  }, []);

  const live =
    snapshot !== null &&
    ["listening", "speaking", "tool_running", "interrupted", "reconnecting"].includes(snapshot.state);
  const busy = snapshot?.state === "creating" || snapshot?.state === "connecting";

  return (
    <main>
      <div className="status-row" style={{ borderBottom: "none", paddingBottom: 0 }}>
        <h1 style={{ margin: 0 }}>Sesli Asistan</h1>
        <SignOutButton />
      </div>
      <p className="subtitle">Gerçek zamanlı konuşma — tarayıcı medya bacağı (M12 / D).</p>

      <div className="panel">
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
          <button
            onClick={live ? disconnect : connect}
            disabled={busy || snapshot === null}
            style={{
              padding: "0.6rem 1.2rem",
              borderRadius: 8,
              border: "none",
              background: live ? "var(--fail)" : "var(--accent)",
              color: "#0f1115",
              fontWeight: 600,
              cursor: busy ? "progress" : "pointer",
            }}
          >
            {live ? "Bağlantıyı kes" : busy ? STATE_LABEL[snapshot.state] : "Bağlan"}
          </button>
          <label className="muted">
            Mikrofon{" "}
            <select value={micId} onChange={(e) => changeMic(e.target.value)} aria-label="Mikrofon">
              <option value="">Varsayılan</option>
              {devices
                .filter((d) => d.kind === "audioinput")
                .map((d) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label}
                  </option>
                ))}
            </select>
          </label>
          <label className="muted">
            Hoparlör{" "}
            <select value={speakerId} onChange={(e) => changeSpeaker(e.target.value)} aria-label="Hoparlör">
              <option value="">Varsayılan</option>
              {devices
                .filter((d) => d.kind === "audiooutput")
                .map((d) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label}
                  </option>
                ))}
            </select>
          </label>
          <button onClick={refreshDevices} className="badge unknown" style={{ border: "none", cursor: "pointer", font: "inherit" }}>
            Cihazları yenile
          </button>
        </div>
        {simulated && live && (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            Sunucu simülatör sağlayıcısını seçti: ses yok, kablolama gerçek API üzerinden çalışıyor.{" "}
            <button
              onClick={() => simulatorRef.current?.demoToolCall()}
              style={{ background: "none", border: "1px solid #232734", borderRadius: 8, color: "var(--accent)", cursor: "pointer", padding: "0.2rem 0.6rem" }}
            >
              Demo araç çağrısı (clock.now)
            </button>
          </p>
        )}
      </div>

      {snapshot && (
        <div className="panel">
          <div className="status-row">
            <strong>Durum</strong>
            <span className={`badge ${snapshot.state === "error" ? "fail" : live ? "ok" : "unknown"}`}>
              {STATE_LABEL[snapshot.state]}
            </span>
          </div>
          <div className="status-row">
            <span>Oturum</span>
            <span className="muted">
              {snapshot.sessionId ? `${snapshot.sessionId.slice(0, 8)}… · ${snapshot.provider} · ${snapshot.transport} · bacak ${snapshot.legs}` : "—"}
            </span>
          </div>
          <div className="status-row">
            <span>Tur</span>
            <span className="muted">{snapshot.turn}</span>
          </div>
          <div className="status-row">
            <span>Ağ</span>
            <span className={`badge ${snapshot.online ? "ok" : "fail"}`}>{snapshot.online ? "çevrimiçi" : "çevrimdışı"}</span>
          </div>
          <div className="status-row">
            <span>Olaylar</span>
            <span className="muted">{snapshot.eventsAccepted} kabul · {snapshot.eventsPending} bekliyor</span>
          </div>
          <div className="status-row">
            <span>Duraksama koruması</span>
            <span className="muted">
              {snapshot.hesitation.held} uzatıldı · {snapshot.hesitation.resumed_within_hold} devam etti
              {snapshot.hesitation.last ? ` · son: ${snapshot.hesitation.last}` : ""}
            </span>
          </div>
          {snapshot.toolsRunning.length > 0 && (
            <div className="status-row">
              <span>Çalışan araçlar</span>
              <span className="muted">{snapshot.toolsRunning.join(", ")}</span>
            </div>
          )}
          {snapshot.lastError && (
            <p className="muted" style={{ color: "var(--fail)" }}>{snapshot.lastError}</p>
          )}
        </div>
      )}

      {snapshot && (
        <div className="panel">
          <strong>Gecikmeler (istemci ölçümü)</strong>
          {LATENCY_LABEL.map(([key, label]) => (
            <div className="status-row" key={key}>
              <span>{label}</span>
              <span className="muted">{snapshot.latency[key] ? `${snapshot.latency[key]?.value} ms` : "—"}</span>
            </div>
          ))}
        </div>
      )}

      {snapshot && (snapshot.assistantText || snapshot.ownerText) && (
        <div className="panel">
          {snapshot.ownerText && (
            <p className="muted" style={{ margin: "0 0 0.5rem" }}>Sahip: {snapshot.ownerText}</p>
          )}
          {snapshot.assistantText && <p style={{ margin: 0, lineHeight: 1.5 }}>{snapshot.assistantText}</p>}
        </div>
      )}

      {snapshot && snapshot.sidebandLog.length > 0 && (
        <div className="panel">
          <strong>Yan kanal</strong>
          {snapshot.sidebandLog.map((line, i) => (
            <div className="status-row" key={`${i}-${line}`}>
              <span className="muted">{line}</span>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}

export default function VoicePage() {
  return (
    <OwnerGate>
      <VoiceConsole />
    </OwnerGate>
  );
}
