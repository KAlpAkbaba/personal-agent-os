"""``ArtifactSpec``: the structured, validated input to the Artifact Factory
(docs/M22_ARTIFACT_FACTORY_SPEC.md §1, ADR-0085 decision 1).

The spec is produced by the assistant from the owner's own words — never free prose
handed straight to a renderer. Every number the owner said is carried literally in the
structure; ``spoken_numbers``, when given, is the closed set of numbers the structure is
allowed to contain (the "never invented" rule, ADR-0085 decision 1 / M21's own literal-
carry discipline for mail/calendar). A number that shows up in the rendered artifact but
was never in ``spoken_numbers`` is a bug in the caller that built the spec, and this
module refuses to construct such a spec at all rather than let it reach a renderer.

Bounds (module docstring numbers straight from the task brief): at most 200 sections /
rows / slides, at most 20 000 characters of text across the whole spec, at most 50
columns in a spreadsheet sheet or a dataset. These are sanity bounds against a runaway
cognitive-backend response, not a promise that a real owner document is ever this large.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- kinds

KIND_DOCUMENT = "document"
KIND_SPREADSHEET = "spreadsheet"
KIND_PRESENTATION = "presentation"
KIND_DATASET = "dataset"
KIND_PAGE = "page"

ARTIFACT_KINDS: tuple[str, ...] = (
    KIND_DOCUMENT,
    KIND_SPREADSHEET,
    KIND_PRESENTATION,
    KIND_DATASET,
    KIND_PAGE,
)

#: spec §1's table, verbatim: which render formats a kind may produce.
KIND_FORMATS: dict[str, tuple[str, ...]] = {
    KIND_DOCUMENT: ("docx", "pdf", "html", "md", "txt"),
    KIND_SPREADSHEET: ("xlsx", "csv"),
    KIND_PRESENTATION: ("pptx",),
    KIND_DATASET: ("csv", "json"),
    KIND_PAGE: ("html", "md"),
}

MAX_SECTIONS = 200
MAX_ROWS = 200
MAX_SLIDES = 200
MAX_COLUMNS = 50
MAX_TEXT_CHARS = 20_000

#: A totals directive's aggregation — spec §1: ``totals?: {column: sum|avg}``.
TOTALS_AGGREGATIONS: tuple[str, ...] = ("sum", "avg")

CellValue = str | int | float

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers_in_text(text: str) -> list[float]:
    """Every digit run embedded in free text, as a float ("yüzde 8 arttı" -> [8.0])."""
    return [float(m.group()) for m in _NUMBER_RE.finditer(text)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


# ------------------------------------------------------------------------- document


class Table(_StrictModel):
    columns: list[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[list[CellValue]] = Field(default_factory=list, max_length=MAX_ROWS)


class Section(_StrictModel):
    heading: str = Field(min_length=1, max_length=500)
    level: int = Field(default=1, ge=1, le=3)
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] | None = None
    table: Table | None = None


# ---------------------------------------------------------------------- spreadsheet


class Sheet(_StrictModel):
    name: str = Field(min_length=1, max_length=200)
    columns: list[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[list[CellValue]] = Field(default_factory=list, max_length=MAX_ROWS)
    #: ``{column_name: "sum" | "avg"}`` — which columns get a computed totals row, and how.
    totals: dict[str, Literal["sum", "avg"]] | None = None
    #: ``{cell_ref: text_or_formula}`` — an explicit cell written verbatim at that address;
    #: a value starting with ``"="`` is a formula (openpyxl convention), anything else is a
    #: literal text label. Never scanned for the "never invented" rule (a formula's own
    #: literals, like a VAT rate, are not numbers the owner said).
    formulas: dict[str, str] | None = None

    @model_validator(mode="after")
    def _rows_match_columns(self) -> Sheet:
        width = len(self.columns)
        for row in self.rows:
            if len(row) != width:
                raise ValueError(
                    f"sheet {self.name!r}: row {row!r} has {len(row)} cells, "
                    f"expected {width} to match its columns"
                )
        if self.totals:
            unknown = set(self.totals) - set(self.columns)
            if unknown:
                raise ValueError(f"sheet {self.name!r}: totals name unknown column(s) {unknown}")
        return self


# --------------------------------------------------------------------- presentation


class Slide(_StrictModel):
    title: str = Field(min_length=1, max_length=500)
    bullets: list[str] = Field(default_factory=list)
    notes: str | None = None


# ------------------------------------------------------------------------- the spec


class ArtifactSpec(_StrictModel):
    kind: Literal["document", "spreadsheet", "presentation", "dataset", "page"]
    title: str = Field(min_length=1, max_length=500)
    language: str = "tr"

    # document | page
    sections: list[Section] | None = Field(default=None, max_length=MAX_SECTIONS)

    # spreadsheet
    sheets: list[Sheet] | None = Field(default=None, max_length=200)

    # presentation
    slides: list[Slide] | None = Field(default=None, max_length=MAX_SLIDES)

    # dataset
    columns: list[str] | None = Field(default=None, max_length=MAX_COLUMNS)
    rows: list[list[CellValue]] | None = Field(default=None, max_length=MAX_ROWS)

    style: dict[str, str] | None = None

    #: The closed set of numbers the owner actually said, when the caller can name it.
    #: ``None`` means "not checked" (a caller that has not adopted the rule yet);
    #: ``[]`` means "the owner said no numbers at all" and the structure must carry none.
    spoken_numbers: list[float] | None = None

    # ----------------------------------------------------------------- kind shape

    @model_validator(mode="after")
    def _kind_shape(self) -> ArtifactSpec:
        kind = self.kind
        present = {
            "sections": self.sections is not None,
            "sheets": self.sheets is not None,
            "slides": self.slides is not None,
            "columns": self.columns is not None,
            "rows": self.rows is not None,
        }
        required: dict[str, tuple[str, ...]] = {
            KIND_DOCUMENT: ("sections",),
            KIND_PAGE: ("sections",),
            KIND_SPREADSHEET: ("sheets",),
            KIND_PRESENTATION: ("slides",),
            KIND_DATASET: ("columns", "rows"),
        }
        needed = required[kind]
        for field_name in needed:
            if not present[field_name]:
                raise ValueError(f"kind {kind!r} requires {field_name!r}")
        for field_name, is_present in present.items():
            if field_name not in needed and is_present:
                raise ValueError(f"kind {kind!r} must not carry {field_name!r}")

        if kind == KIND_DOCUMENT and not self.sections:
            raise ValueError("document requires at least one section")
        if kind == KIND_PAGE and not self.sections:
            raise ValueError("page requires at least one section")
        if kind == KIND_SPREADSHEET and not self.sheets:
            raise ValueError("spreadsheet requires at least one sheet")
        if kind == KIND_PRESENTATION and not self.slides:
            raise ValueError("presentation requires at least one slide")
        if kind == KIND_DATASET:
            assert self.columns is not None and self.rows is not None
            width = len(self.columns)
            for row in self.rows:
                if len(row) != width:
                    raise ValueError(
                        f"dataset row {row!r} has {len(row)} cells, expected {width}"
                    )
        return self

    # --------------------------------------------------------------------- bounds

    @model_validator(mode="after")
    def _bounds(self) -> ArtifactSpec:
        total_chars = 0
        for text in self._all_text():
            total_chars += len(text)
        if total_chars > MAX_TEXT_CHARS:
            raise ValueError(
                f"spec carries {total_chars} characters of text, "
                f"more than the {MAX_TEXT_CHARS} bound"
            )
        return self

    # ------------------------------------------------------- the never-invented rule

    @model_validator(mode="after")
    def _numbers_never_invented(self) -> ArtifactSpec:
        if self.spoken_numbers is None:
            return self
        allowed = {float(n) for n in self.spoken_numbers}
        found = self._structure_numbers()
        invented = sorted(found - allowed)
        if invented:
            raise ValueError(
                "spec contains number(s) not in spoken_numbers (never invented rule): "
                f"{invented} not in {sorted(allowed)}"
            )
        return self

    # ------------------------------------------------------------------- traversal

    def _all_text(self) -> list[str]:
        """Every free-text string in the structure (title excluded — see module
        docstring: title is a label, not part of "the structure" spec §1 describes)."""
        out: list[str] = []
        if self.sections:
            for section in self.sections:
                out.append(section.heading)
                out.extend(section.paragraphs)
                if section.bullets:
                    out.extend(section.bullets)
                if section.table:
                    out.extend(section.table.columns)
                    for row in section.table.rows:
                        out.extend(str(c) for c in row)
        if self.sheets:
            for sheet in self.sheets:
                out.extend(sheet.columns)
                for row in sheet.rows:
                    out.extend(str(c) for c in row)
        if self.slides:
            for slide in self.slides:
                out.append(slide.title)
                out.extend(slide.bullets)
                if slide.notes:
                    out.append(slide.notes)
        if self.columns:
            out.extend(self.columns)
        if self.rows:
            for row in self.rows:
                out.extend(str(c) for c in row)
        return out

    def _structure_numbers(self) -> set[float]:
        """Every number appearing in the structure — literal numeric cells compared
        directly, text scanned for embedded digit runs ("yüzde 8 arttı" -> 8.0).
        ``formulas`` (a sheet's own dict of cell_ref -> text/formula) is deliberately
        excluded: a formula's constants (a VAT rate, a cell address digit) are the
        renderer's own arithmetic, never a number the owner said."""
        found: set[float] = set()

        def scan_cell(value: CellValue) -> None:
            if isinstance(value, bool):
                return
            if isinstance(value, int | float):
                found.add(float(value))
            else:
                found.update(_numbers_in_text(str(value)))

        def scan_text(text: str) -> None:
            found.update(_numbers_in_text(text))

        if self.sections:
            for section in self.sections:
                scan_text(section.heading)
                for p in section.paragraphs:
                    scan_text(p)
                if section.bullets:
                    for b in section.bullets:
                        scan_text(b)
                if section.table:
                    for col in section.table.columns:
                        scan_text(col)
                    for row in section.table.rows:
                        for cell in row:
                            scan_cell(cell)
        if self.sheets:
            for sheet in self.sheets:
                for col in sheet.columns:
                    scan_text(col)
                for row in sheet.rows:
                    for cell in row:
                        scan_cell(cell)
        if self.slides:
            for slide in self.slides:
                scan_text(slide.title)
                for b in slide.bullets:
                    scan_text(b)
                if slide.notes:
                    scan_text(slide.notes)
        if self.columns:
            for col in self.columns:
                scan_text(col)
        if self.rows:
            for row in self.rows:
                for cell in row:
                    scan_cell(cell)
        return found

    def formats(self) -> tuple[str, ...]:
        """The render formats this spec's kind may produce (spec §1's table)."""
        return KIND_FORMATS[self.kind]

    def canonical_json(self) -> str:
        """Deterministic JSON serialisation used as the artifact's canonical body
        (stable key order, no whitespace padding, so the same spec always hashes the
        same way — the same discipline the renderers use for their own bytes)."""
        import json

        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


NumberList = Annotated[list[float], Field()]


__all__ = [
    "ARTIFACT_KINDS",
    "ArtifactSpec",
    "KIND_DATASET",
    "KIND_DOCUMENT",
    "KIND_FORMATS",
    "KIND_PAGE",
    "KIND_PRESENTATION",
    "KIND_SPREADSHEET",
    "MAX_COLUMNS",
    "MAX_ROWS",
    "MAX_SECTIONS",
    "MAX_SLIDES",
    "MAX_TEXT_CHARS",
    "Section",
    "Sheet",
    "Slide",
    "Table",
    "TOTALS_AGGREGATIONS",
]
