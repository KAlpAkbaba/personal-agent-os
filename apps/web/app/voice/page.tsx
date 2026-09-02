"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import OwnerGate, { SignOutButton } from "../components/OwnerGate";
import { apiFetch } from "../lib/session";
import { VoiceSessionApi } from "../lib/voice/api";
import {
  BrowserMicrophone,
  BrowserNetworkMonitor,
  type GatedDetectorSnapshot,
  GatedSpeechDetector,
  WebAudioPlayback,
  WebAudioUplinkShaper,
  listAudioDevices,
  measureNoiseFloor,
} from "../lib/voice/audio";
import type { EnvironmentClass, EnvironmentMode } from "../lib/voice/calibration";
import { type ControllerSnapshot, VoiceSessionController } from "../lib/voice/controller";
import { PassthroughDenoiser } from "../lib/voice/denoiser";
import { FakeTransport } from "../lib/voice/fake";
import type { AppliedInputSettings, AudioDevice } from "../lib/voice/ports";
import {
  type AgcPreference,
  type DeviceIdentity,
  LocalStorageProfileStore,
  type MicrophoneProfile,
  VOICE_CANDIDATES,
  type VoiceChoice,
  constraintsFor,
  defaultProfile,
  effectiveAgc,
  fingerprintDevice,
  loadVoiceChoice,
  normalizeVoiceChoice,
  saveVoiceChoice,
  scoreBenchmarkSample,
} from "../lib/voice/profile";
import {
  type RealtimeTransport,
  type TransportDescriptor,
  TransportConfigError,
} from "../lib/voice/transport";
import type { UplinkShaper } from "../lib/voice/uplink";
import { WebRtcTransport } from "../lib/voice/webrtc";

/**
 * /voice — the browser leg of a realtime voice session (M12 track D).
 *
 * Functional, not styled: connect, pick devices, watch the state and the
 * client-measured latencies. The owner judges quality later on a real
 * microphone against a real provider; what this page proves is the wiring.
 *
 * ADR-0044 adds the microphone/noise layer: browser processing requested and
 * read back, a calibrated local speech gate, per-device MicrophoneProfiles,
 * owner-facing environment modes and a "Tanılama" view for the real-device
 * measurements. The normal view stays a voice page, not a dashboard.
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

const MODE_LABEL: Record<EnvironmentMode, string> = {
  auto: "Otomatik",
  quiet: "Sessiz ortam",
  noisy: "Gürültülü ortam",
  very_noisy: "Çok gürültülü ortam",
};

const ENVIRONMENT_LABEL: Record<EnvironmentClass, string> = {
  quiet: "Sessiz",
  normal: "Normal",
  noisy: "Gürültülü",
  very_noisy: "Çok gürültülü",
};

const VOICE_LABEL: Record<VoiceChoice, string> = { marin: "Marin", cedar: "Cedar" };

const SUPPRESSION_LABEL: Record<MicrophoneProfile["noiseSuppressionMode"], string> = {
  auto: "Otomatik",
  browser: "Tarayıcı",
  off: "Kapalı",
};

/** Attenuation depth when the profile opts into the gated uplink shaper. */
const GATED_ATTENUATION_DB = 9;

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

type AudioRig = {
  playback: WebAudioPlayback;
  microphone: BrowserMicrophone;
  detector: GatedSpeechDetector;
  store: LocalStorageProfileStore;
};

const smallButton: React.CSSProperties = {
  background: "none",
  border: "1px solid #232734",
  borderRadius: 8,
  color: "var(--accent)",
  cursor: "pointer",
  padding: "0.2rem 0.6rem",
  font: "inherit",
};

