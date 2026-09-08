"""3D creation (docs/M25_CREATIVE_3D_SPEC.md, ADR-0088): the Cloud Core half.

``spec`` — the structured, validated ``ScenePlan`` input (spec §2): a closed operation
vocabulary, closed-alphabet names, bounded numbers, never free text spliced into code.
``drivers`` — the FIXED, sha256-pinned driver files the tools run through their own
scripting interfaces (spec §3): ``blender_driver.py`` (Blender headless Python) and
``SceneDriver.cs`` (Unity Editor script), plus ``manifest.json`` pinning both.
``compare`` — the closed loop's comparison (spec §4): plan constraints vs. the tool's own
read-back inspection.
``models`` — the ``scenes`` ORM row (migration ``20260908_0032_scenes``).
``service`` — ``SceneService``: create/apply/render/inspect/status/list, on the ONE
device port every M19-M24 family already shares.
``routes`` — the owner-gated REST surface for the Cockpit's "3B Sahne" panel.
"""

from __future__ import annotations
