"""The public entry: collect -> narrate -> audit -> repair."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.narrative.auditor import audit, repair
from app.narrative.collector import Period, collect
from app.narrative.facts import only_failures
from app.narrative.narrator import Narrator, RuleNarrator

#: "ne başarısız oldu" over a period that holds no failure. A constant: no narrator, rule or
#: model, is asked to phrase the absence of something.
NO_FAILURES_TEXT = "Bu dönemde başarısız iş yok."


def tell(
    db: Session,
    period: Period | str,
    device: str | None = None,
    narrator: Narrator | None = None,
    *,
    now: datetime | None = None,
    failures_only: bool = False,
) -> str:
    facts = collect(db, period, device, now=now)
    if failures_only:
        # Narrowed BEFORE narrating: the narrators and the auditor are the ones every other
        # narrative uses, and none of them is shown a completed row.
        facts = only_failures(facts)
        if not facts.failed:
            return NO_FAILURES_TEXT
    text = repair(facts, (narrator or RuleNarrator()).tell(facts))
    if audit(facts, text).ok:
        return text
    # Repair only restores failures. A narrator that invented a number or skipped a subsystem
    # is not trusted: the rule text is built from the facts alone and passes by construction.
    return RuleNarrator().tell(facts)
