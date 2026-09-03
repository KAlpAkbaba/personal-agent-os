import { describe, expect, it } from "vitest";

import {
  DEFAULT_MAX_SOURCES,
  DEFAULT_RECENCY_DAYS,
  DEFAULT_TOPIC,
  LABEL_BADGE,
  STAGE_LABEL,
  STATEMENT_LABELS,
  TERMINAL_STAGES,
  advertisesBrowser,
  citationsFor,
  clampMaxSources,
  clampRecencyDays,
  describeTaskError,
  importanceDots,
  isTerminal,
  normaliseSelection,
  presenceOf,
  stageLabel,
} from "../../app/lib/research/model";
import { REPORT } from "./fixtures";

const ALL_STAGES = [
  "planned",
  "selecting_device",
  "discovering",
  "fetching",
  "ranking",
  "synthesizing",
  "persisting",
  "ready",
  "failed",
  "cancelled",
] as const;

describe("stage labels", () => {
  it("has a Turkish label for every spec §4 stage and nothing else", () => {
    expect(Object.keys(STAGE_LABEL).toSorted()).toEqual(ALL_STAGES.toSorted());
    for (const stage of ALL_STAGES) {
      const label = STAGE_LABEL[stage];
      expect(label.length).toBeGreaterThan(0);
      expect(label).not.toBe(stage);
      expect(stageLabel(stage)).toBe(label);
    }
    expect(stageLabel("ready")).toBe("Hazır");
    expect(stageLabel("failed")).toBe("Başarısız");
    expect(stageLabel("cancelled")).toBe("İptal edildi");
    expect(stageLabel("selecting_device")).toBe("Cihaz seçiliyor");
  });

  it("falls back for unknown or missing stages", () => {
    expect(stageLabel("something_new")).toBe("something_new");
    expect(stageLabel(null)).toBe("Bilinmiyor");
  });

  it("knows which stages are terminal", () => {
    expect([...TERMINAL_STAGES].toSorted()).toEqual(["cancelled", "failed", "ready"]);
    for (const stage of ALL_STAGES) {
      expect(isTerminal({ stage, status: "RUNNING" })).toBe(TERMINAL_STAGES.has(stage));
    }
    // A terminal task status ends polling even when the stage field lags.
    expect(isTerminal({ stage: "persisting", status: "READY" })).toBe(true);
    expect(isTerminal({ stage: "fetching", status: "FAILED" })).toBe(true);
    expect(isTerminal({ stage: null, status: "PLANNED" })).toBe(false);
  });
});

describe("statement labels", () => {
  it("renders the four labels as the agreed Turkish badges", () => {
    expect(STATEMENT_LABELS).toEqual(["source_fact", "model_inference", "recommendation", "uncertainty"]);
    expect(LABEL_BADGE).toEqual({
      source_fact: "kaynak bulgusu",
      model_inference: "model çıkarımı",
      recommendation: "öneri",
      uncertainty: "belirsizlik",
    });
  });
});

describe("citations", () => {
  it("maps evidence ids to sources and keeps unknown ids visible", () => {
    const cites = citationsFor(["e1", "e9", "e3"], REPORT.sources);
    expect(cites.map((c) => c.id)).toEqual(["e1", "e9", "e3"]);
    expect(cites[0].source?.publisher).toBe("Framework A");
    expect(cites[1].source).toBeNull();
    expect(cites[2].source?.injection_suspected).toBe(true);
    expect(citationsFor(undefined, REPORT.sources)).toEqual([]);
  });

  it("draws importance as five dots and clamps", () => {
    expect(importanceDots(5)).toBe("●●●●●");
    expect(importanceDots(3)).toBe("●●●○○");
    expect(importanceDots(0)).toBe("○○○○○");
    expect(importanceDots(9)).toBe("●●●●●");
    expect(importanceDots(Number.NaN)).toBe("○○○○○");
  });
});

describe("devices", () => {
  it("reads presence from the M13 field and falls back to the legacy status", () => {
    expect(presenceOf({ presence: "stale", status: "online" })).toBe("stale");
    expect(presenceOf({ status: "online" })).toBe("online");
    expect(presenceOf({ status: "revoked" })).toBe("revoked");
    expect(presenceOf({})).toBe("unknown");
    expect(advertisesBrowser({ capabilities: ["browser.chrome"] })).toBe(true);
    expect(advertisesBrowser({ capabilities: ["browser.search"] })).toBe(false);
    expect(advertisesBrowser({})).toBe(false);
  });

  it("normalises the selection result across plausible shapes", () => {
    expect(normaliseSelection({ device: { device_id: "d1", name: "Ev" }, reason: "explicit" })).toEqual({
      kind: "device",
      device_id: "d1",
      name: "Ev",
      reason: "explicit",
    });
    expect(normaliseSelection({ device_id: "d2", name: "Laptop" })).toMatchObject({ kind: "device", device_id: "d2" });
    expect(normaliseSelection({ device: null, detail: "Uygun cihaz yok." })).toEqual({
      kind: "none",
      detail: "Uygun cihaz yok.",
    });
    expect(normaliseSelection(null)).toEqual({ kind: "none", detail: "Uygun cihaz bulunamadı." });
  });
});

describe("inputs and defaults", () => {
  it("prefills the first owner use case and clamps the optional numbers", () => {
    expect(DEFAULT_TOPIC).toBe("Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeleri araştır.");
    expect(DEFAULT_RECENCY_DAYS).toBe(3);
    expect(DEFAULT_MAX_SOURCES).toBe(12);
    expect(clampRecencyDays(0)).toBe(1);
    expect(clampRecencyDays(14)).toBe(14);
    expect(clampRecencyDays(99)).toBe(14);
    expect(clampRecencyDays(Number.NaN)).toBe(3);
    expect(clampMaxSources(31)).toBe(30);
    expect(clampMaxSources(0)).toBe(1);
    expect(clampMaxSources(Number.NaN)).toBe(12);
  });

  it("describes task errors from either shape", () => {
    expect(describeTaskError(null)).toBeNull();
    expect(describeTaskError("Cihaz yok.")).toBe("Cihaz yok.");
    expect(describeTaskError({ code: "no_capable_device", message: "Uygun cihaz yok." })).toBe("Uygun cihaz yok.");
    expect(describeTaskError({ code: "timeout" })).toBe("timeout");
  });
});
