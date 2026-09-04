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
import { type ControllerSnapshot, VoiceSessionController, describeNarrationCursor } from "../lib/voice/controller";
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
  appliedSettingsRecord,
  constraintsFor,
  defaultProfile,
  effectiveAgc,
  fingerprintDevice,
  learnFromSession,
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
import { copySessionId, shortSessionId } from "../lib/voice/session-id";
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
  const [idCopied, setIdCopied] = useState<"idle" | "copied" | "failed">("idle");
  /** read-only holder of the full session id: the legacy copy fallback selects it */
  const sessionIdInputRef = useRef<HTMLInputElement | null>(null);

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
      // ADR-0047 §4: the profile's learned offsets and last measured echo residual.
      adaptation: () => profileRef.current?.learned ?? null,
      initialEchoResidualDb: () => profileRef.current?.measuredEchoResidualDb ?? null,
    });
    /** The default device only reveals its identity after the grant. */
    const currentProfile = (): MicrophoneProfile | null => {
      const readBack = microphone.applied;
      return (
        profileRef.current ??
        (readBack ? resolveProfile({ deviceId: readBack.deviceId ?? "", label: readBack.label, groupId: readBack.groupId ?? "" }) : null)
      );
    };
    detector.onCalibration((c) => {
      // ADR-0047 §5: only a MEASUREMENT updates the profile; a dead or
      // contaminated window is reported, never persisted as "the room".
      if (c.measured !== 1) return;
      const readBack = microphone.applied;
      const current = currentProfile();
      if (!current) return;
      const at = new Date().toISOString();
      commitProfile({
        ...current,
        measuredNoiseFloorDb: c.noise_floor_db,
        lastCalibratedAt: at,
        appliedVoiceIsolation: readBack?.voiceIsolation ?? current.appliedVoiceIsolation,
        appliedSettings: readBack ? appliedSettingsRecord(readBack, at) : current.appliedSettings,
        friendlyName: readBack?.label || current.friendlyName,
      });
    });
    // ADR-0047 §4: the measured echo residual is a per-device fact; persist it.
    detector.onEchoResidualMeasured((residualDb) => {
      const current = currentProfile();
      if (!current || current.measuredEchoResidualDb === residualDb) return;
      commitProfile({ ...current, measuredEchoResidualDb: residualDb });
    });
    rigRef.current = { playback, microphone, detector, store };
    setVoice(loadVoiceChoice());

    const api = new VoiceSessionApi(apiFetch);
    const controller = new VoiceSessionController({
      api,
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
      // ADR-0047 §4: the AGC A/B result travels with the read-back, as numbers.
      inputEvidence: () => {
        const bench = profileRef.current?.agcBenchmark;
        if (!bench) return { agc_bench: 0 };
        return {
          agc_bench: 1,
          agc_bench_off_score: bench.off.score,
          agc_bench_on_score: bench.on.score,
          agc_bench_off_floor_db: bench.off.noise_floor_db,
          agc_bench_on_floor_db: bench.on.noise_floor_db,
          agc_bench_recommended_on: bench.recommended === "on" ? 1 : 0,
        };
      },
    });
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setSnapshot);
    // ADR-0045: the same scrubbed request record the diagnostics view shows,
    // once per request on the dev console; never a header, never a credential.
    const unsubscribeLog =
      process.env.NODE_ENV === "development"
        ? api.onRequest((entry) => {
            // oxlint-disable-next-line no-console
            console.debug("[voice] Cloud Core", entry);
          })
        : () => {};
    // Ask the server which contract it speaks before the owner clicks Connect,
    // so the voice selector can already say when the choice will not apply.
    void controller.probeContract();
    return () => {
      unsubscribe();
      unsubscribeLog();
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
    // ADR-0047 §3: the output AudioContext is created and resumed inside the
    // owner's click, so the first response never waits for `resume()`.
    rigRef.current?.playback.prepare();
    await controllerRef.current?.connect({ deviceId: micId || undefined, voice });
    // Labels are only revealed after a getUserMedia grant.
    void refreshDevices();
    const readBack = rigRef.current?.microphone.applied ?? null;
    setApplied(readBack);
    if (readBack) {
      const current =
        profileRef.current ??
        resolveProfile({ deviceId: readBack.deviceId ?? "", label: readBack.label, groupId: readBack.groupId ?? "" });
      // ADR-0047 §4: the read-back is a measurement; the profile keeps it as one.
      commitProfile({ ...current, appliedSettings: appliedSettingsRecord(readBack, new Date().toISOString()) });
    }
  }, [micId, voice, refreshDevices, resolveProfile, commitProfile]);

  const disconnect = useCallback(async () => {
    const controller = controllerRef.current;
    if (!controller) return;
    await controller.disconnect();
    // ADR-0047 §4: the profile learns from the session's own counters — bounded, reversible, no owner question.
    const current = profileRef.current;
    const m = controller.getSnapshot().micMetrics;
    if (current) {
      const learned = learnFromSession(
        current,
        { false_starts: m.false_starts, false_barge_ins: m.false_barge_ins, gate_opens: m.gate_opens, confirmed_turns: m.confirmed_turns },
        new Date().toISOString(),
      );
      if (learned !== current) {
        commitProfile(learned);
        rigRef.current?.detector.applyPreferences();
      }
    }
  }, [commitProfile]);

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

  /**
   * Copy the CANONICAL full session id (the owner once re-typed it from network
   * traffic and transposed two hex characters). Works after disconnect too: the
   * controller keeps the last id until a new session starts.
   */
  const copyId = useCallback(async () => {
    const { outcome } = await copySessionId(snapshot?.sessionId, {
      clipboard: typeof navigator === "undefined" ? null : (navigator.clipboard ?? null),
      fallback: (text) => {
        const input = sessionIdInputRef.current;
        if (!input) return false;
        input.value = text;
        input.focus();
        input.select();
        input.setSelectionRange(0, text.length);
        // Deprecated but still the only path when the async API is refused
        // (insecure origin, denied permission); the selection stays for Ctrl+C.
        return typeof document.execCommand === "function" && document.execCommand("copy");
      },
    });
    setIdCopied(outcome === "failed" ? "failed" : "copied");
    setTimeout(() => setIdCopied("idle"), 1500);
  }, [snapshot?.sessionId]);

  const copyIdButton = snapshot?.sessionId ? (
    <button onClick={copyId} style={smallButton} title={snapshot.sessionId} aria-label="Session ID kopyala">
      {idCopied === "copied" ? "Kopyalandı" : idCopied === "failed" ? "Kopyalanamadı (seçildi)" : "Session ID kopyala"}
    </button>
  ) : null;

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

  // ADR-0045: the A/B voice selector stays visible; on a server whose contract
  // has no `voice` field it is marked unavailable instead of silently ignored.
  const contract = snapshot?.contract ?? null;
  const voiceUnavailable = contract !== null && contract.version !== null && !contract.createFields.includes("voice");
  const contractLabel =
    contract === null
      ? "sorgulanıyor…"
      : contract.source === "unauthorized"
        ? "oturum açık değil"
        : contract.source === "legacy"
          ? `v${contract.version} (eski sunucu; ses seçimi yok)`
          : contract.known
            ? `v${contract.version} (sunucu)`
            : `v${contract.version}? (sürüm bilinmiyor; paket içi varsayıldı)`;

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
          <label className="muted" title={voiceUnavailable ? `Sunucu sözleşmesi v${contract?.version}: ses seçimi bu sürümde yok` : undefined}>
            Ses{voiceUnavailable ? " (sunucuda kullanılamaz)" : ""}{" "}
            <select
              value={voice}
              onChange={(e) => changeVoice(e.target.value)}
              aria-label="Ses"
              aria-disabled={voiceUnavailable || undefined}
              disabled={live || busy || voiceUnavailable}
            >
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
            <span className="muted" style={{ display: "inline-flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
              <span title={snapshot.sessionId ?? undefined}>
                {snapshot.sessionId
                  ? `${shortSessionId(snapshot.sessionId)} · ${snapshot.provider} · ${snapshot.transport} · bacak ${snapshot.legs}` +
                    ` · ses: ${snapshot.voice ?? voice} · profil: ${snapshot.voiceProfile ?? "—"}`
                  : `ses: ${voice}`}
              </span>
              {copyIdButton}
              {snapshot.sessionId && (
                <input
                  ref={sessionIdInputRef}
                  readOnly
                  value={snapshot.sessionId}
                  aria-label="Oturum kimliği (tam)"
                  tabIndex={-1}
                  style={{ position: "absolute", left: -9999, width: 1, height: 1, opacity: 0 }}
                />
              )}
            </span>
          </div>
          <div className="status-row">
            <span>Sözleşme</span>
            <span className="muted">{contractLabel}</span>
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
          {snapshot.contractNotice && (
            <p className="muted" style={{ color: "var(--warn)" }}>{snapshot.contractNotice}</p>
          )}
          {snapshot.lastError && (
            <div style={{ color: "var(--fail)" }}>
              <p className="muted" style={{ color: "inherit", margin: 0 }}>{snapshot.lastError}</p>
              {snapshot.lastErrorLines.map((line, i) => (
                <p className="muted" style={{ color: "inherit", margin: "0.2rem 0 0 1rem", fontSize: "0.85rem" }} key={`${i}-${line}`}>
                  {line}
                </p>
              ))}
            </div>
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
                ? `${mic.params.basis} · marj ${mic.params.openMarginDb} dB · başlangıç ${mic.params.minOnsetMs} ms · tutma ${mic.params.hangMs} ms · ön-kayıt ${mic.params.preRollMs} ms` +
                  ` · konuşma sırasında +${mic.params.echoExtraMarginDb} dB${mic.params.echoResidualAdjustDb > 0 ? " (ölçülen yankıdan)" : ""}` +
                  (mic.params.adaptation.marginDb || mic.params.adaptation.onsetMs || mic.params.adaptation.echoMarginDb
                    ? ` · öğrenilen +${mic.params.adaptation.marginDb} dB / +${mic.params.adaptation.onsetMs} ms / yankı +${mic.params.adaptation.echoMarginDb} dB`
                    : "")
                : "—"}
            </span>
          </div>
          <div className="status-row">
            <span>Zamanlama ölçümü</span>
            <span className="muted">
              {mic
                ? `yakalama gecikmesi ${mic.captureLagMs === null ? "ölçülemiyor" : `${mic.captureLagMs} ms`}` +
                  ` · son ön-kayıt ${mic.lastPreRollMs === null ? "—" : `${mic.lastPreRollMs} ms`}` +
                  ` · yankı kalıntısı ${mic.echoResidualDb === null ? "ölçülmedi" : `${mic.echoResidualDb} dBFS`}` +
                  (mic.deadWindows > 0 ? ` · ölü giriş pencereleri ${mic.deadWindows}` : "")
                : "—"}
            </span>
          </div>
          <div className="status-row">
            <span>Gecikme bileşenleri</span>
            <span className="muted" style={{ textAlign: "right" }}>
              {(() => {
                const d = snapshot.latencyDetail;
                const parts: string[] = [];
                if (d.barge_in) {
                  parts.push(
                    `söze girme: ${d.barge_in.playback_stopped_ms} ms = ön-kayıt ${d.barge_in.pre_roll_ms ?? "?"} + algılama ${d.barge_in.detect_ms ?? "?"} + kazanç→0 ${d.barge_in.gain_zero_ms ?? "?"} (komut ${d.barge_in.stop_command_ms})` +
                      `${d.barge_in.early_mute ? " · erken sustur" : ""}${d.barge_in.anomaly ? " · ANOMALİ" : ""}`,
                  );
                }
                if (d.first_audio) {
                  parts.push(
                    `ilk ses: yanıt ${d.first_audio.response_created_ms ?? "?"} + üretim ${d.first_audio.first_delta_ms ?? "?"} + çalma ${d.first_audio.playback_ms ?? "?"} ms (${d.first_audio.basis ? "yerel" : "sağlayıcı"})`,
                  );
                }
                if (d.uplink) {
                  parts.push(
                    `uplink: ön-kayıt ${d.uplink.pre_roll_ms ?? "?"} + kapı ${d.uplink.gate_ms ?? "?"} + RTP ${d.uplink.rtp_ms ?? "?"} ms (sağlayıcı ${d.uplink.provider_ms ?? "?"} ms, ${d.uplink.basis ? "rtp" : "sağlayıcı"})`,
                  );
                }
                return parts.length ? parts.join(" · ") : "—";
              })()}
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
                ? `AEC ${String(applied.echoCancellation)} · NS ${String(applied.noiseSuppression)} · AGC ${String(applied.autoGainControl)} · kanal ${applied.channelCount ?? "?"} · ${applied.sampleRate ?? "?"} Hz · giriş gecikmesi ${applied.inputLatencyMs === null ? "bilinmiyor" : `${applied.inputLatencyMs} ms`}` +
                  (applied.notHonoured.length ? ` · uygulanmayan: ${applied.notHonoured.join(", ")}` : " · istenenlerin hepsi uygulandı")
                : "mikrofon açık değil"}
            </span>
          </div>
          <div className="status-row">
            <span>Sayaçlar</span>
            <span className="muted">
              {`kapı ${snapshot.micMetrics.gate_opens} · arka plan ${snapshot.micMetrics.gated_out} · tık ${snapshot.micMetrics.click_rejects} · yanlış başlangıç ${snapshot.micMetrics.false_starts} · yanlış kesme ${snapshot.micMetrics.false_barge_ins} · boş tur ${snapshot.micMetrics.false_turns} · onaylı tur ${snapshot.micMetrics.confirmed_turns} · erken sustur ${snapshot.micMetrics.early_mutes} (geri ${snapshot.micMetrics.early_mute_reverts}) · boş iptal ${snapshot.micMetrics.cancel_noop_errors} · ölçüm ${snapshot.micMetrics.calibrations}`}
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
          <div className="status-row">
            <span>Oturum kimliği (tam)</span>
            <span className="muted" style={{ display: "inline-flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
              <code style={{ userSelect: "all" }} title={snapshot.sessionId ?? undefined}>{snapshot.sessionId ?? "—"}</code>
              {copyIdButton}
            </span>
          </div>
          <div className="status-row">
            <span>Sunucu sözleşmesi</span>
            <span className="muted">
              {contractLabel}
              {contract && contract.createFields.length ? ` · oturum alanları: ${contract.createFields.join(", ")}` : ""}
            </span>
          </div>
          <div className="status-row" style={{ alignItems: "flex-start" }}>
            <span>Cloud Core istekleri</span>
            <span className="muted" style={{ textAlign: "right", maxWidth: "75%" }}>
              {snapshot.requestLog.length === 0 ? "—" : `son ${snapshot.requestLog.length} istek (başlıksız, kimlik bilgisi temizlenmiş)`}
            </span>
          </div>
          {snapshot.requestLog.length > 0 && (
            <pre className="muted" style={{ maxHeight: 220, overflow: "auto", fontSize: "0.75rem", margin: "0 0 0.6rem", whiteSpace: "pre-wrap" }}>
              {snapshot.requestLog
                .toReversed()
                .map((entry) => {
                  const status = entry.status === null ? `ağ hatası${entry.error ? `: ${entry.error}` : ""}` : `HTTP ${entry.status}`;
                  const body = entry.body ? ` ${JSON.stringify(entry.body).slice(0, 300)}` : "";
                  const detail = entry.detail.length ? `\n    ${entry.detail.join("\n    ")}` : "";
                  return `#${entry.seq} ${entry.method} ${entry.path} → ${status}${body}${detail}`;
                })
                .join("\n")}
            </pre>
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

      {snapshot?.narrationCursor && (
        <div className="panel">
          <strong>Anlatım imleci</strong>
          <div className="status-row">
            <span>
              {snapshot.narrationCursor.state}
              {snapshot.narrationCursor.action ? ` · ${snapshot.narrationCursor.action}` : ""}
            </span>
            <span className="muted">{describeNarrationCursor(snapshot.narrationCursor)}</span>
          </div>
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
