import { readFileSync } from "node:fs";
import { join } from "node:path";

import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  MODELS_PATH,
  applySetting,
  putModels,
  type ModelSetting,
  type OfficeView as Office,
} from "../../app/core/office/officeApi";
import { buildOffice, buildPanel, chooseModel } from "../../app/core/office/officeModel";
import OfficeView from "../../app/core/office/OfficeView";
import { modelPolicy } from "./fixtures";

// The model policy's half on the Ofis page (ADR-0214 addendum 7; the contract of addendum 14).

const FABLE = "claude-fable-5-1" as const;
const OPUS = "claude-opus-5-5" as const;
const SONNET = "claude-sonnet-5-5" as const;

type Props = Parameters<typeof OfficeView>[0];

function render(over: Partial<Props> = {}) {
  return renderToStaticMarkup(
    <OfficeView
      view={modelPolicy()}
      selected={null}
      offline={false}
      reducedMotion={false}
      onSelect={() => {}}
      {...over}
    />,
  );
}

const panelOf = (html: string) => html.match(/<aside[\s\S]*?<\/aside>/)?.[0] ?? "";
const topBarOf = (html: string) =>
  html.match(/<div class="office-topbar"[\s\S]*?<\/div>/)?.[0] ?? "";
const selectOf = (html: string) => html.match(/<select[\s\S]*?<\/select>/)?.[0] ?? "";
const optionOf = (html: string, model: string) =>
  html.match(new RegExp(`<option[^>]*value="${model}"[^>]*>[^<]*</option>`))?.[0] ?? "";
const seatHtml = (html: string, seat: string) =>
  html.match(new RegExp(`<button[^>]*data-seat="${seat}"[\\s\\S]*?</button>`))?.[0] ?? "";

/** Expand the element tree (function components called) and find the first match. */
function find(node: ReactNode, match: (el: ReactElement<Record<string, unknown>>) => boolean) {
  const walk = (n: ReactNode): ReactElement<Record<string, unknown>> | null => {
    if (Array.isArray(n)) {
      for (const child of n) {
        const hit = walk(child);
        if (hit) return hit;
      }
      return null;
    }
    if (!isValidElement<Record<string, unknown>>(n)) return null;
    if (typeof n.type === "function") {
      return walk((n.type as (p: unknown) => ReactNode)(n.props));
    }
    if (match(n)) return n;
    return walk(n.props.children as ReactNode);
  };
  return walk(node);
}

/** Two digits of the instant's local hour and minute, read without the page's formatter. */
const pad = (n: number) => String(n).padStart(2, "0");
const localClock = (iso: string) => {
  const at = new Date(iso);
  return `${pad(at.getHours())}:${pad(at.getMinutes())}`;
};

// A block body: an arrow returning the mock would hand vitest a teardown that calls it.
beforeEach(() => {
  apiFetch.mockReset();
});

