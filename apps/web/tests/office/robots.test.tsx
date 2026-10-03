import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import OfficeView from "../../app/core/office/OfficeView";
import { energyOf } from "../../app/core/office/officeEnergy";
import { characterOf } from "../../app/core/office/officeRobots";
import { twoWorkers } from "./fixtures";

// The owner's request of 2026-10-03: the office as a room of friendly amber and navy robots
// (his reference pictures), the owner himself a person, a "!" bubble when work came back,
// and a bar of the team's energy and the agents at work under the room.

function render(view = twoWorkers()) {
  return renderToStaticMarkup(
    <OfficeView view={view} selected={null} offline={false} reducedMotion={false} onSelect={() => {}} />,
  );
}

const seatHtml = (html: string, seat: string) =>
  html.match(new RegExp(`<button[^>]*data-seat="${seat}"[\\s\\S]*?</button>`))?.[0] ?? "";

describe("who sits at a seat", () => {
  it("is decided by the role: four characters, a person for the owner, nobody at an unknown seat", () => {
    const table = {
      lead: "lead",
      researcher: "researcher",
      integrator: "integrator",
      "worker-1": "worker",
      "worker-12": "worker",
      inspector: "inspector",
      owner: "owner",
    } as const;
    for (const [seat, character] of Object.entries(table)) {
      expect(characterOf(seat, false), seat).toBe(character);
    }
    expect(characterOf("auditor-2", true)).toBe("none");
    expect(characterOf("lead", true)).toBe("none");
  });

  it("draws amber builders and navy judges: a worker is amber, the inspector and the lead navy", () => {
    const html = render();
    expect(seatHtml(html, "worker-1")).toContain("office-character-worker");
    expect(seatHtml(html, "worker-1")).toContain("var(--office-shirt)");
    for (const seat of ["inspector", "lead", "integrator"]) {
      const cell = seatHtml(html, seat);
      expect(cell, seat).toContain("var(--office-navy)");
      expect(cell, seat).not.toContain('fill="var(--office-shirt)"');
    }
    // the lead wears the gold light on its antenna; nobody else does
    expect(seatHtml(html, "lead")).toContain("var(--office-gold)");
    expect(seatHtml(html, "worker-2")).not.toContain("var(--office-gold)");
  });

  it("draws the owner as a person - skin and hair - and every other seat as a robot with a screen face", () => {
    const html = render();
    const owner = seatHtml(html, "owner");
    expect(owner).toContain("var(--office-skin)");
    expect(owner).not.toContain("var(--office-face)");
    for (const seat of ["lead", "researcher", "worker-1", "inspector"]) {
      expect(seatHtml(html, seat), seat).toContain("var(--office-face)");
      expect(seatHtml(html, seat), seat).not.toContain("var(--office-skin)");
    }
  });
});

describe("what a character shows", () => {
  it("a returned task: standing beside the desk with a '!' bubble; a working one types; a waiting one sits", () => {
    const html = render();
    const returned = seatHtml(html, "inspector");
    expect(returned).toContain('class="office-warning"');
    expect(returned).toMatch(/<text[^>]*>!<\/text>/);
    const working = seatHtml(html, "worker-1");
    expect(working).toContain("office-typing");
    expect(working).not.toContain("office-warning");
    const waiting = seatHtml(html, "worker-3");
    expect(waiting).not.toContain("office-typing");
    expect(waiting).not.toContain("office-warning");
    // three poses, three different drawings
    const shapes = new Set([returned, working, waiting].map((cell) => cell.replace(/data-seat="[^"]*"/, "")));
    expect(shapes.size).toBe(3);
  });

  it("the furniture is in the room but is never a seat", () => {
    const html = render();
    expect(html).toContain('class="office-decor"');
    expect(html.match(/data-seat="/g)).toHaveLength(9);
    // the decor comes after the seats, so the grid's first cell is still a seat
    expect(html.indexOf('class="office-decor"')).toBeGreaterThan(html.lastIndexOf("data-seat="));
  });
});

function levelAt(used: unknown) {
  const view = twoWorkers();
  (view.cycle as unknown as { limits: unknown }).limits = { all: { used_pct: used } };
  return energyOf(view).level;
}

describe("the energy bar", () => {
  it("is what is left of the usage window, and the agents at work out of the seats", () => {
    const view = twoWorkers();
    (view.cycle as unknown as { limits: unknown }).limits = { all: { used_pct: 77 } };
    const energy = energyOf(view);
    expect(energy).toEqual({ percent: 23, text: "Enerji %23", working: "Çalışan ajanlar 2/6", level: "low" });
    const html = render(view);
    expect(html).toContain('data-office="energy"');
    expect(html).toContain('aria-valuenow="23"');
    expect(html).toContain("width:23%");
  });

  it("says 'bilinmiyor' and draws no fill when the answer has no limits - never a made-up full bar", () => {
    const energy = energyOf(twoWorkers());
    expect(energy.percent).toBeNull();
    expect(energy.level).toBe("unknown");
    const html = render();
    expect(html).toContain("Enerji bilinmiyor");
    expect(html).not.toContain("aria-valuenow");
  });

  it("has four levels at their bounds: full, low at 30 %, empty at 0, unknown", () => {
    const at = levelAt;
    expect(at(0)).toBe("ok");
    expect(at(69)).toBe("ok");
    expect(at(70)).toBe("low");
    expect(at(100)).toBe("empty");
    expect(at(140)).toBe("empty");
    expect(at("77")).toBe("unknown");
    expect(at(Number.NaN)).toBe("unknown");
  });
});
