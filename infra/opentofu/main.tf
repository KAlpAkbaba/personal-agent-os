// Personal Agent OS — production host (Hetzner NBG1 + Tailscale).
//
// The shape of this file follows one rule from docs/CLOUD_INFRASTRUCTURE.md §3 and the
// constitution's network posture: the machine has no public service surface. Not "a public
// surface behind a password" — none. Every application port is reachable only over the
// tailnet, and the firewall below is written so that forgetting to lock something down is
// not possible by omission: inbound is denied by default and each exception is deliberate.
//
// Nothing here contains a secret. The Hetzner token and the Tailscale auth key arrive as
// environment variables (TF_VAR_hcloud_token, TF_VAR_tailscale_auth_key) and never enter
// version control; state files are gitignored for the same reason.

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.51"
    }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}

// The owner's SSH key, so the machine is reachable if Tailscale itself is what broke.
// This is the recovery path, not the working path.
resource "hcloud_ssh_key" "owner" {
  name       = "${var.name_prefix}-owner"
  public_key = var.owner_ssh_public_key
}

resource "hcloud_firewall" "agent_os" {
  name = "${var.name_prefix}-firewall"

  // Tailscale's direct-connection port. Without it, peers still connect via DERP relays —
  // it works, but every packet takes a detour. This is the one inbound port that exists.
  rule {
    direction  = "in"
    protocol   = "udp"
    port       = "41641"
    source_ips = ["0.0.0.0/0", "::/0"]
    description = "Tailscale direct connection (WireGuard)"
  }

  // ICMP, so the machine can be diagnosed at all.
  rule {
    direction   = "in"
    protocol    = "icmp"
    source_ips  = ["0.0.0.0/0", "::/0"]
    description = "ICMP for diagnostics"
  }

  // Public SSH is OFF unless the owner explicitly lists source addresses. The default is an
  // empty list, which produces no rule at all: SSH goes over the tailnet like everything
  // else. Set ssh_admin_cidrs only if the tailnet is unavailable and you need a way back in.
  dynamic "rule" {
    for_each = length(var.ssh_admin_cidrs) > 0 ? [1] : []
    content {
      direction   = "in"
      protocol    = "tcp"
      port        = "22"
      source_ips  = var.ssh_admin_cidrs
      description = "Break-glass SSH from named addresses only"
    }
  }
}

resource "hcloud_server" "agent_os" {
  name         = "${var.name_prefix}-core"
  server_type  = var.server_type
  image        = var.image
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.owner.id]
  firewall_ids = [hcloud_firewall.agent_os.id]

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  labels = {
    project = "personal-agent-os"
    role    = "cloud-core"
  }

  user_data = templatefile("${path.module}/cloud-init.yaml", {
    tailscale_auth_key = var.tailscale_auth_key
    tailscale_hostname = "${var.name_prefix}-core"
    admin_user         = var.admin_user
    ssh_public_key     = var.owner_ssh_public_key
  })

  // Replacing the server would destroy the database volume with it. Deliberate changes go
  // through a documented migration, never through an implicit replace on a plan diff.
  lifecycle {
    prevent_destroy = true
  }
}

// Application data lives on its own volume so the server can be rebuilt, resized or replaced
// without taking PostgreSQL with it.
resource "hcloud_volume" "data" {
  name      = "${var.name_prefix}-data"
  size      = var.data_volume_gb
  server_id = hcloud_server.agent_os.id
  automount = true
  format    = "ext4"

  lifecycle {
    prevent_destroy = true
  }
}
