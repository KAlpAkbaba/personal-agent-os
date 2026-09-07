/**
 * The Documents panel and the Core's readout for `document.analysis` (M20
 * spec §3): sentences about the file and the place the Cloud Core published,
 * never a file this page fetched or a place it guessed.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DocumentsPanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import { DOCUMENT_STEP_TTL_MS } from "../../app/lib/uistate/contract";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  DOCUMENT_ANALYSIS,
  DOCUMENT_ANALYSIS_BARE,
  DOCUMENT_ANSWERED,
  OPERATOR_RUNNING,
  T0,
  event,
  iso,
  resetSequence,
  response,
} from "../uistate/fixtures";

function truthOf(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return applyResponse(emptyTruth(), response(events), at);
}

function panel(events: ReturnType<typeof event>[], now = T0) {
  return renderToStaticMarkup(<DocumentsPanel truth={truthOf(events)} now={now} />);
}

function readout(events: ReturnType<typeof event>[], compact = false, now = T0) {
  return renderToStaticMarkup(<StateReadout intent={visualFor(truthOf(events), now)} compact={compact} />);
}

const PATH_2026 = "C:\\Users\\alpak\\Documents\\sozlesmeler\\2026\\sozlesme.docx";
const PATH_2025 = "C:\\Users\\alpak\\Documents\\sozlesmeler\\2025\\sozlesme.docx";

describe("the Documents panel", () => {
  it("is empty, in words, before any document was ever read", () => {
    const html = panel([AGENT_IDLE(), OPERATOR_RUNNING()]);
    expect(html).toContain('data-panel="documents"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain('data-document-stage="none"');
    expect(html).toContain('data-document-last-known=""');
    expect(html).toContain("Henüz bir belge okunmadı.");
    expect(html).toContain(">0<");
    expect(html).not.toContain("data-document-current");
    expect(html).not.toContain("Önceki belge");
    expect(html).not.toContain("Son yanıtın kaynakları");
  });

  it("states the current document by name, the place in the owner's words, the step and the age", () => {
    const html = panel([DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")], T0 + 3_000);
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-document-stage="analysing"');
    expect(html).toContain('data-document-last-known="analysing"');
    expect(html).toContain("data-document-current");
    expect(html).toContain('data-document-file="rapor.pdf"');
    expect(html).toContain('data-document-part="p3"');
    expect(html).toContain('data-document-step="answer"');
    expect(html).toContain(">rapor.pdf</span>");
    expect(html).toContain("dosya: rapor.pdf · yer: 3. sayfa · adım: yanıtlıyor");
    expect(html).toContain("3 sn önce");
    expect(html).toContain(">inceleniyor<");
    expect(html).not.toContain("Son bilinen");
    expect(html).not.toContain("Önceki belge");
    expect(html).not.toContain("Son yanıtın kaynakları");
  });

  it("shows the path only when the Core published one, and never derives it from the name", () => {
    const byName = panel([DOCUMENT_ANALYSIS("sozlesme.docx", "p3", "read")]);
    expect(byName).toContain('data-document-path=""');
    expect(byName).not.toContain("data-document-path-line");
    expect(byName).not.toContain("Users");

    const byPath = panel([DOCUMENT_ANALYSIS("sozlesme.docx", "p3", "read", { path: PATH_2026 })]);
    expect(byPath).toContain(`data-document-path="${PATH_2026}"`);
    expect(byPath).toContain(`<span class="muted" data-document-path-line="true">${PATH_2026}</span>`);
    // A DOCX p3 is a paragraph, by the published name's extension.
    expect(byPath).toContain("yer: 3. paragraf");
  });

  it("says what was not reported when the publisher sent no metadata", () => {
    const html = panel([DOCUMENT_ANALYSIS_BARE()]);
    expect(html).toContain('data-document-stage="analysing"');
    expect(html).toContain(">Belge inceleniyor</span>");
    expect(html).toContain("dosya bildirilmedi · yer bildirilmedi · adım bildirilmedi</span>");
    expect(html).not.toContain("data-document-path-line");
  });

  it("names the previous document — the last different file the bus carried — with its own age and place", () => {
    resetSequence();
    const earlier = { ...DOCUMENT_ANALYSIS("notlar.md", "h2:Kararlar", "read", { path: "D:\\notlar\\notlar.md" }), at: iso(-30_000) };
    const html = renderToStaticMarkup(
      <DocumentsPanel
        truth={applyResponse(emptyTruth(), response([earlier, DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")]), T0)}
        now={T0}
      />,
    );
    expect(html).toContain("data-document-previous");
    expect(html).toContain("Önceki belge: notlar.md");
    expect(html).toContain('data-document-file="notlar.md"');
    expect(html).toContain("Kararlar bölümü");
    expect(html).toContain("30 sn önce");
    expect(html).toContain("D:\\notlar\\notlar.md");
    // The current one is still the current one.
    expect(html).toContain('data-document-file="rapor.pdf"');
    expect(html).toContain("dosya: rapor.pdf · yer: 3. sayfa · adım: yanıtlıyor");
  });

  it("does not invent a previous document from the same file at another place", () => {
    const html = panel([DOCUMENT_ANALYSIS("rapor.pdf", "p1", "read"), DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")]);
    expect(html).not.toContain("Önceki belge");
    expect(html).not.toContain("data-document-previous");
  });

  it("lists the last answer's refs as dosya · yer · alıntı, exactly as published", () => {
    const html = panel([
      DOCUMENT_ANSWERED(
        [
          { ref: "p3", path: PATH_2026, excerpt: "Madde 3: 45 gün" },
          { ref: "p3", path: PATH_2025, excerpt: "Madde 3: 30 gün" },
        ],
        "sozlesme.docx",
        "p3",
      ),
    ]);
    expect(html).toContain('data-document-refs="2"');
    expect(html).toContain("Son yanıtın kaynakları");
    expect(html).toContain(`data-document-ref="p3" data-document-ref-path="${PATH_2026}"`);
    expect(html).toContain(`${PATH_2026} · 3. paragraf · Madde 3: 45 gün`);
    expect(html).toContain(`${PATH_2025} · 3. paragraf · Madde 3: 30 gün`);
    // Two documents with one title are two paths, both on screen.
    expect(html.indexOf(PATH_2026)).not.toBe(html.lastIndexOf(PATH_2025));
  });

  it("keeps the last answer's refs under a later read that cited none, dated by their own event", () => {
    resetSequence();
    const answered = { ...DOCUMENT_ANSWERED([{ ref: "r7", path: "C:\\x\\veri.csv", excerpt: "Kerem\tİzmir" }], "veri.csv", "r7"), at: iso(-20_000) };
    const html = renderToStaticMarkup(
      <DocumentsPanel
        truth={applyResponse(emptyTruth(), response([answered, DOCUMENT_ANALYSIS("veri.csv", "r8", "read")]), T0)}
        now={T0}
      />,
    );
    expect(html).toContain('data-document-refs="1"');
    expect(html).toContain("C:\\x\\veri.csv · 7. satır · Kerem\tİzmir");
    expect(html).toContain("20 sn önce");
    // The current read is the newer event.
    expect(html).toContain('data-document-part="r8"');
  });

  it("says a ref's missing fields are missing rather than filling them in", () => {
    const html = panel([DOCUMENT_ANSWERED([{ ref: "$.ses" }], "ayarlar.json", "$.ses")]);
    expect(html).toContain("dosya bildirilmedi · ses anahtarı · alıntı bildirilmedi");
    expect(html).toContain('data-document-ref-path=""');
  });

  it("shows no refs when no document event in the tail cited any", () => {
    const html = panel([DOCUMENT_ANALYSIS("rapor.pdf", "p3", "summarize")]);
    expect(html).not.toContain("Son yanıtın kaynakları");
    expect(html).not.toContain("data-document-refs");
  });

  it("an aged-out read is last-known, not finished", () => {
    const html = panel([DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")], T0 + DOCUMENT_STEP_TTL_MS + 1_000);
    expect(html).toContain('data-document-stage="none"');
    expect(html).toContain('data-document-last-known="analysing"');
    expect(html).toContain("Son bilinen: rapor.pdf");
    expect(html).toContain("İncelemenin bittiği bildirilmedi");
    expect(html).toContain(">son bilinen<");
    // Still the facts it had: the place is where it WAS reading.
    expect(html).toContain("yer: 3. sayfa");
  });

  it("the newest document event is the one shown, whatever else was published since", () => {
    const html = panel([DOCUMENT_ANALYSIS("notlar.md", "h2:Kararlar", "read"), AGENT_IDLE(), OPERATOR_RUNNING()]);
    expect(html).toContain('data-document-stage="analysing"');
    expect(html).toContain("dosya: notlar.md · yer: Kararlar bölümü · adım: okuyor");
  });
});

describe("the Core's readout for a document", () => {
  it("headlines the reading Core with the file and place as the caption and the facts beneath", () => {
    const html = readout([DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")]);
    expect(html).toContain('data-core-kind="document_analysis"');
    expect(html).toContain('data-core-state="document.analysis"');
    expect(html).toContain('data-core-subsystem="documents"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain("Belge inceleniyor");
    expect(html).toContain("Belgeler");
    // The caption is the bus label, not a voice caption.
    expect(html).toContain('data-label="true">rapor.pdf · 3. sayfa · yanıtlıyor</p>');
    expect(html).not.toContain("data-caption");
    expect(html).toContain("data-document-facts");
    expect(html).toContain('data-document-file="rapor.pdf"');
    expect(html).toContain('data-document-place="3. sayfa"');
    expect(html).toContain("dosya: rapor.pdf · yer: 3. sayfa · adım: yanıtlıyor");
    expect(html).toContain("İlerleme bildirilmez.");
    // No progress bar: none was published — and the bus line says so.
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-operator-facts");
  });

  it("captions only the bare statement when no file was named, and says what was not reported", () => {
    const html = readout([DOCUMENT_ANALYSIS(null, "p3", "answer")]);
    expect(html).toContain('data-label="true">Belge inceleniyor</p>');
    expect(html).toContain("dosya bildirilmedi · yer: 3. sayfa · adım: yanıtlıyor");
    expect(html).toContain('data-document-file=""');
  });

  it("keeps the caption in the compact form and drops the long line", () => {
    const html = readout([DOCUMENT_ANALYSIS("sunum-q3.pptx", "s4", "summarize")], true);
    expect(html).toContain("sunum-q3.pptx · 4. slayt · özetliyor");
    expect(html).not.toContain("data-document-facts");
  });

  it("names the aged-out read as last-known rather than as reading, with its facts", () => {
    const html = readout([DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")], false, T0 + DOCUMENT_STEP_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="document.analysis"');
    expect(html).toContain("Belge inceleniyor");
    expect(html).toContain("data-document-facts");
    expect(html).toContain("dosya: rapor.pdf");
  });

  it("prints no document line for any other kind", () => {
    expect(readout([AGENT_IDLE()])).not.toContain("data-document-facts");
    expect(readout([OPERATOR_RUNNING()])).not.toContain("data-document-facts");
  });
});
