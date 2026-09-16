/**
 * B14 req 295: the Rutinler panel.
 *
 * The Routine Engine has been complete since M18 with no surface at all — no panel, no
 * voice tool, nothing but `/v1/routines` and a clock. Every one of the seven routines in
 * production was created by the alarm subsystem on the owner's behalf, and nothing showed
 * them that, so "what does this system do on its own?" had no answer short of reading the
 * database.
 *
 * Most of what is tested here is the TRANSLATION: a trigger is stored as data and the owner
 * asked for it in words. Showing "hafta içi 07:15" when the row says `[0,1,2,3,4] 07:15` is
 * the whole job; showing it wrong means the owner cannot tell whether the system misheard
 * them or the panel is lying.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { RoutinesPanel } from "../../app/core/panels/CockpitPanels";
import {
  ROUTINE_ROWS_SHOWN,
  controlFor,
  needsAttention,
  routinesBadge,
  sourceLabel,
  statusLabel,
  triggerPhrase,
} from "../../app/lib/cockpit/routine-rows";
import {
  ROUTINES_PATH,
  type RoutineClient,
  type RoutineRow,
  fetchRoutines,
  parseRoutine,
  routinePausePath,
  routineResumePath,
} from "../../app/lib/cockpit/routines";
import {
  ROUTINE_CONTROL_IDLE,
  type RoutineControlState,
  runRoutineControl,
} from "../../app/lib/cockpit/useRoutineControl";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  apiFetch.mockReset();
});

const ROW: RoutineRow = {
  routine_id: "r1",
  name: "Sabah rutini",
  status: "armed",
  trigger_kind: "schedule",
  trigger: { weekdays: [0, 1, 2, 3, 4], time: "07:15", timezone: "Europe/Istanbul" },
  created_at: "2026-09-01T05:00:00Z",
  paused_at: null,
  pause_reason: null,
  last_condition_met: false,
  last_condition_at: null,
  source: "voice",
};

function row(patch: Partial<RoutineRow> = {}): RoutineRow {
  return { ...ROW, ...patch };
}

// --------------------------------------------------------------- the translation

describe("when a routine runs, in the owner's words", () => {
  it("names the weekday sets the owner actually says", () => {
    expect(triggerPhrase("schedule", { weekdays: [0, 1, 2, 3, 4], time: "07:15" })).toBe(
      "hafta içi 07:15",
    );
    expect(triggerPhrase("schedule", { weekdays: [5, 6], time: "10:00" })).toBe(
      "hafta sonu 10:00",
    );
    expect(triggerPhrase("schedule", { weekdays: [0, 1, 2, 3, 4, 5, 6], time: "08:00" })).toBe(
      "her gün 08:00",
    );
  });

  it("spells out an arbitrary set rather than calling it something it is not", () => {
    expect(triggerPhrase("schedule", { weekdays: [0, 3], time: "06:45" })).toBe(
      "pazartesi, perşembe 06:45",
    );
  });

  it("says a condition trigger in minutes, because that is how it was asked for", () => {
    expect(triggerPhrase("condition", { kind: "device_idle", min_seconds: 600 })).toBe(
      "bilgisayar 10 dakika boşta kalınca",
    );
  });

  it("does not invent a sentence for a shape it does not know", () => {
    expect(triggerPhrase("telepathy", {})).toBe("telepathy");
    expect(triggerPhrase("schedule", {})).toBe("?");
    expect(triggerPhrase(null, {})).toBe("tetikleyici bildirilmedi");
  });

  it("says who set it up, so the owner can tell their own from the alarm's", () => {
    expect(sourceLabel("voice")).toBe("sesle kuruldu");
    expect(sourceLabel("alarm")).toBe("alarmın kendi rutini");
    expect(sourceLabel("something-new")).toBe("something-new");
    expect(sourceLabel(null)).toBeNull();
  });

  it("shows an unknown status verbatim rather than dropping it", () => {
    expect(statusLabel("armed")).toBe("çalışıyor");
    expect(statusLabel("quarantined")).toBe("quarantined");
  });
});

// ------------------------------------------------------------------ the controls

describe("which control a row offers", () => {
  it("is Duraklat while it runs and Devam ettir while it is paused", () => {
    expect(controlFor(row())?.action).toBe("pause");
    expect(controlFor(row({ status: "paused" }))?.action).toBe("resume");
  });

  it("is nothing at all once the routine is over", () => {
    // A button that would 409 is a button that teaches the owner not to trust buttons.
    expect(controlFor(row({ status: "completed" }))).toBeNull();
    expect(controlFor(row({ status: "cancelled" }))).toBeNull();
  });
});

describe("the badge and the border", () => {
  it("shows how many are running out of how many there are", () => {
    expect(routinesBadge([row(), row({ status: "paused" })])).toBe("1/2");
    expect(routinesBadge([row(), row()])).toBe("2");
    expect(routinesBadge([])).toBeNull();
  });

  it("draws attention to a paused routine, because that is the state a list can hide", () => {
    expect(needsAttention([row(), row({ status: "paused" })])).toBe(true);
    expect(needsAttention([row(), row()])).toBe(false);
  });
});

// -------------------------------------------------------------------- the client

describe("the client", () => {
  it("reads the routines route", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { routines: [ROW] }));

    const state = await fetchRoutines();

    expect(apiFetch).toHaveBeenCalledWith(ROUTINES_PATH);
    if (state.kind === "ok") expect(state.value[0]?.name).toBe("Sabah rutini");
  });

  it("says the route is absent rather than empty when this Cloud Core has none", async () => {
    apiFetch.mockResolvedValueOnce(json(404, { detail: "not found" }));

    expect((await fetchRoutines()).kind).toBe("absent");
  });

  it("drops a row with no id, because that is not a routine", () => {
    expect(parseRoutine({ name: "Sabah" })).toBeNull();
  });

  it("names a routine with no name rather than rendering nothing", () => {
    expect(parseRoutine({ routine_id: "r9" })?.name).toBe("(adsız)");
  });

  it("posts pause and resume to that routine's own paths", () => {
    expect(routinePausePath("r1")).toBe("/v1/routines/r1/pause");
    expect(routineResumePath("r1")).toBe("/v1/routines/r1/resume");
  });
});

// ------------------------------------------------------------ the control's two rules

function ports(client: RoutineClient) {
  let state = ROUTINE_CONTROL_IDLE;
  let refreshed = 0;
  return {
    ports: {
      client,
      read: () => state,
      write: (next: RoutineControlState) => {
        state = next;
      },
      onSettled: () => {
        refreshed += 1;
      },
    },
    refreshes: () => refreshed,
    state: () => state,
  };
}

describe("pausing from the panel", () => {
  it("calls pause for pause and resume for resume, and nothing else", async () => {
    const client: RoutineClient = { pause: vi.fn(async () => null), resume: vi.fn(async () => null) };
    const harness = ports(client);

    await runRoutineControl(harness.ports, "pause", "r1");
    await runRoutineControl(harness.ports, "resume", "r1");

    expect(client.pause).toHaveBeenCalledWith("r1");
    expect(client.resume).toHaveBeenCalledWith("r1");
  });

  it("runs one at a time", async () => {
    const client: RoutineClient = { pause: vi.fn(async () => null), resume: vi.fn(async () => null) };
    const harness = ports(client);
    harness.ports.write({ busyId: "r2", error: null });

    expect(await runRoutineControl(harness.ports, "pause", "r1")).toBe(false);
    expect(client.pause).not.toHaveBeenCalled();
  });

  it("says a failure rather than leaving the row looking paused", async () => {
    // The consequence of swallowing this is a routine that runs tomorrow morning when the
    // owner believes they turned it off.
    const client: RoutineClient = {
      pause: vi.fn(async () => {
        throw new Error("HTTP 409");
      }),
      resume: vi.fn(async () => null),
    };
    const harness = ports(client);

    await runRoutineControl(harness.ports, "pause", "r1");

    expect(harness.state()).toEqual({ busyId: null, error: "HTTP 409" });
    expect(harness.refreshes()).toBe(0);
  });
});

// --------------------------------------------------------------------- the panel

type ElementLike = { type: unknown; props: Record<string, unknown> };

function isElement(node: unknown): node is ElementLike {
  return !!node && typeof node === "object" && "props" in node && "type" in node;
}

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

const IDLE_CONTROL = { busyId: null, error: null, onControl: () => {} };

function loaded(rows: RoutineRow[]) {
  return { kind: "ok" as const, value: rows, at: 0 };
}

describe("the panel", () => {
  it("shows what each routine is and when it runs", () => {
    const html = renderToStaticMarkup(
      <RoutinesPanel state={loaded([row()])} control={IDLE_CONTROL} />,
    );

    expect(html).toContain("Sabah rutini");
    expect(html).toContain("hafta içi 07:15");
    expect(html).toContain("sesle kuruldu");
  });

  it("says there are none on its own page, and takes no slot on the cockpit", () => {
    // B24 req 714: an owner with no routines does not need a cockpit panel telling them
    // so thirteen times over; on /routines the sentence is what they came to read.
    expect(renderToStaticMarkup(<RoutinesPanel state={loaded([])} control={IDLE_CONTROL} />)).toBe("");

    const page = renderToStaticMarkup(
      <RoutinesPanel state={loaded([])} control={IDLE_CONTROL} always />,
    );
    expect(page).toContain("Kurulu rutin yok.");
  });

  it("says it could not find out rather than showing an empty list", () => {
    const html = renderToStaticMarkup(
      <RoutinesPanel state={{ kind: "failed", error: "boom" }} control={IDLE_CONTROL} />,
    );

    expect(html).toContain("Alınamadı");
    expect(html).not.toContain("Kurulu rutin yok.");
  });

  it("asks for exactly the routine that was clicked, with the right action", () => {
    const asked: Array<[string, string]> = [];
    const tree = (
      <RoutinesPanel
        state={loaded([row(), row({ routine_id: "r2", status: "paused" })])}
        control={{ ...IDLE_CONTROL, onControl: (a, id) => asked.push([a, id]) }}
      />
    );

    const button = findByData(tree, "data-routine-target", "r2");
    expect(button).not.toBeNull();
    (button!.props.onClick as () => void)();

    expect(asked).toEqual([["resume", "r2"]]);
  });

  it("offers nothing on a routine that is over", () => {
    const tree = (
      <RoutinesPanel
        state={loaded([row({ routine_id: "r3", status: "cancelled" })])}
        control={IDLE_CONTROL}
      />
    );

    expect(findByData(tree, "data-routine-target", "r3")).toBeNull();
  });

  it("presses nothing while another call is in flight", () => {
    const tree = (
      <RoutinesPanel state={loaded([row()])} control={{ ...IDLE_CONTROL, busyId: "r1" }} />
    );

    expect(findByData(tree, "data-routine-target", "r1")?.props.disabled).toBe(true);
  });

  it("shows the failure where the owner is looking", () => {
    const html = renderToStaticMarkup(
      <RoutinesPanel state={loaded([row()])} control={{ ...IDLE_CONTROL, error: "HTTP 409" }} />,
    );

    expect(html).toContain("HTTP 409");
  });

  it("shows no more rows than it says it does", () => {
    const many = Array.from({ length: ROUTINE_ROWS_SHOWN + 4 }, (_, i) =>
      row({ routine_id: `r${i}` }),
    );
    const html = renderToStaticMarkup(
      <RoutinesPanel state={loaded(many)} control={IDLE_CONTROL} />,
    );

    expect(html.split("data-routine-id=").length - 1).toBe(ROUTINE_ROWS_SHOWN);
  });
});
