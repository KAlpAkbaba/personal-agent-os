"""Typed field contracts for the research pipeline: numbers are numbers, text is text.

Owner incident, 2026-09-04 (research run `f6eb5021`, DuckDuckGo discovery, 243 candidates
discovered, 12 fetched and ranked): the synthesis model returned a finding whose
``importance`` carried a Turkish prose sentence instead of a number. ``int(data["importance"])``
raised ``ValueError: invalid literal for int() with base 10: 'Bu model, yapay zeka …'`` and the
whole research job failed — after every expensive step had already succeeded. The exception was
the symptom; the missing contract was the defect: a numeric field had no declared type, no
range, no provenance and no validation, so free-form extracted text could reach it.

What this module gives every numeric field in the pipeline:

* an explicit **name** and **owning entity** (discovered result, fetched source, evidence item,
  ranked candidate, finding);
* an explicit **type** (``int``/``float``) and **valid range**;
* an explicit **provenance** — where a legitimate value comes from, so a reviewer can tell at a
  glance whether a model, a parser or our own ranking produced it;
* **deterministic validation before use** (:func:`require_number`), which accepts only real
  numbers and clean numeric strings and refuses prose, dates, empty strings, booleans,
  containers and out-of-range values;
* the **inverse guard** (:func:`require_text`): a text field that receives a number is just as
  wrong as a numeric field that receives prose, and is refused with the same machinery, so a
  title/snippet/body can never be silently read as a rank or a score.

Violations raise :class:`ContractViolation`, which carries what an operator needs and nothing
more: the entity kind and id, the field name, what was expected, the observed Python type and a
**value class** (``prose_text``, ``date_like_string``, ``numeric_string``, ``bool``, …). The
offending text itself is never copied into the error, the logs or the run events — only its
class and length — because source content must not leak out of the evidence store.

Fault isolation is a policy the callers apply with :class:`QuarantineLedger`: one malformed
candidate or finding is quarantined (`invalid_evidence_contract`), its raw evidence is left
untouched in the evidence store, its reason is recorded, and the run continues on the valid
remainder. Only when too little valid evidence is left to answer the request does the run fail,
with :class:`InsufficientValidEvidence` naming the counts and the reasons.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# entities and stages
# --------------------------------------------------------------------------- #

ENTITY_DISCOVERED_RESULT = "discovered_result"
ENTITY_FETCHED_SOURCE = "fetched_source"
ENTITY_EVIDENCE_ITEM = "evidence_item"
ENTITY_RANKED_CANDIDATE = "ranked_candidate"
ENTITY_FINDING = "finding"

ENTITIES = (
    ENTITY_DISCOVERED_RESULT,
    ENTITY_FETCHED_SOURCE,
    ENTITY_EVIDENCE_ITEM,
    ENTITY_RANKED_CANDIDATE,
    ENTITY_FINDING,
)

#: Error class reported to Cloud Core/the owner when a candidate breaks the contract.
ERROR_INVALID_EVIDENCE_CONTRACT = "invalid_evidence_contract"
#: Error class when quarantining left too little to answer the request.
ERROR_INSUFFICIENT_VALID_EVIDENCE = "insufficient_valid_evidence"

#: How many contract-valid evidence items a run needs before ranking/synthesis is attempted.
MIN_VALID_EVIDENCE = 3


# --------------------------------------------------------------------------- #
# field specifications
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class NumericFieldSpec:
    """One numeric field: what it is called, its type, its range and where it comes from."""

    entity: str
    name: str
    kind: str  # "int" | "float"
    minimum: float
    maximum: float
    provenance: str
    required: bool = True
    default: float | None = None

    def describe(self) -> str:
        return f"{self.kind} in [{self.minimum}, {self.maximum}]"


def _spec(*args: Any, **kwargs: Any) -> NumericFieldSpec:
    return NumericFieldSpec(*args, **kwargs)


#: Every numeric field the pipeline reads from data it did not compute itself.
NUMERIC_FIELDS: dict[tuple[str, str], NumericFieldSpec] = {
    (s.entity, s.name): s
    for s in (
        _spec(
            ENTITY_DISCOVERED_RESULT,
            "rank",
            "int",
            1,
            1000,
            provenance="position in the search provider's organic result list (1-based)",
            required=False,
            default=None,
        ),
        _spec(
            ENTITY_FETCHED_SOURCE,
            "http_status",
            "int",
            100,
            599,
            provenance="HTTP status line of the device's page fetch",
            required=False,
            default=None,
        ),
        _spec(
            ENTITY_EVIDENCE_ITEM,
            "rank",
            "int",
            0,
            10000,
            provenance="assigned by dedup_and_rank; 0 before ranking",
            required=False,
            default=0,
        ),
        _spec(
            ENTITY_EVIDENCE_ITEM,
            "score",
            "float",
            -1000.0,
            1000.0,
            provenance="computed by dedup_and_rank from recency/source class/topic overlap",
            required=False,
            default=0.0,
        ),
        _spec(
            ENTITY_RANKED_CANDIDATE,
            "rank",
            "int",
            1,
            10000,
            provenance="assigned by dedup_and_rank (1-based, dense)",
        ),
        _spec(
            ENTITY_RANKED_CANDIDATE,
            "score",
            "float",
            -1000.0,
            1000.0,
            provenance="computed by dedup_and_rank",
        ),
        _spec(
            ENTITY_FINDING,
            "importance",
            "int",
            1,
            5,
            provenance="the synthesis provider's own rating; a model's free text is NOT a rating",
        ),
    )
}

#: Text fields that must never be handed a number (the inverse of the incident).
TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    ENTITY_DISCOVERED_RESULT: ("url", "title", "snippet"),
    ENTITY_FETCHED_SOURCE: ("url", "title", "excerpt", "publisher"),
    ENTITY_EVIDENCE_ITEM: ("url", "title", "excerpt", "publisher", "source_class"),
    ENTITY_RANKED_CANDIDATE: ("url", "title", "excerpt", "publisher", "source_class"),
    ENTITY_FINDING: ("id", "title", "summary", "why_it_matters", "label"),
}


def numeric_spec(entity: str, name: str) -> NumericFieldSpec:
    try:
        return NUMERIC_FIELDS[(entity, name)]
    except KeyError:  # pragma: no cover - a typo in a caller, caught by tests
        raise LookupError(f"no numeric contract declared for {entity}.{name}") from None


# --------------------------------------------------------------------------- #
# value classification (never carries the value itself)
# --------------------------------------------------------------------------- #

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_DATE_LIKE_RE = re.compile(r"^\s*\d{4}[-/.]\d{1,2}([-/.]\d{1,2})?")


def classify_value(value: Any) -> str:
    """A short, safe description of WHAT was received — never the content itself."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "nan_or_inf" if (math.isnan(value) or math.isinf(value)) else "float"
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "empty_string"
        if _INT_RE.match(stripped):
            return "numeric_string"
        if _FLOAT_RE.match(stripped):
            return "numeric_string"
        if _DATE_LIKE_RE.match(stripped):
            return "date_like_string"
        return "prose_text"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "mapping"
    return f"other:{type(value).__name__}"


