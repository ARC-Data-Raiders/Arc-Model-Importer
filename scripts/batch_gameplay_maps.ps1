# Batch gameplay extract + minimap overlay for all Pioneer maps.
param(
    [string]$PioneerRoot = $env:ARC_PIONEER_ROOT,
    [string]$Workspace = "GameplayExtraction",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

if (-not $PioneerRoot) {
    throw "Set -PioneerRoot or ARC_PIONEER_ROOT to the PioneerGame export root"
}

$mapsRoot = Join-Path $PioneerRoot "PioneerGame\Content\Pioneer\Maps"
if (-not (Test-Path -LiteralPath $mapsRoot)) {
    $mapsRoot = Join-Path $PioneerRoot "Content\Pioneer\Maps"
}
if (-not (Test-Path -LiteralPath $mapsRoot)) {
    throw "Maps folder not found under Pioneer root: $PioneerRoot"
}

$ws = if ([System.IO.Path]::IsPathRooted($Workspace)) { $Workspace } else { Join-Path $RepoRoot $Workspace }
$py = "python"

Write-Host "Building spawn catalog..."
& $py (Join-Path $RepoRoot "map_tools\build_spawn_catalog.py") `
    --catalog `
    --pioneer-root $PioneerRoot `
    --workspace $ws

Get-ChildItem -LiteralPath $mapsRoot -Directory | ForEach-Object {
    $mapDir = $_.FullName
    $mapName = $_.Name
    Write-Host "`n=== $mapName ==="
    & $py (Join-Path $RepoRoot "map_tools\extract_gameplay_spawns.py") `
        --map-dir $mapDir `
        --workspace $ws
    & $py (Join-Path $RepoRoot "map_tools\gameplay_map_overlay.py") `
        --map-dir $mapDir `
        --workspace $ws
}

Write-Host "`nDone. Outputs under $ws"
