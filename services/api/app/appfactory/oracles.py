"""The App Factory's DOM oracles (docs/M23_APP_FACTORY_SPEC.md §4, §6): for each web
template, the structural elements a page must carry and the interaction sequence a real
headless browser worker (the device lab, track B) independently asserts.

Cloud Core (this module) is the SINGLE SOURCE of these oracles; an identical copy is
committed at ``services/api/tests/fixtures/apps/task-tracker/oracle.json`` for the device
lab to consume without importing Cloud Core's package (module docstring of
``app.appfactory.service.exercise``: "the device lab uses the same files" — spec §6).
``test_appfactory_generator.py`` asserts the two never drift.

``app.appfactory.service.AppFactoryService.exercise`` proves only what
``BrowserGateway.fetch_evidence`` can observe (the page is reachable and serving
content); the ``initial_assertions``/``interaction_steps`` below are recorded on the
receipt as what the real headless worker checks — this module never claims a DOM
assertion Cloud Core cannot itself observe.
"""

from __future__ import annotations

from typing import Any

_TASK_TRACKER_ORACLE: dict[str, Any] = {
    "template": "task-tracker",
    "url_path": "/",
    "initial_assertions": [
        {"selector": "#app-title", "exists": True},
        {"selector": "#task-form", "exists": True},
        {"selector": "#new-task-input", "exists": True},
        {"selector": "#add-task-button", "exists": True},
        {"selector": "#task-list", "exists": True},
    ],
    "interaction_steps": [
        {"action": "fill", "selector": "#new-task-input", "value": "Sütü al"},
        {"action": "click", "selector": "#add-task-button"},
        {"action": "assert_text", "selector": "#task-list", "contains": "Sütü al"},
        {"action": "click", "selector": ".task-item .toggle-done"},
        {"action": "assert_class", "selector": ".task-item", "class": "done"},
        {"action": "reload"},
        {"action": "assert_text", "selector": "#task-list", "contains": "Sütü al"},
        {"action": "assert_class", "selector": ".task-item", "class": "done"},
    ],
}

_ORACLES: dict[str, dict[str, Any]] = {
    "task-tracker": _TASK_TRACKER_ORACLE,
}


class OracleNotFoundError(KeyError):
    pass


def load_oracle(template: str) -> dict[str, Any]:
    oracle = _ORACLES.get(template)
    if oracle is None:
        raise OracleNotFoundError(f"no DOM oracle for template {template!r}")
    return oracle


__all__ = ["OracleNotFoundError", "load_oracle"]
