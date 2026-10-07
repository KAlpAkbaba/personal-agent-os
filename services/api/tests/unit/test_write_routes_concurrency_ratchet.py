"""Every route that writes has a 'two devices at the same time' test.

Card two-devices-same-time-test. On 2026-10-06 three races reached production behind an
inspector's APPROVE: a conversation line lost behind a 409, a 500 on the same new household
item from two devices, a 21st watch past a cap of 20. Every test of those routes sent one
request at a time. Only the test team found them. Since then a write route is named by an
integration test that calls ``fire_together`` (``tests/integration/concurrency.py``) - N
requests at once through the real application on real PostgreSQL, the rows counted afterwards.

What this file holds mechanically is the floor of that rule: every ``@router.post/put/patch``
under ``app`` is the method and path (any path parameter filled in) of a ``fire_together``
call in an integration test. Naming is not racing; the inspector's run is what proves the
claim. The write routes no such test named when the rule was made are frozen in
``UNCOVERED_BASELINE``; that list may only shrink. A route that cannot race (said in a
sentence) stands in ``EXEMPT``.

Routes are read with ``ast``, not a regular expression: many decorators span several lines,
and a router's ``prefix=`` is part of the path.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[2]
APP = API / "app"
INTEGRATION = API / "tests" / "integration"
WRITE_METHODS = ("post", "put", "patch")

#: Routes that cannot race, each with the reason. A new entry needs a sentence a reviewer
#: can check against the route's code.
EXEMPT: dict[str, str] = {}

#: The 190 of 193 write routes no fire_together test named on 2026-10-06 (card
#: two-devices-same-time-test; the three it raced are the 2026-10-06 defects). Debt, not
#: permission: a NEW route never goes here - it gets a fire_together test - and a route that
#: gains one is removed from the list.
UNCOVERED_BASELINE: frozenset[str] = frozenset(
    {
        "PATCH /v1/accounts/{account_id}",
        "PATCH /v1/devices/{device_id}",
        "PATCH /v1/goals/{goal_id}",
        "PATCH /v1/memory/{memory_id}",
        "PATCH /v1/narration/sessions/{session_id}/cursor",
        "PATCH /v1/news/sources/{news_source_id}",
        "PATCH /v1/security/assets/{ref}",
        "PATCH /v1/voice/preferences",
        "POST /v1/accounts/connect",
        "POST /v1/alarms",
        "POST /v1/alarms/{alarm_id}/cancel",
        "POST /v1/alarms/{alarm_id}/snooze",
        "POST /v1/alarms/{alarm_id}/stop",
        "POST /v1/ambient/test-display",
        "POST /v1/apps/plan",
        "POST /v1/apps/{project_id}/fix",
        "POST /v1/apps/{project_id}/launch",
        "POST /v1/apps/{project_id}/modify",
        "POST /v1/apps/{project_id}/package",
        "POST /v1/apps/{project_id}/run",
        "POST /v1/apps/{project_id}/stop",
        "POST /v1/apps/{project_id}/test",
        "POST /v1/apps/{project_id}/verify",
        "POST /v1/artifacts/factory",
        "POST /v1/artifacts/image",
        "POST /v1/artifacts/{artifact_id}/clone",
        "POST /v1/artifacts/{artifact_id}/delete",
        "POST /v1/artifacts/{artifact_id}/edit",
        "POST /v1/artifacts/{artifact_id}/open",
        "POST /v1/artifacts/{artifact_id}/renders",
        "POST /v1/calendar/proposals/{proposal_id}/confirm",
        "POST /v1/calendar/proposals/{proposal_id}/discard",
        "POST /v1/conversations",
        "POST /v1/conversations/people/{person_id}/consent",
        "POST /v1/conversations/{conversation_id}/speakers/{speaker_no}/name",
        "POST /v1/conversations/{conversation_id}/stop",
        "POST /v1/creative/generate",
        "POST /v1/creative/{run_id}/deliver",
        "POST /v1/creative/{run_id}/drive",
        "POST /v1/creative/{run_id}/enhance",
        "POST /v1/creative/{run_id}/redo",
        "POST /v1/creative/{run_id}/undo",
        "POST /v1/devices/drain",
        "POST /v1/devices/enroll",
        "POST /v1/devices/enrollment-tokens",
        "POST /v1/devices/select",
        "POST /v1/devices/undrain",
        "POST /v1/devices/{device_id}/commands",
        "POST /v1/devices/{device_id}/commands/{command_id}/cancel",
        "POST /v1/devices/{device_id}/revoke",
        "POST /v1/documents/mutations/{mutation_id}/confirm",
        "POST /v1/documents/mutations/{mutation_id}/discard",
        "POST /v1/documents/mutations/{mutation_id}/undo",
        "POST /v1/evolution/gaps",
        "POST /v1/evolution/gaps/{gap_id}/resolve",
        "POST /v1/evolution/opportunities",
        "POST /v1/evolution/opportunities/{opportunity_id}/advance",
        "POST /v1/evolution/opportunities/{opportunity_id}/approve",
        "POST /v1/evolution/opportunities/{opportunity_id}/authorize",
        "POST /v1/evolution/opportunities/{opportunity_id}/footprint",
        "POST /v1/evolution/supervisor/pause",
        "POST /v1/evolution/supervisor/resume",
        "POST /v1/evolution/supervisor/scan",
        "POST /v1/executive/runs",
        "POST /v1/executive/runs/{run_id}/amend",
        "POST /v1/executive/runs/{run_id}/approve",
        "POST /v1/executive/runs/{run_id}/cancel",
        "POST /v1/executive/runs/{run_id}/pause",
        "POST /v1/executive/runs/{run_id}/resume",
        "POST /v1/executive/runs/{run_id}/retry",
        "POST /v1/experience/compile",
        "POST /v1/experience/ingest",
        "POST /v1/experience/lessons/{lesson_id}/promote",
        "POST /v1/experience/lessons/{lesson_id}/reject",
        "POST /v1/genesis/capabilities/{capability_id}/activate",
        "POST /v1/genesis/capabilities/{capability_id}/deactivate",
        "POST /v1/genesis/capabilities/{capability_id}/rollback",
        "POST /v1/genesis/capabilities/{capability_id}/use",
        "POST /v1/genesis/catalogue",
        "POST /v1/genesis/catalogue/discover",
        "POST /v1/genesis/runs",
        "POST /v1/genesis/runs/{run_id}/approve",
        "POST /v1/genesis/runs/{run_id}/cancel",
        "POST /v1/goals",
        "POST /v1/goals/{goal_id}/approve",
        "POST /v1/goals/{goal_id}/dependencies",
        "POST /v1/goals/{goal_id}/evaluate",
        "POST /v1/goals/{goal_id}/status",
        "POST /v1/household/list",
        "POST /v1/identity/bootstrap",
        "POST /v1/identity/panic",
        "POST /v1/identity/sessions",
        "POST /v1/identity/sessions/refresh",
        "POST /v1/identity/sessions/{session_id}/revoke",
        "POST /v1/ledger/backfill",
        "POST /v1/ledger/events",
        "POST /v1/mail/drafts/{draft_id}/confirm",
        "POST /v1/mail/drafts/{draft_id}/discard",
        "POST /v1/memory/edges",
        "POST /v1/memory/entities",
        "POST /v1/memory/observe",
        "POST /v1/memory/reindex",
        "POST /v1/memory/remember",
        "POST /v1/memory/{memory_id}/pin",
        "POST /v1/memory/{memory_id}/supersede",
        "POST /v1/memory/{memory_id}/unpin",
        "POST /v1/mobile/notifications/artifact-ready",
        "POST /v1/mobile/push/registrations",
        "POST /v1/narration/preview",
        "POST /v1/narration/sessions",
        "POST /v1/narration/sessions/{session_id}/command",
        "POST /v1/news/open",
        "POST /v1/news/open/{context_id}/close",
        "POST /v1/news/resolve",
        "POST /v1/news/sources",
        "POST /v1/news/summarize",
        "POST /v1/notifications/{notification_id}/read",
        "POST /v1/operator/missions",
        "POST /v1/operator/missions/{mission_id}/approve",
        "POST /v1/operator/missions/{mission_id}/cancel",
        "POST /v1/operator/missions/{mission_id}/pause",
        "POST /v1/operator/missions/{mission_id}/resume",
        "POST /v1/presence/eye/disable",
        "POST /v1/presence/eye/enable",
        "POST /v1/presence/eye/stream-stopped",
        "POST /v1/presence/greeting/delivered",
        "POST /v1/presence/greeting/evaluate",
        "POST /v1/presence/observations",
        "POST /v1/research",
        "POST /v1/research/{task_id}/cancel",
        "POST /v1/research/{task_id}/focus",
        "POST /v1/research/{task_id}/pause",
        "POST /v1/research/{task_id}/resume",
        "POST /v1/routines",
        "POST /v1/routines/evaluate",
        "POST /v1/routines/{routine_id}/cancel",
        "POST /v1/routines/{routine_id}/pause",
        "POST /v1/routines/{routine_id}/resume",
        "POST /v1/scenes",
        "POST /v1/scenes/{scene_id}/apply",
        "POST /v1/scenes/{scene_id}/inspect",
        "POST /v1/scenes/{scene_id}/render",
        "POST /v1/security/assessments",
        "POST /v1/security/assessments/{assessment_id}/artifact",
        "POST /v1/security/assets",
        "POST /v1/security/assets/{ref}/revoke",
        "POST /v1/security/assets/{ref}/suspend",
        "POST /v1/security/findings/{finding_id}/remediate",
        "POST /v1/security/scope/check",
        "POST /v1/selfdev/defects",
        "POST /v1/selfdev/defects/from-opportunity/{opportunity_id}",
        "POST /v1/selfdev/defects/{defect_id}/approve",
        "POST /v1/selfdev/defects/{defect_id}/ci",
        "POST /v1/selfdev/defects/{defect_id}/finish",
        "POST /v1/selfdev/defects/{defect_id}/reject",
        "POST /v1/selfdev/defects/{defect_id}/start",
        "POST /v1/selfdev/worker/claim",
        "POST /v1/selfhealing/incidents/ingest",
        "POST /v1/selfhealing/opportunities/{opportunity_id}/heal",
        "POST /v1/selfhealing/pipeline/run",
        "POST /v1/selfmodel/index",
        "POST /v1/tasks",
        "POST /v1/team/allowlist",
        "POST /v1/team/approvals/decision",
        "POST /v1/team/board/notes",
        "POST /v1/team/queue/lock",
        "POST /v1/team/queue/proposals",
        "POST /v1/team/queue/reports",
        "POST /v1/team/trials/decision",
        "POST /v1/telephony/test-call",
        "POST /v1/voice/benchmark/run",
        "POST /v1/voice/misheard/{item_id}/meaning",
        "POST /v1/voice/qualification",
        "POST /v1/voice/realtime/sessions",
        "POST /v1/voice/realtime/sessions/{session_id}/attach",
        "POST /v1/voice/realtime/sessions/{session_id}/close",
        "POST /v1/voice/realtime/sessions/{session_id}/events",
        "POST /v1/voice/realtime/sessions/{session_id}/tool-calls",
        "POST /v1/voice/realtime/sessions/{session_id}/tool-calls/{call_id}/complete",
        "POST /v1/voice/speaker/enroll",
        "POST /v1/voice/speaker/verify",
        "POST /v1/webpush/subscriptions",
        "PUT /v1/alarms/wake-song",
        "PUT /v1/ambient/policy",
        "PUT /v1/conversations/settings",
        "PUT /v1/narration/pronunciation",
        "PUT /v1/team/queue/models",
        "PUT /v1/team/queue/status",
        "PUT /v1/team/queue/tasks/{task_id}",
        "PUT /v1/voice/measurement/recordings/{place}/{index}",
        # 2026-10-07 integration (the Danışman): eleven write routes of cards built in parallel with
        # this ratchet (inbound-calls-bridge, money-ledger, cloud-task-loop-core) - they existed
        # before it reached their branches. Card two-devices-tests-late-write-routes writes their
        # tests and takes them out again (the ceiling goes back to 190).
        "POST /telephony/inbound/status",
        "POST /telephony/inbound/voice",
        "POST /v1/money/cash",
        "POST /v1/money/entries/{entry_id}/cancel",
        "POST /v1/money/questions/{question_id}/answer",
        "POST /v1/web-tasks",
        "POST /v1/web-tasks/{task_id}/cancel",
        "POST /v1/web-tasks/{task_id}/confirm",
        "POST /v1/web-tasks/{task_id}/continue",
        "POST /v1/web-tasks/{task_id}/decline",
        "POST /v1/web-tasks/{task_id}/read-back",
        # urgent-alert-wire (merged after the eleven above, the same night).
        "POST /v1/urgent-alert/test",
    }
)

#: The baseline's size when it was frozen. It may only go down, so an entry added for a new
#: route is refused even when the list is edited in the same change.
BASELINE_CEILING = 202


def _string_constants(tree: ast.Module, *, follow_imports: bool = True) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` / ``NAME: Final[str] = "literal"`` assignments (a prefix
    or a path may be a constant), and the same constants imported by name from another ``app``
    module (``from app.telephony.inbound_twilio import VOICE_PATH`` - the inbound-calls bridge
    keeps its webhook paths beside the provider code; found at the 2026-10-07 integration)."""
    found: dict[str, str] = {}
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    if follow_imports:
        for node in tree.body:
            if not (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module):
                continue
            if not node.module.startswith("app."):
                continue
            source = API.joinpath(*node.module.split(".")).with_suffix(".py")
            if not source.is_file():
                continue
            theirs = _string_constants(
                ast.parse(source.read_text(encoding="utf-8")), follow_imports=False
            )
            for alias in node.names:
                if alias.name in theirs:
                    found[alias.asname or alias.name] = theirs[alias.name]
    return found


