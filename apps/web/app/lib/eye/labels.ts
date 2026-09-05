/**
 * Turkish surface text for the Active Eye's LOCAL control surface.
 *
 * This is a separate small vocabulary from `app/lib/uistate/labels.ts` on
 * purpose, the same way `types.ts` mirrors `app/presence/observations.py`
 * rather than `app/uistate/contract.py`: `uistate/labels.ts` names the states
 * the Cloud Core *told* the Core about (`eye.active` / `eye.disabled`, via
 * `EyeView`), which is the durable, server-side truth `AmbientBand` already
 * renders untouched by this feature. The strings here name what THIS
 * device's own camera and browser permission are doing right now — a
 * question the server-side truth cannot answer (it does not know whether
 * this specific tab has a live `MediaStream`), so it needs its own honest
 * wording rather than borrowing the other file's.
 */

import type { ActivityLevel, AwakeState, CameraPermission, Posture } from "./types";

export const ACTIVITY_LEVEL_LABEL: Record<ActivityLevel, string> = {
  none: "hareket yok",
  low: "az hareket",
  medium: "orta hareket",
  high: "yoğun hareket",
};

export const POSTURE_LABEL: Record<Posture, string> = {
  unknown: "bilinmiyor",
  upright: "dik",
  resting: "dinleniyor",
};

export const AWAKE_STATE_LABEL: Record<AwakeState, string> = {
  awake: "uyanık",
  resting: "dinleniyor",
  uncertain: "belirsiz",
};

/**
 * The local status line: what THIS device's camera is doing, distinguished
 * from the server-side `eye.*` truth the same way `AmbientBand` distinguishes
 * "disabled" from "untold" — "permission not granted", "the local loop is
 * off", and "we have not asked yet" are three different sentences.
 */
export function localEyeStatusTitle(running: boolean, permission: CameraPermission): string {
  if (running) return "Yerel algı çalışıyor";
  if (permission === "denied") return "Kamera izni verilmedi";
  if (permission === "prompt") return "Kamera izni istenmedi";
  if (permission === "unsupported") return "Tarayıcı izin durumunu bildiremiyor";
  if (permission === "unknown") return "Kamera izni kontrol ediliyor…";
  return "Yerel algı durduruldu"; // permission === "granted" but not running
}

export function localEyeStatusDetail(running: boolean, permission: CameraPermission): string {
  if (running) {
    return "Görüntü bu cihazdan çıkmaz; yalnızca sayısal gözlemler gönderilir.";
  }
  if (permission === "denied") {
    return "Tarayıcı ayarlarından bu sayfaya kamera izni verilmeli.";
  }
  if (permission === "prompt") {
    return "Başlat'a basınca tarayıcı izin isteyecek.";
  }
  if (permission === "unsupported") {
    return "İzin ancak kamerayı açmayı deneyince öğrenilebilir.";
  }
  if (permission === "unknown") {
    return "";
  }
  return "Kamera kapalı; ışığı sönük olmalı.";
}
