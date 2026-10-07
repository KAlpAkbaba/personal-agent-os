# ADR draft: the test team's 'yanlis-duyulan' findings on POST /v1/narration/preview

Task: test-bulgulari-yanlis-duyulan-20261007 (cycle d20261007). Status: proposed. The fix is
not written yet: it needs files outside this card's area (ALAN_ISTEGI in the worker report).

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
   narration. `CommandRequest.mode` is left for a separate card (not in these findings).
3. The narration normalizer expands a title abbreviation only when it is capitalised, ends
   with a dot and a capitalised name follows: `Dr.` -> doktor, `Prof.` -> profesör,
   `Av.` -> avukat (`Doç.` -> doçent, `Op.` -> operatör may join the same table). Technical
   mode is unchanged. A bare `Av`, `Avrupa`, lower-case `av.` and a title with no name after
   it stay as written.

## Evidence

The red tests are kept below (Appendix A), not on the branch: the card's area is this ADR
only, and the inspector sent the first return back for carrying the test file outside it.
When the area is widened (ALAN_ISTEGI in the worker report), the appendix goes in byte for
byte as `services/api/tests/unit/test_narration_preview_test_findings.py`, together with the
fix. On d74a8daa it ran 21 cases: 15 RED (3 whitespace, 4 unknown mode, 8 title) and 6
guard cases green (the known modes still answer; words that are not titles stay as written).
Mutation proof comes after the fix.

## Appendix A: services/api/tests/unit/test_narration_preview_test_findings.py

```python
"""Regression tests: the test team's 'yanlis-duyulan' findings on POST /v1/narration/preview.

Round t-w10071102 on staging d74a8daa (tester-1, job tj-t-w10071102-1, improvised scenarios
tj1-imp-turkce-metin / tj1-imp-okunus) found three cases, forwarded as
test-fail-yanlis-duyulan-272dff2eb1 (whitespace-only text answered 200 with spoken ""),
test-fail-yanlis-duyulan-8d2f45fa9c (an unknown mode "şarkı" answered 200 and was spoken as
narration) and test-fail-yanlis-duyulan-bf67980bf9 ("Dr. Ayşe geldi." spoken as "Dr.").
Each case is asserted through the real router (owner session overridden, no pronunciation
lookup, so no database) and, for the abbreviations, through the pure normalizer too.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.identity.dependencies import require_owner_session
from app.narration.normalizer import normalize
from app.narration.routes import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_owner_session] = lambda: None
    # use_pronunciation=False below: the handler never opens a session on this stub.
    app.state.narration = SimpleNamespace()
    return TestClient(app)


def _preview(client: TestClient, **body: object):
    return client.post("/v1/narration/preview", json={"use_pronunciation": False, **body})


@pytest.mark.parametrize("text", ["     ", "\t\n ", "  　"])
def test_whitespace_only_text_is_rejected(client: TestClient, text: str) -> None:
    # test-fail-yanlis-duyulan-272dff2eb1: nothing to speak is a bad request, not "".
    assert _preview(client, text=text, mode="narration").status_code == 422


@pytest.mark.parametrize("mode", ["şarkı", "NARRATION", "", "semantic"])
def test_unknown_mode_is_rejected(client: TestClient, mode: str) -> None:
    # test-fail-yanlis-duyulan-8d2f45fa9c: only "narration" and "technical" exist.
    assert _preview(client, text="Merhaba dünya.", mode=mode).status_code == 422


@pytest.mark.parametrize("mode", ["narration", "technical"])
def test_known_modes_still_answer(client: TestClient, mode: str) -> None:
    resp = _preview(client, text="Merhaba dünya.", mode=mode)
    assert resp.status_code == 200
    assert resp.json()["spoken"] == "Merhaba dünya."


TITLES = [
    ("Dr. Ayşe geldi.", "doktor Ayşe geldi."),
    ("Prof. Mehmet geldi.", "profesör Mehmet geldi."),
    ("Av. Zeynep geldi.", "avukat Zeynep geldi."),
    ("Yarın Dr. Ayşe Öztürk'le görüşeceğiz.", "Yarın doktor Ayşe Öztürk'le görüşeceğiz."),
]


@pytest.mark.parametrize(("text", "spoken"), TITLES)
def test_title_abbreviation_is_spoken_in_full(text: str, spoken: str) -> None:
    # test-fail-yanlis-duyulan-bf67980bf9
    assert normalize(text) == spoken


@pytest.mark.parametrize(("text", "spoken"), TITLES)
def test_title_abbreviation_through_the_route(client: TestClient, text: str, spoken: str) -> None:
    resp = _preview(client, text=text)
    assert resp.status_code == 200
    assert resp.json()["spoken"] == spoken


@pytest.mark.parametrize(
    "text",
    [
        "Av",  # a bare word, no dot: not a title
        "Avrupa'ya gittik.",
        "Bugün av. sezonu açıldı.",  # lower case: not the title
        "Dr.",  # no name follows
    ],
)
def test_title_rule_leaves_other_words_alone(text: str) -> None:
    assert normalize(text) == text
```
