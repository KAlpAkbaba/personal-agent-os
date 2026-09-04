"""Shared fixtures for the Self Model tests: a miniature repository on disk.

The fixture tree is a *real* directory the indexer walks the same way it walks
the checkout -- same trees, same suffixes, same doc globs -- so the tests
exercise the production code path rather than a stubbed one, while staying
small enough to assert on exactly.

Layout (mirrors ``DEFAULT_TREES``)::

    services/api/app/__init__.py
    services/api/app/observer/__init__.py
    services/api/app/observer/diagnostic_observer.py   class/function/route/table
    services/api/app/observer/helpers.py               import + call target
    services/api/tests/unit/test_observer_diagnostic_observer.py
    docs/DECISIONS.md                                  ADR-0099 mentions the module
    docs/M99_OBSERVER_SPEC.md                          spec mentions the package
    services/browser/browser_agent/worker.py           foreign python
    apps/web/app/lib/observer.ts                       foreign typescript
    devices/windows-agent/Agent.cs                     foreign c#
    scripts/Deploy-Observer.ps1                        foreign powershell
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from app.ledger.models import ActivityEventRow
from app.selfhealing.models import Incident, Release
from app.selfmodel.models import CodeEdge, CodeModule, CodeSymbol, ModuleProvenance

#: The module the fixture tree is built around. Named so that "Diagnostic
#: Observer", spoken in Turkish, has something real to resolve to.
FIXTURE_MODULE = "app.observer.diagnostic_observer"
FIXTURE_PACKAGE = "app.observer"
FIXTURE_TEST_MODULE = "tests.unit.test_observer_diagnostic_observer"

_OBSERVER_SOURCE = '''"""Watches subsystem health and records what it saw.

Longer prose that must never reach the index: only the first line is a purpose.
"""

from fastapi import APIRouter
from sqlalchemy.orm import Mapped, mapped_column

from app.observer import helpers

router = APIRouter(prefix="/v1/observer")

OBSERVER_VERSION = 3


class ObservationRow:
    """One recorded observation."""

    __tablename__ = "observations"

    def summarize(self) -> str:
        """One line about this observation."""
        return "observed"


def observe(target: str, *, deep: bool = False) -> dict[str, str]:
    """Look at one target and return what was seen."""
    return {"target": helpers.normalize(target), "deep": str(deep)}


def connect(endpoint: str, *, api_key: str = "sk-live-NOT-A-REAL-SECRET", retries: int = 2) -> None:
    """Connect to the upstream observer feed."""


@router.get("/status")
async def read_status(auth_token: str = "hdr-NOT-A-REAL-SECRET") -> dict[str, str]:
    """Current observer status."""
    return {"status": "ok"}
'''

_HELPERS_SOURCE = '''"""Small pure helpers for the observer."""


def normalize(value: str) -> str:
    """Lowercase and strip."""
    return value.strip().lower()
'''

_PACKAGE_SOURCE = '''"""Diagnostic observation subsystem."""
'''

_TEST_SOURCE = '''"""Tests for the diagnostic observer."""


def test_observe() -> None:
    assert True
'''

_DECISIONS = """# Architecture Decision Log

## ADR-0098 - Something unrelated

Decision: unrelated to the observer.

## ADR-0099 - Diagnostic observer records evidence

Status: Accepted

Decision: `app/observer/diagnostic_observer.py` records what it saw rather than
what it inferred, so a later explanation can cite a row.

Reason: an observation without evidence is a guess.
"""

_SPEC = """# M99 Observer Spec

The observer package `app/observer` owns subsystem health observation.
"""

_WORKER = '''"""Browser worker (foreign tree: indexed by name and path only)."""
'''

_TYPESCRIPT = "export const observerName = 'diagnostic';\n"
_CSHARP = "namespace Agent { public class Observer { } }\n"
_POWERSHELL = "Write-Output 'deploy observer'\n"


def write_fixture_tree(root: Path) -> Path:
    """Materialize the miniature repository under ``root``; returns ``root``."""
    files = {
        "services/api/app/__init__.py": '"""Fixture API package."""\n',
        "services/api/app/observer/__init__.py": _PACKAGE_SOURCE,
        "services/api/app/observer/diagnostic_observer.py": _OBSERVER_SOURCE,
        "services/api/app/observer/helpers.py": _HELPERS_SOURCE,
        "services/api/tests/__init__.py": "",
        "services/api/tests/unit/__init__.py": "",
        "services/api/tests/unit/test_observer_diagnostic_observer.py": _TEST_SOURCE,
        "docs/DECISIONS.md": _DECISIONS,
        "docs/M99_OBSERVER_SPEC.md": _SPEC,
        "services/browser/browser_agent/worker.py": _WORKER,
        "apps/web/app/lib/observer.ts": _TYPESCRIPT,
        "devices/windows-agent/Agent.cs": _CSHARP,
        "scripts/Deploy-Observer.ps1": _POWERSHELL,
    }
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    # A directory the indexer must refuse to descend into.
    noise = root / "apps/web/node_modules/junk"
    noise.mkdir(parents=True, exist_ok=True)
    (noise / "index.ts").write_text("export const junk = 1;\n", encoding="utf-8")
    return root


#: Every table a self-model test may touch. The evidence tables are created
#: alongside the four self-model tables because the indexer reads them when
#: they exist -- and several tests turn on what happens when they are empty.
ALL_TABLES = [
    CodeModule.__table__,
    CodeSymbol.__table__,
    CodeEdge.__table__,
    ModuleProvenance.__table__,
    Release.__table__,
    Incident.__table__,
    ActivityEventRow.__table__,
]


def make_engine(*, with_evidence_tables: bool = True) -> Engine:
    """A SQLite engine with the self-model tables created from metadata.

    ``with_evidence_tables=False`` reproduces a deployment whose ledger and
    self-healing tables are not present, which the indexer must survive.
    """
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    tables = ALL_TABLES if with_evidence_tables else ALL_TABLES[:4]
    for table in tables:
        table.create(engine)
    return engine


__all__ = [
    "ALL_TABLES",
    "FIXTURE_MODULE",
    "FIXTURE_PACKAGE",
    "FIXTURE_TEST_MODULE",
    "make_engine",
    "write_fixture_tree",
]
