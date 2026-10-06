"""radicale-stack-ops: the owner's calendar is in the backup, the drill and the restore -
proven by RUNNING the scripts, not by reading them.

backup-cloud-core.sh, restore-cloud-core.sh and install-radicale.sh run here under Git Bash
against a sandbox: a fake /mnt/pagentos-data and /opt/pagentos, and fake docker / restic /
flock / chown / chmod (the layout scripts/tests/cloud-release.tests.ps1 uses for the release
script). The fake restic keeps the one snapshot it was given as a directory, so the restore
reads back exactly what the backup wrote, through the real backup_manifest.py.

Git Bash is required: on this machine it is installed, and a skipped run is not evidence
(NOT_RUN is never a pass), so its absence FAILS.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CLOUD = REPO / "scripts" / "cloud"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe") if os.name == "nt" else Path("/bin/bash")
SNAPSHOT = "ab" * 32
PRE_RESTORE = "cd" * 32
PASSWORD = "Takvim-Gizli-Parola-7781"

FAKE_DOCKER = r"""#!/usr/bin/env bash
state=${FAKE_STATE:?}
printf '%s\n' "$*" >> "$state/docker.log"
cmd=$1; shift
case "$cmd" in
  exec)
    [ "$1" = "-i" ] && shift
    shift
    case "$1" in
      pg_dumpall) echo "-- roles";;
      psql)
        case "$*" in
          *pg_database*) echo pagentos_prod;;
          *alembic_version*) echo 0066_watches;;
          *) cat >/dev/null;;
        esac;;
      pg_dump) echo "FAKE-DUMP";;
      pg_restore)
        cat >/dev/null
        case "$*" in *--data-only*) printf 'COPY public.t (a) FROM stdin;\n1\n\\.\n';; esac;;
      mc) echo '{"status":"success","key":"artifacts/"}';;
      *) :;;
    esac;;
  cp)
    case "$1" in
      *:/tmp/pagentos-backup-*) mkdir -p "$2/artifacts"; echo object > "$2/artifacts/a.txt";;
      *:/tmp/pagentos-restore-out/.) cp -a "$(cat "$state/minio_src")/." "$2/";;
      *) printf '%s' "${1%/.}" > "$state/minio_src";;
    esac;;
  run)
    case " $* " in
      *" --entrypoint python "*)
        # install-radicale.sh: the password arrives on STDIN only; a bcrypt-shaped line out.
        pw=$(cat)
        [ -n "$pw" ] || exit 1
        printf '$2b$12$%s\n' "FAKEbcryptSALTandHASHfortheTESTonlyNOTREALxxxxxxxxxxx";;
      *)
        for a; do
          case "$a" in *:/restore:ro) printf '%s' "${a%:/restore:ro}" > "$state/minio_src";; esac
        done
        echo fake-container-id;;
    esac;;
  image) [ -f "$state/no-image" ] && exit 1; exit 0;;
  build) : > "$state/built";;
  inspect)
    for last; do :; done
    case "$last" in
      *radicale*) [ -f "$state/radicale-container" ] || exit 1; echo radicale;;
      *) echo fake/image:1;;
    esac;;
  stop|start)
    # Which folder was live at that moment: production's (later.ics) or the snapshot's.
    n=$(find "${FAKE_DATA}/radicale" -name later.ics 2>/dev/null | wc -l | tr -d ' ')
    echo "$cmd $1 later=$n" >> "$state/lifecycle.log";;
  *) :;;
esac
"""

FAKE_RESTIC = r"""#!/usr/bin/env bash
repo=${RESTIC_REPOSITORY:?}
case "$1" in
  cat) [ -f "$repo/config" ];;
  init) mkdir -p "$repo"; : > "$repo/config";;
  backup)
    for staging; do :; done
    rm -rf "$repo/snap"; mkdir -p "$repo/snap"; cp -a "$staging/." "$repo/snap/"
    echo '{"message_type":"summary","snapshot_id":"'"$FAKE_SNAPSHOT"'"}';;
  snapshots) echo '[{"id":"'"$FAKE_SNAPSHOT"'"}]';;
  restore)
    while [ $# -gt 0 ]; do [ "$1" = "--target" ] && target=$2; shift; done
    mkdir -p "$target"; cp -a "$repo/snap/." "$target/";;
  *) :;;
