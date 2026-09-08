/**
 * The Uygulamalar panel's row logic (M23 spec §6), kept pure and apart from
 * the client so a test can prove each sentence without a network.
 *
 * Every function here reads the row as the list route sent it and says
 * either what it said or that it did not say it. Nothing infers a state: a
 * project is running because its row says `running`, and only then does it
 * get a link — and the link is built from the row's port alone, on the
 * loopback host, for the owner's own browser to open. This page never
 * fetches it.
 */

import { appTestsPhrase, appUrl } from "../uistate/apps";
import { appStateWord } from "../uistate/labels";
import type { AppAction, AppProjectRow, AppsBusy } from "./apps";

/** How many of the list's projects the panel shows: the last ones, as the route orders them. */
export const APP_ROWS_SHOWN = 8;

/** The chips' words, in the spec's order: run, stop, test. */
export const APP_ACTION_LABEL: Record<AppAction, string> = {
  run: "Çalıştır",
  stop: "Durdur",
  test: "Testleri çalıştır",
};

/** The project's kind in the owner's words when it is one the spec names (§1), verbatim otherwise. */
export const APP_KIND_LABEL: Record<string, string> = {
  web_static: "statik web",
  web_api: "web API",
  cli: "komut satırı",
};

export function appKindLabel(kind: string | null): string | null {
  if (!kind) return null;
  return APP_KIND_LABEL[kind] ?? kind;
}

/** True for a row whose state is `running` — the one state that earns a link and a "Durdur". */
export function rowIsRunning(row: Pick<AppProjectRow, "state">): boolean {
  return row.state === "running";
}

/**
 * The URL the owner's browser can open for this row: only for a RUNNING
 * project with a port, and only ever `http://127.0.0.1:<port>/`. A stopped
 * project that still carries its last port gets none — nothing is listening.
 */
export function appRowUrl(row: Pick<AppProjectRow, "state" | "port">): string | null {
  return rowIsRunning(row) ? appUrl(row.port) : null;
}

/**
 * One project on one line under its name: "çalışıyor · port: 8123",
 * "test edildi · testler: 12 geçti / 0 başarısız", "başarısız · testler:
 * 10 geçti / 2 başarısız", "iskeleti kuruluyor". The port's absence is said
 * only beside `running`; the counts' absence only beside a result — the
 * facts line's rule, applied to a row.
 */
export function appRowLine(row: AppProjectRow): string {
  const parts = [appStateWord(row.state)];
  if (row.port !== null) parts.push(`port: ${row.port}`);
  else if (rowIsRunning(row)) parts.push("port bildirilmedi");
  const tests = appTestsPhrase(row.tests);
  if (tests) parts.push(`testler: ${tests}`);
  else if (row.state === "tested" || row.state === "failed") parts.push("test sayısı bildirilmedi");
  return parts.join(" · ");
}

// ------------------------------------------------------------------ the gate

/** Said under a disabled chip while another call is in flight. */
export const APP_REASON_BUSY = "Bir istek sürüyor; sonucu bekleniyor.";

/** Said under a disabled "Çalıştır" / "Testleri çalıştır" for a project with no files on the device yet. */
export const APP_REASON_NOT_SCAFFOLDED = "Henüz iskeleti kurulmadı; cihazda çalıştırılacak dosya yok.";

/** Said under a disabled "Çalıştır" for a project already running. */
export const APP_REASON_ALREADY_RUNNING = "Zaten çalışıyor.";

/** Said under a disabled "Durdur" for a project that is not running. */
export const APP_REASON_NOT_RUNNING = "Çalışmıyor; durdurulacak süreç yok.";

export type AppActionGate = {
  enabled: boolean;
  reason: string | null;
  reasonKind: "busy" | "not_scaffolded" | "already_running" | "not_running" | null;
};

/**
 * Whether one chip may be pressed for this row, and if not, why in words.
 *
 * The page decides nothing the Cloud Core would not: a `planned` project has
 * no files on the device to run or test (ADR-0086 §2 — generation is
 * validated before it leaves the Cloud Core, and `project.scaffold` is what
 * writes them), a running one is not started twice, and only a running one
 * has a job to close — so the chip never invites a click the gate would
 * refuse; and one call at a time, so nothing is asked of the device twice.
 * A state this build cannot read gates nothing: the Cloud Core refuses on
 * its own terms, and the refusal is printed.
 */
export function appActionGate(row: Pick<AppProjectRow, "state">, action: AppAction, busy: AppsBusy | null): AppActionGate {
  if (busy !== null) return { enabled: false, reason: APP_REASON_BUSY, reasonKind: "busy" };
  switch (action) {
    case "run":
      if (row.state === "running") return { enabled: false, reason: APP_REASON_ALREADY_RUNNING, reasonKind: "already_running" };
      if (row.state === "planned") return { enabled: false, reason: APP_REASON_NOT_SCAFFOLDED, reasonKind: "not_scaffolded" };
      return { enabled: true, reason: null, reasonKind: null };
    case "stop":
      if (!rowIsRunning(row)) return { enabled: false, reason: APP_REASON_NOT_RUNNING, reasonKind: "not_running" };
      return { enabled: true, reason: null, reasonKind: null };
    case "test":
      if (row.state === "planned") return { enabled: false, reason: APP_REASON_NOT_SCAFFOLDED, reasonKind: "not_scaffolded" };
      return { enabled: true, reason: null, reasonKind: null };
  }
}
