# ADR draft: the test team's 'yanlis-duyulan' findings on POST /v1/narration/preview

Task: test-bulgulari-yanlis-duyulan-20261007 (cycle d20261007). Status: proposed. Return 3:
the area was widened to `narration/routes.py`, `narration/normalizer.py` and the test file;
the fix is on this branch.

## Re-run (staging sha d74a8daa83389d87a3ad68e1177e99e802e816b3)

Source: round t-w10071102, job tj-t-w10071102-1 (tester-1), improvised scenarios
`tj1-imp-turkce-metin` and `tj1-imp-okunus` under K:/AI/tmp-team/testteam/t-w10071102/.
Re-run on 2026-10-07 through `scripts/testteam/run-scenario.ps1` against staging
(K:/AI/tmp-team/testteam/rerun-yanlis-duyulan-20261007/): staging reports sha d74a8daa but
its owner session answers 401, so the runner stopped with state `environment` (no step
reached the route). The re-run was then done in-process on the same code (this branch has no
`services/api` diff against d74a8daa), with `normalize()` and the real router:

| Card | Input | Expected | Now (d74a8daa) | Still failing |
|---|---|---|---|---|
| 272dff2eb1 | text "     ", mode narration | 422 | 200, spoken "" | yes |
| 8d2f45fa9c | text "Merhaba dünya.", mode "şarkı" | 422 | 200, spoken as narration | yes |
| bf67980bf9 | "Dr. Ayşe geldi." (also Prof., Av.) | "doktor Ayşe geldi." | "Dr. Ayşe geldi." | yes |

No case already passes. Note: the tester's other scenario (`tj1-imp-okunus`) expected the
opposite for the first two (200 and spoken ""), and those steps passed; the cards' titles
("reddedilir") and this ADR take the rejecting reading, so that scenario's two steps must be
changed to 422 by the test lead when the fix lands.

## Decision

1. `PreviewRequest.text`: a text that is empty after `str.strip()` is rejected with 422.
   Nothing to speak is a bad request; answering "" hides a client bug.
2. `PreviewRequest.mode` (and the normalizer's `Mode`) becomes
   `Literal["narration", "technical"]`; anything else is 422 instead of silently being
   narration. As built, only `PreviewRequest.mode` is the Literal: the normalizer's `Mode`
   alias stays `str` because `engine.py` and `tables.py` (outside the area) pass a plain
   `str` into `normalize()`; tightening it is part of the follow-up card below.
   `CommandRequest.mode` (`routes.py`, the command route) still accepts any value: that needs
   a separate card (follow-up 2).
3. The narration normalizer expands a title abbreviation only when it is capitalised, ends
   with a dot and a capitalised name follows: `Dr.` -> doktor, `Prof.` -> profesör,
   `Av.` -> avukat (`Doç.` -> doçent, `Op.` -> operatör may join the same table). Technical
   mode is unchanged. A bare `Av`, `Avrupa`, lower-case `av.` and a title with no name after
   it stay as written.

## Evidence

The red tests went in byte for byte from the former Appendix A as
`services/api/tests/unit/test_narration_preview_test_findings.py` (sha256 b27ba215...4ee2,
the same as the file removed in 1557543e). 21 cases:

| Run | Result |
|---|---|
| d74a8daa code (before the fix) | 15 failed, 6 passed |
| fix | 21 passed |
| M1 blank-text validator off (`if False:`) | 3 failed (whitespace) |
| M2 `PreviewRequest.mode: str` again | 4 failed (unknown mode) |
| M3 title expansion off | 8 failed (titles, normalizer + route) |
| M1+M2+M3 (the fix fully undone) | 15 failed, 6 passed |
| restored from backup copies, sha256 of both files equal before/after | 21 passed |

The six guards (two known modes; `Av`, `Avrupa'ya`, lower-case `av.`, lone `Dr.`) stayed
green in every run. Every unit test file importing `app.narration` or calling the preview
route: 866 passed.

## Follow-ups (for the lead)

1. Test lead: the scenario `tj1-imp-okunus` expects 200 (and spoken "") for its first two
   steps ("bilinmeyen mod yanıtı", "boşluk metin ne okunur"); with this fix those answers are
   422, so the two steps must be changed to expect 422.
2. Separate card: `CommandRequest.mode` accepts any value too, and the normalizer's `Mode`
   alias (with `engine.py`/`tables.py`) can become the same Literal.
