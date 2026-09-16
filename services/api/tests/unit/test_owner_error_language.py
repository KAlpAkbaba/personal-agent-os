"""B22 req 704/705: the owner reads Turkish, never a Python exception.

The matrix files 705 as BROKEN and trust-breaking, with a production number beside it: ten
of nineteen research runs failed, and what the owner saw for them was the failure's own
English. Thirty-three route handlers answered with `HTTPException(detail=str(exc))`, and
the exceptions behind those are developer sentences — "no matching weekday within a week —
refusing to guess", "only a ringing alarm can be snoozed", "unknown IANA timezone: 'x'".
The web renders `detail` verbatim.

Three guards here, and the AST one is the point: a dictionary is a thing somebody has to
remember to use, and the only way "no Python errors shown" stays true is if a route that
passes an exception to the owner cannot be written without a test saying so.
"""

from __future__ import annotations

import ast
import enum
import importlib
import re
from pathlib import Path

import pytest

from app.errors import owner_detail, owner_sentence
from app.errors.catalog import TR, UNKNOWN_WHAT, describe, sentence
from app.errors.owner import looks_like_developer_text

APP = Path(__file__).resolve().parents[2] / "app"

#: The eight subsystem taxonomies. Read from the modules rather than restated, so a new
#: family cannot be added without this test seeing it.
ERROR_MODULES = (
    "app.evolution.errors",
    "app.identity.errors",
    "app.memory.errors",
    "app.mobile.errors",
    "app.release.errors",
    "app.security.errors",
    "app.selfhealing.errors",
    "app.voice.errors",
)


#: The OTHER half of the taxonomy, and the reason this test reads two sources. Not every
#: subsystem declares an Enum: news, research, artifacts, the device action path and the
#: operator use module-level `ERROR_*` constants and inline `"error_class": "..."` literals,
#: and they reach the owner through exactly the same surfaces. A completeness test that read
#: only the enums would have been both halves of one claim - it would have passed over forty
#: classes the owner can actually meet, and B22 found them only because a route rewrite
#: broke a news test that pinned one of them by name.
_STRING_CLASS = re.compile(
    r'(?:^|\s)ERROR_[A-Z_]+\s*=\s*"([a-z_]+)"|"error_class":\s*"([a-z_]+)"', re.M
)


def _declared_classes() -> set[str]:
    found: set[str] = set()
    for name in ERROR_MODULES:
        module = importlib.import_module(name)
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, enum.Enum):
                found.update(str(member.value) for member in obj)
    for path in APP.rglob("*.py"):
        for match in _STRING_CLASS.finditer(path.read_text(encoding="utf-8")):
            found.add(match.group(1) or match.group(2))
    return found


# --------------------------------------------------------------- req 704: the dictionary


def test_every_error_class_has_turkish():
    """Both directions, like every complete map in this repository.

    A class with no entry means the owner meets a token; an entry for a class nobody
    declares is a sentence that can never be shown, and both are how a dictionary rots.
    """
    declared = _declared_classes()
    assert declared, "the error taxonomies are no longer readable; this test is stale"
    assert declared - set(TR) == set(), sorted(declared - set(TR))
    assert set(TR) - declared == set(), sorted(set(TR) - declared)


def test_every_sentence_is_written_for_a_person():
    for token, message in TR.items():
        assert message.what, token
        # No class tokens, no CamelCase identifiers, no code shapes.
        assert not looks_like_developer_text(message.sentence()), token
        assert "_" not in message.what, f"{token}: the WHAT reads like an identifier"
        assert message.what[0].isupper(), f"{token}: starts mid-sentence"
        # Two sentences at most: what happened, what can be done.
        assert message.sentence().count(".") <= 3, token


def test_an_unknown_class_is_named_rather_than_guessed():
    answer = describe("some_class_nobody_declared")
    assert UNKNOWN_WHAT in answer.what
    # The token is IN the sentence: it is what the log says too, and a generic "bir hata
    # oluştu" would hide which failure this was.
    assert "some_class_nobody_declared" in answer.what
    assert sentence(None).startswith(UNKNOWN_WHAT)


def test_a_remedy_the_owner_can_act_on_says_who_can_act():
    # The two families where only the owner can do anything name them explicitly, because
    # "yapılandırma eksik" without an actor is a sentence that waits for nobody.
    assert "yalnızca sen" in TR["provider_auth_missing"].remedy
    assert "yalnızca sen" in TR["generator_not_configured"].remedy
    assert "yalnızca sen" in TR["permission_denied"].remedy


# ------------------------------------------------------- req 705: no exception escapes


def test_the_sanitiser_recognises_a_python_failure():
    assert looks_like_developer_text("ValueError: no matching weekday within a week")
    assert looks_like_developer_text("KeyError('device_id')")
    assert looks_like_developer_text("Traceback (most recent call last):")
    assert looks_like_developer_text('File "app/alarms/routes.py", line 144')
    assert looks_like_developer_text("sqlalchemy.exc.OperationalError: no such table")
    assert looks_like_developer_text("SELECT * FROM alarms WHERE id = ?")
    # And leaves Turkish alone.
    assert not looks_like_developer_text("Geçmiş bir zaman söyledin efendim.")
    assert not looks_like_developer_text("Alarmı kuramadım; saati anlayamadım.")


