<#
.SYNOPSIS
  Build a clean Blender addon zip (runtime modules only) and optionally sync to AppData.

.DESCRIPTION
  Excludes __pycache__, *.pyc, logs, tests/verify tools, docs, .git, .cursor, blend backups,
  and other repo-only tooling so the shipped zip does not balloon on every edit.

  Runtime bytecode is redirected by __init__.py to:
    %LOCALAPPDATA%\DataRaiders-BlenderImporter\pycache

.EXAMPLE
  .\package_addon.ps1
  .\package_addon.ps1 -SyncAppData
  .\package_addon.ps1 -OutZip ".\dist\DataRaiders-BlenderImporter.zip"
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$OutZip = "",
    [switch]$SyncAppData,
    [string]$AppDataAddon = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    if ($PSScriptRoot) { $RepoRoot = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $RepoRoot = (Get-Location).Path }
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

if (-not $AppDataAddon) {
    $AppDataAddon = Join-Path $env:APPDATA "Blender Foundation\Blender\5.1\scripts\addons\DataRaiders-BlenderImporter"
}

# Runtime files/dirs that Blender needs. Everything else stays in git but out of the zip.
$RootFiles = @(
    "__init__.py",
    "properties.py",
    "operators.py",
    "ui.py",
    "importing.py",
    "materials.py",
    "textures.py",
    "utils.py",
    "map_placement.py",
    "fmdex.py",
    "rig.py",
    "palette_calibration.py",
    "outfit_reference.csv",
    # Required for clothing/outfit ColorMask materials — without this, imports
    # succeed as bare Principled stubs ("Arc Texturer unavailable").
    "ArcTexturer.blend",
    "add-on-io-scene-psk-psa-v9_1_2.zip",
    "ADDON_SYNC.txt"
)

$RootDirs = @(
    "assets",
    "map_tools"
)

# Under map_tools: ship runtime helpers only (exclude analyze dumps / docs).
$MapToolsExclude = @(
    "_analyze_ism.py",
    "_analyze_ism_owners.py",
    "_ism_analysis.json",
    "_ism_owners.json",
    "_ism_samples.json",
    "FMODEL_BRIDGE.md",
    "__pycache__"
)

function Get-AddonVersion {
    $init = Join-Path $RepoRoot "__init__.py"
    $text = Get-Content -LiteralPath $init -Raw
    if ($text -match '"version"\s*:\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)') {
        return "{0}.{1}.{2}" -f $Matches[1], $Matches[2], $Matches[3]
    }
    return "0.0.0"
}

function New-StagingDir {
    $stage = Join-Path $env:TEMP ("DataRaiders-BlenderImporter-stage-" + [guid]::NewGuid().ToString("n"))
    $addonStage = Join-Path $stage "DataRaiders-BlenderImporter"
    New-Item -ItemType Directory -Path $addonStage -Force | Out-Null

    foreach ($f in $RootFiles) {
        $src = Join-Path $RepoRoot $f
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $addonStage $f) -Force
        }
    }

    foreach ($d in $RootDirs) {
        $src = Join-Path $RepoRoot $d
        if (-not (Test-Path -LiteralPath $src)) { continue }
        $dst = Join-Path $addonStage $d
        if ($d -eq "map_tools") {
            New-Item -ItemType Directory -Path $dst -Force | Out-Null
            Get-ChildItem -LiteralPath $src -Force | ForEach-Object {
                if ($MapToolsExclude -contains $_.Name) { return }
                if ($_.PSIsContainer -and $_.Name -eq "__pycache__") { return }
                if ($_.Name -like "*.pyc") { return }
                Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dst $_.Name) -Recurse -Force
            }
        }
        else {
            # assets: copy but skip caches / thumbs
            robocopy $src $dst /E /NFL /NDL /NJH /NJS /nc /ns /np `
                /XD __pycache__ .git `
                /XF *.pyc *.pyo *.log *.blend1 Thumbs.db | Out-Null
            if ($LASTEXITCODE -ge 8) {
                throw "robocopy failed for $d (exit $LASTEXITCODE)"
            }
        }
    }

    # Belt-and-suspenders: scrub any cache that slipped in
    Get-ChildItem -LiteralPath $addonStage -Recurse -Force -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue }
    Get-ChildItem -LiteralPath $addonStage -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".pyc", ".pyo", ".log", ".blend1" } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -Confirm:$false -ErrorAction SilentlyContinue }

    return @{ StageRoot = $stage; AddonDir = $addonStage }
}

