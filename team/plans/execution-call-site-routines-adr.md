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
- **"A deny-listed url -> refused deny_listed_site" is not what the rule answers, and is not built.**
  ADR-0213's table: "acting on a deny-listed site -> refused; reading is not refused"; the addendum
  and `wiring.choose` force `acting=False` for every scheduled job;
  `test_execution_wiring.py::test_the_routine_adapter_is_read_only_and_cloud_only` (outside the area)
  asserts a deny-listed url is SELECTED cloud. Built: the mapping (a refusal the rule RETURNS -
  deny-list, payment, ask_owner - is the same failed result with its reason, never sent), tested
  with a stubbed decision; and a test that pins the real answer (selected cloud). Refusing scheduled
  reads of deny-listed sites is a change to the rule (`app/execution/`, outside the area).

Consequences (not softened):
- A scheduled browser action runs in the cloud or fails. Cloud offline, revoked, not advertising
  the operation or denied it by policy -> `no_capable_device`, with a machine online and able.
- A routine that names a machine for a browser action is refused (`forced_target_not_allowed`),
  even with that machine and the cloud both up. No routine field names a device today (nothing
  calls `scheduled(targets=...)`); the refusal is what any future one gets. The word "bulutta" is
  the cloud.
- A routine `browser_action` that ACTS (`click`, `fill`, `upload`) is sent to the cloud as a read
  (the rule never sees `acting`); the cloud worker's READ+NAVIGATE session policy is what refuses
  it. A routine that used to click on a home machine no longer does.
- A routine `browser_action` that names a `session_id` opened on a machine finds no such session
  on the cloud worker.
- No setting guards this (the research call site has one, ADR-0248). With the cloud worker's
  image not rebuilt, every routine `browser_action` fails `no_capable_device` from the release on.
  If the lead wants the research order (image first), a setting is the lead's to add: `config.py`
  is outside the area.
