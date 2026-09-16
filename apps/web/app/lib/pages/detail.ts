"use client";

/**
 * B24: the read-only clients the FAMILY PAGES need and the cockpit never fetched.
 *
 * Requirements 689/693/694/696/697/698/699 all say the same word — *Sayfa* — and a page
 * that is one cockpit panel in a larger font is not one. The cockpit answers "is anything
 * happening"; a family's own page answers "show me everything you have about this". So
 * each page keeps the panel for the summary and adds the routes below, every one of which
 * the API has served for months with nothing in the product calling it.
 *
 * Everything here is a GET. These pages set no policy, start no work and reach no
 * provider: a page that could change what an alarm does would be a second authority
 * surface beside the voice router, which ADR-0053 §5 forbids. The one exception already
 * in the product — pausing a routine — stays on its existing client.
 *
 * Parsers are deliberately thin: they take the fields the page shows and drop the rest,
 * so an added server field is not a client crash and a missing one is a visible gap
 * rather than `undefined` printed at the owner.
 */

import { type Loaded, load } from "../cockpit/api";

function obj(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

function rowsAt(raw: unknown, key: string): Record<string, unknown>[] {
  const value = obj(raw)[key];
  return Array.isArray(value) ? value.map(obj) : [];
}

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function bool(value: unknown): boolean {
  return value === true;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

// ------------------------------------------------------------------ req 689: memory

export type MemoryRow = {
  memory_id: string;
  memory_class: string | null;
  key: string | null;
  text: string | null;
  stage: string | null;
  status: string | null;
  explicit: boolean;
  pinned: boolean;
  confidence: number | null;
  occurred_at: string | null;
};

function parseMemory(raw: Record<string, unknown>): MemoryRow | null {
  const id = str(raw.memory_id);
  if (!id) return null;
  return {
    memory_id: id,
    memory_class: str(raw.memory_class),
    key: str(raw.key),
    text: str(raw.text),
    stage: str(raw.stage),
    status: str(raw.status),
    explicit: bool(raw.explicit),
    pinned: bool(raw.pinned),
    confidence: num(raw.confidence),
    occurred_at: str(raw.occurred_at),
  };
}

function present<T>(value: T | null): value is T {
  return value !== null;
}

/**
 * What the system remembers.
 *
 * The route is the retrieval path itself (`hybrid_search`), so an empty query is the
 * ordinary listing and a typed one is the same ranking the assistant uses when it answers
 * — the owner sees what the system would have retrieved, not a separate browse view.
 */
export const fetchMemories = (query: string): Promise<Loaded<MemoryRow[]>> => {
  const q = query.trim();
  const path = q ? `/v1/memory/search?limit=25&q=${encodeURIComponent(q)}` : "/v1/memory/search?limit=25";
  return load<MemoryRow[]>(path, (raw) => rowsAt(raw, "results").map(parseMemory).filter(present));
};

export type EntityRow = { entity_id: string; kind: string | null; name: string | null };

export const fetchEntities = (): Promise<Loaded<EntityRow[]>> =>
  load<EntityRow[]>("/v1/memory/entities?limit=100", (raw) =>
    rowsAt(raw, "entities")
      .map((row) => {
        const id = str(row.entity_id);
        return id ? { entity_id: id, kind: str(row.kind), name: str(row.name) } : null;
      })
      .filter(present),
  );

// ------------------------------------------------------------------ req 694: alarms

export type AlarmEventRow = {
  event_id: string;
  event_type: string | null;
  state: string | null;
  occurred_at: string | null;
  alarm_id: string | null;
  summary: string | null;
  reason: string | null;
  is_test: boolean;
};

/**
 * Every alarm occurrence, from the Activity Ledger.
 *
 * Not from the alarm rows: a recurring alarm reuses its row and rewinds its terminal
 * state when it schedules tomorrow, so "did my 07:30 ring on Tuesday" is unanswerable
 * from the table by construction. B13 req 285 put the occurrences in the ledger, and
 * until this page nothing in the product read them.
 */
export const fetchAlarmHistory = (): Promise<Loaded<AlarmEventRow[]>> =>
  load<AlarmEventRow[]>("/v1/alarms/history?limit=40", (raw) =>
    rowsAt(raw, "events")
      .concat(rowsAt(raw, "history"))
      .map((row) => {
        const id = str(row.event_id);
        return id
          ? {
              event_id: id,
              event_type: str(row.event_type),
              state: str(row.state),
              occurred_at: str(row.occurred_at),
              alarm_id: str(row.alarm_id),
              summary: str(row.summary),
              reason: str(row.reason),
              is_test: bool(row.is_test),
            }
          : null;
      })
      .filter(present),
  );

// ----------------------------------------------------------------- req 696: security

export type SecurityAssetRow = {
  asset_ref: string;
  name: string | null;
  kind: string | null;
  environment: string | null;
  status: string | null;
  valid_until: string | null;
};

export const fetchSecurityAssets = (): Promise<Loaded<SecurityAssetRow[]>> =>
  load<SecurityAssetRow[]>("/v1/security/assets?limit=200", (raw) =>
    rowsAt(raw, "assets")
      .map((row) => {
        const ref = str(row.asset_ref);
        return ref
          ? {
              asset_ref: ref,
              name: str(row.name),
              kind: str(row.kind),
              environment: str(row.environment),
              status: str(row.status),
              valid_until: str(row.valid_until),
            }
          : null;
      })
      .filter(present),
  );

export type SecurityFindingRow = {
  id: string;
  title: string | null;
  severity: string | null;
  status: string | null;
  created_at: string | null;
  resolved_at: string | null;
};

export const fetchSecurityFindings = (): Promise<Loaded<SecurityFindingRow[]>> =>
  load<SecurityFindingRow[]>("/v1/security/findings?limit=100", (raw) =>
    rowsAt(raw, "findings")
      .map((row) => {
        const id = str(row.id);
        return id
          ? {
              id,
              title: str(row.title),
              severity: str(row.severity),
              status: str(row.status),
              created_at: str(row.created_at),
              resolved_at: str(row.resolved_at),
            }
          : null;
      })
      .filter(present),
  );

export type SecurityAssessmentRow = {
  id: string;
  testing_class: string | null;
  status: string | null;
  target: string | null;
  created_at: string | null;
  completed_at: string | null;
};

export const fetchSecurityAssessments = (): Promise<Loaded<SecurityAssessmentRow[]>> =>
  load<SecurityAssessmentRow[]>("/v1/security/assessments?limit=50", (raw) =>
    rowsAt(raw, "assessments")
      .map((row) => {
        const id = str(row.id);
        return id
          ? {
              id,
              testing_class: str(row.testing_class),
              status: str(row.status),
              target: str(row.target),
              created_at: str(row.created_at),
              completed_at: str(row.completed_at),
            }
          : null;
      })
      .filter(present),
  );

export type AuthorizationEventRow = {
  id: string;
  action: string | null;
  asset_ref: string | null;
  requested_target: string | null;
  allowed: boolean;
  reason: string | null;
  created_at: string | null;
};

/**
 * The append-only authorization trail — grants, scope changes AND every refusal.
 *
 * The refusals are the half worth a page: "security testing is allowed only for assets
 * recorded as owner/enrolled/authorized" is a product invariant, and the owner has had no
 * way to see it being enforced.
 */
export const fetchSecurityAudit = (): Promise<Loaded<AuthorizationEventRow[]>> =>
  load<AuthorizationEventRow[]>("/v1/security/audit?limit=60", (raw) =>
    rowsAt(raw, "events")
      .map((row) => {
        const id = str(row.id) ?? (num(row.id) !== null ? String(row.id) : null);
        return id
          ? {
              id,
              action: str(row.action),
              asset_ref: str(row.asset_ref),
              requested_target: str(row.requested_target),
              allowed: bool(row.allowed),
              reason: str(row.reason),
              created_at: str(row.created_at),
            }
          : null;
      })
      .filter(present),
  );

// ------------------------------------------------------------------ req 697: selfdev

export type CapabilityGapRow = {
  id: string;
  requested_capability: string | null;
  status: string | null;
  resolution: string | null;
  created_at: string | null;
  resolved_at: string | null;
};

export const fetchCapabilityGaps = (): Promise<Loaded<CapabilityGapRow[]>> =>
  load<CapabilityGapRow[]>("/v1/evolution/gaps?limit=50", (raw) =>
    rowsAt(raw, "gaps")
      .map((row) => {
        const id = str(row.id);
        return id
          ? {
              id,
              requested_capability: str(row.requested_capability),
              status: str(row.status),
              resolution: str(row.resolution),
              created_at: str(row.created_at),
              resolved_at: str(row.resolved_at),
            }
          : null;
      })
      .filter(present),
  );

export type SkillVersionRow = {
  id: string;
  capability_id: string | null;
  version: string | null;
  status: string | null;
  rejected_reason: string | null;
  registered_at: string | null;
};

export const fetchSkillVersions = (): Promise<Loaded<SkillVersionRow[]>> =>
  load<SkillVersionRow[]>("/v1/evolution/skill-versions?limit=50", (raw) =>
    rowsAt(raw, "skill_versions")
      .concat(rowsAt(raw, "versions"))
      .map((row) => {
        const id = str(row.id);
        return id
          ? {
              id,
              capability_id: str(row.capability_id),
              version: str(row.version) ?? (num(row.version) !== null ? String(row.version) : null),
              status: str(row.status),
              rejected_reason: str(row.rejected_reason),
              registered_at: str(row.registered_at),
            }
          : null;
      })
      .filter(present),
  );

// ----------------------------------------------------------------- req 699: settings

/**
 * A policy document, as name/value pairs.
 *
 * Six subsystems publish a `/policy` route saying what vocabulary and bounds THIS Cloud
 * Core understands, so a caller never has to guess. They are read as a flat map on
 * purpose: the settings page's honest job is to show the owner what the running system's
 * rules are, and inventing a bespoke shape per subsystem would mean the page silently
 * drops a rule the server added.
 */
export type PolicyEntry = { name: string; value: string };

function describe(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length === 0 ? "—" : value.map(describe).join(", ");
  if (typeof value === "boolean") return value ? "açık" : "kapalı";
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    return entries.length === 0 ? "—" : entries.map(([k, v]) => `${k}: ${describe(v)}`).join(" · ");
  }
  return String(value);
}

export function policyEntries(raw: unknown): PolicyEntry[] {
  return Object.entries(obj(raw))
    .map(([name, value]) => ({ name, value: describe(value) }))
    .sort((a, b) => a.name.localeCompare(b.name, "tr-TR"));
}

export const fetchPolicy = (path: string): Promise<Loaded<PolicyEntry[]>> =>
  load<PolicyEntry[]>(path, policyEntries);
