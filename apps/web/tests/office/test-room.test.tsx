import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import OfficeView from "../../app/core/office/OfficeView";
import type { BoardNote } from "../../app/core/office/officeBoard";
import {
  fetchTestRoom,
  TEST_SEATS,
  TestSeatCells,
  testMoodOf,
  testRoomFromBoard,
} from "../../app/core/office/officeTestRoom";
import { twoWorkers } from "./fixtures";

// The owner, 2026-10-03: "Test ekibi ve çalışan ekibi ayrı olsun; 4 test ekibi çalışanı ve 1
// proje yöneticisi olsun." Five test seats, amber and teal, the same mood rules, each seat's
// current job and the last breaking point; since 2026-10-06 ("Aynı ofiste olsunlar") they sit
// on the software team's own office floor, not in a room of their own.

const NOW = new Date("2026-10-05T12:30:00Z");

/** The whole markup of the <div> that opens with `open`, nested divs and all (no DOM in node). */
function elementMarkup(html: string, open: string): string {
  const start = html.indexOf(open);
  expect(start, `${open} in the page`).toBeGreaterThanOrEqual(0);
  const tag = /<div\b[^>]*>|<\/div>/g;
  tag.lastIndex = start;
  let depth = 0;
  for (let m = tag.exec(html); m; m = tag.exec(html)) {
    depth += m[0] === "</div>" ? -1 : 1;
    if (depth === 0) return html.slice(start, m.index + m[0].length);
  }
  throw new Error(`${open} is never closed`);
}

let n = 0;
function note(seat: string, text: string, at: string): BoardNote {
  n += 1;
  return { id: `n-${String(n).padStart(4, "0")}`, at, seat, task: "test-team", kind: "bilgi", to: "", reply_to: "", text };
}

