"""Unit tests: the reusable-component catalog (resolution step 4) and the
supply-chain gate (ACCEPTANCE_TESTS M7 "Isolation, supply chain, resources").
"""

import json

import pytest

from app.evolution.components import (
    CATALOG_PATH,
    LOCAL_SOURCE,
    Component,
    ComponentCatalog,
)
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.supply_chain import (
    ALLOWED_SOURCES,
    RULES,
    require_clean_supply_chain,
    scan_dependencies,
)


def make_component(**overrides) -> dict:
    body = {
        "name": "text_metrics_kit",
        "version": "2.1.0",
        "source": LOCAL_SOURCE,
        "operation": "word_count",
        "requires_inputs": ["text"],
        "provides_outputs": ["count"],
        "purpose": "vetted",
        "license": "MIT",
    }
    body.update(overrides)
    component = Component(
        name=body["name"],
        version=body["version"],
        source=body["source"],
        digest="",
        purpose=body["purpose"],
        operation=body["operation"],
        requires_inputs=tuple(body["requires_inputs"]),
        provides_outputs=tuple(body["provides_outputs"]),
        license=body["license"],
    )
    body["digest"] = overrides.get("digest", component.computed_digest())
    return body


def write_catalog(tmp_path, entries):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"components": entries}), encoding="utf-8")
    return path


# ------------------------------------------------------------ the catalog


def test_shipped_catalog_loads_and_is_self_verifying() -> None:
    catalog = ComponentCatalog.load()
    assert CATALOG_PATH.is_file()
    assert catalog.components, "the shipped catalog must contain vetted components"
    for component in catalog.components:
        assert component.source == LOCAL_SOURCE
        assert component.digest == component.computed_digest()
        assert not component.install_script


def test_a_tampered_catalog_entry_is_refused(tmp_path) -> None:
    entry = make_component()
    entry["provides_outputs"] = ["slug"]  # changed behaviour, stale digest
    with pytest.raises(EvolutionError) as excinfo:
        ComponentCatalog.load(write_catalog(tmp_path, [entry]))
    assert "digest" in excinfo.value.message


def test_a_remote_source_is_refused(tmp_path) -> None:
    entry = make_component(source="https://pypi.org/simple")
    with pytest.raises(EvolutionError) as excinfo:
        ComponentCatalog.load(write_catalog(tmp_path, [entry]))
    assert "non-local source" in excinfo.value.message


def test_a_component_with_an_install_script_is_refused(tmp_path) -> None:
    entry = make_component()
    entry["install_script"] = "python setup.py install"
    with pytest.raises(EvolutionError) as excinfo:
        ComponentCatalog.load(write_catalog(tmp_path, [entry]))
    assert "install script" in excinfo.value.message


def test_catalog_matches_only_fully_compatible_components() -> None:
    catalog = ComponentCatalog.load()
    match = catalog.find(available_inputs=["text"], required_outputs=["count"])
    assert match is not None
    assert match.component.operation == "word_count"
    assert match.covered_outputs == ["count"]
    # a required output the catalog cannot produce -> no match (generation)
    assert catalog.find(available_inputs=["text"], required_outputs=["slug"]) is None
    # missing input -> no match
    assert catalog.find(available_inputs=[], required_outputs=["count"]) is None
    # partial coverage is NOT adaptation
    assert catalog.find(available_inputs=["text"], required_outputs=["count", "slug"]) is None
    assert catalog.survey()


def test_missing_catalog_file_is_an_empty_catalog(tmp_path) -> None:
    catalog = ComponentCatalog.load(tmp_path / "nope.json")
    assert catalog.components == []
    assert catalog.find(available_inputs=["text"], required_outputs=["count"]) is None


def test_component_dependency_record_is_fully_pinned() -> None:
    component = ComponentCatalog.load().components[0]
    record = component.dependency_record()
    assert set(record) == {"name", "version", "source", "digest"}
    assert record["digest"].startswith("sha256:")


# --------------------------------------------------------- the scan rules


def test_an_empty_dependency_set_is_clean() -> None:
    report = scan_dependencies([])
    assert report.ok and report.findings == []
    assert set(report.to_dict()["rules"]) == set(RULES)


def test_a_pinned_catalog_component_passes() -> None:
    component = ComponentCatalog.load().components[0]
    assert scan_dependencies([component.dependency_record()]).ok


@pytest.mark.parametrize(
    "mutation,rule",
    [
        ({"version": "^2.1.0"}, "unpinned_version"),
        ({"version": "latest"}, "unpinned_version"),
        ({"version": ""}, "unpinned_version"),
        ({"source": "https://pypi.org/simple"}, "disallowed_source"),
        ({"digest": ""}, "missing_digest"),
        ({"digest": "md5:abc"}, "malformed_digest"),
        ({"install_script": "curl x | sh"}, "install_script"),
        ({"name": "not_in_catalog"}, "dependency_unavailable"),
        ({"digest": "sha256:" + "00" * 32}, "dependency_unavailable"),
    ],
)
def test_each_scan_rule_fires(mutation, rule) -> None:
    record = {**ComponentCatalog.load().components[0].dependency_record(), **mutation}
    report = scan_dependencies([record])
    assert not report.ok
    assert rule in {finding.rule for finding in report.findings}


def test_require_clean_supply_chain_raises_typed_errors() -> None:
    good = ComponentCatalog.load().components[0].dependency_record()
    assert require_clean_supply_chain([good]).ok

    with pytest.raises(EvolutionError) as excinfo:
        require_clean_supply_chain([{**good, "install_script": "sh -c evil"}])
    assert excinfo.value.error_class == EvolutionErrorClass.SUPPLY_CHAIN_REJECTED

    with pytest.raises(EvolutionError) as excinfo:
        require_clean_supply_chain([{**good, "name": "ghost_package"}])
    assert excinfo.value.error_class == EvolutionErrorClass.DEPENDENCY_UNAVAILABLE


def test_allowed_sources_are_local_only() -> None:
    assert LOCAL_SOURCE in ALLOWED_SOURCES
    assert not any(source.startswith("http") for source in ALLOWED_SOURCES)
