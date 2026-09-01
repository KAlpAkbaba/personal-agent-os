"""Headless reference mobile client (M9).

**Read this first.** There is no native mobile application in this repository,
and none can be built on the owner's current machine: no Android SDK, no Xcode,
no device. Rather than claim a gate that cannot be run, M9 ships the *contract*
a native client talks to and this client, which exercises that contract over
real HTTP in exactly the order and with exactly the constraints a native app
would. **In the M9 acceptance gate this program stands in for the native app.**
It is honest about the substitution: what it proves is that the server side of
"authenticated native client connects", "push notification for artifact ready",
"audio narration resumes from cloud cursor", "file share/export works" and
"device revocation invalidates session" is correct and complete. It proves
nothing about Apple's or Google's delivery infrastructure, about microphone
behaviour on a real handset, or about app-store distribution — see the owner
action list in the M9 return.

What it does, in the order a native app does it:

1. **Bootstrap-less sign-in.** It never mints an owner credential; it is handed
   one (an owner action, done once) and exchanges it for an opaque bearer
   session with ``client_kind="mobile"``, optionally bound to an enrolled
   device so that revoking the device revokes this session.
2. **Register for push**, binding a provider token to that session. The token
   is stored server-side only as a hash.
3. **Receive the artifact-ready notification** by polling
   ``GET /v1/mobile/notifications`` — the deterministic stand-in for an OS push
   (see the endpoint's own docstring).
4. **Fetch the executive summary WITHOUT the full body.** The client asserts the
   response carries no ``canonical_body``: constitution §3, a completed task
   notifies briefly and waits.
5. **Resume narration from the cloud cursor** — it reads the cursor the cloud
   holds and continues from there, never from a local position.
6. **Download a render** through the share/export endpoint and write it to disk.
7. **Handle revocation.** Any 401 is treated as "the session is gone": with
   ``--reauth`` it signs in again and continues; otherwise it exits cleanly with
   a distinct status code instead of retrying into a wall.

Dependencies: the standard library plus httpx (already a dependency of the API
service). No mobile SDK, no notification daemon, no audio.

Run:

    python -m clients.reference.mobile_client --help
    python -m clients.reference.mobile_client flow \\
        --base-url http://127.0.0.1:8001 --credential pagentos_ok_... \\
        --artifact-id <uuid> --download-dir ./out
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised implicitly; the API service pins httpx
    import httpx
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "the reference mobile client needs httpx: uv add httpx / pip install httpx"
    ) from exc

DEFAULT_BASE_URL = "http://127.0.0.1:8001"
ENV_CREDENTIAL = "PAGENTOS_OWNER_CREDENTIAL"  # noqa: S105 - env var name, not a secret

#: Process exit codes, so a script wrapping this client can branch.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_SESSION_REVOKED = 3
EXIT_CONTRACT_FAILED = 4


class SessionRevoked(RuntimeError):
    """The API answered 401: this session no longer exists."""


class ContractFailed(RuntimeError):
    """The server did something the mobile contract forbids."""


@dataclass
class FlowReport:
    """Machine-readable trace of a run, so the gate asserts on data not stdout."""

    steps: list[dict[str, Any]] = field(default_factory=list)

    def record(self, step: str, **detail: Any) -> None:
        self.steps.append({"step": step, **detail})

    def get(self, step: str) -> dict[str, Any] | None:
        for entry in self.steps:
            if entry["step"] == step:
                return entry
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"steps": self.steps}


class MobileReferenceClient:
    """The mobile contract, implemented once.

    `http` is a transport seam: pass a pre-built ``httpx.Client`` (the
    integration gate passes a client bound to the ASGI app) or leave it None and
    the client opens its own connection to ``base_url``.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        credential: str | None = None,
        http: httpx.Client | None = None,
        label: str = "reference-mobile-client",
        device_id: uuid.UUID | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential = credential
        self.label = label
        self.device_id = device_id
        self._owns_http = http is None
        self._http = http or httpx.Client(base_url=self.base_url, timeout=timeout_s)
        self.token: str | None = None
        self.session_id: str | None = None
        self.registration_id: str | None = None

    # ------------------------------------------------------------- lifecycle

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> MobileReferenceClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --------------------------------------------------------------- request

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expect: tuple[int, ...] = (200,),
        raw: bool = False,
    ) -> Any:
        response = self._http.request(
            method, path, json=json_body, params=params, headers=self._headers()
        )
        if response.status_code == 401:
            # One coarse meaning, exactly as ADR-0027 intends: re-authenticate
            # or stop. The client never tries to distinguish expired from
            # revoked; the server deliberately does not say.
            self.token = None
            raise SessionRevoked(f"{method} {path} -> 401")
        if response.status_code not in expect:
            raise ContractFailed(
                f"{method} {path} -> {response.status_code} "
                f"(expected {expect}): {response.text[:400]}"
            )
        return response if raw else response.json()

    # ------------------------------------------------------------- 1. sign in

    def sign_in(self) -> dict[str, Any]:
        """Exchange the owner credential for a mobile session.

        Bootstrap-less on purpose: minting the owner credential is a one-time
        owner action on the host. A client that could create owner authority
        would be a client that could be stolen into owner authority.
        """
        if not self.credential:
            raise ContractFailed("no owner credential provided")
        body: dict[str, Any] = {
            "owner_credential": self.credential,
            "client_kind": "mobile",
            "label": self.label,
        }
        if self.device_id is not None:
            # Bound to an enrolled device, so revoking the device revokes this
            # session (M9 acceptance).
            body["device_id"] = str(self.device_id)
        response = self._http.post("/v1/identity/sessions", json=body)
        if response.status_code in (401, 429):
            raise SessionRevoked(f"credential exchange refused: {response.status_code}")
        if response.status_code != 201:
            raise ContractFailed(
                f"POST /v1/identity/sessions -> {response.status_code}: {response.text[:400]}"
            )
        payload = response.json()
        self.token = payload["token"]
        self.session_id = payload["session_id"]
        return payload

    def whoami(self) -> dict[str, Any]:
        return self._request("GET", "/v1/identity/sessions/current")

    def sign_out(self) -> dict[str, Any]:
        result = self._request("DELETE", "/v1/identity/sessions/current")
        self.token = None
        return result

    # -------------------------------------------------------- 2. push register

    def register_push(
        self,
        *,
        provider: str = "fake",
        token: str | None = None,
        platform: str = "headless",
        locale: str = "tr-TR",
    ) -> dict[str, Any]:
        """Bind a push token to THIS session.

        A real client hands over the token the OS gave it. Here the token is
        generated locally: the server stores only its hash either way, so what
        is exercised is identical.
        """
        push_token = token or f"reference-{uuid.uuid4().hex}"
        payload = self._request(
            "POST",
            "/v1/mobile/push/registrations",
            json_body={
                "provider": provider,
                "token": push_token,
                "platform": platform,
                "locale": locale,
            },
            expect=(201,),
        )
        self.registration_id = payload["registration_id"]
        return payload

    def list_push_registrations(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/mobile/push/registrations")["registrations"]

    def unregister_push(self, registration_id: str | None = None) -> dict[str, Any]:
        target = registration_id or self.registration_id
        if target is None:
            raise ContractFailed("no registration to unregister")
        return self._request("DELETE", f"/v1/mobile/push/registrations/{target}")

    # --------------------------------------------------- 3. artifact-ready push

    def notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._request(
            "GET", "/v1/mobile/notifications", params={"limit": limit}
        )["notifications"]

    def wait_for_artifact_ready(
        self,
        *,
        artifact_id: str | None = None,
        timeout_s: float = 20.0,
        interval_s: float = 0.25,
        sleep=time.sleep,
        now=time.monotonic,
    ) -> dict[str, Any]:
        """Poll until an artifact-ready notification arrives (or give up).

        A native client would be woken by the OS instead of polling. The wait is
        the honest local equivalent, and the timeout is what keeps a missing
        notification a *failure* rather than a hang.
        """
        deadline = now() + timeout_s
        while True:
            for item in self.notifications():
                data = item.get("data") or {}
                if data.get("kind") != "artifact_ready":
                    continue
                if artifact_id is None or data.get("artifact_id") == artifact_id:
                    return item
            if now() >= deadline:
                raise ContractFailed(
                    f"no artifact_ready notification within {timeout_s}s"
                    + (f" for artifact {artifact_id}" if artifact_id else "")
                )
            sleep(interval_s)

    # ------------------------------------------------- 4. summary, not the body

    def executive_summary(self, artifact_id: str) -> dict[str, Any]:
        """Fetch metadata + the executive summary and PROVE the body is absent.

        This is the constitution's "a completed task must not automatically
        force a long result onto the owner" expressed as a client-side
        assertion: if a future change started returning the report body by
        default, this client fails rather than quietly showing the owner a wall
        of text.
        """
        payload = self._request("GET", f"/v1/artifacts/{artifact_id}")
        if "canonical_body" in payload:
            raise ContractFailed(
                "GET /v1/artifacts/{id} returned the full body without ?include=body"
            )
        return payload

    def full_body(self, artifact_id: str) -> str:
        """Only ever called when the owner explicitly asks for the whole report."""
        payload = self._request(
            "GET", f"/v1/artifacts/{artifact_id}", params={"include": "body"}
        )
        return payload.get("canonical_body") or ""

    # ------------------------------------------------ 5. narration cursor resume

    def start_narration(
        self, artifact_id: str, *, version: int = 1, device_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"artifact_id": artifact_id, "artifact_version": version}
        target = device_id or self.device_id
        if target is not None:
            body["device_id"] = str(target)
        return self._request(
            "POST", "/v1/narration/sessions", json_body=body, expect=(201,)
        )

    def read_cursor(self, narration_session_id: str) -> dict[str, Any]:
        """The cloud cursor. A resuming client reads this and never guesses."""
        return self._request(
            "GET", f"/v1/narration/sessions/{narration_session_id}/cursor"
        )

    def resume_narration(self, narration_session_id: str) -> dict[str, Any]:
        """Resume from wherever the cloud says the owner stopped.

        `devam` is the M4 command, and the point of routing the resume through
        it rather than through a local seek is that the position is the cloud's:
        another device may have moved it minutes ago.
        """
        before = self.read_cursor(narration_session_id)
        resumed = self._request(
            "POST",
            f"/v1/narration/sessions/{narration_session_id}/command",
            json_body={"utterance": "devam et"},
        )
        return {"resumed_from": before["cursor"], "state": resumed["state"], **resumed}

    def checkpoint_cursor(
        self,
        narration_session_id: str,
        cursor: dict[str, Any],
        *,
        playback_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Flush a lifecycle checkpoint (background / interruption / terminate)."""
        body: dict[str, Any] = {"cursor": cursor}
        if playback_seconds is not None:
            body["playback_seconds"] = playback_seconds
        if self.device_id is not None:
            body["device_id"] = str(self.device_id)
        return self._request(
            "PATCH", f"/v1/narration/sessions/{narration_session_id}/cursor", json_body=body
        )

    # ------------------------------------------------------ 6. share / download

    def share_index(self, artifact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/mobile/share/{artifact_id}")

    def download_render(
        self, artifact_id: str, fmt: str = "pdf", *, dest_dir: Path | None = None
    ) -> dict[str, Any]:
        """Download a render for the OS share sheet, honouring the filename."""
        response = self._request(
            "GET", f"/v1/mobile/share/{artifact_id}/{fmt}", raw=True
        )
        disposition = response.headers.get("content-disposition", "")
        filename = _filename_from_disposition(disposition) or f"{artifact_id}.{fmt}"
        written: str | None = None
        if dest_dir is not None:
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = dest_dir / filename
            path.write_bytes(response.content)
            written = str(path)
        return {
            "artifact_id": artifact_id,
            "format": fmt,
            "filename": filename,
            "mime_type": response.headers.get("content-type", ""),
            "size_bytes": len(response.content),
            "content_hash": response.headers.get("x-content-hash", ""),
            "written_to": written,
        }

    # ------------------------------------------------------------- 7. the flow

    def run_flow(
        self,
        *,
        artifact_id: str | None = None,
        push_provider: str = "fake",
        push_token: str | None = None,
        platform: str = "headless",
        locale: str = "tr-TR",
        share_format: str = "pdf",
        download_dir: Path | None = None,
        wait_timeout_s: float = 20.0,
        wait_interval_s: float = 0.25,
        reauth: bool = False,
        expect_notification: bool = True,
        reuse_session: bool = False,
        report: FlowReport | None = None,
    ) -> FlowReport:
        """The whole mobile contract, once, in order.

        `reuse_session=True` continues with the session this client already
        holds instead of exchanging the credential again. That is what a native
        app does on every launch after the first: the owner credential is typed
        once, the session token is what the app keeps.
        """
        report = report or FlowReport()

        def step(name: str, fn):
            try:
                return fn()
            except SessionRevoked:
                if not reauth:
                    report.record(name, ok=False, session_revoked=True)
                    raise
                report.record("reauthenticated", after=name)
                self.sign_in()
                return fn()

        if reuse_session and self.token:
            report.record("session_reused", session_id=self.session_id)
        else:
            session = self.sign_in()
            report.record(
                "signed_in",
                session_id=session["session_id"],
                client_kind=session["client_kind"],
                device_bound=session["device_id"] is not None,
                expires_at=session["expires_at"],
            )

        who = step("whoami", self.whoami)
        report.record("connected", session_id=who["session_id"], client_kind=who["client_kind"])

        registration = step(
            "register_push",
            lambda: self.register_push(
                provider=push_provider, token=push_token, platform=platform, locale=locale
            ),
        )
        report.record(
            "push_registered",
            registration_id=registration["registration_id"],
            provider=registration["provider"],
            status=registration["status"],
        )

        if expect_notification:
            notification = step(
                "await_notification",
                lambda: self.wait_for_artifact_ready(
                    artifact_id=artifact_id,
                    timeout_s=wait_timeout_s,
                    interval_s=wait_interval_s,
                ),
            )
            resolved = (notification.get("data") or {}).get("artifact_id")
            artifact_id = artifact_id or resolved
            report.record(
                "notified",
                artifact_id=resolved,
                title=notification.get("title"),
                body=notification.get("body"),
            )

        if artifact_id is None:
            report.record("finished", ok=True, note="no artifact requested")
            return report

        summary = step("summary", lambda: self.executive_summary(artifact_id))
        report.record(
            "summary_fetched",
            artifact_id=artifact_id,
            state=summary["state"],
            summary_chars=len(summary.get("executive_summary") or ""),
            body_withheld=True,
        )

        narration = step("narration", lambda: self.start_narration(artifact_id))
        narration_id = narration["session_id"]
        resumed = step("resume", lambda: self.resume_narration(narration_id))
        report.record(
            "narration_resumed",
            narration_session_id=narration_id,
            resumed_from=resumed["resumed_from"],
            state=resumed["state"],
        )

        index = step("share_index", lambda: self.share_index(artifact_id))
        formats = [r["format"] for r in index["renders"]]
        chosen = share_format if share_format in formats else (formats[0] if formats else None)
        if chosen is None:
            raise ContractFailed(f"artifact {artifact_id} has no renders to share")
        download = step(
            "download", lambda: self.download_render(artifact_id, chosen, dest_dir=download_dir)
        )
        report.record("downloaded", **download)

        report.record("finished", ok=True)
        return report


def _filename_from_disposition(disposition: str) -> str | None:
    """Prefer RFC 5987 `filename*` (the real Turkish title) over `filename`."""
    from urllib.parse import unquote

    ascii_name: str | None = None
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename*="):
            value = part.split("=", 1)[1]
            if "''" in value:
                value = value.split("''", 1)[1]
            return unquote(value).strip('"')
        if part.lower().startswith("filename="):
            ascii_name = part.split("=", 1)[1].strip('"')
    return ascii_name


# ------------------------------------------------------------------------ CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m clients.reference.mobile_client",
        description=(
            "Headless reference client for the Personal Agent OS mobile contract. "
            "Stands in for the native app in the M9 gate."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The owner credential is a one-time owner action; this client never "
            "mints one. Pass --credential or set " + ENV_CREDENTIAL + "."
        ),
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument(
        "--credential",
        default=os.environ.get(ENV_CREDENTIAL, ""),
        help=f"owner credential (default: ${ENV_CREDENTIAL})",
    )
    parser.add_argument("--label", default="reference-mobile-client")
    parser.add_argument(
        "--device-id",
        default=None,
        help="bind the session to this enrolled device, so revoking it revokes the session",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout, seconds")
    parser.add_argument("--json", action="store_true", help="emit the run report as JSON")

    # `flow` is the default command, so a bare invocation must already carry
    # every option the flow reads; a chosen subparser overrides these.
    parser.set_defaults(
        command=None,
        artifact_id=None,
        push_provider="fake",
        push_token=None,
        platform="headless",
        locale="tr-TR",
        share_format="pdf",
        download_dir=None,
        wait_timeout=20.0,
        wait_interval=0.25,
        reauth=False,
        reuse_session=False,
        no_wait=False,
        narration_session_id=None,
        limit=50,
        wait=False,
    )

    sub = parser.add_subparsers(dest="command")

    flow = sub.add_parser("flow", help="run the whole mobile contract end to end (default)")
    flow.add_argument("--artifact-id", default=None)
    flow.add_argument("--push-provider", default="fake", choices=["fake", "fcm", "apns", "webpush"])
    flow.add_argument("--push-token", default=None)
    flow.add_argument("--platform", default="headless")
    flow.add_argument("--locale", default="tr-TR")
    flow.add_argument("--format", dest="share_format", default="pdf")
    flow.add_argument("--download-dir", default=None)
    flow.add_argument("--wait-timeout", type=float, default=20.0)
    flow.add_argument("--wait-interval", type=float, default=0.25)
    flow.add_argument("--reauth", action="store_true", help="re-authenticate on a 401")
    flow.add_argument(
        "--reuse-session",
        action="store_true",
        help="continue with an already-held session instead of exchanging the credential",
    )
    flow.add_argument(
        "--no-wait",
        action="store_true",
        help="skip waiting for the artifact-ready notification",
    )

    sub.add_parser("connect", help="sign in and report the session (the connectivity check)")

    register = sub.add_parser("register-push", help="register this session for push")
    register.add_argument(
        "--push-provider", default="fake", choices=["fake", "fcm", "apns", "webpush"]
    )
    register.add_argument("--push-token", default=None)
    register.add_argument("--platform", default="headless")
    register.add_argument("--locale", default="tr-TR")

    notifications = sub.add_parser("notifications", help="poll this session's notifications")
    notifications.add_argument("--limit", type=int, default=50)
    notifications.add_argument("--wait", action="store_true", help="block until one arrives")
    notifications.add_argument("--artifact-id", default=None)
    notifications.add_argument("--wait-timeout", type=float, default=20.0)

    resume = sub.add_parser("narration-resume", help="resume narration from the cloud cursor")
    resume.add_argument("--artifact-id", required=True)
    resume.add_argument("--narration-session-id", default=None)

    download = sub.add_parser("download", help="download a render for the share sheet")
    download.add_argument("--artifact-id", required=True)
    download.add_argument("--format", dest="share_format", default="pdf")
    download.add_argument("--download-dir", default=".")

    return parser


def _emit(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, dict) and "steps" in payload:
        for entry in payload["steps"]:
            detail = " ".join(f"{k}={v}" for k, v in entry.items() if k != "step")
            print(f"- {entry['step']}: {detail}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "flow"
    if not args.credential:
        parser.error(f"an owner credential is required (--credential or ${ENV_CREDENTIAL})")

    device_id = uuid.UUID(args.device_id) if args.device_id else None
    client = MobileReferenceClient(
        args.base_url,
        credential=args.credential,
        label=args.label,
        device_id=device_id,
        timeout_s=args.timeout,
    )
    try:
        if command == "flow":
            report = client.run_flow(
                artifact_id=args.artifact_id,
                push_provider=args.push_provider,
                push_token=args.push_token,
                platform=args.platform,
                locale=args.locale,
                share_format=args.share_format,
                download_dir=Path(args.download_dir) if args.download_dir else None,
                wait_timeout_s=args.wait_timeout,
                wait_interval_s=args.wait_interval,
                reauth=args.reauth,
                reuse_session=args.reuse_session,
                expect_notification=not args.no_wait,
            )
            _emit(report.to_dict(), as_json=args.json)
        elif command == "connect":
            client.sign_in()
            _emit(client.whoami(), as_json=args.json)
        elif command == "register-push":
            client.sign_in()
            _emit(
                client.register_push(
                    provider=args.push_provider,
                    token=args.push_token,
                    platform=args.platform,
                    locale=args.locale,
                ),
                as_json=args.json,
            )
        elif command == "notifications":
            client.sign_in()
            if args.wait:
                _emit(
                    client.wait_for_artifact_ready(
                        artifact_id=args.artifact_id, timeout_s=args.wait_timeout
                    ),
                    as_json=args.json,
                )
            else:
                _emit(client.notifications(limit=args.limit), as_json=args.json)
        elif command == "narration-resume":
            client.sign_in()
            narration_id = args.narration_session_id or client.start_narration(
                args.artifact_id
            )["session_id"]
            _emit(client.resume_narration(narration_id), as_json=args.json)
        elif command == "download":
            client.sign_in()
            _emit(
                client.download_render(
                    args.artifact_id,
                    args.share_format,
                    dest_dir=Path(args.download_dir),
                ),
                as_json=args.json,
            )
        else:  # pragma: no cover - argparse constrains the choices
            parser.error(f"unknown command: {command}")
    except SessionRevoked as exc:
        # Exit cleanly. A revoked session is a normal end state for a mobile
        # client (the owner revoked the phone), not a crash.
        print(f"session revoked: {exc}", file=sys.stderr)
        return EXIT_SESSION_REVOKED
    except ContractFailed as exc:
        print(f"contract failed: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_FAILED
    except httpx.HTTPError as exc:
        print(f"transport error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_FAILED
    finally:
        client.close()
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
