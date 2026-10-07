/**
 * The /voice/measure page, rendered with fixtures.
 *
 * No DOM (vitest runs in node): markup through react-dom/server, and a PRESS is the
 * button's own onClick taken from the rendered element tree - the view is hook-free and
 * `bindView` is the same wiring the page ships, so the press reaches the real session,
 * which reaches the real client over a mocked apiFetch.
 */

import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import MeasureView, { bindView } from "../../app/voice/measure/MeasureView";
import type { Measurement } from "../../app/lib/voice/measure/api";
import { CHROME_NOT_RUN_TR, LIVE_REASON_TR } from "../../app/lib/voice/measure/model";
import { MeasureSession } from "../../app/lib/voice/measure/session";
import {
  FakeClock,
  FakeMicrophone,
  MADE_UP,
  fakeCapture,
  item,
  measurement,
  sentences,
  settle,
  threeDone,
} from "./fixtures";

type Props = Record<string, unknown> & { children?: ReactNode };

const fetchMock = vi.mocked(apiFetch);

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function calls(): Array<{ path: string; method: string }> {
  return fetchMock.mock.calls.map(([path, init]) => ({ path, method: init?.method ?? "GET" }));
}

function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  if (typeof node.type === "function") return elements((node.type as (p: Props) => ReactNode)(node.props));
  return [node, ...elements(node.props.children)];
}

function text(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement<Props>(node)) {
    if (typeof node.type === "function") return text((node.type as (p: Props) => ReactNode)(node.props));
    return text(node.props.children);
  }
  return "";
}

function buttons(all: ReactElement<Props>[], label: string): ReactElement<Props>[] {
  return all.filter((element) => element.type === "button" && text(element.props.children) === label);
}

function row(all: ReactElement<Props>[], index: number): ReactElement<Props> {
  const found = all.find((element) => element.props["data-measure-row"] === index);
  if (!found) throw new Error(`no row ${index}`);
  return found;
}

function press(button: ReactElement<Props> | undefined): void {
  if (!button) throw new Error("no such button");
  (button.props.onClick as () => void)();
}

