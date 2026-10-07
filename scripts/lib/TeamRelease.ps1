<#
.SYNOPSIS
    What the automatic release step decides (scripts/team/release.ps1, ADR-0214 addendum 9):
    whether gated roadmap work on main may be released without asking the owner, and whether
    a release that ran is what production now serves.

.DESCRIPTION
    Dot-sourced after `NativeProcess.ps1`, `TeamQueue.ps1`, `TeamRun.ps1` and `TeamIntegrate.ps1`.
    The first half is decisions - functions that take their inputs and return their answer, so
    the tests drive them without a repository or a host:

      * Test-TeamMigrationChange: whether a changed path is a migration - a commit with one is
        never released by this step (the Danışman's decision of 2026-10-04);
      * Get-TeamMigrationVerdict: whether an alembic version is expand-only, read conservatively
        (an unreadable one, a changed or deleted existing one, is not) - since 2026-10-04 only an
        information line in the report, never part of the decision;
      * Read-TeamHostProbe / Get-TeamHostProbeCommand: the ONE read-only look at the host;
      * Get-TeamReleaseDecision: 'release' or 'stop', with every reason that stops it;
      * Test-TeamReleaseVerified: RELEASE, APPROVED_SHA, the reconcile's last line and the
        health through the edge all name the sha.

    The second half reads the repository and the gate's records (git only; nothing here writes
    to a branch, a worktree or the host).

    What does NOT become automatic (the addendum's own list) is exactly what stops here: any
    migration (the Danışman releases those), the host's compose or edge, anything the gate did not
    pass, a maintenance window within 30 minutes, health that is not ok - and an earlier
    automatic release that was rolled back (team/release-blocked.json) until the lead looked.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

$script:TeamReleaseMaintenanceSeconds = 1800
$script:TeamReleaseApprover = "standing_rule"
$script:TeamReleaseBlockedName = "release-blocked.json"
$script:TeamReleaseProbeKeys = @("release", "app_release", "pin", "lkg", "colour", "maintenance_marker", "maintenance_in_s", "health_b64", "reconcile")
# A changed file among these is the host's own configuration beyond the image: the owner's.
$script:TeamReleaseHostFiles = @("infra/docker/docker-compose.prod.yml")
$script:TeamReleaseHostPrefixes = @("infra/docker/edge/")

# ---------------------------------------------------------------------------- migrations

function Test-TeamMigrationPath {
    <# Whether a repository path is an alembic version file (what the analyzer reads). #>
    param([Parameter(Mandatory = $true)][string]$Path)
    return ((($Path -replace '\\', '/')) -match '(^|/)alembic/versions/[^/]+\.py$')
}

function Test-TeamMigrationChange {
    <#
        Whether a changed path is migration territory - anything under an alembic/ or migrations/
        folder, or alembic.ini. The Danışman's decision of 2026-10-04: a commit that changes one
        is never released by this step, whatever the analyzer says.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    return ((($Path -replace '\\', '/')) -match '(^|/)(alembic|migrations)/|(^|/)alembic\.ini$')
}

function Get-TeamPythonMask {
    <#
    The Python text with every string literal's content and every comment blanked to the same
    length ('x' inside a literal, ' ' for a comment), so its structure is read without what the
    strings say. FStrings: where each f-string starts (its braces are code the mask hides);
    Strings: every literal (Start = its prefix, Quote = its opening quote, Width 1 or 3, End =
    its closing quote); Closed: false when a string never ends.
    #>
    param([AllowEmptyString()][string]$Text = "")
    $chars = $Text.ToCharArray()
    $fstrings = New-Object System.Collections.ArrayList
    $strings = New-Object System.Collections.ArrayList
    $closed = $true
    $i = 0
    while ($i -lt $Text.Length) {
        $c = $Text[$i]
        if ($c -eq '#') {
            while ($i -lt $Text.Length -and $Text[$i] -ne "`n") { $chars[$i] = ' '; $i++ }
            continue
        }
        if ($c -ne '"' -and $c -ne "'") { $i++; continue }
        $p = $i - 1
        while ($p -ge 0 -and "rRbBuUfF".IndexOf($Text[$p]) -ge 0) { $p-- }
        $prefix = ""
        if ($p -lt 0 -or $Text[$p] -notmatch '\w') { $prefix = $Text.Substring($p + 1, $i - $p - 1) }
        if ($prefix -match '[fF]') { [void]$fstrings.Add($p + 1) }
        $width = 1
        if ($i + 2 -lt $Text.Length -and $Text[$i + 1] -eq $c -and $Text[$i + 2] -eq $c) { $width = 3 }
        $quote = ([string]$c) * $width
        $end = -1
        $j = $i + $width
        while ($j -lt $Text.Length) {
            if ($Text[$j] -eq '\') { $j += 2; continue }
            if ($width -eq 1 -and $Text[$j] -eq "`n") { break }
            if ($j + $width -le $Text.Length -and $Text.Substring($j, $width) -ceq $quote) { $end = $j; break }
            $j++
        }
        if ($end -lt 0) { $closed = $false; $end = $Text.Length }
        [void]$strings.Add([pscustomobject]@{ Start = $i - $prefix.Length; Quote = $i; Width = $width; End = $end })
        for ($k = $i + $width; $k -lt $end; $k++) { $chars[$k] = 'x' }
        $i = $end + $width
    }
    return [pscustomobject]@{ Masked = (New-Object string -ArgumentList (, $chars)); FStrings = @($fstrings.ToArray()); Strings = @($strings.ToArray()); Closed = $closed }
}

function Get-TeamPythonStatements {
    <#
    The logical statements of masked Python text (Get-TeamPythonMask): split at a newline or ';'
    outside brackets, a line ending in '\' continued. Start/End index the text; Indented is
    true unless the statement starts a line (a statement after ';' counts as indented).
    #>
    param([AllowEmptyString()][string]$Masked = "")
    $statements = New-Object System.Collections.ArrayList
    $add = {
        param([int]$From, [int]$To)
        $s = $From
        while ($s -lt $To -and " `t`r`n".IndexOf($Masked[$s]) -ge 0) { $s++ }
        $e = $To
        while ($e -gt $s -and " `t`r`n".IndexOf($Masked[$e - 1]) -ge 0) { $e-- }
        if ($e -gt $s) { [void]$statements.Add([pscustomobject]@{ Start = $s; End = $e; Indented = ($s -gt 0 -and $Masked[$s - 1] -ne "`n") }) }
    }
    $depth = 0
    $start = 0
    for ($i = 0; $i -lt $Masked.Length; $i++) {
        $c = $Masked[$i]
        if ("([{".IndexOf($c) -ge 0) { $depth++; continue }
        if (")]}".IndexOf($c) -ge 0) { if ($depth -gt 0) { $depth-- }; continue }
        if ($depth -ne 0 -or ($c -ne "`n" -and $c -ne ';')) { continue }
        if ($c -eq "`n") {
            $b = $i - 1
            while ($b -ge 0 -and " `t`r".IndexOf($Masked[$b]) -ge 0) { $b-- }
            if ($b -ge 0 -and $Masked[$b] -eq '\') { continue }
        }
        & $add $start $i
        $start = $i + 1
    }
    & $add $start $Masked.Length
    return @($statements.ToArray())
}

function Get-TeamPythonArguments {
    <# The top-level comma-separated pieces of a masked argument list, as Start/End pairs. #>
    param([AllowEmptyString()][string]$Masked = "")
    $pieces = New-Object System.Collections.ArrayList
    $depth = 0
    $start = 0
    for ($i = 0; $i -le $Masked.Length; $i++) {
        $c = if ($i -lt $Masked.Length) { $Masked[$i] } else { ',' }
        if ("([{".IndexOf($c) -ge 0) { $depth++ }
        elseif (")]}".IndexOf($c) -ge 0) { if ($depth -gt 0) { $depth-- } }
        elseif ($c -eq ',' -and ($depth -eq 0 -or $i -eq $Masked.Length)) {
            if ($Masked.Substring($start, $i - $start).Trim()) { [void]$pieces.Add([pscustomobject]@{ Start = $start; End = $i }) }
            $start = $i + 1
        }
    }
    return @($pieces.ToArray())
}

function Get-TeamCallObjection {
    <#
    Why masked expression code may do more than build a schema object, or "" when it only
    calls what the allow-list names: sa.<Name>() (a capitalised schema builder), sa.text(),
    sa.literal_column(), sa.true/false/null(), sa.func.now/current_timestamp(),
    postgresql.<Name>(), op.f(), and .with_variant() on such a result. Any other call - a
    helper, os.system, op.get_bind, Session, sa.select, sa.func.pg_sleep, .delete() - and
    lambda/await/yield/import are not on it.
    #>
    param([AllowEmptyString()][string]$Code = "")
    $word = [regex]::Match($Code, '\b(lambda|await|yield|import|exec|eval)\b')
    if ($word.Success) { return "izin listesinde olmayan ifade: $($word.Value)" }
    if ($Code -match '[)\]}]\s*\(') { return "bir ifadenin sonucu çağrılıyor" }
    $allowed = '^(?:(?:sa|sqlalchemy)\.(?:[A-Z]\w*|text|literal_column|true|false|null|func\.(?:now|current_timestamp))|(?:(?:sa|sqlalchemy)\.dialects\.)?postgresql\.[A-Z]\w*|op\.f)$'
    foreach ($m in [regex]::Matches($Code, '(?<![\w.])(\.\s*)?([A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)\s*\(')) {
        $name = $m.Groups[2].Value -replace '\s', ''
        if ($m.Groups[1].Success) { if ($name -cne "with_variant") { return "izin listesinde olmayan çağrı: .$name()" } }
        elseif ($name -cnotmatch $allowed) { return "izin listesinde olmayan çağrı: $name()" }
    }
    return ""
}

function Get-TeamStringObjection {
    <#
    Why a string literal of masked statement code [From, To) may carry SQL, or "" when every one
    is on the white list (Danışman 2026-10-04, return 5). The FIRST argument of sa.text(),
    sa.literal_column(), sa.CheckConstraint(), sa.Computed() and sa.DDL() must be one literal
    whose SQL is a number, a short single-quoted string without ';', now() or CURRENT_TIMESTAMP.
    A comment= or server_default= literal (SQLAlchemy quotes it) holds no ';' or '\';
    ondelete=/onupdate= is one of SQLAlchemy's own phrases. Every other literal is a plain name
    (letters, digits, '_', '.'), so an index expression ('lower(name)') or SQL in a constant stops.
    #>
    param([string]$Text, [string]$Masked, [object[]]$Strings, [int]$From, [int]$To)
    $sqlCall = '(?:sa|sqlalchemy)\s*\.\s*(?:text|literal_column|CheckConstraint|Computed|DDL)\s*\(\s*'
    $code = $Masked.Substring($From, $To - $From)
    $bare = [regex]::Match($code, $sqlCall + '(?=\S)(?![rRuUbB]{0,2}["''])')
    if ($bare.Success) { return "SQL bir dize değil: $($bare.Value.Trim())" }
    foreach ($s in @($Strings | Where-Object { $_.Start -ge $From -and $_.Start -lt $To })) {
        $content = $Text.Substring($s.Quote + $s.Width, $s.End - $s.Quote - $s.Width)
        $before = $Masked.Substring($From, $s.Start - $From)
        $rest = [Math]::Min($To, $s.End + $s.Width)
        $alone = ($Masked.Substring($rest, $To - $rest) -match '^\s*[,)]')
        if ($before -cmatch ($sqlCall + '$')) {
            if (-not $alone) { return "SQL tek bir dize değil: $content" }
            if ($content -cnotmatch '^(?:[0-9]+(?:\.[0-9]+)?|''[^'';\\\r\n]{0,64}''|now\(\)|NOW\(\)|CURRENT_TIMESTAMP|current_timestamp)$') { return "SQL beyaz listede değil: $content" }
            continue
        }
        if ($alone -and $before -cmatch '(?<![\w.])(?:comment|server_default)\s*=\s*$') {
            if ($content -cmatch '[;\\]') { return "dizgede ';' ya da '\': $content" }
            continue
        }
        if ($alone -and $before -cmatch '(?<![\w.])(?:ondelete|onupdate)\s*=\s*$' -and $content -cmatch '^(?:CASCADE|RESTRICT|SET NULL|SET DEFAULT|NO ACTION)$') { continue }
        if ($content -cnotmatch '^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$') { return "dizge bir ad değil (beyaz listede değil): $content" }
    }
    return ""
}

function Get-TeamTopLevelAssignCount {
    <# How many '=' of masked statement code assign (outside brackets; not ==, !=, <=, >=). #>
    param([AllowEmptyString()][string]$Code = "")
    $n = 0
    $depth = 0
    for ($i = 0; $i -lt $Code.Length; $i++) {
        $c = $Code[$i]
        if ("([{".IndexOf($c) -ge 0) { $depth++ }
        elseif (")]}".IndexOf($c) -ge 0) { if ($depth -gt 0) { $depth-- } }
        elseif ($c -eq '=' -and $depth -eq 0) {
            $before = if ($i -gt 0) { $Code[$i - 1] } else { ' ' }
            $after = if ($i + 1 -lt $Code.Length) { $Code[$i + 1] } else { ' ' }
            if ("=!<>:".IndexOf($before) -lt 0 -and $after -ne '=') { $n++ }
        }
    }
    return $n
}

function Get-TeamCallPieces {
    <#
    The top-level arguments of a masked argument list, read one by one: Star when it is a * or
    ** spread (its names cannot be read), Keyword ("" for a positional one) and Value, trimmed.
    #>
    param([AllowEmptyString()][string]$Masked = "")
    $out = New-Object System.Collections.ArrayList
    foreach ($piece in @(Get-TeamPythonArguments -Masked $Masked)) {
        $text = $Masked.Substring($piece.Start, $piece.End - $piece.Start).Trim()
        $named = [regex]::Match($text, '^([A-Za-z_]\w*)\s*=(?!=)([\s\S]*)$')
        $keyword = ""
        $value = $text
        if ($named.Success) { $keyword = $named.Groups[1].Value; $value = $named.Groups[2].Value.Trim() }
        [void]$out.Add([pscustomobject]@{ Star = $text.StartsWith("*"); Keyword = $keyword; Value = $value })
    }
    return @($out.ToArray())
}

function Get-TeamAddColumnObjection {
    <#
    Why masked op.add_column(...) arguments may add a column the old colour cannot live with, or
    "" when the column is plainly nullable or defaulted. Read from the sa.Column call's OWN
    top-level keywords: nullable must be the bare literal True (or False beside a server_default),
    a server_default must not be None or sa.null(), primary_key must be absent or False. A spread,
    a column that is not a direct sa.Column(...) call, or any other value is not read: in doubt, no.
    #>
    param([AllowEmptyString()][string]$Masked = "")
    $column = $null
    $index = 0
    foreach ($p in @(Get-TeamCallPieces -Masked $Masked)) {
        if ($p.Star) { return "add_column içinde açılım (* / **)" }
        if ($p.Keyword -ceq "column" -or (-not $p.Keyword -and $index -eq 1)) { $column = $p.Value }
        elseif ($p.Keyword -and $p.Keyword -cnotmatch '^(table_name|schema)$') { return "add_column içinde tanınmayan anahtar: $($p.Keyword)" }
        if (-not $p.Keyword) { $index++ }
    }
    $call = if ($column) { [regex]::Match($column, '^(?:sa|sqlalchemy)\.Column\s*\(') } else { $null }
    if (-not $call -or -not $call.Success -or -not $column.EndsWith(")")) { return "add_column sütunu doğrudan bir sa.Column(...) değil" }
    $inner = $column.Substring($call.Length, $column.Length - $call.Length - 1)
    $depth = 0
    foreach ($ch in $inner.ToCharArray()) {
        if ("([{".IndexOf($ch) -ge 0) { $depth++ } elseif (")]}".IndexOf($ch) -ge 0) { $depth--; if ($depth -lt 0) { return "add_column sütunu doğrudan bir sa.Column(...) değil" } }
    }
    $nullable = ""
    $defaulted = $false
    foreach ($p in @(Get-TeamCallPieces -Masked $inner)) {
        if ($p.Star) { return "sa.Column içinde açılım (* / **)" }
        if ($p.Keyword -ceq "nullable") {
            if ($p.Value -cnotmatch '^(True|False)$') { return "nullable düz bir True/False değil: $($p.Value)" }
            $nullable = $p.Value
        }
        elseif ($p.Keyword -ceq "server_default") {
            $defaulted = ($p.Value -and $p.Value -cne "None" -and $p.Value -cnotmatch '^(?:sa|sqlalchemy)\s*\.\s*null\s*\(\s*\)$')
        }
        elseif ($p.Keyword -ceq "primary_key" -and $p.Value -cne "False") { return "add_column birincil anahtar" }
    }
    if ($nullable -ceq "True" -or $defaulted) { return "" }
    return "add_column ne nullable=True ne server_default"
}

function ConvertTo-TeamTableToken {
    <# A table argument as a comparable token: s:<literal> or n:<name>; "" when it is neither. #>
    param([AllowEmptyString()][string]$Raw = "")
    $t = $Raw.Trim()
    $literal = [regex]::Match($t, '^[rRuU]?("|'')([^"''\\]*)\1$')
    if ($literal.Success) { return "s:" + $literal.Groups[2].Value }
    if ($t -match '^[A-Za-z_]\w*$') { return "n:$t" }
    return ""
}

function Get-TeamTableArgument {
    <# The token (ConvertTo-TeamTableToken) of a call's argument by keyword, else by position. #>
    param([AllowEmptyString()][string]$Raw = "", [AllowEmptyString()][string]$Masked = "", [int]$Position, [string]$Keyword)
    $index = 0
    foreach ($piece in @(Get-TeamPythonArguments -Masked $Masked)) {
        $text = $Raw.Substring($piece.Start, $piece.End - $piece.Start)
        $named = [regex]::Match($text, '^\s*([A-Za-z_]\w*)\s*=(?!=)([\s\S]*)$')
        if ($named.Success) {
            if ($named.Groups[1].Value -ceq $Keyword) { return (ConvertTo-TeamTableToken -Raw $named.Groups[2].Value) }
            continue
        }
        if ($index -eq $Position) { return (ConvertTo-TeamTableToken -Raw $text) }
        $index++
    }
    return ""
}

function Get-TeamMigrationVerdict {
    <#
    .SYNOPSIS
        Whether one alembic version in the diff may go out without the owner: ExpandOnly, and
        Why when it may not.

    .DESCRIPTION
        Expand-only means both colours of a blue-green release can serve beside the new schema.
        It is an ALLOW-list of alembic calls (Proje Yöneticisi 2026-10-03, after three returns
        that each found one more SQL form a deny-list let through; the owner's rule, ADR-0214
        addendum 9: an irreversible migration is not released by itself). upgrade() may hold
        ONLY, each as a bare statement:
          * op.create_table(...), op.create_index(...) (not unique, no spread);
          * op.add_column(...) whose sa.Column has its OWN bare nullable=True or a server_default
            that is not None/sa.null() (no primary key, no spread: Get-TeamAddColumnObjection);
          * op.create_foreign_key(...) whose source table this upgrade creates;
          * pass and a docstring.
        Their arguments may call only schema builders (Get-TeamCallObjection) and every string
        literal is a plain name or white-listed SQL (Get-TeamStringObjection); code outside
        strings and comments is ASCII only. The module
        around it may hold only imports, constant assignments, docstrings and the two defs;
        downgrade() is not read (it drops what the upgrade added). EVERYTHING else is not
        expand-only: op.execute (even 'SELECT 1'), any raw SQL, any other op, an ORM write, a
        helper, a loop, a variable in upgrade(), an f-string, an unrecognised import of op/sa,
        a star import, a ':=' anywhere, a module-level assignment to op/sa/upgrade/downgrade or
        to more than one target.
        Only an ADDED file is judged (status 'A'); a changed, deleted or unreadable one stops.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [string]$Status = "A", [AllowNull()][AllowEmptyString()][string]$Text = "")
    $verdict = { param([bool]$Ok, [string]$Why) [pscustomobject]@{ Path = $Path; ExpandOnly = $Ok; Why = $Why } }
    if ($Status -ne "A") { return (& $verdict $false "var olan bir göç dosyası değişti ya da silindi (git $Status)") }
    if ([string]::IsNullOrWhiteSpace($Text)) { return (& $verdict $false "göç okunamadı") }
    $mask = Get-TeamPythonMask -Text $Text
    if (-not $mask.Closed) { return (& $verdict $false "göç okunamadı (kapanmayan dize)") }
    $masked = $mask.Masked
    # Python reads non-ASCII names (fullwidth 'ｏｓ' is 'os'): code outside strings and comments is ASCII only.
    $foreign = [regex]::Match($masked, '[^\x00-\x7F]')
    if ($foreign.Success) { return (& $verdict $false ("göç kodunda ASCII olmayan karakter (U+{0:X4})" -f [int][char]$foreign.Value)) }
    # A walrus binds a name anywhere, op and sa among them: not read.
    if ($masked.Contains(":=")) { return (& $verdict $false "':=' ile bir ad bağlanıyor") }
    $docstring = '^(?:[rRuU]{0,2}("""|''''''|"|'')x*\1\s*)+$'
    # The names the allowed calls go through must be what they seem: op is alembic's, sa is SQLAlchemy.
    $bindings = @('^import\s+sqlalchemy(?:\s+as\s+sa)?$', '^from\s+alembic\s+import\s+\(?\s*(?:op|context)(?:\s*,\s*(?:op|context))*\s*,?\s*\)?$',
        '^from\s+sqlalchemy\.dialects\s+import\s+postgresql$')
    $calls = New-Object System.Collections.ArrayList
    $current = ""
    $sawUpgrade = $false
    foreach ($s in @(Get-TeamPythonStatements -Masked $masked)) {
        $code = $masked.Substring($s.Start, $s.End - $s.Start)
        $raw = $Text.Substring($s.Start, $s.End - $s.Start)
        $first = ($raw -split "`n")[0].Trim()
        if (-not $s.Indented) {
            $current = ""
            $def = [regex]::Match($code, '^def\s+(upgrade|downgrade)\s*\(\s*\)\s*(?:->\s*None\s*)?:$')
            if ($def.Success) {
                $current = $def.Groups[1].Value
                if ($current -eq "upgrade") { $sawUpgrade = $true }
                continue
            }
        }
        elseif ($current -eq "downgrade") { continue }
        $inside = @($mask.FStrings | Where-Object { $_ -ge $s.Start -and $_ -lt $s.End })
        if (@($inside).Count -gt 0) { return (& $verdict $false "f-dizesi okunamadı: $first") }
        if ($code -cmatch $docstring) { continue }
        if (-not $s.Indented) {
            if ($code -cmatch '^(import|from)\s') {
                if ($code.Contains("*")) { return (& $verdict $false "yıldızlı içe aktarma bilinmeyen adlar bağlıyor: $first") }
                if ($code -cmatch '\b(op|sa|sqlalchemy|postgresql)\b' -and @($bindings | Where-Object { $code -cmatch $_ }).Count -eq 0) {
                    return (& $verdict $false "op/sa tanınmayan biçimde bağlanıyor: $first")
                }
                continue
            }
            $assign = [regex]::Match($code, '^([A-Za-z_]\w*)\s*(?::[^=]*)?=(?!=)')
            if ($assign.Success) {
                if ($assign.Groups[1].Value -cmatch '^(op|sa|sqlalchemy|postgresql|upgrade|downgrade)$') { return (& $verdict $false "op/sa/upgrade/downgrade yeniden bağlanıyor: $first") }
                # One target only: in 'X = op = 1' the later targets are bound too.
                if ((Get-TeamTopLevelAssignCount -Code $code) -ne 1) { return (& $verdict $false "birden çok hedefe atama: $first") }
                $why = Get-TeamCallObjection -Code $code
                if (-not $why) { $why = Get-TeamStringObjection -Text $Text -Masked $masked -Strings $mask.Strings -From $s.Start -To $s.End }
                if ($why) { return (& $verdict $false "$why ($first)") }
                continue
            }
            return (& $verdict $false "modül düzeyinde izin listesinde olmayan kod: $first")
        }
        if ($current -ne "upgrade") { return (& $verdict $false "izin listesinde olmayan kod: $first") }
        if ($code -ceq "pass") { continue }
        $call = [regex]::Match($code, '^op\.(create_table|create_index|add_column|create_foreign_key)\s*\(')
        $open = $call.Index + $call.Length - 1
        $close = -1
        if ($call.Success) {
            $depth = 0
            for ($i = $open; $i -lt $code.Length; $i++) {
                if ($code[$i] -eq '(') { $depth++ }
                elseif ($code[$i] -eq ')') { $depth--; if ($depth -eq 0) { $close = $i; break } }
            }
        }
        # Not a bare allowed call: in doubt, not expand-only.
        if (-not $call.Success -or $close -ne $code.Length - 1) { return (& $verdict $false "upgrade() içinde izin listesinde olmayan: $first") }
        $arguments = $code.Substring($open + 1, $close - $open - 1)
        $why = Get-TeamCallObjection -Code $arguments
        if (-not $why) { $why = Get-TeamStringObjection -Text $Text -Masked $masked -Strings $mask.Strings -From $s.Start -To $s.End }
        if ($why) { return (& $verdict $false "$why ($first)") }
        [void]$calls.Add([pscustomobject]@{ Name = $call.Groups[1].Value; Masked = $arguments; Raw = $raw.Substring($open + 1, $close - $open - 1); First = $first })
    }
    if (-not $sawUpgrade) { return (& $verdict $false "göçte upgrade() okunamadı") }
    $created = @($calls.ToArray() | Where-Object { $_.Name -eq "create_table" } |
        ForEach-Object { Get-TeamTableArgument -Raw $_.Raw -Masked $_.Masked -Position 0 -Keyword "table_name" } | Where-Object { $_ })
    foreach ($c in $calls.ToArray()) {
        if ($c.Name -eq "add_column") {
            $why = Get-TeamAddColumnObjection -Masked $c.Masked
            if ($why) { return (& $verdict $false "${why}: $($c.First)") }
        }
        if ($c.Name -eq "create_index") {
            foreach ($p in @(Get-TeamCallPieces -Masked $c.Masked)) {
                if ($p.Star) { return (& $verdict $false "create_index içinde açılım (* / **): $($c.First)") }
                if ($p.Keyword -ceq "unique" -and $p.Value -cne "False") { return (& $verdict $false "create_index unique (eski rengin satırlarını reddedebilir): $($c.First)") }
            }
        }
        if ($c.Name -eq "create_foreign_key") {
            $source = Get-TeamTableArgument -Raw $c.Raw -Masked $c.Masked -Position 1 -Keyword "source_table"
            if (-not $source -or $created -notcontains $source) { return (& $verdict $false "create_foreign_key bu göçün yaratmadığı bir tabloya: $($c.First)") }
        }
    }
    return (& $verdict $true "")
}

# ---------------------------------------------------------------------------- the host

function Get-TeamHostProbeCommand {
    <#
    .SYNOPSIS
        The ONE look at the host, as a bash command for ssh: it reads and prints key=value lines
        and changes nothing. The seconds to the maintenance window are counted on the HOST's
        clock (one clock for one decision).
    #>
    param(
        [string]$HostBase = "/opt/pagentos",
        [string]$RecoveryRoot = "/opt/pagentos-recovery",
        [string]$EdgeDir = "/mnt/pagentos-data/edge",
        [string]$HealthUrl = "http://127.0.0.1:8001/v1/system/health"
    )
    foreach ($p in @($HostBase, $RecoveryRoot, $EdgeDir)) {
        if ($p -cnotmatch '^/[A-Za-z0-9_./-]+$') { throw "unsafe remote path '$p'" }
    }
    if ($HealthUrl -cnotmatch '^http://[A-Za-z0-9.:-]+/[A-Za-z0-9_./-]*$') { throw "unsafe health url '$HealthUrl'" }
    $lines = @(
        "b='$HostBase'; r='$RecoveryRoot'; e='$EdgeDir'",
        'm() { if [ -f "$1" ]; then cat "$1" | tr -d "[:space:]"; fi; }',
        'echo "release=$(m "$b/RELEASE")"',
        'echo "app_release=$(m "$b/app/RELEASE")"',
        'echo "pin=$(m "$r/APPROVED_SHA")"',
        'echo "lkg=$(m "$b/LAST_KNOWN_GOOD")"',
        'echo "colour=$(m "$e/active.txt" | tr "[:upper:]" "[:lower:]")"',
        'if [ -e "$b/MAINTENANCE_MARKER" ]; then echo maintenance_marker=yes; else echo maintenance_marker=no; fi',
        'n=$(systemctl show pagentos-maintenance-window.timer -p NextElapseUSecRealtime --value 2>/dev/null || true)',
        'if [ -z "$n" ] || [ "$n" = "n/a" ]; then echo maintenance_in_s=none; elif t=$(date -d "$n" +%s 2>/dev/null); then echo "maintenance_in_s=$((t - $(date +%s)))"; else echo maintenance_in_s=unknown; fi',
        "echo `"health_b64=`$(curl -fsS --max-time 10 '$HealthUrl' 2>/dev/null | base64 -w0)`"",
        'echo "reconcile=$(journalctl -u pagentos-bluegreen-reconcile.service -n 200 --no-pager -o cat 2>/dev/null | grep -E "^RECONCILE" | tail -n 1)"'
    )
    return ($lines -join "; ")
}

function Read-TeamHostProbe {
    <#
    .SYNOPSIS
        What the probe printed. Ok only when ssh exited 0 AND every key is there: a probe that
        said half is not a probe. Health is the top-level status and release.version of the body.
    #>
    param([AllowEmptyString()][string]$Text = "", [int]$ExitCode = 0)
    $values = @{}
    foreach ($line in @(([string]$Text) -split "`r?`n")) {
        $at = $line.IndexOf("=")
        if ($at -lt 1) { continue }
        $key = $line.Substring(0, $at).Trim()
        if ($script:TeamReleaseProbeKeys -contains $key -and -not $values.ContainsKey($key)) { $values[$key] = $line.Substring($at + 1).Trim() }
    }
    $missing = @($script:TeamReleaseProbeKeys | Where-Object { -not $values.ContainsKey($_) })
    $value = { param([string]$Key, [string]$Default = "") if ($values.ContainsKey($Key)) { [string]$values[$Key] } else { $Default } }
    $status = ""
    $served = ""
    $encoded = & $value "health_b64"
    if ($encoded) {
        try {
            $document = ConvertFrom-Json -InputObject ([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)))
            $status = [string](Get-TeamProperty -InputObject $document -Name "status" -Default "")
            $served = [string](Get-TeamProperty -InputObject (Get-TeamProperty -InputObject $document -Name "release" -Default $null) -Name "version" -Default "")
        }
        catch { $status = "" }
    }
    return [pscustomobject]@{
        Ok                   = ($ExitCode -eq 0 -and @($missing).Count -eq 0)
        ExitCode             = $ExitCode
        Missing              = @($missing)
        Release              = (& $value "release")
        AppRelease           = (& $value "app_release")
        Pin                  = (& $value "pin")
        Lkg                  = (& $value "lkg")
        Colour               = (& $value "colour")
        MaintenanceMarker    = ((& $value "maintenance_marker" "yes") -ne "no")
        MaintenanceInSeconds = (& $value "maintenance_in_s" "unknown")
        HealthStatus         = $status
        HealthRelease        = $served
        Reconcile            = (& $value "reconcile")
    }
}

# ---------------------------------------------------------------------------- the decision

function Get-TeamReleaseDecision {
    <#
    .SYNOPSIS
        'release' or 'stop', and every reason that stops it (Reasons: Code + Text; Reason: the
        texts in one line).

    .DESCRIPTION
        -Facts carries: Sha (what would go), MainTip (origin/main's tip), Gate (Found, Pass, Why:
        the integrate step's record and log for that sha), Blocked (the block marker's name, or
        ""), Lock (Get-TeamLockDecision's answer), Host (Read-TeamHostProbe's answer) and Diff
        (Readable, Why, Files, Migrations: the verdicts of Get-TeamMigrationVerdict).

        -LocalOnly judges what is known before the host is read (the sha, the gate, the marker,
        the lock): a release whose own evidence is missing never looks at production.
    #>
    param([Parameter(Mandatory = $true)]$Facts, [switch]$LocalOnly)
    $reasons = New-Object System.Collections.ArrayList
    $notes = New-Object System.Collections.ArrayList
    $add ={ param([string]$Code, [string]$Text) [void]$reasons.Add([pscustomobject]@{ Code = $Code; Text = $Text }) }

    $sha = [string]$Facts.Sha
    $tip = [string]$Facts.MainTip
    if ($sha -cnotmatch '^[0-9a-f]{40}$' -or $sha -ne $tip) {
        & $add "not_tip" $(if ($tip) { "iş $sha, origin/main'in ucu $tip değil" } else { "origin/main okunamadı" })
    }
    $gate = $Facts.Gate
    if ($null -eq $gate -or -not [bool]$gate.Found) {
        & $add "no_gate" ("kapı kaydı yok: " + $(if ($null -ne $gate -and $gate.Why) { [string]$gate.Why } else { "$sha için yeşil kapı kaydı bulunamadı" }))
    }
    elseif (-not [bool]$gate.Pass) { & $add "gate_not_pass" "kapı PASS demiyor: $([string]$gate.Why)" }
    if ([string]$Facts.Blocked) {
        & $add "blocked" "önceki otomatik yayın geri alındı ya da doğrulanamadı ($([string]$Facts.Blocked)); lead kaldırana kadar otomatik yayın durur"
    }
    $lock = $Facts.Lock
    if ($null -ne $lock -and -not [bool]$lock.MayRun) { & $add "lock" "kilit $([string]$lock.Holder) makinesinde ($([string]$lock.Since)); başka bir yayın ya da döngü sürüyor" }

    if (-not $LocalOnly) {
        $probe = $Facts.Host
        if ($null -eq $probe -or -not [bool]$probe.Ok) {
            $said = if ($null -eq $probe) { "okunmadı" } else { "ssh çıkış kodu $($probe.ExitCode)" + $(if (@($probe.Missing).Count -gt 0) { ", eksik: " + (@($probe.Missing) -join ", ") } else { "" }) }
            & $add "host_unread" "üretim okunamadı ($said)"
        }
        else {
            if ([string]$probe.HealthStatus -ne "ok") { & $add "health" "üretimde sağlık ok değil ('$([string]$probe.HealthStatus)'); yayından önce ok olmalı" }
            if ([bool]$probe.MaintenanceMarker) { & $add "maintenance_marker" "sunucuda bakım işareti var (MAINTENANCE_MARKER): bakım sürüyor" }
            $window = [string]$probe.MaintenanceInSeconds
            $seconds = 0
            if ($window -ne "none") {
                if ([int]::TryParse($window, [ref]$seconds)) {
                    if ($seconds -ge 0 -and $seconds -le $script:TeamReleaseMaintenanceSeconds) {
                        & $add "maintenance_window" "bakım penceresi $([Math]::Ceiling($seconds / 60)) dk içinde (30 dk kuralı)"
                    }
                }
                else { & $add "maintenance_unknown" "bakım penceresinin zamanı okunamadı ('$window')" }
            }
        }
        $diff = $Facts.Diff
        if ($null -eq $diff -or -not [bool]$diff.Readable) {
            & $add "diff_unreadable" ("yayındakiyle fark okunamadı: " + $(if ($null -ne $diff) { [string]$diff.Why } else { "okunmadı" }))
        }
        else {
            $files = @(@($diff.Files) | ForEach-Object { ([string]$_) -replace '\\', '/' })
            # Danışman 2026-10-04: a commit with a migration is never released by itself - the
            # analyzer below is information, not a decision (masking Python with text kept leaking).
            $migrationFiles = @($files | Where-Object { Test-TeamMigrationChange -Path $_ })
            if (@($migrationFiles).Count -gt 0) {
                & $add "migration" ("göç içeren commit (" + ($migrationFiles -join ", ") + "): otomatik yayın göç yayınlamaz, Danışman yayınlar")
            }
            foreach ($verdict in @(@($diff.Migrations) | Where-Object { $null -ne $_ })) {
                $said = if ([bool]$verdict.ExpandOnly) { "genişletme" } else { "genişletme değil ($([string]$verdict.Why))" }
                [void]$notes.Add("bilgi (karara girmez) - göç çözümleyicisi: $([string]$verdict.Path): $said")
            }
            foreach ($file in $script:TeamReleaseHostFiles) {
                if ($files -contains $file) { & $add "compose" "$file değişti: sunucunun compose'u imajın ötesinde değişiyor" }
            }
            $edge = @($files | Where-Object { $path = $_; @($script:TeamReleaseHostPrefixes | Where-Object { $path.StartsWith($_) }).Count -gt 0 })
            if (@($edge).Count -gt 0) { & $add "edge" ("infra/docker/edge değişti: " + (@($edge) -join ", ")) }
        }
    }
    $all = @($reasons.ToArray())
    return [pscustomobject]@{
        Action  = $(if (@($all).Count -eq 0) { "release" } else { "stop" })
        Reasons = $all
        Reason  = ((@($all) | ForEach-Object { $_.Text }) -join "; ")
        Notes   = @($notes.ToArray())
    }
}

function Test-TeamReleaseVerified {
    <#
    .SYNOPSIS
        After the release and the pin: RELEASE == sha, APPROVED_SHA == sha, the reconcile's
        last line is RECONCILE OK for THAT sha, and health through the edge is ok serving it.
    #>
    param([Parameter(Mandatory = $true)]$Probe, [Parameter(Mandatory = $true)][string]$Sha)
    $problems = New-Object System.Collections.ArrayList
    if (-not [bool]$Probe.Ok) { [void]$problems.Add("üretim okunamadı (ssh çıkış kodu $($Probe.ExitCode))") }
    else {
        if ([string]$Probe.Release -ne $Sha) { [void]$problems.Add("RELEASE '$($Probe.Release)', beklenen $Sha") }
        if ([string]$Probe.Pin -ne $Sha) { [void]$problems.Add("APPROVED_SHA '$($Probe.Pin)', beklenen $Sha") }
        if ([string]$Probe.Reconcile -notmatch ('^RECONCILE OK: .*\(release ' + [regex]::Escape($Sha) + '\)')) {
            [void]$problems.Add("uzlaştırmanın son satırı $Sha için RECONCILE OK değil: '$($Probe.Reconcile)'")
        }
        if ([string]$Probe.HealthStatus -ne "ok" -or [string]$Probe.HealthRelease -ne $Sha) {
            [void]$problems.Add("edge üzerinden sağlık '$($Probe.HealthStatus)', sunulan sürüm '$($Probe.HealthRelease)'; beklenen ok / $Sha")
        }
    }
    $all = @($problems.ToArray())
    return [pscustomobject]@{ Ok = (@($all).Count -eq 0); Problems = $all }
}

function Get-TeamStagingOutcome {
    <#
    .SYNOPSIS
        After a verified release, staging follows (staging-follows-release): did deploy.ps1
        reach the released sha ('STAGING DEPLOYED: <sha>') and did seed.ps1 make a valid
        session ('STAGING SEEDED')? Either missing is a risk, never a failed release -
        production is already promoted. $null for Seed: it was not run.
    #>
    param([Parameter(Mandatory = $true)]$Deploy, $Seed = $null, [Parameter(Mandatory = $true)][string]$Sha)
    $reached = ""
    $match = [regex]::Match([string]$Deploy.StdOut, 'STAGING DEPLOYED: ([0-9a-f]{40})')
    if ($match.Success) { $reached = $match.Groups[1].Value }
    $problem = ""
    if ([int]$Deploy.ExitCode -ne 0 -or $reached -ne $Sha) {
        $problem = "staging $Sha sürümüne güncellenemedi: deploy.ps1 çıkış $($Deploy.ExitCode)" + $(if ($reached) { ", ulaştığı $reached" } else { "" })
    }
    elseif ($null -eq $Seed -or [int]$Seed.ExitCode -ne 0 -or [string]$Seed.StdOut -notmatch 'STAGING SEEDED') {
        $code = if ($null -eq $Seed) { "çalışmadı" } else { "çıkış $($Seed.ExitCode)" }
        $problem = "staging $Sha sürümünde ama oturumu yenilenemedi: seed.ps1 $code"
    }
    return [pscustomobject]@{ Ok = (-not $problem); Reached = $reached; Problem = $problem }
}

# ---------------------------------------------------------------------------- the repository and the records

function Find-TeamReleaseGate {
    <#
    .SYNOPSIS
        The integrate step's evidence for a sha on main: the newest green record
        (team/reports/<cycle>/gate-<n>.json) whose `main` IS the sha, and its log beside it,
        which must say QUALITY GATE: PASS (Read-TeamGateLog, the integrate step's own reader).
        No record for that sha, or no log: Found is false.
        A 'gate A + rerun B' record (one with `rerun_of`, card gate-rerun-failed-steps) is evidence
        only through its chain (Test-TeamGateRerunChain: A's FAIL log red only in the steps B
        reran green, B descends from A); Log is then the rerun's log. That needs git: without
        -RepoRoot, or without scripts/lib/TeamGateRerun.ps1, the record's own log (A's, red) is
        read as before and the record is refused.
    #>
    param([Parameter(Mandatory = $true)][string]$ReportsRoot, [Parameter(Mandatory = $true)][string]$Sha, [string]$RepoRoot = "")
    $none = { param([string]$Why) [pscustomobject]@{ Found = $false; Pass = $false; Why = $Why; Directory = ""; CycleId = ""; Log = "" } }
    if (-not (Test-Path -LiteralPath $ReportsRoot)) { return (& $none "team/reports yok") }
    $best = $null
    foreach ($folder in @(Get-ChildItem -LiteralPath $ReportsRoot -Directory)) {
        foreach ($file in @(Get-ChildItem -LiteralPath $folder.FullName -Filter "gate-*.json" -File)) {
            if ($file.Name -notmatch '^gate-(\d+)\.json$') { continue }
            try { $record = Read-TeamJson -Path $file.FullName } catch { continue }
            if ([string](Get-TeamProperty -InputObject $record -Name "result" -Default "") -ne "green") { continue }
            if ([string](Get-TeamProperty -InputObject $record -Name "main" -Default "") -ne $Sha) { continue }
            $at = [string](Get-TeamProperty -InputObject $record -Name "at" -Default "")
            if ($null -eq $best -or $at -gt $best.At) { $best = [pscustomobject]@{ Record = $record; Folder = $folder; At = $at } }
        }
    }
    if ($null -eq $best) { return (& $none "main $Sha için yeşil kapı kaydı (team/reports/<döngü>/gate-<n>.json) bulunamadı") }
    $rerunOf = Get-TeamProperty -InputObject $best.Record -Name "rerun_of" -Default $null
    if ($null -ne $rerunOf -and $RepoRoot -and (Get-Command -Name Test-TeamGateRerunChain -ErrorAction SilentlyContinue)) {
        $chain = Test-TeamGateRerunChain -RepoRoot $RepoRoot -Directory $best.Folder.FullName -Record $best.Record
        $rerunLog = Split-Path -Leaf ([string](Get-TeamProperty -InputObject $best.Record -Name "rerun_log" -Default ""))
        return [pscustomobject]@{
            Found = $true; Pass = [bool]$chain.Ok; Why = $(if ($chain.Ok) { "" } else { "zincirli kapı kaydı reddedildi: $($chain.Why)" })
            Directory = $best.Folder.FullName; CycleId = $best.Folder.Name; Log = $(if ($rerunLog) { "team/reports/$($best.Folder.Name)/$rerunLog" } else { "" })
        }
    }
    $logName = Split-Path -Leaf ([string](Get-TeamProperty -InputObject $best.Record -Name "log" -Default ""))
    $relative = "team/reports/$($best.Folder.Name)/$logName"
    $logPath = if ($logName) { Join-Path $best.Folder.FullName $logName } else { "" }
    if (-not $logPath -or -not (Test-Path -LiteralPath $logPath)) {
        $missing = & $none "kapı günlüğü yok ($relative)"
        $missing.Directory = $best.Folder.FullName
        $missing.CycleId = $best.Folder.Name
        return $missing
    }
    $gate = Read-TeamGateLog -Text ([System.IO.File]::ReadAllText($logPath, [System.Text.Encoding]::UTF8)) -ExitCode 0
    return [pscustomobject]@{
        Found = $true; Pass = [bool]$gate.Green; Why = $(if ($gate.Green) { "" } else { "$relative ($($gate.Why))" })
        Directory = $best.Folder.FullName; CycleId = $best.Folder.Name; Log = $relative
    }
}

function Get-TeamReleaseDiff {
    <#
    .SYNOPSIS
        What changes between what production serves (-From, the host's RELEASE) and -To: the
        files, and a verdict for every alembic version among them. Not readable when -From is
        not a sha this repository has.
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [string]$From = "", [Parameter(Mandatory = $true)][string]$To)
    $unread = { param([string]$Why) [pscustomobject]@{ Readable = $false; Why = $Why; Files = @(); Migrations = @() } }
    if ($From -cnotmatch '^[0-9a-f]{40}$') { return (& $unread "yayındaki sürüm okunamadı ('$From')") }
    if (-not (Get-TeamRevision -RepoRoot $RepoRoot -Revision $From)) { return (& $unread "yayındaki sürüm $From bu depoda yok") }
    $listed = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("diff", "--name-status", "--no-renames", $From, $To)
    if (-not $listed.Success) { return (& $unread "git diff başarısız: $(($listed.StdErr -replace '\s+', ' ').Trim())") }
    $files = New-Object System.Collections.ArrayList
    $migrations = New-Object System.Collections.ArrayList
    foreach ($line in @($listed.StdOut -split "`r?`n" | Where-Object { $_.Trim() })) {
        $parts = $line.Split("`t")
        if (@($parts).Count -lt 2) { return (& $unread "git diff satırı okunamadı: '$line'") }
        $status = $parts[0].Trim()
        $path = $parts[@($parts).Count - 1].Trim()
        [void]$files.Add($path)
        if (-not (Test-TeamMigrationPath -Path $path)) { continue }
        $text = $null
        if ($status -ne "D") {
            $shown = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("show", "${To}:$path")
            if ($shown.Success) { $text = $shown.StdOut }
        }
        [void]$migrations.Add((Get-TeamMigrationVerdict -Path $path -Status $status -Text $text))
    }
    return [pscustomobject]@{ Readable = $true; Why = ""; Files = @($files.ToArray()); Migrations = @($migrations.ToArray()) }
}

function Get-TeamReleaseNextNumber {
    <# One past the highest release-<n>.* in a cycle's report folder. #>
    param([Parameter(Mandatory = $true)][string]$Directory)
    $highest = 0
    if (Test-Path -LiteralPath $Directory) {
        foreach ($file in @(Get-ChildItem -LiteralPath $Directory -Filter "release-*" -File)) {
            if ($file.Name -match '^release-(\d+)\.') { $highest = [Math]::Max($highest, [int]$Matches[1]) }
        }
    }
    return ($highest + 1)
}

function New-TeamReleaseReport {
    <#
    .SYNOPSIS
        The step's report, in Turkish: the section 'Yayın' names what, the sha, the colour and
        the last known good - or why it stopped and that it waits for the owner.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Machine,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$Result,
        [string]$Sha = "",
        [string]$Colour = "",
        [string]$LastKnownGood = "",
        [string[]]$Tasks = @(),
        [string[]]$Lines = @()
    )
    $out = New-Object System.Collections.ArrayList
    [void]$out.Add("# Otomatik yayın raporu")
    [void]$out.Add("")
    [void]$out.Add("Makine: $Machine · başladı $StartedAt · bitti $(Get-TeamTimestamp)")
    [void]$out.Add("")
    [void]$out.Add("Kural: kapıdan geçip main'e giren roadmap işi sahibe sorulmadan yayınlanır (ADR-0214 ek 9); kuralın saydığı durumlarda durur ve sahibe bırakır.")
    [void]$out.Add("")
    [void]$out.Add("## Yayın")
    [void]$out.Add("")
    [void]$out.Add("- sonuç: $Result")
    [void]$out.Add("- ne: " + $(if (@($Tasks).Count -gt 0) { $Tasks -join ", " } else { "yok" }))
    [void]$out.Add("- sha: " + $(if ($Sha) { $Sha } else { "-" }))
    [void]$out.Add("- renk: " + $(if ($Colour) { $Colour } else { "-" }))
    [void]$out.Add("- son bilinen iyi (LKG): " + $(if ($LastKnownGood) { $LastKnownGood } else { "-" }))
    foreach ($line in @($Lines)) { if ($line) { [void]$out.Add("- $line") } }
    [void]$out.Add("")
    return (($out.ToArray()) -join "`n")
}
