# ADR (proposed, team-board-talk): the board talks - consultation (danisma) and the test queue on the board

Status: proposed by worker (cycle d20261004); the lead numbers it and moves it into
`docs/DECISIONS.md` at merge. Builds ON the team-board ADR (same store, same client, same
bounds); one ADR for the joined card (team-board-consult + test-slots-on-board).

## Context

The owner, 2026-10-03: "bir ajan diğerine desin ki: ben de şu anda şu iş var, ama şöyle mi
ilerlesem sence yoksa şöyle mi daha doğru olur - ki karşısındaki ajan da vereceği cevapta işi
bilerek cevap versin." And (2026-10-02/03): "bir test yapacakları zaman burada şu an test yapan
var mı yok mu diye birbirlerine yönlendirme, direktif alsınlar." The board carried only free
text; a question arrived without the asker's work, and the machine's test queue (ONAY/BEKLE)
was invisible to the office.

## Decision - consultation

1. **A fifth kind, `danisma`** = `{seat, task, situation (<= 400), options (2 or 3, each <= 200
   named), my_lean (<= 200), files (<= 5 repository paths), topic (kod | test | kural), to (a
   seat or auto), reply_to?}`. The note's `text` IS the situation (every reader already prints
   `text`); a danisma has no separate `text`. Option *i* is named `A:`/`B:`/`C:` by its place;
   an option that names itself must name its own place. All checked in `board.py` (both stores,
   one rule): 1 or 4 options, an over-long field, `..`/drive/absolute/backslash paths, an unknown
   seat, `to` = `herkes` or the asker itself, a consult field on another kind -> 422. The secret
   scan covers every free field (situation, options, lean, files, choice).
2. **The cevap names its choice.** A `cevap` whose `reply_to` is a danisma must carry `choice`
   = one of THAT danisma's letters or `başka: <what>` (<= 200); the reason is the `text` (<= 280).
   A `choice` anywhere else is a 422.
3. **Routing `to: auto`** (server side, at post time; the resolved seat and the reason are kept
   in the note: `to`, `route`). Running seats come from the queue's lock (a live cycle) and the
   status' `runs` (`{role, task}`); a worker run's seat is the seat whose newest note is on that
   task (every run says hello first - the role text already asks for that bilgi); another
   role's run that wrote nothing sits in its role's seat; the lead runs while the cycle is live.
   Order: a `kural` question -> the lead; files outside the asker's area AND every running
   area -> the lead ("alan dışı"); else the running seat whose area shares the most of the
   asker's area + files (directory and glob entries hold the files under them); else the
   running inspector for a `test` question; else the lead. Never the asker, never a seat that is
   not running; nobody to send it to -> `herkes` with the reason. A store that cannot be read
   routes to `herkes` - a consultation never fails for lack of context.
4. **The answerer reads the work.** `GET /v1/team/board/notes` also returns `cards`: for every
   danisma's task its title, the first two lines of goal and acceptance (240 chars each);
   `reply_to=<id>` returns only the answers to one note. `GET /v1/team/board/notes/{id}/context`
   returns the note, the asker's card (+ branch, area) and the answers (404 / 422 for a missing /
   malformed id). `board.ps1 read` prints under each danisma its options, the lean, the files,
   the route, the card lines and the exact answer command; `board.ps1 context -Note <id>` prints
   the card and `git diff --stat main...<asker's branch>` (local, else `origin/`, never throws).
5. **Waiting never blocks.** `board.ps1 wait -Note <id> -Minutes <n>` (0 < n <= 15, refused
   above) polls every 30 s in the FOREGROUND (ADR-0214 addendum 16) and prints `CEVAP <seat>:
   seçim B - ...` or `cevap gelmedi (...); kendi eğiliminle devam et` - exit 0 both ways, and a
   board that cannot be reached is also "cevap gelmedi". The asker then goes on with `my_lean`.