describe("the model selector in the seat's panel", () => {
  it("renders a select of the three models for a worker seat, the configured one selected", () => {
    const panel = panelOf(render({ selected: "worker-2" }));
    const select = selectOf(panel);
    expect(select.match(/<option/g)).toHaveLength(3);
    expect(optionOf(select, FABLE)).toContain(">Fable 5.1<");
    expect(optionOf(select, OPUS)).toContain(">Opus 5.5<");
    expect(optionOf(select, SONNET)).toContain(">Sonnet 5.5<");
    expect(optionOf(select, OPUS)).toContain('selected=""');
    expect(optionOf(select, FABLE)).not.toContain('selected=""');
    expect(panel).toContain("Bir sonraki koşudan itibaren geçerli.");
  });

  it("says the four worker seats share one role, and three when three are sent", () => {
    expect(panelOf(render({ selected: "worker-1" }))).toContain(
      "Dört çalışan aynı modeli kullanır",
    );
    const view = modelPolicy();
    view.agents = view.agents.filter((a) => a.seat !== "worker-4");
    expect(panelOf(render({ view, selected: "worker-1" }))).toContain(
      "Üç çalışan aynı modeli kullanır",
    );
    expect(panelOf(render({ selected: "lead" }))).not.toContain("aynı modeli kullanır");
  });

  it("shows the role's setting, not the seat's stale model, after a choice", () => {
    const view = modelPolicy();
    view.models!.roles.lead = SONNET;
    view.models!.roles.inspector = SONNET;
    view.models!.roles.worker = SONNET;
    expect(optionOf(selectOf(panelOf(render({ view, selected: "lead" }))), SONNET)).toContain(
      'selected=""',
    );
  });

  it("gives the owner's seat no selector, nor an unknown seat, nor an answer with no setting", () => {
    expect(panelOf(render({ selected: "owner" }))).not.toContain("<select");
    const view = modelPolicy();
    delete view.models;
    expect(panelOf(render({ view, selected: "worker-1" }))).not.toContain("<select");
    expect(buildPanel(modelPolicy(), "owner")!.model).toBeNull();
  });

  it("calls the handler with the seat's role and the chosen model", () => {
    const onChooseModel = vi.fn();
    const tree = OfficeView({
      view: modelPolicy(),
      selected: "worker-3",
      offline: false,
      reducedMotion: false,
      onSelect: () => {},
      onChooseModel,
    });
    const select = find(tree, (el) => el.type === "select");
    expect(select).not.toBeNull();
    (select!.props.onChange as (e: unknown) => void)({ target: { value: SONNET } });
    expect(onChooseModel).toHaveBeenCalledWith("worker", SONNET);
  });

  it("disables, in the inspector's selector, a model weaker than the worker's and says why", () => {
    const panel = panelOf(render({ selected: "inspector" }));
    const select = selectOf(panel);
    expect(optionOf(select, SONNET)).toContain('disabled=""');
    expect(optionOf(select, OPUS)).not.toContain("disabled");
    expect(optionOf(select, FABLE)).not.toContain("disabled");
    expect(panel).toContain("Denetleyici işçiden zayıf modelde koşamaz");
  });

  it("disables, in the worker's selector, a model stronger than the inspector's", () => {
    const view = modelPolicy();
    view.models!.roles.inspector = OPUS;
    const panel = panelOf(render({ view, selected: "worker-1" }));
    const select = selectOf(panel);
    expect(optionOf(select, FABLE)).toContain('disabled=""');
    expect(optionOf(select, SONNET)).not.toContain("disabled");
    expect(panel).toContain("Denetleyici işçiden zayıf modelde koşamaz");
    // nothing to explain when every model is allowed
    expect(panelOf(render({ selected: "lead" }))).not.toContain("zayıf modelde");
  });

  it("refuses the weaker choice in the page before any PUT, and builds the whole setting", () => {
    const setting = modelPolicy().models!;
    expect(chooseModel(setting, "inspector", SONNET)).toEqual({
      refusal: "Denetleyici işçiden zayıf modelde koşamaz",
    });
    expect(chooseModel({ ...setting, roles: { ...setting.roles, inspector: OPUS } }, "worker", FABLE))
      .toEqual({ refusal: "Denetleyici işçiden zayıf modelde koşamaz" });
    const chosen = chooseModel(setting, "researcher", SONNET);
    expect(chosen).toEqual({
      setting: { ...setting, roles: { ...setting.roles, researcher: SONNET } },
    });
    expect(setting.roles.researcher).toBe(OPUS); // the shown setting is not mutated
  });
});

