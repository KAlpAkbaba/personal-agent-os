"""The collector reads the misheard notebook: a notebook row becomes a proposal for the corpus.

``scripts/voice/collect-stt-corpus.ps1`` read two kinds of dump line ('session', 'audit') and
had never had anything to read from a paid session. The notebook (ADR-0254, table
``misheard_utterances``) holds the sentence the recogniser WROTE in either mode, and - once the
owner has answered - what he meant. These tests run the real script under Windows PowerShell
5.1 on a dump written here, and hold four things: a 'misheard' line is proposed like a
'session' line and carries what the notebook knows; the same sentence is one proposal however
many lines name it, an answer is never lost and two answers are never merged; a dump with no
'misheard' line gives exactly what it gave before; and everything the tool refused before it
still refuses. The corpus file itself is only ever READ.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.voice_corpus import stt_corpus
from tests.voice_corpus.stt_corpus import ORIGIN_DERIVED, ORIGIN_REAL, all_stt_cases

REPO = Path(__file__).resolve().parents[4]
COLLECT_SCRIPT = REPO / "scripts" / "voice" / "collect-stt-corpus.ps1"
CORPUS_FILE = Path(stt_corpus.__file__)

CASES = all_stt_cases()
REAL = [c for c in CASES if c.origin == ORIGIN_REAL]
DERIVED = [c for c in CASES if c.origin == ORIGIN_DERIVED]

#: The CONTRACT's columns, in the table's order (identical in the four misheard-* cards).
CONTRACT_COLUMNS = (
    "id",
    "heard_at",
    "sentence",
    "mode",
    "engine",
    "device_id",
    "band",
    "confidence",
    "reason",
    "resolved_intent",
    "tool",
    "session_id",
    "meant",
    "answered_at",
    "expires_at",
)
#: What a 'misheard' proposal carries from its line, as the line holds it.
CARRIED = ("mode", "engine", "device_id", "reason", "resolved_intent", "band", "confidence")

SENTENCE = "Ekranları kapatın lütfen."
MEANT = "Ofis bilgisayarının ekranını kapat; ışığı söndür, ğüşöçİı."
DEVICE = "7a1d2c3b-0000-4000-8000-00000000d001"


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    candidate = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return str(candidate) if candidate.is_file() else None


needs_powershell = pytest.mark.skipif(
    _powershell() is None, reason="Windows PowerShell is not on this machine"
)


def _collect(*arguments: str) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    assert shell is not None
    return subprocess.run(  # noqa: S603 - a fixed interpreter and this repository's own script
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(COLLECT_SCRIPT),
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _misheard(
    index: int,
    sentence: str,
    *,
    day: str = "2026-10-02",
    meant: str | None = None,
    reason: str = "no_intent",
    mode: str = "paid",
    **columns: object,
) -> dict:
    """One notebook row, as the -ShowQuery SELECT writes it: every CONTRACT column."""
    heard = f"{day}T09:{index % 60:02d}:00+00:00"
    row = {
        "kind": "misheard",
        "id": f"0b5e1a2c-0000-4000-8000-{index:012d}",
        "heard_at": heard,
        "sentence": sentence,
        "mode": mode,
        "engine": "gpt-4o-transcribe" if mode == "paid" else "web-speech",
        "device_id": DEVICE,
        "band": "low",
        "confidence": 0.31,
        "reason": reason,
        "resolved_intent": None,
        "tool": None,
        "session_id": f"5d1c1f5e-0000-4000-8000-{index:012d}",
        "meant": meant,
        "answered_at": f"{day}T10:00:00+00:00" if meant is not None else None,
        "expires_at": f"{day}T09:{index % 60:02d}:00+00:00",
    }
    row.update(columns)
    return row


def _session(index: int, sentence: str, day: str = "2026-10-01", **turn: object) -> dict:
    """One local-mode miss, as the dump's 'session' line holds it."""
    at = f"{day}T21:{index % 60:02d}:00Z"
    return {
        "kind": "session",
        "session_id": f"5d1c1f5e-0000-4000-8000-{index:012d}",
        "provider": "local-router",
        "updated_at": at,
        "last_utterance": {"at": at, "intent": "none", "chat_question": sentence, **turn},
    }


def _run(tmp_path: Path, rows: list[dict]) -> dict:
    dump = tmp_path / "stt-dump.jsonl"
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    dump.write_text("\n".join([*lines, ""]), encoding="utf-8")
    out = tmp_path / "stt-proposals.json"
    before = _sha256(CORPUS_FILE)
    ran = _collect("-DumpPath", str(dump), "-OutPath", str(out))
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert _sha256(CORPUS_FILE) == before  # read, never written
    return json.loads(out.read_text(encoding="utf-8-sig"))


def _only(report: dict) -> dict:
    assert len(report["proposals"]) == 1, report["proposals"]
    return report["proposals"][0]


# ------------------------------------------------------------------- 1, 2: one line


