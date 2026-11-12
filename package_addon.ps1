<#
.SYNOPSIS
  Build a clean Blender addon zip (runtime modules only) and optionally sync to AppData.

.DESCRIPTION
  Excludes __pycache__, *.pyc, logs, tests/verify tools, docs, .git, .cursor, blend backups,
  and other repo-only tooling so the shipped zip does not balloon on every edit.

  Two SEPARATE Blender addons (different AppData folders — never overwrite each other):

    outfits       folder: Arc-Asset-Importer
                  bl_info: "Arc Asset Importer"
                  branches: outfits-stable / pre-map-importer
                  AppData: %APPDATA%\Blender Foundation\Blender\5.1\scripts\addons\Arc-Asset-Importer
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
  .\package_addon.ps1 -OutZip ".\dist\custom\Arc-Asset-Importer.zip"
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$OutZip = "",
    [ValidateSet("auto", "outfits", "map-importer")]
    [string]$Line = "auto",
    [switch]$SyncAppData,
    [string]$AppDataAddon = "",
    [string]$BlenderExe = "",
    [string]$InstallCheckRoot = "",
    # ArcPak exporter source. Cue4ParseRoot (or env ARCPAK_CUE4PARSE_ROOT) = perf CUE4Parse tree:
    # publish from current sources against it. Without it: copy the prebuilt binaries in ArcPak/.
    # Either way the packaged CUE4Parse.Arc.dll must carry the perf types, or packaging fails.
    [string]$Cue4ParseRoot = $env:ARCPAK_CUE4PARSE_ROOT,
    [ValidateSet("", "Fpng", "FastPng")]
    [string]$ArcPakPng = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    if ($PSScriptRoot) { $RepoRoot = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $RepoRoot = (Get-Location).Path }
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

# Folder names under Blender scripts/addons (also zip inner root).
$script:AddonFolderOutfits = "Arc-Asset-Importer"
$script:LegacyAddonFolderOutfits = "DataRaiders-Outfits"
$script:AddonFolderMap = "DataRaiders-MapImporter"
$script:BlInfoNameOutfits = "Arc Asset Importer"
$script:BlInfoNameMap = "Arc Raiders Map Importer"

# Runtime files/dirs that Blender needs. Everything else stays in git but out of the zip.
$RootFilesShared = @(
    "__init__.py",
    "fmdex.py",
    "sidebar.py",
    # Required for clothing/outfit ColorMask materials; without this, imports
    # succeed as bare Principled stubs ("Arc Texturer unavailable").
    "ArcTexturer.blend",
    "add-on-io-scene-psk-psa-v9_1_2.zip",
    "ADDON_SYNC.txt"
)

# One folder per sidebar section plus Data/ catalogs. Each ships whole (minus excludes below),
# so a new runtime module in any of them is packaged without editing this list.
$SectionDirs = @(
    "Outfit_Accessories",
    "Weapons",
    "Arc",
    "Animations",
    "Niagara",
    "Lighting",
    "Maps",
    "Script_Helpers",
    "Shared",
    "Data"
)

# Dev-only scripts and local (gitignored) data inside the section folders.
$SectionExcludeFiles = @(
    "build_outfit_reference.py",
    "package_addon.py",
    "parse_outfit_toggles.py",
    "arc_palette_calibration.json",
    "arc_white_materials_inventory.json"
)

$RootDirs = @(
    "assets",
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
    return ($script:BlInfoNameOutfits + " v" + (Get-AddonVersion))
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
    if ($b -eq "outfits-stable" -or $b -eq "pre-map-importer" -or $b -eq "outfits" -or $b -eq "pakdirect") {
        return "outfits"
    }
    # Heuristic for other local branch names
    if ($b -match "map-importer") { return "map-importer" }
    if ($b -match "outfit|pre-map") { return "outfits" }
    Write-Warning "Could not map git branch '$Branch' to a release line; defaulting to 'outfits'. Pass -Line outfits|map-importer to override."
    return "outfits"
}

