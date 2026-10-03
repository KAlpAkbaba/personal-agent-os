/**
 * The seat panel as the owner reads it (ADR-0241 addendum, office-panel-plain-turkish): the
 * task's state once and in Turkish, the English card text written for the agents closed
 * under one <details>. The owner read "-> RED." at the end of an acceptance as the task's
 * verdict (2026-10-02 17:40).
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import OfficePanel from "../../app/core/office/OfficePanel";
import { buildPanel } from "../../app/core/office/officeModel";
import type { OfficeView } from "../../app/core/office/officeApi";

const SCHEMA = resolve(__dirname, "../../../../team/queue.schema.json");

/** Every state the queue can hold, from the queue's own schema - not retyped here. */
function queueStates(): string[] {
  const schema = JSON.parse(readFileSync(SCHEMA, "utf8"));
  const states: unknown = schema.$defs.task.properties.state.enum;
  if (!Array.isArray(states) || states.length === 0) throw new Error("no state enum");
  return states as string[];
}

const TITLE = "Ofis sayfasında ayrıntı sahibin okuyacağı gibi olsun";
const GOAL =
  "The owner, 2026-10-02 17:40, asked why the panel still runs; show the state in Turkish & keep <the card> text.";
const ACCEPTANCE =
  "Red first. apps/web/tests/office/panel.test.tsx: the details closed; mutation: rule removed -> RED.";
const EVIDENCE = "PROVEN_AUTOMATED: red-first run, vitest / tsc / oxlint raw counts.";
const SINCE = "2026-10-01T10:00:00Z";

type Extra = { reason?: string | null; outcome?: string | null };

/** One working worker whose task is in `state`, plus a waiting lead and the owner. */
function office(state: string, extra: Extra = {}): OfficeView {
  const idle = { state: "waiting" as const, task_id: null, task_title: null, since: null };
  return {
    cycle: {
      cycle_id: "cycle-test",
      machine: "ev-pc",
      started_at: SINCE,
      running: true,
      running_agents: 1,
      capacity: 6,
      estimated_usd: 0,
      usage_limit: { state: "ok", resets_at: null },
      updated_at: SINCE,
    },
    agents: [
      { seat: "lead", role: "lead", ...idle },
      {
        seat: "worker-1",
        role: "worker",
        state: "working",
        task_id: "t-1",
        task_title: TITLE,
        since: SINCE,
      },
      { seat: "owner", role: "owner", ...idle },
    ],
    tasks: {
      "t-1": {
        title: TITLE,
        state,
        goal: GOAL,
        acceptance: ACCEPTANCE,
        // not in the page's type: the panel shows it when the API sends it
        ...({ evidence_expected: EVIDENCE } as object),
        branch: "team/test/worker-one",
        sha: null,
        reason: extra.reason ?? null,
        report:
          extra.outcome == null
            ? null
            : { role: "worker", at: SINCE, outcome: extra.outcome, summary: ["satır 1"] },
      },
    },
    approvals: [],
  };
}

function render(view: OfficeView, seat = "worker-1"): string {
  return renderToStaticMarkup(<OfficePanel panel={buildPanel(view, seat)} />);
}

function unescape(html: string): string {
  return html
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&");
}

/** The markup split at the one <details>: before it, inside it, after it. */
function parts(html: string) {
  const open = html.indexOf("<details");
  const close = html.indexOf("</details>");
  expect(open, "a <details> element").toBeGreaterThan(-1);
  expect(html.indexOf("<details", open + 1), "only one <details>").toBe(-1);
  return {
    before: html.slice(0, open),
    tag: html.slice(open, html.indexOf(">", open) + 1),
    inside: html.slice(open, close + "</details>".length),
    after: html.slice(close + "</details>".length),
  };
}

const clockOf = (iso: string) =>
  new Date(iso).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });

