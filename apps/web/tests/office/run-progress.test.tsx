import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import OfficePanel from "../../app/core/office/OfficePanel";
import { buildPanel, panelProgress } from "../../app/core/office/officeModel";
import { twoWorkers } from "./fixtures";

// The owner, 2026-10-05: "tıkladığımda ajanların çalıştıkları kısımda kodun yüzde kaçını yazdığı
// bir kısım ekler misin". Measured by the cycle from the run's worktree, said honestly.

const NOW = new Date("2026-10-05T16:10:00Z");
const PROGRESS = {
  area_total: 4,
  area_touched: 3,
  tests_changed: true,
  adr_draft: false,
  commits: 2,
  last_change_at: "2026-10-05T16:06:00Z",
};

function working() {
  const view = twoWorkers();
  const agent = view.agents.find((a) => a.seat === "worker-1")!;
  agent.progress = PROGRESS;
  return { view, agent };
}

describe("how far a working run has got", () => {
  it("is the share of the card's files with a change, never 'the work is done'", () => {
    const { agent } = working();
    const p = panelProgress(agent, NOW)!;
    expect(p.percent).toBe(75);
    expect(p.label).toBe("Kartın dosyalarının %75'i değişti (3/4)");
    expect(p.marks).toEqual([
      { text: "Testler yazıldı", done: true },
      { text: "Kod değişti", done: true },
      { text: "ADR taslağı", done: false },
    ]);
    expect(p.lastChange).toBe("4 dk önce");
  });

  it("shows nothing for an older cycle, a seat not working, or a card without an area", () => {
    const { agent } = working();
    expect(panelProgress({ ...agent, progress: undefined }, NOW)).toBeNull();
    expect(panelProgress({ ...agent, state: "waiting" }, NOW)).toBeNull();
    expect(panelProgress({ ...agent, progress: { ...PROGRESS, area_total: 0 } }, NOW)).toBeNull();
  });

  it("the panel draws the bar, the label and the marks", () => {
    const { view } = working();
    const panel = buildPanel(view, "worker-1")!;
    expect(panel.progress?.percent).toBe(75);
    const html = renderToStaticMarkup(<OfficePanel panel={panel} />);
    expect(html).toContain('data-panel="progress"');
    expect(html).toContain('aria-valuenow="75"');
    expect(html).toContain("Kartın dosyalarının %75");
    expect(html).toContain("Testler yazıldı");
  });
});
