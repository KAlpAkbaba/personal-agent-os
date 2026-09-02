"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { API_BASE } from "./lib/session";

// NB: /v1/system/health is the one endpoint that is deliberately reachable
// without an owner session (ADR-0027), so this page needs no sign-in. Every
// other surface goes through `apiFetch` behind `OwnerGate`.

type CheckResult = {
  status: "ok" | "fail";
  latency_ms?: number;
};

type HealthResponse = {
  status: "ok" | "degraded";
  version: string;
  checks: Record<string, CheckResult>;
};

type FetchState =
  | { kind: "loading" }
  | { kind: "unreachable"; error: string }
  | { kind: "loaded"; health: HealthResponse };

function Badge({ status }: { status: string }) {
  const cls =
    status === "ok" ? "ok" : status === "fail" ? "fail" : "unknown";
  return <span className={`badge ${cls}`}>{status}</span>;
}

export default function Home() {
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/v1/system/health`, {
        cache: "no-store",
      });
      const health = (await res.json()) as HealthResponse;
      setState({ kind: "loaded", health });
    } catch (err) {
      setState({
        kind: "unreachable",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  return (
    <main>
      <h1>Personal Agent OS</h1>
      <p className="subtitle">
        Web shell —{" "}
        <Link href="/artifacts" style={{ color: "var(--accent)" }}>
          Araştırma Gelen Kutusu →
        </Link>
        {" · "}
        <Link href="/voice" style={{ color: "var(--accent)" }}>
          Sesli Asistan →
        </Link>
      </p>

      <div className="panel">
        <div className="status-row">
          <strong>Cloud core API</strong>
          {state.kind === "loading" && <Badge status="..." />}
          {state.kind === "unreachable" && <Badge status="fail" />}
          {state.kind === "loaded" && <Badge status={state.health.status} />}
        </div>
        {state.kind === "unreachable" && (
          <p className="muted">
            API erişilemez ({API_BASE}): {state.error}
          </p>
        )}
        {state.kind === "loaded" &&
          Object.entries(state.health.checks).map(([name, check]) => (
            <div className="status-row" key={name}>
              <span>{name}</span>
              <span>
                {check.latency_ms != null && (
                  <span className="muted">{check.latency_ms} ms&nbsp;&nbsp;</span>
                )}
                <Badge status={check.status} />
              </span>
            </div>
          ))}
      </div>

      {state.kind === "loaded" && (
        <p className="muted">API sürümü: {state.health.version}</p>
      )}
    </main>
  );
}
