/**
 * B25 req 702/703/724/725: the search, the palette, and the keyboard that drives them.
 *
 * The audit's lowest score in the whole product was discoverability — 0.5 out of 5 — and
 * the three requirements here are its three halves: a way to find anything by name, a way
 * to reach it without a pointer, and a way into the content that does not cost fourteen
 * tab stops.
 *
 * The keyboard model is tested as a FUNCTION, not by reading the component for the word
 * "ArrowDown". This suite runs in Node with no DOM, and a contract asserted by grepping its
 * own implementation is a spelling check: it passes for a handler wired to nothing. So
 * every key, in every state, goes through `paletteAction` here.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import CommandPalette, {
  KIND_LABEL,
  SECTION_LIMIT,
  focusTargetAfterClose,
  isTypingTarget,
  paletteAction,
  paletteItems,
  search,
} from "../../app/components/CommandPalette";
import { NAV } from "../../app/components/SiteNav";
import { FAMILIES } from "../../app/lib/cockpit/families";
import type { CapabilityList } from "../../app/lib/pages/capabilities";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: () => {} }),
}));

const CAPS: CapabilityList = {
  rows: [
    {
      name: "alarm.snooze",
      family: "alarm",
      familyTr: "Alarmlar",
      summary: "Çalan alarmı ERTELER",
      phrases: ["beş dakika ertele", "on dakika ertele"],
    },
    {
      name: "eye.disable",
      family: "eye",
      familyTr: "Kamera",
      summary: "Active Eye'ı KAPATIR",
      phrases: ["gözünü kapat"],
    },
    {
      name: "memory.why",
      family: "memory",
      familyTr: "Hafıza",
      summary: "Bir kaydı NEDEN hatırladığını anlatır",
      phrases: [],
    },
  ],
  families: [
    { family: "alarm", familyTr: "Alarmlar", count: 1 },
    { family: "eye", familyTr: "Kamera", count: 1 },
    { family: "memory", familyTr: "Hafıza", count: 1 },
  ],
  speech: "Efendim, 3 şey yapabiliyorum.",
};

// --------------------------------------------------------------- what it searches

describe("the palette searches the lists the product already keeps (req 702)", () => {
  it("holds every page, and every panel exactly once", () => {
    const items = paletteItems(null);
    for (const entry of NAV) {
      expect(items.some((item) => item.href === entry.href), entry.href).toBe(true);
    }
    // Two families share the research panel; a second row for it would be a duplicate the
    // owner cannot tell apart.
    const panels = items.filter((item) => item.kind === "panel").map((item) => item.id);
    expect(new Set(panels).size).toBe(panels.length);
    // Every family's panel is here...
    for (const family of FAMILIES) {
      expect(panels, family.key).toContain(`panel:${family.panel}`);
    }
    // ...and exactly one panel belongs to no family: the kill switch (req 660), which has
    // nothing to derive it from and is named in the palette on purpose.
    const familyPanels = new Set(FAMILIES.map((family) => `panel:${family.panel}`));
    expect(panels.filter((id) => !familyPanels.has(id))).toEqual(["panel:panic"]);
  });

  it("holds nothing to say until the capability list has been fetched", () => {
    expect(paletteItems(null).some((item) => item.kind === "say")).toBe(false);
    expect(paletteItems(CAPS).filter((item) => item.kind === "say")).toHaveLength(3);
  });

  it("labels a capability with the owner's own sentence when there is one", () => {
    const items = paletteItems(CAPS);
    const snooze = items.find((item) => item.id === "say:alarm.snooze");
    expect(snooze?.label).toBe("beş dakika ertele");
    expect(snooze?.href).toBe("/voice#cap-alarm-snooze");
    // No quoted example in the description: the summary stands in rather than an invented
    // phrase the assistant was never told to listen for.
    expect(items.find((item) => item.id === "say:memory.why")?.label).toBe(
      "Bir kaydı NEDEN hatırladığını anlatır",
    );
  });

  it("finds a word across all three kinds at once", () => {
    const results = search(paletteItems(CAPS), "alarm");
    const kinds = new Set(results.map((item) => item.kind));
    expect(kinds.has("page")).toBe(true);
    expect(kinds.has("panel")).toBe(true);
    expect(kinds.has("say")).toBe(true);
  });

  it("folds case the Turkish way, in both directions", () => {
    const items = paletteItems(CAPS);
    // The dotted capital İ and the dotless ı are the pair a `toLowerCase()` gets wrong.
    expect(search(items, "HAFIZA").some((item) => item.label === "Hafıza")).toBe(true);
    expect(search(items, "hafıza").some((item) => item.label === "Hafıza")).toBe(true);
    expect(search(items, "GÜVENLİK").some((item) => item.href === "/security")).toBe(true);
  });

  it("keeps the sections in one order and caps each of them", () => {
    const many: CapabilityList = {
      ...CAPS,
      rows: Array.from({ length: SECTION_LIMIT + 5 }, (_, index) => ({
        name: `alarm.tool${index}`,
        family: "alarm",
        familyTr: "Alarmlar",
        summary: "alarm işi",
        phrases: [`alarm komutu ${index}`],
      })),
    };
    const results = search(paletteItems(many), "alarm");
    const kinds = results.map((item) => item.kind);
    expect(kinds).toEqual([...kinds].sort((a, b) => order(a) - order(b)));
    expect(results.filter((item) => item.kind === "say")).toHaveLength(SECTION_LIMIT);
  });

  it("an empty query offers everything rather than nothing", () => {
    expect(search(paletteItems(CAPS), "  ").length).toBeGreaterThan(NAV.length);
  });

  it("a word nothing matches finds nothing, rather than everything", () => {
    expect(search(paletteItems(CAPS), "zzzzz")).toEqual([]);
  });
});

function order(kind: string): number {
  return ["page", "panel", "say"].indexOf(kind);
}

// --------------------------------------------------------------- the keyboard

describe("the keyboard model (req 725)", () => {
  const closed = { open: false, count: 0, active: 0 };
  const openFive = { open: true, count: 5, active: 0 };

  it("Ctrl+K and Cmd+K open it, and close it again", () => {
    expect(paletteAction({ key: "k", ctrlKey: true }, closed)).toEqual({ kind: "open" });
    expect(paletteAction({ key: "K", metaKey: true }, closed)).toEqual({ kind: "open" });
    expect(paletteAction({ key: "k", ctrlKey: true }, openFive)).toEqual({ kind: "close" });
  });

  it("`/` opens it, unless the owner is typing a slash into something", () => {
    expect(paletteAction({ key: "/" }, closed)).toEqual({ kind: "open" });
    expect(paletteAction({ key: "/", typing: true }, closed)).toBeNull();
  });

  it("an ordinary key is not the palette's, so typing still works", () => {
    for (const key of ["a", "Escape", "ArrowDown", "Enter"]) {
      expect(paletteAction({ key }, closed), key).toBeNull();
    }
  });

  it("the arrows move, and the list wraps at both ends", () => {
    expect(paletteAction({ key: "ArrowDown" }, openFive)).toEqual({ kind: "move", active: 1 });
    expect(paletteAction({ key: "ArrowDown" }, { ...openFive, active: 4 })).toEqual({
      kind: "move",
      active: 0,
    });
    // A palette that stops dead at the last row sends the owner back to the pointer.
    expect(paletteAction({ key: "ArrowUp" }, openFive)).toEqual({ kind: "move", active: 4 });
    expect(paletteAction({ key: "ArrowUp" }, { ...openFive, active: 3 })).toEqual({
      kind: "move",
      active: 2,
    });
  });

  it("Enter takes the current row, and never points past the end", () => {
    expect(paletteAction({ key: "Enter" }, { ...openFive, active: 2 })).toEqual({
      kind: "choose",
      index: 2,
    });
    // The query narrowed the list under a selection that was further down.
    expect(paletteAction({ key: "Enter" }, { open: true, count: 2, active: 4 })).toEqual({
      kind: "choose",
      index: 1,
    });
  });

  it("Escape always closes, and an empty list answers nothing else", () => {
    const empty = { open: true, count: 0, active: 0 };
    expect(paletteAction({ key: "Escape" }, empty)).toEqual({ kind: "close" });
    expect(paletteAction({ key: "Enter" }, empty)).toBeNull();
    expect(paletteAction({ key: "ArrowDown" }, empty)).toBeNull();
  });

  it("comes back to a place that still exists", () => {
    // Measured in a browser, not guessed: the element that most often had focus is this
    // component's OWN button, and that button is unmounted while the palette is open.
    // Focusing the remembered node then focused a detached element — which does nothing,
    // and left `document.activeElement` on `<body>` with a keyboard user at the top of the
    // document.
    const live = { isConnected: true, name: "the search box on the page" };
    const gone = { isConnected: false, name: "the open button, now unmounted" };
    const fresh = { isConnected: true, name: "the open button, freshly mounted" };

    expect(focusTargetAfterClose(live, fresh)).toBe(live);
    expect(focusTargetAfterClose(gone, fresh)).toBe(fresh);
    expect(focusTargetAfterClose(null, fresh)).toBe(fresh);
    // Nothing to go back to and nothing to fall back on is honestly nothing.
    expect(focusTargetAfterClose(gone, null)).toBeNull();
  });

  it("knows where the owner is typing", () => {
    expect(isTypingTarget({ tagName: "INPUT" } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: "TEXTAREA" } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: "DIV", isContentEditable: true } as unknown as EventTarget)).toBe(
      true,
    );
    expect(isTypingTarget({ tagName: "DIV" } as unknown as EventTarget)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});

// --------------------------------------------------------------- what it renders

describe("the palette on the page", () => {
  it("closed, it is one affordance that says its own shortcut", () => {
    const html = renderToStaticMarkup(<CommandPalette />);
    expect(html).toContain("data-palette-open");
    expect(html).toContain('aria-keyshortcuts="Control+K Meta+K"');
    // Closed means closed: no dialog in the markup a screen reader walks.
    expect(html).not.toContain('role="dialog"');
  });

  it("is mounted in the layout, with the skip link before it (req 724)", async () => {
    const fs = await import("node:fs/promises");
    const layout = await fs.readFile(new URL("../../app/layout.tsx", import.meta.url), "utf8");
    expect(layout).toContain("<CommandPalette />");
    expect(layout).toContain("data-skip-link");
    expect(layout).toContain('href="#main"');
    expect(layout).toContain('id="main"');
    // The skip link must come FIRST, or it is one more thing to tab past.
    expect(layout.indexOf("data-skip-link")).toBeLessThan(layout.indexOf("<SiteNav />"));
  });

  it("the dialog is announced as one, and the list is a listbox", async () => {
    // The ARIA wiring cannot be exercised without a DOM, so it is pinned where it is
    // written — and the behaviour it describes is proven by `paletteAction` above.
    const fs = await import("node:fs/promises");
    const source = await fs.readFile(
      new URL("../../app/components/CommandPalette.tsx", import.meta.url),
      "utf8",
    );
    expect(source).toContain('role="dialog"');
    expect(source).toContain('aria-modal="true"');
    expect(source).toContain('role="listbox"');
    expect(source).toContain('role="option"');
    expect(source).toContain("aria-activedescendant");
    // Focus goes in on open and comes back on close — after the re-render, because the
    // element it goes back to usually does not exist until then.
    expect(source).toContain("inputRef.current?.focus()");
    expect(source).toContain("focusTargetAfterClose<HTMLElement>(restoreTo.current, openButtonRef.current)");
  });

  it("names its three sections in the owner's words", () => {
    expect(KIND_LABEL.page).toBe("Sayfalar");
    expect(KIND_LABEL.panel).toBe("Paneller");
    expect(KIND_LABEL.say).toBe("Söyleyebilecekleriniz");
  });

  it("the focus ring is defined, or the keyboard is invisible", async () => {
    const fs = await import("node:fs/promises");
    const css = await fs.readFile(new URL("../../app/globals.css", import.meta.url), "utf8");
    expect(css).toContain(":focus-visible");
    expect(css).toContain(".skip-link:focus");
    // A shell whose focus cannot be seen is a shell the keyboard cannot be used on,
    // however correct its tab order is.
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:/);
  });
});
