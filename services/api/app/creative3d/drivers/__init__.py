"""The FIXED, sha256-pinned driver files 3D scene creation runs through (docs/
M25_CREATIVE_3D_SPEC.md §3, ADR-0088 decision 2).

``blender_driver.py`` — runs inside Blender's own Python
(``blender.exe -b [scene.blend] --python blender_driver.py -- <plan.json> <out.json>``).
``SceneDriver.cs`` — a Unity Editor script (``PagentOS.SceneDriver.Run``), copied into
the fixture project at scaffold time and invoked through
``Unity.exe -batchmode -nographics -quit -executeMethod PagentOS.SceneDriver.Run``.
``manifest.json`` — the sha256 of both files, asserted by
``tests/unit/test_blender_driver.py`` (never edited without also updating the pin: a
driver that changed silently is exactly the tampering this pin exists to catch).

Neither file is generated from a plan or from owner/model prose (ADR-0088 decision 1);
both are committed, reviewed repository text, read verbatim by the tool that runs them.
"""

from __future__ import annotations