describe("the first view of a working seat", () => {
  it("names the seat, the Turkish state, the title and since when - no English card text, no RED", () => {
    const { before } = parts(render(office("in_progress", { outcome: "ilk taslak hazır" })));
    for (const absent of ["RED", "in_progress", "The owner, 2026", "Red first", "PROVEN_"])
      expect(before, absent).not.toContain(absent);
    expect(before).toContain("<h2>Çalışan 1</h2>");
    expect(before).toContain("yazılıyor");
    expect(before).toContain(TITLE);
    expect(before).toContain(`Başladı: ${clockOf(SINCE)}`);
    expect(before).toContain("Son rapor: ilk taslak hazır");
    const order = ["<h2>Çalışan 1</h2>", "yazılıyor", TITLE, "Başladı:", "Son rapor:"];
    const at = order.map((s) => before.indexOf(s));
    expect(at.every((pos, i) => pos > -1 && (i === 0 || pos > at[i - 1])), String(at)).toBe(true);
  });
});

describe("the card text written for the agents", () => {
  it("is in one closed <details> under its Turkish summary, whole, after the note about RED / GREEN", () => {
    const { tag, inside } = parts(render(office("in_progress")));
    expect(tag).not.toMatch(/\sopen/);
    expect(inside).toContain(
      "<summary>Ajanlar için yazılmış kart metni (İngilizce, teknik)</summary>",
    );
    const text = unescape(inside);
    const note = text.indexOf("RED / GREEN / PASS / FAIL");
    expect(note).toBeGreaterThan(-1);
    for (const whole of [GOAL, ACCEPTANCE, EVIDENCE]) {
      expect(text, whole).toContain(whole);
      expect(note).toBeLessThan(text.indexOf(whole));
    }
    expect(text).toContain("işin sonucu değil");
  });

  it("leaves the evidence line out when the API does not send one", () => {
    const view = office("in_progress");
    delete (view.tasks["t-1"] as { evidence_expected?: string }).evidence_expected;
    const { inside } = parts(render(view));
    expect(unescape(inside)).toContain(ACCEPTANCE);
    expect(inside).not.toContain("Beklenen kanıt");
    expect(parts(render(office("in_progress"))).inside).toContain("Beklenen kanıt");
  });
});

describe("the queue's state words", () => {
  it("are never printed outside the <details>, for every state of the queue", () => {
    const states = queueStates();
    expect(states).toContain("in_progress");
    for (const state of states) {
      const { before, after } = parts(render(office(state)));
      expect(before + after, state).not.toContain(state);
    }
  });

  it("shows a state the page does not know as it is", () => {
    const { before } = parts(render(office("brand_new_state")));
    expect(before).toContain("brand_new_state");
  });
});

describe("the reason", () => {
  it("is in the first view when it is Turkish", () => {
    const reason = "sahip onayladı, sıradaki iş bu";
    const { before, inside } = parts(render(office("approved", { reason })));
    expect(before).toContain(`Neden: ${reason}`);
    expect(inside).not.toContain(reason);
  });

  it("goes under the fold when it is a lead note", () => {
    const reason = "LEAD: returned twice, rule removed -> RED on the inspector's run";
    const html = render(office("returned", { reason }));
    const { before, inside, after } = parts(html);
    expect(before).not.toContain("LEAD");
    expect(before).not.toContain("Neden");
    expect(after).not.toContain("LEAD");
    expect(unescape(inside)).toContain(reason);
  });

  it("has no label when the task has none", () => {
    expect(render(office("in_progress"))).not.toContain("Neden");
  });
});

describe("a seat with no task", () => {
  it("renders as it did before this change", () => {
    const view = office("in_progress");
    expect(render(view, "lead")).toBe(
      '<aside class="office-panel" data-office="panel" data-panel-seat="lead"><h2>Proje Yöneticisi</h2><p class="muted">Durum: bekliyor</p><p class="muted">Bu koltuğun şu an bir işi yok.</p></aside>',
    );
    expect(render(view, "owner")).toBe(
      '<aside class="office-panel" data-office="panel" data-panel-seat="owner"><h2>Sahip</h2><p class="muted">Durum: bekliyor</p><p class="muted">Bu koltuğun şu an bir işi yok.</p></aside>',
    );
  });
});
