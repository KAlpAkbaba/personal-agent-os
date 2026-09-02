import { describe, expect, it } from "vitest";

import {
  DEFAULT_HESITATION_CONFIG,
  HesitationGuard,
  classifyTail,
  isFiller,
  lastToken,
  turkishLower,
} from "../../app/lib/voice/hesitation";

describe("Turkish hesitation guard", () => {
  it("lower-cases the Turkish way", () => {
    expect(turkishLower("ŞEY")).toBe("şey");
    expect(turkishLower("IŞIK")).toBe("ışık");
    expect(turkishLower("İkinci")).toBe("ikinci");
  });

  it("takes the last token without punctuation", () => {
    expect(lastToken("raporun ikinci bölümünü, şey...")).toBe("şey");
    expect(lastToken("  ")).toBeNull();
    expect(lastToken("Yani…")).toBe("yani");
  });

  it("recognises the spec fillers and elongated sounds", () => {
    for (const filler of ["şey", "yani", "hani", "ııı", "eee", "hmm"]) {
      expect(isFiller(filler)).toBe(true);
    }
    expect(isFiller("ıııııı")).toBe(true);
    expect(isFiller("oku")).toBe(false);
    expect(isFiller("rapor")).toBe(false);
  });

  it("classifies a tail as filler / elongated / none", () => {
    expect(classifyTail("raporun ikinci bölümünü şey")).toBe("filler");
    expect(classifyTail("sadece OpenAI kısmına, yani")).toBe("filler");
    expect(classifyTail("hani şu")).toBe("none");
    expect(classifyTail("toplantıyı yarına aaal")).toBe("none");
    expect(classifyTail("raporuuu")).toBe("elongated");
    expect(classifyTail("ikinci maddeyi tekrar oku")).toBe("none");
    expect(classifyTail("")).toBe("none");
  });

  it("extends the trailing silence after a filler, bounded by maxHoldMs", () => {
    const guard = new HesitationGuard({
      baseTrailingSilenceMs: 200,
      fillerExtensionMs: 700,
      maxHoldMs: 800,
    });
    expect(guard.decide("ikinci maddeyi tekrar oku", 1000)).toMatchObject({
      holdMs: 200,
      reason: "none",
    });
    expect(guard.decide("ikinci maddeyi, şey", 2000)).toMatchObject({
      holdMs: 800, // 200 + 700 capped at 800
      reason: "filler",
      tail: "şey",
    });
    expect(guard.decide("sonraaa", 3000).reason).toBe("elongated");
    expect(guard.stats().held).toBe(2);
  });

  it("treats speech inside the hold as a continuation, not a new turn", () => {
    const guard = new HesitationGuard(DEFAULT_HESITATION_CONFIG);
    const decision = guard.decide("şey", 1000);
    expect(guard.isHolding).toBe(true);
    expect(guard.speechStarted(1000 + decision.holdMs - 1)).toBe(true);
    expect(guard.isHolding).toBe(false);
    guard.decide("tamam", 5000);
    guard.expire();
    expect(guard.speechStarted(5100)).toBe(false);
    expect(guard.stats()).toEqual({ held: 1, resumed_within_hold: 1 });
  });

  it("is deterministic: same input, same hold", () => {
    const a = new HesitationGuard();
    const b = new HesitationGuard();
    expect(a.decide("hani şu, ııı", 0)).toEqual(b.decide("hani şu, ııı", 0));
  });
});
