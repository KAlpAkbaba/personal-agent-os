<#
.SYNOPSIS
    The automatic release of the agent team (scripts/team/release.ps1, ADR-0214 addendum 9):
    gated roadmap work that reached main is released blue/green, pinned and verified - or the
    step stops, says why, and leaves it to the owner.

.DESCRIPTION
    Two halves, as in team-integrate.tests.ps1.

    The decisions (scripts/lib/TeamRelease.ps1) are driven as functions: which migration is
    expand-only, what the host probe says, when a release stops, what the verification wants.

    The step itself is run for real, in a git repository made for the test (with a bare
    "origin"), with scripts/tests/lib/fake-release.ps1 in place of BOTH the release script and
    ssh.exe. The host is a folder; the fake writes every call it gets into it. Nothing here
    reaches a host, and this repository's own branches, worktrees and team/ files are not
    written to.

    Run: powershell -NoProfile -File scripts\tests\team-release.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamIntegrate.ps1")
$releaseLib = Join-Path $repoRoot "scripts\lib\TeamRelease.ps1"
$releaseScript = Join-Path $repoRoot "scripts\team\release.ps1"
$fakeRelease = Join-Path $repoRoot "scripts\tests\lib\fake-release.ps1"
if (Test-Path -LiteralPath $releaseLib) { . $releaseLib }

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

$shaA = "a" * 40
$shaB = "b" * 40
$shaC = "c" * 40

function New-Probe {
    param(
        [string]$Release = $shaA, [string]$Pin = $shaA, [string]$Lkg = $shaC, [string]$Colour = "blue",
        [string]$Marker = "no", [string]$InSeconds = "none", [string]$Health = "ok", [string]$HealthRelease = $shaA,
        [string]$Reconcile = "", [int]$ExitCode = 0
    )
    if (-not $Reconcile) { $Reconcile = "RECONCILE OK: api-$Colour is canonical (release $Release); markers, upstreams and containers agree" }
    $body = ConvertTo-Json -Compress -InputObject ([ordered]@{ status = $Health; release = [ordered]@{ version = $HealthRelease } })
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($body))
    $text = @("release=$Release", "app_release=$Release", "pin=$Pin", "lkg=$Lkg", "colour=$Colour", "maintenance_marker=$Marker",
        "maintenance_in_s=$InSeconds", "health_b64=$b64", "reconcile=$Reconcile") -join "`n"
    return (Read-TeamHostProbe -Text $text -ExitCode $ExitCode)
}

function New-Facts {
    <# Every fact of a release that may go: a test changes one. #>
    param([hashtable]$Change = @{})
    $facts = [ordered]@{
        Sha     = $shaB
        MainTip = $shaB
        Gate    = [pscustomobject]@{ Found = $true; Pass = $true; Why = "" }
        Blocked = ""
        Lock    = [pscustomobject]@{ MayRun = $true; Kind = "free"; Holder = ""; Since = "" }
        Host    = (New-Probe)
        Diff    = [pscustomobject]@{ Readable = $true; Why = ""; Files = @(); Migrations = @() }
    }
    foreach ($name in @($Change.Keys)) { $facts[$name] = $Change[$name] }
    return [pscustomobject]$facts
}

function Get-StopCodes {
    param($Decision)
    return ((@($Decision.Reasons) | ForEach-Object { [string]$_.Code }) -join ",")
}

$expandOnly = @'
"""Expand-only: a nullable column, a table and an index.

The downgrade drops them again.
"""
import sqlalchemy as sa
from alembic import op

revision = "0066_expand"
down_revision = "0065_misheard_utterances"


def upgrade() -> None:
    op.add_column("notes", sa.Column("colour", sa.String(length=16), nullable=True))
    op.create_table("labels", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("name", sa.String(64), nullable=False))
    op.create_index("ix_labels_name", "labels", ["name"])


def downgrade() -> None:
    op.drop_index("ix_labels_name", table_name="labels")
    op.drop_table("labels")
    op.drop_column("notes", "colour")
'@

$dropColumn = @'
import sqlalchemy as sa
from alembic import op

revision = "0066_contract"
down_revision = "0065_misheard_utterances"


def upgrade() -> None:
    op.drop_column("notes", "colour")


def downgrade() -> None:
    op.add_column("notes", sa.Column("colour", sa.String(length=16), nullable=True))
'@

# ============================================================================ the decisions

Write-Host ""
Write-Host "which migration is expand-only (read conservatively: an unreadable one stops)"

Test-Case "add_column nullable, create_table and create_index are expand-only; what the DOWNGRADE drops is not read" {
    $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $expandOnly
    Assert-True -Condition $verdict.ExpandOnly -Because "an expand-only migration: $($verdict.Why)"
}

