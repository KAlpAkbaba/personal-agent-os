/**
 * Contract v2's two new channels: the room, and the release path.
 *
 * The rule under test throughout is that these are *statements of a different
 * kind* from the agent's activity. The owner going to bed is not the assistant
 * going quiet; a deployment being watched is not the assistant working. So they
 * are asserted here to sit beside the core, to carry their own age, and — for
 * presence — to expire into "unknown" rather than into "still there".
 */

import { describe, expect, it } from "vitest";

import { eyeView, presenceView, releaseView } from "../../app/lib/uistate/ambient";
import {
  OBSERVATION_TTL_MS,
  OPERATION_TTL_MS,
  isCoreChannel,
  isPresenceState,
  stateChannel,
  stateTtlMs,
} from "../../app/lib/uistate/contract";
import { PRESENCE_LABEL, formatConfidence } from "../../app/lib/uistate/labels";
import {
  applyResponse,
  coreClaim,
  emptyTruth,
  eyeClaim,
  presenceClaim,
  releaseClaim,
} from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  ALARM_TRIGGERED,
  EYE_ACTIVE,
  EYE_DISABLED,
  OWNER_AWAY,
  OWNER_LIKELY_ASLEEP,
  OWNER_PRESENT,
  OWNER_PRESENT_NO_CONFIDENCE,
  RELEASE_APPROVAL_REQUIRED,
  RELEASE_DEPLOYING,
  ROUTINE_ARMED,
  T0,
  VOICE_THINKING,
  event,
  resetSequence,
  response,
} from "./fixtures";

function truthOf(...events: ReturnType<typeof event>[]) {
  resetSequence();
  return applyResponse(emptyTruth(), response(events.map((e) => e)), T0);
}

describe("channels do not displace one another", () => {
  it("presence does not blank a core that is genuinely working", () => {
    resetSequence();
    const thinking = VOICE_THINKING();
    const asleep = OWNER_LIKELY_ASLEEP();
    const truth = applyResponse(emptyTruth(), response([thinking, asleep]), T0);

    // The API's `current` is the newest event overall — the presence one.
    expect(truth.current?.state).toBe("owner.likely_asleep");
    // The core still shows the work it was told about.
    expect(coreClaim(truth, T0).event?.state).toBe("agent.thinking");
    expect(visualFor(truth, T0).kind).toBe("thinking");
    // And the room is still readable, on its own channel.
    expect(presenceView(presenceClaim(truth, T0)).kind).toBe("likely_asleep");
  });

  it("the eye and the owner are separate facts", () => {
    resetSequence();
    const truth = applyResponse(emptyTruth(), response([EYE_ACTIVE(), OWNER_AWAY()]), T0);

    // A presence update must not silently blank the privacy indicator.
    expect(eyeView(eyeClaim(truth, T0)).status).toBe("active");
    expect(presenceView(presenceClaim(truth, T0)).kind).toBe("away");
  });

  it("assigns every v2 state to a channel deliberately", () => {
    expect(stateChannel("eye.active")).toBe("ambient");
    expect(stateChannel("owner.likely_asleep")).toBe("ambient");
    expect(stateChannel("release.deploying")).toBe("release");
    expect(stateChannel("routine.armed")).toBe("release");
    expect(stateChannel("alarm.triggered")).toBe("release");
    expect(stateChannel("agent.thinking")).toBe("agent");
    expect(stateChannel("evolution.building")).toBe("lab");
    expect(isCoreChannel("owner.present")).toBe(false);
    expect(isCoreChannel("agent.idle")).toBe(true);
  });
});

describe("the eye indicator", () => {
  it("says 'active' only when the eye said so", () => {
    const view = eyeView(eyeClaim(truthOf(EYE_ACTIVE()), T0));
    expect(view.status).toBe("active");
    expect(view.camera).toBe("cam-0");
  });

  it("never claims the camera is off on no evidence", () => {
    const view = eyeView(eyeClaim(emptyTruth(), T0));
    expect(view.status).toBe("untold");
    expect(view.status).not.toBe("disabled");
    expect(view.unknownState).toBe(false);
  });

  it("an eye state this build cannot read is flagged, not read as off", () => {
    const view = eyeView(eyeClaim(truthOf(event({ state: "eye.recalibrating" })), T0));
    expect(view.status).toBe("untold");
    expect(view.unknownState).toBe(true);
  });

  it("reports disabled when the owner turned it off", () => {
    expect(eyeView(eyeClaim(truthOf(EYE_DISABLED()), T0)).status).toBe("disabled");
  });
});

