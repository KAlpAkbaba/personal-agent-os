# ADR text: the cloud browser worker (ADR-0213 PR 2)

Decision: the cloud worker is the SAME browser worker package on Linux in its own container
beside the Cloud Core, never in the api process. Because nothing on Linux plays the C#
companion, `browser_agent/cloud` is a small Python companion: it enrolls as device
`name="bulut"`, `platform="cloud"` (the api has no `device_kind` field; `platform` is a free
1..64 string), dials `/v1/devices/connect` with the same hello/challenge/ECDSA-auth/heartbeat/
command_ack protocol as the Windows agent, and relays commands to the unchanged worker child
(headless ManagedBackend, chromium, dedicated profile, no `--trusted-origin`, no
`--allow-private-destinations`).

Policy: READ+NAVIGATE, enforced in the companion. A `session_open` that requests a wider
class set, or a profile other than `research`, is refused with `security_scope_error` before
the worker sees it; the worker itself would honour a wider request, so this clamp is the guard.

Start: no persisted identity and no non-empty enrollment token file = exit 2. The token is
minted on the Cloud Core host (owner session + loopback) and handed over as a file once; the
worker persists `device_id` + key in its state volume (mode 600) and deletes the token.

Container: `init`, `shm_size 2gb`, `mem_limit 2g`, `memswap_limit 2g`, `cpus 2`, restart
unless-stopped, no ports, all caps dropped, healthcheck on a liveness marker touched at welcome
and each heartbeat. Memory on CPX32 is NOT measured (Docker not runnable on the dev PC): the
evidence file says NOT_RUN and carries the command.

Consequences: an optional extra `cloud` (websockets, cryptography) in services/browser only.
The alias `bulut` is owner data in `devices.metadata_json.aliases`; enrollment does not set it.
