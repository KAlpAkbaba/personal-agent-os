/**
 * The versioned realtime-session wire contract, as the browser client uses it
 * (ADR-0045). One source: `packages/protocol/realtime-session-contract.json`,
 * generated from the API's Pydantic request models
 * (services/api/scripts/export_realtime_contract.py; explained in
 * services/api/app/voice/realtime_sessions/contract.py).
 *
 * Why: the first real owner qualification clicked Connect and got only
 * "HTTP 422". The page (contract v2) sent `voice`; the deployed Cloud Core
 * (contract v1, `extra="forbid"`) refused it as `extra_forbidden` on
 * `body.voice`. The server is right to refuse; the client is what has to
 * (a) know which version it is talking to, (b) send only what that version
 * accepts, and (c) say so in the owner's language when something was left out.
 *
 * Deliberately NOT a JSON-Schema library: it supports exactly what this
 * schema uses — `type`, `pattern`, `anyOf` (a typed branch + `null`), `enum`,
 * integer bounds, `format: uuid` and string length bounds — nothing else.
 */

import bundled from "../../../../../packages/protocol/realtime-session-contract.json";

/** The subset of JSON Schema a request-body field can carry here. */
export type FieldSchema = {
  type?: string;
  pattern?: string;
  format?: string;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  anyOf?: FieldSchema[];
  default?: unknown;
  title?: string;
};

/** One request body (`requests.<name>` in the document). */
export type RequestSchema = {
  properties: Record<string, FieldSchema>;
  additionalProperties?: boolean;
  required?: string[];
  title?: string;
};

export type ContractDocument = {
  contract_version: number;
  kind?: string;
  requests: Record<string, RequestSchema>;
  legacy?: Record<string, { create_session?: string[] }>;
};

export const BUNDLED_CONTRACT: ContractDocument = bundled as unknown as ContractDocument;
export const BUNDLED_CONTRACT_VERSION: number = BUNDLED_CONTRACT.contract_version;
/** Where the server serves its own version of the document (404 on a v1 server). */
export const CONTRACT_PATH = "/v1/voice/realtime/contract";

const LEGACY_V1_CREATE_FIELDS: readonly string[] = BUNDLED_CONTRACT.legacy?.["1"]?.create_session ?? [];

/**
 * The `create_session` fields a contract version accepts. v1 is the frozen
 * legacy list in the document; anything else is the properties of `doc`
 * (default: the bundled document).
 */
export function createSessionFields(version: number, doc: ContractDocument = BUNDLED_CONTRACT): string[] {
  if (version === 1) return [...LEGACY_V1_CREATE_FIELDS];
  return Object.keys(doc.requests.create_session?.properties ?? {});
}

/**
 * The `create_session` schema for a version. A v1 server validated the same
 * fields with the same rules (the fields never changed, only `voice` was
 * added), so v1 is the current schema restricted to the legacy field list.
 */
export function createSessionSchema(version: number, doc: ContractDocument = BUNDLED_CONTRACT): RequestSchema {
  const current = doc.requests.create_session ?? { properties: {} };
  if (version !== 1) return current;
  const properties: Record<string, FieldSchema> = {};
  for (const field of LEGACY_V1_CREATE_FIELDS) {
    properties[field] = current.properties[field] ?? {};
  }
  return { properties, additionalProperties: false, required: current.required?.filter((f) => f in properties) };
}

export type FieldProblem = { field: string; reason: string };

export type CreateBodyValidation = {
  /** Exactly the fields the target version accepts, values untouched. */
  body: Record<string, unknown>;
  /** Fields the target version does not accept (not sent). */
  dropped: string[];
  /** Accepted fields whose value breaks the schema; field names and reasons only. */
  problems: FieldProblem[];
};

/**
 * Restrict `body` to what contract `version` accepts and check the values.
 * `schema` overrides the bundled document (a served contract may be newer).
 */
