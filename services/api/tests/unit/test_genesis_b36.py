"""B36 - the Genesis front door (req 561-565, 569-580).

What was measured before: the catalogue was an in-memory list nothing registered into
(empty in every production process); a capability could be requested only by voice or
by a test calling the service; the interface fetch was loopback-only; generated code
had no security gate and the model was not asked; a capability that existed was reused,
never versioned, never rolled back, never deactivated.

Each seam is proven by executing it against the counter box fixture (a real HTTP
application on a free loopback port), through the real service and, for the routes, the
real application object with an owner session.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.actions.confirmation_gate import CONFIRM_SOURCE_REST, Confirmation
from app.evolution.authorization import StaticAuthorizationProvider
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.genesis import security_gate
from app.genesis.adapter import AdapterSpec, HttpAdapterGenerator
from app.genesis.catalogue import (
    CatalogueStore,
    GenesisInterfaceCatalogue,
    entry_from_dict,
    get_catalogue,
)
from app.genesis.interface import InterfaceDescription, fetch_interface
from app.genesis.model_generator import ScriptedAdapterCodeModel
from app.genesis.runtime import GenesisRuntime
from app.genesis.service import GenesisService
from app.voice.intents import normalize_transcript
from tests.fixtures.genesis import counterbox_app
from tests.unit.genesis_stack import make_stack
from tests.voice_corpus.harness import build_harness

COUNTERBOX_RAW = {**counterbox_app.SPEC_TEMPLATE, "base_url": "http://127.0.0.1:54321"}


def _spec(operation_id: str = "read", **kwargs) -> AdapterSpec:
    return AdapterSpec(
        interface=InterfaceDescription.parse(COUNTERBOX_RAW), operation_id=operation_id, **kwargs
    )


def _service_with_model(stack, model, *, enabled: bool = True) -> GenesisService:
    return GenesisService(
        stack.service._session_factory,
        registry=stack.registry,
        gaps=stack.gaps,
        detector=stack.detector,
        sandbox=stack.sandbox,
        skills_root=stack.skills_root,
        dispatcher=stack.dispatcher,
        mutation_authorization=stack.service.mutation_authorization,
        adapter_model=model,
        model_generation_enabled=enabled,
    )


# ------------------------------------------------------------- the security gate (579/680)


def test_the_gate_names_a_foreign_host_a_disallowed_import_and_system_access_by_line() -> None:
    texts = {
        "src/x.py": (
            "import json\n"
            "import subprocess\n"
            "import requests\n"
            'URL = "http://127.0.0.1:54321/counter"\n'
            'OTHER = "https://evil.example.com/x"\n'
            'open("/tmp/out", "w")\n'
        )
    }
    verdict = security_gate.review_sources(texts, allowed_hosts=("127.0.0.1",))
    found = {(f.check, f.line, f.detail) for f in verdict.findings}
    assert (security_gate.CHECK_IMPORT, 2, "subprocess") in found
    assert (security_gate.CHECK_IMPORT, 3, "requests") in found
    assert (security_gate.CHECK_FOREIGN_HOST, 5, "evil.example.com") in found
    assert (security_gate.CHECK_SYSTEM_ACCESS, 2, "subprocess") in found
    assert (security_gate.CHECK_SYSTEM_ACCESS, 6, "file write") in found
    assert not any(f.detail == "127.0.0.1" for f in verdict.findings)


def test_the_rendered_adapter_passes_the_gate_and_a_model_proposal_is_judged_by_it(
    tmp_path,
) -> None:
    rendered = HttpAdapterGenerator().generate(_spec(), tmp_path / "plain")
    verdict = security_gate.review_layout(rendered, allowed_hosts=("127.0.0.1",))
    assert verdict.passed, verdict.summary()
    assert "src/counterbox_read.py" in verdict.reviewed_paths

    hostile = rendered.module_path.read_text(encoding="utf-8") + "\nimport subprocess\n"
    generator = HttpAdapterGenerator(model=ScriptedAdapterCodeModel(proposal=hostile))
    proposed = generator.generate(_spec(), tmp_path / "model")
    assert generator.model_generated is True
    assert generator.generator_name == "http_adapter+model"
    assert proposed.module_path.read_text(encoding="utf-8").endswith("import subprocess\n")
    refused = security_gate.review_layout(proposed, allowed_hosts=("127.0.0.1",))
    assert not refused.passed
    assert {f.check for f in refused.findings} >= {
        security_gate.CHECK_IMPORT,
        security_gate.CHECK_SYSTEM_ACCESS,
    }


def test_without_a_model_the_generator_says_so_and_asks_nothing(tmp_path) -> None:
    model = ScriptedAdapterCodeModel()
    generator = HttpAdapterGenerator()
    generator.generate(_spec(), tmp_path / "x")
    assert generator.model_generated is False and generator.generator_name == "http_adapter"
    assert model.asked == []


# ------------------------------------------------------------- the model under the flag (577/580)


def test_a_model_written_read_adapter_waits_for_the_owner_and_stays_read_only(tmp_path) -> None:
    stack = make_stack(tmp_path)
    model = ScriptedAdapterCodeModel()  # answers with the rendered module: a valid proposal
    service = _service_with_model(stack, model)
    with counterbox_app.serve() as server:
        result = service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="read",
            session_id="s1",
            turn=1,
        )
        assert result["state"] == "awaiting_approval", result
        assert result["approval_required"] is True
        assert result["side_effect_class"] == "read"
        assert result["evidence"]["model_generated"] is True
        assert result["evidence"]["generator"] == "http_adapter+model"
        assert result["evidence"]["security_review"]["passed"] is True
        assert model.asked == ["counterbox.read"]
        # Nothing is registered before the owner's word.
        assert stack.registry.resolve("counterbox.read") is None

        approved = service.approve(
            uuid.UUID(result["id"]),
            Confirmation(source=CONFIRM_SOURCE_REST, session_id="s1"),
        )
        assert approved["state"] == "verified", approved
        resolved = stack.registry.resolve("counterbox.read")
        assert resolved is not None
        # Approval of the CODE is not an authorization of a mutation (580).
        assert resolved["manifest"]["authority_class"] == "read_only"
        assert resolved["manifest"]["provenance"]["generator"] == "http_adapter+model"
        assert resolved["manifest"]["provenance"]["model_generated"] is True
        assert resolved["manifest"]["provenance"]["security_review_passed"] is True


def test_a_model_proposal_that_reaches_for_the_system_is_refused_and_never_registered(
    tmp_path,
) -> None:
    stack = make_stack(tmp_path)
    rendered = HttpAdapterGenerator().generate(_spec(), tmp_path / "seed")
    hostile = rendered.module_path.read_text(encoding="utf-8") + "\nimport subprocess\n"
    service = _service_with_model(stack, ScriptedAdapterCodeModel(proposal=hostile))
    with counterbox_app.serve() as server:
        result = service.request(
            interface_name="counterbox", interface_url=server.spec_url, operation_id="read"
        )
    assert result["state"] == "failed"
    assert result["error_class"] == "security_refused"
    findings = result["evidence"]["security_review"]["findings"]
    assert any(f["check"] == security_gate.CHECK_SYSTEM_ACCESS for f in findings)
    assert stack.registry.resolve("counterbox.read") is None
    versions = stack.registry.list_skill_versions(capability_id="counterbox.read")
    assert versions and versions[0]["status"] == "rejected"
    assert "security gate" in (versions[0]["rejected_reason"] or "")


def test_with_the_flag_off_the_model_is_never_asked(tmp_path) -> None:
    stack = make_stack(tmp_path)
    model = ScriptedAdapterCodeModel()
    service = _service_with_model(stack, model, enabled=False)
    with counterbox_app.serve() as server:
        result = service.request(
            interface_name="counterbox", interface_url=server.spec_url, operation_id="read"
        )
    assert result["state"] == "verified", result
    assert model.asked == []
    assert result["evidence"]["generator"] == "http_adapter"
    assert result["evidence"]["model_generated"] is False


# ------------------------------------------------------------- the catalogue (562-564)


def _fake_fetch(url: str) -> InterfaceDescription:
    return InterfaceDescription.parse(COUNTERBOX_RAW, source={"kind": "http", "url": url})


def test_the_catalogue_persists_rebuilds_the_spoken_index_and_proposes_from_a_description(
    tmp_path,
) -> None:
    stack = make_stack(tmp_path)
    catalogue = GenesisInterfaceCatalogue()
    store = CatalogueStore(stack.service._session_factory, catalogue=catalogue, fetch=_fake_fetch)
    assert store.list() == []
    assert catalogue.resolve(("sayaç", "kutusunu", "artır")) is None

    proposal = store.discover("http://127.0.0.1:54321/spec")
    assert proposal["name"] == "counterbox"
    assert [op["operation_id"] for op in proposal["operations"]] == ["read", "increment", "reset"]
    assert proposal["read_back"] == "read" and len(proposal["spec_digest"]) == 64

    entry = store.register(
        name=proposal["name"],
        url=proposal["url"],
        target_phrases=["Sayaç Kutusu", "sayaç kutusunu", "sayaç"],
        operations=[{"operation_id": "increment", "verbs": ["artır", "arttır"]}],
        spec_digest=proposal["spec_digest"],
    )
    assert entry["enabled"] is True and entry["source"] == "owner_rest"
    # The in-memory catalogue the router reads is rebuilt at once.
    resolved = catalogue.resolve(normalize_transcript("Sayaç kutusunu bir artır.")[1])
    assert resolved is not None and resolved.name == "counterbox"
    assert resolved.resolve_operation(("artır",)) == "increment"
    # A second process builds the same catalogue from the rows.
    fresh = GenesisInterfaceCatalogue()
    again = CatalogueStore(stack.service._session_factory, catalogue=fresh)
    assert again.load() == 1
    assert fresh.resolve(("sayaç",)) is not None
    # Disabled rows stay listed and leave the spoken index.
    store.disable("counterbox")
    assert store.list()[0]["enabled"] is False
    assert catalogue.resolve(("sayaç",)) is None
    with pytest.raises(LookupError):
        store.disable("nothing")


@pytest.mark.parametrize(
    "raw",
    [
        {"name": "Counter Box", "url": "http://x", "target_phrases": ["a"]},
        {"name": "counterbox", "url": "", "target_phrases": ["a"]},
        {"name": "counterbox", "url": "http://x", "target_phrases": []},
        {
            "name": "counterbox",
            "url": "http://x",
            "target_phrases": ["a"],
            "operations": [{"operation_id": "Bad Op"}],
        },
    ],
)
def test_a_catalogue_entry_is_refused_when_its_shape_is_wrong(raw) -> None:
    with pytest.raises(ValueError):
        entry_from_dict(raw)


# ------------------------------------------------------------- versions, rollback, activation, use


def test_a_second_run_builds_the_next_version_and_the_registry_rolls_back_and_re_activates(
    tmp_path,
) -> None:
    stack = make_stack(tmp_path)
    with counterbox_app.serve() as server:
        first = stack.service.request(
            interface_name="counterbox", interface_url=server.spec_url, operation_id="read"
        )
        assert first["state"] == "verified"
        assert stack.registry.resolve("counterbox.read")["version"] == "0.1.0"
        # Req 571: the same request with new_version builds 0.1.1 and supersedes 0.1.0.
        second = stack.service.request(
            interface_name="counterbox",
            interface_url=server.spec_url,
            operation_id="read",
            new_version=True,
        )
        assert second["new_run"] is True and second["state"] == "verified", second
        assert second["evidence"]["requested_version"] == "0.1.1"
        assert stack.registry.resolve("counterbox.read")["version"] == "0.1.1"
        listed = stack.service.versions("counterbox.read")
        assert sorted(v["version"] for v in listed["versions"]) == ["0.1.0", "0.1.1"]
        assert listed["capability"]["version"] == "0.1.1"
        # Req 572: rollback serves 0.1.0 again; nothing deleted.
        rolled = stack.service.rollback("counterbox.read", "0.1.0")
        assert rolled["version"] == "0.1.0"
        assert stack.registry.resolve("counterbox.read")["version"] == "0.1.0"
        with pytest.raises(EvolutionError):
            stack.service.rollback("counterbox.read", "9.9.9")
        # Req 570: deactivated = does not resolve; activated = the current version again.
        stack.service.deactivate("counterbox.read")
        assert stack.registry.resolve("counterbox.read") is None
        with pytest.raises(EvolutionError) as missing:
            stack.service.use("counterbox.read", {})
        assert missing.value.error_class is EvolutionErrorClass.CAPABILITY_MISSING
        stack.service.activate("counterbox.read")
        assert stack.registry.resolve("counterbox.read")["version"] == "0.1.0"
        # Req 573: the production use path dispatches the registered adapter.
        used = stack.service.use("counterbox.read", {})
        assert used["new_run"] is False and used["output"]["value"] == server.value
        with pytest.raises(EvolutionError):
            stack.service.versions("nothing.here")


# ------------------------------------------------------------- the host rule (565)


def test_a_non_loopback_host_is_researched_only_when_the_owner_authorized_it() -> None:
    with pytest.raises(EvolutionError) as refused:
        fetch_interface("https://lamp.example.invalid/spec")
    assert refused.value.error_class is EvolutionErrorClass.VALIDATION_ERROR
    # With the predicate saying yes, the url is accepted and the fetch is attempted (and
    # fails as unreachable, which is a different class: the network, not the rule).
    with pytest.raises(EvolutionError) as unreachable:
        fetch_interface(
            "https://lamp.example.invalid/spec", host_allowed=lambda h: h == "lamp.example.invalid"
        )
    assert unreachable.value.error_class is EvolutionErrorClass.DEPENDENCY_UNAVAILABLE
    # The predicate never loosens the loopback rules.
    with pytest.raises(EvolutionError) as privileged:
        fetch_interface("http://127.0.0.1:80/spec", host_allowed=lambda h: True)
    assert privileged.value.error_class is EvolutionErrorClass.VALIDATION_ERROR


def test_the_runtime_answers_the_host_rule_from_the_owners_asset_registry() -> None:
    evolution = SimpleNamespace(
        authorization=StaticAuthorizationProvider(
            {
                "lamp.example.invalid": {"network_permissions": ["lamp.example.invalid"]},
                "other.example.invalid": {"network_permissions": []},
            }
        ),
        session=None,
        settings=SimpleNamespace(anthropic_api_key=""),
        skills_root=None,
        work_root=None,
    )
    runtime = GenesisRuntime(evolution)  # type: ignore[arg-type]
    assert runtime.host_allowed("lamp.example.invalid") is True
    assert runtime.host_allowed("other.example.invalid") is False  # enrolled, no grant
    assert runtime.host_allowed("unknown.example.invalid") is False
    assert (
        GenesisRuntime(evolution, authorized_hosts_enabled=False).host_allowed(  # type: ignore[arg-type]
            "lamp.example.invalid"
        )
        is False
    )


# ------------------------------------------------------------- the REST surface (561/574)


def test_the_front_door_over_the_real_application_object() -> None:
    h = build_harness()
    with counterbox_app.serve() as server:
        # Nothing registered: a request without a url is refused by name.
        refused = h.client.post(
            "/v1/genesis/runs", json={"interface_name": "counterbox", "operation_id": "read"}
        )
        assert refused.status_code == 422
        assert "not in the catalogue" in refused.json()["detail"]["message"]

        proposal = h.client.post("/v1/genesis/catalogue/discover", json={"url": server.spec_url})
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["proposal"]["name"] == "counterbox"

        registered = h.client.post(
            "/v1/genesis/catalogue",
            json={
                "name": "counterbox",
                "url": server.spec_url,
                "target_phrases": ["sayaç kutusu", "sayaç"],
                "operations": [{"operation_id": "read", "verbs": ["kaç"]}],
                "spec_digest": proposal.json()["proposal"]["spec_digest"],
            },
        )
        assert registered.status_code == 201, registered.text
        listed = h.client.get("/v1/genesis/catalogue").json()["entries"]
        assert [e["name"] for e in listed] == ["counterbox"]
        # The router's own catalogue sees it at once.
        assert get_catalogue().resolve(("sayaç", "kaç")) is not None

        bad = h.client.post(
            "/v1/genesis/catalogue",
            json={
                "name": "counterbox",
                "url": server.spec_url,
                "target_phrases": ["x"],
                "operations": [{"operation_id": "Bad Op"}],
            },
        )
        assert bad.status_code == 422
        assert "refused" in bad.json()["detail"]["message"]
        foreign = h.client.post(
            "/v1/genesis/catalogue",
            json={
                "name": "foreign",
                "url": "https://evil.example.invalid/spec",
                "target_phrases": ["x"],
            },
        )
        assert foreign.status_code == 422

        run = h.client.post(
            "/v1/genesis/runs", json={"interface_name": "counterbox", "operation_id": "read"}
        )
        assert run.status_code == 201, run.text
        assert run.json()["state"] == "verified"
        assert run.json()["evidence"]["security_review"]["passed"] is True

        versions = h.client.get("/v1/genesis/capabilities/counterbox.read/versions")
        assert versions.status_code == 200
        assert [v["version"] for v in versions.json()["versions"]] == ["0.1.0"]
        used = h.client.post("/v1/genesis/capabilities/counterbox.read/use", json={"arguments": {}})
        assert used.status_code == 200 and used.json()["output"]["value"] == server.value
        off = h.client.post("/v1/genesis/capabilities/counterbox.read/deactivate")
        assert off.status_code == 200 and off.json()["capability"]["status"] == "deprecated"
        gone = h.client.post("/v1/genesis/capabilities/counterbox.read/use", json={})
        assert gone.status_code == 422
        on = h.client.post("/v1/genesis/capabilities/counterbox.read/activate")
        assert on.status_code == 200 and on.json()["capability"]["status"] == "production"
        rollback_missing = h.client.post(
            "/v1/genesis/capabilities/counterbox.read/rollback", json={"version": "9.9.9"}
        )
        assert rollback_missing.status_code == 404
        unknown = h.client.get("/v1/genesis/capabilities/nothing.here/versions")
        assert unknown.status_code == 404

        disabled = h.client.delete("/v1/genesis/catalogue/counterbox")
        assert disabled.status_code == 200 and disabled.json()["entry"]["enabled"] is False
        assert get_catalogue().resolve(("sayaç",)) is None
        assert h.client.delete("/v1/genesis/catalogue/nothing").status_code == 404


def test_every_new_genesis_route_requires_an_owner_session() -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    with TestClient(app) as anonymous:
        for method, path in (
            ("POST", "/v1/genesis/runs"),
            ("GET", "/v1/genesis/catalogue"),
            ("POST", "/v1/genesis/catalogue"),
            ("POST", "/v1/genesis/catalogue/discover"),
            ("GET", "/v1/genesis/capabilities/x.y/versions"),
            ("POST", "/v1/genesis/capabilities/x.y/use"),
        ):
            assert anonymous.request(method, path, json={}).status_code == 401, (method, path)
