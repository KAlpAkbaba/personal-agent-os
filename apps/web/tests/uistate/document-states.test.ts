/**
 * Contract v5: `document.analysis` (M20 spec §3).
 *
 * The rule this file holds is the one every other file in this directory
 * holds, applied to the owner's documents: **the Core names a file and a
 * place because the Cloud Core published them — in the same words the Cloud
 * Core speaks — never a file it inferred, never a place it guessed, never
 * progress it was not told.**
 *
 * Plus the boring, load-bearing one: a Cloud Core that still answers v4 (or
 * v3, or v2) is read normally, because v5 only added.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  DOCUMENT_STATES,
  DOCUMENT_STEP_TTL_MS,
  KNOWN_CONTRACT_VERSION,
  MAX_DOCUMENT_REFS,
  MAX_LABEL_CHARS,
  MIN_SUPPORTED_CONTRACT_VERSION,
  OPERATOR_STATES,
  SUBSYSTEMS,
  TRANSIENT_TTL_MS,
  UI_STATES,
  contractCompatibility,
  isCoreChannel,
  isDocumentState,
  isKnownState,
  isOperatorState,
  parseDocumentRefs,
  parseEvent,
  stateChannel,
  stateKind,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import {
  DOCUMENT_CAPTION_BARE,
  documentCaption,
  documentFacts,
  documentIsAnalysing,
  documentKindOf,
  documentPartPhrase,
  documentStepLabel,
  documentView,
  lastAnswerRefs,
  previousDocument,
  refKind,
} from "../../app/lib/uistate/documents";
import {
  DOCUMENT_EMPTY,
  DOCUMENT_LABEL,
  KIND_LABEL,
  STATE_LABEL,
  contractLagNote,
  documentFactsLine,
  documentRefLine,
  stateLabel,
  subsystemLabel,
} from "../../app/lib/uistate/labels";
import { applyError, applyResponse, coreClaim, documentClaim, emptyTruth } from "../../app/lib/uistate/truth";
import {
  type VisualIntent,
  type VoiceOverlay,
  applyVoiceOverlay,
  isDocumentReading,
  isOperatorActing,
  visualFor,
} from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  DOCUMENT_ANALYSIS,
  DOCUMENT_ANALYSIS_BARE,
  DOCUMENT_ANSWERED,
  OPERATOR_RUNNING,
  OWNER_AWAY,
  T0,
  event,
  iso,
  resetSequence,
  response,
} from "./fixtures";

// The sequence counter is module-level, and `truthOf` resets it AFTER its argument list has
// already been evaluated - JavaScript builds `[DOCUMENT_ANSWERED(...), ...]` before the call.
// So the reset never applied to the events being passed in; it applied to the NEXT caller's,
// and every assertion about an absolute `sequence` was really an assertion that the previous
// test had left the counter at zero. Run in a different order - which `--sequence.shuffle`
// does - and `expect(sequence).toBe(1)` saw 4.
//
// Resetting before each test makes the numbering a property of the test rather than of the
// file's declaration order. The reset inside `truthOf` stays: it is what lets a single test
// build two independent truths and have both start at 1.
beforeEach(resetSequence);

function truthOf(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return applyResponse(emptyTruth(), response(events), at);
}

function intentOf(events: ReturnType<typeof event>[], at = T0) {
  return visualFor(truthOf(events), at);
}

function viewOf(events: ReturnType<typeof event>[], at = T0) {
  return documentView(documentClaim(truthOf(events), at));
}

/** Every channel that means "the Core itself is moving". */
const MOTION_CHANNELS = [
  "breathAmplitude",
  "inwardFlow",
  "topology",
  "pulse",
  "agitation",
  "ringSpin",
  "flowRate",
  "ownerVoice",
  "constellationDrift",
  "energy",
] as const satisfies readonly (keyof VisualIntent)[];

const VOICE_TOOL_RUNNING: VoiceOverlay = {
  state: "tool_running",
  micLevel: null,
  outputLevel: null,
  caption: null,
  toolLabel: "Belge",
  lastError: null,
};

