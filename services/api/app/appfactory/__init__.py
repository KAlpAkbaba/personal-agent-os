"""The App Factory (docs/M23_APP_FACTORY_SPEC.md, ADR-0086): the Cloud Core half.

``spec`` — the structured, validated ``AppSpec`` input.
``generator`` — ``AppGenerator`` Protocol + the deterministic/Claude backends.
``validation`` — the ``ProjectFiles`` policy (bounds, paths, secrets, the manifest
command allowlist) every generated file set passes before it ever reaches a device.
``models`` — the ``app_projects`` ORM row (migration ``20260908_0030_app_projects``).
``service`` — ``AppFactoryService``: create/run/exercise/test/stop/status/list.
"""

from __future__ import annotations
