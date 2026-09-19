/**
 * The "Yerel mod" switch hands the microphone over; it never leaves two channels live.
 * (Security review 2026-09-19, MEDIUM - see app/lib/voice/localToggle.ts.)
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { toggleLocalVoice } from "../../app/lib/voice/localToggle";

function channels() {
  const order: string[] = [];
  return {
    order,
    api: {
      setLocalVoice: (on: boolean) => void order.push(`set:${on}`),
      stopLocal: async () => void order.push("stopLocal"),
      disconnectPaid: async () => void order.push("disconnectPaid"),
    },
  };
}

describe("one voice channel at a time", () => {
  it("turning local mode ON ends the paid session first, and never the local one", async () => {
    const c = channels();
    await toggleLocalVoice(true, c.api);
    expect(c.order).toEqual(["disconnectPaid", "set:true"]);
  });

  it("turning it OFF ends the local session first, and never the paid one", async () => {
    const c = channels();
    await toggleLocalVoice(false, c.api);
    expect(c.order).toEqual(["stopLocal", "set:false"]);
  });

  it("a paid session that fails to close leaves the switch where it was", async () => {
    const c = channels();
    c.api.disconnectPaid = async () => {
      throw new Error("still connected");
    };
    await expect(toggleLocalVoice(true, c.api)).rejects.toThrow("still connected");
    expect(c.order).toEqual([]);
  });

  it("the control really routes its switch through the hand-over", () => {
    const control = readFileSync(
      join(__dirname, "..", "..", "app", "core", "VoiceControl.tsx"),
      "utf8",
    );
    expect(control).toContain("toggleLocalVoice(on, {");
    expect(control).toContain("disconnectPaid: () => actions.disconnect()");
    // The old handler flipped the preference itself and only ever stopped the local side.
    expect(control).not.toMatch(/onToggle: \(on\) => \{\s*setLocalVoice\(on\);/);
  });
});