describe("contract v5 is v4 plus the document state, and says so", () => {
  it("still reads a v5, v4, v3 and v2 server from a build at v5 or later", () => {
    // v6 (M21) bumped the build past this file's contract; the assertion is
    // relative, as the v4 file's became when v5 landed, so the v5 additions
    // stay proven without pinning the build to a version it has left.
    expect(KNOWN_CONTRACT_VERSION).toBeGreaterThanOrEqual(5);
    expect(MIN_SUPPORTED_CONTRACT_VERSION).toBe(2);
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION)).toBe("current");
    expect(contractCompatibility(4)).toBe("older_supported");
    expect(contractCompatibility(3)).toBe("older_supported");
    expect(contractCompatibility(2)).toBe("older_supported");
    // A server ahead of this build is a different problem: we do not know its
    // vocabulary, so nothing is drawn from it.
    expect(contractCompatibility(KNOWN_CONTRACT_VERSION + 1)).toBe("unsupported");
    expect(contractCompatibility(1)).toBe("unsupported");
  });

  it("knows the one document state, by membership rather than by prefix", () => {
    expect(DOCUMENT_STATES).toEqual(["document.analysis"]);
    expect(UI_STATES).toContain("document.analysis");
    // Appended after the last v4 token, never reordered; v6 appends after it in turn.
    expect(UI_STATES.indexOf("document.analysis")).toBe(UI_STATES.indexOf("operator.failed") + 1);
    expect(isKnownState("document.analysis")).toBe(true);
    expect(isDocumentState("document.analysis")).toBe(true);
    // A newer server's word is not a state this build may draw as reading.
    expect(isDocumentState("document.indexing")).toBe(false);
    expect(isDocumentState("agent.tool_running")).toBe(false);
    expect(isOperatorState("document.analysis")).toBe(false);
  });

  it("adds the documents subsystem and names it", () => {
    expect(SUBSYSTEMS).toContain("documents");
    expect(subsystemLabel("documents")).toBe("Belgeler");
  });

  it("keeps the document on the agent channel, which drives the core body", () => {
    expect(stateChannel("document.analysis")).toBe("agent");
    expect(isCoreChannel("document.analysis")).toBe(true);
    // The room is untouched by the addition.
    expect(stateChannel("owner.away")).toBe("ambient");
    expect(stateChannel("operator.running")).toBe("operator");
  });

  it("classifies a read as transient, on the companion's command horizon", () => {
    expect(stateKind("document.analysis")).toBe("transient");
    expect(DOCUMENT_STEP_TTL_MS).toBe(45_000);
    expect(DOCUMENT_STEP_TTL_MS).toBeGreaterThan(TRANSIENT_TTL_MS);
    expect(stateTtlMs("document.analysis")).toBe(DOCUMENT_STEP_TTL_MS);
    // The publisher's own ttl_s still beats every figure here.
    const declared = event({ state: "document.analysis", subsystem: "documents", metadata: { ttl_s: 10 } });
    expect(stateTtlMs("document.analysis", declared)).toBe(10_000);
  });

  it("gives the state the Turkish the spec asks for", () => {
    expect(STATE_LABEL["document.analysis"]).toBe("Belge inceleniyor");
    expect(KIND_LABEL.document_analysis).toBe("Belge inceleniyor");
    expect(DOCUMENT_LABEL.analysing).toBe("Belge inceleniyor");
    expect(DOCUMENT_LABEL.none).not.toBe("Boşta");
    expect(DOCUMENT_EMPTY).toBe("Henüz bir belge okunmadı.");
    expect(stateLabel("document.analysis")).not.toBe("document.analysis");
  });

  it("names exactly the families an older server will never publish", () => {
    const v4 = contractLagNote(4, 5);
    expect(v4).toContain("v4");
    expect(v4).toContain("v5");
    expect(v4).toContain("Belge inceleme durumu");
    expect(v4).not.toContain("operatör");
    expect(v4).toContain("yok demek değil");

    const v3 = contractLagNote(3, 5);
    expect(v3).toContain("Dijital operatör durumları");
    expect(v3).toContain("belge inceleme durumu");

    const v2 = contractLagNote(2, 5);
    expect(v2).toContain("Alarm ve ekran durumları");
    expect(v2).toContain("dijital operatör durumları");
    expect(v2).toContain("belge inceleme durumu");

    // The v4 wording M19 shipped is unchanged for a v3 server seen from v4.
    expect(contractLagNote(3, 4)).toContain("Dijital operatör durumları bu sunucudan henüz yayınlanmıyor");
  });

  it("leaves every v4 token, kind and horizon exactly as it was", () => {
    expect(OPERATOR_STATES).toEqual(["operator.running", "operator.verifying", "operator.failed"]);
    for (const state of OPERATOR_STATES) expect(isKnownState(state), state).toBe(true);
    expect(stateKind("operator.failed")).toBe("steady");
    expect(stateTtlMs("operator.running")).toBe(45_000);
    expect(stateTtlMs("agent.thinking")).toBe(TRANSIENT_TTL_MS);
    expect(stateKind("agent.idle")).toBe("steady");
    expect(UI_STATES.indexOf("operator.failed")).toBe(UI_STATES.indexOf("document.analysis") - 1);
  });
});

