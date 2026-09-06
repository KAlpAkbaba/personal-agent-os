/**
 * The device status the cockpit reads off `/v1/devices` rows (M18.3 spec §5.3).
 *
 * The row's own `status` is the ENROLLMENT status string; the heartbeat status rides under
 * `heartbeat_status`, in the companion's shape as shipped (a flat `display_state`, armed
 * alarms as a COUNT) or the spec's nested shape. Both must read the same, and a row with
 * no heartbeat status must say so rather than presume a lit screen.
 */

import { describe, expect, it } from "vitest";

import { parseDevice } from "../../app/lib/cockpit/api";

describe("parseDevice", () => {
  it("reads the companion's flat heartbeat status under heartbeat_status", () => {
    const device = parseDevice({
      device_id: "d-1",
      name: "masaüstü",
      status: "enrolled",
      presence: "online",
      heartbeat_status: {
        input_idle_s: 3.5,
        display_state: "off",
        display_observed_at: "2026-09-07T04:00:00Z",
        alarm_ringing: true,
        armed_alarm_count: 2,
        armed_alarms: [],
      },
    });
    expect(device.statusKnown).toBe(true);
    expect(device.online).toBe(true);
    expect(device.display_state).toBe("off");
    expect(device.display_observed_at).toBe("2026-09-07T04:00:00Z");
    expect(device.alarm_ringing).toBe(true);
    expect(device.armed_alarms).toBe(2);
    expect(device.input_idle_s).toBe(3.5);
  });

  it("reads the spec's nested shape and an id list the same way", () => {
    const device = parseDevice({
      device_id: "d-1",
      heartbeat_status: {
        input_idle_s: 900,
        display: { state: "on", observed_at: "2026-09-07T04:00:00Z" },
        armed_alarms: ["a-1", "a-2"],
      },
    });
    expect(device.display_state).toBe("on");
    expect(device.armed_alarms).toBe(2);
  });

  it("never mistakes the enrollment status string for a heartbeat status", () => {
    const device = parseDevice({ device_id: "d-1", status: "enrolled", presence: "offline" });
    expect(device.statusKnown).toBe(false);
    expect(device.display_state).toBeNull();
    expect(device.armed_alarms).toBeNull();
    expect(device.online).toBe(false);
  });

  it("accepts a bare count under armed_alarms", () => {
    const device = parseDevice({ device_id: "d-1", heartbeat_status: { armed_alarms: 1 } });
    expect(device.armed_alarms).toBe(1);
  });
});
