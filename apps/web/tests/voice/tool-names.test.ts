/**
 * The vendor spelling of a tool name (`eye__disable`) reads as the Cloud Core
 * spelling (`eye.disable`) wherever the client acts on a name locally; a name
 * already in the Cloud Core spelling is untouched. This is the one mapping
 * behind the owner's 2026-09-06 `capability_missing` defect.
 */

import { describe, expect, it } from "vitest";

import { VENDOR_DOT, cloudToolName } from "../../app/lib/voice/tool-names";

describe("cloudToolName", () => {
  it("maps the vendor spelling back onto the Cloud Core spelling", () => {
    expect(cloudToolName("eye__disable")).toBe("eye.disable");
    expect(cloudToolName("eye__enable")).toBe("eye.enable");
    expect(cloudToolName("state__now")).toBe("state.now");
    expect(cloudToolName("release__promote")).toBe("release.promote");
  });

  it("leaves a Cloud Core name unchanged", () => {
    expect(cloudToolName("eye.disable")).toBe("eye.disable");
    expect(cloudToolName("state.now")).toBe("state.now");
    expect(cloudToolName("clock.now")).toBe("clock.now");
  });

  it("maps every `__` (a nested namespace) and nothing else", () => {
    expect(cloudToolName("a__b__c")).toBe("a.b.c");
    expect(cloudToolName("snake_case_name")).toBe("snake_case_name"); // a single `_` is not the vendor dot
    expect(cloudToolName("")).toBe("");
    expect(VENDOR_DOT).toBe("__");
  });
});
