/**
 * A picture of the Core, for an owner who cannot see the WebGL scene.
 *
 * No browser is ever launched in this repository's test environment (ADR-0056
 * §6, and `services/browser/tests/test_test_isolation_guards.py` is why), so
 * the 3D Core has never actually been looked at here. The 2D fallback,
 * however, is pure SVG rendered by `react-dom/server` and carries the same
 * identity from the same `VisualIntent` — so rendering it for five real states
 * and writing the files out gives the integrator something to hand the owner
 * before qualification A.
 *
 * It lives as a test rather than as a script because `tsx` is not a dependency
 * of this package and adding one to draw a picture would be a poor trade. It
 * asserts what it writes, so it is a real test as well as a generator:
 * every state must produce its own distinguishable structure.
 *
 * Output (gitignored, regenerate with
 * `pnpm exec vitest run tests/preview/core-preview.test.tsx`):
 *
 *   apps/web/preview/core-idle.svg
 *   apps/web/preview/core-listening.svg
 *   apps/web/preview/core-speaking.svg
 *   apps/web/preview/core-researching.svg
 *   apps/web/preview/core-alarm-playing.svg
 *   apps/web/preview/core-preview.html   (all five on the real ground)
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import CoreFallback2D from "../../app/core/CoreFallback2D";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { type VisualIntent, visualFor } from "../../app/lib/uistate/visual";
import { KIND_LABEL } from "../../app/lib/uistate/labels";
import {
  AGENT_IDLE,
  ALARM_PLAYING,
  RESEARCH_RANKING,
  T0,
  VOICE_LISTENING,
  VOICE_SPEAKING,
  event,
  resetSequence,
  response,
} from "../uistate/fixtures";

const OUT = resolve(__dirname, "../../preview");

function intentOf(events: ReturnType<typeof event>[]): VisualIntent {
  resetSequence();
  return visualFor(applyResponse(emptyTruth(), response(events), T0), T0);
}

/** The five states the owner asked to see, each from a real publisher shape. */
const SCENES: Array<{ file: string; title: string; note: string; intent: VisualIntent }> = [
  {
    file: "core-idle",
    title: "Boşta",
    note: "agent.idle — bildirilen sakinlik: yavaş nefes, halkalar sürükleniyor, akış yok.",
    intent: intentOf([AGENT_IDLE()]),
  },
  {
    file: "core-listening",
    title: "Dinliyor",
    note: "agent.listening (intensity 0.5) — enerji içe çekiliyor, kabuklar kapanıyor.",
    intent: intentOf([VOICE_LISTENING()]),
  },
  {
    file: "core-speaking",
    title: "Konuşuyor",
    note: "agent.speaking (intensity 0.72) — nabız kabuğu, ölçülen çıkış zarfı kadar.",
    intent: intentOf([VOICE_SPEAKING(0.72)]),
  },
  {
    file: "core-researching",
    title: "Araştırıyor",
    note: "agent.researching (12 aday, 5 tutulan) — sayılan takımyıldız ve aday alanı.",
    intent: intentOf([RESEARCH_RANKING(12, 5)]),
  },
  {
    file: "core-alarm-playing",
    title: "Boşta + alarm çalıyor",
    note: "agent.idle + alarm.playing (0.6) — uyanma dalgası kendi halkasında; çekirdek hâlâ boşta.",
    intent: intentOf([AGENT_IDLE(), ALARM_PLAYING(0.6)]),
  },
];

function markupOf(intent: VisualIntent): string {
  return renderToStaticMarkup(<CoreFallback2D intent={intent} tier="high" />);
}

/** Just the `<svg>`, for a standalone file: the wrapper is a page's div. */
function svgOf(intent: VisualIntent): string {
  const markup = markupOf(intent);
  const start = markup.indexOf("<svg");
  const end = markup.lastIndexOf("</svg>") + "</svg>".length;
  expect(start).toBeGreaterThanOrEqual(0);
  return markup.slice(start, end);
}

/** The CSS variables the component sets inline, as an SVG-embeddable style. */
function inlineVars(intent: VisualIntent): string {
  const round = (v: number) => Math.round(v * 100) / 100;
  return [
    `--breath-amp:${intent.breathAmplitude}`,
    `--breath-dur:${intent.breathHz > 0 ? round(1 / intent.breathHz) : 0}s`,
    `--core-opacity:${round(1 - intent.dim * 0.75)}`,
    `--core-glow:${round(intent.glow)}`,
    `--ring-dur:${intent.ringSpin > 0 ? round(7 / intent.ringSpin) : 0}s`,
    `--orbit-dur:${intent.ringSpin > 0 ? round(11 / intent.ringSpin) : 0}s`,
    `--core-surge:${round(intent.wakeSurge)}`,
  ].join(";");
}

