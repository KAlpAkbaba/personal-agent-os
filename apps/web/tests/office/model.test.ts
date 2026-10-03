import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  TASK_STATE_TR,
  buildOffice,
  buildPanel,
  selectSeat,
  taskStateText,
} from "../../app/core/office/officeModel";
import { SEAT_ORDER, busyCycle, twoWorkers } from "./fixtures";

/** The queue's states, read from team/queue.schema.json at the repository root. */
function queueStates(): string[] {
  const schema = JSON.parse(readFileSync(resolve(__dirname, "../../../../team/queue.schema.json"), "utf8"));
  return schema.$defs.task.properties.state.enum as string[];
}

describe("the task's state in Turkish", () => {
  it("has a phrase for every state of the queue's schema", () => {
    const states = queueStates();
    expect(states.length).toBeGreaterThanOrEqual(13);
    for (const state of states) {
      expect(Object.hasOwn(TASK_STATE_TR, state), state).toBe(true);
      expect(taskStateText(state), state).not.toBe(state);
      expect(taskStateText(state), state).not.toContain("_");
    }
  });

  it("does not give the same phrase to states that mean different things to the owner", () => {
    const four = ["in_progress", "inspecting", "returned", "stopped"].map(taskStateText);
    expect(new Set(four).size).toBe(4);
    const all = queueStates().map(taskStateText);
    expect(new Set(all).size).toBe(all.length);
  });

  it("returns a state it does not know unchanged, prototype names included", () => {
    for (const unknown of ["brand_new_state", "", "constructor", "toString"])
      expect(taskStateText(unknown)).toBe(unknown);
  });
});

