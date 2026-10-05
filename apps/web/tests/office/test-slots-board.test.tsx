import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../../app/lib/session", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

import {
  type BoardNote,
  fetchSlotSigns,
  SLOT_SIGN_MAX_AGE_MIN,
  SlotSignBadge,
  slotSignText,
  slotSigns,
} from "../../app/core/office/officeBoard";

// The owner, 2026-10-03: "bir test yapacakları zaman burada şu an test yapan var mı yok mu diye
// birbirlerine yönlendirme, direktif alsınlar" - the Ofis shows who holds the heavy slot (TEST)
// and the place of each waiter. The queue decides; the board's notes only report it.

const NOW = new Date("2026-10-04T10:00:00Z");

function note(n: number, minutesAgo: number, slot?: BoardNote["slot"], seat = "worker-2"): BoardNote {
  return {
    id: `n-20261004T09${String(n).padStart(2, "0")}00000000Z-0000abcd`,
    at: new Date(NOW.getTime() - minutesAgo * 60000).toISOString().replace(/\.\d{3}Z$/, "Z"),
    seat,
    task: "slot-task",
    kind: "bilgi",
    to: "herkes",
    reply_to: "",
    text: "test sırası",
    ...(slot ? { slot } : {}),
  };
}

const TAKE = note(1, 20, {
  state: "take",
  kinds: ["heavy"],
  holders: ["worker-2"],
  waiting: [],
  estimate_min: 6,
});
const WAIT = note(
  2,
  10,
  {
    state: "wait",
    kinds: ["heavy"],
    holders: ["worker-2", "inspector-2"],
    waiting: ["gate", "worker-3", "worker-4"],
  },
  "worker-4",
);
const FREE = note(
  3,
  5,
  { state: "free", kinds: ["heavy"], holders: ["inspector-2"], waiting: ["worker-3", "worker-4"] },
  "worker-2",
);

describe("the test queue on the Ofis desks", () => {
  it("puts TEST on the holder and its place on each waiter, from the newest snapshot", () => {
    const signs = slotSigns([TAKE, WAIT, note(4, 8)], NOW);
    expect(signs.get("worker-2")).toEqual({ kind: "test", estimateMin: 6 });
    expect(signs.get("inspector")).toEqual({ kind: "test", estimateMin: null });
    expect(signs.get("worker-3")).toEqual({ kind: "queue", position: 2 });
    expect(signs.get("worker-4")).toEqual({ kind: "queue", position: 3 });
    expect(signs.has("gate")).toBe(false); // the gate has no desk; it still counts in the line
    expect(slotSignText(signs.get("worker-2")!)).toBe("TEST");
    expect(slotSignText(signs.get("worker-3")!)).toBe("Sıra 2");
  });

  it("follows the line: after the slot frees, the next one is first and the old holder has no sign", () => {
    const signs = slotSigns([FREE, TAKE, WAIT], NOW); // any order: the ids say which is newest
    expect(signs.has("worker-2")).toBe(false);
    expect(signs.get("worker-3")).toEqual({ kind: "queue", position: 1 });
    expect(signs.get("inspector")?.kind).toBe("test");
  });

  it("shows nothing for a stale snapshot, a note without one or a malformed one", () => {
    expect(slotSigns([note(1, SLOT_SIGN_MAX_AGE_MIN + 1, TAKE.slot)], NOW).size).toBe(0);
    expect(slotSigns([note(1, 1)], NOW).size).toBe(0);
    const broken = { ...note(2, 1), slot: { state: "take", holders: "worker-2" } } as unknown as BoardNote;
    expect(slotSigns([broken], NOW).size).toBe(0);
  });

  it("renders the sign a desk carries, with a Turkish label", () => {
    const signs = slotSigns([TAKE, WAIT], NOW);
    const test = renderToStaticMarkup(<SlotSignBadge seat="worker-2" sign={signs.get("worker-2")!} />);
    expect(test).toContain(">TEST<");
    expect(test).toContain('data-slot-sign="test"');
    expect(test).toContain("Çalışan 2 ağır test koşuyor, tahmini 6 dk");
    const queue = renderToStaticMarkup(<SlotSignBadge seat="worker-3" sign={signs.get("worker-3")!} />);
    expect(queue).toContain(">Sıra 2<");
    expect(queue).toContain("Çalışan 3 test sırasında 2.");
  });
});

describe("fetchSlotSigns", () => {

  it("reads the board's newest notes", async () => {
    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ notes: [TAKE, WAIT], cards: {} })));
    const signs = await fetchSlotSigns(NOW);
    expect(apiFetch).toHaveBeenCalledWith("/v1/team/board/notes?limit=100");
    expect(signs.get("worker-2")?.kind).toBe("test");
  });

  it("an unreachable or refusing board is no sign, never an error on the page", async () => {
    apiFetch.mockResolvedValueOnce(new Response("{}", { status: 503 }));
    expect((await fetchSlotSigns(NOW)).size).toBe(0);
    apiFetch.mockRejectedValueOnce(new Error("offline"));
    expect((await fetchSlotSigns(NOW)).size).toBe(0);
  });
});
