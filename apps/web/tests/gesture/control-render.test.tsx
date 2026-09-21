/**
 * What the "El kumandası" control actually puts on the screen (ADR-0198, Stage 1).
 *
 * `GestureControlView` is a pure function of its props (see its own file), so — like
 * `EyeControlView` — it is rendered with `react-dom/server` in Node: no browser, no
 * Playwright, no jsdom.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import GestureControlView, { type GestureControlViewProps } from "../../app/core/GestureControlView";
import type { GestureControllerSnapshot } from "../../app/lib/gesture/controller";

const OFF: GestureControllerSnapshot = {
  enabled: false,
  running: false,
  lastGesture: null,
  lastGestureAtMs: null,
  trackingFps: null,
  measure: null,
  lastError: null,
};

function render(overrides: Partial<GestureControllerSnapshot> = {}, onToggle = vi.fn()) {
  const props: GestureControlViewProps = { gesture: { ...OFF, ...overrides }, onToggle };
  return renderToStaticMarkup(<GestureControlView {...props} />);
}

describe("GestureControlView: the toggle", () => {
  it("shows 'aç' when off and 'kapat' when on, with aria-pressed reflecting the toggle", () => {
    const off = render();
    expect(off).toContain("El kumandasını aç");
    expect(off).toContain('aria-pressed="false"');

    const on = render({ enabled: true });
    expect(on).toContain("El kumandasını kapat");
    expect(on).toContain('aria-pressed="true"');
  });

  it("waiting text shows once enabled but not yet running (gate not fully open)", () => {
    const html = render({ enabled: true, running: false });
    expect(html).toContain("Bekleniyor");
  });

  it("never shows the waiting text while off", () => {
    const html = render({ enabled: false, running: false });
    expect(html).not.toContain("Bekleniyor");
  });
});

describe("GestureControlView: the tracking HUD", () => {
  it("shows the fps once running", () => {
    const html = render({ enabled: true, running: true, trackingFps: 24.6 });
    expect(html).toContain("İzleniyor");
    expect(html).toContain("25 kare/sn");
  });

  it("shows the last gesture in Turkish, never the raw event name alone", () => {
    const html = render({ lastGesture: "swipe_right" });
    expect(html).toContain("sağa kaydırma");
  });

  it("falls back to the raw name for a gesture the label table does not (yet) know", () => {
    const html = render({ lastGesture: "future_gesture" as GestureControllerSnapshot["lastGesture"] });
    expect(html).toContain("future_gesture");
  });

  it("shows a tracker error when present", () => {
    const html = render({ lastError: "El takibi dosyaları yok; pnpm run fetch:mediapipe" });
    expect(html).toContain("El takibi dosyaları yok; pnpm run fetch:mediapipe");
  });

  it("shows nothing extra in the idle/off state", () => {
    const html = render();
    expect(html).not.toContain("İzleniyor");
    expect(html).not.toContain("Son hareket");
    expect(html).not.toContain("data-gesture-error");
  });
});
