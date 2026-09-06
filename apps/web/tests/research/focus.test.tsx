/**
 * M18.2 — telling four runs of "OpenAI son gelişmeler" apart, and saying which
 * one the conversation is about.
 *
 * The properties held here are the ones the owner actually hit:
 *
 * · two runs with the same title are two visibly different rows, because the
 *   identity line is built from the run's own facts and not from its name;
 * · a fact the server did not report is missing from the line rather than
 *   invented;
 * · the focus chip is placed by `current.research_job_id` — never by topic,
 *   and never by a flag the list route left behind;
 * · clicking a row (or a clarification candidate) POSTs that row's id;
 * · a 409 says the report is not finished; a 404 says this Cloud Core has no
 *   focus at all, once, and changes nothing else.
 *
 * `react-dom/server` in Node. No browser: rows are "clicked" by taking the
 * handler off the rendered element tree and calling it, which is exactly what
 * a click would do.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { ResearchPanel } from "../../app/core/panels/CockpitPanels";
import type { Loaded, ResearchTask } from "../../app/lib/cockpit/api";
import { selectResearchFocus, setResearchFocus } from "../../app/lib/research/api";
import {
  FOCUS_NOT_COMPLETED,
  FOCUS_UNSUPPORTED,
  type FocusEntry,
  type FocusState,
  identityLine,
  parseFocusState,
} from "../../app/lib/research/focus";
import type { ResearchTaskSummary } from "../../app/lib/research/model";
import FocusStrip from "../../app/research/FocusStrip";
import TaskList from "../../app/research/TaskList";

// 2026-09-07T18:00Z is 21:00 in Europe/Istanbul, so "today" is the 7th.
const NOW = Date.parse("2026-09-07T18:00:00Z");

const SAME_TITLE = "OpenAI son gelişmeler";

/** Two runs of the same topic, plus one still running with less reported. */
const ROWS: ResearchTaskSummary[] = [
  {
    task_id: "task-1",
    topic: SAME_TITLE,
    status: "READY",
    stage: "ready",
    device: null,
    created_at: "2026-09-07T14:00:00Z",
    ready_at: "2026-09-07T17:19:00Z",
    completed_at: "2026-09-07T17:19:00Z",
    mode: "quick",
    source_count: 5,
    artifact_id: "art-1",
  },
  {
    task_id: "task-2",
    topic: SAME_TITLE,
    status: "READY",
    stage: "ready",
    device: null,
    created_at: "2026-09-06T10:00:00Z",
    ready_at: "2026-09-06T16:40:00Z",
    completed_at: "2026-09-06T16:40:00Z",
    mode: "deep",
    source_count: 18,
    artifact_id: "art-2",
  },
  {
    // An older Cloud Core's row: no mode, no source count, not finished.
    task_id: "task-3",
    topic: "Sadece konu",
    status: "RUNNING",
    stage: "fetching",
    device: null,
    created_at: "2026-09-07T17:50:00Z",
  },
];

const ENTRY_2: FocusEntry = {
  research_job_id: "task-2",
  artifact_id: "art-2",
  topic: SAME_TITLE,
  completed_at: "2026-09-06T16:40:00Z",
  mode: "deep",
  source_count: 18,
  status: "READY",
  source_of_focus: "owner_selected_in_ui",
  selected_at: "2026-09-07T17:55:00Z",
};

const ENTRY_1: FocusEntry = {
  research_job_id: "task-1",
  artifact_id: "art-1",
  topic: SAME_TITLE,
  completed_at: "2026-09-07T17:19:00Z",
  mode: "quick",
  source_count: 5,
  status: "READY",
  source_of_focus: "research_just_completed",
  selected_at: "2026-09-07T17:19:30Z",
};

const FOCUS: FocusState = {
  current: ENTRY_2,
  previous: ENTRY_1,
  stack: [ENTRY_2, ENTRY_1],
  pending_clarification: null,
};

