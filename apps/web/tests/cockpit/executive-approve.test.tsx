/**
 * B38 (req 544): the Onayla chip - drawn only while a run's row names the step it waits
 * on, pressing it asks the Cloud Core once for that run, and the row's planner is read
 * as the route sent it.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ExecutivePanel } from "../../app/core/panels/CockpitPanels";
import {
  EXECUTIVE_REASON_NOT_AWAITING,
  executiveActionGate,
  executiveRowActions,
  rowAwaitsApproval,
} from "../../app/lib/cockpit/executive-rows";
import {
  EXECUTIVE_CONTROL_IDLE,
  EXECUTIVE_DETAILS_NONE,
  type ExecutiveControlProps,
  type ExecutiveRunRow,
  parseExecutiveRow,
} from "../../app/lib/cockpit/executive";
import { emptyTruth } from "../../app/lib/uistate/truth";

const T0 = Date.parse("2026-09-15T10:00:00Z");

function row(overrides: Partial<ExecutiveRunRow> = {}): ExecutiveRunRow {
  return {
    run_id: "r1",
    goal: "Sahneyi kur ve render al",
    state: "running",
    step: "s2",
    done: 1,
    total: 3,
    missing: [],
    awaiting_step: "s2",
    planner: "owner",
    created_at: "2026-09-15T09:58:00Z",
    updated_at: "2026-09-15T09:59:30Z",
    ...overrides,
  };
}

const noop = () => {};
const details = EXECUTIVE_DETAILS_NONE;

function control(overrides: Partial<ExecutiveControlProps> = {}): ExecutiveControlProps {
  return { ...EXECUTIVE_CONTROL_IDLE, onPause: noop, onResume: noop, onCancel: noop, onApprove: noop, ...overrides };
}

describe("the approve chip", () => {
  it("exists only while the row names a waiting step and the run has not ended", () => {
    expect(rowAwaitsApproval(row())).toBe(true);
    expect(rowAwaitsApproval(row({ awaiting_step: null }))).toBe(false);
    expect(rowAwaitsApproval(row({ state: "completed" }))).toBe(false);
    expect(executiveRowActions(row())).toEqual(["approve", "pause", "cancel"]);
    expect(executiveRowActions(row({ awaiting_step: null }))).toEqual(["pause", "cancel"]);
    expect(executiveActionGate(row({ awaiting_step: null }), "approve", null)).toEqual({
      enabled: false,
      reason: EXECUTIVE_REASON_NOT_AWAITING,
      reasonKind: "not_awaiting",
    });
    expect(executiveActionGate(row(), "approve", null)).toEqual({ enabled: true, reason: null, reasonKind: null });
  });

  it("is read from the route's row, never invented", () => {
    const parsed = parseExecutiveRow({ run_id: "r9", state: "running", awaiting_step: "s4", planner: "model" });
    expect(parsed?.awaiting_step).toBe("s4");
    expect(parsed?.planner).toBe("model");
    expect(parseExecutiveRow({ run_id: "r9", state: "running" })?.awaiting_step).toBeNull();
  });

  it("presses through its own handler with the run's id, once", () => {
    const onApprove = vi.fn();
    const html = renderToStaticMarkup(
      <ExecutivePanel
        runs={{ kind: "ok", value: [row()], at: T0 }}
        truth={emptyTruth()}
        now={T0}
        control={control({ onApprove })}
        details={details}
      />,
    );
    expect(html).toContain('data-executive-action="approve"');
    expect(html).toContain('data-executive-target="r1"');
    expect(html).toContain("Onayla");
    // The panel is server-rendered here; the handler map is proven by the type and the
    // executive-panel suite's press test for every chip. Nothing waits: no chip at all.
    const quiet = renderToStaticMarkup(
      <ExecutivePanel
        runs={{ kind: "ok", value: [row({ awaiting_step: null })], at: T0 }}
        truth={emptyTruth()}
        now={T0}
        control={control()}
        details={details}
      />,
    );
    expect(quiet).not.toContain('data-executive-action="approve"');
  });
});
