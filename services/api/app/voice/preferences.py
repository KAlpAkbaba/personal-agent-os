"""Voice memory / preferences (VOICE_SPEC §12).

The owner's spoken-output preferences. The system MAY learn some of these over
time, but an explicit owner instruction always overrides an inferred value
(constitution + VOICE_SPEC §12). We model that by tagging each field's *source*
(``owner`` vs ``inferred``) and refusing to let an inferred update overwrite a
value the owner set explicitly.

The dataclass is the in-memory shape; it round-trips to the
``narration_settings_json`` column of ``voice_profiles`` (plus top-level
``locale``). Everything here is pure and fully unit-testable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from app.voice.errors import VoiceError, VoiceErrorClass

_BOOL_FIELDS = (
    "executive_summary_first",
    "read_headings",
    "read_urls",
    "read_footnotes",
    "barge_in",
)


@dataclass(slots=True)
class VoicePreferences:
    """VOICE_SPEC §12 preference block. ``locale`` maps to voice_profiles.locale;
    the rest live in narration_settings_json."""

    locale: str = "tr-TR"
    executive_summary_first: bool = True
    narration_speed: float = 1.0
    read_headings: bool = True
    read_urls: bool = False
    read_footnotes: bool = False
    barge_in: bool = True
    # Names of fields the owner has set explicitly; these are immune to inferred
    # updates. Persisted alongside the values so the override rule survives reloads.
    owner_set: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- (de)serialize

    def to_narration_settings(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("locale")  # stored in its own column
        return data

    @classmethod
    def from_row(
        cls, *, locale: str, narration_settings: dict[str, Any] | None
    ) -> VoicePreferences:
        settings = dict(narration_settings or {})
        known = {f.name for f in fields(cls)} - {"locale"}
        filtered = {k: v for k, v in settings.items() if k in known}
        return cls(locale=locale, **filtered)

    # ------------------------------------------------------------------ validate

    def validate(self) -> VoicePreferences:
        if not (0.5 <= self.narration_speed <= 3.0):
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR, "narration_speed must be in [0.5, 3.0]"
            )
        if not self.locale or "-" not in self.locale:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"locale must look like 'tr-TR', got {self.locale!r}",
            )
        for name in _BOOL_FIELDS:
            if not isinstance(getattr(self, name), bool):
                raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, f"{name} must be a bool")
        return self

    # -------------------------------------------------------------- update logic

    def apply_update(self, updates: dict[str, Any], *, source: str = "owner") -> VoicePreferences:
        """Apply a partial update. ``source='owner'`` marks touched fields as
        explicit and always wins; ``source='inferred'`` skips any field the owner
        has already set explicitly (VOICE_SPEC §12: explicit overrides inferred).
        """
        if source not in ("owner", "inferred"):
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                f"source must be 'owner' or 'inferred', got {source!r}",
            )
        valid = {f.name for f in fields(self)} - {"owner_set"}
        for key, value in updates.items():
            if key not in valid:
                raise VoiceError(
                    VoiceErrorClass.VALIDATION_ERROR, f"unknown preference field: {key!r}"
                )
            if source == "inferred" and key in self.owner_set:
                continue  # explicit owner value is protected
            setattr(self, key, value)
            if source == "owner" and key not in self.owner_set:
                self.owner_set.append(key)
        return self.validate()


__all__ = ["VoicePreferences"]
