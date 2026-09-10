"""Build the self-model index from the checkout by static analysis only.

Hard rules this module is built around:

- **No model context.** Nothing here calls an LLM or the network. The index is
  produced by :mod:`ast` and by filename/path shape, so it is deterministic and
  costs nothing to re-run.
- **No whole repository in memory.** Exactly one file is held at a time, only
  when it is about to be parsed, and only if it is under
  :data:`MAX_FILE_BYTES`. What survives the parse is extracted structure --
  names, kinds, signatures, line numbers, one docstring line -- never a body.
- **Only ``services/api`` is parsed.** The other trees (``services/browser``,
  ``apps/web``, ``devices``, ``scripts``) are scanned for name, path and kind.
  Guessing at TypeScript or C# semantics with a Python parser would put fiction
  into a table whose whole purpose is to be trustworthy.
- **Source truth never becomes runtime truth.** The walk writes ``source``
  provenance. ``installed``/``runtime`` rows are written by
  :func:`record_evidence_provenance` and only where a real row -- a ``releases``
  record, or a ledger ``deployment.*`` event carrying a module block something
  running reported back -- says so. This is the defect
  ``services/browser/browser_agent/release.py`` documents: an installer that
  verified the source tree while the live worker ran something else.
- **Incremental.** A module whose ``(size, mtime_ns)`` fingerprint is unchanged
  is not opened, not parsed and not written. A second run over an untouched
  checkout performs zero database writes, which
  ``tests/unit/test_selfmodel_indexer.py`` asserts on the report counters.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.selfmodel.models import (
    EDGE_CALLS,
    EDGE_DOCUMENTED_BY,
    EDGE_IMPLEMENTS_CAPABILITY,
    EDGE_IMPORTS,
    EDGE_RELEASED_AS,
    EDGE_TESTS,
    EDGE_USES_CAPABILITY,
    EVIDENCE_ONLY_TRUTHS,
    MODULE_KIND_CLIENT,
    MODULE_KIND_MODULE,
    MODULE_KIND_PACKAGE,
    MODULE_KIND_SCRIPT,
    MODULE_KIND_SERVICE,
    PRODUCTION_STATE_INSTALLED,
    PRODUCTION_STATE_RUNNING,
    PRODUCTION_STATE_SHADOW,
    PRODUCTION_STATE_SOURCE_ONLY,
    SYMBOL_KIND_CAPABILITY,
    SYMBOL_KIND_CLASS,
    SYMBOL_KIND_CONSTANT,
    SYMBOL_KIND_FUNCTION,
    SYMBOL_KIND_METHOD,
    SYMBOL_KIND_ROUTE,
    SYMBOL_KIND_TABLE,
    SYMBOL_KIND_TOOL,
    TRUTH_EVIDENCE,
    TRUTH_INSTALLED,
    TRUTH_RUNTIME,
    TRUTH_SOURCE,
    CodeEdge,
    CodeModule,
    CodeSymbol,
    ModuleProvenance,
    symbol_id_for,
)
from app.selfmodel.progress import IndexProgress

logger = get_logger("app.selfmodel.indexer")

# ------------------------------------------------------------------- bounds

#: Never open a file larger than this. A 400 KB Python file is already far
#: outside anything this repository writes; anything bigger is data, a vendored
#: bundle or a mistake, and parsing it would be unbounded work for no facts.
MAX_FILE_BYTES: Final[int] = 400_000
#: Upper bound on rows one index run may create, so a stray directory (a build
#: output, a copied venv) can never turn into a hundred thousand modules.
MAX_MODULES: Final[int] = 8_000
MAX_SYMBOLS_PER_MODULE: Final[int] = 400
MAX_SIGNATURE_CHARS: Final[int] = 400
MAX_DOCSTRING_CHARS: Final[int] = 200
#: Documentation files are streamed line by line; this caps a single doc file.
MAX_DOC_BYTES: Final[int] = 4_000_000

#: Directory names never descended into.
EXCLUDED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".claude",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".next",
        ".turbo",
        "dist",
        "build",
        "out",
        "obj",
        "bin",
        "coverage",
        "htmlcov",
        ".idea",
        ".vs",
        ".vscode",
    }
)

HTTP_VERBS: Final[frozenset[str]] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}
)
_CAPABILITY_DECORATORS: Final[frozenset[str]] = frozenset({"capability", "register_capability"})
_TOOL_DECORATORS: Final[frozenset[str]] = frozenset({"tool", "register_tool"})
_CONSTANT_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{1,}$")

#: How a capability is DECLARED in this repository. The decorator sets above are
#: kept -- they cost nothing -- but nothing here has ever used them, which is why
#: the capability layer of this index held zero rows from the day it was written
#: until 2026-09-10, while the other layers held 222 modules and 3813 symbols.
#: The real form is a constructor call::
#:
#:     reg.register(ToolSpec(name=TOOL_TYPE, ..., handler=operator_type))
#:
#: ``name`` is the capability the owner's voice reaches; ``handler`` is the
#: function that answers it, in this same file. That pair is the whole point of
#: the layer: "operator.type failed" has to become a file and a line without
#: reading the tree.
_CAPABILITY_CONSTRUCTORS: Final[frozenset[str]] = frozenset({"ToolSpec"})
#: Deliberately a copy of ``app.evolution.tokens.CAPABILITY_ID_RE`` rather than an
#: import: a map of the system must not depend on the subsystem it maps.
_CAPABILITY_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,4}$"
)
#: A constant's value is kept in its signature only up to here. Long enough for
#: every capability id in the repository, short enough that no file's contents
#: can be smuggled into the index one constant at a time.
MAX_CONSTANT_VALUE_CHARS: Final[int] = 120
#: ...and only this many constants per module are carried, for the same reason
#: ``MAX_SYMBOLS_PER_MODULE`` exists. Highest in this repository: 118.
MAX_CONSTANTS_PER_MODULE: Final[int] = 400
#: Unresolved cross-file capability names carried out of one parse. Highest in
#: this repository: 4.
MAX_CAPABILITY_REFS_PER_MODULE: Final[int] = 100
#: Capability ids read out of module-level dict literals. Highest in this
#: repository: 108 (``app/voice/intents.py``'s utterance-to-capability tables).
MAX_MAPPED_CAPABILITIES_PER_MODULE: Final[int] = 400


# -------------------------------------------------------------- tree layout


@dataclass(frozen=True, slots=True)
class TreeSpec:
    """One scanned tree of the repository.

    ``parse`` is the switch between the two halves of this indexer: the API's
    Python tree is parsed for real structure, everything else contributes a
    name, a path and a kind.
    """

    #: repo-relative POSIX root.
    root: str
    kind: str
    language: str
    suffixes: tuple[str, ...]
    parse: bool = False
    #: when set, module ids are dotted and rooted here (``app``, ``tests``)
    #: instead of being the repo-relative path.
    dotted_root: str | None = None
    #: fixed owner_area; when ``None`` it is derived from the path.
    owner_area: str | None = None


#: Scanned in this order. ``services/api/app`` first so its dotted module ids
#: exist before test and documentation linking runs.
DEFAULT_TREES: Final[tuple[TreeSpec, ...]] = (
    TreeSpec(
        root="services/api/app",
        kind=MODULE_KIND_MODULE,
        language="python",
        suffixes=(".py",),
        parse=True,
        dotted_root="app",
    ),
    TreeSpec(
        root="services/api/tests",
        kind=MODULE_KIND_MODULE,
        language="python",
        suffixes=(".py",),
        parse=False,
        dotted_root="tests",
        owner_area="tests",
    ),
    TreeSpec(
        root="services/browser",
        kind=MODULE_KIND_SERVICE,
        language="python",
        suffixes=(".py",),
        owner_area="browser",
    ),
    TreeSpec(
        root="apps/web",
        kind=MODULE_KIND_CLIENT,
        language="typescript",
        suffixes=(".ts", ".tsx"),
        owner_area="web",
    ),
    TreeSpec(
        root="devices",
        kind=MODULE_KIND_SERVICE,
        language="csharp",
        suffixes=(".cs",),
        owner_area="windows_agent",
    ),
    TreeSpec(
        root="scripts",
        kind=MODULE_KIND_SCRIPT,
        language="powershell",
        suffixes=(".ps1", ".psm1"),
        owner_area="scripts",
    ),
)

#: Documentation grepped for module references. ``DECISIONS.md`` yields
#: ``ADR-xxxx`` refs; the spec files yield ``spec_refs``.
DEFAULT_DOC_GLOBS: Final[tuple[str, ...]] = ("docs/DECISIONS.md", "docs/*SPEC*.md")

#: How a ``releases.component`` / ledger subsystem name maps onto module ids.
#: A component that matches none of these links to nothing rather than to a
#: plausible-looking neighbour.
COMPONENT_MODULE_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "cloud_core": ("app",),
    "cloud-core": ("app",),
    "api": ("app",),
    "agent": ("devices",),
    "device_service": ("devices",),
    "windows_agent": ("devices",),
    "browser": ("services/browser",),
    "browser_worker": ("services/browser",),
    "web": ("apps/web",),
}


#: The layout a DEPLOYED Cloud Core has: the image ships the ``services/api`` subtree only,
#: so there is no ``services/`` and no ``docs/`` to walk. Without this the indexer scanned
#: nothing in production and the self model answered "I have not indexed my own code" while
#: running from that very code (M17 deployment, 2026-09-05). A self model that can only
#: describe a developer checkout is not describing the thing that is running.
DEPLOYED_TREES: Final[tuple[TreeSpec, ...]] = (
    TreeSpec(
        root="app",
        kind=MODULE_KIND_MODULE,
        language="python",
        suffixes=(".py",),
        parse=True,
        dotted_root="app",
    ),
)


def detect_layout(root: Path) -> str:
    """``"repo"`` for a developer checkout, ``"deployed"`` for a shipped image, else
    ``"unknown"`` - and an unknown layout indexes nothing rather than guessing."""
    if (root / "services").is_dir() and (root / "docs").is_dir():
        return "repo"
    if (root / "app").is_dir() and (root / "app" / "__init__.py").is_file():
        return "deployed"
    return "unknown"


@dataclass(frozen=True, slots=True)
class IndexConfig:
    repo_root: Path
    trees: tuple[TreeSpec, ...] = DEFAULT_TREES
    doc_globs: tuple[str, ...] = DEFAULT_DOC_GLOBS
    max_file_bytes: int = MAX_FILE_BYTES
    max_modules: int = MAX_MODULES
    #: ``"stat"`` fingerprints on ``(size, mtime_ns)`` and never opens an
    #: unchanged file; ``"content"`` hashes every candidate (slower, immune to
    #: mtime-preserving edits). ``"stat"`` is the default because the whole
    #: point of the incremental path is to not read.
    fingerprint_mode: str = "stat"


def default_repo_root(start: Path | None = None) -> Path:
    """Walk up from this file to the checkout root.

    Deliberately not caller-supplied anywhere in the HTTP surface: an
    owner-session caller must not be able to point the indexer at an arbitrary
    directory (same reasoning as the workspace-root restriction in
    ``app/selfhealing/routes.py``). ``PAGENTOS_SELFMODEL_REPO_ROOT`` overrides
    it for operators, not for requests.
    """
    override = os.environ.get("PAGENTOS_SELFMODEL_REPO_ROOT")
    if override:
        return Path(override).resolve()
    here = (start or Path(__file__)).resolve()
    for candidate in here.parents:
        if (candidate / "services").is_dir() and (candidate / "docs").is_dir():
            return candidate
    # No checkout above us: this is a deployed image, whose root is the directory that
    # holds the ``app`` package (``/srv/pagentos``), not its grandparent.
    for candidate in here.parents:
        if (candidate / "app" / "__init__.py").is_file():
            return candidate
    return here.parents[3] if len(here.parents) > 3 else here.parent


# --------------------------------------------------------------- extraction


@dataclass(slots=True)
class SymbolFact:
    name: str
    kind: str
    signature: str = ""
    lineno: int = 0
    docstring_summary: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    """A capability declared by a constant that lives in another file.

    ``ToolSpec(name=actions.TOOL_STATE_NOW, handler=state_now)`` -- the id is a
    string, but not one this parse can read, because exactly one file is open at
    a time and it is not that one. Four of this repository's tools are named this
    way, ``release.promote`` among them, so dropping the shape would leave a hole
    in the map precisely where self-development lives.
    """

    #: dotted reference, e.g. ``app.voice.realtime_sessions.actions.TOOL_STATE_NOW``
    ref: str
    handler: str | None
    lineno: int


@dataclass(slots=True)
class ModuleFacts:
    """Everything the indexer keeps about one file after the file is closed."""

    module_id: str
    kind: str
    path: str
    language: str
    owner_area: str
    fingerprint: str
    size: int
    purpose: str | None = None
    digest: str | None = None
    symbols: list[SymbolFact] = field(default_factory=list)
    #: raw dotted import targets, resolved against known module ids later.
    imports: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    #: capability ids this file ANSWERS -- one per registered tool.
    capabilities: list[str] = field(default_factory=list)
    #: capability ids this file DISPATCHES, to the device or another executor.
    capabilities_used: list[str] = field(default_factory=list)
    #: ``ToolSpec`` declarations whose capability id is a constant defined in
    #: another file; carried unresolved out of the parse and settled in
    #: :meth:`Indexer._settle_capability_refs`, where every module is available.
    capability_refs: list[CapabilityRef] = field(default_factory=list)
    #: module-level ``NAME = "value"`` bindings, for that same resolution.
    string_constants: dict[str, str] = field(default_factory=dict)
    #: set when the file was skipped (too large / unparsable) -- recorded, never
    #: silently dropped, so an unindexed file is a visible fact.
    note: str | None = None


def _first_line(text: str | None, limit: int = MAX_DOCSTRING_CHARS) -> str | None:
    if not text:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line[:limit]
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unparse(node: ast.AST | None, limit: int = MAX_SIGNATURE_CHARS) -> str:
    """Render an AST fragment as source text.

    Parameter lists are redacted here rather than at each call site, so every
    path that renders a signature -- plain function, method, route handler --
    is covered by construction rather than by remembering.
    """
    if node is None:
        return ""
    if isinstance(node, ast.arguments):
        _redact_secret_defaults(node)
    try:
        return ast.unparse(node)[:limit]
    except Exception:  # noqa: BLE001 - a signature is never worth an exception
        return ""


def _decorator_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for dec in getattr(node, "decorator_list", []) or []:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def _router_prefix(tree: ast.Module) -> str:
    """The ``APIRouter(prefix=...)`` of a routes module, so a route symbol reads
    as the full path the owner would actually call."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if func_name != "APIRouter":
            continue
        for kw in value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    return kw.value.value
    return ""


