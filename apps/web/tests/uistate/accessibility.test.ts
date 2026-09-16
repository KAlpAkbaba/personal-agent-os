/**
 * B25 req 724: the shell is usable without a pointer and without sight.
 *
 * `Yok` is what the matrix said, and it was accurate: no skip link, no focus ring, and a
 * nav that had grown to fourteen entries in front of every page's content. None of that is
 * a component you can add; they are properties of the whole shell, which is why they are
 * checked here across every page at once rather than in any one page's test.
 *
 * These are source-level checks on purpose, and the reason is worth writing down: this
 * suite runs in Node with no DOM, so "the focus ring is visible" and "there is one main
 * landmark" cannot be measured by rendering. What CAN be made true is that the shell never
 * ships a page without them — and a property that holds for every page in the directory,
 * including the fifteenth, is worth more than one page rendered correctly.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const APP = fileURLToPath(new URL("../../app", import.meta.url));

function read(relative: string): string {
  return readFileSync(join(APP, relative), "utf8");
}

/** Every `page.tsx` under `app/`, which is every route this shell serves. */
function pages(): string[] {
  const found: string[] = [];
  const walk = (dir: string, prefix: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full, `${prefix}${entry}/`);
      else if (entry === "page.tsx") found.push(`${prefix}${entry}`);
    }
  };
  walk(APP, "");
  return found.sort();
}

describe("the document", () => {
  it("declares its language, so a screen reader pronounces Turkish as Turkish", () => {
    expect(read("layout.tsx")).toContain('<html lang="tr">');
  });

  it("opens with a skip link, and the link has somewhere to land", () => {
    const layout = read("layout.tsx");
    expect(layout).toContain('href="#main"');
    expect(layout).toContain('id="main"');
    // First in the document. A skip link that is not first is one more thing to tab past.
    expect(layout.indexOf("data-skip-link")).toBeLessThan(layout.indexOf("<SiteNav"));
    // And it must be able to take focus, or "skip" moves the scroll and not the caret.
    expect(layout).toContain("tabIndex={-1}");
  });

  it("the nav says what it is, and which entry is current", () => {
    const nav = read("components/SiteNav.tsx");
    expect(nav).toContain('aria-label="Site gezinmesi"');
    expect(nav).toContain('aria-current={current ? "page" : undefined}');
  });
});

describe("every page", () => {
  it("there are pages to check, so this file cannot pass vacuously", () => {
    expect(pages().length).toBeGreaterThanOrEqual(14);
  });

  it("has exactly one main landmark, through its own tag or the shared shell", () => {
    for (const page of pages()) {
      const source = read(page);
      const own = (source.match(/<main[\s>]/g) ?? []).length;
      // `FamilyPage` supplies the landmark for the pages built on it; the rest declare
      // their own. Two would give a screen reader two "main" regions to choose between.
      const shared = source.includes("<FamilyPage") ? 1 : 0;
      expect(own + shared, `${page}: ${own} own + ${shared} shared`).toBe(1);
    }
  });

  it("has a heading to enter at", () => {
    for (const page of pages()) {
      const source = read(page);
      const heading = source.includes("<h1") || source.includes("<FamilyPage");
      expect(heading, `${page} has no <h1>`).toBe(true);
    }
  });
});

describe("motion is a preference, not a default", () => {
  it("every animated rule in the shell has a reduced-motion answer", () => {
    const css = read("globals.css");
    const transitions = (css.match(/^\s*transition:/gm) ?? []).length;
    expect(transitions, "the guard matches nothing if nothing animates").toBeGreaterThan(0);
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
  });
});

describe("the keyboard can see where it is", () => {
  it("the focus ring is defined once, for everything", () => {
    const css = read("globals.css");
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px/);
    // `:focus-visible` rather than `:focus`, so a pointer click does not light it up —
    // which is how focus rings get removed altogether and the keyboard goes blind.
    expect(css).toContain(":focus-visible");
  });

  it("the skip link is only invisible until it is focused", () => {
    const css = read("globals.css");
    expect(css).toMatch(/\.skip-link\s*\{[^}]*left:\s*-9999px/);
    expect(css).toMatch(/\.skip-link:focus[^{]*\{[^}]*left:\s*0/);
    // Never `display: none`: a hidden element cannot be focused, so the link would be
    // unreachable by the one input method it exists for.
    const block = /\.skip-link\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(block).not.toContain("display: none");
    expect(block).not.toContain("visibility: hidden");
  });
});
