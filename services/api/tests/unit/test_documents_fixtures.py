"""Fixture integrity gate (task brief): the oracle cannot drift from the fixtures
unnoticed. Every ``truth.json.files[]`` entry exists with the recorded ``size`` and
``sha256``, and every ``expected/*.extract.json`` names a file ``truth.json`` also knows.
"""

from __future__ import annotations

import hashlib
import json

from tests.documents_support import EXPECTED_DIR, FIXTURES_DIR, load_expected, load_truth


def test_every_truth_file_exists_with_recorded_size_and_sha256() -> None:
    truth = load_truth()
    assert truth["files"], "truth.json lists no files"
    for entry in truth["files"]:
        path = FIXTURES_DIR / entry["path"]
        assert path.is_file(), f"missing fixture: {entry['path']}"
        data = path.read_bytes()
        assert len(data) == entry["size"], f"{entry['path']}: size drifted"
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], (
            f"{entry['path']}: sha256 drifted"
        )


def load_expected_from_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_expected_extract_names_a_file_truth_json_knows() -> None:
    truth_paths = {e["path"] for e in load_truth()["files"]}
    expected_files = sorted(EXPECTED_DIR.glob("*.extract.json"))
    assert expected_files, "no expected/*.extract.json fixtures found"
    for expected_path in expected_files:
        data = load_expected_from_file(expected_path)
        assert data["path"] in truth_paths, f"{expected_path.name} names an unknown file"


def test_every_truth_file_has_a_matching_expected_extract() -> None:
    for entry in load_truth()["files"]:
        expected = load_expected(entry["path"])
        assert expected["path"] == entry["path"]
        assert expected["kind"] == entry["kind"]
        assert len(expected["blocks"]) == entry["blocks"]