describe("the refs, at the boundary", () => {
  it("reads metadata.refs as [{ref, path, excerpt}] and keeps everything else content-free", () => {
    const e = parseEvent({
      state: "document.analysis",
      at: iso(0),
      metadata: {
        file: "rapor.pdf",
        part: "p3",
        refs: [
          { ref: "p3", path: "C:\\Users\\alpak\\Documents\\rapor.pdf", excerpt: "Üçüncü sayfada yer alan bu cümle referans testidir." },
          { ref: "h2:Kararlar", path: "C:\\Users\\alpak\\Documents\\notlar.md" },
        ],
        nested: { a: 1 },
        list: [1, 2],
      },
    });
    expect(e?.metadata).toEqual({ file: "rapor.pdf", part: "p3" });
    expect(e?.refs).toEqual([
      { ref: "p3", path: "C:\\Users\\alpak\\Documents\\rapor.pdf", excerpt: "Üçüncü sayfada yer alan bu cümle referans testidir." },
      { ref: "h2:Kararlar", path: "C:\\Users\\alpak\\Documents\\notlar.md", excerpt: null },
    ]);
  });

  it("does not put a refs field on an event that carried none — a v4 event parses as before", () => {
    const e = parseEvent({ state: "operator.running", at: iso(0), metadata: { step: "open_notepad" } });
    expect(e).not.toHaveProperty("refs");
    const noRefs = parseEvent({ state: "document.analysis", at: iso(0), metadata: { file: "rapor.pdf", refs: [] } });
    expect(noRefs).not.toHaveProperty("refs");
  });

  it("drops a ref without a ref, bounds every string and caps the list — never inventing one", () => {
    expect(parseDocumentRefs("p3")).toEqual([]);
    expect(parseDocumentRefs([null, 3, "p3", { path: "x" }, { ref: "" }])).toEqual([]);
    const long = "x".repeat(MAX_LABEL_CHARS + 10);
    const [bounded] = parseDocumentRefs([{ ref: long, path: long, excerpt: long }]);
    expect(bounded.ref).toHaveLength(MAX_LABEL_CHARS);
    expect(bounded.path).toHaveLength(MAX_LABEL_CHARS);
    expect(bounded.excerpt).toHaveLength(MAX_LABEL_CHARS);
    const many = Array.from({ length: MAX_DOCUMENT_REFS + 4 }, (_, i) => ({ ref: `p${i + 1}` }));
    expect(parseDocumentRefs(many)).toHaveLength(MAX_DOCUMENT_REFS);
    // A non-string path or excerpt is not a token anyone published.
    expect(parseDocumentRefs([{ ref: "r7", path: 12, excerpt: true }])).toEqual([
      { ref: "r7", path: null, excerpt: null },
    ]);
  });
});

