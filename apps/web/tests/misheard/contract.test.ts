/**
 * The two halves of the misheard notebook read each other: every path the client calls is a
 * route in services/api/app/voice/misheard/routes.py with the same method, and every item
 * field the page reads is a column of services/api/app/voice/misheard/models.py.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import {
  answerMeaning,
  fetchNotebook,
  forgetAll,
  forgetOne,
} from "../../app/core/misheard/misheardApi";

const ROOT = join(__dirname, "..", "..", "..", "..");
const API = join(ROOT, "services", "api", "app", "voice", "misheard");
const WEB = join(__dirname, "..", "..", "app", "core", "misheard");

const fetchMock = vi.mocked(apiFetch);

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => new Response("{}", { status: 200 }));
});

describe("the client's paths", () => {
  it("are the routes' paths, with the routes' methods", async () => {
    const routes = readFileSync(join(API, "routes.py"), "utf8");
    const MARK = "ITEM-ID";
    await fetchNotebook();
    await answerMeaning(MARK, "x");
    await forgetOne(MARK);
    await forgetAll();
    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [path, init] of fetchMock.mock.calls) {
      const method = (init?.method ?? "GET").toLowerCase();
      const route = path.replace(MARK, "{item_id}");
      expect(routes, `${method} ${route}`).toContain(`@router.${method}("${route}")`);
    }
  });
});

describe("the item fields the page reads", () => {
  it("are columns of the table", () => {
    const models = readFileSync(join(API, "models.py"), "utf8");
    const columns = new Set([...models.matchAll(/^ {4}(\w+): Mapped\[/gm)].map((match) => match[1]));
    expect(columns.size).toBeGreaterThan(10);
    const read = new Set<string>();
    for (const file of ["misheardModel.ts", "MisheardView.tsx", "page.tsx"]) {
      const source = readFileSync(join(WEB, file), "utf8");
      for (const match of source.matchAll(/\bitem\.(\w+)/g)) read.add(match[1]);
    }
    expect(read.size).toBeGreaterThan(5);
    for (const field of read) expect(columns, field).toContain(field);
  });

  it("and the item type names exactly the table's columns", () => {
    const models = readFileSync(join(API, "models.py"), "utf8");
    const columns = [...models.matchAll(/^ {4}(\w+): Mapped\[/gm)].map((match) => match[1]).toSorted();
    const client = readFileSync(join(WEB, "misheardApi.ts"), "utf8");
    const type = client.slice(client.indexOf("export type MisheardItem"), client.indexOf("};", client.indexOf("export type MisheardItem")));
    const fields = [...type.matchAll(/^ {2}(\w+): /gm)].map((match) => match[1]).toSorted();
    expect(fields).toEqual(columns);
  });
});
