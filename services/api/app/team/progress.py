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


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def progress(root: Path, *, as_of: str | None) -> dict[str, Any]:
    """The strip's answer for the tree at ``root``; ``as_of`` is the release sha (or ``None``)."""
    roadmap = _read(root / ROADMAP)
    matrix = _read(root / MATRIX)
    return {
        "jarvis": parse_jarvis(roadmap) if roadmap is not None else None,
        "order": parse_order(roadmap) if roadmap is not None else None,
        "v1": parse_matrix(matrix) if matrix is not None else None,
        "rule": RULE,
        "as_of": as_of,
    }


__all__ = [
    "MATRIX",
    "ROADMAP",
    "RULE",
    "parse_jarvis",
    "parse_matrix",
    "parse_order",
    "percent",
    "progress",
]
