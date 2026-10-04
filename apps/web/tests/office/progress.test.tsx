/**
 * The Ofis's İlerleme strip (office-progress): the owner, 2026-10-03, "roadmap'e göre projenin
 * ortalama yüzde kaçı tamamlandı, yüzde kaçı kaldı göremiyorum". Three percents with thin bars
 * under the top bar; the panel opens on a click (<details>) and lists the JARVIS rows by state
 * and the order's next open step. A section the server could not read says so - never a number.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { OfficeProgress as Progress } from "../../app/core/office/officeApi";
import OfficeProgress, { buildProgress } from "../../app/core/office/OfficeProgress";
import OfficeView from "../../app/core/office/OfficeView";
import { twoWorkers } from "./fixtures";

function sample(): Progress {
  return {
    jarvis: {
      have: 1,
      partial: 1,
      missing: 1,
      never: 1,
      unknown: ["Kimsesiz <b>satır</b>"],
      counted: 4,
      percent: 37,
      rows: [
        { name: "Konuşur", state: "have" },
        { name: "Her yerde", state: "partial" },
        { name: "Evi yönetir", state: "missing" },
        { name: "Zırhı uçurur", state: "never" },
        { name: "Kimsesiz <b>satır</b>", state: "unknown" },
      ],
    },
    order: {
      steps: [
        { n: 1, title: "Memory", state: "done" },
        { n: 2, title: "browser-use, anywhere", state: "partial" },
        { n: 3, title: "Secretary", state: "open" },
      ],
      percent: 50,
      next: { n: 2, title: "browser-use, anywhere", state: "partial" },
    },
    v1: {
      total: 750,
      done: 711,
      by_status: { DONE: 711 },
      by_proof: { PR: 153 },
      percent_done: 95,
      percent_proven_real: 20,
    },
    rule: "Var 1, Yarım 0,5, Yok 0",
    as_of: "a5e68d92d9271ececec51da01b713e43a394e28c",
  };
}

describe("the progress model", () => {
  it("reads the three percents into one Turkish line", () => {
    const model = buildProgress(sample());
    expect(model?.line).toBe(
      "JARVIS hedefi %37 · Sıralı plan %50 · v1.0 listesi %95 yapıldı, %20 gerçekte kanıtlı",
    );
    expect(model?.bars.map((b) => b.percent)).toEqual([37, 50, 95]);
  });

  it("groups the JARVIS rows by state, the never row apart", () => {
    const model = buildProgress(sample());
    expect(model?.groups.map((g) => [g.title, g.rows])).toEqual([
      ["Var", ["Konuşur"]],
      ["Yarım", ["Her yerde"]],
      ["Yok", ["Evi yönetir"]],
      ["Okunamadı (yapılmamış sayıldı)", ["Kimsesiz <b>satır</b>"]],
      ["Hedef değil (asla / donanım)", ["Zırhı uçurur"]],
    ]);
    expect(model?.next).toBe("2. browser-use, anywhere (yarım)");
    expect(model?.asOf).toBe("a5e68d9");
  });

  it("says 'okunamadı' for a section the server could not read, never a number", () => {
    const model = buildProgress({ ...sample(), jarvis: null, v1: null });
    expect(model?.line).toBe(
      "JARVIS hedefi okunamadı · Sıralı plan %50 · v1.0 listesi okunamadı",
    );
    expect(model?.bars.map((b) => b.percent)).toEqual([null, 50, null]);
    expect(model?.groups).toEqual([]);
  });

  it("is nothing for an answer that predates the field", () => {
    expect(buildProgress(undefined)).toBeNull();
    expect(buildProgress(null)).toBeNull();
  });
});

describe("the strip and its panel", () => {
  it("renders three meters and a <details> panel listing the rows by state", () => {
    const html = renderToStaticMarkup(<OfficeProgress progress={sample()} />);
    expect(html).toContain("<details");
    expect(html).toContain("<summary");
    expect(html).toContain("İlerleme");
    expect(html.match(/role="meter"/g)).toHaveLength(3);
    expect(html).toContain('aria-valuenow="95"');
    expect(html).toContain("Var");
    expect(html).toContain("Yarım");
    expect(html).toContain("Yok");
    expect(html).toContain("Sıradaki adım: 2. browser-use, anywhere (yarım)");
  });

  it("renders a row name as text, never as markup", () => {
    const html = renderToStaticMarkup(<OfficeProgress progress={sample()} />);
    expect(html).toContain("Kimsesiz &lt;b&gt;satır&lt;/b&gt;");
    expect(html).not.toContain("<b>satır</b>");
  });

  it("wraps at phone width: the strip is a wrapping flex row, no fixed width", () => {
    const html = renderToStaticMarkup(<OfficeProgress progress={sample()} />);
    expect(html).toContain("flex-wrap:wrap");
    expect(html).not.toMatch(/width:\d{3,}px/);
  });

  it("sits under the top bar of the office, and an older answer draws no strip", () => {
    const view = { ...twoWorkers(), progress: sample() };
    const html = renderToStaticMarkup(
      <OfficeView view={view} selected={null} offline={false} reducedMotion={false} onSelect={() => {}} />,
    );
    const top = html.indexOf('data-office="topbar"');
    const strip = html.indexOf('data-office="progress"');
    const main = html.indexOf('class="office-main"');
    expect(top).toBeGreaterThan(-1);
    expect(strip).toBeGreaterThan(top);
    expect(strip).toBeLessThan(main);
    const older = renderToStaticMarkup(
      <OfficeView view={twoWorkers()} selected={null} offline={false} reducedMotion={false} onSelect={() => {}} />,
    );
    expect(older).not.toContain('data-office="progress"');
  });
});
