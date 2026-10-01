import { describe, expect, it } from "vitest";

import { buildOffice, buildPanel, selectSeat } from "../../app/core/office/officeModel";
import { SEAT_ORDER, twoWorkers } from "./fixtures";

describe("the office model", () => {
  it("draws two working workers typing with their task titles and 2/6 in the top bar", () => {
    const office = buildOffice(twoWorkers());
    const w1 = office.seats.find((s) => s.seat === "worker-1")!;
    const w2 = office.seats.find((s) => s.seat === "worker-2")!;
    expect([w1.pose, w2.pose]).toEqual(["typing", "typing"]);
    expect([w1.label, w2.label]).toEqual(["Birinci iş", "İkinci iş"]);
    expect(office.topBar.runningAgents).toBe("koşan ajan 2/6");
  });

  it("keeps the eight seats in contract order", () => {
    expect(buildOffice(twoWorkers()).seats.map((s) => s.seat)).toEqual([...SEAT_ORDER]);
  });

  it("stands a returned seat beside the desk with the warning mark", () => {
    const seat = buildOffice(twoWorkers()).seats.find((s) => s.seat === "inspector")!;
    expect(seat.pose).toBe("standing");
    expect(seat.warning).toBe(true);
  });

  it("seats a waiting seat still, without a mark or a label", () => {
    const seat = buildOffice(twoWorkers()).seats.find((s) => s.seat === "lead")!;
    expect(seat.pose).toBe("seated");
    expect(seat.warning).toBe(false);
    expect(seat.label).toBeNull();
  });

  it("names role and state in the aria-label", () => {
    const office = buildOffice(twoWorkers());
    expect(office.seats.find((s) => s.seat === "worker-1")!.ariaLabel).toBe("Çalışan 1, çalışıyor");
    expect(office.seats.find((s) => s.seat === "inspector")!.ariaLabel).toBe("Denetleyici, döndü");
    expect(office.seats.find((s) => s.seat === "lead")!.ariaLabel).toBe("Hakim, bekliyor");
  });

  it("gives the owner seat the count of waiting approvals", () => {
    const office = buildOffice(twoWorkers());
    const owner = office.seats.find((s) => s.seat === "owner")!;
    expect(owner.pose).toBe("seated");
    expect(owner.badge).toBe("2");
    expect(office.approvals.map((a) => [a.title, a.gate])).toEqual([
      ["Fikir bir", "Fikir onayı"],
      ["Yayın iki", "Yayın onayı"],
    ]);
  });

  it("always says tahmini with the dollars", () => {
    expect(buildOffice(twoWorkers()).topBar.estimated).toBe("tahmini $3.50");
    const zero = twoWorkers();
    zero.cycle.estimated_usd = 0;
    expect(buildOffice(zero).topBar.estimated).toBe("tahmini $0.00");
  });

  it("words the usage limit as Açık / Bekliyor <saat> / Durdu", () => {
    const v = twoWorkers();
    expect(buildOffice(v).topBar.limit).toBe("Açık");
    v.cycle.usage_limit = { state: "stopped", resets_at: null };
    expect(buildOffice(v).topBar.limit).toBe("Durdu");
    v.cycle.usage_limit = { state: "waiting", resets_at: "2026-10-01T15:30:00Z" };
    expect(buildOffice(v).topBar.limit).toMatch(/^Bekliyor \d\d:\d\d$/);
  });

  it("shows an idle office without a cycle", () => {
    const v = twoWorkers();
    v.cycle = {
      ...v.cycle,
      cycle_id: null,
      started_at: null,
      running: false,
      running_agents: 0,
      estimated_usd: 0,
    };
    const top = buildOffice(v).topBar;
    expect(top.cycleId).toBe("döngü yok");
    expect(top.runningAgents).toBe("koşan ajan 0/6");
  });
});

describe("the seat panel", () => {
  it("shows role, card, branch and the first 12 characters of the sha", () => {
    const panel = buildPanel(twoWorkers(), "worker-1")!;
    expect(panel.role).toBe("Çalışan 1");
    expect(panel.task).toMatchObject({
      title: "Birinci iş",
      state: "implementing",
      goal: "birinci hedef",
      acceptance: "birinci kabul",
    });
    expect(panel.branch).toBe("team/cycle/worker-one");
    expect(panel.sha).toBe("0123456789ab");
    expect(panel.shaFull).toBe("0123456789abcdef0123456789abcdef01234567");
  });

  it("caps the report at 40 lines", () => {
    const panel = buildPanel(twoWorkers(), "worker-1")!;
    expect(panel.reportLines).toHaveLength(40);
    expect(panel.reportLines[39]).toBe("satır 40");
  });

  it("has no card for the owner and none for a seat without a task", () => {
    expect(buildPanel(twoWorkers(), "owner")!.task).toBeNull();
    expect(buildPanel(twoWorkers(), "lead")!.task).toBeNull();
    expect(buildPanel(twoWorkers(), "nobody")).toBeNull();
  });

  it("carries the reason of a returned task", () => {
    expect(buildPanel(twoWorkers(), "inspector")!.reason).toBe("testler kırmızı");
  });

  it("selects on click and deselects on a second click", () => {
    expect(selectSeat(null, "lead")).toBe("lead");
    expect(selectSeat("lead", "worker-1")).toBe("worker-1");
    expect(selectSeat("lead", "lead")).toBeNull();
  });
});
