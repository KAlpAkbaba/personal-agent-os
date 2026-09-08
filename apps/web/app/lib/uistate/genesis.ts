/**
 * Capability Genesis's channel — contract v9 (M24 spec §8).
 *
 * The Cloud Core publishes `capability.genesis` at every transition of ONE
 * genesis run — the owner asked for something the registry could not
 * resolve, and the assistant is researching the interface, writing an
 * adapter, testing it against the running application, classifying it,
 * waiting for the owner when authority requires it, rolling it out,
 * registering it, using it for the original request and verifying the
 * result through the application itself — with `{capability?, state?,
 * approval_required?, error_class?}` in its metadata: the capability the
 * request needs, the run's state in the §5 names, whether authority parked
 * it for the owner, and on `failed` the error class.
 *
 * The rules are the app's, applied to a capability being acquired:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    capability is the token the publisher sent; the state is the state it
 *    sent. A missing key is rendered as "not reported", never filled in.
 * 2. **A state this build does not know is the plain state.** The caption
 *    for a `state` outside the thirteen the contract names is "Yeni
 *    yetenek" and no more: a word we cannot read is not a step we may
 *    narrate — and above all it is never "kullanılabilir" or "doğrulandı".
 * 3. **The directive's proof is said only when published (ADR-0087 §4).**
 *    "Doğrulandı" needs `verified`; "kullanılabilir" needs `available`; a
 *    `failed` is worded as one with its error class — never dressed as done,
 *    never rounded up.
 * 4. **Nothing here can research, build, approve or cancel.** This channel
 *    is presentation. The runs themselves are rows the Cockpit reads from
 *    the list route (`lib/cockpit/genesis.ts`), and "Onayla" / "Vazgeç"
 *    there ask the Cloud Core to run its own gate — never this page.
 */

