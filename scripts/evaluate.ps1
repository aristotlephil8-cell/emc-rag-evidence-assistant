[CmdletBinding()]
param(
    [string]$Output = "artifacts/evaluation/latest.json",
    [string]$ModelDir = "models/deepdoc",
    [string]$ElasticsearchUrl = "http://localhost:9200",
    [string]$CondaEnvironment = "cvrag",
    [switch]$ValidateOnly,
    [switch]$SkipAssetPreparation
)

$ErrorActionPreference = "Stop"

function Assert-NotTemporaryPath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    $normalized = $PathValue.Replace("/", "\")
    if ($normalized -match "(^|\\)(tmp|\.tmp)(\\|$)") {
        throw "Temporary tmp/.tmp paths are forbidden for CVRAG evaluation inputs and outputs."
    }
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found. Create the '$CondaEnvironment' environment first."
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$validator = Join-Path $repositoryRoot "evaluation/validate_dataset.py"
$assetPreparer = Join-Path $repositoryRoot "backend/scripts/prepare_deepdoc_assets.py"
$evaluationRunner = Join-Path $repositoryRoot "backend/scripts/run_evaluation.py"
$modelPath = if ([System.IO.Path]::IsPathRooted($ModelDir)) {
    [System.IO.Path]::GetFullPath($ModelDir)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $ModelDir))
}
$outputPath = if ([System.IO.Path]::IsPathRooted($Output)) {
    [System.IO.Path]::GetFullPath($Output)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $Output))
}
Assert-NotTemporaryPath -PathValue $modelPath
Assert-NotTemporaryPath -PathValue $outputPath

Push-Location $repositoryRoot
try {
    & conda run --no-capture-output -n $CondaEnvironment python $validator --repo-root $repositoryRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Frozen synthetic dataset validation failed."
    }

    if ($ValidateOnly) {
        Write-Host "Frozen synthetic dataset is valid. No metrics were generated."
        return
    }
    if ([string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY)) {
        throw "DASHSCOPE_API_KEY must be set in the current process environment."
    }

    if (-not $SkipAssetPreparation) {
        & conda run --no-capture-output -n $CondaEnvironment python $assetPreparer --model-dir $modelPath
        if ($LASTEXITCODE -ne 0) {
            throw "Pinned DeepDOC asset preparation or verification failed."
        }
    }

    & conda run --no-capture-output -n $CondaEnvironment python $evaluationRunner `
        --model-dir $modelPath `
        --elasticsearch-url $ElasticsearchUrl `
        --output $outputPath
    if ($LASTEXITCODE -ne 0) {
        throw "VERIFIED_SYNTHETIC online evaluation or its effect gates failed."
    }
}
finally {
    Pop-Location
}

Write-Host "VERIFIED_SYNTHETIC metrics written to $outputPath"
