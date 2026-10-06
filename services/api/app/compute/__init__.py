"""compute.run: model-written Python in a one-shot, network-less docker container.

Unbound on purpose: no router, no ToolSpec, no ledger kind yet. The binding card (its full
text is in team/plans/compute-run-sandbox-adr.md) calls ``run_compute`` and uses the names
below. ``JOB_KIND`` is read from the execution rule, which already sends it to the cloud only.
"""

from __future__ import annotations

from typing import Final

from app.compute.policy import ComputePolicyError, ComputeRequest
from app.compute.receipt import ComputeOutcome, ComputeReceipt
from app.compute.runner import run as run_compute
from app.execution.rule import JobKind

TOOL_NAME: Final = "compute.run"
LEDGER_KIND: Final = "compute.run"
JOB_KIND: Final = JobKind.COMPUTE

__all__ = [
    "JOB_KIND",
    "LEDGER_KIND",
    "TOOL_NAME",
    "ComputeOutcome",
    "ComputePolicyError",
    "ComputeReceipt",
    "ComputeRequest",
    "run_compute",
]