describe("the setting's PUT", () => {
  const setting = (): ModelSetting => ({
    roles: { lead: FABLE, researcher: OPUS, integrator: OPUS, worker: SONNET, inspector: FABLE },
    fallback: true,
    updated_at: "2026-10-01T09:00:00Z",
  });

  it("sends the whole setting to the contract's route and returns the server's document", async () => {
    const stored = { ...setting(), updated_at: "2026-10-03T12:00:00Z" };
    apiFetch.mockResolvedValue(new Response(JSON.stringify(stored), { status: 200 }));
    const answer = await putModels(setting());
    expect(MODELS_PATH).toBe("/v1/team/queue/models");
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe(MODELS_PATH);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual(setting());
    expect(answer).toEqual({ ok: true, setting: stored });
  });

  it("answers a refusal with the server's sentence", async () => {
    const detail = {
      code: "inspector_weaker_than_worker",
      message: "the inspector runs on a weaker model than the worker",
    };
    apiFetch.mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 422 }));
    expect(await putModels(setting())).toEqual({ ok: false, message: detail.message });
    apiFetch.mockResolvedValue(new Response("not json", { status: 503 }));
    expect(await putModels(setting())).toEqual({ ok: false, message: "HTTP 503" });
  });

  it("answers a failed network call with its message", async () => {
    apiFetch.mockRejectedValue(new Error("ağ yok"));
    expect(await putModels(setting())).toEqual({ ok: false, message: "ağ yok" });
  });

  it("shows the choice at once and keeps the server's document when it is stored", async () => {
    const shown: ModelSetting[] = [];
    const said: (string | null)[] = [];
    const stored = { ...setting(), updated_at: "2026-10-03T12:00:00Z" };
    const next = { ...setting(), roles: { ...setting().roles, lead: OPUS } };
    const put = vi.fn().mockResolvedValue({ ok: true, setting: stored });
    await applySetting(setting(), next, {
      put,
      show: (s) => shown.push(s),
      say: (m) => said.push(m),
    });
    expect(put).toHaveBeenCalledWith(next);
    expect(shown).toEqual([next, stored]);
    expect(said.at(-1)).toBeNull();
  });

  it("restores the previous selection with the server's sentence on a refusal", async () => {
    const shown: ModelSetting[] = [];
    const said: (string | null)[] = [];
    const previous = setting();
    const next = { ...previous, roles: { ...previous.roles, inspector: SONNET } };
    await applySetting(previous, next, {
      put: async () => ({ ok: false, message: "the inspector runs on a weaker model" }),
      show: (s) => shown.push(s),
      say: (m) => said.push(m),
    });
    expect(shown).toEqual([next, previous]);
    expect(said.at(-1)).toBe("the inspector runs on a weaker model");
  });

  it("shows the notice in the panel", () => {
    const panel = panelOf(render({ selected: "worker-1", modelNotice: "sunucu reddetti" }));
    expect(panel).toMatch(/role="alert"[^>]*>sunucu reddetti</);
  });
});

