/**
 * The Görevler panel's row logic (M26 spec §6), kept pure and apart from
 * the client so a test can prove each sentence and each gate without a
 * network.
 *
 * Every function here reads the row as the list route sent it and says
 * either what it said or that it did not say it. Nothing infers a state: a
 * run is running because its row says `running`, and only then does it get
 * a "Duraklat"; a run is paused because its row says `paused`, and only
 * then does it get a "Devam". A run that ended — however it ended — gets
 * neither, and a state this build cannot read gets nothing at all: the row
 * logic never invents that a run is going.
 */

import { type ExecutiveRunState, isExecutiveRunState } from "../uistate/contract";
import { executiveRunIsActive, executiveStepsPhrase } from "../uistate/executive";
import { EXECUTIVE_MISSING_UNTOLD, EXECUTIVE_STEPS_UNTOLD, executiveStateLine } from "../uistate/labels";
import type { ExecutiveBusy, ExecutiveChipAction, ExecutiveMissingStep, ExecutiveRunRow } from "./executive";

/** How many of the list's runs the panel shows: the last ones, as the route orders them. */
export const EXECUTIVE_ROWS_SHOWN = 8;

/** The chips' words, in the spec's own order: "Duraklat / Devam / İptal". */
export const EXECUTIVE_ACTION_LABEL: Record<ExecutiveChipAction, string> = {
  pause: "Duraklat",
  resume: "Devam",
  cancel: "İptal",
  approve: "Onayla",
};

export const EXECUTIVE_REASON_NOT_AWAITING = "onay bekleyen adım yok";

/** B38 (req 544): true for an unfinished row that names the step it waits on. */
export function rowAwaitsApproval(row: Pick<ExecutiveRunRow, "state" | "awaiting_step">): boolean {
  return rowIsActive(row) && typeof row.awaiting_step === "string" && row.awaiting_step !== "";
}

/** The row's state when it is one of the seven this build knows, else `null`. */
export function rowState(row: Pick<ExecutiveRunRow, "state">): ExecutiveRunState | null {
  return isExecutiveRunState(row.state) ? row.state : null;
}

/** True for a row whose run has not ended — the runs the owner still has levers over. */
export function rowIsActive(row: Pick<ExecutiveRunRow, "state">): boolean {
  return executiveRunIsActive(rowState(row));
}

/**
 * True for a row the publisher said is RUNNING — the one state where
 * "Duraklat" means something. A `planned` run is deliberately here too:
 * pause means "finish the running activity and start none" (spec §3), and
 * starting none is exactly as meaningful before the first step as during
 * the third. A `paused` run is not: pausing a paused run asks for nothing.
 */
export function rowIsPausable(row: Pick<ExecutiveRunRow, "state">): boolean {
  const state = rowState(row);
  return state === "planned" || state === "running";
}

/** True for a row whose state is `paused` — the ONLY state where "Devam" means something. */
export function rowIsPaused(row: Pick<ExecutiveRunRow, "state">): boolean {
  return row.state === "paused";
}

/** True for a row whose state is `partial`: the run ended with steps that did not verify, and they are named. */
export function rowIsPartial(row: Pick<ExecutiveRunRow, "state">): boolean {
  return row.state === "partial";
}

/** True for a row whose state is `failed`: drawn to the owner's attention. */
export function rowIsFailed(row: Pick<ExecutiveRunRow, "state">): boolean {
  return row.state === "failed";
}

/**
 * True for a row whose state is `completed` — the ONE row state that may be
 * read as done. Nothing else in this file, and nothing in the panel, may
 * answer this question (ADR-0089 §3).
 */
export function rowIsComplete(row: Pick<ExecutiveRunRow, "state">): boolean {
  return row.state === "completed";
}

/**
 * Which controls a row shows at all: "Duraklat" while the run is planned or
 * running, "Devam" only while it is paused, "İptal" while it has not ended
 * — and none once it has, however it ended. A state this build cannot read,
 * or none at all, shows nothing: the row logic does not know the run is
 * going, and a chip that invited a click on that guess would be the page
 * inventing state. The owner's voice ("Bu işi durdur", "Devam et", "Bunu
 * iptal et") still reaches the Cloud Core, which decides on its own terms.
 */
export function executiveRowActions(row: Pick<ExecutiveRunRow, "state"> & { awaiting_step?: string | null }): ExecutiveChipAction[] {
  const actions: ExecutiveChipAction[] = [];
  if (rowAwaitsApproval({ state: row.state, awaiting_step: row.awaiting_step ?? null })) actions.push("approve");
  if (rowIsPausable(row)) actions.push("pause");
  if (rowIsPaused(row)) actions.push("resume");
  if (rowIsActive(row)) actions.push("cancel");
  return actions;
}

