/**
 * What the ambient band actually puts on the screen.
 *
 * The camera cell is a privacy indicator, so its markup is asserted as strictly
 * as the geometry elsewhere: "nobody told us" must never render as the sentence
 * an owner would read as "you are not being watched".
 *
 * Rendered with `react-dom/server` in Node, like the rest of this suite — no
 * browser, no Playwright.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AmbientBand from "../../app/core/AmbientBand";
import { eyeView, presenceView, releaseView } from "../../app/lib/uistate/ambient";
import { OBSERVATION_TTL_MS } from "../../app/lib/uistate/contract";
import {
  applyResponse,
  emptyTruth,
  eyeClaim,
  presenceClaim,
  releaseClaim,
} from "../../app/lib/uistate/truth";
import {
  EYE_ACTIVE,
  EYE_DISABLED,
  OWNER_LIKELY_ASLEEP,
  OWNER_PRESENT,
  OWNER_PRESENT_NO_CONFIDENCE,
  RELEASE_APPROVAL_REQUIRED,
  RELEASE_DEPLOYING,
  T0,
  event,
  resetSequence,
  response,
} from "./fixtures";

function band(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  const truth = applyResponse(emptyTruth(), response(events), T0);
  return renderToStaticMarkup(
    <AmbientBand
      eye={eyeView(eyeClaim(truth, at))}
      presence={presenceView(presenceClaim(truth, at))}
      release={releaseView(releaseClaim(truth, at))}
    />,
  );
}

describe("the camera indicator", () => {
  it("is unmistakable when perception is running", () => {
    const html = band([EYE_ACTIVE()]);
    expect(html).toContain('data-eye-status="active"');
    expect(html).toContain("Göz açık");
    expect(html).toContain("Görüntü buluta gönderilmiyor ve kaydedilmiyor.");
    expect(html).toContain("cam-0");
  });

  it("never renders 'off' when nothing has been published", () => {
    const html = band([]);
    expect(html).toContain('data-eye-status="untold"');
    expect(html).toContain("Kamera durumu bildirilmedi");
    // The apostrophe is HTML-escaped in the rendered markup.
    expect(html).toContain("demek değil.");
    expect(html).toContain("Kameranın açık mı kapalı mı olduğu bildirilmedi.");
    expect(html).not.toContain('data-eye-status="disabled"');
  });

  it("reports a disabled eye as disabled", () => {
    const html = band([EYE_DISABLED()]);
    expect(html).toContain('data-eye-status="disabled"');
    expect(html).toContain("Algı durduruldu.");
  });

  it("flags an eye state it cannot read instead of guessing", () => {
    const html = band([event({ state: "eye.recalibrating" })]);
    expect(html).toContain('data-eye-unknown="yes"');
  });
});

describe("presence on screen", () => {
  it("states the confidence the engine reported", () => {
    const html = band([OWNER_LIKELY_ASLEEP(0.86)]);
    expect(html).toContain("Sahip büyük olasılıkla uyuyor");
    expect(html).toContain("güven %86");
    expect(html).toContain("4 sinyal");
  });

  it("says the confidence is missing rather than showing a number", () => {
    const html = band([OWNER_PRESENT_NO_CONFIDENCE()]);
    expect(html).toContain("güven bildirilmedi");
    expect(html).not.toContain("güven %");
  });

  it("tells 'never observed' apart from 'the observation aged out'", () => {
    expect(band([])).toContain("Henüz bir varlık gözlemi bildirilmedi.");
  });

  it("a decayed observation is drawn as unknown, with what it was", () => {
    const html = band([OWNER_PRESENT()], T0 + OBSERVATION_TTL_MS + 1);
    expect(html).toContain('data-presence="unknown"');
    expect(html).toContain("Sahip durumu bilinmiyor");
    expect(html).toContain("Son bilinen: Sahip burada");
    expect(html).toContain("artık şu an için kanıt sayılmıyor");
  });

  it("says perception is not authentication, on every render", () => {
    for (const events of [[], [OWNER_PRESENT()], [EYE_ACTIVE()]]) {
      expect(band(events)).toContain("Algı kimlik doğrulaması değildir.");
    }
  });
});

describe("the release strip shows and never acts", () => {
  it("names the stage, the candidate and the declared risk tier", () => {
    const html = band([RELEASE_DEPLOYING()]);
    expect(html).toContain('data-release-stage="deploying"');
    expect(html).toContain("Kuruluyor");
    expect(html).toContain("opp-1");
    expect(html).toContain("Risk kademesi: 2");
    expect(html).toContain("İlerleme: %40");
  });

  it("marks what is waiting on the owner", () => {
    const html = band([RELEASE_APPROVAL_REQUIRED()]);
    expect(html).toContain('data-release-stage="owner_approval_required"');
    expect(html).toContain("Sahip onayı bekleniyor");
  });

  it("says where release authority lives, and offers no control", () => {
    const html = band([RELEASE_DEPLOYING()]);
    expect(html).toContain("Üretime alma yetkisi doğrulanmış sahip oturumundadır.");
    // The Core is a pure consumer: no button, no form, no write path anywhere.
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<form");
    expect(html).not.toContain("<input");
  });

  it("says nothing is being released when nothing was published", () => {
    const html = band([]);
    expect(html).toContain('data-release-stage="none"');
    expect(html).toContain("Şu anda izlenen bir yayın adımı bildirilmedi.");
  });
});
