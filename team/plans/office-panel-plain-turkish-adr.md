# ADR-0241 addendum (lead numbers it): the seat panel speaks the owner's language

**Owner, 2026-10-02 17:40**, with a screenshot of the Ofis panel of a working seat:
"Çalışan kısmının kabul kısmının sonu red gözüküyor, neden hala devam ediyor?" The panel printed
the card's `acceptance` raw (English, written for the worker and the inspector); its last sentence
was a mutation instruction, "... rule removed -> RED.", and he read RED as the task's verdict. Under
"Durum: çalışıyor" it also printed "Durum: in_progress", the queue's own word.

**Decision (page only; no server change, no new API field, no setting).**

1. The task's state is shown once, in Turkish (`taskStateText` in `officeModel.ts`). The raw word
   is never printed outside the fold; an unknown state is shown as it is, never hidden.

   | queue state | panel says |
   |---|---|
   | proposed | önerildi, henüz başlamadı |
   | awaiting_owner | sahibin onayını bekliyor |
   | approved | onaylandı, sırada |
   | assigned | bir çalışana verildi, başlıyor |
   | in_progress | yazılıyor |
   | inspecting | denetleniyor |
   | returned | denetleyici geri gönderdi; yeniden yazılacak |
   | stopped | durdu: Hakim bakacak |
   | merged | birleştirildi, yayın bekliyor |
   | awaiting_release | yayın için sahibin onayını bekliyor |
   | released | yayında |
   | awaiting_real_evidence | yayında; gerçek kullanımda kanıt bekliyor |
   | done | bitti |
   | rejected (not in the schema; kept for older rows) | vazgeçildi |

   `model.test.ts` reads the state enum of `team/queue.schema.json`: a state added there without a
   phrase fails the test.
2. First view, in order: seat name, seat state, "İşin durumu: <Turkish>", the title, "Başladı:
   HH:MM", "Neden: …" (only a reason that does not start with `LEAD`), "Son rapor: <outcome>".
3. `goal`, `acceptance`, `evidence_expected` (shown only when the API sends it - today it does not)
   and a `LEAD …` reason sit inside ONE closed native `<details>` titled "Ajanlar için yazılmış kart
   metni (İngilizce, teknik)", whose first line says RED / GREEN / PASS / FAIL there are the agents'
   test instructions, not the task's result.

**Why folded, not removed.** The text is the contract the worker and inspector are held to; the
lead and the owner (when he asks why something came back) need it whole and selectable. Folding
keeps it one click away without being the first thing the owner reads. A native `<details>` needs
no state hook, is keyboard reachable, and survives the 5-second refresh closed.

Unchanged: the seat-state line, the several-runs list, the report block, branch, sha, approvals;
a seat with no task renders byte-equal to before (literal in `panel.test.tsx`).