describe("the place, in the words the Cloud Core speaks (spec §3)", () => {
  it("speaks every ref form of the scheme", () => {
    expect(documentPartPhrase("p3", "pdf")).toBe("3. sayfa");
    expect(documentPartPhrase("p8", "docx")).toBe("8. paragraf");
    expect(documentPartPhrase("p2", "txt")).toBe("2. paragraf");
    expect(documentPartPhrase("s4", "pptx")).toBe("4. slayt");
    expect(documentPartPhrase("sheet:Ozet!A5:B5", "xlsx")).toBe("Ozet sayfası, 5. satır");
    expect(documentPartPhrase("sheet:Detay!A12:E12", "xlsx")).toBe("Detay sayfası, 12. satır");
    expect(documentPartPhrase("h2:Kararlar", "md")).toBe("Kararlar bölümü");
    expect(documentPartPhrase("h1:Toplantı Notları", "md")).toBe("Toplantı Notları bölümü");
    expect(documentPartPhrase("r7", "csv")).toBe("7. satır");
    expect(documentPartPhrase("$.ses", "json")).toBe("ses anahtarı");
    expect(documentPartPhrase("L1-40", "source")).toBe("1-40. satırlar");
    expect(documentPartPhrase("L41-80", "source")).toBe("41-80. satırlar");
  });

  it("reads a p-ref by the kind the file name tells, and takes the general wording without one", () => {
    expect(documentKindOf("rapor.pdf")).toBe("pdf");
    expect(documentKindOf("sozlesme.DOCX")).toBe("docx");
    expect(documentKindOf("kod.py")).toBe("source");
    expect(documentKindOf("ayarlar.json")).toBe("json");
    expect(documentKindOf("okubeni")).toBe("unknown");
    expect(documentKindOf("arsiv.rar")).toBe("unknown");
    // B32: pictures and zip archives are kinds of their own.
    expect(documentKindOf("metin.PNG")).toBe("image");
    expect(documentKindOf("foto.jpeg")).toBe("image");
    expect(documentKindOf("arsiv.zip")).toBe("archive");
    expect(documentKindOf(null)).toBeNull();
    // Only PDF, DOCX and TXT produce a p-ref (§2); a name the client cannot
    // classify gets the spec's general wording rather than a guess at a kind.
    expect(documentPartPhrase("p3", "unknown")).toBe("3. sayfa");
    expect(documentPartPhrase("p3", null)).toBe("3. sayfa");
  });

  it("shows a ref the spec gave no wording for verbatim, and nothing for no ref", () => {
    expect(documentPartPhrase("t2", "docx")).toBe("t2"); // a DOCX table: §3 fixes no words
    expect(documentPartPhrase("block:9", "pdf")).toBe("block:9"); // a newer scheme
    expect(documentPartPhrase("p", "pdf")).toBe("p");
    expect(documentPartPhrase("sheet:Ozet", "xlsx")).toBe("sheet:Ozet");
    expect(documentPartPhrase(null, "pdf")).toBeNull();
  });

  it("speaks the step when its verb is known and prints it as sent otherwise", () => {
    expect(documentStepLabel("answer")).toBe("yanıtlıyor");
    expect(documentStepLabel("document.answer")).toBe("yanıtlıyor");
    expect(documentStepLabel("file.search")).toBe("arıyor");
    expect(documentStepLabel("document.extract")).toBe("çıkarıyor");
    expect(documentStepLabel("compare")).toBe("karşılaştırıyor");
    expect(documentStepLabel("common_points")).toBe("ortak noktaları buluyor");
    expect(documentStepLabel("ocr")).toBe("ocr");
    expect(documentStepLabel(null)).toBeNull();
  });
});

