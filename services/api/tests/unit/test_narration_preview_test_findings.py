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
