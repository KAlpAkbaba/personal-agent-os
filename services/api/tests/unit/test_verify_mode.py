"""Doğrula modu (card verify-mode, d20261006): "bunu doğrula: ..." -> a claim, a verdict
DOĞRU | YANLIŞ | KISMEN | BELİRSİZ, 2-5 sources, the strongest counter-argument; recalled
later by text and by date.

The two voice tools live in the research family (``research.verify`` starts a crawl the same
way ``research.start`` does; ``research.verify_recall`` reads the owner's own records back).
Every registered tool must carry a step-up tier (``app.security.step_up._TIERS``,
``test_voice_step_up.test_every_tool_has_a_tier``), so the tiers are decided here first.
"""

from __future__ import annotations

from app.security import step_up

TOOL_VERIFY = "research.verify"
TOOL_VERIFY_RECALL = "research.verify_recall"


def test_the_verify_tools_have_a_decided_tier() -> None:
    """A verify starts a crawl on an owner device - the same tier ``research.start`` has.
    Reading back what was verified changes nothing - OPEN, like ``memory.search``."""
    assert step_up._TIERS.get(TOOL_VERIFY) == step_up.TIER_SENSITIVE
    assert step_up._TIERS.get(TOOL_VERIFY_RECALL) == step_up.TIER_OPEN