const ok = (value: FocusState): Loaded<FocusState> => ({ kind: "ok", value, at: 0 });
const noop = () => {};

function list(focus: Loaded<FocusState>, tasks: ResearchTaskSummary[] = ROWS): string {
  return renderToStaticMarkup(
    <TaskList tasks={tasks} focus={focus} now={NOW} onOpen={noop} />,
  );
}

// ------------------------------------------------------- element-tree clicks

type ElementLike = { type: unknown; props: Record<string, unknown> };

function isElement(node: unknown): node is ElementLike {
  return typeof node === "object" && node !== null && "props" in node && "type" in node;
}

/**
 * Find the rendered element carrying `attr={value}`, expanding function
 * components by calling them. These are pure presentational components with
 * no hooks, so calling them is exactly what React would do.
 */
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

function click(node: ElementLike | null): void {
  expect(node).not.toBeNull();
  const handler = node?.props.onClick as (() => void) | undefined;
  expect(typeof handler).toBe("function");
  handler?.();
}

async function flush(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  apiFetch.mockReset();
});

// --------------------------------------------------------- the identity line

describe("identity line", () => {
  it("renders the time, mode, source count and state the row reported", () => {
    const html = list(ok(FOCUS));
    expect(html).toContain("Bugün 20:19 · QUICK · 5 kaynak · hazır");
    expect(html).toContain("Dün 19:40 · DEEP · 18 kaynak · hazır");
  });

  it("omits a field the row did not report instead of inventing one", () => {
    const html = list(ok(FOCUS));
    // task-3 has no mode and no source count; it gets neither, and certainly
    // not a fabricated "0 kaynak".
    expect(html).toContain("Bugün 20:50 · çalışıyor");
    expect(identityLine(ROWS[2], NOW)).toBe("Bugün 20:50 · çalışıyor");
    expect(identityLine(ROWS[2], NOW)).not.toContain("kaynak");
    // A reported zero is a fact and stays.
    expect(identityLine({ ...ROWS[2], source_count: 0 }, NOW)).toContain("0 kaynak");
  });

  it("keeps two runs of the same title apart", () => {
    const html = list(ok(FOCUS));
    expect(html.match(new RegExp(SAME_TITLE, "g"))?.length).toBeGreaterThanOrEqual(2);
    expect(html).toContain('data-research-task="task-1"');
    expect(html).toContain('data-research-task="task-2"');
    // The distinguishing fact is the second line, and the two differ.
    expect(identityLine(ROWS[0], NOW)).not.toBe(identityLine(ROWS[1], NOW));
    // The id is never printed as text — it only ever rides an attribute.
    expect(html).not.toContain(">task-1<");
    expect(html).not.toContain(">task-2<");
  });

  it("dates anything older than yesterday rather than saying Bugün", () => {
    const old = { ...ROWS[0], completed_at: "2026-09-03T17:19:00Z" };
    expect(identityLine(old, NOW)).toBe("3 Eyl 20:19 · QUICK · 5 kaynak · hazır");
    const lastYear = { ...ROWS[0], completed_at: "2025-12-31T17:19:00Z" };
    expect(identityLine(lastYear, NOW)).toContain("2025");
  });
});

// ----------------------------------------------------------- the focus chip

describe("the focus chip", () => {
  it("lands on the row whose task id is current.research_job_id, not on the title", () => {
    const html = list(ok(FOCUS));
    expect(html.match(/data-focus="current"/g)?.length).toBe(1);
    expect(html).toMatch(/data-research-task="task-2"[^>]*data-focus="current"/);
    expect(html).toMatch(/data-research-task="task-2"[^>]*aria-current="true"/);
    expect(html).toContain("Konuşma odağı");
    // The other row shares the topic exactly and is NOT the focus.
    expect(html).toMatch(/data-research-task="task-1"[^>]*data-focus="previous"/);
  });

  it("prefers the focus route over a stale is_focus flag on the row", () => {
    const stale = [{ ...ROWS[0], is_focus: true }, ROWS[1], ROWS[2]];
    const html = list(ok(FOCUS), stale);
    expect(html.match(/data-focus="current"/g)?.length).toBe(1);
    expect(html).toMatch(/data-research-task="task-2"[^>]*data-focus="current"/);
  });

  it("marks no row at all when there is no focus", () => {
    const html = list(ok(parseFocusState({})));
    expect(html).not.toContain('data-focus="current"');
    expect(html).not.toContain("Konuşma odağı");
  });
});

