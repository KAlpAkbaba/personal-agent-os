/**
 * The Approval Center: what it shows, and — more importantly — what it cannot do.
 *
 * Rendered with `react-dom/server` like the rest of this suite. No browser.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ShadowReadyPanel } from "../../app/core/panels/CockpitPanels";
import type { Opportunity, ShadowReady } from "../../app/lib/cockpit/api";

function candidate(overrides: Partial<Opportunity> = {}): Opportunity {
  return {
    opportunity_id: "opp-1",
    title: "Kabul ifadesi koruması",
    status: "owner_approval_required",
    scores: { composite: 0.58 },
    candidate_ref: "lab/candidates/acceptance_wording_guard",
    approved_at: null,
    updated_at: null,
    risk_tier: 2,
    risk_tier_label: "internal_logic",
    requires_second_confirmation: false,
    risk_reasons: ["touches app/experience"],
    ...overrides,
  };
}

function render(items: Opportunity[], floor: number | null = 3, always = false) {
  const value: ShadowReady = {
    awaiting_approval: items,
    count: items.length,
    note: "",
    second_confirmation_floor: floor,
  };
  return renderToStaticMarkup(<ShadowReadyPanel state={{ kind: "ok", value, at: 0 }} always={always} />);
}

describe("the Approval Center shows what decides the answer", () => {
  it("names the candidate, its status and its risk tier", () => {
    const html = render([candidate()]);
    expect(html).toContain("Kabul ifadesi koruması");
    expect(html).toContain("sahip onayı istendi");
    expect(html).toContain("risk kademesi 2");
    expect(html).toContain('data-risk-tier="2"');
    expect(html).toContain('data-second-confirmation="no"');
    expect(html).toContain("touches app/experience");
  });

  it("says when a second confirmation is required", () => {
    const html = render([
      candidate({ risk_tier: 5, requires_second_confirmation: true, risk_reasons: ["authority kernel"] }),
    ]);
    expect(html).toContain("risk kademesi 5");
    expect(html).toContain("ikinci onay gerekir");
    expect(html).toContain('data-second-confirmation="yes"');
  });

  it("derives the second-confirmation rule from the server's floor when the flag is absent", () => {
    const html = render([candidate({ risk_tier: 3, requires_second_confirmation: null })], 3);
    expect(html).toContain("ikinci onay gerekir");
  });

  it("says 'not assessed' rather than guessing a tier", () => {
    const html = render([
      candidate({ risk_tier: null, risk_tier_label: null, requires_second_confirmation: null, risk_reasons: [] }),
    ]);
    expect(html).toContain("risk kademesi belirlenmedi");
    expect(html).toContain('data-risk-tier="unknown"');
    expect(html).toContain('data-second-confirmation="unknown"');
    expect(html).not.toContain("risk kademesi 1");
  });

  it("lists shadow_ready candidates too, with their own word", () => {
    const html = render([candidate({ status: "shadow_ready" })]);
    expect(html).toContain("gölge hazır");
  });

  it("is empty with its own sentence on /selfdev, and takes no cockpit slot", () => {
    // B24 req 714: nothing waiting for the owner is good news that needs no panel. It is
    // still said in words where the owner went to look for it.
    expect(render([])).toBe("");

    const page = render([], 3, true);
    expect(page).toContain("Sahip onayı bekleyen aday yok.");
    expect(page).toContain('data-panel-empty="yes"');
  });
});

describe("the Approval Center cannot act", () => {
  it("renders no control of any kind, and says where the action lives", () => {
    const html = render([candidate(), candidate({ opportunity_id: "opp-2", risk_tier: 5 })]);
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<form");
    expect(html).not.toContain("<input");
    expect(html).not.toContain("<a ");
    expect(html).toContain("canlıya alınmadı");
    expect(html).toContain("doğrulanmış sahip oturumunda");
  });
});
