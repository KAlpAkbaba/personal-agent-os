/**
 * Row 331 (B48, ADR-0079): the ambient thresholds and quiet hours, editable from the
 * Cockpit and Settings panels.
 *
 * Three layers, tested the way this suite already tests the four switches and the
 * camera-mode chips (`b48-web.test.tsx`, `b48-camera-web.test.tsx`): the pure form logic
 * (`useAmbientThresholds.ts`) with no React at all, the write (`updateAmbientThresholds`)
 * against a mocked `apiFetch`, and the panel's rendering with a hand-built control object —
 * this suite has no jsdom, so a click is never simulated; what a handler DOES is proven by
 * calling it directly.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../../app/lib/session", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

import { AmbientPanel } from "../../app/core/panels/CockpitPanels";
import {
  AMBIENT_THRESHOLD_BOUNDS,
  AMBIENT_THRESHOLD_FIELDS,
  type AmbientPolicy,
  validateAmbientQuietHours,
  validateAmbientThreshold,
  updateAmbientThresholds,
} from "../../app/lib/cockpit/api";
import {
  type AmbientThresholdsControl,
  buildThresholdChanges,
  draftFromPolicy,
  emptyThresholdsDraft,
} from "../../app/lib/cockpit/useAmbientThresholds";

const POLICY: AmbientPolicy = {
  auto_off_enabled: true,
  off_when_away: true,
  off_when_asleep: false,
  wake_on_return: true,
  keep_on: false,
  away_after_s: 900,
  asleep_after_s: 600,
  asleep_min_confidence: 0.7,
  input_holdoff_s: 600,
  command_holdoff_s: 900,
  alarm_holdoff_s: 1800,
  return_holdoff_s: 600,
  asleep_after_outside_quiet_s: 1800,
  camera_unknown_grace_s: 120,
  quiet_hours: { start: "23:30", end: "07:30", timezone: "Europe/Istanbul" },
};

beforeEach(() => {
  apiFetch.mockReset();
});

// ------------------------------------------------------------- validateAmbientThreshold

describe("validateAmbientThreshold (bounds mirror PolicyIn)", () => {
  it("refuses every field's floor minus one and accepts the floor", () => {
    for (const field of AMBIENT_THRESHOLD_FIELDS) {
      const bounds = AMBIENT_THRESHOLD_BOUNDS[field];
      const belowFloor = field === "asleep_min_confidence" ? bounds.min - 0.5 : bounds.min - 1;
      expect(validateAmbientThreshold(field, belowFloor)).not.toBeNull();
      expect(validateAmbientThreshold(field, bounds.min)).toBeNull();
      expect(validateAmbientThreshold(field, bounds.max)).toBeNull();
      const aboveCeiling = field === "asleep_min_confidence" ? bounds.max + 0.5 : bounds.max + 1;
      expect(validateAmbientThreshold(field, aboveCeiling)).not.toBeNull();
    }
  });

  it("row 331's own safety rule: the four holdoffs refuse zero", () => {
    for (const field of ["input_holdoff_s", "command_holdoff_s", "alarm_holdoff_s", "return_holdoff_s"] as const) {
      expect(validateAmbientThreshold(field, 0)).not.toBeNull();
      expect(validateAmbientThreshold(field, 1)).toBeNull();
    }
  });

  it("refuses NaN and non-finite input before it ever reaches a bound", () => {
    expect(validateAmbientThreshold("away_after_s", Number.NaN)).not.toBeNull();
    expect(validateAmbientThreshold("away_after_s", Number.POSITIVE_INFINITY)).not.toBeNull();
  });
});

// ------------------------------------------------------------- validateAmbientQuietHours

describe("validateAmbientQuietHours", () => {
  const VALID = { start: "23:30", end: "07:30", timezone: "Europe/Istanbul" };

  it("accepts the one shape the server accepts, cross-midnight included", () => {
    expect(validateAmbientQuietHours(VALID)).toBeNull();
  });

  it("refuses anything that is not HH:MM", () => {
    expect(validateAmbientQuietHours({ ...VALID, start: "23:3" })).not.toBeNull();
    expect(validateAmbientQuietHours({ ...VALID, start: "24:00" })).not.toBeNull();
    expect(validateAmbientQuietHours({ ...VALID, end: "yarın sabah" })).not.toBeNull();
  });

  it("refuses a window whose start equals its end", () => {
    expect(validateAmbientQuietHours({ ...VALID, end: VALID.start })).not.toBeNull();
  });

  it("refuses an unrecognised IANA timezone", () => {
    expect(validateAmbientQuietHours({ ...VALID, timezone: "Mars/Olympus_Mons" })).not.toBeNull();
  });

  it("refuses a blank timezone", () => {
    expect(validateAmbientQuietHours({ ...VALID, timezone: "  " })).not.toBeNull();
  });
});

// ------------------------------------------------------------------- buildThresholdChanges

describe("buildThresholdChanges (the form's raw strings -> a PUT body)", () => {
  it("an untouched form sends nothing at all", () => {
    const result = buildThresholdChanges(emptyThresholdsDraft());
    expect(result).toEqual({ changes: {} });
  });

  it("only the fields the owner actually typed are sent", () => {
    const draft = emptyThresholdsDraft();
    draft.away_after_s = "1200";
    const result = buildThresholdChanges(draft);
    expect(result).toEqual({ changes: { away_after_s: 1200 } });
  });

  it("seeds from the server policy, so re-saving untouched sends every current value", () => {
    const draft = draftFromPolicy(POLICY);
    const result = buildThresholdChanges(draft);
    if ("error" in result) throw new Error("expected changes");
    expect(result.changes).toMatchObject({
      away_after_s: 900,
      asleep_after_s: 600,
      asleep_min_confidence: 0.7,
      input_holdoff_s: 600,
      command_holdoff_s: 900,
      alarm_holdoff_s: 1800,
      return_holdoff_s: 600,
      asleep_after_outside_quiet_s: 1800,
      camera_unknown_grace_s: 120,
    });
    expect(result.changes.quiet_hours).toEqual(POLICY.quiet_hours);
  });

  it("a non-numeric field is refused with the field named", () => {
    const draft = emptyThresholdsDraft();
    draft.away_after_s = "bir sürü";
    const result = buildThresholdChanges(draft);
    expect(result).toEqual({ error: "away_after_s: sayı olmalı." });
  });

  it("both quiet-hours times blank leaves the window untouched", () => {
    const result = buildThresholdChanges(emptyThresholdsDraft());
    if ("error" in result) throw new Error("expected changes");
    expect(result.changes.quiet_hours).toBeUndefined();
    expect(result.changes.clear_quiet_hours).toBeUndefined();
  });

  it("one quiet-hours time filled and the other blank is refused, never silently dropped", () => {
    const draft = emptyThresholdsDraft();
    draft.quietHours.start = "23:00";
    const result = buildThresholdChanges(draft);
    expect(result).toEqual({
      error: "Sessiz saatlerin başlangıcı ve bitişi birlikte girilmeli.",
    });
  });

  it("both times filled sends the window", () => {
    const draft = emptyThresholdsDraft();
    draft.quietHours = { start: "22:00", end: "06:00", timezone: "Europe/Istanbul" };
    const result = buildThresholdChanges(draft);
    expect(result).toEqual({
      changes: { quiet_hours: { start: "22:00", end: "06:00", timezone: "Europe/Istanbul" } },
    });
  });

  it("the explicit clear checkbox wins even over a half- or fully-typed window", () => {
    const draft = emptyThresholdsDraft();
    draft.quietHours.start = "22:00"; // half-typed, would otherwise be an error
    draft.clearQuietHours = true;
    const result = buildThresholdChanges(draft);
    expect(result).toEqual({ changes: { clear_quiet_hours: true } });
  });
});

// ---------------------------------------------------------------------- updateAmbientThresholds

describe("updateAmbientThresholds (the write)", () => {
  it("PUTs exactly the given changes and returns the Cloud Core's sentence", async () => {
    apiFetch.mockResolvedValue(new Response(JSON.stringify({ speech: "Eşikler güncellendi." }), { status: 200 }));
    const result = await updateAmbientThresholds({ away_after_s: 1200 });
    expect(result).toEqual({ ok: true, speech: "Eşikler güncellendi." });
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/ambient/policy");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ away_after_s: 1200 });
  });

  it("sends clear_quiet_hours through untouched, never inventing a quiet_hours key", async () => {
    apiFetch.mockResolvedValue(new Response(JSON.stringify({ speech: "ok" }), { status: 200 }));
    await updateAmbientThresholds({ clear_quiet_hours: true });
    const [, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ clear_quiet_hours: true });
  });

  it("refuses locally out of bounds without ever calling the network", async () => {
    const result = await updateAmbientThresholds({ away_after_s: 5 });
    expect(result.ok).toBe(false);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("refuses a malformed quiet window locally, without a request", async () => {
    const result = await updateAmbientThresholds({
      quiet_hours: { start: "25:00", end: "07:30", timezone: "Europe/Istanbul" },
    });
    expect(result.ok).toBe(false);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("surfaces the server's FastAPI validation list as an owner-readable line", async () => {
    apiFetch.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: [{ loc: ["body", "away_after_s"], msg: "Input should be >= 60", type: "greater_than_equal" }] }),
        { status: 422 },
      ),
    );
    const result = await updateAmbientThresholds({ command_holdoff_s: 300 });
    expect(result.ok).toBe(false);
    expect(result.speech).toContain("away_after_s");
    expect(result.speech).toContain("Input should be >= 60");
  });

  it("surfaces the server's owner_detail sentence (a ValueError caught server-side)", async () => {
    apiFetch.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { error_class: "validation_error", message: "isteği anlayamadım" } }),
        { status: 422 },
      ),
    );
    const result = await updateAmbientThresholds({ command_holdoff_s: 300 });
    expect(result.ok).toBe(false);
    expect(result.speech).toContain("isteği anlayamadım");
  });

  it("falls back to the HTTP status when the body carries nothing readable", async () => {
    apiFetch.mockResolvedValue(new Response("not json", { status: 500 }));
    const result = await updateAmbientThresholds({ command_holdoff_s: 300 });
    expect(result).toEqual({ ok: false, speech: "Eşikler güncellenemedi (HTTP 500)." });
  });

  it("answers a network failure in words and never throws", async () => {
    apiFetch.mockRejectedValue(new Error("ağ yok"));
    expect(await updateAmbientThresholds({ command_holdoff_s: 300 })).toEqual({ ok: false, speech: "ağ yok" });
  });
});

// ------------------------------------------------------------------------ the panel's form

function fakeControl(overrides: Partial<AmbientThresholdsControl> = {}): AmbientThresholdsControl {
  return {
    draft: draftFromPolicy(POLICY),
    saving: false,
    message: null,
    onFieldChange: () => undefined,
    onQuietHoursChange: () => undefined,
    onClearQuietHoursChange: () => undefined,
    onSave: () => undefined,
    ...overrides,
  };
}

function panelHtml(thresholds?: AmbientThresholdsControl) {
  return renderToStaticMarkup(
    <AmbientPanel
      policy={{ kind: "ok", value: POLICY, at: 0 }}
      devices={{ kind: "ok", value: [], at: 0 }}
      always
      thresholds={thresholds}
    />,
  );
}

describe("the thresholds form on the panel (row 331)", () => {
  it("stays read-only where no control is given, as it always has", () => {
    const html = panelHtml();
    expect(html).not.toContain("data-ambient-thresholds-form");
    expect(html).not.toContain("<form");
  });

  it("renders one bounded number input per field, seeded from the draft", () => {
    const html = panelHtml(fakeControl());
    for (const field of AMBIENT_THRESHOLD_FIELDS) {
      const bounds = AMBIENT_THRESHOLD_BOUNDS[field];
      expect(html).toContain(`data-ambient-threshold-input="${field}"`);
      expect(html).toContain(`min="${bounds.min}"`);
      expect(html).toContain(`max="${bounds.max}"`);
    }
    // A representative value round-trips from the draft onto the input.
    expect(html).toContain('data-ambient-threshold-input="away_after_s" value="900"');
  });

  it("renders the quiet-hours window and the explicit clear checkbox", () => {
    const html = panelHtml(fakeControl());
    expect(html).toContain('data-ambient-quiet-hours-start');
    expect(html).toContain('value="23:30"');
    expect(html).toContain('data-ambient-quiet-hours-end');
    expect(html).toContain('value="07:30"');
    expect(html).toContain('data-ambient-quiet-hours-timezone');
    expect(html).toContain('value="Europe/Istanbul"');
    expect(html).toContain("data-ambient-quiet-hours-clear");
  });

  it("disables the Save button while a save is in flight", () => {
    expect(panelHtml(fakeControl({ saving: true }))).toContain(
      'data-ambient-thresholds-save="true" disabled=""',
    );
    expect(panelHtml(fakeControl({ saving: false }))).not.toContain('disabled=""');
  });

  it("shows the server's own sentence, marked by whether it succeeded", () => {
    const ok = panelHtml(fakeControl({ message: { ok: true, text: "Eşikler güncellendi." } }));
    expect(ok).toContain('data-ambient-thresholds-ok="true"');
    expect(ok).toContain("Eşikler güncellendi.");

    const failed = panelHtml(fakeControl({ message: { ok: false, text: "60 ile 86400 arasında olmalı." } }));
    expect(failed).toContain('data-ambient-thresholds-ok="false"');
    expect(failed).toContain("60 ile 86400 arasında olmalı.");
  });
});
