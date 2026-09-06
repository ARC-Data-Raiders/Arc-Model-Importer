<#
.SYNOPSIS
  Build a clean Blender addon zip (runtime modules only) and optionally sync to AppData.

.DESCRIPTION
  Excludes __pycache__, *.pyc, logs, tests/verify tools, docs, .git, .cursor, blend backups,
  and other repo-only tooling so the shipped zip does not balloon on every edit.

  Two SEPARATE Blender addons (different AppData folders — never overwrite each other):

    outfits       folder: DataRaiders-Outfits
                  bl_info: "Arc Model Importer"
                  branches: outfits-stable / pre-map-importer
                  AppData: %APPDATA%\Blender Foundation\Blender\5.1\scripts\addons\DataRaiders-Outfits
                  zip dir: dist/outfits/

    map-importer  folder: DataRaiders-MapImporter
                  bl_info: "Arc Raiders Map Importer"
                  branch: feature/map-importer
                  AppData: %APPDATA%\Blender Foundation\Blender\5.1\scripts\addons\DataRaiders-MapImporter
                  zip dir: dist/map-importer/

  Auto-detects from the current git branch; override with -Line.
  Zip inner root folder name always matches the AppData addon folder for that line.
  -SyncAppData writes ONLY to that line's folder (outfits never touches MapImporter and vice versa).

  Runtime bytecode is redirected by __init__.py to:
    %LOCALAPPDATA%\<AddonFolder>\pycache

.EXAMPLE
  .\package_addon.ps1
  .\package_addon.ps1 -SyncAppData
  .\package_addon.ps1 -Line outfits -SyncAppData
  .\package_addon.ps1 -Line map-importer -SyncAppData
  .\package_addon.ps1 -OutZip ".\dist\custom\DataRaiders-Outfits.zip"
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$OutZip = "",
    [ValidateSet("auto", "outfits", "map-importer")]
    [string]$Line = "auto",
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

# Folder names under Blender scripts/addons (also zip inner root).
$script:AddonFolderOutfits = "DataRaiders-Outfits"
$script:AddonFolderMap = "DataRaiders-MapImporter"
$script:BlInfoNameOutfits = "Arc Model Importer"
$script:BlInfoNameMap = "Arc Raiders Map Importer"

# Runtime files/dirs that Blender needs. Everything else stays in git but out of the zip.
$RootFilesShared = @(
    "__init__.py",
    "addon_line.py",
    "properties.py",
    "operators.py",
    "ui.py",
    "importing.py",
    "textures.py",
    "utils.py",
    "fmdex.py",
    "rig.py",
    "palette_calibration.py",
    "outfit_reference.csv",
    "asset_domain.py",
    "group_hotswap.py",
    "mask_debug.py",
    "ocm_zone_cache.py",
    "prop_assemble.py",
    "weapon_catalog.py",
    "weapon_catalog_data.py",
    "animation_catalog.py",
    "animation_import.py",
    "lighting_looks.py",
    "lighting_atmosphere.py",
    "niagara_curves.py",
    # Required for clothing/outfit ColorMask materials — without this, imports
    # succeed as bare Principled stubs ("Arc Texturer unavailable").
    "ArcTexturer.blend",
    "add-on-io-scene-psk-psa-v9_1_2.zip",
    "ADDON_SYNC.txt"
)

# Present on map-importer line (and optionally outfits for bridge helpers).
$RootFilesMapExtra = @(
    "map_placement.py",
    "map_hybrid.py",
    "map_family.py"
)

$RootDirs = @(
    "assets",
    "materials",
    "map_tools",
    "reference"
)

# Under map_tools: ship runtime helpers only (exclude analyze dumps / docs).
$MapToolsExcludeAlways = @(
    "_analyze_ism.py",
    "_analyze_ism_owners.py",
    "_ism_analysis.json",
    "_ism_owners.json",
    "_ism_samples.json",
    "_scan_decal_overrides.py",
    "_decal_override_scan.json",
    "_decal_actor_overrides_full.json",
    "FMODEL_BRIDGE.md",
    "__pycache__",
    "_tmp_airbag_extract"
)

# Map-only tooling — never ship inside the outfits addon folder.
$MapToolsExcludeOutfits = @(
    "map_placement_overlay.py",
    "map_placement_extract.py",
    "prepare_blender_heightmap.py"
) + $MapToolsExcludeAlways

function Get-AddonFolderName([string]$ReleaseLine) {
    if ($ReleaseLine -eq "map-importer") { return $script:AddonFolderMap }
    return $script:AddonFolderOutfits
}

