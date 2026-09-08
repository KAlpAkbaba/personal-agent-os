/**
 * The Posta panel, its approval pair and the Core's readout for
 * `mail.activity` (M21 spec §3): sentences about the rows the Cloud Core
 * holds and the tokens it published, a pair that asks the Cloud Core to run
 * its own gate exactly once, and never a message this page fetched.
 *
 * Rendered with `react-dom/server` like the rest of this suite. The click
 * is proven the way `tests/research/focus.test.tsx` proves it: the panels
 * are hook-free, so the element tree is walked to the button and its
 * handler invoked — exactly what React would do.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { MailPanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import {
  APPROVAL_REASON_BUSY,
  APPROVAL_REASON_NOT_READ_BACK,
  DRAFT_LINE_MAX_CHARS,
  approvalGate,
  firstLine,
  rowPending,
  rowReadBack,
} from "../../app/lib/cockpit/approval-rows";
import {
  APPROVAL_PAIR_IDLE,
  ApprovalError,
  type ApprovalClient,
  type ApprovalPairProps,
  type ApprovalPairState,
  type ApprovalReceipt,
  type PendingDraft,
} from "../../app/lib/cockpit/approvals";
import { OUTCOME_NO_STATE_TR, outcomeText, runApproval } from "../../app/lib/cockpit/useApprovalPair";
import { MAIL_ACTIVITY_TTL_MS } from "../../app/lib/uistate/contract";
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

function draft(overrides: Partial<PendingDraft> = {}): PendingDraft {
  return {
    draft_id: "d1",
    kind: "reply",
    to: ["ali@example.com"],
    cc: [],
    subject: "Re: Proje planı",
    body: "Merhaba Ali,\r\n\r\nPlanı aldım, Cuma uygun.\nSelamlar",
    in_reply_to: "m1",
    state: "prepared",
    read_back_at: null,
    confirmed_at: null,
    sent_message_id: null,
    created_at: iso(-30_000),
    ...overrides,
  };
}

const READ_BACK = () => draft({ state: "read_back", read_back_at: iso(-12_000) });

const ok = (drafts: PendingDraft[]) => ({ kind: "ok" as const, value: drafts, at: T0 });
const noop = () => {};

function pairOf(overrides: Partial<ApprovalPairProps> = {}): ApprovalPairProps {
  return { ...APPROVAL_PAIR_IDLE, onConfirm: noop, onDiscard: noop, ...overrides };
}

function panel(
  pending: Parameters<typeof MailPanel>[0]["pending"],
  events: ReturnType<typeof event>[] = [AGENT_IDLE()],
  pair: ApprovalPairProps = pairOf(),
  now = T0,
) {
  return renderToStaticMarkup(<MailPanel pending={pending} truth={truthOf(events)} now={now} pair={pair} />);
}

function readout(events: ReturnType<typeof event>[], compact = false, now = T0) {
  return renderToStaticMarkup(<StateReadout intent={visualFor(truthOf(events), now)} compact={compact} />);
}

type ElementLike = { type: unknown; props: Record<string, unknown> };

function isElement(node: unknown): node is ElementLike {
  return !!node && typeof node === "object" && "props" in node && "type" in node;
}

/**
 * Find the rendered element carrying `attr={value}`, expanding function
 * components by calling them. The panels here are pure presentational
 * components with no hooks, so calling them is exactly what React would do.
 */
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

const RECEIPT_SENT: ApprovalReceipt = { state: "sent", summary: "Ali'ye yanıt gönderildi.", receiptId: "r1" };

function fakeClient(overrides: Partial<ApprovalClient> = {}): ApprovalClient {
  return {
    confirmDraft: vi.fn(async () => RECEIPT_SENT),
    discardDraft: vi.fn(async () => ({ state: "discarded", summary: null, receiptId: "r2" })),
    confirmProposal: vi.fn(async () => ({ state: "committed", summary: null, receiptId: "r3" })),
    discardProposal: vi.fn(async () => ({ state: "discarded", summary: null, receiptId: "r4" })),
    ...overrides,
  };
}

