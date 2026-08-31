# M3 Security Review — 2026-08-31

Independent review of the M3 additions (research + artifact backend, renderers, CORS, bundled fonts, Windows `desktop.open_artifact`, web inbox). Companion verification: independent test-engineer reproduced the M3 acceptance criteria. **No High/Critical findings.**

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Medium | HTML renderer passed the (future-untrusted) canonical body through `markdown` with no safe mode — raw `<script>`/`<img onerror>` from a provider `title`/`snippet` would survive into the HTML render, becoming live markup once `WebResearchProvider` ships and `desktop.open_artifact` ShellExecutes an `.html` render in the owner's real browser. | **Fixed at M3**: `HtmlRenderer` escapes `&`/`<`/`>` across the whole body before Markdown conversion (lossless for our `#`/`-`/`**`-only markup, neutralizes all injected HTML). Unit tests for injected `<script>`/`<img onerror>` and for Turkish/ampersand preservation. Hard gate before any networked research provider. |
| 2 | Medium | `desktop.open_artifact` symlink defense re-resolved only the leaf file, not ancestor path segments — a directory junction planted inside an artifact root but pointing outside could smuggle an outside file past textual containment (Windows resolves junctions transparently at open time). Needs same-user local write to exploit (defense in depth). | **Fixed at M3**: `ArtifactOpener` now walks every ancestor directory segment between the matched root and the file, rejecting (`security_scope_error`) any reparse point whose resolved target leaves the root, failing closed on inspection errors. xUnit test creates a real `mklink /J` junction and asserts rejection. |

## Confirmed resolved from prior reviews

- **M0 finding #3 (no CORS)** — resolved: `CORSMiddleware` with a scoped `web_origins` allowlist (never `*`), `allow_credentials` default false, methods/headers scoped, env override via JSON. Unit-tested (allowed/denied/preflight/never-wildcard).
- **M0 finding #4 (`validate_object_key`)** — still enforced; wraps every M3 render object key.

## Clean areas (verified)

All M3 persistence uses SQLAlchemy ORM (no raw SQL); task input capped (4000 chars), `source_limit` bounded, `provider` checked against a wired allowlist so the unimplemented networked provider cannot be invoked; READY-without-auto-read enforced at the API layer (list endpoint has no `include` param; single-get body only via `?include=body`); `/renders/{fmt}` allowlists format and derives object keys server-side (no arbitrary fetch); error responses leak no internals; bundled fonts are genuine TrueType with a license note and no host-path traversal; the web inbox uses auto-escaped React text interpolation (no `dangerouslySetInnerHTML`), render links use only server-supplied formats; no new tracked secrets, no non-loopback binds, no CI changes.
