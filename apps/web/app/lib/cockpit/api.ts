"use client";

/**
 * Read-only clients for the cockpit's detail panels.
 *
 * The Core is driven entirely by `/v1/ui/state`. These endpoints answer the
 * different question the cockpit asks — not "what is happening now" but "what
 * currently exists": which goals, which candidates, which facts, what the
 * ledger recorded. Every call here is a GET; nothing in the cockpit mutates
 * anything, and approving a candidate or a goal remains an owner action taken
 * on the surface that owns it.
 *
 * The important type in this file is `Loaded<T>`. A panel must be able to say
 * "there are no goals" and "I could not find out whether there are goals" in
 * different words, because showing an empty list for a failed request is the
 * same class of lie as animating work that is not happening.
 */

import { UnauthorizedError, apiFetch } from "../session";

export type Loaded<T> =
  | { kind: "loading" }
  | { kind: "ok"; value: T; at: number }
  | { kind: "failed"; error: string };

export function isOk<T>(state: Loaded<T>): state is { kind: "ok"; value: T; at: number } {
  return state.kind === "ok";
}

async function getJson<T>(path: string): Promise<T> {
  const response = await apiFetch(path);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as T;
}

/** Never throws; a panel failing must not take the page or its siblings down. */
export async function load<T>(path: string, pick: (raw: unknown) => T): Promise<Loaded<T>> {
  try {
    return { kind: "ok", value: pick(await getJson<unknown>(path)), at: Date.now() };
  } catch (err) {
    if (err instanceof UnauthorizedError) return { kind: "failed", error: "oturum reddedildi" };
    return { kind: "failed", error: err instanceof Error ? err.message : String(err) };
  }
}

function arrayAt<T>(raw: unknown, key: string): T[] {
  if (!raw || typeof raw !== "object") return [];
  const value = (raw as Record<string, unknown>)[key];
  return Array.isArray(value) ? (value as T[]) : [];
}

// --------------------------------------------------------------- research

export type ResearchTask = {
  task_id: string;
  topic: string;
  status: string;
  stage: string;
  device: string | null;
  created_at: string | null;
  ready_at: string | null;
  artifact_id: string | null;
};

export const fetchResearchTasks = () =>
  load<ResearchTask[]>("/v1/research", (raw) => arrayAt<ResearchTask>(raw, "tasks"));

// ------------------------------------------------------------------ goals

export type Goal = {
  goal_id: string;
  title: string;
  status: string;
  priority: number;
  horizon: string;
  deadline: string | null;
  requires_owner_approval: boolean;
  approved_at: string | null;
  success_criteria: unknown[];
  blockers: unknown[];
};

export const GOAL_STATUS_LABEL: Record<string, string> = {
  draft: "taslak",
  active: "etkin",
  blocked: "engelli",
  waiting_owner: "sahibi bekliyor",
  achieved: "başarıldı",
  abandoned: "bırakıldı",
  superseded: "yerine geçildi",
};

export const fetchGoals = () =>
  load<Goal[]>("/v1/goals?limit=100", (raw) => arrayAt<Goal>(raw, "goals"));

// -------------------------------------------------------------- evolution

export type Opportunity = {
  opportunity_id: string;
  title: string;
  status: string;
  scores: { composite?: number } & Record<string, number | undefined>;
  candidate_ref: string | null;
  approved_at: string | null;
  updated_at: string | null;
};

export const fetchOpportunities = () =>
  load<Opportunity[]>("/v1/evolution/opportunities?limit=50", (raw) =>
    arrayAt<Opportunity>(raw, "opportunities"),
  );

export type ShadowReady = { awaiting_approval: Opportunity[]; count: number; note: string };

export const fetchShadowReady = () =>
  load<ShadowReady>("/v1/evolution/shadow-ready?limit=50", (raw) => {
    const o = (raw ?? {}) as Record<string, unknown>;
    return {
      awaiting_approval: arrayAt<Opportunity>(raw, "awaiting_approval"),
      count: typeof o.count === "number" ? o.count : 0,
      note: typeof o.note === "string" ? o.note : "",
    };
  });

