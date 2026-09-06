/**
 * What actually reaches the screen.
 *
 * `visual.test.ts` proves the state→intent mapping; this proves the intent
 * survives the trip into markup. Both matter: an honest `VisualIntent` rendered
 * by a component that draws a default breathing circle regardless would fail
 * the owner exactly as badly as a dishonest mapping.
 *
 * Rendered with `react-dom/server` in Node, matching the existing research
 * suite. There is deliberately no browser here: the 2D view is animated purely
 * by CSS and its geometry is a pure function of the intent, so a headless
 * browser would add orphan-process risk and flakiness for no extra coverage.
 * (`services/browser/tests/test_test_isolation_guards.py` exists because that
 * risk was once realised on this owner's desktop.)
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ChannelReadout from "../../app/core/ChannelReadout";
import CoreFallback2D from "../../app/core/CoreFallback2D";
import StateReadout from "../../app/core/StateReadout";
import Panel from "../../app/core/panels/Panel";
import { applyError, applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { CONSTELLATION_MOTIF, applyVoiceOverlay, visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_ERROR,
  AGENT_IDLE,
  EYE_ACTIVE,
  GOAL_WAITING_OWNER,
  LAB_BUILDING,
  LAB_SHADOW_READY,
  MEMORY_RETRIEVAL,
  RESEARCH_NO_COUNTS,
  RESEARCH_RANKING,
  SELFMODEL_THINKING,
  T0,
  TOOL_RUNNING,
  VOICE_LISTENING,
  VOICE_SPEAKING,
  event,
  resetSequence,
  response,
} from "./fixtures";

/** Build the intent for one event, exactly as the pages do. */
function intentFor(make: () => ReturnType<typeof event>, at = T0) {
  resetSequence();
  return visualFor(applyResponse(emptyTruth(), response([make()]), T0), at);
}

function core(make: () => ReturnType<typeof event>, at = T0, tier: "high" | "balanced" | "low" = "high") {
  return renderToStaticMarkup(<CoreFallback2D intent={intentFor(make, at)} tier={tier} />);
}

function readout(make: () => ReturnType<typeof event>, at = T0) {
  return renderToStaticMarkup(<StateReadout intent={intentFor(make, at)} />);
}

