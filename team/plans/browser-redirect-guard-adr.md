# ADR (browser-redirect-guard, 2026-10-04): every request of the worker's browser is held to the destination policy

**Context.** The worker checked only the REQUESTED url (`_check_destination`). A public page that
redirects - or a sub-request - into the tailnet (100.64.0.0/10, Cloud Core's API at
100.90.158.26:8001), loopback or link-local went through, and the cloud worker runs on the Cloud
Core host (the changedetection.io advisories GHSA-3c45-4pj5-ch7m / GHSA-gwph-fp79-379w broke the
same way). Playwright's `page.route` is not called for redirect hops (integrator finding,
measured on 1.62).

**Decision.**
1. `destination.RequestGuard`: CDP `Fetch.enable` with `requestStage: Request` for every url on
   each page the session drives (attached once per page, before the op touches it; tab_new and
   fetch_evidence `tab:new` open blank, attach, then navigate). Every paused request - each
   redirect hop included - is checked by `require_public_destination` (same rules, same
   trusted-origin exception) in a thread; forbidden -> `Fetch.failRequest(BlockedByClient)`
   before anything is sent; a check that cannot decide fails the request (fail closed).
2. A refused main-frame document (requested url or hop) ends the op with `security_scope_error`
   (not retryable), no page text. Sub-requests/iframes are blocked; the page still reads.
3. After navigate / tab_new / fetch_evidence the worker re-checks the response's redirect chain
   and final url, and the address Chromium was served from (`response.server_addr()`): a name
   the policy resolved as public but Chromium reached on a forbidden address is refused
   (rebinding, main frame). The trusted report view is exempt from the served-address check
   only when its url is admitted.
4. The guard exists only without `--allow-private-destinations` (fixtures keep working with it).
5. `fetch_evidence`: optional `selector` (CSS, <= 200 chars, else validation_error),
   `text_sha256` (whitespace-collapsed sha256 of the matched text joined by newline, or of the
   WHOLE primary text before the excerpt cut), `selector_matched` (bool / null).

**Not chosen here: a network-level egress rule.** The cloud container runs `cap_drop: ALL`, so
iptables inside it is impossible, and the worker process itself must reach `api` on a private
docker address. The working form is a Chromium-only egress proxy (Stripe Smokescreen, MIT, as a
`--proxy-server` inside the container - verify it denies 100.64/10) or a host DOCKER-USER rule.
That is a separate task (third-party record, image change). Residual gaps until then:
WebSockets, service-worker and out-of-process (cross-site) iframe sub-requests, the first
request of a popup before the worker closes it, and a DNS-rebinding SUB-request.
