# ADR (no number yet): the roadmap feeds the queue - `scripts/team/feed.ps1`

Status: accepted by the worker of `lead-roadmap-feeder`; the lead numbers it and moves it into
`docs/DECISIONS.md` at merge time. Implements ADR-0214 addendum 8 (and the "feeder" of
addendum 9); narrows nothing.

## Context

The queue was fed by the lead in a chat session. When it ran dry at night, eight seats sat
idle until a person wrote cards. The owner, 2026-10-01: "hiçbir ajan mümkün olduğunca
durmasın, mümkün olduğunca roadmap'ten ilerleyelim ve araştırmacının yeni fikirleri
onaylanırsa bu fikirler roadmap'e eklensin."

## Decision

`scripts/team/feed.ps1` (decisions in `scripts/lib/TeamFeed.ps1`) is a step the scheduled task
runs BEFORE `cycle.ps1`, on the same store (files, or `-QueueUrl`/`-QueueToken`).

1. **Runnable** = state `approved`, `assigned`, `returned`, `in_progress` or `inspecting`
   AND no unmet dependency (`Get-TeamUnmetDependencies`). With `-MinRunnable` (3) or more the
   script does nothing: no lock, no file, one line on stdout.
2. **One lead run** (`.claude/agents/lead.md`, the model `team/models.json` names for the
   lead; tools = the role file's minus `Agent`, `Bash`, and `Edit` unless an idea row is to
   be written) is asked for at most `-MaxNew` (3) cards in ONE file,
   `team/plans/feed-<date>-<n>.json`. The prompt carries the roadmap's rows as the script
   reads them, "The limits, stated once", "The order", every task of the queue (id, state,
   title) and the areas in work.
3. **The script judges, whole or nothing** (`Test-TeamFeed`). The cards go through the judge
   of the lead's split, `Test-TeamSplit` - reused, not forked: fields, a free id, no area of
   a task in work, no shared file, no main. The feeder adds: at most `-MaxNew`; a title no
   task has; `roadmap_row` IS a row of `docs/ROADMAP.md`. Accepted cards are appended as
   `approved`, reason `roadmap: <row>; fed by the lead run <date>`, `created_at` one second
   apart so the lead's order is the queue's order (the queue is ordered by `created_at`).
4. **What a row is.** Read from "## The JARVIS target": the first cell of each row of the
   JARVIS table (and its bold part; not the row whose state says NEVER), the bold title of each item of "The order", the
   section's other headings ("Definition of done", "How it is built from here"), the bold
   name of each approved idea. A card quotes one exactly; ONE note in brackets may follow
   ("browser-use, anywhere (order 2b, ADR-0213)") - that is how the queue's cards are written
   today, and a test holds eleven of them to the real ROADMAP.md.
5. **What needs the owner is never a task.** The lead run marks such an item `needs_owner`
   with one sentence (a new dependency or account, a paid service, an irreversible or
   production action, a roadmap row that does not exist). The script queues it
   `awaiting_owner`, without an area, WITH a proposal file it writes itself
   (`team/proposals/<date>-feed-<id>.md`). The proposal is not decoration: an approved task
   with neither an area nor a proposal is moved to `assigned` by the cycle and breaks the
   queue's protocol for every cycle; with a proposal it is the lead's to split.
   A card whose row does not exist and is NOT marked is refused with the whole file.
6. **Approved ideas -> roadmap.** For a task that is `done`, has a `proposal`, a reason
   starting "sahip onayladı", and whose proposal path is nowhere in ROADMAP.md, the same run
   adds one row to the "Approved ideas" table. `Test-TeamFeedRoadmapEdit` accepts only: every
   old line still there in order; new lines inside that table; five cells, a date first, no
   empty cell; each row names exactly one asked idea's proposal path; no idea twice. The
   script then commits `docs/ROADMAP.md` alone (`git commit -m ... -- docs/ROADMAP.md`) on the
   branch the checkout is on. On `main`, on a detached HEAD, or when ROADMAP.md has
   uncommitted changes, the row is not asked for at all and the report says why. No push.
7. **A run that left its file is not trusted.** `git status` + a sha256 of every listed file
   is taken before and after the run (the main checkout is rarely clean, so "dirty" is not
   the test - the difference is). Any path that differs, other than the feed file (and
   ROADMAP.md when a row was asked for), refuses the WHOLE run: nothing queued, nothing
   committed, ROADMAP.md put back byte for byte. The stray file itself is left as found and
   named in the report - in a checkout a person also works in, the script cannot tell a stray
   write from that person's work, and the working tree is never reset.
   The two verdicts are otherwise independent: a refused feed file does not stop a sound
   idea row from being committed, and a missing idea row does not stop sound cards.
8. **The same stops as the cycle.** The team lock is taken as cycle `feed-<date>` and
   released in `finally` (other machine's fresh lock: exit 3; stale or dead: taken over, said
   so). `team/stop.flag`: nothing starts and the flag is LEFT - it is the cycle's to remove.
   Usage limit: waited out and asked once more when the tool says when it lifts, else stop.
   `-DryRun` prints the plan and writes nothing. Report: `team/reports/feed-<date>.md`
   (Turkish, a section per lead run that was STARTED; posted to the store in API mode).
9. **No run, no report.** When nothing was started - the seats are full, `team/stop.flag`, a
   lock somebody holds (read, or lost in the race of the API's acquire) - the feeder says so
   on standard output and neither writes nor posts a report. The Onay Merkezi shows the
   newest report in the store; a "the lock is held" section posted every 30 minutes while a
   cycle runs took the place of that cycle's report (inspector, on the real routes).
   A run that fails or is killed at its deadline is not trusted either, whatever it left on
   disk: a valid feed file written before the failure is not read.

## Consequences / what is deliberately not here

- The feeder cannot see whether an item "needs the owner"; it relies on the lead's mark plus
  the checks a script can make (row exists, area rules). The inspector's first approved feed
  card is the real test of the prompt.
- Idea rows are written only when a lead run happens, i.e. when the queue is low (the card's
  letter). With a full queue an approved idea waits for the next low moment.
- An idea the CYCLE splits ends with reason "bölündü: ..." - it no longer starts with "sahip
  onayladı", so step 6 does not see it. Keeping the owner's word on that task is the cycle's
  or the Onay Merkezi's (outside this task's area).
- On today's queue seed three ideas of 2026-09-30 (`idea-2026-09-30-anlati-satiri`,
  `-bulutta-yurutme`, `-gorev-dongusu-bulutta`) match step 6: they became items 2b/2c of "The
  order" and were never rows of "Approved ideas". The first real run will ask for their rows.
- A lead run every 30 minutes while the roadmap's next item cannot be cut (an empty list
  each time) is not throttled here.
- The live status (Ofis page) is not written by the feeder: the lead's seat shows nothing
  while the feed run works.
- The feeder's roadmap commit does not touch `docs/HANDOFF.md` (the lead's file).
- The LEAD wires it: the call in the scheduled task (`scripts/team/register-nightly.ps1`,
  before `cycle.ps1`, same `-QueueUrl/-QueueToken`), `scripts/tests/team-feed.tests.ps1` in
  `scripts/quality-gate.ps1` and in `.github/workflows/ci.yml` beside `team-cycle.tests.ps1`.
