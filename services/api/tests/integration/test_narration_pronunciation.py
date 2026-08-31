"""M4 integration: owner pronunciation dictionary CRUD persists (VOICE_SPEC §5).

Explicit owner entries are inspectable, editable and deletable, and win over
inferred entries. Requires the compose stack + schema at head.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


def test_pronunciation_crud_roundtrip(settings: Settings) -> None:
    token = f"TESTTOKEN{id(object())}"
    with TestClient(create_app(settings)) as client:
        # PUT (create) an explicit owner entry.
        created = client.put(
            "/v1/narration/pronunciation",
            json={"token": token, "spoken_form": "test telaffuz", "explicit": True},
        )
        assert created.status_code == 200, created.text
        entry = created.json()
        assert entry["token"] == token
        assert entry["explicit"] is True
        assert entry["confidence"] == 1.0  # explicit -> highest confidence
        entry_id = entry["id"]

        # GET list includes it (inspectable).
        listing = client.get("/v1/narration/pronunciation").json()["entries"]
        assert any(e["id"] == entry_id for e in listing)

        # PUT again (edit) updates the spoken form in place (same token+context).
        edited = client.put(
            "/v1/narration/pronunciation",
            json={"token": token, "spoken_form": "yeni telaffuz", "explicit": True},
        ).json()
        assert edited["id"] == entry_id
        assert edited["spoken_form"] == "yeni telaffuz"

        # DELETE removes it.
        deleted = client.delete(f"/v1/narration/pronunciation/{entry_id}")
        assert deleted.status_code == 200
        after = client.get("/v1/narration/pronunciation").json()["entries"]
        assert all(e["id"] != entry_id for e in after)

        # Deleting a second time is a clean 404.
        assert client.delete(f"/v1/narration/pronunciation/{entry_id}").status_code == 404


def test_pronunciation_applied_in_preview(settings: Settings) -> None:
    token = f"XYZZY{id(object())}"
    with TestClient(create_app(settings)) as client:
        client.put(
            "/v1/narration/pronunciation",
            json={"token": token, "spoken_form": "iks ye zet", "explicit": True},
        )
        try:
            preview = client.post(
                "/v1/narration/preview",
                json={"text": f"{token} kelimesi", "use_pronunciation": True},
            ).json()
            assert preview["spoken"] == "iks ye zet kelimesi"
        finally:
            listing = client.get("/v1/narration/pronunciation").json()["entries"]
            for e in listing:
                if e["token"] == token:
                    client.delete(f"/v1/narration/pronunciation/{e['id']}")
