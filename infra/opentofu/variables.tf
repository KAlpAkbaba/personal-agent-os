variable "hcloud_token" {
  description = "Hetzner Cloud API token. Supply via TF_VAR_hcloud_token; never commit it."
  type        = string
  sensitive   = true
}

variable "tailscale_auth_key" {
  description = <<-EOT
    Tailscale auth key used once, at first boot, to join the tailnet. Supply via
    TF_VAR_tailscale_auth_key.

    Use a key that is pre-authorized, tagged (tag:agent-os-cloud), single-use — and
    NOT ephemeral.

    Single-use is safe because cloud-init spends it exactly once; afterwards the node
    holds its own node key. "Not ephemeral" is the part that matters and is easy to get
    wrong: Tailscale REMOVES an ephemeral node from the tailnet shortly after it goes
    offline. A VPS reboot is one of the resilience criteria this milestone has to pass,
    and an ephemeral node would be deleted while it reboots and come back — if at all —
    as a different node with a different tailnet address. The Windows agent dials a
    pinned broker endpoint, so that is precisely the failure it cannot recover from
    without owner intervention. A persistent, tagged node keeps its address across
    reboots and network loss, which is what "recovery without owner intervention" needs.
  EOT
  type        = string
  sensitive   = true
}

variable "name_prefix" {
  description = "Prefix for every created resource."
  type        = string
  default     = "pagentos"
}

variable "location" {
  description = "Hetzner location. NBG1 (Nuremberg) per docs/CLOUD_INFRASTRUCTURE.md §1."
  type        = string
  default     = "nbg1"
}

variable "server_type" {
  description = <<-EOT
    Hetzner server type. The plan targets ~8 vCPU / 16 GB (CPX42 class): PostgreSQL,
    Temporal, the application containers and observability are cramped below 16 GB.
    Verify current availability and price in the Hetzner console before applying, and
    record the chosen SKU in docs/DECISIONS.md.
  EOT
  type        = string
  default     = "cpx42"
}

variable "image" {
  description = "Base image. Ubuntu LTS."
  type        = string
  default     = "ubuntu-24.04"
}

variable "data_volume_gb" {
  description = "Size of the separate data volume holding PostgreSQL and artifacts."
  type        = number
  default     = 100
}

variable "admin_user" {
  description = "Non-root account created at first boot; root login over SSH stays disabled."
  type        = string
  default     = "pagentos"
}

variable "owner_ssh_public_key" {
  description = "The owner's SSH public key, for the break-glass path if Tailscale is down."
  type        = string
}

variable "ssh_admin_cidrs" {
  description = <<-EOT
    Public source addresses allowed to reach TCP/22. Empty by default, which creates no
    public SSH rule at all — administration goes over the tailnet. Populate this only as a
    deliberate break-glass measure, and empty it again afterwards.
  EOT
  type        = list(string)
  default     = []
}
