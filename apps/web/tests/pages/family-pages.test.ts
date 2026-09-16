/**
 * B24 req 689/693/694/696/697/698/699: the seven families that had no page.
 *
 * Every one of these was filed `MISSING` for the same reason, and it is a shape this
 * repository has hit before: **the client existed and nothing called it.**
 * `fetchNotificationHistory` was written for req 377, parses the delivery record including
 * the rows no channel carried, and had zero callers. `/v1/alarms/history` has served every
 * alarm OCCURRENCE since B13 req 285 — the only question an owner actually asks about an
 * alarm — and nothing in the product had ever asked it. Same for the security
 * authorization trail, the memory entities, and six `/policy` routes.
 *
 * So the tests here are about the wiring, in two halves that cannot both be wrong in the
 * same direction:
 *
 *  * the parsers, driven through the REAL client functions with the session stubbed, so a
 *    field renamed on the wire shows up here rather than as a blank line on a page;
 *  * the pages, read as source, for the two things a page can silently not do — reuse a
 *    panel without turning req 714's hiding off (an empty page), and claim a family
 *    without calling its client (a page that shows nothing for ever).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  fetchAlarmHistory,
  fetchCapabilityGaps,
  fetchEntities,
  fetchMemories,
  fetchPolicy,
  fetchSecurityAssets,
  fetchSecurityAudit,
  fetchSecurityFindings,
  fetchSkillVersions,
  policyEntries,
} from "../../app/lib/pages/detail";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function source(path: string): string {
  return readFileSync(fileURLToPath(new URL(`../../app/${path}`, import.meta.url)), "utf8");
}

beforeEach(() => {
  apiFetch.mockReset();
});

// ------------------------------------------------------------------ the clients

describe("the clients these pages needed and nothing had called", () => {
  it("reads an alarm OCCURRENCE, which the alarm row cannot describe", async () => {
    apiFetch.mockResolvedValue(
      json(200, {
        events: [
          {
            event_id: "e1",
            event_type: "alarm_missed",
            state: "MISSED",
            occurred_at: "2026-09-14T04:30:00Z",
            alarm_id: "a1",
            summary: "07:30 alarmı çalmadı",
            reason: "device_offline",
            is_test: false,
          },
        ],
      }),
    );
    const loaded = await fetchAlarmHistory();
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value).toHaveLength(1);
    expect(loaded.value[0]).toEqual({
      event_id: "e1",
      event_type: "alarm_missed",
      state: "MISSED",
      occurred_at: "2026-09-14T04:30:00Z",
      alarm_id: "a1",
      summary: "07:30 alarmı çalmadı",
      reason: "device_offline",
      is_test: false,
    });
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/alarms/history?limit=40");
  });

  it("drops a row with no id rather than drawing an unaddressable one", async () => {
    apiFetch.mockResolvedValue(json(200, { events: [{ event_type: "alarm_fired" }] }));
    const loaded = await fetchAlarmHistory();
    expect(loaded.kind === "ok" && loaded.value).toEqual([]);
  });

  it("asks the retrieval path itself for memories, so the page shows what would be recalled", async () => {
    apiFetch.mockResolvedValue(
      json(200, {
        results: [
          {
            memory_id: "m1",
            memory_class: "PREFERENCE",
            key: "coffee",
            text: "Sabah kahvesini sade içer.",
            stage: "COMMITTED",
            status: "active",
            explicit: true,
            pinned: false,
            confidence: 0.9,
            occurred_at: "2026-09-01T06:00:00Z",
          },
        ],
      }),
    );
    const loaded = await fetchMemories("kahve");
    expect(loaded.kind === "ok" && loaded.value[0].text).toBe("Sabah kahvesini sade içer.");
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/memory/search?limit=25&q=kahve");
  });

  it("asks without a query when the box is empty, and never sends a bare `q=`", async () => {
    apiFetch.mockResolvedValue(json(200, { results: [] }));
    await fetchMemories("   ");
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/memory/search?limit=25");
  });

  it("reads the entity graph the memory panel never showed", async () => {
    apiFetch.mockResolvedValue(
      json(200, { entities: [{ entity_id: "e1", kind: "person", name: "Ali" }] }),
    );
    const loaded = await fetchEntities();
    expect(loaded.kind === "ok" && loaded.value[0]).toEqual({
      entity_id: "e1",
      kind: "person",
      name: "Ali",
    });
  });

  it("reads the authorized assets, including the ones that are no longer authorized", async () => {
    apiFetch.mockResolvedValue(
      json(200, {
        assets: [
          { asset_ref: "web:example", name: "Örnek", kind: "web", environment: "prod", status: "active", valid_until: null },
          { asset_ref: "web:old", name: null, kind: "web", environment: "prod", status: "revoked", valid_until: "2026-01-01T00:00:00Z" },
        ],
      }),
    );
    const loaded = await fetchSecurityAssets();
    expect(loaded.kind === "ok" && loaded.value.map((row) => row.status)).toEqual([
      "active",
      "revoked",
    ]);
  });

  it("reads the authorization trail with its REFUSALS, which is the half worth a page", async () => {
    apiFetch.mockResolvedValue(
      json(200, {
        events: [
          { id: 7, action: "scope_check", asset_ref: null, requested_target: "example.net", allowed: false, reason: "unknown target", created_at: "2026-09-14T08:00:00Z" },
          { id: 6, action: "enroll", asset_ref: "web:example", requested_target: null, allowed: true, reason: null, created_at: "2026-09-13T08:00:00Z" },
        ],
      }),
    );
    const loaded = await fetchSecurityAudit();
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    // A numeric id is still an id: dropping it would silently hide every refusal.
    expect(loaded.value.map((row) => row.id)).toEqual(["7", "6"]);
    expect(loaded.value[0].allowed).toBe(false);
    expect(loaded.value[0].reason).toBe("unknown target");
  });

  it("reads findings and keeps 'resolved' distinct from 'never had one'", async () => {
    apiFetch.mockResolvedValue(
      json(200, {
        findings: [
          { id: "f1", title: "Zayıf başlık", severity: "high", status: "open", created_at: "x", resolved_at: null },
        ],
      }),
    );
    const loaded = await fetchSecurityFindings();
    expect(loaded.kind === "ok" && loaded.value[0].resolved_at).toBeNull();
  });

  it("reads the gaps and the skill versions the self-development pipeline produced", async () => {
    apiFetch.mockResolvedValueOnce(
      json(200, { gaps: [{ id: "g1", requested_capability: "counterbox.increment", status: "open", resolution: null, created_at: "x", resolved_at: null }] }),
    );
    const gaps = await fetchCapabilityGaps();
    expect(gaps.kind === "ok" && gaps.value[0].requested_capability).toBe("counterbox.increment");

    apiFetch.mockResolvedValueOnce(
      json(200, { skill_versions: [{ id: "s1", capability_id: "counterbox.increment", version: 3, status: "registered", rejected_reason: null, registered_at: "y" }] }),
    );
    const versions = await fetchSkillVersions();
    // The wire may number a version; the page prints it, so it must survive as text.
    expect(versions.kind === "ok" && versions.value[0].version).toBe("3");
  });

  it("a route this Cloud Core does not serve stays 'absent', never an empty list", async () => {
    apiFetch.mockResolvedValue(json(404, { detail: "not found" }));
    const loaded = await fetchSecurityFindings();
    expect(loaded.kind).toBe("absent");
  });

  it("a failure stays a failure, with the class B22 gave it", async () => {
    apiFetch.mockResolvedValue(json(503, { error_class: "provider_unavailable", message: "yok" }));
    const loaded = await fetchCapabilityGaps();
    expect(loaded.kind).toBe("failed");
  });
});

// ----------------------------------------------------------------- the policies

describe("a policy document, as the settings page reads it", () => {
  it("flattens the rules into name/value pairs, in Turkish for the booleans", () => {
    const entries = policyEntries({
      auto_off_enabled: true,
      wake_on_return: false,
      away_after_s: 300,
      states: ["ARMED", "RINGING"],
      bounds: { max_snooze_minutes: 30 },
      quiet_hours: null,
    });
    const byName = new Map(entries.map((entry) => [entry.name, entry.value]));
    expect(byName.get("auto_off_enabled")).toBe("açık");
    expect(byName.get("wake_on_return")).toBe("kapalı");
    expect(byName.get("away_after_s")).toBe("300");
    expect(byName.get("states")).toBe("ARMED, RINGING");
    expect(byName.get("bounds")).toBe("max_snooze_minutes: 30");
    // Not reported is not the same as off.
    expect(byName.get("quiet_hours")).toBe("—");
  });

  it("is sorted, so the same policy reads the same way twice", () => {
    const names = policyEntries({ zeta: 1, alpha: 2, mu: 3 }).map((entry) => entry.name);
    expect(names).toEqual(["alpha", "mu", "zeta"]);
  });

  it("asks the path it was given and nothing else", async () => {
    apiFetch.mockResolvedValue(json(200, { max_snooze_minutes: 30 }));
    await fetchPolicy("/v1/alarms/policy");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/alarms/policy");
  });
});

// -------------------------------------------------------------------- the pages

/** Each page, the client it must call, and the panel it reuses from the cockpit. */
const PAGES = [
  { req: 689, file: "memory/page.tsx", calls: ["fetchMemories", "fetchEntities", "fetchMemoryAudit", "fetchEmbeddingStatus", "useMemoryControl"], panel: "MemoryPanel" },
  { req: 693, file: "routines/page.tsx", calls: ["fetchRoutines", "fetchPolicy"], panel: "RoutinesPanel" },
  { req: 694, file: "alarms/page.tsx", calls: ["fetchAlarms", "fetchAlarmHistory", "fetchPolicy"], panel: "AlarmsPanel" },
  { req: 696, file: "security/page.tsx", calls: ["fetchSecurityAssets", "fetchSecurityFindings", "fetchSecurityAssessments", "fetchSecurityAudit"], panel: null },
  { req: 697, file: "selfdev/page.tsx", calls: ["fetchCapabilityGaps", "fetchSkillVersions", "fetchEvolutionSupervisor", "fetchShadowReady", "fetchPendingCandidates", "fetchGenesisCatalogue"], panel: "EvolutionSupervisorPanel" },
  { req: 698, file: "notifications/page.tsx", calls: ["fetchInbox", "fetchNotificationHistory"], panel: "NotificationsPanel" },
  { req: 699, file: "settings/page.tsx", calls: ["fetchPolicy", "fetchAmbientPolicy", "fetchVoiceQualification"], panel: "AmbientPanel" },
];

