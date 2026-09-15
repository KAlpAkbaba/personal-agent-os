"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import OwnerGate, { SignOutButton } from "../components/OwnerGate";
import {
  BrowserMicrophone,
  type GatedDetectorSnapshot,
  measureNoiseFloor,
} from "../lib/voice/audio";
import type { EnvironmentMode } from "../lib/voice/calibration";
import { type ControllerSnapshot, describeNarrationCursor } from "../lib/voice/controller";
import {
  ENVIRONMENT_LABEL,
  MODE_LABEL,
  SUPPRESSION_LABEL,
  VOICE_LABEL,
  VOICE_STATE_LABEL,
  speechPhaseNote,
  voiceStateLabel,
} from "../lib/voice/labels";
import {
  type AgcPreference,
  type DeviceIdentity,
  type MicrophoneProfile,
  VOICE_CANDIDATES,
  effectiveAgc,
  scoreBenchmarkSample,
} from "../lib/voice/profile";
import { GATED_ATTENUATION_DB } from "../lib/voice/rig";
import { copySessionId, shortSessionId } from "../lib/voice/session-id";
import { isBusyState, isLiveState, microphoneName } from "../lib/voice/store";
import { useVoiceSession } from "../lib/voice/useVoiceSession";

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
 * browser media path. The rig then drives a scripted fake transport so the
 * session, event, tool-relay and close paths still run against the real API.
 *
 * M18 / ADR-0061: this page no longer OWNS the audio rig or the controller.
 * They live in `lib/voice/store.ts`, the tab's one voice session, which `/core`
 * reads too; this is the diagnostics / developer view over that same session.
 * Leaving this page does not end the session, and returning to it does not
 * open a second microphone.
 */

const STATE_LABEL = VOICE_STATE_LABEL;