describe("the office model", () => {
  it("draws two working workers typing with their task titles and 2/6 in the top bar", () => {
    const office = buildOffice(twoWorkers());
    const w1 = office.seats.find((s) => s.seat === "worker-1")!;
    const w2 = office.seats.find((s) => s.seat === "worker-2")!;
    expect([w1.pose, w2.pose]).toEqual(["typing", "typing"]);
    expect([w1.label, w2.label]).toEqual(["Birinci iş", "İkinci iş"]);
    expect(office.topBar.runningAgents).toBe("koşan ajan 2/6");
  });

  it("keeps the seats in the order the API sends them", () => {
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

const names = (view: ReturnType<typeof busyCycle>) => buildOffice(view).seats.map((s) => s.name);

describe("the seats the API sends", () => {
  it("draws four worker desks, Çalışan 1 to Çalışan 4, in an office with no run", () => {
    expect(names(busyCycle(0, 0))).toEqual([
      "Hakim",
      "Araştırmacı",
      "Entegratör",
      "Çalışan 1",
      "Çalışan 2",
      "Çalışan 3",
      "Çalışan 4",
      "Denetleyici",
      "Sahip",
    ]);
    expect(buildOffice(twoWorkers()).seats.find((s) => s.seat === "worker-4")!.ariaLabel).toBe(
      "Çalışan 4, bekliyor",
    );
  });

  it("draws a fifth and a sixth worker desk when the cycle runs that many", () => {
    const office = buildOffice(busyCycle(6, 0));
    expect(office.seats).toHaveLength(11);
    expect(office.seats.slice(3, 9).map((s) => [s.name, s.pose])).toEqual(
      [1, 2, 3, 4, 5, 6].map((n) => [`Çalışan ${n}`, "typing"]),
    );
    expect(office.seats.every((s) => !s.plain)).toBe(true);
  });

  it("says 5/6 for four workers and one inspection, from the API's own numbers", () => {
    expect(buildOffice(busyCycle(4, 1)).topBar.runningAgents).toBe("koşan ajan 5/6");
    expect(buildOffice(busyCycle(5, 2)).topBar.runningAgents).toBe("koşan ajan 7/7");
  });

  it("marks a seat with three runs ×3, labelled with the first task's title", () => {
    const seat = buildOffice(busyCycle(0, 3)).seats.find((s) => s.seat === "inspector")!;
    expect(seat.runCount).toBe("×3");
    expect(seat.label).toBe("Denetim 1");
    expect(seat.ariaLabel).toBe("Denetleyici, çalışıyor, 3 koşu");
  });

  it("gives a seat with one run, or none, no run count", () => {
    const office = buildOffice(busyCycle(4, 1));
    expect(office.seats.map((s) => s.runCount)).toEqual(office.seats.map(() => null));
    expect(office.seats.find((s) => s.seat === "inspector")!.ariaLabel).toBe(
      "Denetleyici, çalışıyor",
    );
    // an answer from before `runs` existed draws as it always did
    expect(buildOffice(twoWorkers()).seats.map((s) => s.runCount)).toEqual(
      SEAT_ORDER.map(() => null),
    );
  });

  it("draws a seat id it does not know as a plain desk named by its id", () => {
    const view = busyCycle(1, 0);
    view.agents.splice(3, 0, {
      seat: "auditor-2",
      role: "auditor",
      state: "working",
      task_id: "t-one",
      task_title: "Birinci iş",
      since: null,
    });
    view.agents.push({ ...view.agents[0], seat: "constructor", state: "waiting" });
    const office = buildOffice(view);
    const unknown = office.seats.find((s) => s.seat === "auditor-2")!;
    expect([unknown.name, unknown.plain, unknown.label]).toEqual(["auditor-2", true, "Birinci iş"]);
    expect(unknown.ariaLabel).toBe("auditor-2, çalışıyor");
    expect(office.seats.find((s) => s.seat === "constructor")!.name).toBe("constructor");
    expect(office.seats.filter((s) => s.plain).map((s) => s.seat)).toEqual([
      "auditor-2",
      "constructor",
    ]);
    expect(buildPanel(view, "auditor-2")!.role).toBe("auditor-2");
    expect(buildPanel(view, "auditor-2")!.task?.title).toBe("Birinci iş");
  });

  it("does not take worker-0, worker-x or a padded number for a worker seat", () => {
    const view = busyCycle(0, 0);
    for (const seat of ["worker-0", "worker-x", "worker-04", "worker-", "xworker-1", "worker-1d"]) {
      view.agents = [{ ...view.agents[0], seat }];
      const drawn = buildOffice(view).seats[0];
      expect([drawn.name, drawn.plain], seat).toEqual([seat, true]);
    }
  });

  it("names a worker seat past the ninth by its whole number", () => {
    const view = busyCycle(0, 0);
    view.agents = ["worker-10", "worker-12"].map((seat) => ({ ...view.agents[0], seat }));
    expect(names(view)).toEqual(["Çalışan 10", "Çalışan 12"]);
  });
});

describe("the panel of a seat with several runs", () => {
  it("lists every run with its title and start time, the first one's card below", () => {
    const panel = buildPanel(busyCycle(0, 3), "inspector")!;
    expect(panel.runs.map((r) => r.title)).toEqual(["Denetim 1", "Denetim 2", "Denetim 3"]);
    for (const run of panel.runs) expect(run.since).toMatch(/^\d\d:\d\d$/);
    expect(new Set(panel.runs.map((r) => r.since)).size).toBe(3);
    expect(panel.task?.goal).toBe("ilk denetimin hedefi");
    expect(panel.branch).toBe("team/cycle/inspect-one");
  });

  it("names a run without a title by its task id", () => {
    const view = busyCycle(0, 2);
    const inspector = view.agents.find((a) => a.seat === "inspector")!;
    inspector.runs![1].task_title = null;
    expect(buildPanel(view, "inspector")!.runs[1].title).toBe("t-inspector-2");
  });

  it("lists nothing for a seat with one run or with none", () => {
    expect(buildPanel(busyCycle(4, 1), "inspector")!.runs).toEqual([]);
    expect(buildPanel(busyCycle(4, 1), "worker-4")!.runs).toEqual([]);
    expect(buildPanel(twoWorkers(), "worker-1")!.runs).toEqual([]);
    expect(buildPanel(busyCycle(0, 3), "owner")!.runs).toEqual([]);
  });
});

describe("the seat panel", () => {
  it("shows role, card, branch and the first 12 characters of the sha", () => {
    const panel = buildPanel(twoWorkers(), "worker-1")!;
    expect(panel.role).toBe("Çalışan 1");
    expect(panel.task).toMatchObject({
      title: "Birinci iş",
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

  it("gives the panel the task's state in Turkish and its start time", () => {
    const panel = buildPanel(twoWorkers(), "worker-1")!;
    expect(panel.task?.stateText).toBe("implementing"); // not a queue state: shown as it is
    expect(panel.task?.since).toMatch(/^\d\d:\d\d$/);
    expect(buildPanel(twoWorkers(), "inspector")!.task?.stateText).toBe(
      taskStateText("returned"),
    );
    expect(buildPanel(twoWorkers(), "inspector")!.task?.since).toBeNull();
  });

  it("selects on click and deselects on a second click", () => {
    expect(selectSeat(null, "lead")).toBe("lead");
    expect(selectSeat("lead", "worker-1")).toBe("worker-1");
    expect(selectSeat("lead", "lead")).toBeNull();
  });
});
