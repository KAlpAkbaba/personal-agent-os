<#
.SYNOPSIS
    The protected-path list: what a request never widens a card's area into, whoever asks.

.DESCRIPTION
    Dot-sourced by scripts/lib/TeamArea.ps1 (after TeamQueue.ps1, whose $script:TeamSharedFiles
    it reads). The list has its own file so that the file a request must not shorten is this
    list alone: TeamArea.ps1's area logic is ordinary team code a lead may grant
    (protected-list-own-file, 2026-10-07 - before, a card fixing area logic stopped at the
    Danışman because the whole of TeamArea.ps1 was protected).

    Defines New-TeamAreaProtectedEntry and $script:TeamAreaProtected; nothing else.

    Windows PowerShell 5.1, StrictMode.
#>

function New-TeamAreaProtectedEntry {
    param([string]$Name, [string]$Kind, [string]$Value, [string]$Source)
    return [pscustomobject]@{ Name = $Name; Kind = $Kind; Value = $Value; Source = $Source }
}

# Never widened into, whoever asks. ONE list:
#   path     the path itself, anything under it, and any directory that holds it;
#   pattern  a regular expression on the path as Get-TeamAreaKey spells it (lower case,
#            forward slashes).
# The shared files are TeamQueue.ps1's own list, taken as it is: two lists would drift.
$script:TeamAreaProtected = @(
    @($script:TeamSharedFiles | ForEach-Object {
            New-TeamAreaProtectedEntry -Name $_ -Kind "path" -Value $_ -Source "scripts/lib/TeamQueue.ps1 `$script:TeamSharedFiles (TEAM_PROTOCOL section 4: the lead writes it)"
        })

    # The lead itself may not edit these without the owner.
    New-TeamAreaProtectedEntry -Name "docs/ROADMAP.md" -Kind "path" -Value "docs/ROADMAP.md" -Source ".claude/agents/lead.md (never edit ROADMAP without an owner decision)"
    New-TeamAreaProtectedEntry -Name "docs/TEAM_PROTOCOL.md" -Kind "path" -Value "docs/TEAM_PROTOCOL.md" -Source ".claude/agents/lead.md (never edit TEAM_PROTOCOL without an owner decision)"
    New-TeamAreaProtectedEntry -Name ".claude/agents" -Kind "path" -Value ".claude/agents" -Source "docs/TEAM_PROTOCOL.md section 2 (the role files are the roles' own rules)"
    New-TeamAreaProtectedEntry -Name "hand-gestures" -Kind "pattern" -Value "hand[-_]?gestures" -Source ".claude/agents/worker.md (feat/hand-gestures-stage1 is frozen); Test-TeamSplit refuses the same"

    # Secrets: what .gitignore keeps out of git, and the secret root (constitution section 6).
    New-TeamAreaProtectedEntry -Name "env-file" -Kind "pattern" -Value "(^|/)\.env(\.[^/]*)?$" -Source ".gitignore '# Secrets': .env, .env.*"
    New-TeamAreaProtectedEntry -Name "key-material" -Kind "pattern" -Value "\.(key|pem|pfx)$" -Source ".gitignore '# Secrets': *.key, *.pem, *.pfx"
    New-TeamAreaProtectedEntry -Name "tofu-state" -Kind "pattern" -Value "(\.tfstate(\.[^/]*)?|\.tfvars|\.tfplan|(^|/)tfplan\.binary)$" -Source ".gitignore: *.tfstate, *.tfvars, tfplan.binary, *.tfplan (they hold the tokens)"
    New-TeamAreaProtectedEntry -Name "secrets" -Kind "path" -Value "secrets" -Source ".gitignore '# Secrets': secrets/local/"
    New-TeamAreaProtectedEntry -Name "services/api/var" -Kind "path" -Value "services/api/var" -Source ".gitignore: the owner identity root (the hash of the owner credential)"
    New-TeamAreaProtectedEntry -Name "scripts/lib/SecretStore.ps1" -Kind "path" -Value "scripts/lib/SecretStore.ps1" -Source "PROJECT_CONSTITUTION.md section 6 'secret root': the DPAPI store"
    New-TeamAreaProtectedEntry -Name "scripts/secret-store.ps1" -Kind "path" -Value "scripts/secret-store.ps1" -Source "PROJECT_CONSTITUTION.md section 6 'secret root': the store's entry point"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/install-env-secret.sh" -Kind "path" -Value "scripts/cloud/install-env-secret.sh" -Source "PROJECT_CONSTITUTION.md section 6 'secret root': writes the Cloud Core's .env"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/set-cloud-secret.ps1" -Kind "path" -Value "scripts/cloud/set-cloud-secret.ps1" -Source "PROJECT_CONSTITUTION.md section 6 'secret root': sends a secret to the Cloud Core"

    # Last-known-good metadata: the pointer and the two places that write it.
    New-TeamAreaProtectedEntry -Name "last-known-good" -Kind "pattern" -Value "last[-_]?known[-_]?good" -Source "PROJECT_CONSTITUTION.md section 6 'last-known-good release pointer'; docs/DEVELOPMENT_POLICY.md section 11"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/release-cloud-core-bluegreen.sh" -Kind "path" -Value "scripts/cloud/release-cloud-core-bluegreen.sh" -Source "writes RELEASE and LAST_KNOWN_GOOD on the host; installed as the recovery root's reconcile.sh (scripts/cloud/install-recovery-supervisor.sh)"
    New-TeamAreaProtectedEntry -Name "services/recovery-supervisor" -Kind "path" -Value "services/recovery-supervisor" -Source "PROJECT_CONSTITUTION.md section 6 'recovery supervisor'; recovery_supervisor/workspace.py holds last_known_good.txt; services/api/app/evolution/sandbox.py PROTECTED_TREES"

    # The recovery roots: what must run when the main application is broken.
    New-TeamAreaProtectedEntry -Name "services/api/app/identity/root.py" -Kind "path" -Value "services/api/app/identity/root.py" -Source "PROJECT_CONSTITUTION.md section 6 'owner identity root'"
    New-TeamAreaProtectedEntry -Name "infra/docker/docker-compose.prod.yml" -Kind "path" -Value "infra/docker/docker-compose.prod.yml" -Source "copied into /opt/pagentos-recovery (scripts/cloud/install-recovery-supervisor.sh)"
    New-TeamAreaProtectedEntry -Name "infra/docker/edge" -Kind "path" -Value "infra/docker/edge" -Source "nginx.conf is copied into /opt/pagentos-recovery (scripts/cloud/install-recovery-supervisor.sh)"
    New-TeamAreaProtectedEntry -Name "infra/systemd" -Kind "path" -Value "infra/systemd" -Source "the root units of the reconcile timer and the backup (services/api/app/evolution/risk.py: 'the recovery timer')"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/install-recovery-supervisor.sh" -Kind "path" -Value "scripts/cloud/install-recovery-supervisor.sh" -Source "writes the recovery root and its pin (APPROVED_SHA)"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/uninstall-recovery-supervisor.sh" -Kind "path" -Value "scripts/cloud/uninstall-recovery-supervisor.sh" -Source "removes the recovery root"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/backup-cloud-core.sh" -Kind "path" -Value "scripts/cloud/backup-cloud-core.sh" -Source "PROJECT_CONSTITUTION.md section 6 'backup/restore primitives'"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/restore-cloud-core.sh" -Kind "path" -Value "scripts/cloud/restore-cloud-core.sh" -Source "PROJECT_CONSTITUTION.md section 6 'backup/restore primitives'"

    # The lead's ruling at merge (ADR-0253, 'Protected by the lead'): what governs the agents
    # themselves and what sends a tree to production is never widened into by a request.
    # Shared code that cards hold every day (the gate, the CI file, cycle.ps1, TeamQueue.ps1,
    # and TeamArea.ps1's area logic) is deliberately NOT here: a holder in work makes the
    # request wait, the inspector reads the diff, and a new suite needs its gate line -
    # refusing that would stop the very cards this file exists for. THIS file is: it is the
    # list, and a request must not be able to shorten it (protected-list-own-file).
    New-TeamAreaProtectedEntry -Name "scripts/lib/TeamAreaProtected.ps1" -Kind "path" -Value "scripts/lib/TeamAreaProtected.ps1" -Source "this list: a request must not be able to shorten it"
    New-TeamAreaProtectedEntry -Name ".claude/hooks" -Kind "path" -Value ".claude/hooks" -Source "CLAUDE.md 'Session continuity': the hook that hands every session its handoff"
    New-TeamAreaProtectedEntry -Name "claude-settings" -Kind "pattern" -Value "(^|/)\.claude/settings(\.[^/]*)?\.json$" -Source "the agents' own permissions and hooks"
    New-TeamAreaProtectedEntry -Name "claude-md" -Kind "pattern" -Value "(^|/)claude\.md$" -Source "the engineering contract every agent is given, in any directory"
    New-TeamAreaProtectedEntry -Name "PROJECT_CONSTITUTION.md" -Kind "path" -Value "PROJECT_CONSTITUTION.md" -Source "the product's constitution: the owner's"
    New-TeamAreaProtectedEntry -Name "docs/DEVELOPMENT_POLICY.md" -Kind "path" -Value "docs/DEVELOPMENT_POLICY.md" -Source "the owner's permanent directive of 2026-09-07 (CLAUDE.md 'Engineering operating mode')"
    New-TeamAreaProtectedEntry -Name "git-internals" -Kind "pattern" -Value "(^|/)\.git(/|$)" -Source "the repository's own files and hooks are not source"
    New-TeamAreaProtectedEntry -Name "gitignore" -Kind "pattern" -Value "(^|/)\.gitignore$" -Source "its '# Secrets' block is what keeps secrets out of git"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/release-cloud-core.ps1" -Kind "path" -Value "scripts/cloud/release-cloud-core.ps1" -Source "sends a tree to the production host (ADR-0214 addendum 9: the release is the lead's)"
)
