import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import ReportView, { LabelBadge } from "../../app/research/ReportView";
import { REPORT } from "./fixtures";

// Outside the Next app router there is no router context; a plain anchor is
// what the link renders to anyway.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function render(props: Partial<React.ComponentProps<typeof ReportView>> = {}) {
  return renderToStaticMarkup(<ReportView report={REPORT} {...props} />);
}

function positions(html: string, needles: string[]): number[] {
  return needles.map((n) => {
    const at = html.indexOf(n);
    if (at < 0) throw new Error(`missing: ${n}`);
    return at;
  });
}

describe("ReportView", () => {
  it("renders the spec §3 sections in presentation order", () => {
    const html = render();
    const order = positions(html, [
      "Yönetici Özeti",
      "Bulgular",
      "Neden Önemli",
      "Sonraki Sinyaller",
      "Ayrıntılar",
      "Belirsizlikler",
      "Kaynaklar",
    ]);
    for (let i = 1; i < order.length; i += 1) expect(order[i]).toBeGreaterThan(order[i - 1]);
    // The section content sits under its own heading.
    expect(html.indexOf(REPORT.executive_summary)).toBeGreaterThan(order[0]);
    expect(html.indexOf(REPORT.executive_summary)).toBeLessThan(order[1]);
    expect(html.indexOf("Çerçeve A göç kılavuzunu izle.")).toBeGreaterThan(order[3]);
    expect(html.indexOf("Çerçeve A göç kılavuzunu izle.")).toBeLessThan(order[4]);
  });

  it("renders every finding as a card with importance dots, label badge and citation chips", () => {
    const html = render();
    expect(html.match(/data-finding-id="/g)?.length).toBe(3);
    expect(html).toContain('aria-label="Önem 5/5"');
    expect(html).toContain("●●●●●");
    expect(html).toContain("●●○○○");
    expect(html).toContain("Neden önemli:</em> Mevcut iş akışları göç gerektirecek.");
    // The card for f1 cites e1 and e2.
    expect(html).toContain('href="#src-e1"');
    expect(html).toContain('href="#src-e2"');
    expect(html).toContain("kanıt alıntısı ifadeyi desteklemediği için düşürüldü");
  });

  it("shows the four statement labels as Turkish badges", () => {
    const html = render();
    expect(html).toContain('data-label="source_fact"');
    expect(html).toContain("kaynak bulgusu");
    expect(html).toContain("model çıkarımı");
    expect(html).toContain("öneri");
    expect(html).toContain("belirsizlik");
    expect(renderToStaticMarkup(<LabelBadge label="recommendation" />)).toContain("öneri");
    // An unknown label falls through to its raw name rather than vanishing.
    expect(renderToStaticMarkup(<LabelBadge label="other" />)).toContain("other");
  });

  it("maps citation chips to source rows and marks a dangling citation", () => {
    const html = render();
    for (const id of ["e1", "e2", "e3"]) {
      expect(html).toContain(`id="src-${id}"`);
      expect(html).toContain(`href="#src-${id}"`);
    }
    // f3 cites e9, which is not in sources: chip present, visibly broken, no target.
    expect(html).toContain('href="#src-e9"');
    expect(html).toContain("Kaynak listede bulunamadı");
    expect(html).not.toContain('id="src-e9"');
  });

  it("lists sources with publisher, class, dates, injection warning and syndication note", () => {
    const html = render();
    expect(html).toContain("Framework A 2.0 release notes");
    expect(html).toContain('href="https://example.org/framework-a/2.0"'); // final_url preferred
    expect(html).toContain("resmî");
    expect(html).toContain("haber");
    expect(html).toContain("topluluk");
    expect(html).toContain("yayın:");
    expect(html).toContain("alındı:");
    expect(html.match(/enjeksiyon şüphesi/g)?.length).toBe(1);
    expect(html).toContain("[e1]</code> kaynağının kopyası");
    expect(html).toContain('rel="noreferrer noopener"');
  });

  it("keeps the details section collapsed by default", () => {
    const html = render();
    expect(html).toContain("<details");
    expect(html).not.toContain("<details open");
    expect(html).toContain("Sürüm notları çok ajanlı modu anlatıyor.");
  });

  it("links to the artifact inbox only when an artifact exists and offers the JSON copy", () => {
    const withArtifact = render({ artifactId: "art-1", onCopyJson: () => {} });
    expect(withArtifact).toContain('href="/artifacts"');
    expect(withArtifact).toContain('data-artifact-id="art-1"');
    expect(withArtifact).toContain("Rapor JSON&#x27;unu kopyala");
    const without = render();
    expect(without).not.toContain('href="/artifacts"');
    expect(without).not.toContain("kopyala");
    expect(render({ onCopyJson: () => {}, copyState: "copied" })).toContain("Kopyalandı");
  });

  it("renders the stats line", () => {
    expect(render()).toContain("9 sorgu · 41 keşfedildi · 18 getirildi · 3 başarısız · 6 yinelenen · 12 kanıt");
  });
});