describe("the Core, drawn for the owner", () => {
  const css = readFileSync(resolve(__dirname, "../../app/core/core.css"), "utf8");

  it("writes one standalone SVG per state, and they differ", () => {
    mkdirSync(OUT, { recursive: true });
    const written: string[] = [];
    for (const scene of SCENES) {
      const body = svgOf(scene.intent)
        .replace(
          "<svg",
          `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="640" style="background:#06050a;${inlineVars(scene.intent)}"`,
        )
        // The stylesheet travels with the picture: a standalone SVG has no page.
        .replace("</svg>", `<style>${css}</style></svg>`);
      const path = resolve(OUT, `${scene.file}.svg`);
      writeFileSync(path, `${body}\n`, "utf8");
      written.push(body);
    }
    // Five different states must look like five different things.
    expect(new Set(written).size).toBe(SCENES.length);
  });

  it("shows each state's own evidence, and no other state's", () => {
    const [idle, listening, speaking, researching, alarm] = SCENES.map((s) => markupOf(s.intent));

    expect(idle).toContain('data-core-kind="idle"');
    expect(idle).toContain('data-breathing="yes"');
    expect(idle).not.toContain("core-inward");
    expect(idle).not.toContain("core-wake");

    expect(listening).toContain('data-core-kind="listening"');
    expect(listening).toContain("core-inward");

    expect(speaking).toContain('data-core-kind="speaking"');
    expect(speaking).toContain("core-pulse");

    expect(researching).toContain('data-constellation="counted"');
    expect(researching).toContain('data-drawn-nodes="5"');
    expect(researching).toContain('data-field-nodes="7"');

    // The one that matters most for M18.3: the alarm surges, and the Core
    // underneath it is still saying "idle" rather than pretending to work.
    expect(alarm).toContain('data-core-kind="idle"');
    expect(alarm).toContain('data-wake-stage="playing"');
    expect(alarm).toContain("core-wake");
  });

  it("writes one page with all five on the real ground", () => {
    mkdirSync(OUT, { recursive: true });
    const cards = SCENES.map(
      (scene) => `
    <figure class="card">
      <div class="stage" style="${inlineVars(scene.intent)}">${svgOf(scene.intent)}</div>
      <figcaption>
        <strong>${scene.title}</strong>
        <span>${KIND_LABEL[scene.intent.kind]}</span>
        <small>${scene.note}</small>
      </figcaption>
    </figure>`,
    ).join("\n");

    const html = `<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8" />
<title>Living Core — M18.3 önizleme</title>
<style>
:root { color-scheme: dark; }
body {
  margin: 0; padding: 2rem; background: #06050a; color: #e6e8ec;
  font-family: "Segoe UI", system-ui, sans-serif;
}
h1 { font-size: 1.2rem; font-weight: 600; margin: 0 0 0.35rem; }
.lede { color: #9aa3b2; font-size: 0.85rem; max-width: 60rem; margin: 0 0 2rem; line-height: 1.5; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1.5rem; }
.card { margin: 0; }
.stage { aspect-ratio: 1; background: #06050a; border: 1px solid #1b1712; border-radius: 14px; overflow: hidden; }
.stage svg { width: 100%; height: 100%; }
figcaption { display: flex; flex-direction: column; gap: 0.15rem; padding: 0.6rem 0.2rem; }
figcaption span { color: #ffc86a; font-size: 0.85rem; }
figcaption small { color: #9aa3b2; font-size: 0.75rem; line-height: 1.45; }
${css}
</style>
</head>
<body>
<h1>Living Core — M18.3, 2B yol</h1>
<p class="lede">
  Bu sayfa WebGL sahnesinin kendisi değildir: aynı <code>VisualIntent</code>'ten çizilen, aynı
  kimliği taşıyan 2B yedeği (<code>CoreFallback2D</code>) sunucuda oluşturulmuştur. Bu ortamda
  hiçbir tarayıcı açılmadığı için 3B sahne henüz kimse tarafından görülmedi; ölçek, derinlik ve
  parlaklık yalnızca gerçek bir tarayıcıda değerlendirilebilir. Her karede çizilen her şey
  yayınlanmış bir olaydan gelir: hiçbir hareket süsleme değildir.
</p>
<div class="grid">${cards}
</div>
</body>
</html>
`;
    writeFileSync(resolve(OUT, "core-preview.html"), html, "utf8");
    expect(html).toContain("core-wake");
    expect(html.match(/<svg/g)?.length).toBe(SCENES.length);
  });
});
