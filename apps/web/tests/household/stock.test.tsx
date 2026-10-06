/**
 * 'Ev stoğu' (home-stock-list): the view rendered with fixtures, its buttons pressed through the
 * element tree, the real client driven with the session stubbed, and the client's field list
 * read against the server's `_view` so the two halves cannot drift.
 *
 * No DOM here (vitest runs in node): the markup is asserted through react-dom/server.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import HouseholdView, {
  EMPTY_LIST,
  EMPTY_STOCK,
  entryText,
  type HouseholdViewProps,
} from "../../app/household/HouseholdView";
import {
  addToList,
  fetchHousehold,
  removeFromList,
  setLevel,
  type Household,
  type HouseholdItem,
} from "../../app/household/householdApi";

type Props = Record<string, unknown> & { children?: ReactNode };

function item(over: Partial<HouseholdItem>): HouseholdItem {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    name: "tuvalet kağıdı",
    level: "azaldı",
    on_list: true,
    list_quantity: null,
    usual_quantity: null,
    updated_at: "2026-10-05T09:00:00+00:00",
    depleted_at: "2026-10-05T09:00:00+00:00",
    restocked_at: null,
    cycle_days: null,
    predicted_out_at: null,
    ...over,
  };
}

const PAPER = item({});
const PASTA = item({
  id: "00000000-0000-0000-0000-000000000002",
  name: "makarna",
  level: null,
  list_quantity: "iki paket",
});
const SOAP = item({
  id: "00000000-0000-0000-0000-000000000003",
  name: "sabun",
  level: "var",
  on_list: false,
  cycle_days: 21,
});

function household(over: Partial<Household> = {}): Household {
  return {
    items: [PAPER, PASTA, SOAP],
    list: [PAPER, PASTA],
    soon: [SOAP],
    speech: "Listede 2 şey var: tuvalet kağıdı (azaldı) ve iki paket makarna.",
    ...over,
  };
}

function props(over: Partial<HouseholdViewProps> = {}): HouseholdViewProps {
  return {
    household: household(),
    error: null,
    notice: null,
    busy: false,
    draftName: "",
    draftQuantity: "",
    onDraftName: () => {},
    onDraftQuantity: () => {},
    onAdd: () => {},
    onLevel: () => {},
    onRemove: () => {},
    onForget: () => {},
    ...over,
  };
}

function html(over: Partial<HouseholdViewProps> = {}): string {
  return renderToStaticMarkup(<HouseholdView {...props(over)} />);
}

/** Every host element in the tree, function components expanded (they hold no hooks). */
function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  if (typeof node.type === "function") {
    return elements((node.type as (p: Props) => ReactNode)(node.props));
  }
  return [node, ...elements(node.props.children)];
}

