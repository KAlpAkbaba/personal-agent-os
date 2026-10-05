"""The Ofis's İlerleme strip: ``app.team.progress`` reads the roadmap's JARVIS table, its
binding order and the v1.0 feature matrix of the tree it runs in (the owner, 2026-10-03:
"roadmap'e göre projenin ortalama yüzde kaçı tamamlandı, yüzde kaçı kaldı göremiyorum").

The numbers asserted against the REAL documents are the ones the Danışman counted by hand that
evening; when the documents change, these numbers change with them - that is the point.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.team import progress
from app.team import store as team_store
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]

JARVIS = """\
### What JARVIS does, and where this system stands (2026-09-27)

| JARVIS | PersonalAgentOS today | State |
|---|---|---|
| Talks | voice | **HAVE** — quality work remains |
| Everywhere | two PCs | **PARTIAL** (2026-09-29) — two PCs |
| Runs the house | Home Assistant | **MISSING** — adopt |
| **Records everything** — "her şeyi" | ledger | **PARTIAL** — its own line |
| Flies the suit | — | **NEVER / HARDWARE** — see the limits |

### The limits, stated once
"""

ORDER = """\
### The order (binding until the owner changes it)

1. **Memory** — DONE 2026-09-29: PR-1 in production.
2. **browser-use, anywhere** — the JARVIS that does anything on the web:
   - 2a. **In the owner's own Chrome**: PR-C remains.
3. **Secretary** — Radicale.

