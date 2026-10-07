# ADR draft: conversation search folds Turkish letters to ASCII (conversation-search-turkish-fold)

Context: test team round t-manual-20261006e (tester-3): `GET /v1/conversations?q=ISIGI` did not
find a line saying 'ışığı'. The earlier fold (ADR of conversation-transcripts) made the four i's
one letter and lower-cased the Turkish capitals, but kept ş/ğ/ü/ö/ç, so 'isigi' (fold of 'ISIGI')
never met 'isiği' (fold of 'ışığı'), and 'sut' never found 'süt'. The owner types on a phone
keyboard, often without Turkish letters or in capitals.

Decision: both the needle and every stored line fold to one plain spelling - I/İ/ı/i -> i,
Ç/ç -> c, Ğ/ğ -> g, Ö/ö -> o, Ş/ş -> s, Ü/ü -> u, and the circumflexed Â/â, Î/î, Û/û -> a, i, u -
then `lower()`. Each letter maps straight to its folded form, so the order of replacements cannot
matter. The fold lives in the new module `app/conversations/search.py` (`search_fold` for Python,
`search_fold_sql` for the same chain of `replace()` calls in SQL); `service.list_conversations`
calls it. No stored folded column and no migration: the fold runs in SQL over the candidate rows,
as before.

Consequences: a search is accent-blind in Turkish ('sut' also finds 'süt' and 'sut'; 'cam'
finds 'çam' and 'cam'). That is the owner's wish: he would rather see one extra conversation than
miss his. A future stored folded column (for an index) must use this same table.
