"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import OwnerGate, { SignOutButton } from "../components/OwnerGate";
import type { Loaded } from "../lib/cockpit/api";
import {
  cancelResearch,
  pauseResearch,
  resumeResearch,
  RESEARCH_MODES,
  RESEARCH_MODE_LABEL,
  type ResearchMode,
  explainError,
  fetchReportJson,
  getResearchFocus,
  getResearchTask,
  listDevices,
  listResearchTasks,
  previewSelection,
  selectResearchFocus,
  startResearch,
} from "../lib/research/api";
import type { FocusState } from "../lib/research/focus";
import {
  DEFAULT_MAX_SOURCES,
  DEFAULT_RECENCY_DAYS,
  DEFAULT_TOPIC,
  type DeviceInfo,
  MAX_MAX_SOURCES,
  MAX_RECENCY_DAYS,
  MIN_MAX_SOURCES,
  MIN_RECENCY_DAYS,
  type ResearchTaskDetail,
  type ResearchTaskSummary,
  type SelectionPreview,
  clampMaxSources,
  clampRecencyDays,
  isTerminal,
} from "../lib/research/model";
import { ResearchPoller } from "../lib/research/poll";
import { UnauthorizedError } from "../lib/session";
import DeviceChooser, { AUTO } from "./DeviceChooser";
import FocusStrip from "./FocusStrip";
import ProgressPanel from "./ProgressPanel";
import ReportView from "./ReportView";
import TaskList from "./TaskList";

/**
 * /research — M13 track F: the owner's research surface.
 *
 * Topic → device choice → start → live progress (polled every 3 s while the
 * task is not terminal) → the report in the spec §3 order. Previous tasks are
 * listed newest first; opening one loads that task's detail (and its report
 * when ready). Nothing else is auto-loaded: a finished task is announced in
 * the progress panel, the report is what the owner opened — not what the
 * system pushed (constitution: notify briefly and wait).
 *
 * M18.2: several runs can share a title, so every row carries an identity line
 * built from the run's own facts, and opening a row also makes it the
 * **conversational focus** — what "Bunu anlat." refers to next. The focus is
 * read and set by id; the topic is presentation metadata and is never used to
 * decide which report is meant.
 */

const inputStyle: React.CSSProperties = {
  padding: "0.5rem 0.7rem",
  borderRadius: 8,
  border: "1px solid #232734",
  background: "#0f1115",
  color: "var(--text)",
  font: "inherit",
};

