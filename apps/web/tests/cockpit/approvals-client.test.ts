/**
 * The approval client (M21 spec §3): six routes, exactly, through the owner
 * session — and the rows and receipts read from whatever shape the Cloud
 * Core answers with, never filled in.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  APPROVAL_REFUSAL_TR,
  ApprovalError,
  CALENDAR_PROPOSALS_PENDING_PATH,
  MAIL_DRAFTS_PENDING_PATH,
  approvalClient,
  approvalErrorText,
  calendarProposalConfirmPath,
  calendarProposalDiscardPath,
  confirmDraft,
  confirmProposal,
  discardDraft,
  discardProposal,
  fetchPendingDrafts,
  fetchPendingProposals,
  mailDraftConfirmPath,
  mailDraftDiscardPath,
  parseDraft,
  parseProposal,
  parseReceipt,
} from "../../app/lib/cockpit/approvals";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  apiFetch.mockReset();
});

const DRAFT = {
  draft_id: "d1",
  kind: "reply",
  to: ["ali@example.com"],
  cc: [],
  subject: "Re: Proje planı",
  body: "Merhaba Ali,\n\nCuma uygun.",
  in_reply_to: "m1",
  state: "prepared",
  read_back_at: null,
  confirmed_at: null,
  sent_message_id: null,
  created_at: "2026-09-09T09:00:00+03:00",
};

describe("the six routes, exactly", () => {
  it("names them once", () => {
    expect(MAIL_DRAFTS_PENDING_PATH).toBe("/v1/mail/drafts/pending");
    expect(CALENDAR_PROPOSALS_PENDING_PATH).toBe("/v1/calendar/proposals/pending");
    expect(mailDraftConfirmPath("d1")).toBe("/v1/mail/drafts/d1/confirm");
    expect(mailDraftDiscardPath("d1")).toBe("/v1/mail/drafts/d1/discard");
    expect(calendarProposalConfirmPath("p1")).toBe("/v1/calendar/proposals/p1/confirm");
    expect(calendarProposalDiscardPath("p1")).toBe("/v1/calendar/proposals/p1/discard");
    // An id is a path segment, never a path.
    expect(mailDraftConfirmPath("d 1/../x")).toBe("/v1/mail/drafts/d%201%2F..%2Fx/confirm");
  });

  it("GET /v1/mail/drafts/pending, and reads the rows from the shape the route answers with", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { drafts: [DRAFT, { no_id: true }] }));
    const loaded = await fetchPendingDrafts();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit | undefined];
    expect(path).toBe("/v1/mail/drafts/pending");
    expect(init?.method ?? "GET").toBe("GET");
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value).toHaveLength(1); // a row with no id is not a draft
    expect(loaded.value[0]).toEqual(DRAFT);

    apiFetch.mockResolvedValueOnce(json(200, [{ id: "d2", state: "read_back", to: "a@x, b@y" }]));
    const bare = await fetchPendingDrafts();
    expect(bare.kind).toBe("ok");
    if (bare.kind !== "ok") return;
    expect(bare.value[0].draft_id).toBe("d2");
    expect(bare.value[0].to).toEqual(["a@x", "b@y"]);
    expect(bare.value[0].subject).toBeNull();
    expect(bare.value[0].read_back_at).toBeNull();
  });

  it("GET /v1/calendar/proposals/pending, reading the event fields flat or nested", async () => {
    apiFetch.mockResolvedValueOnce(
      json(200, {
        proposals: [
          {
            proposal_id: "p1",
            kind: "create",
            event: { title: "Ali ile toplantı", start: "2026-09-09T09:30:00+03:00", end: "2026-09-09T10:30:00+03:00", location: "Ofis" },
            conflicts: [{ title: "Sprint", start: "2026-09-09T09:00:00+03:00" }, "Diş hekimi", null, {}],
            state: "prepared",
            read_back_at: "2026-09-09T09:05:00+03:00",
          },
          { id: "p2", kind: "reschedule", summary: "Diş hekimi", starts_at: "2026-09-10", all_day: true, event_id: "ev-3" },
        ],
      }),
    );
    const loaded = await fetchPendingProposals();
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/calendar/proposals/pending");
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value).toHaveLength(2);
    expect(loaded.value[0]).toEqual({
      proposal_id: "p1",
      kind: "create",
      title: "Ali ile toplantı",
      start: "2026-09-09T09:30:00+03:00",
      end: "2026-09-09T10:30:00+03:00",
      all_day: null,
      location: "Ofis",
      conflicts: [
        { title: "Sprint", start: "2026-09-09T09:00:00+03:00", end: null },
        { title: "Diş hekimi", start: null, end: null },
      ],
      state: "prepared",
      read_back_at: "2026-09-09T09:05:00+03:00",
      confirmed_at: null,
      event_id: null,
      created_at: null,
    });
    expect(loaded.value[1]).toMatchObject({ proposal_id: "p2", title: "Diş hekimi", start: "2026-09-10", all_day: true, event_id: "ev-3", conflicts: null });
  });

  it("answers absent for a route this Cloud Core does not have yet, and failed for a broken one", async () => {
    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    const absent = await fetchPendingDrafts();
    expect(absent.kind).toBe("absent");
    if (absent.kind === "absent") expect(absent.detail).toContain("/v1/mail/drafts/pending");
    apiFetch.mockResolvedValueOnce(new Response("", { status: 500 }));
    const failed = await fetchPendingProposals();
    expect(failed).toEqual({ kind: "failed", error: "HTTP 500" });
  });

  it("POSTs each of the four actions to its own route, once, with the id as a segment", async () => {
    const cases: Array<[() => Promise<unknown>, string]> = [
      [() => confirmDraft("d 1"), "/v1/mail/drafts/d%201/confirm"],
      [() => discardDraft("d1"), "/v1/mail/drafts/d1/discard"],
      [() => confirmProposal("p1"), "/v1/calendar/proposals/p1/confirm"],
      [() => discardProposal("p1"), "/v1/calendar/proposals/p1/discard"],
    ];
    for (const [call, expected] of cases) {
      apiFetch.mockReset();
      apiFetch.mockResolvedValueOnce(json(200, { state: "sent" }));
      await call();
      expect(apiFetch).toHaveBeenCalledTimes(1);
      const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
      expect(path).toBe(expected);
      expect(init.method).toBe("POST");
      expect(init.body).toBeUndefined(); // the gate takes the id and the session, nothing this page could add
    }
    // The client object the page hands the hook is these four and no other.
    expect(Object.keys(approvalClient).toSorted()).toEqual(["confirmDraft", "confirmProposal", "discardDraft", "discardProposal"]);
  });

  it("reads the receipt from either shape, and never invents a state", async () => {
    expect(parseReceipt({ state: "sent", receipt: { receipt_id: "r1", factual_summary: "Gönderildi." } })).toEqual({
      state: "sent",
      summary: "Gönderildi.",
      receiptId: "r1",
    });
    expect(parseReceipt({ draft: { state: "sent" }, receipt: { id: "r2", speech: "Ali'ye gönderdim." } })).toEqual({
      state: "sent",
      summary: "Ali'ye gönderdim.",
      receiptId: "r2",
    });
    expect(parseReceipt({ proposal: { state: "committed" }, message: "İşlendi." })).toEqual({ state: "committed", summary: "İşlendi.", receiptId: null });
    expect(parseReceipt({ receipt_id: "r3", status: "ok" })).toEqual({ state: "ok", summary: null, receiptId: "r3" });
    expect(parseReceipt(null)).toEqual({ state: null, summary: null, receiptId: null });
    expect(parseReceipt("sent")).toEqual({ state: null, summary: null, receiptId: null });

    apiFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await confirmDraft("d1")).toEqual({ state: null, summary: null, receiptId: null });
  });

  it("turns a refusal into a typed error with the Cloud Core's code, and into the owner's words", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { detail: { code: "send_disabled", message: "host flag off" } }));
    const err = await confirmDraft("d1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApprovalError);
    const typed = err as ApprovalError;
    expect(typed.status).toBe(409);
    expect(typed.code).toBe("send_disabled");
    expect(typed.detail).toBe("host flag off");
    expect(approvalErrorText(typed)).toBe(APPROVAL_REFUSAL_TR.send_disabled);
    expect(approvalErrorText(typed)).toContain("gönderilmedi");

    apiFetch.mockResolvedValueOnce(json(422, { detail: "Taslak henüz okunmadı." }));
    const plain = (await discardDraft("d1").catch((e: unknown) => e)) as ApprovalError;
    expect(plain.code).toBeNull();
    expect(approvalErrorText(plain)).toBe("Taslak henüz okunmadı.");

    apiFetch.mockResolvedValueOnce(json(409, { error_class: "already_committed" }));
    const flat = (await confirmProposal("p1").catch((e: unknown) => e)) as ApprovalError;
    expect(flat.code).toBe("already_committed");
    expect(approvalErrorText(flat)).toBe(APPROVAL_REFUSAL_TR.already_committed);

    apiFetch.mockResolvedValueOnce(new Response("not json", { status: 503 }));
    const opaque = (await discardProposal("p1").catch((e: unknown) => e)) as ApprovalError;
    expect(opaque.code).toBeNull();
    expect(approvalErrorText(opaque)).toBe("HTTP 503");

    apiFetch.mockResolvedValueOnce(json(400, { detail: { code: "strange", message: "açıklama" } }));
    const strange = (await confirmDraft("d1").catch((e: unknown) => e)) as ApprovalError;
    expect(approvalErrorText(strange)).toBe("strange: açıklama");
    expect(approvalErrorText(new Error("ağ koptu"))).toBe("ağ koptu");
  });

  it("parses rows defensively: no id is no row, lists are lists, everything else is null", () => {
    expect(parseDraft(null)).toBeNull();
    expect(parseDraft("d1")).toBeNull();
    expect(parseDraft({ subject: "x" })).toBeNull();
    expect(parseDraft({ id: "d1", to: 5, cc: ["", "c@x"], draft_state: "read_back" })).toEqual({
      draft_id: "d1",
      kind: null,
      to: [],
      cc: ["c@x"],
      subject: null,
      body: null,
      in_reply_to: null,
      state: "read_back",
      read_back_at: null,
      confirmed_at: null,
      sent_message_id: null,
      created_at: null,
    });
    expect(parseProposal({ title: "x" })).toBeNull();
    expect(parseProposal({ proposal_id: "p1", conflicts: "two" })?.conflicts).toBeNull();
    expect(parseProposal({ proposal_id: "p1", conflicts: [] })?.conflicts).toEqual([]);
    expect(parseProposal({ proposal_id: "p1", proposal_state: "read_back" })?.state).toBe("read_back");
  });
});
