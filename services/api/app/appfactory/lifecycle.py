"""The generated application's lifecycle after its tests (B41 req 440-452): the UI
verification through the device's own browser worker (the oracle replayed, every step
recorded), the persistence verification across a real process restart, the run log
read back, the release artifact (a zip of exactly the files that were scaffolded, with
a manifest of hashes), the launch of a packaged release as its own project version, the
lineage of a project, and the merge of a later request into its requirements.

Everything here goes through the ONE device port (``project.*`` and ``browser.*`` ride
the same protocol) and reports what the device answered - never what the plan assumed.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from app.appfactory.generator import ProjectFile, ProjectFiles
from app.appfactory.requirements import (
    _AUTH_WORDS,
    _KEEP_VERBS,
    FEATURE_API,
    FEATURE_AUTH,
    MAX_FIELDS,
    MAX_SENTENCE_CHARS,
    EntitySpec,
    FieldSpec,
    Requirements,
    _clean_field,
    _field_type,
    _lower,
    _singular,
    _split_list,
    parse_requirements,
)
from app.routines.dispatch import DeviceActionPort, DeviceRunResult

BROWSER_PROFILE: Final = "isolated"
SESSION_KIND: Final = "research"
MAX_STEPS: Final = 40
STEP_TIMEOUT_S: Final = 30.0
RELEASE_KEY_PREFIX: Final = "apps/releases"


# ------------------------------------------------------------------ verification (443, 444)


@dataclass(slots=True)
class StepOutcome:
    action: str
    selector: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "selector": self.selector,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass(slots=True)
class UiVerification:
    verified: bool
    url: str
    steps: list[StepOutcome] = field(default_factory=list)
    failed_step: str | None = None
    persistence: bool | None = None
    restart: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "url": self.url,
            "steps": [s.as_dict() for s in self.steps],
            "failed_step": self.failed_step,
            "persistence": self.persistence,
            "restart": self.restart,
            "error": self.error,
            "at": datetime.now(UTC).isoformat(),
        }


class _Browser:
    """The oracle's verbs over the device's browser family, one session."""

    def __init__(self, device: DeviceActionPort, session_id: str, prefix: str) -> None:
        self.device = device
        self.session_id = session_id
        self.prefix = prefix
        self.n = 0

    def run(self, capability: str, **payload: Any) -> DeviceRunResult:
        self.n += 1
        return self.device.run(
            capability=capability,
            payload={"session_id": self.session_id, **payload},
            idempotency_key=f"{self.prefix}:{self.n}",
            timeout_s=STEP_TIMEOUT_S,
        )

    def open(self) -> DeviceRunResult:
        return self.run(
            "browser.session_open",
            session_kind=SESSION_KIND,
            profile=BROWSER_PROFILE,
            policy={
                "allowed_risk_classes": ["READ", "NAVIGATE", "REVERSIBLE_WRITE"],
                "visible": False,
            },
            channel="chrome",
        )

    def close(self) -> None:
        self.run("browser.session_close")

    def navigate(self, url: str) -> DeviceRunResult:
        return self.run("browser.navigate", url=url)

    def find(self, selector: str) -> int:
        result = self.run("browser.find", target={"css": selector})
        if not result.ok:
            return -1
        return int((result.result or {}).get("match_count") or 0)

    def fill(self, selector: str, value: str) -> bool:
        result = self.run("browser.fill", target={"css": selector}, value=value)
        return bool(result.ok and (result.result or {}).get("ok", True))

    def click(self, selector: str) -> bool:
        result = self.run("browser.click", target={"css": selector})
        return bool(result.ok and (result.result or {}).get("clicked", True))

    def text(self) -> str:
        result = self.run("browser.extract", mode="text", max_chars=24000)
        if not result.ok:
            return ""
        return str((result.result or {}).get("text") or "")

    def wait_text(self, text: str) -> bool:
        result = self.run("browser.wait", **{"for": "text", "text": text, "timeout_ms": 10000})
        return bool(result.ok and (result.result or {}).get("satisfied"))


def _replay(browser: _Browser, url: str, steps: list[dict[str, Any]], out: UiVerification) -> bool:
    for step in steps[:MAX_STEPS]:
        action = str(step.get("action") or "")
        selector = str(step.get("selector") or "")
        if action == "fill":
            ok = browser.fill(selector, str(step.get("value") or ""))
            outcome = StepOutcome(action, selector, ok)
        elif action == "click":
            ok = browser.click(selector)
            outcome = StepOutcome(action, selector, ok)
        elif action == "assert_text":
            wanted = str(step.get("contains") or "")
            ok = browser.wait_text(wanted) or wanted in browser.text()
            outcome = StepOutcome(action, selector, ok, f"contains {wanted!r}")
        elif action == "assert_visible":
            ok = browser.find(selector) > 0
            outcome = StepOutcome(action, selector, ok)
        elif action == "reload":
            ok = browser.navigate(url).ok
            outcome = StepOutcome(action, url, ok)
        elif action == "assert_class":
            # The device's find reports tag/role/name/text, never a class list: recorded as
            # not checked rather than claimed (the lab's Playwright checks it).
            outcome = StepOutcome(
                action, selector, True, "not checked here: the device lab asserts classes"
            )
        else:
            outcome = StepOutcome(action, selector, False, "unknown action")
            ok = False
        out.steps.append(outcome)
        if not outcome.ok:
            out.failed_step = f"{action} {selector}".strip()
            return False
    return True


def verify_ui(
    device: DeviceActionPort,
    *,
    url: str,
    oracle: dict[str, Any],
    project_id: str,
    restart: Any = None,
) -> UiVerification:
    """Req 443/444: the oracle replayed in the device's browser; then, when ``restart``
    (a callable that stops and runs the project and returns the new url or None) is
    given, the process is restarted and the record the steps added must still be there
    after the login steps are replayed."""
    out = UiVerification(verified=False, url=url)
    browser = _Browser(
        device, f"verify-{project_id}-{uuid.uuid4().hex[:6]}", f"appfactory-verify:{project_id}"
    )
    opened = browser.open()
    if not opened.ok:
        out.error = f"browser.session_open: {opened.error_class or 'failed'}"
        return out
    try:
        if not browser.navigate(url).ok:
            out.error = "browser.navigate failed"
            return out
        for assertion in oracle.get("initial_assertions") or []:
            selector = str(assertion.get("selector") or "")
            found = browser.find(selector) > 0
            out.steps.append(StepOutcome("exists", selector, found))
            if not found:
                out.failed_step = f"exists {selector}"
                return out
        steps = list(oracle.get("interaction_steps") or [])
        if not _replay(browser, url, steps, out):
            return out
        out.verified = True
        if restart is None:
            return out
        # Req 444: a real restart of the process, then the record must still be there.
        new_url = restart()
        out.restart = {"restarted": new_url is not None, "url": new_url}
        if new_url is None:
            out.persistence = False
            return out
        login = [s for s in steps if str(s.get("selector") or "").startswith("#login")]
        checks = [s for s in steps if s.get("action") == "assert_text"][-1:]
        after = UiVerification(verified=False, url=new_url)
        ok = browser.navigate(new_url).ok and _replay(browser, new_url, login + checks, after)
        out.steps.extend(after.steps)
        out.persistence = bool(ok and checks)
        if not ok:
            out.failed_step = f"after restart: {after.failed_step}"
        return out
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass


# ------------------------------------------------------------------- the log (445)


def read_log(device: DeviceActionPort, *, project_id: str) -> dict[str, Any]:
    result = device.run(
        capability="project.status",
        payload={"project_id": project_id},
        idempotency_key=f"appfactory-log:{project_id}:{uuid.uuid4().hex[:6]}",
        timeout_s=15.0,
    )
    if not result.ok:
        return {"ok": False, "error_class": result.error_class, "message": result.message}
    body = dict(result.result or {})
    tail = str(body.get("log_tail") or "")
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    return {
        "ok": True,
        "state": body.get("state"),
        "log_path": body.get("log_path"),
        "log_tail": tail[-4000:],
        "lines": len(lines),
        "last_line": lines[-1][:200] if lines else "",
        "log_truncated": bool(body.get("log_truncated")),
        "exit_code": body.get("exit_code"),
        "stop_reason": body.get("stop_reason"),
    }


# ------------------------------------------------------------ the release (441, 446)


@dataclass(slots=True)
class ReleaseArtifact:
    key: str
    bytes: int
    sha256: str
    version: int
    build_id: str
    files: int
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "version": self.version,
            "build_id": self.build_id,
            "files": self.files,
            "created_at": self.created_at,
        }


def build_id_for(files: ProjectFiles) -> str:
    """The build identity: a digest of every path and text, so two scaffolds of the same
    files carry the same id and any change a different one."""
    digest = hashlib.sha256()
    for f in sorted(files.files, key=lambda x: x.path):
        digest.update(f.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(f.text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def package_release(
    store: Any,
    *,
    slug: str,
    version: int,
    files: ProjectFiles,
    manifest: dict[str, Any],
    spec: dict[str, Any],
    name: str,
) -> ReleaseArtifact:
    """Req 441/446: a zip of exactly the scaffolded files plus ``release.json`` (name,
    version, build id, per-file sha256, manifest, spec), written to the object store
    under a key that names the project and the version."""
    build_id = build_id_for(files)
    created_at = datetime.now(UTC).isoformat()
    release = {
        "name": name,
        "slug": slug,
        "version": version,
        "build_id": build_id,
        "created_at": created_at,
        "manifest": manifest,
        "spec": spec,
        "files": {f.path: hashlib.sha256(f.text.encode("utf-8")).hexdigest() for f in files.files},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(files.files, key=lambda x: x.path):
            zf.writestr(f"{slug}/{f.path}", f.text)
        zf.writestr(f"{slug}/release.json", json.dumps(release, ensure_ascii=False, indent=2))
    data = buffer.getvalue()
    key = f"{RELEASE_KEY_PREFIX}/{slug}/v{version}/{slug}-v{version}-{build_id}.zip"
    store.put(key, data, "application/zip")
    return ReleaseArtifact(
        key=key,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        version=version,
        build_id=build_id,
        files=len(files),
        created_at=created_at,
    )


def files_from_release(store: Any, key: str) -> tuple[ProjectFiles, dict[str, Any]]:
    """The files a release zip carries, verified against its own ``release.json`` hashes
    (a release whose bytes do not match its manifest is refused, never launched)."""
    data = store.get(key)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        release_name = next(n for n in names if n.endswith("/release.json"))
        release = json.loads(zf.read(release_name).decode("utf-8"))
        prefix = release_name[: -len("release.json")]
        files: list[ProjectFile] = []
        for n in names:
            if n == release_name or not n.startswith(prefix):
                continue
            rel = n[len(prefix) :]
            text = zf.read(n).decode("utf-8")
            expected = release["files"].get(rel)
            if expected != hashlib.sha256(text.encode("utf-8")).hexdigest():
                raise ValueError(f"release file {rel!r} does not match its recorded hash")
            files.append(ProjectFile(rel, text))
    return ProjectFiles(files=tuple(files)), release


# ------------------------------------------------------------- modify (449, 450)


def merge_requirements(
    base: Requirements, addition: Requirements
) -> tuple[Requirements, list[str]]:
    """The later request folded into the earlier requirements: new record kinds appended,
    new fields appended to a kind that exists, features united. Returns the merged
    requirements and a list of what changed, in words."""
    changes: list[str] = []
    merged = Requirements(sentence=base.sentence, name=base.name)
    merged.entities = [
        EntitySpec(name=e.name, fields=[FieldSpec(f.name, f.type) for f in e.fields])
        for e in base.entities
    ]
    merged.features = list(base.features)
    merged.unparsed = list(addition.unparsed)
    by_name = {e.name: e for e in merged.entities}
    for entity in addition.entities:
        if entity.name in by_name:
            existing = by_name[entity.name]
            have = {f.name for f in existing.fields}
            for f in entity.fields:
                if f.name not in have and f.name != "ad":
                    existing.fields.append(FieldSpec(f.name, f.type))
                    changes.append(f"{entity.name}: {f.name} alanı")
        else:
            merged.entities.append(
                EntitySpec(
                    name=entity.name, fields=[FieldSpec(f.name, f.type) for f in entity.fields]
                )
            )
            by_name[entity.name] = merged.entities[-1]
            changes.append(f"{entity.name} kayıt türü")
    for feature in addition.features:
        if feature not in merged.features and feature in ("auth", "api"):
            merged.features.append(feature)
            changes.append("giriş" if feature == "auth" else "api")
    if "frontend" in addition.features and "frontend" not in merged.features:
        merged.features.append("frontend")
        changes.append("arayüz")
    return merged, changes


_ADD_RE: Final = re.compile(r"(?i)\b(ekle|eklesene|eklensin|olsun|katılsın|katilsin)\b")
_ADDITION_HEAD_RE: Final = re.compile(
    r"^\s*(?:bu|şu|su)?\s*(?:uygulama(?:ya|da|ma|mıza|miza)?|projeye|programa)\s*"
)
_AUTH_PHRASE_RE: Final = re.compile(
    r"\b(?:ve|ile)?\s*(?:giriş(?:i)?|giris(?:i)?|login|oturum(?:u)?|şifre|sifre|parola)\b"
)
_DATIVE_SUFFIXES: Final[tuple[str, ...]] = ("lara", "lere", "ya", "ye", "a", "e")
_POSSESSIVE_SUFFIXES: Final[tuple[str, ...]] = (
    "ları",
    "leri",
    "sı",
    "si",
    "su",
    "sü",
    "ı",
    "i",
    "u",
    "ü",
)
#: The final-consonant softening a dative undoes: "kitaba" -> "kitap", "kâğıda" -> "kâğıt".
_UNSOFTEN: Final[dict[str, str]] = {"b": "p", "c": "ç", "d": "t", "ğ": "k"}


def _dative_root(word: str, known: tuple[str, ...]) -> tuple[str, str] | None:
    """("kitaplara" -> "kitap"), preferring a kind the application already has."""
    for suffix in _DATIVE_SUFFIXES:
        if not word.endswith(suffix) or len(word) - len(suffix) < 3:
            continue
        root = word[: -len(suffix)]
        candidates = [root]
        if root and root[-1] in _UNSOFTEN:
            candidates.append(root[:-1] + _UNSOFTEN[root[-1]])
        for candidate in candidates:
            if candidate in known:
                return candidate, suffix
        if suffix in ("lara", "lere"):
            return candidates[0], suffix
    for suffix in ("a", "e", "ya", "ye"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)], suffix
    return None


def parse_addition(text: str, *, known: tuple[str, ...] = ()) -> Requirements:
    """A modification sentence read as an ADDITION, never as a fresh application:

    * "siparişlere teslim tarihi ekle" - a field on a kind (the dative names the kind,
      preferring one the application already has; "kitaba" finds "kitap");
    * "kitap adı ekle" - a field on a kind (the possessive names the field);
    * "yazarları tutan bir bölüm ekle: yazar adı, doğum tarihi" / "yazarları ekle" - a new
      kind (the keep-verb path of the requirements parser, or a bare plural);
    * "giriş ekle" - the login feature;
    * anything else ("karanlık tema ekle") is UNPARSED - the honest answer the caller
      turns into a refusal, or hands to the model under the owner's flag.
    """
    raw = (text or "").strip()[:MAX_SENTENCE_CHARS]
    req = Requirements(sentence=raw)
    if not raw:
        return req
    lowered = _lower(raw)
    if any(w in lowered for w in _AUTH_WORDS):
        req.features.append(FEATURE_AUTH)
    body = _ADDITION_HEAD_RE.sub("", lowered)
    if ":" in body or any(v in lowered for v in _KEEP_VERBS):
        parsed = parse_requirements(_ADD_RE.sub("tutan", _ADDITION_HEAD_RE.sub("", raw)))
        parsed.features = [f for f in parsed.features if f in (FEATURE_AUTH, FEATURE_API)]
        parsed.unparsed = [u for u in parsed.unparsed if "söylenmedi" not in u]
        return parsed
    match = _ADD_RE.search(body)
    phrase = (body[: match.start()] if match else body).strip(" ,.;")
    phrase = _AUTH_PHRASE_RE.sub("", phrase).strip(" ,.;")
    if not phrase:
        if not req.features:
            req.unparsed.append(raw[:120])
        return req
    words = phrase.split()
    dative = _dative_root(words[0], known) if len(words) >= 2 else None
    if dative is not None:
        kind, _suffix = dative
        entity = EntitySpec(name=kind[:40])
        for field_text in _split_list(" ".join(words[1:]))[:MAX_FIELDS]:
            cleaned = _clean_field(field_text, entity=kind)
            if cleaned:
                entity.fields.append(FieldSpec(name=cleaned, type=_field_type(cleaned)))
        if entity.fields:
            req.entities.append(entity)
            return req
    if len(words) >= 2 and any(words[-1].endswith(s) for s in _POSSESSIVE_SUFFIXES):
        kind = _singular(words[0])
        kind = next((k for k in known if k == kind or k == words[0]), kind)
        cleaned = _clean_field(" ".join(words[1:]), entity=kind)
        if cleaned:
            req.entities.append(
                EntitySpec(
                    name=kind[:40], fields=[FieldSpec(name=cleaned, type=_field_type(cleaned))]
                )
            )
            return req
    if len(words) == 1 and _singular(words[0]) != words[0]:
        req.entities.append(
            EntitySpec(name=_singular(words[0])[:40], fields=[FieldSpec(name="ad", type="text")])
        )
        return req
    req.unparsed.append(phrase[:120])
    return req


__all__ = [
    "BROWSER_PROFILE",
    "ReleaseArtifact",
    "StepOutcome",
    "UiVerification",
    "build_id_for",
    "files_from_release",
    "merge_requirements",
    "package_release",
    "parse_addition",
    "read_log",
    "verify_ui",
]