## Decision - the test queue on the board

6. **The queue stays the single truth; the board only reports.** `test-slot.ps1` sends a note
   AFTER `Invoke-TestSlotAsk` / `Complete-TestSlotRun` decided: a fresh ONAY (`take`: "Çalışan 2:
   birim testleri başlatıyorum (ağır), tahmini 6 dk"), a FIRST BEKLE (`wait`: "Çalışan 3: test
   sırası bekliyorum (veritabanı: ...), sıram 1, önümde Çalışan 2") and a finished `run`
   (`free`: "Çalışan 2: birim testleri bitti (6 dk, çıkış 0); sıradaki: Çalışan 3"). Re-asks post
   nothing (20 notes/task/hour stays far). Each note is a `bilgi` with a `slot` snapshot
   `{state, kinds, holders, waiting, estimate_min?}` (validated server-side; holders/waiting are
   seat or role names, at most 20). The estimate is the mean of the last 5 runs of the same
   command (else the same kinds) in `runs.log`; none -> "süre tahmini yok".
7. **Names**: the entry now records the asker's seat (`-Seat`, default `$env:PAGENTOS_TEAM_SEAT`);
   the gate writes as `lead` ("Kapı"); a worker with no seat or a task the board does not know
   posts nothing. **Board down changes nothing**: no address / no token file -> no sender; the
   sender has a 5 s timeout and every failure is swallowed (`Publish-TestSlotBoardNote`); the
   decision, its line and its exit code are computed before and never read the board.
8. **`test-slot.ps1 who`**: "Şu an test yapan: 1, sırada: 2" + `TEST  Çalışan 2 - ... (ağır), 4 dk`
   + `SIRA 1  Kapı - ...` lines, UTF-8, from the store (under the lock, as `status`).
9. **The Ofis** (`apps/web/app/core/office/officeBoard.ts`): `slotSigns(notes, now)` takes the
   NEWEST snapshot (ids sort in writing order; older than 180 min = none) -> `TEST` on each
   holder's desk (estimate from the newest take that named it), `Sıra n` on each waiter (its
   place in the whole line, the gate counted, the gate drawn nowhere); `inspector-2` sits at the
   inspector's desk. `SlotSignBadge` renders it; `fetchSlotSigns` reads 100 notes and is empty on
   any failure.

## What the lead wires at merge (outside this task's area)

- `OfficeView.tsx` / `OfficeScene.tsx`: call `fetchSlotSigns()` beside the office poll and put
  `<SlotSignBadge seat sign/>` on the desk of each seat in the map; one CSS rule for
  `.office-slot-sign` (`office.css`).
- `scripts/team/cycle.ps1`: already gives each run `PAGENTOS_TEAM_SEAT`; `test-slot.ps1` reads it.
- `quality-gate.ps1` keeps calling `Wait-TestSlotGrant` (no notes from the gate yet; a gate
  note needs a seat and a task id and is a follow-up, not needed for the acceptance).
- The role text below into `.claude/agents/{lead,researcher,integrator,worker,inspector}.md`.

## Proposed role text (added to the "Ekip panosu" block of every role file)

```
Danışma (A mı B mi?) - when you stand before a real choice in YOUR task:
  powershell -NoProfile -File scripts\team\board.ps1 post -Seat <your seat> -Task <task> -Kind danisma -Situation '<what I am doing, where I am>' -OptionA '...' -OptionB '...' [-OptionC '...'] -Lean '<A: why>' -Files '<a,b,c>' [-Topic test|kural] [-To <seat>]
  powershell -NoProfile -File scripts\team\board.ps1 wait -Note <id> -Minutes 5
- at most 3 danisma per run; -To auto (the default) finds the running seat that shares your
  files; with "cevap gelmedi" go on with your lean and say so in your report.
- read the board also after each finished step of your plan; a danisma addressed to your seat
  (">> SANA") is answered before you go on: first `board.ps1 context -Note <id>`, then
  `board.ps1 post -Kind cevap -ReplyTo <id> -Choice A|B|C|'başka: ...' -Text '<reason>'`.
- a cevap is advice: the asker still owns its task, its area and its tests; a cevap never
  widens an area, skips a test or touches a protected file.
Test sırası: before a heavy run, `scripts\team\test-slot.ps1 who` shows who tests now; `ask`
posts your take/wait on the board by itself (pass -Seat <your seat> if PAGENTOS_TEAM_SEAT is unset).
```

