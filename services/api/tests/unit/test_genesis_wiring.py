"""M24 wiring: ``app.state.genesis`` is a real ``GenesisRuntime`` built onto the
SAME ``EvolutionRuntime`` (no second engine, no second registry), and the
router is mounted on the real application object.
"""

from __future__ import annotations

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
