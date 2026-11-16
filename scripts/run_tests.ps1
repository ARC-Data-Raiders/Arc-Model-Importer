# Run the addon tests inside headless Blender (hidden window, 6 threads, private config).
# Example: powershell -NoProfile -File scripts/run_tests.ps1 -Pattern "weapon_browser,uemodel"
param(
    [string]$Blender = 'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe',
    [string]$Pattern = '',
    [string]$ConfigDir = (Join-Path $env:TEMP 'arc_tests_cfg'),
    [string]$Temp = (Join-Path $env:TEMP 'arc_tests'),
    [string]$Json = '',
    [string]$Log = ''
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Blender)) { throw "Blender not found: $Blender" }
$runner = Join-Path $PSScriptRoot 'run_tests_in_blender.py'
New-Item -ItemType Directory -Force -Path $ConfigDir, $Temp | Out-Null
if (-not $Log) { $Log = Join-Path $Temp 'run_tests.log' }

# Private profile: never touch the user's %APPDATA% Blender config.
$env:BLENDER_USER_CONFIG = Join-Path $ConfigDir 'config'
$env:BLENDER_USER_SCRIPTS = Join-Path $ConfigDir 'scripts'
$env:BLENDER_USER_DATAFILES = Join-Path $ConfigDir 'datafiles'
$env:DATARAIDERS_TEST_TEMP = $Temp

function Quote([string]$s) { if ($s -match '[\s"]') { '"' + ($s -replace '"', '\"') + '"' } else { $s } }
$argv = @('--background', '--factory-startup', '--threads', '6', '--python', $runner, '--', $Pattern, '--temp', $Temp)
if ($Json) { $argv += @('--json', $Json) }
$argLine = ($argv | ForEach-Object { if ($_ -eq '') { '""' } else { Quote $_ } }) -join ' '

$err = "$Log.err"
$proc = Start-Process -FilePath $Blender -ArgumentList $argLine -WindowStyle Hidden -PassThru -Wait `
    -RedirectStandardOutput $Log -RedirectStandardError $err
Get-Content -LiteralPath $Log | Where-Object { $_ -match '^(TESTFILE|RED_FILES|TESTS )' }
Write-Host "exit=$($proc.ExitCode) log=$Log"
exit $proc.ExitCode
