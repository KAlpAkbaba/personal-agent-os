/**
 * B24 req 714: every one of the twenty-seven families either shows data or is hidden.
 *
 * The matrix measured the defect and stated it in five words: **13/27 boş**. Thirteen of
 * the cockpit's panels drew a title, a badge and a sentence saying there was nothing, all
 * at once, on a system that was doing plenty. The target is one line: *"Boş aile panel
 * doğurmaz."*
 *
 * This file is the REAL_PROOF the batch names ("27 panelin tamamı ya veri gösteriyor ya
 * gizli"), and it proves it the only way that counts: by rendering every panel with its
 * family empty and asserting the markup is EMPTY — not by reading the source for a call to
 * the right helper, which would pass for a helper wired to nothing.
 *
 * Three things are kept apart, because collapsing any of them is the lie `Loaded` exists
 * to prevent:
 *
 *   * `failed` is never quiet   — we could not find out; the panel stays, with its retry.
 *   * `loading` is never quiet  — the question is still in flight.
 *   * a talking bus is never quiet — a build running RIGHT NOW is worth a panel even
 *     though the list of finished builds is empty.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { APPROVAL_PAIR_IDLE } from "../../app/lib/cockpit/approvals";
import { APPS_CONTROL_IDLE } from "../../app/lib/cockpit/apps";
import { ARTIFACT_OPEN_IDLE } from "../../app/lib/cockpit/artifacts";
import { CREATIVE_CONTROL_IDLE, CREATIVE_PREVIEW_NONE } from "../../app/lib/cockpit/creative";
import { EXECUTIVE_CONTROL_IDLE, EXECUTIVE_DETAILS_NONE } from "../../app/lib/cockpit/executive";
import { GENESIS_CONTROL_IDLE } from "../../app/lib/cockpit/genesis";
import { SCENE_CONTROL_IDLE, SCENE_PREVIEW_NONE } from "../../app/lib/cockpit/scenes";
import type { Loaded } from "../../app/lib/cockpit/api";
import {
  EMPTY_WHEN,
  FAMILIES,
  type FamilyKey,
  familyQuality,
  isQuiet,
  quietFamilies,
} from "../../app/lib/cockpit/families";
import type { CockpitData } from "../../app/lib/cockpit/useCockpitData";
import { QuietFamilies } from "../../app/core/panels/Panel";
import { EMPTY_FOCUS } from "../../app/lib/research/focus";
import { emptyTruth } from "../../app/lib/uistate/truth";
import {
  AlarmsPanel,
  AmbientPanel,
  AppsPanel,
  ArtifactsPanel,
  CalendarPanel,
  CreativePanel,
  DevicesPanel,
  DocumentsPanel,
  EvolutionPanel,
  EvolutionSupervisorPanel,
  ExecutivePanel,
  GenesisPanel,
  GoalsPanel,
  HealthPanel,
  LedgerPanel,
  LessonsPanel,
  MailPanel,
  MemoryPanel,
  NativePanel,
  NotificationsPanel,
  OwnerActionsPanel,
  ResearchPanel,
  RoutinesPanel,
  ScenesPanel,
  ShadowReadyPanel,
  VoiceQualificationPanel,
  WorldPanel,
} from "../../app/core/panels/CockpitPanels";

const T0 = Date.UTC(2026, 8, 14, 9, 0, 0);
const noop = () => {};
const TRUTH = emptyTruth();

function ok<T>(value: T): Loaded<T> {
  return { kind: "ok", value, at: T0 };
}

/**
 * A `CockpitData` in which every family answered, and every answer was nothing.
 *
 * Written by hand rather than derived from `EMPTY_WHEN`: a fixture generated from the
 * predicate under test could only ever agree with it. These are the shapes the parsers
 * actually produce for an empty system.
 */
