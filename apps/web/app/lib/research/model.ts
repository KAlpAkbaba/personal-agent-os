/**
 * Research surface model (M13 track F, `docs/M13_RESEARCH_SPEC.md` §3/§4/§8).
 *
 * Pure types, labels and helpers shared by the /research page and its tests.
 * Nothing here touches the network or the DOM; the API client lives in
 * `./api.ts` and polling in `./poll.ts`.
 */

// ------------------------------------------------------------------ constants

/** The first real owner use case; prefilled into the topic box (spec §8). */
export const DEFAULT_TOPIC =
  "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeleri araştır.";

export const BROWSER_CAPABILITY = "browser.chrome";

export const DEFAULT_RECENCY_DAYS = 3;
export const MIN_RECENCY_DAYS = 1;
export const MAX_RECENCY_DAYS = 14;

export const DEFAULT_MAX_SOURCES = 12;
export const MIN_MAX_SOURCES = 1;
export const MAX_MAX_SOURCES = 30;

/** Polling cadence for a task that is not terminal yet (spec: 3 s). */
export const POLL_INTERVAL_MS = 3000;

// ---------------------------------------------------------------- statements

export type StatementLabel =
  | "source_fact"
  | "model_inference"
  | "recommendation"
  | "uncertainty";

export const STATEMENT_LABELS: readonly StatementLabel[] = [
  "source_fact",
  "model_inference",
  "recommendation",
  "uncertainty",
];

/** Turkish badge text per label (spec §8: every statement shows its label). */
export const LABEL_BADGE: Record<StatementLabel, string> = {
  source_fact: "kaynak bulgusu",
  model_inference: "model çıkarımı",
  recommendation: "öneri",
  uncertainty: "belirsizlik",
};

export function labelBadge(label: string): string {
  return (LABEL_BADGE as Record<string, string>)[label] ?? label;
}

export type Statement = {
  text: string;
  label: StatementLabel;
  evidence_ids: string[];
  provenance_note?: string | null;
};

export type Finding = {
  id: string;
  title: string;
  summary: string;
  why_it_matters: string;
  importance: number;
  label: StatementLabel;
  evidence_ids: string[];
  first_seen?: string | null;
  provenance_note?: string | null;
};

export type DetailSection = {
  heading: string;
  statements: Statement[];
};

export type SourceClass = "official" | "technical" | "academic" | "news" | "community";

export type ReportSource = {
  id: string;
  url: string;
  final_url?: string | null;
  title?: string | null;
  publisher?: string | null;
  source_class?: SourceClass | string | null;
  published_at?: string | null;
  retrieved_at?: string | null;
  excerpt?: string | null;
  device_id?: string | null;
  command_id?: string | null;
  injection_suspected?: boolean;
  syndicated_of?: string | null;
};

export type ReportStats = {
  queries?: number;
  discovered?: number;
  fetched?: number;
  fetch_failed?: number;
  deduplicated?: number;
  evidence?: number;
};

export type ResearchReport = {
  schema_version: number;
  task_id: string;
  topic: string;
  window?: { start?: string; end?: string; label?: string } | null;
  generated_at?: string | null;
  synthesis_provider?: string | null;
  executive_summary: string;
  findings: Finding[];
  why_it_matters: Statement[];
  watch_next: Statement[];
  details: DetailSection[];
  uncertainty: Statement[];
  sources: ReportSource[];
  stats?: ReportStats | null;
};

export const SOURCE_CLASS_LABEL: Record<string, string> = {
  official: "resmî",
  technical: "teknik",
  academic: "akademik",
  news: "haber",
  community: "topluluk",
};

export function sourceClassLabel(value: string | null | undefined): string {
  if (!value) return "kaynak";
  return SOURCE_CLASS_LABEL[value] ?? value;
}

/** DOM id of a source row; citation chips link to `#${sourceAnchorId(id)}`. */
export function sourceAnchorId(sourceId: string): string {
  return `src-${sourceId}`;
}

export type Citation = {
  id: string;
  source: ReportSource | null;
};

/**
 * Resolve `[eN]` evidence ids against the report's sources. Unknown ids are
 * kept (with `source: null`) rather than dropped: a dangling citation is a
 * visible defect in the report, not something the UI should paper over.
 */
export function citationsFor(
  evidenceIds: readonly string[] | undefined,
  sources: readonly ReportSource[],
): Citation[] {
  const byId = new Map(sources.map((s) => [s.id, s]));
  return (evidenceIds ?? []).map((id) => ({ id, source: byId.get(id) ?? null }));
}

/** 1–5 importance rendered as filled/empty dots; out-of-range values clamp. */
export function importanceDots(importance: number): string {
  const n = Math.min(5, Math.max(0, Math.round(Number(importance) || 0)));
  return "●".repeat(n) + "○".repeat(5 - n);
}

// -------------------------------------------------------------------- stages

export type ResearchStage =
  | "planned"
  | "selecting_device"
  | "discovering"
  | "fetching"
  | "ranking"
  | "synthesizing"
  | "persisting"
  | "ready"
  | "failed"
  | "cancelled";

export const STAGE_LABEL: Record<ResearchStage, string> = {
  planned: "Planlandı",
  selecting_device: "Cihaz seçiliyor",
  discovering: "Kaynaklar keşfediliyor",
  fetching: "Sayfalar Chrome ile getiriliyor",
  ranking: "Kaynaklar sıralanıyor",
  synthesizing: "Rapor yazılıyor",
  persisting: "Kaydediliyor",
  ready: "Hazır",
  failed: "Başarısız",
  cancelled: "İptal edildi",
};

