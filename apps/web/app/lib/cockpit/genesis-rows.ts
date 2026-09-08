/**
 * The Yeni Yetenek panel's row logic (M24 spec §8), kept pure and apart
 * from the client so a test can prove each sentence and each gate without
 * a network.
 *
 * Every function here reads the row as the list route sent it and says
 * either what it said or that it did not say it. Nothing infers a state: a
 * run is waiting for the owner because its row says `awaiting_approval`,
 * and only then does it get an "Onayla"; a run is active because its row
 * says one of the §5 working states, and only then does it get a "Vazgeç".
 * A settled or failed run gets neither, and a state this build cannot read
 * gets neither too — the row logic never invents that a run is going.
 */

import { type GenesisRunState, isGenesisRunState } from "../uistate/contract";
import { genesisRunIsActive, genesisStatePhrase } from "../uistate/genesis";
import { GENESIS_ERROR_CLASS_UNTOLD, genesisStateWord } from "../uistate/labels";
import type { GenesisAction, GenesisBusy, GenesisRunRow } from "./genesis";

/** How many of the list's runs the panel shows: the last ones, as the route orders them. */
export const GENESIS_ROWS_SHOWN = 8;

/** The chips' words, in the spec's order: approve, cancel. */
export const GENESIS_ACTION_LABEL: Record<GenesisAction, string> = {
  approve: "Onayla",
  cancel: "Vazgeç",
};

/** The authority class in the owner's words when it is one the spec names (§4), verbatim otherwise. */
export const GENESIS_AUTHORITY_LABEL: Record<string, string> = {
  read_only: "salt okunur",
  mutating_authorized_asset: "yetkili varlıkta değişiklik",
  mutating_unauthorized: "yetkisiz varlıkta değişiklik",
};

/** The side-effect class in the owner's words when it is one the spec names (§4), verbatim otherwise. */
export const GENESIS_SIDE_EFFECT_LABEL: Record<string, string> = {
  none: "yan etkisiz",
  read: "okuma",
  mutate_external: "dış değişiklik",
};

export function genesisAuthorityLabel(value: string | null): string | null {
  if (!value) return null;
  return GENESIS_AUTHORITY_LABEL[value] ?? value;
}

export function genesisSideEffectLabel(value: string | null): string | null {
  if (!value) return null;
  return GENESIS_SIDE_EFFECT_LABEL[value] ?? value;
}

/** The row's state when it is one of the thirteen this build knows, else `null`. */
export function rowState(row: Pick<GenesisRunRow, "state">): GenesisRunState | null {
  return isGenesisRunState(row.state) ? row.state : null;
}

/** True for a row whose state is `awaiting_approval` — the one state that earns an "Onayla". */
export function rowIsAwaiting(row: Pick<GenesisRunRow, "state">): boolean {
  return row.state === "awaiting_approval";
}

/** True for a row in a KNOWN working or waiting state — the runs that earn a "Vazgeç". */
export function rowIsActive(row: Pick<GenesisRunRow, "state">): boolean {
  return genesisRunIsActive(rowState(row));
}

/** True for a row whose state is `failed`: drawn to the owner's attention, with its error. */
export function rowIsFailed(row: Pick<GenesisRunRow, "state">): boolean {
  return row.state === "failed";
}

/**
 * Which controls a row shows at all: "Onayla" only at `awaiting_approval`
 * (M24 spec §8 — never anywhere else), "Vazgeç" while the run is active
 * (a parked run is active: giving up is the other answer to "Onaylıyorum"),
 * and none once the run is settled or failed. A state this build cannot
 * read, or none at all, shows nothing: the row logic does not know the run
 * is going, and a chip that invited a click on that guess would be the
 * page inventing state. The owner's voice ("Vazgeç, yapma") still reaches
 * the Cloud Core, which decides on its own terms.
 */
export function genesisRowActions(row: Pick<GenesisRunRow, "state">): GenesisAction[] {
  const actions: GenesisAction[] = [];
  if (rowIsAwaiting(row)) actions.push("approve");
  if (rowIsActive(row)) actions.push("cancel");
  return actions;
}

/**
 * One run on one line under its capability: "onay bekliyor · onay gerekli ·
 * yetki: yetkisiz varlıkta değişiklik · etki: dış değişiklik", "başarısız —
 * dependency_unavailable", "doğrulandı". The state carries its error class
 * beside `failed` (and the statement that none came, only there); the
 * approval flag and the two classes are printed whenever the row carried
 * them and their absence is never said — a class nobody published is not a
 * fact either way.
 */
export function genesisRowLine(row: GenesisRunRow): string {
  const state = rowState(row);
  const phrase = genesisStatePhrase({ state, errorClass: row.error_class });
  const parts = [phrase ?? genesisStateWord(row.state)];
  if (state === "failed" && row.error_class === null) parts.push(GENESIS_ERROR_CLASS_UNTOLD);
  if (row.approval_required !== null) parts.push(row.approval_required ? "onay gerekli" : "onay gerekmiyor");
  const authority = genesisAuthorityLabel(row.authority_class);
  if (authority) parts.push(`yetki: ${authority}`);
  const effect = genesisSideEffectLabel(row.side_effect_class);
  if (effect) parts.push(`etki: ${effect}`);
  return parts.join(" · ");
}

// ------------------------------------------------------------------ the gate

/** Said under a disabled chip while another call is in flight. */
export const GENESIS_REASON_BUSY = "Bir istek sürüyor; sonucu bekleniyor.";

/** Said for an "Onayla" asked of a run that is not waiting for the owner (the panel never draws one; the gate still answers). */
export const GENESIS_REASON_NOT_AWAITING = "Onay bekleyen bir çalışma değil; onaylanacak bir şey yok.";

/** Said for a "Vazgeç" asked of a run that is not active (the panel never draws one; the gate still answers). */
export const GENESIS_REASON_NOT_ACTIVE = "Çalışma sürmüyor; vazgeçilecek bir şey yok.";

export type GenesisActionGate = {
  enabled: boolean;
  reason: string | null;
  reasonKind: "busy" | "not_awaiting" | "not_active" | null;
};

/**
 * Whether one chip may be pressed for this row, and if not, why in words.
 *
 * The page decides nothing the Cloud Core would not: an "Onayla" is for a
 * run that is waiting for the owner and a "Vazgeç" for one that is going
 * (spec §8), so the chip never invites a click the gate would refuse; and
 * one call at a time, so nothing is asked of the Cloud Core twice.
 */
export function genesisActionGate(row: Pick<GenesisRunRow, "state">, action: GenesisAction, busy: GenesisBusy | null): GenesisActionGate {
  if (busy !== null) return { enabled: false, reason: GENESIS_REASON_BUSY, reasonKind: "busy" };
  switch (action) {
    case "approve":
      if (!rowIsAwaiting(row)) return { enabled: false, reason: GENESIS_REASON_NOT_AWAITING, reasonKind: "not_awaiting" };
      return { enabled: true, reason: null, reasonKind: null };
    case "cancel":
      if (!rowIsActive(row)) return { enabled: false, reason: GENESIS_REASON_NOT_ACTIVE, reasonKind: "not_active" };
      return { enabled: true, reason: null, reasonKind: null };
  }
}
