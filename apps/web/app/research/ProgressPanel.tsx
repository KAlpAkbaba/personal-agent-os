"use client";

import {
  type ResearchTaskDetail,
  describeTaskError,
  formatWhen,
  isTerminal,
  stageLabel,
} from "../lib/research/model";

/**
 * Live progress of one research task: Turkish stage label, counters, the
 * chosen device and the last events. Shows readiness only — the report is a
 * separate view the owner opens (constitution: notify briefly and wait).
 */

const STAGE_CLASS: Record<string, string> = {
  ready: "ok",
  failed: "fail",
  cancelled: "unknown",
};

export type ProgressPanelProps = {
  task: ResearchTaskDetail;
  onCancel?: () => void;
  cancelBusy?: boolean;
  /** B31 req 203/204: pause while running, resume while paused. */
  onPause?: () => void;
  onResume?: () => void;
  pauseBusy?: boolean;
  pollError?: string | null;
  maxEvents?: number;
};

export const PAUSED_NOTICE = "Araştırma duraklatıldı; devam edince kaldığı aşamadan sürer.";

function counter(label: string, value: number | undefined, total?: number | undefined) {
  if (value == null && total == null) return null;
  return (
    <span key={label} className="muted" style={{ whiteSpace: "nowrap" }}>
      {label}: {value ?? 0}
      {total != null && ` / ${total}`}
    </span>
  );
}

export default function ProgressPanel({
  task,
  onCancel,
  cancelBusy,
  onPause,
  onResume,
  pauseBusy,
  pollError,
  maxEvents = 8,
}: ProgressPanelProps) {
  const terminal = isTerminal(task);
  const progress = task.progress ?? {};
  const paused = progress.paused === true;
  const events = (task.events ?? []).slice(-maxEvents).toReversed();
  const error = describeTaskError(task.error);
  const stage = String(task.stage ?? "");

  return (
    <div className="panel progress" data-stage={stage} data-terminal={terminal ? "yes" : "no"}>
      <div className="status-row" style={{ borderBottom: "none", paddingTop: 0 }}>
        <strong style={{ lineHeight: 1.4 }}>{task.topic}</strong>
        <span className={`badge ${STAGE_CLASS[stage] ?? "unknown"}`} data-stage-label>
          {stageLabel(stage)}
          {!terminal && "…"}
        </span>
      </div>

      <div style={{ display: "flex", gap: "0.9rem", flexWrap: "wrap", margin: "0.25rem 0" }}>
        {counter("sorgu", progress.queries_done, progress.queries_total)}
        {counter("keşfedilen", progress.discovered)}
        {counter("getirilen", progress.fetch_done, progress.fetch_total)}
        {counter("başarısız", progress.fetch_failed)}
        {counter("kanıt", progress.evidence)}
      </div>

      <p className="muted" style={{ margin: "0.25rem 0" }}>
        Cihaz:{" "}
        {task.device ? (
          <span data-device-id={task.device.device_id}>{task.device.name}</span>
        ) : (
          <span>henüz seçilmedi</span>
        )}
        {task.artifact_id && <> · artifact hazır</>}
      </p>

      {error && (
        <p style={{ margin: "0.5rem 0", color: "var(--fail)" }} className="task-error">
          {error}
        </p>
      )}
      {!terminal && paused && (
        <p className="notice" data-paused="yes" style={{ margin: "0.5rem 0", color: "var(--warn, #b26a00)" }}>
          {PAUSED_NOTICE}
        </p>
      )}
      {!terminal && (onPause || onResume) && (
        <p style={{ margin: "0.5rem 0" }}>
          {paused
            ? onResume && (
                <button type="button" onClick={onResume} disabled={pauseBusy} data-action="resume">
                  Devam et
                </button>
              )
            : onPause && (
                <button type="button" onClick={onPause} disabled={pauseBusy} data-action="pause">
                  Duraklat
                </button>
              )}
        </p>
      )}
      {stage === "waiting_for_owner_verification" && (
        <p
          className="notice"
          style={{
            margin: "0.5rem 0",
            padding: "0.6rem 0.8rem",
            borderRadius: 8,
            border: "1px solid var(--warn, #b26a00)",
            background: "rgba(232, 179, 57, 0.1)",
            color: "var(--warn, #b26a00)",
            lineHeight: 1.5,
          }}
        >
          Google bir doğrulama sayfası gösterdi. Chrome penceresi öne getirildi; sayfayı
          tamamlayın, araştırma otomatik olarak devam eder.
          {progress.verification_url && (
            <>
              {" "}
              <a
                href={progress.verification_url}
                target="_blank"
                rel="noreferrer noopener"
                style={{ color: "inherit", textDecoration: "underline" }}
              >
                Doğrulama sayfasını aç
              </a>
            </>
          )}
        </p>
      )}
      {stage === "ready" && task.report && (
        <p style={{ margin: "0.5rem 0", color: "var(--ok)" }}>
          Araştırma tamamlandı. Rapor aşağıda; artifact gelen kutusuna da düştü.
        </p>
      )}
      {stage === "ready" && !task.report && (
        <p style={{ margin: "0.5rem 0", color: "var(--warn, #b26a00)" }}>
          Araştırma tamamlandı ama rapor kaydı bulunamadı; sayfayı yenileyin veya görevi yeniden
          açın.
        </p>
      )}
      {pollError && (
        <p className="muted" style={{ margin: "0.25rem 0" }}>
          Durum alınamadı, yeniden denenecek: {pollError}
        </p>
      )}

      {events.length > 0 && (
        <ul className="events" style={{ paddingLeft: "1.1rem", margin: "0.5rem 0 0" }}>
          {events.map((e, i) => (
            <li key={i} className="muted" style={{ lineHeight: 1.5 }}>
              <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
                {formatWhen(e.at)}
              </span>{" "}
              · {stageLabel(e.stage)}
              {e.detail && <> — {e.detail}</>}
            </li>
          ))}
        </ul>
      )}

      {!terminal && onCancel && (
        <button
          type="button"
          onClick={onCancel}
          disabled={cancelBusy}
          style={{
            marginTop: "0.75rem",
            background: "none",
            border: "1px solid #232734",
            borderRadius: 8,
            color: "var(--fail)",
            cursor: cancelBusy ? "progress" : "pointer",
            padding: "0.3rem 0.7rem",
            fontSize: "0.85rem",
          }}
        >
          {cancelBusy ? "İptal ediliyor…" : "İptal et"}
        </button>
      )}
    </div>
  );
}
