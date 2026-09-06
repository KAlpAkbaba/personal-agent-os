/**
 * The Active Eye's own vocabulary (M18_HOLOGRAPHIC_CORE_SPEC.md §2).
 *
 * These mirror `services/api/app/presence/observations.py`'s closed field set
 * exactly — same names, same enums, same seven keys and no others. That file
 * is the authority; this is a copy of its contract for the device side, the
 * same relationship `app/lib/uistate/contract.ts` has with the API's UI-state
 * contract.
 */

export const ACTIVITY_LEVELS = ["none", "low", "medium", "high"] as const;
export type ActivityLevel = (typeof ACTIVITY_LEVELS)[number];

export const POSTURES = ["unknown", "upright", "resting"] as const;
export type Posture = (typeof POSTURES)[number];

export const AWAKE_STATES = ["awake", "resting", "uncertain"] as const;
export type AwakeState = (typeof AWAKE_STATES)[number];

/**
 * The API accepts four sources; this module only ever produces `"camera"` —
 * it is the local-perception client, not the voice or task publishers that
 * share the same endpoint.
 */
export const OBSERVATION_SOURCE = "camera" as const;

/**
 * One structured perception reading — exactly the seven bounded fields the
 * Cloud Core boundary accepts. Never a frame, never a crop, never anything
 * imagery-shaped; see `signal.ts` and `perception.ts` for how that is a
 * property of the code producing this type, not a promise about it.
 */
export type EyeObservation = {
  person_present: boolean;
  presence_confidence: number;
  activity_level: ActivityLevel;
  posture: Posture;
  awake_state: AwakeState;
  /** ISO-8601, UTC, `Z`-suffixed. */
  observed_at: string;
  source: typeof OBSERVATION_SOURCE;
};

/**
 * Why the LOCAL eye capability failed, in the closed vocabulary the Cloud
 * Core's action receipt speaks (M18_ACTION_CONTRACT.md §5.1, §5.2). The
 * server maps each to one spoken sentence; nothing outside this set may be
 * relayed, so `EyeStore` maps every failure onto exactly one of these —
 * structured, never a generic "could not open" (owner requirement,
 * 2026-09-06). Each names WHERE the action stopped:
 *
 * - `permission_denied` — `getUserMedia` refused: NotAllowedError / SecurityError;
 * - `device_not_found` — no such camera: NotFoundError / OverconstrainedError;
 * - `device_busy` — the camera exists but could not be read: NotReadableError / AbortError;
 * - `get_user_media_failed` — any other `getUserMedia` rejection;
 * - `stream_created_but_track_ended` — `getUserMedia` resolved, but the video
 *   track's `readyState` was not `"live"` (the track is stopped, nothing durable);
 * - `perception_start_failed` — the stream opened, but the sampling loop's
 *   start threw afterwards;
 * - `state_transition_failed` — the store could not reach the requested state
 *   for any other reason (an unexpected throw in the transition itself);
 * - `timeout` — the local bound (`LOCAL_EYE_TIMEOUT_MS`) elapsed first;
 * - `capability_missing` — this client has no camera API at all.
 */
export const EYE_ERROR_CLASSES = [
  "permission_denied",
  "device_not_found",
  "device_busy",
  "get_user_media_failed",
  "stream_created_but_track_ended",
  "perception_start_failed",
  "state_transition_failed",
  "timeout",
  "capability_missing",
] as const;
export type EyeErrorClass = (typeof EYE_ERROR_CLASSES)[number];

/** A `MediaStreamTrack.readyState`, or `null` when this device holds no video track. */
export type MediaTrackReadyState = "live" | "ended";

/** The browser's own answer to "may this page use the camera". */
export type CameraPermission =
  /** The owner has granted it; `getUserMedia` should not prompt again. */
  | "granted"
  /** The owner has refused it, in this session or permanently. */
  | "denied"
  /** Neither granted nor denied yet — asking will show the browser prompt. */
  | "prompt"
  /** This browser has no Permissions API for `camera`; only `getUserMedia`
   *  itself will say, and only once it is actually called. */
  | "unsupported"
  /** Not checked yet. Distinct from `"unsupported"` so the UI can tell
   *  "we have not asked" from "this browser cannot answer". */
  | "unknown";