Test-Case "drop_column, drop_table, alter_column with a type or nullable change, a rename, and DELETE/UPDATE in op.execute are not" {
    $cases = @{
        "drop_column"   = 'op.drop_column("notes", "colour")'
        "drop_table"    = 'op.drop_table("notes")'
        "batch drop"    = "with op.batch_alter_table(`"notes`") as batch:`n        batch.drop_column(`"colour`")"
        "alter type"    = "op.alter_column(`n        `"notes`", `"colour`",`n        type_=sa.String(32),`n    )"
        "alter null"    = 'op.alter_column("notes", "colour", existing_type=sa.String(16), nullable=False)'
        "rename column" = 'op.alter_column("notes", "colour", new_column_name="hue")'
        "rename table"  = 'op.rename_table("notes", "memos")'
        "delete"        = 'op.execute("DELETE FROM notes WHERE colour IS NULL")'
        "update"        = "op.execute(sa.text(`"update notes set colour = 'red'`"))"
        "not null add"  = 'op.add_column("notes", sa.Column("colour", sa.String(16), nullable=False))'
    }
    foreach ($name in @($cases.Keys)) {
        $text = "from alembic import op`nimport sqlalchemy as sa`n`n`ndef upgrade() -> None:`n    " + $cases[$name] + "`n`n`ndef downgrade() -> None:`n    pass`n"
        $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $text
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "$name is not expand-only"
        Assert-True -Condition ([bool]$verdict.Why) -Because "$name says why"
    }
}

Test-Case "an UPDATE inside a column NAME stays expand-only (a name is not SQL)" {
    $text = "from alembic import op`nimport sqlalchemy as sa`n`n`ndef upgrade() -> None:`n    op.add_column(`"notes`", sa.Column(`"updated_at`", sa.DateTime(), nullable=True))`n`n`ndef downgrade() -> None:`n    pass`n"
    $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $text
    Assert-True -Condition $verdict.ExpandOnly -Because "a nullable column named updated_at: $($verdict.Why)"
}

Test-Case "an unreadable migration, one without upgrade(), and a CHANGED or DELETED existing migration stop" {
    foreach ($text in @("", $null)) {
        $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $text
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "no text is not expand-only"
    }
    $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text "revision = 'x'`n"
    Assert-True -Condition (-not $verdict.ExpandOnly) -Because "no upgrade() cannot be read"
    foreach ($status in @("M", "D", "R100", "T")) {
        $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status $status -Text $expandOnly
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "status $status of an existing migration stops"
    }
}

function Get-UpgradeVerdict {
    param([string]$Body)
    $text = "from alembic import op`nimport os`nimport sqlalchemy as sa`n`n`ndef upgrade() -> None:`n    " + $Body + "`n`n`ndef downgrade() -> None:`n    pass`n"
    return (Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $text)
}

function Assert-NotExpandOnly {
    param([string[]]$Bodies, [string]$Because)
    foreach ($body in $Bodies) {
        $verdict = Get-UpgradeVerdict -Body $body
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "$Because - $body"
        Assert-True -Condition ([bool]$verdict.Why) -Because "it says why - $body"
    }
}

# Expand-only is an ALLOW-list of alembic calls (Proje Yöneticisi, 2026-10-03 21:00, after three
# returns that each found one more SQL form a deny-list let through): upgrade() may hold ONLY
# op.create_table, op.create_index, op.add_column (nullable=True or a server_default) and
# op.create_foreign_key on a table it creates. No raw SQL is safe any more.
Test-Case "allow-list: op.create_table alone is expand-only" {
    $verdict = Get-UpgradeVerdict -Body "op.create_table(`n        `"labels`",`n        sa.Column(`"id`", sa.Uuid(), primary_key=True),`n        sa.Column(`"name`", sa.String(64), nullable=False),`n    )"
    Assert-True -Condition $verdict.ExpandOnly -Because "a new table: $($verdict.Why)"
}

Test-Case "allow-list: op.create_index alone is expand-only" {
    $verdict = Get-UpgradeVerdict -Body 'op.create_index(op.f("ix_notes_colour"), "notes", ["colour"], unique=False)'
    Assert-True -Condition $verdict.ExpandOnly -Because "a new index: $($verdict.Why)"
}

Test-Case "allow-list: op.add_column with nullable=True is expand-only" {
    $verdict = Get-UpgradeVerdict -Body 'op.add_column("notes", sa.Column("colour", sa.String(length=16), nullable=True))'
    Assert-True -Condition $verdict.ExpandOnly -Because "a nullable column: $($verdict.Why)"
}

Test-Case "allow-list: op.add_column NOT NULL with a server_default is expand-only" {
    $verdict = Get-UpgradeVerdict -Body 'op.add_column("notes", sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()))'
    Assert-True -Condition $verdict.ExpandOnly -Because "a defaulted column: $($verdict.Why)"
}

Test-Case "allow-list: op.create_foreign_key on a table the same upgrade creates is expand-only" {
    $verdict = Get-UpgradeVerdict -Body "op.create_table(`"labels`", sa.Column(`"id`", sa.Uuid(), primary_key=True), sa.Column(`"note_id`", sa.Uuid(), nullable=True))`n    op.create_foreign_key(`"fk_labels_note`", `"labels`", `"notes`", [`"note_id`"], [`"id`"])"
    Assert-True -Condition $verdict.ExpandOnly -Because "a foreign key on a new table: $($verdict.Why)"
}

Test-Case "the real expand-only migration 0065 (module constants, a docstring, a unique constraint) is expand-only" {
    $path = Join-Path $repoRoot "services/api/alembic/versions/20261002_0065_misheard_utterances.py"
    $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text ([IO.File]::ReadAllText($path))
    Assert-True -Condition $verdict.ExpandOnly -Because "0065 is one table and its index: $($verdict.Why)"
}

Test-Case "op.create_foreign_key on an EXISTING table is not expand-only (its rows are validated against the new constraint)" {
    Assert-NotExpandOnly -Because "a constraint on an existing table" -Bodies @(
        'op.create_foreign_key("fk_notes_label", "notes", "labels", ["label_id"], ["id"])',
        "op.create_table(`"labels`", sa.Column(`"id`", sa.Uuid(), primary_key=True))`n    op.create_foreign_key(`"fk_notes_label`", `"notes`", `"labels`", [`"label_id`"], [`"id`"])")
}

Test-Case "op.add_column without nullable=True or a server_default is not expand-only" {
    Assert-NotExpandOnly -Because "not provably nullable or defaulted" -Bodies @(
        'op.add_column("notes", sa.Column("colour", sa.String(16)))',
        'op.add_column("notes", sa.Column("colour", sa.String(16), nullable=False))',
        'op.add_column("notes", sa.Column("id2", sa.Integer(), primary_key=True, nullable=True))')
}

Test-Case "op.execute is never expand-only - not even op.execute('SELECT 1') or CREATE INDEX" {
    Assert-NotExpandOnly -Because "raw SQL is not on the allow-list" -Bodies @(
        "op.execute('SELECT 1')", "op.execute('CREATE INDEX IF NOT EXISTS ix ON t (c)')",
        'op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))', "op.get_bind().exec_driver_sql('SELECT 1')")
}

