import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import OfficeView from "../../app/core/office/OfficeView";
import type { OfficeView as Office } from "../../app/core/office/officeApi";
import { buildOffice } from "../../app/core/office/officeModel";
import { arrivals, MOOD_TR, moodOf, TIRED_AFTER_MIN } from "../../app/core/office/officeMood";
import { twoWorkers } from "./fixtures";

// The owner, 2026-10-03: "karakterler yeni iş alacağı zaman hareket etsinler, başarısız işlerde
// sinirlensinler, yorulsunlar, duyguları olsun ... güzel teknoloji bir ofis yap, motive olsunlar".

const NOW = new Date("2026-10-01T10:30:00Z");

function at(minutesAfter10: number): Date {
  return new Date(Date.parse("2026-10-01T10:00:00Z") + minutesAfter10 * 60000);
}

function seatHtml(html: string, seat: string): string {
  return html.match(new RegExp(`<button[^>]*data-seat="${seat}"[\\s\\S]*?</button>`))?.[0] ?? "";
}

function render(view: Office, arriving: string[] = [], reducedMotion = false): string {
  return renderToStaticMarkup(
    <OfficeView
      view={view}
      selected={null}
      offline={false}
      reducedMotion={reducedMotion}
      onSelect={() => {}}
      arriving={arriving}
    />,
  );
}

describe("how a character feels", () => {
  it("working: focused before the tired mark, tired from it on (worker-1 started at 10:00)", () => {
    const view = twoWorkers();
    const worker = view.agents.find((a) => a.seat === "worker-1")!;
    expect(moodOf(worker, view.tasks["t-one"], at(TIRED_AFTER_MIN - 1))).toBe("focused");
    expect(moodOf(worker, view.tasks["t-one"], at(TIRED_AFTER_MIN))).toBe("tired");
    expect(moodOf({ ...worker, since: null }, undefined, at(500))).toBe("focused");
  });

  it("a task back from the inspector makes it sad; a stopped or failed one makes it angry", () => {
    const view = twoWorkers();
    const inspector = view.agents.find((a) => a.seat === "inspector")!;
    const old = view.tasks["t-old"];
    expect(moodOf(inspector, old, NOW)).toBe("sad");
    expect(moodOf(inspector, { ...old, state: "stopped" }, NOW)).toBe("angry");
    expect(
      moodOf(inspector, { ...old, report: { role: "worker", at: "", outcome: "başarısız: exit 1", summary: [] } }, NOW),
    ).toBe("angry");
  });

  it("waiting is relaxed and the CTO is happy; every mood has a Turkish word", () => {
    const view = twoWorkers();
    expect(moodOf(view.agents.find((a) => a.seat === "lead")!, undefined, NOW)).toBe("relaxed");
    expect(moodOf(view.agents.find((a) => a.seat === "owner")!, undefined, NOW)).toBe("happy");
    expect(Object.keys(MOOD_TR).toSorted()).toEqual(["angry", "focused", "happy", "relaxed", "sad", "tired"]);
  });

  it("buildOffice carries each seat's mood from the clock it is given", () => {
    const early = buildOffice(twoWorkers(), at(10)).seats;
    const late = buildOffice(twoWorkers(), at(90)).seats;
    expect(early.find((s) => s.seat === "worker-1")!.mood).toBe("focused");
    expect(late.find((s) => s.seat === "worker-1")!.mood).toBe("tired");
    expect(late.find((s) => s.seat === "inspector")!.mood).toBe("sad");
    expect(late.find((s) => s.seat === "lead")!.mood).toBe("relaxed");
  });
});

describe("what the feeling looks like", () => {
  it("each mood draws its own face, and the marks that go with it", () => {
    const view = twoWorkers();
    view.tasks["t-old"] = { ...view.tasks["t-old"], state: "stopped" };
    const html = render(view);
    const angry = seatHtml(html, "inspector");
    expect(angry).toContain("office-mood-angry");
    expect(angry).toContain("var(--office-face-angry)");
    expect(angry).toContain('class="office-steam"');
    const relaxed = seatHtml(html, "lead");
    expect(relaxed).toContain("office-mood-relaxed");
    expect(relaxed).toContain('class="office-mug"');
    // the two faces differ from each other
    const faces = new Set(
      ["inspector", "lead"].map((s) => seatHtml(html, s).match(/<g transform="translate\(5 4\)"[\s\S]*?<\/g>/)?.[0]),
    );
    expect(faces.size).toBe(2);
  });

  it("a tired worker sweats and types slower; a focused one does neither", () => {
    const view = twoWorkers();
    view.agents = view.agents.map((a) => (a.seat === "worker-1" ? { ...a, since: "2000-01-01T00:00:00Z" } : a));
    view.agents = view.agents.map((a) => (a.seat === "worker-2" ? { ...a, since: new Date().toISOString() } : a));
    const html = render(view);
    const tired = seatHtml(html, "worker-1");
    expect(tired).toContain("office-tired");
    expect(tired).toContain('class="office-sweat"');
    const focused = seatHtml(html, "worker-2");
    expect(focused).toContain("office-mood-focused");
    expect(focused).not.toContain("office-tired");
    expect(focused).not.toContain("office-sweat");
  });

  it("the seat says its mood to the page (data-mood) for the panel and the tests", () => {
    const html = render(twoWorkers());
    expect(seatHtml(html, "lead")).toContain('data-mood="relaxed"');
    expect(seatHtml(html, "owner")).toContain('data-mood="happy"');
  });
});

describe("walking in to a new task", () => {
  it("a seat that took a task it did not have walks in; the first answer moves nobody", () => {
    const before = twoWorkers();
    const after = twoWorkers();
    after.agents = after.agents.map((a) =>
      a.seat === "worker-3" ? { ...a, state: "working" as const, task_id: "t-new", task_title: "Yeni iş", since: null } : a,
    );
    expect(arrivals(null, after)).toEqual([]);
    expect(arrivals(before, after)).toEqual(["worker-3"]);
    expect(arrivals(after, after)).toEqual([]);
    // the same seat on another task walks in again
    const next = structuredClone(after);
    next.agents = next.agents.map((a) => (a.seat === "worker-1" ? { ...a, task_id: "t-other" } : a));
    expect(arrivals(after, next)).toEqual(["worker-1"]);
  });

  it("the walking seat's figure carries the walk; never under reduced motion", () => {
    const view = twoWorkers();
    expect(seatHtml(render(view, ["worker-1"]), "worker-1")).toContain("office-arriving");
    expect(seatHtml(render(view, ["worker-1"]), "worker-2")).not.toContain("office-arriving");
    expect(seatHtml(render(view, ["worker-1"], true), "worker-1")).not.toContain("office-arriving");
  });
});

describe("the tech office", () => {
  it("has a night-city window, a live wall screen, a server rack, a coffee machine and a cleaning robot - none of them a seat", () => {
    const html = render(twoWorkers());
    for (const piece of ["window", "screen", "rack", "coffee", "vacuum", "sofa"]) {
      expect(html, piece).toContain(`office-decor-${piece}`);
    }
    expect(html.match(/data-seat="/g)).toHaveLength(9);
    expect(html.indexOf('class="office-decor"')).toBeGreaterThan(html.lastIndexOf("data-seat="));
  });

  it("names the owner's seat CTO", () => {
    expect(seatHtml(render(twoWorkers()), "owner")).toContain("CTO");
  });
});
