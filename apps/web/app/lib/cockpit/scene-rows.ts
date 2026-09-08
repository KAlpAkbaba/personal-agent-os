/**
 * The 3B Sahne panel's row logic (M25 spec §6), kept pure and apart from
 * the client so a test can prove each sentence and each gate without a
 * network.
 *
 * Every function here reads the row as the list route sent it and says
 * either what it said or that it did not say it. Nothing infers a step: a
 * scene is verified because its row says `verified`, a render exists
 * because its row says so, and a tool is undriveable because its row says
 * `unavailable` — and only then is the row drawn without controls, because
 * there is nothing on the other side to ask.
 */

import { type SceneRunState, isSceneRunState } from "../uistate/contract";
import { SCENE_NO_OBJECT_NAMES, sceneStateWord } from "../uistate/labels";
import { sceneStatePhrase, sceneToolWord } from "../uistate/scenes";
import type { SceneAction, SceneBusy, SceneRow } from "./scenes";

/** How many of the list's scenes the panel shows: the last ones, as the route orders them. */
export const SCENE_ROWS_SHOWN = 8;

/** The chips' words, in the spec's order: render, then read the tool back. */
export const SCENE_ACTION_LABEL: Record<SceneAction, string> = {
  render: "Render al",
  inspect: "Sahneyi oku",
};

/** The row's step when it is one of the eight this build knows, else `null`. */
export function rowState(row: Pick<SceneRow, "state">): SceneRunState | null {
  return isSceneRunState(row.state) ? row.state : null;
}

/** The row's tool when it is one of the two this build knows, else `null` — used for the licence wording alone. */
export function rowTool(row: Pick<SceneRow, "tool">): "blender" | "unity" | null {
  return row.tool === "blender" || row.tool === "unity" ? row.tool : null;
}

/** True for a row whose step is `unavailable`: the tool could not be driven at all. */
export function rowIsUnavailable(row: Pick<SceneRow, "state">): boolean {
  return row.state === "unavailable";
}

/** True for a row whose step is `mismatch`: the read-back did not match, and the object is named. */
export function rowIsMismatch(row: Pick<SceneRow, "state">): boolean {
  return row.state === "mismatch";
}

/** True for a row whose step is `failed`: drawn to the owner's attention, with its error. */
export function rowIsFailed(row: Pick<SceneRow, "state">): boolean {
  return row.state === "failed";
}

/** True for a row whose step is `verified`: the read-back matched the plan. Nothing else is. */
export function rowIsVerified(row: Pick<SceneRow, "state">): boolean {
  return row.state === "verified";
}

/**
 * True when the ROW says a render exists to fetch. The image is drawn on
 * this and on nothing else — never on a step, never on a name, never on
 * hope: an `<img>` for a render nobody stored would show the owner a broken
 * picture where the honest answer is that there is none.
 */
export function rowHasRender(row: Pick<SceneRow, "has_render">): boolean {
  return row.has_render;
}

/**
 * Which controls a row shows at all: both chips for a scene whose step this
 * build can read and whose tool the Cloud Core could drive; NONE for an
 * `unavailable` row (there is no editor to ask), and none for a step this
 * build cannot read — the row logic does not know the scene is there to be
 * driven, and a chip that invited a click on that guess would be the page
 * inventing state. The owner's voice ("Render al") still reaches the Cloud
 * Core, which decides on its own terms.
 */
export function sceneRowActions(row: Pick<SceneRow, "state">): SceneAction[] {
  if (rowState(row) === null || rowIsUnavailable(row)) return [];
  return ["render", "inspect"];
}

/**
 * The alt text for one render: what the picture IS, named from the scene's
 * own name. A scene the row did not name gets a sentence that says so —
 * never an empty `alt`, and never a name this page made up.
 */
export function sceneRenderAlt(row: Pick<SceneRow, "scene">): string {
  return row.scene ? `${row.scene} sahnesinin son render'ı` : "Adı bildirilmeyen sahnenin son render'ı";
}

/**
 * One scene on one line under its name: "Blender · doğrulandı (3 nesne)",
 * "Blender · uyuşmazlık — Kure.Kup", "Unity · lisans yok — yapılamadı",
 * "durum bildirilmedi". The tool is printed whenever the row carried one
 * (the token verbatim for a word this build cannot read); the step carries
 * the object count on `verified` and the mismatching object on `mismatch`,
 * each only when the row named it.
 */
export function sceneRowLine(row: SceneRow): string {
  const state = rowState(row);
  const phrase = sceneStatePhrase({ state, tool: rowTool(row), objects: row.objects }, row.mismatch);
  const parts: string[] = [];
  const tool = sceneToolWord(row.tool);
  if (tool) parts.push(tool);
  parts.push(phrase ?? sceneStateWord(row.state));
  return parts.join(" · ");
}

/**
 * What the last inspection called the objects it read, on one line — the
 * names as the route listed them, or the statement that it listed none. A
 * row that never had an inspection has no line at all (the panel does not
 * draw one), because "no names" and "never read" are different answers.
 */
export function sceneObjectNamesLine(row: Pick<SceneRow, "object_names">): string {
  return row.object_names.length > 0 ? row.object_names.join(", ") : SCENE_NO_OBJECT_NAMES;
}

// ------------------------------------------------------------------ the gate

/** Said under a disabled chip while another call is in flight. */
export const SCENE_REASON_BUSY = "Bir istek sürüyor; sonucu bekleniyor.";

/** Said for a chip asked of a tool that could not be driven (the panel never draws one; the gate still answers). */
export const SCENE_REASON_UNAVAILABLE = "Araç sürülemiyor; istenecek bir şey yok.";

/** Said for a chip asked of a row whose step this build cannot read (the panel never draws one; the gate still answers). */
export const SCENE_REASON_UNKNOWN_STATE = "Sahnenin durumu bilinmiyor; istek gönderilmedi.";

export type SceneActionGate = {
  enabled: boolean;
  reason: string | null;
  reasonKind: "busy" | "unavailable" | "unknown_state" | null;
};

/**
 * Whether one chip may be pressed for this row, and if not, why in words.
 *
 * The page decides nothing the Cloud Core would not: a render or an
 * inspection is for a scene in a tool that can be driven (spec §6), so the
 * chip never invites a click the gate would refuse; and one call at a time,
 * so no editor is asked to start twice.
 */
export function sceneActionGate(
  row: Pick<SceneRow, "state">,
  action: SceneAction,
  busy: SceneBusy | null,
): SceneActionGate {
  void action;
  if (busy !== null) return { enabled: false, reason: SCENE_REASON_BUSY, reasonKind: "busy" };
  if (rowIsUnavailable(row)) return { enabled: false, reason: SCENE_REASON_UNAVAILABLE, reasonKind: "unavailable" };
  if (rowState(row) === null) return { enabled: false, reason: SCENE_REASON_UNKNOWN_STATE, reasonKind: "unknown_state" };
  return { enabled: true, reason: null, reasonKind: null };
}
