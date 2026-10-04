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

**Return of 2026-10-04 (inspector: a cross-site iframe, a popup and a WebSocket reached the
"tailnet"; tab_new had no test; the owner's tab stayed intercepted) - decisions added:**
6. **Network layer: an in-process egress proxy** (`destination.EgressProxy`, the smokescreen idea
   in ~250 lines of asyncio, no new dependency). Every MANAGED browser the worker launches gets
   `--proxy-server=http://127.0.0.1:<port>`, `--proxy-bypass-list=<-loopback>` (Chromium
   otherwise sends loopback around a proxy) and
   `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` (WebRTC UDP would go around it).
   Every connection - any target (OOPIF, popup, worker), each hop, WebSockets (CONNECT) - is
   checked by `require_public_destination` with the same resolver and trusted origin, and the
   proxy dials the address it VETTED (resolved once: the DNS-rebinding gap between our resolver
   and Chromium's is closed, not only detected). One request per plain-http connection
   (`Connection: close` both ways) so a kept-alive connection cannot carry a second host.
   Chosen over CDP `Target.setAutoAttach`+`waitForDebuggerOnStart`: Playwright's CDPSession
   cannot address the flattened child sessions, and auto-attach still misses WebSockets.
   Chosen over a container iptables rule: `cap_drop: ALL` forbids it, and the office/home
   Windows workers need the same guard. Smokescreen (Go binary) is not needed for that.
7. The page guard (decision 1) stays: it identifies a refused PAGE navigation (so the op ends in
   `security_scope_error`, not a proxy 403 page) and covers owner-profile sessions. When the
   page was served through the proxy, the served-address check (decision 3) is the proxy's.
8. **Owner-profile sessions** (the owner's own Chrome, attached over CDP): not ours to launch, so
   no proxy. The page guard holds the owner's tab ONLY while an op of the worker drives it; when
   the op ends (or fails) `Fetch.disable` + detach - the owner's own requests to the web shell,
   NAS or router are never failed or slowed afterwards. Residual: an owner session has no
   network layer (OOPIF/popup/WebSocket gaps of decision 1) - accepted: it is the owner's own
   browser on the owner's own network, and the requested url is still checked.
9. `tab_new` with a url opens blank, attaches, then navigates - now under test.

10. (Return of 2026-10-04.) A CONNECT never gets the trusted-origin exception. The first cut
   admitted the trusted host:port for any tunnel; Chromium tunnels ws:// WebSockets as CONNECT
   too, so a hostile public page reached ANY path of the Cloud Core API
   (`/v1/devices/ws-probe`, the inspector's probe). A tunnel has no path to check, so the
   exception (report-view route only) cannot apply to it; the trusted view is http and comes
   absolute-form, where the path is checked. Cost: an https trusted origin is not reachable
   from a launched browser (fail closed; none is configured today).

11. (Return 3, 2026-10-04.) **The address check is an allow-list, not a deny-list.** The
   deny-list (`is_private`/`is_loopback`/... plus extra networks) had a hole for each new IPv6
   spelling of an IPv4 address: `::ffff:100.90.158.26` (`::ffff:645a:9e1a`) was admitted, and a
   dual-stack socket on the Linux host dials it as the Cloud Core API (GHSA-gwph-fp79-379w
   class). Now `address_is_forbidden` parses (scope `%..` dropped; unparseable = forbidden),
   unwraps every embedded IPv4 - `ipv4_mapped`, `sixtofour`, both Teredo ends, NAT64
   `64:ff9b::/96` and `64:ff9b:1::/48` (RFC 6052 bit layout), IPv4-compatible `::/96` - and
   admits only when the address AND every unwrapped IPv4 are `is_global`, not multicast
   (224.0.0.1 is "global" to ipaddress) and outside `100.64.0.0/10`. The page guard, the
   requested-url check and `EgressProxy.vet` share it. Unwrap is explicit so the verdict does
   not depend on the Python patch release (3.12.7's `is_global` already follows the mapped
   IPv4, older ones do not; NAT64 and `::a.b.c.d` are `is_global` there and are caught only by
   the unwrap - the mutation test).
12. (Return 3.) A page navigation ONLY the egress proxy refused ends `security_scope_error`, not
   retryable, instead of `dependency_unavailable` (CONNECT refused -> tunnel failure) or the
   proxy's empty 403 as "the page". The guard records the host:port of each main-frame document
   it lets through; a proxy refusal since the op's mark with the same host:port is the
   navigation's refusal. A refused sub-request of another host does not fail the page. A
   redirect to `file:///` (Chromium's own `ERR_UNSAFE_REDIRECT`, never seen by the guard) maps
   the same way. Residual: a sub-request to the same host:port as a page navigation that the
   proxy refused would also fail the op - that host was already refused for the page, so the
   op failing closed is accepted.

**Residual risk / follow-up.** Follow-up task
(not opened here): a host-level DOCKER-USER rule dropping the cloud-browser container's traffic
to 100.64.0.0/10 and the host's own addresses (belt and braces against a bug in the proxy;
needs the host's firewall, release-engineer). Media sessions (the alarm, YouTube) now stream
through the proxy too: watch the first alarm after release.