function Get-BlInfoName([string]$ReleaseLine) {
    if ($ReleaseLine -eq "map-importer") { return $script:BlInfoNameMap }
    return $script:BlInfoNameOutfits
}

function Get-DefaultAppDataAddon([string]$ReleaseLine) {
    $folder = Get-AddonFolderName $ReleaseLine
    return (Join-Path $env:APPDATA "Blender Foundation\Blender\5.1\scripts\addons\$folder")
}

function Get-AddonVersion {
    $init = Join-Path $RepoRoot "__init__.py"
    $text = Get-Content -LiteralPath $init -Raw
    if ($text -match '"version"\s*:\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)') {
        return "{0}.{1}.{2}" -f $Matches[1], $Matches[2], $Matches[3]
    }
    return "0.0.0"
}

function Get-GitBranchName {
    $gitDir = Join-Path $RepoRoot ".git"
    if (-not (Test-Path -LiteralPath $gitDir)) { return "" }
    try {
        $branch = & git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -ne 0) { return "" }
        return ([string]$branch).Trim()
    }
    catch {
        return ""
    }
}

function Resolve-ReleaseLine {
    param(
        [string]$Requested,
        [string]$Branch
    )
    if ($Requested -and $Requested -ne "auto") {
        return $Requested
    }
    $b = ($Branch | ForEach-Object { $_ }).ToLowerInvariant()
    if ($b -eq "feature/map-importer" -or $b -eq "map-importer") {
        return "map-importer"
    }
    if ($b -eq "outfits-stable" -or $b -eq "pre-map-importer" -or $b -eq "outfits") {
        return "outfits"
    }
    # Heuristic for other local branch names
    if ($b -match "map-importer") { return "map-importer" }
    if ($b -match "outfit|pre-map") { return "outfits" }
    Write-Warning "Could not map git branch '$Branch' to a release line; defaulting to 'outfits'. Pass -Line outfits|map-importer to override."
    return "outfits"
}