describe("facts, view and caption from published metadata only", () => {
  it("reads the file, the part and the step verbatim, and the kind from the name", () => {
    const facts = documentFacts(DOCUMENT_ANALYSIS("sozlesme.docx", "p8", "summarize"));
    expect(facts).toEqual({ file: "sozlesme.docx", path: null, part: "p8", step: "summarize", kind: "docx" });
    expect(documentCaption(facts)).toBe("sozlesme.docx · 8. paragraf · özetliyor");
    expect(documentFactsLine(facts)).toBe("dosya: sozlesme.docx · yer: 8. paragraf · adım: özetliyor");
  });

  it("captions with the same words the Cloud Core speaks for every ref form", () => {
    const cases: Array<[string, string, string]> = [
      ["rapor.pdf", "p3", "rapor.pdf · 3. sayfa"],
      ["sozlesme.docx", "p8", "sozlesme.docx · 8. paragraf"],
      ["sunum-q3.pptx", "s4", "sunum-q3.pptx · 4. slayt"],
      ["butce-2026.xlsx", "sheet:Ozet!A5:B5", "butce-2026.xlsx · Ozet sayfası, 5. satır"],
      ["notlar.md", "h2:Kararlar", "notlar.md · Kararlar bölümü"],
      ["veri.csv", "r7", "veri.csv · 7. satır"],
      ["ayarlar.json", "$.ses", "ayarlar.json · ses anahtarı"],
      ["kod.py", "L1-40", "kod.py · 1-40. satırlar"],
      ["sozlesme.docx", "t2", "sozlesme.docx · t2"],
    ];
    for (const [file, part, expected] of cases) {
      expect(documentCaption(documentFacts(DOCUMENT_ANALYSIS(file, part, null))), part).toBe(expected);
    }
  });

  it("says only that a document is being read when the publisher named no file", () => {
    // No file, but a part and a step: a place with no document is not a place.
    const facts = documentFacts(DOCUMENT_ANALYSIS(null, "p3", "answer"));
    expect(facts.file).toBeNull();
    expect(facts.kind).toBeNull();
    expect(documentCaption(facts)).toBe(DOCUMENT_CAPTION_BARE);
    expect(DOCUMENT_CAPTION_BARE).toBe("Belge inceleniyor");
    // The long line still says what WAS published, and what was not.
    expect(documentFactsLine(facts)).toBe("dosya bildirilmedi · yer: 3. sayfa · adım: yanıtlıyor");
  });

  it("invents nothing when the publisher sent no metadata at all", () => {
    const facts = documentFacts(DOCUMENT_ANALYSIS_BARE());
    expect(facts).toEqual({ file: null, path: null, part: null, step: null, kind: null });
    expect(documentCaption(facts)).toBe("Belge inceleniyor");
    expect(documentFactsLine(facts)).toBe("dosya bildirilmedi · yer bildirilmedi · adım bildirilmedi");
    expect(documentFacts(null)).toEqual({ file: null, path: null, part: null, step: null, kind: null });
  });

  it("carries the path only when the Core chose to name the file by it", () => {
    const byName = documentFacts(DOCUMENT_ANALYSIS("sozlesme.docx", "p3"));
    expect(byName.path).toBeNull();
    const byPath = documentFacts(
      DOCUMENT_ANALYSIS("sozlesme.docx", "p3", "answer", { path: "C:\\Users\\alpak\\Documents\\sozlesmeler\\2026\\sozlesme.docx" }),
    );
    expect(byPath.path).toBe("C:\\Users\\alpak\\Documents\\sozlesmeler\\2026\\sozlesme.docx");
    // The caption stays the name: the path is the panel's fact, not the Core's line.
    expect(documentCaption(byPath)).toBe("sozlesme.docx · 3. paragraf · yanıtlıyor");
  });

  it("the view is analysing while live, with the phrase, the step and the refs", () => {
    const view = viewOf([DOCUMENT_ANSWERED()]);
    expect(view.stage).toBe("analysing");
    expect(view.lastKnown).toBe("analysing");
    expect(view.file).toBe("rapor.pdf");
    expect(view.partPhrase).toBe("3. sayfa");
    expect(view.stepLabel).toBe("yanıtlıyor");
    expect(view.taskId).toBe("doc-task-1");
    expect(view.refs).toHaveLength(1);
    expect(view.refs[0].ref).toBe("p3");
    expect(documentIsAnalysing(view)).toBe(true);
  });

  it("the view is none, with nothing last-known, before any document was published", () => {
    const view = viewOf([AGENT_IDLE(), OPERATOR_RUNNING()]);
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBeNull();
    expect(view.file).toBeNull();
    expect(view.refs).toEqual([]);
    expect(documentIsAnalysing(view)).toBe(false);
  });

  it("a ref line is dosya · yer · alıntı, the place read from the cited file's own kind", () => {
    expect(
      documentRefLine({ ref: "p3", path: "C:\\Users\\alpak\\Documents\\rapor.pdf", excerpt: "Üçüncü sayfada yer alan bu cümle referans testidir." }),
    ).toBe("C:\\Users\\alpak\\Documents\\rapor.pdf · 3. sayfa · Üçüncü sayfada yer alan bu cümle referans testidir.");
    // The same `p3` in a DOCX is a paragraph — the kind is the cited file's.
    expect(refKind({ ref: "p3", path: "D:\\sozlesmeler\\2025\\sozlesme.docx", excerpt: null })).toBe("docx");
    expect(documentRefLine({ ref: "p3", path: "D:\\sozlesmeler\\2025\\sozlesme.docx", excerpt: "Madde 3: 30 gün" })).toBe(
      "D:\\sozlesmeler\\2025\\sozlesme.docx · 3. paragraf · Madde 3: 30 gün",
    );
    expect(documentRefLine({ ref: "sheet:Ozet!A5:B5", path: "/home/x/butce-2026.xlsx", excerpt: "Toplam\t65000" })).toBe(
      "/home/x/butce-2026.xlsx · Ozet sayfası, 5. satır · Toplam\t65000",
    );
    // What was not sent is said to be missing, never filled in.
    expect(documentRefLine({ ref: "r7", path: null, excerpt: null })).toBe("dosya bildirilmedi · 7. satır · alıntı bildirilmedi");
    expect(refKind({ ref: "r7", path: null, excerpt: null })).toBeNull();
  });
});