describe("the seven family pages", () => {
  it.each(PAGES)("req $req: $file calls the clients it is about", ({ file, calls }) => {
    const text = source(file);
    for (const call of calls) expect(text, `${file} -> ${call}`).toContain(call);
  });

  it.each(PAGES)("req $req: $file is a FamilyPage with an id and a lead", ({ file }) => {
    const text = source(file);
    expect(text).toContain("<FamilyPage");
    expect(text).toMatch(/id="[a-z-]+"/);
    expect(text).toContain("lead=");
  });

  it.each(PAGES.filter((page) => page.panel !== null))(
    "req $req: the cockpit panel it reuses is told to stay visible",
    ({ file, panel }) => {
      // Without `always`, req 714 would hide the panel exactly when the owner opened the
      // page to find out that there is nothing — the page's whole answer, deleted.
      const text = source(file);
      const mount = new RegExp(`<${panel}[^>]*\\salways\\s*/?>`);
      expect(text, `${file}: <${panel} … always />`).toMatch(mount);
    },
  );

  it("no family page can change what it shows", () => {
    // These pages read. Setting an alarm, changing a policy, forgetting a memory and
    // enrolling an asset all go through the one gated path (ADR-0053 §5); a button here
    // would be a second authority surface. The exceptions each use one existing client, never
    // a request of their own: routines (B14's pause/resume), notifications (mark read), and
    // the settings page's four ambient switches (B48, ADR-0155 decision 5), which PUT the same
    // owner-gated policy the voice tool writes.
    for (const { file } of PAGES) {
      const text = source(file);
      const posts = [...text.matchAll(/method:\s*"(POST|PATCH|PUT|DELETE)"/g)];
      expect(posts, file).toHaveLength(0);
    }
    expect(source("routines/page.tsx")).toContain("useRoutineControl");
    expect(source("notifications/page.tsx")).toContain("useNotificationRead");
    expect(source("settings/page.tsx")).toContain("updateAmbientPolicy");
  });
});
