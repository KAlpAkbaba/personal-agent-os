output "server_id" {
  description = "Hetzner server id."
  value       = hcloud_server.agent_os.id
}

output "tailscale_hostname" {
  description = <<-EOT
    The name to use for everything. The public IPv4 below exists for break-glass and for
    Hetzner's own console; the agent, the web shell and the owner's devices should all reach
    this host by its tailnet name, never by its public address.
  EOT
  value       = "${var.name_prefix}-core"
}

output "public_ipv4" {
  description = "Break-glass only. No application port is served here."
  value       = hcloud_server.agent_os.ipv4_address
}

output "data_volume_device" {
  description = "Linux device path of the data volume."
  value       = hcloud_volume.data.linux_device
}