@needs_powershell
def test_an_unanswered_misheard_line_is_one_proposal_carrying_what_the_notebook_knows(tmp_path):
    line = _misheard(1, SENTENCE, resolved_intent="app_open", band="medium", confidence=0.62)
    proposal = _only(_run(tmp_path, [line]))

    assert proposal["rendering"] == SENTENCE
    assert proposal["origin"] == ORIGIN_REAL
    assert proposal["heard_at"] == "2026-10-02"
    assert proposal["times_heard"] == 1
    assert proposal["status"] == "needs_owner_meaning"
    assert proposal["meant"] is None
    assert proposal["meant_also"] == []
    for column in CARRIED:
        assert proposal[column] == line[column], column
    assert proposal["confirms_derived_case"] is None
    # What the corpus case needs, a person maps from the owner's words; the tool never guesses.
    for slot in ("intent", "tool", "application", "device"):
        assert proposal[slot] is None, slot


@needs_powershell
def test_an_answered_line_carries_the_owners_words_letter_for_letter(tmp_path):
    proposal = _only(_run(tmp_path, [_misheard(2, SENTENCE, meant=MEANT, reason="objected")]))

    assert proposal["meant"] == MEANT
    assert proposal["status"] == "owner_answered"
    assert proposal["reason"] == "objected"
    for slot in ("intent", "tool", "application", "device"):
        assert proposal[slot] is None, slot


@needs_powershell
def test_the_utc_day_of_a_row_is_its_day_whatever_offset_the_database_wrote(tmp_path):
    """A database whose session TimeZone is not UTC writes 01:30+03:00 for 22:30 UTC the day
    before: the day the row was heard is the UTC one."""
    line = _misheard(3, SENTENCE, heard_at="2026-10-03T01:30:00+03:00")
    assert _only(_run(tmp_path, [line]))["heard_at"] == "2026-10-02"


# ------------------------------------------------------------------- 3, 4: one sentence


@needs_powershell
def test_one_sentence_in_lines_of_either_kind_is_one_proposal(tmp_path):
    rows = [
        _misheard(1, SENTENCE, day="2026-10-02", meant=MEANT),
        _session(2, SENTENCE, day="2026-09-28"),
        _misheard(3, SENTENCE, day="2026-09-30"),  # unanswered: the answer above must stay
    ]
    proposal = _only(_run(tmp_path, rows))

    assert proposal["times_heard"] == 3
    assert proposal["heard_at"] == "2026-09-28"
    assert proposal["meant"] == MEANT
    assert proposal["status"] == "owner_answered"
    assert proposal["meant_also"] == []


@needs_powershell
def test_two_answers_for_one_sentence_are_both_kept_and_never_merged(tmp_path):
    other = "Salondaki ekranı kapat."
    rows = [
        _misheard(1, SENTENCE),
        _misheard(2, SENTENCE, meant=MEANT),
        _misheard(3, SENTENCE),
        _misheard(4, SENTENCE, meant=other),
        _misheard(5, SENTENCE, meant=MEANT),  # the same answer again is not a third one
    ]
    proposal = _only(_run(tmp_path, rows))

    assert proposal["times_heard"] == 5
    assert proposal["status"] == "owner_answered"
    assert [proposal["meant"], *proposal["meant_also"]] == [MEANT, other]


# ------------------------------------------------------------------- 5: the corpus


@needs_powershell
def test_a_real_rendering_is_skipped_and_a_derived_one_is_a_confirmed_case(tmp_path):
    real, derived = REAL[0], DERIVED[0]
    report = _run(tmp_path, [_misheard(1, real.rendering), _misheard(2, derived.rendering)])

    assert report["skipped"]["already_in_corpus"] == 1
    proposal = _only(report)
    assert proposal["rendering"] == derived.rendering
    assert proposal["confirms_derived_case"] == derived.case_id


# ------------------------------------------------------------------- 6: the report


@needs_powershell
def test_the_report_counts_the_notebook_and_the_counts_add_up(tmp_path):
    rows = [
        _misheard(1, "bir", reason="no_intent", mode="paid"),
        _misheard(2, "iki", reason="no_intent", mode="local", meant="İki."),
        _misheard(3, "üç", reason="tool_failed", mode="paid", tool="app_open"),
        _misheard(4, REAL[0].rendering, reason="objected", mode="local", meant="aynen"),
        _session(5, "beş"),
    ]
    misheard = _run(tmp_path, rows)["misheard"]

    assert misheard["rows"] == 4
    assert misheard["by_reason"] == {"no_intent": 2, "tool_failed": 1, "objected": 1}
    assert misheard["by_mode"] == {"paid": 2, "local": 2}
    assert misheard["answered"] == 2
    assert sum(misheard["by_reason"].values()) == misheard["rows"]
    assert sum(misheard["by_mode"].values()) == misheard["rows"]


@needs_powershell
def test_a_dump_without_the_notebook_counts_none_of_it(tmp_path):
    report = _run(tmp_path, [_session(1, "beş")])
    assert report["misheard"] == {"rows": 0, "by_reason": {}, "by_mode": {}, "answered": 0}