export function validateCreateBody(
  body: Record<string, unknown>,
  version: number,
  schema: RequestSchema = createSessionSchema(version),
): CreateBodyValidation {
  const accepted: Record<string, unknown> = {};
  const dropped: string[] = [];
  const problems: FieldProblem[] = [];
  for (const [field, value] of Object.entries(body)) {
    if (value === undefined) continue;
    const fieldSchema = schema.properties[field];
    if (fieldSchema === undefined) {
      dropped.push(field);
      continue;
    }
    accepted[field] = value;
    const reason = checkValue(value, fieldSchema);
    if (reason !== null) problems.push({ field, reason });
  }
  for (const field of schema.required ?? []) {
    if (!(field in accepted)) problems.push({ field, reason: "required" });
  }
  return { body: accepted, dropped, problems };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** `null` when the value satisfies the schema, otherwise a reason (never the value). */
export function checkValue(value: unknown, schema: FieldSchema): string | null {
  if (schema.anyOf) {
    const reasons: string[] = [];
    for (const branch of schema.anyOf) {
      const reason = checkValue(value, branch);
      if (reason === null) return null;
      reasons.push(reason);
    }
    return reasons.join(" | ");
  }
  if (schema.enum && !schema.enum.some((allowed) => allowed === value)) {
    return `enum:${schema.enum.map((v) => String(v)).join(",")}`;
  }
  switch (schema.type) {
    case undefined:
      return null;
    case "null":
      return value === null ? null : "type:null";
    case "string": {
      if (typeof value !== "string") return "type:string";
      if (schema.minLength !== undefined && value.length < schema.minLength) return `minLength:${schema.minLength}`;
      if (schema.maxLength !== undefined && value.length > schema.maxLength) return `maxLength:${schema.maxLength}`;
      if (schema.pattern !== undefined && !new RegExp(schema.pattern, "u").test(value)) return `pattern:${schema.pattern}`;
      if (schema.format === "uuid" && !UUID.test(value)) return "format:uuid";
      return null;
    }
    case "integer":
    case "number": {
      if (typeof value !== "number" || !Number.isFinite(value)) return `type:${schema.type}`;
      if (schema.type === "integer" && !Number.isInteger(value)) return "type:integer";
      if (schema.minimum !== undefined && value < schema.minimum) return `minimum:${schema.minimum}`;
      if (schema.maximum !== undefined && value > schema.maximum) return `maximum:${schema.maximum}`;
      return null;
    }
    case "boolean":
      return typeof value === "boolean" ? null : "type:boolean";
    case "object":
      return value !== null && typeof value === "object" && !Array.isArray(value) ? null : "type:object";
    case "array":
      return Array.isArray(value) ? null : "type:array";
    default:
      return `type:${schema.type}`;
  }
}

// ----------------------------------------------------------- server probe

/** What `GET /v1/voice/realtime/contract` told us, or why it could not. */
export type ContractProbe =
  | { outcome: "served"; version: number; document: ContractDocument }
  /** 404: the route does not exist there — a contract v1 server. */
  | { outcome: "legacy" }
  /** 401: not signed in; says nothing about the server's version. */
  | { outcome: "unauthorized" }
  /** network failure / unusable answer: the version stays unknown. */
  | { outcome: "unknown"; reason: string };

/** The contract the client decided to honour for this page load. */
export type ResolvedContract = {
  version: number;
  source: "server" | "legacy" | "bundled";
  /** false when the server could not be asked and the bundled version was assumed */
  known: boolean;
  createSession: RequestSchema;
  createFields: string[];
};

export function isContractDocument(value: unknown): value is ContractDocument {
  if (!value || typeof value !== "object") return false;
  const doc = value as Partial<ContractDocument>;
  return (
    Number.isInteger(doc.contract_version) &&
    (doc.contract_version as number) >= 1 &&
    !!doc.requests &&
    typeof doc.requests === "object" &&
    !!doc.requests.create_session &&
    typeof doc.requests.create_session.properties === "object"
  );
}

/** Turn a probe outcome into the contract to honour (`null` when not signed in). */
export function resolveContract(probe: ContractProbe): ResolvedContract | null {
  switch (probe.outcome) {
    case "served":
      return {
        version: probe.version,
        source: "server",
        known: true,
        createSession: probe.document.requests.create_session,
        createFields: createSessionFields(probe.version, probe.document),
      };
    case "legacy":
      return {
        version: 1,
        source: "legacy",
        known: true,
        createSession: createSessionSchema(1),
        createFields: createSessionFields(1),
      };
    case "unauthorized":
      return null;
    case "unknown":
      return {
        version: BUNDLED_CONTRACT_VERSION,
        source: "bundled",
        known: false,
        createSession: createSessionSchema(BUNDLED_CONTRACT_VERSION),
        createFields: createSessionFields(BUNDLED_CONTRACT_VERSION),
      };
  }
}

// ---------------------------------------------------------- owner wording

/** Turkish, owner-facing: what was left out of the create request and why. */
export function droppedFieldsNotice(version: number, dropped: string[]): string | null {
  if (dropped.length === 0) return null;
  const parts = dropped.map((field) =>
    field === "voice"
      ? `'voice' alanı bu sürümde yok; varsayılan ses kullanılacak (marin/cedar seçimi için Cloud Core güncellenmeli)`
      : `'${field}' alanı bu sürümde yok; gönderilmedi`,
  );
  return `Sunucu sözleşmesi v${version}: ${parts.join(" · ")}`;
}

/** Turkish: the server could not be asked, so the bundled version was assumed. */
export function unknownVersionNotice(reason: string): string {
  return `Sunucu sözleşme sürümü bilinmiyor (${reason}); paket içi v${BUNDLED_CONTRACT_VERSION} varsayıldı. Sunucu daha eskiyse istek HTTP 422 ile reddedilebilir.`;
}

/** Turkish: a field the target version accepts but whose value breaks its rule. */
export function problemsNotice(problems: FieldProblem[]): string {
  return problems.map((p) => `alan: ${p.field} · neden: ${p.reason}`).join(" · ");
}
