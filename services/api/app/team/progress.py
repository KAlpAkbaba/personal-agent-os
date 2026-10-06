"""The Ofis's İlerleme strip: how much of the roadmap is done, read from the documents themselves.

The owner, 2026-10-03: "roadmap'e göre projenin ortalama yüzde kaçı tamamlandı, yüzde kaçı kaldı
göremiyorum." Three numbers, each from the repository tree this process serves (never typed in):

- ``jarvis``: the table "What JARVIS does, and where this system stands" in ``docs/ROADMAP.md``.
  HAVE weighs 1, PARTIAL 0.5, MISSING 0; the NEVER / HARDWARE row is not a goal and is not
  counted. A row whose state cannot be read is ``unknown``, named, and counted as not done.
- ``order``: the binding order's numbered steps. A step's state is its leading marker
  (``**DONE**`` / ``**PARTIAL**``, office-progress ADR); a step with no marker is ``done`` when
  its own line says DONE, otherwise ``open``. done 1, partial 0.5, open 0.
- ``v1``: the v1.0 feature matrix, every table row with a numeric ID: IMPL ``DONE`` is done,
  PROOF ``PR`` (PROVEN_REAL) is proven in reality; an empty IMPL is ``unknown``, not done.

A percent is the nearest whole number and an exact half rounds DOWN - progress is never rounded
up from a tie. A document that is missing or has no such section makes its part ``None``.
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Any

ROADMAP = Path("docs/ROADMAP.md")
MATRIX = Path("docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md")

RULE = (
    "JARVIS hedefi: Var 1, Yarım 0,5, Yok 0; 'asla / donanım' satırı sayılmaz. "
    "Sıralı plan: bitti 1, yarım 0,5, açık 0. v1.0 listesi: IMPL DONE yapıldı, "
    "PROOF PR gerçekte kanıtlı. Okunamayan satır yapılmamış sayılır; yarım yüzde aşağı yuvarlanır."
)

JARVIS_HEADING = "### What JARVIS does"
ORDER_HEADING = "### The order"

_WEIGHT = {"have": Fraction(1), "partial": Fraction(1, 2), "missing": Fraction(0)}
_STEP_WEIGHT = {"done": Fraction(1), "partial": Fraction(1, 2), "open": Fraction(0)}
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_STEP = re.compile(r"^(\d+)\.\s+(.*)$")
_MARKER = re.compile(r"^\*\*(DONE|PARTIAL)\*\*\s*")


def percent(score: Fraction | int, total: int) -> int | None:
    """``score / total`` as a whole percent, an exact half rounded down; ``None`` for no rows."""
    if total <= 0:
        return None
    value = Fraction(score) * 100 / total
    whole = value.numerator // value.denominator
    return whole + 1 if value - whole > Fraction(1, 2) else whole


def _section(text: str, heading: str) -> list[str] | None:
    lines = text.splitlines()
    for at, line in enumerate(lines):
        if line.startswith(heading):
            body = []
            for rest in lines[at + 1 :]:
                if rest.startswith("#"):
                    break
                body.append(rest)
            return body
    return None


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _plain(cell: str) -> str:
    return " ".join(cell.replace("**", "").split())


def _jarvis_state(cell: str) -> str:
    first = _BOLD.search(cell)
    word = first.group(1).strip().upper() if first else ""
    if word.startswith("NEVER"):
        return "never"
    return {"HAVE": "have", "PARTIAL": "partial", "MISSING": "missing"}.get(word, "unknown")


def parse_jarvis(text: str) -> dict[str, Any] | None:
    body = _section(text, JARVIS_HEADING)
    if body is None:
        return None
    table = [line for line in body if line.lstrip().startswith("|")]
    rows: list[dict[str, str]] = []
    for line in table[2:]:  # the header and its separator
        cells = _cells(line)
        if len(cells) < 2:
            continue
        rows.append({"name": _plain(cells[0]), "state": _jarvis_state(cells[-1])})
    if not rows:
        return None
    counts = {state: sum(r["state"] == state for r in rows) for state in _WEIGHT}
    counted = [r for r in rows if r["state"] != "never"]
    score = sum((_WEIGHT.get(r["state"], Fraction(0)) for r in counted), Fraction(0))
    return {
        **counts,
        "never": sum(r["state"] == "never" for r in rows),
        "unknown": [r["name"] for r in rows if r["state"] == "unknown"],
        "counted": len(counted),
        "percent": percent(score, len(counted)),
        "rows": rows,
    }


def parse_order(text: str) -> dict[str, Any] | None:
    body = _section(text, ORDER_HEADING)
    if body is None:
        return None
    steps: list[dict[str, Any]] = []
    for line in body:
        match = _STEP.match(line)  # top-level only: sub-steps are indented
        if not match:
            continue
        rest = match.group(2)
        marker = _MARKER.match(rest)
        if marker:
            state = marker.group(1).lower()
            rest = rest[marker.end() :]
        else:
            state = "done" if re.search(r"\bDONE\b", rest) else "open"
        title = _BOLD.search(rest)
        name = title.group(1) if title else rest.split("—")[0]
        steps.append({"n": int(match.group(1)), "title": _plain(name), "state": state})
    if not steps:
        return None
    score = sum((_STEP_WEIGHT[s["state"]] for s in steps), Fraction(0))
    upcoming = next((s for s in steps if s["state"] != "done"), None)
    return {"steps": steps, "percent": percent(score, len(steps)), "next": upcoming}


def parse_matrix(text: str) -> dict[str, Any] | None:
    header: list[str] | None = None
    by_status: dict[str, int] = {}
    by_proof: dict[str, int] = {}
    total = 0
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if cells[0] == "ID":
            header = cells
            continue
        if header is None or not cells[0].isdigit():
            continue
        row = dict(zip(header, cells, strict=False))
        status = row.get("IMPL", "").strip("` ") or "unknown"
        proof = row.get("PROOF", "").strip("` ") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        by_proof[proof] = by_proof.get(proof, 0) + 1
        total += 1
    if total == 0:
        return None
    done = by_status.get("DONE", 0)
    return {
        "total": total,
        "done": done,
        "by_status": by_status,
        "by_proof": by_proof,
        "percent_done": percent(done, total),
        "percent_proven_real": percent(by_proof.get("PR", 0), total),
    }


# ------------------------------------------------------------------ the proof
# (proof-from-test-rounds-and-trials)
#
# The owner, 2026-10-06: "kanıt kısmı neden ilerlemiyor". The v1.0 matrix's PROOF column is a
# document nobody edits per run; the JARVIS rows are proven by what is stored in the Cloud Core:
# a test round's per-row result on staging (scripts/testteam/test-round.ps1 posts it) and the
# owner's trials on the queue's tasks (the Dene list, app.team.trials).

PROOF_RULE = (
    "Staging'de kanıtlı: satırın şu anki yayında koşan son test turu geçti (kalan senaryo yok). "
    "Gerçekte kanıtlı: satırın sahip tarafından karara bağlanan son denemesi 'oldu'. "
    "'Asla / donanım' satırı sayılmaz."
)
ROUND_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")  # test-round.ps1's -Round
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ROUND_KEYS = {"round", "staging_sha", "at", "rows"}
ROW_KEYS = {"row", "passed", "failed", "families"}
ROUND_ROWS_MAX = 100
ROW_NAME_MAX = 300
_TRIAL_DECIDED = ("oldu", "olmadi")  # app.team.trials PASSED / FAILED


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def round_problems(doc: Any) -> list[str]:
    """Why ``doc`` is not a round's proof the store may keep; empty when it is."""
    if not isinstance(doc, dict):
        return ["a round's proof is an object"]
    problems = [f"unknown key {key!r}" for key in sorted(set(doc) - ROUND_KEYS)]
    problems += [f"missing key {key!r}" for key in sorted(ROUND_KEYS - set(doc))]
    if not isinstance(doc.get("round"), str) or not ROUND_ID.fullmatch(doc["round"]):
        problems.append(f"round matches {ROUND_ID.pattern}")
    if not isinstance(doc.get("staging_sha"), str) or not _SHA.fullmatch(doc["staging_sha"]):
        problems.append("staging_sha is a commit sha, 7-40 lower-case hex")
    if not isinstance(doc.get("at"), str) or not _STAMP.fullmatch(doc["at"]):
        problems.append("at is UTC, YYYY-MM-DDTHH:MM:SSZ")
    rows = doc.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= ROUND_ROWS_MAX:
        return problems + [f"rows is a list of 1-{ROUND_ROWS_MAX} rows"]
    for at, row in enumerate(rows):
        where = f"rows[{at}]"
        if not isinstance(row, dict):
            problems.append(f"{where} is an object")
            continue
        problems += [f"{where}: unknown key {key!r}" for key in sorted(set(row) - ROW_KEYS)]
        name = row.get("row")
        if not isinstance(name, str) or not name.strip() or len(name) > ROW_NAME_MAX:
            problems.append(f"{where}.row is a roadmap row name, 1-{ROW_NAME_MAX} characters")
        if not (_count(row.get("passed")) and _count(row.get("failed"))):
            problems.append(f"{where}: passed and failed are whole numbers, 0 or more")
        elif row["passed"] + row["failed"] == 0:
            problems.append(f"{where}: a row no scenario ran on proves nothing")
        families = row.get("families", [])
        if not isinstance(families, list) or not all(isinstance(f, str) for f in families):
            problems.append(f"{where}.families is a list of names")
    return problems