const NOTHING: CockpitData = {
  research: ok([]),
  researchFocus: ok(EMPTY_FOCUS),
  goals: ok([]),
  opportunities: ok([]),
  shadowReady: ok({ awaiting_approval: [], count: 0, note: "", second_confirmation_floor: null }),
  world: ok({ facts: [], uncertainties: [], generated_at: null }),
  ledger: ok([]),
  briefings: ok([]),
  memory: ok([]),
  lessons: ok([]),
  health: ok({ status: "unknown", version: "", checks: {} }),
  alarms: ok([]),
  ambientPolicy: ok({
    auto_off_enabled: null,
    off_when_away: null,
    off_when_asleep: null,
    wake_on_return: null,
    away_after_s: null,
    asleep_after_s: null,
    input_holdoff_s: null,
    quiet_hours: null,
  }),
  devices: ok([]),
  voiceQualification: ok({
    state: "NOT_YET_RUN",
    routing_state: "NOT_YET_RUN",
    owner_audio_qualified: false,
    open_opportunities: 0,
    latest_synthetic_run: null,
    latest_owner_audio_run: null,
  }),
  evolutionSupervisor: ok({
    paused: false,
    enabled: true,
    last_scan_at: null,
    last_scan: null,
    open_by_priority: {},
    building: [],
    pending_candidates: [],
    release_failures: [],
    last_fix: null,
    running: null,
  }),
  mailDrafts: ok([]),
  calendarProposals: ok([]),
  documentMutations: ok([]),
  artifacts: ok([]),
  apps: ok([]),
  genesisRuns: ok([]),
  scenes: ok([]),
  executiveRuns: ok([]),
  creativeRuns: ok([]),
  nativeBuilds: ok([]),
  notifications: ok({ rows: [], unread: 0 }),
  routines: ok([]),
};

const PAIR = { ...APPROVAL_PAIR_IDLE, onConfirm: noop, onDiscard: noop };

/**
 * Each family, rendered by the panel the cockpit actually mounts for it.
 *
 * Several families share a panel — `research` and `researchFocus`, and the three the
 * Approval Center combines — so the map is family → the markup the cockpit would draw.
 */
const RENDER: Record<FamilyKey, (data: CockpitData) => string> = {
  research: (d) =>
    renderToStaticMarkup(
      <ResearchPanel state={d.research} focus={d.researchFocus} now={T0} onSelect={noop} notice={null} />,
    ),
  researchFocus: (d) =>
    renderToStaticMarkup(
      <ResearchPanel state={d.research} focus={d.researchFocus} now={T0} onSelect={noop} notice={null} />,
    ),
  goals: (d) => renderToStaticMarkup(<GoalsPanel state={d.goals} now={T0} />),
  opportunities: (d) => renderToStaticMarkup(<EvolutionPanel state={d.opportunities} />),
  shadowReady: (d) => renderToStaticMarkup(<ShadowReadyPanel state={d.shadowReady} />),
  world: (d) => renderToStaticMarkup(<WorldPanel state={d.world} />),
  ledger: (d) => renderToStaticMarkup(<LedgerPanel state={d.ledger} now={T0} />),
  briefings: (d) =>
    renderToStaticMarkup(
      <OwnerActionsPanel
        briefings={d.briefings}
        goals={d.goals}
        shadowReady={d.shadowReady}
        now={T0}
      />,
    ),
  memory: (d) => renderToStaticMarkup(<MemoryPanel state={d.memory} now={T0} />),
  lessons: (d) => renderToStaticMarkup(<LessonsPanel state={d.lessons} />),
  health: (d) => renderToStaticMarkup(<HealthPanel state={d.health} />),
  alarms: (d) => renderToStaticMarkup(<AlarmsPanel state={d.alarms} now={T0} />),
  ambientPolicy: (d) =>
    renderToStaticMarkup(<AmbientPanel policy={d.ambientPolicy} devices={d.devices} />),
  devices: (d) => renderToStaticMarkup(<DevicesPanel devices={d.devices} now={T0} />),
  voiceQualification: (d) =>
    renderToStaticMarkup(<VoiceQualificationPanel state={d.voiceQualification} now={T0} />),
  evolutionSupervisor: (d) =>
    renderToStaticMarkup(<EvolutionSupervisorPanel state={d.evolutionSupervisor} now={T0} />),
  mailDrafts: (d) =>
    renderToStaticMarkup(<MailPanel pending={d.mailDrafts} truth={TRUTH} now={T0} pair={PAIR} />),
  calendarProposals: (d) =>
    renderToStaticMarkup(
      <CalendarPanel pending={d.calendarProposals} truth={TRUTH} now={T0} pair={PAIR} />,
    ),
  // B34: the proposed file changes live in the Documents panel, beside the bus caption.
  documentMutations: (d) =>
    renderToStaticMarkup(
      <DocumentsPanel truth={TRUTH} now={T0} pending={d.documentMutations} pair={PAIR} />,
    ),
  artifacts: (d) =>
    renderToStaticMarkup(
      <ArtifactsPanel
        artifacts={d.artifacts}
        truth={TRUTH}
        now={T0}
        open={{ ...ARTIFACT_OPEN_IDLE, onOpen: noop }}
      />,
    ),
  apps: (d) =>
    renderToStaticMarkup(
      <AppsPanel
        apps={d.apps}
        truth={TRUTH}
        now={T0}
        control={{ ...APPS_CONTROL_IDLE, onRun: noop, onStop: noop, onTest: noop }}
      />,
    ),
  genesisRuns: (d) =>
    renderToStaticMarkup(
      <GenesisPanel
        runs={d.genesisRuns}
        truth={TRUTH}
        now={T0}
        control={{ ...GENESIS_CONTROL_IDLE, onApprove: noop, onCancel: noop }}
      />,
    ),
  scenes: (d) =>
    renderToStaticMarkup(
      <ScenesPanel
        scenes={d.scenes}
        truth={TRUTH}
        now={T0}
        control={{ ...SCENE_CONTROL_IDLE, onRender: noop, onInspect: noop }}
        preview={SCENE_PREVIEW_NONE}
      />,
    ),
  executiveRuns: (d) =>
    renderToStaticMarkup(
      <ExecutivePanel
        runs={d.executiveRuns}
        truth={TRUTH}
        now={T0}
        control={{ ...EXECUTIVE_CONTROL_IDLE, onPause: noop, onResume: noop, onCancel: noop, onApprove: noop }}
        details={EXECUTIVE_DETAILS_NONE}
      />,
    ),
  creativeRuns: (d) =>
    renderToStaticMarkup(
      <CreativePanel
        runs={d.creativeRuns}
        truth={TRUTH}
        now={T0}
        control={{ ...CREATIVE_CONTROL_IDLE, onExport: noop, onCompare: noop }}
        preview={CREATIVE_PREVIEW_NONE}
      />,
    ),
  nativeBuilds: (d) =>
    renderToStaticMarkup(<NativePanel builds={d.nativeBuilds} truth={TRUTH} now={T0} />),
  notifications: (d) =>
    renderToStaticMarkup(
      <NotificationsPanel state={d.notifications} onMarkRead={noop} busyId={null} />,
    ),
  routines: (d) =>
    renderToStaticMarkup(
      <RoutinesPanel
        state={d.routines}
        control={{ busyId: null, error: null, onControl: noop }}
      />,
    ),
};

