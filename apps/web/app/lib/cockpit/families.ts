/**
 * B24 req 714: the twenty-seven families, and the one rule about showing them.
 *
 * The audit counted it and the matrix records it: **13 of the cockpit's 27 panels were
 * empty**. Not broken — empty. Each one drew a title, a badge and a sentence saying there
 * was nothing, and thirteen of those stacked on top of each other is a page that reads as
 * a system doing nothing, on a system that is doing plenty. The requirement's target is
 * one line long and says exactly what to do about it: *"Boş aile panel doğurmaz."*
 *
 * So a family that has nothing to show renders no panel at all. What it does NOT do is
 * disappear: `/availability` (req 700) lists every one of the 27 with what it answered
 * just now, so "where did the alarms panel go" has an address rather than a guess. The
 * two surfaces read the same live data through `useCockpitData`; this module is what they
 * both mean by "a family" and by "quiet".
 *
 * Two distinctions this file keeps, because collapsing either is the lie `Loaded` exists
 * to prevent:
 *
 * * **failed is never quiet.** A family we could not ask about is not a family with
 *   nothing in it. Its panel stays, with B22's class, hint and retry.
 * * **loading is never quiet.** Hiding a panel for the 200 ms before the first answer
 *   would make the page flash its own contents away.
 */

import type { Loaded } from "./api";
import type { CockpitData } from "./useCockpitData";

/** The `Loaded<T>` payload type, so a predicate can be written against the real value. */
type Value<L> = L extends { kind: "ok"; value: infer T } ? T : never;

export type FamilyKey = keyof CockpitData;

/**
 * What a family answered.
 *
 * `ready` and `empty` are both successful requests; the difference is whether there is
 * anything to draw. `absent` is a route this Cloud Core does not serve, `failed` is a
 * route that answered badly, and `loading` is not an answer at all.
 */
export type FamilyQuality = "loading" | "ready" | "empty" | "absent" | "failed";

export type Family = {
  key: FamilyKey;
  /** The panel's own title, so a list of hidden families uses the words the owner knows. */
  label: string;
  /** The hidden panel's `data-panel` id — the anchor the availability page links to. */
  panel: string;
  /** The page that shows this family in full. */
  page: string;
};

/**
 * Every family, its panel and the page that owns it.
 *
 * The keys must be exactly the keys of `CockpitData`, and a test proves it by reading the
 * OTHER file's source rather than importing a list from it — a list imported from the
 * thing under test cannot disagree with it.
 */
export const FAMILIES: readonly Family[] = [
  { key: "research", label: "Araştırma", panel: "research", page: "/research" },
  { key: "researchFocus", label: "Araştırma odağı", panel: "research", page: "/research" },
  { key: "goals", label: "Hedefler", panel: "goals", page: "/core/cockpit" },
  { key: "opportunities", label: "Evrim", panel: "evolution", page: "/selfdev" },
  { key: "shadowReady", label: "Onay merkezi", panel: "shadow-ready", page: "/selfdev" },
  { key: "world", label: "Dünya modeli", panel: "world", page: "/core/cockpit" },
  { key: "ledger", label: "Defter", panel: "ledger", page: "/core/cockpit" },
  { key: "briefings", label: "Sahip işlemleri", panel: "owner-actions", page: "/core/cockpit" },
  { key: "memory", label: "Hafıza", panel: "memory", page: "/memory" },
  { key: "lessons", label: "Dersler", panel: "lessons", page: "/selfdev" },
  { key: "health", label: "Sistem sağlığı", panel: "health", page: "/core/cockpit" },
  { key: "alarms", label: "Alarmlar", panel: "alarms", page: "/alarms" },
  { key: "ambientPolicy", label: "Ekran / Ortam", panel: "ambient", page: "/settings" },
  { key: "devices", label: "Cihazlar", panel: "devices", page: "/core/cockpit" },
  {
    key: "voiceQualification",
    label: "Ses yönlendirme sınaması",
    panel: "voice-qualification",
    page: "/settings",
  },
  {
    key: "evolutionSupervisor",
    label: "Evrim gözetmeni",
    panel: "evolution-supervisor",
    page: "/selfdev",
  },
  { key: "mailDrafts", label: "Posta", panel: "mail", page: "/core/cockpit" },
  { key: "calendarProposals", label: "Takvim", panel: "calendar", page: "/core/cockpit" },
  { key: "documentMutations", label: "Dosya değişiklikleri", panel: "documents", page: "/core/cockpit" },
  { key: "artifacts", label: "Üretilenler", panel: "artifacts", page: "/artifacts" },
  { key: "apps", label: "Uygulamalar", panel: "apps", page: "/core/cockpit" },
  { key: "genesisRuns", label: "Yeni Yetenek", panel: "genesis", page: "/selfdev" },
  { key: "scenes", label: "3B Sahne", panel: "scenes", page: "/core/cockpit" },
  { key: "executiveRuns", label: "Görevler", panel: "executive", page: "/core/cockpit" },
  { key: "creativeRuns", label: "Yaratıcı", panel: "creative", page: "/core/cockpit" },
  { key: "nativeBuilds", label: "Yerel Uygulamalar", panel: "native", page: "/core/cockpit" },
  { key: "notifications", label: "Bildirimler", panel: "notifications", page: "/notifications" },
  { key: "routines", label: "Rutinler", panel: "routines", page: "/routines" },
];