function buttons(over: Partial<HouseholdViewProps>, attr: string, value: string) {
  return elements(<HouseholdView {...props(over)} />).filter(
    (el) => el.type === "button" && el.props[attr] === value,
  );
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

describe("the household view", () => {
  it("lists what to buy, low and out marked, with the quantity", () => {
    const markup = html();
    expect(markup).toContain("tuvalet kağıdı (azaldı)");
    expect(markup).toContain("iki paket makarna");
    expect(markup.indexOf("tuvalet kağıdı (azaldı)")).toBeLessThan(markup.indexOf("iki paket makarna"));
    expect(markup).toContain('id="household-soon"');
    expect(markup).toContain("genelde 21 günde bir biter");
  });

  it("says when the list and the stock are empty, and shows no soon section", () => {
    const markup = html({ household: household({ items: [], list: [], soon: [] }) });
    expect(markup).toContain(EMPTY_LIST);
    expect(markup).toContain(EMPTY_STOCK.replaceAll("'", "&#x27;"));
    expect(markup).not.toContain('id="household-soon"');
  });

  it("shows the server's refusal and a loading line, never an empty success", () => {
    expect(html({ household: null, error: "Ürünün adı boş olamaz." })).toContain(
      'role="alert">Ürünün adı boş olamaz.',
    );
    expect(html({ household: null })).toContain("yükleniyor");
  });

  it("presses: bought sets var by name, remove by id, a level button by name", () => {
    const onLevel = vi.fn();
    const onRemove = vi.fn();
    (buttons({ onLevel }, "data-household-action", "bought")[0].props.onClick as () => void)();
    expect(onLevel).toHaveBeenCalledWith("tuvalet kağıdı", "var");
    (buttons({ onRemove }, "data-household-action", "remove")[1].props.onClick as () => void)();
    expect(onRemove).toHaveBeenCalledWith(PASTA.id);
    const out = buttons({ onLevel }, "data-household-level", "bitti");
    expect(out).toHaveLength(3);
    (out[2].props.onClick as () => void)();
    expect(onLevel).toHaveBeenLastCalledWith("sabun", "bitti");
  });

  it("adds the trimmed draft, and cannot add an empty one", () => {
    const onAdd = vi.fn();
    expect(buttons({ onAdd }, "data-household-action", "add")[0].props.disabled).toBe(true);
    const add = buttons({ onAdd, draftName: "  süt ", draftQuantity: "2 litre" }, "data-household-action", "add")[0];
    expect(add.props.disabled).toBe(false);
    (add.props.onClick as () => void)();
    expect(onAdd).toHaveBeenCalledWith("süt", "2 litre");
  });

  it("marks the level the item has", () => {
    const pressed = elements(<HouseholdView {...props()} />).filter(
      (el) => el.type === "button" && el.props["aria-pressed"] === true,
    );
    expect(pressed.map((el) => el.props["data-household-level"])).toEqual(["azaldı", "var"]);
    expect(entryText(SOAP)).toBe("sabun");
  });
});

describe("the household client", () => {
  it("reads /v1/household", async () => {
    apiFetch.mockResolvedValueOnce(json(200, household()));
    const result = await fetchHousehold();
    expect(apiFetch).toHaveBeenCalledWith("/v1/household");
    expect(result.ok && result.household.list.map((i) => i.name)).toEqual(["tuvalet kağıdı", "makarna"]);
  });

  it("posts a level and a list entry with the server's own sentence back", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { item: PAPER, speech: "Tamam, tuvalet kağıdı azaldı; listeye ekledim." }));
    const level = await setLevel("tuvalet kağıdı", "azaldı");
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/household/items");
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ name: "tuvalet kağıdı", level: "azaldı" });
    expect(level).toEqual({ ok: true, message: "Tamam, tuvalet kağıdı azaldı; listeye ekledim." });

    apiFetch.mockResolvedValueOnce(json(200, { item: PASTA, speech: "Listeye ekledim: makarna.", already: false }));
    await addToList("makarna", "  ");
    expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({ name: "makarna", quantity: null });
  });

  it("surfaces a refusal with the server's sentence", async () => {
    apiFetch.mockResolvedValueOnce(
      json(422, { detail: { code: "household_refused", message: "Seviye 'var', 'azaldı' ya da 'bitti' olmalı." } }),
    );
    expect(await setLevel("süt", "var")).toEqual({
      ok: false,
      code: "household_refused",
      message: "Seviye 'var', 'azaldı' ya da 'bitti' olmalı.",
    });
    apiFetch.mockResolvedValueOnce(json(404, { detail: { code: "not_found", message: "Bu ürün yok; silinmiş olabilir." } }));
    const gone = await removeFromList("x/y");
    expect(apiFetch.mock.calls[1][0]).toBe("/v1/household/list/x%2Fy");
    expect(gone.ok).toBe(false);
  });
});

describe("the contract with routes.py", () => {
  it("the client's item fields are exactly the server's _view keys", () => {
    const routes = readFileSync(
      fileURLToPath(new URL("../../../../services/api/app/household/routes.py", import.meta.url)),
      "utf8",
    );
    const body = routes.slice(routes.indexOf("def _view("), routes.indexOf("def _refused("));
    const serverKeys = [...body.matchAll(/^\s+"([a-z_]+)":/gm)].map((m) => m[1]).toSorted();
    expect(serverKeys).toEqual(Object.keys(PAPER).toSorted());
  });
});
