"""Restore a device's broker registration from the device's own identity material.

Why this exists, stated bluntly: the machine's real enrollment row was destroyed by the
integration suite. The "production" Cloud Core shared the dev database, and the suite's
migration test round-trips the schema (drop + recreate), taking `devices` with it. The
device itself was never wrong — its private key and state.json are intact — so the honest
repair is to restore the SERVER'S record from the DEVICE'S identity, preserving the original
device_id and public key. This is not re-enrollment: no new identity is minted, no
enrollment token is consumed, and a device that already has a row is left alone unless its
key differs (which is an error, not something to silently overwrite).

Usage (from services/api, with PAGENTOS_DATABASE_URL pointing at the target database):

    uv run python scripts/restore_device_registration.py \
        --device-id <uuid> --name <name> --public-key-spki-b64 <b64> [--capability X ...]

stdout is exactly one JSON document; diagnostics go to stderr (the machine-readable
contract this repository already learned the hard way).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--platform", default="windows")
    parser.add_argument("--public-key-spki-b64", required=True)
    parser.add_argument("--capability", action="append", default=[])
    args = parser.parse_args()

    from app.broker.models import DEVICE_STATUS_ENROLLED, Device
    from app.config import get_settings
    from app.db import build_engine, build_session_factory

    device_id = uuid.UUID(args.device_id)
    capabilities = args.capability or ["desktop.open_application", "desktop.open_artifact"]

    engine = build_engine(get_settings().database_url)
    factory = build_session_factory(engine)
    with factory() as session:
        existing = session.get(Device, device_id)
        if existing is not None:
            if existing.public_key_spki_b64 != args.public_key_spki_b64:
                print(
                    json.dumps(
                        {
                            "action": "restore_device",
                            "outcome": "refused",
                            "reason": (
                                "a device with this id exists with a DIFFERENT public "
                                "key; refusing to overwrite an identity"
                            ),
                        }
                    )
                )
                return 2
            print(
                json.dumps(
                    {
                        "action": "restore_device",
                        "outcome": "already_present",
                        "device_id": str(device_id),
                        "status": existing.status,
                    }
                )
            )
            return 0

        session.add(
            Device(
                id=device_id,
                name=args.name,
                platform=args.platform,
                public_key_spki_b64=args.public_key_spki_b64,
                capabilities_json=capabilities,
                status=DEVICE_STATUS_ENROLLED,
                enrolled_at=datetime.now(UTC),
            )
        )
        session.commit()

    print(
        json.dumps(
            {
                "action": "restore_device",
                "outcome": "restored",
                "device_id": str(device_id),
                "name": args.name,
                "capabilities": capabilities,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