def _norm(text: Any) -> str:
    return _plain(str(text)).casefold().strip(" .:;-—")


def _names_row(row: str, ref: Any) -> bool:
    """``ref`` (a round's row, a task's roadmap_row) names the JARVIS row ``row``: the same
    words, or one is the other's leading words ending at a word boundary - never half a word."""
    a, b = _norm(row), _norm(ref)
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return long_ == short or (long_.startswith(short) and not long_[len(short)].isalnum())


def _same_release(sha: Any, release: str | None) -> bool:
    if release is None:
        return True
    a, b = str(sha).lower(), release.lower()
    return len(min(a, b, key=len)) >= 7 and (a.startswith(b) or b.startswith(a))


def _latest_round(name: str, rounds: list[Any], release: str | None) -> dict[str, Any] | None:
    found: list[tuple[str, str, dict[str, Any]]] = []
    for doc in rounds:
        if round_problems(doc) or not _same_release(doc["staging_sha"], release):
            continue
        passed = sum(r["passed"] for r in doc["rows"] if _names_row(name, r["row"]))
        failed = sum(r["failed"] for r in doc["rows"] if _names_row(name, r["row"]))
        if passed + failed:
            found.append(
                (
                    doc["at"],
                    doc["round"],
                    {
                        "round": doc["round"],
                        "sha": doc["staging_sha"],
                        "at": doc["at"],
                        "passed": passed,
                        "failed": failed,
                    },
                )
            )
    return max(found, key=lambda f: (f[0], f[1]))[2] if found else None


