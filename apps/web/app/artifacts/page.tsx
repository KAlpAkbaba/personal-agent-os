"use client";

import { useCallback, useEffect, useState } from "react";

import OwnerGate, { SignOutButton } from "../components/OwnerGate";
import { UnauthorizedError, apiFetch } from "../lib/session";

type RenderInfo = {
  format: string;
  mime_type: string;
  size_bytes: number;
};

type ArtifactSummary = {
  artifact_id: string;
  task_id: string | null;
  title: string;
  state: string;
  executive_summary: string | null;
  available_renders: RenderInfo[];
  updated_at: string | null;
};

type TaskStatus = {
  task_id: string;
  status: string;
  artifact_id?: string | null;
};

// The inbox deliberately mirrors the product's "notify briefly and wait"
// rule: it lists artifacts with their executive summary only. The full
// report body is never auto-loaded — the owner opens a render on demand.
//
// M9: every call goes through `apiFetch`, which attaches the owner session and
// clears it on a 401. A cleared session re-renders `OwnerGate` into the
// sign-in panel, so an expired or revoked session asks the owner to
// authenticate instead of showing an empty inbox and a network error.
function ArtifactInbox() {
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [topic, setTopic] = useState("");
  const [pendingTask, setPendingTask] = useState<TaskStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const report = useCallback((err: unknown) => {
    // A 401 is not an error to show: OwnerGate has already taken over.
    if (err instanceof UnauthorizedError) return;
    setError(err instanceof Error ? err.message : String(err));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const res = await apiFetch("/v1/artifacts");
      const data = await res.json();
      setArtifacts(data.artifacts ?? []);
      setError(null);
    } catch (err) {
      report(err);
    }
  }, [report]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll a freshly created task until it reaches a terminal state, then
  // refresh the list. We show only readiness — never the body.
  useEffect(() => {
    if (!pendingTask || ["READY", "FAILED_TERMINAL"].includes(pendingTask.status)) {
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const res = await apiFetch(`/v1/tasks/${pendingTask.task_id}`);
        const data = (await res.json()) as TaskStatus;
        setPendingTask(data);
        if (data.status === "READY") refresh();
      } catch (err) {
        if (err instanceof UnauthorizedError) return; // stop polling; sign in
        /* otherwise keep polling */
      }
    }, 1500);
    return () => clearTimeout(timer);
  }, [pendingTask, refresh]);

  const startResearch = useCallback(async () => {
    if (!topic.trim()) return;
    setError(null);
    try {
      const res = await apiFetch("/v1/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: topic.trim() }),
      });
      const data = (await res.json()) as TaskStatus;
      setPendingTask(data);
      setTopic("");
    } catch (err) {
      report(err);
    }
  }, [topic, report]);

  // Renders are behind the owner session too, so a plain <a href> now 401s.
  // Fetch the bytes with the token and hand the browser a blob URL instead.
  const openRender = useCallback(
    async (artifactId: string, format: string) => {
      try {
        const res = await apiFetch(`/v1/artifacts/${artifactId}/renders/${format}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        window.open(url, "_blank", "noreferrer");
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } catch (err) {
        report(err);
      }
    },
    [report],
  );

  return (
    <main>
      <div className="status-row" style={{ borderBottom: "none", paddingBottom: 0 }}>
        <h1 style={{ margin: 0 }}>Araştırma Gelen Kutusu</h1>
        <SignOutButton />
      </div>
      <p className="subtitle">
        Bir konu ver, hazır olduğunda burada yönetici özetiyle görünür.
      </p>

      <div className="panel">
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && startResearch()}
            placeholder="Örn: Son üç günde yapay zekâ ajanlarındaki gelişmeler"
            aria-label="Araştırma konusu"
            style={{
              flex: 1,
              padding: "0.6rem 0.8rem",
              borderRadius: 8,
              border: "1px solid #232734",
              background: "#0f1115",
              color: "var(--text)",
            }}
          />
          <button
            onClick={startResearch}
            style={{
              padding: "0.6rem 1.2rem",
              borderRadius: 8,
              border: "none",
              background: "var(--accent)",
              color: "#0f1115",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Araştır
          </button>
        </div>
        {pendingTask && pendingTask.status !== "READY" && (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            Görev durumu: {pendingTask.status}… (hazır olunca bildirilecek)
          </p>
        )}
        {pendingTask && pendingTask.status === "READY" && (
          <p style={{ marginTop: "0.75rem", color: "var(--ok)" }}>
            Araştırma tamamlandı. Rapor hazır.
          </p>
        )}
        {error && (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            API hatası: {error}
          </p>
        )}
      </div>

      {artifacts.length === 0 && <p className="muted">Henüz artifact yok.</p>}

      {artifacts.map((a) => (
        <div className="panel" key={a.artifact_id}>
          <div className="status-row">
            <strong>{a.title}</strong>
            <span className="badge ok">{a.state}</span>
          </div>
          {a.executive_summary && (
            <p style={{ margin: "0.5rem 0", lineHeight: 1.5 }}>
              {expanded[a.artifact_id]
                ? a.executive_summary
                : a.executive_summary.slice(0, 240) +
                  (a.executive_summary.length > 240 ? "…" : "")}
            </p>
          )}
          {a.executive_summary && a.executive_summary.length > 240 && (
            <button
              onClick={() =>
                setExpanded((s) => ({ ...s, [a.artifact_id]: !s[a.artifact_id] }))
              }
              style={{
                background: "none",
                border: "none",
                color: "var(--accent)",
                cursor: "pointer",
                padding: 0,
                fontSize: "0.85rem",
              }}
            >
              {expanded[a.artifact_id] ? "Daha az" : "Özetin tamamı"}
            </button>
          )}
          <div
            style={{
              marginTop: "0.75rem",
              display: "flex",
              gap: "0.5rem",
              flexWrap: "wrap",
            }}
          >
            {a.available_renders.map((r) => (
              <button
                key={r.format}
                onClick={() => openRender(a.artifact_id, r.format)}
                className="badge ok"
                style={{ border: "none", cursor: "pointer", font: "inherit" }}
              >
                {r.format.toUpperCase()} ·{" "}
                {Math.max(1, Math.round(r.size_bytes / 1024))} KB
              </button>
            ))}
          </div>
        </div>
      ))}
    </main>
  );
}

export default function ArtifactsPage() {
  return (
    <OwnerGate>
      <ArtifactInbox />
    </OwnerGate>
  );
}
