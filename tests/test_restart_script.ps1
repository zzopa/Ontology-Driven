# Read-only checks: never stop a process or start the web application.
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectDir 'scripts\service\restart.ps1'
$scriptText = [IO.File]::ReadAllText($scriptPath, [Text.Encoding]::UTF8)
$tokens = $null
$parseErrors = $null
[Management.Automation.Language.Parser]::ParseInput($scriptText, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
. ([scriptblock]::Create($scriptText)) -ProjectRoot $projectDir -LibraryOnly

$pythonPath = 'D:\Project With Spaces\.venv\Scripts\python.exe'
$entryPath = 'D:\Project With Spaces\ask_web.py'
function Assert-Equal {
    param($Actual, $Expected, [string]$Description)
    if ($Actual -ne $Expected) { throw "FAILED: $Description (actual: $Actual)" }
    Write-Host "PASS: $Description"
}
function New-FakeProcess {
    param([int]$Id, [int]$ParentId, [string]$Executable, [string]$Command)
    return [pscustomobject]@{ProcessId=$Id; ParentProcessId=$ParentId; ExecutablePath=$Executable; CommandLine=$Command}
}
$relative = '"' + $pythonPath + '" -X utf8 ask_web.py'
$absolute = '"' + $pythonPath + '" -X utf8 "' + $entryPath + '"'
$wrapper = New-FakeProcess 100 1 $pythonPath $relative
$child = New-FakeProcess 101 100 'C:\Python313\python.exe' $relative
$processes = @{100=$wrapper;101=$child}
Assert-Equal (Test-ProjectProcess $wrapper $processes $pythonPath $entryPath) $true 'existing relative venv launcher'
Assert-Equal (Test-ProjectProcess $child $processes $pythonPath $entryPath) $true 'verified base-interpreter child'
$wrapper.CommandLine = $absolute
$child.CommandLine = $absolute
Assert-Equal (Test-ProjectProcess $wrapper $processes $pythonPath $entryPath) $true 'absolute entrypoint with spaces'
Assert-Equal (Test-ProjectProcess $child $processes $pythonPath $entryPath) $true 'absolute child with spaces'
Assert-Equal (Test-ProjectProcess $child @{} $pythonPath $entryPath) $false 'unverifiable parent fails closed'
$wrong = New-FakeProcess 200 1 $pythonPath ('"' + $pythonPath + '" other.py')
Assert-Equal (Test-ProjectProcess $wrong $processes $pythonPath $entryPath) $false 'other entrypoint rejected'
$wrong.CommandLine = '"D:\Other\.venv\Scripts\python.exe" -X utf8 ask_web.py'
Assert-Equal (Test-ProjectProcess $wrong $processes $pythonPath $entryPath) $false 'another project rejected'
$wrong.CommandLine = '"' + $pythonPath + '" -c "print(''ask_web.py'')"'
Assert-Equal (Test-ProjectProcess $wrong $processes $pythonPath $entryPath) $false 'arbitrary Python command rejected'
$wrong.CommandLine = $absolute + ' --unexpected'
Assert-Equal (Test-ProjectProcess $wrong $processes $pythonPath $entryPath) $false 'unexpected arguments rejected'
$wrong.CommandLine = $absolute
$wrong.ExecutablePath = 'C:\Other\python.exe'
Assert-Equal (Test-ProjectProcess $wrong $processes $pythonPath $entryPath) $false 'untrusted executable rejected'
Assert-Equal (Test-ProjectProcess $null $processes $pythonPath $entryPath) $false 'missing process rejected'
Assert-ProjectOwners @(100,101) $processes $pythonPath $entryPath
Assert-ProjectOwners @() $processes $pythonPath $entryPath
$rejected = $false
try { Assert-ProjectOwners @(999) $processes $pythonPath $entryPath }
catch { $rejected = $true }
Assert-Equal $rejected $true 'unknown listener prevents restart'
Write-Host 'All restart script safety checks passed.'