// ---------------------------------------------------------------- the strip

describe("the focus strip", () => {
  it("states the focus, its identity and how it was set", () => {
    const html = renderToStaticMarkup(
      <FocusStrip state={ok(FOCUS)} now={NOW} onSelect={noop} />,
    );
    expect(html).toContain("Konuşma odağı:");
    expect(html).toContain(`${SAME_TITLE} · Dün 19:40 · DEEP · 18 kaynak · hazır`);
    expect(html).toContain("siz seçtiniz");
    expect(html).toContain("Önceki odak:");
    expect(html).toContain("Bugün 20:19 · QUICK · 5 kaynak · hazır");
  });

  it("names each way the focus can have been set, in Turkish", () => {
    const phrases: Record<string, string> = {
      research_just_completed: "araştırma tamamlandığında",
      owner_selected_in_ui: "siz seçtiniz",
      owner_selected_by_voice: "sesle seçildi",
      followup_reference: "önceki araştırmaya dönüldü",
    };
    for (const [source, phrase] of Object.entries(phrases)) {
      const html = renderToStaticMarkup(
        <FocusStrip
          state={ok({ ...FOCUS, previous: null, current: { ...ENTRY_2, source_of_focus: source } })}
          now={NOW}
          onSelect={noop}
        />,
      );
      expect(html).toContain(phrase);
    }
  });

  it("says there is no focus rather than leaving the strip blank", () => {
    const html = renderToStaticMarkup(
      <FocusStrip state={ok(parseFocusState({}))} now={NOW} onSelect={noop} />,
    );
    expect(html).toContain("Konuşma odağı yok.");
  });
});

// -------------------------------------------------------------- selection

describe("selecting a report", () => {
  it("POSTs the clicked row's id, and only that id", async () => {
    apiFetch
      .mockResolvedValueOnce(json(200, { focus: ENTRY_2 }))
      .mockResolvedValueOnce(json(200, { current: ENTRY_2, previous: null, stack: [] }));

    const tree = (
      <TaskList
        tasks={ROWS}
        focus={ok(FOCUS)}
        now={NOW}
        onOpen={(taskId) => {
          void selectResearchFocus(taskId);
        }}
      />
    );
    click(findByData(tree, "data-research-task", "task-2"));
    await flush();

    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/research/task-2/focus");
    expect(init.method).toBe("POST");
    // …then the focus is read back, on the same refresh path as everything else.
    expect(apiFetch.mock.calls[1]?.[0]).toBe("/v1/research/focus");
  });

  it("escapes the id it was given rather than building the path by hand", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { focus: ENTRY_2 }));
    await setResearchFocus("a b/c");
    expect(apiFetch.mock.calls[0]?.[0]).toBe("/v1/research/a%20b%2Fc/focus");
  });

  it("says the report is not finished when the API answers 409", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { error: "not_completed" }));
    const selection = await selectResearchFocus("task-3");
    expect(selection.notice).toBe(FOCUS_NOT_COMPLETED);
    expect(selection.focus).toBeNull();
    // Only the POST happened: there is no point reading a focus that was refused.
    expect(apiFetch).toHaveBeenCalledTimes(1);

    const html = renderToStaticMarkup(
      <FocusStrip state={ok(FOCUS)} now={NOW} notice={selection.notice} onSelect={noop} />,
    );
    expect(html).toContain("data-focus-notice");
    expect(html).toContain("Bu araştırma henüz tamamlanmadı; odak olamaz.");
  });

  it("keeps the API's own Turkish sentence when it sent one", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { detail: "Rapor hâlâ yazılıyor." }));
    const outcome = await setResearchFocus("task-3");
    expect(outcome).toEqual({ kind: "not_completed", detail: "Rapor hâlâ yazılıyor." });
  });
});

