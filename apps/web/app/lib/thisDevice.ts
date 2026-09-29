/**
 * "Which enrolled computer is this browser on?" (ADR-0208).
 *
 * A browser cannot know which Cloud Core device it shares a machine with: the enrolled agent
 * is a Windows service, the page is a tab. The owner says it once, on `/voice` ("Bu bilgisayar:
 * ofis"), and it is remembered per browser. It is sent as `device_id` when a voice session is
 * created or re-attached (contract v3), so a command that names no computer - "hesap
 * makinesini aç" - runs on the computer the owner is sitting at instead of whichever one Cloud
 * Core would have picked.
 *
 * It is a CLAIM and nothing more. Cloud Core honours it only for an enrolled, non-revoked,
 * online, capable device, ignores anything else without an error, and never treats it as
 * authority: the owner session is what authorises a command, this only chooses among machines
 * the owner could have named. So there is nothing here to protect beyond "not a UUID".
 *
 * Every storage access is guarded: a browser with storage off simply has no answer, and the
 * page behaves as it did before this existed.
 */

export const THIS_DEVICE_KEY = "pagentos.this_device_id";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type DeviceStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function defaultStorage(): DeviceStorage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** The remembered device id, or `null` (never set, storage unavailable, or not a UUID). */
export function getThisDeviceId(storage: DeviceStorage | null = defaultStorage()): string | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(THIS_DEVICE_KEY);
    return raw !== null && UUID.test(raw) ? raw.toLowerCase() : null;
  } catch {
    return null;
  }
}

/** Remember (or with `null` forget) the device. Returns whether the choice is now stored. */
export function setThisDeviceId(
  deviceId: string | null,
  storage: DeviceStorage | null = defaultStorage(),
): boolean {
  if (!storage) return false;
  try {
    if (deviceId === null) {
      storage.removeItem(THIS_DEVICE_KEY);
      return true;
    }
    if (!UUID.test(deviceId)) return false;
    storage.setItem(THIS_DEVICE_KEY, deviceId.toLowerCase());
    return true;
  } catch {
    return false;
  }
}
