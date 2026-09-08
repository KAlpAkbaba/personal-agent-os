/**
 * The Yeni Yetenek panel, its two chips and the Core's readout for
 * `capability.genesis` (M24 spec §8): sentences about the rows the list
 * route holds and the tokens the Cloud Core published, "Onayla" ONLY for a
 * run at `awaiting_approval`, "Vazgeç" while a run is active, neither once
 * it settled or failed, two chips that ask the Cloud Core exactly once each,
 * and never a capability this page registered.
 *
 * Rendered with `react-dom/server` like the rest of this suite. The click
 * is proven the way `apps-panel.test.tsx` proves it: the panel is
 * hook-free, so the element tree is walked to the control and its handler
 * invoked — exactly what React would do.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { GenesisPanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import {
  GENESIS_ACTION_LABEL,
  GENESIS_AUTHORITY_LABEL,
  GENESIS_REASON_BUSY,
  GENESIS_REASON_NOT_ACTIVE,
  GENESIS_REASON_NOT_AWAITING,
  GENESIS_ROWS_SHOWN,
  GENESIS_SIDE_EFFECT_LABEL,
  genesisActionGate,
  genesisRowActions,
  genesisRowLine,
  rowIsActive,
  rowIsAwaiting,
} from "../../app/lib/cockpit/genesis-rows";
import {
  GENESIS_CONTROL_IDLE,
  GENESIS_ROUTE_ABSENT,
  type GenesisActionReceipt,
  type GenesisClient,
  type GenesisControlProps,
  type GenesisControlState,
  type GenesisRunRow,
  GenesisActionError,
} from "../../app/lib/cockpit/genesis";
import { GENESIS_OUTCOME_NO_STATE_TR, genesisOutcomeText, runGenesisAction } from "../../app/lib/cockpit/useGenesisControl";
import { GENESIS_RUN_STATES, GENESIS_TTL_MS } from "../../app/lib/uistate/contract";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  ARTIFACT_FACTORY,
  CAPABILITY_GENESIS,
  CAPABILITY_GENESIS_BARE,
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

function row(overrides: Partial<GenesisRunRow> = {}): GenesisRunRow {
  return {
    run_id: "g1",
    capability: "counterbox.increment",
    state: "building",
    approval_required: null,
    authority_class: null,
    side_effect_class: null,
    error_class: null,
    error_message: null,
    created_at: iso(-90_000),
    updated_at: iso(-30_000),
    ...overrides,
  };
}

const AWAITING = () =>
  row({ state: "awaiting_approval", approval_required: true, authority_class: "mutating_unauthorized", side_effect_class: "mutate_external" });
const ACTIVE = () => row({ run_id: "g2", capability: "lampbox.toggle", state: "testing" });
const VERIFIED = () => row({ run_id: "g3", capability: "counterbox.read", state: "verified", authority_class: "read_only", side_effect_class: "read" });
const FAILED = () =>
  row({ run_id: "g4", capability: "lampbox.set", state: "failed", error_class: "dependency_unavailable", error_message: "Test lambası cevap vermiyor." });

const ok = (rows: GenesisRunRow[]) => ({ kind: "ok" as const, value: rows, at: T0 });
const noop = () => {};

function controlOf(overrides: Partial<GenesisControlProps> = {}): GenesisControlProps {
  return { ...GENESIS_CONTROL_IDLE, onApprove: noop, onCancel: noop, ...overrides };
}

function panel(
  runs: Parameters<typeof GenesisPanel>[0]["runs"],
  events: ReturnType<typeof event>[] = [AGENT_IDLE()],
  control: GenesisControlProps = controlOf(),
  now = T0,
) {
  return renderToStaticMarkup(<GenesisPanel runs={runs} truth={truthOf(events)} now={now} control={control} />);
}

function readout(events: ReturnType<typeof event>[], compact = false, now = T0) {
  return renderToStaticMarkup(<StateReadout intent={visualFor(truthOf(events), now)} compact={compact} />);
}

type ElementLike = { type: unknown; props: Record<string, unknown> };

function isElement(node: unknown): node is ElementLike {
  return !!node && typeof node === "object" && "props" in node && "type" in node;
}

/**
 * Find the rendered element carrying every `attrs` pair, expanding function
 * components by calling them. The panel is a pure presentational component
 * with no hooks, so calling it is exactly what React would do.
 */
