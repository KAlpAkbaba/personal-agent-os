/**
 * The proposal parser (`proposalSections.ts`): the researcher's Markdown split into its
 * sections, the "Bugün / Bununla" pairs lifted out of "Faydası — örneklerle", and nothing
 * from the text that could become markup or a non-http link.
 */

import { describe, expect, it } from "vitest";

import {
  parseInlines,
  parseProposal,
  safeHref,
  type Inline,
} from "../../app/core/approvals/proposalSections";
import { BOLD_HEADINGS, HOSTILE, WITHOUT_BENEFIT, WITH_BENEFIT } from "./fixtures";

function plain(inlines: Inline[]): string {
  return inlines.map((inline) => inline.text).join("");
}

describe("sections", () => {
  it("splits a '## ' proposal into its sections, in the file's order, unknown ones kept", () => {
    const parsed = parseProposal(WITH_BENEFIT);
    expect(parsed.title).toBe("Öneri: Soniox'u Türkçe akışlı STT adayı olarak ÖLÇ");
    expect(parsed.sections.map((section) => section.title)).toEqual([
      "",
      "Ne",
      "Faydası — örneklerle",
      "Neden şimdi",
      "Nasıl",
      "Maliyet/risk",
      "Deneme notu",
      "Kanıt planı",
      "Karar",
    ]);
    expect(parsed.sections.map((section) => section.role)).toEqual([
      "other",
      "what",
      "benefit",
      "other",
      "other",
      "other",
      "other",
      "other",
      "other",
    ]);
  });

  it("joins a paragraph's lines, and reads list items with their continuation lines", () => {
    const parsed = parseProposal(WITH_BENEFIT);
    const [preamble, what, , why] = parsed.sections;
    expect(preamble.blocks).toHaveLength(1);
    expect(preamble.blocks[0].kind).toBe("paragraph");
    expect(what.blocks).toHaveLength(1);
    const paragraph = what.blocks[0];
    expect(paragraph.kind === "paragraph" && plain(paragraph.inlines)).toBe(
      "Soniox'un akışlı STT'sini mevcut sağlayıcı arayüzünün arkasında BİR aday olarak ekleyip elimizdeki derlemle ölçmek. Sahibin cümlesi değişmez.",
    );
    expect(why.blocks).toHaveLength(1);
    const list = why.blocks[0];
    expect(list.kind).toBe("list");
    if (list.kind !== "list") return;
    expect(list.items).toHaveLength(2);
    expect(plain(list.items[0])).toBe("Soniox: gerçek zamanlı 0,12 USD/saat (fiyat, Türkçe).");
    expect(list.items[0].filter((inline) => inline.kind === "link")).toEqual([
      { kind: "link", text: "fiyat", href: "https://soniox.com/pricing" },
      { kind: "link", text: "Türkçe", href: "https://soniox.com/speech-to-text/turkish" },
    ]);
    // Emphasis is not rendered; its markers do not litter the sentence either.
    expect(plain(list.items[1])).toBe("Kanıt ince: bağımsız Türkçe WER karşılaştırması bulamadım.");
  });

  it("splits the older '**Heading**:' shape, and only at the section names the rule knows", () => {
    const parsed = parseProposal(BOLD_HEADINGS);
    expect(parsed.sections.map((section) => section.title)).toEqual([
      "Ne",
      "Faydası — örneklerle",
      "Neden şimdi",
      "Maliyet/risk",
      "Karar",
    ]);
    const [what, , why, cost, decision] = parsed.sections;
    expect(what.role).toBe("what");
    expect(what.blocks[0].kind === "paragraph" && plain(what.blocks[0].inlines)).toBe(
      "Ledger üzerinde tek bir konuşulan anlatı. İkinci satır aynı paragrafın devamı.",
    );
    // A bold lead inside a list item is the item's text, not a heading.
    expect(why.blocks[0].kind === "list" && why.blocks[0].items.map(plain)).toEqual([
      "Parçalar mevcut.",
      "Kanıt ince: bu bir liste öğesi, başlık değil.",
    ]);
    // "**Efor**:" is not a section of the rule: it stays in the section it was written in.
    expect(cost.blocks.map((block) => block.kind === "paragraph" && plain(block.inlines))).toEqual([
      "orta.",
      "Efor: bu satır bilinen bir bölüm değil, metin olarak kalır.",
    ]);
    expect(decision.blocks[0].kind === "paragraph" && plain(decision.blocks[0].inlines)).toBe("Yapalım mı?");
    expect(parsed.benefit?.pairs.map((pair) => [plain(pair.today), plain(pair.withIt)])).toEqual([
      ['"bu hafta ne oldu" diye soruyorum, yanıt yok.', "30 saniyelik özet gelir."],
    ]);
  });

  it("answers an empty parse for an empty text, and one untitled section for bare prose", () => {
    expect(parseProposal("")).toEqual({ title: null, sections: [], benefit: null });
    expect(parseProposal("  \n\n ")).toEqual({ title: null, sections: [], benefit: null });
    const bare = parseProposal("Yalnızca bir cümle.\r\nİkinci satır.");
    expect(bare.sections).toHaveLength(1);
    expect(bare.sections[0].title).toBe("");
    expect(bare.sections[0].blocks[0].kind === "paragraph" && plain(bare.sections[0].blocks[0].inlines)).toBe(
      "Yalnızca bir cümle. İkinci satır.",
    );
  });
});