esac
"""

FAKE_FLOCK = r"""#!/usr/bin/env bash
all="$*"
while [ $# -gt 0 ]; do case "$1" in -w) shift 2;; -*) shift;; *) break;; esac; done
# A lock taken on a file descriptor is logged with the file it was opened on.
echo "lock $1 $(readlink "/proc/$$/fd/$1" 2>/dev/null) [$all]" >> "$FAKE_STATE/flock.log"
shift
[ $# -gt 0 ] && exec "$@"
exit 0
"""

FAKE_CHOWN = r"""#!/usr/bin/env bash
echo "chown $*" >> "$FAKE_STATE/posture.log"
"""

FAKE_CHMOD = r"""#!/usr/bin/env bash
echo "chmod $*" >> "$FAKE_STATE/posture.log"
exec /usr/bin/chmod "$@"
"""

PRE_RESTORE_BACKUP = r"""#!/usr/bin/env bash
echo '{"snapshot":"__PRE__"}' > "$PAGENTOS_BACKUP_ROOT/LAST_BACKUP.json"
echo "BACKUP OK: snapshot __PRE__ (manual)"
"""

RECONCILE = "#!/usr/bin/env bash\necho reconciled\n"


def _posix(path: Path | str) -> str:
    text = str(path)
    if os.name != "nt":
        return text
    drive, rest = os.path.splitdrive(text)
    return "/" + drive.rstrip(":").lower() + rest.replace("\\", "/")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


class Sandbox:
    def __init__(self, root: Path) -> None:
        assert BASH.exists(), f"Git Bash is required for this proof and is not at {BASH}"
        self.root = root
        self.base = root / "opt"
        self.data = root / "data"
        self.backup = root / "backup"
        self.state = root / "state"
        bin_dir = root / "bin"
        for directory in (self.base, self.data, self.backup, self.state, root / "systemd"):
            directory.mkdir(parents=True, exist_ok=True)
        _write(self.base / ".env", "PAGENTOS_DATABASE_URL=x\n")
        _write(self.base / "RELEASE", "0123456789abcdef0123456789abcdef01234567\n")
        _write(self.base / "backup.password", "restic-test-password\n")
        self.fakes = {
            "PAGENTOS_DOCKER": _write(bin_dir / "docker", FAKE_DOCKER),
            "PAGENTOS_RESTIC": _write(bin_dir / "restic", FAKE_RESTIC),
            "PAGENTOS_FLOCK": _write(bin_dir / "flock", FAKE_FLOCK),
            "PAGENTOS_CHOWN": _write(bin_dir / "chown", FAKE_CHOWN),
            "PAGENTOS_CHMOD": _write(bin_dir / "chmod", FAKE_CHMOD),
            "PAGENTOS_BACKUP_SCRIPT": _write(
                bin_dir / "pre-restore-backup", PRE_RESTORE_BACKUP.replace("__PRE__", PRE_RESTORE)
            ),
            "PAGENTOS_RECONCILE_SCRIPT": _write(bin_dir / "reconcile", RECONCILE),
        }

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update({name: _posix(path) for name, path in self.fakes.items()})
        env.update(
            {
                "PAGENTOS_ALLOW_NONROOT": "1",
                "PAGENTOS_BASE": _posix(self.base),
                "PAGENTOS_DATA": _posix(self.data),
                "PAGENTOS_BACKUP_ROOT": _posix(self.backup),
                "PAGENTOS_RECOVERY_ROOT": _posix(self.root / "no-recovery"),
                "PAGENTOS_SYSTEMD_DIR": _posix(self.root / "systemd"),
                "PAGENTOS_PYTHON": _posix(sys.executable),
                "PAGENTOS_BACKUP_OFFHOST_ENV": _posix(self.root / "no-offhost.env"),
                "PAGENTOS_DRILL_READY_TRIES": "2",
                "PAGENTOS_RESTORE_LOCK_WAIT_S": "1",
                "RESTIC_REPOSITORY": _posix(self.root / "repo"),
                "RESTIC_PASSWORD_FILE": _posix(self.base / "backup.password"),
                "FAKE_STATE": _posix(self.state),
                "FAKE_DATA": _posix(self.data),
                "FAKE_SNAPSHOT": SNAPSHOT,
            }
        )
        # Any value of this switches Git Bash's path conversion OFF, and the native python
        # would then be handed /e/... paths it cannot open.
        env.pop("MSYS_NO_PATHCONV", None)
        return env

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(BASH), _posix(CLOUD / script), *args],
            env=self.env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        result.output = result.stdout + result.stderr  # type: ignore[attr-defined]
        return result

    def calendar(self, items: int = 3) -> Path:
        """A Radicale data folder as Radicale lays it out - including its CACHE, which also
        names its files `<href>.ics` and must not be counted as calendar items."""
        collection = self.data / "radicale" / "collection-root" / "owner" / "takvim"
        for n in range(1, items + 1):
            _write(collection / f"e{n}.ics", f"BEGIN:VCALENDAR\nUID:e{n}\nEND:VCALENDAR\n")
            _write(collection / ".Radicale.cache" / "item" / f"e{n}.ics", "cache\n")
            _write(collection / ".Radicale.cache" / "history" / f"e{n}.ics", "history\n")
        _write(collection / ".Radicale.props", '{"tag": "VCALENDAR"}')
        _write(self.data / "radicale" / ".Radicale.lock", "")
        _write(self.data / "radicale-auth" / "users", "owner:$2b$12$fakebcryptline\n")
        return collection

    def snapshot(self) -> Path:
        return self.root / "repo" / "snap"

    def log(self, name: str) -> str:
        path = self.state / name
        return path.read_text("utf-8") if path.exists() else ""


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


def _last_backup(box: Sandbox) -> dict:
    return json.loads((box.backup / "LAST_BACKUP.json").read_text("utf-8"))


# ---- backup ----------------------------------------------------------------------------------


def test_the_backup_takes_the_calendar_and_its_users_file(sandbox: Sandbox) -> None:
    sandbox.calendar(3)

    result = sandbox.run("backup-cloud-core.sh")

    assert result.returncode == 0, result.output
    assert "BACKUP OK" in result.stdout
    snap = sandbox.snapshot()
    assert (snap / "radicale" / "collection-root" / "owner" / "takvim" / "e3.ics").is_file()
    assert (snap / "config" / "pagentos-data" / "radicale-auth" / "users").is_file()
    manifest = json.loads((snap / "MANIFEST.json").read_text("utf-8"))
    assert "radicale/collection-root/owner/takvim/e1.ics" in manifest["files"]
    assert "config/pagentos-data/radicale-auth/users" in manifest["files"]
    record = _last_backup(sandbox)
    assert record["radicale_items"] == 3, "the cache's .ics files are not calendar items"
    assert record["radicale"] == "ok"
    assert "radicale: 3 item(s)" in result.stdout


def test_the_backup_copies_under_radicale_s_own_lock(sandbox: Sandbox) -> None:
    """Radicale's writers take /data/.Radicale.lock exclusively; the copy holds it shared, so
    no write lands half-way through the copy."""
    sandbox.calendar(1)

    result = sandbox.run("backup-cloud-core.sh")

    assert result.returncode == 0, result.output
    held = [
        line for line in sandbox.log("flock.log").splitlines() if "radicale/.Radicale.lock" in line
    ]
    assert held, sandbox.log("flock.log")
    assert "[-s " in held[0], f"the copy must hold Radicale's lock SHARED: {held[0]}"


def test_a_night_before_radicale_is_installed_is_still_green(sandbox: Sandbox) -> None:
    result = sandbox.run("backup-cloud-core.sh")

    assert result.returncode == 0, result.output
    assert "radicale: not installed" in result.stdout
    record = _last_backup(sandbox)
    assert record["radicale_items"] == 0
    assert record["radicale"] == "not installed"
    assert not (sandbox.snapshot() / "radicale").exists()


# ---- drill and restore -------------------------------------------------------------------------


def test_the_drill_counts_and_verifies_the_calendar(sandbox: Sandbox) -> None:
    sandbox.calendar(3)
    assert sandbox.run("backup-cloud-core.sh").returncode == 0

    result = sandbox.run("restore-cloud-core.sh", "--drill")

    assert result.returncode == 0, result.output
    reports = list((sandbox.backup / "drills").glob("*-drill.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text("utf-8"))["radicale_items"] == 3


def test_the_drill_fails_when_a_calendar_file_differs_from_the_manifest(sandbox: Sandbox) -> None:
    collection = sandbox.calendar(3)
    assert sandbox.run("backup-cloud-core.sh").returncode == 0
    tampered = sandbox.snapshot() / collection.relative_to(sandbox.data) / "e2.ics"
    tampered.write_bytes(b"BEGIN:VCALENDAR\nUID:changed\nEND:VCALENDAR\n")

    result = sandbox.run("restore-cloud-core.sh", "--drill")

    assert result.returncode == 97, result.output
    assert "radicale/collection-root/owner/takvim/e2.ics" in result.output


def test_apply_stops_radicale_swaps_the_folder_and_starts_it(sandbox: Sandbox) -> None:
    collection = sandbox.calendar(3)
    assert sandbox.run("backup-cloud-core.sh").returncode == 0
    # Production moves on after the snapshot: one event deleted, one added.
    (collection / "e1.ics").unlink()
    _write(collection / "later.ics", "BEGIN:VCALENDAR\nUID:later\nEND:VCALENDAR\n")
    _write(sandbox.state / "radicale-container", "")

    result = sandbox.run(
        "restore-cloud-core.sh",
        "--apply",
        "--snapshot",
        SNAPSHOT,
        "--confirm",
        f"RESTORE {SNAPSHOT} OVER PRODUCTION",
    )

    assert result.returncode == 0, result.output
    assert (collection / "e1.ics").is_file(), "the snapshot's calendar is back"
    assert not (collection / "later.ics").exists()
    pre = sandbox.data / "radicale.pre-restore" / collection.relative_to(sandbox.data / "radicale")
    assert (pre / "later.ics").is_file(), "the replaced folder is kept beside it"
    lifecycle = sandbox.log("lifecycle.log").splitlines()
    radicale = [line for line in lifecycle if "pagentos-prod-radicale" in line]
    # Stopped while production's folder (with later.ics) was still live; started only once
    # the snapshot's folder (without it) was in place.
    assert [line.split()[0] for line in radicale] == ["stop", "start"], lifecycle
    assert radicale[0].endswith("later=1") and radicale[1].endswith("later=0"), radicale
    assert "chown -R 10002:10002" in sandbox.log("posture.log")
    reports = list((sandbox.backup / "drills").glob("*-apply.json"))
    assert json.loads(reports[0].read_text("utf-8"))["radicale_items"] == 3


def test_apply_leaves_the_calendar_alone_when_radicale_is_not_wired(sandbox: Sandbox) -> None:
    collection = sandbox.calendar(3)
    assert sandbox.run("backup-cloud-core.sh").returncode == 0
    _write(collection / "later.ics", "x")

    result = sandbox.run(
        "restore-cloud-core.sh",
        "--apply",
        "--snapshot",
        SNAPSHOT,
        "--confirm",
        f"RESTORE {SNAPSHOT} OVER PRODUCTION",
    )

    assert result.returncode == 0, result.output
    assert "no pagentos-prod-radicale container" in result.stdout
    assert (collection / "later.ics").exists()
    assert not (sandbox.data / "radicale.pre-restore").exists()
    assert "pagentos-prod-radicale" not in sandbox.log("lifecycle.log")


# ---- install-radicale.sh -------------------------------------------------------------------


def test_install_refuses_without_the_password_and_never_prints_one(sandbox: Sandbox) -> None:
    result = sandbox.run("install-radicale.sh")

    assert result.returncode == 2, result.output
    assert "set-cloud-secret.ps1" in result.output
    assert not (sandbox.data / "radicale-auth" / "users").exists()


def test_install_writes_a_bcrypt_users_file_from_stdin(sandbox: Sandbox) -> None:
    _write(sandbox.base / ".env", f"PAGENTOS_DATABASE_URL=x\nPAGENTOS_CALDAV_PASSWORD={PASSWORD}\n")

    result = sandbox.run("install-radicale.sh")

    assert result.returncode == 0, result.output
    users = (sandbox.data / "radicale-auth" / "users").read_text("utf-8")
    user, _, digest = users.strip().partition(":")
    assert user == "owner" and digest.startswith("$2"), users
    for where, text in {
        "output": result.output,
        "users file": users,
        "docker argv": sandbox.log("docker.log"),
        "posture log": sandbox.log("posture.log"),
    }.items():
        assert PASSWORD not in text, f"the password reached the {where}"
    assert "--network none" in sandbox.log("docker.log"), "the hash is made without a network"
    posture = sandbox.log("posture.log")
    assert "chmod 0600" in posture and "radicale-auth/users" in posture
    assert "chmod 0700" in posture
    assert "chown 10002:10002" in posture
    assert (sandbox.data / "radicale").is_dir()


def test_install_refuses_a_password_bcrypt_would_truncate(sandbox: Sandbox) -> None:
    long_password = "x" * 73
    _write(sandbox.base / ".env", f"PAGENTOS_CALDAV_PASSWORD={long_password}\n")

    result = sandbox.run("install-radicale.sh")

    assert result.returncode == 70, result.output
    assert long_password not in result.output
    assert not (sandbox.data / "radicale-auth" / "users").exists()


def test_install_builds_the_image_when_it_is_not_there(sandbox: Sandbox) -> None:
    _write(sandbox.base / ".env", f"PAGENTOS_CALDAV_PASSWORD={PASSWORD}\n")
    _write(sandbox.state / "no-image", "")

    result = sandbox.run("install-radicale.sh")

    assert result.returncode == 0, result.output
    build = [line for line in sandbox.log("docker.log").splitlines() if line.startswith("build")]
    assert build and build[0].endswith("infra/docker/radicale"), build


# ---- the wiring card's secret order (ADR "BAĞLAMA KARTININ TAM METNİ") -----------------------
#
# install-env-secret.sh (the host half of set-cloud-secret.ps1) refuses with 67 a tree whose
# compose does not wire the name, and the wired compose's `${PAGENTOS_CALDAV_PASSWORD:?}` refuses
# every compose command while .env lacks it. The ADR names the way out: stage the wired tree,
# install the secret against THAT tree, then release. This runs the ADR's own command line
# through the real install-env-secret.sh (the contract halves read each other).

ADR = REPO / "team" / "plans" / "radicale-stack-ops-adr.md"
SET_SECRET_LINE = r".\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_CALDAV_PASSWORD"

FAKE_COMPOSE_DOCKER = r"""#!/usr/bin/env bash
# `docker compose -f FILE --env-file ENV config [-q]` as compose answers it: a `${NAME:?...}`
# with no value in ENV fails; without -q the (here: raw) definition is printed.
case "$1" in
  compose)
    shift; file=""; envf=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -f) file=$2; shift 2;;
        --env-file) envf=$2; shift 2;;
        --profile) shift 2;;
        *) break;;
      esac
    done
    [ "$1" = config ] || exit 0
    for name in $(grep -o '\${[A-Z_]*:?' "$file" | sed 's/^\${//; s/:?$//'); do
      grep -q "^$name=." "$envf" || { echo "required variable $name has no value" >&2; exit 15; }
    done
    [ "${2:-}" = -q ] || cat "$file";;
  ps) case "$*" in *pagentos-prod-api-green*) echo pagentos-prod-api-green;; esac;;
  *) exit 0;;
