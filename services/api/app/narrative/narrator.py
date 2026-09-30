"""The narrator seam. ``RuleNarrator`` writes Turkish from the facts alone - no model call; a
model narrator (a later task) implements the same Protocol and is held to the same auditor."""

from __future__ import annotations

from typing import Protocol

from app.narrative.facts import FactEvent, NarrativeFacts, subsystem_label

EMPTY_TEXT = "bu dönemde kayıt yok"


class Narrator(Protocol):
    def tell(self, facts: NarrativeFacts) -> str: ...


def failure_sentence(event: FactEvent) -> str:
    """One failure as a sentence; the auditor's repair uses the same words the narrator does."""
    text = f"{subsystem_label(event.subsystem).capitalize()} alanında: {event.summary}"
    if event.reason:
        text += f" (sebep: {event.reason})"
    return text + "."


class RuleNarrator:
    def tell(self, facts: NarrativeFacts) -> str:
        if facts.is_empty:
            return EMPTY_TEXT
        parts: list[str] = []
        if facts.device:
            parts.append(f"{facts.device.capitalize()} için özet.")
        if facts.failed:
            parts.append(f"{len(facts.failed)} iş başarısız oldu.")
            parts.extend(failure_sentence(e) for e in facts.failed)
            if facts.no_capable_device:
                parts.append(
                    f"Bunlardan {len(facts.no_capable_device)} tanesi uygun cihaz "
                    "bulunamadığı için yapılamadı."
                )
        if facts.completed:
            done = ", ".join(
                f"{subsystem_label(sub)} alanında {len(evs)}" for sub, evs in facts.completed
            )
            parts.append(f"Tamamlananlar: {done} iş tamamlandı.")
        parts.append(f"Toplam {facts.total} kayıt.")
        if len(facts.counts_by_device) > 1:
            spread = ", ".join(f"{dev} {n}" for dev, n in facts.counts_by_device)
            parts.append(f"Cihaz dağılımı: {spread}.")
        return " ".join(parts)
