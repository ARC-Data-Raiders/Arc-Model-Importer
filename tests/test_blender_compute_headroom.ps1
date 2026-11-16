param([switch]$Live)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$packagePath = Join-Path $repoRoot "package_addon.ps1"
$packageText = Get-Content -LiteralPath $packagePath -Raw
if ($packageText -match "ARC_BL_MAX|Get-Process blender|Blender process limit") {
    throw "Blender admission still uses a process-count cap"
}
$tokens = $null
$parseErrors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($packagePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "Package script parse errors: $parseErrors" }
$functionNames = @("Get-BlenderComputeHeadroom", "Assert-BlenderComputeHeadroom")
$definitions = @($tree.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $functionNames}, $true))
if ($definitions.Count -ne $functionNames.Count) { throw "The compute-headroom functions are missing" }
foreach ($definition in $definitions) {
    . ([ScriptBlock]::Create($definition.Extent.Text))
}
$script:checks = 0
function Assert-Admission([string]$Name, [psobject]$Headroom, [bool]$Expected, [switch]$RequireGpu) {
    $admitted = $true
    try { Assert-BlenderComputeHeadroom -Headroom $Headroom -RequireGpu:$RequireGpu | Out-Null }
    catch { $admitted = $false }
    if ($admitted -ne $Expected) { throw "Unexpected admission for $Name" }
    $script:checks++
}
function New-TestHeadroom([double]$Cpu = 30, [double]$Ram = 16, [array]$Gpus = @()) {
    [pscustomobject]@{ CpuPercent = $Cpu; AvailableMemoryGiB = $Ram; Gpus = $Gpus }
}
$busyGpu = [pscustomobject]@{ Index = 0; UtilizationPercent = 99; FreeMemoryMiB = 300 }
$freeGpu = [pscustomobject]@{ Index = 1; UtilizationPercent = 10; FreeMemoryMiB = 8000 }
Assert-Admission "CPU-only without GPU telemetry" (New-TestHeadroom) $true
Assert-Admission "CPU-only with busy GPUs" (New-TestHeadroom -Gpus @($busyGpu)) $true
Assert-Admission "second GPU has headroom" (New-TestHeadroom -Gpus @($busyGpu, $freeGpu)) $true -RequireGpu
Assert-Admission "all GPUs saturated" (New-TestHeadroom -Gpus @($busyGpu)) $false -RequireGpu
Assert-Admission "missing GPU telemetry for GPU work" (New-TestHeadroom) $false -RequireGpu
Assert-Admission "CPU pressure" (New-TestHeadroom -Cpu 95 -Gpus @($freeGpu)) $false
Assert-Admission "RAM pressure" (New-TestHeadroom -Ram 2 -Gpus @($freeGpu)) $false
Assert-Admission "CPU boundary" (New-TestHeadroom -Cpu 90) $false
Assert-Admission "RAM boundary" (New-TestHeadroom -Ram 4) $true
Assert-Admission "unknown CPU" ([pscustomobject]@{ AvailableMemoryGiB = 16; Gpus = @() }) $false
Assert-Admission "invalid CPU" (New-TestHeadroom -Cpu ([double]::NaN)) $false
Assert-Admission "invalid RAM" (New-TestHeadroom -Ram ([double]::PositiveInfinity)) $false
$gpuNoMemory = [pscustomobject]@{ Index = 1; UtilizationPercent = 10; FreeMemoryMiB = 500 }
Assert-Admission "GPU memory pressure" (New-TestHeadroom -Gpus @($gpuNoMemory)) $false -RequireGpu
$gpuBoundary = [pscustomobject]@{ Index = 1; UtilizationPercent = 89; FreeMemoryMiB = 1024 }
Assert-Admission "GPU boundary" (New-TestHeadroom -Gpus @($gpuBoundary)) $true -RequireGpu
$gpuUnknown = [pscustomobject]@{ Index = 1; UtilizationPercent = $null; FreeMemoryMiB = $null }
Assert-Admission "unknown GPU measurements" (New-TestHeadroom -Gpus @($gpuUnknown)) $false -RequireGpu
$gateDefinition = $tree.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq "Test-PackagedAddon"}, $true)
$gateText = $gateDefinition.Extent.Text
$admissionOffset = $gateText.IndexOf("Assert-BlenderComputeHeadroom")
$launchOffset = $gateText.IndexOf("[Diagnostics.Process]::Start")
if ($admissionOffset -lt 0 -or $admissionOffset -ge $launchOffset) { throw "The package gate must check headroom before launch" }
if ($gateText -match "--threads|OMP_NUM_THREADS|OPENBLAS_NUM_THREADS|DOTNET_PROCESSOR_COUNT") { throw "The package gate still has fixed worker-thread caps" }
$script:checks++
$script:gpuQueryMode = "valid"
function Get-CimInstance {
    param([string]$ClassName, [string]$Filter)
    if ($ClassName -eq "Win32_OperatingSystem") {
        return [pscustomobject]@{ FreePhysicalMemory = 16 * 1MB }
    }
    [pscustomobject]@{ PercentProcessorTime = 30 }
}
function Get-Command {
    param([string]$Name, [string]$CommandType)
    if ($script:gpuQueryMode -eq "absent") { return $null }
    [pscustomobject]@{ Source = "Invoke-TestGpuQuery" }
}
function Invoke-TestGpuQuery {
    if ($script:gpuQueryMode -eq "failure") { throw "GPU driver telemetry failed" }
    $global:LASTEXITCODE = 0
    "0, 99, 300"
    "1, 10, 8000"
    "2, [N/A], [N/A]"
}
try {
    $snapshot = Get-BlenderComputeHeadroom
    if ($snapshot.CpuPercent -ne 30 -or $snapshot.AvailableMemoryGiB -ne 16 -or
        $snapshot.Gpus.Count -ne 2 -or $snapshot.Gpus[1].Index -ne 1) {
        throw "CPU/RAM conversion or per-GPU telemetry parsing failed"
    }
    $script:checks++
    foreach ($mode in @("absent", "failure")) {
        $script:gpuQueryMode = $mode
        $snapshot = Get-BlenderComputeHeadroom
        if ($snapshot.Gpus.Count -ne 0) { throw "Unavailable GPU telemetry was not preserved" }
        Assert-Admission "CPU-only with $mode telemetry" $snapshot $true
        Assert-Admission "GPU-required with $mode telemetry" $snapshot $false -RequireGpu
    }
}
finally {
    Remove-Item Function:\Get-CimInstance, Function:\Get-Command, Function:\Invoke-TestGpuQuery
}
if ($Live) {
    $headroom = Get-BlenderComputeHeadroom
    Assert-BlenderComputeHeadroom -Headroom $headroom
    Assert-BlenderComputeHeadroom -Headroom $headroom -RequireGpu
    $headroom | ConvertTo-Json -Depth 4 -Compress
    $script:checks += 2
}
Write-Output "BLENDER_COMPUTE_HEADROOM_OK: $script:checks checks passed"
