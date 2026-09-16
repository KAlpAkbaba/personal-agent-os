/**
 * B22 req 705/708/709/710/711: what a failed request MEANS to the owner.
 *
 * The Cloud Core answers a failure with `{error_class, message}` — a token from a closed
 * taxonomy and a Turkish sentence from `app.errors.catalog` (req 704). Until now the web
 * threw both away: `getJson` raised `new Error("HTTP 500")` and the panel printed
 * "Alınamadı: HTTP 500", which tells the owner that something went wrong, not what, and
 * above all not whether it is worth pressing anything.
 *
 * Four of those five requirements are the same requirement seen from four angles: a failure
 * the owner CAN retry, one that is waiting on a credential, one that is waiting on a
 * permission, and one that is waiting on a decision only they can make. They need different
 * words and different controls — a retry button on a missing API key is an invitation to
 * press it forever.
 *
 * The class lists below are the SERVER's own taxonomy, restated here because a browser
 * cannot import Python. `test_the_web_failure_kinds_match_the_catalogue` reads this file
 * and fails when a class listed here is not in the catalogue, the same discipline
 * `test_voice_unavailable_contract` uses for the voice surface.
 */

/** What kind of failure this is, from the owner's point of view. */
export type FailureKind = "retry" | "provider_blocked" | "permission" | "owner_action" | "failed";

export type Failure = {
  kind: FailureKind;
  /** The server's own class token, kept for diagnostics and never shown as the message. */
  errorClass: string;
  /** The Turkish sentence the server sent, or a fallback when it sent none. */
  message: string;
  /** True when pressing "tekrar dene" could plausibly work. */
  retryable: boolean;
  /** The HTTP status, when there was one. 0 for a transport failure. */
  status: number;
};

/**
 * req 708. Something outside this system did not answer, or asked us to slow down. These
 * are the only failures a retry control belongs on: the state that produced them can change
 * on its own.
 */
export const RETRYABLE_CLASSES: ReadonlySet<string> = new Set([
  "all_providers_failed",
  "dependency_unavailable",
  "fetch_failed",
  "provider_unavailable",
  "rate_limited",
  "send_failed",
  "target_unavailable",
  "throttled",
  "timeout",
  "workflow_start_failed",
  "research_workflow_start_failed",
  "news_summarize_workflow_start_failed",
]);

/**
 * req 709. A provider exists in the product and not in this deployment. Pressing anything
 * changes nothing; a key does, and only the owner can add one — never through a browser
 * form (the constitution keeps secrets in the DPAPI store).
 */
export const PROVIDER_BLOCKED_CLASSES: ReadonlySet<string> = new Set([
  "provider_auth_missing",
  "generator_not_configured",
  "backend_not_configured",
  "optional_dependency_missing",
  "capability_missing",
  "no_capable_device",
  "unsupported_provider",
]);

/** req 710. The system is not allowed to do this, and the owner is who allows it. */
export const PERMISSION_CLASSES: ReadonlySet<string> = new Set([
  "permission_denied",
  "scope_missing",
  "out_of_scope",
  "origin_refused",
]);

/**
 * req 711. Nothing is broken and nothing is missing: a decision is outstanding, and it is
 * the owner's. Distinct from a permission (which is a standing grant) because what is
 * needed here is a judgement about THIS case.
 */
export const OWNER_ACTION_CLASSES: ReadonlySet<string> = new Set([
  "product_change_required",
  "remediation_not_automatable",
  "not_superior",
  "review_rejected",
  "preflight_refused",
]);

/** The heading each kind is shown under. Short, and never the word "hata" for a wait. */
export const FAILURE_LABEL: Record<FailureKind, string> = {
  retry: "Şu an ulaşılamadı",
  provider_blocked: "Bu özellik bu kurulumda kapalı",
  permission: "İzin gerekiyor",
  owner_action: "Senin kararını bekliyor",
  failed: "Alınamadı",
};

/** What the owner can do, when the answer is not in the server's own sentence. */
export const FAILURE_HINT: Record<FailureKind, string> = {
  retry: "Tekrar denenebilir.",
  provider_blocked: "Gerekeni yalnızca sen ekleyebilirsin.",
  permission: "İzni yalnızca sen verebilirsin.",
  owner_action: "Hazır olduğunda söyle, kaldığı yerden devam ederim.",
  failed: "",
};

function kindOf(errorClass: string): FailureKind {
  if (PROVIDER_BLOCKED_CLASSES.has(errorClass)) return "provider_blocked";
  if (PERMISSION_CLASSES.has(errorClass)) return "permission";
  if (OWNER_ACTION_CLASSES.has(errorClass)) return "owner_action";
  if (RETRYABLE_CLASSES.has(errorClass)) return "retry";
  return "failed";
}

/**
 * Build a `Failure` from what the server said.
 *
 * A body with no class is still a failure worth showing — `status` carries what little is
 * known — but it is never dressed up as one of the four named kinds, because those four
 * make promises about what will help.
 */
export function classifyFailure(
  errorClass: string | null | undefined,
  message: string | null | undefined,
  status = 0,
): Failure {
  const cls = (errorClass ?? "").trim();
  const kind = cls ? kindOf(cls) : "failed";
  const said = (message ?? "").trim();
  return {
    kind,
    errorClass: cls,
    message: said || fallbackMessage(status),
    // A generic failure is retryable too — the owner has nothing else to try — but a
    // provider, permission or decision block is NOT: the button would be a lie.
    retryable: kind === "retry" || kind === "failed",
    status,
  };
}

function fallbackMessage(status: number): string {
  if (status === 0) return "Cloud Core'a ulaşamadım.";
  if (status >= 500) return "Cloud Core bu isteği tamamlayamadı.";
  return `İstek kabul edilmedi (HTTP ${status}).`;
}

/**
 * Read the server's error body. Understands both shapes this API produces: the plain
 * `{error_class, message}` and FastAPI's `{detail: {...}}` wrapper, plus a `detail` that is
 * a bare string (older routes) — which is shown as-is only when it is not developer text.
 */
export function failureFromBody(body: unknown, status: number): Failure {
  const inner =
    body && typeof body === "object" && "detail" in (body as object)
      ? (body as { detail: unknown }).detail
      : body;
  if (inner && typeof inner === "object") {
    const record = inner as Record<string, unknown>;
    const cls = typeof record.error_class === "string" ? record.error_class : "";
    const message =
      typeof record.message === "string"
        ? record.message
        : typeof record.detail === "string"
          ? record.detail
          : "";
    return classifyFailure(cls, sanitise(message), status);
  }
  if (typeof inner === "string") return classifyFailure("", sanitise(inner), status);
  return classifyFailure("", "", status);
}

/**
 * req 705, on this side of the wire too. The server no longer sends exception text, and
 * this is the second lock: a body that carries a Python failure is dropped in favour of the
 * fallback sentence. An old Cloud Core, a proxy's error page and a route nobody has
 * converted yet all arrive here, and none of them get to show the owner a traceback.
 */
const DEVELOPER_TEXT = [
  /\b[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Failure)\b\s*:/,
  /Traceback \(most recent call last\)/,
  /\bFile "[^"]+", line \d+/,
  /\b(?:app|sqlalchemy|httpx|pydantic|fastapi)\.[a-z_]+\.[A-Za-z_]/,
];

export function looksLikeDeveloperText(text: string): boolean {
  return DEVELOPER_TEXT.some((pattern) => pattern.test(text));
}

function sanitise(text: string): string {
  return looksLikeDeveloperText(text) ? "" : text;
}