export const TERMINAL_STAGES: ReadonlySet<string> = new Set(["ready", "failed", "cancelled"]);

/** Task `status` values that end polling even when `stage` lags behind. */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "READY",
  "FAILED",
  "FAILED_TERMINAL",
  "CANCELLED",
  "ready",
  "failed",
  "cancelled",
]);

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "Bilinmiyor";
  return (STAGE_LABEL as Record<string, string>)[stage] ?? stage;
}

export function isTerminal(task: { stage?: string | null; status?: string | null }): boolean {
  if (task.stage && TERMINAL_STAGES.has(task.stage)) return true;
  if (task.status && TERMINAL_STATUSES.has(task.status)) return true;
  return false;
}

// --------------------------------------------------------------------- tasks

export type DeviceRef = { device_id: string; name: string } | null;

export type ResearchProgress = {
  queries_total?: number;
  queries_done?: number;
  discovered?: number;
  fetch_total?: number;
  fetch_done?: number;
  fetch_failed?: number;
  evidence?: number;
};

export type ResearchEvent = { at: string; stage: string; detail?: string | null };

export type ResearchTaskSummary = {
  task_id: string;
  topic: string;
  status: string;
  stage: ResearchStage | string;
  device: DeviceRef;
  created_at?: string | null;
  ready_at?: string | null;
  artifact_id?: string | null;
};

export type ResearchTaskDetail = {
  task_id: string;
  topic: string;
  status: string;
  stage: ResearchStage | string;
  progress?: ResearchProgress | null;
  device: DeviceRef;
  plan?: unknown;
  report: ResearchReport | null;
  artifact_id?: string | null;
  memory_id?: string | null;
  error?: string | { code?: string; message?: string; detail?: string } | null;
  events?: ResearchEvent[];
};

export type StartResearchResponse = {
  task_id: string;
  workflow_id?: string;
  status: string;
  device: DeviceRef;
};

/** Turkish, single-line description of an error object from the task detail. */
export function describeTaskError(error: ResearchTaskDetail["error"]): string | null {
  if (!error) return null;
  if (typeof error === "string") return error;
  return error.detail ?? error.message ?? error.code ?? null;
}

// ------------------------------------------------------------------- devices

export type DevicePresence = "online" | "stale" | "offline" | "revoked" | "unknown";

export type DeviceInfo = {
  device_id: string;
  name: string;
  platform?: string | null;
  status?: string | null;
  presence?: string | null;
  capabilities?: string[] | null;
  aliases?: string[] | null;
  labels?: string[] | Record<string, unknown> | null;
  health?: Record<string, unknown> | null;
  policy?: Record<string, unknown> | null;
  last_seen_at?: string | null;
};

export const PRESENCE_LABEL: Record<DevicePresence, string> = {
  online: "çevrimiçi",
  stale: "yanıt gecikiyor",
  offline: "çevrimdışı",
  revoked: "iptal edilmiş",
  unknown: "bilinmiyor",
};

/**
 * `presence` is the M13 field; older API builds only have `status`
 * (online/offline/revoked). Read the richer one and fall back.
 */
export function presenceOf(device: Pick<DeviceInfo, "presence" | "status">): DevicePresence {
  const raw = (device.presence ?? device.status ?? "").toLowerCase();
  if (raw === "online" || raw === "stale" || raw === "offline" || raw === "revoked") return raw;
  return "unknown";
}

export function advertisesBrowser(device: Pick<DeviceInfo, "capabilities">): boolean {
  return (device.capabilities ?? []).includes(BROWSER_CAPABILITY);
}

export function aliasesOf(device: Pick<DeviceInfo, "aliases">): string[] {
  return (device.aliases ?? []).filter((a) => typeof a === "string" && a.trim().length > 0);
}

/** Result of `POST /v1/devices/select`, normalised for display. */
export type SelectionPreview =
  | { kind: "device"; device_id: string; name: string; reason?: string | null }
  | { kind: "none"; detail: string };

/**
 * The selection endpoint returns "the selection result"; accept the natural
 * shapes (`{device:{…}}`, `{device_id,name}`, `{selected:{…}}`) so the page
 * keeps working across the API's own iterations.
 */
export function normaliseSelection(payload: unknown): SelectionPreview {
  const obj = (payload ?? {}) as Record<string, unknown>;
  const candidate = (obj.device ?? obj.selected ?? obj) as Record<string, unknown> | null;
  if (candidate && typeof candidate.device_id === "string") {
    const reason = typeof obj.reason === "string" ? obj.reason : null;
    return {
      kind: "device",
      device_id: candidate.device_id,
      name: typeof candidate.name === "string" ? candidate.name : candidate.device_id,
      reason,
    };
  }
  const detail =
    typeof obj.detail === "string"
      ? obj.detail
      : typeof obj.reason === "string"
        ? obj.reason
        : "Uygun cihaz bulunamadı.";
  return { kind: "none", detail };
}

// ------------------------------------------------------------------- inputs

export function clampInt(value: number, min: number, max: number, fallback: number): number {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

export function clampRecencyDays(value: number): number {
  return clampInt(value, MIN_RECENCY_DAYS, MAX_RECENCY_DAYS, DEFAULT_RECENCY_DAYS);
}

export function clampMaxSources(value: number): number {
  return clampInt(value, MIN_MAX_SOURCES, MAX_MAX_SOURCES, DEFAULT_MAX_SOURCES);
}

/** Owner-facing local time; the API speaks ISO-8601 UTC. */
export function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" });
}
