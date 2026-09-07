/**
 * The voice routing qualification panel (ADR-0080): five states, each a sentence about
 * rows that exist, and the honest words for a route this Cloud Core does not have.
 *
 * Rendered with `react-dom/server` like the rest of this suite. No browser.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { VoiceQualificationPanel } from "../../app/core/panels/CockpitPanels";
import type { VoiceQualification, VoiceQualificationRun } from "../../app/lib/cockpit/api";

const NOW = Date.parse("2026-09-08T08:00:00Z");

function run(overrides: Partial<VoiceQualificationRun> = {}): VoiceQualificationRun {
  return {
    recorded_at: "2026-09-08T02:00:00Z",
    age_s: 21_600,
    summary: "HEALTHY",
    corpus_version: 1,
    total_cases: 345,
    passed: 345,
    clarification: 0,
    failed_routing: 0,
    forbidden_side_effects: 0,
    confusion: [],
    ...overrides,
  };
}

function render(value: VoiceQualification) {
  return renderToStaticMarkup(
    <VoiceQualificationPanel state={{ kind: "ok", value, at: NOW }} now={NOW} />,
  );
}

describe("the voice routing qualification panel", () => {
  it("says the owner's audio test is still required when routing is healthy", () => {
    const html = render({
      state: "OWNER_AUDIO_TEST_REQUIRED",
      routing_state: "HEALTHY",
      owner_audio_qualified: false,
      open_opportunities: 0,
      latest_synthetic_run: run(),
      latest_owner_audio_run: null,
    });
    expect(html).toContain("sahibin ses testi bekleniyor");
    expect(html).toContain('data-voice-qualification-state="OWNER_AUDIO_TEST_REQUIRED"');
    expect(html).toContain("345 cümle");
    expect(html).toContain("345 doğru");
    expect(html).toContain("sahibin ses testi kayıtlı değil");
    expect(html).not.toContain("açık evrim fırsatı");
  });

  it("names the failing cases and draws attention when a regression is being healed", () => {
    const html = render({
      state: "SELF_HEALING",
      routing_state: "SELF_HEALING",
      owner_audio_qualified: false,
      open_opportunities: 2,
      latest_synthetic_run: run({
        summary: "REGRESSION_FOUND",
        passed: 343,
        failed_routing: 2,
        confusion: [
          { case_id: "d.wake.3", utterance: "Ekranı uyandır.", expected: "display_wake", resolved: "alarm_create" },
          { case_id: "a.stop.4", utterance: "Sustur.", expected: "alarm_stop", resolved: "none" },
        ],
      }),
      latest_owner_audio_run: null,
    });
    expect(html).toContain("kendini onarıyor");
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-voice-qualification-confusion="d.wake.3"');
    expect(html).toContain("display_wake → alarm_create");
    expect(html).toContain("Sustur.");
    expect(html).toContain("2 açık evrim fırsatı");
  });

  it("is empty, not failed, before the first run", () => {
    const html = render({
      state: "NOT_YET_RUN",
      routing_state: "NOT_YET_RUN",
      owner_audio_qualified: false,
      open_opportunities: 0,
      latest_synthetic_run: null,
      latest_owner_audio_run: null,
    });
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Henüz hiç sınama kaydı yok.");
  });

  it("says 'not on this server' for a 404 rather than an empty list", () => {
    const html = renderToStaticMarkup(
      <VoiceQualificationPanel
        state={{ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/voice/qualification yok (HTTP 404)." }}
        now={NOW}
      />,
    );
    expect(html).toContain("Henüz yok.");
    expect(html).not.toContain("sınama kaydı yok");
  });
});