def _latest_trial(name: str, queue: dict[str, Any]) -> dict[str, Any] | None:
    found: list[tuple[str, dict[str, Any]]] = []
    for task in queue.get("tasks", []) if isinstance(queue, dict) else []:
        if not isinstance(task, dict) or not _names_row(name, task.get("roadmap_row", "")):
            continue
        for trial in task.get("owner_trials") or []:
            if not isinstance(trial, dict) or trial.get("verdict") not in _TRIAL_DECIDED:
                continue
            at = str(trial.get("at") or "")
            found.append(
                (
                    at,
                    {
                        "task_id": task.get("id"),
                        "trial_id": trial.get("id"),
                        "verdict": trial["verdict"],
                        "at": at or None,
                    },
                )
            )
    return max(found, key=lambda f: f[0])[1] if found else None


def proof(
    jarvis: dict[str, Any] | None,
    rounds: list[Any],
    queue: dict[str, Any],
    *,
    release: str | None,
) -> dict[str, Any] | None:
    """Each counted JARVIS row's two proofs. Staging-proven: the latest round on ``release``
    (any release when it is ``None``) that ran the row has no failed scenario. Real-proven: the
    row's latest decided owner trial is "oldu". ``None`` when the table could not be read."""
    if jarvis is None:
        return None
    rows = []
    for row in jarvis["rows"]:
        if row["state"] == "never":
            continue
        staging = _latest_round(row["name"], rounds, release)
        trial = _latest_trial(row["name"], queue)
        rows.append(
            {
                "name": row["name"],
                "state": row["state"],
                "staging": staging,
                "staging_proven": bool(staging and staging["failed"] == 0),
                "trial": trial,
                "real_proven": bool(trial and trial["verdict"] == "oldu"),
            }
        )
    staging_proven = sum(r["staging_proven"] for r in rows)
    real_proven = sum(r["real_proven"] for r in rows)
    return {
        "counted": len(rows),
        "staging_proven": staging_proven,
        "real_proven": real_proven,
        "percent_staging": percent(staging_proven, len(rows)),
        "percent_real": percent(real_proven, len(rows)),
        "release": release,
        "rows": rows,
        "rule": PROOF_RULE,
    }


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def progress(
    root: Path,
    *,
    as_of: str | None,
    rounds: list[Any] | None = None,
    queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The strip's answer for the tree at ``root``; ``as_of`` is the release sha (or ``None``);
    ``rounds`` are the stored test rounds' proofs and ``queue`` the team queue (its trials)."""
    roadmap = _read(root / ROADMAP)
    matrix = _read(root / MATRIX)
    jarvis = parse_jarvis(roadmap) if roadmap is not None else None
    return {
        "jarvis": jarvis,
        "order": parse_order(roadmap) if roadmap is not None else None,
        "v1": parse_matrix(matrix) if matrix is not None else None,
        "proof": proof(jarvis, rounds or [], queue or {}, release=as_of),
        "rule": RULE,
        "as_of": as_of,
    }


__all__ = [
    "MATRIX",
    "PROOF_RULE",
    "ROADMAP",
    "RULE",
    "parse_jarvis",
    "parse_matrix",
    "parse_order",
    "percent",
    "progress",
    "proof",
    "round_problems",
]
