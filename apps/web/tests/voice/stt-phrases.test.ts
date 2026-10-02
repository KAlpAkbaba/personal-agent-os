/**
 * The words the on-device recogniser is told to expect (chrome-on-device-stt), and the
 * setting that decides whether it is asked at all.
 *
 * Pinned: the list is built by a pure function, the session's device aliases come first
 * so the cap can never cut them, it is capped, Turkish casing does not produce duplicates,
 * every boost is inside the range Chrome accepts (outside it the constructor throws); and
 * the setting is OFF for every value nobody wrote on purpose.
 */

import { describe, expect, it } from "vitest";

import {
  ALIAS_FLOOR,
  BOOST_ALIAS,
  BOOST_APPLICATION,
  BOOST_VERB,
  MAX_PHRASE_CHARS,
  OPEN_VERB_FORMS,
  PHRASE_CAP,
  applicationNamesIn,
  buildPhrases,
  clampBoost,
} from "../../app/lib/voice/sttPhrases";
import {
  STT_SETTING_DEFAULT,
  STT_SETTING_KEY,
  STT_SETTING_UI_KEY,
  parseSttSetting,
  readSttSetting,
  sttSettingUiEnabled,
} from "../../app/lib/voice/sttSetting";

const texts = (list: Array<{ phrase: string }>) => list.map((p) => p.phrase);
const stored = (map: Record<string, string>) => ({ getItem: (key: string) => map[key] ?? null });

describe("buildPhrases", () => {
  it("puts the session's device aliases first, then the spoken floor, the application names and the open-verb forms", () => {
    const list = buildPhrases({
      deviceAliases: ["GMKADIRAKBABA", "çalışma odası"],
      capabilityPhrases: ["Hesap makinesini aç", "PowerShell aç", "Saat kaç?"],
    });
    expect(texts(list)).toEqual([
      "GMKADIRAKBABA",
      "çalışma odası",
      ...ALIAS_FLOOR,
      "Hesap makinesini",
      "PowerShell",
      ...OPEN_VERB_FORMS,
    ]);
    expect(list[0].boost).toBe(BOOST_ALIAS);
    expect(list.find((p) => p.phrase === "ofis")?.boost).toBe(BOOST_ALIAS);
    expect(list.find((p) => p.phrase === "PowerShell")?.boost).toBe(BOOST_APPLICATION);
    expect(list.find((p) => p.phrase === "aç")?.boost).toBe(BOOST_VERB);
  });

  it("is capped, and the cap cuts from the END: the session's aliases survive it", () => {
    const aliases = ["salon", "mutfak", "atölye"];
    const many = Array.from({ length: 200 }, (_, i) => `Uygulama${i} aç`);
    const list = buildPhrases({ deviceAliases: aliases, capabilityPhrases: many });
    expect(list).toHaveLength(PHRASE_CAP);
    expect(texts(list).slice(0, 3)).toEqual(aliases);
    for (const floor of ALIAS_FLOOR) expect(texts(list)).toContain(floor);
    // an explicit cap is honoured too, down to nothing
    expect(texts(buildPhrases({ deviceAliases: aliases }, 2))).toEqual(["salon", "mutfak"]);
    expect(buildPhrases({ deviceAliases: aliases }, 0)).toEqual([]);
    expect(buildPhrases({ deviceAliases: aliases }, -5)).toEqual([]);
  });

  it("with no sources at all (a failed fetch) the floor and the verb forms still stand", () => {
    for (const nothing of [null, undefined, {}]) {
      expect(texts(buildPhrases(nothing))).toEqual([...ALIAS_FLOOR, ...OPEN_VERB_FORMS]);
    }
  });

  it("dedupes with Turkish casing, drops empties and anything too long to be a name", () => {
    const list = buildPhrases({
      // "IŞIK" lower-cases to "ışık" in Turkish and to "işik" anywhere else
      deviceAliases: ["ışık", "IŞIK", "  ", "", "İŞ", "Ofis", " ev  bilgisayarı ", "x".repeat(MAX_PHRASE_CHARS + 1)],
      capabilityPhrases: [],
    });
    expect(texts(list)).toEqual(["ışık", "İŞ", "Ofis", "ev bilgisayarı", "ev", "laptop", ...OPEN_VERB_FORMS]);
  });

  it("survives sources that are not what the type promises (a server answer is not a type)", () => {
    const list = buildPhrases({
      deviceAliases: ["salon", 7, null, { a: 1 }] as unknown as string[],
      capabilityPhrases: "Hesap makinesini aç" as unknown as string[],
    });
    expect(texts(list)).toEqual(["salon", ...ALIAS_FLOOR, ...OPEN_VERB_FORMS]);
  });

  it("every boost is inside Chrome's [0, 10]", () => {
    for (const phrase of buildPhrases({ deviceAliases: ["salon"], capabilityPhrases: ["Not defterini aç"] })) {
      expect(phrase.boost).toBeGreaterThanOrEqual(0);
      expect(phrase.boost).toBeLessThanOrEqual(10);
    }
    expect(clampBoost(11)).toBe(10);
    expect(clampBoost(-1)).toBe(0);
    expect(clampBoost(Number.NaN)).toBe(1);
    expect(clampBoost(2.5)).toBe(2.5);
  });
});