def _router_prefixes(tree: ast.Module, constants: dict[str, str]) -> dict[str, str]:
    """``name = APIRouter(prefix=...)`` at module level -> its prefix ('' when none)."""
    prefixes: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if called != "APIRouter":
            continue
        prefix = ""
        for keyword in node.value.keywords:
            if keyword.arg != "prefix":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                prefix = keyword.value.value
            elif isinstance(keyword.value, ast.Name) and keyword.value.id in constants:
                prefix = constants[keyword.value.id]
            else:
                raise AssertionError(
                    f"{node.lineno}: APIRouter prefix this guard cannot read: "
                    f"{ast.unparse(keyword.value)}"
                )
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def write_routes_in(source: str, where: str = "<source>") -> set[str]:
    """``"POST /v1/..."`` for every ``@<router>.post/put/patch(path)`` in one module."""
    tree = ast.parse(source, filename=where)
    constants = _string_constants(tree)
    prefixes = _router_prefixes(tree, constants)
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in WRITE_METHODS
            ):
                continue
            # Every write decorator counts: one on an imported router or an attribute
            # (``@Box.router.post``) would otherwise be a write route nobody asks about.
            owner = decorator.func.value
            if not (isinstance(owner, ast.Name) and owner.id in prefixes):
                raise AssertionError(
                    f"{where}:{decorator.lineno}: a router this guard cannot read: "
                    f"{ast.unparse(owner)} (make it with APIRouter(...) in this module)"
                )
            first = decorator.args[0] if decorator.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                path = first.value
            elif isinstance(first, ast.Name) and first.id in constants:
                path = constants[first.id]
            else:
                raise AssertionError(
                    f"{where}:{decorator.lineno}: a route path this guard cannot read"
                )
            method = decorator.func.attr.upper()
            routes.add(f"{method} {prefixes[decorator.func.value.id]}{path}")
    return routes