Test-Case "an alembic call that is not on the allow-list is not expand-only" {
    Assert-NotExpandOnly -Because "an unrecognised op" -Bodies @(
        'op.alter_column("notes", "colour", comment="the colour")', 'op.bulk_insert(notes_table, [{"id": 1}])',
        'op.create_check_constraint("ck", "notes", "x > 0")', 'op.create_unique_constraint("uq", "notes", ["x"])',
        "with op.batch_alter_table(`"notes`") as batch:`n        batch.add_column(sa.Column(`"c`", sa.Integer(), nullable=True))",
        'op.create_table_comment("notes", "x")', 'op.invoke(something)')
}

Test-Case "anything in upgrade() that is not a bare allowed call is not expand-only (a helper, a loop, a variable, a call inside the arguments)" {
    Assert-NotExpandOnly -Because "not a bare allowed op call" -Bodies @(
        '_helper()', "for name in NAMES:`n        op.create_index(name, `"notes`", [name])", "t = `"notes`"`n    op.create_index(`"ix`", t, [`"c`"])",
        'op.create_table("labels", *_columns())', 'op.create_index("ix", "notes", ["c"]); op.execute("DELETE FROM notes")',
        'op.create_table("labels", sa.Column("id", sa.Uuid(), default=os.system("x")))', 'op.add_column("notes", sa.Column("c", sa.Integer(), nullable=True)) or op.execute("DELETE FROM notes")')
}

Test-Case "module-level code beyond imports and constants is not expand-only (a helper def, a call, an f-string)" {
    $tail = "`n`n`ndef upgrade() -> None:`n    op.create_index(`"ix`", `"notes`", [`"c`"])`n`n`ndef downgrade() -> None:`n    pass`n"
    foreach ($head in @("from alembic import op`nop.execute(`"DELETE FROM notes`")", "from alembic import op`n`n`ndef _helper():`n    pass",
            "from alembic import op`nimport os`nX = f`"{os.system('x')}`"", "from alembic import op`nif True:`n    op.execute(`"DELETE FROM notes`")",
            "from alembic import op`nX = op.get_bind()")) {
        $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text ($head + $tail)
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "module code: $head"
    }
}

# Every example from the inspector's three reports (2026-10-03) - each is not expand-only.
Test-Case "inspector examples: ALTER <col> TYPE, SET NOT NULL, ADD COLUMN NOT NULL without DEFAULT, RENAME VALUE" {
    Assert-NotExpandOnly -Because "an ALTER changes the old colour's schema" -Bodies @(
        "op.execute('ALTER TABLE tasks ALTER legacy_col TYPE bigint')", 'op.execute("alter table tasks alter legacy_col set data type bigint")',
        "op.execute('ALTER TABLE tasks ALTER legacy_col SET NOT NULL')", "op.execute('ALTER TABLE tasks ADD COLUMN x int NOT NULL')",
        'op.execute("alter table tasks add x int not null")', "op.execute(`"ALTER TYPE task_state RENAME VALUE 'a' TO 'b'`")",
        "op.execute('ALTER TABLE tasks DROP legacy_col')", "op.execute('ALTER TABLE tasks RENAME old_col TO new_col')")
}

Test-Case "inspector examples: UPDATE ONLY, quoted and aliased UPDATE, MERGE ... DELETE" {
    Assert-NotExpandOnly -Because "a data write" -Bodies @(
        'op.execute("UPDATE ONLY jobs SET status = 1")', "op.execute('UPDATE `"my jobs`" SET status = 1')",
        'op.execute("UPDATE jobs AS j SET status = 1")', 'op.execute("MERGE INTO jobs USING x ON jobs.id = x.id WHEN MATCHED THEN DELETE")')
}

Test-Case "inspector examples: DROP FUNCTION / SEQUENCE / TRIGGER / MATERIALIZED VIEW" {
    Assert-NotExpandOnly -Because "a drop" -Bodies @(
        'op.execute("DROP FUNCTION notify_job()")', 'op.execute("DROP SEQUENCE job_seq")',
        'op.execute("DROP MATERIALIZED VIEW job_stats")', 'op.execute("DROP TRIGGER t ON jobs")')
}

Test-Case "inspector examples: an ORM data write - Session(...), .delete(), .update() - with no execute() at all" {
    Assert-NotExpandOnly -Because "an ORM write" -Bodies @(
        'Session(bind=op.get_bind()).query(Job).filter(Job.done).delete()',
        "session = Session(bind=op.get_bind())`n    session.query(Job).update({`"status`": 1})`n    session.commit()",
        'sa.orm.Session(bind=op.get_bind()).query(Job).delete()')
}

Test-Case "inspector example: a NOT NULL add hidden behind a ';' inside an SQL comment" {
    Assert-NotExpandOnly -Because "a NOT NULL add without DEFAULT" -Bodies @("op.execute(`"`"`"ALTER TABLE jobs ADD c int -- note; x`n    NOT NULL`"`"`")")
}

Test-Case "upper-case SQL with an I in it (DROP INDEX / CONSTRAINT / VIEW) stops on a tr-TR machine too" {
    $culture = [System.Threading.Thread]::CurrentThread.CurrentCulture
    try {
        # tr-TR: (?i) folds 'I' to dotless 'ı', so "INDEX" was not "index" (the owner's PC runs this step).
        [System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::GetCultureInfo("tr-TR")
        foreach ($body in @("op.execute('DROP INDEX ix_tasks_state')", "op.execute('DROP VIEW v_tasks')", "op.execute('DROP CONSTRAINT ck_x')")) {
            $verdict = Get-UpgradeVerdict -Body $body
            Assert-True -Condition (-not $verdict.ExpandOnly) -Because "$body drops something"
        }
    } finally { [System.Threading.Thread]::CurrentThread.CurrentCulture = $culture }
}

Test-Case "the earlier rounds' SQL - any ALTER, ADD VALUE, an unreadable or a once 'readable' execute - is not expand-only either" {
    Assert-NotExpandOnly -Because "raw SQL" -Bodies @(
        "op.execute('ALTER TABLE tasks ADD COLUMN x int NOT NULL DEFAULT 0')", "op.execute(`"ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'parked'`")",
        "op.execute('ALTER TABLE tasks SET UNLOGGED')", "op.execute('ALTER SEQUENCE tasks_id_seq RESTART WITH 1')",
        "op.execute('DO `$`$ BEGIN ALTER TABLE tasks ALTER x TYPE bigint; END `$`$')", "op.execute(open(os.path.join(here, 'x.sql')).read())",
        "sql = 'CREATE INDEX ix ON t (c)'`n    op.execute(sql)", "op.execute(f`"CREATE INDEX ix ON {table} (c)`")",
        "op.get_bind().execute(sa.text(load('x.sql')))", "op.execute(`"`"`"`n        CREATE TABLE x (id int)`n    `"`"`")")
}

# Denetleyici-4 (2026-10-03): three holes in "in doubt, not expand-only", each closed with a case.
function Get-ModuleVerdict {
    param([string]$Head)
    $tail = "`n`n`ndef upgrade() -> None:`n    op.create_index(`"ix`", `"notes`", [`"c`"])`n`n`ndef downgrade() -> None:`n    pass`n"
    return (Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text ("from alembic import op`nimport sqlalchemy as sa`n" + $Head + $tail))
}

Test-Case "inspector-4 (1): a star import binds unknown names (op/sa among them) - not expand-only" {
    foreach ($head in @("from helpers import *", "from sqlalchemy import *", "from alembic.op import (`n    *`n)")) {
        $verdict = Get-ModuleVerdict -Head $head
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "star import: $head"
        Assert-True -Condition ([bool]$verdict.Why) -Because "it says why - $head"
    }
}

Test-Case "inspector-4 (2): any binding of upgrade/downgrade/op/sa - a walrus, a later target, a module-level assignment - is not expand-only" {
    foreach ($head in @("Y = (op := 1)", "Y = [sa := 2]", "upgrade = sa.text", "downgrade = 1", "X = op = 1", "X = Y = sa = 1",
            "X: int = (upgrade := 3)", "sa: object = 1")) {
        $verdict = Get-ModuleVerdict -Head $head
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "rebinding: $head"
        Assert-True -Condition ([bool]$verdict.Why) -Because "it says why - $head"
    }
    Assert-NotExpandOnly -Because "a walrus inside upgrade()" -Bodies @('op.create_index("ix", (op := "notes"), ["c"])')
    $verdict = Get-ModuleVerdict -Head "revision = `"0066`"`nX: int = 1"
    Assert-True -Condition $verdict.ExpandOnly -Because "a plain constant still passes: $($verdict.Why)"
}

Test-Case "inspector-4 (3): add_column reads the column's OWN keywords - a nested nullable=True, ** spread, sa.null() default or non-literal nullable is not expand-only" {
    Assert-NotExpandOnly -Because "not provably nullable or defaulted" -Bodies @(
        "op.add_column('t', sa.Column('c', sa.Integer, nullable=0, info={'k': sa.Column('z', nullable=True)}))",
        "op.add_column('t', sa.Column('c', sa.Integer, server_default=sa.null(), **NN))",
        "op.add_column('t', sa.Column('c', sa.Integer, nullable=True, **NN))",
        "op.add_column('t', sa.Column('c', sa.Integer, server_default=sa.null()))",
        "op.add_column('t', sa.Column('c', sa.Integer, server_default=None))",
        "op.add_column('t', sa.Column('c', sa.Integer, nullable=NULLABLE, server_default='0'))",
        "op.add_column('t', sa.Column('c', sa.Integer, nullable=(True)))",
        "op.add_column('t', sa.Column('c', sa.Integer, *EXTRA, nullable=True))",
        "op.add_column('t', COLUMN)",
        "op.add_column('t', sa.Column('c', sa.Integer, nullable=True), **KW)",
        "op.add_column('t', sa.Column('c', sa.Integer, comment='nullable=True'))")
    foreach ($body in @("op.add_column('t', sa.Column('c', sa.Integer, nullable=True))",
            "op.add_column('t', column=sa.Column('c', sa.Integer(), nullable=False, server_default='0'))",
            "op.add_column(table_name='t', column=sa.Column('c', sa.Integer(), server_default=sa.text('0')), schema='public')")) {
        $verdict = Get-UpgradeVerdict -Body $body
        Assert-True -Condition $verdict.ExpandOnly -Because "a plainly nullable/defaulted column: $body - $($verdict.Why)"
    }
}

Test-Case "inspector-4 (ek): create_index(..., unique=True) is not expand-only (it may reject rows the old colour writes)" {
    Assert-NotExpandOnly -Because "a unique index" -Bodies @(
        'op.create_index("ix", "notes", ["c"], unique=True)', 'op.create_index("ix", "notes", ["c"], unique=UNIQUE)',
        'op.create_index("ix", "notes", ["c"], **KW)')
}

# Danışman, 2026-10-04 04:50 (return 5): a WHITE list, so no new escape is left - non-ASCII code
# stops anywhere, and every string literal of the judged code is either a plain name or sits
# where its SQL is read from a short white list.
Test-Case "inspector-5 (1): ANY non-ASCII character in the masked code (a name, an import alias) is not expand-only" {
    $fullwidthOs = [string][char]0xFF4F + [char]0xFF53
    $eAcute = [string][char]0x00E9
    Assert-NotExpandOnly -Because "a non-ASCII name" -Bodies @(
        "op.create_table('t', sa.Column('id', sa.Integer, info=$fullwidthOs.system('echo pwn')))",
        "op.add_column('t', sa.Column('c', sa.Integer, nullable=True, info=$eAcute))")
    $verdict = Get-ModuleVerdict -Head "from os import system as $eAcute"
    Assert-True -Condition (-not $verdict.ExpandOnly) -Because "a non-ASCII import alias: $($verdict.Why)"
    Assert-True -Condition ([bool]$verdict.Why) -Because "it says why"
    # Turkish in a comment or a docstring is not code: still expand-only.
    $verdict = Get-ModuleVerdict -Head ("# s" + [char]0x0131 + "n" + [char]0x0131 + "r`n`"`"`"Sahibin g" + [char]0x00F6 + "revi.`"`"`"")
    Assert-True -Condition $verdict.ExpandOnly -Because "non-ASCII only in a comment and a docstring: $($verdict.Why)"
}

Test-Case "inspector-5 (2): SQL in sa.text / CheckConstraint / Computed / an index expression passes only digits, a short ';'-free quoted string, now() or CURRENT_TIMESTAMP" {
    Assert-NotExpandOnly -Because "SQL outside the white list" -Bodies @(
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.text('0; DROP TABLE users')))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.text(`"'a;b'`")))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.text('now(); DROP TABLE users')))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.text('pg_sleep(100)')))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.text('0' '; DROP TABLE users')))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.text('0' + DROP)))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.text(SQL)))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.literal_column('1; DROP TABLE users')))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default=sa.func.pg_sleep(100)))",
        "op.add_column('users', sa.Column('x', sa.Integer, server_default='0; DROP TABLE users'))",
        "op.create_table('t', sa.Column('id', sa.Integer, primary_key=True), sa.CheckConstraint('id > 0'))",
        "op.create_table('t', sa.Column('id', sa.Integer, primary_key=True), sa.Column('d', sa.Integer, sa.Computed('id * 2')))",
        "op.create_index('ix', 'notes', [sa.text('lower(name)')])",
        "op.create_index('ix', 'notes', ['lower(name)'])",
        "op.create_index('ix', 'notes', [sa.func.lower(sa.column('name'))])",
        "op.create_index('ix', 'notes', ['c'], postgresql_where=sa.text('deleted_at IS NULL'))",
        "op.create_table('t', sa.Column('id', sa.Integer, primary_key=True), sa.Index('ix_t', sa.text('id + 1')))",
        "op.create_table('t', sa.Column('id', sa.Integer, primary_key=True, info=sa.select(sa.text('1'))))")
    $verdict = Get-ModuleVerdict -Head "DEFAULT = '0; DROP TABLE users'"
    Assert-True -Condition (-not $verdict.ExpandOnly) -Because "a module constant holding SQL: $($verdict.Why)"
    foreach ($body in @("op.add_column('t', sa.Column('c', sa.Integer(), nullable=False, server_default=sa.text('0')))",
            "op.add_column('t', sa.Column('c', sa.String(8), nullable=False, server_default=sa.text(`"'pending'`")))",
            "op.add_column('t', sa.Column('c', sa.DateTime(), nullable=False, server_default=sa.text('now()')))",
            "op.add_column('t', sa.Column('c', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))",
            "op.add_column('t', sa.Column('c', sa.DateTime(), nullable=False, server_default=sa.func.now()))",
            "op.add_column('t', sa.Column('c', sa.String(8), nullable=True, comment='the colour, for the list view'))",
            "op.create_table('l', sa.Column('id', sa.Uuid(), primary_key=True), sa.Column('n', sa.Uuid(), sa.ForeignKey('notes.id', ondelete='SET NULL'), nullable=True))")) {
        $verdict = Get-UpgradeVerdict -Body $body
        Assert-True -Condition $verdict.ExpandOnly -Because "on the white list: $body - $($verdict.Why)"
    }
}

Test-Case "inspector-5 (3): add_column(sa.Column(..., unique=True)) stays expand-only (the new column starts NULL; the old colour never writes it)" {
    $verdict = Get-UpgradeVerdict -Body "op.add_column('notes', sa.Column('slug', sa.String(64), nullable=True, unique=True))"
    Assert-True -Condition $verdict.ExpandOnly -Because "a nullable unique new column: $($verdict.Why)"
}

Test-Case "only alembic versions are migrations" {
    Assert-True -Condition (Test-TeamMigrationPath -Path "services/api/alembic/versions/20261003_0066_x.py") -Because "a version file"
    Assert-True -Condition (-not (Test-TeamMigrationPath -Path "services/api/alembic/env.py")) -Because "env.py is not a version"
    Assert-True -Condition (-not (Test-TeamMigrationPath -Path "services/api/app/models.py")) -Because "a model is not a migration"
}

Write-Host ""
Write-Host "what the host probe says"

Test-Case "the probe is parsed: markers, the colour, the maintenance marker and window, the health body and the reconcile line" {
    $probe = New-Probe -Release $shaA -Pin $shaB -Colour "green" -Marker "yes" -InSeconds "1200" -Health "degraded" -HealthRelease $shaA
    Assert-True -Condition $probe.Ok -Because "a probe that answered"
    Assert-Equal -Expected $shaA -Actual $probe.Release -Because "RELEASE"
    Assert-Equal -Expected $shaB -Actual $probe.Pin -Because "APPROVED_SHA"
    Assert-Equal -Expected "green" -Actual $probe.Colour -Because "the colour"
    Assert-True -Condition $probe.MaintenanceMarker -Because "the marker"
    Assert-Equal -Expected "1200" -Actual $probe.MaintenanceInSeconds -Because "seconds to the window (the HOST's clock)"
    Assert-Equal -Expected "degraded" -Actual $probe.HealthStatus -Because "the health status"
    Assert-Equal -Expected $shaA -Actual $probe.HealthRelease -Because "the served release"
    $failed = Read-TeamHostProbe -Text "" -ExitCode 255
    Assert-True -Condition (-not $failed.Ok) -Because "ssh that did not answer is not a probe"
}

Test-Case "the probe command reads only: no redirection into a file, no systemctl start/stop, no rm" {
    $command = Get-TeamHostProbeCommand -HostBase "/opt/pagentos"
    Assert-True -Condition ($command -notmatch '(^|[^2])>\s*[/$]' -and $command -notmatch '\brm\b' -and $command -notmatch 'systemctl (start|stop|restart|enable|disable)') -Because $command
    Assert-True -Condition ($command -match 'pagentos-maintenance-window\.timer' -and $command -match 'MAINTENANCE_MARKER' -and $command -match 'APPROVED_SHA') -Because "it reads what the decision needs: $command"
    Assert-True -Condition ($command -notmatch '2>&1') -Because "no merged streams"
}

Write-Host ""
Write-Host "when the release goes and when it stops (Get-TeamReleaseDecision)"

Test-Case "everything in order: release" {
    $decision = Get-TeamReleaseDecision -Facts (New-Facts)
    Assert-Equal -Expected "release" -Actual $decision.Action -Because (Get-StopCodes $decision)
}

Test-Case "each rule stops on its own, with its own reason" {
    $marker = New-Probe -Marker "yes"
    $soon = New-Probe -InSeconds "1500"
    $unknownWindow = New-Probe -InSeconds "unknown"
    $sick = New-Probe -Health "degraded"
    $unread = Read-TeamHostProbe -Text "" -ExitCode 255
    $migration = [pscustomobject]@{ Path = "services/api/alembic/versions/x.py"; ExpandOnly = $false; Why = "drop_column" }
    $cases = [ordered]@{
        "not_tip"            = @{ MainTip = $shaC }
        "no_gate"            = @{ Gate = [pscustomobject]@{ Found = $false; Pass = $false; Why = "kapı kaydı yok" } }
        "gate_not_pass"      = @{ Gate = [pscustomobject]@{ Found = $true; Pass = $false; Why = "FAIL" } }
        "blocked"            = @{ Blocked = "team/release-blocked.json" }
        "lock"               = @{ Lock = [pscustomobject]@{ MayRun = $false; Kind = "held"; Holder = "OTHER"; Since = "2026-10-03T10:00:00Z" } }
        "host_unread"        = @{ Host = $unread }
        "health"             = @{ Host = $sick }
        "maintenance_marker" = @{ Host = $marker }
        "maintenance_window" = @{ Host = $soon }
        "maintenance_unknown" = @{ Host = $unknownWindow }
        "diff_unreadable"    = @{ Diff = [pscustomobject]@{ Readable = $false; Why = "git diff failed"; Files = @(); Migrations = @() } }
        "migration"          = @{ Diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = @("services/api/alembic/versions/x.py"); Migrations = @($migration) } }
        "compose"            = @{ Diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = @("infra/docker/docker-compose.prod.yml"); Migrations = @() } }
        "edge"               = @{ Diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = @("infra/docker/edge/nginx.conf"); Migrations = @() } }
    }
    foreach ($code in @($cases.Keys)) {
        $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change $cases[$code])
        Assert-Equal -Expected "stop" -Actual $decision.Action -Because "$code stops"
        Assert-Equal -Expected $code -Actual (Get-StopCodes $decision) -Because "only $code is named"
        Assert-True -Condition ([bool]$decision.Reason) -Because "$code says why"
    }
}

Test-Case "Danışman 2026-10-04: ANY changed migration file stops - the analyzer's 'expand-only' does not release it" {
    $clean = [pscustomobject]@{ Path = "services/api/alembic/versions/x.py"; ExpandOnly = $true; Why = "" }
    # The inspector's lone-CR probe: the analyzer once called it expand-only; it stops on the path alone.
    $loneCr = "def upgrade():`n    # harmless`r    op.execute('DROP TABLE users')`n    op.create_table('t', sa.Column('id', sa.Integer, primary_key=True))`n"
    $probeVerdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/y.py" -Status "A" -Text $loneCr
    $paths = @(
        @{ Files = @("services/api/alembic/versions/x.py"); Migrations = @($clean) },
        @{ Files = @("services/api/alembic/versions/y.py"); Migrations = @($probeVerdict) },
        @{ Files = @("services/api/alembic/env.py"); Migrations = @() },
        @{ Files = @("services/api/alembic.ini"); Migrations = @() },
        @{ Files = @("services/api/migrations/0001_x.sql"); Migrations = @() },
        @{ Files = @("services\api\alembic\versions\z.py"); Migrations = @() }
    )
    foreach ($case in $paths) {
        $diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = $case.Files; Migrations = $case.Migrations }
        $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Diff = $diff })
        Assert-Equal -Expected "stop" -Actual $decision.Action -Because "$($case.Files) is a migration change"
        Assert-Equal -Expected "migration" -Actual (Get-StopCodes $decision) -Because "only the migration rule: $($decision.Reason)"
        Assert-True -Condition ($decision.Reason -match "Danışman yayınlar") -Because "the Danışman releases it: $($decision.Reason)"
    }
    $info = Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = @("services/api/alembic/versions/x.py"); Migrations = @($clean) } })
    Assert-True -Condition ((@($info.Notes) -join " ") -match "bilgi.*çözümleyici.*x\.py.*genişletme") -Because "the analyzer is an information line: $(@($info.Notes) -join ' / ')"
    foreach ($file in @("services/api/app/models.py", "services/api/tests/unit/test_migrations.py", "docs/migrations.md")) {
        $diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = @($file); Migrations = @() }
        Assert-Equal -Expected "release" -Actual (Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Diff = $diff })).Action -Because "$file is not a migration"
    }
}

