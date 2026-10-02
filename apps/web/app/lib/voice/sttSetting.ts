/**
 * Which recogniser the local mode asks Chrome for (chrome-on-device-stt).
 *
 * Three values, per browser, beside the "Yerel mod" switch in `localStorage`
 * (`app/core/usePreferences.ts` keeps `pagentos.core.localVoice` there the same way):
 *
 * - `kapali` - the default. The recogniser is started exactly as before this setting
 *   existed; nothing on it is written.
 * - `acik`   - on-device (`processLocally`) with our phrase list when Chrome says the
 *   Turkish pack is usable; otherwise today's path, with the reason recorded.
 * - `olc`    - alternate the two between recogniser runs and record which one heard
 *   each sentence. Nothing else changes.
 *
 * The owner approved this OFF (2026-10-01): turning it on is a separate decision after
 * the measurement. So there is no switch yet - `sttSettingUiEnabled` is the flag the
 * shell reads before it draws one - and anything that is not literally one of the three
 * values is `kapali`.
 */

export type SttSetting = "kapali" | "acik" | "olc";

export const STT_SETTING_DEFAULT: SttSetting = "kapali";

export const STT_SETTING_KEY = "pagentos.core.localStt";
/** "1" when the shell may show the setting's switch. Not the setting itself. */
export const STT_SETTING_UI_KEY = "pagentos.core.localSttUi";

export const STT_SETTING_LABEL: Record<SttSetting, string> = {
  kapali: "kapalı",
  acik: "açık (cihaz içi)",
  olc: "ölç (dönüşümlü)",
};

/** The read half of `localStorage`; injectable so tests need no window. */
export type SttSettingStorage = { getItem(key: string): string | null };

export function parseSttSetting(raw: unknown): SttSetting {
  return raw === "kapali" || raw === "acik" || raw === "olc" ? raw : STT_SETTING_DEFAULT;
}

function stored(key: string, storage: SttSettingStorage | null): string | null {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function readSttSetting(storage: SttSettingStorage | null): SttSetting {
  return parseSttSetting(stored(STT_SETTING_KEY, storage));
}

export function sttSettingUiEnabled(storage: SttSettingStorage | null): boolean {
  return stored(STT_SETTING_UI_KEY, storage) === "1";
}
