/**
 * B23 req 685/686/690/691/692/695/716: no page is a dead end.
 *
 * Measured before this batch, by reading every `href` in every page:
 *
 *   /              → all five
 *   /research      → /artifacts
 *   /voice         → /core
 *   /artifacts     → nothing
 *   /core          → nothing
 *   /core/cockpit  → nothing
 *
 * `layout.tsx` was `<body>{children}</body>`. So an owner who opened a report from a
 * notification had the browser's back button and nothing else — and installed as a PWA
 * (`start_url: "/core"`, `display: standalone`) they did not even have that.
 *
 * The property this pins is not "there is a nav component". It is that EVERY page can
 * reach every other, which is a claim about the product and stays true when a seventh page
 * is added — because the nav is rendered from one list, in the layout, and this test walks
 * that list.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import SiteNav, { NAV, currentHref, isCoreRoute } from "../../app/components/SiteNav";

vi.mock("next/navigation", () => ({
  usePathname: () => globalThis.__pathname ?? "/",
}));

declare global {
  // eslint-disable-next-line no-var
  var __pathname: string | undefined;
}

function render(pathname: string): string {
  globalThis.__pathname = pathname;
  return renderToStaticMarkup(<SiteNav />);
}

/**
 * Every page this product has. B23 shipped six; B24 added the seven family pages
 * (689/693/694/696/697/698/699) and the availability page (700), and the property below is
 * unchanged by that: every page still reaches every other, because they are all rendered
 * from the one `NAV` list in the layout.
 */
const MAIN = ["/", "/core", "/core/cockpit", "/voice", "/research", "/artifacts"];

const FAMILY = [
  "/memory",
  "/routines",
  "/alarms",
  "/notifications",
  "/security",
  "/selfdev",
  "/settings",
  "/availability",
];

const PAGES = [...MAIN, ...FAMILY];

describe("every page of the shell", () => {
  it("the list holds exactly the pages this product has", () => {
    expect(NAV.map((item) => item.href).sort()).toEqual([...PAGES].sort());
  });

  it("each entry is filed under the row it is rendered in", () => {
    // Two rows, one source list: the main shell an owner moves through, and the
    // subsystems' own pages a step behind it. A page in neither group would be rendered
    // nowhere while still counting as "in the nav".
    expect(NAV.filter((item) => item.group === "main").map((item) => item.href)).toEqual(MAIN);
    expect(NAV.filter((item) => item.group === "family").map((item) => item.href)).toEqual(FAMILY);
  });

  it("each family page exists on disk under the href it claims", async () => {
    // The half a list cannot check about itself: `/security` in `NAV` and no
    // `app/security/page.tsx` is a nav entry that 404s, which is a worse dead end than
    // the missing link B23 removed.
    const fs = await import("node:fs/promises");
    for (const href of FAMILY) {
      const file = new URL(`../../app${href}/page.tsx`, import.meta.url);
      await expect(fs.access(file), href).resolves.toBeUndefined();
    }
  });

  it("every page can reach every other page", () => {
    for (const from of PAGES) {
      const html = render(from);
      for (const to of PAGES) {
        // Including itself: the current entry stays a link, so a stuck page can be
        // reloaded from the nav rather than from the address bar the PWA does not have.
        expect(html, `${from} → ${to}`).toContain(`data-nav-item="${to}"`);
      }
    }
  });

  it("exactly one entry is marked current, and it is the longest match", () => {
    // A prefix match would light both /core and /core/cockpit on the cockpit.
    expect(currentHref("/core/cockpit")).toBe("/core/cockpit");
    expect(currentHref("/core")).toBe("/core");
    expect(currentHref("/artifacts")).toBe("/artifacts");
    expect(currentHref("/")).toBe("/");
    // A page not in the list marks nothing rather than guessing.
    expect(currentHref("/nowhere")).toBeNull();

    for (const page of PAGES) {
      const html = render(page);
      const currents = html.match(/data-nav-current="yes"/g) ?? [];
      expect(currents.length, page).toBe(1);
      expect(html).toContain('aria-current="page"');
    }
  });

  it("the Core keeps the same links in a quieter weight", () => {
    // req 716: the PWA opens at /core with no address bar. A nav that hid itself there
    // would be the dead end this batch exists to remove; a bar across the top would be the
    // chrome the manifest refuses. Same links, different weight.
    const core = render("/core");
    expect(core).toContain('data-nav-variant="quiet"');
    for (const to of PAGES) expect(core).toContain(`data-nav-item="${to}"`);

    const page = render("/artifacts");
    expect(page).toContain('data-nav-variant="bar"');
    expect(isCoreRoute("/core")).toBe(true);
    expect(isCoreRoute("/core/cockpit")).toBe(true);
    expect(isCoreRoute("/artifacts")).toBe(false);
  });

  it("every entry says what the page is for", () => {
    const html = render("/");
    for (const item of NAV) {
      expect(html).toContain(item.label);
      expect(html).toContain(item.hint);
    }
  });

  it("the nav is labelled for a screen reader", () => {
    expect(render("/")).toContain('aria-label="Site gezinmesi"');
  });
});

