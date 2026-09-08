"""The ``ProjectFiles`` policy (docs/M23_APP_FACTORY_SPEC.md §2, ADR-0086 decision 1):
every generated file set is validated before it ever leaves the Cloud Core, and before
the device is ever asked to write anything.

Checked, in order:

1. bounds — at most :data:`MAX_FILES` files, at most :data:`MAX_TOTAL_BYTES` total;
2. path shape — relative, no ``..``, no absolute/drive/UNC prefix, no reserved Windows
   device name, no embedded NUL;
3. text only — every file decodes as UTF-8 (the generator only ever emits text; a
   generator that somehow produced binary is refused here rather than trusted);
4. secrets — every file's text is scanned with the SAME scanner
   ``app.security.redaction.contains_secret`` uses for the M8 security agent and M22's
   own artifact-publish gate (``app.security.artifacts.publish_assessment_artifact``) —
   a hit refuses the whole project, never redacts and continues (a credential in
   generated source is a bug, not a formatting problem);
5. the manifest — ``entry`` names a file the project actually carries; every ``run``/
   ``test`` command is a byte-for-byte member of a FIXED allowlist (never a command line
   the manifest itself invents — spec §3's ``project.run`` payload carries a KEY, never a
   command line, and this is the other half: the KEY's own command line was already
   validated here before the device ever saw the manifest); ``port``, when present, is a
   plain integer in the unprivileged range.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from app.appfactory.generator import ProjectFiles
from app.security.redaction import contains_secret

MAX_FILES = 200
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_PATH_CHARS = 240

#: Windows reserved device names (case-insensitive, with or without an extension) —
#: refused as a path component anywhere in the tree, the same bound
#: ``app.artifacts``/``app.documents`` device-facing paths already respect implicitly
#: through ``AuthorisedRoots``' own resolve-then-contain, restated here because THIS
#: layer is what runs before any device or filesystem exists at all.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)

#: The fixed allowlist a manifest's ``run``/``test`` command values must be a member of,
#: byte for byte (module docstring point 5). ``{port}``/``{entry}`` are the only
#: substitution tokens a command template may carry — filled in by the DEVICE at
#: dispatch time (the free port it bound / the manifest's own entry), never by this
#: layer or by the model.
ALLOWED_RUN_COMMANDS = frozenset(
    {
        "python -m http.server {port} --bind 127.0.0.1",
    }
)
ALLOWED_TEST_COMMANDS = frozenset(
    {
        "node tests/run.js",
    }
)

MIN_PORT = 1024
MAX_PORT = 65535


class AppValidationError(ValueError):
    """A ``ProjectFiles`` set (or its manifest) failed the App Factory policy. The
    message never echoes file content — only paths, counts and command/pattern names,
    the same discipline ``app.artifacts.spec``'s own refusal messages follow."""

    def __init__(self, reason: str, *, code: str, details: dict | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool
    file_count: int
    total_bytes: int
    errors: tuple[str, ...] = field(default_factory=tuple)


def _check_path(path: str) -> str | None:
    if not path or path != path.strip():
        return "path is empty or carries leading/trailing whitespace"
    if len(path) > MAX_PATH_CHARS:
        return f"path exceeds {MAX_PATH_CHARS} characters"
    if "\x00" in path:
        return "path contains a NUL byte"
    if path.startswith("/") or path.startswith("\\"):
        return "path must be relative"
    pure = PureWindowsPath(path)
    if pure.drive or pure.root:
        return "path must not name a drive or be absolute"
    parts = pure.parts
    if ".." in parts:
        return "path must not contain '..'"
    for part in parts:
        stem = part.split(".")[0].upper()
        if stem in _RESERVED_NAMES:
            return f"path segment {part!r} is a reserved Windows device name"
    return None


def validate_files(files: ProjectFiles) -> ValidationReport:
    """Bounds, path shape, text-decodability and the secret scan (module docstring
    points 1-4). Raises :class:`AppValidationError` on the FIRST violation found, in a
    deterministic order, so a refusal is always reproducible."""
    if len(files) == 0:
        raise AppValidationError("a project must carry at least one file", code="empty_project")
    if len(files) > MAX_FILES:
        raise AppValidationError(
            f"project carries {len(files)} files, more than the {MAX_FILES} bound",
            code="too_many_files",
            details={"count": len(files)},
        )
    seen_paths: set[str] = set()
    total_bytes = 0
    for f in files.files:
        path_error = _check_path(f.path)
        if path_error is not None:
            raise AppValidationError(
                f"invalid path {f.path!r}: {path_error}",
                code="invalid_path",
                details={"path": f.path},
            )
        if f.path in seen_paths:
            raise AppValidationError(
                f"duplicate path {f.path!r}", code="duplicate_path", details={"path": f.path}
            )
        seen_paths.add(f.path)
        try:
            encoded = f.text.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - str is always encodable
            raise AppValidationError(
                f"file {f.path!r} is not valid UTF-8 text",
                code="not_text",
                details={"path": f.path},
            ) from exc
        total_bytes += len(encoded)
        hit = contains_secret(f.text)
        if hit is not None:
            raise AppValidationError(
                f"file {f.path!r} failed the secret-hygiene gate ({hit})",
                code="secret_detected",
                details={"path": f.path, "pattern": hit},
            )
    if total_bytes > MAX_TOTAL_BYTES:
        raise AppValidationError(
            f"project carries {total_bytes} bytes, more than the {MAX_TOTAL_BYTES} bound",
            code="too_large",
            details={"bytes": total_bytes},
        )
    return ValidationReport(ok=True, file_count=len(files), total_bytes=total_bytes)


def validate_manifest(files: ProjectFiles) -> dict:
    """Point 5 of the module docstring. Returns the parsed manifest on success."""
    manifest = files.manifest()
    if not isinstance(manifest, dict):
        raise AppValidationError("manifest.json must hold a mapping", code="invalid_manifest")

    entry = manifest.get("entry")
    if not isinstance(entry, str) or not entry:
        raise AppValidationError("manifest.json is missing 'entry'", code="invalid_manifest")
    if entry not in files.path_set():
        raise AppValidationError(
            f"manifest 'entry' {entry!r} names no file in the project",
            code="entry_not_found",
            details={"entry": entry},
        )

    run = manifest.get("run") or {}
    if not isinstance(run, dict):
        raise AppValidationError("manifest 'run' must be a mapping", code="invalid_manifest")
    for key, command in run.items():
        if not isinstance(command, str) or command not in ALLOWED_RUN_COMMANDS:
            raise AppValidationError(
                f"manifest run command {key!r} is not on the allowlist",
                code="command_not_allowed",
                details={"key": key},
            )

    test = manifest.get("test") or {}
    if not isinstance(test, dict):
        raise AppValidationError("manifest 'test' must be a mapping", code="invalid_manifest")
    for key, command in test.items():
        if not isinstance(command, str) or command not in ALLOWED_TEST_COMMANDS:
            raise AppValidationError(
                f"manifest test command {key!r} is not on the allowlist",
                code="command_not_allowed",
                details={"key": key},
            )

    port = manifest.get("port")
    if port is not None:
        port_is_plain_int = isinstance(port, int) and not isinstance(port, bool)
        if not port_is_plain_int or not (MIN_PORT <= port <= MAX_PORT):
            raise AppValidationError(
                f"manifest 'port' {port!r} is out of range", code="invalid_manifest"
            )

    return manifest


def validate(files: ProjectFiles) -> tuple[ValidationReport, dict]:
    """The whole policy, in order: files first, then the manifest they carry."""
    report = validate_files(files)
    manifest = validate_manifest(files)
    return report, manifest


__all__ = [
    "ALLOWED_RUN_COMMANDS",
    "ALLOWED_TEST_COMMANDS",
    "MAX_FILES",
    "MAX_PORT",
    "MAX_TOTAL_BYTES",
    "MIN_PORT",
    "AppValidationError",
    "ValidationReport",
    "validate",
    "validate_files",
    "validate_manifest",
]
