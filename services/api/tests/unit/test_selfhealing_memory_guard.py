"""ADR-0023 guard: the engineering loop may READ memory but must NEVER mutate
explicit owner memories or recovery roots.

Grep-level enforcement over the selfhealing package sources: no owner-actor
usage, no /remember surface, no memory mutation imports. If a future change
legitimately needs memory *observations*, it must go through the non-explicit
observation path and extend this guard consciously.
"""

from pathlib import Path

SELFHEALING_DIR = Path(__file__).resolve().parents[2] / "app" / "selfhealing"

# Owner-authority memory mutation surface (see app.memory.routes/service).
FORBIDDEN_SUBSTRINGS = (
    "Actor.OWNER",
    "/remember",
    ".remember(",
    ".forget(",
    ".supersede_memory(",
    "MemoryLinks",
    "explicit=True",
)

# The selfhealing package must not import the memory subsystem at all in M6
# (read-paths can be added later, but never the mutation service silently).
FORBIDDEN_IMPORTS = (
    "from app.memory",
    "import app.memory",
)


def test_selfhealing_sources_exist() -> None:
    sources = list(SELFHEALING_DIR.glob("*.py"))
    assert len(sources) >= 7  # __init__, errors, models, monitoring, service, ...


def test_pipeline_never_touches_owner_memory_mutation() -> None:
    offenders: list[str] = []
    for source_file in SELFHEALING_DIR.glob("*.py"):
        text = source_file.read_text(encoding="utf-8")
        for needle in FORBIDDEN_SUBSTRINGS + FORBIDDEN_IMPORTS:
            if needle in text:
                offenders.append(f"{source_file.name}: {needle}")
    assert offenders == [], (
        "selfhealing engineering loop must never use the owner-actor memory "
        f"mutation surface (ADR-0023); found: {offenders}"
    )


def test_supervisor_project_is_stdlib_only() -> None:
    """The Recovery Supervisor must survive a broken main release: no runtime
    dependencies, no imports of the app package."""
    supervisor_dir = (
        Path(__file__).resolve().parents[4] / "services" / "recovery-supervisor"
    )
    pyproject = (supervisor_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    for source_file in (supervisor_dir / "recovery_supervisor").glob("*.py"):
        text = source_file.read_text(encoding="utf-8")
        assert "from app" not in text and "import app." not in text, source_file.name
        assert "sqlalchemy" not in text.lower(), source_file.name
        assert "anthropic" not in text.lower(), source_file.name
