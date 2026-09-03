"""app.devices.capabilities: family marker or per-op name."""

from app.devices.capabilities import family_marker, has_capability, missing_capabilities


def test_exact_capability_match() -> None:
    assert has_capability(["browser.fetch_evidence"], "browser.fetch_evidence") is True


def test_family_marker_satisfies_any_op_in_namespace() -> None:
    assert has_capability(["browser.chrome"], "browser.fetch_evidence") is True
    assert has_capability(["browser.chrome"], "browser.search") is True


def test_missing_capability_is_false() -> None:
    assert has_capability(["desktop.open_application"], "browser.fetch_evidence") is False


def test_family_marker_lookup() -> None:
    assert family_marker("browser") == "browser.chrome"
    assert family_marker("desktop") is None


def test_missing_capabilities_lists_only_unsatisfied() -> None:
    caps = ["browser.chrome", "desktop.open_application"]
    missing = missing_capabilities(caps, ["browser.search", "browser.download_x", "fs.read"])
    assert missing == ["fs.read"]