describe("the benefit section", () => {
  it("lifts each 'Bugün / Bununla' pair, with its continuation line and its link", () => {
    const { benefit } = parseProposal(WITH_BENEFIT);
    expect(benefit).not.toBeNull();
    if (!benefit) return;
    expect(benefit.title).toBe("Faydası — örneklerle");
    expect(benefit.pairs.map((pair) => [plain(pair.today), plain(pair.withIt)])).toEqual([
      [
        '"ışığı kapat" dediğimde mutfakta "ışığı kapa at" yazılıyor ve komut düşüyor.',
        "aynı cümle gürültüde de doğru yazılır; komut ilk seferde çalışır.",
      ],
      [
        "hangi sağlayıcının daha iyi olduğunu tahmin ediyoruz.",
        "üç sağlayıcı aynı derlemde yan yana ölçülür ve tablo raporda durur.",
      ],
      ["ölçüm için fiyat sayfasına elle bakıyorum.", "maliyet ölçümle birlikte gelir."],
    ]);
    expect(benefit.pairs[2].today).toContainEqual({
      kind: "link",
      text: "fiyat sayfasına",
      href: "https://soniox.com/pricing",
    });
    expect(benefit.gain && plain(benefit.gain)).toBe("yanlış anlama oranı derlemde sayıyla görülür.");
    expect(benefit.notGained && plain(benefit.notGained)).toBe("TTS kalitesi değişmez.");
    expect(benefit.rest).toEqual([]);
  });

  it("reads bold labels, keeps a half pair, and keeps what is not a pair as the section's own text", () => {
    const { benefit } = parseProposal(
      [
        "## Faydası — örneklerle",
        "Üç örnek, sahibin gününden.",
        "",
        "**Bugün:** elle bakıyorum.",
        "**Bununla:** kendiliğinden gelir.",
        "",
        "- **Bugün**: yalnızca bugünü yazılmış.",
        "",
        "**Kazanç:** ölçülür.",
      ].join("\n"),
    );
    expect(benefit?.pairs.map((pair) => [plain(pair.today), plain(pair.withIt)])).toEqual([
      ["elle bakıyorum.", "kendiliğinden gelir."],
      ["yalnızca bugünü yazılmış.", ""],
    ]);
    expect(benefit?.gain && plain(benefit.gain)).toBe("ölçülür.");
    expect(benefit?.notGained).toBeNull();
    expect(benefit?.rest.map((block) => block.kind === "paragraph" && plain(block.inlines))).toEqual([
      "Üç örnek, sahibin gününden.",
    ]);
  });

  it("is absent from a proposal written before the rule", () => {
    const parsed = parseProposal(WITHOUT_BENEFIT);
    expect(parsed.benefit).toBeNull();
    expect(parsed.sections.map((section) => section.title)).toEqual(["", "Ne", "Neden şimdi", "Karar"]);
    // "Faydalanılan kaynaklar" begins with the same letters and is not the benefit section.
    const lookalike = parseProposal("## Faydalanılan kaynaklar\nBugün: a\nBununla: b\n");
    expect(lookalike.benefit).toBeNull();
    expect(lookalike.sections[0].role).toBe("other");
  });
});

describe("links and markup", () => {
  it("makes a link of http and https only", () => {
    expect(safeHref("https://soniox.com/pricing")).toBe("https://soniox.com/pricing");
    expect(safeHref("http://example.com/a?b=1")).toBe("http://example.com/a?b=1");
    for (const refused of [
      "javascript:void0",
      "JaVaScRiPt:void0",
      " javascript:void0",
      "data:text/html;base64,PHNjcmlwdD4=",
      "vbscript:x",
      "file:///C:/Windows",
      "//evil.example/x",
      "/core/approvals",
      "mailto:a@example.com",
      "https:",
      "",
    ]) {
      expect(safeHref(refused), refused).toBeNull();
    }
  });

  it("keeps a refused link as the text the researcher wrote", () => {
    expect(parseInlines("önce [tıkla](javascript:void0) sonra")).toEqual([
      { kind: "text", text: "önce [tıkla](javascript:void0) sonra" },
    ]);
    expect(parseInlines("[a](https://a.example/x) ve [b](data:x)")).toEqual([
      { kind: "link", text: "a", href: "https://a.example/x" },
      { kind: "text", text: " ve [b](data:x)" },
    ]);
  });

  it("carries tags as text and never yields a non-http link, anywhere in a hostile proposal", () => {
    const parsed = parseProposal(HOSTILE);
    const inlines: Inline[] = parsed.sections.flatMap((section) =>
      section.blocks.flatMap((block) => (block.kind === "paragraph" ? block.inlines : block.items.flat())),
    );
    expect(inlines.filter((inline) => inline.kind === "link")).toEqual([]);
    const text = plain(inlines);
    expect(text).toContain("<script>alert(1)</script>");
    expect(text).toContain("[tıkla](javascript:void0)");
    expect(text).toContain("[x](JaVaScRiPt:void0)");
    expect(parsed.title).toBe('Öneri: <script>alert("başlık")</script>');
  });
});