describe("the Core draws the reading posture from the published state", () => {
  it("is its own calm kind, with the caption from the file and the place", () => {
    const intent = intentOf([DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")]);
    expect(intent.kind).toBe("document_analysis");
    expect(intent.source).toBe("bus");
    expect(intent.subsystem).toBe("documents");
    expect(intent.palette).toBe("reading");
    expect(intent.label).toBe("rapor.pdf · 3. sayfa · yanıtlıyor");
    expect(intent.document).toEqual({ file: "rapor.pdf", path: null, part: "p3", step: "answer", kind: "pdf" });
    expect(isDocumentReading(intent)).toBe(true);
    expect(isOperatorActing(intent)).toBe(false);

    // The posture: the same language as a tool at work, turned inward and
    // calmed — information comes in; the shells sit closer than a plain
    // tool's, the rings turn slowly, nothing agitates, nothing is held back.
    const tool = intentOf([event({ state: "agent.tool_running" })]);
    const idle = intentOf([AGENT_IDLE()]);
    expect(intent.inwardFlow).toBeGreaterThan(0);
    expect(tool.inwardFlow).toBe(0);
    expect(intent.flowRate).toBeLessThan(tool.flowRate);
    expect(intent.shellSpread).toBeLessThan(tool.shellSpread);
    expect(intent.shellSpread).toBeGreaterThan(idle.shellSpread);
    expect(intent.ringSpin).toBeLessThan(tool.ringSpin);
    expect(intent.ringSpin).toBeGreaterThan(idle.ringSpin);
    expect(intent.topology).toBeGreaterThan(0);
    expect(intent.agitation).toBe(0);
    expect(intent.restraint).toBe(0);
    expect(intent.pulse).toBe(0);
  });

  it("never draws progress, a constellation or a candidate: none is published", () => {
    for (const events of [[DOCUMENT_ANALYSIS()], [DOCUMENT_ANSWERED()], [DOCUMENT_ANALYSIS_BARE()]]) {
      const intent = intentOf(events);
      expect(intent.progress).toBeNull();
      expect(intent.constellationNodes).toBe(0);
      expect(intent.constellationDrift).toBe(0);
      expect(intent.capabilityNodes).toBe(0);
      expect(intent.satelliteComplete).toBe(false);
    }
  });

  it("captions the bare statement when the publisher named no file", () => {
    const intent = intentOf([DOCUMENT_ANALYSIS_BARE()]);
    expect(intent.kind).toBe("document_analysis");
    expect(intent.label).toBe("Belge inceleniyor");
    expect(intent.document).toEqual({ file: null, path: null, part: null, step: null, kind: null });
  });

  it("does not let the room displace a read in flight", () => {
    const intent = intentOf([DOCUMENT_ANALYSIS(), OWNER_AWAY()]);
    expect(intent.kind).toBe("document_analysis");
  });

  it("leaves every other kind's `document` null", () => {
    expect(intentOf([AGENT_IDLE()]).document).toBeNull();
    expect(intentOf([OPERATOR_RUNNING()]).document).toBeNull();
    expect(intentOf([event({ state: "agent.tool_running" })]).document).toBeNull();
  });
});

describe("honesty over time", () => {
  it("a read is live for its horizon and last-known after it, with its facts kept", () => {
    const truth = truthOf([DOCUMENT_ANALYSIS("rapor.pdf", "p3", "answer")]);
    const live = visualFor(truth, T0 + DOCUMENT_STEP_TTL_MS - 1);
    expect(live.kind).toBe("document_analysis");

    const stale = visualFor(truth, T0 + DOCUMENT_STEP_TTL_MS + 1);
    expect(stale.kind).toBe("last_known");
    expect(stale.state).toBe("document.analysis");
    for (const channel of MOTION_CHANNELS) expect(stale[channel], channel).toBe(0);
    // What it WAS reading is still a fact; the readout names it as last-known.
    expect(stale.label).toBe("rapor.pdf · 3. sayfa · yanıtlıyor");
    expect(stale.document?.file).toBe("rapor.pdf");
    expect(isDocumentReading(stale)).toBe(false);
  });

  it("an expired read is none-but-last-known, never finished", () => {
    const view = viewOf([DOCUMENT_ANALYSIS()], T0 + 60_000);
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBe("analysing");
    expect(view.expired).toBe(true);
    expect(view.ageMs).toBe(60_000);
    expect(view.file).toBe("rapor.pdf");
    expect(documentIsAnalysing(view)).toBe(false);
  });

  it("is replaced by the agent's next word, and never outlives it", () => {
    const replaced = truthOf([DOCUMENT_ANALYSIS(), AGENT_IDLE()]);
    expect(visualFor(replaced, T0).kind).toBe("idle");
    expect(coreClaim(replaced, T0).event?.state).toBe("agent.idle");
    // The cockpit's own claim still knows which document it was.
    expect(documentView(documentClaim(replaced, T0)).file).toBe("rapor.pdf");
  });

  it("an unreachable API keeps the shape and the facts, and stops the motion", () => {
    let truth = truthOf([DOCUMENT_ANALYSIS()]);
    truth = applyError(truth, "ağ koptu", T0);
    const intent = visualFor(truth, T0);
    expect(intent.kind).toBe("unreachable");
    expect(intent.state).toBe("document.analysis");
    expect(intent.document?.file).toBe("rapor.pdf");
    expect(intent.flowRate).toBe(0);
    expect(intent.ringSpin).toBe(0);
    expect(intent.inwardFlow).toBe(0);
  });
});

describe("the history the bus itself carried", () => {
  it("the previous document is the last different file published before the current one", () => {
    const truth = truthOf([
      DOCUMENT_ANALYSIS("notlar.md", "h2:Kararlar", "read"),
      DOCUMENT_ANALYSIS("sozlesme.docx", "p3", "read"),
      DOCUMENT_ANALYSIS("sozlesme.docx", "p8", "answer"), // same file, another place: not a previous
      AGENT_IDLE(),
    ]);
    const current = documentClaim(truth, T0).event;
    expect(current?.metadata.file).toBe("sozlesme.docx");
    const previous = previousDocument(truth, current);
    expect(previous?.facts.file).toBe("notlar.md");
    expect(previous?.facts.part).toBe("h2:Kararlar");
    expect(previous?.event.sequence).toBe(1);
  });

  it("is null when the tail holds no earlier, different file — never a document the bus did not carry", () => {
    const one = truthOf([DOCUMENT_ANALYSIS("rapor.pdf", "p3")]);
    expect(previousDocument(one, documentClaim(one, T0).event)).toBeNull();
    const samePlaceTwice = truthOf([DOCUMENT_ANALYSIS("rapor.pdf", "p1"), DOCUMENT_ANALYSIS("rapor.pdf", "p3")]);
    expect(previousDocument(samePlaceTwice, documentClaim(samePlaceTwice, T0).event)).toBeNull();
    // An event with no file is nobody's previous document.
    const nameless = truthOf([DOCUMENT_ANALYSIS_BARE(), DOCUMENT_ANALYSIS("rapor.pdf", "p3")]);
    expect(previousDocument(nameless, documentClaim(nameless, T0).event)).toBeNull();
    expect(previousDocument(truthOf([AGENT_IDLE()]), null)).toBeNull();
  });

  it("the last answer's refs come from the newest document event that cited any", () => {
    const truth = truthOf([
      DOCUMENT_ANSWERED([{ ref: "p3", path: "C:\\x\\rapor.pdf", excerpt: "eski" }]),
      DOCUMENT_ANALYSIS("rapor.pdf", "p4", "read"), // a later read with no refs does not erase the answer
    ]);
    const answer = lastAnswerRefs(truth);
    expect(answer?.refs).toEqual([{ ref: "p3", path: "C:\\x\\rapor.pdf", excerpt: "eski" }]);
    expect(answer?.event.sequence).toBe(1);
    expect(lastAnswerRefs(truthOf([DOCUMENT_ANALYSIS()]))).toBeNull();
    expect(lastAnswerRefs(truthOf([AGENT_IDLE()]))).toBeNull();
  });
});

describe("older servers", () => {
  it("draws a v4 feed without the document token normally, and records the lag", () => {
    resetSequence();
    const idle = AGENT_IDLE();
    const v4 = applyResponse(
      emptyTruth(),
      { contract_version: 4, current: idle, events: [idle], sequence: idle.sequence },
      T0,
    );
    expect(v4.connection.kind).toBe("live");
    expect(v4.contractVersion).toBe(4);
    expect(visualFor(v4, T0).kind).toBe("idle");
    const view = documentView(documentClaim(v4, T0));
    expect(view.stage).toBe("none");
    expect(view.lastKnown).toBeNull();
    expect(contractLagNote(4, KNOWN_CONTRACT_VERSION)).toContain("yok demek değil");
  });

  it("a newer server's document word is not read as a read, and the Core says it cannot read it", () => {
    const unknown = event({ state: "document.indexing", subsystem: "documents" });
    const truth = truthOf([DOCUMENT_ANALYSIS(), unknown]);
    expect(documentView(documentClaim(truth, T0)).stage).toBe("analysing");
    expect(visualFor(truth, T0).kind).toBe("unknown_state");
  });
});

describe("the voice overlay and the reading Core", () => {
  it("a local tool_running does not hide a live read: the bus knows which file and which page", () => {
    const bus = intentOf([DOCUMENT_ANALYSIS()]);
    const drawn = applyVoiceOverlay(bus, VOICE_TOOL_RUNNING);
    expect(drawn).toBe(bus);
    expect(drawn.kind).toBe("document_analysis");
    expect(drawn.label).toBe("rapor.pdf · 3. sayfa · yanıtlıyor");
  });

  it("but every other local state, and a last-known read, keep the overlay's precedence", () => {
    const bus = intentOf([DOCUMENT_ANALYSIS()]);
    const speaking = applyVoiceOverlay(bus, { ...VOICE_TOOL_RUNNING, state: "speaking", outputLevel: 0.4 });
    expect(speaking.kind).toBe("speaking");
    expect(speaking.source).toBe("voice");

    const stale = visualFor(truthOf([DOCUMENT_ANALYSIS()]), T0 + DOCUMENT_STEP_TTL_MS + 1);
    const overStale = applyVoiceOverlay(stale, VOICE_TOOL_RUNNING);
    expect(overStale.kind).toBe("tool_running");
    expect(overStale.source).toBe("voice");
  });
});
