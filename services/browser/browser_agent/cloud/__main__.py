"""``python -m browser_agent.cloud``: enroll if needed, start the worker, dial the broker."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterable

from .. import policy
from ..obs_logging import configure_logging, get_logger
from . import broker, config, healthcheck
from . import policy as cloud_policy

logger = get_logger(__name__)

RECONNECT_BACKOFF_S = (1, 2, 5, 10, 30)


def build_worker_args(
    cfg: config.CloudConfig,
    session_classes: Iterable[policy.RiskClass] = cloud_policy.CLOUD_SESSION_CLASSES,
) -> list[str]:
    """The worker CLI arguments (after ``-m browser_agent.worker``): headless ManagedBackend on
    chromium, a dedicated profile, no ``--trusted-origin``, no ``--allow-private-destinations``,
    no owner enrollment."""
    cloud_policy.assert_policy_within(session_classes)
    return [
        "--data-dir",
        str(cfg.data_dir),
        "--profile-dir",
        str(cfg.data_dir / "profile"),
        "--channel",
        "chromium",
        "--headless",
    ]


async def _run(cfg: config.CloudConfig) -> None:
    import websockets  # the `cloud` extra

    if not cfg.has_identity():
        await asyncio.to_thread(broker.enroll, cfg)
    device_id = cfg.read_device_id()
    pem = cfg.key_path.read_bytes()
    worker = broker.SubprocessWorker(build_worker_args(cfg))
    await worker.start()
    bridge = broker.CloudBridge(
        device_id=device_id,
        signer=lambda nonce_b64: broker.sign_challenge(pem, nonce_b64, device_id),
        worker=worker,
        max_concurrent=cfg.max_concurrent,
        on_alive=lambda: healthcheck.touch(cfg),
    )
    attempt = 0
    try:
        while True:
            try:
                async with websockets.connect(cfg.ws_url) as ws:
                    attempt = 0
                    await bridge.serve(ws)
            except broker.AuthRefused:
                logger.error("cloud.auth_refused", device_id=device_id)
                return
            except (OSError, websockets.ConnectionClosed) as exc:
                logger.warning("cloud.reconnect", error=str(exc))
            await asyncio.sleep(RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)])
            attempt += 1
    finally:
        await worker.stop()


def main(argv: list[str] | None = None) -> int:
    del argv
    configure_logging(stream=sys.stderr)
    try:
        cfg = config.load_config(os.environ)
    except config.ConfigError as exc:
        sys.stderr.write(f"cloud worker refuses to start: {exc}\n")
        return 2
    asyncio.run(_run(cfg))
    return 1  # the loop only ends on an auth refusal


if __name__ == "__main__":
    # The container's ENTRYPOINT. Without this the module defined `main` and exited 0
    # having done nothing (found by the first real build, 2026-09-30).
    raise SystemExit(main())
