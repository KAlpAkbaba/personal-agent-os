import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import OfficePanel from "../../app/core/office/OfficePanel";
import { accusative, buildOffice, buildPanel, panelProgress, waitText } from "../../app/core/office/officeModel";
import { twoWorkers } from "./fixtures";

// The owner, 2026-10-05: "tıkladığımda ajanların çalıştıkları kısımda kodun yüzde kaçını yazdığı
// bir kısım ekler misin". Measured by the cycle from the run's worktree, said honestly.

const NOW = new Date("2026-10-05T16:10:00Z");
const PROGRESS = {
  area_total: 4,
  area_touched: 3,
  tests_changed: true,
  adr_draft: false,
  commits: 2,
  last_change_at: "2026-10-05T16:06:00Z",
};

function working() {
  const view = twoWorkers();
  const agent = view.agents.find((a) => a.seat === "worker-1")!;
  agent.progress = PROGRESS;
  return { view, agent };
}

describe("how far a working run has got", () => {
  it("is the share of the card's files with a change, never 'the work is done'", () => {
    const { agent } = working();
    const p = panelProgress(agent, NOW)!;
    expect(p.percent).toBe(75);
    expect(p.label).toBe("Kartın dosyalarının %75'i değişti (3/4)");
    expect(p.marks).toEqual([
      { text: "Testler yazıldı", done: true },
      { text: "Kod değişti", done: true },
      { text: "ADR taslağı", done: false },
    ]);
    expect(p.lastChange).toBe("4 dk önce");
  });

  it("shows nothing for an older cycle, a seat not working, or a card without an area", () => {
    const { agent } = working();
    expect(panelProgress({ ...agent, progress: undefined }, NOW)).toBeNull();
    expect(panelProgress({ ...agent, state: "waiting" }, NOW)).toBeNull();
    expect(panelProgress({ ...agent, progress: { ...PROGRESS, area_total: 0 } }, NOW)).toBeNull();
  });

  it("the panel draws the bar, the label and the marks", () => {
    const { view } = working();
    const panel = buildPanel(view, "worker-1")!;
    expect(panel.progress?.percent).toBe(75);
    const html = renderToStaticMarkup(<OfficePanel panel={panel} />);
    expect(html).toContain('data-panel="progress"');
    expect(html).toContain('aria-valuenow="75"');
    expect(html).toContain("Kartın dosyalarının %75");
    expect(html).toContain("Testler yazıldı");
  });
});