def _route_facts(node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str) -> list[SymbolFact]:
    """``@router.get("/policy")`` -> a ``route`` symbol named ``GET /v1/x/policy``."""
    facts: list[SymbolFact] = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        verb = dec.func.attr.lower()
        if verb not in HTTP_VERBS:
            continue
        base = dec.func.value
        base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        if not (base_name == "app" or base_name.endswith("router")):
            continue
        path = ""
        if dec.args and isinstance(dec.args[0], ast.Constant):
            if isinstance(dec.args[0].value, str):
                path = dec.args[0].value
        facts.append(
            SymbolFact(
                name=f"{verb.upper()} {prefix}{path}",
                kind=SYMBOL_KIND_ROUTE,
                signature=f"{node.name}({_unparse(node.args)})",
                lineno=node.lineno,
                docstring_summary=_first_line(ast.get_docstring(node)),
                tags=["route", verb],
            )
        )
    return facts


def _tablename(node: ast.ClassDef) -> str | None:
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "__tablename__":
                    if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                        return stmt.value.value
    return None


#: Parameter names whose STRING default is redacted out of a stored signature.
#: A signature is unparsed with its defaults, so ``def connect(token: str =
#: "sk-live-...")`` would otherwise copy a literal out of a source file into the
#: canonical database and back out over the API. The repository must never
#: contain such a literal (CLAUDE.md: never commit secrets) -- but the self
#: model is precisely the subsystem that will one day index code it did not
#: write, so it fails closed on the names that matter.
_SECRET_PARAM_RE: Final[re.Pattern[str]] = re.compile(
    r"secret|token|password|passwd|credential|api_?key|private_?key|access_?key|auth",
    re.IGNORECASE,
)
REDACTED_DEFAULT: Final[str] = "<redacted>"


