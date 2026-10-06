# The processes the tick never stops, even when they sit in a finished cycle's job object.
#
# 2026-10-06 06:46 and 14:58: a cycle's run started Docker Desktop (a `docker compose up` while
# the engine was down starts it), so Docker Desktop, its WSL distro and their helpers were in
# the cycle's job; when the cycle ended the tick ended the job and with it the dev Postgres,
# Temporal and the staging stack every other run and the release gate stand on. A machine
# service is not a leftover: it is left running and named in the log.

$script:TeamTickKeepNames = @(
    "Docker Desktop.exe",
    "com.docker.backend.exe",
    "com.docker.build.exe",
    "com.docker.service",
    "com.docker.proxy.exe",
    "com.docker.dev-envs.exe",
    "docker.exe",
    "dockerd.exe",
    "docker-proxy.exe",
    "vpnkit.exe",
    "vpnkit-bridge.exe",
    "wsl.exe",
    "wslhost.exe",
    "wslservice.exe",
    "wslrelay.exe",
    "vmmem",
    "vmmemWSL",
    "vmwp.exe"
)

function Test-TeamTickKeep {
    <# $true for a process the tick must leave running: a machine service, matched by its image
       name (case-insensitive), or any process whose command line runs Docker Desktop's own
       files or the docker-desktop WSL distro. #>
    param([string]$Name, [string]$CommandLine = "")
    foreach ($keep in $script:TeamTickKeepNames) {
        if ([string]::Equals($Name, $keep, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    if ($Name -like "com.docker.*") { return $true }
    if ($CommandLine -match '(?i)\\Docker\\Docker\\|-d docker-desktop') { return $true }
    return $false
}