/** Plain ports over a local state cell, recording every write. */
function portsOf(client: ApprovalClient, onSettled = vi.fn()) {
  let state: ApprovalPairState = APPROVAL_PAIR_IDLE;
  const writes: ApprovalPairState[] = [];
  return {
    ports: {
      client,
      read: () => state,
      write: (next: ApprovalPairState) => {
        state = next;
        writes.push(next);
      },
      onSettled,
      now: () => T0,
    },
    writes,
    current: () => state,
    onSettled,
  };
}

// ---------------------------------------------------------------- the panel

describe("the Posta panel", () => {
  it("is empty, in words, when the pending route answered with no draft and the bus said nothing", () => {
    const html = panel(ok([]));
    expect(html).toContain('data-panel="mail"');
    expect(html).toContain('data-panel-state="ok"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Bekleyen taslak yok");
    expect(html).toContain('data-mail-activity="untold"');
    expect(html).toContain("Posta etkinliği bildirilmedi.");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).not.toContain("<button");
    expect(html).not.toContain("data-approval-pair");
    expect(html).not.toContain("attention");
    // The gate is named in words on every render.
    expect(html).toContain("aynı kapıdan geçer");
    expect(html).toContain("posta sunucusuna doğrudan ulaşmaz");
  });

  it("never renders the empty sentence for a route that is loading, failed or absent", () => {
    const loading = panel({ kind: "loading" });
    expect(loading).toContain("data-panel-loading");
    expect(loading).toContain("yükleniyor…");
    expect(loading).toContain('data-panel-empty=""');
    expect(loading).not.toContain("Bekleyen taslak yok");
    expect(loading).not.toContain("data-panel-badge");

    const failed = panel({ kind: "failed", error: "HTTP 503" });
    expect(failed).toContain("Alınamadı: HTTP 503");
    expect(failed).not.toContain("Bekleyen taslak yok");

    const absent = panel({ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/mail/drafts/pending yok (HTTP 404)." });
    expect(absent).toContain("data-panel-absent");
    expect(absent).toContain("Henüz yok. Bu Cloud Core sürümünde /v1/mail/drafts/pending yok (HTTP 404).");
    expect(absent).not.toContain("Bekleyen taslak yok");
    for (const html of [loading, failed, absent]) expect(html).not.toContain("<button");
  });

  it("shows a prepared draft — to, subject, the first line of the body, its state — with the pair disabled and the reason", () => {
    const html = panel(ok([draft()]));
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-panel-badge="true">1<');
    expect(html).toContain('data-draft="d1"');
    expect(html).toContain('data-draft-state="prepared"');
    expect(html).toContain('data-draft-pending="yes"');
    expect(html).toContain('data-draft-read-back="no"');
    expect(html).toContain(">Re: Proje planı</span>");
    expect(html).toContain("kime: ali@example.com");
    expect(html).toContain('data-draft-first-line="true">Merhaba Ali,</span>');
    expect(html).not.toContain("Planı aldım"); // the first line and no more
    expect(html).toContain("taslak: hazır · henüz okunmadı");
    expect(html).toContain("yanıt · 30 sn önce");
    // The pair: present, disabled, and saying why.
    expect(html).toContain('data-approval-pair="d1"');
    expect(html).toContain('data-approval-family="draft"');
    expect(html).toContain('data-approval-enabled="no"');
    expect(html).toContain('data-approval-action="confirm" data-approval-target="d1" disabled=""');
    expect(html).toContain('data-approval-action="discard" data-approval-target="d1" disabled=""');
    expect(html).toContain("Onayla — gönder");
    expect(html).toContain("Vazgeç");
    expect(html).toContain('data-approval-reason="not_read_back"');
    expect(html).toContain(APPROVAL_REASON_NOT_READ_BACK);
    // A draft nobody has heard yet is not the owner's to answer: no attention border.
    expect(html).not.toContain("attention");
  });

  it("enables the pair once the draft was read back, and the confirm press asks for that draft exactly once", () => {
    const onConfirm = vi.fn();
    const onDiscard = vi.fn();
    const pair = pairOf({ onConfirm, onDiscard });
    const html = panel(ok([READ_BACK()]), [AGENT_IDLE()], pair);
    expect(html).toContain('data-draft-read-back="yes"');
    expect(html).toContain('data-approval-enabled="yes"');
    expect(html).not.toContain('disabled=""');
    expect(html).toContain("taslak: okundu (12 sn önce)");
    expect(html).not.toContain("henüz okunmadı");
    expect(html).not.toContain("data-approval-reason");
    expect(html).toContain('class="panel attention"');

    const tree = <MailPanel pending={ok([READ_BACK()])} truth={truthOf([AGENT_IDLE()])} now={T0} pair={pair} />;
    click(findByData(tree, "data-approval-action", "confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("d1");
    expect(onDiscard).not.toHaveBeenCalled();

    click(findByData(tree, "data-approval-action", "discard"));
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(onDiscard).toHaveBeenCalledWith("d1");
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("disables the pair while a call is in flight, with that reason", () => {
    const html = panel(ok([READ_BACK()]), [AGENT_IDLE()], pairOf({ busy: { action: "confirm_draft", id: "d1" } }));
    expect(html).toContain('data-approval-enabled="no"');
    expect(html).toContain('data-approval-in-flight="yes"');
    expect(html).toContain('data-approval-action="confirm" data-approval-target="d1" disabled=""');
    expect(html).toContain('data-approval-reason="busy"');
    expect(html).toContain(APPROVAL_REASON_BUSY);
    // A call on ANOTHER row disables this pair too: one at a time.
    const other = panel(ok([READ_BACK()]), [AGENT_IDLE()], pairOf({ busy: { action: "confirm_proposal", id: "p9" } }));
    expect(other).toContain('data-approval-enabled="no"');
    expect(other).toContain('data-approval-in-flight="no"');
  });

  it("shows a sent draft without a pair, counts it as nothing pending, and names what was sent", () => {
    const sent = draft({ state: "sent", read_back_at: iso(-60_000), confirmed_at: iso(-20_000), sent_message_id: "m-77" });
    const html = panel(ok([sent]), [MAIL_ACTIVITY("INBOX", "Re: Proje planı", "sent")], pairOf(), T0 + 3_000);
    expect(html).toContain('data-draft-state="sent"');
    expect(html).toContain('data-draft-pending="no"');
    expect(html).not.toContain("data-approval-pair");
    expect(html).not.toContain("<button");
    expect(html).toContain("taslak: gönderildi · onaylandı 23 sn önce · ileti: m-77");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).toContain("Bekleyen taslak yok");
    expect(html).not.toContain("attention");
    // The bus agrees, in the spec's words, with its age.
    expect(html).toContain('data-mail-activity="active"');
    expect(html).toContain("Gönderildi · Re: Proje planı · 3 sn önce");
  });

  it("prints the last answer for a draft, dated, and not a proposal's", () => {
    const outcome = { action: "confirm_draft" as const, id: "d1", ok: true, text: "Gönderildi · Ali'ye yanıt gönderildi.", at: T0 - 5_000 };
    const html = panel(ok([]), [AGENT_IDLE()], pairOf({ outcome }));
    expect(html).toContain('data-approval-outcome="confirm_draft"');
    expect(html).toContain('data-approval-ok="yes"');
    expect(html).toContain('data-approval-target="d1"');
    expect(html).toContain("Ali&#x27;ye yanıt gönderildi. · 5 sn önce");

    const refused = panel(ok([]), [AGENT_IDLE()], pairOf({ outcome: { ...outcome, ok: false, text: "Gönderme bu sunucuda kapalı" } }));
    expect(refused).toContain('data-approval-ok="no"');
    expect(refused).toContain("panel-unknown");

    const proposal = panel(ok([]), [AGENT_IDLE()], pairOf({ outcome: { ...outcome, action: "confirm_proposal", id: "p1" } }));
    expect(proposal).not.toContain("data-approval-outcome");
  });

  it("states the bus activity in the spec's words with its age, and last-known once it aged out", () => {
    const live = panel(ok([]), [MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")], pairOf(), T0 + 3_000);
    expect(live).toContain('data-mail-stage="active"');
    expect(live).toContain('data-mail-activity="active"');
    expect(live).toContain('data-mail-caption="Taslak okundu — onay bekliyor · Proje planı"');
    expect(live).toContain("Taslak okundu — onay bekliyor · Proje planı · 3 sn önce");
    expect(live).not.toContain("Son bilinen");

    const stale = panel(ok([]), [MAIL_ACTIVITY("INBOX")], pairOf(), T0 + MAIL_ACTIVITY_TTL_MS + 1_000);
    expect(stale).toContain('data-mail-stage="none"');
    expect(stale).toContain('data-mail-last-known="active"');
    expect(stale).toContain("Son bilinen: Gelen kutusu okunuyor · 46 sn önce");

    // A calendar event is not a mail event.
    expect(panel(ok([]), [CALENDAR_ACTIVITY("today")])).toContain('data-mail-activity="untold"');
  });

  it("says what a row did not report rather than filling it in", () => {
    const bare = draft({ kind: null, to: [], subject: null, body: null, state: null, created_at: null });
    const html = panel(ok([bare]));
    expect(html).toContain(">konu bildirilmedi</span>");
    expect(html).toContain("alıcı bildirilmedi");
    expect(html).toContain('data-draft-first-line="true">gövde bildirilmedi</span>');
    expect(html).toContain("taslak: durum bildirilmedi · henüz okunmadı");
    // No state at all: the pending route listed it, so it is pending — and not read back.
    expect(html).toContain('data-draft-pending="yes"');
    expect(html).toContain('data-approval-enabled="no"');
  });
});

// ------------------------------------------------------------ the runner

describe("the approval runner", () => {
  it("makes exactly one call with the draft's id, writes busy then the receipt's outcome, and reloads the lists", async () => {
    const client = fakeClient();
    const { ports, writes, current, onSettled } = portsOf(client);
    expect(await runApproval(ports, "confirm_draft", "d1")).toBe(true);
    expect(client.confirmDraft).toHaveBeenCalledTimes(1);
    expect(client.confirmDraft).toHaveBeenCalledWith("d1");
    expect(client.discardDraft).not.toHaveBeenCalled();
    expect(client.confirmProposal).not.toHaveBeenCalled();
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual({ busy: { action: "confirm_draft", id: "d1" }, outcome: null });
    expect(current().busy).toBeNull();
    expect(current().outcome).toEqual({
      action: "confirm_draft",
      id: "d1",
      ok: true,
      text: "Gönderildi · Ali'ye yanıt gönderildi.",
      at: T0,
    });
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it("refuses a second press while the first is in flight: the client is still called once", async () => {
    // The first call hangs until released; every later call answers at once.
    const deferred: { release: ((receipt: ApprovalReceipt) => void) | null } = { release: null };
    const confirmDraft = vi.fn(async () => RECEIPT_SENT);
    confirmDraft.mockImplementationOnce(
      () =>
        new Promise<ApprovalReceipt>((resolve) => {
          deferred.release = resolve;
        }),
    );
    const client = fakeClient({ confirmDraft });
    const { ports, current } = portsOf(client);

    const first = runApproval(ports, "confirm_draft", "d1");
    expect(current().busy).toEqual({ action: "confirm_draft", id: "d1" });
    expect(await runApproval(ports, "confirm_draft", "d1")).toBe(false);
    expect(await runApproval(ports, "discard_draft", "d1")).toBe(false);
    expect(await runApproval(ports, "confirm_proposal", "p1")).toBe(false);
    expect(confirmDraft).toHaveBeenCalledTimes(1);
    expect(client.discardDraft).not.toHaveBeenCalled();
    expect(client.confirmProposal).not.toHaveBeenCalled();

    expect(deferred.release).not.toBeNull();
    deferred.release?.(RECEIPT_SENT);
    expect(await first).toBe(true);
    expect(current().busy).toBeNull();
    // Once settled, the next press goes through — a second send is the GATE's to refuse, not this page's to hide.
    expect(await runApproval(ports, "confirm_draft", "d1")).toBe(true);
    expect(confirmDraft).toHaveBeenCalledTimes(2);
  });

  it("turns the gate's refusal into the owner's words, says nothing was sent, and still reloads", async () => {
    const client = fakeClient({
      confirmDraft: vi.fn(async () => {
        throw new ApprovalError(409, "send_disabled", "send disabled by host flag");
      }),
    });
    const { ports, current, onSettled } = portsOf(client);
    expect(await runApproval(ports, "confirm_draft", "d1")).toBe(true);
    const outcome = current().outcome;
    expect(outcome?.ok).toBe(false);
    expect(outcome?.text).toContain("PAGENTOS_MAIL_SEND_ENABLED");
    expect(outcome?.text).toContain("gönderilmedi");
    expect(outcome?.text).not.toContain("Gönderildi");
    expect(current().busy).toBeNull();
    expect(onSettled).toHaveBeenCalledTimes(1);

    const unknown = fakeClient({
      confirmDraft: vi.fn(async () => {
        throw new ApprovalError(422, "odd_code", "Taslak bulunamadı");
      }),
    });
    const second = portsOf(unknown);
    await runApproval(second.ports, "confirm_draft", "d2");
    expect(second.current().outcome?.text).toBe("odd_code: Taslak bulunamadı");
  });

  it("never says 'sent' on the strength of a 2xx alone", () => {
    expect(outcomeText("confirm_draft", { state: null, summary: null, receiptId: "r1" })).toBe(OUTCOME_NO_STATE_TR.confirm_draft);
    expect(outcomeText("confirm_draft", { state: null, summary: null, receiptId: "r1" })).not.toContain("Gönderildi");
    expect(outcomeText("discard_draft", { state: "discarded", summary: null, receiptId: null })).toBe("Taslaktan vazgeçildi");
    expect(outcomeText("confirm_draft", { state: "sent", summary: null, receiptId: null })).toBe("Gönderildi");
    expect(outcomeText("confirm_draft", { state: "queued", summary: "sırada", receiptId: null })).toBe("durum: queued · sırada");
    expect(outcomeText("confirm_proposal", { state: "committed", summary: "Takvime eklendi.", receiptId: null })).toBe(
      "Takvime işlendi · Takvime eklendi.",
    );
    // A draft word is not a proposal word.
    expect(outcomeText("confirm_proposal", { state: "sent", summary: null, receiptId: null })).toBe("durum: sent");
  });
});

// -------------------------------------------------------------- the rows

describe("the row helpers", () => {
  it("the first line is the first non-empty line, bounded, and never the body", () => {
    expect(firstLine("Merhaba Ali,\r\n\r\nPlanı aldım.")).toBe("Merhaba Ali,");
    expect(firstLine("\n\n  Selam  \nikinci")).toBe("Selam");
    expect(firstLine("")).toBeNull();
    expect(firstLine(null)).toBeNull();
    expect(firstLine("\n \n")).toBeNull();
    const long = "a".repeat(DRAFT_LINE_MAX_CHARS + 40);
    const cut = firstLine(long);
    expect(cut).toHaveLength(DRAFT_LINE_MAX_CHARS);
    expect(cut?.endsWith("…")).toBe(true);
    expect(firstLine("a".repeat(DRAFT_LINE_MAX_CHARS))).toHaveLength(DRAFT_LINE_MAX_CHARS);
  });

  it("pending and read-back are read from the row alone", () => {
    expect(rowPending("prepared")).toBe(true);
    expect(rowPending("read_back")).toBe(true);
    expect(rowPending(null)).toBe(true);
    expect(rowPending("sent")).toBe(false);
    expect(rowPending("committed")).toBe(false);
    expect(rowPending("discarded")).toBe(false);
    expect(rowReadBack({ state: "prepared", read_back_at: null })).toBe(false);
    expect(rowReadBack({ state: "prepared", read_back_at: iso(-1) })).toBe(true);
    expect(rowReadBack({ state: "read_back", read_back_at: null })).toBe(true);
    expect(rowReadBack({ state: null, read_back_at: null })).toBe(false);
  });

  it("the gate opens only for a pending row that was read back while nothing is in flight", () => {
    expect(approvalGate({ state: "prepared", read_back_at: null }, null)).toEqual({
      enabled: false,
      reason: APPROVAL_REASON_NOT_READ_BACK,
      reasonKind: "not_read_back",
    });
    expect(approvalGate({ state: "read_back", read_back_at: iso(-1) }, null)).toEqual({ enabled: true, reason: null, reasonKind: null });
    expect(approvalGate({ state: "read_back", read_back_at: iso(-1) }, { action: "discard_draft", id: "x" })).toEqual({
      enabled: false,
      reason: APPROVAL_REASON_BUSY,
      reasonKind: "busy",
    });
    expect(approvalGate({ state: "sent", read_back_at: iso(-1) }, null)).toEqual({ enabled: false, reason: null, reasonKind: "settled" });
  });
});

// ------------------------------------------------------------- the readout

describe("the Core's readout for mail", () => {
  it("headlines the mail posture with the caption and the facts beneath, and no bar", () => {
    const html = readout([MAIL_ACTIVITY("INBOX", "Proje planı", "read_back")]);
    expect(html).toContain('data-core-kind="mail_activity"');
    expect(html).toContain('data-core-state="mail.activity"');
    expect(html).toContain('data-core-subsystem="mail"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain("Posta okunuyor");
    expect(html).toContain("Posta");
    expect(html).toContain('data-label="true">Taslak okundu — onay bekliyor · Proje planı</p>');
    expect(html).not.toContain("data-caption");
    expect(html).toContain("data-mail-facts");
    expect(html).toContain('data-mail-folder="INBOX"');
    expect(html).toContain('data-mail-subject="Proje planı"');
    expect(html).toContain('data-mail-draft-state="read_back"');
    expect(html).toContain("klasör: Gelen kutusu · konu: Proje planı · taslak: okundu");
    expect(html).toContain("Hiçbir şey sahip onayı olmadan gönderilmez.");
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-document-facts");
    expect(html).not.toContain("data-calendar-facts");
  });

  it("says what was not reported when the publisher named nothing", () => {
    const html = readout([MAIL_ACTIVITY(null)]);
    expect(html).toContain('data-label="true">Posta okunuyor</p>');
    expect(html).toContain("klasör bildirilmedi · konu bildirilmedi · taslak bildirilmedi");
    expect(html).toContain('data-mail-folder=""');
  });

  it("keeps the caption in the compact form and drops the long line", () => {
    const html = readout([MAIL_ACTIVITY("Arşiv", "Fatura")], true);
    expect(html).toContain("Arşiv klasörü okunuyor · Fatura");
    expect(html).not.toContain("data-mail-facts");
  });

  it("names the aged-out activity as last-known rather than as reading, with its facts", () => {
    const html = readout([MAIL_ACTIVITY("INBOX", "Fatura", "read_back")], false, T0 + MAIL_ACTIVITY_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="mail.activity"');
    expect(html).toContain("Posta okunuyor");
    expect(html).toContain("data-mail-facts");
    expect(html).toContain("konu: Fatura");
    expect(html).not.toContain("Gönderildi");
  });

  it("prints no mail line for any other kind", () => {
    expect(readout([AGENT_IDLE()])).not.toContain("data-mail-facts");
    expect(readout([DOCUMENT_ANALYSIS()])).not.toContain("data-mail-facts");
    expect(readout([CALENDAR_ACTIVITY()])).not.toContain("data-mail-facts");
  });
});