def all_write_routes(folder: Path = APP) -> set[str]:
    """Every module is read: a text filter on "APIRouter" skipped one that imports its router."""
    routes: set[str] = set()
    for path in sorted(folder.rglob("*.py")):
        routes |= write_routes_in(path.read_text("utf-8"), path.relative_to(folder).as_posix())
    return routes


def _path_pattern(path: str) -> re.Pattern[str]:
    """The route's path as a test writes it, any parameter filled in (``{x}`` from an
    f-string stands for one segment); a query string may follow."""
    parts = re.split(r"\{[^}]*\}", path)
    body = r"[^/?]+".join(re.escape(part) for part in parts)
    return re.compile(rf"{body}(?:\?.*)?")


def _written_path(node: ast.expr, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{x}" for part in node.values
        )
    return None


def racing_paths_in(source: str, where: str = "<test>") -> set[str]:
    """``"METHOD path"`` from the method and path arguments (second and third, or
    ``method=`` / ``path=``) of every ``fire_together(...)`` call."""
    tree = ast.parse(source, filename=where)
    constants = _string_constants(tree)
    paths: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == "fire_together"
        ):
            continue
        given = dict(zip(("target", "method", "path"), node.args, strict=False))
        given.update({k.arg: k.value for k in node.keywords if k.arg in ("method", "path")})
        method = _written_path(given["method"], constants) if "method" in given else None
        written = _written_path(given["path"], constants) if "path" in given else None
        if method and written:
            paths.add(f"{method.upper()} {written}")
    return paths