/**
 * Each missing step as the owner reads it: "s4 (belge bulunamadı)" where
 * the route gave a reason, "s5" where it did not. These are the words
 * `executiveStatePhrase` joins into the partial sentence, so the sentence
 * is spelled in ONE place (`lib/uistate/executive.ts`) and this file only
 * decides what goes into it.
 */
export function missingStepPhrases(missing: readonly ExecutiveMissingStep[]): string[] {
  return missing.map((m) => (m.reason ? `${m.step} (${m.reason})` : m.step));
}

/**
 * One run on one line under its goal: "adım s3 · çalışıyor · 3/5 adım",
 * "kısmen bitti — eksik: s4 (belge bulunamadı) · 3/5 adım", "tamamlandı ·
 * 5/5 adım", "duraklatıldı".
 *
 * The state carries its missing steps beside `partial` and only there (and
 * the statement that none were named, only there); the counts are printed
 * whenever the row counted BOTH and their absence said only beside
 * `partial`, where how much got done is the whole question. The step is
 * said only while the run is still on one — "adım s3 · tamamlandı" would
 * read as a run that ended in the middle of its third step.
 */
export function executiveRowLine(row: ExecutiveRunRow): string {
  const state = rowState(row);
  const parts: string[] = [];
  if (row.step && executiveRunIsActive(state)) parts.push(`adım ${row.step}`);
  // The missing steps ride into the ONE place the partial sentence is
  // spelled; the reasons in them are the ROUTE's words, never this page's.
  const missing = missingStepPhrases(row.missing);
  parts.push(executiveStateLine({ state, stateToken: row.state }, missing));
  if (state === "partial" && missing.length === 0) parts.push(EXECUTIVE_MISSING_UNTOLD);
  const steps = executiveStepsPhrase(row.done, row.total);
  if (steps) parts.push(steps);
  else if (state === "partial") parts.push(EXECUTIVE_STEPS_UNTOLD);
  return parts.join(" · ");
}

// ------------------------------------------------------------------ the gate

/** Said under a disabled chip while another call is in flight. */
export const EXECUTIVE_REASON_BUSY = "Bir istek sürüyor; sonucu bekleniyor.";

/** Said for a "Duraklat" asked of a run that is not going (the panel never draws one; the gate still answers). */
export const EXECUTIVE_REASON_NOT_RUNNING = "İş sürmüyor; duraklatılacak bir şey yok.";

/** Said for a "Devam" asked of a run that is not paused (the panel never draws one; the gate still answers). */
export const EXECUTIVE_REASON_NOT_PAUSED = "İş duraklatılmış değil; sürdürülecek bir şey yok.";

/** Said for an "İptal" asked of a run that has ended (the panel never draws one; the gate still answers). */
export const EXECUTIVE_REASON_NOT_ACTIVE = "İş bitmiş; iptal edilecek bir şey yok.";

export type ExecutiveActionGate = {
  enabled: boolean;
  reason: string | null;
  reasonKind: "busy" | "not_running" | "not_paused" | "not_active" | "not_awaiting" | null;
};

/**
 * Whether one chip may be pressed for this row, and if not, why in words.
 *
 * The page decides nothing the Cloud Core would not: a "Duraklat" is for a
 * run that is going, a "Devam" for one the owner stopped and an "İptal" for
 * one that has not ended (spec §3, §6), so the chip never invites a click
 * the gate would refuse; and one call at a time, so nothing is asked of the
 * Cloud Core twice.
 */
export function executiveActionGate(
  row: Pick<ExecutiveRunRow, "state"> & { awaiting_step?: string | null },
  action: ExecutiveChipAction,
  busy: ExecutiveBusy | null,
): ExecutiveActionGate {
  if (busy !== null) return { enabled: false, reason: EXECUTIVE_REASON_BUSY, reasonKind: "busy" };
  switch (action) {
    case "approve":
      if (!rowAwaitsApproval({ state: row.state, awaiting_step: row.awaiting_step ?? null }))
        return { enabled: false, reason: EXECUTIVE_REASON_NOT_AWAITING, reasonKind: "not_awaiting" };
      return { enabled: true, reason: null, reasonKind: null };
    case "pause":
      if (!rowIsPausable(row)) return { enabled: false, reason: EXECUTIVE_REASON_NOT_RUNNING, reasonKind: "not_running" };
      return { enabled: true, reason: null, reasonKind: null };
    case "resume":
      if (!rowIsPaused(row)) return { enabled: false, reason: EXECUTIVE_REASON_NOT_PAUSED, reasonKind: "not_paused" };
      return { enabled: true, reason: null, reasonKind: null };
    case "cancel":
      if (!rowIsActive(row)) return { enabled: false, reason: EXECUTIVE_REASON_NOT_ACTIVE, reasonKind: "not_active" };
      return { enabled: true, reason: null, reasonKind: null };
  }
}
