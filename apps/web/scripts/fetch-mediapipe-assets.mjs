#!/usr/bin/env node
/**
 * El hareketi kumandası (Stage 1) — fetches the two kinds of asset the in-browser hand
 * tracker needs (`app/lib/gesture/tracker.ts`), neither of which is committed to the repo
 * (gitignored: see `.gitignore`'s `public/mediapipe/` entry) and neither of which the
 * tracker ever fetches from a CDN at runtime — that is the point: frames and the model
 * that reads them stay entirely on this machine (M18's "frame never escapes" guarantee,
 * `app/lib/eye/perception.ts`, extends to the gesture feature the same way).
 *
 * 1. The WASM runtime: COPIED from `node_modules/@mediapipe/tasks-vision/wasm` (already
 *    verified by pnpm's own lockfile integrity hashes when it was installed) into
 *    `public/mediapipe/wasm/`. Each file's sha256 is checked against a pin recorded below;
 *    a mismatch is NOT fatal here (a legitimate `pnpm update` of the package changes these
 *    files on purpose) but is printed loudly so a developer notices and re-pins with
 *    `--reprint-pins` after confirming the new files are what they expect.
 * 2. The model: DOWNLOADED once from Google's public MediaPipe model bucket (a pinned,
 *    versioned URL, never "latest") into `public/mediapipe/hand_landmarker.task`. Its
 *    sha256 IS checked against a hard pin and a mismatch IS fatal (the file is deleted and
 *    the script exits 1) — unlike the local copy above, a byte that arrived over the network
 *    different from what was pinned when this script was written is exactly the case a pin
 *    exists to catch.
 *
 * Run with `pnpm run fetch:mediapipe` from `apps/web` (wired in package.json). Idempotent:
 * safe to run again; existing files whose sha256 already matches the pin are left alone.
 */

import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = join(scriptDir, "..");
const WASM_SRC_DIR = join(WEB_ROOT, "node_modules", "@mediapipe", "tasks-vision", "wasm");
const PUBLIC_MEDIAPIPE_DIR = join(WEB_ROOT, "public", "mediapipe");
const WASM_DEST_DIR = join(PUBLIC_MEDIAPIPE_DIR, "wasm");
const MODEL_DEST = join(PUBLIC_MEDIAPIPE_DIR, "hand_landmarker.task");

/**
 * ADR-0198 (Stage 1): a pinned, VERSIONED URL — never "latest" — for the float16 hand
 * landmark model (Apache-2.0, MediaPipe). "float16/1" is Google's own model version path
 * segment; bumping it is a deliberate, reviewed change to this file, not an automatic pull.
 */
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";

/** Computed 2026-09-21 from the URL above; see the module docstring for what a mismatch means. */
const MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1";
const MODEL_BYTES = 7_819_105;

/**
 * Computed 2026-09-21 against `@mediapipe/tasks-vision@1.0.1` (see apps/web/package.json).
 * A future `pnpm update` of that package is expected to change these — see the module
 * docstring for why that is a warning here, not a fatal error.
 */
const WASM_PINS = {
  "vision_wasm_internal.js": "e170ee67dd4e16c1a6fcd8840a206687e5a59b22c20e4a902bc445b095454d73",
  "vision_wasm_internal.wasm": "8da277a733926eacd0474b8704b36742d6ec3231c57a860c5b889dff8f1df886",
  "vision_wasm_module_internal.js": "da8934057f147b622e82cfb4c0dbd85461c598e268588b5a8ba9ca963a8ff82d",
  "vision_wasm_module_internal.wasm": "2dabd8e23c60984628beb7bb338764c81a08e6837145273f59578684b5d53c1b",
  "vision_wasm_nosimd_internal.js": "e81d715a3d42cc3373602eb2f7aff795d164934db680e32496b65dab537f9658",
  "vision_wasm_nosimd_internal.wasm": "a28483cd42e74e855bf5ebdb6b40d9b66a5b49e35e95020bc97669e6822a3192",
};

function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

function log(line) {
  process.stdout.write(`[fetch-mediapipe-assets] ${line}\n`);
}

function copyWasm() {
  if (!existsSync(WASM_SRC_DIR)) {
    log(`node_modules/@mediapipe/tasks-vision/wasm bulunamadı; önce "pnpm install" çalıştırın.`);
    process.exitCode = 1;
    return;
  }
  mkdirSync(WASM_DEST_DIR, { recursive: true });
  for (const [name, pin] of Object.entries(WASM_PINS)) {
    const src = join(WASM_SRC_DIR, name);
    if (!existsSync(src)) {
      log(`eksik paket dosyası: ${name} (@mediapipe/tasks-vision sürümü pinlerle uyuşmuyor olabilir)`);
      process.exitCode = 1;
      continue;
    }
    const buf = readFileSync(src);
    const actual = sha256(buf);
    if (actual !== pin) {
      log(
        `uyarı: ${name} sha256 pinle eşleşmiyor (beklenen ${pin}, bulunan ${actual}) — ` +
          `muhtemelen @mediapipe/tasks-vision güncellendi; dosya yine de kopyalanıyor, pinleri gözden geçirip güncelleyin.`,
      );
    }
    copyFileSync(src, join(WASM_DEST_DIR, name));
    log(`kopyalandı: wasm/${name}`);
  }
}

async function downloadModel() {
  if (existsSync(MODEL_DEST)) {
    const existing = readFileSync(MODEL_DEST);
    if (sha256(existing) === MODEL_SHA256) {
      log("hand_landmarker.task zaten var ve sha256 pinle eşleşiyor; indirme atlandı.");
      return;
    }
    log("hand_landmarker.task var ama sha256 pinle eşleşmiyor; yeniden indiriliyor.");
  }
  log(`indiriliyor: ${MODEL_URL}`);
  const response = await fetch(MODEL_URL);
  if (!response.ok) {
    log(`indirme başarısız: HTTP ${response.status}`);
    process.exitCode = 1;
    return;
  }
  const buf = Buffer.from(await response.arrayBuffer());
  const actual = sha256(buf);
  if (buf.length !== MODEL_BYTES || actual !== MODEL_SHA256) {
    log(
      `bütünlük hatası: model dosyası pinle eşleşmiyor ` +
        `(beklenen sha256 ${MODEL_SHA256}, ${MODEL_BYTES} bayt — bulunan ${actual}, ${buf.length} bayt). ` +
        `Dosya YAZILMADI; bu, olağan bir sürüm değişikliğinden değil, beklenmeyen bir ` +
        `içerik değişikliğinden şüphelenmeyi gerektirir.`,
    );
    process.exitCode = 1;
    return;
  }
  mkdirSync(PUBLIC_MEDIAPIPE_DIR, { recursive: true });
  writeFileSync(MODEL_DEST, buf);
  log(`indirildi ve doğrulandı: hand_landmarker.task (${buf.length} bayt)`);
}

async function main() {
  copyWasm();
  await downloadModel();
  if (process.exitCode) {
    log("bitti (hatalarla).");
  } else {
    log("bitti.");
  }
}

await main();