import {
  GENESIS_CAPTION_BARE,
  GENESIS_STATE_LABEL,
  type GenesisRunState,
  type Severity,
  type UiStateEvent,
  isGenesisRunState,
  isGenesisState,
  isSeverity,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every genesis event, read verbatim. */
export type GenesisFacts = {
  /** The capability the request needs (`metadata.capability`), or `null` if none was sent. */
  capability: string | null;
  /** `metadata.state` exactly as sent, or `null` when none was. */
  stateToken: string | null;
  /** The state when it is one of the thirteen this build knows, else `null`. */
  state: GenesisRunState | null;
  /** `metadata.approval_required` when the publisher sent a flag, else `null`. */
  approvalRequired: boolean | null;
  /** The failure's class (`metadata.error_class`), verbatim, or `null`. */
  errorClass: string | null;
};

/** A flag the publisher actually sent, or `null` for anything that is not a boolean. */
function metaFlag(event: UiStateEvent | null, key: string): boolean | null {
  if (!event) return null;
  const value = event.metadata[key];
  return typeof value === "boolean" ? value : null;
}

/** The published facts on one event, or explicit nulls for no event. */
export function genesisFacts(event: UiStateEvent | null): GenesisFacts {
  const token = metaToken(event, "state");
  return {
    capability: metaToken(event, "capability"),
    stateToken: token,
    state: isGenesisRunState(token) ? token : null,
    approvalRequired: metaFlag(event, "approval_required"),
    errorClass: metaToken(event, "error_class"),
  };
}

// -------------------------------------------------------------- the posture

/**
 * The shape the Core takes for a run, from the published state alone:
 *
 *   building  — the assistant is working on the adapter (every state from
 *               `capability_missing` to `registering`, except the parked one)
 *   waiting   — `awaiting_approval`: the owner is the thing being waited on
 *   settled   — `available`, `used`, `verified`: the work is over and the
 *               capability is what the run says it is
 *   failed    — `failed`: the run stopped; the gap stays open
 *
 * A state this build cannot read, or none at all, is the building posture:
 * the only thing a `capability.genesis` with no readable state can mean is
 * that a run exists and nothing settled has been said about it.
 */
export type GenesisPosture = "building" | "waiting" | "settled" | "failed";

/** The states after which nothing is being built: the capability is available, used or verified. */
//: A cancelled run is settled, not building: the OWNER stopped it. Without the word here
//: the Core drew a run they had given up on as one still being made, because an unreadable
//: state falls through to the building posture (found 2026-09-08 by asking M25's question
//: of every earlier family).
export const GENESIS_SETTLED_STATES: readonly GenesisRunState[] = [
  "available",
  "used",
  "verified",
  "cancelled",
];

const GENESIS_SETTLED_SET: ReadonlySet<string> = new Set(GENESIS_SETTLED_STATES);

export function genesisPosture(state: GenesisRunState | null): GenesisPosture {
  if (state === "awaiting_approval") return "waiting";
  if (state === "failed") return "failed";
  if (state !== null && GENESIS_SETTLED_SET.has(state)) return "settled";
  return "building";
}

/**
 * True for a KNOWN state in which the run is still going — building or
 * parked for the owner — the one condition under which "Vazgeç" means
 * anything. A settled or failed run has nothing to give up; a state this
 * build cannot read is not known to be active (the row logic decides what
 * to do with that, on its own terms).
 */
export function genesisRunIsActive(state: GenesisRunState | null): boolean {
  const posture = genesisPosture(state);
  return state !== null && (posture === "building" || posture === "waiting");
}

// ------------------------------------------------------------- the captions

/** The state in the owner's words, re-exported from the contract where it is spelled once. */
export { GENESIS_CAPTION_BARE, GENESIS_STATE_LABEL } from "./contract";

/**
 * The states whose word is said FOR the capability rather than OF it:
 * "Sayaç kutusu için yetenek yok", "… için bağdaştırıcı yazılıyor" — the
 * thing named does not yet do anything; something is being made for it.
 * From `testing` on, the word is said of it: "Sayaç kutusu sınanıyor",
 * "… onay bekliyor", "… doğrulandı".
 */
export const GENESIS_PREPARING_STATES: readonly GenesisRunState[] = [
  "capability_missing",
  "researching",
  "designing",
  "building",
];

const GENESIS_PREPARING_SET: ReadonlySet<string> = new Set(GENESIS_PREPARING_STATES);

/** "başarısız — dependency_unavailable", or "başarısız" alone when no class was published. */
function failedWord(errorClass: string | null): string {
  return errorClass ? `${GENESIS_STATE_LABEL.failed} — ${errorClass}` : GENESIS_STATE_LABEL.failed;
}

/**
 * The state's word with the failure's class when there is one: the piece
 * every caption, facts line and row is built from. `null` for no state.
 */
export function genesisStatePhrase(facts: Pick<GenesisFacts, "state" | "errorClass">): string | null {
  if (facts.state === null) return null;
  return facts.state === "failed" ? failedWord(facts.errorClass) : GENESIS_STATE_LABEL[facts.state];
}

/**
 * The caption the Core draws under the posture (spec §8):
 *
 *   capability_missing → "Sayaç kutusu için yetenek yok — deneniyor"
 *   building           → "Sayaç kutusu için bağdaştırıcı yazılıyor"
 *   testing            → "Sayaç kutusu sınanıyor"
 *   awaiting_approval  → "Sayaç kutusu onay bekliyor"
 *   registering        → "Sayaç kutusu kaydediliyor"
 *   available          → "Sayaç kutusu kullanılabilir"
 *   used               → "Sayaç kutusu kullanıldı"
 *   verified           → "Sayaç kutusu doğrulandı"
 *   failed             → "Sayaç kutusu başarısız — dependency_unavailable"
 *
 * — each part only if it was published. Without a capability the caption
 * is the plain state name ("onay bekliyor", "başarısız — timeout") and no
 * more (rule 1); a state this build cannot read, or none at all, yields the
 * bare token name alone (rule 2), which is true of a run in any state.
 */
export function genesisCaption(facts: GenesisFacts): string {
  const phrase = genesisStatePhrase(facts);
  if (phrase === null) return GENESIS_CAPTION_BARE;
  if (!facts.capability) return phrase;
  const state = facts.state;
  return state !== null && GENESIS_PREPARING_SET.has(state)
    ? `${facts.capability} için ${phrase}`
    : `${facts.capability} ${phrase}`;
}

// ---------------------------------------------------------------- the view

export type GenesisStage =
  /** `capability.genesis` is current: a run was published and the claim has not aged out. */
  | "active"
  /** Nothing has been published about a genesis run, or the claim decayed. */
  | "none";

export type GenesisView = GenesisFacts & {
  stage: GenesisStage;
  /**
   * `"active"` when a genesis event was ever published, regardless of age;
   * `null` when none was — the panel's "nothing reported", as distinct from
   * a run we stopped hearing about.
   */
  lastKnown: "active" | null;
  /** The posture, from the facts alone. */
  posture: GenesisPosture;
  /** The caption, from the facts alone. */
  caption: string;
  /** The publisher's short label. Never prose. */
  label: string | null;
  taskId: string | null;
  severity: Severity;
  ageMs: number | null;
  /** True once the claim has aged out; `stage` is then `none`. */
  expired: boolean;
};

export function genesisView(claim: Claim): GenesisView {
  const event = claim.event;
  const named = event !== null && isGenesisState(event.state);
  const facts = genesisFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "active" : "none",
    lastKnown: named ? "active" : null,
    posture: genesisPosture(facts.state),
    caption: genesisCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually doing something about a capability right now. */
export function genesisIsActive(view: GenesisView): boolean {
  return view.stage === "active";
}

/** True for the facts of a run the publisher said is parked for the owner: the one state "Onayla" belongs to. */
export function genesisIsAwaiting(facts: Pick<GenesisFacts, "state">): boolean {
  return facts.state === "awaiting_approval";
}