describe("the test room", () => {
  it("has its own five seats, none of them a software seat", () => {
    expect(TEST_SEATS).toEqual(["test-lead", "tester-1", "tester-2", "tester-3", "tester-4"]);
    const seats = testRoomFromBoard([], NOW);
    expect(seats.map((s) => s.seat)).toEqual([...TEST_SEATS]);
    expect(seats.every((s) => s.state === "waiting" && s.job === null)).toBe(true);
  });

  it("reads each seat's job and its last result from the board; software seats' notes are not the test room's", () => {
    const notes = [
      note("tester-1", "iş: nobet (tj-r1-1)", "2026-10-05T12:00:00Z"),
      note("tester-2", "iş: saglik (tj-r1-2)", "2026-10-05T11:00:00Z"),
      note("tester-2", "sonuç: broke - saglik (tj-r1-2) - kopma: yük 256, p95 7982 ms", "2026-10-05T11:40:00Z"),
      note("tester-3", "iş: iptal (tj-r1-3)", "2026-10-05T12:10:00Z"),
      note("tester-3", "sonuç: failed - iptal (tj-r1-3)", "2026-10-05T12:20:00Z"),
      note("worker-1", "iş: başka bir şey", "2026-10-05T12:25:00Z"),
      note("test-lead", "Danışman'a, test turu r1: kopma noktası GET /v1/system/health - yük 256", "2026-10-05T12:21:00Z"),
    ];
    const byseat = new Map(testRoomFromBoard(notes, NOW).map((s) => [s.seat, s]));
    expect(byseat.get("tester-1")).toMatchObject({ state: "working", job: "nobet (tj-r1-1)", since: "2026-10-05T12:00:00Z" });
    expect(byseat.get("tester-2")).toMatchObject({ state: "broke", job: "saglik (tj-r1-2)" });
    expect(byseat.get("tester-2")?.breaking).toContain("yük 256");
    expect(byseat.get("tester-3")).toMatchObject({ state: "failed" });
    expect(byseat.get("tester-4")).toMatchObject({ state: "waiting", job: null });
    expect(byseat.get("test-lead")?.breaking).toContain("Danışman'a");
    expect([...byseat.keys()]).not.toContain("worker-1");
  });

  it("follows the office's mood rules: focused, tired after 45 minutes, angry on a failure, waiting relaxed", () => {
    const base = { seat: "tester-1", job: "x", breaking: null } as const;
    expect(testMoodOf({ ...base, state: "working", since: "2026-10-05T12:00:00Z" }, NOW)).toBe("focused");
    expect(testMoodOf({ ...base, state: "working", since: "2026-10-05T11:30:00Z" }, NOW)).toBe("tired");
    expect(testMoodOf({ ...base, state: "failed", since: null }, NOW)).toBe("angry");
    expect(testMoodOf({ ...base, state: "broke", since: null }, NOW)).toBe("sad");
    expect(testMoodOf({ ...base, state: "waiting", since: null }, NOW)).toBe("relaxed");
  });

  it("draws the five test seats like software seats: job above, amber and teal figure, name card, breaking point", () => {
    const seats = testRoomFromBoard(
      [
        note("tester-1", "iş: nobet (tj-r1-1)", "2026-10-05T12:00:00Z"),
        note("test-lead", "Danışman'a, test turu r1: kopma noktası yük 256", "2026-10-05T12:21:00Z"),
      ],
      NOW,
    );
    const html = renderToStaticMarkup(<TestSeatCells seats={seats} now={NOW} animated={false} />);
    expect(html).toContain('class="office-floor-divider"');
    expect(html).toContain("Test ekibi");
    expect(html.match(/data-test-seat="/g)?.length).toBe(5);
    expect(html.match(/class="office-seat office-test-seat"/g)?.length).toBe(5);
    // the name stands on its own white name card, never run into the state text
    expect(html).toContain('<span class="office-name" aria-hidden="true">Test Proje Yöneticisi</span>');
    expect(html).toContain('<span class="office-name" aria-hidden="true">Test çalışanı 4</span>');
    expect(html).toMatch(/<span class="office-label" title="nobet \(tj-r1-1\)" aria-hidden="true">nobet \(tj-r1-1\)<\/span>/);
    expect(html).toMatch(/<span class="office-label" title="iş bekliyor" aria-hidden="true">iş bekliyor<\/span>/);
    expect(html).toMatch(/class="office-test-breaking"[^>]*>Son kopma noktası: Danışman&#x27;a, test turu r1: kopma noktası yük 256/);
    expect(html).toContain('aria-label="Test çalışanı 2: iş bekliyor, dinleniyor"');
    expect(html).toContain('aria-label="Test çalışanı 1: test ediyor, odaklanmış, iş: nobet (tj-r1-1)"');
    expect(html).toMatch(/--office-shirt:\s*#f4b13a/);
    expect(html).toMatch(/--office-navy:\s*#14b8a6/);
  });

  it("an unreachable board is five waiting seats, never an error", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("down"));
    const seats = await fetchTestRoom(NOW);
    expect(seats).toHaveLength(5);
    expect(seats.every((s) => s.state === "waiting")).toBe(true);
  });

  // The inspector, 2026-10-05: <TestRoom> was never mounted - the Ofis did not show the Test
  // odası (an acceptance item). OfficeView takes the test seats and draws the room beside the
  // software team; page.tsx reads them from the board with every office poll.
  it("the Ofis page itself shows the test team beside the software team", () => {
    const seats = testRoomFromBoard([note("tester-2", "iş: saglik (tj-r1-2)", "2026-10-05T12:00:00Z")], NOW);
    const html = renderToStaticMarkup(
      <OfficeView view={twoWorkers()} selected={null} offline={false} reducedMotion={false} onSelect={() => {}} testSeats={seats} />,
    );
    expect(html.match(/data-test-seat="/g)?.length).toBe(5);
    expect(html).toContain("saglik (tj-r1-2)");
  });

  // The owner, 2026-10-06: "Aynı ofiste olsunlar". The test team sits on the software team's
  // own floor - inside `.office-floor`, after the software seats - and nowhere else.
  it("seats the test team inside the office floor, once each, with no separate Test odası", () => {
    const seats = testRoomFromBoard([], NOW);
    const html = renderToStaticMarkup(
      <OfficeView view={twoWorkers()} selected={null} offline={false} reducedMotion={false} onSelect={() => {}} testSeats={seats} />,
    );
    expect(html.match(/class="office-floor"/g)).toHaveLength(1);
    const floor = elementMarkup(html, '<div class="office-floor">');
    const outside = html.replace(floor, "");
    for (const id of TEST_SEATS) {
      expect(html.split(`data-test-seat="${id}"`).length - 1, id).toBe(1);
      expect(floor.split(`data-test-seat="${id}"`).length - 1, `${id} inside .office-floor`).toBe(1);
    }
    expect(outside).not.toContain("data-test-seat=");
    const names = [...floor.matchAll(/data-test-seat="[^"]*"[\s\S]*?<span class="office-name"[^>]*>([^<]*)<\/span>/g)].map(
      (m) => m[1],
    );
    expect(names).toEqual(["Test Proje Yöneticisi", "Test çalışanı 1", "Test çalışanı 2", "Test çalışanı 3", "Test çalışanı 4"]);
    // the divider is in the floor, and the test seats come after every software seat
    expect(floor).toMatch(/<div class="office-floor-divider"[^>]*><span>Test ekibi<\/span><\/div>/);
    expect(floor.lastIndexOf('data-seat="')).toBeGreaterThan(0);
    expect(floor.indexOf("office-floor-divider")).toBeGreaterThan(floor.lastIndexOf('data-seat="'));
    expect(floor.indexOf("data-test-seat=")).toBeGreaterThan(floor.indexOf("office-floor-divider"));
    expect(html).not.toContain("office-test-room");
    expect(html).not.toContain("Test odası");
  });
});
