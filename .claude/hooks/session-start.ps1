<#
  SessionStart hook: puts the live handoff into a new session's context, whichever Claude
  account opened it. The owner alternates two accounts; the repository is shared, the
  conversation is not. What is printed here is what a fresh session would otherwise have to
  be told by the owner.

  Prints, in order:
    1. the docs/HANDOFF.md block between the session-start markers
       (work in progress, current state, next tasks);
    2. git status --short and the last commits;
    3. a warning when the tree is dirty (the other account may have stopped mid-task).

  Must never fail a session start: every step is guarded, exit code is always 0.
  ASCII-only on purpose: Windows PowerShell 5.1 reads a BOM-less script as ANSI.
#>
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root = $env:CLAUDE_PROJECT_DIR
if (-not $root) { $root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }

$git = "git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $git = "C:\Program Files\Git\cmd\git.exe"
}

$out = New-Object System.Collections.Generic.List[string]
$out.Add("=== HESAP GECISI / OTURUM BASLANGICI (otomatik: .claude/hooks/session-start.ps1) ===")
$out.Add("Kural: sahibe Turkce yaz. Once CLAUDE.md, sonra docs/HANDOFF.md. Asagidaki blok HANDOFF'un canli kismidir.")
$out.Add("")

try {
    $handoff = Join-Path $root "docs\HANDOFF.md"
    $lines = Get-Content -LiteralPath $handoff -Encoding UTF8 -ErrorAction Stop
    $inside = $false
    $taken = 0
    foreach ($line in $lines) {
        if ($line -match "<!--\s*session-start:begin\s*-->") { $inside = $true; continue }
        if ($line -match "<!--\s*session-start:end\s*-->") { break }
        if ($inside) { $out.Add($line); $taken++ }
    }
    if ($taken -eq 0) {
        $out.Add("(HANDOFF.md icinde session-start isaretleri bulunamadi; dosyayi tamamen oku.)")
    }
} catch {
    $out.Add("(docs/HANDOFF.md okunamadi: $($_.Exception.Message))")
}

$out.Add("")
try {
    $status = & $git -C $root status --short --branch 2>$null
    $out.Add("--- git status ---")
    foreach ($s in $status) { $out.Add([string]$s) }
    $dirty = @($status | Where-Object { $_ -and -not ([string]$_).StartsWith("##") })
    if ($dirty.Count -gt 0) {
        $out.Add("")
        $out.Add("UYARI: calisma agacinda $($dirty.Count) islenmemis degisiklik var. Onceki oturum (belki diger hesap) is ortasinda kesilmis olabilir.")
        $out.Add("'Su an uzerinde calisilan' bolumu ile git diff'i karsilastir; bu degisiklikleri silme, tamamla.")
    }
    $out.Add("")
    $out.Add("--- son commitler ---")
    foreach ($c in (& $git -C $root log -8 --format="%h %ad %s" --date=format:"%Y-%m-%d %H:%M" 2>$null)) { $out.Add([string]$c) }
} catch {
    $out.Add("(git okunamadi: $($_.Exception.Message))")
}

[Console]::Out.Write(($out -join "`n") + "`n")
exit 0