// ----------------------------------------------------------- clarification

describe("the pending clarification", () => {
  const CLARIFYING: FocusState = parseFocusState({
    current: null,
    previous: null,
    stack: [],
    pending_clarification: {
      asked_at: "2026-09-07T17:58:00Z",
      question: "Hangi OpenAI son gelişmeler raporunu kastediyorsun?",
      candidates: [ENTRY_1, ENTRY_2],
    },
  });

  it("renders the question and both candidates with their identity lines", () => {
    const html = renderToStaticMarkup(
      <FocusStrip state={ok(CLARIFYING)} now={NOW} onSelect={noop} />,
    );
    expect(html).toContain("Hangi OpenAI son gelişmeler raporunu kastediyorsun?");
    expect(html).toContain('data-focus-candidate="task-1"');
    expect(html).toContain('data-focus-candidate="task-2"');
    expect(html).toContain("Bugün 20:19 · QUICK · 5 kaynak · hazır");
    expect(html).toContain("Dün 19:40 · DEEP · 18 kaynak · hazır");
  });

  it("answers by POSTing the chosen candidate's focus", async () => {
    apiFetch
      .mockResolvedValueOnce(json(200, { focus: ENTRY_2 }))
      .mockResolvedValueOnce(json(200, { current: ENTRY_2, previous: null, stack: [] }));

    const tree = (
      <FocusStrip
        state={ok(CLARIFYING)}
        now={NOW}
        onSelect={(taskId) => {
          void selectResearchFocus(taskId);
        }}
      />
    );
    click(findByData(tree, "data-focus-candidate", "task-2"));
    await flush();

    expect(apiFetch.mock.calls[0]?.[0]).toBe("/v1/research/task-2/focus");
  });
});

// ------------------------------------------------------- an older Cloud Core

describe("a Cloud Core without the focus routes", () => {
  it("turns a 404 on the POST into the absent state, not an error", async () => {
    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    const selection = await selectResearchFocus("task-2");
    expect(selection.notice).toBeNull();
    expect(selection.focus).toEqual({ kind: "absent", detail: FOCUS_UNSUPPORTED });
  });

  it("says it once above the list and changes nothing else", () => {
    const strip = renderToStaticMarkup(
      <FocusStrip state={{ kind: "absent", detail: FOCUS_UNSUPPORTED }} now={NOW} onSelect={noop} />,
    );
    expect(strip).toContain("Bu Cloud Core sürümünde odak yok.");
    expect(strip.match(/Bu Cloud Core sürümünde odak yok\./g)?.length).toBe(1);
    expect(strip).not.toContain("Konuşma odağı");
    expect(strip).not.toContain("data-focus-notice");

    const html = list({ kind: "absent", detail: FOCUS_UNSUPPORTED });
    expect(html).not.toContain('data-focus="current"');
    expect(html).not.toContain("Konuşma odağı");
    // The rows themselves are untouched.
    expect(html).toContain("Bugün 20:19 · QUICK · 5 kaynak · hazır");
    expect(html).toContain('data-research-task="task-2"');
  });

  it("distinguishes 'could not find out' from 'has no focus'", () => {
    const html = renderToStaticMarkup(
      <FocusStrip state={{ kind: "failed", error: "HTTP 503" }} now={NOW} onSelect={noop} />,
    );
    expect(html).toContain("Konuşma odağı alınamadı: HTTP 503");
    expect(html).not.toContain(FOCUS_UNSUPPORTED);
  });
});

