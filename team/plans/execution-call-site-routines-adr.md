## ADR (number by lead): a routine's browser action asks the execution_target rule; the shared device port does not

Task: `execution-call-site-routines` (roadmap 2b, ADR-0213 rule row 1, ADR-0220). 2026-10-02.

Context: `app.routines.target.choose_routine_target` had no caller. A routine's `browser_action`
was sent by `BrokerDeviceAction` to whatever `select_device` picked - the healthiest machine.

Decision:
1. `ActionDispatcher._browser_action` sends through `BrokerDeviceAction.scheduled()`, a view of
   the port (like `bound_to`'s). On that view a `browser.<op>` of `BROWSER_ACTION_ALLOWLIST` asks
   `select_routine_device` (`choose_routine_target` over the same registry snapshot, with the
   operation as `capabilities` and the payload's `url`), then `wiring.device_for`: the cloud
   device, or `NoCapableDeviceError` -> the existing failed `DeviceRunResult("no_capable_device")`
   whose message ends with the decision's reason (`... (no_target_available)`), which is the text
   the routine's failure notification already carries. A refusal never reaches `_select_for`.
2. Every other capability on that view (`app.launch`, `desktop.*`) keeps `_select_for`: the rule
   is not asked, no execution row is written.
3. `selection_for` on the view asks the same rule through a pure twin (`probe_routine_device`:
   `rule.decide` over `wiring.device_for`'s availability test) because `wiring.choose` writes and
   commits ledger rows and a probe is not a run. One parametrized test holds probe == run.
4. The ledger rows are the record, not the decision (`ledger_required=False`, as the research
   start): a ledger that will not write is logged and the action is still sent.
5. **Behind a setting, OFF by default** (lead ruling 2026-10-03):
   `routines_execution_rule_enabled: bool = False` (`PAGENTOS_ROUTINES_EXECUTION_RULE_ENABLED`),
   read by `ActionDispatcher._browser_action` at each firing. OFF: the dispatcher calls the
   plain port exactly as main does (`self._device_action.run(...)`, same arguments) - the
   machine `_select_for` picks, and the rule writes no row, not even a probe's. ON: points 1-4.
   No compose line: turning it on is its own owner decision, after the cloud worker can keep a
   session across firings (next section). Tests through the object `create_app` builds (the
   routine dispatcher over `app.state.device_action`), off and on, unit and dev-stack
   PostgreSQL. `get_settings` is `lru_cache`d: the value is the process's at start.

**Where the build departs from the card - the lead decides each.**
- **The card put the check in `_select_for` for every caller, by capability. Not built that way.**
  `BrokerDeviceAction` is ONE object (`app/main.py:289`) held by the wake sequence, the operator,
  the mission, the voice path, the toast ladder and artifact open. The cloud worker advertises
  `browser.media_play`, `browser.session_open`, `browser.navigate`: a capability-only check sends
  the wake alarm's music and the operator's "ofiste şunu aç" to the cloud, or refuses them
  (`forced_target_not_allowed`). So the rule is asked only by the scheduled view, and only the
  routine dispatcher takes that view. `test_the_shared_port_itself_does_not_ask_the_rule` holds it.
- **`media_playback` does not ask the rule.** It opens a VISIBLE `isolated` window for an owner who
  is to hear it; the cloud worker refuses every profile but `research`
  (`browser_agent/cloud/policy.py`) and has no display or speaker. Under the rule every
  `media_playback` routine would fail for ever. If the owner wants row 1 to cover it, it is one
  line (`_media_playback` takes the same view) and the feature is then dead until a cloud audio
  path exists. OPEN - an owner/lead decision.
- **Deny-listed site: the rule's own answer is the cloud, and a routine inherits it** (the card's
  acceptance line "a deny-listed url -> refused deny_listed_site" is WITHDRAWN by the lead,
  2026-10-03). The deny-list is the machines' allow-list concern, not the cloud's: ADR-0213's
  table says "acting on a deny-listed site -> refused; reading is not refused", and the addendum
  and `wiring.choose` force `acting=False` for every scheduled job, so
  `test_execution_wiring.py::test_the_routine_adapter_is_read_only_and_cloud_only` (outside the
  area) asserts a deny-listed url is SELECTED cloud. A scheduled routine reading a deny-listed
  site is therefore sent to the cloud worker (no owner session there; READ+NAVIGATE policy).
  Built: the mapping (a refusal the rule RETURNS - payment, ask_owner, or a future deny-list
  refusal - is the same failed result with its reason, never sent), tested with a stubbed
  decision, and a test pinning the real answer (selected cloud). Whether scheduled reads of such
  sites should be refused is a rule question the lead cards separately (`app/execution/`).

Consequences (not softened; every one below holds with the setting ON - OFF, nothing changes):
- A scheduled browser action runs in the cloud or fails. Cloud offline, revoked, not advertising
  the operation or denied it by policy -> `no_capable_device`, with a machine online and able.
- A routine that names a machine for a browser action is refused (`forced_target_not_allowed`),
  even with that machine and the cloud both up. No routine field names a device today (nothing
  calls `scheduled(targets=...)`); the refusal is what any future one gets. The word "bulutta" is
  the cloud.
- A routine `browser_action` that ACTS (`click`, `fill`, `upload`) is sent to the cloud as a read
  (the rule never sees `acting`); the cloud worker's READ+NAVIGATE session policy is what refuses
  it. A routine that used to click on a home machine no longer does.
  A machine word beside the cloud word (`("bulutta", "ev")`, either order) is the machine word:
  refused, on the run and on the probe.
- **A `selected` ledger row is not proof the action ran.** The cloud worker requires a `session_id`
  on every operation but `browser.worker_status` (`browser_agent/worker.py::_require_session_id`,
  lines 1131-1149: a payload without one is `validation_error`), and every operation other than
  `session_open` must name a session already open ON THAT WORKER (`unknown session`). The routine
  dispatcher sends ONE operation per firing and opens no session, so a `browser_action` that
  carries no `session_id`, or names one opened on a machine, is selected for the cloud, written
  `execution.selected target=cloud`, and then FAILS on the worker. The row records where the rule
  sent the operation, nothing more; whether it ran is the firing's own outcome (`DispatchOutcome`,
  the device command's result). So the card's PROVEN_REAL criterion ("the first routine whose
  ledger row says target=cloud") is not sufficient: PROVEN_REAL needs that row AND a succeeded
  command result from the cloud device for the same firing. Until a routine can open a cloud
  session (one action = one operation today), the only `browser_action` that can succeed there
  is `session_open` itself. Before turning the setting on: count production routines whose
  action kind is `browser_action` - each one that works today through a machine's session stops
  working.
- Whether production's cloud worker advertises `browser.navigate` today is UNVERIFIED (the last
  host snapshot predates the 2026-10-02 release and holds no device capabilities). If it does
  not, every routine `browser_action` fails `no_capable_device` with the setting on.
- **READY_FOR_OWNER, not part of the merge:** the first production evidence needs the setting
  ON (an owner decision, after the cloud worker keeps a session across firings) and is a firing
  whose ledger row says `execution.selected target=cloud` AND whose cloud device command
  succeeded. The merge itself changes nothing in production (the setting is off).