esac
"""


def _adr_secret_command() -> str:
    lines = [
        line.strip()
        for line in ADR.read_text("utf-8").splitlines()
        if line.strip().startswith(SET_SECRET_LINE)
    ]
    assert len(lines) == 1, f"the ADR must name one set-cloud-secret line for it: {lines}"
    return lines[0]


def _adr_wired_password_line() -> str:
    lines = [
        line.strip()
        for line in ADR.read_text("utf-8").splitlines()
        if line.strip().startswith("PAGENTOS_CALDAV_PASSWORD: ${PAGENTOS_CALDAV_PASSWORD:?")
    ]
    assert len(lines) == 1, lines
    return lines[0]


def _install_secret(
    box: Sandbox, repo_root: str, value: str, command: str
) -> subprocess.CompletedProcess[str]:
    """What New-RemoteSecretInstallCommand sends for this command line, run on the sandbox."""
    fake_bin = box.root / "secret-bin"
    _write(fake_bin / "docker", FAKE_COMPOSE_DOCKER)
    skip_verify = "-SkipVerify" in command
    expect = "" if skip_verify else "openai-realtime"
    recreate = "0" if "-SkipRestart" in command else "1"
    host_repo = _posix(box.base) + repo_root.removeprefix("/opt/pagentos")
    env = box.env()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["PAGENTOS_ALLOW_NONROOT_ENV"] = "1"
    result = subprocess.run(
        [
            str(BASH),
            f"{host_repo}/scripts/cloud/install-env-secret.sh",
            "PAGENTOS_CALDAV_PASSWORD",
            _posix(box.base / ".env"),
            host_repo,
            expect,
            recreate,
            "",
        ],
        env=env,
        input=value + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    result.output = result.stdout + result.stderr  # type: ignore[attr-defined]
    return result


def _host_trees(box: Sandbox) -> None:
    """app = the tree serving today (no radicale wiring); app.next = the wiring card's tree,
    staged by `release-cloud-core.ps1 -BlueGreen -StageOnly`."""
    prod = (REPO / "infra" / "docker" / "docker-compose.prod.yml").read_text("utf-8")
    script = (CLOUD / "install-env-secret.sh").read_text("utf-8")
    # The host's .env already carries every secret the serving compose requires.
    required = sorted(set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):\?", prod)))
    _write(box.base / ".env", "".join(f"{name}=present\n" for name in required))
    for tree, compose in (
        ("app", prod),
        ("app.next", prod + "\n# wiring card\n      " + _adr_wired_password_line() + "\n"),
    ):
        _write(box.base / tree / "infra" / "docker" / "docker-compose.prod.yml", compose)
        _write(box.base / tree / "scripts" / "cloud" / "install-env-secret.sh", script)


def test_the_adr_s_secret_order_installs_through_the_staged_tree(sandbox: Sandbox) -> None:
    command = _adr_secret_command()
    assert '-ExpectProvider ""' in command and "-SkipVerify" in command, command
    match = re.search(r"-HostRepoRoot (\S+)", command)
    assert match, f"the ADR's command must name the staged tree: {command}"
    _host_trees(sandbox)

    # The default tree (the one serving) refuses: 67, "release first".
    unstaged = command.replace(match.group(0), "")
    serving = _install_secret(sandbox, "/opt/pagentos/app", PASSWORD, unstaged)
    assert serving.returncode == 67, serving.output
    # ...but the value was already written before the wiring check (the ADR says so; the
    # owner must not read 67 as "nothing changed").
    assert f"PAGENTOS_CALDAV_PASSWORD={PASSWORD}\n" in (sandbox.base / ".env").read_text("utf-8")
    _host_trees(sandbox)  # back to a .env without the password

    # The ADR's line: against the staged, wired tree it is installed and compose is valid;
    # 73 is install-env-secret's "installed; finish with release -BlueGreen -Force".
    staged = _install_secret(sandbox, match.group(1), PASSWORD, command)
    assert staged.returncode == 73, staged.output
    assert "PAGENTOS_CALDAV_PASSWORD is wired" in staged.output
    assert f"PAGENTOS_CALDAV_PASSWORD={PASSWORD}\n" in (sandbox.base / ".env").read_text("utf-8")
    assert PASSWORD not in serving.output + staged.output


def test_the_adr_names_the_characters_the_env_file_refuses(sandbox: Sandbox) -> None:
    """A '$' (or space, #, quotes, backslash) is refused with 65 and nothing is written - the
    ADR tells the owner so before he chooses the password."""
    _host_trees(sandbox)
    command = _adr_secret_command()
    match = re.search(r"-HostRepoRoot (\S+)", command)
    assert match, command

    refused = _install_secret(sandbox, match.group(1), "Takvim$Parola", command)

    assert refused.returncode == 65, refused.output
    assert "PAGENTOS_CALDAV_PASSWORD" not in (sandbox.base / ".env").read_text("utf-8")
    text = ADR.read_text("utf-8")
    assert "65" in text and "A-Za-z0-9" in text, "the ADR must give the owner the character rule"


def test_the_sandbox_is_not_the_host() -> None:
    """The fakes are found by name: if a default ever won over the environment, these tests
    would run the real docker against the real machine."""
    for script in ("backup-cloud-core.sh", "restore-cloud-core.sh", "install-radicale.sh"):
        text = (CLOUD / script).read_text("utf-8")
        assert "docker_bin=${PAGENTOS_DOCKER:-docker}" in text, script
