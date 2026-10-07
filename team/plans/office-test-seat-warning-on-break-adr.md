# ADR draft: a test seat raises the '!' on every result someone must act on

- Task: office-test-seat-warning-on-break (cycle d20261007)
- Context: the owner, 2026-10-07: "1 ve 2'de üzgün ama uyarı vermiyor?" `TestSeatCells`
  (apps/web/app/core/office/officeTestRoom.tsx) drew the '!' only for `failed`; a tester that
  found a breaking point (`broke`) was sad with no mark.
- Decision: the '!' is raised for `failed`, `broke` and `error` (a `WARNS` set); `waiting` and
  `working` raise none. Moods are unchanged (`failed`/`broke` sad, `error` angry).
- Why `error` carries it too: on the software seats the '!' means "something came back that a
  person must act on" (officeModel.ts: a returned seat that is sad or angry). A tester's own error
  (staging unreachable, a broken job) also needs the Test Proje Yöneticisi's action, so the test
  seats follow the same rule: every sad or angry seat raises the '!'. The aria-label already names
  the state, so the mark adds no spoken text.
- Consequence: the mark no longer tells a bug from a breaking point from an own error; the face
  (sad vs angry) and the aria-label do. Reversible in one line.
- Evidence: tests/office/test-room.test.tsx "raises the warning mark on a found bug, a breaking
  point and the tester's own error only"; mutations (drop `broke`; add `working`) both RED.
