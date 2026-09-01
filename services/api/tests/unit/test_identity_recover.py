"""M9 unit tests: the host-side owner recovery CLI (SECURITY_MODEL §10).

`python -m app.identity.recover` is the answer to "I lost the credential that
authenticates every API call". It must work without the API, mint a genuinely
new credential, and — because a credential you had to recover may have leaked —
revoke the sessions minted under the old one by default.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.identity import recover
from app.identity.root import FileCredentialRoot
from app.identity.runtime import IdentityRuntime
from tests.identity_support import make_identity_engine


@pytest.fixture()
def runtime(tmp_path) -> IdentityRuntime:
    return IdentityRuntime(
        Settings(_env_file=None),
        engine=make_identity_engine(),
        root=FileCredentialRoot(tmp_path / "identity", harden=False),
    )


def run_json(capsys, runtime: IdentityRuntime, *argv: str) -> dict:
    assert recover.main([*argv, "--json"], runtime=runtime) == 0
    # structlog also writes single-line JSON to stdout; the CLI's own payload is
    # the pretty-printed block that starts at the last bare `{` line.
    lines = capsys.readouterr().out.splitlines()
    start = max(i for i, line in enumerate(lines) if line == "{")
    return json.loads("\n".join(lines[start:]))


# -------------------------------------------------------------------- status


def test_status_reports_an_unbootstrapped_system(capsys, runtime) -> None:
    payload = run_json(capsys, runtime, "--status")
    assert payload["action"] == "status"
    assert payload["bootstrapped"] is False
    assert payload["root"]["kind"] == "file"
    assert payload["active_sessions"] == 0
    assert payload["session_ttl_s"] > 0


def test_status_is_the_default_action(capsys, runtime) -> None:
    payload = run_json(capsys, runtime)
    assert payload["action"] == "status"


def test_status_reports_bootstrap_state_and_live_sessions(capsys, runtime) -> None:
    runtime.service.bootstrap()
    runtime.service.issue_session(client_kind="mobile", label="phone")
    payload = run_json(capsys, runtime, "--status")
    assert payload["bootstrapped"] is True
    assert payload["active_sessions"] == 1
    assert payload["rotations"] == 0


# -------------------------------------------------------------------- rotate


def test_rotate_mints_a_new_credential_without_the_old_one(capsys, runtime) -> None:
    old = runtime.service.bootstrap()
    payload = run_json(capsys, runtime, "--rotate")
    new = payload["owner_credential"]
    assert new != old
    assert new.startswith("pagentos_ok_")
    assert not runtime.service.verify_owner_credential(old)
    assert runtime.service.verify_owner_credential(new)


def test_rotate_works_on_a_system_that_was_never_bootstrapped(capsys, runtime) -> None:
    """Recovery is also the way out of 'the root file was deleted'."""
    payload = run_json(capsys, runtime, "--rotate")
    assert runtime.service.verify_owner_credential(payload["owner_credential"])


def test_rotate_revokes_existing_sessions_by_default(capsys, runtime) -> None:
    runtime.service.bootstrap()
    issued = [runtime.service.issue_session(client_kind="cli") for _ in range(3)]
    payload = run_json(capsys, runtime, "--rotate")
    assert payload["sessions_revoked"] == 3
    for session in issued:
        assert not runtime.service.verify(session.token).ok


def test_rotate_can_keep_sessions_when_the_owner_asks(capsys, runtime) -> None:
    runtime.service.bootstrap()
    issued = runtime.service.issue_session(client_kind="cli")
    payload = run_json(capsys, runtime, "--rotate", "--keep-sessions")
    assert payload["sessions_revoked"] == 0
    assert runtime.service.verify(issued.token).ok


def test_rotate_records_the_rotation_in_the_root(capsys, runtime) -> None:
    runtime.service.bootstrap()
    run_json(capsys, runtime, "--rotate")
    run_json(capsys, runtime, "--rotate")
    record = runtime.root.load()
    assert record is not None
    assert record.rotations == 2
    assert record.rotated_at is not None


# ---------------------------------------------------------------- revoke-all


def test_revoke_all_kills_sessions_but_keeps_the_credential(capsys, runtime) -> None:
    credential = runtime.service.bootstrap()
    issued = runtime.service.issue_session(client_kind="cli")
    payload = run_json(capsys, runtime, "--revoke-all")
    assert payload["sessions_revoked"] == 1
    assert not runtime.service.verify(issued.token).ok
    assert runtime.service.verify_owner_credential(credential)


# ------------------------------------------------------------------- output


def test_human_output_shows_the_credential_once_with_a_warning(capsys, runtime) -> None:
    runtime.service.bootstrap()
    assert recover.main(["--rotate"], runtime=runtime) == 0
    out = capsys.readouterr().out
    assert "OWNER CREDENTIAL: pagentos_ok_" in out
    assert "shown once" in out


def test_status_output_never_contains_the_credential_hash(capsys, runtime) -> None:
    runtime.service.bootstrap()
    record = runtime.root.load()
    assert record is not None
    assert recover.main(["--status"], runtime=runtime) == 0
    assert record.credential_hash not in capsys.readouterr().out


def test_mutually_exclusive_actions_are_rejected(runtime) -> None:
    with pytest.raises(SystemExit):
        recover.main(["--rotate", "--status"], runtime=runtime)
