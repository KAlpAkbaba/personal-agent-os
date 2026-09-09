"""M27 Creative Tools Operator, Cloud Core half (docs/M27_CREATIVE_TOOLS_SPEC.md, ADR-0093).

A creative edit is planned as data (:mod:`app.creative.spec`), executed through the most
structured interface the installed application really offers (:mod:`app.creative.providers`,
:mod:`app.creative.execute`), exported, and compared against what was asked
(:mod:`app.creative.compare`) — the same closed-loop shape ``app.creative3d`` (M25)
already established, applied here to 2D image editing rather than 3D scenes.
"""

from __future__ import annotations