## Sample transcript (Turkish, as `board.ps1 read -For worker-3` prints it)

```
Ekip panosu: 4 not, 1 tanesi worker-3 koltuğuna.
   09:01 UTC  worker-1 -> herkese  [bilgi] team-board-talk: board.py ve TeamBoard.ps1 üzerinde çalışıyorum.  (no n-...a1)
>> SANA 09:04 UTC  worker-1 -> worker-3  [danisma] team-board-talk: Danışma notunu ekliyorum; seçenekleri nerede doğrulayayım?  (no n-...b2)
        seçenekler: A: board.py içinde, saf fonksiyonla | B: route'ta pydantic modeliyle
        eğilimi: A: iki depo aynı kuralı kullanır
        dosyalar: services/api/app/team/board.py, scripts/lib/TeamBoard.ps1, scripts/team/board.ps1
        yönlendirme: ortak dosya: scripts/lib/TeamBoard.ps1
        kart: Ekip panosunda konuşma
        hedef: Ajanlar bağlamla danışır ('A mı B mi?') ...
        kabul: [team-board-consult] On the dev stack (PostgreSQL): a danisma posted by worker-1 ...
        cevap: board.ps1 post -Kind cevap -ReplyTo n-...b2 -Choice <A/B/C | 'başka: ...'> -Text '<neden>'; bağlam: board.ps1 context -Note n-...b2
   09:06 UTC  worker-3 -> worker-1  [cevap] team-board-talk: seçim A - FileBoard da aynı kuralı okur; route'ta model DbBoard'u atlar.  (no n-...c3, yanıtladığı n-...b2)
   09:07 UTC  worker-2 -> herkese  [bilgi] ofis-ui: Çalışan 2: birim testleri başlatıyorum (ağır), tahmini 6 dk  (no n-...d4)
```

and the two seats sharing the slot (the notes `test-slot.ps1` writes by itself):

```
09:07 worker-2 [bilgi] Çalışan 2: birim testleri başlatıyorum (veritabanı), tahmini 6 dk
09:09 worker-3 [bilgi] Çalışan 3: test sırası bekliyorum (veritabanı: entegrasyon testleri), sıram 1, önümde Çalışan 2
09:13 worker-2 [bilgi] Çalışan 2: birim testleri bitti (6 dk, çıkış 0); sıradaki: Çalışan 3
09:13 worker-3 [bilgi] Çalışan 3: entegrasyon testleri başlatıyorum (veritabanı), süre tahmini yok
```
(`worker-1`'s `board.ps1 wait -Note n-...b2 -Minutes 5` printed `CEVAP worker-3: seçim A - ...`.)

## Cost bound

A danisma is one POST; `context` one GET + one local `git diff --stat`; `wait` at most 31 GETs
(15 min / 30 s). A test-queue note is one POST with a 5 s timeout, at most 3 per heavy run. No
model call.

## Consequences

- An answer arrives with the asker's card and files in front of the answerer; routing follows
  the areas the lead already writes on the cards.
- A worker run that never says hello cannot be routed to (by design: the hello is the seat's
  claim on its task); a danisma then goes to the lead.
- The Ofis signs are as fresh as the last queue event; a holder killed without `run` ending
  leaves its TEST sign until the 180-minute bound or the next queue event.
- Two API processes may each accept a danisma inside the same hourly window (as the team-board ADR).
