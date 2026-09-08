"""Capability Genesis (M24, ADR-0087): an owner request against an interface with
no adapter becomes a generated, proven, registered capability.

See ``docs/M24_CAPABILITY_GENESIS_SPEC.md``. This package is additive to
``app.evolution`` (M7/M18.4): it reuses the sandbox, the evaluator, the
independent reviewer, the shadow/canary runners, the capability registry and
the dispatcher rather than reimplementing any of them; it adds a second
research step (``interface.py``), a second ``SkillGenerator``
(``adapter.py``), six additive manifest keys (``app.evolution.manifest``) and
the state machine that drives one ``GenesisRun`` end to end (``service.py``).
"""

from __future__ import annotations