function VoiceConsole() {
  const controllerRef = useRef<VoiceSessionController | null>(null);
  const rigRef = useRef<AudioRig | null>(null);
  const simulatorRef = useRef<ScriptedSimulatorTransport | null>(null);
  const profileRef = useRef<MicrophoneProfile | null>(null);
  const [snapshot, setSnapshot] = useState<ControllerSnapshot | null>(null);
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [micId, setMicId] = useState<string>("");
  const [speakerId, setSpeakerId] = useState<string>("");
  const [simulated, setSimulated] = useState(false);
  const [profile, setProfile] = useState<MicrophoneProfile | null>(null);
  const [mic, setMic] = useState<GatedDetectorSnapshot | null>(null);
  const [applied, setApplied] = useState<AppliedInputSettings | null>(null);
  const [diagnostics, setDiagnostics] = useState(false);
  const [voice, setVoice] = useState<VoiceChoice>("marin");
  const [benchmark, setBenchmark] = useState<"idle" | "running" | "done" | "failed">("idle");
  const [copied, setCopied] = useState(false);

  /** Persist + publish a profile change (the ref is what the audio rig reads). */
  const commitProfile = useCallback((next: MicrophoneProfile) => {
    profileRef.current = next;
    setProfile(next);
    rigRef.current?.store.save(next);
  }, []);

  const resolveProfile = useCallback((identity: DeviceIdentity): MicrophoneProfile => {
    const store = rigRef.current?.store;
    const fingerprint = fingerprintDevice(identity.label, identity.groupId);
    const found = store?.load(fingerprint) ?? null;
    const next = found ? { ...found, deviceId: identity.deviceId || found.deviceId } : defaultProfile(identity);
    profileRef.current = next;
    setProfile(next);
    return next;
  }, []);

  useEffect(() => {
    const store = new LocalStorageProfileStore();
    const playback = new WebAudioPlayback();
    let shaper: UplinkShaper | null = null;
    const microphone = new BrowserMicrophone({
      constraintsFor: () => (profileRef.current ? constraintsFor(profileRef.current) : {}),
      denoiser: new PassthroughDenoiser(),
      uplinkFor: () => {
        shaper = profileRef.current?.inputGainStrategy === "gated_attenuation" ? new WebAudioUplinkShaper() : null;
        return shaper;
      },
    });
    const detector = new GatedSpeechDetector({
      playbackActive: () => playback.playing,
      mode: () => profileRef.current?.environmentMode ?? "auto",
      sensitivity: () => profileRef.current?.preferredVadSensitivity ?? "auto",
      attenuationDb: () => (profileRef.current?.inputGainStrategy === "gated_attenuation" ? GATED_ATTENUATION_DB : 0),
      uplink: () => shaper,
      analysisStream: () => microphone.raw,
    });
    detector.onCalibration((c) => {
      const readBack = microphone.applied;
      // The default device only reveals its identity after the grant.
      const current =
        profileRef.current ??
        (readBack ? resolveProfile({ deviceId: readBack.deviceId ?? "", label: readBack.label, groupId: readBack.groupId ?? "" }) : null);
      if (!current) return;
      commitProfile({
        ...current,
        measuredNoiseFloorDb: c.noise_floor_db,
        lastCalibratedAt: new Date().toISOString(),
        appliedVoiceIsolation: readBack?.voiceIsolation ?? current.appliedVoiceIsolation,
        friendlyName: readBack?.label || current.friendlyName,
      });
    });
    rigRef.current = { playback, microphone, detector, store };
    setVoice(loadVoiceChoice());

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
      microphone,
      localSpeech: detector,
      network: new BrowserNetworkMonitor(),
      now: () => performance.now(),
    });
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setSnapshot);
    return () => {
      unsubscribe();
      controller.dispose();
      detector.stop();
      playback.dispose();
    };
  }, [commitProfile, resolveProfile]);

  const refreshDevices = useCallback(async () => {
    setDevices(await listAudioDevices());
  }, []);

  useEffect(() => {
    void refreshDevices();
  }, [refreshDevices]);

  // The selected device's profile (labels are only known after a grant).
  useEffect(() => {
    if (!micId) return;
    const device = devices.find((d) => d.kind === "audioinput" && d.deviceId === micId);
    if (device && device.label) resolveProfile(device);
  }, [micId, devices, resolveProfile]);

  const live =
    snapshot !== null &&
    ["listening", "speaking", "tool_running", "interrupted", "reconnecting"].includes(snapshot.state);
  const busy = snapshot?.state === "creating" || snapshot?.state === "connecting";

  // Live level / gate meter while a session is open.
  useEffect(() => {
    if (!live) {
      setMic(null);
      return;
    }
    const timer = setInterval(() => {
      setMic(rigRef.current?.detector.snapshot() ?? null);
      setApplied(rigRef.current?.microphone.applied ?? null);
    }, 100);
    return () => clearInterval(timer);
  }, [live]);

  const connect = useCallback(async () => {
    await controllerRef.current?.connect({ deviceId: micId || undefined, voice });
    // Labels are only revealed after a getUserMedia grant.
    void refreshDevices();
    const readBack = rigRef.current?.microphone.applied ?? null;
    setApplied(readBack);
    if (readBack && !profileRef.current) {
      resolveProfile({ deviceId: readBack.deviceId ?? "", label: readBack.label, groupId: readBack.groupId ?? "" });
    }
  }, [micId, voice, refreshDevices, resolveProfile]);

  const disconnect = useCallback(async () => {
    await controllerRef.current?.disconnect();
  }, []);

  const changeMic = useCallback(async (deviceId: string) => {
    setMicId(deviceId);
    const device = devices.find((d) => d.kind === "audioinput" && d.deviceId === deviceId);
    if (device) resolveProfile(device);
    if (snapshot && ["listening", "speaking", "tool_running", "interrupted"].includes(snapshot.state)) {
      // A new device: its own profile, its own calibration (the detector recalibrates on start).
      await controllerRef.current?.switchMicrophone(deviceId);
    }
  }, [snapshot, devices, resolveProfile]);

  const changeSpeaker = useCallback(async (deviceId: string) => {
    setSpeakerId(deviceId);
    await rigRef.current?.playback.setOutputDevice(deviceId);
  }, []);

  const changeVoice = useCallback((value: string) => {
    const choice = normalizeVoiceChoice(value);
    setVoice(choice);
    saveVoiceChoice(choice);
  }, []);

  const changeMode = useCallback((mode: EnvironmentMode) => {
    const current = profileRef.current;
    if (!current) return;
    commitProfile({ ...current, environmentMode: mode });
    rigRef.current?.detector.applyPreferences();
  }, [commitProfile]);

  const patchProfile = useCallback((partial: Partial<MicrophoneProfile>) => {
    const current = profileRef.current;
    if (!current) return;
    commitProfile({ ...current, ...partial });
    rigRef.current?.detector.applyPreferences();
  }, [commitProfile]);

  const recalibrate = useCallback(() => {
    rigRef.current?.detector.recalibrate(2);
  }, []);

  /** AGC A/B: two short ambient measurements on a temporary capture; the better floor wins. */
  const runAgcBenchmark = useCallback(async () => {
    const rig = rigRef.current;
    const current = profileRef.current;
    if (!rig || live) return;
    setBenchmark("running");
    try {
      const samples: Record<"off" | "on", { noise_floor_db: number; spread_db: number; clip_risk: number; score: number }> = {
        off: { noise_floor_db: 0, spread_db: 0, clip_risk: 0, score: 0 },
        on: { noise_floor_db: 0, spread_db: 0, clip_risk: 0, score: 0 },
      };
      let identity: DeviceIdentity | null = null;
      for (const agc of [false, true] as const) {
        const probe = new BrowserMicrophone();
        const stream = await probe.open(micId || undefined, { autoGainControl: agc });
        const readBack = probe.applied;
        if (readBack && !identity) identity = { deviceId: readBack.deviceId ?? "", label: readBack.label, groupId: readBack.groupId ?? "" };
        try {
          const result = await measureNoiseFloor(stream, 2000);
          const key = agc ? "on" : "off";
          samples[key] = {
            noise_floor_db: result.noiseFloorDb,
            spread_db: result.spreadDb,
            clip_risk: result.clipRisk,
            score: scoreBenchmarkSample({ noise_floor_db: result.noiseFloorDb, spread_db: result.spreadDb, clip_risk: result.clipRisk }),
          };
        } finally {
          probe.close();
        }
      }
      const base = current ?? (identity ? resolveProfile(identity) : null);
      if (!base) throw new Error("no profile");
      // AGC has to win clearly (≥ 1 point) to be switched on for a sensitive microphone.
      const recommended: "off" | "on" = samples.on.score >= samples.off.score + 1 ? "on" : "off";
      commitProfile({ ...base, agcBenchmark: { ...samples, recommended, at: new Date().toISOString() } });
      setBenchmark("done");
    } catch {
      setBenchmark("failed");
    }
  }, [live, micId, commitProfile, resolveProfile]);

  const copyReadBack = useCallback(async () => {
    if (!applied) return;
    const text = JSON.stringify(
      {
        label: applied.label,
        requested: applied.requested,
        settings: applied.settings,
        capabilities: applied.capabilities,
        notHonoured: applied.notHonoured,
        supportedConstraints: applied.supportedConstraints,
        calibration: mic?.calibration ?? null,
        params: mic?.params ?? null,
        userAgent: typeof navigator === "undefined" ? "" : navigator.userAgent,
      },
      null,
      2,
    );
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the <pre> below stays selectable */
    }
  }, [applied, mic]);

  const gate = mic?.gate ?? null;
  const detection =
    !mic || mic.phase === "idle"
      ? "—"
      : mic.phase === "calibrating"
        ? `ortam ölçülüyor… ${Math.round(mic.calibrationProgress * 100)}%`
        : gate?.classification === "speech"
          ? "sahibin sesi"
          : gate?.classification === "background"
            ? "arka plan"
            : "sessiz";
  const environment = mic?.calibration ? ENVIRONMENT_LABEL[mic.calibration.environment] : mic ? "ölçülüyor…" : "—";
  const levelPercent = gate ? Math.round(Math.min(1, Math.max(0, (gate.rmsDb + 80) / 80)) * 100) : 0;
  const floorPercent = gate ? Math.round(Math.min(1, Math.max(0, (gate.floorDb + 80) / 80)) * 100) : 0;
  const levelColor =
    gate?.classification === "speech" ? "var(--accent)" : gate?.classification === "background" ? "var(--warn)" : "#3a4256";
  const micName = applied?.label || profile?.friendlyName || devices.find((d) => d.deviceId === micId)?.label || "Varsayılan";

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
          <label className="muted">
            Ses{" "}
            <select value={voice} onChange={(e) => changeVoice(e.target.value)} aria-label="Ses" disabled={live || busy}>
              {VOICE_CANDIDATES.map((v) => (
                <option key={v} value={v}>
                  {VOICE_LABEL[v]}
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
            <button onClick={() => simulatorRef.current?.demoToolCall()} style={smallButton}>
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
              {snapshot.sessionId
                ? `${snapshot.sessionId.slice(0, 8)}… · ${snapshot.provider} · ${snapshot.transport} · bacak ${snapshot.legs}` +
                  ` · ses: ${snapshot.voice ?? voice} · profil: ${snapshot.voiceProfile ?? "—"}`
                : `ses: ${voice}`}
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
          <div className="status-row">
            <strong>Mikrofon</strong>
            <span className="muted">{micName}</span>
          </div>
          <div className="status-row">
            <span>Ortam</span>
            <span className="muted">
              {environment}
              {profile && profile.environmentMode !== "auto" ? ` · seçim: ${MODE_LABEL[profile.environmentMode]}` : ""}
            </span>
          </div>
          <div className="status-row">
            <span>Gürültü bastırma</span>
            <span className="muted">{SUPPRESSION_LABEL[profile?.noiseSuppressionMode ?? "auto"]}</span>
          </div>
          <div className="status-row">
            <span>Ses algılama</span>
            <span className="muted">{detection}</span>
          </div>
          <div style={{ position: "relative", height: 8, background: "#1a1e29", borderRadius: 4, margin: "0.6rem 0" }} aria-label="Ses seviyesi">
            <div style={{ width: `${levelPercent}%`, height: "100%", background: levelColor, borderRadius: 4, transition: "width 80ms linear" }} />
            {gate && (
              <div
                title="Gürültü tabanı"
                style={{ position: "absolute", left: `${floorPercent}%`, top: -3, width: 2, height: 14, background: "var(--warn)" }}
              />
            )}
          </div>
          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", alignItems: "center" }}>
            {(Object.keys(MODE_LABEL) as EnvironmentMode[]).map((mode) => {
              const active = (profile?.environmentMode ?? "auto") === mode;
              return (
                <button
                  key={mode}
                  onClick={() => changeMode(mode)}
                  disabled={!profile}
                  style={{ ...smallButton, color: active ? "#0f1115" : "var(--accent)", background: active ? "var(--accent)" : "none" }}
                >
                  {MODE_LABEL[mode]}
                </button>
              );
            })}
            <span style={{ flex: 1 }} />
            <button onClick={recalibrate} disabled={!live} style={smallButton}>
              Yeniden ölçümle
            </button>
            <button onClick={() => setDiagnostics((v) => !v)} style={smallButton}>
              {diagnostics ? "Tanılamayı gizle" : "Tanılama"}
            </button>
          </div>
        </div>
      )}

      {snapshot && diagnostics && (
        <div className="panel">
          <strong>Tanılama</strong>
          <div className="status-row">
            <span>Gürültü tabanı</span>
            <span className="muted">
              {mic?.calibration
                ? `${mic.calibration.noiseFloorDb} dBFS · sabit ${mic.calibration.stationaryNoiseDb} · yayılım ${mic.calibration.spreadDb} dB · hassasiyet ${mic.calibration.sensitivity}`
                : "—"}
              {mic?.runningFloorDb !== null && mic?.runningFloorDb !== undefined ? ` · anlık ${mic.runningFloorDb} (sapma ${mic.driftDb})` : ""}
            </span>
          </div>
          <div className="status-row">
            <span>Konuşma olasılığı</span>
            <span className="muted">{gate ? `${Math.round(gate.prob * 100)}% · spektral ${Math.round(gate.spectralScore * 100)}%` : "—"}</span>
          </div>
          <div className="status-row">
            <span>Giriş RMS</span>
            <span className="muted">{gate ? `${gate.rmsDb} dBFS · marj ${gate.marginDb} dB` : "—"}</span>
          </div>
          <div className="status-row">
            <span>Kırpma</span>
            <span className="muted">
              {gate ? (gate.clipRatio > 0 ? `var (${Math.round(gate.clipRatio * 1000) / 10}%)` : "yok") : "—"}
              {mic?.calibration ? ` · risk ${mic.calibration.clipRisk}` : ""}
            </span>
          </div>
          <div className="status-row">
            <span>Kapı parametreleri</span>
            <span className="muted">
              {mic
                ? `${mic.params.basis} · marj ${mic.params.openMarginDb} dB · başlangıç ${mic.params.minOnsetMs} ms · tutma ${mic.params.hangMs} ms · ön-kayıt ${mic.params.preRollMs} ms`
                : "—"}
            </span>
          </div>
          <div className="status-row">
            <span>Seçili işleme</span>
            <span className="muted">
              {`AGC ${profile ? (effectiveAgc(profile) ? "açık" : "kapalı") : "—"} (${profile?.agcPreference ?? "auto"})`}
              {` · bastırma ${SUPPRESSION_LABEL[profile?.noiseSuppressionMode ?? "auto"]}`}
              {` · voiceIsolation ${applied?.voiceIsolation === null || applied?.voiceIsolation === undefined ? "bilinmiyor" : applied.voiceIsolation ? "uygulandı" : "uygulanmadı"}`}
              {` · denoiser passthrough · uplink ${profile?.inputGainStrategy === "gated_attenuation" ? `kapılı -${GATED_ATTENUATION_DB} dB` : "dokunulmadı"}`}
            </span>
          </div>
          <div className="status-row">
            <span>Uygulanan kısıtlar</span>
            <span className="muted">
              {applied
                ? `AEC ${String(applied.echoCancellation)} · NS ${String(applied.noiseSuppression)} · AGC ${String(applied.autoGainControl)} · kanal ${applied.channelCount ?? "?"} · ${applied.sampleRate ?? "?"} Hz` +
                  (applied.notHonoured.length ? ` · uygulanmayan: ${applied.notHonoured.join(", ")}` : " · istenenlerin hepsi uygulandı")
                : "mikrofon açık değil"}
            </span>
          </div>
          <div className="status-row">
            <span>Sayaçlar</span>
            <span className="muted">
              {`kapı ${snapshot.micMetrics.gate_opens} · arka plan ${snapshot.micMetrics.gated_out} · tık ${snapshot.micMetrics.click_rejects} · yanlış başlangıç ${snapshot.micMetrics.false_starts} · yanlış kesme ${snapshot.micMetrics.false_barge_ins} · boş tur ${snapshot.micMetrics.false_turns} · ölçüm ${snapshot.micMetrics.calibrations}`}
            </span>
          </div>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center", margin: "0.6rem 0" }}>
            <label className="muted">
              AGC{" "}
              <select
                value={profile?.agcPreference ?? "auto"}
                disabled={!profile}
                onChange={(e) => patchProfile({ agcPreference: e.target.value as AgcPreference })}
                aria-label="AGC tercihi"
              >
                <option value="auto">ölçüme göre</option>
                <option value="off">kapalı</option>
                <option value="on">açık</option>
              </select>
            </label>
            <label className="muted">
              Hassasiyet{" "}
              <select
                value={profile?.preferredVadSensitivity ?? "auto"}
                disabled={!profile}
                onChange={(e) => patchProfile({ preferredVadSensitivity: e.target.value as MicrophoneProfile["preferredVadSensitivity"] })}
                aria-label="Ses algılama hassasiyeti"
              >
                <option value="auto">otomatik</option>
                <option value="low">düşük</option>
                <option value="normal">normal</option>
                <option value="high">yüksek</option>
              </select>
            </label>
            <label className="muted">
              Bastırma{" "}
              <select
                value={profile?.noiseSuppressionMode ?? "auto"}
                disabled={!profile}
                onChange={(e) => patchProfile({ noiseSuppressionMode: e.target.value as MicrophoneProfile["noiseSuppressionMode"] })}
                aria-label="Gürültü bastırma modu"
              >
                <option value="auto">otomatik</option>
                <option value="browser">tarayıcı</option>
                <option value="off">kapalı</option>
              </select>
            </label>
            <label className="muted">
              <input
                type="checkbox"
                checked={profile?.inputGainStrategy === "gated_attenuation"}
                disabled={!profile || live}
                onChange={(e) => patchProfile({ inputGainStrategy: e.target.checked ? "gated_attenuation" : "browser" })}
              />{" "}
              Kapılı uplink zayıflatma (deneysel, sonraki oturumda)
            </label>
            <button onClick={runAgcBenchmark} disabled={live || busy || benchmark === "running"} style={smallButton}>
              {benchmark === "running" ? "AGC A/B ölçülüyor…" : "AGC A/B ölçümü (2×2 s)"}
            </button>
          </div>
          {(profile?.agcBenchmark || benchmark === "failed") && (
            <div className="status-row">
              <span>AGC A/B</span>
              <span className="muted">
                {benchmark === "failed"
                  ? "ölçüm başarısız (mikrofon izni?)"
                  : profile?.agcBenchmark
                    ? `kapalı: ${profile.agcBenchmark.off.noise_floor_db} dBFS / yayılım ${profile.agcBenchmark.off.spread_db} / puan ${profile.agcBenchmark.off.score} · ` +
                      `açık: ${profile.agcBenchmark.on.noise_floor_db} dBFS / yayılım ${profile.agcBenchmark.on.spread_db} / puan ${profile.agcBenchmark.on.score} · ` +
                      `öneri: ${profile.agcBenchmark.recommended === "on" ? "açık" : "kapalı"}`
                    : ""}
              </span>
            </div>
          )}
          {applied && (
            <>
              <div className="status-row">
                <span>Okunan ayarlar / yetenekler</span>
                <button onClick={copyReadBack} style={smallButton}>
                  {copied ? "Kopyalandı" : "JSON kopyala"}
                </button>
              </div>
              <pre className="muted" style={{ maxHeight: 220, overflow: "auto", fontSize: "0.75rem", margin: 0, whiteSpace: "pre-wrap" }}>
                {JSON.stringify(
                  { settings: applied.settings, capabilities: applied.capabilities, notHonoured: applied.notHonoured, supportedConstraints: applied.supportedConstraints },
                  null,
                  1,
                )}
              </pre>
            </>
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
