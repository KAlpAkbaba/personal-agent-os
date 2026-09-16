/**
 * The Görevler panel, its three chips and the Core's readout for
 * `executive.run` (M26 spec §6): sentences about the rows the list route
 * holds and the tokens the Cloud Core published, "Duraklat" only while a
 * run is going, "Devam" ONLY while one is paused, "İptal" only while one
 * has not ended, three chips that ask the Cloud Core exactly once each, and
 * never a run this page started, paused or finished.
 *
 * The Cloud Core half of M26 is built on a parallel track (ADR-0089 §8), so
 * the routes do not exist in this worktree: every call here goes through a
 * client double, exactly as `genesis-panel.test.tsx` proves M24's two chips.
 * What is proven is this half — the gates, the sentences, the one-call
 * rule and the honest empty state — not that the Cloud Core answers.
 *
 * Rendered with `react-dom/server` like the rest of this suite. The click
 * is proven the way the genesis panel's is: the panel is hook-free, so the
 * element tree is walked to the control and its handler invoked — exactly
 * what React would do.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ExecutivePanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import {
  EXECUTIVE_ACTION_LABEL,
  EXECUTIVE_REASON_BUSY,
  EXECUTIVE_REASON_NOT_ACTIVE,
  EXECUTIVE_REASON_NOT_PAUSED,
  EXECUTIVE_REASON_NOT_RUNNING,
  EXECUTIVE_ROWS_SHOWN,
  executiveActionGate,
  executiveRowActions,
  executiveRowLine,
  missingStepPhrases,
  rowIsActive,
  rowIsComplete,
  rowIsPaused,
  rowIsPausable,
} from "../../app/lib/cockpit/executive-rows";
import {
  EXECUTIVE_ACTIONS,
  EXECUTIVE_CHIP_ACTIONS,
  EXECUTIVE_CONTROL_IDLE,
  EXECUTIVE_DETAILS_NONE,
  EXECUTIVE_ROUTE_ABSENT,
  EXECUTIVE_RUNS_PATH,
  type ExecutiveActionReceipt,
  type ExecutiveClient,
  type ExecutiveControlProps,
  type ExecutiveControlState,
  type ExecutiveDetailProps,
  type ExecutiveRunRow,
  ExecutiveActionError,
  executiveActionPath,
  executiveRunPath,
  parseExecutiveDetail,
  parseExecutiveRow,
  parseMissingSteps,
} from "../../app/lib/cockpit/executive";
import {
  EXECUTIVE_OUTCOME_NO_STATE_TR,
  executiveOutcomeText,
  runExecutiveAction,
} from "../../app/lib/cockpit/useExecutiveControl";
import { executiveDetailKey, executiveDetailNotice } from "../../app/lib/cockpit/useExecutiveDetail";
import { EXECUTIVE_RUN_STATES, EXECUTIVE_TTL_MS } from "../../app/lib/uistate/contract";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  ARTIFACT_FACTORY,
  CAPABILITY_GENESIS,
  DOCUMENT_ANALYSIS,
  EXECUTIVE_RUN,
  EXECUTIVE_RUN_BARE,
  MAIL_ACTIVITY,
  SCENE_ACTIVITY,
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

function row(overrides: Partial<ExecutiveRunRow> = {}): ExecutiveRunRow {
  return {
    run_id: "r1",
    goal: "Son üç gündeki AI gelişmelerini araştır",
    state: "running",
    step: "s3",
    done: 2,
    total: 5,
    missing: [],
    awaiting_step: null,
    planner: null,
    created_at: iso(-90_000),
    updated_at: iso(-30_000),
    ...overrides,
  };
}

const RUNNING = () => row();
const PLANNED = () => row({ run_id: "r2", state: "planned", step: null, done: 0, total: 5 });
const PAUSED = () => row({ run_id: "r3", state: "paused", step: "s2", done: 1, total: 5 });
const COMPLETED = () => row({ run_id: "r4", state: "completed", step: null, done: 5, total: 5 });
const PARTIAL = () =>
  row({
    run_id: "r5",
    goal: "Bu klasördeki teklifleri karşılaştır",
    state: "partial",
    step: null,
    done: 3,
    total: 5,
    missing: [
      { step: "s4", reason: "belge bulunamadı" },
      { step: "s5", reason: null },
    ],
  });
const CANCELLED = () => row({ run_id: "r6", state: "cancelled", step: null, done: 2, total: 5 });
const FAILED = () => row({ run_id: "r7", state: "failed", step: "s4", done: 3, total: 5 });

const ok = (rows: ExecutiveRunRow[]) => ({ kind: "ok" as const, value: rows, at: T0 });
const noop = () => {};

function controlOf(overrides: Partial<ExecutiveControlProps> = {}): ExecutiveControlProps {
  return { ...EXECUTIVE_CONTROL_IDLE, onPause: noop, onResume: noop, onCancel: noop, onApprove: noop, ...overrides };
}

function detailsOf(explains: Record<string, string> = {}, notice: string | null = null): ExecutiveDetailProps {
  return {
    detailFor: (runId) => (explains[runId] ? { run_id: runId, explain: explains[runId], missing: [] } : null),
    notice,
  };
}

function panel(
  runs: Parameters<typeof ExecutivePanel>[0]["runs"],
  events: ReturnType<typeof event>[] = [AGENT_IDLE()],
  control: ExecutiveControlProps = controlOf(),
  now = T0,
  details: ExecutiveDetailProps = EXECUTIVE_DETAILS_NONE,
) {
  return renderToStaticMarkup(
    <ExecutivePanel runs={runs} truth={truthOf(events)} now={now} control={control} details={details} />,
  );
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

const RECEIPT_PAUSED: ExecutiveActionReceipt = { state: "paused", summary: "Çalışan adım bitirildi.", receiptId: "r1" };
const RECEIPT_CANCELLED: ExecutiveActionReceipt = { state: "cancelled", summary: null, receiptId: "r2" };

function fakeClient(overrides: Partial<ExecutiveClient> = {}): ExecutiveClient {
  return {
    pause: vi.fn(async () => RECEIPT_PAUSED),
    resume: vi.fn(async () => ({ ...RECEIPT_PAUSED, state: "running", summary: null })),
    cancel: vi.fn(async () => RECEIPT_CANCELLED),
    approve: vi.fn(async () => ({ ...RECEIPT_PAUSED, state: "running", summary: null })),
    ...overrides,
  };
}

/** Plain ports over a local state cell, recording every write. */
function portsOf(client: ExecutiveClient, onSettled = vi.fn()) {
  let state: ExecutiveControlState = EXECUTIVE_CONTROL_IDLE;
  const writes: ExecutiveControlState[] = [];
  return {
    ports: {
      client,
      read: () => state,
      write: (next: ExecutiveControlState) => {
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

describe("the Görevler panel", () => {
  it("draws nothing at all when the list is empty and the bus said nothing (B24 req 714)", () => {
    expect(panel(ok([]))).toBe("");
    expect(panel({ kind: "absent", detail: `Bu Cloud Core sürümünde ${EXECUTIVE_RUNS_PATH} yok (HTTP 404).` })).toBe("");
  });

  it("says the bus told it nothing, when there are rows to show anyway", () => {
    const rows = panel(ok([row()]));
    expect(rows).toContain('data-executive-activity="untold"');
    expect(rows).toContain("Çok adımlı iş etkinliği bildirilmedi.");
  });

  it("keeps every word when the bus is telling us something the list cannot show", () => {
    const html = panel(ok([]), [EXECUTIVE_RUN("r1", "running", "s3", 2, 5)]);
    expect(html).toContain('data-panel="executive"');
    expect(html).toContain('data-panel-state="ok"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Devam eden bir iş yok.");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).toContain('data-executive-active="0"');
    expect(html).toContain(">Görevler<");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("data-executive-outcome");
    expect(html).not.toContain("attention");
    // What the chips ask for — and what "done" means — is said in words on every render.
    expect(html).toContain("aynı sinyalleri gönderir");
    expect(html).toContain("kanıtıyla doğrulandığında tamamlandı sayılır");
    expect(html).toContain("nesi eksik olduğu yazılır");
    expect(html).toContain("Bu ekran iş planlamaz, adım çalıştırmaz, iş bitirmez.");
  });

  it("never renders the empty sentence for a route that is loading, failed or absent", () => {
    const loading = panel({ kind: "loading" });
    expect(loading).toContain("data-panel-loading");
    expect(loading).toContain("yükleniyor…");
    expect(loading).toContain('data-panel-empty=""');
    expect(loading).not.toContain("Devam eden bir iş yok.");
    expect(loading).not.toContain("data-panel-badge");

    const failed = panel({ kind: "failed", error: "HTTP 503" });
    expect(failed).toContain("Alınamadı: HTTP 503");
    expect(failed).not.toContain("Devam eden bir iş yok.");

    // A route this Cloud Core does not serve still says so WHILE the bus is talking:
    // "the list is not here" and "nothing is happening" are different facts.
    const absent = panel({ kind: "absent", detail: `Bu Cloud Core sürümünde ${EXECUTIVE_RUNS_PATH} yok (HTTP 404).` }, [EXECUTIVE_RUN("r1", "running", "s3", 2, 5)]);
    expect(absent).toContain("data-panel-absent");
    expect(absent).toContain("Henüz yok. Bu Cloud Core sürümünde /v1/executive/runs yok (HTTP 404).");
    expect(absent).not.toContain("Devam eden bir iş yok.");
    for (const html of [loading, failed, absent]) expect(html).not.toContain("<button");
  });

  it("lists a running run with 'Duraklat' and 'İptal', and no 'Devam'", () => {
    const html = panel(ok([RUNNING()]));
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-panel-badge="true">1 sürüyor / 1<');
    expect(html).toContain('data-executive-active="1"');
    expect(html).toContain('data-executive-run="r1"');
    expect(html).toContain('data-executive-run-state="running"');
    expect(html).toContain('data-executive-run-step="s3"');
    expect(html).toContain('data-executive-run-active="yes"');
    expect(html).toContain('data-executive-run-paused="no"');
    expect(html).toContain('data-executive-run-complete="no"');
    expect(html).toContain('data-executive-run-done="2"');
    expect(html).toContain('data-executive-run-total="5"');
    expect(html).toContain("Son üç gündeki AI gelişmelerini araştır");
    expect(html).toContain("30 sn önce");
    expect(html).toContain('data-executive-line="true">adım s3 · çalışıyor · 2/5 adım</span>');
    expect(html).toContain('data-executive-controls="r1"');
    expect(html).toContain('data-executive-in-flight="no"');
    expect(html).toContain('data-executive-action="pause" data-executive-target="r1" data-executive-enabled="yes">Duraklat</button>');
    expect(html).toContain('data-executive-action="cancel" data-executive-target="r1" data-executive-enabled="yes">İptal</button>');
    expect(html).not.toContain('data-executive-action="resume"');
    expect((html.match(/<button/g) ?? []).length).toBe(2);
    expect(html).not.toContain("data-executive-reason");
    expect(html).not.toContain("attention");
    // A running run is not a finished one, however many of its steps are
    // done. The panel's note explains what "tamamlandı" MEANS on every
    // render, so the assertion is about what the row says, not about the
    // word appearing on the page.
    expect(html).not.toContain(">tamamlandı");
  });

  it("lists a paused run with 'Devam' and 'İptal', and no 'Duraklat'", () => {
    const html = panel(ok([PAUSED()]));
    expect(html).toContain('data-executive-run="r3"');
    expect(html).toContain('data-executive-run-state="paused"');
    expect(html).toContain('data-executive-run-paused="yes"');
    expect(html).toContain('data-executive-run-active="yes"');
    expect(html).toContain('data-executive-line="true">adım s2 · duraklatıldı · 1/5 adım</span>');
    expect(html).toContain('data-executive-action="resume" data-executive-target="r3" data-executive-enabled="yes">Devam</button>');
    expect(html).toContain('data-executive-action="cancel" data-executive-target="r3" data-executive-enabled="yes">İptal</button>');
    expect(html).not.toContain('data-executive-action="pause"');
    expect(html).not.toContain(">Duraklat<");
    expect((html.match(/<button/g) ?? []).length).toBe(2);
    // A paused run is where the owner put it: no attention, no error wording.
    expect(html).not.toContain("attention");
    expect(html).not.toContain("başarısız");
    // A planned run is going too: "Duraklat" and "İptal", never "Devam".
    const planned = panel(ok([PLANNED()]));
    expect(planned).toContain('data-executive-action="pause"');
    expect(planned).toContain('data-executive-action="cancel"');
    expect(planned).not.toContain('data-executive-action="resume"');
    expect(planned).toContain('data-executive-line="true">planlandı · 0/5 adım</span>');
  });

  it("lists a completed run with no chip at all, and says 'tamamlandı' for it alone", () => {
    const html = panel(ok([COMPLETED()]));
    expect(html).toContain('data-executive-run="r4"');
    expect(html).toContain('data-executive-run-state="completed"');
    expect(html).toContain('data-executive-run-complete="yes"');
    expect(html).toContain('data-executive-run-active="no"');
    expect(html).toContain('data-executive-line="true">tamamlandı · 5/5 adım</span>');
    expect(html).not.toContain("<button");
    expect(html).not.toContain("data-executive-controls");
    expect(html).not.toContain("attention");
    // Every other ended state gets no chip either, and none of them reads as done.
    for (const state of ["partial", "cancelled", "failed"]) {
      const settled = panel(ok([row({ state, missing: [] })]));
      expect(settled, state).not.toContain("<button");
      expect(settled, state).not.toContain(">tamamlandı");
      expect(settled, state).not.toContain("tamamlandı ·");
    }
  });

  it("lists a partial run naming what is missing, draws attention to it, and never rounds it up", () => {
    const html = panel(ok([PARTIAL()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-executive-run="r5"');
    expect(html).toContain('data-executive-run-state="partial"');
    expect(html).toContain('data-executive-run-partial="yes"');
    expect(html).toContain('data-executive-run-active="no"');
    expect(html).toContain("Bu klasördeki teklifleri karşılaştır");
    // The missing steps, with the reason the ROUTE gave and none where it gave none.
    expect(html).toContain(
      'data-executive-line="true">kısmen bitti — eksik: s4 (belge bulunamadı), s5 · 3/5 adım</span>',
    );
    expect(html).not.toContain(">tamamlandı");
    expect(html).not.toContain("<button");
    // A partial run whose missing steps nobody named says so rather than nothing.
    const untold = panel(ok([row({ state: "partial", step: null, done: null, total: null })]));
    expect(untold).toContain('data-executive-line="true">kısmen bitti · eksik adımlar bildirilmedi · adım sayısı bildirilmedi</span>');
  });

  it("lists a failed run, draws attention to it, and offers no chip", () => {
    const html = panel(ok([FAILED()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-executive-run="r7"');
    expect(html).toContain('data-executive-run-failed="yes"');
    expect(html).toContain('data-executive-run-active="no"');
    expect(html).toContain('data-executive-line="true">başarısız · 3/5 adım</span>');
    expect(html).not.toContain("<button");
    // A cancelled run is settled and is not worded as a failure.
    const cancelled = panel(ok([CANCELLED()]));
    expect(cancelled).toContain('data-executive-line="true">iptal edildi · 2/5 adım</span>');
    expect(cancelled).not.toContain("başarısız");
    expect(cancelled).not.toContain("attention");
    expect(cancelled).not.toContain("<button");
  });

  it("says what a row did not report rather than filling it in, and gives a word it cannot read no chip", () => {
    const html = panel(ok([row({ goal: null, state: null, step: null, done: null, total: null, updated_at: null, created_at: null })]));
    expect(html).toContain(">iş bildirilmedi<");
    expect(html).toContain('data-executive-run-state=""');
    expect(html).toContain('data-executive-run-step=""');
    expect(html).toContain('data-executive-run-done=""');
    expect(html).toContain('data-executive-line="true">durum bildirilmedi</span>');
    // No state is not a run known to be going: nothing invites a click on a guess.
    expect(html).not.toContain("<button");
    // A newer server's word is printed verbatim — still a published fact — and earns no chip either.
    const unknown = panel(ok([row({ state: "completed_with_errors" })]));
    expect(unknown).toContain('data-executive-line="true">completed_with_errors · 2/5 adım</span>');
    expect(unknown).toContain('data-executive-run-active="no"');
    expect(unknown).toContain('data-executive-run-complete="no"');
    expect(unknown).not.toContain(">tamamlandı");
    expect(unknown).not.toContain("<button");
    // A run whose counts nobody sent draws no fraction.
    const uncounted = panel(ok([row({ done: 2, total: null })]));
    expect(uncounted).toContain('data-executive-line="true">adım s3 · çalışıyor</span>');
  });

  it("prints the current step's sentence exactly as the route sent it, and nothing when neither route sent one", () => {
    const withDetail = panel(ok([RUNNING()]), [AGENT_IDLE()], controlOf(), T0, detailsOf({ r1: "Üçüncü adım: kaynaklar getiriliyor, tarayıcı yanıtı bekleniyor." }));
    expect(withDetail).toContain('data-executive-explain="true">Üçüncü adım: kaynaklar getiriliyor, tarayıcı yanıtı bekleniyor.</span>');
    // The run's own route is the only source: the list carries no sentence, so a run the
    // detail hook has not answered for yet writes nothing rather than inventing a line.
    expect(panel(ok([RUNNING()]))).not.toContain("data-executive-explain");
    // A detail that could not be fetched is said, and the rows stay.
    const noticed = panel(ok([RUNNING()]), [AGENT_IDLE()], controlOf(), T0, detailsOf({}, "Ayrıntı alınamadı (r1): HTTP 503"));
    expect(noticed).toContain('data-executive-detail-notice="true">Ayrıntı alınamadı (r1): HTTP 503</p>');
    expect(noticed).toContain('data-executive-run="r1"');
  });

  it("shows the last runs as the route orders them, bounded, and counts the going ones in the badge", () => {
    const rows = Array.from({ length: EXECUTIVE_ROWS_SHOWN + 3 }, (_, i) =>
      row({ run_id: `r${i}`, goal: `iş ${i}`, state: i < 2 ? "running" : "completed", step: null }),
    );
    const html = panel(ok(rows));
    expect(html).toContain(`data-panel-badge="true">2 sürüyor / ${EXECUTIVE_ROWS_SHOWN + 3}<`);
    expect(html).toContain('data-executive-active="2"');
    expect((html.match(/data-executive-run="r\d+"/g) ?? []).length).toBe(EXECUTIVE_ROWS_SHOWN);
    expect(html).toContain('data-executive-run="r0"');
    expect(html).not.toContain(`data-executive-run="r${EXECUTIVE_ROWS_SHOWN}"`);
    expect((html.match(/data-executive-action="pause"/g) ?? []).length).toBe(2);
  });

  it("each chip asks for that run exactly once, and only through its own handler", () => {
    const onPause = vi.fn();
    const onResume = vi.fn();
    const onCancel = vi.fn();
    const rows = [RUNNING(), PAUSED(), COMPLETED(), FAILED()];
    const tree = (
      <ExecutivePanel
        runs={ok(rows)}
        truth={truthOf([AGENT_IDLE()])}
        now={T0}
        control={controlOf({ onPause, onResume, onCancel })}
        details={EXECUTIVE_DETAILS_NONE}
      />
    );

    click(findByData(tree, { "data-executive-action": "pause", "data-executive-target": "r1" }));
    expect(onPause).toHaveBeenCalledTimes(1);
    expect(onPause).toHaveBeenCalledWith("r1");
    expect(onResume).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();

    click(findByData(tree, { "data-executive-action": "resume", "data-executive-target": "r3" }));
    expect(onResume).toHaveBeenCalledTimes(1);
    expect(onResume).toHaveBeenCalledWith("r3");

    click(findByData(tree, { "data-executive-action": "cancel", "data-executive-target": "r3" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledWith("r3");
    expect(onPause).toHaveBeenCalledTimes(1);

    // A running run has no "Devam" to find; a paused one no "Duraklat"; an
    // ended one none of the three.
    expect(findByData(tree, { "data-executive-action": "resume", "data-executive-target": "r1" })).toBeNull();
    expect(findByData(tree, { "data-executive-action": "pause", "data-executive-target": "r3" })).toBeNull();
    for (const id of ["r4", "r7"]) {
      for (const action of ["pause", "resume", "cancel"]) {
        expect(findByData(tree, { "data-executive-action": action, "data-executive-target": id }), `${id}/${action}`).toBeNull();
      }
    }
  });

  it("disables every chip while one call is in flight, marking the run and the action it is, with the reason said once", () => {
    const html = panel(ok([RUNNING(), PAUSED()]), [AGENT_IDLE()], controlOf({ busy: { action: "pause", id: "r1" } }));
    expect(html).toContain('data-executive-controls="r1" data-executive-in-flight="yes" data-executive-in-flight-action="pause"');
    expect(html).toContain('data-executive-controls="r3" data-executive-in-flight="no" data-executive-in-flight-action=""');
    expect(html).toContain('data-executive-action="pause" data-executive-target="r1" data-executive-enabled="no" disabled=""');
    expect(html).toContain('data-executive-action="cancel" data-executive-target="r1" data-executive-enabled="no" disabled=""');
    expect(html).toContain('data-executive-action="resume" data-executive-target="r3" data-executive-enabled="no" disabled=""');
    expect(html).toContain('data-executive-reason="busy" data-executive-reason-for="pause,cancel"');
    expect(html).toContain('data-executive-reason="busy" data-executive-reason-for="resume,cancel"');
    expect(html).toContain(EXECUTIVE_REASON_BUSY);
    // Said once per run, not once per chip.
    expect((html.match(/data-executive-reason="busy"/g) ?? []).length).toBe(2);
  });

  it("prints the last call's answer, dated, with the run and the action it was about", () => {
    const outcome = { action: "pause" as const, id: "r1", ok: true, text: "Duraklatıldı · Çalışan adım bitirildi.", at: T0 - 5_000 };
    const html = panel(ok([RUNNING()]), [AGENT_IDLE()], controlOf({ outcome }));
    expect(html).toContain('data-executive-outcome="pause"');
    expect(html).toContain('data-executive-ok="yes"');
    expect(html).toContain('data-executive-target="r1"');
    expect(html).toContain("Duraklatıldı · Çalışan adım bitirildi. · 5 sn önce");

    const refused = panel(ok([RUNNING()]), [AGENT_IDLE()], controlOf({ outcome: { ...outcome, ok: false, text: "İş duraklatılmış değil; sürdürülecek bir şey yok." } }));
    expect(refused).toContain('data-executive-ok="no"');
    expect(refused).toContain("panel-unknown");
    expect(refused).toContain("İş duraklatılmış değil; sürdürülecek bir şey yok.");
  });

  it("states the bus activity in the spec's words with its age and posture, and last-known once it aged out", () => {
    const live = panel(ok([]), [EXECUTIVE_RUN("r1", "running", "s3", 2, 5)], controlOf(), T0 + 3_000);
    expect(live).toContain('data-executive-stage="active"');
    expect(live).toContain('data-executive-activity="active"');
    expect(live).toContain('data-executive-posture="running"');
    expect(live).toContain('data-executive-caption="adım s3 · çalışıyor · 2/5 adım"');
    expect(live).toContain("adım s3 · çalışıyor · 2/5 adım · 3 sn önce");
    expect(live).not.toContain("Son bilinen");
    // The list is the list: the bus running something does not put a row on it, nor a chip.
    expect(live).toContain("Devam eden bir iş yok.");
    expect(live).not.toContain("<button");

    const paused = panel(ok([]), [EXECUTIVE_RUN("r1", "paused", "s3", 2, 5)], controlOf(), T0 + 3_000);
    expect(paused).toContain('data-executive-posture="paused"');
    expect(paused).toContain("adım s3 · duraklatıldı · 2/5 adım · 3 sn önce");
    // The bus says a run is paused; the ROW is what earns "Devam", and there is none.
    expect(paused).not.toContain("<button");

    const partial = panel(ok([]), [EXECUTIVE_RUN("r1", "partial", null, 3, 5)], controlOf(), T0 + 3_000);
    expect(partial).toContain('data-executive-posture="partial"');
    expect(partial).toContain("kısmen bitti · 3/5 adım · 3 sn önce");
    expect(partial).not.toContain(">tamamlandı");

    const stale = panel(ok([]), [EXECUTIVE_RUN("r1", "running", "s3", 2, 5)], controlOf(), T0 + EXECUTIVE_TTL_MS + 1_000);
    expect(stale).toContain('data-executive-stage="none"');
    expect(stale).toContain('data-executive-last-known="active"');
    expect(stale).toContain('data-executive-posture="running"');
    expect(stale).toContain("Son bilinen: adım s3 · çalışıyor · 2/5 adım · 16 dk önce");

    // A document, mail, artifact, app, genesis or scene event is not an executive event.
    for (const other of [[DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [ARTIFACT_FACTORY()], [APP_FACTORY()], [CAPABILITY_GENESIS()], [SCENE_ACTIVITY()]]) {
      // A row so the panel renders at all: no rows AND no event of its own is a quiet
      // family now (B24 req 714).
      const html = panel(ok([row()]), other);
      expect(html).toContain('data-executive-activity="untold"');
      expect(html).toContain('data-executive-posture=""');
    }
  });
});

// -------------------------------------------------------------- the rows

describe("the rows", () => {
  it("a run is going because its row says a known unfinished state, and paused because its row says paused", () => {
    for (const state of EXECUTIVE_RUN_STATES) {
      const active = ["planned", "running", "paused"].includes(state);
      expect(rowIsActive({ state }), state).toBe(active);
      expect(rowIsPausable({ state }), state).toBe(state === "planned" || state === "running");
      expect(rowIsPaused({ state }), state).toBe(state === "paused");
      expect(rowIsComplete({ state }), state).toBe(state === "completed");
    }
    expect(rowIsActive({ state: null })).toBe(false);
    expect(rowIsPausable({ state: null })).toBe(false);
    expect(rowIsPaused({ state: null })).toBe(false);
    expect(rowIsComplete({ state: null })).toBe(false);
    // Membership, not a prefix or a case fold.
    expect(rowIsPaused({ state: "PAUSED" })).toBe(false);
    expect(rowIsComplete({ state: "completed_with_errors" })).toBe(false);
    expect(rowIsActive({ state: "pausing" })).toBe(false);
  });

  it("draws each chip exactly where its action means something, and none once the run has ended", () => {
    expect(executiveRowActions({ state: "planned" })).toEqual(["pause", "cancel"]);
    expect(executiveRowActions({ state: "running" })).toEqual(["pause", "cancel"]);
    expect(executiveRowActions({ state: "paused" })).toEqual(["resume", "cancel"]);
    for (const state of ["completed", "partial", "cancelled", "failed", "completed_with_errors", null]) {
      expect(executiveRowActions({ state }), String(state)).toEqual([]);
    }
    // Never "Duraklat" on a completed run, and never "Devam" on one that is not paused.
    for (const state of [...EXECUTIVE_RUN_STATES, null, "completed_with_errors"]) {
      const actions = executiveRowActions({ state });
      if (state !== "paused") expect(actions, String(state)).not.toContain("resume");
      if (state !== "planned" && state !== "running") expect(actions, String(state)).not.toContain("pause");
    }
    expect(EXECUTIVE_ACTION_LABEL).toEqual({ pause: "Duraklat", resume: "Devam", cancel: "İptal", approve: "Onayla" });
    expect(EXECUTIVE_CHIP_ACTIONS).toEqual(["pause", "resume", "cancel", "approve"]);
  });

  it("the gate opens each chip only for a row the Cloud Core would not refuse, while nothing is in flight", () => {
    const busy = { action: "pause" as const, id: "r9" };
    for (const state of [...EXECUTIVE_RUN_STATES, null, "completed_with_errors"]) {
      for (const action of EXECUTIVE_CHIP_ACTIONS) {
        expect(executiveActionGate({ state }, action, busy), `${state}/${action}`).toEqual({
          enabled: false,
          reason: EXECUTIVE_REASON_BUSY,
          reasonKind: "busy",
        });
      }
    }
    const open = { enabled: true, reason: null, reasonKind: null };
    expect(executiveActionGate({ state: "running" }, "pause", null)).toEqual(open);
    expect(executiveActionGate({ state: "planned" }, "pause", null)).toEqual(open);
    expect(executiveActionGate({ state: "paused" }, "resume", null)).toEqual(open);
    for (const state of ["planned", "running", "paused"]) {
      expect(executiveActionGate({ state }, "cancel", null), `${state}/cancel`).toEqual(open);
    }
    for (const state of ["paused", "completed", "partial", "cancelled", "failed", null, "completed_with_errors"]) {
      expect(executiveActionGate({ state }, "pause", null), `${state}/pause`).toEqual({
        enabled: false,
        reason: EXECUTIVE_REASON_NOT_RUNNING,
        reasonKind: "not_running",
      });
    }
    for (const state of ["planned", "running", "completed", "partial", "cancelled", "failed", null, "completed_with_errors"]) {
      expect(executiveActionGate({ state }, "resume", null), `${state}/resume`).toEqual({
        enabled: false,
        reason: EXECUTIVE_REASON_NOT_PAUSED,
        reasonKind: "not_paused",
      });
    }
    for (const state of ["completed", "partial", "cancelled", "failed", null, "completed_with_errors"]) {
      expect(executiveActionGate({ state }, "cancel", null), `${state}/cancel`).toEqual({
        enabled: false,
        reason: EXECUTIVE_REASON_NOT_ACTIVE,
        reasonKind: "not_active",
      });
    }
  });

  it("lines each row with its step, its state, what is missing and the counts, each as the row said it", () => {
    expect(executiveRowLine(RUNNING())).toBe("adım s3 · çalışıyor · 2/5 adım");
    expect(executiveRowLine(PAUSED())).toBe("adım s2 · duraklatıldı · 1/5 adım");
    expect(executiveRowLine(COMPLETED())).toBe("tamamlandı · 5/5 adım");
    expect(executiveRowLine(PARTIAL())).toBe("kısmen bitti — eksik: s4 (belge bulunamadı), s5 · 3/5 adım");
    expect(executiveRowLine(CANCELLED())).toBe("iptal edildi · 2/5 adım");
    expect(executiveRowLine(FAILED())).toBe("başarısız · 3/5 adım");
    expect(executiveRowLine(row({ state: null, step: null, done: null, total: null }))).toBe("durum bildirilmedi");
    expect(executiveRowLine(row({ state: "completed_with_errors", step: null, done: null, total: null }))).toBe("completed_with_errors");
    // A missing list beside a state that is not partial is not a partial run.
    expect(executiveRowLine(row({ state: "completed", step: null, missing: [{ step: "s4", reason: null }] }))).toBe("tamamlandı · 2/5 adım");
    // The step is not printed beside a run that has ended.
    expect(executiveRowLine(row({ state: "completed", step: "s4" }))).not.toContain("adım s4");
    expect(missingStepPhrases(PARTIAL().missing)).toEqual(["s4 (belge bulunamadı)", "s5"]);
    expect(missingStepPhrases([])).toEqual([]);
  });
});

// ------------------------------------------------------- the routes and rows

describe("the routes and the shapes they answer with", () => {
  it("spells every route the spec names, with the id as a path segment", () => {
    expect(EXECUTIVE_RUNS_PATH).toBe("/v1/executive/runs");
    expect(executiveRunPath("r1")).toBe("/v1/executive/runs/r1");
    expect(EXECUTIVE_ACTIONS).toEqual(["pause", "resume", "cancel", "retry", "amend", "approve"]);
    for (const action of EXECUTIVE_ACTIONS) {
      expect(executiveActionPath("r1", action)).toBe(`/v1/executive/runs/r1/${action}`);
    }
    // An id is a segment, never a query, and is escaped as one.
    expect(executiveActionPath("r 1/../x", "pause")).toBe("/v1/executive/runs/r%201%2F..%2Fx/pause");
  });

  it("reads a row's fields verbatim, and a row with no id is not a run", () => {
    const parsed = parseExecutiveRow({
      run_id: "r1",
      goal: "Bu mail zincirini analiz et",
      state: "running",
      step: "s2",
      done: 1,
      total: 4,
      explain: "İkinci adım: ilgili dosyalar aranıyor.",
      missing: [],
      created_at: "2026-09-08T10:00:00.000Z",
      updated_at: "2026-09-08T10:01:00.000Z",
    });
    expect(parsed?.goal).toBe("Bu mail zincirini analiz et");
    expect(parsed?.state).toBe("running");
    expect(parsed?.done).toBe(1);
    // `explain` is not a row field: it belongs to the run's own route, and the row type
    // no longer declares a field the list route never sends.
    expect("explain" in (parsed ?? {})).toBe(false);
    expect(parseExecutiveRow({ state: "running" })).toBeNull();
    expect(parseExecutiveRow(null)).toBeNull();
    // A count that is not a whole non-negative number is no count.
    expect(parseExecutiveRow({ run_id: "r1", done: 1.5, total: "4" })?.done).toBeNull();
    expect(parseExecutiveRow({ run_id: "r1", done: 1.5, total: "4" })?.total).toBeNull();
    // The route's OWN words and no others. This used to accept `id`/`current_step`/
    // `steps_done` as well, which is how the two halves of M26 appeared to agree while the
    // list route was still sending a different set: a fallback hides a rename instead of
    // failing on it. A row in the old spelling is not a run at all now, and the Python
    // guard (`test_executive_row_shape.py`) goes red before it could ever be served.
    expect(parseExecutiveRow({ id: "r2", current_step: "s7", steps_done: 3, steps_total: 6 })).toBeNull();
  });

  it("reads the missing steps from either shape, and drops an entry that names no step", () => {
    expect(parseMissingSteps(["s4", "s5"])).toEqual([
      { step: "s4", reason: null },
      { step: "s5", reason: null },
    ]);
    expect(parseMissingSteps([{ step: "s4", reason: "not_found" }, { step_id: "s5", error_class: "timeout" }])).toEqual([
      { step: "s4", reason: "not_found" },
      { step: "s5", reason: "timeout" },
    ]);
    expect(parseMissingSteps([{ reason: "not_found" }, null, 3])).toEqual([]);
    expect(parseMissingSteps(null)).toEqual([]);
    // Bounded by the graph's own bound: no honest list is longer than 24 steps.
    expect(parseMissingSteps(Array.from({ length: 40 }, (_, i) => `s${i}`))).toHaveLength(24);
  });

  it("reads one run's detail from the run's own route, and nothing from a body with no run", () => {
    expect(parseExecutiveDetail({ run_id: "r1", explain: "Üçüncü adım: rapor yazılıyor.", missing: ["s4"] })).toEqual({
      run_id: "r1",
      explain: "Üçüncü adım: rapor yazılıyor.",
      missing: [{ step: "s4", reason: null }],
    });
    // The detail route answers with the run's fields at the top level, and this reads
    // exactly that: a nested `run` object or an `explanation` spelling is a shape nobody
    // sends, and guessing at it is what let the two halves drift unnoticed.
    expect(parseExecutiveDetail({ run: { run_id: "r1", explanation: "İkinci adım." } })).toBeNull();
    expect(parseExecutiveDetail({ explain: "bir cümle" })).toBeNull();
    expect(parseExecutiveDetail(null)).toBeNull();
    // A run is re-asked when the list says it moved, and not otherwise.
    expect(executiveDetailKey({ run_id: "r1", step: "s3", state: "running" })).toBe("r1#s3#running");
    expect(executiveDetailKey({ run_id: "r1", step: "s4", state: "running" })).not.toBe(
      executiveDetailKey({ run_id: "r1", step: "s3", state: "running" }),
    );
    expect(executiveDetailNotice("r1", new Error("HTTP 503"))).toBe("Ayrıntı alınamadı (r1): HTTP 503");
  });
});

// ------------------------------------------------------------ the runner

describe("the action runner", () => {
  it("makes exactly one call with the run's id, writes busy then the receipt's outcome, and reloads the list", async () => {
    const client = fakeClient();
    const { ports, writes, current, onSettled } = portsOf(client);
    expect(await runExecutiveAction(ports, "pause", "r1")).toBe(true);
    expect(client.pause).toHaveBeenCalledTimes(1);
    expect(client.pause).toHaveBeenCalledWith("r1");
    expect(client.resume).not.toHaveBeenCalled();
    expect(client.cancel).not.toHaveBeenCalled();
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual({ busy: { action: "pause", id: "r1" }, outcome: null });
    expect(current().busy).toBeNull();
    expect(current().outcome).toEqual({
      action: "pause",
      id: "r1",
      ok: true,
      text: "Duraklatıldı · Çalışan adım bitirildi.",
      at: T0,
    });
    expect(onSettled).toHaveBeenCalledTimes(1);

    const second = portsOf(fakeClient());
    await runExecutiveAction(second.ports, "cancel", "r2");
    expect(second.ports.client.cancel).toHaveBeenCalledTimes(1);
    expect(second.ports.client.cancel).toHaveBeenCalledWith("r2");
    expect(second.ports.client.pause).not.toHaveBeenCalled();
    expect(second.current().outcome?.text).toBe("İptal edildi");

    const third = portsOf(fakeClient());
    await runExecutiveAction(third.ports, "resume", "r3");
    expect(third.ports.client.resume).toHaveBeenCalledTimes(1);
    expect(third.current().outcome?.text).toBe("Çalışıyor");
  });

  it("refuses a second press while the first is in flight: the client is still called once", async () => {
    const deferred: { release: ((receipt: ExecutiveActionReceipt) => void) | null } = { release: null };
    const pause = vi.fn(async () => RECEIPT_PAUSED);
    pause.mockImplementationOnce(
      () =>
        new Promise<ExecutiveActionReceipt>((resolve) => {
          deferred.release = resolve;
        }),
    );
    const client = fakeClient({ pause });
    const { ports, current } = portsOf(client);

    const first = runExecutiveAction(ports, "pause", "r1");
    expect(current().busy).toEqual({ action: "pause", id: "r1" });
    expect(await runExecutiveAction(ports, "pause", "r1")).toBe(false);
    expect(await runExecutiveAction(ports, "cancel", "r1")).toBe(false);
    expect(await runExecutiveAction(ports, "resume", "r3")).toBe(false);
    expect(pause).toHaveBeenCalledTimes(1);
    expect(client.cancel).not.toHaveBeenCalled();
    expect(client.resume).not.toHaveBeenCalled();

    expect(deferred.release).not.toBeNull();
    deferred.release?.(RECEIPT_PAUSED);
    expect(await first).toBe(true);
    expect(current().busy).toBeNull();
    // Once settled, the next press goes through — a second pause is the Cloud
    // Core's to refuse, not this page's to hide.
    expect(await runExecutiveAction(ports, "pause", "r1")).toBe(true);
    expect(pause).toHaveBeenCalledTimes(2);
  });

  it("turns the Cloud Core's refusal — and a route not there yet — into the owner's words, says nothing happened, and still reloads", async () => {
    const client = fakeClient({
      resume: vi.fn(async () => {
        throw new ExecutiveActionError(409, "not_paused", "run is in state running");
      }),
    });
    const { ports, current, onSettled } = portsOf(client);
    expect(await runExecutiveAction(ports, "resume", "r1")).toBe(true);
    const outcome = current().outcome;
    expect(outcome?.ok).toBe(false);
    expect(outcome?.text).toBe("İş duraklatılmış değil; sürdürülecek bir şey yok.");
    expect(outcome?.text).not.toContain("çalışıyor");
    expect(current().busy).toBeNull();
    expect(onSettled).toHaveBeenCalledTimes(1);

    const absent = fakeClient({
      cancel: vi.fn(async () => {
        throw new ExecutiveActionError(404, EXECUTIVE_ROUTE_ABSENT, "Bu Cloud Core sürümünde /v1/executive/runs/r1/cancel yok (HTTP 404).");
      }),
    });
    const second = portsOf(absent);
    await runExecutiveAction(second.ports, "cancel", "r1");
    expect(second.current().outcome?.ok).toBe(false);
    expect(second.current().outcome?.text).toBe("Bu Cloud Core sürümünde /v1/executive/runs/r1/cancel yok (HTTP 404). Yapılmadı.");
  });

  it("never says 'tamamlandı' or 'duraklatıldı' on the strength of a 2xx alone", () => {
    const none: ExecutiveActionReceipt = { state: null, summary: null, receiptId: "r1" };
    for (const action of EXECUTIVE_CHIP_ACTIONS) {
      expect(executiveOutcomeText(action, none)).toBe(EXECUTIVE_OUTCOME_NO_STATE_TR[action]);
      expect(executiveOutcomeText(action, none)).not.toMatch(/tamamlandı|duraklatıldı|iptal edildi/);
    }
    expect(executiveOutcomeText("pause", { ...none, state: "paused" })).toBe("Duraklatıldı");
    expect(executiveOutcomeText("cancel", { ...none, state: "cancelled" })).toBe("İptal edildi");
    expect(executiveOutcomeText("resume", { ...none, state: "completed" })).toBe("Tamamlandı");
    // A state this build cannot read is printed as the token, never as one of the seven.
    expect(executiveOutcomeText("pause", { ...none, state: "pausing", summary: "sinyal iletildi" })).toBe(
      "durum: pausing · sinyal iletildi",
    );
    expect(executiveOutcomeText("pause", { ...none, state: "completed_with_errors" })).not.toContain("tamamlandı");
    // A summary with no state rides beside the no-state sentence.
    expect(executiveOutcomeText("pause", { ...none, summary: "İletildi." })).toBe(
      `${EXECUTIVE_OUTCOME_NO_STATE_TR.pause} · İletildi.`,
    );
  });
});

// ------------------------------------------------------------- the readout

describe("the Core's readout for Executive Autonomy", () => {
  it("headlines the running posture with the caption and the facts beneath, and a bar from two counts", () => {
    const html = readout([EXECUTIVE_RUN("r1", "running", "s3", 2, 5)]);
    expect(html).toContain('data-core-kind="executive_run"');
    expect(html).toContain('data-core-state="executive.run"');
    expect(html).toContain('data-core-subsystem="executive"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain('data-executive-posture="running"');
    expect(html).toContain("Çok adımlı iş");
    expect(html).toContain('data-label="true">adım s3 · çalışıyor · 2/5 adım</p>');
    expect(html).toContain("data-executive-facts");
    expect(html).toContain('data-executive-run="r1"');
    expect(html).toContain('data-executive-step="s3"');
    expect(html).toContain('data-executive-state="running"');
    expect(html).toContain('data-executive-done="2"');
    expect(html).toContain('data-executive-total="5"');
    expect(html).toContain("iş: r1 · adım: s3 · durum: çalışıyor · 2/5 adım");
    expect(html).toContain("core-progress-fill");
    expect(html).not.toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-scene-facts");
    expect(html).not.toContain("data-genesis-facts");
    // No control on the Core: the three chips are the Cockpit's, from the row.
    expect(html).not.toContain("<button");
  });

  it("draws no bar when only one count came, and says so", () => {
    const html = readout([EXECUTIVE_RUN("r1", "running", "s3", 2, null)]);
    expect(html).toContain('data-executive-done="2"');
    expect(html).toContain('data-executive-total=""');
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).toContain("iş: r1 · adım: s3 · durum: çalışıyor");
  });

  it("marks the paused posture and never words it as an error", () => {
    const html = readout([EXECUTIVE_RUN("r1", "paused", "s3", 2, 5)]);
    expect(html).toContain('data-executive-posture="paused"');
    expect(html).toContain('data-executive-paused="yes"');
    expect(html).toContain('data-label="true">adım s3 · duraklatıldı · 2/5 adım</p>');
    expect(html).toContain('data-severity="info"');
    expect(html).not.toContain("başarısız");
    expect(html).not.toContain("<button");
  });

  it("says 'tamamlandı' only for completed, and names a partial run as partly done", () => {
    const completed = readout([EXECUTIVE_RUN("r1", "completed", null, 5, 5)]);
    expect(completed).toContain('data-executive-posture="completed"');
    expect(completed).toContain('data-label="true">tamamlandı · 5/5 adım</p>');

    const partial = readout([EXECUTIVE_RUN("r1", "partial", null, 3, 5)]);
    expect(partial).toContain('data-executive-posture="partial"');
    expect(partial).toContain('data-label="true">kısmen bitti · 3/5 adım</p>');
    // The readout's detail sentence explains what "tamamlandı" means on every
    // render; what is asserted is that no other state's LABEL reaches it.
    expect(partial).not.toContain(">tamamlandı");

    for (const state of ["planned", "running", "paused", "cancelled", "failed"]) {
      expect(readout([EXECUTIVE_RUN("r1", state, "s5", 5, 5)]), state).not.toContain(">tamamlandı");
    }
  });

  it("says what was not reported when the publisher named nothing", () => {
    const html = readout([EXECUTIVE_RUN_BARE()]);
    expect(html).toContain('data-label="true">Çok adımlı iş</p>');
    expect(html).toContain("iş bildirilmedi · adım bildirilmedi · durum bildirilmedi");
    expect(html).toContain('data-executive-run=""');
    expect(html).toContain('data-executive-state=""');
    // Nothing settled was said, so the run is drawn as one in progress.
    expect(html).toContain('data-executive-posture="running"');
    expect(html).not.toContain("core-progress-fill");
  });

  it("keeps the caption and the posture in the compact form and drops the long line", () => {
    const html = readout([EXECUTIVE_RUN("r1", "paused", "s3", 2, 5)], true);
    expect(html).toContain("adım s3 · duraklatıldı · 2/5 adım");
    expect(html).toContain('data-executive-posture="paused"');
    expect(html).not.toContain("data-executive-facts");
  });

  it("names the aged-out run as last-known rather than as running, with its facts", () => {
    const html = readout([EXECUTIVE_RUN("r1", "running", "s3", 2, 5)], false, T0 + EXECUTIVE_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="executive.run"');
    expect(html).toContain("data-executive-facts");
    expect(html).toContain("iş: r1");
    // The posture follows the facts, not the life of the claim: what it WAS is still a fact.
    expect(html).toContain('data-executive-posture="running"');
    expect(html).not.toContain("tamamlandı");
  });

  it("prints no executive line or posture for any other kind", () => {
    for (const events of [[AGENT_IDLE()], [DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [ARTIFACT_FACTORY()], [APP_FACTORY()], [CAPABILITY_GENESIS()], [SCENE_ACTIVITY()]]) {
      const html = readout(events);
      expect(html).not.toContain("data-executive-facts");
      expect(html).not.toContain("data-executive-posture");
    }
  });
});
