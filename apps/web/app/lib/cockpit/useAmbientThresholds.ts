"use client";

/**
 * Row 331 (B48, ADR-0079): the ambient thresholds and quiet hours, editable from the
 * Cockpit and Settings panels through the same owner-gated `PUT /v1/ambient/policy` the
 * four switches and the camera-mode chips already use.
 *
 * Kept apart from `CockpitPanels.tsx` for the same reason `useRoutineControl.ts` is: the
 * form logic — seeding a draft from the server, turning raw strings into a validated PUT
 * body, reporting the server's own sentence back — is plain functions a test can call
 * directly, and the hook is a thin `useState` wrapper around them. `buildThresholdChanges`
 * is the one a RED proof mutates: it is the only place a typo in a bound or a dropped
 * field would go unnoticed by every other test in this file.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  AMBIENT_THRESHOLD_FIELDS,
  type AmbientPolicy,
  type AmbientThresholdChanges,
  type AmbientThresholdField,
  type Loaded,
  updateAmbientThresholds as realUpdateAmbientThresholds,
} from "./api";

export type QuietHoursDraft = { start: string; end: string; timezone: string };

/**
 * Every threshold field as the RAW STRING an `<input>` holds, plus the quiet-hours draft
 * and its own explicit clear flag. A blank numeric field means "leave the server's own
 * value alone" — the owner touched nothing; `clearQuietHours` is a SEPARATE, explicit
 * checkbox rather than inferred from a blank window, precisely so "the form has not
 * finished loading yet" (both times still blank) can never be read as "clear it".
 */
export type AmbientThresholdsDraft = Record<AmbientThresholdField, string> & {
  quietHours: QuietHoursDraft;
  clearQuietHours: boolean;
};

const DEFAULT_TIMEZONE = "Europe/Istanbul";

export function emptyThresholdsDraft(): AmbientThresholdsDraft {
  const draft = {} as AmbientThresholdsDraft;
  for (const field of AMBIENT_THRESHOLD_FIELDS) draft[field] = "";
  draft.quietHours = { start: "", end: "", timezone: DEFAULT_TIMEZONE };
  draft.clearQuietHours = false;
  return draft;
}

/** The draft an owner who has changed nothing yet sees: the server's own numbers, spelled
 * as strings, and the current quiet-hours window (or the default timezone with a blank
 * window when none is set). */
export function draftFromPolicy(policy: AmbientPolicy): AmbientThresholdsDraft {
  const draft = {} as AmbientThresholdsDraft;
  for (const field of AMBIENT_THRESHOLD_FIELDS) {
    const value = policy[field];
    draft[field] = value === null || value === undefined ? "" : String(value);
  }
  draft.quietHours = {
    start: policy.quiet_hours?.start ?? "",
    end: policy.quiet_hours?.end ?? "",
    timezone: policy.quiet_hours?.timezone ?? DEFAULT_TIMEZONE,
  };
  draft.clearQuietHours = false;
  return draft;
}

/**
 * The form's raw strings -> a validated PUT body, or the first Turkish sentence that stops
 * it. A numeric field left blank is omitted (the owner did not touch it). The quiet-hours
 * window is sent only when BOTH times were typed; one filled and the other blank is refused
 * rather than silently dropped, so a half-finished edit never looks like a successful save.
 * `clearQuietHours` checked overrides all of that and sends the server's own clear flag.
 */
export function buildThresholdChanges(
  draft: AmbientThresholdsDraft,
): { changes: AmbientThresholdChanges } | { error: string } {
  const changes: AmbientThresholdChanges = {};
  for (const field of AMBIENT_THRESHOLD_FIELDS) {
    const raw = draft[field].trim();
    if (raw === "") continue;
    const value = Number(raw);
    if (!Number.isFinite(value)) return { error: `${field}: sayı olmalı.` };
    changes[field] = value;
  }
  if (draft.clearQuietHours) {
    changes.clear_quiet_hours = true;
  } else {
    const start = draft.quietHours.start.trim();
    const end = draft.quietHours.end.trim();
    if (start === "" && end === "") {
      // Untouched (or not yet loaded) — leave the server's own window exactly alone.
    } else if (start === "" || end === "") {
      return { error: "Sessiz saatlerin başlangıcı ve bitişi birlikte girilmeli." };
    } else {
      changes.quiet_hours = { start, end, timezone: draft.quietHours.timezone.trim() || DEFAULT_TIMEZONE };
    }
  }
  return { changes };
}

export type AmbientThresholdsMessage = { ok: boolean; text: string };

export type AmbientThresholdsControl = {
  draft: AmbientThresholdsDraft;
  saving: boolean;
  message: AmbientThresholdsMessage | null;
  onFieldChange: (field: AmbientThresholdField, raw: string) => void;
  onQuietHoursChange: (part: keyof QuietHoursDraft, raw: string) => void;
  onClearQuietHoursChange: (clear: boolean) => void;
  onSave: () => void;
};

/** The one network call, injectable so a test can record what would have been sent
 * without a fetch mock — the same seam `RoutineControlPorts.client` gives `useRoutineControl`. */
export type AmbientThresholdsPort = {
  update: (changes: AmbientThresholdChanges) => Promise<{ ok: boolean; speech: string }>;
};

const REAL_PORT: AmbientThresholdsPort = { update: realUpdateAmbientThresholds };

export function useAmbientThresholdsControl(
  policy: Loaded<AmbientPolicy>,
  onSaved?: () => void,
  port: AmbientThresholdsPort = REAL_PORT,
): AmbientThresholdsControl {
  const [draft, setDraft] = useState<AmbientThresholdsDraft>(emptyThresholdsDraft);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<AmbientThresholdsMessage | null>(null);
  // Seed the draft from the server exactly once — the cockpit polls this policy on its own
  // clock, and re-seeding on every poll would erase whatever the owner is mid-typing. The ref
  // is read/written only inside the effect, never during render.
  const seeded = useRef(false);
  useEffect(() => {
    if (!seeded.current && policy.kind === "ok") {
      seeded.current = true;
      setDraft(draftFromPolicy(policy.value));
    }
  }, [policy]);

  const onFieldChange = useCallback((field: AmbientThresholdField, raw: string) => {
    setDraft((prev) => ({ ...prev, [field]: raw }));
  }, []);

  const onQuietHoursChange = useCallback((part: keyof QuietHoursDraft, raw: string) => {
    setDraft((prev) => ({ ...prev, quietHours: { ...prev.quietHours, [part]: raw } }));
  }, []);

  const onClearQuietHoursChange = useCallback((clear: boolean) => {
    setDraft((prev) => ({ ...prev, clearQuietHours: clear }));
  }, []);

  const onSave = useCallback(() => {
    if (saving) return;
    const built = buildThresholdChanges(draft);
    if ("error" in built) {
      setMessage({ ok: false, text: built.error });
      return;
    }
    setSaving(true);
    setMessage(null);
    void port
      .update(built.changes)
      .then((result) => {
        setSaving(false);
        setMessage({ ok: result.ok, text: result.speech });
        if (result.ok) onSaved?.();
      })
      .catch((err: unknown) => {
        setSaving(false);
        setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
      });
  }, [draft, onSaved, port, saving]);

  return {
    draft,
    saving,
    message,
    onFieldChange,
    onQuietHoursChange,
    onClearQuietHoursChange,
    onSave,
  };
}
