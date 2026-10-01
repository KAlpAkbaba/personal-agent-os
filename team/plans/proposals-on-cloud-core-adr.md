# ADR (unnumbered - the lead numbers it) — An idea's text lives in the team's store, and the owner decides while a cycle runs (2026-10-01)

Task: `proposals-on-cloud-core`. Amends ADR-0222 ("the Onay Merkezi's proposal text still
comes from `team/` on the machine serving the API") and ADR-0217 (the `cycle_running` refusal).

## Context

1. Since 2026-10-01 the team's state is in the Cloud Core's PostgreSQL. There is no `team/`
   folder there, so an idea in the Onay Merkezi showed its title and nothing to read.
2. Owner, 2026-10-01, three ideas waiting and a cycle running: "neden onaylayamıyorum?" The
   decision was refused while a cycle held the lock - and the cycle now runs all day
   (ADR-0214 addendum 6), so the owner could almost never decide.

## Decision

**Proposals.** `TeamStore` gains `put_proposal(name, text)` / `read_proposal(name)`.
`DbStore` keeps a row `kind='proposal'`, `key=<file name>`, `doc={'text': ...}` in the existing
`team_state` table - no new table, no migration. `FileStore` writes and reads
`team/proposals/<name>`. One rule for the name on both stores:
`^[a-z0-9][a-z0-9._-]{1,120}\.md$` (matched whole - `$` alone accepts a trailing newline) AND
at most 80 characters, the width of `team_state.key`, read from the model. A longer name is
refused (422), never cut: a cut name is another proposal's key. The text is a string of at
most 200 000 characters.

Two more rules, both from the inspector's break pass (2026-10-01), both on BOTH stores so a
body is never good on one machine and a 500 on the other:

- **A text neither store can keep is refused (422).** U+0000: PostgreSQL's JSONB has no such
  character (`UntranslatableCharacter`, a 500 on the Cloud Core) while SQLite and a file keep
  it - the addendum-4 shape. Half of a surrogate pair: not UTF-8, so the file store cannot
  write it either. The cycle report (`put_report`) had the same hole and has the same rule.
- **A Windows device name is refused (422).** A name whose part before the first dot is
  `con`, `prn`, `aux`, `nul`, `com0`-`com9` or `lpt0`-`lpt9` is a device on the machine that
  runs the file store (`nul.md` is the null device: `os.replace` onto it fails; `com1.md`
  opens a serial port). Refused in `proposal_name_problems`, and for report names too, rather
  than turning the file store's `OSError` into `Invalid`: the database store would otherwise
  keep a name the home PC cannot.

`POST /v1/team/queue/proposals` (owner session; body `{name, text}`, no other field) answers
`{ok: true}`; a second post of the same name replaces the text; anything else is 422 and
nothing is written. This contract is binding with `researcher-every-cycle`, whose `cycle.ps1`
posts each proposal file.

`GET /v1/team/approvals` reads `proposal_text` through the store: the task's `proposal`
`team/proposals/<name>` -> `read_proposal(<name>)`. The file under `team/` is the fallback of
the FileStore only (a hand-written file, a name the route would refuse, a path under `team/`
that is not a proposal). On the DbStore a file on the serving machine is never shown: the two
machines must read the same text. The listing still cuts the text at 20 000 characters
(unchanged); the store keeps all of it.

**Decisions while a cycle runs.** One function, `approvals.decisions_open(store, lock, at)`,
is what the listing says (`decisions_open`, beside `cycle_running`, which stays for
information) and what `decide()` enforces: always true on the DbStore; `not cycle_running` on
the FileStore. The refusal was made for the file store, where the cycle rewrites the whole
queue file at its end. On the database store the cycle writes back only the tasks it changed,
each conditional on the `updated_at` it read, and does not touch a task waiting at a gate; the
decision's own write stays conditional on the `updated_at` it read (a task changed meanwhile
-> 409 `stale_write`), and a cycle holding the older version is refused the same way. Only a
task at one of the two gates can be decided, cycle or not (`not_at_a_gate` otherwise). The
answer now carries `cycle_running` and a `message` saying the decision takes effect in the
next cycle.

## Alternatives rejected

- A `team_proposals` table: a migration and a release step for what one more `kind` holds.
- Truncating a long name to 80 characters: two proposals could share a key.
- Sending the text inside the task (`proposal` as prose): the queue schema and every queue
  read would carry up to 200 000 characters per idea.
- Opening decisions on the FileStore too: the cycle's whole-file rewrite would overwrite them.

## Evidence

Unit (both stores) `tests/unit/test_team_proposals.py`,
`tests/unit/test_team_approvals_while_running.py`; real PostgreSQL (dev stack)
`tests/integration/test_team_proposals_postgres.py`,
`tests/integration/test_team_approvals_postgres.py`.

## Open

- The proposals already waiting in production have their text only on the home PC: they show
  no text until `cycle.ps1` posts them (`researcher-every-cycle`), or the lead posts them once.
- The web page still disables its buttons on `cycle_running`; it must read `decisions_open`
  (`approvals-detail-view`). The Ofis view (`office.py`) is unchanged.
- The ledger event is recorded before the queue write (ADR-0217): a decision refused as
  `stale_write` has left an event for a decision that did not land. That was possible before;
  with decisions open during a cycle it is less rare.
- A request body whose string holds half of a surrogate pair is refused by pydantic before the
  route runs (nothing is written), but the app's validation-error answer then fails to encode
  the echoed input: a 500 instead of a 422, on every route, not only this one. It is outside
  this task's area (the error handler); the store's own rule is what this task tests.
- `put_task` keeps a task document as JSONB: a U+0000 inside a task's string would be refused
  by PostgreSQL the same way. Not measured here; the cycle writes those strings itself.
- `PROVEN_REAL` needs the owner: read an idea's text and approve it with the home PC off and a
  cycle running, after the release.

## For the lead at merge

- The two existing tests the new route and the new rule change
  (`tests/unit/test_team_state.py`: the route is in `read_by_others` with its reason, and
  `researcher-every-cycle` removes the entry when its `cycle.ps1` calls the route;
  `tests/unit/test_team_approvals.py`: the database half of the cycle-holds-the-lock test
  expects the decision applied) are on this branch again: the lead put both files in the area
  and `1df514f0` is cherry-picked. Nothing to do at merge.
- `voice/realtime_sessions/tools_team.py` calls `approvals.list_pending(queue, root)` without
  the store. On the Cloud Core that falls back to the file store and the spoken path gets no
  proposal text: pass the store (the file is outside this task's area).
- The web page reads `decisions_open` (`approvals-detail-view`).