// ------------------------------------------------------------ world model

export type WorldFact = {
  key: string;
  category: string;
  value: unknown;
  truth_kind: string;
  observed_at: string;
  confidence: number;
  stale: boolean;
  note: string;
};

export type WorldUncertainty = { category: string; subject: string; reason: string };

export type World = {
  facts: WorldFact[];
  uncertainties: WorldUncertainty[];
  generated_at: string | null;
};

/** The four truth kinds are never averaged (ADR-0053 §2); they are labelled. */
export const TRUTH_KIND_LABEL: Record<string, string> = {
  source_truth: "kaynak",
  installed_truth: "kurulu",
  runtime_truth: "çalışan",
  evidence_truth: "kanıt",
};

export const fetchWorld = () =>
  load<World>("/v1/world", (raw) => {
    const o = (raw ?? {}) as Record<string, unknown>;
    return {
      facts: arrayAt<WorldFact>(raw, "facts"),
      uncertainties: arrayAt<WorldUncertainty>(raw, "uncertainties"),
      generated_at: typeof o.generated_at === "string" ? o.generated_at : null,
    };
  });

// ----------------------------------------------------------------- ledger

export type LedgerEvent = {
  event_id: string;
  occurred_at: string | null;
  event_type: string;
  subsystem: string | null;
  status: string | null;
  severity: string | null;
  factual_summary: string | null;
  production_state: string | null;
};

/**
 * "Meaningful" is defined by the ledger's own status vocabulary rather than by
 * this client's taste: anything that failed, or that a subsystem marked as a
 * completion, is worth the owner's attention. Filtering on prose would be the
 * renderer deciding what matters, which is not its job.
 */
export const MEANINGFUL_STATUSES = new Set(["failed", "completed", "pending"]);

export const fetchLedgerEvents = () =>
  load<LedgerEvent[]>("/v1/ledger/events?limit=40", (raw) =>
    arrayAt<LedgerEvent>(raw, "events"),
  );

export type PendingBriefing = {
  briefing_id: string;
  created_at: string;
  priority: string | number;
  speech: string;
  delivered_at: string | null;
};

export const fetchPendingBriefings = () =>
  load<PendingBriefing[]>("/v1/ledger/briefings/pending", (raw) =>
    arrayAt<PendingBriefing>(raw, "briefings"),
  );

// ----------------------------------------------------------------- memory

export type MemoryAuditEvent = {
  id: number;
  action: string;
  memory_class: string | null;
  key: string | null;
  actor: string | null;
  created_at: string;
};

export const fetchMemoryAudit = () =>
  load<MemoryAuditEvent[]>("/v1/memory/audit?limit=12", (raw) =>
    arrayAt<MemoryAuditEvent>(raw, "events"),
  );

// ------------------------------------------------------------- experience

export type Lesson = {
  lesson_id: string;
  title: string;
  status: string;
  score: number;
  confidence: number;
  recurrence: number;
};

export const fetchLessons = () =>
  load<Lesson[]>("/v1/experience/lessons?limit=10", (raw) => arrayAt<Lesson>(raw, "lessons"));

// ----------------------------------------------------------------- health

export type HealthCheck = { status: string; latency_ms?: number; error?: string };
export type Health = { status: string; version: string; checks: Record<string, HealthCheck> };

export const fetchHealth = () =>
  load<Health>("/v1/system/health", (raw) => {
    const o = (raw ?? {}) as Record<string, unknown>;
    return {
      status: typeof o.status === "string" ? o.status : "unknown",
      version: typeof o.version === "string" ? o.version : "",
      checks:
        o.checks && typeof o.checks === "object"
          ? (o.checks as Record<string, HealthCheck>)
          : {},
    };
  });

/** A check reporting `skipped` is healthy: the subsystem is not configured. */
export function isHealthy(status: string): boolean {
  return status === "ok" || status === "skipped";
}
