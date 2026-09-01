<#
.SYNOPSIS
    Read fields out of the recovery tool's status/rotate payloads — in one place, tested.

.DESCRIPTION
    A real rotation reported "rotations 0 -> 0" with a warning while the rotation had in fact
    advanced the counter to 2. The wrapper read `$status.root.rotations`, but `rotations` is a
    TOP-LEVEL field of the payload; `root` is the storage descriptor
    (`{bootstrapped, kind, path}`) and has never carried a counter. Defensive
    property-existence checks then turned the wrong path into a silent zero instead of an
    error — defence that hid the defect it should have surfaced.

    So: one accessor, used by every script that reads these payloads, which THROWS on a
    payload that does not carry the field rather than defaulting. A missing counter is a
    protocol change to be noticed, not a zero.
#>

Set-StrictMode -Version Latest

function Get-IdentityRotationCount {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Status)

    if ($Status.PSObject.Properties.Name -notcontains "rotations") {
        throw "the identity status payload has no top-level 'rotations' field; the recovery tool's output shape changed"
    }

    return [int]$Status.rotations
}

function Get-IdentityRootPath {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Status)

    if (($Status.PSObject.Properties.Name -notcontains "root") -or
        ($Status.root.PSObject.Properties.Name -notcontains "path")) {
        throw "the identity status payload has no root.path; the recovery tool's output shape changed"
    }

    return [string]$Status.root.path
}

function Get-IdentityCreatedAt {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Status)

    if ($Status.PSObject.Properties.Name -notcontains "created_at") {
        throw "the identity status payload has no created_at; the recovery tool's output shape changed"
    }

    return [string]$Status.created_at
}
