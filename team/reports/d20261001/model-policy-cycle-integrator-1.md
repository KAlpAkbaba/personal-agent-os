# Integrator report — model-policy-cycle (cycle d20261001)

**Choice: ADAPT, no new dependency.** The plan is written; I added nothing to the tree and wrote no feature code. The limit rejection itself could not be provoked, so its shape comes from the binary's code and the docs, not from a real run.

Tool: `claude.exe` 2.1.285, Max subscription. Three real probe runs (Sonnet, Fable, Opus), about 0.14 USD on Fable and cents on the others.

**(i) What the tool prints when one model's limit is hit**
- **Sentence:** `You've hit your <Fable|Opus|Sonnet|session|weekly|usage> limit · resets <local time>`, or `You're out of usage credits …`. The model is named by the limit name; "session" and "weekly" name none because they close all models.
- **Defect in today's detector:** the regex in `Read-TeamRunResult` matches none of those six sentences. A Fable-limited run is read today as an ordinary failed run and counted against the task.
- **Defect in the reset time:** `Claude AI usage limit reached|<epoch>` is not in 2.1.285 at all (0 hits in the binary). `ResetsAt` is always empty from the real tool, so the cycle stops instead of waiting.
- **Machine-readable form:** only `--output-format stream-json --verbose` prints a `rate_limit_event` line with `status` (allowed / allowed_warning / rejected), `rateLimitType` and `resetsAt` as an epoch, per limit. Seen in real runs for the allowed and warning states.
- **Exit code and stderr on rejection:** not observed. stderr was empty in all three probes.
- **Consequence for the chain:** lowering helps only for a model's own limit (`seven_day_overage_included` = Fable, `seven_day_opus`, `seven_day_sonnet`). A `five_hour` or `seven_day` rejection limits all three, so the plan goes straight to the existing wait with no lowered run.

**(ii) The two percentages: a real source exists**
- **Source:** `rate_limit_info.unifiedWindows` in the same event. `seven_day_overage_included.utilization` is the Fable week; `seven_day.utilization` is the week of all models.
- **Today's real values:** Fable 81 % (warning), all models 46 %, both reset 2026-10-05T16:00:00Z; the 5-hour session is at 6 %.
- **Limits of the source:** the Fable window appears only in events of runs that ran on Fable, so `fable.used_pct` is null until one has finished. The field is marked `@internal`; if it is absent the value is null and the page says "bilinmiyor".
- **Not the status line:** it is interactive only and has no Fable window.

**(iii) `--fallback-model`**
- **It exists and must not be passed.** The docs say rate-limit errors "never trigger a switch"; it fires on overloaded or unavailable, lasts one turn, and shows only a notice.
- **A second, undocumented substitution is in the binary:** when the Fable limit is reached the tool can switch the session to a non-Fable model (`system` / `model_consent_fallback`) and the run then succeeds. I could not decide from the code whether a plain `-p` run reaches it.
- **Guards in the plan:** start every run with `CLAUDE_CODE_NO_MODEL_FALLBACK=1` (found in the binary, absent from the env-vars page), and compare `modelUsage` with the model asked for after every run.
- **History:** the result documents kept under `team/reports/` all ran on `claude-sonnet-5-5`, before the setting existed. No silent lowering has happened yet.

**Seam**
- `Get-TeamRunArguments`: `json` becomes `stream-json --verbose`.
- `Start-TeamRun`: sets the environment variable.
- `Read-TeamRunResult`: reads both shapes, line by line. The real result line does not start with `{"type"`, and PowerShell 5.1 refuses JSON over 2 MB, so the whole output is never parsed at once.
- Pure helpers go in `TeamQueue.ps1`; the chain, `team/limits.json` and the status go in `cycle.ps1`. Files, seven extra tests with real-format fixtures, and the rollback are in the plan.

**Footprint:** a trivial run's stream is 6 KB (measured). A worker's stream is estimated at 1–20 MB in memory. The plan keeps only the result line in `<stem>.json`, so the reports folder does not grow.

**Risks**
- With Fable at 81 % and four days to the reset, the first real lowering is likely soon; until then the evidence is PROVEN_AUTOMATED.
- Two undocumented fields can change with a tool update.
- A session limit now waits at once instead of trying lower models, which the card does not spell out.
- Test 5 (the tool lowered an inspector, so its verdict is not taken) goes past the card's acceptance.

**For the lead at merge**
- THIRD_PARTY entry text is in the plan.
- `.gitignore`: decide whether `team/limits.json` and `team/status.json` are machine-local.
- The contract is unchanged. The other two cards should know that `limits.all` is the week of all models, not the 5-hour session.

Plan: `team/plans/model-policy-cycle-integration.md`