Test-Case "a window more than 30 minutes away, or one that has passed, does not stop it; 30 minutes exactly does" {
    foreach ($seconds in @("1801", "-60", "none")) {
        Assert-Equal -Expected "release" -Actual (Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = (New-Probe -InSeconds $seconds) })).Action -Because "$seconds s"
    }
    Assert-Equal -Expected "stop" -Actual (Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = (New-Probe -InSeconds "1800") })).Action -Because "1800 s"
}

Test-Case "-LocalOnly judges what is known before the host is read; the host's rules are not asked" {
    $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = $null; Diff = $null }) -LocalOnly
    Assert-Equal -Expected "release" -Actual $decision.Action -Because (Get-StopCodes $decision)
    $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = $null; Diff = $null; Gate = [pscustomobject]@{ Found = $false; Pass = $false; Why = "x" } }) -LocalOnly
    Assert-Equal -Expected "no_gate" -Actual (Get-StopCodes $decision) -Because "a local rule still stops"
    $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = $null; Diff = $null })
    Assert-Equal -Expected "stop" -Actual $decision.Action -Because "without -LocalOnly a host that was never read is not 'in order'"
}

Test-Case "the verification wants RELEASE, APPROVED_SHA, the reconcile's last line and the edge's health on the sha" {
    $good = New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaB -Colour "green"
    Assert-True -Condition (Test-TeamReleaseVerified -Probe $good -Sha $shaB).Ok -Because "all four agree"
    $checks = @{
        "RELEASE"      = (New-Probe -Release $shaA -Pin $shaB -HealthRelease $shaB -Reconcile "RECONCILE OK: api-green is canonical (release $shaB); x")
        "APPROVED_SHA" = (New-Probe -Release $shaB -Pin $shaA -HealthRelease $shaB)
        "RECONCILE"    = (New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaB -Reconcile "RECONCILE DEGRADED: api-green ($shaB) stays canonical")
        "health"       = (New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaB -Health "degraded")
        "edge"         = (New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaA)
    }
    foreach ($name in @($checks.Keys)) {
        $verified = Test-TeamReleaseVerified -Probe $checks[$name] -Sha $shaB
        Assert-True -Condition (-not $verified.Ok) -Because "$name wrong is not verified"
    }
    $old = New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaB -Reconcile "RECONCILE OK: api-blue is canonical (release $shaA); x"
    Assert-True -Condition (-not (Test-TeamReleaseVerified -Probe $old -Sha $shaB).Ok) -Because "a RECONCILE OK for ANOTHER sha is not the reconcile of this release"
}