# --------------------------------------------------------------------------- #
# violations
# --------------------------------------------------------------------------- #


class ContractViolation(Exception):
    """A field did not satisfy its declared contract.

    Carries the identification an operator needs (entity, id, field, expected, observed class,
    stage) and never the offending content.
    """

    def __init__(
        self,
        *,
        entity: str,
        field_name: str,
        expected: str,
        observed: Any,
        entity_id: str | None = None,
        stage: str | None = None,
        reason: str = "type",
    ) -> None:
        self.entity = entity
        self.entity_id = entity_id or "unknown"
        self.field_name = field_name
        self.expected = expected
        self.reason = reason
        self.observed_type = type(observed).__name__ if observed is not None else "NoneType"
        self.observed_class = classify_value(observed)
        self.observed_length = (
            len(observed) if isinstance(observed, (str, list, tuple, dict)) else None
        )
        self.stage = stage
        super().__init__(
            f"{entity}.{field_name} ({self.entity_id}): expected {expected}, "
            f"observed {self.observed_class} ({self.observed_type}"
            + (f", length {self.observed_length}" if self.observed_length is not None else "")
            + f"); reason {reason}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_class": ERROR_INVALID_EVIDENCE_CONTRACT,
            "entity": self.entity,
            "entity_id": self.entity_id,
            "field": self.field_name,
            "expected": self.expected,
            "observed_type": self.observed_type,
            "observed_class": self.observed_class,
            "observed_length": self.observed_length,
            "reason": self.reason,
            "stage": self.stage,
        }


class InsufficientValidEvidence(Exception):
    """Quarantining left too little valid evidence to answer the request."""

    def __init__(self, *, stage: str, valid: int, required: int, quarantined: list[dict[str, Any]]):
        self.stage = stage
        self.valid = valid
        self.required = required
        self.quarantined = quarantined
        super().__init__(
            f"{stage}: {valid} contract-valid item(s), {required} required; "
            f"{len(quarantined)} quarantined"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_class": ERROR_INSUFFICIENT_VALID_EVIDENCE,
            "stage": self.stage,
            "valid": self.valid,
            "required": self.required,
            "quarantined": self.quarantined[:20],
            "quarantined_total": len(self.quarantined),
        }


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

_MISSING = object()