def test_a_specific_message_that_is_really_an_exception_is_refused():
    body = owner_detail("validation_error", specific="ValueError: bad input")
    assert body["message"] == sentence("validation_error")
    assert "ValueError" not in body["message"]
    # A real Turkish sentence from the domain IS used - that is what `specific` is for.
    assert owner_sentence("validation_error", specific="Geçmiş bir saat söyledin.") == (
        "Geçmiş bir saat söyledin."
    )


def _exception_names(handler: ast.ExceptHandler) -> set[str]:
    return {handler.name} if handler.name else set()


#: The one thing a handler may read off its exception and hand to the owner: the CLASS.
#: A class is a token from a closed taxonomy that `app.errors.catalog` translates; the
#: exception's text is developer English and never crosses. `exc.message`, `exc.args` and
#: `str(exc)` are all mentions of the exception that are NOT this, and all fail.
ALLOWED_ATTRIBUTE = "error_class"


def _mentions(node: ast.AST, names: set[str]) -> bool:
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Name) and sub.id in names):
            continue
        parent = _parent_of(node, sub)
        if isinstance(parent, ast.Attribute) and parent.attr == ALLOWED_ATTRIBUTE:
            continue
        return True
    return False


def _parent_of(root: ast.AST, target: ast.AST) -> ast.AST | None:
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            if child is target:
                return node
    return None


def _http_exception_offenders(tree: ast.AST, path: Path) -> list[str]:
    """Every `HTTPException(detail=<something built from the caught exception>)`."""
    offenders: list[str] = []
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        bound = _exception_names(handler)
        if not bound:
            continue
        for call in (n for n in ast.walk(handler) if isinstance(n, ast.Call)):
            func = call.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "HTTPException":
                continue
            for keyword in call.keywords:
                if keyword.arg != "detail":
                    continue
                if _mentions(keyword.value, bound):
                    offenders.append(f"{path.name}:{call.lineno}")
    return offenders


def test_no_route_answers_the_owner_with_a_python_exception():
    """The structural half, and the reason this stays fixed.

    A dictionary is something a writer has to remember; this is something they cannot
    forget. Any `HTTPException` whose `detail` is built from the caught exception —
    `str(exc)`, `f"{exc}"`, `f"{type(exc).__name__}: {exc}"` — fails here, wherever it is
    written, including in a module added next year.
    """
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "HTTPException" not in source:
            continue
        offenders.extend(
            f"{path.relative_to(APP)}:{entry.split(':')[1]}"
            for entry in _http_exception_offenders(ast.parse(source), path)
        )
    assert offenders == [], (
        "these answer the owner with a Python exception; use app.errors.owner_detail:\n"
        + "\n".join(sorted(offenders))
    )


@pytest.mark.parametrize("token", sorted(TR))
def test_each_entry_renders_a_full_sentence(token: str):
    text = sentence(token)
    assert text.endswith(".")
    assert len(text) > 20, token


# ------------------------------------------------- the web reads the same taxonomy

WEB_FAILURE = (
    Path(__file__).resolve().parents[4] / "apps" / "web" / "app" / "lib" / "errors" / "failure.ts"
)


def _web_sets() -> dict[str, set[str]]:
    source = WEB_FAILURE.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for name in (
        "RETRYABLE_CLASSES",
        "PROVIDER_BLOCKED_CLASSES",
        "PERMISSION_CLASSES",
        "OWNER_ACTION_CLASSES",
    ):
        match = re.search(
            rf"{name}: ReadonlySet<string> = new Set\(\[(.*?)\]\)", source, re.S
        )
        assert match, f"{name} not found in the web client"
        out[name] = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    return out


def test_the_web_failure_kinds_match_the_catalogue():
    """B22 req 708-711: the browser cannot import Python, so it restates the classes.

    Two lists in two languages is the shape this repository has been bitten by before —
    both sides green, both describing a different contract. Every class the web sorts into
    a bucket must be one the server can actually send AND one the catalogue can translate;
    otherwise the panel is waiting for a word that never comes.
    """
    known = _declared_classes() | set(TR)
    for name, classes in _web_sets().items():
        assert classes, name
        unknown = classes - known
        assert unknown == set(), f"{name} lists classes the server never sends: {sorted(unknown)}"


def test_no_class_is_in_two_web_buckets():
    """A class that is both "retryable" and "waiting on a key" would render as whichever
    branch ran first — which is how a retry button ends up on a missing credential."""
    sets = _web_sets()
    seen: dict[str, str] = {}
    for name, classes in sets.items():
        for cls in classes:
            assert cls not in seen, f"{cls} is in both {seen.get(cls)} and {name}"
            seen[cls] = name
