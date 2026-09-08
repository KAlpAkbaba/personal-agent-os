---
name: m24-capability-genesis-review
description: M24 Capability Genesis review (git diff d051866..5f058a5, merged main@5f058a5, 2026-09-08). High/verified-live - GenesisService._classify() authorizes a mutate_external operation by checking only mutation_authorization.verify(spec.interface.name) is not-None, never AssetAuthorization.covers(); the name checked is the untrusted description's own self-reported field, unbound to the enrolled asset's locator or to the caller-supplied interface_name/URL - PoC executed, an asset enrolled with EMPTY allowed_permissions_json fully authorizes a live mutating dispatch with approval_required=false. Medium - approve() has no CAS/row lock on the awaiting_approval->rolling_out transition (blind session.get + overwrite in _commit), same double-send class as the M21 finding. Medium (architectural, currently inert) - base_url loopback allowlist excludes no port, including the Cloud Core's own 8001; not reachable today only because the production GenesisInterfaceCatalogue is empty with no M24 registration surface. Low - {id} path template accepted at parse but never substituted by the generator (functional gap, fails safe). Everything else (token choke points, repr()/json.dumps() splicing, fetch_interface bounds/redirect/host checks, dispatcher's mutating_unauthorized hard refusal, additive manifest/contract, no-shortcut guard, end-to-end proof reading the real fixture) verified sound, several empirically.
metadata:
  type: project
---

Reviewed git diff d051866..5f058a5 (M24 Capability Genesis, merged to main at
5f058a5, 2026-09-08): Cloud Core services/api/app/genesis/{interface,adapter,
service,models,routes,catalogue,runtime}.py, the six manifest keys in
app/evolution/manifest.py, the dispatcher refusal in
app/evolution/task_resumption.py, app/evolution/{rollout,review,evaluation,
errors}.py, app/voice/realtime_sessions/tools_genesis.py, app/voice/
intents.py, app/uistate/contract.py, migration
20260908_0031_genesis_runs.py, fixtures tests/fixtures/genesis/, corpus
category genesis; web apps/web/app/lib/uistate/genesis.ts, lib/cockpit/
{genesis,genesis-rows,useGenesisControl}.ts, the GenesisPanel. Read
docs/M24_CAPABILITY_GENESIS_SPEC.md, ADR-0087, and continued the M23 lesson
([[m23-app-factory-review]]) of hunting free-text-into-generated-source first.
228 targeted tests (test_genesis_*, test_identity_enforcement.py) run live,
all pass -- the findings below are gaps the existing suite does not cover, not
regressions.

**High (verified live, working PoC) -- the MUTATE-approval gate authorizes on a
bare "is this name enrolled at all" check, never on what was actually granted,
and the name it checks is the untrusted description's own self-reported field,
not bound to the interface's real origin or to what the caller/catalogue
resolved.** app/genesis/service.py::_classify (~line 388):

    verified = self.mutation_authorization.verify(spec.interface.name)
    mutation_authorized = verified is not None

Two independent problems compound:
1. spec.interface.name is InterfaceDescription.name -- a field the fetched
   /spec JSON body supplies and self-declares (app/genesis/interface.py
   InterfaceDescription.parse, validated only to NAME_RE token shape, i.e.
   any lowercase slug). It is never cross-checked against the interface_name
   the caller/catalogue passed into GenesisService.request (used only to
   build capability_id for idempotency/rate-limit lookups, service.py
   ~line 179), nor against the URL the description was fetched from, nor
   against the enrolled asset's locator column
   (app/security/models.py::AuthorizedAsset.locator, never read by
   RegistryAuthorizationProvider.verify -- app/security/provider.py:72-112
   only matches the asset_ref string). A /spec document can name itself
   anything.