describe("the twenty-eight families (req 714; B34 added the twenty-eighth)", () => {
  it("the list is exactly the families the cockpit loads", async () => {
    // Read from the OTHER file's source, not imported from it: a list imported from the
    // thing under test cannot disagree with it. `INITIAL` is where `useCockpitData`
    // enumerates what it fetches, so a twenty-ninth family added there and forgotten here
    // fails this test rather than becoming a panel nobody can find on /availability.
    const fs = await import("node:fs/promises");
    const source = await fs.readFile(
      new URL("../../app/lib/cockpit/useCockpitData.ts", import.meta.url),
      "utf8",
    );
    const initial = /const INITIAL: CockpitData = \{([\s\S]*?)\n\};/.exec(source);
    expect(initial, "INITIAL is where the families are enumerated").not.toBeNull();
    const keys = [...(initial?.[1] ?? "").matchAll(/^ {2}([A-Za-z]+):/gm)].map((m) => m[1]);

    expect(keys).toHaveLength(28);
    expect([...keys].sort()).toEqual(FAMILIES.map((family) => family.key).sort());
  });

  it("every family knows which page owns it, and that page is in the nav", async () => {
    const nav = await import("../../app/components/SiteNav");
    const hrefs = new Set(nav.NAV.map((item) => item.href));
    for (const family of FAMILIES) {
      expect(hrefs.has(family.page), `${family.key} -> ${family.page}`).toBe(true);
      expect(family.label.length, family.key).toBeGreaterThan(0);
      expect(family.panel, family.key).toMatch(/^[a-z-]+$/);
    }
  });

  it("all twenty-eight answer 'empty' for a system with nothing in it", () => {
    // The fixture is the point: if any of these shapes stopped counting as empty, the
    // panel below would render and the batch's claim would be false.
    for (const family of FAMILIES) {
      const empty = EMPTY_WHEN[family.key] as (value: unknown) => boolean;
      const state = NOTHING[family.key] as Loaded<unknown>;
      expect(familyQuality(state, empty), family.key).toBe("empty");
      expect(isQuiet("empty")).toBe(true);
    }
    expect(quietFamilies(NOTHING).map((family) => family.key)).toEqual(
      FAMILIES.map((family) => family.key),
    );
  });

  it("and every one of their panels renders nothing at all", () => {
    // REAL_PROOF: "27 panelin tamamı ya veri gösteriyor ya gizli". With nothing in the
    // system, the whole panel column is empty markup rather than twenty-seven titles.
    for (const family of FAMILIES) {
      expect(RENDER[family.key](NOTHING), family.key).toBe("");
    }
  });

  it("a family that FAILED keeps its panel, because we do not know that it is empty", () => {
    for (const family of FAMILIES) {
      const failed: CockpitData = {
        ...NOTHING,
        [family.key]: { kind: "failed", error: "HTTP 503" },
      };
      expect(RENDER[family.key](failed), family.key).not.toBe("");
      expect(quietFamilies(failed).map((f) => f.key), family.key).not.toContain(family.key);
    }
  });

  it("a family still LOADING keeps its panel, because the question is in flight", () => {
    for (const family of FAMILIES) {
      const loading: CockpitData = { ...NOTHING, [family.key]: { kind: "loading" } };
      expect(RENDER[family.key](loading), family.key).not.toBe("");
      expect(quietFamilies(loading).map((f) => f.key), family.key).not.toContain(family.key);
    }
  });

  it("a route this Cloud Core does not serve is quiet, and says so on /availability", () => {
    for (const family of FAMILIES) {
      const absent: CockpitData = {
        ...NOTHING,
        [family.key]: { kind: "absent", detail: "Bu Cloud Core sürümünde yok (HTTP 404)." },
      };
      expect(RENDER[family.key](absent), family.key).toBe("");
      const empty = EMPTY_WHEN[family.key] as (value: unknown) => boolean;
      expect(familyQuality(absent[family.key] as Loaded<unknown>, empty), family.key).toBe(
        "absent",
      );
    }
  });

  it("every panel is a real anchor, because /availability links to one", () => {
    // The link is `${family.page}#${family.panel}`. Before B24 the id lived only in
    // `data-panel`, so B23's "addressable at #devices" was addressable in a test and
    // nowhere else: every one of those links would have scrolled to nothing.
    const shown: CockpitData = { ...NOTHING, devices: ok([]) };
    for (const family of FAMILIES) {
      const failed: CockpitData = {
        ...shown,
        [family.key]: { kind: "failed", error: "HTTP 503" },
      };
      expect(RENDER[family.key](failed), family.key).toContain(`id="${family.panel}"`);
    }
  });

  it("one row anywhere is enough to bring that family's panel back", () => {
    // The other half of the claim: hidden means "nothing to show", not "hidden for good".
    const withGoal: CockpitData = {
      ...NOTHING,
      goals: ok([
        {
          goal_id: "g1",
          title: "Sabah rutini",
          status: "active",
          priority: 1,
          horizon: "week",
          deadline: null,
          requires_owner_approval: false,
          approved_at: null,
          success_criteria: [],
          blockers: [],
        },
      ]),
    };
    expect(RENDER.goals(withGoal)).toContain('data-panel="goals"');
    expect(quietFamilies(withGoal).map((family) => family.key)).not.toContain("goals");
    expect(quietFamilies(withGoal)).toHaveLength(FAMILIES.length - 1);
  });
});

describe("the cockpit does not lose what it hides", () => {
  it("names every quiet family and points at the page that explains them", () => {
    const html = renderToStaticMarkup(
      <QuietFamilies labels={quietFamilies(NOTHING).map((family) => family.label)} />,
    );
    expect(html).toContain('data-panel-quiet="28"');
    expect(html).toContain("Hafıza");
    expect(html).toContain("Alarmlar");
    expect(html).toContain("/availability");
  });

  it("says nothing at all when nothing is quiet", () => {
    // The line is about families that went away. With none, it would be one more thing on
    // the page saying nothing — which is the defect this whole requirement is about.
    expect(renderToStaticMarkup(<QuietFamilies labels={[]} />)).toBe("");
  });
});
