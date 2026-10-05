import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { OFFICE_PATH, fetchOffice } from "../../app/core/office/officeApi";
import OfficeView from "../../app/core/office/OfficeView";
import { busyCycle, queuedTask, twoWorkers } from "./fixtures";

function render(over: Partial<Parameters<typeof OfficeView>[0]> = {}) {
  return renderToStaticMarkup(
    <OfficeView
      view={twoWorkers()}
      selected={null}
      offline={false}
      reducedMotion={false}
      onSelect={() => {}}
      {...over}
    />,
  );
}

beforeEach(() => apiFetch.mockReset());

describe("the office fetch", () => {
  it("asks the contract's route and returns the body", async () => {
    apiFetch.mockResolvedValue(new Response(JSON.stringify(twoWorkers()), { status: 200 }));
    const view = await fetchOffice();
    expect(apiFetch).toHaveBeenCalledWith(OFFICE_PATH);
    expect(OFFICE_PATH).toBe("/v1/team/office");
    expect(view.cycle.cycle_id).toBe("cycle-2026-10-01");
  });

  it("throws on a refusal, so the page keeps the last office", async () => {
    apiFetch.mockResolvedValue(new Response("{}", { status: 503 }));
    await expect(fetchOffice()).rejects.toThrow();
  });
});