describe("presence is probabilistic, and says so", () => {
  it("carries the engine's confidence verbatim", () => {
    const view = presenceView(presenceClaim(truthOf(OWNER_LIKELY_ASLEEP(0.86)), T0));
    expect(view.kind).toBe("likely_asleep");
    expect(view.confidence).toBe(0.86);
    expect(view.signals).toBe(4);
    expect(formatConfidence(view.confidence)).toBe("güven %86");
  });

  it("says the confidence is missing rather than inventing one", () => {
    const view = presenceView(presenceClaim(truthOf(OWNER_PRESENT_NO_CONFIDENCE()), T0));
    expect(view.kind).toBe("present");
    expect(view.confidence).toBeNull();
    expect(formatConfidence(view.confidence)).toBe("güven bildirilmedi");
  });

  it("words 'likely' as likely and never as a fact", () => {
    expect(PRESENCE_LABEL.likely_asleep).toContain("büyük olasılıkla");
    expect(PRESENCE_LABEL.unknown).toContain("bilinmiyor");
  });

  it("every presence state is recognised as probabilistic", () => {
    for (const state of [
      "owner.present",
      "owner.away",
      "owner.returned",
      "owner.resting",
      "owner.likely_asleep",
      "owner.awake",
    ]) {
      expect(isPresenceState(state)).toBe(true);
    }
    expect(isPresenceState("agent.idle")).toBe(false);
  });
});

describe("an old observation is not evidence about now", () => {
  it("expires to unknown, never to 'still present'", () => {
    const truth = truthOf(OWNER_PRESENT());
    const later = T0 + OBSERVATION_TTL_MS + 1;

    const view = presenceView(presenceClaim(truth, later));
    expect(view.kind).toBe("unknown");
    expect(view.expired).toBe(true);
    // What it *was* is still readable, so the readout can say so honestly.
    expect(view.lastKnown).toBe("present");
  });

  it("is still current inside its lifetime", () => {
    const truth = truthOf(OWNER_PRESENT());
    expect(presenceView(presenceClaim(truth, T0 + OBSERVATION_TTL_MS - 1)).kind).toBe("present");
  });

  it("prefers the publisher's own ttl_s over the client's default", () => {
    // The presence engine owns the staleness policy; the client's five minutes
    // is only what it falls back to. This fixture declares half an hour.
    const asleep = OWNER_LIKELY_ASLEEP();
    expect(stateTtlMs("owner.likely_asleep", asleep)).toBe(1_800_000);
    expect(stateTtlMs("owner.likely_asleep")).toBe(OBSERVATION_TTL_MS);

    const truth = truthOf(asleep);
    expect(presenceView(presenceClaim(truth, T0 + OBSERVATION_TTL_MS + 1)).kind).toBe(
      "likely_asleep",
    );
    expect(presenceView(presenceClaim(truth, T0 + 1_800_001)).kind).toBe("unknown");
  });
});

describe("the release channel shows, and only shows", () => {
  it("reads a deployment in flight from the published stage", () => {
    const view = releaseView(releaseClaim(truthOf(RELEASE_DEPLOYING()), T0));
    expect(view.stage).toBe("deploying");
    expect(view.inFlight).toBe(true);
    expect(view.awaitingOwner).toBe(false);
    expect(view.moduleId).toBe("opp-1");
    expect(view.riskTier).toBe(2);
    expect(view.progress).toBe(0.4);
  });

  it("distinguishes waiting on the owner from acting", () => {
    const view = releaseView(releaseClaim(truthOf(RELEASE_APPROVAL_REQUIRED()), T0));
    expect(view.stage).toBe("owner_approval_required");
    expect(view.awaitingOwner).toBe(true);
    expect(view.inFlight).toBe(false);
  });

  it("never infers a risk tier the publisher did not declare", () => {
    const view = releaseView(
      releaseClaim(truthOf(event({ state: "release.deploying", subsystem: "deployment" })), T0),
    );
    expect(view.riskTier).toBeNull();
  });

  it("gives a release stage minutes, not seconds, before it expires", () => {
    expect(stateTtlMs("release.deploying")).toBe(OPERATION_TTL_MS);
    const truth = truthOf(RELEASE_DEPLOYING());
    expect(releaseView(releaseClaim(truth, T0 + 60_000)).stage).toBe("deploying");
    expect(releaseView(releaseClaim(truth, T0 + OPERATION_TTL_MS + 1)).stage).toBe("none");
  });

  it("keeps a stage that waits on the owner for as long as it waits", () => {
    const truth = truthOf(RELEASE_APPROVAL_REQUIRED());
    const view = releaseView(releaseClaim(truth, T0 + 86_400_000));
    expect(view.stage).toBe("owner_approval_required");
    expect(view.expired).toBe(false);
  });

  it("carries routines and alarms on the same channel", () => {
    expect(releaseView(releaseClaim(truthOf(ROUTINE_ARMED()), T0)).stage).toBe("routine_armed");
    expect(releaseView(releaseClaim(truthOf(ALARM_TRIGGERED()), T0)).stage).toBe(
      "alarm_triggered",
    );
  });

  it("a deployment in flight does not become the core's own activity", () => {
    resetSequence();
    const idle = event({ state: "agent.idle", subsystem: "system" });
    const truth = applyResponse(emptyTruth(), response([idle, RELEASE_DEPLOYING()]), T0);
    expect(visualFor(truth, T0).kind).toBe("idle");
    expect(releaseView(releaseClaim(truth, T0)).inFlight).toBe(true);
  });
});