# ------------------------------------------------------------------- 7: nothing else moved

#: A dump of the two kinds the script read before this card, and what it proposed for it then
#: (taken from the script at 75f04e05, before the notebook was read).
_OLD_DUMP = [
    _session(1, "Ekranları kapatın lütfen.", band="low", confidence=0.0, candidates=[]),
    _session(2, "Ekranları kapatın lütfen."),
    _session(3, "hesap makinesini aç.", candidates=[{"intent": "app_open"}]),
    _session(4, REAL[0].rendering),
    _session(5, DERIVED[0].rendering, day="2026-09-30"),
    {
        "kind": "session",
        "session_id": "5d1c1f5e-0000-4000-8000-000000000006",
        "provider": "openai-realtime",
        "updated_at": "2026-10-01T20:05:00Z",
        "last_utterance": {"at": "2026-10-01T20:05:00Z", "intent": "app_open", "band": "high"},
    },
    {
        "kind": "audit",
        "at": "2026-10-01T18:02:11Z",
        "metadata": {"intent": "none", "understanding": {"layer": "none", "band": "low"}},
    },
]


def _old_proposal(rendering: str, times: int, candidates: list, **fields: object) -> dict:
    proposal = {
        "rendering": rendering,
        "origin": "real",
        "heard_at": "2026-10-01",
        "times_heard": times,
        "provider": "local-router",
        "resolved_intent": "none",
        "band": None,
        "confidence": None,
        "candidates": candidates,
        "confirms_derived_case": None,
        "meant": None,
        "intent": None,
        "tool": None,
        "application": None,
        "device": None,
        "status": "needs_owner_meaning",
    }
    proposal.update(fields)
    return proposal


@needs_powershell
def test_a_dump_without_a_misheard_line_gives_the_proposals_it_gave_before(tmp_path):
    report = _run(tmp_path, _OLD_DUMP)

    assert report["proposals"] == [
        _old_proposal("Ekranları kapatın lütfen.", 2, [], band="low", confidence=0.0),
        _old_proposal("hesap makinesini aç.", 1, [{"intent": "app_open"}]),
        _old_proposal(
            DERIVED[0].rendering,
            1,
            [],
            heard_at="2026-09-30",
            confirms_derived_case=DERIVED[0].case_id,
        ),
    ]
    assert report["skipped"] == {
        "already_in_corpus": 1,
        "no_sentence_kept": 1,
        "unreadable_lines": 0,
    }
    assert report["audit"] == {"turns": 1, "by_band": {"low": 1}, "by_layer": {"none": 1}}


# ------------------------------------------------------------------- 8: the refusals


@needs_powershell
def test_every_refusal_still_stands_and_the_corpus_is_never_written(tmp_path):
    dump = tmp_path / "stt-dump.jsonl"
    dump.write_text(
        json.dumps(_misheard(1, SENTENCE, meant=MEANT), ensure_ascii=False) + "\n", "utf-8"
    )
    scratch = tmp_path / "stt_corpus.py"
    scratch.write_bytes(CORPUS_FILE.read_bytes())
    corpus_before, scratch_before, dump_before = (
        _sha256(CORPUS_FILE),
        _sha256(scratch),
        _sha256(dump),
    )
    tracked = REPO / "docs" / "stt-corpus-proposals-misheard-test.json"

    refused = {
        "the corpus": ("-OutPath", str(scratch), "-CorpusPath", str(scratch)),
        "a .py": ("-OutPath", str(tmp_path / "proposals.py")),
        "the dump": ("-OutPath", str(dump)),
        "a tracked path": ("-OutPath", str(tracked)),
    }
    try:
        for name, arguments in refused.items():
            ran = _collect("-DumpPath", str(dump), *arguments)
            assert ran.returncode != 0, (name, ran.stdout)
            assert "refusing" in ran.stdout + ran.stderr, name
        assert not tracked.exists()
    finally:
        tracked.unlink(missing_ok=True)
    assert not (tmp_path / "proposals.py").exists()
    assert (_sha256(CORPUS_FILE), _sha256(scratch), _sha256(dump)) == (
        corpus_before,
        scratch_before,
        dump_before,
    )


# ------------------------------------------------------------------- 9: the query


def _statements(output: str) -> list[str]:
    body = "\n".join(line for line in output.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


@needs_powershell
def test_the_query_is_three_read_only_selects_naming_every_contract_column():
    ran = _collect("-ShowQuery", "-Days", "30")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    statements = _statements(ran.stdout)

    assert len(statements) == 3, statements
    for statement in statements:
        assert statement.lower().startswith("select "), statement
        assert not re.search(r"\b(insert|update|delete|drop|alter|truncate)\b", statement, re.I)
    notebook = next(s for s in statements if "misheard_utterances" in s)
    assert "'kind','misheard'" in notebook
    for column in CONTRACT_COLUMNS:
        assert f"'{column}',{column}" in notebook, column
    assert "interval '30 days'" in notebook