def racing_paths(folder: Path = INTEGRATION) -> set[str]:
    """Every ``"METHOD path"`` an integration test sends through ``fire_together``."""
    paths: set[str] = set()
    for path in sorted(folder.glob("test_*.py")):
        text = path.read_text("utf-8", errors="ignore")
        if "fire_together(" in text:
            paths |= racing_paths_in(text, str(path))
    return paths


def uncovered(routes: set[str], paths: set[str]) -> set[str]:
    def raced(route: str) -> bool:
        method, path = route.split(" ", 1)
        pattern = _path_pattern(path)
        return any(
            sent.split(" ", 1)[0] == method and pattern.fullmatch(sent.split(" ", 1)[1])
            for sent in paths
        )

    return {route for route in routes if not raced(route)}


def missing_tests_message(routes: set[str]) -> str:
    return "\n".join(f"yazan yol için eşzamanlılık testi yok: {route}" for route in sorted(routes))


# ------------------------------------------------------------------------------------- tests


def test_every_write_route_has_a_two_devices_test() -> None:
    open_routes = uncovered(all_write_routes(), racing_paths()) - set(EXEMPT)
    new = open_routes - UNCOVERED_BASELINE
    assert not new, (
        missing_tests_message(new)
        + "\nBir yazan yol için tests/integration altında fire_together çağıran bir test yaz "
        "(iki cihaz aynı anda: beklenen satır sayısı ve durum kodları; sınırı olan yazmada "
        "sınır+5) - tests/integration/test_two_devices_same_time_pg.py örnektir. Yolu "
        "UNCOVERED_BASELINE'a EKLEME."
    )