function Copy-ArcPakRuntime {
    param([string]$AddonDir, [string]$ReleaseLine)
    if ($ReleaseLine -eq "map-importer") { return }
    $proj = Join-Path $RepoRoot "ArcPak\ArcPak.Export.csproj"
    if (-not (Test-Path -LiteralPath $proj)) { return }
    $arcDir = Join-Path $RepoRoot "ArcPak"
    $dst = Join-Path $AddonDir "ArcPak"
    $runtimeRoot = Join-Path $env:TEMP ("ArcPak-runtime-" + [guid]::NewGuid().ToString("n"))
    $publish = Join-Path $runtimeRoot "publish"
    $buildRoot = Join-Path $runtimeRoot "build"
    try {
        if ($Cue4ParseRoot) {
            # Build from current sources against the perf CUE4Parse tree.
            $c4p = (Resolve-Path -LiteralPath $Cue4ParseRoot -ErrorAction Stop).Path
            if (-not (Test-Path -LiteralPath (Join-Path $c4p "CUE4Parse\CUE4Parse.csproj"))) { throw "Cue4ParseRoot has no CUE4Parse\CUE4Parse.csproj: $c4p" }
            $pngArg = @(); if ($ArcPakPng) { $pngArg = @("-p:ArcPakPng=$ArcPakPng") }
            Write-Host "Publishing ArcPak.Export against $c4p (png=$(if ($ArcPakPng) { $ArcPakPng } else { 'auto' }))..."
            & dotnet publish $proj -c Release -r win-x64 --self-contained true -m:4 -o $publish "-p:ArcPakBuildRoot=$buildRoot" "-p:Cue4ParseRoot=$c4p" @pngArg --nologo
            if ($LASTEXITCODE -ne 0) { throw "ArcPak.Export publish failed ($LASTEXITCODE)" }
            $src = $publish
        }
        elseif ((Test-Path -LiteralPath (Join-Path $arcDir "ArcPak.Export.exe")) -and (Test-Path -LiteralPath (Join-Path $arcDir "CUE4Parse.Arc.dll"))) {
            # Prebuilt exporter in ArcPak/ (the promoted, measured build).
            Write-Host "Copying prebuilt ArcPak.Export from $arcDir (no -Cue4ParseRoot)..."
            $newestSrc = Get-ChildItem -LiteralPath $arcDir -File -Filter *.cs | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            $built = (Get-Item -LiteralPath (Join-Path $arcDir "ArcPak.Export.dll")).LastWriteTimeUtc
            if ($newestSrc -and $newestSrc.LastWriteTimeUtc -gt $built) { Write-Warning "ArcPak/$($newestSrc.Name) is newer than prebuilt ArcPak.Export.dll; rebuild with -Cue4ParseRoot to ship it." }
            $src = $arcDir
        }
        else {
            throw "No ArcPak exporter source: pass -Cue4ParseRoot <perf CUE4Parse tree> (or set ARCPAK_CUE4PARSE_ROOT), or place prebuilt ArcPak.Export.exe + CUE4Parse.Arc.dll in $arcDir."
        }
        $exe = Join-Path $src "ArcPak.Export.exe"
        if (-not (Test-Path -LiteralPath $exe)) { throw "ArcPak.Export source has no executable: $src" }
        New-Item -ItemType Directory -Path $dst -Force | Out-Null
        Get-ChildItem -LiteralPath $src -File | Where-Object { @(".dll", ".exe", ".json") -contains $_.Extension -and @("BouncyCastle.Cryptography.dll") -notcontains $_.Name } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dst $_.Name) -Force
        }
        # Gate: the shipped CUE4Parse must be the perf build.
        $c4dll = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes((Join-Path $dst "CUE4Parse.Arc.dll")))
        foreach ($t in @("TheiaKeystream", "PerfCounters")) {
            if (-not $c4dll.Contains("`0$t`0")) { throw "Packaged CUE4Parse.Arc.dll lacks perf type $t (source $src); use -Cue4ParseRoot <perf CUE4Parse tree>." }
        }
        $convDll = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes((Join-Path $dst "CUE4Parse.Conversion.Arc.dll")))
        $png = if ($convDll.Contains("`0FastPng`0")) { "FastPng" } else { "Fpng" }
        if ($png -eq "Fpng" -and -not (Test-Path -LiteralPath (Join-Path $dst "fpng.dll"))) { throw "Packaged exporter uses Fpng but fpng.dll is missing" }
        if ($ArcPakPng -and $png -ne $ArcPakPng) { throw "Packaged exporter PNG path $png != requested $ArcPakPng" }
        Write-Host "ArcPak exporter: perf CUE4Parse, png=$png"
    }
    finally {
        if (Test-Path -LiteralPath $runtimeRoot) {
            $resolvedRuntime = (Resolve-Path -LiteralPath $runtimeRoot).Path
            $resolvedTemp = (Resolve-Path -LiteralPath $env:TEMP).Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
            if (-not $resolvedRuntime.StartsWith($resolvedTemp + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing ArcPak build cleanup outside the configured temporary root: $resolvedRuntime"
            }
            Remove-Item -LiteralPath $resolvedRuntime -Recurse -Force -Confirm:$false
        }
    }
    # pak-index.bin: regenerate on each game patch: ArcPak.Export.exe reindex --game-dir <Paks> --index-dir <tmp> --out <tmp> --usmap <usmap>; copy <tmp>/pak-index-<buildid>.bin here
    $mapSrc = Join-Path $RepoRoot "ArcPak\mappings"
    if (Test-Path -LiteralPath $mapSrc) {
        $mapDst = Join-Path $dst "mappings"
        New-Item -ItemType Directory -Path $mapDst -Force | Out-Null
        Get-ChildItem -LiteralPath $mapSrc -File -ErrorAction SilentlyContinue | Where-Object {
            $_.Extension -eq ".usmap" -or $_.Name -eq "pak-index.bin"
        } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $mapDst $_.Name) -Force
        }
    }
}

