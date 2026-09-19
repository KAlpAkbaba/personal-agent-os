"""The search region Cloud Core sends is the one the browser worker actually reads.

ADR-0178 D2 shipped the sending half alone: ``DeviceBrowserGateway.search`` put ``region``
on the ``browser.search`` payload and the worker ignored the key, so every Turkish search
was still answered from the default region while both sides' own tests stayed green - the
gateway's test asserted the field was SENT, and the worker had no test for a field it did
not know about.

So this reads BOTH sides from source: the key Cloud Core puts in the payload, the key
``browser_agent.worker._op_search`` takes out of it, and the engine parameter the region
ends up as. A rename on either side fails here instead of in the owner's hands.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CLOUD_GATEWAY = REPO / "services" / "api" / "app" / "research" / "browser_gateway.py"
AGENT_WORKER = REPO / "services" / "browser" / "browser_agent" / "worker.py"
AGENT_ENGINES = REPO / "services" / "browser" / "browser_agent" / "search_engines.py"


def _cloud_region_value() -> str:
    src = CLOUD_GATEWAY.read_text(encoding="utf-8")
    assert 'payload["region"] = SEARCH_REGION_TURKISH' in src, (
        "the gateway no longer sets the region the way this test reads it"
    )
    match = re.search(r'^SEARCH_REGION_TURKISH\s*=\s*"([^"]+)"', src, re.M)
    assert match, "SEARCH_REGION_TURKISH is no longer a module constant"
    return match.group(1)


def test_the_worker_reads_the_key_cloud_core_sends() -> None:
    worker = AGENT_WORKER.read_text(encoding="utf-8")
    assert 'region = payload.get("region")' in worker, (
        "browser.search sends a 'region' field; the worker must take it out of the payload"
    )
    assert "region=region," in worker, "the worker takes 'region' and never passes it on"


def test_the_region_reaches_the_url_builder_the_engines_use() -> None:
    """What the URL then looks like is proved in the browser package's own suite
    (``tests/unit/test_search_engines.py::TestSearchRegion``) - that package's
    dependencies are not installed here. This half asserts the seam exists."""
    engines = AGENT_ENGINES.read_text(encoding="utf-8")
    assert "def region_params(" in engines, "the worker no longer has a region parameter map"
    assert re.search(r"def build_search_url\((?:[^)]*\n)*?\s*region: str \| None", engines), (
        "build_search_url no longer takes a region"
    )
    assert "region_params(engine, region)" in engines, "build_search_url ignores the region"


def test_the_region_shape_both_sides_agree_on_is_language_dash_country() -> None:
    """``region_params`` drops anything else silently, so a constant in another shape
    would disable the region with nothing failing."""
    assert re.fullmatch(r"[a-z]{2}-[a-z]{2}", _cloud_region_value())