describe("the limits and the fallback in the top bar", () => {
  it("renders a null used_pct as bilinmiyor, never %0", () => {
    const bar = topBarOf(render());
    expect(bar).toContain("Fable: bilinmiyor");
    expect(bar).toContain("Tüm modeller: bilinmiyor");
    expect(bar).not.toContain("%0");
    const old = modelPolicy();
    delete old.cycle.limits;
    expect(topBarOf(render({ view: old }))).toContain("Fable: bilinmiyor");
  });

  it("renders a real percentage, a real zero included", () => {
    const view = modelPolicy();
    view.cycle.limits!.fable.used_pct = 42.4;
    view.cycle.limits!.all.used_pct = 0;
    const bar = topBarOf(render({ view }));
    expect(bar).toContain("Fable: %42");
    expect(bar).toContain("Tüm modeller: %0");
  });

  it("renders limited with its reset time in local time", () => {
    const view = modelPolicy();
    view.cycle.limits!.fable = { state: "limited", resets_at: "2026-10-01T13:45:00Z", used_pct: 97 };
    const bar = topBarOf(render({ view }));
    expect(bar).toContain(`Fable: limitte, ${localClock("2026-10-01T13:45:00Z")}`);
    expect(bar).not.toContain("%97");
  });

  it("draws the fallback as a toggle that reflects the setting and flips it", () => {
    expect(topBarOf(render())).toMatch(/<button[^>]*aria-pressed="true"[^>]*>yedek model: açık</);
    const off = modelPolicy();
    off.models!.fallback = false;
    expect(topBarOf(render({ view: off }))).toMatch(
      /<button[^>]*aria-pressed="false"[^>]*>yedek model: kapalı</,
    );
    const onToggleFallback = vi.fn();
    const tree = OfficeView({
      view: off,
      selected: null,
      offline: false,
      reducedMotion: false,
      onSelect: () => {},
      onToggleFallback,
    });
    const toggle = find(tree, (el) => el.type === "button" && "aria-pressed" in el.props);
    (toggle!.props.onClick as () => void)();
    expect(onToggleFallback).toHaveBeenCalledWith({ ...off.models, fallback: true });
  });

  it("shows the newest downgrade as one line with the task's name", () => {
    const view = modelPolicy();
    view.cycle.limits!.lowered = [
      { task: "t-old", role: "worker", from: FABLE, to: OPUS, at: "2026-10-01T10:01:00Z" },
      { task: "t-one", role: "worker", from: OPUS, to: SONNET, at: "2026-10-01T10:02:00Z" },
    ];
    const bar = topBarOf(render({ view }));
    expect(bar).toContain("model düşürüldü: Opus 5.5 → Sonnet 5.5, Birinci iş");
    expect(bar).not.toContain("Eski iş");
    expect(topBarOf(render())).not.toContain("model düşürüldü");
  });

  it("draws a dead cycle's limits as of the status' time, a running one's as the present", () => {
    const live = modelPolicy();
    live.cycle.limits!.fable = { state: "ok", resets_at: null, used_pct: 97 };
    live.cycle.limits!.lowered = [
      { task: "t-one", role: "worker", from: FABLE, to: OPUS, at: "2026-10-01T10:02:00Z" },
    ];
    const dead = structuredClone(live);
    dead.cycle.running = false;
    const [liveBar, deadBar] = [render({ view: live }), render({ view: dead })].map(topBarOf);
    for (const bar of [liveBar, deadBar]) {
      expect(bar).toContain("Fable: %97");
      expect(bar).toContain("model düşürüldü: Fable 5.1 → Opus 5.5, Birinci iş");
    }
    expect(liveBar).not.toContain("itibarıyla");
    expect(deadBar).toMatch(/class="muted"[^>]*>[^<]*itibarıyla/);
    expect(deadBar).toContain(localClock(dead.cycle.updated_at!));
    expect(liveBar).not.toBe(deadBar);
  });
});

describe("a seat whose live run is on another model", () => {
  const lowered = (): Office => {
    const view = modelPolicy();
    view.agents = view.agents.map((a) =>
      a.seat === "worker-1" ? { ...a, running_model: SONNET } : a,
    );
    return view;
  };

  it("says şu an: <model> (düşürüldü) on the seat and in the panel", () => {
    const html = render({ view: lowered(), selected: "worker-1" });
    expect(seatHtml(html, "worker-1")).toContain("şu an: Sonnet 5.5 (düşürüldü)");
    expect(panelOf(html)).toContain("şu an: Sonnet 5.5 (düşürüldü)");
    expect(seatHtml(html, "worker-2")).not.toContain("düşürüldü");
    expect(buildOffice(lowered()).seats.find((s) => s.seat === "worker-1")!.ariaLabel).toContain(
      "düşürüldü",
    );
  });
});

describe("the model policy at phone width", () => {
  const css = readFileSync(join(__dirname, "..", "..", "app", "core", "office", "office.css"), "utf8");
  /** The declarations of the one rule whose selector is exactly `selector`. */
  const block = (selector: string) =>
    css
      .split("}")
      .map((rule) => rule.split("{"))
      .find((parts) => parts.length === 2 && parts[0].trim().split("\n").pop()?.trim() === selector)?.[1] ??
    "";

  it("wraps the top bar's lines and the seat's downgrade, and keeps the select inside the panel", () => {
    expect(block(".office-topbar")).toContain("flex-wrap: wrap");
    expect(block(".office-topbar > span")).toContain("overflow-wrap: anywhere");
    expect(block(".office-lowered")).toContain("overflow-wrap: anywhere");
    expect(block(".office-panel select")).toContain("max-width: 100%");
  });
});