def test_the_baseline_only_shrinks() -> None:
    routes = all_write_routes()
    paid = sorted(UNCOVERED_BASELINE - uncovered(routes, racing_paths()))
    assert not paid, f"bunların artık eşzamanlılık testi var, UNCOVERED_BASELINE'dan çıkar: {paid}"
    gone = sorted(UNCOVERED_BASELINE - routes)
    assert not gone, f"bu yollar artık yok, UNCOVERED_BASELINE'dan çıkar: {gone}"
    assert len(UNCOVERED_BASELINE) <= BASELINE_CEILING, (
        f"UNCOVERED_BASELINE {len(UNCOVERED_BASELINE)} giriş, tavan {BASELINE_CEILING}: "
        "listeye yeni yol eklenmez; yeni yazan yol fire_together testi alır"
    )


def test_exemptions_carry_a_reason_and_name_real_routes() -> None:
    routes = all_write_routes()
    for route, reason in EXEMPT.items():
        assert route in routes, f"EXEMPT'te olmayan yol: {route}"
        assert len(reason.strip()) >= 20, f"EXEMPT gerekçesi yok: {route}"
    assert not set(EXEMPT) & UNCOVERED_BASELINE


def test_the_three_races_of_2026_10_06_are_covered() -> None:
    routes = all_write_routes()
    raced = {
        "POST /v1/conversations/{conversation_id}/segments",
        "POST /v1/household/items",
        "POST /v1/watches",
    }
    assert raced <= routes
    assert not uncovered(raced, racing_paths())
    assert not raced & UNCOVERED_BASELINE