describe("the office view", () => {
  it("draws every seat as a button with aria-pressed and a role+state label", () => {
    const html = render({ selected: "worker-1" });
    expect(html.match(/data-seat="/g)).toHaveLength(9);
    expect(html).toContain('aria-label="Çalışan 1, çalışıyor"');
    expect(html).toMatch(/data-seat="worker-1" aria-pressed="true"/);
    expect(html).toMatch(/data-seat="lead" aria-pressed="false"/);
  });

  it("puts the task title above a working head and types", () => {
    const html = render();
    expect(html).toContain("Birinci iş");
    expect(html).toContain("office-typing");
    expect(html).toContain("2/6");
    expect(html).toContain("tahmini $3.50");
    expect(html).toContain("cycle-2026-10-01");
  });

  it("marks a returned seat with the warning", () => {
    expect(render()).toContain('data-warning="true"');
  });

  it("renders no animation class under reduced motion, with the static badge instead", () => {
    const html = render({ reducedMotion: true });
    expect(html).not.toContain("office-typing");
    expect(html).toContain("çalışıyor");
    expect(html).toContain("office-badge-static");
  });

  it("shows the clicked seat's panel: card, 40 lines, branch, 12-char sha with full title", () => {
    const html = render({ selected: "worker-1" });
    expect(html).toContain("birinci hedef");
    expect(html).toContain("birinci kabul");
    expect(html).toContain("satır 40");
    expect(html).not.toContain("satır 41");
    expect(html).toContain("team/cycle/worker-one");
    expect(html).toContain(">0123456789ab<");
    expect(html).toContain('title="0123456789abcdef0123456789abcdef01234567"');
  });

  it("asks for a click when no seat is selected", () => {
    expect(render()).toContain("koltuğa tıklayın");
  });

  it("lists the approvals with their gate, a link to /core/approvals, and the owner's count", () => {
    const html = render();
    expect(html).toContain("Fikir bir");
    expect(html).toContain("Yayın onayı");
    expect(html).toContain('href="/core/approvals"');
    expect(html).toMatch(/data-seat="owner"[\s\S]*?data-count="2"/);
  });

  it("says bağlantı yok on a failed fetch but still draws the last office", () => {
    const html = render({ offline: true });
    expect(html).toContain("bağlantı yok");
    expect(html).toContain("Birinci iş");
    expect(render()).not.toContain("bağlantı yok");
  });
});

const seatHtml = (html: string, seat: string) =>
  html.match(new RegExp(`<button[^>]*data-seat="${seat}"[\\s\\S]*?</button>`))?.[0] ?? "";

describe("the office with every run on the page", () => {
  it("draws four worker desks labelled Çalışan 1 to Çalışan 4", () => {
    const html = render();
    for (const n of [1, 2, 3, 4]) {
      const seat = seatHtml(html, `worker-${n}`);
      expect(seat, `worker-${n}`).toContain(
        `<span class="office-name" aria-hidden="true">Çalışan ${n}</span>`,
      );
      expect(seat).toContain("var(--office-shirt)");
    }
    expect(html).toContain('aria-label="Çalışan 4, döndü"');
    expect(html).not.toContain('data-seat="worker-5"');
  });

  it("draws Çalışan 5 and Çalışan 6 when the API sends them", () => {
    const html = render({ view: busyCycle(6, 0) });
    expect(html.match(/data-seat="worker-\d"/g)).toHaveLength(6);
    expect(html).toContain('aria-label="Çalışan 6, çalışıyor"');
    expect(html).toContain("koşan ajan 6/6");
  });

  it("shows 5/6 in the top bar for four workers and one inspection", () => {
    const html = render({ view: busyCycle(4, 1) });
    expect(html).toContain("<span>koşan ajan 5/6</span>");
    expect(html.match(/office-typing/g)).toHaveLength(5);
    expect(html).not.toContain("office-run-count");
  });

  it("puts a ×3 badge beside çalışıyor on a seat with three runs, motion or not", () => {
    for (const reducedMotion of [false, true]) {
      const html = render({ view: busyCycle(1, 3), reducedMotion });
      const inspector = seatHtml(html, "inspector");
      expect(inspector).toMatch(/çalışıyor<span class="office-run-count"> ×3<\/span>/);
      expect(inspector).toContain('title="Denetim 1"');
      expect(inspector).toContain('aria-label="Denetleyici, çalışıyor, 3 koşu"');
      expect(seatHtml(html, "worker-1")).not.toContain("office-run-count");
      expect(html.match(/office-run-count/g)).toHaveLength(1);
    }
  });

  it("lists the three runs in the seat's panel above the first one's card and report", () => {
    const html = render({ view: busyCycle(0, 3), selected: "inspector" });
    const panel = html.match(/<aside[\s\S]*?<\/aside>/)?.[0] ?? "";
    const runs = panel.match(/<section data-panel="runs">[\s\S]*?<\/section>/)?.[0] ?? "";
    expect(runs).toContain("Koşan işler (3)");
    expect(runs.match(/<li>/g)).toHaveLength(3);
    expect(runs).toMatch(/Denetim 1[\s\S]*Denetim 2[\s\S]*Denetim 3/);
    expect(panel.indexOf('data-panel="runs"')).toBeLessThan(panel.indexOf('data-panel="card"'));
    expect(panel).toContain("ilk denetimin hedefi");
    expect(panel).toContain("satır 40");
    expect(render({ view: busyCycle(4, 1), selected: "inspector" })).not.toContain(
      'data-panel="runs"',
    );
  });

  it("draws an unknown seat id as a plain desk with its id, and does not throw", () => {
    const view = busyCycle(0, 0);
    view.agents.push({
      seat: "auditor-2",
      role: "auditor",
      state: "waiting",
      task_id: null,
      task_title: null,
      since: null,
    });
    const html = render({ view, selected: "auditor-2" });
    expect(html.match(/data-seat="/g)).toHaveLength(10);
    const seat = seatHtml(html, "auditor-2");
    expect(seat).toContain('<span class="office-name" aria-hidden="true">auditor-2</span>');
    expect(seat).toContain("var(--office-desk)");
    expect(seat).not.toContain("var(--office-shirt)");
    expect(html).toContain('data-panel-seat="auditor-2"');
    expect(html).toContain("<h2>auditor-2</h2>");
  });
});

describe("a task that waits for its next run (office-stable-seats)", () => {
  it("draws the queued seat seated, its title muted and sırada, with NO warning; the returned one keeps it", () => {
    const html = render({ view: queuedTask() });
    const queued = seatHtml(html, "worker-3");
    const returned = seatHtml(html, "worker-4");
    expect(queued).toContain('data-warning="false"');
    expect(queued).not.toContain("office-warning");
    expect(queued).toContain('aria-label="Çalışan 3, sırada"');
    expect(queued).toMatch(/<span class="office-label"[^>]*color:var\(--muted\)[^>]*>Sıradaki iş<\/span>/);
    expect(queued).toContain('<span class="office-badge-static" aria-hidden="true">sırada</span>');
    expect(queued).not.toContain("office-typing");
    expect(returned).toContain('data-warning="true"');
    expect(returned).toContain("office-warning");
    expect(returned).not.toContain("sırada");
  });

  it("renders an older server's answer (no queued field) as today: no sırada anywhere", () => {
    const html = render();
    expect(html).not.toContain("sırada");
    expect(seatHtml(html, "worker-3")).toContain('aria-label="Çalışan 3, bekliyor"');
    expect(html.match(/data-warning="true"/g)).toHaveLength(1);
  });
});
