/**
 * The Evolution Supervisor panel and the priority / promotion class on the Evrim panel
 * (ADR-0081): sentences about rows, never a promise.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EvolutionPanel, EvolutionSupervisorPanel } from "../../app/core/panels/CockpitPanels";
import type { EvolutionSupervisorStatus, Opportunity } from "../../app/lib/cockpit/api";

const NOW = Date.parse("2026-09-08T08:00:00Z");

function status(overrides: Partial<EvolutionSupervisorStatus> = {}): EvolutionSupervisorStatus {
  return {
    paused: false,
    enabled: true,
    last_scan_at: "2026-09-08T07:55:00Z",
    last_scan: { status: "scanned", signals: 3, opened: ["o1"], already_tracked: 2 },
    open_by_priority: { P0: 1, P1: 0, P2: 2, P3: 0 },
    building: [{ opportunity_id: "o1", title: "Sürüm modeli", status: "building", priority: "P1" }],
    pending_candidates: [{ opportunity_id: "o2", title: "Gölge aday", status: "shadow_ready" }],
    release_failures: [],
    last_fix: {
      incident_id: "i1",
      component: "cloud-core",
      fixed_release_id: "rel-9",
      last_seen_at: "2026-09-07T22:00:00Z",
      error_class: "selftest_failed",
    },
    running: { version: "65459a4", version_source: "env", contracts: { action: 12, ui_state: 3 } },
    ...overrides,
  };
}

function render(value: EvolutionSupervisorStatus) {
  return renderToStaticMarkup(
    <EvolutionSupervisorPanel state={{ kind: "ok", value, at: NOW }} now={NOW} />,
  );
}

describe("the Evolution Supervisor panel", () => {
  it("states the switch, the last scan, the counts, the work, the last fix and the version", () => {
    const html = render(status());
    expect(html).toContain('data-supervisor-paused="false"');
    expect(html).toContain("Kendi kendini geliştirme açık");
    expect(html).toContain("son tarama: 3 sinyal, 1 yeni fırsat, 2 zaten izleniyor");
    expect(html).toContain("P0 1");
    expect(html).toContain('data-supervisor-building="o1"');
    expect(html).toContain("Sürüm modeli");
    expect(html).toContain("sahip kararı bekliyor");
    expect(html).toContain("son düzeltilen olay: cloud-core · selftest_failed · sürüm rel-9");
    expect(html).toContain("çalışan sürüm: 65459a4 (dışa aktarılmış) · eylem sözleşmesi 12");
    expect(html).not.toContain("gelişiyor");
  });

  it("draws attention when paused and says the version is not exported when it is not", () => {
    const html = render(
      status({
        paused: true,
        running: { version: "unknown", version_source: "unknown", contracts: { action: 12 } },
        last_fix: null,
      }),
    );
    expect(html).toContain('class="panel attention"');
    expect(html).toContain("duraklatıldı");
    expect(html).toContain("kimliği dışa aktarılmamış");
    expect(html).toContain("kayıtlı düzeltilmiş olay yok");
  });

  it("is empty, not failed, before the first scan", () => {
    const html = render(status({ last_scan: null, last_scan_at: null, building: [], pending_candidates: [] }));
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Gözetmen henüz taramadı.");
  });
});

describe("the Evrim panel names priority and promotion class", () => {
  it("says the derived classes, and says when they were never derived", () => {
    const items: Opportunity[] = [
      {
        opportunity_id: "o1",
        title: "Olay: cloud-core selftest_failed",
        status: "idea",
        scores: { composite: 0.7 },
        candidate_ref: null,
        approved_at: null,
        updated_at: null,
        priority: "P0",
        promotion_class: "OWNER_APPROVAL_REQUIRED",
      },
      {
        opportunity_id: "o2",
        title: "Elle açılmış fırsat",
        status: "idea",
        scores: { composite: 0.3 },
        candidate_ref: null,
        approved_at: null,
        updated_at: null,
        priority: null,
        promotion_class: null,
      },
    ];
    const html = renderToStaticMarkup(
      <EvolutionPanel state={{ kind: "ok", value: items, at: NOW }} />,
    );
    expect(html).toContain('data-opportunity-priority="P0"');
    expect(html).toContain("sahip onayı gerekir");
    expect(html).toContain('data-opportunity-priority="unclassified"');
    expect(html).toContain("öncelik sınıflanmadı");
    expect(html).toContain("terfi sınıfı belirlenmedi");
  });
});
