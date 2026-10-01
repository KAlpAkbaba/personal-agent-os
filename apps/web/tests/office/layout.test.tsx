import { readFileSync } from "node:fs";
import { join } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import OfficeView from "../../app/core/office/OfficeView";
import { twoWorkers } from "./fixtures";

// The owner's screenshot of a real cycle (docs/evidence/office-page-real-cycle-2026-10-01.png):
// a ~670 px column, the rightmost label cut by the scene's edge, four fixed columns in a
// sideways-scrolling box. A label stays inside its own seat cell; the seats wrap.

const LONG_TITLE =
  "ADR-0224 katman 3 - güven eşiği politikası, ofisin sesli özeti ve operatörün cihaz damgası";

const css = readFileSync(
  join(__dirname, "..", "..", "app", "core", "office", "office.css"),
  "utf8",
);

/** The declarations of the one rule whose selector is exactly `selector`. */
function rule(selector: string): string {
  const found = css
    .split("}")
    .map((block) => block.split("{"))
    .filter((parts) => parts.length === 2 && parts[0].trim().split("\n").pop()?.trim() === selector);
  expect(found, `one rule for ${selector}`).toHaveLength(1);
  return found[0][1];
}

const rem = (declarations: string, property: string) =>
  Number(declarations.match(new RegExp(`(?:^|[\\s;])${property}:\\s*([\\d.]+)rem\\s*;`))?.[1]);

/**
 * How many seats share a row when the scene's box is `boxRem` wide: the browser's own auto-fill
 * arithmetic on the track minimum office.css declares, the whole `max(...)` term included (a
 * percentage resolves against the floor's content box).
 */
function seatsPerRow(boxRem: number): number {
  const floor = rule(".office-floor");
  const gap = rem(floor, "gap");
  const content = boxRem - 2 * rem(floor, "padding");
  const track = floor.match(/repeat\(\s*auto-fill,\s*minmax\(\s*(.+),\s*1fr\s*\)\s*\)\s*;/)?.[1] ?? "";
  const capped = track.match(
    /^max\(\s*([\d.]+)rem,\s*calc\(\s*([\d.]+)%\s*-\s*([\d.]+)rem\s*\)\s*\)$/,
  );
  const plain = track.match(/^([\d.]+)rem$/);
  expect(capped ?? plain, `a track minimum this test can read: "${track}"`).not.toBeNull();
  const min = capped
    ? Math.max(Number(capped[1]), (Number(capped[2]) / 100) * content - Number(capped[3]))
    : Number(plain?.[1]);
  return Math.max(1, Math.floor((content + gap) / (min + gap)));
}

function longTitleOffice() {
  const view = twoWorkers();
  view.agents = view.agents.map((agent) =>
    agent.seat === "worker-1" ? { ...agent, task_title: LONG_TITLE } : agent,
  );
  view.tasks["t-one"] = { ...view.tasks["t-one"], title: LONG_TITLE };
  return view;
}

function render(selected: string | null = null) {
  return renderToStaticMarkup(
    <OfficeView
      view={longTitleOffice()}
      selected={selected}
      offline={false}
      reducedMotion={false}
      onSelect={() => {}}
    />,
  );
}

describe("a long task title above a seat", () => {
  it("is truncated inside its own cell and keeps the full title in the title attribute", () => {
    expect(LONG_TITLE).toHaveLength(90);
    const html = render();
    const seat = html.match(/<button[^>]*data-seat="worker-1"[\s\S]*?<\/button>/)?.[0] ?? "";
    expect(seat).toMatch(
      new RegExp(`<span class="office-label" title="${LONG_TITLE}"[^>]*>${LONG_TITLE}</span>`),
    );
  });

  it("gives a seat without a task no title attribute", () => {
    const html = render();
    const lead = html.match(/<button[^>]*data-seat="lead"[\s\S]*?<\/button>/)?.[0] ?? "";
    expect(lead).toContain('class="office-label"');
    expect(lead).not.toContain("title=");
    // two working seats in the fixture, two titled labels
    expect(html.match(/class="office-label" title="/g)).toHaveLength(2);
  });

  it("is cut by the label's own box, never by the scene: ellipsis at the cell's width", () => {
    const label = rule(".office-label");
    expect(label).toMatch(/max-width:\s*100%/);
    expect(label).toMatch(/overflow:\s*hidden/);
    expect(label).toMatch(/text-overflow:\s*ellipsis/);
    expect(label).toMatch(/white-space:\s*nowrap/);
    // the cell may not grow to the title's width: a flex/grid item's default minimum is its content
    expect(rule(".office-seat")).toMatch(/min-width:\s*0/);
  });

  it("is still shown in full in the right panel", () => {
    expect(render("worker-1")).toContain(`<h3>${LONG_TITLE}</h3>`);
  });
});

describe("the scene's seats", () => {
  it("sit in the wrapping grid inside the scene's scroll box", () => {
    expect(render()).toMatch(
      /<div class="office-scroll" data-office="scene"><div class="office-floor"><button/,
    );
  });

  it("wrap by auto-fill with a minimum cell width, with no fixed four-column width", () => {
    const floor = rule(".office-floor");
    expect(floor).toMatch(/display:\s*grid/);
    expect(floor).toMatch(/grid-template-columns:\s*repeat\(\s*auto-fill,\s*minmax\(/);
    expect(floor).not.toMatch(/repeat\(\s*\d/);
    expect(floor).not.toMatch(/(^|[\s;])(min-)?width:/);
  });

  it("fit two in a row at phone width, and the figure shrinks with its cell", () => {
    // a 320 px phone less globals.css `main`'s 1.5rem a side = 17rem
    expect(seatsPerRow(17)).toBe(2);
    const figure = rule(".office-figure");
    expect(figure).toMatch(/(^|[\s;])width:\s*100%/);
    expect(figure).toMatch(/max-width:\s*8rem/);
  });

  it("are four a row at the owner's width and never five: eight seats are 4+4, not 5+3", () => {
    // the owner's screenshot: globals.css `main` 720 px less its padding = a 42rem box. Five
    // 7rem cells would fit it (5 * 7 + 4 * 0.75 = 38rem of 40.5rem) - only the 25% term stops them
    expect(seatsPerRow(42)).toBe(4);
    for (let box = 17; box <= 120; box += 0.5) {
      expect(seatsPerRow(box), `${box}rem box`).toBeLessThanOrEqual(4);
    }
  });

  it("scroll sideways only inside their own box", () => {
    const scroll = rule(".office-scroll");
    expect(scroll).toMatch(/overflow-x:\s*auto/);
    expect(scroll).toMatch(/min-width:\s*0/);
  });
});
