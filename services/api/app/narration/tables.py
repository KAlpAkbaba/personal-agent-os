"""Semantic narration of Markdown tables and code/log blocks (VOICE_SPEC §6).

Default behaviour is *interpretation*, not a literal cell dump: a table becomes a
concise Turkish sentence that leads with the notable finding (the warning/error
row), and a code/log block becomes a one-line status summary. Literal reading is
available on request (``literal=True``) for when the owner explicitly asks
"hepsini oku".

All output text is run through the deterministic normalizer so embedded numbers,
units and percentages are spoken correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.narration.normalizer import normalize

# Severity keywords (case-insensitive), Turkish + common English, most severe
# first. Used to classify a status-like cell.
_SEVERITY = [
    (
        "error",
        ("hata", "error", "kritik", "critical", "başarısız", "fail", "failed", "down", "arıza"),
    ),
    ("warn", ("uyarı", "warning", "warn", "dikkat", "degraded", "yavaş")),
    ("ok", ("sağlıklı", "healthy", "ok", "tamam", "başarılı", "success", "up", "aktif", "normal")),
]

_STATUS_HEADERS = ("durum", "status", "state", "sonuç", "sağlık", "health", "seviye", "level")


@dataclass
class TableModel:
    headers: list[str]
    rows: list[list[str]]
    status_col: int | None = None


@dataclass
class TableNarration:
    text: str
    row_count: int
    highlighted: list[str] = field(default_factory=list)


def _cell_severity(value: str) -> str | None:
    low = value.casefold()
    for label, keywords in _SEVERITY:
        if any(k in low for k in keywords):
            return label
    return None


def parse_markdown_table(block: str) -> TableModel | None:
    """Parse a GitHub-style Markdown table into a :class:`TableModel`.

    Returns ``None`` if ``block`` is not a table (no header/separator rows).
    """
    lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
    rows = [ln for ln in lines if ln.startswith("|") or "|" in ln]
    if len(rows) < 2:
        return None

    def split_row(line: str) -> list[str]:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [c.strip() for c in line.split("|")]

    header = split_row(rows[0])
    sep = split_row(rows[1])
    if not all(re.fullmatch(r":?-{2,}:?", c) for c in sep if c):
        return None  # second row is not the --- separator: not a table
    data = [split_row(r) for r in rows[2:]]
    data = [r for r in data if any(cell for cell in r)]

    status_col: int | None = None
    for i, h in enumerate(header):
        if h.casefold() in _STATUS_HEADERS:
            status_col = i
            break
    if status_col is None:
        # Fall back to the column whose cells look most like statuses.
        best, best_hits = None, 0
        for i in range(len(header)):
            hits = sum(1 for r in data if i < len(r) and _cell_severity(r[i]))
            if hits > best_hits:
                best, best_hits = i, hits
        status_col = best
    return TableModel(headers=header, rows=data, status_col=status_col)


def _plural_join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " ve " + items[-1]


def narrate_table(
    block: str,
    *,
    literal: bool = False,
    mode: str = "narration",
    pronunciation: dict[str, str] | None = None,
) -> TableNarration:
    """Turkish narration of a Markdown table.

    literal=False (default): a semantic summary that leads with the notable rows.
    literal=True: a row-by-row reading of every cell.
    """
    model = parse_markdown_table(block)
    if model is None:
        spoken = normalize(block, mode=mode, pronunciation=pronunciation)
        return TableNarration(text=spoken, row_count=0)

    n = len(model.rows)
    key_col = 0  # first column names the entity ("Bileşen")

    def norm(s: str) -> str:
        return normalize(s, mode=mode, pronunciation=pronunciation)

    if literal:
        parts: list[str] = [f"{norm('Tablo')}, {model_count_phrase(n)}."]
        for r in model.rows:
            pairs = [
                f"{norm(model.headers[i])}: {norm(r[i])}"
                for i in range(len(model.headers))
                if i < len(r) and r[i]
            ]
            parts.append("; ".join(pairs) + ".")
        return TableNarration(text=" ".join(parts), row_count=n)

    # Semantic mode: classify rows by severity and lead with problems.
    problems: list[tuple[str, str, list[str]]] = []  # (severity, name, extra cells)
    ok_names: list[str] = []
    for r in model.rows:
        name = r[key_col] if key_col < len(r) else ""
        sev = None
        if model.status_col is not None and model.status_col < len(r):
            sev = _cell_severity(r[model.status_col])
        if sev in ("warn", "error"):
            extras = [
                norm(r[i])
                for i in range(len(r))
                if i not in (key_col, model.status_col) and r[i]
            ]
            problems.append((sev, norm(name), extras))
        else:
            ok_names.append(norm(name))

    sentences: list[str] = [model_count_phrase(n).capitalize() + "."]
    highlighted: list[str] = []
    if problems:
        for sev, name, extras in problems:
            highlighted.append(name)
            label = "uyarı durumunda" if sev == "warn" else "hata durumunda"
            detail = f" ({_plural_join(extras)})" if extras else ""
            sentences.append(f"{name} bileşeni {label}{detail}.")
        if ok_names:
            sentences.append(
                f"Diğer bileşenler ({_plural_join(ok_names)}) sağlıklı."
                if len(ok_names) > 1
                else f"{ok_names[0]} bileşeni sağlıklı."
            )
    else:
        sentences.append("Tüm bileşenler sağlıklı görünüyor.")

    return TableNarration(text=" ".join(sentences), row_count=n, highlighted=highlighted)


def model_count_phrase(n: int) -> str:
    from app.narration import numbers

    return f"tabloda {numbers.cardinal(n)} satır var"


# ------------------------------------------------------------- code / log blocks

_LOG_LEVEL_RE = re.compile(r"\b(ERROR|ERR|FATAL|CRITICAL|WARN|WARNING|EXCEPTION|Traceback)\b")


def narrate_code_or_log(
    block: str,
    *,
    literal: bool = False,
    language: str | None = None,
) -> str:
    """Summarize a code/log block (VOICE_SPEC §6): result summary + critical
    warnings/errors by default; the literal content only when requested."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if literal:
        return block

    from app.narration import numbers

    error_lines = [ln.strip() for ln in lines if _LOG_LEVEL_RE.search(ln)]
    total = numbers.cardinal(len(lines))
    if error_lines:
        count = numbers.cardinal(len(error_lines))
        lead = error_lines[0]
        lead = re.sub(r"\s+", " ", lead)[:160]
        return (
            f"Kod/günlük bloğu {total} satır; {count} satırda hata veya uyarı var. "
            f"İlk kritik satır: {lead}"
        )
    kind = "kod" if language else "günlük"
    return f"{kind.capitalize()} bloğu {total} satır, kritik hata veya uyarı bulunmuyor."


__all__ = [
    "TableModel",
    "TableNarration",
    "parse_markdown_table",
    "narrate_table",
    "narrate_code_or_log",
]