// ------------------------------------------------------- the cockpit panel

describe("the cockpit's research panel", () => {
  const PANEL_ROWS: ResearchTask[] = ROWS.map((r) => ({
    task_id: r.task_id,
    topic: r.topic,
    status: r.status,
    stage: r.stage,
    device: null,
    created_at: r.created_at ?? null,
    ready_at: r.ready_at ?? null,
    artifact_id: r.artifact_id ?? null,
    mode: r.mode ?? null,
    source_count: r.source_count ?? null,
    completed_at: r.completed_at ?? null,
  }));

  it("carries the same identity line and the same id-placed chip", () => {
    const html = renderToStaticMarkup(
      <ResearchPanel
        state={{ kind: "ok", value: PANEL_ROWS, at: 0 }}
        focus={ok(FOCUS)}
        now={NOW}
        onSelect={noop}
      />,
    );
    expect(html).toContain("Bugün 20:19 · QUICK · 5 kaynak · hazır");
    expect(html).toContain("Dün 19:40 · DEEP · 18 kaynak · hazır");
    expect(html.match(/data-focus="current"/g)?.length).toBe(1);
    expect(html).toMatch(/data-research-task="task-2"[^>]*data-focus="current"/);
    expect(html).toContain("Konuşma odağı: OpenAI son gelişmeler");
    expect(html).toContain("siz seçtiniz");
  });

  it("POSTs the row's id when a row is clicked", async () => {
    apiFetch
      .mockResolvedValueOnce(json(200, { focus: ENTRY_1 }))
      .mockResolvedValueOnce(json(200, { current: ENTRY_1, previous: null, stack: [] }));

    const tree = (
      <ResearchPanel
        state={{ kind: "ok", value: PANEL_ROWS, at: 0 }}
        focus={ok(FOCUS)}
        now={NOW}
        onSelect={(taskId) => {
          void selectResearchFocus(taskId);
        }}
      />
    );
    click(findByData(tree, "data-research-task", "task-1"));
    await flush();
    expect(apiFetch.mock.calls[0]?.[0]).toBe("/v1/research/task-1/focus");
  });

  it("renders the missing-route sentence rather than an empty focus", () => {
    const html = renderToStaticMarkup(
      <ResearchPanel
        state={{ kind: "ok", value: PANEL_ROWS, at: 0 }}
        focus={{ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/research/focus yok (HTTP 404)." }}
        now={NOW}
      />,
    );
    expect(html).toContain("Bu Cloud Core sürümünde odak yok.");
    expect(html).not.toContain('data-focus="current"');
    expect(html).toContain("Bugün 20:19 · QUICK · 5 kaynak · hazır");
  });

  it("stays read-only when no selection handler is given", () => {
    const html = renderToStaticMarkup(
      <ResearchPanel state={{ kind: "ok", value: PANEL_ROWS, at: 0 }} now={NOW} />,
    );
    expect(html).not.toContain('role="button"');
    expect(html).toContain('data-research-task="task-1"');
  });
});

// --------------------------------------------------------------- parsing

describe("parsing the focus payload", () => {
  it("keeps entries addressed by id and drops the ones that are not", () => {
    const state = parseFocusState({
      current: { research_job_id: "task-9", topic: "X", mode: "standard", source_count: 3 },
      previous: { topic: "no id here" },
      stack: [{ research_job_id: "task-8" }, { nothing: true }],
      pending_clarification: null,
    });
    expect(state.current?.research_job_id).toBe("task-9");
    expect(state.previous).toBeNull();
    expect(state.stack.map((e) => e.research_job_id)).toEqual(["task-8"]);
  });

  it("survives a payload the route has not finished shaping", () => {
    expect(parseFocusState(null)).toEqual({
      current: null,
      previous: null,
      stack: [],
      pending_clarification: null,
    });
    expect(parseFocusState({ pending_clarification: {} }).pending_clarification).toBeNull();
  });
});
