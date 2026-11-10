param([Parameter(Mandatory=$true)][string]$ScratchRoot)
$ErrorActionPreference='Stop'
$testRepo=Split-Path -Parent $PSScriptRoot
$testScratch=[IO.Path]::GetFullPath($ScratchRoot)
if ([IO.Path]::GetPathRoot($testScratch) -ne 'Z:\') { throw 'Use the designated Z scratch root' }
$testProbe=Join-Path $testScratch ('sync-preservation-'+[guid]::NewGuid().ToString('N'))
$resolvedProbe=[IO.Path]::GetFullPath($testProbe)
if (-not $resolvedProbe.StartsWith($testScratch.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Test path escaped scratch' }
$RepoRoot=Join-Path $testProbe 'repository'
$testStage=Join-Path $testProbe 'stage\Arc-Asset-Importer'
$testTarget=Join-Path $testProbe 'profile\Arc-Asset-Importer'
$script:AddonFolderOutfits='Arc-Asset-Importer';$script:AddonFolderMap='DataRaiders-MapImporter'
$script:BlInfoNameOutfits='Arc Asset Importer';$script:BlInfoNameMap='Arc Raiders Map Importer'
$tokens=$null;$parseErrors=$null
$tree=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $testRepo 'package_addon.ps1'),[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw 'Package script parse errors' }
$names=@('Get-AddonFolderName','Get-BlInfoName','Get-AddonVersion','Sync-ToAppData')
foreach ($definition in $tree.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $names},$true)) {
    . ([ScriptBlock]::Create($definition.Extent.Text))
}
New-Item -ItemType Directory -Force -Path $RepoRoot,$testStage,$testTarget,(Join-Path $testTarget 'ArcPak\cache\user-data'),(Join-Path $testTarget 'assets\private'),(Join-Path $testTarget 'materials\__pycache__') | Out-Null
'bl_info = {"version": (2, 20, 0)}' | Set-Content -LiteralPath (Join-Path $RepoRoot '__init__.py')
'new runtime' | Set-Content -LiteralPath (Join-Path $testStage 'runtime_probe.py')
'old runtime' | Set-Content -LiteralPath (Join-Path $testTarget 'runtime_probe.py')
'original blend placeholder' | Set-Content -LiteralPath (Join-Path $testTarget 'user_scene.blend')
'user custom setting' | Set-Content -LiteralPath (Join-Path $testTarget 'custom_setting.txt')
'user pak cache' | Set-Content -LiteralPath (Join-Path $testTarget 'ArcPak\cache\user-data\sentinel.bin')
'user asset' | Set-Content -LiteralPath (Join-Path $testTarget 'assets\private\sentinel.png')
'obsolete bytecode' | Set-Content -LiteralPath (Join-Path $testTarget 'materials\__pycache__\runtime.pyc')
Sync-ToAppData -AddonDir $testStage -ReleaseLine 'outfits' -GitBranch 'local-test' -TargetAppData $testTarget
if ((Get-Content -LiteralPath (Join-Path $testTarget 'runtime_probe.py') -Raw).Trim() -ne 'new runtime') { throw 'Runtime was not updated' }
foreach ($relative in @('user_scene.blend','custom_setting.txt','ArcPak\cache\user-data\sentinel.bin','assets\private\sentinel.png')) {
    if (-not (Test-Path -LiteralPath (Join-Path $testTarget $relative) -PathType Leaf)) { throw "User file lost: $relative" }
}
if (Test-Path -LiteralPath (Join-Path $testTarget 'materials\__pycache__')) { throw 'Generated runtime bytecode was not cleared' }
if (-not $resolvedProbe.StartsWith($testScratch.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing cleanup outside scratch' }
Remove-Item -LiteralPath $resolvedProbe -Recurse -Force
Write-Output 'PACKAGE_SYNC_PRESERVATION_OK: runtime updated; original blend, custom settings, asset cache preserved; runtime bytecode cleared'
exit 0