function New-StagingDir {
    param([string]$ReleaseLine)

    $folderName = Get-AddonFolderName $ReleaseLine
    $stage = Join-Path $env:TEMP ("$folderName-stage-" + [guid]::NewGuid().ToString("n"))
    $addonStage = Join-Path $stage $folderName
    New-Item -ItemType Directory -Path $addonStage -Force | Out-Null

    $rootFiles = [System.Collections.Generic.List[string]]::new()
    foreach ($f in $RootFilesShared) { [void]$rootFiles.Add($f) }

    foreach ($f in $rootFiles) {
        $src = Join-Path $RepoRoot $f
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $addonStage $f) -Force
        }
    }

    $mapToolsExclude = if ($ReleaseLine -eq "map-importer") { $MapToolsExcludeAlways } else { $MapToolsExcludeOutfits }

    foreach ($d in $SectionDirs) {
        $src = Join-Path $RepoRoot $d
        if (-not (Test-Path -LiteralPath $src)) { throw "Missing addon section folder: $src" }
        $dst = Join-Path $addonStage $d
        $xd = @("__pycache__", ".git")
        if ($d -eq "Maps") { $xd += "map_tools" }
        robocopy $src $dst /E /NFL /NDL /NJH /NJS /nc /ns /np `
            /XD @xd `
            /XF *.pyc *.pyo *.log *.blend1 Thumbs.db @SectionExcludeFiles | Out-Null
        if ($LASTEXITCODE -ge 8) {
            throw "robocopy failed for $d (exit $LASTEXITCODE)"
        }
    }

    foreach ($d in @("Maps\map_tools") + $RootDirs) {
        $src = Join-Path $RepoRoot $d
        if (-not (Test-Path -LiteralPath $src)) { continue }
        $dst = Join-Path $addonStage $d
        if ($d -eq "Maps\map_tools") {
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

    # Current material loaders require a reviewed native bank. Retired historical
    # facts stay in the repository for provenance, but are not shipped again.
    if (Test-Path -LiteralPath (Join-Path $addonStage "Shared\materials\native\native_data.py")) {
        $nativeDataRoot = [IO.Path]::GetFullPath((Join-Path $addonStage "Shared\materials\rebuild\data"))
        $nativeManifestPath = Join-Path $nativeDataRoot "native_catalog_active.json"
        if (-not (Test-Path -LiteralPath $nativeManifestPath)) { throw "Missing reviewed current-native material catalog manifest" }
        $nativeManifest = Get-Content -LiteralPath $nativeManifestPath -Raw | ConvertFrom-Json
        if ($nativeManifest.schema -ne "native-catalog-activation-v2" -or $nativeManifest.reviewed_source_complete -ne $true) {
            throw "Current-native catalog requires an explicit reviewed source manifest"
        }
        $nativeBank = [IO.Path]::GetFullPath((Join-Path $nativeDataRoot ([string]$nativeManifest.catalog)))
        $nativeDataPrefix = $nativeDataRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if (-not $nativeBank.StartsWith($nativeDataPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Native catalog path escaped data directory" }
        $nativeBankHash = (Get-FileHash -LiteralPath $nativeBank -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($nativeBankHash -ne ([string]$nativeManifest.sha256).ToLowerInvariant()) { throw "Current-native catalog content hash mismatch" }
        $stagePrefix = [IO.Path]::GetFullPath($addonStage).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        foreach ($retiredRelative in @(
            "assets\preset_defaults.json"
        )) {
            $retiredPath = [IO.Path]::GetFullPath((Join-Path $addonStage $retiredRelative))
            if (-not $retiredPath.StartsWith($stagePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Retired stage path escaped addon" }
            if (Test-Path -LiteralPath $retiredPath -PathType Leaf) { Remove-Item -LiteralPath $retiredPath -Force }
        }
    }

    # Belt-and-suspenders: scrub any cache that slipped in
    Get-ChildItem -LiteralPath $addonStage -Recurse -Force -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue }
    Get-ChildItem -LiteralPath $addonStage -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".pyc", ".pyo", ".log", ".blend1" } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -Confirm:$false -ErrorAction SilentlyContinue }

    # Runtime shader layout and slot-one reference-card calibration.
    if ($ReleaseLine -ne "map-importer") {
        $refDstDir = Join-Path $addonStage "reference"
        New-Item -ItemType Directory -Path $refDstDir -Force | Out-Null
        foreach ($refName in @("goalie_shirt_nct_layout_index.json", "reference_card_defaults.json")) {
            $refSrc = Join-Path (Join-Path $RepoRoot "reference") $refName
            if (-not (Test-Path -LiteralPath $refSrc)) { throw "Missing runtime reference file: $refSrc" }
            Copy-Item -LiteralPath $refSrc -Destination (Join-Path $refDstDir $refName) -Force
        }
    }

    Copy-ArcPakRuntime -AddonDir $addonStage -ReleaseLine $ReleaseLine

    return @{ StageRoot = $stage; AddonDir = $addonStage; FolderName = $folderName }
}

function Write-AddonLineFile([string]$AddonDir, [string]$ReleaseLine) {
    $line = if ($ReleaseLine -eq "map-importer") { "map-importer" } else { "outfits" }
    $folder = Get-AddonFolderName $line
    $path = Join-Path $AddonDir "Outfit_Accessories\addon_line.py"
    $content = @"
"""Release line for this install (outfits vs map-importer).

Baked by package_addon.ps1 (line=$line, folder=$folder). Do not edit AppData copies by hand;
re-run packaging with -Line / from the correct branch.

AppData paths (Blender 5.1):
  outfits      -> .../addons/Arc-Asset-Importer
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


def sibling_addon_module_candidates() -> tuple[str, ...]:
    if is_map_importer_line():
        return ("Arc-Asset-Importer",)
    return ("DataRaiders-MapImporter",)


def sibling_addon_enabled() -> bool:
    try:
        import addon_utils

        for mod in sibling_addon_module_candidates():
            _default, loaded = addon_utils.check(mod)
            if loaded:
                return True
    except Exception:
        pass
    return False
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
    # Rename migration: move the old install folder (with its ArcPak cache) to the new
    # module name so the cache is kept and two copies of the addon never load.
    if ($ReleaseLine -ne "map-importer") {
        $legacy = Join-Path $parent $script:LegacyAddonFolderOutfits
        if ((Test-Path -LiteralPath $legacy) -and ((Split-Path $TargetAppData -Leaf) -eq $folder)) {
            if (Test-Path -LiteralPath $TargetAppData) {
                throw "Both '$legacy' and '$TargetAppData' exist. Close Blender, keep one, then sync again."
            }
            try { Move-Item -LiteralPath $legacy -Destination $TargetAppData -ErrorAction Stop }
            catch { throw "Could not rename '$legacy' to '$folder' (close Blender so its files are not locked): $_" }
            Write-Host "Migrated AppData install: $($script:LegacyAddonFolderOutfits) -> $folder"
        }
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

    # Update shipped runtime files without mirroring away user blends, caches,
    # custom files, or historical compatibility resources in the addon directory.
    $resolvedTarget = [IO.Path]::GetFullPath($TargetAppData)
    $targetItem = Get-Item -LiteralPath $resolvedTarget -Force
    if ($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing runtime sync into a linked addon root" }
    $targetPrefix = $resolvedTarget.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $runtimeCache = [IO.Path]::GetFullPath((Join-Path $resolvedTarget "ArcPak\cache"))
    $stagedCache = Join-Path $AddonDir "ArcPak\cache"
    robocopy $AddonDir $resolvedTarget /E /IS /IT /XJ /NFL /NDL /NJH /NJS /nc /ns /np /R:1 /W:1 `
        /XD $runtimeCache $stagedCache /XF arc_raiders_debug.log outfit_toggles.txt | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy sync failed (exit $LASTEXITCODE)" }

    # Only generated runtime bytecode is disposable. Never traverse the pak cache
    # or data/asset folders, and never follow a junction for cleanup.
    $skipRuntimeDirs = @("arcpak", "assets", "data", "cache", ".local", ".git", "dist", "docs", "tests")
    $runtimeQueue = [Collections.Generic.Queue[string]]::new()
    $runtimeQueue.Enqueue($resolvedTarget)
    while ($runtimeQueue.Count -gt 0) {
        $runtimeDirectory = $runtimeQueue.Dequeue()
        foreach ($child in @(Get-ChildItem -LiteralPath $runtimeDirectory -Force -Directory -ErrorAction SilentlyContinue)) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($skipRuntimeDirs -contains $child.Name.ToLowerInvariant()) { continue }
            $childPath = [IO.Path]::GetFullPath($child.FullName)
            if (-not $childPath.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Runtime cleanup path escaped addon directory" }
            if ($child.Name -eq "__pycache__") {
                Remove-Item -LiteralPath $childPath -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
            }
            else { $runtimeQueue.Enqueue($childPath) }
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

function Resolve-BlenderExe([string]$Requested) {
    $candidates = @(
        $Requested,
        $env:BLENDER_EXE,
        "C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
        "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $onPath = Get-Command blender -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    throw "Blender is required for the package install check. Pass -BlenderExe or set BLENDER_EXE."
}

function Test-PackagedAddon([string]$ZipPath, [string]$Folder, [string]$Version, [string]$StageRoot, [string]$Executable) {
    # Keep the private profile path short: Blender's ZIP extractor still hits
    # Windows MAX_PATH with deeply nested addon icon filenames.
    $requestedCheckRoot = if ($InstallCheckRoot) { $InstallCheckRoot } elseif ($env:DATARAIDERS_INSTALL_CHECK_ROOT) { $env:DATARAIDERS_INSTALL_CHECK_ROOT } else { Split-Path $StageRoot -Parent }
    $scratchBase = (Resolve-Path -LiteralPath $requestedCheckRoot).Path
    $checkRoot = Join-Path $scratchBase ("ic-" + [guid]::NewGuid().ToString("n").Substring(0, 8))
    $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $longestInstallPath = 0
        foreach ($entry in $archive.Entries) {
            $entryPath = [IO.Path]::GetFullPath((Join-Path (Join-Path $checkRoot "scripts\addons") $entry.FullName))
            $longestInstallPath = [Math]::Max($longestInstallPath, $entryPath.Length)
        }
    }
    finally { $archive.Dispose() }
    if ($longestInstallPath -ge 260) {
        throw "Private install profile would exceed Blender ZIP extraction path limits ($longestInstallPath chars). Pass -InstallCheckRoot with a shorter existing scratch directory."
    }
    foreach ($part in @("config", "scripts", "extensions", "local", "temp")) {
        New-Item -ItemType Directory -Path (Join-Path $checkRoot $part) -Force | Out-Null
    }
    $privateEnv = @{
        BLENDER_USER_CONFIG = (Join-Path $checkRoot "config")
        BLENDER_USER_SCRIPTS = (Join-Path $checkRoot "scripts")
        BLENDER_USER_EXTENSIONS = (Join-Path $checkRoot "extensions")
        APPDATA = $checkRoot
        LOCALAPPDATA = (Join-Path $checkRoot "local")
        TEMP = (Join-Path $checkRoot "temp")
        TMP = (Join-Path $checkRoot "temp")
        PYTHONDONTWRITEBYTECODE = "1"
        OMP_NUM_THREADS = "4"
        OPENBLAS_NUM_THREADS = "1"
        DOTNET_PROCESSOR_COUNT = "4"
    }
    $oldEnv = @{}
    foreach ($key in $privateEnv.Keys) {
        $oldEnv[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, $privateEnv[$key], "Process")
    }
    try {
        $checkScript = Join-Path $RepoRoot "scripts\addon_install_smoke.py"
        Write-Host "Checking packaged ZIP through Blender install/enable/overwrite ..."
        $oldErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $checkOutput = & $Executable --background --factory-startup -t 4 --python-exit-code 1 --python $checkScript -- (Resolve-Path -LiteralPath $ZipPath).Path $Folder $Version 2>&1
            $checkExit = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $oldErrorAction
        }
        $checkText = ($checkOutput | Out-String)
        Write-Host $checkText
        if ($checkExit -ne 0 -or $checkText -notmatch "INSTALL_CHECK_OK" -or $checkText -match "Traceback|Error:") {
            throw "Packaged addon install check failed (Blender exit code $checkExit); refusing AppData sync."
        }
    }
    finally {
        foreach ($key in $privateEnv.Keys) {
            [Environment]::SetEnvironmentVariable($key, $oldEnv[$key], "Process")
        }
        $resolvedCheckRoot = (Resolve-Path -LiteralPath $checkRoot -ErrorAction SilentlyContinue).Path
        if ($resolvedCheckRoot -and $resolvedCheckRoot.StartsWith($scratchBase + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedCheckRoot -Recurse -Force -Confirm:$false
        }
    }
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
    # Shared precompute (assets/precompute, shipped with assets): size vs budget, warning only.
    $precomputeDir = Join-Path $staged.AddonDir "assets\precompute"
    if (Test-Path -LiteralPath $precomputeDir) {
        $precomputeMB = ((Get-ChildItem -LiteralPath $precomputeDir -Recurse -Force -File | Measure-Object Length -Sum).Sum) / 1MB
        Write-Host ("precompute {0:N2} MB / 20 MB (+5 weapons)" -f $precomputeMB)
        if ($precomputeMB -gt 25) { Write-Warning ("precompute over budget: {0:N2} MB" -f $precomputeMB) }
        # Shipped wrapper templates (graphs.blend) match only the graph_blocks.py they were built with.
        $graphsJson = Join-Path $precomputeDir "graphs.json"
        if (Test-Path -LiteralPath $graphsJson) {
            $built = (Get-Content -Raw -LiteralPath $graphsJson | ConvertFrom-Json).blocks_recipe
            $now = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $staged.AddonDir "Shared\materials\graph_blocks.py")).Hash.ToLower().Substring(0, 12)
            if ($built -ne $now) { Write-Warning ("graphs.blend is stale (built for graph_blocks {0}, now {1}); templates ignored until rebuilt (X:/Agents/DataRaiders-BlenderImporter/outfit-speed/tools/build_shipped_graphs.py)" -f $built, $now) }
            else { Write-Host ("graphs.blend templates match graph_blocks {0}" -f $now) }
        }
    }
    else { Write-Warning "precompute folder missing from stage (assets\precompute); live fallback only" }

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

    $blenderPath = Resolve-BlenderExe $BlenderExe
    Test-PackagedAddon -ZipPath $OutZip -Folder $folderName -Version $ver -StageRoot $staged.StageRoot -Executable $blenderPath

    if ($SyncAppData) {
        Write-Host "Syncing clean tree to AppData (line=$releaseLine, folder=$folderName) ..."
        Sync-ToAppData -AddonDir $staged.AddonDir -ReleaseLine $releaseLine -GitBranch $gitBranch -TargetAppData $AppDataAddon
        $appSize = (Get-ChildItem -LiteralPath $AppDataAddon -Recurse -Force | Measure-Object Length -Sum).Sum
        Write-Host ("AppData install: {0:N2} MB -> {1}" -f ($appSize / 1MB), $AppDataAddon)
    }
}
finally {
    $resolvedStage = (Resolve-Path -LiteralPath $staged.StageRoot -ErrorAction SilentlyContinue).Path
    $resolvedStageBase = (Resolve-Path -LiteralPath $env:TEMP).Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
    if ($resolvedStage) {
        if (-not $resolvedStage.StartsWith($resolvedStageBase + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing staging cleanup outside the configured temporary root: $resolvedStage"
        }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
    }
}

Write-Host "Done. Enable in Blender: Edit → Preferences → Add-ons → search '$blInfoName'"
Write-Host "Bytecode at runtime -> %LOCALAPPDATA%\$folderName\pycache"

# Robocopy success codes 0-7 must not become the package command exit status.
$global:LASTEXITCODE = 0