# ============================================================================ the step

Write-Host ""
Write-Host "the step, in a repository of its own, with the fake release script and the fake ssh"

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$sandboxes = New-Object System.Collections.ArrayList
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

$greenLog = "=== Required files ===`nall present`n`n=== Quality gate summary ===`n`nStep           Result Seconds`nRequired files PASS       0.1`n`nQUALITY GATE: PASS`n"
$redLog = "=== API unit tests ===`nFAILED tests/unit/test_x.py::test_y - AssertionError`nFAILED: pytest (unit) exited with code 1`n`n=== Quality gate summary ===`n`nQUALITY GATE: FAIL`n"

function New-Sandbox {
    <#
        A repository whose first commit is what the host serves, and whose second - main's tip,
        pushed to a bare origin beside it - is what waits for the release, with -Files in it.
        team/reports/c1/gate-1.json + .log as the integrate step leaves them (-Gate).
        The host is <root>-host\state.json.
    #>
    param(
        [hashtable]$Files = @{}, [string]$Gate = "pass", $Lock = $null, [switch]$Blocked,
        [hashtable]$HostChange = @{}
    )
    $root = Join-Path $env:TEMP ("pagentos-rel-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    $hostDir = "$root-host"
    [void]$sandboxes.Add($root); [void]$sandboxes.Add($hostDir); [void]$sandboxes.Add("$root-origin.git")
    foreach ($folder in @("scripts\lib", "scripts\team", "team", "src")) { [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $folder)) }
    [void](New-Item -ItemType Directory -Force -Path $hostDir)
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1", "TeamIntegrate.ps1", "TeamRelease.ps1")) {
        $from = Join-Path $repoRoot "scripts\lib\$name"
        if (Test-Path -LiteralPath $from) { Copy-Item -LiteralPath $from -Destination (Join-Path $root "scripts\lib\$name") }
    }
    if (Test-Path -LiteralPath $releaseScript) { Copy-Item -LiteralPath $releaseScript -Destination (Join-Path $root "scripts\team\release.ps1") }
    Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/`nteam/reports/`nteam/release-blocked.json`nteam/queue.json`nteam/lock.json" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "src\app.txt") -Value "served" -Encoding ASCII
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "what the host serves"))
    $served = Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "HEAD")
    Add-Content -LiteralPath (Join-Path $root "src\app.txt") -Value "the gated change" -Encoding ASCII
    foreach ($relative in @($Files.Keys)) {
        $target = Join-Path $root ($relative -replace "/", "\")
        $folder = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        [System.IO.File]::WriteAllText($target, [string]$Files[$relative], $utf8)
    }
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "merge: integrate/c1 (gated) into main"))
    $tip = Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "HEAD")
    [void](Invoke-TeamGit -WorkingDirectory $hostDir -Arguments @("init", "-q", "--bare", "$root-origin.git"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("remote", "add", "origin", "$root-origin.git"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("push", "-q", "origin", "main"))

    $task = [pscustomobject]@{
        id = "task-one"; title = "the task"; roadmap_row = "row"; state = "awaiting_release"; area = @("src")
        branch = "team/c1/worker-task-one"; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
        created_at = "2026-10-01T00:00:00Z"; updated_at = "2026-10-01T00:00:00Z"; sha = $tip; integration_branch = "integrate/c1"
        reason = "kapı yeşil: $tip; main $tip; kayıt: team/reports/c1/gate-1.log"
    }
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document ([pscustomobject]@{ version = 1; tasks = @($task) })
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document $(if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased })
    if ($Blocked) { Write-TeamJson -Path (Join-Path $root "team\release-blocked.json") -Document ([pscustomobject]@{ sha = $served; why = "geri alındı" }) }

    $reports = Join-Path $root "team\reports\c1"
    [void](New-Item -ItemType Directory -Force -Path $reports)
    if ($Gate -ne "none") {
        $gatedMain = if ($Gate -eq "other-sha") { $served } else { $tip }
        Write-TeamJson -Path (Join-Path $reports "gate-1.json") -Document ([pscustomobject]@{
                n = 1; branch = "integrate/c1"; at = "2026-10-03T10:00:00Z"; result = "green"; sha = $gatedMain; main = $gatedMain; log = "team/reports/c1/gate-1.log" })
        [System.IO.File]::WriteAllText((Join-Path $reports "gate-1.log"), $(if ($Gate -eq "fail") { $redLog } else { $greenLog }), $utf8)
    }

    $state = [ordered]@{
        release = $served; app_release = $served; pin = $served; lkg = $shaC; colour = "blue"
        maintenance_marker = "no"; maintenance_in_s = "none"; health_status = "ok"; health_release = $served
        reconcile = "RECONCILE OK: api-blue is canonical (release $served); markers, upstreams and containers agree"; probe_exit = 0
    }
    foreach ($name in @($HostChange.Keys)) { $state[$name] = $HostChange[$name] }
    [System.IO.File]::WriteAllText((Join-Path $hostDir "state.json"), (ConvertTo-Json -InputObject ([pscustomobject]$state)), $utf8)
    return [pscustomobject]@{ Root = $root; Host = $hostDir; Served = $served; Tip = $tip }
}

