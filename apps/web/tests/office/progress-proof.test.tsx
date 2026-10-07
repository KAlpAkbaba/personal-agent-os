/**
 * The İlerleme strip's proof (proof-from-test-rounds-and-trials): the owner, 2026-10-06, "kanıt
 * kısmı neden ilerlemiyor". The JARVIS target reads three numbers - yapıldı, staging'de kanıtlı
 * (a test round passed on the current release), gerçekte kanıtlı (an owner trial passed) - and
 * the old v1.0 share stays, labelled "eski v1.0 listesi".
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import OfficeProgress, {
  buildProgress,
  type ProgressWithProof,
} from "../../app/core/office/OfficeProgress";

function sample(): ProgressWithProof {
  return {
    jarvis: {
      have: 1,
      partial: 1,
      missing: 1,
      never: 0,
      unknown: [],
      counted: 3,
      percent: 50,
      rows: [
        { name: "Konuşur", state: "have" },
        { name: "Her yerde", state: "partial" },
        { name: "Evi yönetir", state: "missing" },
      ],
    },
    order: { steps: [], percent: 50, next: null },
    v1: {
      total: 750,
      done: 711,
      by_status: { DONE: 711 },
      by_proof: { PR: 153 },
      percent_done: 95,
      percent_proven_real: 20,
    },
    proof: {
      counted: 3,
      staging_proven: 2,
      real_proven: 1,
      percent_staging: 67,
      percent_real: 33,
      release: "a5e68d92d9271ececec51da01b713e43a394e28c",
      rows: [
        {
          name: "Konuşur",
          state: "have",
          staging: { round: "t1", sha: "a5e68d9", at: "2026-10-06T20:00:00Z", passed: 3, failed: 0 },
          staging_proven: true,
          trial: { task_id: "x", trial_id: "d1", verdict: "oldu", at: "2026-10-06T18:00:00Z" },
          real_proven: true,
        },
        {
          name: "Her <i>yerde</i>",
          state: "partial",
          staging: { round: "t1", sha: "a5e68d9", at: "2026-10-06T20:00:00Z", passed: 1, failed: 0 },
          staging_proven: true,
          trial: null,
          real_proven: false,
        },
        { name: "Evi yönetir", state: "missing", staging: null, staging_proven: false, trial: null, real_proven: false },
      ],
      outside: { count: 4, unknown: [] },
      rule: "Staging'de kanıtlı: son tur geçti.",
    },
    rule: "Var 1, Yarım 0,5, Yok 0",
    as_of: "a5e68d92d9271ececec51da01b713e43a394e28c",
  };
}

describe("the proof in the progress model", () => {
  it("reads the JARVIS target as yapıldı · staging'de kanıtlı · gerçekte kanıtlı, the v1.0 share labelled old", () => {
    const model = buildProgress(sample());
    expect(model?.line).toBe(
      "JARVIS hedefi: yapıldı %50 · staging'de kanıtlı %67 · gerçekte kanıtlı %33 · " +
        "satır dışı: 4 · Sıralı plan %50 · eski v1.0 listesi %95 yapıldı, %20 gerçekte kanıtlı",
    );
    // A server before the "satır dışı" count keeps the three numbers alone.
    const { outside: _o, ...proof } = sample().proof!;
    expect(buildProgress({ ...sample(), proof })?.line).toContain("gerçekte kanıtlı %33 · Sıralı plan");
    expect(model?.bars.map((b) => [b.key, b.percent])).toEqual([
      ["jarvis", 50],
      ["staging", 67],
      ["real", 33],
      ["order", 50],
      ["v1", 95],
    ]);
  });

  it("lists the proven rows by proof", () => {
    const model = buildProgress(sample());
    expect(model?.proven.map((g) => [g.title, g.rows])).toEqual([
      ["Staging'de kanıtlı", ["Konuşur", "Her <i>yerde</i>"]],
      ["Gerçekte kanıtlı", ["Konuşur"]],
    ]);
  });

  it("says okunamadı for a proof the server could not compute, and an older answer keeps the old line", () => {
    const unread = buildProgress({ ...sample(), proof: null });
    expect(unread?.line).toContain("JARVIS hedefi: yapıldı %50 · kanıt okunamadı");
    expect(unread?.line).toContain("eski v1.0 listesi");
    const { proof: _, ...older } = sample();
    const model = buildProgress(older);
    expect(model?.line).toBe(
      "JARVIS hedefi %50 · Sıralı plan %50 · v1.0 listesi %95 yapıldı, %20 gerçekte kanıtlı",
    );
    expect(model?.proven).toEqual([]);
  });
});

describe("the strip with the proof", () => {
  it("renders the three JARVIS numbers as meters and the proven rows as text", () => {
    const html = renderToStaticMarkup(<OfficeProgress progress={sample()} />);
    expect(html.match(/role="meter"/g)).toHaveLength(5);
    expect(html).toContain('aria-valuenow="67"');
    expect(html).toContain('aria-valuenow="33"');
    expect(html).toContain("staging&#x27;de kanıtlı %67");
    expect(html).toContain("satır dışı: 4");
    expect(html).toContain("eski v1.0 listesi");
    expect(html).toContain("Her &lt;i&gt;yerde&lt;/i&gt;");
    expect(html).not.toContain("<i>yerde</i>");
    expect(html).toContain("Staging&#x27;de kanıtlı: son tur geçti.");
  });
});