describe("applicationNamesIn", () => {
  it("takes what stands before an open verb, as the owner says it, and nothing from other sentences", () => {
    expect(
      applicationNamesIn(["Tarayıcıyı aç", "Hesap makinesini açın.", "PowerShell AÇ", "Saat kaç?", "aç", "Alarmı kapat"]),
    ).toEqual(["Tarayıcıyı", "Hesap makinesini", "PowerShell"]);
  });

  it("does not read a verb inside a longer word as the verb (açıkla is not aç)", () => {
    expect(applicationNamesIn(["Bunu açıkla", "Kaç tane var"])).toEqual([]);
  });
});

describe("the setting", () => {
  it("is kapali by default and for everything that is not one of the three values", () => {
    expect(STT_SETTING_DEFAULT).toBe("kapali");
    for (const raw of [undefined, null, "", "1", "true", "on", "ACIK", " acik", "açık", 1, {}]) {
      expect(parseSttSetting(raw)).toBe("kapali");
    }
    expect(parseSttSetting("kapali")).toBe("kapali");
    expect(parseSttSetting("acik")).toBe("acik");
    expect(parseSttSetting("olc")).toBe("olc");
  });

  it("is read from the preferences' storage under its own key, and a storage that throws is kapali", () => {
    expect(readSttSetting(stored({}))).toBe("kapali");
    expect(readSttSetting(stored({ [STT_SETTING_KEY]: "olc" }))).toBe("olc");
    expect(readSttSetting(stored({ [STT_SETTING_UI_KEY]: "1" }))).toBe("kapali"); // the UI flag is not the setting
    expect(readSttSetting(null)).toBe("kapali");
    expect(
      readSttSetting({
        getItem: () => {
          throw new Error("SecurityError");
        },
      }),
    ).toBe("kapali");
    expect(STT_SETTING_KEY.startsWith("pagentos.core.")).toBe(true);
  });

  it("the owner's switch stays hidden until the flag is written", () => {
    expect(sttSettingUiEnabled(stored({}))).toBe(false);
    expect(sttSettingUiEnabled(stored({ [STT_SETTING_KEY]: "acik" }))).toBe(false); // the setting is not the flag
    expect(sttSettingUiEnabled(stored({ [STT_SETTING_UI_KEY]: "0" }))).toBe(false);
    expect(sttSettingUiEnabled(stored({ [STT_SETTING_UI_KEY]: "1" }))).toBe(true);
    expect(sttSettingUiEnabled(null)).toBe(false);
  });
});
