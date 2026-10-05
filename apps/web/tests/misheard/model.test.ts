/**
 * The misheard notebook's row model: what one row shows, in Turkish, and never "null".
 */

import { describe, expect, it } from "vitest";

import {
  MEANT_MAX,
  REASONS,
  canSave,
  modeLabel,
  reasonSentence,
  retentionSentence,
  rowView,
} from "../../app/core/misheard/misheardModel";
import { item } from "./fixtures";

describe("the reason", () => {
  it("has its own Turkish sentence for each of the four reasons", () => {
    expect(REASONS.toSorted()).toEqual(["asked_question", "no_intent", "objected", "tool_failed"]);
    const sentences = REASONS.map(reasonSentence);
    // Four reasons, four DIFFERENT sentences: a table collapsed to one sentence is a menu
    // with fewer behaviours than options.
    expect(new Set(sentences).size).toBe(4);
    for (const sentence of sentences) {
      expect(sentence).not.toBe("");
      expect(REASONS).not.toContain(sentence);
    }
  });

  it("shows an unknown reason as itself, never hidden", () => {
    expect(reasonSentence("timed_out")).toBe("timed_out");
    expect(rowView(item({ reason: "timed_out" })).reason).toBe("timed_out");
  });
});

describe("the mode", () => {
  it("is Turkish", () => {
    expect(modeLabel("paid")).toBe("Ücretli");
    expect(modeLabel("local")).toBe("Yerel");
    expect(rowView(item({ mode: "local" })).mode).toBe("Yerel");
  });
});

describe("a row with nothing known beside the sentence", () => {
  it("renders without the word null or undefined", () => {
    const view = rowView(
      item({ engine: null, device_id: null, band: null, confidence: null, tool: null, meant: null }),
    );
    const text = JSON.stringify(Object.values(view).filter((value) => value !== null));
    expect(text).not.toMatch(/null|undefined/);
    expect(view.machine).toBeNull();
    expect(view.band).toBeNull();
    expect(view.engine).toBeNull();
  });

  it("shows the machine, the band and the engine when they are known", () => {
    const view = rowView(item());
    expect(view.machine).toContain("22222222");
    expect(view.band).toBe("orta güven");
    expect(view.engine).toContain("gpt-4o-transcribe");
  });
});

describe("the time", () => {
  it("is the owner's local time, not the UTC stamp", () => {
    const view = rowView(item({ heard_at: "2026-10-03T07:05:00+00:00" }), "Europe/Istanbul");
    expect(view.when).toContain("10:05");
    expect(view.when).not.toContain("07:05");
  });
});

describe("the sentence and the meaning", () => {
  it("shows the sentence as the recogniser wrote it, and the meaning once answered", () => {
    expect(rowView(item({ sentence: "ışığı kıs" })).sentence).toBe("ışığı kıs");
    expect(rowView(item()).meant).toBeNull();
    expect(rowView(item({ meant: "Işığı aç." })).meant).toBe("Işığı aç.");
  });
});

describe("Kaydet", () => {
  it("is allowed for 1..2000 characters only", () => {
    expect(MEANT_MAX).toBe(2000);
    expect(canSave("")).toBe(false);
    expect(canSave("   ")).toBe(false);
    expect(canSave("a")).toBe(true);
    expect(canSave("ş".repeat(2000))).toBe(true);
    expect(canSave("ş".repeat(2001))).toBe(false);
  });
});

describe("the retention sentence", () => {
  it("carries the number it is given, and says no sound is kept", () => {
    expect(retentionSentence(30)).toContain("30");
    expect(retentionSentence(7)).toContain("7");
    expect(retentionSentence(7)).not.toContain("30");
    expect(retentionSentence(7)).toMatch(/ses/);
  });
});