// The owner, 2026-10-06: "Çalışan 2 ve denetleyicinin son değişiklik süreleri neden bu kadar uzun?"
// last_change_at is the newest COMMIT; the real sign of work is the cycle's last_activity_at.
describe("the last movement, apart from the last commit", () => {
  const MOVED = "2026-10-05T16:08:00Z"; // 2 min before NOW
  const COMMITTED = "2026-10-05T13:10:00Z"; // 3 h before NOW

  function seat(name: string, extra: Record<string, unknown>) {
    const view = twoWorkers();
    view.agents = view.agents.map((a) =>
      a.seat === name
        ? ({
            ...a,
            state: "working",
            task_id: "t-one",
            task_title: "Birinci iş",
            since: "2026-10-05T12:00:00Z",
            progress: { ...PROGRESS, last_change_at: COMMITTED },
            ...extra,
          } as typeof a)
        : a,
    );
    return view;
  }

  function html(view: ReturnType<typeof twoWorkers>, name: string): string {
    return renderToStaticMarkup(<OfficePanel panel={buildPanel(view, name, NOW)!} />);
  }

  it("a worker shows 'Son hareket: 2 dk önce' first and the commit, smaller, as 'Son kayıt'", () => {
    const view = seat("worker-1", { last_activity_at: MOVED, idle_minutes: 2, stuck: false });
    const p = buildPanel(view, "worker-1", NOW)!.progress!;
    expect(p.movement).toBe("2 dk önce");
    expect(p.changeLabel).toBe("Son kayıt");
    expect(p.lastChange).toBe("180 dk önce");
    const page = html(view, "worker-1");
    expect(page).toContain("Son hareket: 2 dk önce");
    expect(page).toContain("Son kayıt: 180 dk önce");
    expect(page.indexOf("Son hareket")).toBeLessThan(page.indexOf("Son kayıt"));
    expect(page).not.toContain("Son değişiklik");
    expect(page).not.toContain("Denetliyor");
  });

  it("an inspector says 'Denetliyor' and never presents the worker's commit as its own", () => {
    const view = seat("inspector", { last_activity_at: MOVED, idle_minutes: 2, stuck: false });
    const p = buildPanel(view, "inspector", NOW)!.progress!;
    expect(p.inspecting).toBe(true);
    expect(p.changeLabel).toBe("Çalışanın son kaydı");
    const page = html(view, "inspector");
    expect(page).toContain("Denetliyor");
    expect(page).toContain("Son hareket: 2 dk önce");
    expect(page).toContain("Çalışanın son kaydı: 180 dk önce");
    expect(page).not.toMatch(/>Son kayıt:/);
  });

  it("a run without last_activity_at shows '—' for the movement", () => {
    const view = seat("worker-1", {});
    expect(buildPanel(view, "worker-1", NOW)!.progress!.movement).toBe("—");
    expect(html(view, "worker-1")).toContain("Son hareket: —");
    const broken = seat("worker-1", { last_activity_at: "dün" });
    expect(buildPanel(broken, "worker-1", NOW)!.progress!.movement).toBe("—");
  });

  it("a run at the stuck bound keeps 'takılmış olabilir'", () => {
    const view = seat("worker-1", { last_activity_at: "2026-10-05T15:36:00Z", idle_minutes: 34, stuck: true });
    expect(buildPanel(view, "worker-1", NOW)!.progress!.stuck).toBe("takılmış olabilir - 34 dk iz yok");
    expect(html(view, "worker-1")).toContain("takılmış olabilir - 34 dk iz yok");
    const fresh = seat("worker-1", { last_activity_at: MOVED, idle_minutes: 2, stuck: false });
    expect(buildPanel(fresh, "worker-1", NOW)!.progress!.stuck).toBeNull();
  });
});

// The owner, 2026-10-05: "işin durumu aslında Proje Yöneticisi değil, Çalışan 2'nin bitirmesini
// beklediği için bunları bu şekilde güncelleyelim".
describe("a stopped task names what it waits for", () => {
  it("names the seat whose task holds its files, and the Danışman when it is with him", () => {
    const view = twoWorkers();
    const holder = view.agents.find((a) => a.seat === "worker-1")!;
    const waiting = view.agents.find((a) => a.seat === "worker-4")!;
    view.tasks[waiting.task_id!] = {
      ...view.tasks[waiting.task_id!],
      state: "stopped",
      reason: `Proje Yöneticisi: testi ekle (alan çakışması: ${holder.task_id}; o iş bitince)`,
    };
    const text = waitText(view, view.tasks[waiting.task_id!])!;
    expect(text.short).toBe("Çalışan 1'i bekliyor");
    expect(text.long).toContain("Sırada: Çalışan 1");
    expect(buildOffice(view).seats.find((s) => s.seat === "worker-4")!.label).toBe("Çalışan 1'i bekliyor");
    expect(buildPanel(view, "worker-4")!.task!.stateText).toContain("Çalışan 1");
    const parked = { ...view.tasks[waiting.task_id!], reason: "Danışman'a iletildi: entegrasyon dalında çakışma" };
    expect(waitText(view, parked)!.short).toBe("Danışman'da");
    expect(waitText(view, { ...parked, reason: "1: gerçek bir hata" })).toBeNull();
  });
});

describe("the seat name in Turkish accusative", () => {
  it("follows the number's sound and the name's last vowel", () => {
    expect(accusative("Çalışan 1")).toBe("Çalışan 1'i");
    expect(accusative("Çalışan 2")).toBe("Çalışan 2'yi");
    expect(accusative("Çalışan 3")).toBe("Çalışan 3'ü");
    expect(accusative("Çalışan 4")).toBe("Çalışan 4'ü");
    expect(accusative("Çalışan 6")).toBe("Çalışan 6'yı");
    expect(accusative("Çalışan 9")).toBe("Çalışan 9'u");
    expect(accusative("Denetleyici")).toBe("Denetleyici'yi");
    expect(accusative("Araştırmacı")).toBe("Araştırmacı'yı");
  });
});