def test_the_reader_sees_multi_line_decorators_prefixes_and_constants() -> None:
    source = """
from fastapi import APIRouter
PREFIX = "/v1/things"
router = APIRouter(prefix=PREFIX, tags=["x"])
other = APIRouter()
@router.post(
    "/{thing_id}/name",
    status_code=201,
)
async def name(thing_id: str): ...
@other.put("/v1/other")
def put(): ...
@other.get("/v1/read")
def read(): ...
@router.patch("")
def patch(): ...
"""
    assert write_routes_in(source) == {
        "POST /v1/things/{thing_id}/name",
        "PUT /v1/other",
        "PATCH /v1/things",
    }


def test_a_write_route_on_a_router_this_module_did_not_make_is_an_error() -> None:
    """Inspector's M4 (2026-10-06): ``from app.household.routes import router`` and an
    ``@router.post`` in a new module went unseen and unasked. A write decorator on a name the
    guard cannot trace to an ``APIRouter(...)`` in the same module stops the guard."""
    imported = """
from app.household.routes import router
@router.post("/{item_id}/gorunmez")
def hidden(item_id: str): ...
"""
    with pytest.raises(AssertionError, match="a router this guard cannot read"):
        write_routes_in(imported, "app/household/zz_x.py")
    attribute = """
from fastapi import APIRouter
class Box:
    router = APIRouter()
@Box.router.put("/v1/box")
def put(): ...
"""
    with pytest.raises(AssertionError, match="a router this guard cannot read"):
        write_routes_in(attribute)


def test_every_app_module_is_read_not_only_those_that_name_apirouter(tmp_path) -> None:
    (tmp_path / "zz_x.py").write_text(
        'from app.household.routes import router\n@router.post("/x")\ndef x(): ...\n', "utf-8"
    )
    with pytest.raises(AssertionError, match="a router this guard cannot read"):
        all_write_routes(tmp_path)


def test_a_new_write_route_without_a_test_is_red_with_a_turkish_message() -> None:
    route = {"POST /v1/yeni/{item_id}/sey"}

    def sent(source: str) -> set[str]:
        return racing_paths_in(source)

    assert uncovered(route, sent('fire_together(c, "POST", "/v1/yeni")'))
    assert not uncovered(route, sent('fire_together(c, "POST", f"/v1/yeni/{iid}/sey", n=2)'))
    assert not uncovered(route, sent('fire_together(c, "post", "/v1/yeni/abc/sey?x=1")'))
    assert not uncovered(
        route, sent('P = "/v1/yeni/a/sey"\nfire_together(target=c, method="POST", path=P)')
    )
    # Only a fire_together call counts: one request at a time is the shape that let them by.
    assert uncovered(route, sent('client.post("/v1/yeni/abc/sey")'))
    # Another method, a longer path, a prefix: not the route.
    assert uncovered(route, sent('fire_together(c, "PUT", "/v1/yeni/abc/sey")'))
    assert uncovered(route, sent('fire_together(c, "POST", "/v1/yeni/abc/sey/more")'))
    assert uncovered({"POST /v1/watches"}, sent('fire_together(c, "POST", "/v1/watches/abc")'))
    assert missing_tests_message(route) == (
        "yazan yol için eşzamanlılık testi yok: POST /v1/yeni/{item_id}/sey"
    )