function findByData(root: unknown, attrs: Record<string, string>): ElementLike | null {
  const queue: unknown[] = [root];
  let guard = 0;
  while (queue.length > 0 && guard++ < 10_000) {
    const node = queue.shift();
    if (Array.isArray(node)) {
      queue.push(...node);
      continue;
    }
    if (!isElement(node)) continue;
    if (Object.entries(attrs).every(([k, v]) => node.props[k] === v)) return node;
    if (typeof node.type === "function") {
      const draw = node.type as (props: Record<string, unknown>) => unknown;
      queue.push(draw(node.props));
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

const RECEIPT_ROLLING_OUT: GenesisActionReceipt = { state: "rolling_out", errorClass: null, summary: "Yetkilendirme kaydedildi.", receiptId: "r1" };
const RECEIPT_CANCELLED: GenesisActionReceipt = { state: "failed", errorClass: "cancelled", summary: null, receiptId: "r2" };

function fakeClient(overrides: Partial<GenesisClient> = {}): GenesisClient {
  return {
    approve: vi.fn(async () => RECEIPT_ROLLING_OUT),
    cancel: vi.fn(async () => RECEIPT_CANCELLED),
    ...overrides,
  };
}

/** Plain ports over a local state cell, recording every write. */
function portsOf(client: GenesisClient, onSettled = vi.fn()) {
  let state: GenesisControlState = GENESIS_CONTROL_IDLE;
  const writes: GenesisControlState[] = [];
  return {
    ports: {
      client,
      read: () => state,
      write: (next: GenesisControlState) => {
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

describe("the Yeni Yetenek panel", () => {
  it("is empty, in words, when the list route answered with no run and the bus said nothing", () => {
    const html = panel(ok([]));
    expect(html).toContain('data-panel="genesis"');
    expect(html).toContain('data-panel-state="ok"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Henüz yeni bir yetenek istenmedi.");
    expect(html).toContain('data-genesis-activity="untold"');
    expect(html).toContain("Yeni yetenek etkinliği bildirilmedi.");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).toContain('data-genesis-awaiting="0"');
    expect(html).toContain(">Yeni Yetenek<");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("data-genesis-outcome");
    expect(html).not.toContain("attention");
    // What the chips do — and what "having" a capability means — is said in words on every render.
    expect(html).toContain("yalnızca onay bekleyen bir çalışma için gösterilir");
    expect(html).toContain("Onaylıyorum");
    expect(html).toContain("kayıt bırakmaz");
    // The quotes around the word are HTML-escaped in the markup; the sentence is what is asserted.
    expect(html).toContain("dendiğinde vardır, önce değil");
    expect(html).toContain("Bu ekran arayüz araştırmaz, bağdaştırıcı yazmaz, yetenek kaydetmez, uygulamaya ulaşmaz.");
  });

  it("never renders the empty sentence for a route that is loading, failed or absent", () => {
    const loading = panel({ kind: "loading" });
    expect(loading).toContain("data-panel-loading");
    expect(loading).toContain("yükleniyor…");
    expect(loading).toContain('data-panel-empty=""');
    expect(loading).not.toContain("Henüz yeni bir yetenek istenmedi");
    expect(loading).not.toContain("data-panel-badge");

    const failed = panel({ kind: "failed", error: "HTTP 503" });
    expect(failed).toContain("Alınamadı: HTTP 503");
    expect(failed).not.toContain("Henüz yeni bir yetenek istenmedi");

    const absent = panel({ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/genesis/runs yok (HTTP 404)." });
    expect(absent).toContain("data-panel-absent");
    expect(absent).toContain("Henüz yok. Bu Cloud Core sürümünde /v1/genesis/runs yok (HTTP 404).");
    expect(absent).not.toContain("Henüz yeni bir yetenek istenmedi");
    for (const html of [loading, failed, absent]) expect(html).not.toContain("<button");
  });

  it("lists an awaiting run with its 'Onayla' and its 'Vazgeç', draws attention to it, and names why it waits", () => {
    const html = panel(ok([AWAITING()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-panel-badge="true">1 onay bekliyor / 1<');
    expect(html).toContain('data-genesis-awaiting="1"');
    expect(html).toContain('data-genesis-run="g1"');
    expect(html).toContain('data-genesis-run-state="awaiting_approval"');
    expect(html).toContain('data-genesis-run-capability="counterbox.increment"');
    expect(html).toContain('data-genesis-run-awaiting="yes"');
    expect(html).toContain('data-genesis-run-active="yes"');
    expect(html).toContain('data-genesis-run-failed="no"');
    expect(html).toContain('data-genesis-run-approval-required="yes"');
    expect(html).toContain("counterbox.increment");
    expect(html).toContain("30 sn önce");
    expect(html).toContain('data-genesis-line="true">onay bekliyor · onay gerekli · yetki: yetkisiz varlıkta değişiklik · etki: dış değişiklik</span>');
    // The chips: approve and cancel, both enabled, nothing in flight.
    expect(html).toContain('data-genesis-controls="g1"');
    expect(html).toContain('data-genesis-in-flight="no"');
    expect(html).toContain('data-genesis-action="approve" data-genesis-target="g1" data-genesis-enabled="yes">Onayla</button>');
    expect(html).toContain('data-genesis-action="cancel" data-genesis-target="g1" data-genesis-enabled="yes">Vazgeç</button>');
    expect((html.match(/<button/g) ?? []).length).toBe(2);
    expect(html).not.toContain("data-genesis-reason");
    expect(html).not.toContain("data-genesis-error-message");
  });

  it("lists an active run with 'Vazgeç' and no 'Onayla'", () => {
    const html = panel(ok([ACTIVE()]));
    expect(html).toContain('data-genesis-run="g2"');
    expect(html).toContain('data-genesis-run-state="testing"');
    expect(html).toContain('data-genesis-run-awaiting="no"');
    expect(html).toContain('data-genesis-run-active="yes"');
    expect(html).toContain("lampbox.toggle");
    expect(html).toContain('data-genesis-line="true">sınanıyor</span>');
    expect(html).toContain('data-genesis-action="cancel" data-genesis-target="g2" data-genesis-enabled="yes">Vazgeç</button>');
    expect(html).not.toContain('data-genesis-action="approve"');
    expect(html).not.toContain(">Onayla<");
    expect((html.match(/<button/g) ?? []).length).toBe(1);
    expect(html).toContain('data-panel-badge="true">1<');
    expect(html).toContain('data-genesis-awaiting="0"');
    expect(html).not.toContain("attention");
    // Every working state earns exactly the one chip, never "Onayla".
    for (const state of ["capability_missing", "researching", "designing", "building", "classifying", "rolling_out", "registering"]) {
      const working = panel(ok([row({ state })]));
      expect(working, state).toContain('data-genesis-action="cancel"');
      expect(working, state).not.toContain('data-genesis-action="approve"');
    }
  });

  it("lists a verified run with neither chip, and never dresses anything else as verified", () => {
    const html = panel(ok([VERIFIED()]));
    expect(html).toContain('data-genesis-run="g3"');
    expect(html).toContain('data-genesis-run-state="verified"');
    expect(html).toContain('data-genesis-run-awaiting="no"');
    expect(html).toContain('data-genesis-run-active="no"');
    expect(html).toContain("counterbox.read");
    expect(html).toContain('data-genesis-line="true">doğrulandı · yetki: salt okunur · etki: okuma</span>');
    expect(html).not.toContain("<button");
    expect(html).not.toContain("data-genesis-controls");
    expect(html).not.toContain("attention");
    // Available and used are settled too: neither chip.
    for (const state of ["available", "used"]) {
      const settled = panel(ok([row({ state })]));
      expect(settled, state).not.toContain("<button");
      expect(settled, state).not.toContain("doğrulandı<");
    }
  });

  it("lists a failed run naming the error, draws attention to it, and offers neither chip", () => {
    const html = panel(ok([FAILED()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-genesis-run="g4"');
    expect(html).toContain('data-genesis-run-state="failed"');
    expect(html).toContain('data-genesis-run-failed="yes"');
    expect(html).toContain('data-genesis-run-active="no"');
    expect(html).toContain('data-genesis-run-error-class="dependency_unavailable"');
    expect(html).toContain("lampbox.set");
    expect(html).toContain('data-genesis-line="true">başarısız — dependency_unavailable</span>');
    expect(html).toContain('data-genesis-error-message="true">Test lambası cevap vermiyor.</span>');
    expect(html).not.toContain("<button");
    // The ROW never says verified or available (the note's sentence about what "doğrulandı" means is not a row).
    expect(html).not.toContain(">doğrulandı");
    expect(html).not.toContain("doğrulandı<");
    expect(html).not.toContain("kullanılabilir");
    // A failure whose class nobody published says so; a message is printed only beside failed.
    const bare = panel(ok([row({ state: "failed" })]));
    expect(bare).toContain('data-genesis-line="true">başarısız · hata sınıfı bildirilmedi</span>');
    expect(bare).not.toContain("data-genesis-error-message");
    const notFailed = panel(ok([row({ state: "testing", error_message: "eski mesaj" })]));
    expect(notFailed).not.toContain("data-genesis-error-message");
  });

  it("says what a row did not report rather than filling it in, and gives a word it cannot read neither chip", () => {
    const html = panel(ok([row({ capability: null, state: null, updated_at: null, created_at: null })]));
    expect(html).toContain(">yetenek bildirilmedi<");
    expect(html).toContain('data-genesis-run-state=""');
    expect(html).toContain('data-genesis-run-capability=""');
    expect(html).toContain('data-genesis-run-approval-required=""');
    expect(html).toContain('data-genesis-line="true">durum bildirilmedi</span>');
    // No state is not a run known to be going: nothing invites a click on a guess.
    expect(html).not.toContain("<button");
    // A newer server's word is printed verbatim — still a published fact — and earns no chip either.
    const unknown = panel(ok([row({ state: "approved" })]));
    expect(unknown).toContain('data-genesis-line="true">approved</span>');
    expect(unknown).toContain('data-genesis-run-active="no"');
    expect(unknown).not.toContain("<button");
    // The flag's "no" is printed when sent, and a class outside the spec's three verbatim.
    const flagged = panel(ok([row({ state: "classifying", approval_required: false, authority_class: "delegated" })]));
    expect(flagged).toContain('data-genesis-run-approval-required="no"');
    expect(flagged).toContain('data-genesis-line="true">sınıflandırılıyor · onay gerekmiyor · yetki: delegated</span>');
  });

  it("shows the last runs as the route orders them, bounded, and counts the waiting ones in the badge", () => {
    const rows = Array.from({ length: GENESIS_ROWS_SHOWN + 3 }, (_, i) =>
      row({ run_id: `g${i}`, capability: `cap.${i}`, state: i < 2 ? "awaiting_approval" : "verified" }),
    );
    const html = panel(ok(rows));
    expect(html).toContain(`data-panel-badge="true">2 onay bekliyor / ${GENESIS_ROWS_SHOWN + 3}<`);
    expect(html).toContain('data-genesis-awaiting="2"');
    expect((html.match(/data-genesis-run="g\d+"/g) ?? []).length).toBe(GENESIS_ROWS_SHOWN);
    expect(html).toContain('data-genesis-run="g0"');
    expect(html).not.toContain(`data-genesis-run="g${GENESIS_ROWS_SHOWN}"`);
    expect((html.match(/data-genesis-action="approve"/g) ?? []).length).toBe(2);
  });

  it("each chip asks for that run exactly once, and only through its own handler", () => {
    const onApprove = vi.fn();
    const onCancel = vi.fn();
    const rows = [AWAITING(), ACTIVE(), VERIFIED(), FAILED()];
    const tree = <GenesisPanel runs={ok(rows)} truth={truthOf([AGENT_IDLE()])} now={T0} control={controlOf({ onApprove, onCancel })} />;

    click(findByData(tree, { "data-genesis-action": "approve", "data-genesis-target": "g1" }));
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onApprove).toHaveBeenCalledWith("g1");
    expect(onCancel).not.toHaveBeenCalled();

    click(findByData(tree, { "data-genesis-action": "cancel", "data-genesis-target": "g2" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledWith("g2");
    expect(onApprove).toHaveBeenCalledTimes(1);

    // An active run has no "Onayla" to find; a verified or failed run has neither.
    expect(findByData(tree, { "data-genesis-action": "approve", "data-genesis-target": "g2" })).toBeNull();
    expect(findByData(tree, { "data-genesis-action": "approve", "data-genesis-target": "g3" })).toBeNull();
    expect(findByData(tree, { "data-genesis-action": "cancel", "data-genesis-target": "g3" })).toBeNull();
    expect(findByData(tree, { "data-genesis-action": "approve", "data-genesis-target": "g4" })).toBeNull();
    expect(findByData(tree, { "data-genesis-action": "cancel", "data-genesis-target": "g4" })).toBeNull();
  });

  it("disables every chip while one call is in flight, marking the run and the action it is, with the reason said once", () => {
    const html = panel(ok([AWAITING(), ACTIVE()]), [AGENT_IDLE()], controlOf({ busy: { action: "approve", id: "g1" } }));
    expect(html).toContain('data-genesis-controls="g1" data-genesis-in-flight="yes" data-genesis-in-flight-action="approve"');
    expect(html).toContain('data-genesis-controls="g2" data-genesis-in-flight="no" data-genesis-in-flight-action=""');
    expect(html).toContain('data-genesis-action="approve" data-genesis-target="g1" data-genesis-enabled="no" disabled=""');
    expect(html).toContain('data-genesis-action="cancel" data-genesis-target="g1" data-genesis-enabled="no" disabled=""');
    expect(html).toContain('data-genesis-action="cancel" data-genesis-target="g2" data-genesis-enabled="no" disabled=""');
    expect(html).toContain('data-genesis-reason="busy" data-genesis-reason-for="approve,cancel"');
    expect(html).toContain('data-genesis-reason="busy" data-genesis-reason-for="cancel"');
    expect(html).toContain(GENESIS_REASON_BUSY);
    expect(html).not.toContain(`Onayla, Vazgeç: ${GENESIS_REASON_BUSY}`);
    // Said once per run, not once per chip.
    expect((html.match(/data-genesis-reason="busy"/g) ?? []).length).toBe(2);
  });

  it("prints the last call's answer, dated, with the run and the action it was about", () => {
    const outcome = { action: "approve" as const, id: "g1", ok: true, text: "Yayına alınıyor · Yetkilendirme kaydedildi.", at: T0 - 5_000 };
    const html = panel(ok([AWAITING()]), [AGENT_IDLE()], controlOf({ outcome }));
    expect(html).toContain('data-genesis-outcome="approve"');
    expect(html).toContain('data-genesis-ok="yes"');
    expect(html).toContain('data-genesis-target="g1"');
    expect(html).toContain("Yayına alınıyor · Yetkilendirme kaydedildi. · 5 sn önce");

    const refused = panel(ok([AWAITING()]), [AGENT_IDLE()], controlOf({ outcome: { ...outcome, ok: false, text: "Çalışma onay beklemiyor; onaylanmadı." } }));
    expect(refused).toContain('data-genesis-ok="no"');
    expect(refused).toContain("panel-unknown");
    expect(refused).toContain("Çalışma onay beklemiyor; onaylanmadı.");
  });

  it("states the bus activity in the spec's words with its age and posture, and last-known once it aged out", () => {
    const live = panel(ok([]), [CAPABILITY_GENESIS("counterbox.increment", "building")], controlOf(), T0 + 3_000);
    expect(live).toContain('data-genesis-stage="active"');
    expect(live).toContain('data-genesis-activity="active"');
    expect(live).toContain('data-genesis-posture="building"');
    expect(live).toContain('data-genesis-caption="counterbox.increment için bağdaştırıcı yazılıyor"');
    expect(live).toContain("counterbox.increment için bağdaştırıcı yazılıyor · 3 sn önce");
    expect(live).not.toContain("Son bilinen");
    // The list is the list: the bus building something does not put a row on it, nor a chip.
    expect(live).toContain("Henüz yeni bir yetenek istenmedi.");
    expect(live).not.toContain("<button");

    const waiting = panel(ok([]), [CAPABILITY_GENESIS("counterbox.increment", "awaiting_approval", null, true)], controlOf(), T0 + 3_000);
    expect(waiting).toContain('data-genesis-posture="waiting"');
    expect(waiting).toContain("counterbox.increment onay bekliyor · 3 sn önce");
    // The bus says a run waits; the ROW is what earns "Onayla", and there is none.
    expect(waiting).not.toContain("<button");

    const failed = panel(ok([]), [CAPABILITY_GENESIS("counterbox.increment", "failed", "postcondition_failed")], controlOf(), T0 + 3_000);
    expect(failed).toContain('data-genesis-posture="failed"');
    expect(failed).toContain("counterbox.increment başarısız — postcondition_failed · 3 sn önce");

    const stale = panel(ok([]), [CAPABILITY_GENESIS("counterbox.increment", "verified")], controlOf(), T0 + GENESIS_TTL_MS + 1_000);
    expect(stale).toContain('data-genesis-stage="none"');
    expect(stale).toContain('data-genesis-last-known="active"');
    expect(stale).toContain('data-genesis-posture="settled"');
    expect(stale).toContain("Son bilinen: counterbox.increment doğrulandı · 46 sn önce");

    // A document, mail, artifact or app event is not a genesis event.
    for (const other of [[DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [ARTIFACT_FACTORY()], [APP_FACTORY()]]) {
      const html = panel(ok([]), other);
      expect(html).toContain('data-genesis-activity="untold"');
      expect(html).toContain('data-genesis-posture=""');
    }
  });
});

// -------------------------------------------------------------- the rows

describe("the rows", () => {
  it("a run waits because its row says awaiting_approval, and is active because its row says a known working state", () => {
    expect(rowIsAwaiting({ state: "awaiting_approval" })).toBe(true);
    expect(rowIsAwaiting({ state: "AWAITING_APPROVAL" })).toBe(false);
    expect(rowIsAwaiting({ state: "approved" })).toBe(false);
    for (const state of GENESIS_RUN_STATES) {
      // A cancelled run is settled: the owner gave it up, so "Vazgeç" has nothing left to
      // ask. Until 2026-09-08 this build could not read the word at all and drew such a
      // run as one still being built.
      const active = !["available", "used", "verified", "failed", "cancelled"].includes(state);
      expect(rowIsActive({ state }), state).toBe(active);
    }
    expect(rowIsActive({ state: null })).toBe(false);
    expect(rowIsActive({ state: "approved" })).toBe(false);
  });

  it("draws 'Onayla' only at awaiting_approval, 'Vazgeç' while active, neither once settled, failed, unread or unreported", () => {
    expect(genesisRowActions({ state: "awaiting_approval" })).toEqual(["approve", "cancel"]);
    for (const state of ["capability_missing", "researching", "designing", "building", "testing", "classifying", "rolling_out", "registering"]) {
      expect(genesisRowActions({ state }), state).toEqual(["cancel"]);
    }
    for (const state of ["available", "used", "verified", "failed", "cancelled", "approved", null]) {
      expect(genesisRowActions({ state }), String(state)).toEqual([]);
    }
    expect(GENESIS_ACTION_LABEL).toEqual({ approve: "Onayla", cancel: "Vazgeç" });
  });

  it("the gate opens each chip only for a row the Cloud Core would not refuse, while nothing is in flight", () => {
    const busy = { action: "approve" as const, id: "g9" };
    for (const state of [...GENESIS_RUN_STATES, null, "approved"]) {
      for (const action of ["approve", "cancel"] as const) {
        expect(genesisActionGate({ state }, action, busy), `${state}/${action}`).toEqual({ enabled: false, reason: GENESIS_REASON_BUSY, reasonKind: "busy" });
      }
    }
    const open = { enabled: true, reason: null, reasonKind: null };
    expect(genesisActionGate({ state: "awaiting_approval" }, "approve", null)).toEqual(open);
    expect(genesisActionGate({ state: "awaiting_approval" }, "cancel", null)).toEqual(open);
    expect(genesisActionGate({ state: "testing" }, "cancel", null)).toEqual(open);
    for (const state of ["testing", "verified", "failed", null, "approved"]) {
      expect(genesisActionGate({ state }, "approve", null), `${state}/approve`).toEqual({
        enabled: false,
        reason: GENESIS_REASON_NOT_AWAITING,
        reasonKind: "not_awaiting",
      });
    }
    for (const state of ["available", "used", "verified", "failed", null, "approved"]) {
      expect(genesisActionGate({ state }, "cancel", null), `${state}/cancel`).toEqual({
        enabled: false,
        reason: GENESIS_REASON_NOT_ACTIVE,
        reasonKind: "not_active",
      });
    }
  });

  it("lines each row with its state, the error class beside failed, the flag and the classes when sent", () => {
    expect(genesisRowLine(row({ state: "building" }))).toBe("bağdaştırıcı yazılıyor");
    expect(genesisRowLine(AWAITING())).toBe("onay bekliyor · onay gerekli · yetki: yetkisiz varlıkta değişiklik · etki: dış değişiklik");
    expect(genesisRowLine(VERIFIED())).toBe("doğrulandı · yetki: salt okunur · etki: okuma");
    expect(genesisRowLine(FAILED())).toBe("başarısız — dependency_unavailable");
    expect(genesisRowLine(row({ state: "failed" }))).toBe("başarısız · hata sınıfı bildirilmedi");
    // An error class beside a state that is not failed is not a failure.
    expect(genesisRowLine(row({ state: "verified", error_class: "stale" }))).toBe("doğrulandı");
    expect(genesisRowLine(row({ state: null }))).toBe("durum bildirilmedi");
    expect(genesisRowLine(row({ state: "approved" }))).toBe("approved");
    expect(genesisRowLine(row({ state: "registering", authority_class: "mutating_authorized_asset", side_effect_class: "none" }))).toBe(
      "kaydediliyor · yetki: yetkili varlıkta değişiklik · etki: yan etkisiz",
    );
    expect(GENESIS_AUTHORITY_LABEL).toEqual({
      read_only: "salt okunur",
      mutating_authorized_asset: "yetkili varlıkta değişiklik",
      mutating_unauthorized: "yetkisiz varlıkta değişiklik",
    });
    expect(GENESIS_SIDE_EFFECT_LABEL).toEqual({ none: "yan etkisiz", read: "okuma", mutate_external: "dış değişiklik" });
  });
});

// ------------------------------------------------------------ the runner

describe("the action runner", () => {
  it("makes exactly one call with the run's id, writes busy then the receipt's outcome, and reloads the list", async () => {
    const client = fakeClient();
    const { ports, writes, current, onSettled } = portsOf(client);
    expect(await runGenesisAction(ports, "approve", "g1")).toBe(true);
    expect(client.approve).toHaveBeenCalledTimes(1);
    expect(client.approve).toHaveBeenCalledWith("g1");
    expect(client.cancel).not.toHaveBeenCalled();
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual({ busy: { action: "approve", id: "g1" }, outcome: null });
    expect(current().busy).toBeNull();
    expect(current().outcome).toEqual({
      action: "approve",
      id: "g1",
      ok: true,
      text: "Yayına alınıyor · Yetkilendirme kaydedildi.",
      at: T0,
    });
    expect(onSettled).toHaveBeenCalledTimes(1);

    const second = portsOf(fakeClient());
    await runGenesisAction(second.ports, "cancel", "g2");
    expect(second.ports.client.cancel).toHaveBeenCalledTimes(1);
    expect(second.ports.client.cancel).toHaveBeenCalledWith("g2");
    expect(second.ports.client.approve).not.toHaveBeenCalled();
    expect(second.current().outcome?.text).toBe("Başarısız — cancelled");
  });

  it("refuses a second press while the first is in flight: the client is still called once", async () => {
    const deferred: { release: ((receipt: GenesisActionReceipt) => void) | null } = { release: null };
    const approve = vi.fn(async () => RECEIPT_ROLLING_OUT);
    approve.mockImplementationOnce(
      () =>
        new Promise<GenesisActionReceipt>((resolve) => {
          deferred.release = resolve;
        }),
    );
    const client = fakeClient({ approve });
    const { ports, current } = portsOf(client);

    const first = runGenesisAction(ports, "approve", "g1");
    expect(current().busy).toEqual({ action: "approve", id: "g1" });
    expect(await runGenesisAction(ports, "approve", "g1")).toBe(false);
    expect(await runGenesisAction(ports, "cancel", "g1")).toBe(false);
    expect(await runGenesisAction(ports, "cancel", "g2")).toBe(false);
    expect(approve).toHaveBeenCalledTimes(1);
    expect(client.cancel).not.toHaveBeenCalled();

    expect(deferred.release).not.toBeNull();
    deferred.release?.(RECEIPT_ROLLING_OUT);
    expect(await first).toBe(true);
    expect(current().busy).toBeNull();
    // Once settled, the next press goes through — a second approval is the Cloud Core's to refuse, not this page's to hide.
    expect(await runGenesisAction(ports, "approve", "g1")).toBe(true);
    expect(approve).toHaveBeenCalledTimes(2);
  });

  it("turns the Cloud Core's refusal — and a route not there yet — into the owner's words, says nothing happened, and still reloads", async () => {
    const client = fakeClient({
      approve: vi.fn(async () => {
        throw new GenesisActionError(409, "not_awaiting_approval", "run is in state rolling_out");
      }),
    });
    const { ports, current, onSettled } = portsOf(client);
    expect(await runGenesisAction(ports, "approve", "g1")).toBe(true);
    const outcome = current().outcome;
    expect(outcome?.ok).toBe(false);
    expect(outcome?.text).toBe("Çalışma onay beklemiyor; onaylanmadı.");
    expect(outcome?.text).not.toContain("alınıyor");
    expect(current().busy).toBeNull();
    expect(onSettled).toHaveBeenCalledTimes(1);

    const absent = fakeClient({
      cancel: vi.fn(async () => {
        throw new GenesisActionError(404, GENESIS_ROUTE_ABSENT, "Bu Cloud Core sürümünde /v1/genesis/runs/g1/cancel yok (HTTP 404).");
      }),
    });
    const second = portsOf(absent);
    await runGenesisAction(second.ports, "cancel", "g1");
    expect(second.current().outcome?.ok).toBe(false);
    expect(second.current().outcome?.text).toBe("Bu Cloud Core sürümünde /v1/genesis/runs/g1/cancel yok (HTTP 404). Yapılmadı.");
  });

  it("never says 'onaylandı', 'kullanılabilir' or 'doğrulandı' on the strength of a 2xx alone", () => {
    const none: GenesisActionReceipt = { state: null, errorClass: null, summary: null, receiptId: "r1" };
    expect(genesisOutcomeText("approve", none)).toBe(GENESIS_OUTCOME_NO_STATE_TR.approve);
    expect(genesisOutcomeText("approve", none)).not.toMatch(/onaylandı|kullanılabilir|doğrulandı/);
    expect(genesisOutcomeText("cancel", none)).toBe(GENESIS_OUTCOME_NO_STATE_TR.cancel);
    expect(genesisOutcomeText("approve", { ...none, state: "rolling_out" })).toBe("Yayına alınıyor");
    expect(genesisOutcomeText("approve", { ...none, state: "verified" })).toBe("Doğrulandı");
    expect(genesisOutcomeText("cancel", { ...none, state: "failed", errorClass: "cancelled" })).toBe("Başarısız — cancelled");
    expect(genesisOutcomeText("cancel", { ...none, state: "failed" })).toBe("Başarısız");
    // A state this build cannot read is printed as the token, never as one of the thirteen.
    expect(genesisOutcomeText("approve", { ...none, state: "approved", summary: "kaydedildi" })).toBe("durum: approved · kaydedildi");
    // A summary with no state rides beside the no-state sentence.
    expect(genesisOutcomeText("approve", { ...none, summary: "İletildi." })).toBe(`${GENESIS_OUTCOME_NO_STATE_TR.approve} · İletildi.`);
  });
});

// ------------------------------------------------------------- the readout

describe("the Core's readout for Capability Genesis", () => {
  it("headlines the building posture with the caption and the facts beneath, and no bar", () => {
    const html = readout([CAPABILITY_GENESIS("counterbox.increment", "building")]);
    expect(html).toContain('data-core-kind="capability_genesis"');
    expect(html).toContain('data-core-state="capability.genesis"');
    expect(html).toContain('data-core-subsystem="genesis"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain('data-genesis-posture="building"');
    expect(html).toContain("Yeni yetenek");
    expect(html).toContain('data-label="true">counterbox.increment için bağdaştırıcı yazılıyor</p>');
    expect(html).not.toContain("data-caption");
    expect(html).toContain("data-genesis-facts");
    expect(html).toContain('data-genesis-capability="counterbox.increment"');
    expect(html).toContain('data-genesis-state="building"');
    expect(html).toContain('data-genesis-approval-required=""');
    expect(html).toContain('data-genesis-error-class=""');
    expect(html).toContain("yetenek: counterbox.increment · durum: bağdaştırıcı yazılıyor");
    expect(html).toContain("Doğrulanmamış bir yetenek yapıldı sayılmaz.");
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-app-facts");
    expect(html).not.toContain("data-artifact-facts");
    expect(html).not.toContain("data-document-facts");
    expect(html).not.toContain("data-mail-facts");
    // No control on the Core: "Onayla" is the Cockpit's, from the row.
    expect(html).not.toContain("<button");
  });

  it("marks the waiting posture at awaiting_approval, with the flag, and never draws progress for it", () => {
    const html = readout([CAPABILITY_GENESIS("counterbox.increment", "awaiting_approval", null, true)]);
    expect(html).toContain('data-genesis-posture="waiting"');
    expect(html).toContain('data-label="true">counterbox.increment onay bekliyor</p>');
    expect(html).toContain('data-genesis-state="awaiting_approval"');
    expect(html).toContain('data-genesis-approval-required="yes"');
    expect(html).toContain("yetenek: counterbox.increment · durum: onay bekliyor · onay gerekli");
    expect(html).not.toContain("core-progress-fill");
    expect(html).not.toContain("<button");
  });

  it("marks the settled posture from available on, and the failure with its class", () => {
    for (const state of ["available", "used", "verified"]) {
      const html = readout([CAPABILITY_GENESIS("counterbox.increment", state)]);
      expect(html, state).toContain('data-genesis-posture="settled"');
      expect(html, state).toContain(`data-genesis-state="${state}"`);
    }
    expect(readout([CAPABILITY_GENESIS("counterbox.increment", "verified")])).toContain('data-label="true">counterbox.increment doğrulandı</p>');
    expect(readout([CAPABILITY_GENESIS("counterbox.increment", "available")])).not.toContain("doğrulandı");

    const failed = readout([CAPABILITY_GENESIS("counterbox.increment", "failed", "dependency_unavailable")]);
    expect(failed).toContain('data-genesis-posture="failed"');
    expect(failed).toContain('data-label="true">counterbox.increment başarısız — dependency_unavailable</p>');
    expect(failed).toContain('data-genesis-error-class="dependency_unavailable"');
    expect(failed).toContain("durum: başarısız · hata: dependency_unavailable");
    expect(failed).not.toContain("doğrulandı");
  });

  it("says what was not reported when the publisher named nothing", () => {
    const html = readout([CAPABILITY_GENESIS_BARE()]);
    expect(html).toContain('data-label="true">Yeni yetenek</p>');
    expect(html).toContain("yetenek bildirilmedi · durum bildirilmedi");
    expect(html).toContain('data-genesis-capability=""');
    expect(html).toContain('data-genesis-state=""');
    expect(html).toContain('data-genesis-posture="building"');
  });

  it("keeps the caption and the posture in the compact form and drops the long line", () => {
    const html = readout([CAPABILITY_GENESIS("counterbox.increment", "awaiting_approval", null, true)], true);
    expect(html).toContain("counterbox.increment onay bekliyor");
    expect(html).toContain('data-genesis-posture="waiting"');
    expect(html).not.toContain("data-genesis-facts");
  });

  it("names the aged-out run as last-known rather than as working, with its facts", () => {
    const html = readout([CAPABILITY_GENESIS("counterbox.increment", "awaiting_approval", null, true)], false, T0 + GENESIS_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="capability.genesis"');
    expect(html).toContain("Yeni yetenek");
    expect(html).toContain("data-genesis-facts");
    expect(html).toContain("yetenek: counterbox.increment");
    // The posture follows the facts, not the life of the claim: what it WAS is still a fact.
    expect(html).toContain('data-genesis-posture="waiting"');
  });

  it("prints no genesis line or posture for any other kind", () => {
    for (const events of [[AGENT_IDLE()], [DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [ARTIFACT_FACTORY()], [APP_FACTORY()]]) {
      const html = readout(events);
      expect(html).not.toContain("data-genesis-facts");
      expect(html).not.toContain("data-genesis-posture");
    }
  });
});
