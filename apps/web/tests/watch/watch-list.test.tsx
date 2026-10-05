/**
 * The 'Nöbetler' section of /routines: the owner's own watches, beside his routines.
 *
 * Three halves, the same shape as the misheard notebook's tests: the client against a mocked
 * `apiFetch` (so a path or a method that drifts from `/v1/watches` shows up here), the
 * Turkish sentences the rows are made of, and the view rendered with react-dom/server with
 * its buttons pressed through the element tree (vitest runs in node, no DOM).
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

import { WatchListView, type WatchListViewProps } from "../../app/routines/WatchList";
import {
  EMPTY_SENTENCE,
  addOne,
  buildCondition,
  conditionSentence,
  domainOf,
  fetchWatches,
  forgetEverything,
  intervalSentence,
  lastReadingSentence,
  loadFailedSentence,
  removeOne,
  type Watch,
} from "../../app/lib/watch/watches";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function watch(over: Partial<Watch> = {}): Watch {
  return {
    id: "w1",
    label: "Telefon fiyatı",
    url: "https://www.example.com/urun/42",
    condition: "number_below:20000",
    every_hours: 6,
    selector: null,
    created_at: "2026-10-04T08:00:00+00:00",
    last_read_at: "2026-10-04T09:30:00+00:00",
    last_outcome: "same",
    last_value: 21499,
    consecutive_failures: 0,
    ...over,
  };
}

const THREE: Watch[] = [
  watch(),
  watch({ id: "w2", label: "Duyuru", url: "https://duyuru.example.org/", condition: "changed", every_hours: 24, last_outcome: "changed" }),
  watch({
    id: "w3",
    label: "Sürüm notları",
    url: "https://example.net/releases",
    condition: "contains:kararlı sürüm",
    last_outcome: "unreadable",
    last_value: null,
    consecutive_failures: 3,
  }),
];

beforeEach(() => {
  apiFetch.mockReset();
});

// ------------------------------------------------------------------ the client

describe("the /v1/watches client", () => {
  it("lists the watches with the contract's fields", async () => {
    apiFetch.mockResolvedValue(json(200, { items: THREE }));
    const result = await fetchWatches();
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/watches");
    expect(result.ok && result.items.map((row) => row.id)).toEqual(["w1", "w2", "w3"]);
    expect(result.ok && result.items[0]).toEqual(THREE[0]);
  });

  it("Kaldır sends DELETE /v1/watches/{id} and the row leaves", async () => {
    apiFetch.mockResolvedValue(json(200, { deleted: 1 }));
    const next = await removeOne(THREE, "w2");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/watches/w2");
    expect(apiFetch.mock.calls[0][1]).toMatchObject({ method: "DELETE" });
    expect(next.items.map((row) => row.id)).toEqual(["w1", "w3"]);
    expect(next.notice).toBe("“Duyuru” nöbeti kaldırıldı.");
  });

  it("a refused removal keeps the row and says the server's sentence", async () => {
    apiFetch.mockResolvedValue(
      json(404, { detail: { code: "not_found", message: "Bu nöbet yok; silinmiş olabilir." } }),
    );
    const next = await removeOne(THREE, "w2");
    expect(next.items).toHaveLength(3);
    expect(next.notice).toBe("Bu nöbet yok; silinmiş olabilir.");
  });

  it("'Hepsini unut' sends ONE DELETE /v1/watches and says how many were deleted", async () => {
    apiFetch.mockResolvedValue(json(200, { deleted: 3 }));
    const next = await forgetEverything();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/watches");
    expect(apiFetch.mock.calls[0][1]).toMatchObject({ method: "DELETE" });
    expect(next.items).toEqual([]);
    expect(next.notice).toBe("3 nöbet unutuldu; okumalarıyla birlikte silindi.");
  });

  it("adds a watch with the contract's fields and the condition built from the form", async () => {
    apiFetch.mockResolvedValue(json(201, watch({ id: "w9", last_read_at: null, last_outcome: null })));
    const next = await addOne([], {
      url: " https://www.example.com/urun/42 ",
      label: "",
      kind: "number_below",
      value: "20.000",
      every_hours: "12",
    });
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe("/v1/watches");
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse(init.body as string)).toEqual({
      url: "https://www.example.com/urun/42",
      // An empty name is the page's domain, not a refusal the owner has to read.
      label: "example.com",
      condition: "number_below:20.000",
      every_hours: 12,
    });
    expect(next.refusal).toBeNull();
    expect(next.items.map((row) => row.id)).toEqual(["w9"]);
  });

  it("a WatchRefused answer comes back as its reason_tr, and nothing is added", async () => {
    apiFetch.mockResolvedValue(
      json(422, {
        detail: {
          code: "watch_refused",
          message: "Bu adres izlenemez: yalnız herkese açık http(s) sayfalar izlenir.",
        },
      }),
    );
    const next = await addOne(THREE, {
      url: "http://192.168.1.1/",
      label: "Modem",
      kind: "changed",
      value: "",
      every_hours: "6",
    });
    expect(next.refusal).toBe("Bu adres izlenemez: yalnız herkese açık http(s) sayfalar izlenir.");
    expect(next.items).toHaveLength(3);
  });

  it("a failed list is one Turkish line, with the server's sentence when it gave one", async () => {
    apiFetch.mockResolvedValue(
      json(503, { detail: { code: "unavailable", message: "Veritabanı şu an yanıt vermiyor." } }),
    );
    const result = await fetchWatches();
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(loadFailedSentence(result)).toBe(
      "Nöbetler okunamadı: Veritabanı şu an yanıt vermiyor.",
    );
  });

  it("a network error is a failed list too, never a throw into the page", async () => {
    apiFetch.mockRejectedValue(new TypeError("Failed to fetch"));
    const result = await fetchWatches();
    expect(result.ok).toBe(false);
  });
});

// --------------------------------------------------------------- the sentences

describe("a watch, in Turkish", () => {
  it.each([
    ["number_below:20000", "20.000'in altına inerse"],
    ["number_below:20.000", "20.000'in altına inerse"],
    ["number_above:150", "150'yi geçerse"],
    ["number_above:19,5", "19,5'i geçerse"],
    ["number_below:19.99", "19,99'un altına inerse"],
    ["changed", "değişince"],
    ["contains:kararlı sürüm", "'kararlı sürüm' geçince"],
  ])("%s -> %s", (condition, sentence) => {
    expect(conditionSentence(condition)).toBe(sentence);
  });

  it("an unknown condition is shown as it is, never guessed", () => {
    expect(conditionSentence("sometime:later")).toBe("sometime:later");
  });

  it("builds the wire condition from the form", () => {
    expect(buildCondition("changed", "ne olursa")).toBe("changed");
    expect(buildCondition("contains", "  kararlı sürüm ")).toBe("contains:kararlı sürüm");
    expect(buildCondition("number_above", "1.299,90")).toBe("number_above:1.299,90");
  });

  it("the interval and the domain", () => {
    expect(intervalSentence(1)).toBe("saatte bir");
    expect(intervalSentence(6)).toBe("6 saatte bir");
    expect(intervalSentence(24)).toBe("günde bir");
    expect(intervalSentence(168)).toBe("haftada bir");
    expect(domainOf("https://www.example.com/urun/42")).toBe("example.com");
    expect(domainOf("bozuk adres")).toBe("bozuk adres");
  });

  it("the last reading: when, and what came of it", () => {
    const tz = "Europe/Istanbul";
    expect(lastReadingSentence(watch(), tz)).toBe("Son okuma 4 Eki 12:30: değişmemiş (21.499).");
    expect(lastReadingSentence(watch({ last_outcome: "condition_met", last_value: 19900 }), tz)).toBe(
      "Son okuma 4 Eki 12:30: koşul gerçekleşti (19.900).",
    );
    expect(lastReadingSentence(watch({ last_read_at: null, last_outcome: null }), tz)).toBe(
      "Henüz okunmadı; ilk okuma birkaç dakika içinde.",
    );
  });

  it("an unreadable one says why in plain Turkish", () => {
    const tz = "Europe/Istanbul";
    expect(lastReadingSentence(THREE[2], tz)).toBe(
      "Son okuma 4 Eki 12:30: okunamadı, art arda 3 kez (sayfa açılmadı ya da aranan yer bulunamadı).",
    );
    // When the Cloud Core states the reading's own reason, that is what is said.
    expect(
      lastReadingSentence({ ...THREE[2], consecutive_failures: 1, last_reason: "Sayfada sayı bulunamadı." }, tz),
    ).toBe("Son okuma 4 Eki 12:30: okunamadı (Sayfada sayı bulunamadı.).");
  });
});

// -------------------------------------------------------------------- the view

type Props = Record<string, unknown> & { children?: ReactNode };

function props(over: Partial<WatchListViewProps> = {}): WatchListViewProps {
  return {
    items: THREE,
    error: null,
    notice: null,
    refusal: null,
    busy: false,
    draft: { url: "", label: "", kind: "changed", value: "", every_hours: "6" },
    onDraft: () => {},
    onAdd: () => {},
    onRemove: () => {},
    onForgetAll: () => {},
    timeZone: "Europe/Istanbul",
    ...over,
  };
}

function html(over: Partial<WatchListViewProps> = {}): string {
  return renderToStaticMarkup(<WatchListView {...props(over)} />);
}

function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  if (typeof node.type === "function") {
    return elements((node.type as (p: Props) => ReactNode)(node.props));
  }
  return [node, ...elements(node.props.children)];
}

function text(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement<Props>(node)) return text(node.props.children);
  return "";
}

function buttons(over: Partial<WatchListViewProps> = {}): ReactElement<Props>[] {
  return elements(<WatchListView {...props(over)} />).filter((el) => el.type === "button");
}

describe("the Nöbetler list", () => {
  it("each row: label, domain, condition in Turkish, interval, last reading", () => {
    const page = html();
    expect(page).toContain("Telefon fiyatı");
    expect(page).toContain("example.com");
    expect(page).toContain("20.000&#x27;in altına inerse");
    expect(page).toContain("değişince");
    expect(page).toContain("&#x27;kararlı sürüm&#x27; geçince");
    expect(page).toContain("6 saatte bir");
    expect(page).toContain("günde bir");
    expect(page).toContain("Son okuma 4 Eki 12:30: değişmemiş (21.499).");
    expect(page).toContain("okunamadı, art arda 3 kez");
    expect(page.match(/data-watch-row=/g)).toHaveLength(3);
  });

  it("empty: the voice sentence that makes one", () => {
    expect(EMPTY_SENTENCE).toBe('Henüz nöbet yok. Sesle: "şu değişince bana söyle".');
    const page = html({ items: [] });
    expect(page).toContain("Henüz nöbet yok. Sesle: &quot;şu değişince bana söyle&quot;.");
    expect(page).not.toContain("data-watch-row=");
  });

  it("a refusal stands beside the form, in the server's words", () => {
    const page = html({ refusal: "En çok 20 nöbet tutulabilir; önce birini kaldır." });
    expect(page).toMatch(/<form[\s\S]*En çok 20 nöbet tutulabilir; önce birini kaldır\.[\s\S]*<\/form>/);
  });

  it("a failed list is one line, and the form still renders", () => {
    const page = html({ items: null, error: "Nöbetler okunamadı: HTTP 503" });
    expect(page).toContain("Nöbetler okunamadı: HTTP 503");
    expect(page.match(/Nöbetler okunamadı/g)).toHaveLength(1);
    expect(page).toContain("<form");
  });

  it("Kaldır presses with its own row's id", () => {
    const onRemove = vi.fn();
    const remove = buttons({ onRemove }).filter((el) => text(el.props.children) === "Kaldır");
    expect(remove).toHaveLength(3);
    (remove[1].props.onClick as () => void)();
    expect(onRemove).toHaveBeenCalledWith("w2");
  });

  it("'Hepsini unut' is one button, off when there is nothing to forget", () => {
    const onForgetAll = vi.fn();
    const forget = buttons({ onForgetAll }).filter((el) => text(el.props.children) === "Hepsini unut");
    expect(forget).toHaveLength(1);
    (forget[0].props.onClick as () => void)();
    expect(onForgetAll).toHaveBeenCalledTimes(1);
    const empty = buttons({ items: [] }).find((el) => text(el.props.children) === "Hepsini unut");
    expect(empty?.props.disabled).toBe(true);
  });

  it("the value box is off for 'değişince', which needs no value", () => {
    const boxes = elements(<WatchListView {...props()} />).filter(
      (el) => el.type === "input" && el.props["aria-label"] === "Değer",
    );
    expect(boxes).toHaveLength(1);
    expect(boxes[0].props.disabled).toBe(true);
  });
});

// -------------------------------------------------------------------- the page

describe("/routines carries the section", () => {
  it("the page mounts the Nöbetler list beside the routines, and holds no request of its own", () => {
    const page = readFileSync(
      fileURLToPath(new URL("../../app/routines/page.tsx", import.meta.url)),
      "utf8",
    );
    expect(page).toContain("<WatchList");
    expect(page).toContain("<RoutinesPanel");
    expect(page).not.toMatch(/method:\s*"(POST|DELETE)"/);
  });
});
