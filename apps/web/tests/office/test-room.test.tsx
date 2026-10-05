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
  TestRoom,
  testMoodOf,
  testRoomFromBoard,
} from "../../app/core/office/officeTestRoom";
import { twoWorkers } from "./fixtures";

// The owner, 2026-10-03: "Test ekibi ve çalışan ekibi ayrı olsun; 4 test ekibi çalışanı ve 1
// proje yöneticisi olsun." The Ofis gets a separate 'Test odası' room: five seats, amber and
// teal, the same mood rules, each seat's current job and the last breaking point.

const NOW = new Date("2026-10-05T12:30:00Z");

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

  it("draws the Test odası with five seats in amber and teal, the job and the last breaking point", () => {
    const seats = testRoomFromBoard(
      [
        note("tester-1", "iş: nobet (tj-r1-1)", "2026-10-05T12:00:00Z"),
        note("test-lead", "Danışman'a, test turu r1: kopma noktası yük 256", "2026-10-05T12:21:00Z"),
      ],
      NOW,
    );
    const html = renderToStaticMarkup(<TestRoom seats={seats} now={NOW} animated={false} />);
    expect(html).toContain("Test odası");
    expect(html.match(/data-test-seat="/g)?.length).toBe(5);
    expect(html).toContain("Test Proje Yöneticisi");
    expect(html).toContain("Test çalışanı 4");
    expect(html).toContain("nobet (tj-r1-1)");
    expect(html).toContain("yük 256");
    expect(html).toContain("office-test-room");
    expect(html).toMatch(/--office-shirt:\s*#f4b13a/);
    expect(html).toMatch(/--office-navy:\s*#14b8a6/);
    expect(html).toContain("odaklanmış");
  });

  it("an unreachable board is five waiting seats, never an error", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("down"));
    const seats = await fetchTestRoom(NOW);
    expect(seats).toHaveLength(5);
    expect(seats.every((s) => s.state === "waiting")).toBe(true);
  });

  // The inspector, 2026-10-05: <TestRoom> was never mounted - the Ofis did not show the Test
  // odası (an acceptance item). RED until OfficeView.tsx (outside this card's area) takes the
  // test seats and draws the room beside the software team (ALAN_ISTEGI).
  it("the Ofis page itself shows the Test odası beside the software team", () => {
    const seats = testRoomFromBoard([note("tester-2", "iş: saglik (tj-r1-2)", "2026-10-05T12:00:00Z")], NOW);
    const extra = { testSeats: seats } as Record<string, unknown>;
    const html = renderToStaticMarkup(
      <OfficeView view={twoWorkers()} selected={null} offline={false} reducedMotion={false} onSelect={() => {}} {...extra} />,
    );
    expect(html).toContain("Test odası");
    expect(html.match(/data-test-seat="/g)?.length).toBe(5);
    expect(html).toContain("saglik (tj-r1-2)");
  });
});