const LATENCY_LABEL: Array<[keyof ControllerSnapshot["latency"], string]> = [
  ["barge_in_to_stop_ms", "Söze girme → ses kesildi"],
  ["eot_to_first_audio_ms", "Söz bitti → ilk ses"],
  ["mic_to_uplink_ms", "Mikrofon → sağlayıcı duydu"],
  ["tool_preamble_ms", "Araç çağrısı → ön cümle"],
  ["tool_done_to_speech_ms", "Araç bitti → konuşma"],
];

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
  const { voice, actions, store } = useVoiceSession();
  const snapshot: ControllerSnapshot | null = voice.ready ? voice.controller : null;
  const { devices, micId, speakerId, simulated, profile, applied } = voice;
  const voiceChoice = voice.voice;
  const [mic, setMic] = useState<GatedDetectorSnapshot | null>(null);
  const [diagnostics, setDiagnostics] = useState(false);
  const [benchmark, setBenchmark] = useState<"idle" | "running" | "done" | "failed">("idle");
  const [copied, setCopied] = useState(false);
  const [idCopied, setIdCopied] = useState<"idle" | "copied" | "failed">("idle");
  /** read-only holder of the full session id: the legacy copy fallback selects it */
  const sessionIdInputRef = useRef<HTMLInputElement | null>(null);

  // ADR-0045: the same scrubbed request record the diagnostics view shows,
  // once per request on the dev console; never a header, never a credential.
  useEffect(() => {
    if (process.env.NODE_ENV !== "development") return;
    // The rig exists once the store is ready; re-run when that flips.
    const rig = voice.ready ? store.peekRig() : null;
    if (!rig) return;
    return rig.parts.api.onRequest((entry) => {
      // oxlint-disable-next-line no-console
      console.debug("[voice] Cloud Core", entry);
    });
  }, [store, voice.ready]);

  useEffect(() => {
    if (voice.ready) void actions.refreshDevices();
  }, [voice.ready, actions]);

  const live = snapshot !== null && isLiveState(snapshot.state);
  const busy = snapshot !== null && isBusyState(snapshot.state);
  // req 235: a missing credential is not retryable from here, and the button says so
  // rather than inviting the owner to press it until they give up.
  const blocked = snapshot?.unavailable != null && !snapshot.unavailable.retryable;

  // Live level / gate meter while a session is open.
  useEffect(() => {
    if (!live) {
      setMic(null);
      return;
    }
    const timer = setInterval(() => {
      setMic(store.detectorSnapshot());
    }, 100);
    return () => clearInterval(timer);
  }, [live, store]);

  const connect = useCallback(() => actions.connect(), [actions]);
  const disconnect = useCallback(() => actions.disconnect(), [actions]);
  const changeMic = useCallback((deviceId: string) => actions.setMicrophone(deviceId), [actions]);
  const changeSpeaker = useCallback((deviceId: string) => actions.setSpeaker(deviceId), [actions]);
  const changeVoice = useCallback((value: string) => actions.setVoice(value), [actions]);
  const changeMode = useCallback((mode: EnvironmentMode) => actions.setNoiseMode(mode), [actions]);
  const patchProfile = useCallback((partial: Partial<MicrophoneProfile>) => actions.patchProfile(partial), [actions]);
  const recalibrate = useCallback(() => actions.recalibrate(), [actions]);

  /**
   * AGC A/B: two short ambient measurements on a temporary capture; the better
   * floor wins. This is the one place outside the rig that opens a microphone,
   * and it is an owner-triggered measurement that closes its probe before
   * returning — never while the session's own microphone is open (`!live`).
   */
  const runAgcBenchmark = useCallback(async () => {
    const rig = store.peekRig();
    const current = rig?.profile ?? null;
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
      const base = current ?? (identity ? rig.resolveProfile(identity) : null);
      if (!base) throw new Error("no profile");
      // AGC has to win clearly (≥ 1 point) to be switched on for a sensitive microphone.
      const recommended: "off" | "on" = samples.on.score >= samples.off.score + 1 ? "on" : "off";
      rig.commitProfile({ ...base, agcBenchmark: { ...samples, recommended, at: new Date().toISOString() } });
      setBenchmark("done");
    } catch {
      setBenchmark("failed");
    }
  }, [live, micId, store]);

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
  const micName = microphoneName(voice);

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
      <p className="subtitle">
        Gerçek zamanlı konuşma — tarayıcı medya bacağı (M12 / D). Bu sayfa tanılama görünümüdür; oturum{" "}
        <Link href="/core">Çekirdek</Link> ile paylaşılır ve sayfadan ayrılınca kapanmaz.
      </p>

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
            {live
              ? "Bağlantıyı kes"
              : busy
                ? STATE_LABEL[snapshot.state]
                : blocked
                  ? "Ses kullanılamıyor"
                  : "Bağlan"}
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
              value={voiceChoice}
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
          <button onClick={() => void actions.refreshDevices()} className="badge unknown" style={{ border: "none", cursor: "pointer", font: "inherit" }}>
            Cihazları yenile
          </button>
        </div>
        {simulated && live && (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            Sunucu simülatör sağlayıcısını seçti: ses yok, kablolama gerçek API üzerinden çalışıyor.{" "}
            <button onClick={() => store.peekRig()?.simulator?.demoToolCall()} style={smallButton}>
              Demo araç çağrısı (clock.now)
            </button>
          </p>
        )}
      </div>

      {snapshot && (
        <div className="panel">
          <div className="status-row">
            <strong>Durum</strong>
            <span className={`badge ${snapshot.state === "error" ? "fail" : live ? "ok" : "unknown"}`} data-speech-phase={snapshot.speech.phase}>
              {voiceStateLabel(snapshot)}
              {speechPhaseNote(snapshot) ? ` · ${speechPhaseNote(snapshot)}` : ""}
            </span>
          </div>
          <div className="status-row">
            <span>Oturum</span>
            <span className="muted" style={{ display: "inline-flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
              <span title={snapshot.sessionId ?? undefined}>
                {snapshot.sessionId
                  ? `${shortSessionId(snapshot.sessionId)} · ${snapshot.provider} · ${snapshot.transport} · bacak ${snapshot.legs}` +
                    ` · ses: ${snapshot.voice ?? voiceChoice} · profil: ${snapshot.voiceProfile ?? "—"}`
                  : `ses: ${voiceChoice}`}
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
          {/* B20 req 235: the provider-unavailable condition is not an error line. It says
              what is missing and who can change it, and the connect button above is
              disabled while nothing the owner does here could work. */}
          {snapshot.unavailable && (
            <div style={{ color: "var(--warn)" }} data-voice-unavailable={snapshot.unavailable.errorClass || "unknown"}>
              <p className="muted" style={{ color: "inherit", margin: 0 }}>{snapshot.unavailable.message}</p>
              {snapshot.unavailable.remedy && (
                <p className="muted" style={{ color: "inherit", margin: "0.2rem 0 0 0" }}>{snapshot.unavailable.remedy}</p>
              )}
            </div>
          )}
          {!snapshot.unavailable && snapshot.lastError && (
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
          <div className="status-row">
            <span>Söze girme</span>
            <span className="muted">
              {`konuşma ${snapshot.micMetrics.speech_detected} · olası kesme ${snapshot.micMetrics.potential_barge_in} · kabul ${snapshot.micMetrics.accepted_owner_interruption} · arka plan reddi ${snapshot.micMetrics.rejected_background_speech} · dur komutu ${snapshot.micMetrics.explicit_stop_command} · boş kesme ${snapshot.micMetrics.false_interruption}`}
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
                <option value="auto">otomatik (öğrenilenle)</option>
                <option value="low">düşük</option>
                <option value="normal">normal (öğrenileni yok say)</option>
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