function ResearchSurface() {
  const [topic, setTopic] = useState(DEFAULT_TOPIC);
  const [deviceValue, setDeviceValue] = useState<string>(AUTO);
  const [recencyDays, setRecencyDays] = useState<number>(DEFAULT_RECENCY_DAYS);
  const [maxSources, setMaxSources] = useState<number>(DEFAULT_MAX_SOURCES);
  // B31 req 192: the mode is the owner's explicit choice; QUICK unless they pick another.
  const [mode, setMode] = useState<ResearchMode>("quick");

  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [devicesError, setDevicesError] = useState<string | null>(null);
  const [preview, setPreview] = useState<SelectionPreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);

  const [tasks, setTasks] = useState<ResearchTaskSummary[]>([]);
  const [focus, setFocus] = useState<Loaded<FocusState>>({ kind: "loading" });
  const [focusNotice, setFocusNotice] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [active, setActive] = useState<ResearchTaskDetail | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [pauseBusy, setPauseBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  const report = useCallback((err: unknown) => {
    // A 401 is not an error to show: OwnerGate has already taken over.
    if (err instanceof UnauthorizedError) return;
    setError(explainError(err));
  }, []);

  /**
   * The list and the focus are one refresh, on the existing cadence: the
   * poller's terminal stop, a start, a selection and the first mount. No
   * second timer — the focus changes exactly when the list does.
   */
  const refreshTasks = useCallback(async () => {
    setNow(Date.now());
    try {
      setTasks(await listResearchTasks());
    } catch (err) {
      report(err);
    }
    // `getResearchFocus` folds every outcome into its result; it never throws.
    setFocus(await getResearchFocus());
  }, [report]);

  /**
   * The owner said "this one". It becomes the conversational focus, so that
   * "Bunu anlat." by voice means this report and not the last one to finish.
   */
  const chooseFocus = useCallback(async (taskId: string) => {
    try {
      const selection = await selectResearchFocus(taskId);
      if (selection.focus) setFocus(selection.focus);
      setFocusNotice(selection.notice);
    } catch (err) {
      if (err instanceof UnauthorizedError) return;
      setFocusNotice(explainError(err));
    }
  }, []);

  const refreshDevices = useCallback(async () => {
    try {
      setDevices(await listDevices());
      setDevicesError(null);
    } catch (err) {
      if (err instanceof UnauthorizedError) return;
      setDevicesError(explainError(err));
    }
  }, []);

  // One poller for the page's lifetime; it only ever tracks the active task.
  const pollerRef = useRef<ResearchPoller | null>(null);
  if (pollerRef.current === null) {
    pollerRef.current = new ResearchPoller({
      fetchTask: getResearchTask,
      onUpdate: (task) => {
        setActive(task);
        setPollError(null);
      },
      onError: (err) => setPollError(explainError(err)),
      onStop: (reason) => {
        if (reason === "terminal") void refreshTasks();
      },
    });
  }
  useEffect(() => {
    const poller = pollerRef.current;
    return () => poller?.stop();
  }, []);

  useEffect(() => {
    void refreshDevices();
    void refreshTasks();
  }, [refreshDevices, refreshTasks]);

  const runPreview = useCallback(async () => {
    setPreviewBusy(true);
    try {
      setPreview(await previewSelection(deviceValue || null));
    } catch (err) {
      if (err instanceof UnauthorizedError) return;
      setPreview({ kind: "none", detail: explainError(err) });
    } finally {
      setPreviewBusy(false);
    }
  }, [deviceValue]);

  const start = useCallback(async () => {
    const input = topic.trim();
    if (!input || starting) return;
    setStarting(true);
    setError(null);
    setCopyState("idle");
    try {
      const started = await startResearch({
        input,
        target_device: deviceValue || null,
        recency_days: clampRecencyDays(recencyDays),
        max_sources: clampMaxSources(maxSources),
        research_mode: mode,
        // Owner-handoff mode (spec §5a): the web page is always attended, so
        // a Google interstitial brings Chrome to the front instead of
        // silently falling back to DuckDuckGo.
        interactive: true,
      });
      setActive({
        task_id: started.task_id,
        topic: input,
        status: started.status,
        stage: "planned",
        device: started.device,
        report: null,
        events: [],
      });
      pollerRef.current?.start(started.task_id);
    } catch (err) {
      report(err);
      // A 409 still records a FAILED task server-side; show it in the list.
      void refreshTasks();
    } finally {
      setStarting(false);
    }
  }, [topic, starting, deviceValue, recencyDays, maxSources, mode, report, refreshTasks]);

  const open = useCallback(
    async (taskId: string) => {
      setError(null);
      setCopyState("idle");
      setFocusNotice(null);
      pollerRef.current?.stop();
      // Opening a report IS selecting it; the two are the same owner act. It
      // runs alongside the detail fetch rather than gating it, so a focus
      // route that is missing or refuses never stops the report from opening.
      void chooseFocus(taskId);
      try {
        const task = await getResearchTask(taskId);
        setActive(task);
        if (!isTerminal(task)) pollerRef.current?.start(taskId);
      } catch (err) {
        report(err);
      }
    },
    [report, chooseFocus],
  );

  const cancel = useCallback(async () => {
    if (!active) return;
    setCancelBusy(true);
    try {
      await cancelResearch(active.task_id);
      // The poller keeps running; the next tick shows `cancelled`.
    } catch (err) {
      report(err);
    } finally {
      setCancelBusy(false);
    }
  }, [active, report]);

  // B31 req 203/204: pause and resume ride the same poller; the next tick shows the
  // paused flag (the stage stays where it was), so nothing here guesses the state.
  const pause = useCallback(async () => {
    if (!active) return;
    setPauseBusy(true);
    try {
      await pauseResearch(active.task_id);
    } catch (err) {
      report(err);
    } finally {
      setPauseBusy(false);
    }
  }, [active, report]);

  const resume = useCallback(async () => {
    if (!active) return;
    setPauseBusy(true);
    try {
      await resumeResearch(active.task_id);
    } catch (err) {
      report(err);
    } finally {
      setPauseBusy(false);
    }
  }, [active, report]);

  const copyJson = useCallback(async () => {
    if (!active) return;
    try {
      const text = await fetchReportJson(active.task_id);
      await navigator.clipboard.writeText(text);
      setCopyState("copied");
    } catch (err) {
      if (err instanceof UnauthorizedError) return;
      setCopyState("failed");
    }
  }, [active]);

  return (
    <main style={{ maxWidth: 860 }}>
      <div className="status-row" style={{ borderBottom: "none", paddingBottom: 0 }}>
        <h1 style={{ margin: 0 }}>Araştırma</h1>
        <SignOutButton />
      </div>
      <p className="subtitle">
        Konuyu ver; gerçek Chrome ile kaynaklar toplanır, rapor hazır olunca burada açılır.{" "}
        <Link href="/artifacts" style={{ color: "var(--accent)" }}>
          Gelen kutusu →
        </Link>
      </p>

      <div className="panel">
        <label className="muted" htmlFor="topic">
          Konu
        </label>
        <textarea
          id="topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          rows={3}
          aria-label="Araştırma konusu"
          style={{ ...inputStyle, width: "100%", marginTop: "0.3rem", resize: "vertical" }}
        />

        <div style={{ marginTop: "0.75rem" }}>
          <DeviceChooser
            devices={devices}
            value={deviceValue}
            onChange={(v) => {
              setDeviceValue(v);
              setPreview(null);
            }}
            preview={preview}
            previewBusy={previewBusy}
            onPreview={runPreview}
            disabled={starting}
            loadError={devicesError}
          />
        </div>

        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginTop: "0.75rem", alignItems: "flex-end" }}>
          <label className="muted" style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
            Güncellik (gün, {MIN_RECENCY_DAYS}–{MAX_RECENCY_DAYS})
            <input
              type="number"
              min={MIN_RECENCY_DAYS}
              max={MAX_RECENCY_DAYS}
              value={recencyDays}
              onChange={(e) => setRecencyDays(Number(e.target.value))}
              onBlur={() => setRecencyDays(clampRecencyDays(recencyDays))}
              aria-label="Güncellik penceresi (gün)"
              style={{ ...inputStyle, width: 90 }}
            />
          </label>
          <label className="muted" style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
            Araştırma modu
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as ResearchMode)}
              aria-label="Araştırma modu"
              disabled={starting}
              style={{ ...inputStyle, width: 120 }}
            >
              {RESEARCH_MODES.map((m) => (
                <option key={m} value={m}>
                  {RESEARCH_MODE_LABEL[m]}
                </option>
              ))}
            </select>
          </label>
          <label className="muted" style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
            En fazla kaynak (≤ {MAX_MAX_SOURCES})
            <input
              type="number"
              min={MIN_MAX_SOURCES}
              max={MAX_MAX_SOURCES}
              value={maxSources}
              onChange={(e) => setMaxSources(Number(e.target.value))}
              onBlur={() => setMaxSources(clampMaxSources(maxSources))}
              aria-label="En fazla kaynak sayısı"
              style={{ ...inputStyle, width: 90 }}
            />
          </label>
          <button
            onClick={start}
            disabled={starting || !topic.trim()}
            style={{
              padding: "0.6rem 1.2rem",
              borderRadius: 8,
              border: "none",
              background: "var(--accent)",
              color: "#0f1115",
              fontWeight: 600,
              cursor: starting ? "progress" : "pointer",
              opacity: starting || !topic.trim() ? 0.6 : 1,
            }}
          >
            {starting ? "Başlatılıyor…" : "Başlat"}
          </button>
        </div>

        {error && (
          <p style={{ marginTop: "0.75rem", color: "var(--fail)", lineHeight: 1.5 }} className="error">
            {error}
          </p>
        )}
      </div>

      {active && (
        <ProgressPanel
          task={active}
          onCancel={cancel}
          cancelBusy={cancelBusy}
          onPause={pause}
          onResume={resume}
          pauseBusy={pauseBusy}
          pollError={pollError}
        />
      )}

      {active?.report && (
        <div className="panel">
          <ReportView
            report={active.report}
            artifactId={active.artifact_id}
            onCopyJson={copyJson}
            copyState={copyState}
          />
        </div>
      )}

      <h2 style={{ fontSize: "1.1rem", margin: "1.5rem 0 0.5rem" }}>Önceki araştırmalar</h2>
      <FocusStrip
        state={focus}
        now={now}
        notice={focusNotice}
        onSelect={(taskId) => void open(taskId)}
      />
      <TaskList tasks={tasks} focus={focus} now={now} onOpen={open} />
    </main>
  );
}

export default function ResearchPage() {
  return (
    <OwnerGate>
      <ResearchSurface />
    </OwnerGate>
  );
}