### Approved ideas
"""

MATRIX_HEAD = (
    "| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _matrix(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f"| {n + 1} | f{n} | a | b | {impl} | {proof} | P0 |\n"
        for n, (impl, proof) in enumerate(rows)
    )
    return "# MATRIX\n\n| Kısaltma | Açılım |\n|---|---|\n| `PR` | PROVEN_REAL |\n\n" + (
        MATRIX_HEAD + body
    )


# ------------------------------------------------------------------ the real documents


def test_the_real_roadmap_jarvis_table_is_17_rows_5_have_6_partial_6_missing_47_percent():
    # 2026-10-05: the owner's five JARVIS rows (calls him, home stock, follows him outside,
    # verifies what he hears, voice anywhere) - 12 rows at 62% became 17 at 47% (8 of 17).
    jarvis = progress.parse_jarvis((REPO / progress.ROADMAP).read_text(encoding="utf-8"))
    assert jarvis is not None
    assert (jarvis["have"], jarvis["partial"], jarvis["missing"], jarvis["never"]) == (5, 6, 6, 1)
    assert jarvis["counted"] == 17
    assert jarvis["unknown"] == []
    assert jarvis["percent"] == 47
    assert len(jarvis["rows"]) == 18
    assert jarvis["rows"][0]["state"] == "have"
    assert jarvis["rows"][-1]["state"] == "never"


def test_the_real_order_is_seven_steps_with_memory_done():
    order = progress.parse_order((REPO / progress.ROADMAP).read_text(encoding="utf-8"))
    assert order is not None
    assert [s["n"] for s in order["steps"]] == [1, 2, 3, 4, 5, 6, 7]
    assert order["steps"][0] == {"n": 1, "title": "Memory", "state": "done"}
    assert order["steps"][1]["title"] == "browser-use, anywhere"
    assert order["next"]["n"] == 2


def test_the_real_matrix_is_750_rows_711_done_95_percent_and_20_percent_proven_real():
    v1 = progress.parse_matrix((REPO / progress.MATRIX).read_text(encoding="utf-8"))
    assert v1 is not None
    assert (v1["total"], v1["done"]) == (750, 711)
    assert v1["by_status"] == {
        "DONE": 711,
        "BLOCKED_PROVIDER": 19,
        "DEFERRED": 10,
        "PARTIAL": 9,
        "BLOCKED_OWNER": 1,
    }
    assert (v1["by_proof"]["PR"], v1["by_proof"]["PA"]) == (153, 586)
    assert (v1["percent_done"], v1["percent_proven_real"]) == (95, 20)


def test_the_whole_answer_reads_the_tree_and_names_the_release():
    answer = progress.progress(REPO, as_of="a" * 40)
    assert answer["as_of"] == "a" * 40
    assert answer["jarvis"]["percent"] == 47
    assert answer["v1"]["done"] == 711
    assert "0,5" in answer["rule"]


# ------------------------------------------------------------------ the rules


def test_partial_weighs_half_and_the_never_row_is_not_counted():
    jarvis = progress.parse_jarvis(JARVIS)
    assert jarvis is not None
    # HAVE 1 + PARTIAL 0.5 * 2 + MISSING 0 over 4 counted rows = 2/4 = 50 %; NEVER excluded.
    assert (jarvis["have"], jarvis["partial"], jarvis["missing"], jarvis["never"]) == (1, 2, 1, 1)
    assert jarvis["counted"] == 4
    assert jarvis["percent"] == 50
    assert jarvis["rows"][3] == {"name": 'Records everything — "her şeyi"', "state": "partial"}


def test_a_row_whose_state_cannot_be_read_is_unknown_named_and_not_done():
    text = JARVIS.replace("**HAVE** — quality work remains", "maybe later")
    jarvis = progress.parse_jarvis(text)
    assert jarvis is not None
    assert jarvis["unknown"] == ["Talks"]
    assert jarvis["rows"][0] == {"name": "Talks", "state": "unknown"}
    assert jarvis["counted"] == 4  # counted, as not done
    assert jarvis["percent"] == 25  # PARTIAL 0.5 * 2 over 4


def test_an_exact_half_percent_rounds_down_never_up():
    assert progress.percent(15, 24) == 62  # 62.5 -> 62
    assert progress.percent(711, 750) == 95  # 94.8 -> 95
    assert progress.percent(0, 0) is None


def test_order_steps_take_the_leading_marker_when_one_is_written():
    marked = ORDER.replace("2. **browser-use", "2. **PARTIAL** **browser-use").replace(
        "1. **Memory** — DONE", "1. **DONE** **Memory** — DONE"
    )
    order = progress.parse_order(marked)
    assert order is not None
    assert [(s["title"], s["state"]) for s in order["steps"]] == [
        ("Memory", "done"),
        ("browser-use, anywhere", "partial"),
        ("Secretary", "open"),
    ]
    assert order["percent"] == 50  # (1 + 0.5 + 0) / 3
    assert order["next"]["title"] == "browser-use, anywhere"


def test_order_without_markers_takes_done_from_the_step_line_and_leaves_the_rest_open():
    order = progress.parse_order(ORDER)
    assert order is not None
    assert [s["state"] for s in order["steps"]] == ["done", "open", "open"]
    assert order["percent"] == 33


def test_a_matrix_row_with_an_unreadable_status_is_not_done():
    v1 = progress.parse_matrix(_matrix([("DONE", "PR"), ("", "PA"), ("PARTIAL", "NYP")]))
    assert v1 is not None
    assert v1["total"] == 3
    assert v1["done"] == 1
    assert v1["by_status"]["unknown"] == 1
    assert (v1["percent_done"], v1["percent_proven_real"]) == (33, 33)


def test_a_missing_file_makes_its_section_null_not_an_error(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / progress.ROADMAP).write_text(JARVIS + "\n" + ORDER, encoding="utf-8")
    answer = progress.progress(tmp_path, as_of=None)
    assert answer["v1"] is None
    assert answer["jarvis"]["percent"] == 50
    assert answer["as_of"] is None
    empty = progress.progress(tmp_path / "nowhere", as_of=None)
    assert (empty["jarvis"], empty["order"], empty["v1"]) == (None, None, None)


def test_a_document_without_the_section_is_null():
    assert progress.parse_jarvis("# nothing here\n") is None
    assert progress.parse_order("# nothing here\n") is None
    assert progress.parse_matrix("# nothing here\n") is None


# ------------------------------------------------------------------ the office answer


@pytest.fixture()
def owner(tmp_path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    root = tmp_path / "team"
    (root / "reports").mkdir(parents=True)
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    app.state.team_store = team_store.FileStore(root)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    client.app_state = app.state
    return client


def test_the_office_answer_carries_progress_from_the_tree(owner):
    body = owner.get("/v1/team/office").json()
    assert body["progress"]["jarvis"]["percent"] == 47
    assert body["progress"]["v1"]["total"] == 750
    assert "agents" in body


def test_the_office_answer_with_no_documents_has_null_sections(owner, tmp_path):
    owner.app_state.progress_root = tmp_path / "empty"
    body = owner.get("/v1/team/office").json()
    assert body["progress"]["jarvis"] is None
    assert body["progress"]["v1"] is None


# ------------------------------------------------------------------ where production reads it
# The inspector's return of 2026-10-03: the api image ships neither document (its build context
# is services/api) and no setting could point the root anywhere else, so the strip read
# "okunamadı" three times on the Cloud Core. The root is now PAGENTOS_PROGRESS_ROOT when set, and
# the production compose mounts exactly the two documents, read-only, under that root.


def test_the_root_is_pagentos_progress_root_when_it_is_set(owner, tmp_path, monkeypatch):
    mounted = tmp_path / "mounted"
    (mounted / "docs" / "product").mkdir(parents=True)
    (mounted / "docs" / "ROADMAP.md").write_text(JARVIS + ORDER, encoding="utf-8")
    monkeypatch.setenv("PAGENTOS_PROGRESS_ROOT", str(mounted))
    body = owner.get("/v1/team/office").json()
    assert body["progress"]["jarvis"]["have"] == 1
    assert body["progress"]["jarvis"]["rows"][0]["name"] == "Talks"
    assert body["progress"]["v1"] is None  # not mounted there: null, not the tree's 750


def test_the_production_api_mounts_both_documents_read_only_under_that_root():
    import yaml

    services = yaml.safe_load(
        (REPO / "infra" / "docker" / "docker-compose.prod.yml").read_text("utf-8")
    )["services"]
    for name in ("api", "api-blue", "api-green"):
        service = services[name]
        root = service["environment"].get("PAGENTOS_PROGRESS_ROOT")
        assert root, f"{name}: PAGENTOS_PROGRESS_ROOT is not set"
        volumes = service.get("volumes", [])
        for doc in (progress.ROADMAP, progress.MATRIX):
            want = f"../../{doc.as_posix()}:{root.rstrip('/')}/{doc.as_posix()}:ro"
            assert want in volumes, f"{name}: {want} is not mounted"