describe("the 2D core draws only what was reported", () => {
  it("an untold core is still: no breathing, no nodes, no satellite", () => {
    const html = renderToStaticMarkup(
      <CoreFallback2D intent={visualFor(applyResponse(emptyTruth(), response([]), T0), T0)} tier="high" />,
    );
    expect(html).toContain('data-core-kind="untold"');
    expect(html).toContain('data-breathing="no"');
    expect(html).not.toContain("core-sources");
    expect(html).not.toContain("core-satellite");
    expect(html).not.toContain("core-lattice");
    expect(html).not.toContain("core-pulse");
  });

  it("only a reported idle state breathes", () => {
    expect(core(AGENT_IDLE)).toContain('data-breathing="yes"');
    const untold = renderToStaticMarkup(
      <CoreFallback2D intent={visualFor(applyResponse(emptyTruth(), response([]), T0), T0)} tier="high" />,
    );
    expect(untold).toContain('data-breathing="no"');
  });

  it("listening contracts and draws inward ticks; nothing else does", () => {
    const html = core(VOICE_LISTENING);
    expect(html).toContain('data-core-kind="listening"');
    expect(html).toContain("core-inward");
    expect(core(AGENT_IDLE)).not.toContain("core-inward");
  });

  it("thinking draws the lattice; idle does not", () => {
    expect(core(SELFMODEL_THINKING)).toContain("core-lattice");
    expect(core(AGENT_IDLE)).not.toContain("core-lattice");
  });

  it("speaking draws a pulse ring sized by the reported energy, and none without it", () => {
    const loud = core(() => VOICE_SPEAKING(0.9));
    expect(loud).toContain("core-pulse");
    const silent = core(() => VOICE_SPEAKING(null));
    expect(silent).toContain('data-core-kind="speaking"');
    expect(silent).not.toContain("core-pulse");
  });

  it("research draws exactly the nodes it counted", () => {
    const html = core(() => RESEARCH_RANKING(12, 5));
    expect(html).toContain('data-drawn-nodes="5"');
    // Five node circles, one per source.
    expect(html.match(/<circle cx="[\d.]+" cy="[\d.]+" r="3.5"/g)?.length).toBe(5);
  });

  it("research with no counts draws no nodes at all", () => {
    const html = core(RESEARCH_NO_COUNTS);
    expect(html).toContain('data-core-kind="researching"');
    expect(html).not.toContain("core-sources");
    expect(html).not.toContain("data-drawn-nodes");
  });

  it("memory draws a convergence ring only against real progress", () => {
    expect(core(() => MEMORY_RETRIEVAL(0.4))).toContain("core-convergence");
    expect(core(() => MEMORY_RETRIEVAL(null))).not.toContain("core-convergence");
  });

  it("the lab draws one layer per phase reached, and no satellite", () => {
    const html = core(LAB_BUILDING);
    expect(html).toContain('data-construction-layer="3"');
    expect(html).not.toContain("core-satellite");
  });

  it("shadow_ready draws the completed satellite and no construction layers", () => {
    const html = core(LAB_SHADOW_READY);
    expect(html).toContain('data-satellite="complete"');
    expect(html).not.toContain("core-construction");
  });

  it("error draws one bounded offset ring, not a strobe", () => {
    const html = core(() => AGENT_ERROR("critical"));
    expect(html).toContain("core-agitation");
    expect(html).toContain('data-agitation="0.35"');
    // Slow breathing means a long duration, never a short flashing one.
    expect(html).toMatch(/--breath-dur:5\.56s/);
  });

  it("an expired claim is drawn still and faded", () => {
    const html = core(SELFMODEL_THINKING, T0 + 60_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-breathing="no"');
    expect(html).not.toContain("core-lattice");
    // Dimmed: opacity is strictly below full.
    expect(html).toMatch(/data-opacity="0\.6[0-9]*"/);
  });

  it("a still core emits no animation even in a state that reported motion", () => {
    const html = renderToStaticMarkup(
      <CoreFallback2D intent={intentFor(AGENT_IDLE)} tier="high" still />,
    );
    expect(html).toContain('data-still="yes"');
    expect(html).toContain('data-breathing="no"');
  });
});

describe("quality tiers cap the geometry, never the truth", () => {
  it("the low tier draws fewer nodes than were reported, and says so", () => {
    const intent = intentFor(() => RESEARCH_RANKING(60, 40));
    const low = renderToStaticMarkup(<CoreFallback2D intent={intent} tier="low" />);
    expect(low).toContain('data-drawn-nodes="12"'); // low caps at 12

    // The readout still reports the real number, plus what was drawn.
    const text = renderToStaticMarkup(<StateReadout intent={intent} drawnSatellites={12} />);
    expect(text).toContain('data-source-nodes="40"');
    expect(text).toContain("40 kaynak");
    expect(text).toContain("12 tanesi çiziliyor");
  });

  it("the low tier draws no lattice at all", () => {
    expect(renderToStaticMarkup(<CoreFallback2D intent={intentFor(SELFMODEL_THINKING)} tier="low" />))
      .not.toContain("core-lattice");
    expect(core(SELFMODEL_THINKING, T0, "high")).toContain("core-lattice");
  });
});

describe("the readout says which kind of silence it is", () => {
  it("'nothing reported' is not 'idle'", () => {
    const untold = renderToStaticMarkup(
      <StateReadout intent={visualFor(applyResponse(emptyTruth(), response([]), T0), T0)} />,
    );
    expect(untold).toContain('data-core-kind="untold"');
    expect(untold).toContain("Henüz bir durum bildirilmedi");
    expect(untold).toContain("Bu &#x27;boşta&#x27; demek değil.");

    const idle = readout(AGENT_IDLE);
    expect(idle).toContain('data-core-kind="idle"');
    expect(idle).toContain("Boşta");
    expect(idle).not.toContain("Henüz bir durum bildirilmedi");
  });

  it("names the state an expired claim came from", () => {
    const html = readout(SELFMODEL_THINKING, T0 + 60_000);
    expect(html).toContain("Son bilinen durum");
    expect(html).toContain('data-last-state="agent.thinking"');
    expect(html).toContain("1 dk önce");
    expect(html).toContain('data-live="no"');
  });

  it("an unreachable API is labelled as memory, not observation", () => {
    resetSequence();
    let truth = applyResponse(emptyTruth(), response([VOICE_SPEAKING()]), T0);
    truth = applyError(truth, "ağ koptu", T0);
    const html = renderToStaticMarkup(<StateReadout intent={visualFor(truth, T0)} />);
    expect(html).toContain("Cloud Core'a ulaşılamıyor".replace("'", "&#x27;"));
    expect(html).toContain("son bilinen durumdur, canlı değildir");
    expect(html).toContain('data-live="no"');
  });

  it("draws a progress bar only where progress was reported", () => {
    const withProgress = readout(LAB_BUILDING);
    expect(withProgress).toContain('data-progress="0.500"');
    expect(withProgress).toContain("%50");

    const without = readout(VOICE_LISTENING);
    expect(without).not.toContain("data-progress=");
    expect(without).toContain("İlerleme bildirilmedi.");
  });

  it("says when research reported no source count", () => {
    const html = readout(RESEARCH_NO_COUNTS);
    expect(html).toContain('data-source-nodes-known="no"');
    expect(html).toContain("Kaynak sayısı bildirilmedi.");
  });

  it("reports zero kept sources as a real zero", () => {
    const html = readout(() => RESEARCH_RANKING(9, 0));
    expect(html).toContain('data-source-nodes-known="yes"');
    expect(html).toContain("Kalite kapısından geçen kaynak yok.");
  });

  it("states plainly that a shadow-ready candidate is not live", () => {
    const html = readout(LAB_SHADOW_READY);
    expect(html).toContain('data-satellite-complete="yes"');
    expect(html).toContain("canlıya alınmadı");
  });

  it("waiting on the owner reads as deliberate, not as work", () => {
    const html = readout(() => event({ state: "agent.waiting_owner", subsystem: "goal" }));
    expect(html).toContain("Sahibi bekliyor");
    expect(html).toContain("kasıtlı olarak duruyor");
  });

  it("an unknown state is named, not guessed", () => {
    const html = readout(() => event({ state: "agent.daydreaming", subsystem: "system" }));
    expect(html).toContain('data-core-kind="unknown_state"');
    expect(html).toContain('data-core-state="agent.daydreaming"');
    expect(html).toContain("Sözleşme güncellenmiş olabilir.");
  });
});

// ---------------------------------------------- M18.1: the layered structure

/** The 2D core for a bus that has never published. */
const untold = () =>
  renderToStaticMarkup(
    <CoreFallback2D intent={visualFor(applyResponse(emptyTruth(), response([]), T0), T0)} tier="high" />,
  );

describe("the 2D core carries the layered structure, drawn only from channels", () => {
  it("draws the rings and shells at every tier, and the tier's count of them", () => {
    expect(core(AGENT_IDLE, T0, "high")).toContain('data-rings="3"');
    expect(core(AGENT_IDLE, T0, "high")).toContain('data-shells="2"');
    expect(core(AGENT_IDLE, T0, "balanced")).toContain('data-rings="2"');
    expect(core(AGENT_IDLE, T0, "balanced")).toContain('data-shells="1"');
    expect(core(AGENT_IDLE, T0, "low")).toContain('data-rings="1"');
    expect(core(AGENT_IDLE, T0, "low")).not.toContain("core-shells");
  });

  it("the rings turn only at a reported spin: idle drifts, untold is still", () => {
    expect(core(AGENT_IDLE)).toContain('data-spinning="yes"');
    expect(core(AGENT_IDLE)).toMatch(/--ring-dur:140s/); // 7 s / 0.05
    const silent = untold();
    expect(silent).toContain('data-spinning="no"');
    expect(silent).toMatch(/--ring-dur:0s/);
    expect(silent).toContain('data-glow="0"');
    expect(silent).toContain('data-shell-spread="0"');
  });

  it("a still core emits no ring rotation even in a state that reported spin", () => {
    const html = renderToStaticMarkup(
      <CoreFallback2D intent={intentFor(SELFMODEL_THINKING)} tier="high" still />,
    );
    expect(html).toContain('data-spinning="no"');
    expect(html).toContain('data-breathing="no"');
  });

  it("thinking states its flow on the paths; the shells stand further off than idle", () => {
    const thinking = core(SELFMODEL_THINKING);
    expect(thinking).toMatch(/core-lattice" data-lattice-segments="\d+" data-flow-rate="0\.\d+"/);
    expect(thinking).toContain('data-shell-spread="0.55"');
    expect(core(AGENT_IDLE)).toContain('data-shell-spread="0.15"');
    expect(core(TOOL_RUNNING)).toContain('data-flow-rate="0.6"');
  });

  it("listening states the owner's measured voice on the inward group", () => {
    resetSequence();
    const bus = visualFor(applyResponse(emptyTruth(), response([AGENT_IDLE()]), T0), T0);
    const speaking = applyVoiceOverlay(bus, {
      state: "listening",
      micLevel: 0.5,
      outputLevel: null,
      caption: null,
      toolLabel: null,
      lastError: null,
    });
    const html = renderToStaticMarkup(<CoreFallback2D intent={speaking} tier="high" />);
    expect(html).toContain('data-owner-voice="0.5"');
    expect(html).toContain('data-inward-flow="0.68"');
    // A bus listening never claims the owner's voice.
    expect(core(VOICE_LISTENING)).toContain('data-owner-voice="0"');
  });

  it("research with counts draws the counted constellation with a spoke per node, and the field", () => {
    const html = core(() => RESEARCH_RANKING(12, 5));
    expect(html).toContain('data-constellation="counted"');
    expect(html).toContain('data-drawn-nodes="5"');
    expect(html.match(/<line x1="[\d.]+" y1="[\d.]+" x2="[\d.]+" y2="[\d.]+"><\/line>/g)?.length).toBeGreaterThanOrEqual(5);
    expect(html).toContain('data-field-nodes="7"');
    expect(html).not.toContain("core-motif");
  });

  it("research without counts draws the fixed motif, labelled as a motif and never as sources", () => {
    const html = core(RESEARCH_NO_COUNTS);
    expect(html).toContain('data-constellation="motif"');
    expect(html).toContain(`data-motif-nodes="${CONSTELLATION_MOTIF}"`);
    expect(html).not.toContain("core-sources");
    expect(html).not.toContain("data-drawn-nodes");
    expect(html).not.toContain("core-field");
    // The readout says so in words, beside the "not reported" line.
    const text = readout(RESEARCH_NO_COUNTS);
    expect(text).toContain("Kaynak sayısı bildirilmedi.");
    expect(text).toContain("sabit bir temsildir, sayım değildir");
  });

  it("the motif is byte-for-byte the same on every render", () => {
    expect(core(RESEARCH_NO_COUNTS)).toBe(core(RESEARCH_NO_COUNTS));
  });

  it("nothing but research draws a constellation", () => {
    for (const make of [AGENT_IDLE, SELFMODEL_THINKING, TOOL_RUNNING, LAB_BUILDING]) {
      expect(core(make)).not.toContain("core-constellation");
    }
  });

  it("shadow_ready parks one capability node for the one candidate, and says it was not counted", () => {
    const html = core(LAB_SHADOW_READY);
    expect(html).toContain('data-capability-nodes="1"');
    expect(html).toContain('data-capability-counted="no"');
    expect(html).toContain('data-satellite="complete"');
    const text = readout(LAB_SHADOW_READY);
    expect(text).toContain("laboratuvar sayı bildirmedi");
    expect(text).toContain("canlıya alınmadı");
  });

  it("a published ready count parks that many, capped, and says it was counted", () => {
    const html = core(() =>
      event({ state: "evolution.shadow_ready", subsystem: "evolution", metadata: { ready: 3 } }),
    );
    expect(html).toContain('data-capability-nodes="3"');
    expect(html).toContain('data-capability-counted="yes"');
    const capped = core(() =>
      event({ state: "evolution.shadow_ready", subsystem: "evolution", metadata: { ready: 40 } }),
    );
    expect(capped).toContain('data-capability-nodes="8"');
    expect(readout(() => event({ state: "evolution.shadow_ready", subsystem: "evolution", metadata: { ready: 40 } })))
      .toContain("40 hazır aday");
  });

  it("the eye's aperture is drawn only while eye.active is current, whatever the core is doing", () => {
    resetSequence();
    const withEye = visualFor(applyResponse(emptyTruth(), response([EYE_ACTIVE(), SELFMODEL_THINKING()]), T0), T0);
    const html = renderToStaticMarkup(<CoreFallback2D intent={withEye} tier="high" />);
    expect(html).toContain('data-eye-active="yes"');
    expect(html).toContain('data-core-kind="thinking"');
    expect(core(SELFMODEL_THINKING)).not.toContain("core-eye");
    expect(untold()).not.toContain("core-eye");
  });

  it("waiting on the owner draws the held boundary and no flow", () => {
    const html = core(GOAL_WAITING_OWNER);
    expect(html).toContain("core-restraint");
    expect(html).toContain('data-flow-rate="0"');
    expect(html).not.toContain("core-lattice");
    expect(html).not.toContain("core-inward");
  });

  it("memory converges inward and draws the ring only against real progress", () => {
    const known = core(() => MEMORY_RETRIEVAL(0.4));
    expect(known).toContain("core-inward");
    expect(known).toContain('data-convergence="0.4"');
    const unknown = core(() => MEMORY_RETRIEVAL(null));
    expect(unknown).toContain("core-inward");
    expect(unknown).not.toContain("core-convergence");
  });

  it("an expired claim keeps the shape and turns nothing", () => {
    const html = core(SELFMODEL_THINKING, T0 + 60_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-spinning="no"');
    expect(html).toContain('data-flow-rate="0"');
    expect(html).toContain('data-shell-spread="0.55"'); // the shape it had
  });
});

describe("the cockpit's channel telemetry prints the intent as it is", () => {
  it("lists every numeric channel with its value", () => {
    const html = renderToStaticMarkup(<ChannelReadout intent={intentFor(SELFMODEL_THINKING)} />);
    expect(html).toContain("data-channel-readout");
    expect(html).toContain('data-channel="ringSpin" data-value="0.50"');
    expect(html).toContain('data-channel="flowRate" data-value="0.50"');
    expect(html).toContain('data-channel="topology" data-value="0.40"');
    expect(html).toContain('data-channel="pulse" data-value="0.00"');
    expect(html).toContain('data-channel="ownerVoice" data-value="0.00"');
    expect(html).toContain('data-channel="eyeActive" data-value="0"');
  });

  it("says whether the constellation and the capability nodes were counted", () => {
    const motif = renderToStaticMarkup(<ChannelReadout intent={intentFor(RESEARCH_NO_COUNTS)} />);
    expect(motif).toContain(`data-channel="constellationNodes" data-value="${CONSTELLATION_MOTIF}"`);
    expect(motif).toContain("sabit temsil");
    const counted = renderToStaticMarkup(<ChannelReadout intent={intentFor(() => RESEARCH_RANKING(12, 5))} />);
    expect(counted).toContain('data-channel="constellationNodes" data-value="5"');
    expect(counted).toContain('data-channel="fieldNodes" data-value="7"');
    const ready = renderToStaticMarkup(<ChannelReadout intent={intentFor(LAB_SHADOW_READY)} />);
    expect(ready).toContain('data-channel="capabilityNodes" data-value="1"');
    expect(ready).toContain("bir aday");
  });

  it("an untold core reads as all zeros", () => {
    const html = renderToStaticMarkup(
      <ChannelReadout intent={visualFor(applyResponse(emptyTruth(), response([]), T0), T0)} />,
    );
    expect(html).not.toMatch(/data-value="0\.[1-9]/);
    expect(html).toContain('data-channel="glow" data-value="0.00"');
  });
});

describe("panels tell empty apart from unknown", () => {
  const render = (state: Parameters<typeof Panel<string[]>>[0]["state"]) =>
    renderToStaticMarkup(
      <Panel<string[]>
        id="demo"
        title="Hedefler"
        state={state}
        empty="Hedef yok."
        isEmpty={(v) => v.length === 0}
        badge={(v) => `${v.length}`}
      >
        {(v) => (
          <ul>
            {v.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        )}
      </Panel>,
    );

  it("says 'no goals' only when it actually asked and got none", () => {
    const html = render({ kind: "ok", value: [], at: 0 });
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Hedef yok.");
  });

  it("never shows an empty list for a failed request", () => {
    const html = render({ kind: "failed", error: "HTTP 503" });
    expect(html).toContain("Alınamadı: HTTP 503");
    expect(html).not.toContain("Hedef yok.");
    expect(html).toContain('data-panel-state="failed"');
  });

  it("distinguishes not-yet-asked from empty", () => {
    const html = render({ kind: "loading" });
    expect(html).toContain("yükleniyor…");
    expect(html).not.toContain("Hedef yok.");
  });

  it("lists rows and counts them when there are some", () => {
    const html = render({ kind: "ok", value: ["a", "b"], at: 0 });
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain("<li>a</li>");
    expect(html).toContain(">2<");
  });
});
