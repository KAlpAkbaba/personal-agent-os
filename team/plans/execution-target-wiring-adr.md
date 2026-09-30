# ADR (no number yet): the execution_target rule is wired through one function (ADR-0213 PR 1b)

Decision: `app/execution/wiring.choose()` is the only place that reads the device registry for the
rule, calls `decide()`, writes its events to the ledger and returns the `Decision`. Services call it
through `app/research/target.py` and `app/routines/target.py`; they never build an `Availability`.
- Registry conventions (nothing new stored): a device with `platform == "cloud"` is the cloud worker
  and never the `device` target; a device labelled `owner_chrome` is the machine whose Chrome the owner
  enrolled. The registry has no `kind` field; if one is added, `_availability` is the one place to change.
- `scheduled=True` forces `acting=False` and the cloud-only `scheduled` kind, whatever the caller says.
- A cloud acting step must pass `app.execution.allowlist.acting_allowed(url)`; a missing module counts as
  "not allowed" (refusal `not_on_owner_allow_list`). The refusal is written as `execution.refused`.
- "No target" refusals (`no_target_available`, `no_eligible_target`, forced_*) map to the existing
  `no_capable_device` class: adapters raise `NoCapableDeviceError`. Policy refusals (payment, deny-list,
  allow-list) keep their own reason and are returned, not raised.
Why: one writer for the events keeps "what was tried and why" in one ledger shape. Consequence: the
call sites still have to be added by the lead.