describe("the layout renders it", () => {
  it("the nav is in the layout, not in the pages", async () => {
    // The property that keeps a seventh page from being half-linked: pages do not carry
    // the nav, the layout does.
    const layout = await import("node:fs/promises").then((fs) =>
      fs.readFile(new URL("../../app/layout.tsx", import.meta.url), "utf8"),
    );
    expect(layout).toContain("<SiteNav />");
  });
});

describe("the cockpit's device surface (req 695)", () => {
  it("has a panel of its own, addressable by id", async () => {
    // `fetchDeviceStatus`, `parseDevice` and `CockpitData.devices` have existed since
    // M18.3; the only place a device appeared was a line about its SCREEN inside
    // "Ekran / Ortam". Everything else the heartbeat carries was fetched, parsed, held in
    // state and never shown.
    const fs = await import("node:fs/promises");
    const panels = await fs.readFile(
      new URL("../../app/core/panels/CockpitPanels.tsx", import.meta.url),
      "utf8",
    );
    expect(panels).toContain("export function DevicesPanel");
    expect(panels).toContain('id="devices"');
    // The distinction the parser preserves must survive into the words.
    expect(panels).toContain("durum bildirmedi");

    const cockpit = await fs.readFile(
      new URL("../../app/core/cockpit/page.tsx", import.meta.url),
      "utf8",
    );
    expect(cockpit).toContain("<DevicesPanel devices={data.devices}");
  });
});

describe("the home index (req 686)", () => {
  it("is built from the same list as the nav", async () => {
    const fs = await import("node:fs/promises");
    const home = await fs.readFile(new URL("../../app/page.tsx", import.meta.url), "utf8");
    expect(home).toContain("NAV.filter");
    expect(home).toContain("data-home-index");
    // Not a hand-written row of links that can drift from the pages that exist.
    expect(home).not.toContain('href="/research" style');
  });
});

/** The declaration blocks of every rule that fades the quiet nav. */
function quietFadeRules(css: string): string[] {
  const out: string[] = [];
  const pattern = /body\[data-core-faded="yes"\][^{]*\.site-nav-quiet[^{]*\{([^}]*)\}/g;
  for (const match of css.matchAll(pattern)) out.push(match[1]);
  return out;
}

describe("minimal mode keeps the stage alone (req 720)", () => {
  it("the nav fades with the control cluster, and can be brought back", async () => {
    const fs = await import("node:fs/promises");
    const core = await fs.readFile(new URL("../../app/core/page.tsx", import.meta.url), "utf8");
    // One fade state, not a second timer: the nav follows the cluster the Core already has.
    expect(core).toContain("document.body.dataset.coreFaded");
    expect(core).toContain("fade.faded");

    const css = await fs.readFile(new URL("../../app/globals.css", import.meta.url), "utf8");
    expect(css).toContain('body[data-core-faded="yes"] .site-nav-quiet');
    // Faded, not removed: a deliberate move brings it back, which is why it keeps its
    // pointer events and has a hover rule.
    expect(css).toContain('body[data-core-faded="yes"] .site-nav-quiet:hover');
    // B25 tightened this. It used to grep the WHOLE stylesheet for "display: none", which
    // says nothing about the faded nav and everything about whatever else the file grows —
    // it went red when B25 added a screen-reader-only class whose COMMENT mentions the
    // phrase. The claim is about these two rules, so it is asserted about these two rules.
    const faded = quietFadeRules(css);
    // A reader that matches nothing passes for ever — the failure the scoped version is
    // meant to avoid, arriving by the other door.
    expect(faded.length, "the fade rules were not found at all").toBeGreaterThanOrEqual(2);
    for (const rule of faded) {
      expect(rule, rule).not.toContain("display: none");
      expect(rule, rule).not.toContain("visibility: hidden");
    }
  });
});
