/**
 * B22 req 705/706/707/708/709/710/711: six answers a panel can give, in six sets of words.
 *
 * `Panel` has distinguished loading / failed / absent / empty since the cockpit was built,
 * and the reasoning in its own header is the reason this batch could be small: collapsing
 * "failed" into "empty" is a confident statement about a world nobody checked. What it
 * could not distinguish is WHY a request failed, because the client threw the body away and
 * kept `HTTP 500`.
 *
 * The four new outcomes are four different waits:
 *
 *   retry            — something outside did not answer; pressing again can work
 *   provider_blocked — the deployment has no key for this; pressing again cannot
 *   permission       — the system is not allowed; only the owner can allow it
 *   owner_action     — nothing is broken; a decision is outstanding, and it is theirs
 *
 * A retry button on a missing API key is an invitation to press it forever, which is why
 * the control is bound to `retryable` rather than to "this is a failure".
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import Panel, { LoadedNotice } from "../../app/core/panels/Panel";
import type { Loaded } from "../../app/lib/cockpit/api";
import {
  FAILURE_HINT,
  FAILURE_LABEL,
  type Failure,
  classifyFailure,
  failureFromBody,
  looksLikeDeveloperText,
} from "../../app/lib/errors/failure";

function failed(errorClass: string, message: string, status = 503): Loaded<string[]> {
  return {
    kind: "failed",
    error: `HTTP ${status}`,
    failure: classifyFailure(errorClass, message, status),
  };
}

function render(state: Loaded<string[]>, onRetry?: () => void, always = false): string {
  return renderToStaticMarkup(
    <Panel
      id="probe"
      title="Deneme"
      state={state}
      empty="hiç yok"
      isEmpty={(rows) => rows.length === 0}
      onRetry={onRetry}
      always={always}
    >
      {(rows) => <ul>{rows.map((r) => <li key={r}>{r}</li>)}</ul>}
    </Panel>,
  );
}

// ------------------------------------------------------------ the classification


describe("what kind of failure this is", () => {
  it("names a provider that is not configured, and does not offer a retry", () => {
    const f = classifyFailure("provider_auth_missing", "Sağlayıcının anahtarı tanımlı değil.", 503);
    expect(f.kind).toBe("provider_blocked");
    expect(f.retryable).toBe(false);
  });

  it("names a permission, and does not offer a retry", () => {
    expect(classifyFailure("permission_denied", "Yetkim yok.", 403)).toMatchObject({
      kind: "permission",
      retryable: false,
    });
    expect(classifyFailure("out_of_scope", "Kapsam dışı.", 403).kind).toBe("permission");
  });

  it("names a decision that is the owner's, and does not offer a retry", () => {
    expect(classifyFailure("product_change_required", "Ürün kararı gerekiyor.", 409)).toMatchObject(
      { kind: "owner_action", retryable: false },
    );
  });

  it("offers a retry for the things that can clear on their own", () => {
    for (const cls of ["timeout", "dependency_unavailable", "rate_limited", "fetch_failed"]) {
      const f = classifyFailure(cls, "…", 503);
      expect(f.kind, cls).toBe("retry");
      expect(f.retryable, cls).toBe(true);
    }
  });

  it("leaves an unclassified failure plain, and still retryable", () => {
    // The owner has nothing else to try, and a generic failure makes no promise about
    // what would help — so it keeps the button and none of the four headings.
    const f = classifyFailure("", "", 500);
    expect(f.kind).toBe("failed");
    expect(f.retryable).toBe(true);
    expect(f.message).toBe("Cloud Core bu isteği tamamlayamadı.");
  });

  it("says it could not reach Cloud Core at all when there was no status", () => {
    expect(classifyFailure("", "", 0).message).toBe("Cloud Core'a ulaşamadım.");
  });
});

describe("reading the server's body", () => {
  it("understands the shape the API sends", () => {
    const f = failureFromBody(
      { error_class: "provider_auth_missing", message: "Sağlayıcının anahtarı tanımlı değil." },
      503,
    );
    expect(f.kind).toBe("provider_blocked");
    expect(f.message).toBe("Sağlayıcının anahtarı tanımlı değil.");
  });

  it("understands FastAPI's detail wrapper", () => {
    const f = failureFromBody({ detail: { error_class: "timeout", message: "Bitmedi." } }, 504);
    expect(f.kind).toBe("retry");
    expect(f.message).toBe("Bitmedi.");
  });

  it("drops a body that carries a Python failure (req 705, on this side too)", () => {
    // An old Cloud Core, a proxy's error page, a route nobody converted yet.
    const f = failureFromBody({ detail: "ValueError: no matching weekday within a week" }, 422);
    expect(f.message).toBe("İstek kabul edilmedi (HTTP 422).");
    expect(f.message).not.toContain("ValueError");
    expect(looksLikeDeveloperText("Traceback (most recent call last):")).toBe(true);
    expect(looksLikeDeveloperText("Geçmiş bir zaman söyledin efendim.")).toBe(false);
  });

  it("passes a plain Turkish detail through", () => {
    expect(failureFromBody({ detail: "Alarmı kuramadım." }, 422).message).toBe("Alarmı kuramadım.");
  });
});

// ------------------------------------------------------------------- the rendering

describe("what the owner sees", () => {
  it("loading and empty keep their own words (req 706/707), and empty takes no slot (req 714)", () => {
    // Loading is never quiet: a question still in flight is not a question answered
    // "nothing", and hiding the panel for the first 200 ms would flash the page away.
    expect(render({ kind: "loading" })).toContain("yükleniyor…");

    // B24: on the cockpit an empty family draws nothing at all...
    expect(render({ kind: "ok", value: [], at: 0 })).toBe("");
    // ...and its sentence is unchanged wherever the emptiness is the answer.
    const emptyHtml = render({ kind: "ok", value: [], at: 0 }, undefined, true);
    expect(emptyHtml).toContain("hiç yok");
    expect(emptyHtml).toContain('data-panel-empty="yes"');

    const loadedHtml = render({ kind: "ok", value: ["bir"], at: 0 });
    expect(loadedHtml).toContain("bir");
    expect(loadedHtml).toContain('data-panel-empty="no"');
  });

  it("a provider block says so and offers no button (req 709)", () => {
    const html = render(
      failed("provider_auth_missing", "Sağlayıcının anahtarı tanımlı değil."),
      () => {},
    );
    expect(html).toContain(FAILURE_LABEL.provider_blocked);
    expect(html).toContain("Sağlayıcının anahtarı tanımlı değil.");
    expect(html).toContain(FAILURE_HINT.provider_blocked);
    expect(html).not.toContain("data-panel-retry");
    expect(html).toContain('data-panel-failure="provider_blocked"');
  });

  it("a permission block says who can grant it (req 710)", () => {
    const html = render(failed("permission_denied", "Bu işlem için yetkim yok."), () => {});
    expect(html).toContain(FAILURE_LABEL.permission);
    expect(html).toContain("yalnızca sen");
    expect(html).not.toContain("data-panel-retry");
  });

  it("a decision that is the owner's is not called an error (req 711)", () => {
    const html = render(failed("product_change_required", "Ürün kararı gerekiyor."), () => {});
    expect(html).toContain(FAILURE_LABEL.owner_action);
    expect(html).not.toContain("Alınamadı");
    expect(html).not.toContain("data-panel-retry");
  });

  it("a retryable failure offers the button, and only when a retry was supplied (req 708)", () => {
    const withRetry = render(failed("timeout", "İşlem verilen sürede bitmedi."), () => {});
    expect(withRetry).toContain("Tekrar dene");
    expect(withRetry).toContain('data-panel-retry');

    const withoutHandler = render(failed("timeout", "İşlem verilen sürede bitmedi."));
    // The BUTTON, by its marker - not the label text. "Tekrar denenebilir." (the hint) is a
    // sentence that contains "Tekrar dene", and asserting on the words rather than on the
    // control is how a test passes while the control is missing (or fails while it is
    // absent, as this one did).
    expect(withoutHandler).not.toContain("data-panel-retry");
    expect(withoutHandler).toContain("Tekrar denenebilir.");
  });

  it("a panel built without a failure keeps the old line", () => {
    // Every panel test written before this batch constructs `{kind: "failed", error}` by
    // hand; none of them should have to change to keep passing.
    const html = render({ kind: "failed", error: "HTTP 503" });
    expect(html).toContain("Alınamadı: HTTP 503");
  });

  it("the notice renders the same words outside a panel", () => {
    const html = renderToStaticMarkup(
      <LoadedNotice state={failed("rate_limited", "Çok sık denendi.")} onRetry={() => {}} />,
    );
    expect(html).toContain(FAILURE_LABEL.retry);
    expect(html).toContain("Tekrar dene");
  });
});

describe("the four kinds are four", () => {
  it("no class lands in two buckets, and every bucket has its own words", () => {
    const kinds = new Set<Failure["kind"]>();
    for (const cls of [
      "timeout",
      "provider_auth_missing",
      "permission_denied",
      "product_change_required",
      "internal_bug",
    ]) {
      kinds.add(classifyFailure(cls, "…", 500).kind);
    }
    expect(kinds.size).toBe(5);
    expect(new Set(Object.values(FAILURE_LABEL)).size).toBe(5);
  });
});
