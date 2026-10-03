/**
 * The two halves of the measurement contract read each other: every route the client
 * calls is declared in the API's routes.py, and none of the owner's twenty sentences
 * (stt_compare.OWNER_SENTENCES) is typed anywhere in the web source of this page.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  MEASUREMENT_PATH,
  RECORDINGS_ROUTE,
  RECORDING_ROUTE,
} from "../../app/lib/voice/measure/api";

const WEB = join(__dirname, "..", "..");
const REPO = join(WEB, "..", "..");
const ROUTES = readFileSync(join(REPO, "services/api/app/voice/measurement/routes.py"), "utf8");
const STT_COMPARE = readFileSync(join(REPO, "services/api/app/voice/stt_compare.py"), "utf8");

function ownerSentences(): string[] {
  const block = /OWNER_SENTENCES[^=]*=\s*\(([\s\S]*?)\n\)/.exec(STT_COMPARE);
  if (!block) throw new Error("OWNER_SENTENCES not found in stt_compare.py");
  return [...block[1].matchAll(/^\s*"((?:[^"\\]|\\.)*)",?\s*$/gm)].map((match) => match[1]);
}

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  });
}

describe("the client's routes are the API's routes", () => {
  it("the prefix and every method + path the client uses are declared in routes.py", () => {
    expect(ROUTES).toContain(`prefix="${MEASUREMENT_PATH}"`);
    expect(ROUTES).toContain('@router.get("")');
    expect(ROUTES).toContain(`@router.put("${RECORDING_ROUTE}")`);
    expect(ROUTES).toContain(`@router.delete("${RECORDING_ROUTE}")`);
    expect(ROUTES).toContain(`@router.delete("${RECORDINGS_ROUTE}")`);
  });
});

describe("no sentence is typed into the web source", () => {
  it("none of OWNER_SENTENCES appears under app/voice/measure or app/lib/voice/measure", () => {
    const sentences = ownerSentences();
    expect(sentences).toHaveLength(20);
    const files = [
      ...filesUnder(join(WEB, "app/voice/measure")),
      ...filesUnder(join(WEB, "app/lib/voice/measure")),
    ];
    expect(files.length).toBeGreaterThan(3);
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const sentence of sentences) expect(source.includes(sentence), `${file}: ${sentence}`).toBe(false);
    }
  });
});

describe("the voice page links here", () => {
  it("apps/web/app/voice/page.tsx carries href='/voice/measure'", () => {
    const page = readFileSync(join(WEB, "app/voice/page.tsx"), "utf8");
    expect(page).toMatch(/href=["']\/voice\/measure["']/);
  });
});