function New-StagingDir {
    param([string]$ReleaseLine)

    $folderName = Get-AddonFolderName $ReleaseLine
    $stage = Join-Path $env:TEMP ("$folderName-stage-" + [guid]::NewGuid().ToString("n"))
    $addonStage = Join-Path $stage $folderName
    New-Item -ItemType Directory -Path $addonStage -Force | Out-Null

    $rootFiles = [System.Collections.Generic.List[string]]::new()
    foreach ($f in $RootFilesShared) { [void]$rootFiles.Add($f) }

    if ($ReleaseLine -eq "map-importer") {
        foreach ($f in $RootFilesMapExtra) { [void]$rootFiles.Add($f) }
    }
    else {
        # Outfits still needs map_placement for FModel TCP listener operator classes only.
        # Map UI is stripped from ui.py on the outfits line; map_tools map scripts are excluded below.
        if (Test-Path -LiteralPath (Join-Path $RepoRoot "map_placement.py")) {
            [void]$rootFiles.Add("map_placement.py")
        }
    }

    foreach ($f in $rootFiles) {
        $src = Join-Path $RepoRoot $f
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $addonStage $f) -Force
        }
    }

    $mapToolsExclude = if ($ReleaseLine -eq "map-importer") { $MapToolsExcludeAlways } else { $MapToolsExcludeOutfits }

    foreach ($d in $RootDirs) {
        $src = Join-Path $RepoRoot $d
        if (-not (Test-Path -LiteralPath $src)) { continue }
        $dst = Join-Path $addonStage $d
        if ($d -eq "map_tools") {
            New-Item -ItemType Directory -Path $dst -Force | Out-Null
            Get-ChildItem -LiteralPath $src -Force | ForEach-Object {
                if ($mapToolsExclude -contains $_.Name) { return }
                if ($_.Name -like "_tmp*") { return }
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

    # Clothing NCT node positions (GoalieShirt reference layout) — outfits line.
    if ($ReleaseLine -ne "map-importer") {
        $refSrc = Join-Path $RepoRoot "reference\goalie_shirt_nct_layout_index.json"
        if (Test-Path -LiteralPath $refSrc) {
            $refDstDir = Join-Path $addonStage "reference"
            New-Item -ItemType Directory -Path $refDstDir -Force | Out-Null
            Copy-Item -LiteralPath $refSrc -Destination (Join-Path $refDstDir "goalie_shirt_nct_layout_index.json") -Force
        }
    }

    return @{ StageRoot = $stage; AddonDir = $addonStage; FolderName = $folderName }
}

function Write-AddonLineFile([string]$AddonDir, [string]$ReleaseLine) {
    $line = if ($ReleaseLine -eq "map-importer") { "map-importer" } else { "outfits" }
    $folder = Get-AddonFolderName $line
    $path = Join-Path $AddonDir "addon_line.py"
    $content = @"
"""Release line for this install (outfits vs map-importer).

Baked by package_addon.ps1 (line=$line, folder=$folder). Do not edit AppData copies by hand;
re-run packaging with -Line / from the correct branch.

AppData paths (Blender 5.1):
  outfits      -> .../addons/DataRaiders-Outfits
  map-importer -> .../addons/DataRaiders-MapImporter
"""

from __future__ import annotations

# "outfits" | "map-importer"
ADDON_LINE = "$line"

MAP_IMPORTER_LINE = "map-importer"
OUTFITS_LINE = "outfits"

# Blender scripts/addons folder name for this package
ADDON_FOLDER = "$folder"


def normalize_line(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in (MAP_IMPORTER_LINE, "map", "maps"):
        return MAP_IMPORTER_LINE
    if raw in (OUTFITS_LINE, "outfit", "pre-map", "pre-map-importer"):
        return OUTFITS_LINE
    return OUTFITS_LINE


def is_map_importer_line() -> bool:
    """True when map importer UI / operators should register."""
    return normalize_line(ADDON_LINE) == MAP_IMPORTER_LINE


def is_outfits_line() -> bool:
    return not is_map_importer_line()
"@
    Set-Content -LiteralPath $path -Value $content -Encoding UTF8
}

function Update-StagedBlInfo([string]$AddonDir, [string]$ReleaseLine) {
    $initPath = Join-Path $AddonDir "__init__.py"
    if (-not (Test-Path -LiteralPath $initPath)) { return }
    $name = Get-BlInfoName $ReleaseLine
    if ($ReleaseLine -eq "map-importer") {
        $desc = "Import Arc Raiders map placements, geometry, and ground materials."
    }
    else {
        $desc = "Import Arc Raiders outfits, weapons, and models by selecting content folders."
    }
    $text = Get-Content -LiteralPath $initPath -Raw
    $updated = [regex]::Replace($text, '("name"\s*:\s*")([^"]*)(")', "`${1}$name`${3}", 1)
    $updated = [regex]::Replace($updated, '("description"\s*:\s*")([^"]*)(")', "`${1}$desc`${3}", 1)
    if ($updated -ne $text) {
        Set-Content -LiteralPath $initPath -Value $updated -Encoding UTF8 -NoNewline
    }
}

function Sync-ToAppData([string]$AddonDir, [string]$ReleaseLine, [string]$GitBranch, [string]$TargetAppData) {
    $folder = Get-AddonFolderName $ReleaseLine
    $parent = Split-Path $TargetAppData -Parent
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $TargetAppData)) {
        New-Item -ItemType Directory -Path $TargetAppData -Force | Out-Null
    }

    # Guard: never sync into the other line's folder by accident.
    $other = if ($ReleaseLine -eq "map-importer") { $script:AddonFolderOutfits } else { $script:AddonFolderMap }
    $leaf = Split-Path $TargetAppData -Leaf
    if ($leaf -eq $other) {
        throw "Refusing to sync line=$ReleaseLine into sibling folder '$other'. Target was: $TargetAppData"
    }
    if ($leaf -ne $folder) {
        Write-Warning "Target folder name '$leaf' != expected '$folder' for line=$ReleaseLine (explicit -AppDataAddon override)."
    }

    # Mirror runtime tree; purge extras (.git, verify_*, docs, pycache, logs, …).
    # /R:1 /W:1 tolerates Blender holding arc_raiders_debug.log open.
    robocopy $AddonDir $TargetAppData /MIR /NFL /NDL /NJH /NJS /nc /ns /np /R:1 /W:1 `
        /XF arc_raiders_debug.log outfit_toggles.txt | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy sync failed (exit $LASTEXITCODE)"
    }

    # Extra scrub for caches / leftovers robocopy may leave when locked
    Get-ChildItem -LiteralPath $TargetAppData -Recurse -Force -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue }
    Get-ChildItem -LiteralPath $TargetAppData -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".pyc", ".pyo", ".blend1" } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -Confirm:$false -ErrorAction SilentlyContinue }
    foreach ($junk in @(".git", ".cursor", "docs", "MapPlacement")) {
        $p = Join-Path $TargetAppData $junk
        if (Test-Path -LiteralPath $p) {
            Remove-Item -LiteralPath $p -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
        }
    }

    # Outfits: strip map-only tools if a prior polluted sync left them behind.
    if ($ReleaseLine -ne "map-importer") {
        $mt = Join-Path $TargetAppData "map_tools"
        foreach ($extra in $MapToolsExcludeOutfits) {
            $p = Join-Path $mt $extra
            if (Test-Path -LiteralPath $p) {
                Remove-Item -LiteralPath $p -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
            }
        }
        if (Test-Path -LiteralPath $mt) {
            Get-ChildItem -LiteralPath $mt -Force -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "_tmp*" } |
                ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue }
        }
        foreach ($mapOnly in @("map_hybrid.py", "map_family.py")) {
            $p = Join-Path $TargetAppData $mapOnly
            if (Test-Path -LiteralPath $p) {
                Remove-Item -LiteralPath $p -Force -Confirm:$false -ErrorAction SilentlyContinue
            }
        }
    }

    $stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffffffK"
    $ver = Get-AddonVersion
    # Portable markers only — never write absolute local user paths into ADDON_SYNC
    # (repo copy is gitignored; AppData copy is machine-local).
    $appdataMarker = "%APPDATA%\Blender Foundation\Blender\5.1\scripts\addons\$folder"
    $syncLines = @(
        "version=$ver"
        "line=$ReleaseLine"
        "folder=$folder"
        "bl_info_name=$(Get-BlInfoName $ReleaseLine)"
        "branch=$GitBranch"
        "synced=$stamp"
        "source=."
        "appdata=$appdataMarker"
        "pycache=%LOCALAPPDATA%\$folder\pycache"
    )
    $syncLines | Set-Content -LiteralPath (Join-Path $TargetAppData "ADDON_SYNC.txt") -Encoding UTF8

    # Mirror stamp into repo for agents (gitignored; keep portable)
    $syncLines | Set-Content -LiteralPath (Join-Path $RepoRoot "ADDON_SYNC.txt") -Encoding UTF8
}

# --- main ---
$ver = Get-AddonVersion
$gitBranch = Get-GitBranchName
$releaseLine = Resolve-ReleaseLine -Requested $Line -Branch $gitBranch
$folderName = Get-AddonFolderName $releaseLine
$blInfoName = Get-BlInfoName $releaseLine

if (-not $AppDataAddon) {
    $AppDataAddon = Get-DefaultAppDataAddon $releaseLine
}

if (-not $OutZip) {
    $dist = Join-Path $RepoRoot "dist"
    $lineDist = Join-Path $dist $releaseLine
    New-Item -ItemType Directory -Path $lineDist -Force | Out-Null
    $OutZip = Join-Path $lineDist ("{0}-{1}.zip" -f $folderName, $ver)
}

Write-Host "Staging $blInfoName v$ver"
Write-Host "  line=$releaseLine  branch=$gitBranch  folder=$folderName"
Write-Host "  AppData target: $AppDataAddon"
$staged = New-StagingDir -ReleaseLine $releaseLine
try {
    Write-AddonLineFile -AddonDir $staged.AddonDir -ReleaseLine $releaseLine
    Update-StagedBlInfo -AddonDir $staged.AddonDir -ReleaseLine $releaseLine
    $stageSize = (Get-ChildItem -LiteralPath $staged.AddonDir -Recurse -Force | Measure-Object Length -Sum).Sum
    Write-Host ("Stage size: {0:N2} MB" -f ($stageSize / 1MB))

    $outParent = Split-Path -Parent $OutZip
    if ($outParent -and -not (Test-Path -LiteralPath $outParent)) {
        New-Item -ItemType Directory -Path $outParent -Force | Out-Null
    }
    if (Test-Path -LiteralPath $OutZip) {
        Remove-Item -LiteralPath $OutZip -Force -Confirm:$false
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    # Zip contents so Blender Install sees <FolderName>/ at archive root
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        (Split-Path -Parent $staged.AddonDir),
        $OutZip,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )
    $zipSize = (Get-Item -LiteralPath $OutZip).Length
    Write-Host ("Wrote {0} ({1:N2} MB)" -f $OutZip, ($zipSize / 1MB))

    if ($SyncAppData) {
        Write-Host "Syncing clean tree to AppData (line=$releaseLine, folder=$folderName) ..."
        Sync-ToAppData -AddonDir $staged.AddonDir -ReleaseLine $releaseLine -GitBranch $gitBranch -TargetAppData $AppDataAddon
        $appSize = (Get-ChildItem -LiteralPath $AppDataAddon -Recurse -Force | Measure-Object Length -Sum).Sum
        Write-Host ("AppData install: {0:N2} MB -> {1}" -f ($appSize / 1MB), $AppDataAddon)
    }
}
finally {
    Remove-Item -LiteralPath $staged.StageRoot -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
}

Write-Host "Done. Enable in Blender: Edit → Preferences → Add-ons → search '$blInfoName'"
Write-Host "Bytecode at runtime -> %LOCALAPPDATA%\$folderName\pycache"
