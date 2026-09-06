/**
 * Tool-name spellings (the mirror of `services/api/app/voice/providers.py`'s
 * `vendor_tool_name` / `cloud_tool_name`).
 *
 * The Cloud Core names its tools with dots (`eye.disable`, `state.now`). The
 * realtime provider does not allow a dot in a function name, so the Cloud
 * Core hands the provider `eye__disable` / `state__now` and maps the spelling
 * back in its own registry when the client relays a call. The client relays
 * the vendor spelling VERBATIM — that is the contract, and it must stay so —
 * but anything the client does LOCALLY with a tool name (the `LocalActionPort`
 * for the camera, M18_ACTION_CONTRACT.md §7.2) must first read the name in the
 * Cloud Core spelling. The owner's 2026-09-06 run (session 3eb6fee7) was the
 * proof: every `eye__enable` / `eye__disable` reached the local port under
 * the vendor spelling, matched nothing, and the Cloud Core recorded
 * `capability_missing` for a browser that had a perfectly good camera.
 *
 * `__` is reserved for the vendor spelling of `.` (a Cloud Core name may not
 * contain it, the server refuses one that does), so the mapping is total and
 * a name already in the Cloud Core spelling passes through unchanged.
 */

/** The vendor's spelling of `.` in a function name. */
export const VENDOR_DOT = "__";

/** `eye__disable` -> `eye.disable`; `eye.disable` -> `eye.disable`. */
export function cloudToolName(name: string): string {
  return name.split(VENDOR_DOT).join(".");
}