def _redact_secret_defaults(args: ast.arguments) -> None:
    """Blank secret-shaped string defaults in place.

    Mutating the node is safe: the tree is discarded as soon as the module is
    analysed, and nothing downstream of here re-reads it.
    """

    def redact(arg: ast.arg | None, default: ast.expr | None) -> None:
        if arg is None or not isinstance(default, ast.Constant):
            return
        if isinstance(default.value, str) and default.value:
            if _SECRET_PARAM_RE.search(arg.arg):
                default.value = REDACTED_DEFAULT

    positional = [*args.posonlyargs, *args.args]
    if args.defaults:
        # defaults align with the TAIL of the positional parameters.
        for arg, default in zip(positional[-len(args.defaults) :], args.defaults, strict=False):
            redact(arg, default)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        redact(arg, default)


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {_unparse(node.returns, 120)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({_unparse(node.args)}){returns}"[:MAX_SIGNATURE_CHARS]


def _collect_imports(tree: ast.Module, package: str) -> tuple[list[str], dict[str, str]]:
    """Dotted import targets plus a ``local name -> module`` binding table.

    The binding table is what makes the ``calls`` edges cheap: a call is
    attributed to a module only when the callee's root name was bound by an
    import in this same file.
    """
    targets: list[str] = []
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
                bindings[(alias.asname or alias.name).split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                base = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                module = ".".join([*base, module]) if module else ".".join(base)
            if not module:
                continue
            targets.append(module)
            for alias in node.names:
                targets.append(f"{module}.{alias.name}")
                bindings[alias.asname or alias.name] = f"{module}.{alias.name}"
    return targets, bindings


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "value"`` bindings, values only, capped and redacted.

    Only the top level: a name bound inside a function or a class is not a
    module constant and reading it as one would put a guess in the table.

    A secret-shaped NAME gets :data:`REDACTED_DEFAULT` as its value, by the same
    rule and the same pattern as a secret-shaped function default
    (:func:`_redact_secret_defaults`). That rule existed and this path did not go
    through it: storing a constant's value is new, and ``code_symbols.signature``
    is returned verbatim by ``GET /v1/selfmodel/search``, so ``API_KEY = "..."``
    would have travelled from the source into the canonical database, into its
    backups, and back out over HTTP (independent security review, 2026-09-10).
    Redacting at the WRITE site is what makes it hold: it is also what
    :func:`_persisted_constant` later reads back.
    """
    found: dict[str, str] = {}
    for node in tree.body:
        if len(found) >= MAX_CONSTANTS_PER_MODULE:
            break
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        if len(value.value) > MAX_CONSTANT_VALUE_CHARS:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if _SECRET_PARAM_RE.search(target.id):
                found[target.id] = REDACTED_DEFAULT
            else:
                found[target.id] = value.value
    return found


def _capability_id(node: ast.expr | None, constants: dict[str, str]) -> str | None:
    """The capability id this expression names, or ``None``.

    A literal, or a constant defined in this same file. Anything else -- a
    variable, an attribute of an object, an f-string -- is genuinely not
    knowable without running the program, and returns ``None`` rather than a
    plausible-looking reconstruction.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text: str = node.value
    elif isinstance(node, ast.Name):
        text = constants.get(node.id, "")
    else:
        return None
    return text if _CAPABILITY_ID_RE.match(text) else None


def _capability_ref(node: ast.expr | None, bindings: dict[str, str]) -> str | None:
    """``actions.TOOL_STATE_NOW`` -> the dotted reference to that constant."""
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
        return None
    root = bindings.get(node.value.id)
    return f"{root}.{node.attr}" if root else None


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _handler_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _collect_capabilities(
    tree: ast.Module, constants: dict[str, str], bindings: dict[str, str]
) -> tuple[list[tuple[str, str | None, int]], list[CapabilityRef], list[str]]:
    """The two halves of a capability: what this file answers, what it dispatches.

    Returns ``(implemented, unresolved, used)``. ``implemented`` carries the line
    the registration is on, so "operator.type is broken" resolves to a file AND a
    line. ``used`` drops anything this same file implements: passing your own tool
    name to a helper (``_capability_missing(ctx, capability=TOOL_TYPE)``) is not
    dispatching it.
    """
    implemented: list[tuple[str, str | None, int]] = []
    unresolved: list[CapabilityRef] = []
    used: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if _callee_name(node.func) in _CAPABILITY_CONSTRUCTORS:
            named = keywords.get("name")
            handler = _handler_name(keywords.get("handler"))
            capability = _capability_id(named, constants)
            if capability is not None:
                implemented.append((capability, handler, node.lineno))
            elif len(unresolved) < MAX_CAPABILITY_REFS_PER_MODULE:
                ref = _capability_ref(named, bindings)
                if ref is not None:
                    unresolved.append(CapabilityRef(ref=ref, handler=handler, lineno=node.lineno))
        if "capability" in keywords:
            dispatched = _capability_id(keywords["capability"], constants)
            if dispatched is not None:
                used.append(dispatched)

    # A third form, and the one that carries the names the owner's incidents are
    # actually filed under: a module-level MAPPING between vocabularies.
    # ``app/alarms/sequence.py``'s ``RECEIPT_BY_DEVICE_CALL`` translates a device
    # call into the receipt capability the ledger records, so "alarm.arm",
    # "media.play" and "greeting.play" exist only as values in that dict -- and
    # ``app/voice/intents.py`` maps an utterance to the capability it means the
    # same way. Without this, 7 of the 25 capability names production receipts
    # carry resolved to nothing (measured 2026-09-10). Keys and values both: in
    # a mapping BETWEEN capability vocabularies, both sides are names this file
    # is where you go to change.
    used.extend(_mapped_capabilities(tree))

    answered = {capability for capability, _, _ in implemented}
    return implemented, unresolved, [c for c in used if c not in answered]


def _mapped_capabilities(tree: ast.Module) -> list[str]:
    """Capability ids appearing as literals in a module-level dict literal."""
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        for item in (*value.keys, *value.values):
            if len(found) >= MAX_MAPPED_CAPABILITIES_PER_MODULE:
                return found
            if (
                isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and _CAPABILITY_ID_RE.match(item.value)
            ):
                found.append(item.value)
    return found


def _collect_calls(tree: ast.Module, bindings: dict[str, str]) -> list[str]:
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        root = None
        if isinstance(func, ast.Name):
            root = func.id
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            root = func.value.id
        if root and root in bindings:
            called.add(bindings[root])
    return sorted(called)


def analyze_python(source: str, module_id: str, package: str) -> dict[str, Any]:
    """Extract structure from one Python source string.

    Raises nothing: a file that does not parse yields a ``note`` instead, so a
    syntax error somewhere in the repository degrades one row rather than
    failing the whole index.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return {"note": f"unparsed:{type(exc).__name__}", "symbols": [], "imports": [], "calls": []}

    prefix = _router_prefix(tree)
    symbols: list[SymbolFact] = []

    def add(fact: SymbolFact) -> None:
        if len(symbols) < MAX_SYMBOLS_PER_MODULE:
            symbols.append(fact)

    capabilities: list[str] = []
    constants = _string_constants(tree)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(_unparse(b, 60) for b in node.bases)
            table = _tablename(node)
            add(
                SymbolFact(
                    name=node.name,
                    kind=SYMBOL_KIND_CLASS,
                    signature=f"class {node.name}({bases})"[:MAX_SIGNATURE_CHARS],
                    lineno=node.lineno,
                    docstring_summary=_first_line(ast.get_docstring(node)),
                    tags=["orm"] if table else [],
                )
            )
            if table:
                add(
                    SymbolFact(
                        name=table,
                        kind=SYMBOL_KIND_TABLE,
                        signature=f"class {node.name}",
                        lineno=node.lineno,
                        docstring_summary=_first_line(ast.get_docstring(node)),
                        tags=["table"],
                    )
                )
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                    if stmt.name.startswith("__") and stmt.name != "__init__":
                        continue
                    add(
                        SymbolFact(
                            name=f"{node.name}.{stmt.name}",
                            kind=SYMBOL_KIND_METHOD,
                            signature=_function_signature(stmt),
                            lineno=stmt.lineno,
                            docstring_summary=_first_line(ast.get_docstring(stmt)),
                        )
                    )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            routes = _route_facts(node, prefix)
            for route in routes:
                add(route)
            decorators = set(_decorator_names(node))
            kind = SYMBOL_KIND_FUNCTION
            if decorators & _CAPABILITY_DECORATORS:
                kind = SYMBOL_KIND_CAPABILITY
                capabilities.append(node.name)
            elif decorators & _TOOL_DECORATORS:
                kind = SYMBOL_KIND_TOOL
            if routes and kind == SYMBOL_KIND_FUNCTION:
                continue
            add(
                SymbolFact(
                    name=node.name,
                    kind=kind,
                    signature=_function_signature(node),
                    lineno=node.lineno,
                    docstring_summary=_first_line(ast.get_docstring(node)),
                    tags=sorted(decorators)[:8],
                )
            )
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and _CONSTANT_RE.match(target.id):
                    add(
                        SymbolFact(
                            name=target.id,
                            kind=SYMBOL_KIND_CONSTANT,
                            signature=_constant_signature(target.id, constants),
                            lineno=node.lineno,
                        )
                    )

    imports, bindings = _collect_imports(tree, package)
    implemented, unresolved, used = _collect_capabilities(tree, constants, bindings)
    for capability, handler, lineno in implemented:
        capabilities.append(capability)
        add(
            SymbolFact(
                name=capability,
                kind=SYMBOL_KIND_CAPABILITY,
                signature=f"{capability} -> {handler}()" if handler else capability,
                lineno=lineno,
                tags=["registered"],
            )
        )
    return {
        "purpose": _first_line(ast.get_docstring(tree), 400),
        "symbols": symbols,
        "imports": imports,
        "calls": _collect_calls(tree, bindings),
        "capabilities": capabilities,
        "capabilities_used": used,
        "capability_refs": unresolved,
        "string_constants": constants,
        "note": None,
        "module_id": module_id,
    }


def _persisted_constant(session: Session, module_id: str, name: str) -> str | None:
    """Read a string constant's value back out of a previous run's symbol row.

    The value was written into the signature as ``NAME = 'value'``;
    :func:`ast.literal_eval` reads it back, and reads nothing else -- the input
    is a repr this indexer produced, and a signature that is not one (an older
    row from before values were stored, a name with no value) yields ``None``.
    """
    row = session.scalars(
        select(CodeSymbol).where(
            CodeSymbol.symbol_id == symbol_id_for(module_id, SYMBOL_KIND_CONSTANT, name)
        )
    ).first()
    if row is None:
        return None
    _, separator, literal = (row.signature or "").partition(" = ")
    if not separator:
        return None
    try:
        value = ast.literal_eval(literal)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _constant_signature(name: str, constants: dict[str, str]) -> str:
    """``TOOL_TYPE = 'operator.type'`` rather than bare ``TOOL_TYPE``.

    The value is kept because a capability id is frequently written once, as a
    constant, and referred to from another file. Persisting it is what lets an
    INCREMENTAL run -- one where the defining file was not reopened -- still
    resolve ``actions.TOOL_STATE_NOW`` to a capability.
    """
    value = constants.get(name)
    signature = f"{name} = {value!r}" if value is not None else name
    return signature[:MAX_SIGNATURE_CHARS]


# ------------------------------------------------------------------- walking


def _contained(candidate: Path, root: Path) -> Path | None:
    """The candidate's real path when it is still inside ``root``, else None.

    Containment is decided by RESOLVING the path, not by testing ``is_symlink()``: a
    Windows NTFS junction is a reparse point, not a symlink, so ``os.walk`` descends into
    it with ``followlinks=False`` and the leaf never reports as a link. A junction planted
    anywhere under a scanned tree would otherwise make the indexer read - and ``ast``-parse
    - files that live outside the checkout entirely. This is the same bug class the M3
    review found in the Windows agent's ArtifactOpener and the M8 review found in
    ``app.security.checks.collect_files``; the fix is deliberately the same one
    (security review of this package, 2026-09-05).
    """
    try:
        real = candidate.resolve()
    except OSError:
        return None
    return real if real.is_relative_to(root) else None


def _iter_files(root: Path, suffixes: tuple[str, ...]) -> Iterator[Path]:
    if not root.is_dir():
        return
    try:
        real_root = root.resolve()
    except OSError:
        return
    for dirpath, dirnames, filenames in os.walk(real_root):
        here = Path(dirpath)
        # Prune by name AND by containment, in place, so the walk never descends through a
        # reparse point that leaves the tree.
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in EXCLUDED_DIRS
            and not d.startswith(".")
            and _contained(here / d, real_root) is not None
        )
        for name in sorted(filenames):
            if not name.endswith(suffixes):
                continue
            real = _contained(here / name, real_root)
            if real is not None:
                yield real


def _dotted_id(rel_parts: tuple[str, ...], dotted_root: str) -> tuple[str, str]:
    """``('selfmodel', 'models.py')`` -> ``("app.selfmodel.models", "module")``."""
    parts = list(rel_parts)
    last = parts[-1]
    if last == "__init__.py":
        parts = parts[:-1]
        kind = MODULE_KIND_PACKAGE
    else:
        parts[-1] = last[:-3] if last.endswith(".py") else last
        kind = MODULE_KIND_MODULE
    return ".".join([dotted_root, *parts]) if parts else dotted_root, kind


#: File suffixes stripped off a leaf before it becomes a display name.
_LEAF_SUFFIXES: Final[tuple[str, ...]] = (".py", ".ts", ".tsx", ".cs", ".ps1", ".psm1")


def display_name_for(module_id: str) -> str:
    """``"app.observer.diagnostic_observer"`` -> ``"diagnostic observer"``.

    The name a person would say, derived from the LEAF -- take the leaf before
    touching separators, or ``services/browser/browser_agent/worker.py`` turns
    into "py" once the underscores become spaces and the result is split on the
    dot.
    """
    leaf = module_id.replace("\\", "/").split("/")[-1]
    if "." in leaf and not leaf.endswith(_LEAF_SUFFIXES):
        leaf = leaf.rsplit(".", 1)[-1]
    for suffix in _LEAF_SUFFIXES:
        if leaf.endswith(suffix):
            leaf = leaf[: -len(suffix)]
            break
    return leaf.replace("_", " ").replace("-", " ").strip() or module_id


def _owner_area(spec: TreeSpec, module_id: str) -> str:
    if spec.owner_area:
        return spec.owner_area
    if spec.dotted_root:
        parts = module_id.split(".")
        return parts[1] if len(parts) > 1 else parts[0]
    parts = module_id.split("/")
    return parts[1] if len(parts) > 1 else parts[0]


def discover(config: IndexConfig) -> list[tuple[TreeSpec, Path, str, str, str]]:
    """``(spec, absolute path, module_id, kind, owner_area)`` for every candidate.

    Pure path work -- no file is opened here, which is what lets the
    incremental gate decide "unchanged" without reading anything.
    """
    found: list[tuple[TreeSpec, Path, str, str, str]] = []
    seen: set[str] = set()
    for spec in config.trees:
        root = config.repo_root / spec.root
        for path in _iter_files(root, spec.suffixes):
            rel_to_tree = path.relative_to(root).parts
            if spec.dotted_root:
                module_id, kind = _dotted_id(rel_to_tree, spec.dotted_root)
            else:
                module_id = path.relative_to(config.repo_root).as_posix()
                kind = spec.kind
            if len(module_id) > 200 or module_id in seen:
                continue
            seen.add(module_id)
            found.append((spec, path, module_id, kind, _owner_area(spec, module_id)))
            if len(found) >= config.max_modules:
                logger.warning("selfmodel_module_cap_reached", cap=config.max_modules)
                return found
    return found


def _fingerprint(path: Path, mode: str) -> tuple[str, int]:
    stat = path.stat()
    if mode == "content":
        return _sha256_file(path), stat.st_size
    return f"{stat.st_size}:{stat.st_mtime_ns}", stat.st_size


# --------------------------------------------------------- documentation refs

_ADR_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,4}\s*(ADR-\d{3,5})\b")
#: ``app/ledger/models.py``, ``app.ledger.models``, ``services/browser/...``.
_MODULE_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:app|tests)[./][A-Za-z0-9_./]+|\b(?:services|apps|devices|scripts)/[A-Za-z0-9_./-]+"
)


def _normalize_doc_token(token: str) -> str:
    token = token.rstrip("./`,;:)")
    if token.startswith(("app/", "tests/")) or token.startswith(("app.", "tests.")):
        token = token.replace("/", ".")
        if token.endswith(".py"):
            token = token[:-3]
    return token


def scan_docs(config: IndexConfig, known: Iterable[str]) -> tuple[dict[str, set[str]], int]:
    """``module_id -> {"ADR-0050", "docs/M16_..._SPEC.md"}`` plus files read.

    Streamed line by line with a size cap: documentation is grepped, never
    loaded. A token only links when it names a module the index already knows,
    so a prose mention of a directory that no longer exists links to nothing.
    """
    known_ids = set(known)
    refs: dict[str, set[str]] = {}
    files_read = 0
    for pattern in config.doc_globs:
        for doc in sorted(config.repo_root.glob(pattern)):
            if not doc.is_file() or doc.stat().st_size > MAX_DOC_BYTES:
                continue
            files_read += 1
            rel = doc.relative_to(config.repo_root).as_posix()
            current_adr: str | None = None
            with open(doc, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    heading = _ADR_HEADING_RE.match(line.strip())
                    if heading:
                        current_adr = heading.group(1)
                        continue
                    for raw in _MODULE_TOKEN_RE.findall(line):
                        token = _normalize_doc_token(raw)
                        if token not in known_ids:
                            continue
                        bucket = refs.setdefault(token, set())
                        if current_adr and doc.name == "DECISIONS.md":
                            bucket.add(current_adr)
                        else:
                            bucket.add(rel)
    return refs, files_read


# ------------------------------------------------------------------- linking


def targets_for_test_module(test_module_id: str, known: set[str]) -> list[str]:
    """``tests.unit.test_ledger_routes`` -> ``["app.ledger.routes"]``.

    Longest-existing-prefix walk: consume as many name tokens as still resolve
    to a real module, then keep going into it. ``test_multi_device_invariant``
    resolves to nothing rather than to a plausible neighbour -- an unlinked test
    is a better answer than a wrong link.
    """
    stem = test_module_id.rsplit(".", 1)[-1]
    if not stem.startswith("test_"):
        return []
    tokens = [t for t in stem[len("test_") :].split("_") if t]
    if not tokens:
        return []
    prefix = "app"
    matched: str | None = None
    index = 0
    while index < len(tokens):
        best: tuple[int, str] | None = None
        for take in range(len(tokens) - index, 0, -1):
            candidate = f"{prefix}.{'_'.join(tokens[index : index + take])}"
            if candidate in known:
                best = (take, candidate)
                break
        if best is None:
            break
        index += best[0]
        prefix = best[1]
        matched = best[1]
    return [matched] if matched else []


def _resolve_import(target: str, known: set[str]) -> str | None:
    """Longest known prefix of a dotted import target."""
    if target in known:
        return target
    parts = target.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in known:
            return candidate
    return None


# -------------------------------------------------------------------- report


@dataclass(slots=True)
class IndexReport:
    """What one index run actually did. Counters only -- an owner-facing report
    that is safe to log and safe to publish."""

    modules_discovered: int = 0
    modules_reparsed: int = 0
    modules_skipped_unchanged: int = 0
    modules_removed: int = 0
    modules_too_large: int = 0
    modules_unparsed: int = 0
    files_read: int = 0
    doc_files_read: int = 0
    symbols_written: int = 0
    edges_written: int = 0
    edges_removed: int = 0
    provenance_written: int = 0
    modules_updated: int = 0
    duration_ms: int = 0
    started_at: str = ""

    @property
    def writes(self) -> int:
        """Total row mutations. Zero on a re-index of an untouched checkout."""
        return (
            self.modules_reparsed
            + self.modules_removed
            + self.modules_updated
            + self.symbols_written
            + self.edges_written
            + self.edges_removed
            + self.provenance_written
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "modules_discovered": self.modules_discovered,
            "modules_reparsed": self.modules_reparsed,
            "modules_skipped_unchanged": self.modules_skipped_unchanged,
            "modules_removed": self.modules_removed,
            "modules_too_large": self.modules_too_large,
            "modules_unparsed": self.modules_unparsed,
            "modules_updated": self.modules_updated,
            "files_read": self.files_read,
            "doc_files_read": self.doc_files_read,
            "symbols_written": self.symbols_written,
            "edges_written": self.edges_written,
            "edges_removed": self.edges_removed,
            "provenance_written": self.provenance_written,
            "writes": self.writes,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
        }


# -------------------------------------------------------------------- indexer


class Indexer:
    """Rebuilds the index in place, writing only what actually differs."""

    def __init__(self, config: IndexConfig, progress: IndexProgress | None = None) -> None:
        self.config = config
        self.progress = progress or IndexProgress()

    # -- filesystem half -----------------------------------------------------

    def _facts_for(
        self, spec: TreeSpec, path: Path, module_id: str, kind: str, owner_area: str
    ) -> ModuleFacts:
        fingerprint, size = _fingerprint(path, self.config.fingerprint_mode)
        rel = path.relative_to(self.config.repo_root).as_posix()
        facts = ModuleFacts(
            module_id=module_id,
            kind=kind,
            path=rel,
            language=spec.language,
            owner_area=owner_area,
            fingerprint=fingerprint,
            size=size,
        )
        if size > self.config.max_file_bytes:
            facts.note = "too_large"
            return facts
        if not spec.parse:
            # Name/path/kind only: a Python parser has nothing true to say about
            # TypeScript, C# or PowerShell, and the foreign Python trees are
            # scanned the same way so the boundary stays one rule, not two.
            return facts

        facts.digest = _sha256_file(path)
        source = path.read_text(encoding="utf-8", errors="replace")
        package = module_id if kind == MODULE_KIND_PACKAGE else module_id.rsplit(".", 1)[0]
        extracted = analyze_python(source, module_id, package)
        del source  # the body never outlives the parse
        facts.purpose = extracted.get("purpose")
        facts.symbols = list(extracted.get("symbols") or [])
        facts.imports = list(extracted.get("imports") or [])
        facts.calls = list(extracted.get("calls") or [])
        facts.capabilities = list(extracted.get("capabilities") or [])
        facts.capabilities_used = list(extracted.get("capabilities_used") or [])
        facts.capability_refs = list(extracted.get("capability_refs") or [])
        facts.string_constants = dict(extracted.get("string_constants") or {})
        facts.note = extracted.get("note")
        return facts

    def _settle_capability_refs(self, session: Session, fresh: dict[str, ModuleFacts]) -> None:
        """Resolve ``ToolSpec(name=<other module>.CONSTANT)`` now that all files are parsed.

        Two places hold the answer and both are consulted, in this order: the
        modules parsed by THIS run, and the constant symbols already in the table
        from an earlier one. The second is what keeps the incremental promise --
        editing ``tools.py`` must not require reopening ``actions.py`` to know
        what ``actions.TOOL_STATE_NOW`` says.

        A reference that resolves to nothing is left out and counted. It is not
        approximated by the constant's NAME: ``TOOL_STATE_NOW`` is not a
        capability id, and a table whose job is to be trusted may not contain one
        that merely looks like an answer.
        """
        wanted: set[str] = set()
        for facts in fresh.values():
            for ref in facts.capability_refs:
                wanted.add(ref.ref)
        if not wanted:
            return

        resolved: dict[str, str] = {}
        missing: list[tuple[str, str]] = []
        for reference in sorted(wanted):
            module_id, _, name = reference.rpartition(".")
            value = (fresh[module_id].string_constants.get(name)) if module_id in fresh else None
            if value is None:
                value = _persisted_constant(session, module_id, name)
            if value is not None and _CAPABILITY_ID_RE.match(value):
                resolved[reference] = value
            else:
                missing.append((module_id, name))

        for facts in fresh.values():
            for ref in facts.capability_refs:
                capability = resolved.get(ref.ref)
                if capability is None or capability in facts.capabilities:
                    continue
                facts.capabilities.append(capability)
                facts.symbols.append(
                    SymbolFact(
                        name=capability,
                        kind=SYMBOL_KIND_CAPABILITY,
                        signature=(
                            f"{capability} -> {ref.handler}()" if ref.handler else capability
                        ),
                        lineno=ref.lineno,
                        tags=["registered"],
                    )
                )
            facts.capabilities_used = [
                c for c in facts.capabilities_used if c not in facts.capabilities
            ]

        if missing:
            logger.warning(
                "selfmodel_capability_ref_unresolved",
                count=len(missing),
                sample=[f"{module}.{name}" for module, name in missing[:5]],
            )

    # -- database half -------------------------------------------------------

    def run(self, session: Session) -> IndexReport:
        started = datetime.now(UTC)
        report = IndexReport(started_at=started.isoformat().replace("+00:00", "Z"))

        self.progress.phase("discovering")
        discovered = discover(self.config)
        report.modules_discovered = len(discovered)
        discovered_ids = {module_id for _, _, module_id, _, _ in discovered}

        existing_rows = {row.module_id: row for row in session.scalars(select(CodeModule))}

        # 1. parse only what changed ----------------------------------------
        self.progress.phase("parsing", done=0, total=len(discovered))
        fresh: dict[str, ModuleFacts] = {}
        for position, (spec, path, module_id, kind, owner_area) in enumerate(discovered, start=1):
            try:
                fingerprint, _ = _fingerprint(path, self.config.fingerprint_mode)
            except OSError:
                continue
            row = existing_rows.get(module_id)
            if row is not None and (row.detail_json or {}).get("fingerprint") == fingerprint:
                report.modules_skipped_unchanged += 1
                continue
            try:
                facts = self._facts_for(spec, path, module_id, kind, owner_area)
            except OSError as exc:
                logger.warning(
                    "selfmodel_file_unreadable", module_id=module_id, error_class=type(exc).__name__
                )
                continue
            if spec.parse and facts.note is None:
                report.files_read += 1
            if facts.note == "too_large":
                report.modules_too_large += 1
            elif facts.note:
                report.modules_unparsed += 1
            fresh[module_id] = facts
            if position % 50 == 0:
                self.progress.phase("parsing", done=position, total=len(discovered))

        self.progress.phase("parsing", done=len(discovered), total=len(discovered))

        # 1b. capability ids that live in another file ------------------------
        self._settle_capability_refs(session, fresh)

        # 2. upsert the changed modules and their symbols --------------------
        now = datetime.now(UTC)
        for module_id, facts in fresh.items():
            row = existing_rows.get(module_id)
            detail = {
                "fingerprint": facts.fingerprint,
                "size": facts.size,
                "digest": facts.digest,
                "symbol_count": len(facts.symbols),
                "note": facts.note,
                "display_name": display_name_for(module_id),
            }
            if row is None:
                row = CodeModule(
                    module_id=module_id,
                    kind=facts.kind,
                    path=facts.path,
                    language=facts.language,
                    purpose=facts.purpose,
                    adr_refs=[],
                    spec_refs=[],
                    owner_area=facts.owner_area,
                    production_state=PRODUCTION_STATE_SOURCE_ONLY,
                    created_at=now,
                    updated_at=now,
                    detail_json=detail,
                )
                session.add(row)
                existing_rows[module_id] = row
            else:
                row.kind = facts.kind
                row.path = facts.path
                row.language = facts.language
                row.purpose = facts.purpose
                row.owner_area = facts.owner_area
                row.updated_at = now
                row.detail_json = detail
            report.modules_reparsed += 1

            session.execute(delete(CodeSymbol).where(CodeSymbol.module_id == module_id))
            seen_symbols: set[str] = set()
            for symbol in facts.symbols:
                symbol_id = symbol_id_for(module_id, symbol.kind, symbol.name)
                if symbol_id in seen_symbols:
                    continue
                seen_symbols.add(symbol_id)
                session.add(
                    CodeSymbol(
                        symbol_id=symbol_id,
                        module_id=module_id,
                        name=symbol.name,
                        kind=symbol.kind,
                        signature=symbol.signature,
                        lineno=symbol.lineno,
                        docstring_summary=symbol.docstring_summary,
                        tags=list(symbol.tags),
                    )
                )
                report.symbols_written += 1

            self._write_source_provenance(session, facts, now, report)

        # 3. drop modules whose files are gone --------------------------------
        for module_id in list(existing_rows):
            if module_id in discovered_ids:
                continue
            session.execute(delete(CodeSymbol).where(CodeSymbol.module_id == module_id))
            session.execute(delete(CodeEdge).where(CodeEdge.from_module == module_id))
            session.execute(delete(ModuleProvenance).where(ModuleProvenance.module_id == module_id))
            session.delete(existing_rows.pop(module_id))
            report.modules_removed += 1

        session.flush()
        known_ids = set(existing_rows)

        # 4. edges ------------------------------------------------------------
        self.progress.phase("linking", done=0, total=len(known_ids))
        desired: dict[tuple[str, str, str], dict[str, Any]] = {}
        scope: set[tuple[str, str]] = set()

        for module_id, facts in fresh.items():
            for edge_kind in (
                EDGE_IMPORTS,
                EDGE_CALLS,
                EDGE_IMPLEMENTS_CAPABILITY,
                EDGE_USES_CAPABILITY,
            ):
                scope.add((module_id, edge_kind))
            for target in dict.fromkeys(facts.imports):
                resolved = _resolve_import(target, known_ids)
                if resolved and resolved != module_id:
                    desired[(module_id, resolved, EDGE_IMPORTS)] = {}
            for target in dict.fromkeys(facts.calls):
                resolved = _resolve_import(target, known_ids)
                if resolved and resolved != module_id:
                    desired[(module_id, resolved, EDGE_CALLS)] = {}
            for capability in dict.fromkeys(facts.capabilities):
                desired[(module_id, f"capability:{capability}", EDGE_IMPLEMENTS_CAPABILITY)] = {}
            for capability in dict.fromkeys(facts.capabilities_used):
                desired[(module_id, f"capability:{capability}", EDGE_USES_CAPABILITY)] = {}

        for module_id in known_ids:
            if not module_id.startswith("tests."):
                continue
            scope.add((module_id, EDGE_TESTS))
            for target in targets_for_test_module(module_id, known_ids):
                desired[(module_id, target, EDGE_TESTS)] = {}

        # 5. documentation ----------------------------------------------------
        self.progress.phase("documenting")
        doc_refs, doc_files = scan_docs(self.config, known_ids)
        report.doc_files_read = doc_files
        for module_id in known_ids:
            scope.add((module_id, EDGE_DOCUMENTED_BY))
            refs = sorted(doc_refs.get(module_id, ()))
            adrs = [r for r in refs if r.startswith("ADR-")]
            specs = [r for r in refs if not r.startswith("ADR-")]
            row = existing_rows[module_id]
            if list(row.adr_refs or []) != adrs or list(row.spec_refs or []) != specs:
                row.adr_refs = adrs
                row.spec_refs = specs
                row.updated_at = now
                report.modules_updated += 1
            for adr in adrs:
                desired[(module_id, f"docs/DECISIONS.md#{adr}", EDGE_DOCUMENTED_BY)] = {}
            for spec_ref in specs:
                desired[(module_id, spec_ref, EDGE_DOCUMENTED_BY)] = {}

        self._sync_edges(session, desired, scope, report)
        session.flush()

        self.progress.phase("provenance")
        report.provenance_written += record_evidence_provenance(session, known_ids)
        session.flush()

        report.duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        self.progress.finish(modules=len(known_ids))
        logger.info("selfmodel_indexed", **report.to_dict())
        return report

    # -- helpers -------------------------------------------------------------

    def _write_source_provenance(
        self, session: Session, facts: ModuleFacts, now: datetime, report: IndexReport
    ) -> None:
        """The checkout said so. Confidence 1.0 for a digested file, 0.6 for a
        file that was only stat-ed (foreign trees, oversized files) -- honest
        about the difference rather than uniformly confident."""
        row = session.scalars(
            select(ModuleProvenance).where(
                ModuleProvenance.module_id == facts.module_id,
                ModuleProvenance.truth_kind == TRUTH_SOURCE,
            )
        ).first()
        confidence = 1.0 if facts.digest else 0.6
        refs = [{"kind": "checkout", "ref": facts.path}]
        if row is None:
            session.add(
                ModuleProvenance(
                    module_id=facts.module_id,
                    truth_kind=TRUTH_SOURCE,
                    version=None,
                    digest=facts.digest,
                    observed_at=now,
                    evidence_refs=refs,
                    confidence=confidence,
                    stale=False,
                )
            )
        else:
            row.digest = facts.digest
            row.observed_at = now
            row.evidence_refs = refs
            row.confidence = confidence
            row.stale = False
        report.provenance_written += 1

    def _sync_edges(
        self,
        session: Session,
        desired: dict[tuple[str, str, str], dict[str, Any]],
        scope: set[tuple[str, str]],
        report: IndexReport,
    ) -> None:
        """Apply only the difference.

        Edges outside ``scope`` (a module that was skipped as unchanged) are
        left alone; inside it, the run is authoritative. Diffing rather than
        delete-and-reinsert is what makes an unchanged re-index cost zero
        writes.
        """
        existing: dict[tuple[str, str, str], CodeEdge] = {}
        for edge in session.scalars(select(CodeEdge)):
            existing[(edge.from_module, edge.to_module, edge.kind)] = edge

        for key, edge in existing.items():
            from_module, _, kind = key
            if (from_module, kind) in scope and key not in desired:
                session.delete(edge)
                report.edges_removed += 1

        for key, detail in desired.items():
            if key in existing:
                continue
            from_module, to_module, kind = key
            session.add(
                CodeEdge(
                    from_module=from_module,
                    to_module=to_module[:400],
                    kind=kind,
                    detail_json=detail,
                )
            )
            report.edges_written += 1


# ------------------------------------------------- evidence-only provenance


def _modules_for_component(component: str, known: set[str]) -> list[str]:
    """Which module ids a deployment/runtime component refers to - conservatively.

    A component maps to modules only through an explicit hint or an exact module id. The
    suffix match this used to fall back on ("observer" -> app.anything.observer) attributed
    RUNTIME truth to modules the evidence never named, and to several of them at once when
    the suffix was common: precisely the confusion the four truth kinds exist to prevent
    (independent test review, 2026-09-05). An unmapped component now links to nothing, and
    the module simply has no runtime truth - which is the honest answer.
    """
    component = (component or "").strip().lower()
    if not component:
        return []
    hinted = COMPONENT_MODULE_HINTS.get(component)
    if hinted:
        return [module_id for module_id in hinted if module_id in known]
    if component in known:
        return [component]
    return []


def _upsert_provenance(
    session: Session,
    *,
    module_id: str,
    truth_kind: str,
    version: str | None,
    digest: str | None,
    observed_at: datetime,
    evidence_refs: list[dict[str, Any]],
    confidence: float,
) -> bool:
    """One row per ``(module_id, truth_kind)``; returns whether anything changed.

    Refuses to write an evidence-only truth without evidence. This is the
    guardrail the whole table exists for, so it lives in the writer rather than
    in a caller's discipline.
    """
    if truth_kind in EVIDENCE_ONLY_TRUTHS and not evidence_refs:
        raise ValueError(f"{truth_kind} provenance requires evidence_refs")
    row = session.scalars(
        select(ModuleProvenance).where(
            ModuleProvenance.module_id == module_id,
            ModuleProvenance.truth_kind == truth_kind,
        )
    ).first()
    if row is None:
        session.add(
            ModuleProvenance(
                module_id=module_id,
                truth_kind=truth_kind,
                version=version,
                digest=digest,
                observed_at=observed_at,
                evidence_refs=evidence_refs,
                confidence=confidence,
                stale=False,
            )
        )
        return True
    if row.observed_at is not None:
        current = row.observed_at
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if current > observed_at:
            return False
    changed = (
        row.version != version
        or row.digest != digest
        or list(row.evidence_refs or []) != evidence_refs
        or abs(row.confidence - confidence) > 1e-9
    )
    row.version = version
    row.digest = digest
    row.observed_at = observed_at
    row.evidence_refs = evidence_refs
    row.confidence = confidence
    return changed


#: Ledger event types that constitute a passed gate for ``ready_for_production``.
GATE_EVENT_TYPES: Final[dict[str, str]] = {
    "evolution.tests_passed": "tests_passed",
    "evolution.security_review_passed": "security_review_passed",
    "evolution.benchmark_completed": "benchmark_completed",
    "evolution.shadow_ready": "shadow_ready",
    "evolution.build_completed": "build_completed",
}


def record_evidence_provenance(session: Session, known: set[str]) -> int:
    """Write ``installed`` / ``runtime`` / ``evidence`` truths -- from rows only.

    Three evidence sources, each with a different strength:

    - ``releases`` (``app.selfhealing.models.Release``) says a version was
      BUILT and promoted. That is an *installed* truth, never a runtime one:
      a promoted release proves an intent to deploy, not a live process.
    - ledger ``deployment.*`` events whose ``detail_json`` carries a module
      block reported back by the thing that started -- that is a *runtime*
      truth, and it is the only kind of row that produces one.
    - ledger ``evolution.*`` gate events -- an *evidence* truth listing which
      gates a module has passed.

    Every table read here is optional: an older database without the ledger, or
    a unit-test engine with only the self-model tables, simply yields no
    evidence rather than an error.
    """
    written = 0
    written += _provenance_from_releases(session, known)
    written += _provenance_from_ledger(session, known)
    return written


def has_table(session: Session, table_name: str) -> bool:
    """Is an optional evidence table present on this session's database?

    Probing rather than try/except: catching a failed SELECT would mean rolling
    the session back, and this runs *inside* an index transaction already
    holding every module, symbol and edge of the run -- a missing optional
    table must cost no evidence, not the whole index.

    The inspection goes through ``session.connection()``, NOT the engine.
    Reflecting off the engine checks out a second connection, which under the
    ``StaticPool`` the unit tests use is the *same* DBAPI connection; releasing
    it rolls back the session's uncommitted work and the index silently
    vanishes at commit time.
    """
    try:
        return sa_inspect(session.connection()).has_table(table_name)
    except Exception:  # noqa: BLE001 - an un-inspectable bind simply has no evidence
        return False


def _provenance_from_releases(session: Session, known: set[str]) -> int:
    from app.selfhealing.models import Release

    if not has_table(session, Release.__tablename__):
        return 0
    rows = list(session.scalars(select(Release)))

    strength = {"active": 0.9, "staging": 0.7}
    written = 0
    latest: dict[tuple[str, str], Any] = {}
    for release in rows:
        if release.status not in strength:
            continue
        for module_id in _modules_for_component(release.component, known):
            key = (module_id, release.component)
            current = latest.get(key)
            if current is None or _as_utc(release.created_at) >= _as_utc(current.created_at):
                latest[key] = release

    for (module_id, component), release in latest.items():
        refs = [
            {
                "kind": "release",
                "ref": str(release.id),
                "component": component,
                "status": release.status,
            }
        ]
        if _upsert_provenance(
            session,
            module_id=module_id,
            truth_kind=TRUTH_INSTALLED,
            version=release.version,
            digest=release.manifest_digest,
            observed_at=_as_utc(release.promoted_at or release.created_at),
            evidence_refs=refs,
            confidence=strength[release.status],
        ):
            written += 1
        edge_key = f"release:{component}:{release.version}"
        exists = session.scalars(
            select(CodeEdge).where(
                CodeEdge.from_module == module_id,
                CodeEdge.to_module == edge_key,
                CodeEdge.kind == EDGE_RELEASED_AS,
            )
        ).first()
        if exists is None:
            session.add(
                CodeEdge(
                    from_module=module_id,
                    to_module=edge_key[:400],
                    kind=EDGE_RELEASED_AS,
                    detail_json={"release_id": str(release.id), "status": release.status},
                )
            )
    return written


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _runtime_block(detail: dict[str, Any]) -> dict[str, Any] | None:
    """A deployment event only proves runtime truth if something running
    reported back -- the ``module``/``runtime`` block the browser worker sends
    with every hello (``services/browser/browser_agent/release.py``)."""
    for key in ("runtime", "module", "module_info"):
        block = detail.get(key)
        if isinstance(block, dict) and (block.get("sha256") or block.get("package_sha256")):
            return block
    return None


def _provenance_from_ledger(session: Session, known: set[str]) -> int:
    from app.ledger.models import ActivityEventRow

    if not has_table(session, ActivityEventRow.__tablename__):
        _apply_production_states(session, known)
        return 0
    rows = list(
        session.scalars(
            select(ActivityEventRow).order_by(ActivityEventRow.occurred_at.asc()).limit(5000)
        )
    )

    written = 0
    gates: dict[str, dict[str, dict[str, Any]]] = {}
    for event in rows:
        detail = dict(event.detail_json or {})
        targets = _event_modules(event, known)
        if not targets:
            continue

        if event.event_type.startswith("deployment.") and event.status == "completed":
            block = _runtime_block(detail)
            if block is not None:
                refs = [
                    {"kind": "activity_event", "ref": str(event.event_id)},
                    *[r for r in (event.evidence_refs or []) if isinstance(r, dict)],
                ]
                for module_id in targets:
                    if _upsert_provenance(
                        session,
                        module_id=module_id,
                        truth_kind=TRUTH_RUNTIME,
                        version=event.version or block.get("version"),
                        digest=block.get("package_sha256") or block.get("sha256"),
                        observed_at=_as_utc(event.occurred_at),
                        evidence_refs=refs,
                        confidence=0.95,
                    ):
                        written += 1

        gate = GATE_EVENT_TYPES.get(event.event_type)
        if gate and event.status in ("completed", "info"):
            for module_id in targets:
                gates.setdefault(module_id, {})[gate] = {
                    "kind": "activity_event",
                    "ref": str(event.event_id),
                    "gate": gate,
                    "occurred_at": _as_utc(event.occurred_at).isoformat().replace("+00:00", "Z"),
                }

    for module_id, passed in gates.items():
        refs = [passed[name] for name in sorted(passed)]
        if _upsert_provenance(
            session,
            module_id=module_id,
            truth_kind=TRUTH_EVIDENCE,
            version=None,
            digest=None,
            observed_at=datetime.now(UTC),
            evidence_refs=refs,
            confidence=0.9,
        ):
            written += 1

    _apply_production_states(session, known)
    return written


def _event_modules(event: Any, known: set[str]) -> list[str]:
    """Which modules a ledger event is about, by explicit reference only."""
    for candidate in (event.related_module_id, event.module):
        if candidate and candidate in known:
            return [candidate]
    if event.event_type.startswith("deployment."):
        parts = event.event_type.split(".")
        if len(parts) >= 3:
            return _modules_for_component(parts[1], known)
    return []


def _apply_production_states(session: Session, known: set[str]) -> None:
    """Derive ``code_modules.production_state`` from the truths that exist.

    Strictly ordered by evidence strength, and ``source_only`` whenever there is
    none: a module the index knows only from the checkout must read as
    ``source_only`` even if its neighbours are live.
    """
    by_module: dict[str, dict[str, ModuleProvenance]] = {}
    for row in session.scalars(select(ModuleProvenance)):
        by_module.setdefault(row.module_id, {})[row.truth_kind] = row

    for module_id in known:
        truths = by_module.get(module_id, {})
        evidence_row = truths.get(TRUTH_EVIDENCE)
        evidence_refs = list(evidence_row.evidence_refs or ()) if evidence_row is not None else []
        shadow_ready = any(
            isinstance(ref, dict) and ref.get("gate") == "shadow_ready" for ref in evidence_refs
        )
        if TRUTH_RUNTIME in truths:
            state = PRODUCTION_STATE_RUNNING
        elif TRUTH_INSTALLED in truths:
            state = PRODUCTION_STATE_INSTALLED
        elif shadow_ready:
            state = PRODUCTION_STATE_SHADOW
        else:
            state = PRODUCTION_STATE_SOURCE_ONLY
        row = session.get(CodeModule, module_id)
        if row is not None and row.production_state != state:
            row.production_state = state


#: One index run at a time in this process, whoever started it.
#:
#: ``app/selfmodel/routes.py`` had an ``asyncio.Lock`` that made a second POST a
#: 409 -- and it could not see the background refresher at all, which arrived in
#: ADR-0111 and calls ``build_index`` directly from a worker thread. Two runs on
#: the same tables each compute ``desired``/``scope`` against their own snapshot,
#: so the overlap is a lost update or an IntegrityError on the same primary key,
#: silently in the refresher's case (independent review, 2026-09-10). The lock
#: lives HERE, next to the only function that writes those tables, rather than
#: next to one of the two callers -- a lock a caller can forget to take is the
#: bug it was meant to prevent.
#:
#: ``threading`` rather than ``asyncio``: both callers already run this in a
#: worker thread, so nothing blocks the event loop.
_INDEX_LOCK: Final[threading.Lock] = threading.Lock()


def index_running() -> bool:
    """Whether an index run is in progress in this process. Advisory only --
    ``build_index`` takes the lock itself; this is for a caller that would rather
    say "busy" than wait (``POST /v1/selfmodel/index`` answers 409)."""
    return _INDEX_LOCK.locked()


def build_index(
    session: Session,
    *,
    repo_root: Path | None = None,
    config: IndexConfig | None = None,
    progress: IndexProgress | None = None,
) -> IndexReport:
    """Index the checkout into ``session``. The one entry point callers need.

    The tree specs follow the LAYOUT that is actually there. A developer checkout gets
    ``DEFAULT_TREES``; a deployed image, which ships only the ``services/api`` subtree,
    gets ``DEPLOYED_TREES`` - otherwise every spec misses and the index is silently empty.
    An explicitly supplied ``config.trees`` is always honoured.

    Serialised on :data:`_INDEX_LOCK`; a second caller waits rather than racing.
    """
    if config is not None:
        cfg = config
    else:
        root = repo_root or default_repo_root()
        trees = DEPLOYED_TREES if detect_layout(root) == "deployed" else DEFAULT_TREES
        cfg = IndexConfig(repo_root=root, trees=trees)
    with _INDEX_LOCK:
        return Indexer(cfg, progress=progress).run(session)


__all__ = [
    "DEFAULT_DOC_GLOBS",
    "DEFAULT_TREES",
    "DEPLOYED_TREES",
    "detect_layout",
    "GATE_EVENT_TYPES",
    "MAX_FILE_BYTES",
    "MAX_MODULES",
    "IndexConfig",
    "IndexReport",
    "Indexer",
    "ModuleFacts",
    "SymbolFact",
    "TreeSpec",
    "analyze_python",
    "build_index",
    "default_repo_root",
    "display_name_for",
    "discover",
    "has_table",
    "index_running",
    "record_evidence_provenance",
    "scan_docs",
    "targets_for_test_module",
]
