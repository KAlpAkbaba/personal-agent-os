"""M24 Capability Genesis test fixture applications (spec §7).

Two small stdlib ``http.server`` applications, each exposing a controllable
feature with no existing adapter: ``counterbox_app`` ("Sayaç Kutusu", a
counter) and ``lampbox_app`` ("Test Lambası", a lamp). Both are TEST
APPLICATIONS — they run only inside tests and the voice corpus harness, never
as a product.
"""

from __future__ import annotations
