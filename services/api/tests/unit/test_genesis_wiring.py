"""M24 wiring: ``app.state.genesis`` is a real ``GenesisRuntime`` built onto the
SAME ``EvolutionRuntime`` (no second engine, no second registry), and the
router is mounted on the real application object.
"""

from __future__ import annotations

import pathlib

from app.config import Settings
from app.evolution.runtime import EvolutionRuntime
from app.genesis.routes import router as genesis_router
from app.genesis.runtime import GenesisRuntime
from app.genesis.service import GenesisService
from app.main import create_app


def test_create_app_wires_a_genesis_runtime():
    app = create_app(Settings(_env_file=None))
    assert isinstance(app.state.genesis, GenesisRuntime)


def test_genesis_service_shares_the_evolution_registry():
    evolution = EvolutionRuntime(Settings(_env_file=None))
    runtime = GenesisRuntime(evolution)
    assert isinstance(runtime.service, GenesisService)
    assert runtime.service.registry is evolution.registry
    assert runtime.service.gaps is evolution.gaps


def test_genesis_router_is_mounted_with_the_expected_paths():
    paths = {route.path for route in genesis_router.routes}
    assert paths == {
        "/v1/genesis/runs",
        "/v1/genesis/runs/{run_id}",
        "/v1/genesis/runs/{run_id}/approve",
        "/v1/genesis/runs/{run_id}/cancel",
    }


def test_genesis_skills_root_and_work_root_are_siblings_under_evolution_roots():
    evolution = EvolutionRuntime(Settings(_env_file=None))
    runtime = GenesisRuntime(evolution)
    assert runtime.skills_root == evolution.skills_root / "genesis"
    assert runtime.work_root == evolution.work_root / "genesis"


# ------------------------------------- what the 2026-09-08 release found on production


def test_building_the_application_creates_no_directory(monkeypatch, tmp_path):
    """The blue/green release of M24 rolled back because the container died before
    uvicorn could load the app: ``create_app`` builds ``GenesisRuntime.service`` eagerly,
    and that property created its skills root — a path that in the image resolves outside
    the app tree, where nothing is writable (``PermissionError: '/skills'``). Building the
    application must touch no filesystem at all; the publish path creates what it needs
    when a run actually publishes.

    Proven by making every ``mkdir`` fail the way the read-only image did, then building
    the real application object.
    """
    from app.config import Settings
    from app.main import create_app

    def _refuse(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(pathlib.Path, "mkdir", _refuse)
    monkeypatch.setenv("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(tmp_path / "never-created"))
    app = create_app(Settings(_env_file=None))
    assert app.state.genesis.service is not None
    assert not (tmp_path / "never-created").exists()


def test_the_default_skills_root_stays_inside_the_app_tree_without_a_checkout(monkeypatch):
    """In an image there is no ``services/api`` above the app tree, and the repository-root
    guess climbs to the filesystem root — which is how the container came to compute
    ``/skills/generated`` and fail to start. Without a checkout above it the default must
    resolve inside the app tree instead."""
    from app.evolution import runtime as evolution_runtime

    monkeypatch.setattr(evolution_runtime, "_REPO_ROOT", pathlib.Path("/"))
    monkeypatch.setattr(evolution_runtime, "_APP_ROOT", pathlib.Path("/srv/pagentos"))
    assert evolution_runtime._default_skills_root() == pathlib.Path(
        "/srv/pagentos/skills/generated"
    )


def test_the_default_skills_root_uses_the_repository_root_in_a_checkout():
    """And in this checkout it still resolves where it always did."""
    from app.evolution import runtime as evolution_runtime

    default = evolution_runtime._default_skills_root()
    assert default.parts[-2:] == ("skills", "generated")
    assert (default.parent.parent / "services" / "api").is_dir()