2. Even granting the name is legitimate, mutation_authorized = verified is
   not None never calls AssetAuthorization.covers() -- the exact mechanism
   app.evolution.authorization was built for ("a request is approvable only
   when every grant is a subset of the corresponding allowed set"). An
   enrolled, active, in-window asset with allowed_permissions_json = {}
   (e.g. one the owner enrolled only for passive security scanning, or for
   an unrelated evolution permission) still makes
   RegistryAuthorizationProvider.verify() return a non-None
   AssetAuthorization(allowed={}) -- genesis treats that as full owner
   authorization for a brand-new MUTATING external HTTP operation.

**PoC (services/api, uv run python, real fixture, real subprocess dispatch,
no mocks):** built the real GenesisService via tests/unit/genesis_stack
.make_stack with mutation_authorization = StaticAuthorizationProvider(
{"mailserver": {}}) (an asset enrolled with zero granted permission classes
-- simulating a RegistryAuthorizationProvider.verify() result for a real DB
row that was never granted anything genesis-relevant), set the real
tests/fixtures/genesis/counterbox_app.py fixture's
SPEC_TEMPLATE["name"] = "mailserver", then called
service.request(interface_name="mailserver", interface_url=server.spec_url,
operation_id="increment", arguments={"by": 5},
session_id="attacker-session") directly (the same call capability_request()
in tools_genesis.py makes). Result: state: "verified", authority_class:
"mutating_authorized_asset", side_effect_class: "mutate_external",
approval_required: false -- the counter fixture really incremented
(server.value moved to 11), no awaiting_approval state was ever reached, no
owner confirmation of any kind occurred. This directly contradicts
ADR-0087's decision 5 and spec section 9 ("a mutate_external capability is
never registered as usable without either an authorized asset or an
explicit owner approval bound to session + turn") -- the "authorized asset"
check as implemented is satisfiable by name-coincidence with zero actual
grant.

**Reachability today:** GenesisService.request() itself performs no
catalogue check -- reachability is entirely contingent on what calls it.
Today, the only production caller is capability_request()
(tools_genesis.py), gated by app.genesis.catalogue.GenesisInterfaceCatalogue,
which is empty in production with no M24 registration surface
(catalogue.py's own docstring: "no registration surface in M24 itself -- a
later milestone's job"), and routes.py exposes no "create a run" REST verb
(only list/get/approve/cancel). So this exact path cannot be driven by an
external attacker in M24 as shipped. It is nonetheless a real defect in code
that is live on main, for three reasons: (a) it is reachable today by
anything else in-process that calls GenesisService.request() directly --
there is no defense-in-depth at the service boundary, only an incidental
gate one layer up; (b) the catalogue's own docstring commits to a near-term
registration milestone that will make interface_url (and by extension
spec.interface.name) attacker/owner-registration-influenced without this
gap being closed first; (c) it directly undermines the constitution's
"security testing... [scope] not silently extend beyond enrolled authorized
assets" invariant, since it is the SAME authorized_assets registry/table
that scopes M8 security-testing, now reachable by asset-name coincidence
rather than by an actual, purpose-matched grant.

**Fix:** (1) bind the asset_ref checked to something the OWNER actually
chose/registered -- the catalogue entry's own registered name (or, better, a
locator/host comparison against AuthorizedAsset.locator), never the fetched
description's self-reported name; (2) call AssetAuthorization.covers()
with the actual requested grant (at minimum network_permissions:
[spec.host], matching what _rollout_stages's own StaticAuthorizationProvider
already scopes) rather than treating verify() is not None as authorization;
consider a dedicated permission class explicitly checked via .covers(),
matching the existing M7 pattern instead of inventing a second, weaker one
beside it.

**Medium -- GenesisService.approve() has no compare-and-swap on the
awaiting_approval -> rolling_out transition; concurrent approvals can both
proceed, causing a double dispatch of the mutating operation (the same class
of bug as the M21 finding: "non-atomic send/commit state transition allows
double-send under concurrent confirms").** approve() (service.py ~line
650) does run = self._require(run_id) (a plain session.get, no
with_for_update), checks run.state != "awaiting_approval" in Python, then --
only much later, after _roll_out/_register/_use/_verify have all run --
persists the transition. _commit (service.py ~line 942) is a blind
session.get + attribute-overwrite + session.commit(), never a conditional
UPDATE ... WHERE state = 'awaiting_approval'. Two approve() calls racing on
the same run_id (a double-tap on the Cockpit's "Onayla", or REST + voice
racing) can both read state == "awaiting_approval" before either writes the
first rolling_out transition, and both proceed to register and dispatch the
capability -- a mutating external operation executed twice from one owner
confirmation. **Fix:** guard the transition with a conditional UPDATE
(WHERE id = :id AND state = 'awaiting_approval', checking rowcount) or a
SELECT ... FOR UPDATE inside one transaction spanning the check and the
first state write, the same fix the M21 finding calls for.

**Medium (architectural, currently inert) -- the loopback base_url allowlist
in app/genesis/interface.py::_parse_base_url/_require_loopback_url excludes
no port, including the Cloud Core API's own 127.0.0.1:8001
(app/main.py:4).** Empirically verified live (_parse_base_url, see below):
http://127.0.0.1:8001 parses and is ACCEPTED, as are :80 and :1 (no
privileged-port floor either, unlike M23's MIN_PORT=1024 for its own run
ports). Spec section 9's claim that "the only reachable network from an
adapter: its one loopback base URL" is true in the narrow sense that the
RENDERED adapter module structurally cannot reach any host but its own
baked-in BASE_URL literal (confirmed by reading adapter.py's _render_src: no
os.environ read at runtime, exactly one urllib call site, no redirects) --
but nothing stops that one literal from BEING the Cloud Core's own port, or
any other unauthenticated loopback admin surface (e.g. a devtools/metrics
port), because SandboxPolicy (app/evolution/sandbox.py) is confirmed to have
zero network policy at all (grepped: no allow_loopback_http field, no
socket/network logic anywhere in the module -- it is purely a filesystem
confinement class) -- the task's own disclosed gap. So the "structural
guarantee = one hardcoded URL" argument holds for confinement to one origin,
not for which origin. **Not reachable today**: interface_url only ever comes
from the empty production catalogue (same gating as the High finding
above), and REST calls into the Cloud Core's own routes require
require_owner_session (a cookie the adapter subprocess has no way to
present), so even a successful redirect-to-8001 would likely hit 401s on the
/v1/* surface rather than doing damage -- but this has not been verified
against every loopback-bound process in a real deployment (Postgres/Redis/
Temporal-UI/etc., if any are ever loopback-exposed without auth), and the
same "later milestone adds catalogue registration" trajectory as the High
finding applies. **Fix:** when the catalogue-registration surface is built,
either exclude the Cloud Core's own bound port explicitly from acceptable
base_urls, or actually implement SandboxPolicy.allow_loopback_http
(spec section 5's own original sketch) as a real enforcement layer instead
of relying solely on generated-source discipline, so a future defect in the
generator is not the only thing standing between a compromised/malicious
local /spec and an internal service.

**Low -- the {id} path template is accepted at parse (interface.py
PATH_SEGMENT_RE/_parse_operation_path, spec section 2: "no template beyond
{id}") but HttpAdapterGenerator._render_src never substitutes it from the
payload** -- grepped app/genesis/*.py for the literal template token:
the only hits outside interface.py's own parser are unrelated route
docstrings in routes.py. An operation declaring a templated path would
render a generated adapter whose PATH constant is the literal string
containing the template token, which would simply 404/fail against any real
application (fails honestly as dependency_unavailable/postcondition_failed,
not an injection vector) -- a functional gap, not a security one, but worth
closing since a future interface author reading the spec's grammar would
reasonably expect it to work.

**Verified sound (no finding), several empirically:**
- **InterfaceDescription.parse is a genuine closed-alphabet choke point.**
  Every one of name/operation id/path segments/schema field names is a
  full-match anchored regex (NAME_RE, OPERATION_ID_RE, PATH_SEGMENT_RE,
  FIELD_NAME_RE), unknown top-level and per-operation keys are rejected,
  size is bounded to 8 KiB BEFORE walking the structure, and control
  characters (except tab/newline) anywhere in the JSON text are refused
  outright. Crucially, the schema has no free-text/prose field anywhere
  (no summary/title/description on the interface or any operation) -- this
  closes off the entire M23-style "free text into a generated
  comment/docstring" injection class by construction, not just by escaping
  discipline, which is a stronger design than M23's AppSpec.page_title.
- **_parse_base_url/_require_loopback_url empirically tested against every
  bypass the task named**, via a live Python REPL against the real
  functions: 127.0.0.2, 0.0.0.0, [::1], 127.1, 2130706433 (decimal IP),
  localhost.evil.test, trailing-dot 127.0.0.1. / localhost., userinfo
  (127.0.0.1@evil.test), a path/query/fragment on the URL, and ../ in an
  operation path are all REFUSED with validation_error; LOCALHOST (mixed
  case) is correctly accepted and normalized via urlsplit().hostname's own
  lowercasing (not a bypass). Only the port-range and Cloud-Core-port gaps
  noted above are real.
- **fetch_interface**: 5 s timeout, 64 KiB cap enforced by reading
  FETCH_MAX_BYTES + 1 bytes and comparing BEFORE json.loads, a
  _NoRedirect handler that raises on any 3xx (both the HTTPRedirectHandler
  override and the explicit 300-399 HTTPError branch), non-2xx ->
  dependency_unavailable, non-UTF-8/non-JSON body -> validation_error.
  No per-operation base_url exists in the schema at all (only one top-level
  base_url for the whole description), so the task's specific "can an
  operation carry its own base_url and redirect the adapter elsewhere"
  concern does not apply to this design.
- **Free text into generated source (the M23 CRITICAL, restaged): re-verified
  by reading, not just from the module's own comments.** Every value
  HttpAdapterGenerator._render_src splices is either repr()'d
  (BASE_URL, METHOD, PATH, TIMEOUT_S) or json.dumps()'d
  (INPUT_FIELDS/OUTPUT_FIELDS/etc.), and every one of those values was
  already constrained to a closed token shape at the choke point AND
  re-validated immediately before interpolation (_require_name_token,
  _require_operation_id, the require_identifier/ALLOWED_FIELD_TYPES
  loop just above the render). Since no free-text field exists in the
  schema to begin with, there is no "newline in a // comment" surface
  analogous to M23's page_title -- the closed schema, not just the
  escaping discipline, is what closes this off. test_genesis_no_shortcut_
  guard.py's static literal-grep (counterbox, lampbox, /counter, /lamp,
  increment, toggle, brightness, etc. absent from
  interface.py/adapter.py/service.py) and its dynamic proof (deleting a
  fixture's /spec fails a FRESH run honestly at researching, with the
  capability never registered) both re-run live and pass.
- **The dispatcher's hard refusal** (app/evolution/task_resumption.py
  CapabilityDispatcher.dispatch): side_effect_class == "mutate_external"
  and authority_class == "mutating_unauthorized" -> permission_denied,
  checked by reading manifest.get(...) (defaults to None for either key)
  -- confirmed additive: an old manifest carrying neither key sails through
  unaffected (test_genesis_manifest.py::
  test_existing_manifest_without_any_m24_key_still_validates passes live).
  Note this hard rule only ever sees mutating_unauthorized; the High
  finding above means a capability can reach mutating_authorized_asset
  (and thus bypass this gate entirely, legitimately) via the weak
  authorization check -- the dispatcher rule is sound on its own terms but
  cannot compensate for a bad classification upstream.
- **capability.approve/capability.cancel resolve WHICH run from durable
  session-bound awaiting_approval state** (find_awaiting_approval), never
  from the model's own run argument -- confirmed by reading
  tools_genesis.py; approve()'s gate itself (check_gate from
  app.actions.confirmation_gate, the M21 mechanism) correctly requires a
  Confirmation bound to session (+turn for voice) and refuses a bare
  model-supplied claim.
- **Registration only after gates_passed**: _rollout_stages runs the
  independent reviewer (auto-approved ONLY for exactly network_permissions:
  [manifest["network_permissions"]] on external_services[0] -- a
  structurally-narrow, non-owner-consent rubber stamp that is fine because
  the grant is inherently just "reach the one already-validated loopback
  host this run researched", not a stand-in for mutation authority), then
  shadow, then canary, each gated with raise on failure before _register is
  ever called -- no path found that calls
  CapabilityRegistry.register/advances to production skipping any of
  evaluation/review/shadow/canary.
- **Contract v9 additive, REST routes owner-gated**: all four /v1/genesis/*
  routes are behind router-level Depends(require_owner_session) and listed
  in test_identity_enforcement.py (4 entries); migration
  20260908_0031_genesis_runs.py is a pure create_table (expand-only,
  correct down_revision chain to 0030_app_projects).
- **Ledger rows carry no raw description text or secrets**: _ledger
  (service.py ~951) writes only run_id/capability_id/state/error_class
  into detail_json -- never interface_json/evidence_json wholesale;
  secrets never enter the picture since generated adapters always declare
  secret_requirements: [].
- **The proof itself (spec section 7/10)**: re-ran test_genesis_end_to_end.py,
  test_genesis_generalisation.py, test_genesis_no_shortcut_guard.py,
  test_genesis_failure_matrix.py live (228 tests total across the genesis
  suite + identity enforcement, all pass). test_counterbox_increment_end_to_
  end reads its pass/fail from THREE independently-observed values agreeing
  (dispatch.output.value == read_back.value == server.value, the last read
  directly off the real fixture's own in-memory state), never from the
  adapter's own claim alone. The generalisation test drives the SAME
  service against a structurally different fixture (lampbox_app.py:
  state/set/toggle, boolean+integer fields, no counter at all).
- **Web**: no dangerouslySetInnerHTML anywhere in the genesis panel/ts
  files (grepped); "Onayla" gated to awaiting_approval both in the row
  helper (genesis-rows.ts::canApprove) and in the panel's own doc comments;
  a run's approval_ref/state text is rendered, never HTML.

**Residual risk.** The High finding is the one to fix before this becomes
reachable: it is a genuine authorization-binding defect (self-reported name,
no .covers() check) sitting exactly on the milestone's central authority
claim, verified with a real end-to-end PoC, and it directly threatens the
constitution's "security testing scope must not silently extend beyond
enrolled authorized assets" invariant the moment ANY catalogue-registration
surface exists -- which is explicitly the next thing planned
(catalogue.py's own docstring). It should block sign-off on any milestone
that adds catalogue registration until fixed; for M24 itself (whose
catalogue ships empty), it is latent rather than actively exploitable by an
outside party today. The approve() race is a real, independently
worth-fixing gap in the same family as the already-known M21 issue -- cheap
to fix with a conditional UPDATE. The base_url/Cloud-Core-port gap is
genuinely the "decide whether the disclosed equivalence holds" question the
task asked: verdict is partially -- structurally confines an adapter to ONE
origin, but does not restrict WHICH origin, and SandboxPolicy provides no
independent enforcement layer at all, so the safety of that one origin rests
entirely on (a) the catalogue never being populated with anything sensitive
and (b) generator correctness, with no second gate.
