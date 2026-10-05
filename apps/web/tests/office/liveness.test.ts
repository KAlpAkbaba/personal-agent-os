import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import OfficeView from "../../app/core/office/OfficeView";
import type { OfficeAgent, OfficeTask } from "../../app/core/office/officeApi";
import { seatLiveness, waitingReason } from "../../app/core/office/officeLiveness";
import { twoWorkers } from "./fixtures";

// The owner, 2026-10-04: "Proje yöneticisine söyle arada gerçekten işte çalışıp çalışmadıklarını
// da kontrol etsin, iş takılmış olmasın." And 2026-10-05: "Çalışan 4 hala kızgın" - a task that
// only waits for another one is not angry.

function seat(extra: Record<string, unknown> = {}): OfficeAgent {
  return {
    seat: "worker-2",
    role: "worker",
    state: "working",
    task_id: "gate-faster",
    task_title: "Kapı hızlı",
    since: "2026-10-04T11:41:00Z",
    runs: [],
    ...extra,
  } as OfficeAgent;
}

function stopped(reason: string | null): OfficeTask {
  return { title: "T", state: "stopped", goal: "", acceptance: "", branch: "", sha: null, reason, report: null };
}

describe("a run that showed no life", () => {
  it("is 'takılmış olabilir' with its idle minutes, not tired", () => {
    expect(seatLiveness(seat({ stuck: true, idle_minutes: 34 }), undefined)).toEqual({
      kind: "stuck",
      label: "takılmış olabilir - 34 dk iz yok",
    });
  });

  it("a run that showed life is not flagged, however long it runs", () => {
    expect(seatLiveness(seat({ stuck: false, idle_minutes: 1, since: "2026-10-04T05:00:00Z" }), undefined)).toBeNull();
  });

  it("an older API without the fields shows nothing new", () => {
    expect(seatLiveness(seat(), undefined)).toBeNull();
  });

  it("a broken field is not trusted", () => {
    expect(seatLiveness(seat({ stuck: "yes", idle_minutes: 34 }), undefined)).toBeNull();
    expect(seatLiveness(seat({ stuck: true, idle_minutes: -1 }), undefined)).toBeNull();
    expect(seatLiveness(seat({ stuck: true, idle_minutes: "34" }), undefined)).toBeNull();
  });

  it("only a working seat can be stuck", () => {
    expect(seatLiveness(seat({ state: "waiting", stuck: true, idle_minutes: 34 }), undefined)).toBeNull();
  });
});

describe("a stopped task that only waits is calm, with the reason in a few words", () => {
  it("waits for another task's files", () => {
    const task = stopped("Proje Yöneticisi: Testi ekle (alan çakışması: watch-voice; o iş bitince)");
    expect(waitingReason(task)).toBe("sırada: watch-voice bitince");
    expect(seatLiveness(seat({ state: "returned" }), task)).toEqual({ kind: "queued", label: "sırada: watch-voice bitince" });
  });

  it("names every holder", () => {
    expect(waitingReason(stopped("PY: x (alan çakışması: a-task, b-task; o iş bitince)"))).toBe(
      "sırada: a-task, b-task bitince",
    );
  });

  it("parked with the Danışman", () => {
    const task = stopped("Danışman'a iletildi: güvenlik kararı gerekiyor, sahibin onayı ile");
    expect(seatLiveness(seat({ state: "returned" }), task)).toEqual({
      kind: "parked",
      label: "Danışman'da: güvenlik kararı gerekiyor…",
    });
  });

  it("a stop nobody decided yet is not calm", () => {
    expect(waitingReason(stopped("Denetleyici reddetti: test kırmızı"))).toBeNull();
    expect(waitingReason(stopped(null))).toBeNull();
    expect(seatLiveness(seat({ state: "returned" }), stopped("budget"))).toBeNull();
    // The words in the middle of a reason are not a decision: only its end is.
    expect(waitingReason(stopped("PY: y (alan çakışması: x; o iş bitince) - ama sonra kırmızı oldu"))).toBeNull();
  });

  it("only a stopped task waits", () => {
    const task = { ...stopped("PY: x (alan çakışması: a; o iş bitince)"), state: "returned" };
    expect(waitingReason(task)).toBeNull();
  });
});

// The seat itself (inspector's return 1, 2026-10-05): the labels must reach the owner's page, not
// only this module. RED until OfficeView/officeMood call seatLiveness (outside this task's area).
function page(view: ReturnType<typeof twoWorkers>) {
  return renderToStaticMarkup(
    createElement(OfficeView, { view, selected: null, offline: false, reducedMotion: false, onSelect: () => {} }),
  );
}

describe("the Ofis seat shows it", () => {
  it("a stuck working seat reads 'takılmış olabilir - N dk iz yok'", () => {
    const view = twoWorkers();
    view.agents = view.agents.map((a) => (a.seat === "worker-1" ? ({ ...a, stuck: true, idle_minutes: 34 } as typeof a) : a));
    expect(page(view)).toContain("takılmış olabilir - 34 dk iz yok");
    expect(page(twoWorkers())).not.toContain("takılmış olabilir");
  });

  it("a task waiting for another's files reads calm 'sırada: X bitince'", () => {
    const view = twoWorkers();
    view.tasks["t-back"] = { ...view.tasks["t-back"], state: "stopped", reason: "PY: bekle (alan çakışması: watch-voice; o iş bitince)" };
    expect(page(view)).toContain("sırada: watch-voice bitince");
  });
});
