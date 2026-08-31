"""Recovery Supervisor (M6).

A tiny, stdlib-only watchdog that manages a versioned release workspace for one
supervised component, polls its health, rolls back to last-known-good when the
release health policy fails, and reports incidents to an outbox (and optionally
to the main API). It contains no model reasoning and must keep working when the
main application release is broken (RECOVERY_AND_SELF_HEALING.md §1-§2).
"""

__version__ = "0.1.0"
