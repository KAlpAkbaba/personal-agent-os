/**
 * ADR-0045: the client honours the realtime-session contract version of the
 * server it is talking to, and a 422 is shown field by field.
 *
 * The defect these pin: the owner's first real qualification clicked Connect
 * and saw only "Oturum oluşturulamadı: HTTP 422" — the page (v2) sent `voice`
 * to a deployed v1 Cloud Core (`extra="forbid"`).
 */

import { describe, expect, it } from "vitest";

import {
  type Fetcher,
  VoiceApiError,
  VoiceSessionApi,
  describeErrorDetail,
  isLogForbiddenKey,
  sanitizeForLog,
} from "../../app/lib/voice/api";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import {
  FakeCloudCore,
  type FakeCloudCoreOptions,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";
import {
  BUNDLED_CONTRACT,
  BUNDLED_CONTRACT_VERSION,
  type ContractDocument,
  checkValue,
  createSessionFields,
  createSessionSchema,
  droppedFieldsNotice,
  resolveContract,
  validateCreateBody,
} from "../../app/lib/voice/session-contract";

const tick = async (rounds = 4): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

/** What the page builds on Connect with a selected A/B voice (ADR-0043). */
const CONNECT_OPTIONS = { language: "tr-TR", voice: "marin" };

function rig(options: FakeCloudCoreOptions = {}, wrap?: (fetcher: Fetcher) => Fetcher) {
  const log: string[] = [];
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc", ...options });
  const api = new VoiceSessionApi(wrap ? wrap(core.fetcher) : core.fetcher);
  const controller = new VoiceSessionController({
    api,
    transportFactory: () => new FakeTransport({ log: (op) => log.push(op), now: scheduler.now }),
    playback: new FakePlayback(scheduler.now, (op) => log.push(op)),
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    log: (op) => log.push(op),
  });
  return { controller, core, api, log, scheduler };
}

const createBodies = (core: FakeCloudCore) =>
  core.requests.filter((r) => r.method === "POST" && r.path === "/v1/voice/realtime/sessions").map((r) => r.body);

describe("bundled contract document", () => {
  it("is v3 (ADR-0198: the gesture event) with voice on create; v1 is the frozen legacy list without it", () => {
    expect(BUNDLED_CONTRACT_VERSION).toBe(3);
    expect(BUNDLED_CONTRACT.kind).toBe("pagentos.realtime_session_contract");
    expect(createSessionFields(3)).toEqual(["client_kind", "language", "narration_session_id", "session_ttl_s", "transport", "voice"]);
    expect(createSessionFields(1)).toEqual(["client_kind", "transport", "language", "narration_session_id", "session_ttl_s"]);
    expect(createSessionSchema(1).properties).not.toHaveProperty("voice");
    expect(createSessionSchema(1).properties.client_kind).toEqual(createSessionSchema(3).properties.client_kind);
  });
});

describe("validateCreateBody", () => {
  it("passes the real create payload clean at v3, drops exactly `voice` at v1 and sends the rest unchanged", () => {
    const real = { client_kind: "web", language: "tr-TR", voice: "marin" };
    const v3 = validateCreateBody(real, 3);
    expect(v3).toEqual({ body: real, dropped: [], problems: [] });
    const v1 = validateCreateBody(real, 1);
    expect(v1.dropped).toEqual(["voice"]);
    expect(v1.problems).toEqual([]);
    expect(v1.body).toEqual({ client_kind: "web", language: "tr-TR" });
  });

  it("reports pattern, null-ability, integer bounds and uuid problems by field and reason only", () => {
    const bad = validateCreateBody(
      { client_kind: "Web Shell", language: "turkish", voice: "Marin!", session_ttl_s: 30, narration_session_id: "nope", transport: null },
      2,
    );
    expect(bad.dropped).toEqual([]);
    expect(bad.problems).toEqual([
      { field: "client_kind", reason: "pattern:^[a-z][a-z0-9_]{0,15}$ | type:null" },
      { field: "language", reason: "pattern:^[a-z]{2}-[A-Z]{2}$" },
      { field: "voice", reason: "pattern:^[a-z]{2,16}$ | type:null" },
      { field: "session_ttl_s", reason: "minimum:60 | type:null" },
      { field: "narration_session_id", reason: "format:uuid | type:null" },
    ]);
    for (const problem of bad.problems) {
      expect(problem.reason).not.toContain("Marin");
      expect(problem.reason).not.toContain("turkish");
    }
    const ok = validateCreateBody(
      { voice: null, session_ttl_s: 3600, narration_session_id: "11111111-2222-4333-8444-555555555555", transport: "webrtc" },
      2,
    );
    expect(ok.problems).toEqual([]);
  });

  it("supports enum and required from the schema subset it claims", () => {
    expect(checkValue("b", { enum: ["a", "b"] })).toBeNull();
    expect(checkValue("c", { enum: ["a", "b"] })).toBe("enum:a,b");
    expect(checkValue(1.5, { type: "integer" })).toBe("type:integer");
    expect(checkValue(90000, { type: "integer", maximum: 86400 })).toBe("maximum:86400");
    const required = validateCreateBody({}, 2, { properties: { name: { type: "string" } }, required: ["name"] });
    expect(required.problems).toEqual([{ field: "name", reason: "required" }]);
  });
});

describe("contract probe outcomes", () => {
  it("404 puts the client in v1 mode: `voice` dropped, the Turkish notice shown, the session still created", async () => {
    const t = rig({ contract: "legacy", legacyCreate: true });
    await t.controller.connect(CONNECT_OPTIONS);
    await tick();
    const snapshot = t.controller.getSnapshot();
    expect(snapshot.state).toBe("listening");
    expect(snapshot.contract).toEqual({
      version: 1,
      source: "legacy",
      known: true,
      createFields: ["client_kind", "transport", "language", "narration_session_id", "session_ttl_s"],
    });
    expect(createBodies(t.core)).toEqual([{ client_kind: "web", language: "tr-TR" }]);
    expect(snapshot.contractNotice).toBe(
      "Sunucu sözleşmesi v1: 'voice' alanı bu sürümde yok; varsayılan ses kullanılacak (marin/cedar seçimi için Cloud Core güncellenmeli)",
    );
    expect(snapshot.voice).toBeNull(); // the server picked its default
    expect(snapshot.lastError).toBeNull();
    expect(t.log).toContain("contract.legacy:v1");
    expect(t.log).toContain("contract.dropped:voice");
  });

  it("200 with the current document keeps v3: the real payload goes out with `voice`, nothing dropped", async () => {
    const t = rig({ contract: "served" });
    await t.controller.connect(CONNECT_OPTIONS);
    await tick();
    const snapshot = t.controller.getSnapshot();
    expect(snapshot.state).toBe("listening");
    expect(snapshot.contract).toMatchObject({ version: 3, source: "server", known: true });
    expect(snapshot.contract?.createFields).toContain("voice");
    expect(createBodies(t.core)).toEqual([{ client_kind: "web", language: "tr-TR", voice: "marin" }]);
    expect(snapshot.contractNotice).toBeNull();
    expect(snapshot.voice).toBe("marin");
  });

  it("200 with a newer property list is honoured over the bundled document", async () => {
    const newer: ContractDocument = {
      ...BUNDLED_CONTRACT,
      contract_version: 3,
      requests: {
        ...BUNDLED_CONTRACT.requests,
        create_session: {
          ...BUNDLED_CONTRACT.requests.create_session,
          properties: {
            ...BUNDLED_CONTRACT.requests.create_session.properties,
            persona: { anyOf: [{ type: "string", pattern: "^[a-z]{2,16}$" }, { type: "null" }], default: null },
          },
        },
      },
    };
    const t = rig({ contract: newer });
    const resolved = await t.controller.probeContract();
    expect(resolved).toMatchObject({ version: 3, source: "server", known: true });
    expect(resolved?.createFields).toEqual([...createSessionFields(3), "persona"]);
    // the served schema, not the bundled one, decides what is accepted
    const checked = validateCreateBody({ client_kind: "web", persona: "calm" }, 3, resolved?.createSession);
    expect(checked).toEqual({ body: { client_kind: "web", persona: "calm" }, dropped: [], problems: [] });
    expect(validateCreateBody({ persona: "calm" }, 3).dropped).toEqual(["persona"]);
    await t.controller.connect(CONNECT_OPTIONS);
    await tick();
    expect(t.controller.getSnapshot().contract?.version).toBe(3);
    // cached per page load: one probe for probeContract() + connect()
    expect(t.core.requests.filter((r) => r.path === "/v1/voice/realtime/contract")).toHaveLength(1);
  });

  it("401 is not v1: no session is attempted and the sign-in state is surfaced", async () => {
    const t = rig({ contract: "unauthorized" });
    await t.controller.connect(CONNECT_OPTIONS);
    await tick();
    const snapshot = t.controller.getSnapshot();
    expect(snapshot.state).toBe("error");
    expect(snapshot.contract).toEqual({ version: null, source: "unauthorized", known: false, createFields: [] });
    expect(snapshot.lastError).toBe("Oturum açık değil: Cloud Core kimlik doğrulaması gerekiyor; yeniden giriş yapın.");
    expect(createBodies(t.core)).toEqual([]);
    expect(t.log).toContain("contract.unauthorized");
  });

  it("apiFetch's thrown UnauthorizedError (name-matched) is the same 401 outcome", async () => {
    const t = rig({}, () => async (path) => {
      const error = new Error(`unauthorized: ${path}`);
      error.name = "UnauthorizedError";
      throw error;
    });
    await t.controller.connect(CONNECT_OPTIONS);
    expect(t.controller.getSnapshot().contract?.source).toBe("unauthorized");
    expect(t.controller.getSnapshot().state).toBe("error");
  });

  it("a network failure on the probe never blocks Connect: bundled v3 assumed and SHOWN as unknown", async () => {
    let probes = 0;
    const t = rig({}, (fetcher) => async (path, init) => {
      if (path === "/v1/voice/realtime/contract") {
        probes += 1;
        throw new TypeError("Failed to fetch");
      }
      return fetcher(path, init);
    });
    await t.controller.connect(CONNECT_OPTIONS);
    await tick();
    const snapshot = t.controller.getSnapshot();
    expect(snapshot.state).toBe("listening");
    expect(snapshot.contract).toMatchObject({ version: 3, source: "bundled", known: false });
    expect(snapshot.contractNotice).toContain("Sunucu sözleşme sürümü bilinmiyor (Failed to fetch)");
    expect(snapshot.contractNotice).toContain("v3 varsayıldı");
    expect(createBodies(t.core)).toEqual([{ client_kind: "web", language: "tr-TR", voice: "marin" }]);
    // an unknown version is not cached: the next Connect asks again
    await t.controller.disconnect();
    await t.controller.connect(CONNECT_OPTIONS);
    expect(probes).toBe(2);
  });

  it("resolveContract maps every outcome", () => {
    expect(resolveContract({ outcome: "unauthorized" })).toBeNull();
    expect(resolveContract({ outcome: "legacy" })).toMatchObject({ version: 1, source: "legacy", known: true });
    expect(resolveContract({ outcome: "unknown", reason: "x" })).toMatchObject({ version: 3, source: "bundled", known: false });
    expect(resolveContract({ outcome: "served", version: 3, document: BUNDLED_CONTRACT })).toMatchObject({ version: 3, source: "server" });
  });
});

describe("structured 422 display", () => {
  it("renders FastAPI's extra_forbidden item as field · reason · message, never the input", () => {
    const lines = describeErrorDetail(FakeCloudCore.extraForbidden("voice"));
    expect(lines).toEqual(["alan: body.voice · neden: extra_forbidden · Extra inputs are not permitted"]);
    expect(lines.join("\n")).not.toContain("REDACTED-BY-TEST");
    expect(lines.join("\n")).not.toContain("pydantic.dev");
  });

  it("renders the API's VoiceError shape and a plain string detail", () => {
    expect(
      describeErrorDetail({
        detail: { error_class: "validation_error", message: "provider 'x' does not offer transport 'y'", transports: ["webrtc"] },
      }),
    ).toEqual(["neden: validation_error · provider 'x' does not offer transport 'y' · ayrıntı alanları: transports"]);
    expect(describeErrorDetail({ detail: { error_class: "capability_missing", message: "no voice", details: { voice: "zz" } } })).toEqual([
      "neden: capability_missing · no voice · ayrıntı alanları: voice",
    ]);
    expect(describeErrorDetail({ detail: "forced 503" })).toEqual(["neden: forced 503"]);
    expect(describeErrorDetail(null)).toEqual([]);
    expect(new VoiceApiError(422, "/x", FakeCloudCore.extraForbidden("voice")).lines).toHaveLength(1);
  });

  it("the owner's real failure now reads as the field and the reason, in the error area and the request log", async () => {
    // The client honours the served version; here the deployed server lies about it
    // (says v2, behaves v1) so the 422 path itself is exercised end to end.
    const t = rig({ contract: "served" });
    t.core.failNext("/sessions", 422, FakeCloudCore.extraForbidden("voice"));
    await t.controller.connect(CONNECT_OPTIONS);
    await tick();
    const snapshot = t.controller.getSnapshot();
    expect(snapshot.state).toBe("error");
    expect(snapshot.lastError).toBe("Oturum oluşturulamadı: HTTP 422");
    expect(snapshot.lastErrorLines).toEqual(["alan: body.voice · neden: extra_forbidden · Extra inputs are not permitted"]);
    const create = snapshot.requestLog.find((e) => e.method === "POST" && e.path === "/v1/voice/realtime/sessions");
    expect(create).toMatchObject({
      status: 422,
      ok: false,
      body: { client_kind: "web", language: "tr-TR", voice: "marin" },
      detail: ["alan: body.voice · neden: extra_forbidden · Extra inputs are not permitted"],
    });
  });

  it("a value that breaks the honoured schema is refused before anything is sent", async () => {
    const t = rig({ contract: "served" });
    await t.controller.connect({ language: "tr-TR", voice: "Marin!" });
    const snapshot = t.controller.getSnapshot();
    expect(snapshot.state).toBe("error");
    expect(snapshot.lastError).toBe("Oturum isteği sözleşmeye (v3) uymuyor");
    expect(snapshot.lastErrorLines).toEqual(["alan: voice · neden: pattern:^[a-z]{2,16}$ | type:null"]);
    expect(createBodies(t.core)).toEqual([]);
  });
});

describe("scrubbed request log", () => {
  it("never carries an Authorization header, a bearer token or the ephemeral credential", async () => {
    const t = rig({ contract: "served" }, (fetcher) => async (path, init = {}) => {
      // what apiFetch does: attach the owner bearer to every call
      const headers = new Headers(init.headers);
      headers.set("Authorization", "Bearer owner-session-token-SECRET");
      return fetcher(path, { ...init, headers });
    });
    await t.controller.connect(CONNECT_OPTIONS);
    await tick();
    t.core.failNext("/events", 422, { detail: "forbidden payload key" });
    await t.controller.flushEvents();
    await t.controller.disconnect();
    const entries = t.controller.getSnapshot().requestLog;
    expect(entries.length).toBeGreaterThanOrEqual(4);
    expect(entries.map((e) => `${e.method} ${e.path}`)).toContain("GET /v1/voice/realtime/contract");
    const text = JSON.stringify(entries);
    expect(text).not.toMatch(/authorization/i);
    expect(text).not.toContain("owner-session-token");
    expect(text).not.toContain("SECRET");
    expect(text).not.toMatch(/ephemeral-/);
    expect(text).not.toMatch(/"secret"/);
    for (const entry of entries) {
      expect(entry).not.toHaveProperty("headers");
      expect(Object.keys(entry).toSorted()).toEqual(["body", "detail", "error", "method", "ok", "path", "seq", "status"]);
    }
    expect(entries.every((e) => typeof e.status === "number")).toBe(true);
  });

  it("keeps at most the last 20 requests", async () => {
    const t = rig({ contract: "served" });
    await t.controller.connect(CONNECT_OPTIONS);
    for (let i = 0; i < 30; i += 1) {
      await t.api.state("11111111-2222-4333-8444-555555555555");
    }
    const entries = t.controller.getSnapshot().requestLog;
    expect(entries).toHaveLength(20);
    expect(entries[entries.length - 1].seq).toBe(32); // probe + create + 30 state reads
  });

  it("sanitizeForLog removes credential-shaped keys under any spelling, recursively", () => {
    expect(
      sanitizeForLog({
        client_kind: "web",
        Authorization: "Bearer x",
        "x-api-token": "t",
        ownerCredential: "c",
        nested: { secret: "s", keep: 1, list: [{ accessToken: "a", ok: true }] },
        bytes: new Uint8Array(2),
      }),
    ).toEqual({ client_kind: "web", nested: { keep: 1, list: [{ ok: true }] }, bytes: "[bytes]" });
    for (const key of ["authorization", "AUTHORIZATION", "owner_credential", "client-secret", "id_token", "apiKey", "password"]) {
      expect(isLogForbiddenKey(key)).toBe(true);
    }
    for (const key of ["client_kind", "language", "voice", "session_ttl_s", "events"]) {
      expect(isLogForbiddenKey(key)).toBe(false);
    }
  });
});

describe("owner wording", () => {
  it("names the dropped field and the fix in Turkish", () => {
    expect(droppedFieldsNotice(1, [])).toBeNull();
    expect(droppedFieldsNotice(1, ["voice", "persona"])).toBe(
      "Sunucu sözleşmesi v1: 'voice' alanı bu sürümde yok; varsayılan ses kullanılacak (marin/cedar seçimi için Cloud Core güncellenmeli) · 'persona' alanı bu sürümde yok; gönderilmedi",
    );
  });
});
