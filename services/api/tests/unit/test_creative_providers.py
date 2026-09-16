"""Creative tool providers (docs/M27_CREATIVE_TOOLS_SPEC.md §1, §4, ADR-0093
decisions 1-3): detection never launches a binary, an application not installed
answers ``dependency_unavailable`` carrying the detection facts, and the router is
offered only the union of the INSTALLED providers' subsets.
"""

from __future__ import annotations

from unittest.mock import patch

from app.creative.providers import (
    ERROR_DEPENDENCY_UNAVAILABLE,
    FULL_CAPABILITIES,
    PAINT_CAPABILITIES,
    DetectionFacts,
    FigmaProvider,
    IllustratorProvider,
    PaintProvider,
    PhotoshopProvider,
    default_providers,
    detect_mspaint,
    installed_capability_union,
)
from app.creative.spec import (
    TOOL_FIGMA,
    TOOL_ILLUSTRATOR,
    TOOL_LAYERED,
    TOOL_PAINT,
    TOOL_PHOTOSHOP,
)


def test_detect_mspaint_never_launches_a_process() -> None:
    """A regression test for the exact class of bug this repo has been bitten by
    before (``chrome --version`` opening a real window): detection must be pure
    filesystem existence, never a subprocess call."""
    with patch("subprocess.run", side_effect=AssertionError("must never launch a process")):
        with patch("subprocess.Popen", side_effect=AssertionError("must never launch a process")):
            facts = detect_mspaint()
    assert isinstance(facts, DetectionFacts)


def test_paint_capabilities_exclude_layer_when_installed() -> None:
    provider = PaintProvider()
    with patch.object(PaintProvider, "detect", lambda self: DetectionFacts(installed=True)):
        result = provider.capabilities()
    assert result.ok
    assert "layer" not in result.capabilities
    assert set(result.capabilities) == set(PAINT_CAPABILITIES)


def test_paint_dependency_unavailable_when_not_found() -> None:
    provider = PaintProvider()
    with patch.object(
        PaintProvider, "detect", lambda self: DetectionFacts(installed=False, detail="x")
    ):
        result = provider.capabilities()
    assert not result.ok
    assert result.error_class == ERROR_DEPENDENCY_UNAVAILABLE
    assert result.detail["installed"] is False


def test_photoshop_never_launches_and_reports_facts_when_absent() -> None:
    provider = PhotoshopProvider()
    with patch("app.creative.providers._registry_key_present", return_value=False):
        with patch("subprocess.run", side_effect=AssertionError("must never launch")):
            result = provider.capabilities()
    assert not result.ok
    assert result.error_class == ERROR_DEPENDENCY_UNAVAILABLE
    assert "checked" in result.detail


def test_photoshop_registry_key_alone_is_not_installed() -> None:
    """A real bug found on the owner's own machine while writing this provider
    (2026-09-09): Creative Cloud writes a bare ``SOFTWARE\\Adobe\\Photoshop``
    registry key even when Photoshop itself is not installed, so trusting the key's
    mere presence reported ``installed=True`` for a machine that has no
    ``Photoshop.exe`` anywhere on disk — exactly the "imitated, not named" mistake
    ADR-0093 decision 3 forbids. Only the real executable may decide ``installed``."""
    provider = PhotoshopProvider()
    with patch("app.creative.providers._registry_key_present", return_value=True):
        with patch("pathlib.Path.is_file", return_value=False):
            result = provider.capabilities()
    assert not result.ok
    assert result.error_class == ERROR_DEPENDENCY_UNAVAILABLE
    assert result.detail["installed"] is False


def test_illustrator_never_launches_and_reports_facts_when_absent() -> None:
    provider = IllustratorProvider()
    with patch("app.creative.providers._registry_key_present", return_value=False):
        result = provider.capabilities()
    assert not result.ok
    assert result.error_class == ERROR_DEPENDENCY_UNAVAILABLE


def test_photoshop_capabilities_full_set_when_installed() -> None:
    provider = PhotoshopProvider()
    with patch.object(
        PhotoshopProvider,
        "detect",
        lambda self: DetectionFacts(installed=True, detail="found"),
    ):
        result = provider.capabilities()
    assert result.ok
    assert set(result.capabilities) == set(FULL_CAPABILITIES)


def test_figma_without_token_is_dependency_unavailable() -> None:
    provider = FigmaProvider(token_present=False)
    result = provider.capabilities()
    assert not result.ok
    assert result.error_class == ERROR_DEPENDENCY_UNAVAILABLE
    assert "no owner Figma token" in result.detail["detail"]


def test_figma_with_token_is_ok() -> None:
    provider = FigmaProvider(token_present=True)
    result = provider.capabilities()
    assert result.ok
    assert set(result.capabilities) == set(FULL_CAPABILITIES)


def test_default_providers_covers_every_tool() -> None:
    providers = default_providers()
    # B43 (req 496): the layered tool is installed by construction beside the four.
    assert set(providers) == {
        TOOL_PAINT,
        TOOL_PHOTOSHOP,
        TOOL_ILLUSTRATOR,
        TOOL_FIGMA,
        TOOL_LAYERED,
    }


def test_installed_capability_union_is_only_the_installed_ones() -> None:
    paint = PaintProvider()
    with (
        patch.object(PaintProvider, "detect", lambda self: DetectionFacts(installed=True)),
        patch.object(PhotoshopProvider, "detect", lambda self: DetectionFacts(installed=False)),
        patch.object(IllustratorProvider, "detect", lambda self: DetectionFacts(installed=False)),
    ):
        union = installed_capability_union(
            {
                TOOL_PAINT: paint,
                TOOL_PHOTOSHOP: PhotoshopProvider(),
                TOOL_ILLUSTRATOR: IllustratorProvider(),
                TOOL_FIGMA: FigmaProvider(token_present=False),
            }
        )
    assert union == frozenset(PAINT_CAPABILITIES)