async function loaded(data: Measurement, live = false) {
  const microphone = new FakeMicrophone();
  const session = new MeasureSession({
    recorder: () => ({ microphone, capture: fakeCapture().factory, recognizer: null, clock: new FakeClock() }),
    mic: () => ({}),
    live: () => live,
  });
  fetchMock.mockResolvedValueOnce(json(200, data));
  await session.load();
  const view = () => <MeasureView {...bindView(session, session.getSnapshot(), live)} />;
  return {
    session,
    microphone,
    tree: () => elements(view()),
    html: () => renderToStaticMarkup(view()),
    all: () => text(view()),
  };
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("what is shown comes from the server", () => {
  it("shows the fixture's own sentence - nothing is typed into the page", async () => {
    const page = await loaded(measurement());
    const current = page.tree().find((element) => element.props["data-measure-current"] !== undefined);
    expect(current && text(current)).toContain(MADE_UP);
    expect(page.all()).toContain("1 / 20");
  });

  it("thirty sentences from the server (the owner's twenty and ten commands) are all shown, 1 / 30", async () => {
    const page = await loaded(measurement({ sentences: sentences(30) }));
    expect(page.all()).toContain("1 / 30");
    expect(page.all()).toContain("Uydurma deneme cümlesi numara 30.");
    expect(row(page.tree(), 30)).toBeDefined();
    expect(page.all()).toContain("Yalnız bu 30 cümle kaydedilir");
  });

  it("with three of twenty done for 'ev' the fourth is current and the counter says 4 / 20", async () => {
    const page = await loaded(threeDone());
    const current = page.tree().find((element) => element.props["data-measure-current"] !== undefined);
    expect(current?.props["data-measure-current"]).toBe(4);
    expect(text(current)).toContain("Uydurma deneme cümlesi numara 4.");
    expect(page.all()).toContain("4 / 20");
    expect(page.html().match(/data-measure-done="true"/g)).toHaveLength(3);
  });

  it("switching to 'ofis' shows that place's own progress", async () => {
    const page = await loaded(threeDone());
    press(buttons(page.tree(), "Ofis")[0]);
    expect(page.session.getSnapshot().place).toBe("ofis");
    expect(page.all()).toContain("2 / 20");
    expect(page.html().match(/data-measure-done="true"/g)).toHaveLength(1);
  });

  it("the retention sentence carries the server's number", async () => {
    const seven = await loaded(measurement({ retention_days: 7 }));
    expect(seven.all()).toMatch(/7 gün/);
    expect(seven.all()).not.toMatch(/30 gün/);
    const thirty = await loaded(measurement());
    expect(thirty.all()).toMatch(/30 gün/);
  });

  it("says what Chrome wrote, or that it did not run", async () => {
    const page = await loaded(threeDone());
    expect(text(row(page.tree(), 1))).toContain("uydurma deneme");
    expect(text(row(page.tree(), 2))).toContain("Chrome çalışmadı");
    expect(text(row(page.tree(), 1))).toContain("2,4 sn");
  });
});

describe("one microphone", () => {
  it("recording is disabled with its Turkish reason while the voice session is live", async () => {
    const page = await loaded(threeDone(), true);
    const [start] = buttons(page.tree(), "Kaydı başlat");
    expect(start.props.disabled).toBe(true);
    expect(page.all()).toContain(LIVE_REASON_TR);
    await page.session.start();
    expect(page.microphone.opens).toHaveLength(0);
  });
});

describe("presses reach the Cloud Core", () => {
  it("'Kaydı başlat' then 'Bitir' uploads the current sentence and the next one comes by itself", async () => {
    const page = await loaded(threeDone());
    press(buttons(page.tree(), "Kaydı başlat")[0]);
    await settle();
    fetchMock
      .mockResolvedValueOnce(json(200, item("ev", 4)))
      .mockResolvedValueOnce(json(200, threeDone({ recordings: [...threeDone().recordings, item("ev", 4)] })));
    press(buttons(page.tree(), "Bitir")[0]);
    await settle(60);
    expect(calls()[1]).toEqual({ path: "/v1/voice/measurement/recordings/ev/4", method: "PUT" });
    expect(page.all()).toContain("5 / 20");
    expect(page.microphone.closes).toBe(1);
  });

  it("'Tekrar' on sentence 2 sends a PUT for index 2", async () => {
    const page = await loaded(threeDone());
    press(buttons(elements(row(page.tree(), 2)), "Tekrar")[0]);
    await settle();
    fetchMock.mockResolvedValueOnce(json(200, item("ev", 2))).mockResolvedValueOnce(json(200, threeDone()));
    press(buttons(page.tree(), "Bitir")[0]);
    await settle(60);
    expect(calls()[1]).toEqual({ path: "/v1/voice/measurement/recordings/ev/2", method: "PUT" });
  });

  it("'Sil' sends that recording's DELETE", async () => {
    const page = await loaded(threeDone());
    fetchMock.mockResolvedValueOnce(json(200, { deleted: 1 })).mockResolvedValueOnce(json(200, threeDone()));
    press(buttons(elements(row(page.tree(), 3)), "Sil")[0]);
    await settle(60);
    expect(calls()[1]).toEqual({ path: "/v1/voice/measurement/recordings/ev/3", method: "DELETE" });
  });

  it("'Ölçüm kayıtlarını sil' sends ONE DELETE with no dialog and then shows the deleted count", async () => {
    const confirm = vi.fn(() => true);
    vi.stubGlobal("confirm", confirm);
    const page = await loaded(threeDone());
    fetchMock.mockResolvedValueOnce(json(200, { deleted: 4 })).mockResolvedValueOnce(json(200, measurement()));
    press(buttons(page.tree(), "Ölçüm kayıtlarını sil")[0]);
    await settle(60);
    const deletes = calls().filter((call) => call.method === "DELETE");
    expect(deletes).toEqual([{ path: "/v1/voice/measurement/recordings", method: "DELETE" }]);
    expect(confirm).not.toHaveBeenCalled();
    expect(page.all()).toContain("4 ölçüm kaydı silindi.");
    vi.unstubAllGlobals();
  });

  it("a refused upload shows the server's sentence and the sentence stays not done", async () => {
    const page = await loaded(threeDone());
    press(buttons(page.tree(), "Kaydı başlat")[0]);
    await settle();
    fetchMock
      .mockResolvedValueOnce(json(422, { detail: { code: "wav_empty", message: "Kayıt boş; cümleyi yeniden oku." } }))
      .mockResolvedValueOnce(json(200, threeDone()));
    press(buttons(page.tree(), "Bitir")[0]);
    await settle(60);
    expect(page.all()).toContain("Kayıt boş; cümleyi yeniden oku.");
    expect(row(page.tree(), 4).props["data-measure-done"]).toBe(false);
    expect(page.all()).toContain("4 / 20");
  });

  it("a take Chrome did not hear says so in one sentence", async () => {
    const page = await loaded(threeDone());
    press(buttons(page.tree(), "Kaydı başlat")[0]);
    await settle();
    fetchMock
      .mockResolvedValueOnce(json(200, item("ev", 4, { browser_transcript: null, browser_engine: null })))
      .mockResolvedValueOnce(json(200, threeDone()));
    press(buttons(page.tree(), "Bitir")[0]);
    await settle(60);
    expect(page.all()).toContain(CHROME_NOT_RUN_TR);
  });
});
