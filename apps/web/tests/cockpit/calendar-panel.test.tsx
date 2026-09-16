/**
 * The Takvim panel, its approval pair and the Core's readout for
 * `calendar.activity` (M21 spec §3): today's events exactly as the bus
 * carried them, the pending proposal with the conflicts the row names, a
 * pair that asks the Cloud Core to run its own gate exactly once, and never
 * an agenda this page fetched.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { CalendarPanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import { APPROVAL_REASON_NOT_READ_BACK, formatEventWhen, proposalConflictsLine } from "../../app/lib/cockpit/approval-rows";
import {
  APPROVAL_PAIR_IDLE,
  type ApprovalClient,
  type ApprovalPairProps,
  type ApprovalPairState,
  type PendingProposal,
} from "../../app/lib/cockpit/approvals";
import { runApproval } from "../../app/lib/cockpit/useApprovalPair";
import { todaysPublishedEvents } from "../../app/lib/uistate/calendar";
import { CALENDAR_ACTIVITY_TTL_MS } from "../../app/lib/uistate/contract";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  CALENDAR_ACTIVITY,
  DOCUMENT_ANALYSIS,
  MAIL_ACTIVITY,
  T0,
  event,
  iso,
  resetSequence,
  response,
} from "../uistate/fixtures";

// ------------------------------------------------------------------ helpers

function truthOf(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return applyResponse(emptyTruth(), response(events), at);
}

const START = "2026-09-09T09:30:00+03:00";
const END = "2026-09-09T10:30:00+03:00";

function proposal(overrides: Partial<PendingProposal> = {}): PendingProposal {
  return {
    proposal_id: "p1",
    kind: "create",
    title: "Ali ile toplantı",
    start: START,
    end: END,
    all_day: null,
    location: null,
    conflicts: [
      { title: "Sprint planlama", start: "2026-09-09T09:00:00+03:00", end: "2026-09-09T10:00:00+03:00" },
      { title: "Diş hekimi", start: "2026-09-09T10:00:00+03:00", end: "2026-09-09T11:00:00+03:00" },
    ],
    state: "prepared",
    read_back_at: null,
    confirmed_at: null,
    event_id: null,
    created_at: iso(-45_000),
    ...overrides,
  };
}

const READ_BACK = () => proposal({ state: "read_back", read_back_at: iso(-8_000) });

const ok = (proposals: PendingProposal[]) => ({ kind: "ok" as const, value: proposals, at: T0 });
const noop = () => {};

function pairOf(overrides: Partial<ApprovalPairProps> = {}): ApprovalPairProps {
  return { ...APPROVAL_PAIR_IDLE, onConfirm: noop, onDiscard: noop, ...overrides };
}

function panel(
  pending: Parameters<typeof CalendarPanel>[0]["pending"],
  events: ReturnType<typeof event>[] = [AGENT_IDLE()],
  pair: ApprovalPairProps = pairOf(),
  now = T0,
) {
  return renderToStaticMarkup(<CalendarPanel pending={pending} truth={truthOf(events)} now={now} pair={pair} />);
}

function readout(events: ReturnType<typeof event>[], compact = false, now = T0) {
  return renderToStaticMarkup(<StateReadout intent={visualFor(truthOf(events), now)} compact={compact} />);
}

type ElementLike = { type: unknown; props: Record<string, unknown> };

function isElement(node: unknown): node is ElementLike {
  return !!node && typeof node === "object" && "props" in node && "type" in node;
}

function findByData(root: unknown, attr: string, value: string): ElementLike | null {
  const queue: unknown[] = [root];
  let guard = 0;
  while (queue.length > 0 && guard++ < 10_000) {
    const node = queue.shift();
    if (Array.isArray(node)) {
      queue.push(...node);
      continue;
    }
    if (!isElement(node)) continue;
    if (node.props[attr] === value) return node;
    if (typeof node.type === "function") {
      const render = node.type as (props: Record<string, unknown>) => unknown;
      queue.push(render(node.props));
      continue;
    }
    const kids = node.props.children;
    if (kids !== undefined) queue.push(kids);
  }
  return null;
}

function click(node: ElementLike | null): void {
  expect(node).not.toBeNull();
  const handler = node?.props.onClick as (() => void) | undefined;
  expect(typeof handler).toBe("function");
  handler?.();
}

// ---------------------------------------------------------------- the panel

describe("the Takvim panel", () => {
  it("draws nothing at all when nothing waits, nothing was published and the bus is silent (B24 req 714)", () => {
    expect(panel(ok([]))).toBe("");
    expect(panel({ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/calendar/proposals/pending yok (HTTP 404)." })).toBe("");
  });

  it("keeps every word while the bus is telling us something", () => {
    const html = panel(ok([]), [CALENDAR_ACTIVITY("today", "Ali ile toplantı", "read_back", 2)]);
    expect(html).toContain('data-panel="calendar"');
    expect(html).toContain('data-panel-state="ok"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Bekleyen öneri yok.");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).not.toContain("<button");
    expect(html).not.toContain("attention");
    expect(html).toContain("aynı kapıdan geçer");
    expect(html).toContain("takvim sunucusuna doğrudan ulaşmaz");
  });

  it("never renders the empty proposal sentence for a route that is loading, failed or absent", () => {
    const loading = panel({ kind: "loading" });
    expect(loading).toContain("yükleniyor…");
    expect(loading).not.toContain("Bekleyen öneri yok");
    expect(loading).toContain('data-panel-empty=""');
    // Today's published entries are the bus's and do not wait for the route.
    expect(loading).toContain("Bugün için kayıt yok");
    const failed = panel({ kind: "failed", error: "HTTP 500" });
    expect(failed).toContain("Alınamadı: HTTP 500");
    expect(failed).not.toContain("Bekleyen öneri yok");
    // Absent still says so WHILE the bus is talking: "the route is not here" and
    // "nothing is happening" are different facts (req 714 only silences both together).
    const absent = panel(
      { kind: "absent", detail: "Bu Cloud Core sürümünde /v1/calendar/proposals/pending yok (HTTP 404)." },
      [CALENDAR_ACTIVITY("today")],
    );
    expect(absent).toContain("Henüz yok. Bu Cloud Core sürümünde /v1/calendar/proposals/pending yok (HTTP 404).");
    expect(absent).not.toContain("Bekleyen öneri yok");
    for (const html of [loading, failed, absent]) expect(html).not.toContain("<button");
  });

  it("lists today's events exactly as the bus carried them: reads for today, by title, newest first, once each", () => {
    resetSequence();
    const events = [
      { ...CALENDAR_ACTIVITY("today", "Ali ile toplantı"), at: iso(-40_000) },
      { ...CALENDAR_ACTIVITY("week", "Haftalık değerlendirme"), at: iso(-30_000) }, // not today's range
      { ...CALENDAR_ACTIVITY(null, "Aralıksız kayıt"), at: iso(-25_000) }, // no range: not claimed for today
      { ...CALENDAR_ACTIVITY("today", "Diş hekimi"), at: iso(-20_000) },
      { ...CALENDAR_ACTIVITY("today", "Öneri X", "prepared", 1), at: iso(-15_000) }, // a proposal, not an event
      { ...CALENDAR_ACTIVITY("bugün", "Diş hekimi"), at: iso(-10_000) }, // the same title again
    ];
    const truth = applyResponse(emptyTruth(), response(events), T0);
    const today = todaysPublishedEvents(truth);
    expect(today.map((e) => e.title)).toEqual(["Diş hekimi", "Ali ile toplantı"]);
    expect(today[0].event.sequence).toBe(6); // the newest mention

    const html = renderToStaticMarkup(<CalendarPanel pending={ok([])} truth={truth} now={T0} pair={pairOf()} />);
    expect(html).toContain('data-calendar-today="2"');
    expect(html).toContain('data-calendar-event="Diş hekimi"');
    expect(html).toContain('data-calendar-event="Ali ile toplantı"');
    expect(html.indexOf("Diş hekimi")).toBeLessThan(html.indexOf("Ali ile toplantı"));
    expect(html).toContain("10 sn önce");
    expect(html).toContain("40 sn önce");
    expect(html).not.toContain("Haftalık değerlendirme");
    expect(html).not.toContain("Aralıksız kayıt");
    expect(html).not.toContain('data-calendar-event="Öneri X"');
    expect(html).not.toContain("Bugün için kayıt yok");
    expect(html).toContain('data-panel-empty="no"');
    // Still no proposal, and that is said separately.
    expect(html).toContain("Bekleyen öneri yok.");
  });

  it("shows a proposal with its time, its kind and every conflict the row names, the pair disabled until read back", () => {
    const html = panel(ok([proposal()]));
    expect(html).toContain('data-proposal="p1"');
    expect(html).toContain('data-proposal-state="prepared"');
    expect(html).toContain('data-proposal-read-back="no"');
    expect(html).toContain('data-proposal-conflicts="2"');
    expect(html).toContain(">Ali ile toplantı</span>");
    expect(html).toContain("yeni etkinlik · 45 sn önce");
    expect(html).toContain('data-proposal-when="true">9 Eyl 09:30–10:30</span>');
    expect(html).toContain('data-proposal-conflicts-line="true">2 çakışma</span>');
    expect(html).toContain('data-proposal-conflict="0"');
    expect(html).toContain("çakışma: Sprint planlama · 9 Eyl 09:00–10:00");
    expect(html).toContain('data-proposal-conflict="1"');
    expect(html).toContain("çakışma: Diş hekimi · 9 Eyl 10:00–11:00");
    expect(html).toContain("öneri: hazır · henüz okunmadı");
    expect(html).toContain('data-approval-pair="p1"');
    expect(html).toContain('data-approval-family="proposal"');
    expect(html).toContain('data-approval-enabled="no"');
    expect(html).toContain('data-approval-action="confirm" data-approval-target="p1" disabled=""');
    expect(html).toContain("Onayla — takvime işle");
    expect(html).toContain(APPROVAL_REASON_NOT_READ_BACK);
    expect(html).toContain('data-panel-badge="true">1<');
    expect(html).not.toContain("attention");
  });

  it("enables the pair once the proposal was read back, and the confirm press asks for that proposal exactly once", () => {
    const onConfirm = vi.fn();
    const onDiscard = vi.fn();
    const pair = pairOf({ onConfirm, onDiscard });
    const html = panel(ok([READ_BACK()]), [AGENT_IDLE()], pair);
    expect(html).toContain('data-proposal-read-back="yes"');
    expect(html).toContain('data-approval-enabled="yes"');
    expect(html).not.toContain('disabled=""');
    expect(html).toContain("öneri: okundu (8 sn önce)");
    expect(html).toContain('class="panel attention"');

    const tree = <CalendarPanel pending={ok([READ_BACK()])} truth={truthOf([AGENT_IDLE()])} now={T0} pair={pair} />;
    click(findByData(tree, "data-approval-action", "confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("p1");
    expect(onDiscard).not.toHaveBeenCalled();
    click(findByData(tree, "data-approval-action", "discard"));
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(onDiscard).toHaveBeenCalledWith("p1");
  });

  it("shows a committed proposal without a pair and counts it as nothing pending", () => {
    const committed = proposal({ state: "committed", read_back_at: iso(-30_000), confirmed_at: iso(-10_000), event_id: "ev-9", conflicts: [] });
    const html = panel(ok([committed]), [CALENDAR_ACTIVITY("today", "Ali ile toplantı", "committed")], pairOf(), T0 + 2_000);
    expect(html).toContain('data-proposal-pending="no"');
    expect(html).not.toContain("data-approval-pair");
    expect(html).not.toContain("<button");
    expect(html).toContain("öneri: işlendi · onaylandı 12 sn önce · etkinlik: ev-9");
    expect(html).toContain('data-proposal-conflicts-line="true">çakışma yok</span>');
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).toContain("Bekleyen öneri yok.");
    expect(html).toContain("Takvime işlendi · Ali ile toplantı · 2 sn önce");
  });

  it("says a row's missing fields are missing rather than filling them in", () => {
    const bare = proposal({ kind: null, title: null, start: null, end: null, conflicts: null, state: null, created_at: null });
    const html = panel(ok([bare]));
    expect(html).toContain(">başlık bildirilmedi</span>");
    expect(html).toContain('data-proposal-when="true">zaman bildirilmedi</span>');
    expect(html).toContain('data-proposal-conflicts=""');
    expect(html).toContain("çakışma bildirilmedi");
    expect(html).toContain("öneri: durum bildirilmedi · henüz okunmadı");
    expect(html).toContain('data-approval-enabled="no"');
  });

  it("prints an all-day proposal and a location as the row gave them", () => {
    const html = panel(ok([proposal({ start: "2026-09-10", end: null, all_day: true, location: "Kadıköy" })]));
    expect(html).toContain("10 Eyl · tüm gün · Kadıköy");
  });

  it("prints the last answer for a proposal, and not a draft's", () => {
    const outcome = { action: "confirm_proposal" as const, id: "p1", ok: true, text: "Takvime işlendi", at: T0 - 4_000 };
    const html = panel(ok([]), [AGENT_IDLE()], pairOf({ outcome }));
    expect(html).toContain('data-approval-outcome="confirm_proposal"');
    expect(html).toContain("Takvime işlendi · 4 sn önce");
    expect(panel(ok([]), [AGENT_IDLE()], pairOf({ outcome: { ...outcome, action: "confirm_draft", id: "d1" } }))).not.toContain(
      "data-approval-outcome",
    );
  });

  it("states the bus activity in the spec's words with its age, and last-known once it aged out", () => {
    const live = panel(ok([]), [CALENDAR_ACTIVITY("today", "Ali ile toplantı", "read_back", 2)], pairOf(), T0 + 3_000);
    expect(live).toContain('data-calendar-stage="active"');
    expect(live).toContain("Öneri okundu — onay bekliyor · 2 çakışma · Ali ile toplantı · 3 sn önce");
    const stale = panel(ok([]), [CALENDAR_ACTIVITY("today")], pairOf(), T0 + CALENDAR_ACTIVITY_TTL_MS + 1_000);
    expect(stale).toContain('data-calendar-stage="none"');
    expect(stale).toContain('data-calendar-last-known="active"');
    expect(stale).toContain("Son bilinen: Bugünün takvimi · 46 sn önce");
    // A mail event is not a calendar event; the pending row is there so the panel renders
    // at all, since a calendar family with nothing at all is quiet now (req 714).
    expect(panel(ok([proposal()]), [MAIL_ACTIVITY()])).toContain('data-calendar-activity="untold"');
  });
});

// ------------------------------------------------------------ the runner

describe("the approval runner, for proposals", () => {
  it("calls confirmProposal exactly once with the id, and discardProposal on the other press", async () => {
    const client: ApprovalClient = {
      confirmDraft: vi.fn(async () => ({ state: "sent", summary: null, receiptId: null })),
      discardDraft: vi.fn(async () => ({ state: "discarded", summary: null, receiptId: null })),
      confirmProposal: vi.fn(async () => ({ state: "committed", summary: "Takvime eklendi.", receiptId: "r1" })),
      discardProposal: vi.fn(async () => ({ state: "discarded", summary: null, receiptId: "r2" })),
      confirmMutation: vi.fn(async () => ({ state: "applied", summary: null, receiptId: "r5" })),
      discardMutation: vi.fn(async () => ({ state: "discarded", summary: null, receiptId: "r6" })),
      approveCandidate: vi.fn(async () => ({ state: "approved", summary: null, receiptId: "r7" })),
      rejectCandidate: vi.fn(async () => ({ state: "rejected", summary: null, receiptId: "r8" })),
    };
    let state: ApprovalPairState = APPROVAL_PAIR_IDLE;
    const onSettled = vi.fn();
    const ports = { client, read: () => state, write: (next: ApprovalPairState) => (state = next), onSettled, now: () => T0 };

    expect(await runApproval(ports, "confirm_proposal", "p1")).toBe(true);
    expect(client.confirmProposal).toHaveBeenCalledTimes(1);
    expect(client.confirmProposal).toHaveBeenCalledWith("p1");
    expect(client.confirmDraft).not.toHaveBeenCalled();
    expect(state.outcome).toEqual({ action: "confirm_proposal", id: "p1", ok: true, text: "Takvime işlendi · Takvime eklendi.", at: T0 });

    expect(await runApproval(ports, "discard_proposal", "p1")).toBe(true);
    expect(client.discardProposal).toHaveBeenCalledTimes(1);
    expect(client.discardProposal).toHaveBeenCalledWith("p1");
    expect(state.outcome?.text).toBe("Öneriden vazgeçildi");
    expect(onSettled).toHaveBeenCalledTimes(2);
  });
});

// -------------------------------------------------------------- the time

describe("when an event is, in the owner's zone", () => {
  it("formats a timed event, one across days, one with no end, an all-day one, and the absences", () => {
    expect(formatEventWhen(START, END, null)).toBe("9 Eyl 09:30–10:30");
    expect(formatEventWhen("2026-09-09T22:00:00+03:00", "2026-09-10T01:00:00+03:00", null)).toBe("9 Eyl 22:00 – 10 Eyl 01:00");
    expect(formatEventWhen(START, null, null)).toBe("9 Eyl 09:30");
    expect(formatEventWhen("2026-09-10", null, null)).toBe("10 Eyl · tüm gün");
    expect(formatEventWhen(START, null, true)).toBe("9 Eyl · tüm gün");
    // UTC is read into Europe/Istanbul: 06:30Z is 09:30 there.
    expect(formatEventWhen("2026-09-09T06:30:00Z", "2026-09-09T07:30:00Z", null)).toBe("9 Eyl 09:30–10:30");
    expect(formatEventWhen("bugün 9'da", null, null)).toBe("bugün 9'da"); // unreadable: verbatim
    expect(formatEventWhen(null, END, null)).toBe("zaman bildirilmedi");
  });

  it("the conflicts line distinguishes none recorded from no field at all", () => {
    expect(proposalConflictsLine({ conflicts: null })).toBe("çakışma bildirilmedi");
    expect(proposalConflictsLine({ conflicts: [] })).toBe("çakışma yok");
    expect(proposalConflictsLine({ conflicts: [{ title: "x", start: null, end: null }] })).toBe("1 çakışma");
  });
});

// ------------------------------------------------------------- the readout

describe("the Core's readout for the calendar", () => {
  it("headlines the planning posture with the caption and the facts beneath, and no bar", () => {
    const html = readout([CALENDAR_ACTIVITY("today", "Ali ile toplantı", "prepared", 2)]);
    expect(html).toContain('data-core-kind="calendar_activity"');
    expect(html).toContain('data-core-state="calendar.activity"');
    expect(html).toContain('data-core-subsystem="calendar"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain("Takvim okunuyor");
    expect(html).toContain("Takvim");
    expect(html).toContain('data-label="true">Öneri hazır — 2 çakışma · Ali ile toplantı</p>');
    expect(html).toContain("data-calendar-facts");
    expect(html).toContain('data-calendar-range="today"');
    expect(html).toContain('data-calendar-event="Ali ile toplantı"');
    expect(html).toContain('data-calendar-proposal-state="prepared"');
    expect(html).toContain('data-calendar-conflicts="2"');
    expect(html).toContain("aralık: Bugün · etkinlik: Ali ile toplantı · öneri: hazır · 2 çakışma");
    expect(html).toContain("Takvime sahip onayı olmadan yazılmaz.");
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-mail-facts");
  });

  it("says what was not reported when the publisher named nothing, and keeps the compact caption alone", () => {
    const bare = readout([CALENDAR_ACTIVITY(null)]);
    expect(bare).toContain('data-label="true">Takvim okunuyor</p>');
    expect(bare).toContain("aralık bildirilmedi · etkinlik bildirilmedi · öneri bildirilmedi");
    expect(bare).toContain('data-calendar-conflicts=""');
    const compact = readout([CALENDAR_ACTIVITY("tomorrow", "Diş hekimi")], true);
    expect(compact).toContain("Yarının takvimi · Diş hekimi");
    expect(compact).not.toContain("data-calendar-facts");
  });

  it("names the aged-out activity as last-known, with its facts, and prints no calendar line for any other kind", () => {
    const stale = readout([CALENDAR_ACTIVITY("today", "Ali ile toplantı", "read_back", 1)], false, T0 + CALENDAR_ACTIVITY_TTL_MS + 1_000);
    expect(stale).toContain('data-core-kind="last_known"');
    expect(stale).toContain('data-last-state="calendar.activity"');
    expect(stale).toContain("data-calendar-facts");
    expect(stale).toContain("1 çakışma");
    expect(readout([AGENT_IDLE()])).not.toContain("data-calendar-facts");
    expect(readout([DOCUMENT_ANALYSIS()])).not.toContain("data-calendar-facts");
    expect(readout([MAIL_ACTIVITY()])).not.toContain("data-calendar-facts");
  });
});
