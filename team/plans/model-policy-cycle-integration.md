# model-policy-cycle — integration plan (integrator, cycle d20261001)

Tool examined: `%USERPROFILE%\.local\bin\claude.exe`, **2.1.285** (`claude --version`), Max
subscription, 2026-10-01. Every fact below names how it was seen. Three classes are kept apart:
**RUN** (a real call printed it), **CODE** (read in the installed binary's bundled JavaScript),
**DOC** (code.claude.com). Nothing here was estimated. The limit rejection itself could NOT be
provoked (no window is exhausted today), so its shape is CODE + DOC, not RUN — said again where
it matters. `ctx.py` in the commands below is a 20-line helper that prints the text around a
literal in the binary; it and the three probe outputs are in `%TEMP%\mpc-probe\` (not in the tree).

## Choice: ADAPT. No new dependency. One flag pair changes, one environment variable is added.

The tool already carries everything the card asks for, but not where the cycle looks today:

- the reset time and the two percentages are in a **`rate_limit_event`** message that only
  `--output-format stream-json --verbose` (or `json --verbose`) prints — never in the single
  `--output-format json` document the cycle reads now;
- `--fallback-model` exists and is the wrong tool for this (fact iii);
- nothing is added to the tree: no package, no binary, no network call of our own.

## Fact (i) — what the tool says when ONE model's limit is hit

**It does not print what `Read-TeamRunResult` waits for.** Three findings, the first two are
defects of today's code and belong to this task:

1. **CODE.** The limit sentence is built as `You've hit your <limit name><resets>` where the
   name comes from the limit type:
   `five_hour`→"session limit", `seven_day`→"weekly limit", `seven_day_opus`→"Opus limit",
   `seven_day_sonnet`→"Sonnet limit" ("weekly limit" on Pro), `seven_day_overage_included`→
   **"Fable limit"**, otherwise "usage limit" / "limit"; with credits involved it can instead be
   `You're out of usage credits · resets …`. `<resets>` is ` · resets <human local time>`
   (e.g. "8:40pm"), sometimes followed by ` · progress saved`.
   Seen with: `python ctx.py 'return o_("usage limit",g,n)' 2300 250` and the table
   `yme={five_hour:"session limit",…,seven_day_overage_included:"Fable limit",overage:"usage credit limit"}`.
   So the model IS named, by the limit name — "Fable limit", "Opus limit", "Sonnet limit" — and a
   limit that is not a model's ("session", "weekly") names none, because it closes ALL models.
2. **Today's detector misses the likely sentences.** `(?i)usage limit|hit your (usage |rate )?limit|
   limit reached|out of extra usage` does not match "You've hit your **Fable** limit", "…**session**
   limit", "…**weekly** limit", "…**Opus** limit" nor "You're out of **usage credits**". A Fable-limited
   lead or inspector run is read today as an ordinary failed run and counted against the task.
3. **The string `Claude AI usage limit reached|<epoch>` is not in 2.1.285 at all** (RUN:
   `python ctx.py 'Claude AI usage limit reached' 200 200` → 0 hits in the 243 MB binary). The
   reset epoch the cycle parses with `limit reached\|(\d{10})` never arrives from the real tool,
   so `ResetsAt` is always empty and the cycle STOPS ("ne zaman açılacağı söylenmedi") instead of
   waiting. The fake's `limited` scenario prints a sentence the real tool no longer has.
4. **Where the machine-readable form is — RUN.** With `--output-format stream-json --verbose`
   each run prints one line per message; one of them is (verbatim, a Fable run today):
   ```
   {"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1791216000,"rateLimitType":"seven_day_overage_included","utilization":0.81,"isUsingOverage":false,"surpassedThreshold":0.75,"unifiedWindows":{"five_hour":{"utilization":0.06,"resetsAt":1790876400},"seven_day":{"utilization":0.46,"resetsAt":1791216000},"seven_day_overage_included":{"utilization":0.81,"resetsAt":1791216000}}},"uuid":"…","session_id":"…"}
   ```
   and a Sonnet run: `…"status":"allowed","resetsAt":1790876400,"rateLimitType":"five_hour",
   "overageStatus":"rejected","overageDisabledReason":"out_of_credits","isUsingOverage":false,
   "unifiedWindows":{"five_hour":{"utilization":0.06,"resetsAt":1790876400},"seven_day":
   {"utilization":0.46,"resetsAt":1791216000}}}`.
   Command: `echo "Reply with the single word: ok" | claude -p --output-format stream-json
   --verbose --model <id> --safe-mode --no-session-persistence --tools ""` (kept:
   `%TEMP%\mpc-probe\stream.out`, `fable.out`, `opus.out`).
   CODE (the event's schema): `status` ∈ allowed | allowed_warning | **rejected**; `rateLimitType`
   ∈ five_hour | seven_day | seven_day_opus | seven_day_sonnet | seven_day_overage_included |
   overage; `resetsAt` unix seconds. A rejected run therefore carries the limit's TYPE (which
   model, or all) and its reset as an EPOCH — per limit, hence per model.
5. **Result document, exit code — CODE/DOC, not RUN.** The result keeps `subtype:"success"`,
   `is_error:true`, `result:` the sentence, and may carry `api_error_status` (429); the assistant
   message carries `error:"rate_limit"` (DOC, Agent SDK `SDKAssistantMessage.error`). The exit
   code was not observed; the cycle must not depend on it (it already does not). stderr was
   empty in all three runs.

**What this means for the chain (the design fact the card could not know):** lowering the model
helps only when the limit is a MODEL's. Type → what is limited:
`seven_day_overage_included` → `claude-fable-5-1` only; `seven_day_opus` → `claude-opus-5-5` only;
`seven_day_sonnet` → `claude-sonnet-5-5` only; `five_hour`, `seven_day`, `overage` → **all three**
(no lowering: mark all, go straight to the existing wait — no doomed second and third start and no
false "model düşürüldü" line); type unknown (text only) → the model that ran, and the chain finds
out the rest the honest way.

## Fact (ii) — the two percentages: a real, script-readable source EXISTS

- **RUN.** `rate_limit_info.unifiedWindows` (above): `seven_day.utilization` = the week of ALL
  models, `seven_day_overage_included.utilization` = the **Fable** week ("Fable limit" in the
  tool's own table; the `/usage` screen titles it "Current week (Fable)"), `five_hour` = the
  5-hour session. Fractions 0–1 with `resetsAt` epochs. Today: **Fable 81 % (warning; resets
  2026-10-05T16:00:00Z), all models 46 % (same reset), session 6 % (resets 17:40Z).**
- Mapping for the contract: `limits.fable.used_pct = round(100 × seven_day_overage_included.utilization)`,
  `limits.all.used_pct = round(100 × seven_day.utilization)`. This is the tool's own number in
  the tool's own unit conversion (its status line does exactly this), not a computed percentage.
- **Limits of the source, said plainly.** (a) The Fable window appears ONLY in events of runs
  that ran on Fable (the Sonnet and Opus runs did not carry it): `fable.used_pct` is null until
  a lead/inspector run on Fable has finished, and is then as old as that run. (b) Windows the
  account does not have are absent (no `seven_day_opus` here). (c) The schema marks
  `unifiedWindows` `@internal`: it can change with a tool update — an absent field is null and
  the page says "bilinmiyor"; never a guess. (d) A value is dropped to null once its own
  `resetsAt` has passed (it describes a window that no longer exists).
- **Not the source:** the status line. DOC + CODE: its `rate_limits` has only `five_hour` and
  `seven_day` (no Fable window), exists only in an interactive session after the first response,
  and a `-p` run draws no status line. `claude -p "/usage"` and the OAuth usage endpoint were not
  used: the first is an interactive screen, the second would mean reading the owner's credential
  (rejected by the integrator's rules).

## Fact (iii) — `--fallback-model`, and a second, silent substitution

- **`--fallback-model <a,b>` exists** (`claude --help`; DOC model-config "Fallback model chains").
  It switches when the model is **overloaded / unavailable / non-retryable server error**; DOC,
  verbatim: "Authentication, billing, **rate-limit**, request-size, and transport errors … never
  trigger a switch". It lasts one turn, retries the primary next turn, is capped at three, and
  the only sign is a notice. So it (1) does not fire on the usage limit at all, (2) would hide a
  lowering inside a run. **Do not pass it.** Ours differs in all three things asked: it fires on
  the limit, it is per task, and it is written down.
- **A built-in Fable substitution exists — CODE, undocumented.** When Fable needs usage credits
  (its weekly limit reached) and a host can answer a consent dialog, the tool asks; unanswered or
  declined it switches the session to a non-Fable model and prints
  `{"type":"system","subtype":"model_consent_fallback","originalModel":…,"fallbackModel":…}`
  ("Switched to … for this session · … requires usage credits"); the run then SUCCEEDS on the
  other model. Whether a plain `-p` run reaches this branch could not be decided from the code
  (it needs a dialog host; a bare `-p` appears to have none) and was not provoked. Either way the
  cycle must not find out by luck:
  1. start every run with the environment variable **`CLAUDE_CODE_NO_MODEL_FALLBACK=1`** (CODE:
     the tool then refuses any substitution with `model_substitution_disabled` instead of
     swapping; it is NOT on the documented env-var page — treat as best effort);
  2. **the proof is `modelUsage`**: after every run compare the model that really ran with the one
     asked for. RUN: `modelUsage` named `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-opus-5-5`
     exactly as started; side models appear beside it (`claude-haiku-4-5-20251001` in the
     researcher's runs), so "really ran" = the entry with the largest `costUSD`.
- The content-safety fallback (DOC "Automatic model fallback": Fable/Opus 5.5 → Opus 5 / 4.8) is
  already off for this owner: `~/.claude/settings.json` has `"switchModelsOnFlag": false`, and
  DOC says a flagged request in non-interactive mode then "ends the turn with a refusal".
  The `modelUsage` check covers it if that setting ever changes.
- History: all 84 result documents under `team/reports/*/` ran on `claude-sonnet-5-5` (the tool's
  default before the setting existed) — no silent lowering has happened yet.

## The seam, exactly

`scripts/lib/TeamRun.ps1`
- `Get-TeamRunArguments`: `"--output-format","json"` → `"--output-format","stream-json","--verbose"`.
  Nothing else changes on the command line; never `--fallback-model`.
- `Start-TeamRun`: `$psi.EnvironmentVariables["CLAUDE_CODE_NO_MODEL_FALLBACK"] = "1"`.
- `New-TeamCycleReport`: the run line gains `model düşürüldü: <from> -> <to>`; an inspector that
  waited gets its own line (below).

`scripts/lib/TeamQueue.ps1` (pure, tested without a process)
- `Read-TeamRunResult` reads BOTH shapes: one JSON document (old fakes, old report files) and a
  line stream. In a stream: keep only lines that contain `"type":"result"` or
  `"type":"rate_limit_event"` or `"subtype":"model_consent_fallback"`, parse each alone, and
  check the PARSED `type` — the real result line starts with `{"duration_api_ms"…`, not with
  `{"type"` (RUN), and an assistant line can quote those words. Never `ConvertFrom-Json` the whole
  output: Windows PowerShell 5.1 refuses JSON over 2 MB and a worker's stream is larger
  (this is also why `json --verbose`, one array, was not chosen).
  New fields: `RanModel` (largest `costUSD` in `modelUsage`, "" when absent), `LimitType`,
  `LimitScope` (`model` | `all` | `unknown`), `LimitedModel` (id or ""), `ResetsAt` (from the
  LAST event's `resetsAt` when its status is `rejected`; UTC Z), `Windows` (fable / all / session:
  `used_pct`, `resets_at`, or `$null`), `Substituted` (a `model_consent_fallback` line, or
  `RanModel` is not the model asked for).
  `UsageLimited` = the run is not Ok AND (the last event is `rejected` OR the text matches
  `(?i)hit your (\w+ )?limit|usage limit|limit reached|out of (extra usage|usage credits)`).
  The limit name in the text ("Fable limit", "Opus limit", "Sonnet limit", "session limit",
  "weekly limit") gives the scope when no event came.
- `Get-TeamModelChain` (the three ids, strongest first), `Test-TeamModelId`, `Get-TeamModelRank`,
  `Read-TeamModelSetting` (validation of the contract + the defaults), and
  `Get-TeamRunModel -Role -Setting -Limited -Now -Floor` → the id to start on, or `$null` =
  "wait" (`-Floor` is the inspector's rule: never below the worker's real model).
- `Get-TeamModelsApi` beside the other `*Api` functions: `GET /v1/team/queue/models` through
  `Invoke-TeamApi`; a 404 is "this Cloud Core has no such route" → the file → the defaults.

`scripts/team/cycle.ps1`
- the setting: API → `team/models.json` → defaults; every id through `Test-TeamModelId` before
  `Start-RoleRun` (an unknown id starts nothing — today's regex accepts any model-shaped word).
- `$limitedModels` (id → reset), loaded from and saved to `team/limits.json`
  (`{"models": {"<id>": {"until": "<UTC Z>"|null, "type": "<rateLimitType>", "seen_at": "<UTC Z>"}},
  "windows": {"fable"|"all"|"session": {"used_pct": n, "resets_at": "<UTC Z>", "observed_at": "<UTC Z>"}}}`;
  an entry whose `until` has passed is dropped at read; `until: null` (reset unknown) lives for
  this cycle only — otherwise an unknown reset would bar a model for ever).
- the chain in the two places that handle `UsageLimited` today (the split run, line ~476; the
  batch loop, line ~580): mark what the result says is limited, ask `Get-TeamRunModel` again;
  an id comes back → start the SAME task at once on it (`runCount` and `failed_runs` untouched,
  the lowering appended to `limits.lowered`, at most 20); `$null` → today's `Wait-UsageLimit`
  with the EARLIEST reset among the models that role may use.
- the report entry of a run records `model` (asked) and `ran_model` (`modelUsage`); the
  inspector's floor is the worker entry's `ran_model`.
- `Write-CycleStatus`: each run gains `model`; `limits` as the contract says. `fable.state` /
  `all.state` from `$limitedModels`; `used_pct` from `Windows`, else null. `usage_limit` stays
  (the office page reads it today).

`scripts/tests/lib/fake-claude.ps1`: prints the line stream (init line, one `rate_limit_event`,
the result with `modelUsage` naming `--model`); `PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS=<id,id>` answers
for those ids the REAL sentence and a `rejected` event of the matching type with a reset epoch;
the old `limited` scenario keeps working through the single-document branch.
`scripts/team/fake-team-api.ps1`: `GET`/`PUT /v1/team/queue/models` (+ a switch that makes the
route answer 404).

## Tests to add (beyond the card's acceptance list)

1. `Read-TeamRunResult` over the three REAL lines quoted above (fixtures, verbatim): Fable 81,
   all 46, session 6, resets `2026-10-05T16:00:00Z`; a Sonnet event gives `fable = $null`.
2. The six real sentences ("You've hit your Fable / Opus / Sonnet / session / weekly limit",
   "You're out of usage credits") each read as limited with the right scope — all six are RED
   on today's regex.
3. A `rejected` `five_hour` event marks all three models: no lowered run is started, the wait
   line appears (RED: a chain that lowers blindly starts two doomed runs).
4. A result line that does not start with `{"type"` and a 3 MB assistant line before it: the
   result is still read (RED for a whole-output `ConvertFrom-Json`).
5. A run whose `modelUsage` names a weaker model than asked: reported as "model düşürüldü (araç)",
   and for an inspector the verdict is NOT taken (the rule "never weaker" holds against the tool
   too). This one goes past the card's acceptance — the lead decides whether it rides here.
6. `Get-TeamRunArguments` has `stream-json` + `--verbose` and no `--fallback-model`; `Start-TeamRun`
   sets the variable (the fake logs `$env:CLAUDE_CODE_NO_MODEL_FALLBACK`).
7. `team/limits.json` written by one cycle keeps the next cycle off the limited model until the
   reset, and an expired entry is ignored.

## Footprint (measured where it could be)

- Output size: a trivial run's stream is 5–6 lines, 6 KB (measured). A real worker run's stream
  carries every tool result: ESTIMATED 1–20 MB held in memory by `ReadToEndAsync` until the run
  ends (method: today's result documents are ~2 KB and a worker reads ~300 k cached tokens ≈ 1 MB
  of text per pass). To keep `team/reports/<cycle>/` small, `Complete-RoleRun` should write to
  `<stem>.json` ONLY the result line (the same document as today — every reader keeps working)
  and the event lines to `<stem>.limits.json`; the raw stream is kept (last 200 KB) only when no
  result line was found.
- CPU: one substring test per line, three small parses per run. No new process, no network.
- The three probe runs cost 0.14 USD-equivalent on Fable and cents on the others.

## Risks

- The rejection shape is CODE/DOC, not RUN. First real proof is close: **Fable is at 81 % of its
  week** with four days to the reset — the lead and inspector runs on Fable will very likely be
  the first real lowering. Until then evidence is PROVEN_AUTOMATED against real-format fixtures.
- `unifiedWindows` and `CLAUDE_CODE_NO_MODEL_FALLBACK` are undocumented; the tool updates itself
  (binary dated 2026-09-30). Absent field → null / "bilinmiyor"; the `modelUsage` check does not
  depend on either.
- `fable.used_pct` is only as fresh as the last Fable run (contract has no `observed_at`; the
  local file keeps it — the lead may want it in the contract later, with the other two cards).
- A session limit (5 h) hit with fallback on now WAITS at once instead of trying lower models:
  correct by the tool's types, but it is a behaviour the card's text does not spell out.

## Rollback

`Get-TeamRunArguments` back to `"--output-format","json"`, drop the environment line: the
single-document branch of `Read-TeamRunResult` is still there, the chain then never sees a
`rejected` event and falls back to the text match; `"fallback": false` in the setting restores
today's wait without a code change. `team/limits.json` can be deleted at any time.

## THIRD_PARTY_COMPONENTS.md entry text (for the lead; no new dependency)

```
### Claude Code CLI as the team cycle's runner (2026-10-01, model-policy-cycle) - no new dependency

scripts/team/cycle.ps1 starts `claude -p --output-format stream-json --verbose --model <id>` with
CLAUDE_CODE_NO_MODEL_FALLBACK=1 and never passes --fallback-model (it does not fire on usage
limits and would lower a run silently). Read from the output, observed on 2.1.285: the result
line (`result`, `is_error`, `total_cost_usd`, `modelUsage` = the model that really ran) and
`rate_limit_event` (`status`, `rateLimitType`, `resetsAt`, `unifiedWindows.{five_hour,seven_day,
seven_day_overage_included}.utilization`). `unifiedWindows` and the environment variable are
undocumented and may change with a tool update: an absent field is shown as "bilinmiyor", never
estimated. Proprietary tool under Anthropic's terms, already the development backend; nothing is
vendored; it talks only to Anthropic, as before. Not used: the status line's rate_limits
(interactive only, no Fable window), the OAuth usage endpoint (credential access).
```

## For the lead at merge

- `docs/THIRD_PARTY_COMPONENTS.md` (entry above), `docs/DECISIONS.md` (addendum from
  `team/plans/model-policy-cycle-adr.md`), `.gitignore`: decide whether `team/limits.json` (and
  `team/status.json`, not ignored today) are machine-local.
- The contract is untouched. Two things the other two cards should know: `limits.all` is the
  WEEK of all models (the 5-hour session window is a third number the contract has no place for),
  and `fable.used_pct` is null until a Fable run has finished.
- `team/models.json` in the tree lacks `fallback` and `updated_at`; the defaults fill them.

Sources: `claude --help`; https://code.claude.com/docs/en/cli-reference ;
https://code.claude.com/docs/en/model-config (Fallback model chains, Automatic model fallback) ;
https://code.claude.com/docs/en/statusline (rate_limits) ;
https://code.claude.com/docs/en/agent-sdk/typescript (SDKAssistantMessage.error, SDKRateLimitEvent) ;
https://code.claude.com/docs/en/env-vars (the variable is absent there).
