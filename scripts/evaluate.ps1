[CmdletBinding()]
param(
    [string]$Output = "artifacts/evaluation/latest.json",
    [string]$ModelDir = "models/deepdoc",
    [string]$ElasticsearchUrl = "http://localhost:9200",
    [string]$CondaEnvironment = "cvrag",
    [ValidateSet("all", "dev", "frozen")]
    [string]$Stage = "all",
    [string]$DevelopmentReport = "artifacts/evaluation/dev.json",
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

function Import-DashScopeApiKeyFromDotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not [string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }

    $match = Select-String `
        -LiteralPath $Path `
        -Pattern '^\s*(?:export\s+)?DASHSCOPE_API_KEY\s*=\s*(?<value>.*)\s*$' `
        | Select-Object -First 1
    if ($null -eq $match) {
        return
    }

    $value = $match.Matches[0].Groups["value"].Value.Trim()
    if (
        $value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )
    ) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $env:DASHSCOPE_API_KEY = $value
    }
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found. Create the '$CondaEnvironment' environment first."
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$dotEnvPath = Join-Path $repositoryRoot ".env"
Import-DashScopeApiKeyFromDotEnv -Path $dotEnvPath
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
$developmentReportPath = if ([System.IO.Path]::IsPathRooted($DevelopmentReport)) {
    [System.IO.Path]::GetFullPath($DevelopmentReport)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $DevelopmentReport))
}
Assert-NotTemporaryPath -PathValue $modelPath
Assert-NotTemporaryPath -PathValue $outputPath
Assert-NotTemporaryPath -PathValue $developmentReportPath

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
        --stage $Stage `
        --development-report $developmentReportPath `
        --output $outputPath
    if ($LASTEXITCODE -ne 0) {
        throw "VERIFIED_SYNTHETIC online evaluation or its effect gates failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Evaluation stage '$Stage' written to $outputPath"
