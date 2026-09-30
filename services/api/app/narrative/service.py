"""The public entry: collect -> narrate -> audit -> repair."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.narrative.auditor import audit, repair
from app.narrative.collector import Period, collect
from app.narrative.narrator import Narrator, RuleNarrator


def tell(
    db: Session,
    period: Period | str,
    device: str | None = None,
    narrator: Narrator | None = None,
    *,
    now: datetime | None = None,
) -> str:
    facts = collect(db, period, device, now=now)
    text = repair(facts, (narrator or RuleNarrator()).tell(facts))
    if audit(facts, text).ok:
        return text
    # Repair only restores failures. A narrator that invented a number or skipped a subsystem
    # is not trusted: the rule text is built from the facts alone and passes by construction.
    return RuleNarrator().tell(facts)
