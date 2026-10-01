## The browser task loop's model planner: one forced `step` call, cheap by default, capable for the re-plan (ADR-0207 PR-C 1/2)

**Decision.** `app/webtask/model_planner.py::ModelPlanner` is the model behind the `TaskPlanner` interface.
One Messages API request per plan: `system` = `build_prompt()["system"]`; ONE user message holding the blocks
`GOAL`, `ELEMENTS`, `HISTORY`, `PAGE` in that order (the goal is read first; the page excerpt is the last thing in
the message and exists only inside the untrusted-content wrapper); `tools=[STEP_TOOL]`,
`tool_choice={"type":"tool","name":"step"}`, `max_tokens=600`, `temperature=0`, no `thinking`. The model is
`settings.research_anthropic_model` (Haiku), and `settings.executive_planner_model` (Sonnet) when
`request.capable` - which the loop sets for the round after a step that did not hold. That field is the whole
re-plan rule: this module adds no budget and no second call. Raw `httpx` through an injectable `send`, one retry
after 1.5 s on 429/529, 30 s timeout (two attempts stay inside the round activity's 150 s heartbeat timeout):
the shape of `assistant_chat.AnthropicChatProvider`. No SDK, no new dependency, no `config.py` change.
`default_planner()` chains `[RuleTablePlanner, ModelPlanner]` when `anthropic_api_key` is set and
`[RuleTablePlanner, NoModelPlanner]` otherwise - a consent banner still costs nothing.

**What is accepted back.** Exactly one `tool_use` block named `step`, whose input survives `parse_step`.
Words, a refusal, another tool, two calls, `stop_reason == "max_tokens"` (a step cut off mid-arguments), any
non-200, a transport failure or a timeout is a `PlannerError`, which the loop already turns into a failed task
(`FAIL_PLANNER`). Every such message is built from fixed words, a status number, an exception CLASS name or a
count: never the model's text, never the vendor's error message, never a key the model invented
(`parse_step` names unknown keys in its error; here that case is replaced by a fixed sentence, because a model
that read a hostile page can write anything as a key).

**Why.** Until now every round the rules could not answer asked the owner "model henüz bağlı değil". The
boundary (prompt, tool, strict parse, gate) was built and tested before a model existed; this puts a model
behind it without the loop, the gate or the service changing.

**Open - for the next card (real model, fixture site), none verified here.**
* No request has been sent to the real API. `temperature: 0` and a forced `tool_choice` are accepted by Haiku
  4.5 as documented; whether `executive_planner_model` (`claude-sonnet-5` by default) accepts a non-default
  `temperature` is NOT_RUN. If it answers 400, every capable round fails with "status 400" - loud, not silent.
* With the key that production already has for research, `default_planner()` returns the model planner as soon
  as this is released. A task still needs the device's `-AuthorizeTasks` grant to run at all (PR-C), so no
  round is planned by a model before the owner grants it; after that each unruled round costs one Haiku call.
* Element NAMES are page-written and sit in `ELEMENTS`, outside the wrapper (`build_prompt`'s design: defused,
  one line each, labelled as data by the system prompt). The guarantee stays `parse_step` + the gate.
* The trail's `planner` field says `model`, not which model; the round after a hint is the capable one.
* A 404 (retired model id) is reported as "status 404" and logged with the model id; it is not retried.