function Invoke-Release {
    param($Box, [string]$Scenario = "ok", [string]$Extra = "")
    $set = @{ PAGENTOS_FAKE_HOST = $Box.Host; PAGENTOS_FAKE_RELEASE_SCENARIO = $Scenario }
    foreach ($name in @($set.Keys)) { Set-Item -Path "Env:\$name" -Value $set[$name] }
    try {
        $command = "& '" + (Join-Path $Box.Root "scripts\team\release.ps1") + "' -Machine 'MAIL'" +
        " -ReleaseScript '$fakeRelease' -SshPath '$powershell'" +
        " -SshPrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','$fakeRelease','ssh'" +
        " -VerifyWaitSeconds 0" + $(if ($Extra) { " " + $Extra } else { "" })
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ($command + "; exit `$LASTEXITCODE")) `
            -WorkingDirectory $Box.Root -TimeoutSeconds 300
    }
    finally { foreach ($name in @($set.Keys)) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue } }
    $callsPath = Join-Path $Box.Host "calls.log"
    $calls = if (Test-Path -LiteralPath $callsPath) { @([System.IO.File]::ReadAllLines($callsPath, [System.Text.Encoding]::UTF8) | Where-Object { $_.Trim() }) } else { @() }
    $reports = Join-Path $Box.Root "team\reports\c1"
    $report = @(Get-ChildItem -LiteralPath $reports -Filter "release-*.md" -File -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1)
    return [pscustomobject]@{
        ExitCode = $result.ExitCode; Output = ($result.StdOut + $result.StdErr)
        Task     = @((Read-TeamJson -Path (Join-Path $Box.Root "team\queue.json")).tasks)[0]
        Calls    = @($calls)
        HostState = (ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText((Join-Path $Box.Host "state.json"), $utf8)))
        Report   = $(if (@($report).Count -gt 0) { [System.IO.File]::ReadAllText($report[0].FullName, [System.Text.Encoding]::UTF8) } else { "" })
        Files    = @(Get-ChildItem -LiteralPath $reports -Filter "release-*" -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
        Blocked  = (Test-Path -LiteralPath (Join-Path $Box.Root "team\release-blocked.json"))
        Lock     = (Read-TeamJson -Path (Join-Path $Box.Root "team\lock.json"))
        Reports  = $reports
    }
}

function Assert-Stopped {
    <# The step stopped: no release command, no pin; the task waits with the reason; the report says it. #>
    param($Run, [string]$Words, [int]$ExitCode = 5)
    Assert-Equal -Expected $ExitCode -Actual $Run.ExitCode -Because $Run.Output
    $acted = @($Run.Calls | Where-Object { $_ -match '^release\|' -or $_ -match '^ssh\|pin' })
    Assert-Equal -Expected 0 -Actual @($acted).Count -Because "no release command was issued: $($Run.Calls -join ' / ')"
    Assert-Equal -Expected "awaiting_release" -Actual $Run.Task.state -Because "the task waits"
    Assert-True -Condition ([string]$Run.Task.reason -match [regex]::Escape($Words)) -Because "the task carries the reason '$Words': $($Run.Task.reason)"
    Assert-True -Condition ([string]$Run.Task.reason -match "Onay Merkezi") -Because "and says it waits for the owner: $($Run.Task.reason)"
    Assert-True -Condition ($Run.Report -match "## Yayın" -and $Run.Report -match [regex]::Escape($Words)) -Because "the report says why: $($Run.Report)"
    Assert-Equal -Expected $false -Actual ([bool](Get-TeamProperty -InputObject $Run.Task -Name "release_approved" -Default $false)) -Because "nothing was approved"
}

try {
    Test-Case "a gated main with a PASS log is released from a clean worktree at the sha, pinned with the full sha, verified, and the task is 'released' by the standing rule" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $releases = @($run.Calls | Where-Object { $_ -match '^release\|' })
        Assert-Equal -Expected 2 -Actual @($releases).Count -Because "preflight, then release: $($run.Calls -join ' / ')"
        Assert-True -Condition ($releases[0] -match '^release\|preflight\|bluegreen=True\|' -and $releases[1] -match '^release\|release\|bluegreen=True\|') -Because "preflight first, both -BlueGreen: $($releases -join ' / ')"
        foreach ($line in $releases) {
            $parts = $line.Split("|")
            Assert-Equal -Expected $box.Tip -Actual $parts[4] -Because "the release ran on the sha: $line"
            Assert-Equal -Expected "dirty=False" -Actual $parts[5] -Because "from a clean tree: $line"
            Assert-True -Condition ($parts[3] -ne $box.Root -and $parts[3] -match 'worktrees') -Because "never the main checkout: $line"
        }
        Assert-True -Condition (@($run.Calls | Where-Object { $_ -eq "ssh|pin|$($box.Tip)" }).Count -eq 1) -Because "pinned with the FULL sha: $($run.Calls -join ' / ')"
        Assert-Equal -Expected $box.Tip -Actual $run.HostState.pin -Because "APPROVED_SHA is the sha"
        Assert-Equal -Expected "released" -Actual $run.Task.state -Because "released"
        Assert-Equal -Expected $true -Actual $run.Task.release_approved -Because "approved"
        Assert-Equal -Expected "standing_rule" -Actual $run.Task.release_approved_by -Because "by the owner's standing rule (ADR-0214 addendum 9)"
        Assert-True -Condition ([string]$run.Task.release_approved_at -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$') -Because "when"
        Assert-True -Condition ([string]$run.Task.reason -match "^yayinlandi \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z, main $($box.Tip) \(green\)$") -Because $run.Task.reason
        Assert-True -Condition ($run.Report -match "## Yayın" -and $run.Report.Contains($box.Tip) -and $run.Report -match "green" -and $run.Report.Contains($box.Served)) -Because "the report names the sha, the colour and the last known good: $($run.Report)"
        Assert-True -Condition ($run.Report -match "task-one") -Because "and what was released"
        Assert-Equal -Expected $false -Actual $run.Blocked -Because "no block marker"
        Assert-Equal -Expected $false -Actual ([bool](Get-TeamProperty -InputObject $run.Lock -Name "held" -Default $false)) -Because "the lock is given back"
        $wt = @((Invoke-SandboxGit -Root $box.Root -Arguments @("worktree", "list", "--porcelain")) -split "`n" | Where-Object { $_ -match '^worktree ' })
        Assert-Equal -Expected 1 -Actual @($wt).Count -Because "the release worktree is removed afterwards: $($wt -join ' / ')"
    }

    Test-Case "each command's stdout and stderr land in SEPARATE files under team/reports/<cycle>/release-<n>.*" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        foreach ($step in @("preflight", "release", "pin")) {
            $out = @($run.Files | Where-Object { $_ -match "^release-1\.$step\.out$" })
            $err = @($run.Files | Where-Object { $_ -match "^release-1\.$step\.err$" })
            Assert-True -Condition (@($out).Count -eq 1 -and @($err).Count -eq 1) -Because "$step has an .out and an .err: $($run.Files -join ', ')"
        }
        $releaseOut = [System.IO.File]::ReadAllText((Join-Path $run.Reports "release-1.release.out"), [System.Text.Encoding]::UTF8)
        $releaseErr = [System.IO.File]::ReadAllText((Join-Path $run.Reports "release-1.release.err"), [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($releaseOut -match "RELEASE OK" -and $releaseOut -notmatch "nginx") -Because "stdout only in .out: $releaseOut"
        Assert-True -Condition ($releaseErr -match "nginx: \[notice\]" -and $releaseErr -notmatch "RELEASE OK") -Because "stderr only in .err: $releaseErr"
    }

    Test-Case "the step's source never merges stderr into stdout (2>&1) - least of all where it runs the release or ssh" {
        foreach ($path in @($releaseScript, $releaseLib)) {
            Assert-True -Condition (Test-Path -LiteralPath $path) -Because "$path exists"
            $lines = @([System.IO.File]::ReadAllLines($path, [System.Text.Encoding]::UTF8))
            $merged = @($lines | Where-Object { $_ -match '2>&1' -or $_ -match '\*>&1' })
            Assert-Equal -Expected 0 -Actual @($merged).Count -Because "no line of $path merges the streams: $($merged -join ' / ')"
        }
        $text = [System.IO.File]::ReadAllText($releaseScript, [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($text -match 'ReleaseScript' -and $text -match 'SshPath') -Because "the release and ssh are run by this script"
    }

    Test-Case "no gate record or log: stop, and nothing is run - not even a look at the host" {
        $box = New-Sandbox -Gate "none"
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "kapı kaydı yok"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
    }

    Test-Case "a FAIL gate log: stop, nothing run" {
        $box = New-Sandbox -Gate "fail"
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "kapı PASS demiyor"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
    }

    Test-Case "a gate log for ANOTHER sha: stop, nothing run" {
        $box = New-Sandbox -Gate "other-sha"
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "kapı kaydı yok"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
    }

    Test-Case "a migration with drop_column in the diff: stop, no release command" {
        $box = New-Sandbox -Files @{ "services/api/alembic/versions/20261003_0066_contract.py" = $dropColumn }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "göç içeren commit"
        Assert-True -Condition ($run.Task.reason -match "0066_contract") -Because "names the migration: $($run.Task.reason)"
    }

    # Danışman kararı 2026-10-04: a commit with ANY migration is not released by this step - not
    # even one the analyzer calls expand-only; the analyzer's verdict is an information line only.
    Test-Case "Danışman 2026-10-04: an expand-only migration (add_column nullable, create_table, create_index) STOPS - the Danışman releases it; the analyzer is only an information line" {
        $box = New-Sandbox -Files @{ "services/api/alembic/versions/20261003_0066_expand.py" = $expandOnly }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "Danışman yayınlar"
        Assert-True -Condition ($run.Task.reason -match "0066_expand") -Because "names the migration: $($run.Task.reason)"
        Assert-True -Condition ($run.Report -match "bilgi.*çözümleyici.*0066_expand.*genişletme") -Because "the analyzer's verdict is in the report as information: $($run.Report)"
    }

    Test-Case "Danışman 2026-10-04: a commit WITHOUT a migration still releases under the earlier rules" {
        $box = New-Sandbox -Files @{ "services/api/app/team/new_feature.py" = "VALUE = 1`n" }
        $run = Invoke-Release -Box $box
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "released" -Actual $run.Task.state -Because $run.Output
        Assert-Equal -Expected "standing_rule" -Actual $run.Task.release_approved_by -Because "by the standing rule"
    }

    Test-Case "a changed prod compose file: stop, no release command" {
        $box = New-Sandbox -Files @{ "infra/docker/docker-compose.prod.yml" = "services: {}`n" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "docker-compose.prod.yml değişti"
    }

    Test-Case "a changed edge: stop, no release command" {
        $box = New-Sandbox -Files @{ "infra/docker/edge/nginx.conf" = "events {}`n" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "edge değişti"
    }

    Test-Case "production health not ok before the release: stop, no release command" {
        $box = New-Sandbox -HostChange @{ health_status = "degraded" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "sağlık ok değil"
    }

    Test-Case "a maintenance window within 30 minutes: stop, no release command" {
        $box = New-Sandbox -HostChange @{ maintenance_in_s = "900" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "bakım penceresi"
    }

    Test-Case "a maintenance marker on the host: stop, no release command" {
        $box = New-Sandbox -HostChange @{ maintenance_marker = "yes" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "bakım işareti"
    }

    Test-Case "a lock another machine holds: stop, nothing run, the lock stays theirs" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $box = New-Sandbox -Lock $held
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "kilit" -ExitCode 3
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual $run.Lock.machine -Because "the lock is still theirs"
    }

    Test-Case "team/release-blocked.json (an automatic release was rolled back and nobody cleared it): stop, nothing run" {
        $box = New-Sandbox -Blocked
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "release-blocked.json"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
    }

    Test-Case "the release script rolls back: the task stays awaiting_release, 'geri alındı' is recorded and the block marker is set" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box -Scenario "rollback"
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "awaiting_release" -Actual $run.Task.state -Because "not released"
        Assert-True -Condition ([string]$run.Task.reason -match "geri alındı") -Because $run.Task.reason
        Assert-True -Condition $run.Blocked -Because "the marker stops the next run until the lead removes it"
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_ -match '^ssh\|pin' }).Count -Because "no pin after a failed release"
        Assert-Equal -Expected $box.Served -Actual $run.HostState.release -Because "the host is what the release script's own rollback left"
        Assert-True -Condition ($run.Report -match "geri alındı") -Because $run.Report
        Remove-Item -LiteralPath (Join-Path $box.Host "calls.log") -Force
        $again = Invoke-Release -Box $box
        Assert-Stopped -Run $again -Words "release-blocked.json"
    }

    Test-Case "a verification that finds APPROVED_SHA != sha is a failed release: the marker is set and the task is not 'released'" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box -Scenario "pin-wrong"
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "awaiting_release" -Actual $run.Task.state -Because "not released"
        Assert-True -Condition $run.Blocked -Because "the marker is set"
        Assert-True -Condition ($run.Report -match "APPROVED_SHA") -Because "the report names what failed: $($run.Report)"
    }

    Test-Case "a preflight that fails changes nothing: the task waits, no release, no marker" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box -Scenario "preflight-fail"
        Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_ -match '^release\|release\|' }).Count -Because "no release after a failed preflight"
        Assert-Equal -Expected "awaiting_release" -Actual $run.Task.state -Because "waits"
        Assert-Equal -Expected $false -Actual $run.Blocked -Because "nothing changed on the host: no marker"
    }

    Test-Case "-DryRun prints the decision and changes nothing" {
        $box = New-Sandbox
        $queueBefore = (Get-FileHash -LiteralPath (Join-Path $box.Root "team\queue.json") -Algorithm SHA256).Hash
        $lockBefore = (Get-FileHash -LiteralPath (Join-Path $box.Root "team\lock.json") -Algorithm SHA256).Hash
        $run = Invoke-Release -Box $box -Extra "-DryRun"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-True -Condition ($run.Output -match "DRY RUN" -and $run.Output -match "release") -Because $run.Output
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_ -notmatch '^ssh\|probe$' }).Count -Because "only reads: $($run.Calls -join ' / ')"
        Assert-Equal -Expected $queueBefore -Actual (Get-FileHash -LiteralPath (Join-Path $box.Root "team\queue.json") -Algorithm SHA256).Hash -Because "the queue is untouched"
        Assert-Equal -Expected $lockBefore -Actual (Get-FileHash -LiteralPath (Join-Path $box.Root "team\lock.json") -Algorithm SHA256).Hash -Because "the lock is untouched"
        Assert-Equal -Expected 0 -Actual @($run.Files).Count -Because "no report, no log: $($run.Files -join ', ')"
        Assert-Equal -Expected $false -Actual (Test-Path -LiteralPath (Join-Path $box.Root ".claude\worktrees")) -Because "no worktree"
    }
}
finally {
    foreach ($root in $sandboxes) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        if (Test-Path -LiteralPath (Join-Path $root ".git")) { try { [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("worktree", "prune")) } catch { } }
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop; break }
            catch { Start-Sleep -Milliseconds 400 }
        }
    }
}

Write-Host ""
Write-Host "team-release tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