/**
 * When a loaded family holds nothing to draw.
 *
 * Written once per family and type-checked against `CockpitData`'s real payloads, so a
 * family whose shape changes fails here rather than quietly answering "not empty" for
 * ever. Each of these matches the `isEmpty` its panel already passed to `Panel`; the test
 * proves the match by rendering the panel with the same empty value and asserting the
 * panel produced nothing.
 */
export const EMPTY_WHEN: { [K in FamilyKey]: (value: Value<CockpitData[K]>) => boolean } = {
  research: (tasks) => tasks.length === 0,
  // The focus is a pointer into the research list; with nothing pointed at, there is
  // nothing for a reader to learn from it.
  researchFocus: (focus) => focus.current === null && focus.pending_clarification === null,
  goals: (goals) => goals.length === 0,
  opportunities: (items) => items.length === 0,
  shadowReady: (value) => value.awaiting_approval.length === 0,
  world: (world) => world.facts.length === 0 && world.uncertainties.length === 0,
  ledger: (events) => events.length === 0,
  briefings: (items) => items.length === 0,
  memory: (events) => events.length === 0,
  lessons: (lessons) => lessons.length === 0,
  health: (health) => Object.keys(health.checks).length === 0,
  alarms: (alarms) => alarms.length === 0,
  // A policy that never reported whether the automatic screen-off is on has told us
  // nothing; one that reports `false` has told us something and stays.
  ambientPolicy: (policy) => policy.auto_off_enabled === null,
  devices: (rows) => rows.length === 0,
  voiceQualification: (q) => q.state === "NOT_YET_RUN",
  evolutionSupervisor: (s) => s.last_scan === null && !s.paused,
  mailDrafts: (rows) => rows.length === 0,
  calendarProposals: (rows) => rows.length === 0,
  documentMutations: (rows) => rows.length === 0,
  artifacts: (rows) => rows.length === 0,
  apps: (rows) => rows.length === 0,
  genesisRuns: (rows) => rows.length === 0,
  scenes: (rows) => rows.length === 0,
  executiveRuns: (rows) => rows.length === 0,
  creativeRuns: (rows) => rows.length === 0,
  nativeBuilds: (rows) => rows.length === 0,
  notifications: (inbox) => inbox.rows.length === 0,
  routines: (rows) => rows.length === 0,
};

export function familyQuality<T>(state: Loaded<T>, empty: (value: T) => boolean): FamilyQuality {
  switch (state.kind) {
    case "loading":
      return "loading";
    case "failed":
      return "failed";
    case "absent":
      return "absent";
    default:
      return empty(state.value) ? "empty" : "ready";
  }
}

/**
 * Whether a panel should draw nothing.
 *
 * `absent` counts: a route this Cloud Core does not serve is a family with nothing to
 * show, and the sentence explaining that belongs on the availability page next to the
 * other twenty-six, not in a panel of its own on the cockpit.
 */
export function isQuiet(quality: FamilyQuality): boolean {
  return quality === "empty" || quality === "absent";
}

/** What the availability page prints for each answer. */
export const QUALITY_LABEL: Record<FamilyQuality, string> = {
  loading: "sorgulanıyor",
  ready: "veri var",
  empty: "boş",
  absent: "bu sürümde yok",
  failed: "alınamadı",
};

/** The one-line reason, for the families whose answer needs one. */
export const QUALITY_DETAIL: Record<FamilyQuality, string> = {
  loading: "Henüz cevap gelmedi.",
  ready: "Paneli açık.",
  empty: "Kaynak boş; panel gösterilmiyor.",
  absent: "Bu Cloud Core sürümü bu ucu sunmuyor; panel gösterilmiyor.",
  failed: "Soruldu, cevap alınamadı; panel duruyor.",
};

/** Every family's answer right now, in `FAMILIES` order. */
export function familyQualities(data: CockpitData): { family: Family; quality: FamilyQuality }[] {
  return FAMILIES.map((family) => {
    // One cast, at the single point where the per-key types meet a loop over the union:
    // `EMPTY_WHEN[family.key]` is exact at its definition site above, which is where a
    // wrong predicate would actually be written.
    const empty = EMPTY_WHEN[family.key] as (value: unknown) => boolean;
    return { family, quality: familyQuality(data[family.key] as Loaded<unknown>, empty) };
  });
}

/** The families drawing no panel right now. */
export function quietFamilies(data: CockpitData): Family[] {
  return familyQualities(data)
    .filter((entry) => isQuiet(entry.quality))
    .map((entry) => entry.family);
}
