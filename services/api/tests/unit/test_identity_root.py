"""M9 unit tests: the on-disk identity root (constitution §6 recovery root).

This is the one part of the layer that is deliberately NOT in the database, so
it gets its own tests: the file must round-trip, contain only a hash, be
written atomically with owner-only permissions, and fail *closed* when it is
unreadable or from an unknown schema version.
"""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from app.identity import tokens
from app.identity.root import (
    ROOT_FILENAME,
    FileCredentialRoot,
    InMemoryCredentialRoot,
    RootRecord,
    utc_now,
)


def make_record(credential: str = "pagentos_ok_secret") -> RootRecord:
    return RootRecord(credential_hash=tokens.hash_token(credential), created_at=utc_now())


def test_file_root_round_trips(tmp_path) -> None:
    root = FileCredentialRoot(tmp_path, harden=False)
    assert not root.exists()
    assert root.load() is None
    record = make_record()
    root.store(record)
    assert root.exists()
    loaded = root.load()
    assert loaded is not None
    assert loaded.credential_hash == record.credential_hash
    assert loaded.rotations == 0


def test_file_root_stores_only_a_hash(tmp_path) -> None:
    credential = tokens.new_owner_credential()
    root = FileCredentialRoot(tmp_path, harden=False)
    root.store(make_record(credential))
    raw = (tmp_path / ROOT_FILENAME).read_text(encoding="utf-8")
    assert credential not in raw
    assert tokens.hash_token(credential) in raw
    assert set(json.loads(raw)) == {
        "version",
        "credential_hash",
        "created_at",
        "rotated_at",
        "rotations",
    }


def test_rotation_metadata_is_preserved(tmp_path) -> None:
    root = FileCredentialRoot(tmp_path, harden=False)
    first = make_record("a")
    root.store(first)
    rotated = RootRecord(
        credential_hash=tokens.hash_token("b"),
        created_at=first.created_at,
        rotated_at=utc_now(),
        rotations=1,
    )
    root.store(rotated)
    loaded = root.load()
    assert loaded is not None
    assert loaded.rotations == 1
    assert loaded.rotated_at is not None
    assert loaded.credential_hash == tokens.hash_token("b")


def test_store_leaves_no_temporary_files_behind(tmp_path) -> None:
    root = FileCredentialRoot(tmp_path, harden=False)
    root.store(make_record())
    root.store(make_record("second"))
    assert [p.name for p in tmp_path.iterdir()] == [ROOT_FILENAME]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits are advisory on Windows")
def test_file_root_is_owner_only(tmp_path) -> None:  # pragma: no cover - POSIX only
    root = FileCredentialRoot(tmp_path)
    root.store(make_record())
    mode = stat.S_IMODE(os.stat(tmp_path / ROOT_FILENAME).st_mode)
    assert mode == 0o600


def test_corrupt_root_fails_closed_rather_than_reading_as_absent(tmp_path) -> None:
    root = FileCredentialRoot(tmp_path, harden=False)
    (tmp_path / ROOT_FILENAME).write_text("{not json", encoding="utf-8")
    assert root.exists()
    with pytest.raises(ValueError):
        root.load()


def test_unknown_schema_version_is_refused(tmp_path) -> None:
    root = FileCredentialRoot(tmp_path, harden=False)
    (tmp_path / ROOT_FILENAME).write_text(
        json.dumps({"version": 99, "credential_hash": "x", "created_at": "2026-01-01T00:00:00"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        root.load()


def test_clear_removes_the_root(tmp_path) -> None:
    root = FileCredentialRoot(tmp_path, harden=False)
    root.store(make_record())
    root.clear()
    assert not root.exists()
    root.clear()  # idempotent


def test_describe_never_leaks_the_hash(tmp_path) -> None:
    root = FileCredentialRoot(tmp_path, harden=False)
    record = make_record()
    root.store(record)
    described = root.describe()
    assert described["kind"] == "file"
    assert described["bootstrapped"] is True
    assert record.credential_hash not in repr(described)


def test_in_memory_root_matches_the_file_root_contract() -> None:
    root = InMemoryCredentialRoot()
    assert not root.exists() and root.load() is None
    record = make_record()
    root.store(record)
    assert root.exists() and root.load() == record
    assert root.describe() == {"kind": "memory", "bootstrapped": True}
    root.clear()
    assert not root.exists()