function Sync-ToAppData([string]$AddonDir) {
    $parent = Split-Path $AppDataAddon -Parent
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $AppDataAddon)) {
        New-Item -ItemType Directory -Path $AppDataAddon -Force | Out-Null
    }

    # Mirror runtime tree; purge extras (.git, verify_*, docs, pycache, logs, …).
    # /R:1 /W:1 tolerates Blender holding arc_raiders_debug.log open.
    robocopy $AddonDir $AppDataAddon /MIR /NFL /NDL /NJH /NJS /nc /ns /np /R:1 /W:1 `
        /XF arc_raiders_debug.log outfit_toggles.txt | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy sync failed (exit $LASTEXITCODE)"
    }

    # Extra scrub for caches / leftovers robocopy may leave when locked
    Get-ChildItem -LiteralPath $AppDataAddon -Recurse -Force -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue }
    Get-ChildItem -LiteralPath $AppDataAddon -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".pyc", ".pyo", ".blend1" } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -Confirm:$false -ErrorAction SilentlyContinue }
    foreach ($junk in @(".git", ".cursor", "docs", "MapPlacement")) {
        $p = Join-Path $AppDataAddon $junk
        if (Test-Path -LiteralPath $p) {
            Remove-Item -LiteralPath $p -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
        }
    }

    $stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffffffK"
    $ver = Get-AddonVersion
    @(
        "version=$ver"
        "synced=$stamp"
        "source=$RepoRoot"
        "pycache=%LOCALAPPDATA%\DataRaiders-BlenderImporter\pycache"
    ) | Set-Content -LiteralPath (Join-Path $AppDataAddon "ADDON_SYNC.txt") -Encoding UTF8

    # Mirror stamp into repo for agents
    Copy-Item -LiteralPath (Join-Path $AppDataAddon "ADDON_SYNC.txt") -Destination (Join-Path $RepoRoot "ADDON_SYNC.txt") -Force
}

# --- main ---
$ver = Get-AddonVersion
if (-not $OutZip) {
    $dist = Join-Path $RepoRoot "dist"
    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    $OutZip = Join-Path $dist "DataRaiders-BlenderImporter-$ver.zip"
}

Write-Host "Staging runtime addon v$ver ..."
$staged = New-StagingDir
try {
    $stageSize = (Get-ChildItem -LiteralPath $staged.AddonDir -Recurse -Force | Measure-Object Length -Sum).Sum
    Write-Host ("Stage size: {0:N2} MB" -f ($stageSize / 1MB))

    if (Test-Path -LiteralPath $OutZip) {
        Remove-Item -LiteralPath $OutZip -Force -Confirm:$false
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    # Zip contents so Blender Install sees DataRaiders-BlenderImporter/ at archive root
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        (Split-Path -Parent $staged.AddonDir),
        $OutZip,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )
    $zipSize = (Get-Item -LiteralPath $OutZip).Length
    Write-Host ("Wrote {0} ({1:N2} MB)" -f $OutZip, ($zipSize / 1MB))

    if ($SyncAppData) {
        Write-Host "Syncing clean tree to AppData ..."
        Sync-ToAppData -AddonDir $staged.AddonDir
        $appSize = (Get-ChildItem -LiteralPath $AppDataAddon -Recurse -Force | Measure-Object Length -Sum).Sum
        Write-Host ("AppData install: {0:N2} MB -> {1}" -f ($appSize / 1MB), $AppDataAddon)
    }
}
finally {
    Remove-Item -LiteralPath $staged.StageRoot -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
}

Write-Host "Done. Bytecode at runtime -> %LOCALAPPDATA%\DataRaiders-BlenderImporter\pycache"