def require_number(
    value: Any,
    entity: str,
    field_name: str,
    *,
    entity_id: str | None = None,
    stage: str | None = None,
) -> int | float | None:
    """Validate ``value`` against the declared contract for ``entity.field_name``.

    Accepts real numbers and clean numeric strings only. Prose, dates, empty strings, booleans
    and containers are refused — that is the whole point: a Turkish sentence can never again be
    read as a rating. Returns the spec's default when the field is absent and optional.
    """
    spec = numeric_spec(entity, field_name)
    if value is _MISSING or value is None:
        if spec.required:
            raise ContractViolation(
                entity=entity,
                field_name=field_name,
                expected=spec.describe(),
                observed=None if value is None else _MISSING,
                entity_id=entity_id,
                stage=stage,
                reason="missing",
            )
        return spec.default

    if isinstance(value, bool):
        raise ContractViolation(
            entity=entity,
            field_name=field_name,
            expected=spec.describe(),
            observed=value,
            entity_id=entity_id,
            stage=stage,
            reason="bool_is_not_a_number",
        )

    number: int | float
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ContractViolation(
                entity=entity,
                field_name=field_name,
                expected=spec.describe(),
                observed=value,
                entity_id=entity_id,
                stage=stage,
                reason="not_finite",
            )
        number = value
    elif isinstance(value, str):
        stripped = value.strip()
        pattern = _INT_RE if spec.kind == "int" else _FLOAT_RE
        if not pattern.match(stripped):
            raise ContractViolation(
                entity=entity,
                field_name=field_name,
                expected=spec.describe(),
                observed=value,
                entity_id=entity_id,
                stage=stage,
                reason="text_is_not_a_number",
            )
        number = int(stripped) if spec.kind == "int" else float(stripped)
    else:
        raise ContractViolation(
            entity=entity,
            field_name=field_name,
            expected=spec.describe(),
            observed=value,
            entity_id=entity_id,
            stage=stage,
            reason="wrong_type",
        )

    if spec.kind == "int":
        if isinstance(number, float) and not float(number).is_integer():
            raise ContractViolation(
                entity=entity,
                field_name=field_name,
                expected=spec.describe(),
                observed=value,
                entity_id=entity_id,
                stage=stage,
                reason="not_an_integer",
            )
        number = int(number)
    else:
        number = float(number)

    if not (spec.minimum <= number <= spec.maximum):
        raise ContractViolation(
            entity=entity,
            field_name=field_name,
            expected=spec.describe(),
            observed=value,
            entity_id=entity_id,
            stage=stage,
            reason="out_of_range",
        )
    return number


def require_text(
    value: Any,
    entity: str,
    field_name: str,
    *,
    entity_id: str | None = None,
    stage: str | None = None,
    allow_empty: bool = True,
) -> str:
    """A declared text field must be a real string — never a number in disguise.

    The inverse guard of :func:`require_number`: it keeps a rank or a score from ever being
    read as a title/snippet/body, which is what makes a positional mix-up detectable at the
    field boundary instead of three stages later.
    """
    declared = TEXT_FIELDS.get(entity, ())
    if field_name not in declared:  # pragma: no cover - caller typo, caught by tests
        raise LookupError(f"no text contract declared for {entity}.{field_name}")
    if isinstance(value, str):
        if not value.strip() and not allow_empty:
            raise ContractViolation(
                entity=entity,
                field_name=field_name,
                expected="non-empty text",
                observed=value,
                entity_id=entity_id,
                stage=stage,
                reason="empty",
            )
        return value
    raise ContractViolation(
        entity=entity,
        field_name=field_name,
        expected="text",
        observed=value,
        entity_id=entity_id,
        stage=stage,
        reason="number_is_not_text"
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else "wrong_type",
    )


# --------------------------------------------------------------------------- #
# quarantine
# --------------------------------------------------------------------------- #


@dataclass
class QuarantineLedger:
    """Candidates/findings excluded for breaking the contract, with their reasons.

    The raw evidence itself is never modified or deleted: only its identity and the reason are
    recorded here, so the run's event trail can say exactly what was set aside and why.
    """

    stage: str
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, violation: ContractViolation, *, url: str | None = None) -> None:
        entry = violation.as_dict()
        entry["stage"] = entry.get("stage") or self.stage
        if url:
            entry["url"] = url
        self.entries.append(entry)

    def record_reason(self, *, entity: str, entity_id: str, reason: str, **extra: Any) -> None:
        self.entries.append(
            {
                "error_class": ERROR_INVALID_EVIDENCE_CONTRACT,
                "entity": entity,
                "entity_id": entity_id,
                "reason": reason,
                "stage": self.stage,
                **extra,
            }
        )

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def empty(self) -> bool:
        return not self.entries

    def summary(self) -> dict[str, Any]:
        by_reason: dict[str, int] = {}
        for entry in self.entries:
            key = f"{entry.get('entity')}.{entry.get('field', '-')}:{entry.get('reason')}"
            by_reason[key] = by_reason.get(key, 0) + 1
        return {"quarantined": len(self.entries), "by_reason": by_reason}


__all__ = [
    "ENTITIES",
    "ENTITY_DISCOVERED_RESULT",
    "ENTITY_EVIDENCE_ITEM",
    "ENTITY_FETCHED_SOURCE",
    "ENTITY_FINDING",
    "ENTITY_RANKED_CANDIDATE",
    "ERROR_INSUFFICIENT_VALID_EVIDENCE",
    "ERROR_INVALID_EVIDENCE_CONTRACT",
    "MIN_VALID_EVIDENCE",
    "NUMERIC_FIELDS",
    "TEXT_FIELDS",
    "ContractViolation",
    "InsufficientValidEvidence",
    "NumericFieldSpec",
    "QuarantineLedger",
    "classify_value",
    "numeric_spec",
    "require_number",
    "require_text",
]
