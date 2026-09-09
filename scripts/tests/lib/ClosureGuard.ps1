<#
.SYNOPSIS
    The static guard for the defect class that has now cost two owner runs: a script block
    bound with .GetNewClosure() that calls a command only a dot-sourced script scope defines.

.DESCRIPTION
    .GetNewClosure() copies the current VARIABLES into a fresh dynamic module and links that
    module to the GLOBAL session state. Functions are not copied and the script scope is not
    in the lookup path, so a bareword command inside the closure resolves only if PowerShell
    itself knows it - or if the outermost script happens to BE the top-level scope, which is
    what `powershell -File script.ps1` makes it and what `.\script.ps1` does not.

    That difference is invisible to every harness in this repository, because they all run
    with -File. It has produced two production failures:

      2026-09-06  an owner harness bound a callback that called Get-NewSessions and crashed
                  after a successful Cloud Core release
      2026-09-09  the installer's Cloud Core fetcher called Invoke-JsonUtf8, could not
                  resolve it, and rolled a healthy 0.6.0 candidate back after 90.6 s

    The 2026-09-06 guard banned .GetNewClosure() outright in the files it covered - and the
    installer's libraries were not among them. A blanket ban is also the wrong rule: a
    closure that resolves no command by name is correct, and that is what the fix on both
    sites looks like (`& $captured` where $captured holds a FunctionInfo). So the rule here
    is the precise one: what may not appear inside a closure is a NAME a fresh PowerShell
    cannot resolve.

    Dot-source. Windows PowerShell 5.1, StrictMode-safe, parses only - nothing is executed.
#>

Set-StrictMode -Version Latest

function Get-ClosureCommandRisks {
    <#
    .SYNOPSIS
        For each .GetNewClosure() site in a script, the bareword commands inside it that this
        (fresh, -NoProfile) PowerShell cannot resolve. Empty means the file is safe.
    .OUTPUTS
        Strings like "line 342: Invoke-JsonUtf8, Get-Json".
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$null)
    $risks = @()
    foreach ($m in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
        if (-not ($m.Member -is [System.Management.Automation.Language.StringConstantExpressionAst])) { continue }
        if ($m.Member.Value -ne "GetNewClosure") { continue }
        $names = @{}
        foreach ($c in $m.Expression.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
            $name = $c.GetCommandName()
            if ([string]::IsNullOrEmpty($name)) { continue }          # `& $variable` - no name to lose
            if ($name -match '^[\\./]' -or $name -match '\.ps1$') { continue }  # a path, not a symbol
            if (Get-Command -Name $name -ErrorAction SilentlyContinue) { continue }
            $names[$name] = $true
        }
        if ($names.Count -gt 0) {
            $risks += "line $($m.Extent.StartLineNumber): $(($names.Keys | Sort-Object) -join ', ')"
        }
    }
    return , $risks
}
