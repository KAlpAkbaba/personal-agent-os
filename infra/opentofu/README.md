# opentofu — Hetzner + Tailscale production host

Provisions the single cloud host described in `docs/CLOUD_INFRASTRUCTURE.md`: one Hetzner
NBG1 server on the owner's tailnet, with **no public application port**. Administration goes
over Tailscale SSH; the public IPv4 exists for break-glass only.

## What it will and will not do

It creates the server, a separate data volume, the SSH key and a firewall that denies
inbound by default. It does **not** deploy the application: pulling and starting images is a
release action with its own health checks and last-known-good pointer, not something a
machine should do to itself while first booting.

## Owner actions this needs (and why they cannot be automated)

1. A Hetzner account with billing, and a project API token — creating an account and
   entering payment details is legally the owner's act, not the agent's.
2. A Tailscale account and an auth key for the new node — same reason, plus the key
   authorizes a machine to join the owner's private network.

Both arrive as environment variables and are never written to the repository:

```bash
export TF_VAR_hcloud_token='...'          # Hetzner project API token
export TF_VAR_tailscale_auth_key='...'    # ephemeral, pre-authorized, tag:agent-os-cloud
export TF_VAR_owner_ssh_public_key="$(cat ~/.ssh/id_ed25519.pub)"
```

Prefer an **ephemeral, pre-authorized, tagged** Tailscale key: ephemeral so a destroyed node
leaves the tailnet with it, tagged so ACLs can talk about "the cloud core" rather than about
a user, and pre-authorized so the node does not sit waiting for manual approval on first
boot.

## Running it

```bash
tofu init
tofu plan     # read this: the server and the data volume both carry prevent_destroy
tofu apply
```

`prevent_destroy` is set on the server and the volume on purpose. A plan that wants to
replace either is telling you that a change you made would take the database with it —
that is a migration to design, not a diff to approve.

## Verify the network posture before trusting it

After apply, from the owner's machine (also on the tailnet):

```bash
tailscale status                       # the node should be listed and reachable
tailscale ssh pagentos@pagentos-core   # administration without a public port
nmap -Pn -p- <public_ipv4>             # expect: no application port open
```

The last one is the point of this whole file. If anything answers on the public address
beyond ICMP and Tailscale's UDP port, the posture is broken and the deployment should stop
until it is understood.

## State and secrets

`*.tfstate` is gitignored. State contains resource attributes and, depending on provider
behaviour, values marked sensitive — treat the state file as a secret, keep it local or in
an encrypted remote backend, and never commit it.
